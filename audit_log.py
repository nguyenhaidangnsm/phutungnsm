# -*- coding: utf-8 -*-
"""
NHẬT KÝ THAY ĐỔI (audit log) - ghi lại AI làm GÌ, LÚC NÀO, trên DỮ LIỆU NÀO. CHỈ ghi vào DB phần mềm.

Cách hoạt động
  1. Hook after_request tự ghi các request ghi dữ liệu (POST/PUT/PATCH/DELETE): người dùng, cửa hàng, IP, hành động.
  2. Ở các chỗ quan trọng, route gọi audit_record(...) để ghi thêm GIÁ TRỊ CŨ -> MỚI. Request nào đã có bản ghi
     chi tiết thì hook KHÔNG ghi thêm bản chung. audit_skip() = bỏ qua (VD: lưu mà không có gì thay đổi).
  3. Đăng nhập / đăng xuất / đăng nhập sai không đi qua hook -> gọi audit_event(...).
  4. Lỗi khi ghi nhật ký KHÔNG BAO GIỜ làm hỏng request chính.
  5. Xem: /admin/audit-log (chỉ admin) + API /api/admin/audit-log + /export (CSV).

HẠN CHẾ PHÌNH DB (đều chỉnh được bằng biến môi trường trong .env):
  AUDIT_RETENTION_DAYS (mặc định 90)  : tự xoá bản ghi cũ hơn N ngày.
  AUDIT_MAX_ROWS       (mặc định 100000): giữ tối đa N bản ghi mới nhất, vượt thì xoá bản cũ nhất.
  Ngoài ra: bản ghi chung KHÔNG lưu nội dung gửi lên (chỉ tên trường / số dòng / tên file), chi tiết cũ->mới bị
  cắt ở ~3000 ký tự, không lưu đường dẫn/phương thức cho bản ghi đã có chi tiết, đăng nhập sai lặp lại trong
  60 giây chỉ ghi 1 lần, và không có index thừa.

Móc nối (app.py): `from audit_log import init_audit, audit_record, audit_event, audit_skip`, rồi `init_audit(app)`
ở cuối file, SAU khi các blueprint đã đăng ký.
"""
import csv
import hmac
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal

from flask import request, session, jsonify, g, Response, render_template, redirect, url_for


def _env_int(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


AUDIT_RETENTION_DAYS = _env_int('AUDIT_RETENTION_DAYS', 90)
AUDIT_MAX_ROWS = _env_int('AUDIT_MAX_ROWS', 100000)
# Mật khẩu xác nhận khi xoá nhật ký. Nên đặt AUDIT_DELETE_PASSWORD trong .env (mặc định dùng giá trị dưới).
AUDIT_DELETE_PASSWORD = os.environ.get('AUDIT_DELETE_PASSWORD', 'Dang2404#')
_clear_fail = {}                 # chống dò mật khẩu: {ip: [số lần sai, thời điểm khoá đến]}
_CLEANUP_EVERY = 6 * 3600        # dọn tối đa 6 giờ / lần / tiến trình
_CLEANUP_BATCH = 5000
_last_cleanup = 0.0

_CSV_HEADER = ['Thời gian', 'Người dùng', 'Tên', 'Cửa hàng', 'Mượn quyền CH', 'IP', 'Module', 'Hành động',
               'Đối tượng', 'Tóm tắt', 'Chi tiết', 'Mã HTTP']
_MAX_STR = 150          # cắt chuỗi dài
_MAX_LIST = 25          # chỉ giữ N phần tử đầu của danh sách dài
_MAX_JSON = 3000        # tổng kích thước detail (ký tự)
_SENSITIVE = re.compile(r'pass|pwd|token|secret|otp', re.I)

# Không ghi nhật ký: request ghi "vô hại"/nhiễu (đánh dấu đã đọc, toạ độ đăng nhập...)
_IGNORED_PREFIXES = ('/static/', '/api/notifications/', '/api/login-geo', '/teamhub/', '/api/admin/audit-log')

# Nhãn tiếng Việt: (tiền tố đường dẫn) -> (module, hành động). Khớp tiền tố DÀI NHẤT trước.
_LABELS = [
    ('/api/admin/update-price', 'Giá bán', 'Sửa giá bán 1 mã'),
    ('/api/admin/import-prices', 'Giá bán', 'Import file giá bán'),
    ('/api/price-adjustment/import', 'Đề xuất giá', 'Import đề xuất tăng giá'),
    ('/api/price-adjustment/propose', 'Đề xuất giá', 'Tạo đề xuất tăng giá'),
    ('/api/price-adjustment/proposals', 'Đề xuất giá', 'Sửa/xoá đề xuất tăng giá'),
    ('/api/price-adjustment/reset', 'Đề xuất giá', 'Xoá toàn bộ đề xuất của 1 mã'),
    ('/api/gom-don-hang/import', 'Gôm đơn hàng', 'Import file tồn kho (tạo đợt gôm)'),
    ('/api/gom-don-hang/save', 'Gôm đơn hàng', 'Lưu chỉnh sửa gôm đơn'),
    ('/api/gom-don-hang/assign-type', 'Gôm đơn hàng', 'Gán loại đơn hàng loạt'),
    ('/api/gom-don-hang/unassign-type', 'Gôm đơn hàng', 'Bỏ gán loại đơn hàng loạt'),
    ('/api/gom-don-hang/delete-batch', 'Gôm đơn hàng', 'Xoá đợt gôm'),
    ('/api/gom-don-hang/bundle-import', 'Gôm đơn hàng', 'Import mã quy cách cha/con'),
    ('/api/admin/order-check/note', 'Duyệt đơn', 'Sửa ghi chú mã hàng'),
    ('/api/admin/order-check', 'Duyệt đơn', 'Chạy kiểm tra duyệt đơn'),
    ('/api/admin/import-order-lock', 'Duyệt đơn', 'Import dữ liệu khoá đặt hàng'),
    ('/api/admin/import-vehicle-models', 'Duyệt đơn', 'Import model xe / giá nhập'),
    ('/api/admin/audit-client', 'Duyệt đơn', 'Sự kiện từ giao diện'),
    ('/api/orders', 'Danh sách đặt hàng', 'Thay đổi đơn đặt hàng'),
    ('/api/locations', 'Vị trí kho', 'Thay đổi vị trí hàng'),
    ('/api/damaged', 'Hàng hỏng', 'Thay đổi hàng hỏng'),
    ('/api/transfer', 'Luân chuyển', 'Thao tác phiếu luân chuyển'),
    ('/api/admin/transfer', 'Luân chuyển', 'Quản trị phiếu luân chuyển'),
    ('/api/admin/users', 'Người dùng', 'Quản lý tài khoản'),
    ('/api/change-password', 'Người dùng', 'Đổi mật khẩu'),
    ('/api/admin/upload-inventory', 'Tồn kho', 'Upload tồn kho'),
    ('/api/upload', 'Tồn kho', 'Upload dữ liệu'),
    ('/api/admin/import-sales-export', 'Tồn kho', 'Import số liệu bán hàng'),
    ('/api/admin/delete-store-data', 'Tồn kho', 'Xoá dữ liệu cửa hàng'),
    ('/api/admin/truck-announcements', 'Thông báo', 'Thông báo xe đi'),
]
_LABELS.sort(key=lambda x: -len(x[0]))


def _label(path):
    for prefix, module, action in _LABELS:
        if path.startswith(prefix):
            return module, action
    return 'Khác', path


def _clean(v, depth=0):
    """Rút gọn payload: bỏ trường nhạy cảm, cắt chuỗi/danh sách dài."""
    if depth > 4:
        return '…'
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:60]:
            out[str(k)] = '***' if _SENSITIVE.search(str(k)) else _clean(x, depth + 1)
        return out
    if isinstance(v, (list, tuple)):
        res = [_clean(x, depth + 1) for x in v[:_MAX_LIST]]
        if len(v) > _MAX_LIST:
            res.append(f'… (+{len(v) - _MAX_LIST} phần tử, tổng {len(v)})')
        return res
    if isinstance(v, str):
        return v if len(v) <= _MAX_STR else v[:_MAX_STR] + '…'
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, Decimal):             # Decimal -> số thường để giao diện so sánh cũ/mới cho gọn
        return float(v)
    return str(v)[:_MAX_STR]


def _dump(obj):
    if obj is None:
        return None
    s = json.dumps(_clean(obj), ensure_ascii=False, default=str)
    if len(s) > _MAX_JSON:
        s = json.dumps({'_truncated': True, 'preview': s[:_MAX_JSON]}, ensure_ascii=False)
    return s


def _now():
    try:
        from app import vn_now
        return vn_now()
    except Exception:
        return datetime.now()


def _client_ip():
    # app.py đã bọc ProxyFix(x_for=1) nên remote_addr là IP thật. KHÔNG đọc X-Forwarded-For thô (có thể bị giả).
    return request.remote_addr


def _actor():
    # Khi admin "mượn quyền" cửa hàng: session['user'] vẫn là admin THẬT, còn role/store_code bị ghi đè
    # sang của cửa hàng -> ghi riêng cột impersonating để không nhầm admin với nhân viên cửa hàng.
    impersonating = session.get('store_code') if session.get('_impersonate_from') else None
    return {
        'username': session.get('user'),
        'full_name': session.get('full_name'),
        'role': session.get('role'),
        'store_code': session.get('store_code'),
        'impersonating': impersonating,
    }


def diff_fields(before, after):
    """So sánh 2 dict -> chỉ giữ các trường KHÁC nhau: ({trường: cũ}, {trường: mới})."""
    b, a = {}, {}
    for k in sorted(set(before or {}) | set(after or {})):
        ov, nv = (before or {}).get(k), (after or {}).get(k)
        if ov != nv:
            b[k], a[k] = ov, nv
    return b, a


def audit_skip():
    """Gọi từ route khi request KHÔNG thay đổi gì (VD: lưu lại đúng giá cũ) -> không ghi nhật ký."""
    try:
        g._audit_skip = True
    except Exception:
        pass


def _payload_brief(payload):
    """Tóm tắt NHỎ về nội dung gửi lên (KHÔNG lưu cả nội dung để DB không phình): tên trường, độ dài danh sách,
    vài giá trị ngắn. Dữ liệu cũ->mới đầy đủ chỉ có ở nơi gọi audit_record."""
    if isinstance(payload, dict):
        out = {}
        for k, v in list(payload.items())[:12]:
            k = str(k)[:30]
            if _SENSITIVE.search(k):
                out[k] = '***'
            elif isinstance(v, (list, tuple, dict)):
                out[k] = f'[{len(v)} mục]'
            elif isinstance(v, str):
                out[k] = v[:40]
            else:
                out[k] = v if isinstance(v, (int, float, bool)) or v is None else str(v)[:40]
        return out
    if isinstance(payload, (list, tuple)):
        return {'so_muc': len(payload)}
    return None


_login_fail_seen = {}


def _login_fail_throttled(key):
    """Đăng nhập sai lặp lại (dò mật khẩu) cùng tên + IP trong 60 giây chỉ ghi 1 lần -> không làm đầy bảng."""
    now = time.time()
    if len(_login_fail_seen) > 1000:
        _login_fail_seen.clear()
    if now - _login_fail_seen.get(key, 0) < 60:
        return True
    _login_fail_seen[key] = now
    return False


def audit_record(action, module, target=None, summary=None, before=None, after=None, extra=None):
    """Gọi từ route để ghi 1 bản ghi CHI TIẾT (cũ -> mới). Gọi SAU khi commit thành công.
    An toàn: không bao giờ ném lỗi. target: mã hàng / id đợt gôm / id đề xuất...;
    before/after: dict hoặc giá trị bất kỳ."""
    try:
        pend = getattr(g, '_audit_pending', None)
        if pend is None:
            pend = g._audit_pending = []
        pend.append({'action': action, 'module': module, 'target': str(target)[:200] if target is not None else None,
                     'summary': (summary or '')[:500],
                     'detail': _dump({k: v for k, v in (('before', before), ('after', after), ('extra', extra)) if v is not None})})
    except Exception:
        pass


def audit_event(action, module, username=None, target=None, summary=None, extra=None, status_code=200):
    """Ghi NGAY 1 sự kiện không thuộc request ghi dữ liệu (đăng nhập, đăng xuất, đăng nhập sai...).
    username=None -> lấy từ session. An toàn: không bao giờ ném lỗi."""
    try:
        if status_code == 401 and _login_fail_throttled((str(username), request.remote_addr)):
            return
        actor = _actor()
        if username:
            actor['username'] = str(username)[:100]
            if username != session.get('user'):      # tên gõ ở form, chưa phải session
                actor.update(full_name=None, role=None, store_code=None, impersonating=None)
        row = dict(actor, ip=_client_ip(), method=request.method, path=request.path, status_code=status_code,
                   module=module, action=action, target=str(target)[:200] if target is not None else None,
                   summary=(summary or '')[:500],
                   detail=_dump({'extra': extra}) if extra is not None else None, at=_now())
        threading.Thread(target=_safe_write, args=([row],), daemon=True).start()
    except Exception:
        pass


def _write_rows(rows):
    """Ghi bằng KẾT NỐI RIÊNG từ pool (không đụng transaction của request)."""
    from app import _get_pool
    pool = _get_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        for r in rows:
            cur.execute(
                '''INSERT INTO audit_logs (at, username, full_name, role, store_code, impersonating, ip, method, path,
                                           module, action, target, summary, detail, status_code)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (r['at'], r['username'], r['full_name'], r['role'], r['store_code'], r.get('impersonating'), r['ip'],
                 r['method'], r['path'], r['module'], r['action'], r['target'], r['summary'], r['detail'],
                 r['status_code']))
        conn.commit()
        cur.close()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn)


def _after_request(resp):
    try:
        if request.method in ('GET', 'HEAD', 'OPTIONS') or getattr(g, '_audit_skip', False):
            return resp
        path = request.path
        if path.startswith(_IGNORED_PREFIXES) or path in ('/login', '/logout'):
            return resp
        actor = _actor()
        base = dict(actor, ip=_client_ip(), method=request.method, path=path, status_code=resp.status_code,
                    at=_now())
        pend = getattr(g, '_audit_pending', None)
        rows = []
        if pend:
            for p in pend:
                # bản ghi đã có chi tiết: không cần lưu thêm method/path (tiết kiệm dung lượng)
                rows.append(dict(base, **p, **{'method': None, 'path': None}))
        else:
            module, action = _label(path)
            payload = request.get_json(silent=True)
            if payload is not None:
                brief = _payload_brief(payload)
            elif request.files:
                brief = {'files': [str(f.filename)[:80] for f in list(request.files.values())[:5]]}
            elif request.form:
                brief = _payload_brief(dict(request.form))
            else:
                brief = None
            rows.append(dict(base, module=module, action=action, target=None,
                             summary=f'{request.method} {path} → {resp.status_code}',
                             detail=_dump({'payload': brief}) if brief else None))
        if resp.status_code >= 400:
            for r in rows:
                if 'THẤT BẠI' not in (r['action'] or ''):
                    r['action'] = (r['action'] or '') + ' (THẤT BẠI)'
        if rows and actor['username']:
            threading.Thread(target=_safe_write, args=(rows,), daemon=True).start()
    except Exception:
        pass
    return resp


def _safe_write(rows):
    for r in rows:
        r.setdefault('at', datetime.now())
    try:
        _write_rows(rows)
    except Exception as e:
        print(f'[audit_log] Không ghi được nhật ký vào DB: {e}', flush=True)
        return
    _maybe_cleanup()


def _cell(v):
    """Giá trị 1 ô CSV: chống 'công thức' (=,+,-,@ đầu chuỗi) và giới hạn độ dài."""
    if v is None:
        return ''
    v = str(v)
    if v[:1] in ('=', '+', '-', '@'):
        v = "'" + v
    return v[:8000]


def _maybe_cleanup():
    """Dọn nhật ký cũ, tối đa 6 giờ / lần / tiến trình, chạy trong thread nền (không chặn request)."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _CLEANUP_EVERY:
        return
    _last_cleanup = now
    try:
        cleanup_old_logs()
    except Exception as e:
        print(f'[audit_log] Dọn nhật ký cũ lỗi: {e}', flush=True)


def cleanup_old_logs():
    """Xoá (1) bản ghi quá AUDIT_RETENTION_DAYS ngày, (2) bản ghi vượt AUDIT_MAX_ROWS (giữ bản mới nhất).
    Xoá theo lô nhỏ để không khoá bảng lâu. Trả về tổng số dòng đã xoá."""
    from app import _get_pool
    pool = _get_pool()
    conn = pool.getconn()
    deleted = 0
    try:
        cur = conn.cursor()
        while True:
            cur.execute("""DELETE FROM audit_logs WHERE id IN (
                               SELECT id FROM audit_logs WHERE at < NOW() - (%s || ' days')::interval
                               ORDER BY id LIMIT %s)""", (str(AUDIT_RETENTION_DAYS), _CLEANUP_BATCH))
            n = cur.rowcount
            conn.commit()
            deleted += n
            if n < _CLEANUP_BATCH:
                break
        while True:
            cur.execute("""DELETE FROM audit_logs WHERE id IN (
                               SELECT id FROM audit_logs
                               WHERE id < (SELECT COALESCE(MAX(id), 0) FROM audit_logs) - %s
                               ORDER BY id LIMIT %s)""", (AUDIT_MAX_ROWS, _CLEANUP_BATCH))
            n = cur.rowcount
            conn.commit()
            deleted += n
            if n < _CLEANUP_BATCH:
                break
        cur.close()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn)
    if deleted:
        print(f'[audit_log] Đã dọn {deleted} bản ghi nhật ký cũ.', flush=True)
    return deleted


def init_audit_tables():
    from app import _get_pool
    pool = _get_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS audit_logs (
                id BIGSERIAL PRIMARY KEY,
                at TIMESTAMP NOT NULL,
                username TEXT, full_name TEXT, role TEXT, store_code TEXT, ip TEXT,
                method TEXT, path TEXT, module TEXT, action TEXT,
                target TEXT, summary TEXT, detail TEXT, status_code INTEGER
            )''')
        cur.execute('ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS impersonating TEXT')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_logs (at DESC)')
        cur.execute('DROP INDEX IF EXISTS idx_audit_target')      # tìm theo ILIKE '%...%' không dùng được index này -> bỏ cho nhẹ
        cur.execute('CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs (username, at DESC)')
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def _parse_dt(s, end=False):
    s = (s or '').strip()
    if not s:
        return None
    try:
        d = datetime.strptime(s[:10], '%Y-%m-%d')
        return d + timedelta(days=1) if end else d
    except ValueError:
        return None


def _int_arg(name, default, lo, hi):
    try:
        v = int(request.args.get(name, default) or default)
    except (TypeError, ValueError):
        v = default
    return min(hi, max(lo, v))


def _query(args, limit, offset):
    from app import get_db
    where, p = [], []
    d1, d2 = _parse_dt(args.get('from')), _parse_dt(args.get('to'), end=True)
    if d1: where.append('at >= %s'); p.append(d1)
    if d2: where.append('at < %s'); p.append(d2)
    for col, key in (('username', 'user'), ('module', 'module'), ('store_code', 'store')):
        if args.get(key): where.append(f'{col} = %s'); p.append(args[key].strip())
    if args.get('failed') == '1': where.append('status_code >= 400')
    q = (args.get('q') or '').strip()
    if q:
        where.append('(target ILIKE %s OR summary ILIKE %s OR detail ILIKE %s OR action ILIKE %s)')
        p += [f'%{q}%'] * 4
    w = ('WHERE ' + ' AND '.join(where)) if where else ''
    cur = get_db().cursor()
    cur.execute(f'SELECT COUNT(*) AS n FROM audit_logs {w}', p)
    total = cur.fetchone()['n']
    cur.execute(f'SELECT * FROM audit_logs {w} ORDER BY at DESC, id DESC LIMIT %s OFFSET %s', p + [limit, offset])
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    for r in rows:
        r['at'] = r['at'].strftime('%d/%m/%Y %H:%M:%S') if r.get('at') else ''
    return total, rows


def init_audit(app):
    # Chỉ tạo bảng 1 lần: tránh chạy 2 lần (tiến trình cha + con của Flask debug reloader) -> deadlock.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        try:
            init_audit_tables()
        except Exception as e:             # lỗi nhật ký không được làm app không khởi động được
            print(f'[audit_log] Không khởi tạo được bảng audit_logs: {e}', flush=True)
        threading.Thread(target=_maybe_cleanup, daemon=True).start()
    app.after_request(_after_request)

    def _admin():
        return 'user' in session and session.get('role') == 'admin'

    @app.route('/admin/audit-log')
    def audit_log_page():
        if 'user' not in session:
            return redirect(url_for('login'))
        if not _admin():
            return 'Forbidden', 403
        return render_template('audit_log.html')

    @app.route('/api/admin/audit-log')
    def audit_log_api():
        if not _admin():
            return jsonify({'error': 'Forbidden'}), 403
        page = _int_arg('page', 1, 1, 100000)
        size = _int_arg('size', 50, 10, 200)
        total, rows = _query(request.args, size, (page - 1) * size)
        from app import get_db
        cur = get_db().cursor()
        cur.execute('SELECT DISTINCT module FROM audit_logs ORDER BY module')
        modules = [r['module'] for r in cur.fetchall() if r['module']]
        cur.execute('SELECT DISTINCT username FROM audit_logs WHERE username IS NOT NULL ORDER BY username')
        users = [r['username'] for r in cur.fetchall()]
        cur.execute('SELECT DISTINCT store_code FROM audit_logs WHERE store_code IS NOT NULL ORDER BY store_code')
        stores = [r['store_code'] for r in cur.fetchall()]
        try:
            cur.execute("SELECT pg_total_relation_size('audit_logs') AS b")
            db_bytes = cur.fetchone()['b']
        except Exception:
            db_bytes = None
        cur.close()
        return jsonify({'success': True, 'total': total, 'page': page, 'size': size, 'data': rows,
                        'modules': modules, 'users': users, 'stores': stores, 'db_bytes': db_bytes,
                        'retention_days': AUDIT_RETENTION_DAYS, 'max_rows': AUDIT_MAX_ROWS})

    @app.route('/api/admin/audit-log/export')
    def audit_log_export():
        if not _admin():
            return jsonify({'error': 'Forbidden'}), 403
        _, rows = _query(request.args, 20000, 0)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(_CSV_HEADER)
        for r in rows:
            w.writerow([_cell(x) for x in (r['at'], r['username'], r['full_name'], r['store_code'],
                                           r.get('impersonating'), r['ip'], r['module'], r['action'], r['target'],
                                           r['summary'], r['detail'], r['status_code'])])
        return Response('\ufeff' + buf.getvalue(), mimetype='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename=nhat-ky-thay-doi.csv'})

    @app.route('/api/admin/audit-log/clear', methods=['POST'])
    def audit_log_clear():
        """Xoá nhật ký, cần mật khẩu xác nhận (kiểm tra ở SERVER, không để mật khẩu trong JS).
        Body JSON: {password, scope: 'all' | 'before', before: 'YYYY-MM-DD'}."""
        if not _admin():
            return jsonify({'error': 'Forbidden'}), 403
        ip = request.remote_addr or '?'
        fails = _clear_fail.get(ip, [0, 0.0])
        if fails[1] > time.time():
            return jsonify({'success': False, 'error': 'Nhập sai quá nhiều lần, vui lòng thử lại sau vài phút.'}), 429
        d = request.get_json(silent=True) or {}
        pw = str(d.get('password') or '')
        if not hmac.compare_digest(pw.encode('utf-8'), AUDIT_DELETE_PASSWORD.encode('utf-8')):
            fails[0] += 1
            if fails[0] >= 5:                       # sai 5 lần -> khoá 5 phút
                fails = [0, time.time() + 300]
            _clear_fail[ip] = fails
            audit_event('Xoá nhật ký: sai mật khẩu', 'Nhật ký', summary='Nhập sai mật khẩu xoá nhật ký', status_code=401)
            return jsonify({'success': False, 'error': 'Mật khẩu không đúng.'}), 403
        _clear_fail.pop(ip, None)
        scope = d.get('scope')
        from app import get_db
        conn = get_db()
        cur = conn.cursor()
        try:
            if scope == 'before':
                cutoff = _parse_dt(d.get('before'))
                if not cutoff:
                    return jsonify({'success': False, 'error': 'Ngày không hợp lệ.'}), 400
                cur.execute('DELETE FROM audit_logs WHERE at < %s', (cutoff,))
                note = f"trước ngày {cutoff.strftime('%d/%m/%Y')}"
            elif scope == 'all':
                cur.execute('DELETE FROM audit_logs')
                note = 'toàn bộ'
            else:
                return jsonify({'success': False, 'error': 'Phạm vi xoá không hợp lệ.'}), 400
            n = cur.rowcount
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f'[audit_log] Xoá nhật ký lỗi: {e}', flush=True)
            return jsonify({'success': False, 'error': 'Không xoá được nhật ký.'}), 500
        finally:
            cur.close()
        # Để lại 1 dòng "ai đã xoá" (ghi sau khi xoá xong nên không bị xoá theo)
        audit_event('Xoá nhật ký', 'Nhật ký', summary=f'Đã xoá {note} nhật ký ({n} bản ghi)', extra={'so_ban_ghi': n})
        return jsonify({'success': True, 'deleted': n})

    @app.route('/api/admin/audit-client', methods=['POST'])
    def audit_client_event():
        """Sự kiện chỉ xảy ra ở giao diện (vd: xuất file duyệt đơn kèm SL duyệt cuối cùng)."""
        if not _admin():
            return jsonify({'error': 'Forbidden'}), 403
        d = request.get_json(silent=True) or {}
        if d.get('event') != 'order_check_export':
            return jsonify({'error': 'Sự kiện không hợp lệ.'}), 400
        lines = d.get('lines') or []
        changed = [l for l in lines if l.get('approved') != l.get('qty_order')]
        audit_record('Xuất file duyệt đơn', 'Duyệt đơn', target=d.get('store'),
                     summary=f"Cửa hàng {d.get('store')}: {len(lines)} mã, {len(changed)} mã có SL duyệt khác SL đặt",
                     after={'lines': lines[:300]}, extra={'tong_ma': len(lines), 'so_ma_khac': len(changed)})
        return jsonify({'success': True})