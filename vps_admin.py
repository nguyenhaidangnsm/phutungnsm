# -*- coding: utf-8 -*-
"""
TRANG QUẢN TRỊ MÁY ẢO (VPS) - chỉ admin. Blueprint độc lập, cùng kiểu gom_don_hang.py.

MÓC NỐI VÀO app.py (cạnh gom_don_hang_bp):
    from vps_admin import vps_admin_bp
    app.register_blueprint(vps_admin_bp)
Mở trang:  /admin/vps        Cài thư viện:  pip install psutil

NGUYÊN TẮC AN TOÀN
  - Không có ô gõ lệnh: mỗi nút chạy 1 lệnh CỐ ĐỊNH; tên dịch vụ/jail/file log lấy từ danh sách trắng bên dưới.
  - Restart / gỡ chặn IP / chạy backup: bắt buộc nhập "mật khẩu thao tác" (biến môi trường VPS_ACTION_PASSWORD),
    sai 5 lần thì khoá 5 phút. Mọi thao tác ghi vào file nhật ký (VPS_AUDIT_LOG).
  - Chỉ cho restart dịch vụ có "restart": True (mặc định chỉ ứng dụng, KHÔNG nginx/ssh).

QUYỀN HỆ THỐNG: user chạy app KHÔNG phải root. Tạo /etc/sudoers.d/namsuong-admin (sudo visudo -f ...):
    <user> ALL=(root) NOPASSWD: /usr/bin/systemctl restart <APP_UNIT>
    <user> ALL=(root) NOPASSWD: /usr/bin/fail2ban-client status *, /usr/bin/fail2ban-client set * unbanip *
  và thêm user vào nhóm: sudo usermod -aG adm,docker <user>   (đọc log nginx, xem container Postgres)
Biến môi trường (tuỳ chọn): VPS_APP_UNIT, VPS_PG_CONTAINER, VPS_BACKUP_DIR, VPS_BACKUP_SCRIPT, VPS_AUDIT_LOG.
"""
import hmac
import ipaddress
import logging
import os
import subprocess
import time
from datetime import datetime

import psutil
from flask import Blueprint, jsonify, request, session, render_template_string

vps_admin_bp = Blueprint('vps_admin', __name__)

APP_UNIT = os.getenv('VPS_APP_UNIT', 'nam-suong')
PG_CONTAINER = os.getenv('VPS_PG_CONTAINER', 'postgres')
BACKUP_DIR = os.path.expanduser(os.getenv('VPS_BACKUP_DIR', '~/backups'))
BACKUP_SCRIPT = os.path.expanduser(os.getenv('VPS_BACKUP_SCRIPT', '~/backup_db.sh'))
AUDIT_LOG = os.path.expanduser(os.getenv('VPS_AUDIT_LOG', '~/vps_admin_audit.log'))

SERVICES = {   # key -> (nhãn, systemd unit, cho restart?)
    'app': ('Ứng dụng (gunicorn)', APP_UNIT, True),
    'webhook': ('GitHub webhook', 'namsuong-webhook', False),
    'nginx': ('Nginx', 'nginx', False),
    'fail2ban': ('Fail2ban', 'fail2ban', False),
}
LOGS = {       # key -> (nhãn, loại, nguồn)
    'app': ('Ứng dụng', 'journal', APP_UNIT),
    'webhook': ('Webhook', 'journal', 'namsuong-webhook'),
    'nginx_error': ('Nginx lỗi', 'file', '/var/log/nginx/error.log'),
    'nginx_access': ('Nginx truy cập', 'file', '/var/log/nginx/access.log'),
    'fail2ban': ('Fail2ban', 'file', '/var/log/fail2ban.log'),
}
JAILS = ['sshd', 'nam-suong-login']

_audit = logging.getLogger('vps_admin_audit')
if not _audit.handlers:
    try:
        _h = logging.FileHandler(AUDIT_LOG, encoding='utf-8')
        _h.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        _audit.addHandler(_h)
    except OSError:
        _audit.addHandler(logging.StreamHandler())
    _audit.setLevel(logging.INFO)

_fails = {}    # user -> (số lần sai, thời điểm khoá đến)


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa
        return 1, str(e)


def _is_admin():
    return session.get('role') == 'admin'


def _deny():
    return jsonify({'error': 'Chỉ admin được dùng chức năng này.'}), 403


def _check_action(data):
    """Kiểm tra header chống CSRF + mật khẩu thao tác. Trả về (ok, response)."""
    if request.headers.get('X-Requested-With') != 'fetch':
        return False, (jsonify({'error': 'Yêu cầu không hợp lệ.'}), 400)
    user = str(session.get('user') or '')
    cnt, until = _fails.get(user, (0, 0))
    if until > time.time():
        return False, (jsonify({'error': 'Nhập sai quá nhiều lần, thử lại sau vài phút.'}), 429)
    expected = os.getenv('VPS_ACTION_PASSWORD', '')
    given = str((data or {}).get('password') or '')
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        cnt += 1
        _fails[user] = (cnt, time.time() + 300 if cnt >= 5 else 0)
        _audit.info('SAI MAT KHAU thao tac user=%s ip=%s', user, request.remote_addr)
        msg = 'Chưa cấu hình VPS_ACTION_PASSWORD.' if not expected else 'Mật khẩu thao tác không đúng.'
        return False, (jsonify({'error': msg}), 403)
    _fails.pop(user, None)
    return True, None


def _unit_state(unit):
    return _run(['systemctl', 'is-active', unit])[1] or 'unknown'


def _latest_backup():
    try:
        files = [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)]
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            return None
        f = max(files, key=os.path.getmtime)
        return {'name': os.path.basename(f), 'size_mb': round(os.path.getsize(f) / 1048576, 1),
                'time': datetime.fromtimestamp(os.path.getmtime(f)).strftime('%d/%m/%Y %H:%M')}
    except OSError:
        return None


@vps_admin_bp.route('/api/admin/vps/status')
def api_status():
    if not _is_admin():
        return _deny()
    vm, du = psutil.virtual_memory(), psutil.disk_usage('/')
    services = [{'key': k, 'label': v[0], 'state': _unit_state(v[1]), 'can_restart': v[2]}
                for k, v in SERVICES.items()]
    pg = _run(['docker', 'inspect', '-f', '{{.State.Status}}', PG_CONTAINER])
    services.append({'key': 'postgres', 'label': 'Postgres (Docker)',
                     'state': pg[1] if pg[0] == 0 else 'unknown', 'can_restart': False})
    return jsonify({
        'cpu': psutil.cpu_percent(interval=0.3),
        'ram': {'percent': vm.percent, 'used_gb': round(vm.used / 1e9, 1), 'total_gb': round(vm.total / 1e9, 1)},
        'disk': {'percent': du.percent, 'used_gb': round(du.used / 1e9, 1), 'total_gb': round(du.total / 1e9, 1)},
        'uptime_h': round((time.time() - psutil.boot_time()) / 3600, 1),
        'load': [round(x, 2) for x in os.getloadavg()],
        'services': services,
        'backup': {'latest': _latest_backup(), 'running': _run(['pgrep', '-f', os.path.basename(BACKUP_SCRIPT)])[0] == 0},
    })


@vps_admin_bp.route('/api/admin/vps/logs')
def api_logs():
    if not _is_admin():
        return _deny()
    key = request.args.get('key', '')
    if key not in LOGS:
        return jsonify({'error': 'Log không hợp lệ.'}), 400
    try:
        n = max(20, min(500, int(request.args.get('lines', 200))))
    except ValueError:
        n = 200
    _, kind, src = LOGS[key]
    cmd = ['journalctl', '-u', src, '-n', str(n), '--no-pager'] if kind == 'journal' else ['tail', '-n', str(n), src]
    rc, out = _run(cmd)
    return jsonify({'text': out, 'ok': rc == 0})


@vps_admin_bp.route('/api/admin/vps/bans')
def api_bans():
    if not _is_admin():
        return _deny()
    res = []
    for jail in JAILS:
        rc, out = _run(['sudo', '-n', 'fail2ban-client', 'status', jail])
        ips = []
        for line in out.splitlines():
            if 'Banned IP list:' in line:
                ips = line.split(':', 1)[1].split()
        res.append({'jail': jail, 'ok': rc == 0, 'ips': ips})
    return jsonify({'jails': res})


@vps_admin_bp.route('/api/admin/vps/unban', methods=['POST'])
def api_unban():
    if not _is_admin():
        return _deny()
    data = request.get_json(silent=True) or {}
    ok, resp = _check_action(data)
    if not ok:
        return resp
    jail = data.get('jail')
    try:
        ip = str(ipaddress.ip_address(str(data.get('ip', '')).strip()))
    except ValueError:
        return jsonify({'error': 'IP không hợp lệ.'}), 400
    if jail not in JAILS:
        return jsonify({'error': 'Jail không hợp lệ.'}), 400
    rc, out = _run(['sudo', '-n', 'fail2ban-client', 'set', jail, 'unbanip', ip])
    _audit.info('UNBAN user=%s jail=%s ip=%s rc=%s', session.get('user'), jail, ip, rc)
    return jsonify({'success': rc == 0, 'message': out})


@vps_admin_bp.route('/api/admin/vps/restart', methods=['POST'])
def api_restart():
    if not _is_admin():
        return _deny()
    data = request.get_json(silent=True) or {}
    ok, resp = _check_action(data)
    if not ok:
        return resp
    svc = SERVICES.get(data.get('key'))
    if not svc or not svc[2]:
        return jsonify({'error': 'Dịch vụ này không được phép restart.'}), 400
    _audit.info('RESTART user=%s unit=%s ip=%s', session.get('user'), svc[1], request.remote_addr)
    # Đợi 1s để trả lời xong cho trình duyệt rồi mới restart (restart app sẽ ngắt chính request này).
    subprocess.Popen(['sh', '-c', 'sleep 1; sudo -n systemctl restart "$0"', svc[1]], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({'success': True, 'message': 'Đang khởi động lại, trang sẽ tải lại sau vài giây.'})


@vps_admin_bp.route('/api/admin/vps/backup', methods=['POST'])
def api_backup():
    if not _is_admin():
        return _deny()
    data = request.get_json(silent=True) or {}
    ok, resp = _check_action(data)
    if not ok:
        return resp
    if _run(['pgrep', '-f', os.path.basename(BACKUP_SCRIPT)])[0] == 0:
        return jsonify({'error': 'Backup đang chạy.'}), 409
    if not os.path.isfile(BACKUP_SCRIPT):
        return jsonify({'error': 'Không thấy file backup: ' + BACKUP_SCRIPT}), 400
    _audit.info('BACKUP user=%s ip=%s', session.get('user'), request.remote_addr)
    subprocess.Popen(['bash', BACKUP_SCRIPT], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({'success': True, 'message': 'Đã bắt đầu chạy backup.'})


@vps_admin_bp.route('/admin/vps')
def page():
    if not _is_admin():
        return 'Chỉ admin được truy cập.', 403
    return render_template_string(PAGE, log_opts=[(k, v[0]) for k, v in LOGS.items()])


PAGE = r'''<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quản trị máy ảo</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
<style>body{background:#f4f6fb}pre#log{background:#0f172a;color:#cbd5e1;max-height:420px;overflow:auto;font-size:12px;padding:12px;border-radius:8px}
.metric{font-size:1.6rem;font-weight:700}</style></head>
<body><div class="container py-4">
<div class="d-flex justify-content-between align-items-center mb-3">
  <h4 class="fw-bold mb-0"><i class="bi bi-hdd-network me-2"></i>Quản trị máy ảo</h4>
  <a href="/" class="btn btn-sm btn-outline-secondary">Về trang chính</a></div>
<div class="row g-3 mb-3" id="metrics"></div>
<div class="row g-3">
 <div class="col-lg-6"><div class="card shadow-sm"><div class="card-header fw-semibold">Dịch vụ</div>
  <ul class="list-group list-group-flush" id="services"></ul></div></div>
 <div class="col-lg-6"><div class="card shadow-sm mb-3"><div class="card-header fw-semibold">Backup</div>
  <div class="card-body d-flex justify-content-between align-items-center"><div id="backup"></div>
  <button class="btn btn-primary btn-sm" onclick="act('/api/admin/vps/backup',{},'Chạy backup ngay?')">Chạy backup ngay</button></div></div>
  <div class="card shadow-sm"><div class="card-header fw-semibold">IP bị fail2ban chặn</div>
  <div class="card-body" id="bans"></div></div></div>
 <div class="col-12"><div class="card shadow-sm"><div class="card-header d-flex gap-2 align-items-center">
  <span class="fw-semibold">Log</span>
  <select id="logKey" class="form-select form-select-sm w-auto" onchange="loadLog()">
   {% for k,l in log_opts %}<option value="{{k}}">{{l}}</option>{% endfor %}</select>
  <select id="logLines" class="form-select form-select-sm w-auto" onchange="loadLog()">
   <option>100</option><option selected>200</option><option>500</option></select>
  <button class="btn btn-sm btn-outline-secondary ms-auto" onclick="loadLog()"><i class="bi bi-arrow-clockwise"></i></button></div>
  <div class="card-body"><pre id="log" class="mb-0">Đang tải...</pre></div></div></div>
</div></div>
<div class="modal fade" id="pwModal" tabindex="-1"><div class="modal-dialog modal-dialog-centered modal-sm"><div class="modal-content">
 <div class="modal-body"><div id="pwMsg" class="mb-2 fw-semibold"></div>
  <input type="password" id="pwInput" class="form-control" placeholder="Mật khẩu thao tác" autocomplete="off">
  <div class="text-danger small mt-2" id="pwErr"></div></div>
 <div class="modal-footer"><button class="btn btn-light btn-sm" data-bs-dismiss="modal">Huỷ</button>
  <button class="btn btn-danger btn-sm" id="pwOk">Xác nhận</button></div></div></div></div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const H={'Content-Type':'application/json','X-Requested-With':'fetch'};
const pct=(l,p,sub)=>`<div class="col-6 col-lg-3"><div class="card shadow-sm"><div class="card-body"><div class="text-muted small">${l}</div>
 <div class="metric">${p}</div><div class="small text-muted">${sub||''}</div></div></div></div>`;
async function loadStatus(){
 try{const d=await (await fetch('/api/admin/vps/status')).json();
  $('metrics').innerHTML=pct('CPU',d.cpu+'%','Load '+d.load.join(' / '))+pct('RAM',d.ram.percent+'%',d.ram.used_gb+'/'+d.ram.total_gb+' GB')
   +pct('Ổ đĩa',d.disk.percent+'%',d.disk.used_gb+'/'+d.disk.total_gb+' GB')+pct('Uptime',d.uptime_h+' giờ','');
  $('services').innerHTML=d.services.map(s=>`<li class="list-group-item d-flex justify-content-between align-items-center">
   <span>${esc(s.label)}</span><span>
   <span class="badge ${s.state==='active'||s.state==='running'?'bg-success':'bg-danger'}">${esc(s.state)}</span>
   ${s.can_restart?`<button class="btn btn-sm btn-outline-danger ms-2" onclick="act('/api/admin/vps/restart',{key:'${esc(s.key)}'},'Khởi động lại ${esc(s.label)}?',true)">Restart</button>`:''}</span></li>`).join('');
  const b=d.backup;$('backup').innerHTML=(b.latest?`<div>${esc(b.latest.name)}</div><div class="small text-muted">${esc(b.latest.time)} - ${b.latest.size_mb} MB</div>`:'<div class="text-muted">Chưa thấy file backup</div>')
   +(b.running?'<span class="badge bg-warning text-dark">Đang chạy</span>':'');
 }catch(e){}}
async function loadBans(){
 const d=await (await fetch('/api/admin/vps/bans')).json();
 $('bans').innerHTML=d.jails.map(j=>`<div class="mb-2"><div class="fw-semibold small">${esc(j.jail)}</div>`+(!j.ok?'<div class="text-danger small">Không đọc được (kiểm tra sudoers)</div>'
  :(j.ips.length?j.ips.map(ip=>`<span class="badge bg-light text-dark border me-1">${esc(ip)}
   <a href="#" class="text-danger ms-1" onclick="act('/api/admin/vps/unban',{jail:'${esc(j.jail)}',ip:'${esc(ip)}'},'Gỡ chặn ${esc(ip)}?');return false">&times;</a></span>`).join(''):'<span class="text-muted small">Không có IP nào</span>'))+'</div>').join('');}
async function loadLog(){
 const d=await (await fetch(`/api/admin/vps/logs?key=${$('logKey').value}&lines=${$('logLines').value}`)).json();
 const el=$('log');el.textContent=d.text||d.error||'(trống)';el.scrollTop=el.scrollHeight;}
let pend=null;const modal=new bootstrap.Modal($('pwModal'));
function act(url,body,msg,reload){pend={url,body,reload};$('pwMsg').textContent=msg;$('pwInput').value='';$('pwErr').textContent='';modal.show();setTimeout(()=>$('pwInput').focus(),300);}
$('pwInput').addEventListener('keydown',e=>{if(e.key==='Enter')$('pwOk').click()});
$('pwOk').onclick=async()=>{
 const r=await fetch(pend.url,{method:'POST',headers:H,body:JSON.stringify({...pend.body,password:$('pwInput').value})});
 const d=await r.json().catch(()=>({}));
 if(r.ok&&d.success!==false){modal.hide();alert(d.message||'Xong');if(pend.reload)setTimeout(()=>location.reload(),4000);else{loadStatus();loadBans();}}
 else $('pwErr').textContent=d.error||d.message||'Thao tác thất bại.';};
loadStatus();loadBans();loadLog();setInterval(loadStatus,10000);
</script></body></html>'''
