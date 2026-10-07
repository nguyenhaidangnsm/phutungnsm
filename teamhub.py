"""Nam Sương Motor - mạng xã hội nội bộ (bảng tin, chat, nhóm, thông báo).

Cách ghép vào app.py: xem HUONG_DAN.md. Module này theo đúng mẫu của
stocktake.py / warehouse3d.py: import get_db từ app, có init_teamhub_tables(cursor),
đăng ký blueprint ở CUỐI app.py.

Nguyên tắc:
- Dùng chung đăng nhập của app (session['user'], session['role']).
- Mọi bảng có tiền tố th_ để không đụng bảng notifications sẵn có.
- KHÔNG có khoá ngoại tới users: xoá/gỡ người dùng thì bài viết, tin nhắn cũ vẫn còn.
"""
import os
import re
import shutil
import uuid
from urllib.parse import quote
from datetime import datetime
from functools import wraps

from flask import Blueprint, request, jsonify, session, render_template, redirect, Response, abort, send_from_directory
from PIL import Image, ImageOps

from app import get_db, SUPER_ADMIN_USERNAME

teamhub_bp = Blueprint('teamhub', __name__)

CATS = ['Thông báo', 'Kinh doanh', 'Kho', 'Góc chia sẻ']   # 'Thông báo' chỉ Admin đăng
DEFAULT_GROUP = 'Toàn công ty'
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'teamhub_uploads')
PAGE = 20
MAX_FILE = 200 * 1024 * 1024   # tệp đính kèm tối đa 200MB
MAX_REQ = MAX_FILE + 10 * 1024 * 1024
MIN_FREE = 2 * 1024 ** 3       # từ chối tải lên nếu ổ đĩa còn trống dưới 2GB
FILES_DIR = os.environ.get('TEAMHUB_FILES_DIR') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'teamhub_files')
ACCEL_PREFIX = os.environ.get('TEAMHUB_ACCEL_PREFIX', '')   # ví dụ /_teamhub_files/  (nginx X-Accel-Redirect, xem hướng dẫn)
FILE_NAME_RE = re.compile(r'[0-9a-f]{32}(\.[a-z0-9]{1,10})?')
BLOCKED_EXT = {'exe', 'bat', 'cmd', 'com', 'scr', 'msi', 'dll', 'sh', 'ps1', 'vbs', 'js', 'jar', 'apk',
               'php', 'py', 'html', 'htm', 'svg', 'lnk', 'reg'}
ALLOW_REMOVE_MEMBER = False   # False = TẮT chức năng Admin "Gỡ" tài khoản khỏi TeamHub (vẫn cho "Khôi phục"). Đặt True để bật lại.
MAX_IMG = 12 * 1024 * 1024   # ảnh tải lên tối đa 12MB (app.py nên đặt MAX_CONTENT_LENGTH >= mức này)


# ---------------------------------------------------------------- bảng dữ liệu
def init_teamhub_tables(cursor):
    for sql in (
        '''CREATE TABLE IF NOT EXISTS th_members (
            username VARCHAR(50) PRIMARY KEY,
            removed_at TIMESTAMPTZ, removed_by VARCHAR(50))''',
        '''CREATE TABLE IF NOT EXISTS th_posts (
            id SERIAL PRIMARY KEY, author VARCHAR(50) NOT NULL,
            category VARCHAR(30) NOT NULL, body TEXT NOT NULL, image VARCHAR(120),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), deleted BOOLEAN NOT NULL DEFAULT FALSE)''',
        'CREATE INDEX IF NOT EXISTS idx_th_posts_cat ON th_posts(category, id DESC)',
        '''CREATE TABLE IF NOT EXISTS th_likes (
            post_id INT NOT NULL REFERENCES th_posts(id) ON DELETE CASCADE,
            username VARCHAR(50) NOT NULL, PRIMARY KEY (post_id, username))''',
        '''CREATE TABLE IF NOT EXISTS th_comments (
            id SERIAL PRIMARY KEY, post_id INT NOT NULL REFERENCES th_posts(id) ON DELETE CASCADE,
            author VARCHAR(50) NOT NULL, body TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''',
        'CREATE INDEX IF NOT EXISTS idx_th_comments_post ON th_comments(post_id, id)',
        '''CREATE TABLE IF NOT EXISTS th_convos (
            id SERIAL PRIMARY KEY, kind VARCHAR(10) NOT NULL, name VARCHAR(100),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''',
        '''CREATE TABLE IF NOT EXISTS th_convo_members (
            convo_id INT NOT NULL REFERENCES th_convos(id) ON DELETE CASCADE,
            username VARCHAR(50) NOT NULL, last_read INT NOT NULL DEFAULT 0,
            PRIMARY KEY (convo_id, username))''',
        '''CREATE TABLE IF NOT EXISTS th_messages (
            id SERIAL PRIMARY KEY, convo_id INT NOT NULL REFERENCES th_convos(id) ON DELETE CASCADE,
            sender VARCHAR(50) NOT NULL, body TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''',
        'CREATE INDEX IF NOT EXISTS idx_th_messages_convo ON th_messages(convo_id, id)',
        '''CREATE TABLE IF NOT EXISTS th_notifs (
            id SERIAL PRIMARY KEY, username VARCHAR(50) NOT NULL, text VARCHAR(300) NOT NULL,
            is_read BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''',
        'CREATE INDEX IF NOT EXISTS idx_th_notifs_user ON th_notifs(username, is_read, id DESC)',
    ):
        cursor.execute(sql)
    cursor.execute('ALTER TABLE th_convos ADD COLUMN IF NOT EXISTS owner VARCHAR(50)')
    for sql in (
        'ALTER TABLE th_posts ADD COLUMN IF NOT EXISTS file_path VARCHAR(80)',
        'ALTER TABLE th_posts ADD COLUMN IF NOT EXISTS file_name VARCHAR(200)',
        'ALTER TABLE th_posts ADD COLUMN IF NOT EXISTS file_size BIGINT',
        'ALTER TABLE th_messages ADD COLUMN IF NOT EXISTS att VARCHAR(80)',
        'ALTER TABLE th_messages ADD COLUMN IF NOT EXISTS att_name VARCHAR(200)',
        'ALTER TABLE th_messages ADD COLUMN IF NOT EXISTS att_size BIGINT',
        'ALTER TABLE th_messages ADD COLUMN IF NOT EXISTS att_kind VARCHAR(10)',
        'CREATE INDEX IF NOT EXISTS idx_th_posts_file ON th_posts(file_path) WHERE file_path IS NOT NULL',
        'CREATE INDEX IF NOT EXISTS idx_th_messages_att ON th_messages(att) WHERE att IS NOT NULL',
    ):
        cursor.execute(sql)
    cursor.execute("SELECT 1 FROM th_convos WHERE kind='group' AND name=%s", (DEFAULT_GROUP,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO th_convos(kind, name) VALUES('group', %s)", (DEFAULT_GROUP,))


# ---------------------------------------------------------------- tiện ích
def _run(sql, args=(), fetch='all', commit=False):
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(sql, args)
        out = cur.fetchall() if fetch == 'all' else cur.fetchone() if fetch == 'one' else None
        if commit:
            db.commit()
        return out
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()


def _ser(r):
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dict(r).items()}


def _body():
    return ((request.get_json(silent=True) or {}) if request.is_json else request.form)


def _text(v, limit):
    v = (v or '').strip()
    return v[:limit] if v else ''


def _api(admin=False):
    """Bắt buộc đăng nhập; chặn người đã bị Admin gỡ khỏi TeamHub; tự tạo hồ sơ lần đầu."""
    def deco(f):
        @wraps(f)
        def wrapper(*a, **k):
            me = session.get('user')
            if not me:
                return jsonify(error='Bạn chưa đăng nhập.'), 401
            m = _run('SELECT removed_at FROM th_members WHERE username=%s', (me,), 'one')
            if m is None:
                _run('INSERT INTO th_members(username) VALUES(%s) ON CONFLICT DO NOTHING', (me,), None)
                _run('''INSERT INTO th_convo_members(convo_id, username)
                        SELECT id, %s FROM th_convos WHERE kind='group' AND name=%s
                        ON CONFLICT DO NOTHING''', (me, DEFAULT_GROUP), None, True)
            elif m['removed_at']:
                return jsonify(error='Tài khoản của bạn đã bị gỡ khỏi Nam Sương Motor. Liên hệ Admin nếu cần.'), 403
            if admin and session.get('role') != 'admin':
                return jsonify(error='Chỉ Admin mới có quyền này.'), 403
            return f(me, *a, **k)
        return wrapper
    return deco


def _push(usernames, title, body, tag=None):
    """Thông báo đẩy (xem push_notify.py). Chỉ xếp hàng, gửi sau khi request thành công; lỗi gì cũng nuốt."""
    try:
        from push_notify import queue_push_users
        queue_push_users(list(usernames), title, body, url='/teamhub', tag=tag)
    except Exception:
        pass


def _notify(usernames, text, push=False):
    rows = [(u, text[:300]) for u in set(usernames)]
    if rows:
        db = get_db()
        cur = db.cursor()
        cur.executemany('INSERT INTO th_notifs(username, text) VALUES(%s,%s)', rows)
        db.commit()
        cur.close()
        if push:
            _push([u for u, _t in rows], 'Kết nối', text)


def _name(u):
    r = _run("SELECT COALESCE(NULLIF(full_name,''), username) AS n FROM users WHERE username=%s", (u,), 'one')
    return r['n'] if r else u


def _in_convo(cid, me):
    return _run('SELECT 1 FROM th_convo_members WHERE convo_id=%s AND username=%s', (cid, me), 'one') is not None


def _save_img(f, folder=None):
    folder = folder or UPLOAD_DIR
    img = ImageOps.exif_transpose(Image.open(f)).convert('RGB')
    img.thumbnail((1280, 1280))
    os.makedirs(folder, exist_ok=True)
    name = uuid.uuid4().hex + '.jpg'
    img.save(os.path.join(folder, name), 'JPEG', quality=82, optimize=True)
    return name


def _img_ok(f):
    f.stream.seek(0, os.SEEK_END)
    n = f.stream.tell()
    f.stream.seek(0)
    return n <= MAX_IMG


def _disk_ok(need=0):
    os.makedirs(FILES_DIR, exist_ok=True)
    if shutil.disk_usage(FILES_DIR).free - need < MIN_FREE:
        raise ValueError('Máy chủ sắp hết dung lượng. Hãy báo Admin dọn dẹp rồi thử lại.')


def _save_file(f):
    """Lưu tệp bất kỳ (tối đa 200MB) ra thư mục riêng, KHÔNG nằm trong static. Trả về (tên lưu, tên gốc, dung lượng)."""
    orig = os.path.basename((f.filename or '').replace('\\', '/'))
    orig = re.sub(r'[\x00-\x1f<>:"|?*]', '', orig).strip()[:150] or 'tep'
    ext = os.path.splitext(orig)[1].lower()
    if ext.lstrip('.') in BLOCKED_EXT:
        raise ValueError('Không gửi được loại tệp này (%s) vì lý do an toàn.' % ext)
    if not re.fullmatch(r'\.[a-z0-9]{1,10}', ext):
        ext = ''
    _disk_ok(request.content_length or 0)
    name = uuid.uuid4().hex + ext
    path = os.path.join(FILES_DIR, name)
    f.save(path)
    size = os.path.getsize(path)
    if size > MAX_FILE:
        os.remove(path)
        raise ValueError('Tệp quá lớn, tối đa 200MB.')
    return name, orig, size


def _rm(folder, name):
    if name:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


@teamhub_bp.record_once
def _raise_upload_limit(state):
    """Nếu app.py đã đặt MAX_CONTENT_LENGTH thấp hơn mức cần cho tệp 200MB thì nâng lên."""
    cur = state.app.config.get('MAX_CONTENT_LENGTH')
    if cur is not None and cur < MAX_REQ:
        state.app.config['MAX_CONTENT_LENGTH'] = MAX_REQ


# ---------------------------------------------------------------- trang + khởi tạo
@teamhub_bp.route('/teamhub')
def page():
    if 'user' not in session:
        return redirect('/login')
    return render_template('teamhub.html')


# ---------------------------------------------------------------- cài lên màn hình chính (PWA)
@teamhub_bp.route('/teamhub/manifest.webmanifest')
def manifest():
    icons = [{'src': f'/static/teamhub_icons/icon-{s}.png', 'sizes': f'{s}x{s}',
              'type': 'image/png', 'purpose': 'any maskable'} for s in (192, 512)]
    r = jsonify(name='Nam Sương Motor', short_name='Nam Sương', start_url='/teamhub', scope='/teamhub',
                display='standalone', background_color='#0f1420', theme_color='#0f1420', lang='vi', icons=icons)
    r.mimetype = 'application/manifest+json'
    return r


@teamhub_bp.route('/teamhub/sw.js')
def service_worker():
    js = ("self.addEventListener('install',()=>self.skipWaiting());"
          "self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));"
          "self.addEventListener('fetch',()=>{});")
    return Response(js, mimetype='application/javascript',
                    headers={'Service-Worker-Allowed': '/', 'Cache-Control': 'no-cache'})


@teamhub_bp.route('/teamhub/api/bootstrap')
@_api()
def bootstrap(me):
    r = _run("""SELECT COALESCE(NULLIF(full_name,''), username) AS name, COALESCE(branch,'') AS branch
                FROM users WHERE username=%s""", (me,), 'one') or {'name': me, 'branch': ''}
    return jsonify(me={'username': me, 'name': r['name'], 'branch': r['branch'],
                       'is_admin': session.get('role') == 'admin'}, cats=CATS)


@teamhub_bp.route('/teamhub/api/pulse')
@_api()
def pulse(me):
    n = _run('SELECT COUNT(*) AS c FROM th_notifs WHERE username=%s AND NOT is_read', (me,), 'one')['c']
    c = _run('''SELECT COUNT(*) AS c FROM th_messages m
                JOIN th_convo_members cm ON cm.convo_id=m.convo_id AND cm.username=%s
                WHERE m.id > cm.last_read AND m.sender<>%s''', (me, me), 'one')['c']
    return jsonify(notif=n, chat=c)


# ---------------------------------------------------------------- bảng tin
@teamhub_bp.route('/teamhub/api/posts')
@_api()
def posts(me):
    cat = request.args.get('cat', '')
    before = request.args.get('before', 0, type=int)
    rows = _run('''
        SELECT p.id, p.author, COALESCE(NULLIF(u.full_name,''), p.author) AS name,
               COALESCE(u.branch,'') AS branch, p.category, p.body, p.image, p.file_path, p.file_name, p.file_size, p.created_at,
               (SELECT COUNT(*) FROM th_likes l WHERE l.post_id=p.id) AS likes,
               EXISTS(SELECT 1 FROM th_likes l WHERE l.post_id=p.id AND l.username=%s) AS liked,
               (SELECT COUNT(*) FROM th_comments c WHERE c.post_id=p.id) AS ncm
        FROM th_posts p LEFT JOIN users u ON u.username=p.author
        WHERE NOT p.deleted AND (%s='' OR p.category=%s) AND (%s=0 OR p.id<%s)
        ORDER BY p.id DESC LIMIT %s''', (me, cat, cat, before, before, PAGE))
    out = []
    for r in rows:
        d = _ser(r)
        d['mine'] = r['author'] == me
        out.append(d)
    return jsonify(posts=out, more=len(out) == PAGE)


@teamhub_bp.route('/teamhub/api/posts', methods=['POST'])
@_api()
def create_post(me):
    b = _body()
    text, cat = _text(b.get('body'), 2000), b.get('category', 'Góc chia sẻ')
    if cat not in CATS:
        cat = 'Góc chia sẻ'
    if cat == 'Thông báo' and session.get('role') != 'admin':
        return jsonify(error='Chỉ Admin mới đăng được mục Thông báo.'), 403
    f = request.files.get('image')
    fl = request.files.get('file')
    has_f = bool(fl and fl.filename)
    if not text and not f and not has_f:
        return jsonify(error='Hãy nhập nội dung hoặc chọn ảnh / tệp.'), 400
    img = None
    if f and f.filename:
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
        if size > MAX_IMG:
            return jsonify(error='Ảnh quá lớn, hãy chọn ảnh nhỏ hơn 12MB.'), 400
        try:
            img = _save_img(f)
        except Exception:
            return jsonify(error='Không đọc được ảnh. Hãy chọn file JPG hoặc PNG.'), 400
    att = (None, None, None)
    if has_f:
        try:
            att = _save_file(fl)
        except ValueError as e:
            _rm(UPLOAD_DIR, img)
            return jsonify(error=str(e)), 400
    pid = _run('INSERT INTO th_posts(author, category, body, image, file_path, file_name, file_size) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id',
               (me, cat, text, img, *att), 'one', True)['id']
    if cat == 'Thông báo':
        rows = _run('SELECT username FROM th_members WHERE removed_at IS NULL AND username<>%s', (me,))
        _notify([r['username'] for r in rows], f'📢 {_name(me)} đăng thông báo mới: {text[:80]}', push=True)
    return jsonify(id=pid)


@teamhub_bp.route('/teamhub/api/posts/<int:pid>/delete', methods=['POST'])
@_api()
def delete_post(me, pid):
    p = _run('SELECT author, image, file_path FROM th_posts WHERE id=%s AND NOT deleted', (pid,), 'one')
    if not p:
        return jsonify(error='Không tìm thấy bài viết.'), 404
    if p['author'] != me and session.get('role') != 'admin':
        return jsonify(error='Bạn chỉ xoá được bài của mình.'), 403
    _run('UPDATE th_posts SET deleted=TRUE WHERE id=%s', (pid,), None, True)
    _rm(UPLOAD_DIR, p['image'])      # xoá luôn ảnh / tệp để giải phóng ổ đĩa
    _rm(FILES_DIR, p['file_path'])
    return jsonify(ok=True)


@teamhub_bp.route('/teamhub/api/posts/<int:pid>/like', methods=['POST'])
@_api()
def like(me, pid):
    p = _run('SELECT author FROM th_posts WHERE id=%s AND NOT deleted', (pid,), 'one')
    if not p:
        return jsonify(error='Không tìm thấy bài viết.'), 404
    had = _run('DELETE FROM th_likes WHERE post_id=%s AND username=%s RETURNING 1', (pid, me), 'one', True)
    if not had:
        _run('INSERT INTO th_likes VALUES(%s,%s) ON CONFLICT DO NOTHING', (pid, me), None, True)
        if p['author'] != me:
            _notify([p['author']], f'{_name(me)} đã thích bài viết của bạn')
    n = _run('SELECT COUNT(*) AS c FROM th_likes WHERE post_id=%s', (pid,), 'one')['c']
    return jsonify(liked=not had, likes=n)


@teamhub_bp.route('/teamhub/api/posts/<int:pid>/comments')
@_api()
def comments(me, pid):
    rows = _run('''SELECT c.id, c.author, COALESCE(NULLIF(u.full_name,''), c.author) AS name, c.body, c.created_at
                   FROM th_comments c LEFT JOIN users u ON u.username=c.author
                   WHERE c.post_id=%s ORDER BY c.id''', (pid,))
    return jsonify(comments=[dict(_ser(r), mine=r['author'] == me) for r in rows])


@teamhub_bp.route('/teamhub/api/posts/<int:pid>/comments', methods=['POST'])
@_api()
def add_comment(me, pid):
    text = _text(_body().get('body'), 500)
    p = _run('SELECT author FROM th_posts WHERE id=%s AND NOT deleted', (pid,), 'one')
    if not p or not text:
        return jsonify(error='Không gửi được bình luận.'), 400
    cid = _run('INSERT INTO th_comments(post_id, author, body) VALUES(%s,%s,%s) RETURNING id',
               (pid, me, text), 'one', True)['id']
    if p['author'] != me:
        _notify([p['author']], f'{_name(me)} đã bình luận bài viết của bạn: {text[:60]}', push=True)
    return jsonify(id=cid, name=_name(me), body=text, mine=True)


@teamhub_bp.route('/teamhub/api/comments/<int:cid>/delete', methods=['POST'])
@_api()
def delete_comment(me, cid):
    c = _run('SELECT author FROM th_comments WHERE id=%s', (cid,), 'one')
    if not c:
        return jsonify(error='Không tìm thấy bình luận.'), 404
    if c['author'] != me and session.get('role') != 'admin':
        return jsonify(error='Bạn chỉ xoá được bình luận của mình.'), 403
    _run('DELETE FROM th_comments WHERE id=%s', (cid,), None, True)
    return jsonify(ok=True)


# ---------------------------------------------------------------- nhóm + chat
@teamhub_bp.route('/teamhub/api/people')
@_api()
def people(me):
    rows = _run('''SELECT u.username, COALESCE(NULLIF(u.full_name,''), u.username) AS name, COALESCE(u.branch,'') AS branch
                   FROM users u LEFT JOIN th_members m ON m.username=u.username
                   WHERE u.username<>%s AND m.removed_at IS NULL ORDER BY name''', (me,))
    return jsonify(people=[_ser(r) for r in rows])


def _can_manage(cid, me):
    """Chủ nhóm (người tạo) hoặc Admin được quản lý nhóm."""
    if session.get('role') == 'admin':
        return True
    r = _run("SELECT owner FROM th_convos WHERE id=%s AND kind='group'", (cid,), 'one')
    return bool(r and r['owner'] == me)


def _active_user(username):
    return _run('''SELECT 1 FROM users u LEFT JOIN th_members m ON m.username=u.username
                   WHERE u.username=%s AND m.removed_at IS NULL''', (username,), 'one') is not None


@teamhub_bp.route('/teamhub/api/groups')
@_api()
def groups(me):
    """Nhóm là RIÊNG TƯ (như Zalo): mỗi người chỉ thấy nhóm mình đang ở trong; Admin thấy tất cả."""
    admin = session.get('role') == 'admin'
    rows = _run('''SELECT c.id, c.name, c.owner,
                   (SELECT COUNT(*) FROM th_convo_members x WHERE x.convo_id=c.id) AS members,
                   EXISTS(SELECT 1 FROM th_convo_members x WHERE x.convo_id=c.id AND x.username=%s) AS joined
                   FROM th_convos c WHERE c.kind='group'
                   AND (%s OR EXISTS(SELECT 1 FROM th_convo_members x WHERE x.convo_id=c.id AND x.username=%s))
                   ORDER BY c.id''', (me, admin, me))
    return jsonify(groups=[dict(_ser(r), can_manage=admin or r['owner'] == me) for r in rows])


@teamhub_bp.route('/teamhub/api/groups', methods=['POST'])
@_api()
def create_group(me):
    """Bất kỳ ai cũng tạo được nhóm, chọn sẵn thành viên; người tạo là chủ nhóm."""
    b = _body()
    name = _text(b.get('name'), 100)
    if not name:
        return jsonify(error='Hãy nhập tên nhóm.'), 400
    picked = [u for u in (b.get('members') or []) if isinstance(u, str) and u != me][:200]
    valid = [u for u in picked if _active_user(u)]
    cid = _run("INSERT INTO th_convos(kind, name, owner) VALUES('group', %s, %s) RETURNING id",
               (name, me), 'one', True)['id']
    db = get_db()
    cur = db.cursor()
    cur.executemany('INSERT INTO th_convo_members(convo_id, username) VALUES(%s,%s) ON CONFLICT DO NOTHING',
                    [(cid, u) for u in [me] + valid])
    db.commit()
    cur.close()
    _notify(valid, f'{_name(me)} đã thêm bạn vào nhóm "{name}"', push=True)
    return jsonify(id=cid)


@teamhub_bp.route('/teamhub/api/groups/<int:cid>/members')
@_api()
def group_members(me, cid):
    if not _in_convo(cid, me) and session.get('role') != 'admin':
        return jsonify(error='Bạn không ở trong nhóm này.'), 403
    rows = _run('''SELECT cm.username, COALESCE(NULLIF(u.full_name,''), cm.username) AS name, COALESCE(u.branch,'') AS branch
                   FROM th_convo_members cm LEFT JOIN users u ON u.username=cm.username
                   WHERE cm.convo_id=%s ORDER BY name''', (cid,))
    return jsonify(members=[_ser(r) for r in rows])


@teamhub_bp.route('/teamhub/api/groups/<int:cid>/members/<act>', methods=['POST'])
@_api()
def group_member_act(me, cid, act):
    to = _text(_body().get('username'), 50)
    g = _run("SELECT name FROM th_convos WHERE id=%s AND kind='group'", (cid,), 'one')
    if not g or act not in ('add', 'remove') or not to:
        return jsonify(error='Không tìm thấy nhóm.'), 404
    if not (act == 'remove' and to == me) and not _can_manage(cid, me):   # ai cũng được tự rời nhóm
        return jsonify(error='Chỉ chủ nhóm hoặc Admin mới quản lý được thành viên.'), 403
    if act == 'add':
        if not _active_user(to):
            return jsonify(error='Không tìm thấy người này.'), 404
        _run('INSERT INTO th_convo_members(convo_id, username) VALUES(%s,%s) ON CONFLICT DO NOTHING', (cid, to), None, True)
        _notify([to], f'{_name(me)} đã thêm bạn vào nhóm "{g["name"]}"', push=True)
    else:
        _run('DELETE FROM th_convo_members WHERE convo_id=%s AND username=%s', (cid, to), None, True)
    return jsonify(ok=True)


@teamhub_bp.route('/teamhub/api/groups/<int:cid>/delete', methods=['POST'])
@_api()
def delete_group(me, cid):
    g = _run("SELECT name FROM th_convos WHERE id=%s AND kind='group'", (cid,), 'one')
    if not g:
        return jsonify(error='Không tìm thấy nhóm.'), 404
    if not _can_manage(cid, me):
        return jsonify(error='Chỉ chủ nhóm hoặc Admin mới xoá được nhóm.'), 403
    if g['name'] == DEFAULT_GROUP:
        return jsonify(error='Không xoá được nhóm mặc định.'), 400
    atts = _run('SELECT att FROM th_messages WHERE convo_id=%s AND att IS NOT NULL', (cid,))
    _run('DELETE FROM th_convos WHERE id=%s', (cid,), None, True)   # xoá luôn thành viên + tin nhắn (CASCADE)
    for a in atts:
        _rm(FILES_DIR, a['att'])
    return jsonify(ok=True)


@teamhub_bp.route('/teamhub/api/convos')
@_api()
def convos(me):
    rows = _run('''
        SELECT c.id, c.kind,
          CASE WHEN c.kind='group' THEN c.name ELSE
            (SELECT COALESCE(NULLIF(u.full_name,''), o.username) FROM th_convo_members o
             LEFT JOIN users u ON u.username=o.username
             WHERE o.convo_id=c.id AND o.username<>%s LIMIT 1) END AS name,
          (SELECT COALESCE(NULLIF(m.body,''), CASE WHEN m.att_kind='image' THEN '📷 Ảnh' ELSE '📎 '||COALESCE(m.att_name,'Tệp') END)
           FROM th_messages m WHERE m.convo_id=c.id ORDER BY m.id DESC LIMIT 1) AS last,
          (SELECT created_at FROM th_messages m WHERE m.convo_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_at,
          (SELECT COUNT(*) FROM th_messages m WHERE m.convo_id=c.id AND m.id>me.last_read AND m.sender<>%s) AS unread
        FROM th_convos c JOIN th_convo_members me ON me.convo_id=c.id AND me.username=%s
        ORDER BY last_at DESC NULLS LAST, c.id''', (me, me, me))
    return jsonify(convos=[_ser(r) for r in rows if r['name']])


@teamhub_bp.route('/teamhub/api/dm', methods=['POST'])
@_api()
def start_dm(me):
    to = _text(_body().get('to'), 50)
    ok = _run('SELECT 1 FROM th_members WHERE username=%s AND removed_at IS NULL', (to,), 'one') \
        or (_run('SELECT 1 FROM users WHERE username=%s', (to,), 'one') and not _run('SELECT 1 FROM th_members WHERE username=%s', (to,), 'one'))
    if not to or to == me or not ok:
        return jsonify(error='Không tìm thấy người này.'), 404
    r = _run('''SELECT c.id FROM th_convos c
                JOIN th_convo_members a ON a.convo_id=c.id AND a.username=%s
                JOIN th_convo_members b ON b.convo_id=c.id AND b.username=%s
                WHERE c.kind='dm' LIMIT 1''', (me, to), 'one')
    if r:
        return jsonify(id=r['id'])
    cid = _run("INSERT INTO th_convos(kind) VALUES('dm') RETURNING id", (), 'one', True)['id']
    _run('INSERT INTO th_convo_members(convo_id, username) VALUES(%s,%s),(%s,%s)', (cid, me, cid, to), None, True)
    return jsonify(id=cid)


@teamhub_bp.route('/teamhub/api/messages')
@_api()
def messages(me):
    cid, after = request.args.get('convo', 0, type=int), request.args.get('after', 0, type=int)
    if not _in_convo(cid, me):
        return jsonify(error='Bạn không ở trong cuộc trò chuyện này.'), 403
    rows = _run('''SELECT m.id, m.sender, COALESCE(NULLIF(u.full_name,''), m.sender) AS name, m.body, m.created_at, m.att, m.att_name, m.att_size, m.att_kind
                   FROM th_messages m LEFT JOIN users u ON u.username=m.sender
                   WHERE m.convo_id=%s AND m.id>%s ORDER BY m.id DESC LIMIT 100''', (cid, after))[::-1]
    if rows:
        _run('UPDATE th_convo_members SET last_read=GREATEST(last_read,%s) WHERE convo_id=%s AND username=%s',
             (rows[-1]['id'], cid, me), None, True)
    return jsonify(messages=[dict(_ser(r), mine=r['sender'] == me) for r in rows])


@teamhub_bp.route('/teamhub/api/messages', methods=['POST'])
@_api()
def send(me):
    b = _body()
    cid, text = int(b.get('convo') or 0), _text(b.get('body'), 2000)
    im, fl = request.files.get('image'), request.files.get('file')
    im = im if im and im.filename else None
    fl = fl if fl and fl.filename else None
    if (not text and not im and not fl) or not _in_convo(cid, me):
        return jsonify(error='Không gửi được tin nhắn.'), 400
    att = (None, None, None, None)
    try:
        if im:
            if not _img_ok(im):
                raise ValueError('Ảnh quá lớn, hãy chọn ảnh nhỏ hơn 12MB.')
            _disk_ok(request.content_length or 0)
            name = _save_img(im, FILES_DIR)
            att = (name, 'Ảnh.jpg', os.path.getsize(os.path.join(FILES_DIR, name)), 'image')
        elif fl:
            n, o, s = _save_file(fl)
            att = (n, o, s, 'file')
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception:
        return jsonify(error='Không đọc được ảnh. Hãy chọn file JPG hoặc PNG.'), 400
    r = _run('INSERT INTO th_messages(convo_id, sender, body, att, att_name, att_size, att_kind) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id',
             (cid, me, text, *att), 'one', True)
    _run('UPDATE th_convo_members SET last_read=%s WHERE convo_id=%s AND username=%s', (r['id'], cid, me), None, True)
    try:        # đẩy tin nhắn tới các thành viên còn lại (chưa bị gỡ khỏi TeamHub); lỗi không ảnh hưởng việc gửi tin
        rc = _run('''SELECT cm.username, c.kind, c.name FROM th_convo_members cm
                     JOIN th_convos c ON c.id = cm.convo_id
                     LEFT JOIN th_members m ON m.username = cm.username
                     WHERE cm.convo_id=%s AND cm.username<>%s AND m.removed_at IS NULL''', (cid, me))
        if rc:
            who = _name(me)
            title = who if rc[0]['kind'] == 'dm' else f"{who} · {rc[0]['name'] or 'Nhóm'}"
            body = text or ('📷 Đã gửi một ảnh' if att[3] == 'image' else '📎 Đã gửi một tệp')
            _push([x['username'] for x in rc], title, body, tag='th-convo-%s' % cid)
    except Exception:
        pass
    return jsonify(id=r['id'])


# ---------------------------------------------------------------- tải tệp (có kiểm tra quyền)
@teamhub_bp.route('/teamhub/file/<name>')
@_api()
def get_file(me, name):
    """Tệp của bài viết: mọi thành viên xem được. Tệp/ảnh trong chat: chỉ người trong cuộc trò chuyện."""
    if not FILE_NAME_RE.fullmatch(name):
        abort(404)
    kind = 'file'
    r = _run('SELECT file_name AS n FROM th_posts WHERE file_path=%s AND NOT deleted', (name,), 'one')
    if not r:
        r = _run('''SELECT m.att_name AS n, m.att_kind AS k FROM th_messages m
                    JOIN th_convo_members cm ON cm.convo_id=m.convo_id AND cm.username=%s
                    WHERE m.att=%s LIMIT 1''', (me, name), 'one')
        kind = (r['k'] if r else None) or 'file'
    if not r or not os.path.isfile(os.path.join(FILES_DIR, name)):
        abort(404)
    image = kind == 'image'
    mime = 'image/jpeg' if image else 'application/octet-stream'
    if ACCEL_PREFIX:   # nginx tự phát tệp, không giữ tiến trình Flask khi tải tệp lớn
        resp = Response(status=200)
        resp.headers['X-Accel-Redirect'] = ACCEL_PREFIX + name
        resp.headers['Content-Type'] = mime
        resp.headers['Content-Disposition'] = ('inline' if image else 'attachment') + "; filename*=UTF-8''" + quote(r['n'])
    else:
        resp = send_from_directory(FILES_DIR, name, as_attachment=not image, download_name=r['n'],
                                   mimetype=mime, conditional=True)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


# ---------------------------------------------------------------- thông báo
@teamhub_bp.route('/teamhub/api/notifs')
@_api()
def notifs(me):
    rows = _run('SELECT id, text, is_read, created_at FROM th_notifs WHERE username=%s ORDER BY id DESC LIMIT 40', (me,))
    return jsonify(notifs=[_ser(r) for r in rows])


@teamhub_bp.route('/teamhub/api/notifs/read', methods=['POST'])
@_api()
def notifs_read(me):
    nid = _body().get('id')
    if nid:
        _run('UPDATE th_notifs SET is_read=TRUE WHERE id=%s AND username=%s', (int(nid), me), None, True)
    else:
        _run('UPDATE th_notifs SET is_read=TRUE WHERE username=%s', (me,), None, True)
    return jsonify(ok=True)


# ---------------------------------------------------------------- Admin: gỡ / khôi phục tài khoản khỏi TeamHub
@teamhub_bp.route('/teamhub/api/admin/members')
@_api(admin=True)
def admin_members(me):
    rows = _run('''SELECT u.username, COALESCE(NULLIF(u.full_name,''), u.username) AS name, u.role,
                   COALESCE(u.branch,'') AS branch, (m.removed_at IS NOT NULL) AS removed
                   FROM users u LEFT JOIN th_members m ON m.username=u.username ORDER BY name''')
    return jsonify(members=[_ser(r) for r in rows])


@teamhub_bp.route('/teamhub/api/admin/members/<username>/<act>', methods=['POST'])
@_api(admin=True)
def admin_member_act(me, username, act):
    if act == 'remove' and not ALLOW_REMOVE_MEMBER:
        return jsonify(error='Chức năng gỡ tài khoản đang tạm tắt.'), 403
    if username == me or username == SUPER_ADMIN_USERNAME:
        return jsonify(error='Không thể gỡ tài khoản này.'), 400
    if not _run('SELECT 1 FROM users WHERE username=%s', (username,), 'one'):
        return jsonify(error='Không tìm thấy tài khoản.'), 404
    if act == 'remove':
        _run('''INSERT INTO th_members(username, removed_at, removed_by) VALUES(%s, NOW(), %s)
                ON CONFLICT (username) DO UPDATE SET removed_at=NOW(), removed_by=EXCLUDED.removed_by''',
             (username, me), None)
        _run('DELETE FROM th_convo_members WHERE username=%s', (username,), None, True)
    else:
        _run('UPDATE th_members SET removed_at=NULL, removed_by=NULL WHERE username=%s', (username,), None)
        _run('''INSERT INTO th_convo_members(convo_id, username)
                SELECT id, %s FROM th_convos WHERE kind='group' AND name=%s ON CONFLICT DO NOTHING''',
             (username, DEFAULT_GROUP), None, True)
    return jsonify(ok=True)