# -*- coding: utf-8 -*-
"""
push_notify.py - THÔNG BÁO ĐẨY (Web Push) cho web app Nam Sương Motor.

Chỉ đẩy các sự kiện: gôm đơn / duyệt đơn / đơn đã duyệt xong, luân chuyển nội bộ, kết nối (TeamHub).
Không đụng tới logic cũ: các thông báo chuông trong DB vẫn tạo y như trước; module này chỉ "đi kèm" -
sau khi request ĐÃ commit và trả về thành công (status < 400) mới gửi push ở 1 luồng nền.

Bật bằng 3 biến môi trường (xem gen_vapid.py để tạo khoá):
    VAPID_PUBLIC_KEY   khoá công khai (base64url)
    VAPID_PRIVATE_KEY  khoá bí mật (base64url 32 byte, hoặc đường dẫn file .pem)
    VAPID_SUBJECT      mailto:email-cua-ban@... (tuỳ chọn)
Chưa đặt khoá / chưa cài pywebpush  ->  tính năng tự tắt, app chạy y như cũ.
"""
import os
import json
import hashlib
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, request, jsonify, session, g, Response, has_request_context

from app import get_db, vn_now, app as _flask_app

try:
    from pywebpush import webpush, WebPushException
except Exception:                       # chưa cài pywebpush
    webpush = None
    WebPushException = Exception

push_bp = Blueprint('push_notify', __name__)

VAPID_PUBLIC_KEY = (os.getenv('VAPID_PUBLIC_KEY') or '').strip()
VAPID_PRIVATE_KEY = (os.getenv('VAPID_PRIVATE_KEY') or '').strip()
VAPID_SUBJECT = (os.getenv('VAPID_SUBJECT') or 'mailto:admin@namsuongmotor.vn').strip()
PUSH_ENABLED = bool(webpush and VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)

ADMIN_TARGET = 'ALL'                    # trùng ADMIN_NOTIF_STORE_CODE trong app.py
_COOKIE = 'ns_push_ep'                  # cookie giữ "dấu" thiết bị để tự gỡ đăng ký khi đăng xuất

_table_ready = False
_table_lock = threading.Lock()


# --------------------------------------------------------------------------- bảng
def _ensure_table(db):
    global _table_ready
    if _table_ready:
        return
    with _table_lock:
        if _table_ready:
            return
        cur = db.cursor()
        try:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id SERIAL PRIMARY KEY,
                    endpoint TEXT NOT NULL UNIQUE,
                    endpoint_hash TEXT NOT NULL,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT '',
                    target TEXT NOT NULL,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL,
                    user_agent TEXT,
                    created_at TIMESTAMP,
                    last_seen_at TIMESTAMP
                )''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_push_sub_target ON push_subscriptions (target)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_push_sub_user ON push_subscriptions (username)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_push_sub_hash ON push_subscriptions (endpoint_hash)')
            db.commit()
            _table_ready = True
        except Exception:
            db.rollback()
            traceback.print_exc()
        finally:
            cur.close()


# --------------------------------------------------------------------------- danh tính
def _real_identity():
    """(username, role, target) THẬT của người dùng - admin đang 'mượn quyền' cửa hàng vẫn tính là admin."""
    origin = session.get('_impersonate_from')
    if origin:
        user, role, store = origin.get('user'), origin.get('role'), origin.get('store_code')
    else:
        user, role, store = session.get('user'), session.get('role'), session.get('store_code')
    target = ADMIN_TARGET if role == 'admin' else (store or '')
    return user, role, target


def _effective_target():
    """Người đang THAO TÁC (theo quyền hiện hành) thuộc nhóm nhận thông báo nào."""
    if not has_request_context() or 'user' not in session:
        return None
    return ADMIN_TARGET if session.get('role') == 'admin' else (session.get('store_code') or None)


# --------------------------------------------------------------------------- hàng đợi (gửi sau khi request thành công)
def _enqueue(item):
    if not PUSH_ENABLED:
        return
    if has_request_context():
        q = g.get('_push_queue')
        if q is None:
            q = []
            g._push_queue = q
        q.append(item)
    else:                               # ngoài request (job nền): gửi ngay
        threading.Thread(target=_send_items, args=([item],), daemon=True).start()


def _clip(s, n):
    s = str(s or '').strip()
    return s if len(s) <= n else s[:n - 1] + '…'


def _go_url(go):
    return '/?go=' + go if go else '/'


def queue_push(target, title, body, go=None, url=None, tag=None):
    """Báo cho 1 NHÓM nhận: target = mã cửa hàng (NS1...) hoặc 'ALL' (toàn bộ admin).
    Không gửi cho chính nhóm của người vừa thao tác (họ đã thấy kết quả trên màn hình)."""
    try:
        if not target or target == _effective_target():
            return
        _enqueue({'mode': 'target', 'keys': [target], 'title': _clip(title, 80), 'body': _clip(body, 180),
                  'url': url or _go_url(go), 'tag': tag})
    except Exception:
        traceback.print_exc()


def queue_push_users(usernames, title, body, url='/teamhub', tag=None, exclude_self=True):
    """Báo cho DANH SÁCH tên đăng nhập cụ thể (dùng cho TeamHub / Kết nối: tin nhắn, bình luận...)."""
    try:
        names = [u for u in (usernames or []) if u]
        if exclude_self and has_request_context():
            me = _real_identity()[0]
            names = [u for u in names if u != me]
        if not names:
            return
        _enqueue({'mode': 'users', 'keys': names, 'title': _clip(title, 80), 'body': _clip(body, 180),
                  'url': url, 'tag': tag})
    except Exception:
        traceback.print_exc()


def queue_notification_push(cursor, store_code, title, message, transfer_id=None):
    """Gọi từ create_notification(): quyết định mở đúng màn hình nào rồi xếp hàng push."""
    if not PUSH_ENABLED:
        return
    if transfer_id is not None:
        if store_code == ADMIN_TARGET:
            go = 'transfer'
        else:
            go = 'transfer-export'      # mặc định: phiếu cửa hàng khác gửi đến
            try:                        # SAVEPOINT: lỗi tra cứu không được làm hỏng transaction của thao tác gốc
                cursor.execute('SAVEPOINT push_q')
                cursor.execute('SELECT from_store FROM transfer_requests WHERE id = %s', (transfer_id,))
                r = cursor.fetchone()
                cursor.execute('RELEASE SAVEPOINT push_q')
                if r and r['from_store'] == store_code:
                    go = 'transfer-import'   # mình là bên xin hàng: xem phiếu đã gửi
            except Exception:
                try:
                    cursor.execute('ROLLBACK TO SAVEPOINT push_q')
                except Exception:
                    pass
        tag = 'transfer-%s' % transfer_id
    else:
        go = 'review' if store_code == ADMIN_TARGET else 'gdh'
        tag = None
    queue_push(store_code, title, message, go=go, tag=tag)


# --------------------------------------------------------------------------- gửi
def _select_subs(items):
    subs = {}
    with _flask_app.app_context():
        db = get_db()
        _ensure_table(db)
        cur = db.cursor()
        try:
            for it in items:
                col = 'target' if it['mode'] == 'target' else 'username'
                cur.execute('SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE %s = ANY(%%s)' % col,
                            (it['keys'],))
                subs[id(it)] = cur.fetchall()
        finally:
            cur.close()
    return subs


def _send_one(sub, payload):
    try:
        webpush(subscription_info={'endpoint': sub['endpoint'], 'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']}},
                data=payload, vapid_private_key=VAPID_PRIVATE_KEY, vapid_claims={'sub': VAPID_SUBJECT},
                ttl=6 * 3600, timeout=10)
        return sub['id'], True, False
    except WebPushException as e:
        code = getattr(getattr(e, 'response', None), 'status_code', None)
        return sub['id'], False, code in (404, 410)      # 404/410: thiết bị đã gỡ đăng ký -> xoá khỏi DB
    except Exception:
        return sub['id'], False, False


def _send_items(items):
    try:
        subs = _select_subs(items)
        jobs = []
        for it in items:
            payload = json.dumps({'title': it['title'], 'body': it['body'], 'url': it['url'], 'tag': it.get('tag')},
                                 ensure_ascii=False)
            for s in subs.get(id(it), []):
                jobs.append((s, payload))
        if not jobs:
            return
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
            results = list(ex.map(lambda j: _send_one(*j), jobs))
        dead = [sid for sid, _ok, gone in results if gone]
        ok = [sid for sid, good, _g in results if good]
        if dead or ok:
            with _flask_app.app_context():
                db = get_db()
                cur = db.cursor()
                try:
                    if dead:
                        cur.execute('DELETE FROM push_subscriptions WHERE id = ANY(%s)', (dead,))
                    db.commit()
                finally:
                    cur.close()
    except Exception:
        traceback.print_exc()


@push_bp.after_app_request
def _flush_queue(resp):
    """Chạy SAU khi route đã commit. Request lỗi (>= 400) thì bỏ hết push đã xếp hàng."""
    try:
        q = g.pop('_push_queue', None)
        if q and resp.status_code < 400:
            threading.Thread(target=_send_items, args=(q,), daemon=True).start()
        if request.path == '/logout':   # đăng xuất: gỡ đăng ký của thiết bị này để không nhận tiếp thông báo của tài khoản cũ
            h = request.cookies.get(_COOKIE)
            if h and PUSH_ENABLED:
                db = get_db()
                _ensure_table(db)
                cur = db.cursor()
                try:
                    cur.execute('DELETE FROM push_subscriptions WHERE endpoint_hash = %s', (h,))
                    db.commit()
                finally:
                    cur.close()
            resp.delete_cookie(_COOKIE, path='/')
    except Exception:
        traceback.print_exc()
    return resp


# --------------------------------------------------------------------------- API cho trình duyệt
def _need_login():
    if 'user' not in session:
        return jsonify({'error': 'Chưa đăng nhập.'}), 401
    return None


@push_bp.route('/api/push/key', methods=['GET'])
def push_key():
    blk = _need_login()
    if blk:
        return blk
    return jsonify({'enabled': PUSH_ENABLED, 'key': VAPID_PUBLIC_KEY if PUSH_ENABLED else '',
                    'me': _real_identity()[0]})


@push_bp.route('/api/push/subscribe', methods=['POST'])
def push_subscribe():
    blk = _need_login()
    if blk:
        return blk
    if not PUSH_ENABLED:
        return jsonify({'error': 'Thông báo đẩy chưa được bật trên máy chủ.'}), 503
    data = request.get_json(silent=True) or {}
    endpoint = str(data.get('endpoint') or '').strip()
    keys = data.get('keys') or {}
    p256dh, auth = str(keys.get('p256dh') or ''), str(keys.get('auth') or '')
    if not endpoint.startswith('https://') or not p256dh or not auth or len(endpoint) > 1000:
        return jsonify({'error': 'Dữ liệu đăng ký không hợp lệ.'}), 400
    user, role, target = _real_identity()
    if not target:
        return jsonify({'error': 'Tài khoản chưa gắn cửa hàng.'}), 400
    h = hashlib.sha256(endpoint.encode('utf-8')).hexdigest()[:40]
    db = get_db()
    _ensure_table(db)
    cur = db.cursor()
    try:
        now = vn_now()
        cur.execute('''
            INSERT INTO push_subscriptions (endpoint, endpoint_hash, username, role, target, p256dh, auth, user_agent, created_at, last_seen_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (endpoint) DO UPDATE SET username = EXCLUDED.username, role = EXCLUDED.role, target = EXCLUDED.target,
                p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth, user_agent = EXCLUDED.user_agent, last_seen_at = EXCLUDED.last_seen_at
        ''', (endpoint, h, user, role or '', target, p256dh, auth, (request.headers.get('User-Agent') or '')[:300], now, now))
        db.commit()
    except Exception:
        db.rollback()
        traceback.print_exc()
        return jsonify({'error': 'Không lưu được đăng ký.'}), 500
    finally:
        cur.close()
    resp = jsonify({'success': True})
    resp.set_cookie(_COOKIE, h, max_age=365 * 24 * 3600, httponly=True, samesite='Lax',
                    secure=bool(_flask_app.config.get('SESSION_COOKIE_SECURE')), path='/')
    return resp


@push_bp.route('/api/push/unsubscribe', methods=['POST'])
def push_unsubscribe():
    blk = _need_login()
    if blk:
        return blk
    endpoint = str((request.get_json(silent=True) or {}).get('endpoint') or '').strip()
    if endpoint:
        db = get_db()
        _ensure_table(db)
        cur = db.cursor()
        try:
            cur.execute('DELETE FROM push_subscriptions WHERE endpoint = %s', (endpoint,))
            db.commit()
        finally:
            cur.close()
    resp = jsonify({'success': True})
    resp.delete_cookie(_COOKIE, path='/')
    return resp


@push_bp.route('/api/push/test', methods=['POST'])
def push_test():
    """Gửi thử 1 thông báo tới các thiết bị đã đăng ký của CHÍNH tài khoản đang đăng nhập."""
    blk = _need_login()
    if blk:
        return blk
    if not PUSH_ENABLED:
        return jsonify({'error': 'Thông báo đẩy chưa được bật trên máy chủ (thiếu pywebpush hoặc khoá VAPID).'}), 503
    user = _real_identity()[0]
    item = {'mode': 'users', 'keys': [user], 'title': 'Nam Sương Motor',
            'body': 'Thông báo đẩy đã hoạt động trên thiết bị này.', 'url': '/', 'tag': 'push-test'}
    subs = _select_subs([item])[id(item)]
    if not subs:
        return jsonify({'error': 'Tài khoản này chưa có thiết bị nào đăng ký.'}), 404
    payload = json.dumps({k: item[k] for k in ('title', 'body', 'url', 'tag')}, ensure_ascii=False)
    res = [_send_one(s, payload) for s in subs]
    return jsonify({'success': True, 'devices': len(subs), 'sent': sum(1 for _i, ok, _g in res if ok)})


# --------------------------------------------------------------------------- service worker (phạm vi toàn site)
_SW_JS = r"""
/* Nam Sương Motor - service worker nhận thông báo đẩy. Chỉ xử lý push, KHÔNG cache / chặn request nào. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

function nsKeepSilentWhenVisible() {            // Chrome/Edge/Firefox cho phép bỏ qua khi trang đang mở (trang đã tự báo toast).
  var ua = self.navigator.userAgent || '';       // Safari/iOS bắt buộc phải hiện thông báo cho mỗi push.
  return /Chrome|Edg|Firefox/.test(ua) && !/iPhone|iPad|iPod/.test(ua);
}

self.addEventListener('push', function (event) {
  var d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { body: event.data ? event.data.text() : '' }; }
  event.waitUntil((async function () {
    var wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    if (nsKeepSilentWhenVisible() && wins.some(function (c) { return c.visibilityState === 'visible' && c.focused; })) return;
    await self.registration.showNotification(d.title || 'Nam Sương Motor', {
      body: d.body || '', icon: '/static/logo.png', badge: '/static/logo.png', lang: 'vi',
      tag: d.tag || undefined, renotify: !!d.tag, data: { url: d.url || '/' }
    });
  })());
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var target = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin);
  var wantHub = target.pathname.indexOf('/teamhub') === 0;
  event.waitUntil((async function () {
    var wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (var i = 0; i < wins.length; i++) {
      var c = wins[i], u = new URL(c.url);
      if (u.origin === target.origin && (u.pathname.indexOf('/teamhub') === 0) === wantHub) {
        await c.focus();
        c.postMessage({ type: 'ns-push-open', url: target.href });
        return;
      }
    }
    await self.clients.openWindow(target.href);
  })());
});
"""


@push_bp.route('/sw.js')
def service_worker():
    return Response(_SW_JS, mimetype='application/javascript',
                    headers={'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/'})
