# test webhook auto deploy
import os
import io
import time
import json
import math
import re
import orjson
import traceback
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, g, send_file
import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor, execute_values
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
import threading
import zlib
import ssl
import urllib.request
import urllib.error
from PIL import Image, ImageOps

# Dùng bộ chứng chỉ gốc (CA) của certifi để xác thực HTTPS khi gọi ra ngoài
# (vd. webhook Google Sheets) - một số máy chủ (đặc biệt là Windows, hoặc
# môi trường Python thiếu/không đồng bộ kho chứng chỉ hệ thống) sẽ báo lỗi
# "CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate" nếu
# dùng context SSL mặc định của urllib. Nếu thiếu gói certifi (chưa
# `pip install certifi`), tự động lùi về context mặc định của hệ thống.
try:
    import certifi
    _HTTPS_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _HTTPS_SSL_CONTEXT = ssl.create_default_context()

# Nạp các biến bảo mật từ file .env
load_dotenv()


def _sanitize_for_json(obj):
    """orjson tuân thủ ĐÚNG chuẩn JSON (RFC 8259): không cho phép NaN/
    Infinity/-Infinity như module json chuẩn của Python vẫn hay "dễ dãi"
    chấp nhận. Dữ liệu ở đây (từ pandas) rất hay có NaN cho các ô trống, nên
    nếu không xử lý trước, orjson.dumps() sẽ ném lỗi ngay khi gặp NaN đầu
    tiên (không đi qua được 'default' - đó là callback cho KIỂU dữ liệu lạ,
    không phải cho GIÁ TRỊ đặc biệt như NaN của kiểu float đã biết).
    Hàm này duyệt đệ quy dict/list, đổi NaN/Infinity -> None trước khi đưa
    cho orjson, để dữ liệu MỚI ghi ra từ giờ luôn là JSON hợp lệ."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, (float, np.floating)):
        try:
            if math.isnan(obj) or math.isinf(obj):
                return None
        except TypeError:
            pass
        return float(obj)
    return obj


def _orjson_default(obj):
    """orjson tự serialize sẵn hầu hết kiểu dữ liệu Python gốc kể cả
    datetime.datetime/date, nhưng KHÔNG biết cách xử lý một số kiểu đặc thù
    của pandas/numpy hay xuất hiện trong dữ liệu ở đây (pd.Timestamp,
    numpy.int64...). Hàm này chỉ được gọi cho đúng những trường hợp orjson
    "bó tay" về KIỂU dữ liệu, đóng vai trò tương đương default=str của
    json.dumps() trước đây. Giá trị NaN/Infinity đã được _sanitize_for_json()
    xử lý từ trước nên không cần lo ở đây nữa."""
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return str(obj)


def dumps_json(obj):
    """Thay cho json.dumps(obj, ensure_ascii=False, default=str). orjson
    nhanh hơn json chuẩn của Python khoảng 3-10 lần cho cả dump lẫn load -
    đáng chú ý vì các cột JSON ở đây (ds_po_json, receipt_json,
    data_json trong bảng cache) có thể chứa tới hàng chục nghìn dòng.
    orjson trả về bytes nên cần decode('utf-8') trước khi lưu vào cột TEXT."""
    return orjson.dumps(_sanitize_for_json(obj), default=_orjson_default).decode('utf-8')


def loads_json(s):
    """Thay cho json.loads(s). Có fallback về json chuẩn: dữ liệu đã lưu
    trong DB TỪ TRƯỚC khi đổi sang orjson có thể chứa literal NaN (json
    chuẩn cho ghi ra, orjson thì không) - nếu orjson đọc thất bại, thử lại
    bằng json chuẩn (vẫn đọc được NaN) thay vì crash, để không cần phải
    migrate lại toàn bộ dữ liệu cũ đang có trong Postgres. Từ nay các lượt
    ghi mới đều đã sạch NaN (nhờ dumps_json ở trên) nên fallback này sẽ
    ngày càng ít được dùng tới theo thời gian, chỉ còn hữu ích cho dữ liệu
    cũ chưa được ghi đè lại."""
    try:
        return orjson.loads(s)
    except orjson.JSONDecodeError:
        return json.loads(s)


app = Flask(__name__)

# File tĩnh (font .otf, logo...) trong thư mục /static gần như không đổi
# giữa các lần deploy - cho trình duyệt CACHE 1 NĂM thay vì mặc định của
# Flask (12 giờ), để những lần load trang SAU không phải tải lại font/logo
# nữa (chỉ tải 1 lần duy nhất). Nếu sau này có thay logo/font, đổi tên file
# (vd: thêm hậu tố phiên bản) để trình duyệt biết mà tải lại bản mới.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000

# ---------------------------------------------------------------------------
# CACHE TRONG BỘ NHỚ cho /api/inventory - kết quả trả về GIỐNG HỆT NHAU cho
# MỌI user (admin lẫn store đều xem toàn bộ hệ thống, không lọc theo quyền),
# nhưng việc tính lại (pivot ~30 nghìn dòng + phân loại tần suất bán) tốn khá
# nhiều CPU. Vì dữ liệu chỉ thay đổi khi admin import (tồn kho / giá bán /
# xuất bán), ta cache lại body JSON đã dựng sẵn (bytes, orjson - nhanh hơn
# json.dumps chuẩn của jsonify) và chỉ tính lại khi có 1 trong 3 loại import
# đó chạy (xem invalidate_inventory_cache() được gọi ở cuối mỗi endpoint
# import liên quan). Dùng threading.Lock để an toàn khi nhiều request cùng
# lúc trong lúc cache đang được dựng lại.
_inventory_cache_lock = threading.Lock()
_inventory_cache = {'body': None, 'version': 0}


# "Đời" (epoch) của dữ liệu tồn kho/giá/xuất bán - tăng lên mỗi khi
# invalidate_inventory_cache() chạy. Dùng làm 1 phần của "chữ ký" cache cho
# /api/locations, /api/transfer/list và thống kê tần suất bán (bên dưới): cache
# nào lưu kèm 1 epoch cũ sẽ tự bị coi là hết hạn.
_inventory_epoch = 0
# Tương tự, tăng lên sau các request ghi KHÔNG thuộc nhóm vị trí/luân chuyển
# (upload, hàng hư hỏng, xoá dữ liệu cửa hàng...) - xem hook
# _bump_cache_epochs_on_write() bên dưới - còn _transfer_epoch tăng sau MỌI
# request ghi liên quan luân chuyển. Đảm bảo cache KHÔNG BAO GIỜ cũ hơn 1 thao
# tác ghi đã hoàn tất, kể cả những thao tác không đổi updated_at (vd: xin xoá
# phiếu).
_write_epoch = 0
_transfer_epoch = 0


def invalidate_inventory_cache():
    """Gọi ngay sau khi commit thành công ở bất kỳ chỗ nào ghi vào
    inventory_items / part_prices / sales_export_items, để lần gọi
    /api/inventory tiếp theo tính lại dữ liệu mới thay vì trả cache cũ."""
    global _inventory_epoch
    with _inventory_cache_lock:
        _inventory_cache['body'] = None
        _inventory_epoch += 1


# ---------------------------------------------------------------------------
# CACHE PHẢN HỒI (response) cho các API "nặng về egress" - /api/locations và
# /api/transfer/list (nhánh mặc định). Trước đây MỖI lần gọi đều đọc lại hàng
# chục nghìn dòng từ Supabase (tính vào hạn mức băng thông/egress), và
# frontend gọi lại rất nhiều lần (mỗi lần mở tab, lưu 1 vị trí, 1 người nào đó
# đổi phiếu luân chuyển ở BẤT KỲ cửa hàng nào...). Giờ: mỗi lần gọi chỉ chạy
# 1 câu SQL nhỏ để lấy "chữ ký" (COUNT + MAX(updated_at) ...); chữ ký không
# đổi thì trả lại body đã dựng sẵn (nén zlib để tiết kiệm RAM), KHÔNG đọc lại
# dữ liệu lớn từ DB. Kết quả trả về y hệt như trước - chỉ đọc DB ít đi.
# ---------------------------------------------------------------------------
_resp_cache = {}
_resp_cache_lock = threading.Lock()
_RESP_CACHE_MAX_KEYS = 40


def _resp_cache_get(key, sig):
    with _resp_cache_lock:
        entry = _resp_cache.get(key)
    if entry is not None and entry[0] == sig:
        return zlib.decompress(entry[1])
    return None


def _resp_cache_put(key, sig, body_bytes):
    packed = zlib.compress(body_bytes, 3)
    with _resp_cache_lock:
        if key not in _resp_cache and len(_resp_cache) >= _RESP_CACHE_MAX_KEYS:
            _resp_cache.clear()  # chặn phình RAM nếu có ai thử nhiều tham số lạ
        _resp_cache[key] = (sig, packed)


# SECRET_KEY bắt buộc phải có trong biến môi trường (không dùng giá trị mặc
# định hardcode trong code nữa — nếu thiếu, ứng dụng sẽ báo lỗi ngay khi
# khởi động thay vì chạy với 1 secret key ai cũng đọc được từ source code).
_secret_key = os.environ.get('FLASK_SECRET_KEY')
if not _secret_key:
    raise RuntimeError(
        "Thiếu biến môi trường FLASK_SECRET_KEY. Hãy đặt biến này trên Render "
        "(Settings > Environment) với 1 chuỗi ngẫu nhiên dài, ví dụ tạo bằng: "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )
app.secret_key = _secret_key

# Render (và hầu hết các nền tảng hosting) chạy app đứng sau 1 reverse proxy,
# nên request.remote_addr mặc định sẽ trả về IP nội bộ của proxy chứ KHÔNG
# PHẢI IP thật của người dùng. ProxyFix đọc header "X-Forwarded-For" (do
# proxy của Render tự thêm, đáng tin cậy - không phải do client tự gửi giả
# được) để request.remote_addr trả về đúng IP thật. x_for=1 nghĩa là chỉ tin
# 1 lớp proxy phía trước (đúng với hạ tầng của Render).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# Server host (Render...) thường chạy theo giờ UTC, không phải giờ Việt Nam.
# Nếu dùng datetime.now() thẳng thì các mốc "Cập nhật lần cuối" sẽ bị lệch
# -7 giờ so với giờ admin thực tế bấm nút. Hàm này luôn trả về giờ VN (naive,
# không kèm tzinfo) để lưu thẳng vào cột TIMESTAMP (không có time zone) của
# Postgres mà không bị Postgres tự quy đổi lại theo timezone của session.
VN_TZ = ZoneInfo('Asia/Ho_Chi_Minh')


def vn_now():
    return datetime.now(VN_TZ).replace(tzinfo=None)


# Theo dõi "đang online" TRONG RAM (không ghi DB mỗi request để khỏi tốn tài
# nguyên) - dict: username -> {'ip', 'last_seen' (datetime), 'user_agent'}.
# Mất dữ liệu khi restart app, nhưng chấp nhận được vì đây chỉ là thông tin
# tức thời ("ai đang online ngay lúc này"), không phải lịch sử cần lưu lâu
# dài (lịch sử đăng nhập thật đã có bảng login_log ở trên).
_online_users = {}
_online_users_lock = threading.Lock()
ONLINE_THRESHOLD_SECONDS = 120  # không thấy hoạt động > 2 phút -> coi là offline


@app.before_request
def _track_online_user():
    # Bỏ qua request tài nguyên tĩnh (ảnh, font, css/js) - không phải hoạt
    # động thật của user, chỉ tốn thêm 1 lần lock/ghi dict không cần thiết.
    if request.path.startswith('/static/'):
        return
    username = session.get('user')
    if not username:
        return
    with _online_users_lock:
        _online_users[username] = {
            'ip': request.remote_addr,
            'last_seen': vn_now(),
            'user_agent': request.headers.get('User-Agent', ''),
        }


# Các request ghi KHÔNG làm cache phản hồi ở trên bị cũ: (đăng nhập/đăng xuất,
# đánh dấu đã đọc thông báo, đổi mật khẩu - không đụng tới dữ liệu tồn
# kho/vị trí/phiếu luân chuyển).
_WRITE_EPOCH_IGNORED_PREFIXES = ('/api/notifications/', '/api/change-password', '/login', '/logout')


@app.after_request
def _bump_cache_epochs_on_write(resp):
    global _write_epoch, _transfer_epoch
    try:
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return resp
        path = request.path
        if path.startswith(_WRITE_EPOCH_IGNORED_PREFIXES):
            return resp
        if path.startswith('/api/locations'):
            # Mọi thao tác ghi vị trí đều đổi COUNT(*) hoặc MAX(updated_at) của
            # part_locations -> "chữ ký" của /api/locations tự đổi, không cần
            # tăng epoch (giữ được cache của các cửa hàng KHÁC không liên quan).
            return resp
        with _resp_cache_lock:
            _transfer_epoch += 1
            if not path.startswith(('/api/transfer', '/api/admin/transfer')):
                _write_epoch += 1  # request ghi lạ/khác: cứ coi như có thể ảnh hưởng mọi cache
    except Exception:
        pass
    return resp


# Tên thứ trong tuần bằng tiếng Việt (dùng cho phần Lịch Sử Tải Lên) -
# datetime.weekday(): Thứ Hai = 0 ... Chủ Nhật = 6.
_WEEKDAY_VI = ['Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy', 'Chủ Nhật']


def format_vi_datetime(dt):
    """Định dạng datetime thành chuỗi tiếng Việt, ví dụ:
    'Thứ Bảy, 05/09/2026 14:10'. Trả về None nếu dt rỗng."""
    if dt is None:
        return None
    return f"{_WEEKDAY_VI[dt.weekday()]}, {dt.strftime('%d/%m/%Y %H:%M')}"

# Khu vực (tỉnh/thành) mà từng cửa hàng trực thuộc - dùng để gộp báo cáo
# luân chuyển nội bộ theo khu vực (xem /api/admin/transfer/region-report)
# thay vì phải xem từng cặp cửa hàng lẻ tẻ. NS1 & NS3 cùng ở Cà Mau, NS5 &
# NSM1 cùng ở Bạc Liêu nên 2 cửa hàng đó sẽ được gộp chung 1 khu vực.
# Tên đăng nhập của tài khoản admin GỐC, quyền cao nhất hệ thống - ĐÂY LÀ
# TÀI KHOẢN DUY NHẤT được phép đổi mật khẩu của bất kỳ ai qua trang Quản Lý
# User, kể cả mật khẩu của các tài khoản admin khác. Các tài khoản admin
# khác (không phải tài khoản này) thì KHÔNG được đổi mật khẩu của bất kỳ
# tài khoản admin nào (kể cả lẫn nhau) - chỉ tự đổi được mật khẩu của chính
# mình qua /api/change-password.
SUPER_ADMIN_USERNAME = 'admin'

# Quy tắc bảo mật: KHÔNG cho phép reset mật khẩu của bất kỳ tài khoản
# quyền 'admin' nào qua trang Quản Lý User (route /api/admin/users) - áp
# dụng cho MỌI admin, kể cả giữa các admin với nhau. 1 tài khoản admin chỉ
# tự đổi được mật khẩu của chính mình qua /api/change-password (bắt buộc
# đăng nhập đúng tài khoản đó + nhập đúng mật khẩu hiện tại). Xem chi tiết
# tại route admin_users() bên dưới.

STORE_REGIONS = {
    'NS1': 'Cà Mau',
    'NS3': 'Cà Mau',
    'NS2': 'Hoà Bình',
    'NS4': 'Hộ Phòng',
    'NS5': 'Bạc Liêu',
    'NSM1': 'Bạc Liêu',
}


def _store_region(store_code):
    """Trả về tên khu vực của 1 mã cửa hàng - nếu mã cửa hàng lạ (chưa có
    trong STORE_REGIONS) thì dùng luôn mã đó làm tên khu vực, để không bao
    giờ vỡ báo cáo khi có cửa hàng mới chưa kịp gán khu vực."""
    return STORE_REGIONS.get(store_code, store_code)


# Cấu hình cookie phiên đăng nhập an toàn hơn:
# - SECURE: chỉ gửi cookie qua HTTPS (Render luôn phục vụ qua HTTPS)
# - HTTPONLY: JavaScript phía trình duyệt không đọc được cookie (chống XSS đánh cắp session)
# - SAMESITE=Lax: hạn chế cookie bị gửi kèm trong các request từ trang khác (chống CSRF cơ bản)
app.config.update(
    #SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', 'True') == 'True',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=200 * 1024 * 1024,  # Giới hạn upload tối đa 200MB / request
    # (200MB thay vì 50MB trước đây - file "Bảng giá bộ áo xe Honda" chứa
    # >700 ảnh xe nhúng trực tiếp nên nặng ~115MB, vượt xa 50MB cũ. Các
    # upload khác trong app (tồn kho, xuất bán...) chỉ vài trăm KB-vài MB
    # nên nới giới hạn chung này không ảnh hưởng gì tới các tính năng đó.)
)

# Nén response (JSON, HTML) bằng gzip để giảm dung lượng truyền tải -> tải nhanh hơn
Compress(app)

# Giới hạn số lần thử đăng nhập để chống brute-force mật khẩu
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")


@app.errorhandler(413)
def handle_file_too_large(e):
    """Mặc định Flask trả về trang lỗi HTML khi vượt MAX_CONTENT_LENGTH,
    khiến frontend (đang gọi res.json()) hiểu nhầm thành mất kết nối mạng.
    Trả JSON để hiển thị đúng thông báo cho người dùng."""
    limit_mb = app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)
    return jsonify({'error': f'File tải lên vượt quá giới hạn cho phép ({limit_mb}MB). Vui lòng chia nhỏ file trước khi tải lên.'}), 413


@app.errorhandler(500)
def handle_internal_error(e):
    """Bắt các lỗi không lường trước (ví dụ mất kết nối DB giữa chừng) để
    luôn trả về JSON thay vì trang lỗi HTML mặc định của Flask/Werkzeug."""
    return jsonify({'error': 'Lỗi máy chủ nội bộ. Vui lòng thử lại sau ít phút.'}), 500

# Lấy chuỗi kết nối bảo mật từ biến môi trường
DATABASE_URL = os.environ.get('DATABASE_URL')

# Connection pool tới Postgres/Neon: tái sử dụng kết nối thay vì mở kết nối
# TCP/TLS mới cho MỖI request (việc mở mới rất tốn thời gian, đặc biệt với
# Neon/serverless Postgres) -> tăng tốc đáng kể cho mọi API.
_db_pool = None


def _get_pool():
    """Tạo connection pool LƯỜI BIẾNG (lazy) - chỉ tạo khi có request đầu
    tiên thật sự cần dùng DB, thay vì tạo ngay lúc import module.
    Lý do: Neon (Postgres serverless) có thể đang "ngủ" (autosuspend) khi
    Render khởi động lại app; nếu tạo pool ngay lúc import mà Neon chưa kịp
    "thức dậy", tiến trình Flask có thể bị treo/timeout ngay từ bước khởi
    động, khiến Render coi app là "không phản hồi" và khởi động lại liên
    tục - biểu hiện ra ngoài là các lỗi kết nối chập chờn."""
    global _db_pool
    if _db_pool is None:
        # Số 10 này là max PER WORKER. Trên gói Render Free (512MB RAM, CPU
        # chia sẻ rất yếu), nên giữ --workers 1 (xem giải thích ở Start
        # Command) - vì vậy tổng kết nối tối đa lý thuyết vẫn là 10, không
        # cần nhân thêm. Nếu sau này nâng cấp gói và tăng số --workers, nhớ
        # NHÂN số này với số --workers để không vượt giới hạn kết nối của
        # Neon (vượt sẽ gây lỗi "too many connections").
        # Dùng ThreadedConnectionPool thay vì SimpleConnectionPool: theo tài
        # liệu chính thức của psycopg2, SimpleConnectionPool KHÔNG an toàn
        # khi có nhiều luồng (thread) cùng gọi getconn()/putconn() đồng
        # thời - nếu gunicorn chạy với nhiều thread (hoặc do 1 lỗi khác dẫn
        # tới việc bị gọi song song), có thể xảy ra tình huống 2 request
        # khác nhau cùng được cấp PHÁT TRÙNG 1 connection, dẫn tới 2 luồng
        # cùng đọc/ghi trên chung 1 socket TLS - biểu hiện ra ngoài đúng
        # kiểu lỗi khó hiểu như "SSL error: decryption failed or bad record
        # mac" (dữ liệu 2 luồng bị xen kẽ làm hỏng khung mã hoá TLS).
        # ThreadedConnectionPool dùng lock nội bộ để đảm bảo an toàn, chi
        # phí thêm không đáng kể so với rủi ro trên.
        _db_pool = pg_pool.ThreadedConnectionPool(1, 10, DATABASE_URL, cursor_factory=RealDictCursor)
    return _db_pool

# Số ngày lưu trữ dữ liệu "Chi tiết PO" trước khi tự động dọn dẹp
PO_DETAIL_RETENTION_DAYS = 120

# Số ngày lưu trữ "Lịch Sử Tải Lên" (bảng upload_log - ghi lại mỗi lượt tải
# file Danh sách PO / Chi tiết PO / Chi tiết nhận hàng) trước khi tự động
# dọn dẹp. Đây CHỈ là log để hiển thị lịch sử, không dùng để tính toán (xem
# ghi chú ở init_db()) nên xoá đi không ảnh hưởng tới dữ liệu Danh sách PO,
# Chi tiết PO hay Chi tiết nhận hàng đang có (các bảng đó có retention/ghi
# đè riêng của chúng).
UPLOAD_LOG_RETENTION_DAYS = 7

# Số ngày lưu trữ "Lịch Sử Đăng Nhập" (bảng login_log) trước khi tự động dọn
# dẹp - giống lý do với upload_log: đây chỉ là log hiển thị, không phải dữ
# liệu nghiệp vụ, nếu không dọn sẽ phình to vô hạn theo thời gian (mỗi lượt
# đăng nhập của mọi user đều ghi 1 dòng).
LOGIN_LOG_RETENTION_DAYS = 90

# Nhân viên phụ tùng theo cửa hàng — dùng khi tạo phiếu luân chuyển nội bộ
# (nhân viên xin) và khi đồng ý phiếu (nhân viên xác nhận).
STORE_EMPLOYEES = {
    'NS1': ['Phạm Huỳnh Trang', 'Phạm Thái Thật'],
    'NS2': ['Trần Cẩm Ái', 'Tạ Ngọc Thơ'],
    'NS3': ['Lý Anh Kiệt', 'Nguyễn Duy Tiếng', 'Phạm Đông Anh', 'Nguyễn Minh Phú', 'Lê Ngọc Hạnh'],
    'NS4': ['Phạm Văn Dược', 'Tô Thị Khả Vi', 'Lê Nguyễn Thuý Hà'],
    'NS5': ['Hồ Kim Ngân', 'Châu Huỳnh Nhân', 'Trần Thị Bé Lài', 'Lâm Thị Tố Quyên'],
    'NSM1': ['Nguyễn Ngọc Quyên', 'Trần Thị Diễm Thuý', 'Hồ Kim Hương'],
}
# Thêm dòng này:
ADMIN_EMPLOYEES = ['Lý Huỳnh Như', 'Nguyễn Như Ngọc']

def _valid_employee_for_store(name, store_code):
    """Trả về tên nhân viên đã chuẩn hoá nếu thuộc đúng cửa hàng; ngược lại None."""
    cleaned = (name or '').strip()
    if not cleaned:
        return None
    allowed = STORE_EMPLOYEES.get((store_code or '').strip().upper(), [])
    return cleaned if cleaned in allowed else None

def _current_actor_name():
    """Tên hiển thị của người dùng đang đăng nhập, dùng để ghi vào các cột
    "uploaded_by"/"updated_by" (thay vì lưu thẳng username như 'admin').
    Ưu tiên full_name (cột full_name trong bảng users, admin tự đặt qua
    trang Quản Lý User) - nếu tài khoản chưa có full_name thì tạm dùng lại
    username như trước đây (session['user'])."""
    return session.get('full_name') or session.get('user')

# ----------------------------------------------------------------------------
# GHI LOG LỊCH SỬ CHUYỂN KHO NỘI BỘ RA GOOGLE SHEETS (qua Apps Script)
# ----------------------------------------------------------------------------
# Mục đích: tạo 1 BẢN SAO ĐỘC LẬP, nằm ngoài Postgres, của toàn bộ lịch sử
# luân chuyển nội bộ (tạo phiếu / đồng ý / từ chối / đổi lại / nhận hàng /
# huỷ). Nếu dữ liệu trong Postgres bị mất/reset vì bất kỳ lý do gì, lịch sử
# trên Google Sheets vẫn còn nguyên để tra cứu, đối chiếu.
#
# CÁCH NÀY KHÔNG DÙNG Google Sheets API / Service Account / thư viện cài
# thêm nào cả - chỉ dùng module chuẩn "urllib" có sẵn trong Python. Thay vào
# đó, ta tận dụng GOOGLE APPS SCRIPT gắn ngay trong chính Google Sheet đó,
# public thành 1 "Web App" (1 URL riêng do Google cấp) chỉ nhận đúng 1 việc:
# nhận JSON gửi tới rồi tự ghi thêm dòng vào Sheet. Flask chỉ cần POST JSON
# tới đúng URL đó - xem hướng dẫn tạo Apps Script ở cuối file (hoặc phần
# hướng dẫn đi kèm).
#
# Cấu hình qua biến môi trường (Render > Settings > Environment):
#   - GOOGLE_SHEETS_WEBHOOK_URL: URL "Web app" do Apps Script cấp sau khi
#     Deploy (dạng https://script.google.com/macros/s/XXXX/exec).
#   - GOOGLE_SHEETS_WEBHOOK_SECRET: 1 chuỗi bí mật tự đặt (ví dụ tạo bằng
#     `python -c "import secrets; print(secrets.token_hex(16))"`), phải
#     TRÙNG với hằng số SECRET khai báo trong Apps Script - dùng để Apps
#     Script từ chối các request lạ không phải từ chính app này gửi tới
#     (vì URL Web App public, ai có URL cũng gọi được nếu không kiểm tra).
GOOGLE_SHEETS_WEBHOOK_URL = os.environ.get('GOOGLE_SHEETS_WEBHOOK_URL')
GOOGLE_SHEETS_WEBHOOK_SECRET = os.environ.get('GOOGLE_SHEETS_WEBHOOK_SECRET')

_gsheet_warned_missing_config = False  # chỉ log cảnh báo thiếu cấu hình 1 lần


def _to_log_value(v):
    """Chuyển 1 giá trị Python bất kỳ (kể cả Decimal của cột NUMERIC trong
    Postgres) về kiểu JSON serializable bằng json.dumps chuẩn."""
    if v is None:
        return ''
    if isinstance(v, (int, float, str, bool)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


_ip_location_cache = {}
_ip_location_cache_lock = threading.Lock()


def _is_private_ip(ip):
    """IP nội bộ/localhost (VD: chạy local, hoặc Render gọi nhau qua mạng
    private) - không có ý nghĩa địa lý thật, tra cứu chỉ tốn thời gian vô ích."""
    if not ip:
        return True
    return (
        ip.startswith('127.') or ip.startswith('10.') or ip.startswith('192.168.')
        or ip == '::1' or ip.startswith('172.16.') or ip.startswith('172.17.')
        or ip.startswith('172.18.') or ip.startswith('172.19.')
        or ip.startswith('172.2') or ip.startswith('172.30.') or ip.startswith('172.31.')
    )


def get_ip_location(ip):
    """Tra cứu thành phố/tỉnh/quốc gia từ địa chỉ IP bằng API miễn phí
    ip-api.com (không cần API key). Có cache theo IP trong RAM để: (1) IP
    trùng lặp (VD: cùng 1 văn phòng/wifi) không gọi API lại nhiều lần, (2)
    tránh chạm giới hạn tần suất miễn phí (45 request/phút) của ip-api.com.
    KHÔNG BAO GIỜ raise lỗi ra ngoài - trả về dict rỗng nếu có bất kỳ vấn đề
    gì (mất mạng, IP lạ, hết hạn mức...), để việc này chỉ là "có thì hay,
    không có cũng không sao", không ảnh hưởng chức năng đăng nhập."""
    if _is_private_ip(ip):
        return {'city': 'Mạng nội bộ', 'region': '', 'country': ''}

    with _ip_location_cache_lock:
        cached = _ip_location_cache.get(ip)
    if cached is not None:
        return cached

    result = {'city': '', 'region': '', 'country': ''}
    try:
        # ip-api.com bản miễn phí chỉ hỗ trợ HTTP (không phải HTTPS) - việc
        # này chấp nhận được vì đây là request server-to-server (không phải
        # trình duyệt người dùng), không phát sinh cảnh báo "mixed content".
        url = f"http://ip-api.com/json/{ip}?fields=status,city,regionName,country"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if data.get('status') == 'success':
            result = {
                'city': data.get('city') or '',
                'region': data.get('regionName') or '',
                'country': data.get('country') or '',
            }
    except Exception as e:
        app.logger.warning("Không tra được vị trí địa lý cho IP %s: %s", ip, e)

    with _ip_location_cache_lock:
        _ip_location_cache[ip] = result
    return result


def resolve_login_location_async(login_log_id, ip):
    """Tra cứu vị trí địa lý và UPDATE lại dòng login_log tương ứng - chạy
    trong THREAD RIÊNG (không chặn response đăng nhập, vì gọi API bên ngoài
    ip-api.com có thể mất 1-2 giây hoặc hơn). Thread nền không dùng chung
    connection với request chính (Flask `g` không tồn tại ở đây), nên tự
    mượn 1 connection riêng từ pool rồi trả lại ngay khi xong."""
    def _run():
        location = get_ip_location(ip)
        if not location.get('city') and not location.get('country'):
            return  # không tra được gì thì khỏi UPDATE, đỡ 1 lần ghi DB
        pool = _get_pool()
        conn = pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE login_log SET city = %s, region = %s, country = %s WHERE id = %s",
                (location['city'], location['region'], location['country'], login_log_id)
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            app.logger.warning("Không lưu được vị trí địa lý cho login_log id=%s: %s", login_log_id, e)
        finally:
            pool.putconn(conn)

    threading.Thread(target=_run, daemon=True).start()


def _post_transfer_log_rows(rows):
    """Gửi (POST) danh sách dòng log lên Apps Script Web App. Chạy hàm này
    trong 1 THREAD RIÊNG (xem log_transfer_event) để không làm chậm response
    trả về cho người dùng khi Google đang phản hồi chậm/mất mạng. Không bao
    giờ raise lỗi ra ngoài - mọi lỗi chỉ log lại ở server.
    Timeout để khá cao (25s thay vì 8s trước đây) vì Apps Script Web App
    hay có độ trễ "cold start" (lần gọi đầu sau một thời gian không hoạt
    động) có thể mất 10-20s mới phản hồi - do hàm này chạy ở thread nền,
    tăng timeout KHÔNG làm chậm thao tác chính của người dùng, chỉ giúp
    giảm các lần bị coi là lỗi timeout oan trong khi Google vẫn đang xử lý."""
    try:
        payload = json.dumps({
            'secret': GOOGLE_SHEETS_WEBHOOK_SECRET,
            'rows': rows,
        }, default=str).encode('utf-8')
        req = urllib.request.Request(
            GOOGLE_SHEETS_WEBHOOK_URL,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=25, context=_HTTPS_SSL_CONTEXT) as resp:
            resp.read()
    except Exception as e:
        app.logger.error(
            "Lỗi gửi log lịch sử chuyển kho tới Google Sheets webhook: %s", e
        )


def log_transfer_event(event, req, items=None, note=None, actor=None):
    """Ghi 1 hoặc nhiều dòng lịch sử ra Google Sheets (qua Apps Script Web
    App) cho 1 phiếu luân chuyển. `req` là dict (hoặc RealDictRow) có ít
    nhất id/from_store/to_store. `items` (nếu có) là danh sách mã hàng liên
    quan tới sự kiện này - mỗi mã hàng ghi thành 1 dòng riêng để dễ lọc/
    tổng hợp trên Sheet; nếu None, ghi 1 dòng chung ở cấp phiếu (dùng cho
    Huỷ/Từ chối/Đổi lại). Việc gửi đi chạy ở 1 thread nền (fire-and-forget)
    và KHÔNG BAO GIỜ làm gián đoạn/làm chậm thao tác chính của người dùng
    dù Google Sheets đang lỗi, chậm hoặc chưa được cấu hình."""
    global _gsheet_warned_missing_config
    if not GOOGLE_SHEETS_WEBHOOK_URL or not GOOGLE_SHEETS_WEBHOOK_SECRET:
        if not _gsheet_warned_missing_config:
            app.logger.warning(
                "Thiếu biến môi trường GOOGLE_SHEETS_WEBHOOK_URL hoặc "
                "GOOGLE_SHEETS_WEBHOOK_SECRET nên KHÔNG ghi log lịch sử "
                "chuyển kho ra Google Sheets được."
            )
            _gsheet_warned_missing_config = True
        return

    now_str = vn_now().strftime('%d/%m/%Y %H:%M:%S')
    req_id = req.get('id') if req.get('id') is not None else req.get('request_id')
    from_store = req.get('from_store')
    to_store = req.get('to_store')
    created_employee = req.get('created_employee') or ''
    confirmed_employee = req.get('confirmed_employee') or ''

    rows = []
    if items:
        for it in items:
            rows.append([
                now_str, event, req_id, from_store, to_store,
                it.get('part_code'), it.get('part_name') or '',
                _to_log_value(it.get('quantity')),
                _to_log_value(it.get('approved_quantity')),
                actor or '', note or '',
                created_employee, confirmed_employee,
            ])
    else:
        rows.append([
            now_str, event, req_id, from_store, to_store,
            '', '', '', '', actor or '', note or '',
            created_employee, confirmed_employee,
        ])

    threading.Thread(target=_post_transfer_log_rows, args=(rows,), daemon=True).start()


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        pool = _get_pool()
        db = pool.getconn()

        # "Pre-ping": kiểm tra kết nối lấy từ pool còn sống hay đã bị Neon
        # đóng do rảnh quá lâu (rất hay gặp với Postgres serverless). Nếu
        # kết nối đã chết, loại bỏ nó khỏi pool và lấy 1 kết nối mới thay vì
        # để lỗi "server closed the connection unexpectedly" làm hỏng cả
        # request của người dùng.
        try:
            probe = db.cursor()
            probe.execute('SELECT 1')
            probe.close()
        except Exception:
            try:
                pool.putconn(db, close=True)
            except Exception:
                pass
            db = pool.getconn()

        g._database = db
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        pool = _get_pool()
        if exception is not None:
            # Có lỗi xảy ra giữa request (bất kỳ Exception nào, kể cả lỗi
            # tầng SSL/socket như "decryption failed or bad record mac") -
            # AN TOÀN NHẤT là ĐÓNG HẲN connection này, không trả về pool để
            # tái sử dụng nữa. Rollback() có thể tự nó cũng thất bại nếu
            # connection đã hỏng ở tầng thấp (socket/SSL), lúc đó KHÔNG THỂ
            # coi connection này còn dùng lại được - trả 1 connection "bẩn"
            # về pool sẽ khiến request TIẾP THEO lấy trúng nó và lỗi lây
            # lan tiếp, dù get_db() có pre-ping cũng chỉ bắt được ở lần gọi
            # SAU, không cứu được request đó.
            try:
                db.rollback()
            except Exception:
                pass
            try:
                pool.putconn(db, close=True)
            except Exception:
                pass
        else:
            try:
                pool.putconn(db)
            except Exception:
                pass


def init_db():
    """Khởi tạo bảng và dữ liệu mẫu trên Neon PostgreSQL"""
    with app.app_context():
        db = get_db()
        cursor = db.cursor()

        # Khoá "advisory lock" ở mức database TRONG PHẠM VI TRANSACTION HIỆN
        # TẠI (tự nhả khi commit/rollback, không cần tự tay mở khoá) - đảm
        # bảo CHỈ 1 process được chạy migrate (CREATE/ALTER TABLE) tại 1
        # thời điểm. Nếu chạy nhiều worker (gunicorn/Render) hoặc chạy 2 lần
        # `python app.py` cùng lúc, các process còn lại sẽ tự CHỜ ở đây cho
        # tới khi process đầu tiên migrate xong, thay vì cùng lúc đụng độ
        # ALTER TABLE với nhau và bị Postgres báo lỗi "deadlock detected".
        # Con số 727270001 chỉ là 1 mã khoá tự đặt, không có ý nghĩa gì khác
        # ngoài việc phải giống nhau ở mọi lần gọi init_db().
        cursor.execute("SELECT pg_advisory_xact_lock(727270001)")

        # Giới hạn thời gian chờ khoá cho các câu ALTER/CREATE TABLE bên
        # dưới (chỉ áp dụng trong TRANSACTION hiện tại - SET LOCAL tự huỷ khi
        # transaction kết thúc). Nếu 1 câu DDL nào đó phải chờ khoá quá 10
        # giây (VD: do trùng lúc traffic thật đang chạy trên instance cũ lúc
        # rolling deploy), Postgres sẽ báo lỗi "lock not available" ngay thay
        # vì chờ vô thời hạn rồi có thể rơi vào deadlock thật sự - lỗi này
        # được _init_db_with_retry() ở cuối file bắt lại và tự thử lại sau
        # vài giây.
        cursor.execute("SET LOCAL lock_timeout = '10s'")

        # 1. Tạo bảng users
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username VARCHAR(50) PRIMARY KEY,
                password VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL,
                store_code VARCHAR(20) NOT NULL
            )
        ''')
        # 1a. Bổ sung thông tin hiển thị của user: Họ và tên (dùng để chào
        # khi đăng nhập, VD "Xin chào Nguyễn Văn A") và Chi nhánh làm việc
        # (nhập tự do, KHÁC với store_code dùng để phân quyền - vì 1 tài
        # khoản cửa hàng có thể có nhiều nhân viên cùng dùng chung, mỗi
        # người vẫn có thể được ghi rõ đang làm việc tại chi nhánh nào).
        # Cho phép NULL vì các tài khoản cũ/mặc định chưa có 2 thông tin
        # này, admin sẽ bổ sung dần qua trang Quản Lý User.
        cursor.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR(100)')
        cursor.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS branch VARCHAR(100)')

        # 1b. Bảng nhật ký đăng nhập - để admin biết mỗi tài khoản đã đăng
        #     nhập từ đâu (IP) và lúc nào, phục vụ theo dõi/bảo mật.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS login_log (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) NOT NULL,
                ip_address VARCHAR(64),
                user_agent TEXT,
                login_time TIMESTAMP NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_login_log_username_time
            ON login_log (username, login_time DESC)
        ''')
        # Cột địa điểm (thành phố/tỉnh) suy ra từ IP - điền SAU KHI đăng nhập
        # xong (chạy nền, xem resolve_login_location_async), nên phải cho
        # phép NULL để không chặn việc ghi log ngay lúc đăng nhập.
        cursor.execute('ALTER TABLE login_log ADD COLUMN IF NOT EXISTS city VARCHAR(100)')
        cursor.execute('ALTER TABLE login_log ADD COLUMN IF NOT EXISTS region VARCHAR(100)')
        cursor.execute('ALTER TABLE login_log ADD COLUMN IF NOT EXISTS country VARCHAR(100)')

        # 1c. Bảng "IP đã biết" của từng cửa hàng - admin đăng ký thủ công
        # (đánh dấu 1 lượt đăng nhập trong lịch sử là "đúng IP cửa hàng"),
        # dùng để SO SÁNH CHÍNH XÁC (không phải suy đoán theo thành phố/tỉnh
        # như city/region ở trên) xem 1 lượt đăng nhập có đúng là từ mạng
        # của cửa hàng hay không. 1 cửa hàng có thể có NHIỀU IP đã biết (VD:
        # đổi nhà mạng, có thêm đường truyền dự phòng, IP động đổi theo thời
        # gian...).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS store_known_ips (
                id SERIAL PRIMARY KEY,
                store_code VARCHAR(20) NOT NULL,
                ip_address VARCHAR(64) NOT NULL,
                label VARCHAR(100),
                added_by VARCHAR(50),
                added_at TIMESTAMP NOT NULL,
                UNIQUE (store_code, ip_address)
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_store_known_ips_store
            ON store_known_ips (store_code)
        ''')

        # 2. Bảng nhật ký các lượt tải file (chỉ để hiển thị lịch sử, không dùng để tính toán)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS upload_log (
                id SERIAL PRIMARY KEY,
                store_code VARCHAR(20) NOT NULL,
                upload_time TIMESTAMP NOT NULL,
                ds_po_filename TEXT,
                po_detail_filename TEXT,
                receipt_filename TEXT
            )
        ''')

        # 3. Bảng lưu dữ liệu MỚI NHẤT của "Danh sách PO" và "Chi tiết nhận hàng"
        #    -> mỗi lần có file mới sẽ GHI ĐÈ (xoá sạch + thay thế) cho từng cửa hàng.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS latest_uploads (
                store_code VARCHAR(20) PRIMARY KEY,
                ds_po_filename TEXT,
                ds_po_json TEXT,
                ds_po_upload_time TIMESTAMP,
                receipt_filename TEXT,
                receipt_json TEXT,
                receipt_upload_time TIMESTAMP
            )
        ''')

        # 4. Bảng lưu dữ liệu "Chi tiết PO" theo kiểu CỘNG DỒN (append).
        #    Mỗi dòng dữ liệu được lưu kèm thời điểm nhập (upload_time) để
        #    có thể tự động xoá các dòng đã nhập quá 120 ngày mà không ảnh
        #    hưởng tới các dữ liệu khác. Cột "quantity" dùng để phát hiện
        #    trùng lặp (Mã PO + Mã phụ tùng + Số lượng) khi ghi thêm dữ liệu.
        #    KHÔNG còn cột row_json (nguyên văn mọi cột gốc file Excel) - đã
        #    bỏ để giảm dung lượng CSDL mỗi lần import, vì cột này chưa bao
        #    giờ được đọc lại (xem load_store_dataframes()).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS po_detail_items (
                id SERIAL PRIMARY KEY,
                store_code VARCHAR(20) NOT NULL,
                po_code VARCHAR(100) NOT NULL,
                part_code VARCHAR(100) NOT NULL,
                quantity NUMERIC,
                filename TEXT,
                upload_time TIMESTAMP NOT NULL
            )
        ''')
        # Đảm bảo cột "quantity" tồn tại kể cả với CSDL đã tạo từ trước
        # (khi bảng po_detail_items đã có sẵn nhưng chưa có cột này).
        cursor.execute('ALTER TABLE po_detail_items ADD COLUMN IF NOT EXISTS quantity NUMERIC')
        # Xoá hẳn cột row_json khỏi CSDL cũ (nếu còn) để giải phóng dung
        # lượng đã lưu từ trước - đây thường là phần nặng nhất của cả bảng
        # vì chứa nguyên văn mọi cột gốc của file Excel cho từng dòng.
        cursor.execute('ALTER TABLE po_detail_items DROP COLUMN IF EXISTS row_json')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_po_detail_store ON po_detail_items(store_code)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_po_detail_upload_time ON po_detail_items(upload_time)')
        # Index gộp (store_code, upload_time DESC): load_store_dataframes()
        # luôn lọc theo store_code RỒI sort theo upload_time giảm dần cùng
        # lúc - 2 index riêng ở trên chỉ giúp được 1 trong 2 việc, Postgres
        # vẫn phải tự sort sau khi lọc. Index gộp này khớp thẳng với mẫu
        # WHERE + ORDER BY của câu query, tránh bước sort tốn thêm khi bảng
        # càng tích luỹ nhiều dữ liệu theo thời gian (cache miss).
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_po_detail_store_time ON po_detail_items(store_code, upload_time DESC)')

        # Dọn các dòng trùng lặp (Mã PO + Mã phụ tùng + Số lượng) có thể đã
        # tồn tại từ TRƯỚC KHI có cơ chế dedup ở CSDL dưới đây (chỉ giữ lại
        # dòng có id nhỏ nhất/cũ nhất của mỗi nhóm trùng) - bước này BẮT
        # BUỘC phải chạy trước khi tạo UNIQUE INDEX, nếu không CREATE UNIQUE
        # INDEX sẽ báo lỗi vì dữ liệu đang vi phạm ràng buộc duy nhất.
        cursor.execute('''
            DELETE FROM po_detail_items a
            USING po_detail_items b
            WHERE a.id > b.id
              AND a.store_code = b.store_code
              AND a.po_code = b.po_code
              AND a.part_code = b.part_code
              AND a.quantity IS NOT DISTINCT FROM b.quantity
        ''')

        # Đổi từ INDEX thường sang UNIQUE INDEX: nhờ vậy việc chống trùng
        # lặp khi ghi thêm dữ liệu (append_po_detail) có thể giao hẳn cho
        # Postgres xử lý bằng "INSERT ... ON CONFLICT DO NOTHING" thay vì
        # phải SELECT toàn bộ khoá đã có của cửa hàng vào RAM Python rồi so
        # sánh từng dòng - cách cũ càng chậm và tốn RAM hơn khi bảng càng
        # tích luỹ nhiều dữ liệu theo thời gian.
        cursor.execute('DROP INDEX IF EXISTS idx_po_detail_dedup')
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_po_detail_dedup_uniq
            ON po_detail_items(store_code, po_code, part_code, quantity)
        ''')

        # 5. Bảng lưu TỒN KHO HỆ THỐNG (dạng "dài": mỗi dòng là 1 mã hàng
        #    tại 1 cửa hàng). Đây là dữ liệu kiểu "ghi đè toàn bộ" mỗi lần
        #    admin tải file tồn kho mới lên (khác với po_detail_items là
        #    cộng dồn) - vì tồn kho là số liệu cuối kỳ tại 1 thời điểm, tải
        #    file mới nghĩa là thay hoàn toàn số liệu cũ.
        #    store_code ở đây dùng đúng giá trị NS1..NS5, NSM1 như bảng users.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory_items (
                id SERIAL PRIMARY KEY,
                part_code VARCHAR(100) NOT NULL,
                part_name TEXT,
                unit VARCHAR(50),
                store_code VARCHAR(20) NOT NULL,
                quantity NUMERIC NOT NULL DEFAULT 0,
                is_pi2 BOOLEAN NOT NULL DEFAULT FALSE
            )
        ''')
        # Cột đánh dấu dòng tồn kho này có gộp số lượng từ kho phụ "PI2" của
        # NSM1 hay không (xem _SUFFIX_ALIAS_TO_STORE) - dùng để xếp các mã
        # hàng liên quan PI2 xuống CUỐI bảng khi hiển thị, còn lại giữ thứ tự
        # như cũ. ADD COLUMN IF NOT EXISTS để không phá dữ liệu cũ khi bảng
        # đã tồn tại trước khi có cột này.
        cursor.execute('ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS is_pi2 BOOLEAN NOT NULL DEFAULT FALSE')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inventory_part ON inventory_items(part_code)')

        # 6. Bảng lưu thông tin lần tải file tồn kho gần nhất (chỉ 1 dòng
        #    duy nhất, luôn bị ghi đè - phục vụ hiển thị "Cập nhật lần cuối").
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory_meta (
                id INTEGER PRIMARY KEY DEFAULT 1,
                filename TEXT,
                uploaded_by VARCHAR(50),
                upload_time TIMESTAMP,
                total_parts INTEGER,
                skipped_rows INTEGER
            )
        ''')

        # 6z. Bảng lưu số liệu XUẤT BÁN (import RIÊNG, KHI CẦN bởi admin từ
        #     file "Tổng hợp tồn kho" - cột Xuất kho) để phục vụ phân loại
        #     tần suất bán TX/TB/CB. Ghi đè toàn bộ mỗi lần import mới,
        #     giống cách inventory_items được ghi đè khi cập nhật tồn kho.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sales_export_items (
                id SERIAL PRIMARY KEY,
                part_code VARCHAR(100) NOT NULL,
                part_name TEXT,
                unit VARCHAR(50),
                store_code VARCHAR(20) NOT NULL,
                qty_sold NUMERIC NOT NULL DEFAULT 0
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sales_export_part ON sales_export_items(part_code)')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sales_export_meta (
                id INTEGER PRIMARY KEY DEFAULT 1,
                filename TEXT,
                uploaded_by VARCHAR(50),
                upload_time TIMESTAMP,
                period_months INTEGER,
                total_parts INTEGER,
                skipped_rows INTEGER
            )
        ''')

        # 6a. Bảng lưu GIÁ BÁN của từng mã hàng - tách RIÊNG khỏi
        #     inventory_items (vốn bị TRUNCATE + nạp lại toàn bộ mỗi lần
        #     admin cập nhật file tồn kho, xem upload_inventory()) để giá bán
        #     KHÔNG bị mất mỗi khi tồn kho được cập nhật lại. Áp dụng chung
        #     cho mã hàng trên toàn hệ thống (không phân biệt theo cửa hàng)
        #     - nạp/ghi đè qua file Excel riêng ở /api/admin/import-prices.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS part_prices (
                part_code VARCHAR(100) PRIMARY KEY,
                sale_price NUMERIC,
                updated_at TIMESTAMP,
                updated_by VARCHAR(50)
            )
        ''')

        # Bảng lưu thông tin lần import giá bán gần nhất (chỉ 1 dòng duy
        # nhất, luôn bị ghi đè) - cùng kiểu với inventory_meta, phục vụ hiển
        # thị "Cập nhật giá bán lần cuối".
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_meta (
                id INTEGER PRIMARY KEY DEFAULT 1,
                filename TEXT,
                uploaded_by VARCHAR(50),
                upload_time TIMESTAMP,
                total_parts INTEGER,
                skipped_rows INTEGER
            )
        ''')

        # 6a-2. Bảng lưu dữ liệu "Khoá đặt hàng" do admin import RIÊNG từ 1
        #     file Excel ngoài (không sinh ra từ trong hệ thống): mã hàng
        #     nào đang bị KHOÁ không cho đặt hàng mới nữa (is_locked), mã
        #     thay thế nếu có, và tồn kho 2 miền Bắc/Nam (dữ liệu ngoài hệ
        #     thống NSM, phục vụ riêng chức năng "Duyệt Đơn Hàng"). Ghi đè
        #     TOÀN BỘ mỗi lần import, giống part_prices/price_meta.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_lock_items (
                part_code VARCHAR(100) PRIMARY KEY,
                is_locked BOOLEAN DEFAULT FALSE,
                replacement_code VARCHAR(100),
                qty_bac NUMERIC,
                qty_nam NUMERIC,
                updated_at TIMESTAMP,
                updated_by VARCHAR(50)
            )
        ''')
        # Cột cờ "có/không tồn" (Y/N) ở 2 miền - dùng cho file nguồn KIỂU MỚI
        # (cột "Part #" / "Block for Order" / "Stock Available in South|North
        # Warehouse" / "Superseeded Part" - chỉ ghi Y/N chứ KHÔNG có số
        # lượng cụ thể, khác với file kiểu CŨ đang dùng qty_bac/qty_nam ở
        # trên). Tách cột riêng thay vì gán tạm 1/0 vào qty_bac/qty_nam, để
        # không làm sai lệch ý nghĩa "số lượng thật" mà trang Duyệt Đơn Hàng
        # đang hiển thị cho admin khi file nguồn là kiểu số lượng cụ thể.
        cursor.execute('ALTER TABLE order_lock_items ADD COLUMN IF NOT EXISTS has_stock_bac BOOLEAN')
        cursor.execute('ALTER TABLE order_lock_items ADD COLUMN IF NOT EXISTS has_stock_nam BOOLEAN')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_lock_meta (
                id INTEGER PRIMARY KEY DEFAULT 1,
                filename TEXT,
                uploaded_by VARCHAR(50),
                upload_time TIMESTAMP,
                total_parts INTEGER,
                skipped_rows INTEGER
            )
        ''')

        # 6a-3. Bảng lưu GHI CHÚ theo mã hàng cho màn "Duyệt Đơn Hàng" - mỗi
        # mã hàng 1 ghi chú duy nhất (UPSERT), KHÔNG gắn theo lượt kiểm tra
        # cụ thể nào, để admin ghi chú 1 lần (vd "hàng lâu về", "chờ mã
        # thay thế mới") và thấy lại ở MỌI lần kiểm tra sau có mã đó, không
        # phân biệt cửa hàng/thời điểm dán danh sách.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_check_notes (
                part_code VARCHAR(100) PRIMARY KEY,
                note TEXT,
                updated_at TIMESTAMP,
                updated_by VARCHAR(50)
            )
        ''')

        # 6b. Bảng lưu VỊ TRÍ KỆ HÀNG (tối đa 3 vị trí) cho mỗi mã hàng tại
        #     từng cửa hàng. Store tự quản lý vị trí của cửa hàng mình (chỉ
        #     cho những mã hàng đã từng xuất hiện trong file tồn kho hệ
        #     thống do admin tải lên - kể cả đang hết hàng, quantity = 0);
        #     Admin xem/sửa được vị trí của TẤT CẢ cửa hàng. Mỗi (store_code,
        #     part_code) chỉ có 1 dòng duy nhất (UPSERT khi lưu/nhập lại) -
        #     không có bảng lịch sử.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS part_locations (
                id SERIAL PRIMARY KEY,
                store_code VARCHAR(20) NOT NULL,
                part_code VARCHAR(100) NOT NULL,
                location_1 VARCHAR(100),
                location_2 VARCHAR(100),
                location_3 VARCHAR(100),
                updated_by VARCHAR(50),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE(store_code, part_code)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_part_locations_store ON part_locations(store_code)')

        # 7b. Bảng lưu HÀNG HƯ HỎNG - mỗi dòng là 1 LẦN BÁO HƯ (không UPSERT
        #     ghi đè như part_locations) vì cùng 1 mã hàng có thể bị hư
        #     NHIỀU LẦN, MỖI LẦN 1 tình trạng/số lượng khác nhau (vd hôm nay
        #     vỡ 3 cái, hôm sau mốc thêm 2 cái) - cần giữ LỊCH SỬ đầy đủ để
        #     cộng dồn số lượng lẫn liệt kê chi tiết từng lần khi rê chuột
        #     xem ghi chú trên bảng Tồn Kho Hệ Thống. Chỉ store tự báo hư
        #     hàng của chính cửa hàng mình (xem /api/damaged/save).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS damaged_items (
                id SERIAL PRIMARY KEY,
                store_code VARCHAR(20) NOT NULL,
                part_code VARCHAR(100) NOT NULL,
                quantity NUMERIC NOT NULL,
                note TEXT,
                created_by VARCHAR(50),
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_damaged_items_part_code ON damaged_items(part_code)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_damaged_items_store ON damaged_items(store_code)')

        # 7c. Ảnh đính kèm cho từng lần báo hư hỏng - TÁCH RIÊNG bảng (không
        #     thêm cột BYTEA vào damaged_items) để API /api/damaged (danh
        #     sách) KHÔNG BAO GIỜ phải tải dữ liệu ảnh nặng, chỉ khi người
        #     dùng thật sự mở ảnh mới gọi endpoint riêng. Ảnh được NÉN/RESIZE
        #     phía server (xem resize_damaged_image()) trước khi lưu để giảm
        #     dung lượng CSDL và băng thông tải xuống tối đa - mỗi ảnh
        #     thường chỉ còn vài chục-vài trăm KB dù ảnh gốc chụp bằng điện
        #     thoại có thể vài MB. Giới hạn số ảnh/lần báo ở tầng API
        #     (MAX_DAMAGED_IMAGES_PER_ITEM) để tránh phình CSDL.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS damaged_item_images (
                id SERIAL PRIMARY KEY,
                damaged_item_id INTEGER NOT NULL REFERENCES damaged_items(id) ON DELETE CASCADE,
                content_type VARCHAR(30) NOT NULL DEFAULT 'image/webp',
                image_data BYTEA NOT NULL,
                byte_size INTEGER NOT NULL,
                created_by VARCHAR(50),
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_damaged_item_images_item ON damaged_item_images(damaged_item_id)')

        # 8. Cache bảng đối soát đã tính sẵn cho mỗi cửa hàng, LƯU TRONG CSDL
        #    (không chỉ RAM của 1 tiến trình) để dùng CHUNG được giữa NHIỀU
        #    worker Flask (nếu Render chạy `gunicorn -w N` với N > 1) - mỗi
        #    worker là 1 tiến trình riêng, không chia sẻ RAM với nhau, nên
        #    biến toàn cục _result_cache trong Python chỉ có tác dụng cho
        #    đúng worker đã tính ra nó. Cột "version" dùng để biết cache còn
        #    hợp lệ hay đã cũ (giống hệt cơ chế data_version đang dùng).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS computed_cache (
                store_code VARCHAR(20) PRIMARY KEY,
                version VARCHAR(50) NOT NULL,
                data_json TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        ''')

        # 8b. Bảng lưu PHIẾU XIN LUÂN CHUYỂN NỘI BỘ giữa các cửa hàng. Mỗi
        #     phiếu có 1 cửa hàng gửi (from_store), 1 cửa hàng được xin
        #     (to_store) và có thể chứa NHIỀU mã hàng (xem bảng
        #     transfer_items bên dưới). to_store chỉ cần xử lý 1 lần duy
        #     nhất cho cả phiếu: Đồng ý hoặc Từ chối - không còn bước
        #     "Đã soạn/Chưa soạn" trung gian nữa. Admin xem được toàn bộ
        #     phiếu của mọi cửa hàng.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transfer_requests (
                id SERIAL PRIMARY KEY,
                from_store VARCHAR(20) NOT NULL,
                to_store VARCHAR(20) NOT NULL,
                note TEXT,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                reject_reason TEXT,
                created_by VARCHAR(50) NOT NULL,
                created_at TIMESTAMP NOT NULL,
                responded_by VARCHAR(50),
                responded_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL,
                created_employee VARCHAR(100),
                confirmed_employee VARCHAR(100)
            )
        ''')
        cursor.execute('ALTER TABLE transfer_requests ADD COLUMN IF NOT EXISTS created_employee VARCHAR(100)')
        cursor.execute('ALTER TABLE transfer_requests ADD COLUMN IF NOT EXISTS confirmed_employee VARCHAR(100)')
        # prepared: bên cho (to_store) đã soạn xong hàng để giao chưa - chỉ
        # có ý nghĩa khi status = 'approved'. Khác với chính hành động Đồng
        # Ý (đồng ý cho) - đây là bước SAU đó, đánh dấu đã chuẩn bị xong
        # hàng vật lý sẵn sàng giao, do to_store tự bấm cập nhật.
        cursor.execute('ALTER TABLE transfer_requests ADD COLUMN IF NOT EXISTS prepared BOOLEAN NOT NULL DEFAULT FALSE')
        cursor.execute('ALTER TABLE transfer_requests ADD COLUMN IF NOT EXISTS prepared_at TIMESTAMP')
        cursor.execute('ALTER TABLE transfer_requests ADD COLUMN IF NOT EXISTS prepared_by VARCHAR(50)')
        # delete_requested*: cửa hàng (bên gửi hoặc bên nhận của phiếu) nhờ
        # ADMIN xoá hẳn phiếu này kèm lý do - không xoá ngay, chỉ đánh dấu để
        # admin xem qua icon chuông rồi tự quyết định xoá hay bỏ qua (xem
        # /api/transfer/request-delete và /api/admin/transfer/dismiss-delete-request).
        cursor.execute('ALTER TABLE transfer_requests ADD COLUMN IF NOT EXISTS delete_requested BOOLEAN NOT NULL DEFAULT FALSE')
        cursor.execute('ALTER TABLE transfer_requests ADD COLUMN IF NOT EXISTS delete_request_reason TEXT')
        cursor.execute('ALTER TABLE transfer_requests ADD COLUMN IF NOT EXISTS delete_requested_by VARCHAR(50)')
        cursor.execute('ALTER TABLE transfer_requests ADD COLUMN IF NOT EXISTS delete_requested_at TIMESTAMP')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_transfer_from_store ON transfer_requests(from_store)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_transfer_to_store ON transfer_requests(to_store)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_transfer_updated_at ON transfer_requests(updated_at)')
        # Index cho created_at: transfer_list() giờ lọc/sắp xếp CHÍNH theo
        # created_at (thay vì updated_at) để danh sách không tự nhảy thứ tự
        # mỗi khi 1 phiếu được thao tác (đồng ý/từ chối/nhận hàng) - xem
        # transfer_list().
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_transfer_created_at ON transfer_requests(created_at DESC)')

        # 8c. Bảng lưu TỪNG MÃ HÀNG bên trong 1 phiếu luân chuyển (1 phiếu -
        #     nhiều dòng). Cột "received" đánh dấu cửa hàng xin (from_store)
        #     đã thực nhận được đúng mã hàng đó hay chưa - khi tick nhận
        #     hàng, ghi chú tô màu tồn kho của mã này sẽ tự biến mất.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transfer_items (
                id SERIAL PRIMARY KEY,
                request_id INTEGER NOT NULL REFERENCES transfer_requests(id) ON DELETE CASCADE,
                part_code VARCHAR(100) NOT NULL,
                part_name TEXT,
                quantity NUMERIC,
                received BOOLEAN NOT NULL DEFAULT FALSE,
                received_at TIMESTAMP
            )
        ''')
        # approved_quantity: số lượng bên cho (to_store) THỰC SỰ đồng ý cho -
        # có thể khác với "quantity" (số lượng bên xin yêu cầu ban đầu) nếu
        # bên cho sửa lại lúc bấm Đồng ý. Giữ nguyên "quantity" gốc để 2 bên
        # so sánh được xin bao nhiêu / cho bao nhiêu. NULL khi phiếu chưa
        # được đồng ý (pending/rejected).
        cursor.execute('ALTER TABLE transfer_items ADD COLUMN IF NOT EXISTS approved_quantity NUMERIC')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_transfer_items_request ON transfer_items(request_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_transfer_items_part ON transfer_items(part_code)')

        # 8e. Bảng key-value nhỏ lưu các "cài đặt hệ thống" linh tinh không
        #     đáng để riêng 1 bảng - hiện dùng để nhớ LẦN CUỐI job dọn dẹp
        #     lịch sử phiếu chuyển kho đã chạy (xem run_transfer_archive_job).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS app_settings (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        ''')

        # 8e-2. Bảng lưu THÔNG BÁO chuyển kho cho từng cửa hàng (phục vụ
        # icon chuông trên giao diện) - mỗi dòng là 1 thông báo đã xảy ra
        # (phiếu mới / được đồng ý / bị từ chối / đã soạn hàng...), có đánh
        # dấu đã đọc hay chưa. Thông báo tự động biến mất sau 7 ngày kể từ
        # lúc xuất hiện - việc dọn dẹp được 1 job NỀN thực hiện tối đa 1
        # lần/ngày (xem run_notifications_cleanup_job), KHÔNG chạy inline
        # mỗi khi có người mở chuông ra xem nữa (trước đây làm vậy khiến
        # việc mở chuông ngày càng chậm khi bảng phình to).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                store_code VARCHAR(20) NOT NULL,
                title VARCHAR(200) NOT NULL,
                message TEXT NOT NULL,
                notif_type VARCHAR(20) NOT NULL DEFAULT 'info',
                transfer_id INTEGER,
                is_read BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_notifications_store ON notifications(store_code)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications(created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_notifications_store_read ON notifications(store_code, is_read)')

        # 8e-3. Bảng lưu THÔNG BÁO "NGÀY XE ĐI" - admin tạo 1 lần, hiển thị
        # dạng banner đỏ cho TẤT CẢ cửa hàng (khác bảng notifications ở trên
        # vốn là thông báo RIÊNG cho từng cửa hàng). Banner tự động ẩn khi
        # qua ngày departure_date (so với ngày hiện tại theo giờ VN) - xử lý
        # HOÀN TOÀN bằng điều kiện SELECT (xem get_active_truck_announcements),
        # KHÔNG xoá dòng dữ liệu, nên dữ liệu vẫn còn nguyên trong CSDL để
        # tra cứu lại lịch sử các lần xe đi trước đó. Cột active để admin có
        # thể ẩn sớm thủ công (huỷ chuyến) mà vẫn giữ lại lịch sử.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS truck_announcements (
                id SERIAL PRIMARY KEY,
                departure_date DATE NOT NULL,
                message TEXT NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by VARCHAR(50),
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_truck_announcements_date ON truck_announcements(departure_date)')

        # 8f. Nơi lưu lại các file Excel đã XUẤT RA khi dọn dẹp phiếu chuyển
        #     kho quá cũ (>1 năm) trước khi xoá hẳn khỏi transfer_requests -
        #     lưu thẳng nội dung file (file_data) vào Postgres luôn (thay vì
        #     ghi ra đĩa server) để không phụ thuộc đĩa cứng có bị mất khi
        #     redeploy hay không - admin tải lại file này bất cứ lúc nào qua
        #     /api/admin/transfer/archives/<id>/download.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transfer_archives (
                id SERIAL PRIMARY KEY,
                archived_at TIMESTAMP NOT NULL DEFAULT NOW(),
                cutoff_date TIMESTAMP NOT NULL,
                request_count INTEGER NOT NULL,
                item_count INTEGER NOT NULL,
                filename VARCHAR(255) NOT NULL,
                file_data BYTEA NOT NULL
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_transfer_archives_archived_at ON transfer_archives(archived_at)')

        # 8d. Migration từ schema CŨ (1 phiếu = đúng 1 mã hàng, có bước "Đã
        #     soạn/Chưa soạn") sang schema MỚI (1 phiếu - nhiều mã hàng,
        #     không còn bước soạn hàng). Chỉ chạy 1 lần duy nhất, tự động
        #     phát hiện qua sự tồn tại của cột "part_code" cũ trên bảng
        #     transfer_requests - nếu đã migrate rồi thì cột này không còn.
        cursor.execute('''
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'transfer_requests' AND column_name = 'part_code'
        ''')
        if cursor.fetchone():
            cursor.execute('''
                INSERT INTO transfer_items (request_id, part_code, part_name, quantity, received, received_at)
                SELECT id, part_code, part_name, quantity,
                       (status = 'approved' AND prepare_status = 'da_soan'), NULL
                FROM transfer_requests WHERE part_code IS NOT NULL
            ''')
            cursor.execute('ALTER TABLE transfer_requests DROP COLUMN IF EXISTS part_code')
            cursor.execute('ALTER TABLE transfer_requests DROP COLUMN IF EXISTS part_name')
            cursor.execute('ALTER TABLE transfer_requests DROP COLUMN IF EXISTS quantity')
            cursor.execute('ALTER TABLE transfer_requests DROP COLUMN IF EXISTS prepare_status')

        # 9b. Bảng cho tính năng KIỂM KÊ KHO - tách trong stocktake.py để
        # khỏi làm app.py dài thêm (xem hướng dẫn ở đầu file stocktake.py).
        init_stocktake_tables(cursor)

        # 9c. Bảng cho tính năng ĐỀ XUẤT TĂNG GIÁ - tách trong
        # price_adjustment.py cùng lý do (xem hướng dẫn ở đầu file đó).
        init_price_adjustment_tables(cursor)

        # 9d. Bảng cho tính năng GỢI Ý NHẬP HÀNG / CẢNH BÁO HẾT HÀNG /
        # DASHBOARD TỔNG QUAN - tách trong dashboard.py cùng lý do (xem
        # hướng dẫn ở đầu file đó).
        init_dashboard_tables(cursor)

        # 9e. Bảng cho tính năng SƠ ĐỒ KHO 3D (sàn kho / kệ / gán mã hàng
        # vào tầng kệ) - tách trong warehouse3d.py cùng lý do (xem hướng
        # dẫn ở đầu file đó).
        init_warehouse3d_tables(cursor)

        # 10. Seed default users nếu chưa có
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()['count']
        if count == 0:
            default_users = [
                ('admin', 'admin123', 'admin', 'ALL'),
                ('NS1', 'ns1123', 'store', 'NS1'),
                ('NS2', 'ns2123', 'store', 'NS2'),
                ('NS3', 'ns3123', 'store', 'NS3'),
                ('NS4', 'ns4123', 'store', 'NS4'),
                ('NS5', 'ns5123', 'store', 'NS5'),
                ('NSM1', 'nsm1123', 'store', 'NSM1'),
            ]
            # Mật khẩu mặc định chỉ dùng cho lần khởi tạo đầu tiên - LƯU DƯỚI
            # DẠNG HASH ngay từ đầu (không lưu plain text). Sau khi deploy,
            # nên đổi ngay các mật khẩu mặc định này qua trang Quản lý user.
            default_users_hashed = [
                (u, generate_password_hash(p), r, s) for (u, p, r, s) in default_users
            ]
            cursor.executemany("INSERT INTO users (username, password, role, store_code) VALUES (%s, %s, %s, %s) ON CONFLICT (username) DO NOTHING", default_users_hashed)

        db.commit()
        cursor.close()


# LƯU Ý: việc import + đăng ký blueprint stocktake, và gọi init_db(), đã
# được CHUYỂN XUỐNG CUỐI FILE (ngay phía trên "if __name__ == '__main__':").
# Lý do: stocktake.py cần import ngược lại _valid_store_codes từ app, mà
# hàm đó lại được định nghĩa RẤT XA phía dưới trong file này - đặt import
# ở đây (khi _valid_store_codes chưa tồn tại) sẽ gây ImportError.


# ==== DỌN DẸP LỊCH SỬ PHIẾU CHUYỂN KHO QUÁ CŨ (tự động, chạy định kỳ) ====
# Phiếu chuyển kho ĐÃ XONG VIỆC (đồng ý/từ chối) quá 1 năm sẽ được xuất ra
# 1 file Excel lưu vào bảng transfer_archives, rồi XOÁ HẲN khỏi
# transfer_requests/transfer_items để bảng chính luôn gọn, không phình to
# vô hạn theo thời gian. Phiếu "pending" (đang chờ xử lý) KHÔNG BAO GIỜ bị
# đụng tới dù có cũ tới đâu - an toàn dữ liệu vẫn là ưu tiên số 1.
_TRANSFER_ARCHIVE_AGE_DAYS = 365
# Job chỉ thực sự chạy nếu đã cách lần chạy trước ít nhất ngần này ngày -
# vòng lặp nền bên dưới kiểm tra mỗi ngày nhưng job tự quyết định có "tới
# hạn" hay chưa, nên dù server khởi động lại nhiều lần trong ngày cũng
# không chạy lặp lại nhiều lần.
_TRANSFER_ARCHIVE_INTERVAL_DAYS = 30
# Khoá advisory cố định của Postgres - đảm bảo nếu app chạy nhiều worker
# process (gunicorn -w nhiều) cùng lúc, chỉ 1 worker được chạy job này tại
# 1 thời điểm, tránh 2 worker cùng xuất trùng 1 lô dữ liệu.
_TRANSFER_ARCHIVE_LOCK_KEY = 918273645


def _get_app_setting(cursor, key):
    cursor.execute('SELECT value FROM app_settings WHERE key = %s', (key,))
    row = cursor.fetchone()
    return row['value'] if row else None


def _set_app_setting(cursor, key, value):
    cursor.execute('''
        INSERT INTO app_settings (key, value, updated_at) VALUES (%s, %s, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    ''', (key, value))


# Số ngày giữ lại 1 thông báo trước khi tự động bị xoá (tính từ lúc xuất
# hiện) - đúng theo yêu cầu "tự xoá sau 7 ngày".
NOTIFICATION_RETENTION_DAYS = 7


def create_notification(cursor, store_code, title, message, notif_type='info', transfer_id=None):
    """Tạo 1 dòng thông báo cho đúng 1 cửa hàng (hiện trên icon chuông) -
    gọi ngay trong CÙNG transaction với thao tác gây ra thông báo đó (tạo
    phiếu, đồng ý, từ chối, huỷ, soạn hàng xong...) để đảm bảo không bao
    giờ bị "mất" thông báo dù người dùng có đang mở web lúc đó hay không -
    khác với cách cũ (chỉ so sánh dữ liệu ở trình duyệt), thông báo giờ
    được lưu bền trong database, xem lại được bất cứ lúc nào qua chuông.

    Mốc thời gian dùng vn_now() (giờ Việt Nam, naive) chứ KHÔNG dùng NOW()
    của Postgres: server (Render...) chạy theo giờ UTC nên NOW() sẽ ghi vào
    cột TIMESTAMP một giờ lệch -7 tiếng, làm chuông hiện sai kiểu "7 giờ
    trước" ngay khi vừa tạo phiếu. Cách này cũng nhất quán với toàn bộ các
    mốc thời gian khác trong app (đều dùng vn_now())."""
    cursor.execute('''
        INSERT INTO notifications (store_code, title, message, notif_type, transfer_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (store_code, title, message, notif_type, transfer_id, vn_now()))


def cleanup_old_notifications(cursor):
    """Xoá hẳn các thông báo đã xuất hiện quá NOTIFICATION_RETENTION_DAYS
    ngày. KHÔNG gọi trực tiếp trong notifications_list() nữa (xem
    run_notifications_cleanup_job bên dưới) - chỉ còn được gọi từ job nền.

    Mốc cắt được tính bằng vn_now() ở Python (không dùng NOW() của
    Postgres) cho khớp với created_at vốn cũng ghi theo giờ VN - nếu trộn 2
    hệ giờ thì thông báo sẽ bị giữ/xoá lệch đúng 7 tiếng."""
    cursor.execute(
        "DELETE FROM notifications WHERE created_at < %s",
        (vn_now() - timedelta(days=NOTIFICATION_RETENTION_DAYS),)
    )


# Job dọn thông báo cũ chỉ thực sự chạy nếu đã cách lần chạy trước ít nhất
# ngần này ngày - cùng cơ chế throttle-qua-app_settings như job lưu trữ
# phiếu chuyển kho ở trên.
_NOTIF_CLEANUP_INTERVAL_DAYS = 1
# Khoá advisory riêng cho job này (khác _TRANSFER_ARCHIVE_LOCK_KEY) - đảm
# bảo nếu app chạy nhiều worker process cùng lúc, chỉ 1 worker chạy job
# này tại 1 thời điểm.
_NOTIF_CLEANUP_LOCK_KEY = 918273646


def run_notifications_cleanup_job(force=False):
    """Dọn thông báo quá hạn trong 1 job NỀN, chạy tối đa 1 lần/ngày.

    TRƯỚC ĐÂY việc dọn dẹp này chạy INLINE ngay trong notifications_list()
    (mỗi lần user bấm mở chuông xem thông báo là 1 lệnh DELETE quét/xoá cả
    bảng notifications) - bảng càng nhiều dữ liệu (nhất là từ khi cảnh báo
    tồn kho tự động mở rộng thêm nhóm TB, không chỉ TX) thì mở chuông càng
    chậm dần, và nhiều người mở chuông cùng lúc còn dễ phải chờ nhau do
    tranh chấp khoá ghi. Dời qua job nền để notifications_list() lúc nào
    cũng chỉ còn 2 câu SELECT nhẹ (đã có index), nhanh như nhau dù bảng có
    bao nhiêu dữ liệu lịch sử. force=True dùng khi cần chạy ngay để kiểm
    tra thủ công (không đợi đủ _NOTIF_CLEANUP_INTERVAL_DAYS kể từ lần chạy
    trước)."""
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute('SELECT pg_try_advisory_lock(%s) AS locked', (_NOTIF_CLEANUP_LOCK_KEY,))
            if not cursor.fetchone()['locked']:
                return {'status': 'skipped', 'reason': 'another worker is already running this job'}

            if not force:
                last_run = _get_app_setting(cursor, 'notifications_cleanup_last_run')
                if last_run:
                    last_run_dt = datetime.fromisoformat(last_run)
                    if datetime.now() - last_run_dt < timedelta(days=_NOTIF_CLEANUP_INTERVAL_DAYS):
                        return {'status': 'skipped', 'reason': 'not due yet', 'last_run': last_run}

            cleanup_old_notifications(cursor)
            _set_app_setting(cursor, 'notifications_cleanup_last_run', datetime.now().isoformat())
            db.commit()
            return {'status': 'ok'}
        except Exception:
            db.rollback()
            traceback.print_exc()
            return {'status': 'error'}
        finally:
            try:
                cursor.execute('SELECT pg_advisory_unlock(%s)', (_NOTIF_CLEANUP_LOCK_KEY,))
                db.commit()
            except Exception:
                pass


def _notifications_cleanup_scheduler_loop():
    """Vòng lặp NỀN: mỗi ngày kiểm tra 1 lần xem đã tới hạn dọn thông báo cũ
    chưa (run_notifications_cleanup_job tự bỏ qua nếu chưa tới hạn, nên dù
    server khởi động lại nhiều lần trong ngày cũng không chạy lặp lại)."""
    time.sleep(90)
    while True:
        try:
            run_notifications_cleanup_job()
        except Exception:
            traceback.print_exc()
        time.sleep(24 * 60 * 60)


threading.Thread(target=_notifications_cleanup_scheduler_loop, daemon=True).start()


def _build_transfer_archive_excel(requests_rows, items_by_request):
    """Đóng gói danh sách phiếu + mã hàng thành 1 file Excel 2 sheet, trả về
    bytes của file - dùng cho cả job tự động lẫn admin tải lại sau này."""
    phieu_rows = []
    chitiet_rows = []
    for r in requests_rows:
        phieu_rows.append({
            'Mã Phiếu': r['id'],
            'Cửa Hàng Xin': r['from_store'],
            'Cửa Hàng Cho': r['to_store'],
            'Trạng Thái': {'approved': 'Đã đồng ý', 'rejected': 'Đã từ chối'}.get(r['status'], r['status']),
            'Lý Do Từ Chối': r.get('reject_reason') or '',
            'Ghi Chú': r.get('note') or '',
            'Người Tạo': r.get('created_by') or '',
            'Nhân Viên Tạo': r.get('created_employee') or '',
            'Người Phản Hồi': r.get('responded_by') or '',
            'Nhân Viên Xác Nhận': r.get('confirmed_employee') or '',
            'Thời Gian Tạo': r.get('created_at'),
            'Thời Gian Phản Hồi': r.get('responded_at'),
            'Cập Nhật Lần Cuối': r.get('updated_at'),
        })
        for it in items_by_request.get(r['id'], []):
            chitiet_rows.append({
                'Mã Phiếu': r['id'],
                'Mã Hàng': it['part_code'],
                'Tên Hàng': it.get('part_name') or '',
                'SL Xin': it.get('quantity'),
                'SL Được Cho': it.get('approved_quantity'),
                'Đã Nhận': 'Có' if it.get('received') else 'Không',
                'Thời Gian Nhận': it.get('received_at'),
            })

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        pd.DataFrame(phieu_rows).to_excel(writer, sheet_name='Phieu', index=False)
        pd.DataFrame(chitiet_rows).to_excel(writer, sheet_name='Chi Tiet Ma Hang', index=False)
    return buffer.getvalue()


def run_transfer_archive_job(force=False):
    """Chạy job dọn dẹp phiếu chuyển kho quá 1 năm - xuất Excel lưu lại rồi
    xoá khỏi bảng chính. force=True dùng khi cần chạy ngay để kiểm tra thủ
    công (không đợi đủ _TRANSFER_ARCHIVE_INTERVAL_DAYS kể từ lần chạy
    trước). Trả về dict mô tả kết quả để log/kiểm tra."""
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute('SELECT pg_try_advisory_lock(%s) AS locked', (_TRANSFER_ARCHIVE_LOCK_KEY,))
            if not cursor.fetchone()['locked']:
                return {'status': 'skipped', 'reason': 'another worker is already running this job'}

            if not force:
                last_run = _get_app_setting(cursor, 'transfer_archive_last_run')
                if last_run:
                    last_run_dt = datetime.fromisoformat(last_run)
                    if datetime.now() - last_run_dt < timedelta(days=_TRANSFER_ARCHIVE_INTERVAL_DAYS):
                        return {'status': 'skipped', 'reason': 'not due yet', 'last_run': last_run}

            cutoff = datetime.now() - timedelta(days=_TRANSFER_ARCHIVE_AGE_DAYS)
            cursor.execute('''
                SELECT * FROM transfer_requests
                WHERE status IN ('approved', 'rejected') AND updated_at < %s
                ORDER BY id
            ''', (cutoff,))
            rows = cursor.fetchall()

            result = {'status': 'ok', 'cutoff': cutoff.isoformat(), 'request_count': len(rows)}

            if rows:
                items_by_request = _fetch_transfer_items(cursor, [r['id'] for r in rows])
                item_count = sum(len(v) for v in items_by_request.values())
                file_bytes = _build_transfer_archive_excel(
                    [_serialize_transfer_row(r, items_by_request.get(r['id'], []), None, 'admin') for r in rows],
                    items_by_request,
                )
                filename = f"luu_tru_phieu_chuyen_kho_{datetime.now():%Y%m%d_%H%M%S}.xlsx"

                cursor.execute('''
                    INSERT INTO transfer_archives (cutoff_date, request_count, item_count, filename, file_data)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (cutoff, len(rows), item_count, filename, psycopg2.Binary(file_bytes)))

                cursor.execute('DELETE FROM transfer_requests WHERE id = ANY(%s)', ([r['id'] for r in rows],))
                result['item_count'] = item_count
                result['filename'] = filename

            _set_app_setting(cursor, 'transfer_archive_last_run', datetime.now().isoformat())
            db.commit()
            return result
        except Exception:
            db.rollback()
            traceback.print_exc()
            return {'status': 'error'}
        finally:
            try:
                cursor.execute('SELECT pg_advisory_unlock(%s)', (_TRANSFER_ARCHIVE_LOCK_KEY,))
                db.commit()
            except Exception:
                pass


def _transfer_archive_scheduler_loop():
    """Vòng lặp chạy NỀN suốt vòng đời process: mỗi ngày kiểm tra 1 lần xem
    job dọn dẹp đã tới hạn (_TRANSFER_ARCHIVE_INTERVAL_DAYS) chưa - bản thân
    run_transfer_archive_job() tự quyết định bỏ qua nếu chưa tới hạn, nên
    việc kiểm tra mỗi ngày ở đây chỉ tốn 1 câu SELECT rất nhẹ, không có gì
    phải lo về hiệu năng."""
    # Đợi 1 chút sau khi app khởi động để không cạnh tranh tài nguyên/kết
    # nối DB với các request đầu tiên của người dùng.
    time.sleep(60)
    while True:
        try:
            run_transfer_archive_job()
        except Exception:
            traceback.print_exc()
        time.sleep(24 * 60 * 60)


threading.Thread(target=_transfer_archive_scheduler_loop, daemon=True).start()


def clean_str(val):
    if pd.isna(val):
        return ""
    return str(val).strip().upper()


def find_col(columns, patterns):
    lower_map = {c: str(c).strip().lower() for c in columns}
    for pattern in patterns:
        for c in columns:
            if pattern in lower_map[c]:
                return c
    return None


def _looks_usable(df):
    """Một DataFrame được coi là hợp lệ khi có nhiều hơn 1 cột (tức là đã
    tách đúng dấu phân cách, không bị dồn hết dữ liệu vào 1 cột)."""
    return df is not None and df.shape[1] > 1


def read_csv_robust(file_storage):
    """
    Đọc file CSV một cách an toàn, chống được các lỗi thường gặp:
    - Sai bảng mã (encoding) tiếng Việt.
    - File được lưu ở dạng UTF-16 (Unicode) - dấu hiệu là 2 byte BOM đầu
      file 0xFF 0xFE hoặc 0xFE 0xFF. Nếu bỏ qua trường hợp này, các bảng mã
      1-byte (latin1, cp1252...) vẫn "đọc thành công" nhưng ra toàn ký tự
      rác xen kẽ \\x00, khiến không tìm được cột nào cả.
    - Dấu phân cách không phải dấu phẩy (chấm phẩy, tab...).
    - File xuất ra có 1-2 dòng tiêu đề/giới thiệu phía trên dòng header thật,
      khiến pandas suy luận nhầm số cột ở những dòng đầu rồi báo lỗi kiểu
      "Error tokenizing data. Expected 1 fields in line 3, saw 3".
    - Một vài dòng lỗi định dạng rải rác trong file.
    """
    encodings = ['utf-8-sig', 'utf-8', 'latin1', 'cp1258', 'cp1252']

    # Ưu tiên thử UTF-16 trước nếu phát hiện BOM tương ứng (thường gặp khi
    # file CSV được xuất ra từ các hệ thống/Excel trên Windows ở định dạng
    # "Unicode Text").
    file_storage.seek(0)
    head = file_storage.read(4)
    file_storage.seek(0)
    if head[:2] in (b'\xff\xfe', b'\xfe\xff'):
        encodings = ['utf-16'] + encodings
    elif head[:4] in (b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff'):
        encodings = ['utf-32'] + encodings

    last_err = None

    for enc in encodings:
        # 1) Thử đọc bình thường, để pandas tự dò dấu phân cách (sep=None)
        try:
            file_storage.seek(0)
            df = pd.read_csv(file_storage, encoding=enc, sep=None, engine='python')
            if _looks_usable(df):
                return df
        except Exception as e:
            last_err = e

        # 2) Nếu file có vài dòng tiêu đề rác phía trên header thật, thử bỏ
        #    qua lần lượt 1-5 dòng đầu để tìm đúng dòng header
        for skip in range(1, 6):
            try:
                file_storage.seek(0)
                df = pd.read_csv(file_storage, encoding=enc, sep=None, engine='python', skiprows=skip)
                if _looks_usable(df):
                    return df
            except Exception as e:
                last_err = e

        # 3) Cuối cùng, chấp nhận bỏ qua các dòng lỗi định dạng rải rác
        try:
            file_storage.seek(0)
            df = pd.read_csv(file_storage, encoding=enc, on_bad_lines='skip', engine='python')
            if df is not None and df.shape[1] >= 1:
                return df
        except Exception as e:
            last_err = e

    raise ValueError(f"Không thể đọc được file CSV (định dạng/bảng mã không hợp lệ). Chi tiết: {last_err}")


# Các từ khoá tiêu đề cột thường gặp trong 3 loại file (Danh sách PO,
# Chi tiết PO, Chi tiết nhận hàng). Dùng để dò đúng dòng header thật khi
# file Excel/CSV có vài dòng tiêu đề/giới thiệu rác phía trên.
_HEADER_KEYWORDS = [
    'order number', 'mã đơn hàng mua', 'mã đơn hàng', 'mã po', 'po number',
    'siebel po number', 'part#', 'part #', 'part number', 'mã phụ tùng',
    'quantity requested', 'số lượng yêu cầu', 'số lượng', 'quantity',
    'ngày tạo đơn hàng mua', 'ngày tạo', 'ngày đặt',
    'ngày gửi đơn đặt hàng', 'trạng thái đơn hàng mua', 'trạng thái đơn hàng',
    'trạng thái po', 'mrn status', 'trạng thái', 'status', 'part',
    'phân loại đơn hàng phụ tùng', 'phân loại đơn hàng', 'loại đơn hàng', 'order type',
]


def _clean_col_name(c):
    # Loại bỏ khoảng trắng thường + khoảng trắng không ngắt dòng (\xa0) + BOM
    return str(c).replace('\ufeff', '').replace('\xa0', ' ').strip()


def parse_mixed_vn_datetime(series):
    """Phân tích cột ngày tháng có thể lẫn 2 KIỂU DỮ LIỆU khác nhau trong
    CÙNG 1 CỘT - lỗi thường gặp ở file "Danh sách PO" do hệ thống Honda/SAP
    xuất ra (đã xác nhận qua file mẫu thực tế của khách hàng):

      - Ô là CHUỖI TEXT dạng "DD/MM/YYYY hh:mm:ss AM/PM" (ngày > 12, VD
        27/08/2026) -> Excel không tự nhận diện được là ngày (vì 27 không
        thể là tháng), nên giữ nguyên dạng text. Đọc bình thường với
        dayfirst=True là ra đúng kết quả.

      - Ô đã bị CHÍNH EXCEL tự động hiểu nhầm thành 1 giá trị ngày thật
        (kiểu datetime) khi ngày gốc <= 12 (VD 01/06/2026) - vì lúc đó cả
        2 số đều <= 12 nên hợp lệ để hiểu theo kiểu Mỹ (MM/DD), Excel áp
        NHẦM ngày <-> tháng cho nhau: "01/06/2026" (1 tháng 6) bị lưu
        thành datetime(2026, 1, 6) tức 6 tháng 1 - hoàn toàn sai lệch so
        với ý định ban đầu. Với các ô này, cần hoán đổi lại NGÀY <-> THÁNG
        để khôi phục đúng giá trị gốc trước khi tính toán.

    Nếu không xử lý riêng 2 trường hợp này, các PO có ngày <= 12 (như
    01/06, 03/08, 08/06...) sẽ bị gán NHẦM sang tháng khác hẳn, khiến
    người dùng tưởng nhầm là "quét thiếu" dữ liệu ngày đó - trong khi thực
    ra dữ liệu vẫn có, chỉ bị gắn sai ngày.

    Trả về Series kiểu datetime64, giá trị không đọc được sẽ là NaT."""
    def _fix_one(v):
        if v is None:
            return pd.NaT
        try:
            if pd.isna(v):
                return pd.NaT
        except (TypeError, ValueError):
            pass
        if isinstance(v, str):
            return pd.to_datetime(v, dayfirst=True, errors='coerce')
        if isinstance(v, (datetime, pd.Timestamp)):
            try:
                # Hoán đổi lại ngày <-> tháng mà Excel đã lỡ đảo ngược.
                return v.replace(day=v.month, month=v.day)
            except ValueError:
                # Trường hợp hiếm: hoán đổi ra ngày/tháng không hợp lệ (vd
                # tháng không có đủ ngày đó) - giữ nguyên giá trị gốc thay
                # vì làm mất hẳn dữ liệu.
                return pd.Timestamp(v)
        return pd.to_datetime(v, dayfirst=True, errors='coerce')

    return series.map(_fix_one)


def _find_best_header_row(raw_df, max_scan=10):
    """Quét (tối đa) max_scan dòng đầu của 1 DataFrame đọc thô (header=None)
    để tìm dòng nào giống dòng tiêu đề cột nhất, dựa trên số từ khoá cột
    quen thuộc xuất hiện trong dòng đó."""
    best_idx, best_score = None, 0
    for i in range(min(max_scan, len(raw_df))):
        row_vals = [_clean_col_name(v).lower() for v in raw_df.iloc[i].tolist()]
        score = sum(1 for kw in _HEADER_KEYWORDS if any(kw in v for v in row_vals))
        if score > best_score:
            best_score, best_idx = score, i
    return best_idx if best_score > 0 else None


def _looks_like_bad_header(df):
    """True nếu phần lớn tên cột bị đọc sai (dạng 'Unnamed: n' hoặc rỗng),
    dấu hiệu cho thấy dòng header thật không phải dòng đầu tiên."""
    if df is None or df.shape[1] == 0:
        return True
    bad = sum(1 for c in df.columns if str(c).strip() == '' or str(c).startswith('Unnamed'))
    return bad / df.shape[1] > 0.3


def read_any(file_storage):
    """
    Hàm đọc file thông minh: Tự động nhận diện file Excel hoặc tự động thử
    các bảng mã/định dạng của file CSV để chống lỗi mã hóa và lỗi cấu trúc.
    Đồng thời tự động dò đúng dòng tiêu đề thật nếu phía trên có vài dòng
    tiêu đề/giới thiệu rác (áp dụng cho cả Excel lẫn CSV).
    """
    filename = (file_storage.filename or "").lower()

    def _read_excel_smart():
        file_storage.seek(0)
        df = pd.read_excel(file_storage)
        if _looks_like_bad_header(df):
            file_storage.seek(0)
            raw = pd.read_excel(file_storage, header=None)
            header_idx = _find_best_header_row(raw)
            if header_idx is not None:
                file_storage.seek(0)
                df = pd.read_excel(file_storage, header=header_idx)
        return df

    if filename.endswith(('.xlsx', '.xls')):
        df = _read_excel_smart()
    elif filename.endswith('.csv'):
        df = read_csv_robust(file_storage)
        if _looks_like_bad_header(df):
            file_storage.seek(0)
            raw = pd.read_csv(file_storage, sep=None, engine='python', header=None)
            header_idx = _find_best_header_row(raw)
            if header_idx is not None:
                file_storage.seek(0)
                df = pd.read_csv(file_storage, sep=None, engine='python', header=header_idx)
    else:
        # Trường hợp định dạng khác, thử đọc như Excel trước, lỗi thì đọc như CSV
        try:
            df = _read_excel_smart()
        except Exception:
            file_storage.seek(0)
            df = read_csv_robust(file_storage)

    df.columns = [_clean_col_name(c) for c in df.columns]
    return df


# ----------------------------------------------------------------------------
# TỒN KHO HỆ THỐNG - import file "Tổng hợp tồn kho"
# ----------------------------------------------------------------------------

# Chỉ lấy các mã kho thuộc 3 nhóm này (tiền tố), các mã kho khác (KBD, KX,
# KKM, KHO151, KHONDAKM, ...) sẽ bị bỏ qua - riêng "KHANGCHAMBAN" (1 kho độc
# lập, không theo dạng tiền tố này) vẫn được lấy riêng, xem
# _SINGLE_WORD_WAREHOUSES bên dưới.
_INVENTORY_ALLOWED_PREFIXES = {'KPT', 'KPK', 'KPTN'}
# Hậu tố tương ứng với 6 cửa hàng - trùng với store_code trong bảng users.
_INVENTORY_ALLOWED_STORES = {'NS1', 'NS2', 'NS3', 'NS4', 'NS5', 'NSM1'}
# Hậu tố "PI2" (ví dụ 'KPT PI2', 'KPK PI2', 'KPTN PI2') thực chất là kho phụ
# của NSM1 - gộp thẳng vào tồn kho NSM1 (cộng dồn số lượng nếu mã hàng đó
# cũng có ở kho 'KPT/KPK/KPTN NSM1') để NSM1 vừa nhìn thấy đủ tồn, vừa tự có
# quyền sửa vị trí kệ hàng cho các mã hàng này (quyền sửa vị trí của store
# dựa theo đúng bảng inventory_items của store đó - xem save_location()).
_SUFFIX_ALIAS_TO_STORE = {'PI2': 'NSM1'}

# "KHANGCHAMBAN" là 1 kho ĐỘC LẬP, hiển thị dưới tên "Kho CB" trong bảng Tồn
# Kho - KHÁC với các cửa hàng NS1..NSM1 ở chỗ: (1) cột "Mã kho" trong file
# Excel chỉ có ĐÚNG 1 TỪ (không theo dạng "TIỀN TỐ HẬU TỐ" như "KPT NS1" nên
# không đi qua bước tách prefix/suffix + lọc _INVENTORY_ALLOWED_PREFIXES/
# _INVENTORY_ALLOWED_STORES ở dưới - tự nó đã là định danh đầy đủ); (2) đây
# CHỈ là 1 kho để XEM số lượng tồn - không có tài khoản đăng nhập riêng,
# không tham gia luân chuyển nội bộ/vị trí hàng hóa/kiểm kê như các cửa hàng
# thật (NS1..NSM1), nên KHÔNG được thêm vào _valid_store_codes()/STORE_REGIONS.
_SINGLE_WORD_WAREHOUSES = {'KHANGCHAMBAN': 'CB'}


def _split_warehouse_code(raw_kho):
    """Tách 'KPT NS1' -> ('KPT', 'NS1'). Trả về (None, None) nếu không đúng
    định dạng "TIỀN TỐ HẬU TỐ" (ví dụ dòng 'Tổng cộng')."""
    parts = str(raw_kho).strip().upper().split()
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]


def parse_inventory_excel(file_storage):
    """
    Đọc file "Tổng hợp tồn kho" (định dạng đặc thù: 2-3 dòng tiêu đề rác phía
    trên + 2 dòng header bị merge ô + dữ liệu). Không dùng read_any() thông
    thường vì cấu trúc header ở đây khác hẳn (2 dòng header lồng nhau kiểu
    "Cuối kỳ" -> "Số lượng"/"Giá trị").

    Tự động dò:
      - Dòng header chính (dòng có ô đầu tiên là "Mã kho").
      - Cột "Cuối kỳ" - "Số lượng" bằng cách quét 2 dòng header, không hardcode
        cứng theo số thứ tự cột (K), để không bị vỡ nếu form Excel đổi bố cục.

    Trả về danh sách dict: {part_code, part_name, unit, store_code, quantity}
    (đã lọc chỉ giữ mã kho thuộc các mã cho phép - riêng hậu tố "PI2" được
    quy đổi thành "NSM1"), kèm số dòng bị bỏ qua và danh sách cảnh báo (nếu
    1 mã hàng bị trùng ở nhiều nhóm tiền tố khác nhau - trường hợp hiếm, sẽ
    cộng dồn số lượng lại thay vì ghi đè).
    """
    file_storage.seek(0)
    # engine_kwargs={'read_only': True}: yêu cầu openpyxl chỉ đọc GIÁ TRỊ ô,
    # bỏ qua toàn bộ style/định dạng/màu sắc/border (thường rất nhiều trong
    # file "Tổng hợp tồn kho" xuất từ hệ thống) - đây thường là phần tốn thời
    # gian + bộ nhớ nhất khi đọc file Excel lớn bằng pandas, dù app không hề
    # dùng tới thông tin định dạng đó. Bọc try/except vì tham số engine_kwargs
    # chỉ có từ pandas >= 1.3 - nếu server đang chạy bản pandas cũ hơn, tự
    # động rơi về cách đọc mặc định (chậm hơn nhưng vẫn đúng).
    try:
        raw = pd.read_excel(
            file_storage, header=None, dtype=object,
            engine='openpyxl', engine_kwargs={'read_only': True},
        )
    except TypeError:
        file_storage.seek(0)
        raw = pd.read_excel(file_storage, header=None, dtype=object)

    # 1) Tìm dòng header chính: ô đầu tiên (đã strip/upper) = "MÃ KHO"
    header_row = None
    for i in range(min(15, len(raw))):
        first_cell = raw.iat[i, 0]
        if first_cell is not None and str(first_cell).strip().upper() == 'MÃ KHO':
            header_row = i
            break
    if header_row is None:
        raise ValueError('Không tìm thấy dòng tiêu đề "Mã kho" trong file. Vui lòng kiểm tra lại đúng file "Tổng hợp tồn kho".')

    # 2) Dò cột "Cuối kỳ" - "Số lượng" bằng cách quét dòng header chính (tên
    #    nhóm cột, ví dụ "Cuối kỳ" chỉ xuất hiện ở ô đầu tiên của nhóm do bị
    #    merge - các ô còn lại là NaN) và dòng ngay dưới nó (tên cột con,
    #    "Số lượng"/"Giá trị").
    group_row = raw.iloc[header_row]
    sub_row = raw.iloc[header_row + 1] if header_row + 1 < len(raw) else None

    qty_col = None
    current_group = ''
    for c in range(raw.shape[1]):
        cell = group_row.iat[c]
        if cell is not None and str(cell).strip() != '' and str(cell).strip().lower() != 'nan':
            current_group = str(cell).strip().lower()
        if 'cuối kỳ' in current_group and sub_row is not None:
            sub_cell = sub_row.iat[c]
            if sub_cell is not None and 'số lượng' in str(sub_cell).strip().lower():
                qty_col = c
                break
    if qty_col is None:
        raise ValueError('Không tìm thấy cột "Cuối kỳ - Số lượng" trong file.')

    # 3) Dữ liệu bắt đầu sau 2 dòng header (header_row + 2)
    data_start = header_row + 2
    kho_col, part_col, name_col, unit_col = 0, 1, 2, 3

    # ------------------------------------------------------------------
    # Xử lý VECTOR HOÁ bằng pandas thay vì lặp từng dòng bằng Python (vòng
    # lặp for + .iat cho mỗi ô rất chậm với file nhiều nghìn dòng, đặc biệt
    # trên Render free tier có CPU rất yếu - từng gây timeout/crash worker).
    # ------------------------------------------------------------------
    data = raw.iloc[data_start:, [kho_col, part_col, name_col, unit_col, qty_col]].copy()
    data.columns = ['kho', 'part_code', 'part_name', 'unit', 'qty']

    data['kho'] = data['kho'].astype(str).str.strip()
    data['part_code'] = data['part_code'].astype(str).str.strip()
    valid_mask = (
        data['kho'].notna() & data['part_code'].notna()
        & (data['kho'] != '') & (data['kho'].str.lower() != 'none')
        & (data['part_code'] != '') & (data['part_code'].str.lower() != 'none')
    )
    data = data[valid_mask]

    # Tách riêng các dòng "Mã kho" thuộc nhóm KHO ĐỘC LẬP 1-TỪ (vd
    # "KHANGCHAMBAN" -> "Kho CB") ra khỏi luồng xử lý "TIỀN TỐ HẬU TỐ" thông
    # thường bên dưới - phải làm TRƯỚC bước tách 2 từ (str.split()), vì các
    # mã kho 1 từ này vốn dĩ sẽ bị coi là "sai định dạng" nếu để lẫn vào đó.
    kho_upper_full = data['kho'].str.upper()
    single_word_mask = kho_upper_full.isin(_SINGLE_WORD_WAREHOUSES.keys())
    single_word_data = data[single_word_mask].copy()
    single_word_data['store_code'] = kho_upper_full[single_word_mask].map(_SINGLE_WORD_WAREHOUSES)
    single_word_data['is_pi2'] = False
    data = data[~single_word_mask]

    # Tách "KPT NS1" -> prefix="KPT", suffix="NS1". Chỉ nhận dòng có đúng 2
    # từ (giống hệt logic _split_warehouse_code cũ).
    kho_parts = data['kho'].str.upper().str.split()
    valid_len_mask = kho_parts.str.len() == 2
    skipped_rows = int((~valid_len_mask).sum())
    data = data[valid_len_mask]
    kho_parts = kho_parts[valid_len_mask]
    data['prefix'] = kho_parts.str[0]
    data['suffix'] = kho_parts.str[1]
    # Đánh dấu dòng nào vốn dĩ thuộc hậu tố "PI2" (TRƯỚC khi quy đổi sang
    # "NSM1" ở dưới) - dùng để sau này biết dòng tồn kho NSM1 nào có gộp số
    # lượng từ kho phụ PI2, phục vụ việc xếp các mã hàng này xuống cuối bảng.
    data['is_pi2'] = data['suffix'].isin(_SUFFIX_ALIAS_TO_STORE.keys())
    # Quy đổi hậu tố "PI2" thành "NSM1" NGAY TỪ ĐÂY, để các bước cộng dồn số
    # lượng/lọc hợp lệ phía dưới coi 'KPT PI2' như thể nó là 'KPT NSM1'.
    data['suffix'] = data['suffix'].replace(_SUFFIX_ALIAS_TO_STORE)

    allowed_mask = data['prefix'].isin(_INVENTORY_ALLOWED_PREFIXES) & data['suffix'].isin(_INVENTORY_ALLOWED_STORES)
    skipped_rows += int((~allowed_mask).sum())
    data = data[allowed_mask]
    data = data.rename(columns={'suffix': 'store_code'})
    data = data.drop(columns=['prefix'])

    # Gộp lại 2 nhóm (cửa hàng NS1..NSM1 theo tiền tố/hậu tố + kho độc lập
    # 1-từ như "Kho CB") thành 1 DataFrame chung để cùng đi qua bước cộng
    # dồn số lượng theo (Mã hàng, store_code) phía dưới.
    if not single_word_data.empty:
        single_word_data = single_word_data.drop(columns=['kho'], errors='ignore')
        data = pd.concat([data, single_word_data], ignore_index=True, sort=False)

    data['part_name'] = data['part_name'].fillna('').astype(str).str.strip()
    data['unit'] = data['unit'].fillna('').astype(str).str.strip()
    data['qty'] = pd.to_numeric(data['qty'], errors='coerce').fillna(0.0)

    warnings = []
    rows = []

    if not data.empty:
        # Tên/đơn vị: lấy theo lần xuất hiện ĐẦU TIÊN của mỗi mã hàng trong
        # file (giữ đúng thứ tự gốc) - giống hành vi parts_map.setdefault cũ.
        name_unit = data.groupby('part_code', sort=False).agg(
            part_name=('part_name', 'first'),
            unit=('unit', 'first'),
        )

        # Số lượng: cộng dồn theo (Mã hàng, Cửa hàng/Kho) - xử lý trường hợp 1
        # mã hàng xuất hiện ở nhiều nhóm tiền tố kho khác nhau nhưng cùng 1
        # cửa hàng, hoặc nhiều dòng cùng thuộc 1 kho độc lập như "Kho CB"
        # (cộng dồn thay vì ghi đè, giống logic cũ). is_pi2 = có ÍT NHẤT 1
        # dòng nguồn thuộc hậu tố PI2 hay không (any) - luôn False với "Kho CB".
        qty_grouped = data.groupby(['part_code', 'store_code'], sort=False).agg(
            quantity=('qty', 'sum'),
            n=('qty', 'size'),
            is_pi2=('is_pi2', 'any'),
        ).reset_index()

        dup_rows = qty_grouped[qty_grouped['n'] > 1]
        warnings = [
            f'Mã hàng {r.part_code} tại {r.store_code} xuất hiện ở nhiều nhóm kho khác nhau - đã cộng dồn số lượng.'
            for r in dup_rows.itertuples()
        ]

        result = qty_grouped.merge(name_unit, left_on='part_code', right_index=True, how='left')
        rows = result[['part_code', 'part_name', 'unit', 'store_code', 'quantity', 'is_pi2']].to_dict(orient='records')

    return rows, skipped_rows, warnings


# ---------------------------------------------------------------------------
# THỐNG KÊ TẦN SUẤT BÁN (Thường xuyên / Trung bình / Chậm bán)
# ---------------------------------------------------------------------------
# Ngưỡng phân loại theo "số tháng tồn" = Tồn hiện tại / (Tổng xuất kho trong
# kỳ / số tháng của kỳ). Đây là chỉ số chuẩn "Months of Inventory" dùng phổ
# biến trong ngành phụ tùng/bán lẻ để đánh giá tốc độ quay vòng hàng tồn.
SALES_FREQ_TX_MAX_MONTHS = 2   # Tồn đủ bán <= 2 tháng -> Thường xuyên (TX)
SALES_FREQ_TB_MAX_MONTHS = 6   # Tồn đủ bán 2-6 tháng -> Trung bình (TB); > 6 -> Chậm bán (CB)

SALES_FREQ_LABELS = {
    'TX': 'Thường xuyên',
    'TB': 'Trung bình',
    'CB': 'Chậm bán',
}

# Các nhóm mã hàng KHÔNG được phép đặt thêm (không thuộc diện quản lý qua
# gợi ý nhập hàng/cảnh báo hết hàng/thống kê tần suất bán) - ví dụ nhóm
# khung xe (mã bắt đầu bằng "50100") do quy trình đặt hàng của nhóm này
# khác, không đi qua kho như phụ tùng thường. Khớp CHÍNH XÁC theo tiền tố
# (vd "50100" khớp "50100K2CV01" nhưng KHÔNG khớp "501001" hay "5010099" -
# thật ra "5010099" vẫn khớp vì cùng bắt đầu bằng "50100"; chỉ mã KHÔNG bắt
# đầu bằng đúng chuỗi "50100" mới không khớp, vd "501" hay "5010" thì không).
# Lọc ngay tại nguồn (_compute_sales_frequency_rows) để áp dụng nhất quán
# cho MỌI nơi dùng chung dữ liệu này: trang Thống Kê Tần Suất Bán, Gợi Ý
# Nhập Hàng, và Cảnh Báo Hết Hàng tự động (dashboard.py).
EXCLUDED_REORDER_PART_CODE_PREFIXES = ('50100',)


def _is_excluded_from_reorder(part_code):
    part_code = part_code or ''
    return any(part_code.startswith(p) for p in EXCLUDED_REORDER_PART_CODE_PREFIXES)


def classify_sales_frequency(qty_on_hand, qty_sold_period, period_months):
    """Phân loại 1 mã hàng theo tần suất bán, dựa trên tồn hiện tại và tổng
    số lượng đã bán trong kỳ (period_months tháng, mặc định kỳ nhập là 3
    tháng theo file "Tổng hợp tồn kho").

    Trả về dict {code, label, avg_month, months_of_stock} hoặc None nếu
    không đủ điều kiện hiển thị icon (tồn <= 0 - theo đúng yêu cầu: mã còn
    tồn mới cần theo dõi phân loại chậm/nhanh bán).
    """
    qty_on_hand = float(qty_on_hand or 0)
    qty_sold_period = float(qty_sold_period or 0)
    period_months = float(period_months or 3) or 3

    if qty_on_hand <= 0:
        return None  # Không có tồn -> không hiện icon

    avg_month = qty_sold_period / period_months

    # Bán = 0 trong cả kỳ nhưng vẫn còn tồn -> luôn CB (không chia cho 0,
    # và rõ ràng không có nhu cầu dù tồn ít).
    if qty_sold_period <= 0:
        return {'code': 'CB', 'label': SALES_FREQ_LABELS['CB'], 'avg_month': 0.0, 'months_of_stock': None}

    months_of_stock = qty_on_hand / avg_month

    if months_of_stock <= SALES_FREQ_TX_MAX_MONTHS:
        code = 'TX'
    elif months_of_stock <= SALES_FREQ_TB_MAX_MONTHS:
        code = 'TB'
    else:
        code = 'CB'

    return {
        'code': code,
        'label': SALES_FREQ_LABELS[code],
        'avg_month': round(avg_month, 2),
        'months_of_stock': round(months_of_stock, 2),
    }


def parse_sales_export_excel(file_storage):
    """Đọc file "Tổng hợp tồn kho" (CÙNG định dạng với parse_inventory_excel)
    nhưng lấy cột "Xuất kho - SL bán hàng" thay vì "Cuối kỳ - Số lượng", để
    nạp số liệu bán ra phục vụ thống kê tần suất bán. File này do admin
    import RIÊNG, KHI CẦN (không tự động theo tuần/tháng), nên cần cho biết
    kỳ báo cáo trong file tương ứng bao nhiêu tháng (period_months) - đọc từ
    dòng "Từ ngày ... đến ngày ..." ở đầu file nếu có, mặc định 3 tháng nếu
    không dò được.

    Trả về (rows, skipped_rows, warnings, period_months) - rows là danh sách
    dict {part_code, part_name, unit, store_code, qty_sold}.
    """
    import re

    file_storage.seek(0)
    try:
        raw = pd.read_excel(
            file_storage, header=None, dtype=object,
            engine='openpyxl', engine_kwargs={'read_only': True},
        )
    except TypeError:
        file_storage.seek(0)
        raw = pd.read_excel(file_storage, header=None, dtype=object)

    # 0) Dò số tháng của kỳ báo cáo từ dòng "Từ ngày dd/mm/yyyy đến ngày
    #    dd/mm/yyyy" (thường ở dòng thứ 2 của file, phía trên header).
    period_months = 3
    for i in range(min(5, len(raw))):
        cell = raw.iat[i, 0]
        if cell is None:
            continue
        text = str(cell)
        m = re.search(r'(\d{2})/(\d{2})/(\d{4}).*?(\d{2})/(\d{2})/(\d{4})', text)
        if m:
            d1, mo1, y1, d2, mo2, y2 = (int(x) for x in m.groups())
            months = (y2 - y1) * 12 + (mo2 - mo1) + 1
            if months > 0:
                period_months = months
            break

    header_row = None
    for i in range(min(15, len(raw))):
        first_cell = raw.iat[i, 0]
        if first_cell is not None and str(first_cell).strip().upper() == 'MÃ KHO':
            header_row = i
            break
    if header_row is None:
        raise ValueError('Không tìm thấy dòng tiêu đề "Mã kho" trong file. Vui lòng kiểm tra lại đúng file "Tổng hợp tồn kho".')

    group_row = raw.iloc[header_row]
    sub_row = raw.iloc[header_row + 1] if header_row + 1 < len(raw) else None

    export_col = None
    current_group = ''
    for c in range(raw.shape[1]):
        cell = group_row.iat[c]
        if cell is not None and str(cell).strip() != '' and str(cell).strip().lower() != 'nan':
            current_group = str(cell).strip().lower()
        if 'xuất kho' in current_group and sub_row is not None:
            sub_cell = sub_row.iat[c]
            if sub_cell is not None and 'bán hàng' in str(sub_cell).strip().lower():
                export_col = c
                break
    if export_col is None:
        raise ValueError('Không tìm thấy cột "Xuất kho - SL bán hàng" trong file.')

    data_start = header_row + 2
    kho_col, part_col, name_col, unit_col = 0, 1, 2, 3

    data = raw.iloc[data_start:, [kho_col, part_col, name_col, unit_col, export_col]].copy()
    data.columns = ['kho', 'part_code', 'part_name', 'unit', 'qty']

    data['kho'] = data['kho'].astype(str).str.strip()
    data['part_code'] = data['part_code'].astype(str).str.strip()
    valid_mask = (
        data['kho'].notna() & data['part_code'].notna()
        & (data['kho'] != '') & (data['kho'].str.lower() != 'none')
        & (data['part_code'] != '') & (data['part_code'].str.lower() != 'none')
    )
    data = data[valid_mask]

    kho_upper_full = data['kho'].str.upper()
    single_word_mask = kho_upper_full.isin(_SINGLE_WORD_WAREHOUSES.keys())
    single_word_data = data[single_word_mask].copy()
    single_word_data['store_code'] = kho_upper_full[single_word_mask].map(_SINGLE_WORD_WAREHOUSES)
    data = data[~single_word_mask]

    kho_parts = data['kho'].str.upper().str.split()
    valid_len_mask = kho_parts.str.len() == 2
    skipped_rows = int((~valid_len_mask).sum())
    data = data[valid_len_mask]
    kho_parts = kho_parts[valid_len_mask]
    data['prefix'] = kho_parts.str[0]
    data['suffix'] = kho_parts.str[1]
    data['suffix'] = data['suffix'].replace(_SUFFIX_ALIAS_TO_STORE)

    allowed_mask = data['prefix'].isin(_INVENTORY_ALLOWED_PREFIXES) & data['suffix'].isin(_INVENTORY_ALLOWED_STORES)
    skipped_rows += int((~allowed_mask).sum())
    data = data[allowed_mask]
    data = data.rename(columns={'suffix': 'store_code'})
    data = data.drop(columns=['prefix'])

    if not single_word_data.empty:
        single_word_data = single_word_data.drop(columns=['kho'], errors='ignore')
        data = pd.concat([data, single_word_data], ignore_index=True, sort=False)

    data['part_name'] = data['part_name'].fillna('').astype(str).str.strip()
    data['unit'] = data['unit'].fillna('').astype(str).str.strip()
    data['qty'] = pd.to_numeric(data['qty'], errors='coerce').fillna(0.0)

    warnings = []
    rows = []
    if not data.empty:
        name_unit = data.groupby('part_code', sort=False).agg(
            part_name=('part_name', 'first'),
            unit=('unit', 'first'),
        )
        qty_grouped = data.groupby(['part_code', 'store_code'], sort=False).agg(
            qty_sold=('qty', 'sum'),
        ).reset_index()
        result = qty_grouped.merge(name_unit, left_on='part_code', right_index=True, how='left')
        rows = result[['part_code', 'part_name', 'unit', 'store_code', 'qty_sold']].to_dict(orient='records')

    return rows, skipped_rows, warnings, period_months


def parse_location_excel(file_storage):
    """Đọc file Excel "Vị trí hàng hóa" do user (store hoặc admin) tải lên.
    Định dạng đơn giản, KHÔNG cần đúng tên cột: cột đầu tiên là "Mã hàng",
    tối đa 3 cột tiếp theo là "Vị trí 1/2/3" (thừa cột thì bỏ qua, thiếu cột
    thì để trống). Dòng nào không có mã hàng sẽ bị bỏ qua.

    Trả về (rows, skipped_rows) với rows là danh sách dict:
    {part_code, location_1, location_2, location_3}."""
    file_storage.seek(0)
    try:
        raw = pd.read_excel(file_storage, dtype=object, engine='openpyxl')
    except Exception:
        file_storage.seek(0)
        raw = pd.read_excel(file_storage, dtype=object)

    if raw is None or raw.shape[1] < 1:
        raise ValueError('File không đúng định dạng. Cột đầu tiên phải là "Mã hàng", tối đa 3 cột tiếp theo là vị trí.')

    cols = list(raw.columns)
    part_col = cols[0]
    location_cols = cols[1:4]  # chỉ lấy tối đa 3 cột vị trí đầu tiên sau mã hàng

    def _clean(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ''
        s = str(v).strip()
        return '' if s.lower() == 'nan' else s

    rows = []
    skipped_rows = 0
    for _, r in raw.iterrows():
        part_code = _clean(r[part_col])
        if not part_code:
            skipped_rows += 1
            continue
        locs = [_clean(r[c]) for c in location_cols]
        while len(locs) < 3:
            locs.append('')
        rows.append({
            'part_code': part_code,
            'location_1': locs[0] or None,
            'location_2': locs[1] or None,
            'location_3': locs[2] or None,
        })

    return rows, skipped_rows


def parse_damaged_excel(file_storage):
    """Đọc file Excel "Hàng hư hỏng" do store tải lên. Định dạng đơn giản,
    KHÔNG cần đúng tên cột: cột đầu tiên là "Mã hàng", cột thứ 2 là "Số
    lượng hư hỏng", cột thứ 3 (nếu có) là "Tình trạng" (ghi chú tự do, vd
    "vỡ", "mốc", "hết hạn"...). Dòng nào thiếu mã hàng hoặc số lượng không
    hợp lệ (<=0, không phải số) sẽ bị bỏ qua.

    Trả về (rows, skipped_rows) với rows là danh sách dict:
    {part_code, quantity, note}."""
    file_storage.seek(0)
    try:
        raw = pd.read_excel(file_storage, dtype=object, engine='openpyxl')
    except Exception:
        file_storage.seek(0)
        raw = pd.read_excel(file_storage, dtype=object)

    if raw is None or raw.shape[1] < 2:
        raise ValueError('File không đúng định dạng. Cột đầu tiên phải là "Mã hàng", cột thứ 2 là "Số lượng hư hỏng", cột thứ 3 (không bắt buộc) là "Tình trạng".')

    cols = list(raw.columns)
    part_col = cols[0]
    qty_col = cols[1]
    note_col = cols[2] if len(cols) > 2 else None

    def _clean(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ''
        s = str(v).strip()
        return '' if s.lower() == 'nan' else s

    rows = []
    skipped_rows = 0
    for _, r in raw.iterrows():
        part_code = _clean(r[part_col])
        qty_raw = parse_qty(r[qty_col]) if qty_col in r else None
        if not part_code or qty_raw is None or qty_raw <= 0:
            skipped_rows += 1
            continue
        note = _clean(r[note_col]) if note_col is not None else ''
        rows.append({
            'part_code': part_code,
            'quantity': qty_raw,
            'note': note or None,
        })

    return rows, skipped_rows


# ---- Ảnh cho "Hàng Cần Xử Lý" (damaged_items) --------------------------
# Giới hạn số ảnh lưu cho MỖI lần báo hư hỏng, để tránh 1 dòng dữ liệu kéo
# theo hàng chục ảnh làm phình CSDL vô tội vạ.
MAX_DAMAGED_IMAGES_PER_ITEM = 4
# Giới hạn dung lượng ảnh GỐC nhận từ client (trước khi nén) - chặn sớm
# trước khi Pillow phải giải mã ảnh quá khổ, tốn RAM/CPU vô ích.
MAX_DAMAGED_IMAGE_SOURCE_BYTES = 15 * 1024 * 1024  # 15MB/ảnh gốc
# Cạnh dài nhất sau khi resize - đủ nét để xem tình trạng hư hỏng trên
# điện thoại/màn hình, không cần giữ độ phân giải gốc của ảnh chụp.
DAMAGED_IMAGE_MAX_EDGE = 1280


def resize_damaged_image(file_storage):
    """Đọc 1 ảnh do người dùng tải lên, tự sửa xoay theo EXIF (ảnh chụp từ
    điện thoại), resize về tối đa DAMAGED_IMAGE_MAX_EDGE ở cạnh dài nhất rồi
    nén sang WebP (nhẹ hơn JPEG/PNG cùng chất lượng) - MỤC ĐÍCH: giảm tối
    đa dung lượng lưu trong CSDL (cột BYTEA) và băng thông khi tải ảnh về
    sau này, vì ảnh gốc chụp điện thoại có thể vài MB trong khi bản đã nén
    thường chỉ còn vài chục-vài trăm KB mà vẫn đủ xem rõ.

    Trả về (image_bytes, content_type). Ném ValueError nếu file không phải
    ảnh hợp lệ hoặc vượt quá MAX_DAMAGED_IMAGE_SOURCE_BYTES."""
    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size <= 0:
        raise ValueError('File ảnh trống.')
    if size > MAX_DAMAGED_IMAGE_SOURCE_BYTES:
        raise ValueError(f'Ảnh quá lớn (tối đa {MAX_DAMAGED_IMAGE_SOURCE_BYTES // (1024*1024)}MB/ảnh).')

    try:
        img = Image.open(file_storage)
        img.load()
    except Exception:
        raise ValueError('File không phải là ảnh hợp lệ.')

    # Sửa xoay theo thẻ EXIF Orientation rồi bỏ luôn EXIF (không cần lưu
    # metadata máy ảnh/GPS... trong CSDL, vừa nhẹ vừa tránh lộ vị trí chụp).
    img = ImageOps.exif_transpose(img)
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')

    w, h = img.size
    longest = max(w, h)
    if longest > DAMAGED_IMAGE_MAX_EDGE:
        ratio = DAMAGED_IMAGE_MAX_EDGE / float(longest)
        img = img.resize((max(1, round(w * ratio)), max(1, round(h * ratio))), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format='WEBP', quality=72, method=4)
    return buf.getvalue(), 'image/webp'


def get_summary_from_data(combined_data):
    total = len(combined_data)
    debt = sum(1 for x in combined_data if x['status'] == 'Nợ')
    shipping = sum(1 for x in combined_data if x['status'] == 'Đang vận chuyển')
    received = sum(1 for x in combined_data if x['status'] == 'Đã nhận hàng')

    valid_dates = []
    for x in combined_data:
        d_str = x.get('order_date')
        if d_str and d_str != 'Chưa có dữ liệu':
            try:
                valid_dates.append(datetime.strptime(d_str, '%d/%m/%Y'))
            except Exception:
                pass

    min_date = min(valid_dates).strftime('%d/%m/%Y') if valid_dates else 'Chưa có dữ liệu'
    max_date = max(valid_dates).strftime('%d/%m/%Y') if valid_dates else 'Chưa có dữ liệu'

    return {
        'total': total,
        'debt': debt,
        'shipping': shipping,
        'received': received,
        'min_date': min_date,
        'max_date': max_date
    }


def parse_qty(val):
    """Chuyển giá trị số lượng về số (float). Trả về 0 nếu không đọc được
    (ô trống, chữ, NaN...)."""
    try:
        qty = pd.to_numeric(val)
        if pd.isna(qty):
            return 0
        return qty
    except Exception:
        return 0


# Ánh xạ mã "Phân loại đơn hàng" (cột trong file Danh sách PO) sang tên
# hiển thị tiếng Việt. Nhận diện theo mã số đứng đầu (10/22/26...) để không
# phụ thuộc chính xác vào phần chữ tiếng Anh phía sau.
_ORDER_TYPE_MAP = [
    ('10', 'Đơn khẩn'),
    ('22', 'Đơn định kỳ'),
    ('26', 'Đơn Bình, điện, lốp, Dầu nhớt'),
]


def map_order_type(raw_val):
    if raw_val is None or pd.isna(raw_val):
        return 'Chưa có dữ liệu'
    val = str(raw_val).strip()
    if not val or val.lower() == 'nan':
        return 'Chưa có dữ liệu'

    val_lower = val.lower()
    for code, label in _ORDER_TYPE_MAP:
        if val.startswith(code + '-') or val.startswith(code + ' ') or val == code:
            return label
    if 'urgent' in val_lower:
        return 'Đơn khẩn'
    if 'stock order' in val_lower:
        return 'Đơn định kỳ'
    if 'drop shipment' in val_lower:
        return 'Đơn Bình, điện, lốp, Dầu nhớt'

    # Không nhận diện được mã -> hiển thị nguyên giá trị gốc để không mất dữ liệu
    return val


def process_data(ds_po_df, po_detail_df, receipt_df):
    """
    Tính bảng đối soát. ĐÃ VECTOR HÓA bằng pandas (groupby/merge/map) thay
    vì lặp qua từng dòng bằng .iterrows() như bản cũ - .iterrows() tạo 1
    Series mới cho mỗi dòng nên rất chậm (chậm hơn hàng chục-hàng trăm lần
    so với thao tác vector hóa) khi số dòng lên tới hàng chục nghìn, và là
    nguyên nhân chính gây chậm/timeout khi dữ liệu lớn.

    po_detail_df LUÔN được load_store_dataframes() chuẩn bị sẵn với đúng 3
    cột đã làm sạch: 'po_code', 'part_code', 'quantity' (lấy trực tiếp từ
    CSDL, không phải file Excel gốc) nên không cần dò tên cột / clean_str /
    parse_qty lại cho DataFrame này như 2 DataFrame còn lại.
    """
    ds_po_df = ds_po_df.copy()
    receipt_df = receipt_df.copy()
    ds_po_df.columns = [str(c).strip() for c in ds_po_df.columns]
    receipt_df.columns = [str(c).strip() for c in receipt_df.columns]

    ds_po_col = find_col(ds_po_df.columns, ['mã đơn hàng mua', 'mã đơn hàng', 'mã po', 'order number', 'po'])
    ds_date_col = find_col(ds_po_df.columns, ['ngày tạo đơn hàng mua', 'ngày tạo', 'ngày đặt', 'ngày gửi đơn đặt hàng', 'ngày'])
    ds_status_col = find_col(ds_po_df.columns, ['trạng thái đơn hàng mua', 'trạng thái đơn hàng', 'trạng thái po'])
    ds_order_type_col = find_col(ds_po_df.columns, ['phân loại đơn hàng phụ tùng', 'phân loại đơn hàng', 'loại đơn hàng', 'order type'])

    rec_po_col = find_col(receipt_df.columns, ['siebel po number', 'po number', 'mã đơn hàng mua', 'mã đơn hàng', 'po'])
    rec_part_col = find_col(receipt_df.columns, ['part#', 'part #', 'part number', 'mã phụ tùng', 'part'])
    rec_status_col = find_col(receipt_df.columns, ['mrn status', 'trạng thái', 'status'])

    if not all([ds_po_col, ds_date_col]):
        raise ValueError("File Danh sách PO thiếu cột 'Mã PO' hoặc 'Ngày đặt'.")
    if not all([rec_po_col, rec_part_col, rec_status_col]):
        raise ValueError("File Chi tiết nhận hàng thiếu cột 'Mã PO', 'Mã phụ tùng' hoặc 'Trạng thái'.")
    for required_col in ('po_code', 'part_code', 'quantity'):
        if required_col not in po_detail_df.columns:
            raise ValueError("Dữ liệu Chi tiết PO thiếu cột bắt buộc để tính toán.")

    if po_detail_df.empty:
        return pd.DataFrame()

    # ---------- 1) Danh sách PO: ngày đặt sớm nhất / PO bị huỷ / loại đơn hàng ----------
    ds = pd.DataFrame({'po_val': ds_po_df[ds_po_col].map(clean_str)})
    ds = ds[ds['po_val'] != '']
    ds['order_date'] = parse_mixed_vn_datetime(ds_po_df.loc[ds.index, ds_date_col])

    # Ngày đặt sớm nhất cho mỗi Mã PO (thay cho "if po_val not in date_lookup or order_date < date_lookup[po_val]")
    date_lookup = ds.dropna(subset=['order_date']).groupby('po_val')['order_date'].min()

    cancelled_pos = set()
    if ds_status_col:
        status_vals = ds_po_df.loc[ds.index, ds_status_col].map(clean_str)
        cancelled_pos = set(ds.loc[status_vals == 'CANCELLED', 'po_val'])

    order_type_lookup = {}
    if ds_order_type_col:
        raw_types = ds_po_df.loc[ds.index, ds_order_type_col]
        valid_type = raw_types.notna() & (raw_types.astype(str).str.strip() != '')
        if valid_type.any():
            # Giữ lần xuất hiện ĐẦU TIÊN của mỗi Mã PO (giống "if po_val not in order_type_lookup" cũ)
            order_type_lookup = (
                pd.DataFrame({'po_val': ds['po_val'][valid_type], 'type': raw_types[valid_type]})
                .drop_duplicates('po_val', keep='first')
                .set_index('po_val')['type']
                .to_dict()
            )

    # ---------- 2) Chi tiết nhận hàng: trạng thái mỗi cặp (Mã PO, Mã phụ tùng) ----------
    rec = pd.DataFrame({
        'po_val': receipt_df[rec_po_col].map(clean_str),
        'part_val': receipt_df[rec_part_col].map(clean_str),
        'status_val': receipt_df[rec_status_col].map(clean_str),
    })
    rec = rec[(rec['po_val'] != '') & (rec['part_val'] != '')]

    if rec.empty:
        receipt_lookup = {}
    else:
        # OPEN luôn thắng nếu có ít nhất 1 dòng OPEN; nếu không thì lấy
        # trạng thái của lần xuất hiện đầu tiên - đúng bằng logic gốc
        # "if key not in receipt_lookup or status_val == 'OPEN': receipt_lookup[key] = status_val"
        has_open = rec.assign(is_open=rec['status_val'] == 'OPEN').groupby(['po_val', 'part_val'])['is_open'].any()
        first_status = rec.drop_duplicates(['po_val', 'part_val'], keep='first').set_index(['po_val', 'part_val'])['status_val']
        receipt_lookup = first_status.where(~has_open, 'OPEN').to_dict()

    # ---------- 3) Chi tiết PO: ghép với 2 bảng trên ----------
    detail = po_detail_df[['po_code', 'part_code', 'quantity']].copy()
    detail = detail[(detail['po_code'] != '') & (detail['part_code'] != '')]
    detail = detail[~detail['po_code'].isin(cancelled_pos)]
    # Chỉ giữ lần xuất hiện ĐẦU TIÊN của mỗi cặp (Mã PO, Mã phụ tùng) - giống "seen_pairs" cũ
    detail = detail.drop_duplicates(subset=['po_code', 'part_code'], keep='first')

    if detail.empty:
        return pd.DataFrame()

    today = datetime.now()
    order_date = pd.to_datetime(detail['po_code'].map(date_lookup))
    has_date = order_date.notna()
    days_diff = (today - order_date).dt.days
    detail['order_date'] = order_date.dt.strftime('%d/%m/%Y').where(has_date, 'Chưa có dữ liệu')

    rec_keys = pd.Series(list(zip(detail['po_code'], detail['part_code'])), index=detail.index)
    rec_status = rec_keys.map(receipt_lookup)

    detail['status'] = np.select(
        [rec_status == 'OPEN', rec_status == 'CLOSED'],
        ['Đang vận chuyển', 'Đã nhận hàng'],
        default='Nợ',
    )

    # show_days = trạng thái đang "Nợ" hoặc "Đang vận chuyển" (phụ tùng chưa nhận đủ hàng)
    show_days = detail['status'].isin(['Nợ', 'Đang vận chuyển'])
    detail['days_debt'] = np.where(show_days & has_date, days_diff.fillna(0).astype(int), 0)
    qty_numeric = pd.to_numeric(detail['quantity'], errors='coerce').fillna(0)
    detail['qty_debt'] = np.where(show_days, qty_numeric, 0)
    detail['order_type'] = detail['po_code'].map(order_type_lookup).map(map_order_type)

    df = detail[['po_code', 'part_code', 'order_date', 'order_type', 'status', 'days_debt', 'qty_debt']].reset_index(drop=True)

    # Cột "cộng dồn": tổng số lượng nợ (trạng thái Nợ + Đang vận chuyển)
    # của TẤT CẢ các PO có cùng mã phụ tùng.
    df['qty_debt_total'] = df.groupby('part_code')['qty_debt'].transform('sum')

    sort_key = np.where(df['status'].isin(['Nợ', 'Đang vận chuyển']), -df['days_debt'], 0)
    df = df.assign(_sort=sort_key).sort_values(by=['_sort', 'po_code']).drop(columns=['_sort']).reset_index(drop=True)

    return df


# ----------------------------------------------------------------------------
# Data storage helpers (thay thế / cộng dồn / dọn dẹp)
# ----------------------------------------------------------------------------

# Các cửa hàng dùng CHUNG 1 "không gian xem hàng nợ" (bảng đối soát PO): dữ
# liệu Danh sách PO / Chi tiết PO / Chi tiết nhận hàng của các cửa hàng
# trong cùng 1 nhóm sẽ được GỘP LẠI khi tính bảng đối soát (cửa hàng nào
# trong nhóm tải file lên thì cả nhóm đều thấy chung 1 bảng đối soát). Việc
# đăng nhập, lịch sử tải lên (upload_log), tồn kho và luân chuyển nội bộ vẫn
# tách riêng theo ĐÚNG store_code thật như cũ - CHỈ bảng đối soát "hàng nợ"
# là được gộp. Cửa hàng không có mặt trong dict này vẫn đứng riêng 1 mình,
# không đổi gì so với trước.
_DEBT_VIEW_GROUPS = {
    'NS2': ('NS2', 'NSM1'),
    'NSM1': ('NS2', 'NSM1'),
}


def get_debt_view_group(store_code):
    """Trả về tuple các store_code cùng chia sẻ không gian xem hàng nợ với
    store_code truyền vào (mặc định chỉ gồm chính nó nếu không thuộc nhóm nào
    trong _DEBT_VIEW_GROUPS)."""
    return _DEBT_VIEW_GROUPS.get(store_code, (store_code,))


def cleanup_old_po_detail(cursor):
    """Xoá các dòng 'Chi tiết PO' đã được nhập quá PO_DETAIL_RETENTION_DAYS
    ngày. Các dữ liệu khác (Danh sách PO, Chi tiết nhận hàng, users...) không
    bị ảnh hưởng."""
    cursor.execute(
        "DELETE FROM po_detail_items WHERE upload_time < NOW() - (%s || ' days')::interval",
        (PO_DETAIL_RETENTION_DAYS,)
    )


def cleanup_old_upload_log(cursor):
    """Xoá các dòng 'Lịch Sử Tải Lên' (upload_log) đã quá
    UPLOAD_LOG_RETENTION_DAYS (7) ngày kể từ lúc tải lên. Chỉ xoá LOG hiển
    thị lịch sử - KHÔNG đụng tới dữ liệu thật của Danh sách PO/Chi tiết
    PO/Chi tiết nhận hàng (nằm ở latest_uploads/po_detail_items, có
    retention/ghi đè riêng)."""
    cursor.execute(
        "DELETE FROM upload_log WHERE upload_time < NOW() - (%s || ' days')::interval",
        (UPLOAD_LOG_RETENTION_DAYS,)
    )


def cleanup_old_login_log(cursor):
    """Xoá các dòng 'Lịch Sử Đăng Nhập' (login_log) đã quá
    LOGIN_LOG_RETENTION_DAYS (90) ngày. Chỉ ảnh hưởng lịch sử hiển thị cho
    admin xem lại - không liên quan gì tới việc đăng nhập/tài khoản hiện tại."""
    cursor.execute(
        "DELETE FROM login_log WHERE login_time < NOW() - (%s || ' days')::interval",
        (LOGIN_LOG_RETENTION_DAYS,)
    )


def save_ds_po(cursor, store_code, ds_po_file, ds_po_df, upload_time):
    """Danh sách PO: XOÁ SẠCH dữ liệu cũ của cửa hàng này và THAY THẾ hoàn
    toàn bằng dữ liệu mới. Không đụng tới dữ liệu Chi tiết nhận hàng đang có,
    để có thể tải riêng lẻ từng loại file mà không làm mất dữ liệu loại kia."""
    ds_po_json = dumps_json(ds_po_df.to_dict(orient='records'))

    cursor.execute('''
        INSERT INTO latest_uploads (store_code, ds_po_filename, ds_po_json, ds_po_upload_time)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (store_code) DO UPDATE SET
            ds_po_filename = EXCLUDED.ds_po_filename,
            ds_po_json = EXCLUDED.ds_po_json,
            ds_po_upload_time = EXCLUDED.ds_po_upload_time
    ''', (store_code, ds_po_file.filename, ds_po_json, upload_time))


def save_receipt(cursor, store_code, receipt_file, receipt_df, upload_time):
    """Chi tiết nhận hàng: XOÁ SẠCH dữ liệu cũ của cửa hàng này và THAY THẾ
    hoàn toàn bằng dữ liệu mới. Không đụng tới dữ liệu Danh sách PO đang có,
    để có thể tải riêng lẻ từng loại file mà không làm mất dữ liệu loại kia."""
    receipt_json = dumps_json(receipt_df.to_dict(orient='records'))

    cursor.execute('''
        INSERT INTO latest_uploads (store_code, receipt_filename, receipt_json, receipt_upload_time)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (store_code) DO UPDATE SET
            receipt_filename = EXCLUDED.receipt_filename,
            receipt_json = EXCLUDED.receipt_json,
            receipt_upload_time = EXCLUDED.receipt_upload_time
    ''', (store_code, receipt_file.filename, receipt_json, upload_time))


def append_po_detail(cursor, store_code, po_detail_file, po_detail_df, upload_time):
    """Chi tiết PO: GHI THÊM vào dữ liệu cũ (không xoá gì cả), NHƯNG sẽ tự
    động BỎ QUA (không ghi vào CSDL) các dòng đã tồn tại sẵn - xác định
    trùng lặp dựa trên bộ 3 giá trị (Mã PO, Mã phụ tùng, Số lượng) của cùng
    1 cửa hàng. Việc kiểm tra này áp dụng cho cả:
      - Dữ liệu đã có sẵn trong CSDL từ các lần tải trước.
      - Các dòng trùng lặp ngay trong chính file đang tải lên lần này.

    Việc chống trùng với dữ liệu ĐÃ CÓ trong CSDL được giao hẳn cho Postgres
    xử lý qua UNIQUE INDEX (store_code, po_code, part_code, quantity) +
    "ON CONFLICT DO NOTHING", thay vì SELECT toàn bộ khoá cũ của cửa hàng
    vào RAM Python rồi so sánh từng dòng như trước - cách cũ càng chậm và
    tốn RAM hơn khi po_detail_items tích luỹ càng nhiều theo thời gian (bảng
    này KHÔNG bị xoá sạch mỗi lần tải như 2 loại file kia).

    Việc dọn dẹp dữ liệu quá hạn 120 ngày được xử lý riêng ở
    cleanup_old_po_detail().

    Trả về (số dòng đã ghi thêm, số dòng bị bỏ qua vì trùng lặp)."""
    detail_po_col = find_col(po_detail_df.columns, ['order number', 'mã đơn hàng mua', 'mã đơn hàng', 'mã po', 'po'])
    detail_part_col = find_col(po_detail_df.columns, ['part#', 'part #', 'part number', 'mã phụ tùng', 'part'])
    detail_qty_col = find_col(po_detail_df.columns, ['quantity requested', 'số lượng yêu cầu', 'số lượng', 'quantity'])

    if not detail_po_col or not detail_part_col:
        raise ValueError("File Chi tiết PO thiếu cột 'Mã PO' hoặc 'Mã phụ tùng'.")
    if not detail_qty_col:
        raise ValueError("File Chi tiết PO thiếu cột 'Số lượng' (Quantity Requested).")

    detail = po_detail_df.copy()
    detail['_po_code'] = detail[detail_po_col].map(clean_str)
    detail['_part_code'] = detail[detail_part_col].map(clean_str)
    detail['_qty'] = pd.to_numeric(detail[detail_qty_col], errors='coerce').fillna(0.0).astype(float)

    detail = detail[(detail['_po_code'] != '') & (detail['_part_code'] != '')]

    # Bỏ các dòng trùng lặp NGAY TRONG chính file đang tải lên (giữ dòng
    # xuất hiện đầu tiên) - vector hóa bằng drop_duplicates() thay cho vòng
    # lặp Python + set thủ công như trước.
    detail = detail.drop_duplicates(subset=['_po_code', '_part_code', '_qty'], keep='first')

    total_candidates = len(detail)
    if total_candidates == 0:
        return 0, 0

    # KHÔNG còn lưu row_json (nguyên văn mọi cột gốc của file Excel) - cột
    # này đã ngừng được đọc lại ở bất kỳ đâu trong code từ khi
    # load_store_dataframes() chuyển sang chỉ đọc 3 cột po_code/part_code/
    # quantity (xem comment ở đó). Việc dừng ghi cột này giúp giảm đáng kể
    # dung lượng CSDL cho mỗi lần import, vì đây từng là phần nặng nhất của
    # mỗi dòng trong bảng po_detail_items (chứa toàn bộ cột gốc file Excel,
    # dù chỉ 3 cột po_code/part_code/quantity thực sự được dùng).
    rows_to_insert = [
        (store_code, po, part, qty, po_detail_file.filename, upload_time)
        for po, part, qty in zip(detail['_po_code'], detail['_part_code'], detail['_qty'])
    ]

    inserted_rows = execute_values(
        cursor,
        '''INSERT INTO po_detail_items (store_code, po_code, part_code, quantity, filename, upload_time)
           VALUES %s
           ON CONFLICT (store_code, po_code, part_code, quantity) DO NOTHING
           RETURNING id''',
        rows_to_insert,
        fetch=True,
    )
    inserted_count = len(inserted_rows)
    skipped_count = total_candidates - inserted_count

    return inserted_count, skipped_count


def load_store_dataframes(cursor, store_code):
    """Tải dữ liệu hiện có để tính toán bảng đối soát cho "không gian xem
    hàng nợ" của store_code (xem get_debt_view_group() - với đa số cửa
    hàng, không gian này chỉ gồm đúng 1 mình nó; riêng NS2/NSM1 dùng chung
    1 không gian nên dữ liệu của cả 2 sẽ được gộp lại ở đây):
    - ds_po_df / receipt_df: gộp bản MỚI NHẤT (đã bị thay thế mỗi lần upload)
      của TỪNG cửa hàng trong nhóm.
    - po_detail_df: gộp TOÀN BỘ dữ liệu còn hiệu lực (đã cộng dồn, chưa quá
      120 ngày) của TỪNG cửa hàng trong nhóm.
    Trả về (ds_po_df, po_detail_df, receipt_df) hoặc None nếu chưa đủ dữ liệu.
    process_data() vốn đã drop_duplicates theo (Mã PO, Mã phụ tùng) / Mã PO,
    nên nếu 2 cửa hàng trong nhóm cùng có dữ liệu cho cùng 1 Mã PO thì không
    bị tính trùng.
    """
    group = get_debt_view_group(store_code)

    cursor.execute(
        "SELECT ds_po_json, receipt_json FROM latest_uploads WHERE store_code = ANY(%s)",
        (list(group),)
    )
    latest_rows = cursor.fetchall()

    ds_po_records = []
    receipt_records = []
    for row in latest_rows:
        if row['ds_po_json']:
            ds_po_records.extend(loads_json(row['ds_po_json']))
        if row['receipt_json']:
            receipt_records.extend(loads_json(row['receipt_json']))

    if not ds_po_records or not receipt_records:
        return None

    # Lấy TRỰC TIẾP 3 cột đã CHUẨN HOÁ (po_code/part_code/quantity - vốn đã
    # được clean_str()/pd.to_numeric() một lần khi ghi ở append_po_detail())
    # thay vì đọc cột row_json (chứa nguyên văn mọi cột gốc của file Excel,
    # nặng hơn nhiều) rồi json.loads() cho TỪNG dòng bằng Python. Với bảng
    # càng lớn (cộng dồn theo thời gian), bỏ được bước này giúp giảm cả
    # dung lượng truyền từ Postgres về lẫn thời gian parse JSON trong app.
    # Thứ tự upload_time giảm dần chỉ còn ý nghĩa lịch sử, không ảnh hưởng
    # kết quả vì process_data() dùng drop_duplicates(keep='first') để giữ
    # đúng 1 dòng cho mỗi cặp (Mã PO, Mã phụ tùng) như hành vi seen_pairs cũ.
    cursor.execute(
        "SELECT po_code, part_code, quantity FROM po_detail_items WHERE store_code = ANY(%s) ORDER BY upload_time DESC",
        (list(group),)
    )
    detail_rows = cursor.fetchall()
    if not detail_rows:
        return None

    ds_po_df = pd.DataFrame(ds_po_records)
    receipt_df = pd.DataFrame(receipt_records)
    po_detail_df = pd.DataFrame(detail_rows, columns=['po_code', 'part_code', 'quantity'])

    return ds_po_df, po_detail_df, receipt_df


def compute_result_for_store(cursor, store_code):
    """Tính bảng đối soát hiện tại cho 1 cửa hàng, dựa trên dữ liệu mới nhất
    của Danh sách PO / Chi tiết nhận hàng và toàn bộ dữ liệu Chi tiết PO còn
    hiệu lực (trong 120 ngày)."""
    dfs = load_store_dataframes(cursor, store_code)
    if dfs is None:
        return []

    ds_po_df, po_detail_df, receipt_df = dfs
    try:
        result_df = process_data(ds_po_df, po_detail_df, receipt_df)
    except Exception:
        return []

    return result_df.to_dict(orient='records')


# Cache đơn giản trong bộ nhớ (RAM) của tiến trình app: /api/data trước đây
# tính lại toàn bộ bảng đối soát (đọc JSON lớn + merge bằng pandas) MỖI LẦN
# được gọi, kể cả khi dữ liệu không hề thay đổi giữa 2 lần gọi liên tiếp
# (ví dụ do polling hoặc bấm refresh nhiều lần).
#
# Mỗi cửa hàng giờ có version RIÊNG (xem get_all_store_data_versions), nên
# cache lưu dạng {store_code: (version, data)} - so sánh version của ĐÚNG
# cửa hàng đó, thay vì trước đây dùng 1 version toàn cục khiến cache của
# TẤT CẢ cửa hàng bị xoá sạch mỗi khi bất kỳ cửa hàng nào có upload mới.
_result_cache = {}


def get_global_data_version(cursor):
    """Trả về mốc thời gian mới nhất trong 3 nguồn dữ liệu ảnh hưởng tới
    bảng đối soát (Danh sách PO / Chi tiết nhận hàng / Chi tiết PO) ở TẤT CẢ
    cửa hàng. CHỈ dùng cho /api/version (frontend poll giá trị này để biết
    "có gì đó vừa đổi ở đâu đó" - không cần chi tiết đổi ở cửa hàng nào)."""
    cursor.execute('''
        SELECT GREATEST(
            COALESCE((SELECT MAX(ds_po_upload_time) FROM latest_uploads), 'epoch'::timestamp),
            COALESCE((SELECT MAX(receipt_upload_time) FROM latest_uploads), 'epoch'::timestamp),
            COALESCE((SELECT MAX(upload_time) FROM po_detail_items), 'epoch'::timestamp)
        ) AS v
    ''')
    return cursor.fetchone()['v']


def get_store_data_version(cursor, store_code):
    """Giống get_global_data_version nhưng chỉ tính cho 1 "không gian xem
    hàng nợ" (xem get_debt_view_group() - với đa số cửa hàng chỉ gồm đúng
    1 mình nó; riêng NS2/NSM1 dùng chung nên version sẽ đổi nếu BẤT KỲ cửa
    hàng nào trong nhóm vừa upload). Dùng khi /api/data chỉ cần bảng đối
    soát của 1 cửa hàng cụ thể (không phải 'ALL'), để tránh việc 1 cửa hàng
    khác (ngoài nhóm) vừa upload làm cache của cửa hàng này bị coi là cũ một
    cách oan uổng."""
    group = list(get_debt_view_group(store_code))
    cursor.execute('''
        SELECT GREATEST(
            COALESCE((SELECT MAX(ds_po_upload_time) FROM latest_uploads WHERE store_code = ANY(%s)), 'epoch'::timestamp),
            COALESCE((SELECT MAX(receipt_upload_time) FROM latest_uploads WHERE store_code = ANY(%s)), 'epoch'::timestamp),
            COALESCE((SELECT MAX(upload_time) FROM po_detail_items WHERE store_code = ANY(%s)), 'epoch'::timestamp)
        ) AS v
    ''', (group, group, group))
    return cursor.fetchone()['v']


def get_all_store_data_versions(cursor):
    """Tính version RIÊNG cho từng cửa hàng trong 1 lần truy vấn (thay vì
    gọi get_store_data_version() lặp lại cho mỗi cửa hàng - tránh N round-trip
    tới DB khi admin xem 'ALL'). Trả về dict {store_code: version}.

    Nhờ có version riêng theo từng cửa hàng, khi 1 cửa hàng upload dữ liệu
    mới, CHỈ cache của đúng cửa hàng đó bị vô hiệu - cache của các cửa hàng
    khác (không hề đổi gì) vẫn dùng lại được, thay vì trước đây dùng chung 1
    version toàn cục khiến TOÀN BỘ cửa hàng đều bị tính lại mỗi khi có bất kỳ
    upload nào xảy ra ở bất kỳ đâu."""
    cursor.execute('''
        SELECT store_code, MAX(ts) AS v FROM (
            SELECT store_code, ds_po_upload_time AS ts FROM latest_uploads
            UNION ALL
            SELECT store_code, receipt_upload_time AS ts FROM latest_uploads
            UNION ALL
            SELECT store_code, upload_time AS ts FROM po_detail_items
        ) combined
        WHERE ts IS NOT NULL
        GROUP BY store_code
    ''')
    return {r['store_code']: r['v'] for r in cursor.fetchall()}


def compute_result_for_store_cached(cursor, store_code, version):
    """Giống compute_result_for_store nhưng có cache 2 TẦNG:
    1) RAM của tiến trình hiện tại (_result_cache) - nhanh nhất, không tốn
       round-trip tới DB, nhưng CHỈ dùng được trong đúng worker đã tính ra nó.
    2) Bảng computed_cache trong Postgres - CHIA SẺ được giữa NHIỀU worker
       (nếu Render chạy nhiều tiến trình Flask cùng lúc): worker nào tính
       trước sẽ ghi kết quả vào đây, các worker khác đọc lại thay vì phải
       tính lại từ đầu (đọc JSON từ DB vẫn rẻ hơn nhiều so với việc load lại
       toàn bộ Danh sách PO/Chi tiết PO/Chi tiết nhận hàng rồi merge bằng
       pandas).
    Cả 2 tầng đều tự động vô hiệu khi "version" của ĐÚNG cửa hàng đó đổi -
    không ảnh hưởng tới cache của các cửa hàng khác.
    """
    global _result_cache
    version_str = str(version)

    cached_entry = _result_cache.get(store_code)
    if cached_entry is not None and cached_entry[0] == version_str:
        return cached_entry[1]

    cursor.execute(
        'SELECT data_json FROM computed_cache WHERE store_code = %s AND version = %s',
        (store_code, version_str)
    )
    cached_row = cursor.fetchone()
    if cached_row:
        data = loads_json(cached_row['data_json'])
        _result_cache[store_code] = (version_str, data)
        return data

    data = compute_result_for_store(cursor, store_code)
    _result_cache[store_code] = (version_str, data)

    # Ghi lại vào DB cho các worker khác dùng chung. Dùng try/except riêng vì
    # đây chỉ là tối ưu hiệu năng - nếu ghi cache lỗi (ví dụ race condition
    # hiếm gặp) thì vẫn trả kết quả đã tính đúng cho request hiện tại, không
    # để lỗi cache làm hỏng cả API.
    try:
        cursor.execute('''
            INSERT INTO computed_cache (store_code, version, data_json, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (store_code) DO UPDATE SET
                version = EXCLUDED.version,
                data_json = EXCLUDED.data_json,
                updated_at = EXCLUDED.updated_at
        ''', (store_code, version_str, dumps_json(data)))
        cursor.connection.commit()
    except Exception:
        try:
            cursor.connection.rollback()
        except Exception:
            pass

    return data


# ----------------------------------------------------------------------------
# Routes & Endpoints
# ----------------------------------------------------------------------------

@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))

    # Đang "mượn quyền" xem giao diện 1 cửa hàng (xem impersonate_store bên
    # dưới) hay không - dùng để hiển thị banner "Quay Lại Quyền Admin" và
    # tên admin thật đang đứng sau, KHÔNG lưu lộ ra store_code/role thật
    # trong session (session['role']/['store_code'] đã đổi hẳn sang của cửa
    # hàng đang xem, đảm bảo MỌI route/quyền truy cập trong toàn hệ thống tự
    # động áp dụng đúng như 1 tài khoản cửa hàng thật, không cần sửa lại
    # từng route kiểm tra quyền rải rác khắp nơi).
    impersonating = bool(session.get('_impersonate_from'))
    real_admin_user = session['_impersonate_from']['user'] if impersonating else None

    # Lấy sẵn banner "ngày xe đi" đang hiệu lực để hiện NGAY khi vừa tải
    # trang (không phải đợi tới lượt poll /api/version đầu tiên sau 20s).
    db = get_db()
    cursor = db.cursor()
    truck_announcements = get_active_truck_announcements(cursor)
    cursor.close()

    return render_template(
        'index.html',
        user=session['user'],
        full_name=session.get('full_name') or session['user'],
        branch=session.get('branch') or '',
        role=session['role'],
        store_code=session['store_code'],
        store_employees=STORE_EMPLOYEES,
        admin_employees=ADMIN_EMPLOYEES,   #thêm dòng này
        impersonating=impersonating,
        real_admin_user=real_admin_user,
        truck_announcements=truck_announcements,
    )


def _is_hashed_password(stored_value):
    """Nhận diện mật khẩu đã được hash bằng werkzeug (các thuật toán werkzeug
    hỗ trợ đều lưu dưới dạng 'method:params$salt$hash', ví dụ
    'pbkdf2:sha256:...' hoặc 'scrypt:...'). Mật khẩu cũ (từ trước khi áp
    dụng hash) là chuỗi thường, không có dạng này."""
    return isinstance(stored_value, str) and stored_value.startswith(('pbkdf2:', 'scrypt:', 'argon2:'))


def verify_and_upgrade_password(cursor, db, user, password):
    """Kiểm tra mật khẩu, hỗ trợ cả 2 trường hợp:
    - Mật khẩu đã hash (trường hợp bình thường) -> so sánh bằng check_password_hash.
    - Mật khẩu cũ còn plain text (tài khoản tạo/đổi từ trước khi nâng cấp
      bảo mật) -> so sánh trực tiếp, và nếu đúng thì TỰ ĐỘNG cập nhật lại
      thành dạng hash ngay trong DB, để những lần đăng nhập sau không còn
      là plain text nữa. Không cần chạy migration riêng, không mất tài
      khoản nào - mỗi user chỉ cần đăng nhập lại 1 lần là tự nâng cấp."""
    stored = user['password']

    if _is_hashed_password(stored):
        return check_password_hash(stored, password)

    # Mật khẩu cũ dạng plain text
    if stored == password:
        new_hash = generate_password_hash(password)
        cursor.execute("UPDATE users SET password = %s WHERE username = %s", (new_hash, user['username']))
        db.commit()
        return True

    return False
@app.route('/health')
def health():
    return 'OK', 200

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()

        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()

        # So sánh mật khẩu (tự hỗ trợ nâng cấp tài khoản cũ còn plain text
        # lên hash ngay khi đăng nhập thành công - xem verify_and_upgrade_password).
        if user and verify_and_upgrade_password(cursor, db, user, password):
            # Ghi lại IP thật (đã qua ProxyFix) + trình duyệt/thiết bị của lượt
            # đăng nhập này, để admin xem lại được "tài khoản này đăng nhập từ
            # đâu, lúc nào" trong /api/admin/login-log.
            #
            # CỐ TÌNH bọc riêng trong try/except: đây chỉ là tính năng phụ để
            # xem lịch sử, KHÔNG được phép làm hỏng việc đăng nhập của user
            # nếu vì lý do gì đó (VD: DB tạm thời chậm/lỗi) mà ghi log thất
            # bại - user vẫn phải đăng nhập được bình thường.
            try:
                cursor.execute(
                    "INSERT INTO login_log (username, ip_address, user_agent, login_time) VALUES (%s, %s, %s, %s) RETURNING id",
                    (user['username'], request.remote_addr, request.headers.get('User-Agent', ''), vn_now())
                )
                new_login_log_id = cursor.fetchone()['id']
                # Tự dọn log cũ ngay trong lượt đăng nhập này (giống cách
                # cleanup_old_upload_log được gọi khi có upload) - không cần
                # thêm cron/job riêng, và DELETE theo index thời gian rất nhẹ
                # ngay cả khi không có dòng nào quá hạn để xoá.
                cleanup_old_login_log(cursor)
                db.commit()
                # Tra vị trí địa lý (thành phố/tỉnh) TRONG THREAD NỀN - gọi
                # API bên ngoài có thể mất 1-2s, không được để user phải chờ
                # thêm chừng đó khi đăng nhập.
                resolve_login_location_async(new_login_log_id, request.remote_addr)
            except Exception:
                db.rollback()

            cursor.close()
            session.clear()
            session['user'] = user['username']
            session['role'] = user['role']
            session['store_code'] = user['store_code']
            # Họ và tên để hiển thị lời chào (VD "Xin chào Nguyễn Văn A") -
            # nếu tài khoản chưa được admin điền Họ và tên thì tạm dùng luôn
            # tên đăng nhập, để chỗ chào hỏi không bao giờ bị trống.
            session['full_name'] = user.get('full_name') or user['username']
            session['branch'] = user.get('branch') or ''
            session.permanent = True
            return redirect(url_for('index'))
        else:
            cursor.close()
            return render_template('login.html', error="Sai tên đăng nhập hoặc mật khẩu!")
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/change-password', methods=['POST'])
@limiter.limit("10 per minute")
def change_own_password():
    """Cho phép CHÍNH user đang đăng nhập tự đổi mật khẩu của mình (khác
    với /api/admin/users vốn chỉ admin mới gọi được để reset mật khẩu cho
    người khác). Bắt buộc phải nhập đúng mật khẩu hiện tại trước khi cho
    đổi, để tránh trường hợp máy đang đăng nhập sẵn bị người khác lợi dụng
    đổi mật khẩu chiếm tài khoản.

    Lưu ý: nếu đang trong chế độ admin "mượn quyền" xem 1 cửa hàng
    (impersonate), session['user'] VẪN LÀ tên đăng nhập THẬT của admin (xem
    admin_impersonate_store ở trên), nên hàm này luôn đổi đúng mật khẩu của
    tài khoản đang thực sự đăng nhập, không bị nhầm sang tài khoản cửa hàng
    đang được xem."""
    if 'user' not in session:
        return jsonify({'error': 'Vui lòng đăng nhập lại.'}), 401

    data = request.json or {}
    current_password = (data.get('current_password') or '').strip()
    new_password = (data.get('new_password') or '').strip()

    if not current_password or not new_password:
        return jsonify({'error': 'Vui lòng nhập đầy đủ mật khẩu hiện tại và mật khẩu mới.'}), 400
    if len(new_password) < 4:
        return jsonify({'error': 'Mật khẩu mới phải có ít nhất 4 ký tự.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE username = %s", (session['user'],))
    user = cursor.fetchone()

    if not user or not verify_and_upgrade_password(cursor, db, user, current_password):
        cursor.close()
        return jsonify({'error': 'Mật khẩu hiện tại không đúng.'}), 400

    cursor.execute(
        "UPDATE users SET password = %s WHERE username = %s",
        (generate_password_hash(new_password), user['username'])
    )
    db.commit()
    cursor.close()
    return jsonify({'success': True})


# ----------------------------------------------------------------------------
# ADMIN "MƯỢN QUYỀN" XEM GIAO DIỆN 1 CỬA HÀNG BẤT KỲ (impersonate), RỒI QUAY
# LẠI QUYỀN ADMIN. Cách làm: tạm cất (user, role, store_code) THẬT của admin
# vào session['_impersonate_from'], rồi ghi ĐÈ session['role']/['store_code']
# thành của cửa hàng được chọn - nhờ vậy MỌI route kiểm tra quyền
# (session['role'] != 'admin' / session['store_code']) trong toàn hệ thống
# tự động coi đây là 1 phiên đăng nhập cửa hàng thật, không cần sửa lại từng
# nơi. session['user'] CỐ TÌNH giữ nguyên tên admin thật (không đổi thành
# tên cửa hàng) để mọi hành động thực hiện trong lúc mượn quyền (tạo phiếu,
# tải file...) vẫn ghi nhận đúng người thực hiện là admin, không bị nhầm là
# chính cửa hàng đó tự thao tác.
# ----------------------------------------------------------------------------
@app.route('/api/admin/impersonate-store', methods=['POST'])
def admin_impersonate_store():
    # Cho phép gọi cả khi ĐANG mượn quyền 1 cửa hàng khác (session['role']
    # lúc này đã là 'store') để admin chuyển thẳng sang xem cửa hàng khác mà
    # không cần quay lại Admin rồi bấm lại - miễn là session còn giữ
    # '_impersonate_from' (gốc admin thật) hoặc bản thân đang là admin thật.
    is_real_admin = session.get('role') == 'admin' and 'user' in session
    is_already_impersonating = bool(session.get('_impersonate_from'))
    if 'user' not in session or not (is_real_admin or is_already_impersonating):
        return jsonify({'error': 'Chỉ tài khoản admin mới dùng được chức năng này.'}), 403

    data = request.json or {}
    store_code = (data.get('store_code') or '').strip()

    db = get_db()
    cursor = db.cursor()
    valid_stores = _valid_store_codes(cursor)
    cursor.close()

    if not store_code or store_code not in valid_stores:
        return jsonify({'error': 'Cửa hàng không hợp lệ.'}), 400

    # Chỉ lưu lại gốc admin thật ở LẦN ĐẦU mượn quyền - nếu đã đang mượn
    # quyền rồi mà chuyển tiếp sang cửa hàng khác, KHÔNG được ghi đè
    # '_impersonate_from' bằng dữ liệu cửa hàng hiện tại (sẽ mất dấu vết
    # admin thật, "Quay Lại Quyền Admin" sẽ bị sai).
    if not is_already_impersonating:
        session['_impersonate_from'] = {
            'user': session['user'],
            'role': session['role'],
            'store_code': session['store_code'],
        }

    session['role'] = 'store'
    session['store_code'] = store_code
    session.modified = True

    return jsonify({'success': True, 'store_code': store_code})


@app.route('/api/admin/return-to-admin', methods=['POST'])
def admin_return_to_admin():
    origin = session.get('_impersonate_from')
    if not origin:
        return jsonify({'error': 'Không ở trong chế độ mượn quyền cửa hàng.'}), 400

    session['user'] = origin['user']
    session['role'] = origin['role']
    session['store_code'] = origin['store_code']
    session.pop('_impersonate_from', None)
    session.modified = True

    return jsonify({'success': True})


@app.route('/api/upload', methods=['POST'])
def upload_files():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    store_code = session['store_code']
    if store_code == 'ALL':
        return jsonify({'error': 'Admin không trực tiếp tải file cửa hàng.'}), 400

    ds_po_file = request.files.get('ds_po_file')
    po_detail_file = request.files.get('po_detail_file')
    receipt_file = request.files.get('receipt_file')

    # Cho phép tải lên MỘT hoặc MỘT VÀI file trong số 3 file, không bắt buộc
    # phải đủ cả 3 mỗi lần. Ví dụ: "Chi tiết PO" là dữ liệu cộng dồn nên có
    # thể tải riêng lẻ nhiều lần mà không cần đi kèm 2 file kia; 2 file
    # "Danh sách PO" / "Chi tiết nhận hàng" (nếu có) vẫn sẽ thay thế dữ liệu
    # cũ của đúng loại đó, các loại không được gửi lên sẽ giữ nguyên.
    if not ds_po_file and not po_detail_file and not receipt_file:
        return jsonify({'error': 'Vui lòng chọn ít nhất một file để tải lên.'}), 400

    try:
        upload_time = vn_now()
        upload_time_str = upload_time.strftime('%Y-%m-%d %H:%M:%S')

        db = get_db()
        cursor = db.cursor()

        # 1) Danh sách PO: nếu có file mới thì xoá sạch & thay thế
        if ds_po_file:
            ds_po_df = read_any(ds_po_file)
            save_ds_po(cursor, store_code, ds_po_file, ds_po_df, upload_time)

        # 2) Chi tiết nhận hàng: nếu có file mới thì xoá sạch & thay thế
        if receipt_file:
            receipt_df = read_any(receipt_file)
            save_receipt(cursor, store_code, receipt_file, receipt_df, upload_time)

        # 3) Chi tiết PO: nếu có file mới thì ghi thêm vào dữ liệu cũ (cộng
        #    dồn), tự động bỏ qua các dòng đã trùng (Mã PO + Mã phụ tùng +
        #    Số lượng) để tránh ghi lặp vào CSDL.
        po_detail_inserted = None
        po_detail_skipped = None
        if po_detail_file:
            po_detail_df = read_any(po_detail_file)
            po_detail_inserted, po_detail_skipped = append_po_detail(cursor, store_code, po_detail_file, po_detail_df, upload_time)

        # 4) Dọn dẹp dữ liệu Chi tiết PO đã quá 120 ngày (các dữ liệu khác giữ nguyên)
        cleanup_old_po_detail(cursor)

        # 4b) Dọn dẹp Lịch Sử Tải Lên đã quá 7 ngày (chỉ xoá log hiển thị,
        #     không ảnh hưởng dữ liệu thật - xem cleanup_old_upload_log()).
        cleanup_old_upload_log(cursor)

        # 5) Ghi log lượt tải (chỉ phục vụ hiển thị lịch sử) - file nào không
        #    được gửi lên lần này sẽ ghi log là NULL.
        cursor.execute('''
            INSERT INTO upload_log (store_code, upload_time, ds_po_filename, po_detail_filename, receipt_filename)
            VALUES (%s, %s, %s, %s, %s)
        ''', (
            store_code, upload_time_str,
            ds_po_file.filename if ds_po_file else None,
            po_detail_file.filename if po_detail_file else None,
            receipt_file.filename if receipt_file else None,
        ))

        db.commit()

        # 6) Tính lại bảng đối soát mới nhất cho cửa hàng này
        data_dicts = compute_result_for_store(cursor, store_code)
        summary = get_summary_from_data(data_dicts)
        cursor.close()

        response = {'success': True, 'data': data_dicts, 'summary': summary, 'upload_time': upload_time_str}

        # Thông báo cho cửa hàng biết số dòng "Chi tiết PO" đã được ghi mới
        # và số dòng bị bỏ qua vì đã tồn tại (trùng Mã PO + Mã phụ tùng + Số lượng).
        if po_detail_file is not None:
            response['po_detail_inserted'] = po_detail_inserted
            response['po_detail_skipped'] = po_detail_skipped

        # Nếu cửa hàng chưa từng có đủ "Danh sách PO" + "Chi tiết nhận hàng"
        # (ví dụ đây là lần tải đầu tiên và chỉ chọn mỗi file Chi tiết PO),
        # bảng đối soát sẽ trống. Vẫn báo tải file thành công nhưng kèm cảnh
        # báo để cửa hàng biết cần bổ sung đủ 3 loại file ít nhất 1 lần.
        if not data_dicts:
            response['warning'] = ('Đã lưu file thành công, nhưng chưa đủ dữ liệu để đối soát. '
                                    'Vui lòng đảm bảo cửa hàng đã tải đủ "Danh sách PO" và '
                                    '"Chi tiết nhận hàng" ít nhất 1 lần.')

        return jsonify(response)
    except Exception as e:
        # In đầy đủ traceback ra Render Logs để chẩn đoán mà không cần mò
        # DevTools của trình duyệt mỗi lần có lỗi.
        app.logger.error("Lỗi /api/upload (store=%s): %s\n%s", store_code, e, traceback.format_exc())
        try:
            get_db().rollback()
        except Exception:
            pass
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/upload-inventory', methods=['POST'])
def upload_inventory():
    """Admin tải file "Tổng hợp tồn kho" lên. Dữ liệu sẽ GHI ĐÈ TOÀN BỘ
    (không cộng dồn) - vì đây là số liệu tồn cuối kỳ tại 1 thời điểm."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    inventory_file = request.files.get('inventory_file')
    if not inventory_file:
        return jsonify({'error': 'Vui lòng chọn file tồn kho để tải lên.'}), 400

    try:
        rows, skipped_rows, warnings = parse_inventory_excel(inventory_file)
        if not rows:
            return jsonify({'error': 'Không đọc được mã hàng nào thuộc các mã kho quy định trong file này.'}), 400

        upload_time = vn_now()
        db = get_db()
        cursor = db.cursor()

        # Ghi đè toàn bộ: xoá sạch dữ liệu tồn kho cũ rồi nạp lại từ đầu.
        cursor.execute('TRUNCATE TABLE inventory_items')
        execute_values(
            cursor,
            '''INSERT INTO inventory_items (part_code, part_name, unit, store_code, quantity, is_pi2)
               VALUES %s''',
            [(r['part_code'], r['part_name'], r['unit'], r['store_code'], r['quantity'], bool(r.get('is_pi2', False))) for r in rows]
        )

        distinct_parts = len({r['part_code'] for r in rows})

        cursor.execute('''
            INSERT INTO inventory_meta (id, filename, uploaded_by, upload_time, total_parts, skipped_rows)
            VALUES (1, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                filename = EXCLUDED.filename,
                uploaded_by = EXCLUDED.uploaded_by,
                upload_time = EXCLUDED.upload_time,
                total_parts = EXCLUDED.total_parts,
                skipped_rows = EXCLUDED.skipped_rows
        ''', (inventory_file.filename, _current_actor_name(), upload_time, distinct_parts, skipped_rows))

        db.commit()

        # Tồn kho vừa đổi -> rà soát lại ngay các mã TX sắp hết hàng thay vì
        # đợi lượt kiểm tra nền hằng ngày (xem dashboard.py). Import trong
        # hàm (không phải đầu file) vì dashboard.py import ngược lại app.py -
        # gọi lúc này (sau khi app.py đã load xong) là an toàn. Lỗi ở bước
        # này (nếu có) không được làm hỏng kết quả upload tồn kho đã thành
        # công - chỉ log lại để xem sau.
        try:
            from dashboard import check_and_notify_low_stock
            check_and_notify_low_stock(cursor)
            db.commit()
        except Exception:
            db.rollback()
            traceback.print_exc()

        cursor.close()
        invalidate_inventory_cache()

        return jsonify({
            'success': True,
            'total_parts': distinct_parts,
            'skipped_rows': skipped_rows,
            'warnings': warnings,
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.error("Lỗi /api/admin/upload-inventory: %s\n%s", e, traceback.format_exc())
        try:
            get_db().rollback()
        except Exception:
            pass
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/import-sales-export', methods=['POST'])
def import_sales_export():
    """Admin import file "Tổng hợp tồn kho" (dùng cột Xuất kho) để cập nhật
    số liệu bán ra phục vụ thống kê tần suất bán TX/TB/CB. Import RIÊNG,
    KHI CẦN - không tự động, không theo tuần. Ghi đè toàn bộ mỗi lần import,
    giống hệt cơ chế của upload_inventory()."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    sales_file = request.files.get('sales_file')
    if not sales_file:
        return jsonify({'error': 'Vui lòng chọn file để tải lên.'}), 400

    try:
        rows, skipped_rows, warnings, period_months = parse_sales_export_excel(sales_file)
        if not rows:
            return jsonify({'error': 'Không đọc được mã hàng nào thuộc các mã kho quy định trong file này.'}), 400

        upload_time = vn_now()
        db = get_db()
        cursor = db.cursor()

        cursor.execute('TRUNCATE TABLE sales_export_items')
        execute_values(
            cursor,
            '''INSERT INTO sales_export_items (part_code, part_name, unit, store_code, qty_sold)
               VALUES %s''',
            [(r['part_code'], r['part_name'], r['unit'], r['store_code'], r['qty_sold']) for r in rows]
        )

        distinct_parts = len({r['part_code'] for r in rows})

        cursor.execute('''
            INSERT INTO sales_export_meta (id, filename, uploaded_by, upload_time, period_months, total_parts, skipped_rows)
            VALUES (1, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                filename = EXCLUDED.filename,
                uploaded_by = EXCLUDED.uploaded_by,
                upload_time = EXCLUDED.upload_time,
                period_months = EXCLUDED.period_months,
                total_parts = EXCLUDED.total_parts,
                skipped_rows = EXCLUDED.skipped_rows
        ''', (sales_file.filename, _current_actor_name(), upload_time, period_months, distinct_parts, skipped_rows))

        db.commit()

        # Số liệu bán ra vừa đổi -> rà soát lại ngay các mã TX sắp hết hàng,
        # cùng lý do như trong upload_inventory() ở trên.
        try:
            from dashboard import check_and_notify_low_stock
            check_and_notify_low_stock(cursor)
            db.commit()
        except Exception:
            db.rollback()
            traceback.print_exc()

        cursor.close()
        invalidate_inventory_cache()

        return jsonify({
            'success': True,
            'total_parts': distinct_parts,
            'skipped_rows': skipped_rows,
            'period_months': period_months,
            'warnings': warnings,
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.error("Lỗi /api/admin/import-sales-export: %s\n%s", e, traceback.format_exc())
        try:
            get_db().rollback()
        except Exception:
            pass
        return jsonify({'error': str(e)}), 500


def _compute_sales_frequency_rows(cursor, store_code=None):
    """Hàm dùng chung cho endpoint thống kê (xem trên web) và xuất Excel:
    gộp tồn kho + xuất bán theo mã hàng (toàn hệ thống, hoặc lọc theo 1
    store_code cụ thể nếu truyền vào), rồi phân loại TX/TB/CB.
    Trả về (rows, period_months)."""
    cursor.execute('SELECT period_months FROM sales_export_meta WHERE id = 1')
    meta_row = cursor.fetchone()
    period_months = (meta_row['period_months'] if meta_row and meta_row.get('period_months') else 3) or 3

    if store_code:
        cursor.execute('''
            SELECT part_code, part_name, unit, SUM(quantity) AS quantity
            FROM inventory_items WHERE store_code = %s
            GROUP BY part_code, part_name, unit
        ''', (store_code,))
    else:
        cursor.execute('''
            SELECT part_code, MAX(part_name) AS part_name, MAX(unit) AS unit, SUM(quantity) AS quantity
            FROM inventory_items GROUP BY part_code
        ''')
    inventory_by_part = {r['part_code']: r for r in cursor.fetchall()}

    if store_code:
        cursor.execute('SELECT part_code, SUM(qty_sold) AS qty_sold FROM sales_export_items WHERE store_code = %s GROUP BY part_code', (store_code,))
    else:
        cursor.execute('SELECT part_code, SUM(qty_sold) AS qty_sold FROM sales_export_items GROUP BY part_code')
    sold_by_part = {r['part_code']: float(r['qty_sold'] or 0) for r in cursor.fetchall()}

    rows = []
    for part_code, inv in inventory_by_part.items():
        if _is_excluded_from_reorder(part_code):
            continue  # Nhóm mã không được phép đặt (vd khung xe 50100...) -
                      # loại khỏi thống kê/gợi ý/cảnh báo ngay từ nguồn.
        qty_on_hand = float(inv['quantity'] or 0)
        qty_sold = sold_by_part.get(part_code, 0.0)
        classification = classify_sales_frequency(qty_on_hand, qty_sold, period_months)
        if classification is None:
            continue  # Tồn <= 0 -> không thuộc thống kê tần suất bán
        rows.append({
            'part_code': part_code,
            'part_name': inv['part_name'],
            'unit': inv['unit'],
            'qty_on_hand': qty_on_hand,
            'qty_sold_period': qty_sold,
            'avg_month': classification['avg_month'],
            'months_of_stock': classification['months_of_stock'],
            'group': classification['code'],
            'group_label': classification['label'],
        })

    return rows, period_months


# Cache kết quả _compute_sales_frequency_rows theo epoch tồn kho/xuất bán. Mỗi
# lần gọi gốc phải đọc + gộp (GROUP BY) toàn bộ tồn kho và xuất bán (hàng chục
# nghìn dòng), mà trang thống kê/gợi ý nhập hàng gọi lại mỗi lần đổi bộ lọc.
# CHỈ dùng cho các route CHỈ ĐỌC - KHÔNG dùng trong check_and_notify_low_stock
# (chạy giữa transaction import, có thể thấy dữ liệu chưa commit). Giữ tối đa 3
# kết quả gần nhất (mỗi kết quả ~ vài chục MB RAM nếu để nhiều store cùng lúc).
_sfr_cache = {}
_sfr_cache_lock = threading.Lock()
_SFR_CACHE_MAX = 3


def _compute_sales_frequency_rows_cached(cursor, store_code=None):
    epoch = _inventory_epoch  # đọc TRƯỚC khi tính - nếu import xen giữa thì cache này tự lệch epoch
    with _sfr_cache_lock:
        entry = _sfr_cache.get(store_code)
    if entry is not None and entry[0] == epoch:
        return list(entry[1]), entry[2]  # bản sao nông của list (route có thể .sort() tại chỗ)
    rows, period_months = _compute_sales_frequency_rows(cursor, store_code)
    with _sfr_cache_lock:
        if store_code not in _sfr_cache and len(_sfr_cache) >= _SFR_CACHE_MAX:
            _sfr_cache.pop(next(iter(_sfr_cache)))
        _sfr_cache[store_code] = (epoch, rows, period_months)
    return list(rows), period_months


@app.route('/api/admin/sales-frequency-stats', methods=['GET'])
def sales_frequency_stats():
    """Thống kê các mã hàng theo tần suất bán TX/TB/CB, cho trang admin
    riêng. Hỗ trợ lọc theo kho/cửa hàng (?store=NS1) và theo nhóm
    (?group=CB), search theo mã/tên hàng (?q=...)."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    store_code = (request.args.get('store') or '').strip().upper() or None
    group_filter = (request.args.get('group') or '').strip().upper() or None
    q = (request.args.get('q') or '').strip().lower()

    db = get_db()
    cursor = db.cursor()
    rows, period_months = _compute_sales_frequency_rows_cached(cursor, store_code)
    cursor.close()

    summary = {'TX': 0, 'TB': 0, 'CB': 0}
    for r in rows:
        summary[r['group']] = summary.get(r['group'], 0) + 1

    if group_filter in ('TX', 'TB', 'CB'):
        rows = [r for r in rows if r['group'] == group_filter]
    if q:
        rows = [r for r in rows if q in r['part_code'].lower() or q in (r['part_name'] or '').lower()]

    rows.sort(key=lambda r: (r['months_of_stock'] is None, -(r['months_of_stock'] or 0)))

    return jsonify({
        'success': True,
        'data': rows,
        'summary': summary,
        'total': len(rows),
        'period_months': period_months,
        'store': store_code,
    })


@app.route('/api/admin/sales-frequency-stats/export', methods=['GET'])
def sales_frequency_stats_export():
    """Xuất Excel danh sách thống kê tần suất bán, áp dụng đúng bộ lọc
    hiện tại trên màn hình (store/group/q), giống logic sales_frequency_stats()."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    store_code = (request.args.get('store') or '').strip().upper() or None
    group_filter = (request.args.get('group') or '').strip().upper() or None
    q = (request.args.get('q') or '').strip().lower()

    db = get_db()
    cursor = db.cursor()
    rows, period_months = _compute_sales_frequency_rows_cached(cursor, store_code)
    cursor.close()

    if group_filter in ('TX', 'TB', 'CB'):
        rows = [r for r in rows if r['group'] == group_filter]
    if q:
        rows = [r for r in rows if q in r['part_code'].lower() or q in (r['part_name'] or '').lower()]
    rows.sort(key=lambda r: (r['months_of_stock'] is None, -(r['months_of_stock'] or 0)))

    df = pd.DataFrame([{
        'Mã hàng': r['part_code'],
        'Tên hàng': r['part_name'],
        'ĐVT': r['unit'],
        'Tồn hiện tại': r['qty_on_hand'],
        f'Xuất kho ({period_months} tháng)': r['qty_sold_period'],
        'TB bán/tháng': r['avg_month'],
        'Số tháng tồn': r['months_of_stock'],
        'Phân loại': f"{r['group']} - {r['group_label']}",
    } for r in rows])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Tần suất bán')
    output.seek(0)

    filename_suffix = store_code or 'tat-ca'
    return send_file(
        output,
        as_attachment=True,
        download_name=f'thong-ke-tan-suat-ban-{filename_suffix}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@app.route('/api/admin/import-prices', methods=['POST'])
def import_prices():
    """Admin import GIÁ BÁN của các mã hàng từ 1 file Excel/CSV đơn giản
    (chỉ cần 2 cột "Mã hàng" và "Giá bán"). Dữ liệu được GHI ĐÈ TOÀN BỘ:
    xoá sạch (TRUNCATE) bảng part_prices rồi nạp lại từ đầu theo đúng file
    vừa tải lên - giống hệt cách "Cập Nhật Tồn Kho" (upload_inventory) đang
    làm. Mã hàng nào có giá cũ nhưng KHÔNG có mặt trong file lần này sẽ mất
    giá (hiển thị trống) cho tới khi được cập nhật lại."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    price_file = request.files.get('price_file')
    if not price_file:
        return jsonify({'error': 'Vui lòng chọn file giá bán để tải lên.'}), 400

    try:
        df = read_any(price_file)
    except Exception as e:
        return jsonify({'error': f'Không đọc được file: {e}'}), 400

    part_col = find_col(df.columns, ['mã phụ tùng', 'mã hàng', 'part code', 'part #', 'part number', 'part#', 'part'])
    price_col = find_col(df.columns, ['giá bán', 'đơn giá bán', 'đơn giá', 'giá', 'sale price', 'unit price', 'price'])
    if not part_col or not price_col:
        return jsonify({'error': 'Không tìm thấy cột "Mã hàng" và "Giá bán" trong file. Vui lòng kiểm tra lại file Excel.'}), 400

    now = vn_now()
    rows = []
    skipped_rows = 0
    for _, r in df.iterrows():
        part_code = str(r.get(part_col, '') or '').strip()
        if not part_code or part_code.lower() == 'nan':
            continue
        try:
            price = float(r.get(price_col))
            if math.isnan(price) or price < 0:
                raise ValueError()
        except (TypeError, ValueError):
            skipped_rows += 1
            continue
        rows.append((part_code, price, now, _current_actor_name()))

    if not rows:
        return jsonify({'error': 'File không có dòng dữ liệu hợp lệ nào (mã hàng + giá bán >= 0).'}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        # Nếu file có nhiều dòng trùng mã hàng, chỉ giữ dòng CUỐI CÙNG (giống
        # cách Excel/pandas trả về theo thứ tự đọc file) để không insert
        # trùng part_code (cột part_code là UNIQUE/PRIMARY KEY).
        dedup = {r[0]: r for r in rows}
        rows = list(dedup.values())

        # Ghi đè toàn bộ: xoá sạch dữ liệu giá cũ rồi nạp lại từ đầu theo
        # đúng file vừa tải lên (giống upload_inventory()).
        cursor.execute('TRUNCATE TABLE part_prices')
        execute_values(
            cursor,
            '''INSERT INTO part_prices (part_code, sale_price, updated_at, updated_by)
               VALUES %s''',
            rows
        )

        cursor.execute('''
            INSERT INTO price_meta (id, filename, uploaded_by, upload_time, total_parts, skipped_rows)
            VALUES (1, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                filename = EXCLUDED.filename,
                uploaded_by = EXCLUDED.uploaded_by,
                upload_time = EXCLUDED.upload_time,
                total_parts = EXCLUDED.total_parts,
                skipped_rows = EXCLUDED.skipped_rows
        ''', (price_file.filename, _current_actor_name(), now, len(rows), skipped_rows))

        db.commit()
        invalidate_inventory_cache()
        return jsonify({'success': True, 'total_parts': len(rows), 'skipped_rows': skipped_rows})
    except Exception as e:
        db.rollback()
        app.logger.error("Lỗi /api/admin/import-prices: %s\n%s", e, traceback.format_exc())
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()


@app.route('/api/admin/import-order-lock', methods=['POST'])
def import_order_lock():
    """Admin import dữ liệu "Khoá đặt hàng" từ 1 file Excel/CSV RIÊNG (ngoài
    hệ thống): mỗi mã hàng có cột Khoá đặt hàng (có/không), Tồn kho Bắc, Tồn
    kho Nam, Mã thay thế (nếu mã hiện tại đã bị khoá). Ghi đè TOÀN BỘ mỗi
    lần import - giống hệt import_prices(). Nhận CẢ 2 kiểu file: file cũ
    (cột tiếng Việt, Tồn kho Bắc/Nam là SỐ LƯỢNG cụ thể) LẪN file kiểu mới
    dạng "Part #/Block for Order/Stock Available in South|North Warehouse/
    Superseeded Part" (cột tồn kho chỉ ghi Y/N - xem _to_stock_flag)."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    lock_file = request.files.get('lock_file')
    if not lock_file:
        return jsonify({'error': 'Vui lòng chọn file khoá đặt hàng để tải lên.'}), 400

    try:
        df = read_any(lock_file)
    except Exception as e:
        return jsonify({'error': f'Không đọc được file: {e}'}), 400

    # part_col/lock_col/replace_col: nhận cả tên cột tiếng Việt (file kiểu cũ)
    # part_col: LƯU Ý thứ tự pattern - 'part #' (có dấu cách) phải đứng
    # TRƯỚC 'part#' (liền, không cách): file kiểu mới ("output11") có CẢ 2
    # cột "Part #" (mã ĐÚNG định dạng hệ thống đang dùng, không gạch ngang,
    # vd "34908GA7701") LẪN "Edit Part#" (mã hiển thị CÓ gạch ngang, vd
    # "34908-GA7-701" - chỉ để người đọc dễ nhìn, KHÔNG khớp với part_code
    # trong inventory_items). find_col so khớp kiểu "chuỗi con" - nếu để
    # pattern 'part#' lên trước, nó khớp NHẦM vào "Edit Part#" (vì "edit
    # part#" cũng chứa "part#") TRƯỚC KHI kịp thử pattern 'part #' đúng,
    # khiến mã hàng bị lưu sai định dạng (có gạch ngang) và tra cứu không
    # bao giờ khớp được mã thật trong hệ thống (lỗi đã gặp thực tế với mã
    # 34908GA7701 - "khoá bán nhưng kết quả ra không khoá").
    # LẪN tên cột tiếng Anh "Part #"/"Block for Order"/"Superseeded Part"
    # (file kiểu mới, vd export "output11" - xem find_col: so khớp CHỨA
    # chuỗi, không phân biệt hoa/thường). bac_col/nam_col cũng nhận thêm
    # "Stock Available in North/South Warehouse" (file kiểu mới - cột này
    # chỉ ghi Y/N chứ không có số lượng, xem _to_stock_flag bên dưới).
    part_col = find_col(df.columns, ['mã phụ tùng', 'mã hàng', 'part code', 'part #', 'part number', 'part#', 'part'])
    lock_col = find_col(df.columns, ['khoá đặt hàng', 'khóa đặt hàng', 'khoá', 'khóa', 'block for order', 'lock'])
    bac_col = find_col(df.columns, ['tồn kho bắc', 'tồn bắc', 'tồn kho miền bắc', 'north warehouse', 'bắc'])
    nam_col = find_col(df.columns, ['tồn kho nam', 'tồn nam', 'tồn kho miền nam', 'south warehouse', 'nam'])
    replace_col = find_col(df.columns, ['mã thay thế', 'mã hàng thay thế', 'thay thế', 'superseeded part', 'superseded part', 'replacement'])

    if not part_col:
        return jsonify({'error': 'Không tìm thấy cột "Mã hàng" trong file. Vui lòng kiểm tra lại file Excel.'}), 400

    def _to_bool_locked(val):
        s = str(val or '').strip().lower()
        return s in ('1', 'true', 'x', 'có', 'co', 'khoá', 'khóa', 'yes', 'y', 'đã khoá', 'đã khóa')

    def _to_qty(val):
        try:
            q = float(val)
            if math.isnan(q):
                return None
            return q
        except (TypeError, ValueError):
            return None

    def _to_stock_flag(val):
        """File kiểu mới chỉ ghi Y/N (có/không tồn), không phải số lượng cụ
        thể - trả về True/False, hoặc None nếu ô trống/không đọc được."""
        s = str(val or '').strip().lower()
        if s in ('', 'nan', 'none'):
            return None
        if s in ('y', 'yes', 'có', 'co', '1', 'true'):
            return True
        if s in ('n', 'no', 'không', 'khong', '0', 'false'):
            return False
        return None

    # XỬ LÝ VECTOR HOÁ (thay cho iterrows()): với file lớn (vài chục nghìn
    # dòng trở lên), iterrows() duyệt từng dòng bằng Python thuần rất chậm
    # (có thể mất hàng chục giây). Dùng các phép toán theo CỘT của pandas
    # (.str, .apply theo cột, numpy) nhanh hơn rất nhiều vì chạy bằng code C
    # bên dưới thay vì lặp Python cho từng ô/từng dòng.
    now = vn_now()
    actor = _current_actor_name()

    # LƯU Ý: KHÔNG được chỉ dựa vào so sánh chuỗi ('nan') để phát hiện ô
    # trống - với pandas bản đang dùng, .astype(str) trên CẢ CỘT (vector
    # hoá) KHÔNG chuyển NaN thành chuỗi "nan" như nhiều người tưởng, nó GIỮ
    # NGUYÊN là số thực NaN. Nếu chỉ lọc bằng .str.lower() != 'nan', NaN sẽ
    # "lọt lưới" (so sánh chuỗi không bao giờ khớp NaN) và bị insert thẳng
    # vào DB dưới dạng số NaN - Postgres trả text đó ra là chữ "NaN" (viết
    # hoa), gây lỗi hiển thị/mã hàng rác. Phải gọi .notna() trên CỘT GỐC
    # (trước khi ép kiểu string) mới bắt đúng được NaN thật.
    part_codes_raw = df[part_col]
    part_codes = part_codes_raw.astype(str).str.strip()
    valid_mask = (
        part_codes_raw.notna()
        & (part_codes != '') & (part_codes.str.lower() != 'nan')
    )
    skipped_rows = int((~valid_mask).sum())

    work = df.loc[valid_mask].copy()
    part_codes = part_codes.loc[valid_mask]

    if lock_col:
        is_locked_s = work[lock_col].apply(_to_bool_locked)
    else:
        is_locked_s = pd.Series(False, index=work.index)

    raw_bac_s = work[bac_col] if bac_col else pd.Series(None, index=work.index)
    raw_nam_s = work[nam_col] if nam_col else pd.Series(None, index=work.index)
    qty_bac_s = raw_bac_s.apply(_to_qty)
    qty_nam_s = raw_nam_s.apply(_to_qty)
    has_stock_bac_s = pd.Series(
        [(_to_stock_flag(v) if q is None else None) for v, q in zip(raw_bac_s, qty_bac_s)],
        index=work.index)
    has_stock_nam_s = pd.Series(
        [(_to_stock_flag(v) if q is None else None) for v, q in zip(raw_nam_s, qty_nam_s)],
        index=work.index)

    if replace_col:
        # Cùng lỗi NaN như phần part_codes ở trên (xem giải thích phía trên) -
        # phải kiểm tra .isna() trên CỘT GỐC trước khi ép sang chuỗi, không
        # được chỉ dựa vào .isin(['', 'nan']) sau khi đã .astype(str), vì NaN
        # thật sẽ không khớp bất kỳ chuỗi nào trong danh sách đó và bị GIỮ
        # NGUYÊN thay vì bị đổi thành None. Đây chính là nguyên nhân khiến
        # các mã "Khoá đặt hàng" không có mã thay thế thật lại bị gán nhầm
        # replacement_code = NaN, rồi bị hiểu lầm là "có mã thay thế hợp lệ"
        # (chuỗi "NaN" không rỗng) ở _apply_order_lock_substitution() trong
        # dashboard.py, gộp lung tung nhiều mã gốc khác nhau vào 1 dòng ảo
        # "NaN" trong Gợi Ý Nhập Hàng.
        replace_raw = work[replace_col]
        replacement_s = replace_raw.astype(str).str.strip()
        invalid_replace = (
            replace_raw.isna()
            | replacement_s.str.lower().isin(['', 'nan', 'none', 'n/a'])
        )
        replacement_s = replacement_s.where(~invalid_replace, None)
    else:
        replacement_s = pd.Series(None, index=work.index)

    rows = list(zip(part_codes, is_locked_s, replacement_s, qty_bac_s, qty_nam_s,
                     has_stock_bac_s, has_stock_nam_s, [now] * len(work), [actor] * len(work)))

    if not rows:
        skipped_rows = len(df)
        return jsonify({'error': 'File không có dòng dữ liệu hợp lệ nào (thiếu mã hàng).'}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        dedup = {r[0]: r for r in rows}
        rows = list(dedup.values())

        cursor.execute('TRUNCATE TABLE order_lock_items')
        # page_size=1000 (mặc định của execute_values chỉ là 100): gộp nhiều
        # dòng hơn vào MỖI câu lệnh INSERT gửi đi, giảm số lượt round-trip
        # tới database - với ~79.000 dòng, mặc định cần ~790 lượt gọi DB,
        # tăng page_size giảm còn ~79 lượt, nhanh hơn đáng kể nhất là khi kết
        # nối tới DB ở xa (Neon/Supabase) có độ trễ mạng.
        execute_values(
            cursor,
            '''INSERT INTO order_lock_items
               (part_code, is_locked, replacement_code, qty_bac, qty_nam, has_stock_bac, has_stock_nam, updated_at, updated_by)
               VALUES %s''',
            rows,
            page_size=1000
        )

        cursor.execute('''
            INSERT INTO order_lock_meta (id, filename, uploaded_by, upload_time, total_parts, skipped_rows)
            VALUES (1, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                filename = EXCLUDED.filename,
                uploaded_by = EXCLUDED.uploaded_by,
                upload_time = EXCLUDED.upload_time,
                total_parts = EXCLUDED.total_parts,
                skipped_rows = EXCLUDED.skipped_rows
        ''', (lock_file.filename, _current_actor_name(), now, len(rows), skipped_rows))

        db.commit()
        return jsonify({'success': True, 'total_parts': len(rows), 'skipped_rows': skipped_rows})
    except Exception as e:
        db.rollback()
        app.logger.error("Lỗi /api/admin/import-order-lock: %s\n%s", e, traceback.format_exc())
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()


# Ngưỡng "số tháng tồn còn lại" để coi 1 mã hàng ở 1 kho khác là "dư, luân
# chuyển được" khi đề xuất luân chuyển thay vì đặt hàng mới trong Duyệt Đơn
# Hàng (kho đó phải bán chậm - TB/CB - VÀ còn đủ dùng q khá lâu sau khi trừ
# phần luân chuyển đi, không đề xuất rút cạn kho người ta).
ORDER_CHECK_TRANSFER_MIN_MONTHS_OF_STOCK = 4.0
_ORDER_CHECK_STORE_CODES = ('NS1', 'NS2', 'NS3', 'NS4', 'NS5', 'NSM1')


@app.route('/api/admin/order-check', methods=['POST'])
def order_check():
    """"Duyệt Đơn Hàng" (admin): nhận 1 danh sách (mã hàng, số lượng đặt) do
    admin dán vào, trả về cho MỖI mã hàng: tần suất bán, khoá đặt hàng (nếu
    có, kèm mã thay thế + tồn Bắc/Nam), tồn tại cửa hàng đang xét, tồn toàn
    hệ thống, đang nợ/đang vận chuyển hay không (từ bảng đối soát PO của
    đúng cửa hàng đó), và đề xuất luân chuyển từ cửa hàng khác đang dư/bán
    chậm thay vì đặt hàng mới."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    store_code = str(data.get('store_code', '') or '').strip()
    items = data.get('items') or []
    if store_code not in _ORDER_CHECK_STORE_CODES:
        return jsonify({'error': 'Vui lòng chọn đúng 1 cửa hàng để kiểm tra.'}), 400
    if not items:
        return jsonify({'error': 'Danh sách mã hàng trống.'}), 400

    # Chuẩn hoá + gộp số lượng nếu 1 mã hàng bị dán trùng nhiều dòng.
    qty_by_part = {}
    order_list = []
    for it in items:
        part_code = str(it.get('part_code', '') or '').strip()
        if not part_code:
            continue
        try:
            qty = float(it.get('qty', 0) or 0)
        except (TypeError, ValueError):
            qty = 0
        if part_code not in qty_by_part:
            order_list.append(part_code)
        qty_by_part[part_code] = qty_by_part.get(part_code, 0) + qty

    if not order_list:
        return jsonify({'error': 'Không đọc được mã hàng hợp lệ nào từ dữ liệu đã dán.'}), 400

    db = get_db()
    cursor = db.cursor()

    # ---- 1) Tồn kho hệ thống (pivot theo cửa hàng) cho đúng các mã hàng đã dán ----
    cursor.execute(
        'SELECT part_code, part_name, unit, store_code, quantity FROM inventory_items WHERE part_code = ANY(%s)',
        (order_list,)
    )
    inv_pivot = {}
    for it in cursor.fetchall():
        p = inv_pivot.setdefault(it['part_code'], {
            'part_name': it['part_name'], 'unit': it['unit'],
            'NS1': 0, 'NS2': 0, 'NS3': 0, 'NS4': 0, 'NS5': 0, 'NSM1': 0, 'CB': 0,
        })
        p[it['store_code']] = float(it['quantity']) if it['quantity'] is not None else 0

    # ---- 2) Tần suất bán (hệ thống + theo từng kho) - giống get_inventory() ----
    cursor.execute('SELECT COUNT(*) AS c FROM sales_export_items')
    has_sales_data = (cursor.fetchone() or {}).get('c', 0) > 0
    period_months = 3
    sold_by_part = {}
    sold_by_part_store = {}
    if has_sales_data:
        cursor.execute('SELECT period_months FROM sales_export_meta WHERE id = 1')
        meta_row = cursor.fetchone()
        period_months = (meta_row['period_months'] if meta_row and meta_row.get('period_months') else 3) or 3
        cursor.execute(
            'SELECT part_code, SUM(qty_sold) AS qty_sold FROM sales_export_items WHERE part_code = ANY(%s) GROUP BY part_code',
            (order_list,)
        )
        sold_by_part = {r['part_code']: float(r['qty_sold'] or 0) for r in cursor.fetchall()}
        cursor.execute(
            'SELECT part_code, store_code, SUM(qty_sold) AS qty_sold FROM sales_export_items WHERE part_code = ANY(%s) GROUP BY part_code, store_code',
            (order_list,)
        )
        for r in cursor.fetchall():
            sold_by_part_store.setdefault(r['part_code'], {})[r['store_code']] = float(r['qty_sold'] or 0)

    # ---- 3) Khoá đặt hàng ----
    cursor.execute(
        'SELECT part_code, is_locked, replacement_code, qty_bac, qty_nam, has_stock_bac, has_stock_nam '
        'FROM order_lock_items WHERE part_code = ANY(%s)',
        (order_list,)
    )
    lock_by_part = {r['part_code']: r for r in cursor.fetchall()}

    # ---- 3b) Mã cùng "họ" hậu tố chữ cái với mã đã dán - vd mã gốc
    # "40545001000", biến thể "40545001000SS": biến thể = mã gốc + hậu tố
    # CHỮ CÁI (1-3 ký tự) ngay sau phần số. NGƯỜI DÙNG CÓ THỂ DÁN VÀO CẢ 2
    # CHIỀU: hoặc dán mã gốc (cần gợi ý biến thể có hậu tố), hoặc dán chính
    # mã có hậu tố (cần gợi ý ngược lại về mã gốc/biến thể khác) - nên với
    # MỖI mã đã dán: trước tiên tách ra "mã gốc" của nó (bỏ hậu tố chữ cái
    # cuối nếu CHÍNH mã đó đã có hậu tố), rồi tìm TẤT CẢ mã (mã gốc trần +
    # mọi biến thể hậu tố khác) đang có trong CẢ inventory_items (để biết
    # tồn) LẪN order_lock_items (để biết có bị khoá không) cùng chung gốc
    # đó, dùng LIKE + lọc lại bằng regex Python để khớp CHÍNH XÁC "mã gốc +
    # 0-3 chữ cái" (không khớp nhầm 1 mã SỐ dài hơn khác, vì bắt buộc phần
    # đuôi phải rỗng hoặc là chữ cái).
    # Lưu ý: dùng escape đúng cú pháp SQL LIKE (chỉ cần escape \, %, _),
    # KHÔNG dùng re.escape (đó là escape cho regex, sai cú pháp với LIKE và
    # sẽ escape sai/dư ký tự với mã hàng có dấu gạch ngang "-" v.v.).
    def _sql_like_escape(s):
        return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

    _suffix_strip_re = re.compile(r'^(.*\d)([A-Za-z]{1,3})$')

    def _root_of(code):
        m = _suffix_strip_re.match(code)
        return m.group(1) if m else code

    root_by_pasted = {pc: _root_of(pc) for pc in order_list}
    all_roots = sorted(set(root_by_pasted.values()))

    root_prefix_patterns = [_sql_like_escape(r) + '%' for r in all_roots]
    cursor.execute(
        '''SELECT DISTINCT part_code FROM (
               SELECT part_code FROM inventory_items WHERE part_code LIKE ANY(%s)
               UNION
               SELECT part_code FROM order_lock_items WHERE part_code LIKE ANY(%s)
           ) t''',
        (root_prefix_patterns, root_prefix_patterns)
    )
    _variant_suffix_re = re.compile(r'^[A-Za-z]{0,3}$')  # rỗng = chính mã gốc, 1-3 chữ = biến thể
    codes_by_root = {}
    for row in cursor.fetchall():
        code = row['part_code']
        for root in all_roots:
            if code.startswith(root) and _variant_suffix_re.match(code[len(root):]):
                codes_by_root.setdefault(root, []).append(code)

    child_codes_by_parent = {}
    for pc in order_list:
        root = root_by_pasted[pc]
        child_codes_by_parent[pc] = sorted({c for c in codes_by_root.get(root, []) if c != pc})

    all_child_codes = sorted({c for lst in child_codes_by_parent.values() for c in lst})
    child_inv_by_code = {}
    child_lock_by_code = {}
    if all_child_codes:
        cursor.execute(
            'SELECT part_code, SUM(quantity) AS total_qty FROM inventory_items WHERE part_code = ANY(%s) GROUP BY part_code',
            (all_child_codes,)
        )
        child_inv_by_code = {r['part_code']: float(r['total_qty'] or 0) for r in cursor.fetchall()}
        cursor.execute(
            'SELECT part_code, is_locked, qty_bac, qty_nam, has_stock_bac, has_stock_nam '
            'FROM order_lock_items WHERE part_code = ANY(%s)',
            (all_child_codes,)
        )
        child_lock_by_code = {r['part_code']: r for r in cursor.fetchall()}

    # ---- 3b-2) Tồn kho + số bán theo TỪNG CỬA HÀNG của các mã con - để FE
    # hiển thị chi tiết (giống cột "Tồn Hệ Thống" của mã cha) thay vì chỉ 1
    # con số tổng, giúp admin biết mã con còn hàng ở cửa hàng nào và tần
    # suất bán ra sao trước khi quyết định dùng mã con thay cho mã cha.
    child_store_breakdown = {}
    child_sold_by_store = {}
    if all_child_codes:
        cursor.execute(
            'SELECT part_code, store_code, quantity FROM inventory_items WHERE part_code = ANY(%s)',
            (all_child_codes,)
        )
        for it in cursor.fetchall():
            sc = it['store_code']
            if sc not in _ORDER_CHECK_STORE_CODES:
                continue
            d = child_store_breakdown.setdefault(it['part_code'], {})
            d[sc] = float(it['quantity']) if it['quantity'] is not None else 0
        if has_sales_data:
            cursor.execute(
                'SELECT part_code, store_code, SUM(qty_sold) AS qty_sold FROM sales_export_items '
                'WHERE part_code = ANY(%s) GROUP BY part_code, store_code',
                (all_child_codes,)
            )
            for r in cursor.fetchall():
                child_sold_by_store.setdefault(r['part_code'], {})[r['store_code']] = float(r['qty_sold'] or 0)

    # ---- 3c) Ghi chú đã lưu trước đó cho các mã hàng đang kiểm tra ----
    cursor.execute('SELECT part_code, note FROM order_check_notes WHERE part_code = ANY(%s)', (order_list,))
    note_by_part = {r['part_code']: r['note'] for r in cursor.fetchall()}

    # ---- 4) Đang nợ / đang vận chuyển (bảng đối soát PO của đúng cửa hàng đang xét) ----
    version = get_store_data_version(cursor, store_code)
    debt_rows = compute_result_for_store_cached(cursor, store_code, version)
    debt_by_part = {}
    for r in debt_rows:
        pc = r.get('part_code')
        if pc not in order_list:
            continue
        d = debt_by_part.setdefault(pc, {'debt_qty': 0, 'shipping_qty': 0, 'po_codes': []})
        if r.get('status') == 'Nợ':
            d['debt_qty'] += float(r.get('qty_debt') or 0)
            d['po_codes'].append(r.get('po_code'))
        elif r.get('status') == 'Đang vận chuyển':
            d['shipping_qty'] += float(r.get('qty_debt') or 0)
            d['po_codes'].append(r.get('po_code'))

    cursor.close()

    # ---- 5) Ghép kết quả từng dòng + đề xuất luân chuyển ----
    result = []
    for part_code in order_list:
        qty_order = qty_by_part.get(part_code, 0)
        inv = inv_pivot.get(part_code)
        store_qty = float(inv.get(store_code, 0)) if inv else 0
        total_qty = sum(float(inv.get(sc, 0)) for sc in _ORDER_CHECK_STORE_CODES) if inv else 0

        # 'freq_system' (tính trên TỔNG tồn + TỔNG bán của CẢ HỆ THỐNG) chỉ
        # còn dùng NỘI BỘ để quyết định có cần gợi ý mã con hậu tố hay không
        # (mã hoàn toàn không có tồn/không được theo dõi ở đâu trong hệ
        # thống). KHÔNG còn trả ra FE làm 'sales_freq' nữa - cột "Tình Hình
        # Bán" trên màn hình giờ phải phản ánh ĐÚNG cửa hàng admin đang chọn
        # để kiểm tra đơn (store_code), không phải gộp cả hệ thống, vì 1 mã
        # có thể bán rất chạy ở cửa hàng này nhưng lại đang tồn đọng ở cửa
        # hàng khác - gộp chung dễ khiến admin hiểu sai tình hình bán thực
        # tế tại đúng cửa hàng cần đặt hàng.
        freq_system = classify_sales_frequency(total_qty, sold_by_part.get(part_code, 0.0), period_months) if inv else None
        sold_map = sold_by_part_store.get(part_code, {})
        freq_store = classify_sales_frequency(store_qty, sold_map.get(store_code, 0.0), period_months) if inv else None

        lock = lock_by_part.get(part_code)
        debt = debt_by_part.get(part_code)

        # Mã cùng họ (mã gốc <-> biến thể hậu tố chữ cái, xem bước 3b ở
        # trên): CHỈ tìm/hiển thị khi chính mã đã dán KHÔNG có tần suất bán
        # riêng của nó (freq_system is None, tức không có tồn/không được hệ
        # thống theo dõi độc lập) - trường hợp đó nhiều khả năng mã dán vào
        # chỉ là "mã gốc" tham chiếu chung, cần gợi ý các biến thể hậu tố
        # thực tế đang tồn tại. Ngược lại, nếu mã đã dán ĐÃ CÓ tần suất bán
        # riêng (có tồn + được phân loại TX/TB/CB) thì đó là 1 mã ĐỘC LẬP,
        # tự nó đã đủ dữ liệu bán riêng - không coi các biến thể hậu tố khác
        # là "mã con" của nó nữa, nên không gợi ý.
        related_child_codes = []
        if freq_system is None:
            for child_code in child_codes_by_parent.get(part_code, []):
                child_lock = child_lock_by_code.get(child_code)
                child_locked = bool(child_lock['is_locked']) if child_lock else False
                child_qty = child_inv_by_code.get(child_code, 0.0)
                child_has_stock = child_qty > 0 or (child_lock and (child_lock.get('has_stock_bac') or child_lock.get('has_stock_nam')))
                if child_has_stock:
                    c_breakdown = child_store_breakdown.get(child_code, {})
                    c_sold_map = child_sold_by_store.get(child_code, {})
                    c_breakdown_freq = {}
                    for sc in _ORDER_CHECK_STORE_CODES:
                        cls_sc = classify_sales_frequency(c_breakdown.get(sc, 0.0), c_sold_map.get(sc, 0.0), period_months)
                        c_breakdown_freq[sc] = cls_sc['code'] if cls_sc else None
                    related_child_codes.append({
                        'part_code': child_code,
                        'is_locked': child_locked,
                        'total_qty': child_qty,
                        'store_breakdown': {sc: c_breakdown.get(sc, 0.0) for sc in _ORDER_CHECK_STORE_CODES},
                        'store_breakdown_freq': c_breakdown_freq,
                        'lock_qty_bac': float(child_lock['qty_bac']) if child_lock and child_lock.get('qty_bac') is not None else None,
                        'lock_qty_nam': float(child_lock['qty_nam']) if child_lock and child_lock.get('qty_nam') is not None else None,
                        'lock_has_stock_bac': child_lock.get('has_stock_bac') if child_lock else None,
                        'lock_has_stock_nam': child_lock.get('has_stock_nam') if child_lock else None,
                    })

        # Đề xuất luân chuyển: chỉ xét khi tồn của cửa hàng đang đặt < số
        # lượng cần, và có kho khác đang bán chậm (TB/CB) VÀ dư nhiều (còn
        # đủ dùng >= ORDER_CHECK_TRANSFER_MIN_MONTHS_OF_STOCK tháng SAU KHI
        # đã trừ phần đề xuất chuyển đi) - ưu tiên kho có tồn nhiều nhất trước.
        transfer_suggestions = []
        still_needed = max(0.0, qty_order - store_qty)
        # sold_map đã tính ở trên (dùng chung cho freq_store).
        # Tần suất bán RIÊNG của từng cửa hàng cho đúng mã này (khác với
        # 'sales_freq' ở dưới - giờ ĐÃ là tần suất bán của RIÊNG cửa hàng
        # store_code đang kiểm tra, không còn là tổng cả hệ thống nữa).
        # Trả về cho FE để hiển thị cạnh mỗi cửa hàng
        # trong "Tồn Hệ Thống", giúp admin tự thấy TẠI SAO 1 cửa hàng có tồn
        # nhưng KHÔNG được đề xuất chuyển (thường là do đang bán nhanh - TX -
        # ngay tại chính cửa hàng đó, nên không có "dư" để cho cửa hàng khác,
        # dù có hàng thật). Hoàn toàn KHÔNG dùng tồn kho Bắc/Nam của Honda ở
        # bất kỳ đâu trong việc tính đề xuất luân chuyển - Bắc/Nam chỉ để
        # tham khảo riêng (xem lock_qty_bac/lock_qty_nam).
        store_breakdown_freq = {}
        if inv:
            for sc in _ORDER_CHECK_STORE_CODES:
                sc_qty_f = float(inv.get(sc, 0))
                cls_sc = classify_sales_frequency(sc_qty_f, sold_map.get(sc, 0.0), period_months)
                store_breakdown_freq[sc] = cls_sc['code'] if cls_sc else None
        if inv and still_needed > 0:
            candidates = []
            for sc in _ORDER_CHECK_STORE_CODES:
                if sc == store_code:
                    continue
                sc_qty = float(inv.get(sc, 0))
                if sc_qty <= 0:
                    continue
                cls = classify_sales_frequency(sc_qty, sold_map.get(sc, 0.0), period_months)
                if cls is None or cls['code'] not in ('TB', 'CB'):
                    continue
                candidates.append((sc, sc_qty, cls))
            candidates.sort(key=lambda x: x[1], reverse=True)
            for sc, sc_qty, cls in candidates:
                if still_needed <= 0:
                    break
                avg_month = cls.get('avg_month') or 0
                # Số lượng tối đa có thể lấy mà kho đó vẫn còn đủ dùng
                # ORDER_CHECK_TRANSFER_MIN_MONTHS_OF_STOCK tháng sau khi chuyển.
                # LÀM TRÒN XUỐNG (floor) thành số NGUYÊN: phụ tùng là đơn vị
                # đếm được (cái/bộ...), không có khái niệm "lấy 3.68 cái" -
                # nếu không floor ở đây, phần thập phân từ avg_month (số
                # lượng bán TRUNG BÌNH/tháng, vốn luôn là số lẻ) sẽ lan
                # sang can_give/take/still_needed, khiến các số trên giao
                # diện đều bị lẻ dù các số lượng gốc trong kho là số nguyên.
                keep_min = avg_month * ORDER_CHECK_TRANSFER_MIN_MONTHS_OF_STOCK
                can_give = math.floor(max(0.0, sc_qty - keep_min))
                if can_give <= 0:
                    continue
                take = min(still_needed, can_give)
                if take <= 0:
                    continue
                transfer_suggestions.append({
                    'store_code': sc, 'qty': take,
                    'store_available_qty': sc_qty, 'sales_freq': cls,
                })
                still_needed -= take

        result.append({
            'part_code': part_code,
            'qty_order': qty_order,
            'part_name': inv.get('part_name') if inv else None,
            'unit': inv.get('unit') if inv else None,
            'found': inv is not None,
            'store_qty': store_qty,
            'total_qty': total_qty,
            'store_breakdown': {sc: float(inv.get(sc, 0)) for sc in _ORDER_CHECK_STORE_CODES} if inv else {},
            # Tần suất bán riêng từng cửa hàng cho mã này (TX/TB/CB hoặc null
            # nếu tồn = 0) - xem giải thích chi tiết ở khối tính transfer_suggestions
            # phía trên. Dùng để FE hiển thị lý do 1 cửa hàng không được đề
            # xuất chuyển dù đang có tồn > 0.
            'store_breakdown_freq': store_breakdown_freq,
            # 'sales_freq' + 'qty_sold_period' hiển thị ở cột "Tình Hình Bán"
            # trên FE: tính theo ĐÚNG cửa hàng đang kiểm tra đơn (store_code),
            # KHÔNG PHẢI gộp cả hệ thống - xem giải thích ở chỗ tính freq_store.
            'sales_freq': freq_store,
            'qty_sold_period': round(sold_map.get(store_code, 0.0), 2),
            'period_months': period_months,
            'is_locked': bool(lock['is_locked']) if lock else False,
            'replacement_code': lock.get('replacement_code') if lock else None,
            'lock_qty_bac': float(lock['qty_bac']) if lock and lock.get('qty_bac') is not None else None,
            'lock_qty_nam': float(lock['qty_nam']) if lock and lock.get('qty_nam') is not None else None,
            # Cờ có/không tồn (Y/N) - chỉ có giá trị khi file nguồn là kiểu
            # mới (không có số lượng cụ thể, xem _to_stock_flag ở
            # import_order_lock()); None nếu chưa import hoặc file kiểu cũ.
            'lock_has_stock_bac': lock.get('has_stock_bac') if lock else None,
            'lock_has_stock_nam': lock.get('has_stock_nam') if lock else None,
            'debt_qty': debt['debt_qty'] if debt else 0,
            'shipping_qty': debt['shipping_qty'] if debt else 0,
            'debt_po_codes': debt['po_codes'] if debt else [],
            'still_needed_after_transfer': round(max(0.0, still_needed), 2),
            'transfer_suggestions': transfer_suggestions,
            'related_child_codes': related_child_codes,
            'note': note_by_part.get(part_code) or '',
            # Số lượng đề xuất để admin DUYỆT (có thể sửa tay ở FE trước khi
            # chốt): mã đang bị khoá đặt hàng -> đề xuất 0 (không đặt được,
            # chờ admin tự quyết định dùng mã thay thế/luân chuyển); còn lại
            # -> đúng bằng phần CÒN THIẾU sau khi đã trừ đề xuất luân chuyển.
            'suggested_approve_qty': 0 if (lock and lock['is_locked']) else round(max(0.0, still_needed), 2),
        })

    cursor2 = db.cursor()
    cursor2.execute('SELECT filename, uploaded_by, upload_time FROM order_lock_meta WHERE id = 1')
    lock_meta_row = cursor2.fetchone()
    lock_meta = dict(lock_meta_row) if lock_meta_row else None
    if lock_meta and lock_meta.get('upload_time'):
        lock_meta['upload_time'] = lock_meta['upload_time'].strftime('%d/%m/%Y %H:%M')
    cursor2.close()

    return jsonify({'success': True, 'data': result, 'lock_meta': lock_meta})


@app.route('/api/admin/order-check/note', methods=['POST'])
def order_check_save_note():
    """Lưu (hoặc xoá nếu để trống) ghi chú của admin cho 1 mã hàng ở màn
    "Duyệt Đơn Hàng" - UPSERT theo part_code, dùng CHUNG cho mọi lần kiểm
    tra sau này có mã hàng đó (xem bảng order_check_notes)."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    part_code = str(data.get('part_code', '') or '').strip()
    note = str(data.get('note', '') or '').strip()
    if not part_code:
        return jsonify({'error': 'Thiếu mã hàng.'}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        if note:
            cursor.execute('''
                INSERT INTO order_check_notes (part_code, note, updated_at, updated_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (part_code) DO UPDATE SET
                    note = EXCLUDED.note,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
            ''', (part_code, note, vn_now(), _current_actor_name()))
        else:
            # Ghi chú rỗng -> xoá hẳn dòng, không lưu ghi chú rỗng vô nghĩa.
            cursor.execute('DELETE FROM order_check_notes WHERE part_code = %s', (part_code,))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        app.logger.error("Lỗi /api/admin/order-check/note: %s\n%s", e, traceback.format_exc())
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()


@app.route('/api/admin/update-price', methods=['POST'])
def update_price():
    """Admin CẬP NHẬT THỦ CÔNG giá bán của 1 mã hàng riêng lẻ (không cần
    upload lại cả file Excel giá bán) - dùng cho ô "Giá Bán" có thể bấm sửa
    trực tiếp ngay trên bảng Tồn Kho. Nếu sale_price để trống -> XOÁ giá của
    mã hàng đó (quay về trạng thái "chưa có giá")."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    part_code = str(data.get('part_code', '') or '').strip()
    if not part_code:
        return jsonify({'error': 'Thiếu mã hàng.'}), 400

    raw_price = data.get('sale_price', None)
    db = get_db()
    cursor = db.cursor()
    try:
        # sale_price rỗng/None -> xoá giá của mã hàng này (không còn giá).
        if raw_price is None or str(raw_price).strip() == '':
            cursor.execute('DELETE FROM part_prices WHERE part_code = %s', (part_code,))
            db.commit()
            return jsonify({'success': True, 'sale_price': None})

        try:
            price = float(raw_price)
            if math.isnan(price) or price < 0:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({'error': 'Giá bán không hợp lệ.'}), 400

        now = vn_now()
        cursor.execute('''
            INSERT INTO part_prices (part_code, sale_price, updated_at, updated_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (part_code) DO UPDATE SET
                sale_price = EXCLUDED.sale_price,
                updated_at = EXCLUDED.updated_at,
                updated_by = EXCLUDED.updated_by
        ''', (part_code, price, now, _current_actor_name()))
        db.commit()
        invalidate_inventory_cache()
        return jsonify({'success': True, 'sale_price': price})
    except Exception as e:
        db.rollback()
        app.logger.error("Lỗi /api/admin/update-price: %s\n%s", e, traceback.format_exc())
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()


@app.route('/api/inventory', methods=['GET'])
def get_inventory():
    """Trả về tồn kho hệ thống dạng pivot (1 dòng/mã hàng, 6 cột theo cửa
    hàng), kèm giá bán (nếu có) - Mọi user (admin lẫn store) đều xem được
    TOÀN BỘ hệ thống - đây là ngoại lệ có chủ đích so với dữ liệu PO (vốn
    giới hạn theo cửa hàng). Vì response GIỐNG HỆT NHAU cho mọi user, dùng
    cache trong bộ nhớ (_inventory_cache) để tránh tính lại pivot + phân
    loại tần suất bán trên mỗi request - xem invalidate_inventory_cache()."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    with _inventory_cache_lock:
        cached_body = _inventory_cache['body']
    if cached_body is not None:
        return app.response_class(cached_body, mimetype='application/json')

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT part_code, part_name, unit, store_code, quantity, is_pi2 FROM inventory_items')
    items = cursor.fetchall()

    # Giá bán lưu ở bảng riêng (part_prices) - áp dụng chung cho mã hàng
    # trên toàn hệ thống, không phân biệt theo cửa hàng.
    cursor.execute('SELECT part_code, sale_price FROM part_prices')
    price_by_part = {r['part_code']: (float(r['sale_price']) if r['sale_price'] is not None else None) for r in cursor.fetchall()}

    pivot = {}
    for it in items:
        p = pivot.setdefault(it['part_code'], {
            'part_code': it['part_code'],
            'part_name': it['part_name'],
            'unit': it['unit'],
            'sale_price': price_by_part.get(it['part_code']),
            'NS1': 0, 'NS2': 0, 'NS3': 0, 'NS4': 0, 'NS5': 0, 'NSM1': 0,
            # "CB" (Kho CB / mã kho KHANGCHAMBAN) là kho ĐỘC LẬP, hiển thị
            # tách riêng - KHÔNG tính vào "Tổng Tồn 6 CH" của 6 cửa hàng
            # NS1..NSM1 (xem _SINGLE_WORD_WAREHOUSES trong parse_inventory_excel).
            'CB': 0,
            '_is_pi2': False,
        })
        qty = float(it['quantity']) if it['quantity'] is not None else 0
        p[it['store_code']] = qty
        # Chỉ cần 1 trong các dòng của mã hàng này có gộp số lượng từ kho
        # phụ PI2 (hiện tại luôn là dòng NSM1) là đủ để xếp mã hàng đó
        # xuống cuối bảng khi hiển thị.
        if it['is_pi2']:
            p['_is_pi2'] = True

    # Sắp xếp: các mã hàng liên quan PI2 luôn nằm DƯỚI CÙNG (is_pi2=True ->
    # 1, sort sau); trong mỗi nhóm vẫn giữ nguyên thứ tự theo mã hàng như cũ.
    data = sorted(pivot.values(), key=lambda x: (x['_is_pi2'], x['part_code']))
    for row in data:
        row.pop('_is_pi2', None)

    # Gắn kèm phân loại tần suất bán (TX/TB/CB) cho icon góc trên-phải mỗi
    # mã hàng - tính CẢ 2 mức: 'sales_freq' (tổng toàn hệ thống, không phân
    # biệt kho) và 'sales_freq_by_store' (riêng từng kho/cửa hàng, dùng
    # đúng tồn + xuất bán của kho đó) - để FE hiển thị tooltip so sánh
    # "cửa hàng đó" vs "hệ thống". Chỉ có nếu admin đã từng import số liệu
    # xuất bán (sales_export_items) - nếu chưa import lần nào, các field
    # này sẽ là None/rỗng và FE tự hiểu là "chưa có dữ liệu, không hiện icon".
    cursor.execute('SELECT COUNT(*) AS c FROM sales_export_items')
    has_sales_data = (cursor.fetchone() or {}).get('c', 0) > 0
    if has_sales_data:
        cursor.execute('SELECT period_months FROM sales_export_meta WHERE id = 1')
        meta_row = cursor.fetchone()
        period_months = (meta_row['period_months'] if meta_row and meta_row.get('period_months') else 3) or 3

        cursor.execute('SELECT part_code, SUM(qty_sold) AS qty_sold FROM sales_export_items GROUP BY part_code')
        sold_by_part = {r['part_code']: float(r['qty_sold'] or 0) for r in cursor.fetchall()}

        cursor.execute('SELECT part_code, store_code, SUM(qty_sold) AS qty_sold FROM sales_export_items GROUP BY part_code, store_code')
        sold_by_part_store = {}
        for r in cursor.fetchall():
            sold_by_part_store.setdefault(r['part_code'], {})[r['store_code']] = float(r['qty_sold'] or 0)

        store_codes = ('NS1', 'NS2', 'NS3', 'NS4', 'NS5', 'NSM1', 'CB')
        for row in data:
            total_qty = sum(float(row.get(sc) or 0) for sc in store_codes)
            row['sales_freq'] = classify_sales_frequency(total_qty, sold_by_part.get(row['part_code'], 0.0), period_months)

            by_store = {}
            sold_map = sold_by_part_store.get(row['part_code'], {})
            for sc in store_codes:
                qty_sc = float(row.get(sc) or 0)
                cls = classify_sales_frequency(qty_sc, sold_map.get(sc, 0.0), period_months)
                if cls is not None:
                    by_store[sc] = cls
            row['sales_freq_by_store'] = by_store
    else:
        for row in data:
            row['sales_freq'] = None
            row['sales_freq_by_store'] = {}

    cursor.execute('SELECT filename, uploaded_by, upload_time, total_parts, skipped_rows FROM inventory_meta WHERE id = 1')
    meta_row = cursor.fetchone()
    meta = dict(meta_row) if meta_row else None
    if meta and meta.get('upload_time'):
        meta['upload_time'] = meta['upload_time'].strftime('%d/%m/%Y %H:%M')

    cursor.execute('SELECT filename, uploaded_by, upload_time, total_parts, skipped_rows FROM price_meta WHERE id = 1')
    price_meta_row = cursor.fetchone()
    price_meta = dict(price_meta_row) if price_meta_row else None
    if price_meta and price_meta.get('upload_time'):
        price_meta['upload_time'] = price_meta['upload_time'].strftime('%d/%m/%Y %H:%M')

    cursor.execute('SELECT filename, uploaded_by, upload_time, period_months FROM sales_export_meta WHERE id = 1')
    sales_meta_row = cursor.fetchone()
    sales_meta = dict(sales_meta_row) if sales_meta_row else None
    if sales_meta and sales_meta.get('upload_time'):
        sales_meta['upload_time'] = sales_meta['upload_time'].strftime('%d/%m/%Y %H:%M')

    cursor.close()

    payload = {'success': True, 'data': data, 'meta': meta, 'price_meta': price_meta, 'sales_meta': sales_meta}
    # orjson serialize nhanh hơn đáng kể so với json.dumps chuẩn (mà jsonify()
    # dùng) với payload lớn cỡ này (~30 nghìn mã hàng) - lưu lại cache để các
    # request tiếp theo (từ user khác, hoặc F5 lại) khỏi phải tính + serialize
    # lại từ đầu, cho tới khi có import mới (xem invalidate_inventory_cache()).
    body = dumps_json(payload).encode('utf-8')
    with _inventory_cache_lock:
        _inventory_cache['body'] = body
    return app.response_class(body, mimetype='application/json')


@app.route('/api/locations', methods=['GET'])
def get_locations():
    """Trả về vị trí kệ hàng, gộp với danh sách tồn kho hệ thống:
      - Store: dạng danh sách phẳng (1 dòng/mã hàng) - CHÍNH cửa hàng mình.
        Lấy HỢP (union) của mọi mã hàng có tồn kho tại cửa hàng này VÀ mọi
        mã hàng đã được gán vị trí tại cửa hàng này (kể cả khi mã đó KHÔNG
        có/không còn tồn kho ở đây) - để mã hàng không "biến mất" khỏi danh
        sách chỉ vì hết hàng hoặc chưa từng nhập/bán tại cửa hàng này.
      - Admin: dạng PIVOT (1 dòng/mã hàng, gộp vị trí của cả 6 cửa hàng vào
        1 dòng, giống cách bảng Tồn Kho Hệ Thống hiển thị), luôn có ĐỦ cả 6
        cửa hàng cho MỌI mã hàng - kể cả cửa hàng chưa từng khai báo mã hàng
        đó trong file tồn kho (quantity = None) - để admin xem/sửa vị trí ở
        bất kỳ cửa hàng nào, không chỉ những cửa hàng đang có mã hàng đó."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    role = session['role']
    db = get_db()
    cursor = db.cursor()

    # --- Cache phản hồi (xem _resp_cache ở đầu file): chỉ chạy 1 câu SQL nhỏ để
    # lấy "chữ ký"; chữ ký không đổi -> trả body dựng sẵn, không đọc lại tồn kho.
    loc_key = None
    loc_sig = None
    if role == 'store':
        loc_key = ('locations', 'store', session['store_code'])
        cursor.execute(
            'SELECT COUNT(*) AS c, MAX(updated_at) AS v FROM part_locations WHERE store_code = %s',
            (session['store_code'],)
        )
        sig_row = cursor.fetchone()
        loc_sig = (_inventory_epoch, _write_epoch, sig_row['c'], str(sig_row['v']))
    elif role == 'admin':
        loc_key = ('locations', 'admin')
        cursor.execute('SELECT COUNT(*) AS c, MAX(updated_at) AS v FROM part_locations')
        sig_row = cursor.fetchone()
        loc_sig = (_inventory_epoch, _write_epoch, sig_row['c'], str(sig_row['v']),
                   tuple(sorted(_valid_store_codes(cursor))))
    if loc_key is not None:
        cached_body = _resp_cache_get(loc_key, loc_sig)
        if cached_body is not None:
            cursor.close()
            return app.response_class(cached_body, mimetype='application/json')

    if role == 'store':
        target_store = session['store_code']

        cursor.execute('''
            SELECT part_code, part_name, unit, quantity
            FROM inventory_items
            WHERE store_code = %s
        ''', (target_store,))
        inv_map = {r['part_code']: r for r in cursor.fetchall()}

        cursor.execute('''
            SELECT part_code, location_1, location_2, location_3, updated_by, updated_at
            FROM part_locations
            WHERE store_code = %s
        ''', (target_store,))
        loc_map = {r['part_code']: r for r in cursor.fetchall()}

        # Mã hàng có thể chỉ tồn tại trong part_locations (đã được gán vị
        # trí dù chưa/không còn tồn kho tại ĐÚNG cửa hàng này - ví dụ admin
        # gán tay, hoặc store tự thêm vị trí cho mã sắp nhập) - vẫn phải
        # đưa vào danh sách hiển thị, không chỉ lấy mã có dòng tồn kho.
        all_part_codes = set(inv_map.keys()) | set(loc_map.keys())

        # part_name/unit: nếu mã hàng không có dòng tồn kho tại CHÍNH cửa
        # hàng này thì lấy tên/đơn vị đại diện từ BẤT KỲ cửa hàng nào khác
        # đã từng khai báo mã hàng đó, để không hiển thị trống tên hàng.
        missing_codes = list(all_part_codes - set(inv_map.keys()))
        fallback_info = {}
        if missing_codes:
            cursor.execute('''
                SELECT DISTINCT ON (part_code) part_code, part_name, unit
                FROM inventory_items
                WHERE part_code = ANY(%s)
                ORDER BY part_code, id DESC
            ''', (missing_codes,))
            fallback_info = {r['part_code']: r for r in cursor.fetchall()}

        data = []
        for part_code in all_part_codes:
            inv = inv_map.get(part_code)
            loc = loc_map.get(part_code)
            fb = fallback_info.get(part_code)
            data.append({
                'part_code': part_code,
                'part_name': (inv['part_name'] if inv else None) or (fb['part_name'] if fb else None),
                'unit': (inv['unit'] if inv else None) or (fb['unit'] if fb else None),
                'quantity': float(inv['quantity']) if inv and inv.get('quantity') is not None else 0,
                'location_1': loc['location_1'] if loc else None,
                'location_2': loc['location_2'] if loc else None,
                'location_3': loc['location_3'] if loc else None,
                'updated_by': loc['updated_by'] if loc else None,
                'updated_at': format_vi_datetime(loc['updated_at']) if loc and loc.get('updated_at') else None,
            })
        data.sort(key=lambda d: d['part_code'])
        cursor.close()
        resp = jsonify({'success': True, 'data': data, 'store': target_store})
        _resp_cache_put(loc_key, loc_sig, resp.get_data())
        return resp

    if role != 'admin':
        cursor.close()
        return jsonify({'error': 'Forbidden'}), 403

    # Admin: dựng bảng PIVOT ngay tại backend - mỗi mã hàng 1 dòng, "stores"
    # là dict con {store_code: {...}} chứa ĐỦ TẤT CẢ cửa hàng hợp lệ trong hệ
    # thống cho MỌI mã hàng, kể cả cửa hàng đó CHƯA TỪNG khai báo mã hàng
    # này trong file tồn kho (quantity = None) - để admin luôn thêm/sửa được
    # vị trí tại bất kỳ cửa hàng nào cho bất kỳ mã hàng nào đã biết trong hệ
    # thống, không bị chặn chỉ vì đúng cửa hàng đó chưa từng có mã hàng này.
    all_stores = sorted(_valid_store_codes(cursor))

    # Tên/đơn vị đại diện cho mỗi mã hàng: lấy từ dòng tồn kho GẦN NHẤT
    # (id lớn nhất) bất kể thuộc cửa hàng nào - vì cùng 1 mã hàng thường có
    # tên/đơn vị giống nhau ở mọi cửa hàng.
    cursor.execute('''
        SELECT DISTINCT ON (part_code) part_code, part_name, unit
        FROM inventory_items
        ORDER BY part_code, id DESC
    ''')
    part_info = {r['part_code']: {'part_name': r['part_name'], 'unit': r['unit']} for r in cursor.fetchall()}

    cursor.execute('SELECT part_code, store_code, quantity, is_pi2 FROM inventory_items')
    inv_rows = cursor.fetchall()
    qty_map = {(r['part_code'], r['store_code']): r['quantity'] for r in inv_rows}
    # Mã hàng nào có ÍT NHẤT 1 dòng tồn kho gộp từ kho phụ PI2 (hiện luôn là
    # dòng NSM1) - dùng để xếp mã hàng đó xuống cuối bảng, giống bảng Tồn
    # Kho Hệ Thống.
    pi2_parts = {r['part_code'] for r in inv_rows if r['is_pi2']}

    cursor.execute('SELECT part_code, store_code, location_1, location_2, location_3, updated_at FROM part_locations')
    loc_rows = cursor.fetchall()
    loc_map = {(r['part_code'], r['store_code']): r for r in loc_rows}
    cursor.close()

    # Mã hàng có thể chỉ tồn tại trong part_locations (đã được gán vị trí dù
    # chưa từng xuất hiện ở BẤT KỲ cửa hàng nào trong file tồn kho) - vẫn
    # đưa vào để admin xem/sửa tiếp, không bị "biến mất" khỏi bảng.
    all_part_codes = set(part_info.keys()) | {r['part_code'] for r in loc_rows}

    pivot = {}
    for part_code in all_part_codes:
        info = part_info.get(part_code, {'part_name': None, 'unit': None})
        stores = {}
        for store in all_stores:
            loc = loc_map.get((part_code, store))
            qty = qty_map.get((part_code, store))
            stores[store] = {
                'location_1': loc['location_1'] if loc else None,
                'location_2': loc['location_2'] if loc else None,
                'location_3': loc['location_3'] if loc else None,
                'updated_at': format_vi_datetime(loc['updated_at']) if loc and loc['updated_at'] else None,
                'quantity': float(qty) if qty is not None else None,
            }
        pivot[part_code] = {
            'part_code': part_code,
            'part_name': info['part_name'],
            'unit': info['unit'],
            'stores': stores,
        }

    # Sắp xếp giống bảng Tồn Kho Hệ Thống: mã hàng liên quan PI2 xuống cuối,
    # còn lại giữ nguyên thứ tự theo mã hàng như cũ.
    data = sorted(pivot.values(), key=lambda x: (x['part_code'] in pi2_parts, x['part_code']))
    resp = jsonify({'success': True, 'data': data})
    _resp_cache_put(loc_key, loc_sig, resp.get_data())
    return resp


@app.route('/api/locations/save', methods=['POST'])
def save_location():
    """Thêm/sửa (thủ công) tối đa 3 vị trí cho 1 mã hàng tại 1 cửa hàng.
    Store chỉ sửa được cho cửa hàng của chính mình. Điều kiện hợp lệ cho cả
    store lẫn admin: mã hàng chỉ cần tồn tại trong danh sách tồn kho ADMIN
    ĐÃ IMPORT ở BẤT KỲ cửa hàng nào trong hệ thống (hoặc đã từng được gán
    vị trí trước đó) - KHÔNG bắt buộc phải CÒN hàng / có hàng đúng tại cửa
    hàng đang nhập vị trí, để tránh tạo vị trí "ma" cho mã hàng chưa từng
    được admin khai báo ở đâu cả, nhưng vẫn cho phép store nhập vị trí cho
    mã hàng đang hết hàng hoặc chưa từng nhập/bán tại chính cửa hàng đó."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    part_code = (data.get('part_code') or '').strip()
    if not part_code:
        return jsonify({'error': 'Vui lòng nhập mã hàng.'}), 400

    locs = [
        (data.get('location_1') or '').strip() or None,
        (data.get('location_2') or '').strip() or None,
        (data.get('location_3') or '').strip() or None,
    ]

    db = get_db()
    cursor = db.cursor()

    if role == 'store':
        store_code = session['store_code']
    else:
        store_code = (data.get('store_code') or '').strip().upper()
        valid_stores = _valid_store_codes(cursor)
        if not store_code or store_code not in valid_stores:
            cursor.close()
            return jsonify({'error': 'Vui lòng chọn cửa hàng hợp lệ.'}), 400

    # Mã hàng chỉ cần tồn tại ở BẤT KỲ cửa hàng nào trong hệ thống (hoặc đã
    # từng được gán vị trí trước đó) - không bắt buộc phải có/còn hàng ĐÚNG
    # tại store_code đang nhập vị trí này.
    cursor.execute(
        '''SELECT 1 FROM inventory_items WHERE part_code = %s
           UNION SELECT 1 FROM part_locations WHERE part_code = %s''',
        (part_code, part_code)
    )
    if not cursor.fetchone():
        cursor.close()
        return jsonify({'error': f'Mã hàng "{part_code}" không tồn tại trong danh sách tồn kho admin đã import.'}), 400

    now = vn_now()
    cursor.execute('''
        INSERT INTO part_locations (store_code, part_code, location_1, location_2, location_3, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (store_code, part_code) DO UPDATE SET
            location_1 = EXCLUDED.location_1,
            location_2 = EXCLUDED.location_2,
            location_3 = EXCLUDED.location_3,
            updated_by = EXCLUDED.updated_by,
            updated_at = EXCLUDED.updated_at
    ''', (store_code, part_code, locs[0], locs[1], locs[2], session['user'], now))
    db.commit()
    cursor.close()

    return jsonify({'success': True})


@app.route('/api/locations/delete', methods=['POST'])
def delete_location():
    """Xoá toàn bộ vị trí đã lưu của 1 mã hàng tại 1 cửa hàng."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    part_code = (data.get('part_code') or '').strip()
    if not part_code:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()

    if role == 'store':
        store_code = session['store_code']
    else:
        store_code = (data.get('store_code') or '').strip().upper()
        valid_stores = _valid_store_codes(cursor)
        if not store_code or store_code not in valid_stores:
            cursor.close()
            return jsonify({'error': 'Vui lòng chọn cửa hàng hợp lệ.'}), 400

    cursor.execute('DELETE FROM part_locations WHERE store_code = %s AND part_code = %s', (store_code, part_code))
    db.commit()
    cursor.close()

    return jsonify({'success': True})


@app.route('/api/locations/import-excel', methods=['POST'])
def import_locations_excel():
    """Nhập vị trí hàng loạt từ file Excel (xem parse_location_excel()).
    Store luôn áp dụng cho cửa hàng của chính mình; Admin phải chọn 1 cửa
    hàng áp dụng (form field 'store_code') trước khi tải file lên. Mã hàng
    chỉ cần tồn tại trong danh sách tồn kho ADMIN ĐÃ IMPORT ở BẤT KỲ cửa
    hàng nào trong hệ thống - không bắt buộc phải có/còn hàng ĐÚNG tại cửa
    hàng đang áp dụng - mới tránh tạo vị trí "ma" cho mã hàng admin chưa
    từng khai báo ở đâu cả, đồng thời vẫn cho nhập vị trí cho mã hàng đang
    hết hàng hoặc chưa từng nhập/bán tại đúng cửa hàng đó. Nếu 1 mã hàng
    xuất hiện NHIỀU LẦN trong cùng file, chỉ áp dụng dòng CUỐI CÙNG (ghi đè
    các dòng trước) - bắt buộc phải lọc trùng trước khi UPSERT, vì Postgres
    không cho phép 1 lệnh INSERT ... ON CONFLICT DO UPDATE tác động lên
    cùng 1 dòng 2 lần trong cùng 1 lệnh (sẽ báo lỗi "ON CONFLICT DO UPDATE
    command cannot affect row a second time")."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    location_file = request.files.get('location_file')
    if not location_file:
        return jsonify({'error': 'Vui lòng chọn file Excel vị trí để tải lên.'}), 400

    db = get_db()
    cursor = db.cursor()

    if role == 'store':
        store_code = session['store_code']
    else:
        store_code = (request.form.get('store_code') or '').strip().upper()
        valid_stores = _valid_store_codes(cursor)
        if not store_code or store_code not in valid_stores:
            cursor.close()
            return jsonify({'error': 'Vui lòng chọn cửa hàng áp dụng trước khi tải file lên.'}), 400

    try:
        rows, skipped_rows = parse_location_excel(location_file)
        if not rows:
            cursor.close()
            return jsonify({'error': 'Không đọc được dòng dữ liệu hợp lệ nào trong file.'}), 400

        # Mã hàng chỉ cần có trong danh sách tồn kho admin đã import ở BẤT
        # KỲ cửa hàng nào (không giới hạn đúng store_code đang áp dụng) -
        # cho phép nhập vị trí cả cho mã hàng chưa từng có tồn kho tại
        # store_code này.
        cursor.execute('SELECT DISTINCT part_code FROM inventory_items')
        valid_parts = {r['part_code'] for r in cursor.fetchall()}

        now = vn_now()
        # Lọc trùng mã hàng NGAY TRONG FILE: dùng dict để chỉ giữ lại dòng
        # CUỐI CÙNG cho mỗi mã hàng (dòng sau ghi đè dòng trước) - nếu không
        # lọc trước, 1 mã hàng xuất hiện >1 lần sẽ khiến execute_values bên
        # dưới cố UPDATE cùng 1 dòng part_locations 2 lần trong 1 lệnh, bị
        # Postgres từ chối với lỗi "ON CONFLICT DO UPDATE command cannot
        # affect row a second time".
        insert_map = {}
        not_in_stock = 0  # mã hàng không có trong danh sách admin đã import (ở BẤT KỲ cửa hàng nào), không phải "hết hàng tại store_code này"
        duplicate_rows = 0
        for r in rows:
            if r['part_code'] not in valid_parts:
                not_in_stock += 1
                continue
            if r['part_code'] in insert_map:
                duplicate_rows += 1
            insert_map[r['part_code']] = (
                store_code, r['part_code'], r['location_1'], r['location_2'], r['location_3'], session['user'], now
            )
        insert_rows = list(insert_map.values())

        if insert_rows:
            execute_values(
                cursor,
                '''INSERT INTO part_locations (store_code, part_code, location_1, location_2, location_3, updated_by, updated_at)
                   VALUES %s
                   ON CONFLICT (store_code, part_code) DO UPDATE SET
                       location_1 = EXCLUDED.location_1,
                       location_2 = EXCLUDED.location_2,
                       location_3 = EXCLUDED.location_3,
                       updated_by = EXCLUDED.updated_by,
                       updated_at = EXCLUDED.updated_at''',
                insert_rows
            )
        db.commit()
        cursor.close()

        return jsonify({
            'success': True,
            'applied': len(insert_rows),
            'skipped_rows': skipped_rows,
            'not_in_stock': not_in_stock,
            'duplicate_rows': duplicate_rows,
            'store_code': store_code,
        })
    except ValueError as e:
        cursor.close()
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.error("Lỗi /api/locations/import-excel: %s\n%s", e, traceback.format_exc())
        try:
            db.rollback()
        except Exception:
            pass
        cursor.close()
        return jsonify({'error': str(e)}), 500


@app.route('/api/damaged/summary', methods=['GET'])
def damaged_summary():
    """Trả về dữ liệu HÀNG HƯ HỎNG dạng gộp theo mã hàng, dùng để tô VÀNG
    cột "Hư Hỏng" trên bảng Tồn Kho Hệ Thống (mọi role đều xem được, giống
    cách bảng tồn kho hiển thị hệ thống chung) và dựng ghi chú (tooltip)
    khi bấm/rê vào ô đó - liệt kê TỪNG LẦN báo hư (cửa hàng nào, bao nhiêu,
    tình trạng gì), không chỉ tổng số."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT part_code, store_code, quantity, note, created_at
        FROM damaged_items
        ORDER BY part_code, created_at
    ''')
    rows = cursor.fetchall()
    cursor.close()

    by_part = {}
    for r in rows:
        p = by_part.setdefault(r['part_code'], {'total': 0, 'entries': []})
        qty = float(r['quantity']) if r['quantity'] is not None else 0
        p['total'] += qty
        p['entries'].append({
            'store': r['store_code'],
            'quantity': qty,
            'note': r['note'],
            'created_at': format_vi_datetime(r['created_at']) if r['created_at'] else None,
        })

    return jsonify({'success': True, 'data': by_part})


@app.route('/api/damaged', methods=['GET'])
def get_damaged():
    """Trả về danh sách CHI TIẾT từng lần báo hàng hư hỏng (dùng cho tab
    quản lý "Hàng Hư Hỏng"). Store chỉ xem/thêm/sửa/xoá của chính cửa hàng
    mình; Admin xem được TOÀN BỘ hệ thống và thêm/sửa/xoá được cho BẤT KỲ
    chi nhánh nào (xem quyền cụ thể ở /api/damaged/save, /api/damaged/update
    và /api/damaged/delete)."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    role = session['role']
    db = get_db()
    cursor = db.cursor()

    if role == 'store':
        cursor.execute('''
            SELECT id, store_code, part_code, quantity, note, created_by, created_at
            FROM damaged_items WHERE store_code = %s ORDER BY created_at DESC
        ''', (session['store_code'],))
    elif role == 'admin':
        cursor.execute('''
            SELECT id, store_code, part_code, quantity, note, created_by, created_at
            FROM damaged_items ORDER BY created_at DESC
        ''')
    else:
        cursor.close()
        return jsonify({'error': 'Forbidden'}), 403

    rows = cursor.fetchall()

    part_codes = list({r['part_code'] for r in rows})
    part_names = {}
    if part_codes:
        cursor.execute('''
            SELECT DISTINCT ON (part_code) part_code, part_name
            FROM inventory_items WHERE part_code = ANY(%s)
            ORDER BY part_code, id DESC
        ''', (part_codes,))
        part_names = {r['part_code']: r['part_name'] for r in cursor.fetchall()}

    # Chỉ lấy SỐ LƯỢNG ảnh mỗi lần báo (COUNT), KHÔNG lấy dữ liệu ảnh, để
    # danh sách hiển thị được badge "có N ảnh" mà không tốn băng thông tải
    # ảnh - ảnh thật chỉ tải khi người dùng bấm xem (xem /api/damaged/<id>/images).
    item_ids = [r['id'] for r in rows]
    image_counts = {}
    if item_ids:
        cursor.execute('''
            SELECT damaged_item_id, COUNT(*) AS c
            FROM damaged_item_images WHERE damaged_item_id = ANY(%s)
            GROUP BY damaged_item_id
        ''', (item_ids,))
        image_counts = {r['damaged_item_id']: r['c'] for r in cursor.fetchall()}
    cursor.close()

    data = []
    for r in rows:
        data.append({
            'id': r['id'],
            'store_code': r['store_code'],
            'part_code': r['part_code'],
            'part_name': part_names.get(r['part_code']),
            'quantity': float(r['quantity']) if r['quantity'] is not None else 0,
            'note': r['note'],
            'created_by': r['created_by'],
            'created_at': format_vi_datetime(r['created_at']) if r['created_at'] else None,
            'image_count': image_counts.get(r['id'], 0),
        })

    return jsonify({'success': True, 'data': data})


@app.route('/api/damaged/save', methods=['POST'])
def save_damaged():
    """Thêm 1 LẦN báo hàng hư hỏng mới. Cửa hàng chỉ báo được cho chính
    mình; Admin báo được thay cho BẤT KỲ chi nhánh nào (phải chọn store_code
    hợp lệ). KHÔNG upsert ghi đè - mỗi lần lưu là 1 dòng lịch sử mới, để
    cộng dồn đúng số lượng hư hỏng qua nhiều lần báo khác nhau (vd mã A hư
    1 ngày x, hư thêm 1 ngày y -> 2 dòng lịch sử riêng, không gộp lại)."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    part_code = (data.get('part_code') or '').strip()
    note = (data.get('note') or '').strip() or None
    try:
        quantity = float(data.get('quantity'))
    except (TypeError, ValueError):
        quantity = None

    if not part_code:
        return jsonify({'error': 'Vui lòng chọn mã hàng.'}), 400
    if quantity is None or quantity <= 0:
        return jsonify({'error': 'Vui lòng nhập số lượng hư hỏng hợp lệ (lớn hơn 0).'}), 400

    db = get_db()
    cursor = db.cursor()

    if role == 'store':
        store_code = session['store_code']
    else:
        store_code = (data.get('store_code') or '').strip()
        valid_stores = _valid_store_codes(cursor)
        if not store_code or store_code not in valid_stores:
            cursor.close()
            return jsonify({'error': 'Vui lòng chọn chi nhánh hợp lệ.'}), 400

    # Mã hàng chỉ cần tồn tại ở BẤT KỲ cửa hàng nào trong hệ thống (hoặc đã
    # từng được báo hư trước đó) - giống điều kiện của tính năng Vị trí.
    cursor.execute(
        '''SELECT 1 FROM inventory_items WHERE part_code = %s
           UNION SELECT 1 FROM damaged_items WHERE part_code = %s''',
        (part_code, part_code)
    )
    if not cursor.fetchone():
        cursor.close()
        return jsonify({'error': f'Mã hàng "{part_code}" không tồn tại trong danh sách tồn kho admin đã import.'}), 400

    now = vn_now()
    cursor.execute('''
        INSERT INTO damaged_items (store_code, part_code, quantity, note, created_by, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (store_code, part_code, quantity, note, _current_actor_name(), now))
    db.commit()
    cursor.close()

    return jsonify({'success': True})


@app.route('/api/damaged/delete', methods=['POST'])
def delete_damaged():
    """Xoá 1 lần báo hư hỏng đã lưu. Store chỉ xoá được của chính cửa hàng
    mình (kiểm tra store_code khớp trước khi xoá để tránh xoá nhầm/xoá hộ
    dữ liệu của cửa hàng khác); Admin được xoá của BẤT KỲ cửa hàng nào."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    try:
        item_id = int(data.get('id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    if role == 'admin':
        cursor.execute('DELETE FROM damaged_items WHERE id = %s', (item_id,))
    else:
        cursor.execute(
            'DELETE FROM damaged_items WHERE id = %s AND store_code = %s',
            (item_id, session['store_code'])
        )
    deleted = cursor.rowcount
    db.commit()
    cursor.close()

    if not deleted:
        return jsonify({'error': 'Không tìm thấy bản ghi (hoặc không thuộc cửa hàng của bạn).'}), 404
    return jsonify({'success': True})


@app.route('/api/damaged/update', methods=['POST'])
def update_damaged():
    """Sửa số lượng/tình trạng của 1 lần báo hư hỏng đã lưu. Store chỉ sửa
    được của chính cửa hàng mình; Admin sửa được của BẤT KỲ cửa hàng nào
    (không đổi được store_code/mã hàng - chỉ số lượng và ghi chú)."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    try:
        item_id = int(data.get('id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400
    note = (data.get('note') or '').strip() or None
    try:
        quantity = float(data.get('quantity'))
    except (TypeError, ValueError):
        quantity = None
    if quantity is None or quantity <= 0:
        return jsonify({'error': 'Vui lòng nhập số lượng hư hỏng hợp lệ (lớn hơn 0).'}), 400

    db = get_db()
    cursor = db.cursor()
    if role == 'admin':
        cursor.execute(
            'UPDATE damaged_items SET quantity = %s, note = %s WHERE id = %s',
            (quantity, note, item_id)
        )
    else:
        cursor.execute(
            'UPDATE damaged_items SET quantity = %s, note = %s WHERE id = %s AND store_code = %s',
            (quantity, note, item_id, session['store_code'])
        )
    updated = cursor.rowcount
    db.commit()
    cursor.close()

    if not updated:
        return jsonify({'error': 'Không tìm thấy bản ghi (hoặc không thuộc cửa hàng của bạn).'}), 404
    return jsonify({'success': True})


@app.route('/api/damaged/import-excel', methods=['POST'])
def import_damaged_excel():
    """Nhập hàng loạt các lần báo hư hỏng từ file Excel (xem
    parse_damaged_excel()). Cửa hàng chỉ nhập được cho chính mình; Admin
    nhập được thay cho BẤT KỲ chi nhánh nào (phải chọn store_code hợp lệ
    trong form). Mỗi dòng hợp lệ trong file tạo 1 dòng lịch sử MỚI trong
    damaged_items - KHÔNG lọc trùng mã hàng trong file (khác với import vị
    trí) vì ở đây 1 mã hàng xuất hiện nhiều lần trong file là hợp lệ (nhiều
    lần hư khác nhau), không phải lỗi cần ghi đè."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    damaged_file = request.files.get('damaged_file')
    if not damaged_file:
        return jsonify({'error': 'Vui lòng chọn file Excel hàng hư hỏng để tải lên.'}), 400

    db = get_db()
    cursor = db.cursor()

    if role == 'store':
        store_code = session['store_code']
    else:
        store_code = (request.form.get('store_code') or '').strip()
        valid_stores = _valid_store_codes(cursor)
        if not store_code or store_code not in valid_stores:
            cursor.close()
            return jsonify({'error': 'Vui lòng chọn chi nhánh hợp lệ.'}), 400

    try:
        rows, skipped_rows = parse_damaged_excel(damaged_file)
        if not rows:
            cursor.close()
            return jsonify({'error': 'Không đọc được dòng dữ liệu hợp lệ nào trong file.'}), 400

        cursor.execute(
            '''SELECT part_code FROM inventory_items
               UNION SELECT part_code FROM damaged_items'''
        )
        valid_parts = {r['part_code'] for r in cursor.fetchall()}

        now = vn_now()
        actor_name = _current_actor_name()
        insert_rows = []
        not_in_stock = 0
        for r in rows:
            if r['part_code'] not in valid_parts:
                not_in_stock += 1
                continue
            insert_rows.append((store_code, r['part_code'], r['quantity'], r['note'], actor_name, now))

        if insert_rows:
            execute_values(
                cursor,
                '''INSERT INTO damaged_items (store_code, part_code, quantity, note, created_by, created_at)
                   VALUES %s''',
                insert_rows
            )
        db.commit()
        cursor.close()

        return jsonify({
            'success': True,
            'applied': len(insert_rows),
            'skipped_rows': skipped_rows,
            'not_in_stock': not_in_stock,
            'store_code': store_code,
        })
    except ValueError as e:
        cursor.close()
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.error("Lỗi /api/damaged/import-excel: %s\n%s", e, traceback.format_exc())
        try:
            db.rollback()
        except Exception:
            pass
        cursor.close()
        return jsonify({'error': str(e)}), 500


def _damaged_item_store_code(cursor, item_id):
    """Trả về store_code của 1 lần báo hư hỏng (dùng để kiểm tra quyền
    thêm/xem/xoá ảnh), hoặc None nếu không tồn tại."""
    cursor.execute('SELECT store_code FROM damaged_items WHERE id = %s', (item_id,))
    row = cursor.fetchone()
    return row['store_code'] if row else None


@app.route('/api/damaged/<int:item_id>/images', methods=['GET'])
def get_damaged_images(item_id):
    """Trả về DANH SÁCH METADATA ảnh (id, dung lượng, người/ngày đăng) của
    1 lần báo hư hỏng - KHÔNG kèm dữ liệu ảnh (xem endpoint riêng
    /api/damaged/image/<id>) để việc mở xem danh sách ảnh không tốn băng
    thông tải cả ảnh khi người dùng chỉ đang xem có bao nhiêu ảnh."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    cursor = db.cursor()
    store_code = _damaged_item_store_code(cursor, item_id)
    if store_code is None:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy lần báo hư hỏng.'}), 404
    if role == 'store' and store_code != session['store_code']:
        cursor.close()
        return jsonify({'error': 'Forbidden'}), 403

    cursor.execute('''
        SELECT id, byte_size, created_by, created_at
        FROM damaged_item_images WHERE damaged_item_id = %s ORDER BY created_at ASC
    ''', (item_id,))
    rows = cursor.fetchall()
    cursor.close()

    return jsonify({'success': True, 'data': [
        {
            'id': r['id'],
            'url': url_for('get_damaged_image', image_id=r['id']),
            'byte_size': r['byte_size'],
            'created_by': r['created_by'],
            'created_at': format_vi_datetime(r['created_at']) if r['created_at'] else None,
        } for r in rows
    ]})


@app.route('/api/damaged/image/<int:image_id>', methods=['GET'])
def get_damaged_image(image_id):
    """Trả thẳng dữ liệu nhị phân của 1 ảnh. Đặt Cache-Control dài hạn +
    immutable vì ảnh KHÔNG BAO GIỜ bị sửa đè sau khi tạo (chỉ có thể xoá
    rồi thêm ảnh mới với id khác) - giúp trình duyệt/CDN không tải lại ảnh
    đã xem, tiết kiệm băng thông đáng kể khi mở lại nhiều lần."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT i.image_data, i.content_type, d.store_code
        FROM damaged_item_images i
        JOIN damaged_items d ON d.id = i.damaged_item_id
        WHERE i.id = %s
    ''', (image_id,))
    row = cursor.fetchone()
    cursor.close()

    if not row:
        return jsonify({'error': 'Không tìm thấy ảnh.'}), 404
    if role == 'store' and row['store_code'] != session['store_code']:
        return jsonify({'error': 'Forbidden'}), 403

    resp = send_file(
        io.BytesIO(bytes(row['image_data'])),
        mimetype=row['content_type'],
        max_age=31536000,
        conditional=True,
    )
    resp.headers['Cache-Control'] = 'private, max-age=31536000, immutable'
    return resp


@app.route('/api/damaged/<int:item_id>/images/upload', methods=['POST'])
@limiter.limit("30 per minute")
def upload_damaged_image(item_id):
    """Thêm 1 ảnh cho 1 lần báo hư hỏng đã có sẵn. Store chỉ thêm được cho
    lần báo của chính mình; Admin thêm được cho bất kỳ. Ảnh luôn được
    resize + nén sang WebP trước khi lưu (xem resize_damaged_image()) để
    tiết kiệm CSDL/băng thông."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    image_file = request.files.get('image')
    if not image_file:
        return jsonify({'error': 'Vui lòng chọn ảnh để tải lên.'}), 400

    db = get_db()
    cursor = db.cursor()
    store_code = _damaged_item_store_code(cursor, item_id)
    if store_code is None:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy lần báo hư hỏng.'}), 404
    if role == 'store' and store_code != session['store_code']:
        cursor.close()
        return jsonify({'error': 'Forbidden'}), 403

    cursor.execute('SELECT COUNT(*) AS c FROM damaged_item_images WHERE damaged_item_id = %s', (item_id,))
    current_count = cursor.fetchone()['c']
    if current_count >= MAX_DAMAGED_IMAGES_PER_ITEM:
        cursor.close()
        return jsonify({'error': f'Mỗi lần báo hư hỏng chỉ được tối đa {MAX_DAMAGED_IMAGES_PER_ITEM} ảnh.'}), 400

    try:
        image_bytes, content_type = resize_damaged_image(image_file)
    except ValueError as e:
        cursor.close()
        return jsonify({'error': str(e)}), 400

    now = vn_now()
    cursor.execute('''
        INSERT INTO damaged_item_images (damaged_item_id, content_type, image_data, byte_size, created_by, created_at)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    ''', (item_id, content_type, psycopg2.Binary(image_bytes), len(image_bytes), _current_actor_name(), now))
    new_id = cursor.fetchone()['id']
    db.commit()
    cursor.close()

    return jsonify({
        'success': True,
        'id': new_id,
        'url': url_for('get_damaged_image', image_id=new_id),
        'byte_size': len(image_bytes),
    })


@app.route('/api/damaged/image/delete', methods=['POST'])
def delete_damaged_image():
    """Xoá 1 ảnh đã đính kèm. Store chỉ xoá được ảnh của lần báo thuộc
    chính cửa hàng mình; Admin xoá được ảnh của bất kỳ lần báo nào."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    try:
        image_id = int(data.get('id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT d.store_code FROM damaged_item_images i
        JOIN damaged_items d ON d.id = i.damaged_item_id
        WHERE i.id = %s
    ''', (image_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy ảnh.'}), 404
    if role == 'store' and row['store_code'] != session['store_code']:
        cursor.close()
        return jsonify({'error': 'Forbidden'}), 403

    cursor.execute('DELETE FROM damaged_item_images WHERE id = %s', (image_id,))
    db.commit()
    cursor.close()
    return jsonify({'success': True})


@app.route('/api/version', methods=['GET'])
def get_version():
    """Trả về "chữ ký phiên bản" hiện tại của từng mảng dữ liệu (Dashboard,
    Lịch sử, Tồn kho, Danh sách user). Được frontend gọi định kỳ (poll) với
    tần suất thấp (nhẹ, chỉ vài giá trị) để phát hiện có thay đổi hay không,
    từ đó tự động tải lại đúng phần dữ liệu đã đổi mà KHÔNG cần F5 lại trang
    và không cần tải lại những phần chưa đổi."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    db = get_db()
    cursor = db.cursor()

    # TỐI ƯU EGRESS: trước đây phần này chạy ~10 câu SQL riêng lẻ (mỗi câu tốn
    # thêm phần "đầu mục" giao thức Postgres), mà trang gọi /api/version mỗi 20
    # giây trên MỌI tab đang mở -> cộng dồn thành lượng băng thông đáng kể. Giờ
    # gộp thành ĐÚNG 1 câu SQL (1 round-trip) trả về cùng các giá trị như cũ:
    #   - data_v    : Dashboard/Kết quả đối soát - đổi khi có upload mới (Danh
    #                 sách PO, Chi tiết nhận hàng, Chi tiết PO) ở bất kỳ cửa hàng.
    #   - history_v : Lịch sử tải lên (MAX id upload_log).
    #   - inv_v     : Tồn kho - GỘP mốc "Cập Nhật Tồn Kho" (inventory_meta) và
    #                 mốc đổi GIÁ BÁN gần nhất (part_prices); GREATEST bỏ qua NULL.
    #   - users_v   : hash gộp cả bảng users (thêm/xoá user, đổi mật khẩu).
    #   - tr_c/tr_v : Luân chuyển nội bộ - COUNT(*) + MAX(updated_at) để bắt cả
    #                 trường hợp phiếu bị XOÁ HẲN.
    #   - loc_v     : Vị trí kệ hàng (mốc lớn nhất toàn bảng).
    #   - dmg_c/v   : Hàng hư hỏng - COUNT(*) + MAX(created_at) (bắt cả xoá).
    #   - unread    : số thông báo CHƯA ĐỌC của cửa hàng/admin đang đăng nhập.
    cursor.execute('''
        SELECT
            GREATEST(
                COALESCE((SELECT MAX(ds_po_upload_time) FROM latest_uploads), 'epoch'::timestamp),
                COALESCE((SELECT MAX(receipt_upload_time) FROM latest_uploads), 'epoch'::timestamp),
                COALESCE((SELECT MAX(upload_time) FROM po_detail_items), 'epoch'::timestamp)
            ) AS data_v,
            (SELECT MAX(id) FROM upload_log) AS history_v,
            GREATEST(
                (SELECT upload_time FROM inventory_meta WHERE id = 1),
                (SELECT MAX(updated_at) FROM part_prices)
            ) AS inv_v,
            (SELECT MD5(COALESCE(string_agg(username || ':' || role || ':' || store_code || ':' || password, ',' ORDER BY username), ''))
               FROM users) AS users_v,
            (SELECT COUNT(*) FROM transfer_requests) AS tr_c,
            (SELECT MAX(updated_at) FROM transfer_requests) AS tr_v,
            (SELECT MAX(updated_at) FROM part_locations) AS loc_v,
            (SELECT COUNT(*) FROM damaged_items) AS dmg_c,
            (SELECT MAX(created_at) FROM damaged_items) AS dmg_v,
            (SELECT COUNT(*) FROM notifications WHERE store_code = %s AND is_read = FALSE) AS unread
    ''', (session.get('store_code'),))
    vrow = cursor.fetchone()

    data_version = vrow['data_v']
    history_version = vrow['history_v']
    inventory_version = vrow['inv_v']
    users_version = vrow['users_v']
    transfer_version = f"{vrow['tr_c']}:{vrow['tr_v']}"
    locations_version = vrow['loc_v']
    damaged_version = f"{vrow['dmg_c']}:{vrow['dmg_v']}"
    unread_notifications = vrow['unread'] if session['role'] in ('store', 'admin') else 0

    # Thông báo "ngày xe đi" (banner đỏ) đang hiệu lực - trả THẲNG luôn nội
    # dung (không chỉ 1 "chữ ký" version) vì danh sách này rất nhỏ, và vì
    # cần tự ẩn khi qua ngày MỚI dù không có gì thay đổi trong DB - trả
    # thẳng nội dung mỗi 20s giúp frontend luôn vẽ lại đúng theo ngày hiện
    # tại mà không cần thêm logic "phát hiện đổi version" riêng cho việc
    # này.
    truck_announcements = get_active_truck_announcements(cursor)

    cursor.close()

    return jsonify({
        'success': True,
        'data_version': str(data_version) if data_version else None,
        'history_version': history_version,
        'inventory_version': str(inventory_version) if inventory_version else None,
        'users_version': users_version,
        'transfer_version': transfer_version,
        'locations_version': str(locations_version) if locations_version else None,
        'damaged_version': damaged_version,
        'unread_notifications': unread_notifications,
        'truck_announcements': truck_announcements,
    })


@app.route('/api/data', methods=['GET'])
def get_data():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    role = session['role']
    store_code_session = session['store_code']
    target_store = request.args.get('store', store_code_session)

    db = get_db()
    cursor = db.cursor()

    if role == 'store':
        target_store = store_code_session

    # Dọn dẹp dữ liệu Chi tiết PO quá hạn trước khi tính toán
    cleanup_old_po_detail(cursor)
    db.commit()

    combined_data = []

    if target_store == 'ALL' and role == 'admin':
        cursor.execute('''
            SELECT DISTINCT store_code FROM (
                SELECT store_code FROM latest_uploads
                UNION
                SELECT store_code FROM po_detail_items
            ) AS stores
        ''')
        store_codes = [r['store_code'] for r in cursor.fetchall()]

        # Lấy version của TỪNG cửa hàng trong 1 lần truy vấn duy nhất, để
        # cache của cửa hàng A không bị coi là "cũ" chỉ vì cửa hàng B vừa
        # upload dữ liệu mới (xem chi tiết ở get_all_store_data_versions()).
        versions_by_store = get_all_store_data_versions(cursor)

        # Các cửa hàng dùng chung 1 "không gian xem hàng nợ" (NS2/NSM1) chỉ
        # được tính (và cộng vào tổng hợp toàn hệ thống) ĐÚNG 1 LẦN - nếu
        # không sẽ bị đếm trùng dữ liệu (cùng 1 bảng đối soát gộp bị cộng 2
        # lần vào summary). Version của cả nhóm = mốc mới nhất trong các cửa
        # hàng thành viên (nếu có dữ liệu).
        seen_groups = set()
        for sc in store_codes:
            group = get_debt_view_group(sc)
            if group in seen_groups:
                continue
            seen_groups.add(group)

            group_versions = [versions_by_store[m] for m in group if m in versions_by_store]
            sc_version = max(group_versions) if group_versions else 'epoch'

            items = compute_result_for_store_cached(cursor, sc, sc_version)
            label = '/'.join(group) if len(group) > 1 else sc
            for itm in items:
                itm['store_code'] = label
                combined_data.append(itm)
    else:
        version = get_store_data_version(cursor, target_store)
        items = compute_result_for_store_cached(cursor, target_store, version)
        for itm in items:
            itm['store_code'] = target_store
            combined_data.append(itm)

    cursor.close()

    summary = get_summary_from_data(combined_data)

    return jsonify({
        'success': True,
        'data': combined_data,
        'summary': summary
    })


@app.route('/api/history', methods=['GET'])
def get_history():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    store_code = session['store_code'] if session['role'] == 'store' else request.args.get('store', 'ALL')

    db = get_db()
    cursor = db.cursor()

    # Dọn các dòng lịch sử tải lên đã quá UPLOAD_LOG_RETENTION_DAYS (7) ngày
    # trước khi trả kết quả - cách đơn giản để không cần thêm job nền riêng.
    cleanup_old_upload_log(cursor)
    db.commit()

    if store_code == 'ALL':
        cursor.execute('SELECT id, store_code, upload_time, ds_po_filename, po_detail_filename, receipt_filename FROM upload_log ORDER BY upload_time DESC LIMIT 50')
    else:
        cursor.execute('SELECT id, store_code, upload_time, ds_po_filename, po_detail_filename, receipt_filename FROM upload_log WHERE store_code = %s ORDER BY upload_time DESC', (store_code,))

    history = [dict(row) for row in cursor.fetchall()]
    cursor.close()
    for h in history:
        h['upload_time'] = format_vi_datetime(h['upload_time'])
    return jsonify({'success': True, 'history': history})


@app.route('/api/admin/users/create', methods=['POST'])
def admin_users_create():
    """Admin tạo TÀI KHOẢN MỚI - chọn quyền 'admin' (xem được toàn bộ hệ
    thống, store_code luôn là 'ALL' như các tài khoản admin có sẵn) hoặc
    'store' (gắn với 1 mã cửa hàng cụ thể, VD NS1/NS6...). Nếu store_code
    nhập là 1 mã CHƯA từng tồn tại, tài khoản này sẽ trở thành tài khoản
    đầu tiên của cửa hàng đó (hệ thống không có danh sách cửa hàng cố định
    riêng - _valid_store_codes() lấy động từ chính bảng users)."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    role = (data.get('role') or '').strip()
    store_code = (data.get('store_code') or '').strip().upper()
    full_name = (data.get('full_name') or '').strip() or None
    branch = (data.get('branch') or '').strip() or None

    if not username or not password:
        return jsonify({'error': 'Vui lòng nhập tên đăng nhập và mật khẩu.'}), 400
    if len(password) < 4:
        return jsonify({'error': 'Mật khẩu phải có ít nhất 4 ký tự.'}), 400
    if role not in ('admin', 'store'):
        return jsonify({'error': 'Vui lòng chọn quyền hợp lệ (Admin hoặc Cửa hàng).'}), 400

    # Tài khoản 'admin' luôn có store_code = 'ALL' (giống mọi tài khoản
    # admin có sẵn) - KHÔNG dùng giá trị store_code người dùng gõ (nếu có)
    # để tránh tạo ra 1 admin bị giới hạn nhầm vào 1 cửa hàng.
    if role == 'admin':
        store_code = 'ALL'
    elif not store_code:
        return jsonify({'error': 'Vui lòng nhập mã cửa hàng cho tài khoản quyền Cửa hàng.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT 1 FROM users WHERE username = %s", (username,))
    if cursor.fetchone():
        cursor.close()
        return jsonify({'error': 'Tên đăng nhập đã tồn tại.'}), 400

    cursor.execute(
        "INSERT INTO users (username, password, role, store_code, full_name, branch) VALUES (%s, %s, %s, %s, %s, %s)",
        (username, generate_password_hash(password), role, store_code, full_name, branch)
    )
    db.commit()
    cursor.close()
    return jsonify({'success': True})


@app.route('/api/admin/users', methods=['GET', 'POST'])
def admin_users():
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    cursor = db.cursor()
    if request.method == 'POST':
        data = request.json or {}
        username = data.get('username')
        new_password = data.get('password')
        # full_name/branch: dùng sentinel `object()` để phân biệt "không gửi
        # trường này" (giữ nguyên giá trị cũ) với "gửi chuỗi rỗng" (xoá về
        # trống) - nếu chỉ check `if data.get('full_name')` thì không thể
        # xoá 1 giá trị đã điền trước đó về rỗng được.
        _MISSING = object()
        full_name = data.get('full_name', _MISSING)
        branch = data.get('branch', _MISSING)

        if not username:
            cursor.close()
            return jsonify({'error': 'Thiếu tên đăng nhập'}), 400

        # QUY TẮC: tài khoản admin GỐC (SUPER_ADMIN_USERNAME, xem hằng số ở
        # đầu file) được phép đổi mật khẩu của BẤT KỲ ai qua route này, kể cả
        # các tài khoản admin khác. Nhưng các tài khoản admin KHÁC (không
        # phải admin gốc) thì KHÔNG được đổi mật khẩu của 1 tài khoản admin
        # (dù là admin gốc hay 1 admin khác) - tức các admin thường không
        # được đổi mật khẩu LẪN NHAU, chỉ admin gốc mới có đặc quyền này.
        # Mật khẩu của chính admin gốc khi KHÔNG phải nó tự đổi vẫn luôn bị
        # chặn qua route này (chỉ tự đổi qua /api/change-password).
        if new_password:
            cursor.execute("SELECT role FROM users WHERE username = %s", (username,))
            target_user = cursor.fetchone()
            is_requester_super_admin = (session['user'] == SUPER_ADMIN_USERNAME)
            if target_user and target_user['role'] == 'admin' and not is_requester_super_admin:
                cursor.close()
                return jsonify({
                    'error': 'Không thể đổi mật khẩu của 1 tài khoản admin qua đây. '
                             'Tài khoản admin chỉ tự đổi được mật khẩu của chính mình '
                             'bằng cách đăng nhập vào tài khoản đó rồi dùng chức năng '
                             '"Đổi Mật Khẩu Của Tôi".'
                }), 403

        # Cho phép gọi API này để: chỉ đổi mật khẩu, chỉ sửa thông tin
        # (Họ và tên/Chi nhánh), hoặc cả hai cùng lúc - miễn có ít nhất 1
        # trong 3 thứ được gửi lên thì mới coi là hợp lệ.
        set_clauses = []
        params = []
        if new_password:
            set_clauses.append('password = %s')
            params.append(generate_password_hash(new_password))
        if full_name is not _MISSING:
            set_clauses.append('full_name = %s')
            params.append((full_name or '').strip() or None)
        if branch is not _MISSING:
            set_clauses.append('branch = %s')
            params.append((branch or '').strip() or None)

        if not set_clauses:
            cursor.close()
            return jsonify({'error': 'Thiếu thông tin'}), 400

        params.append(username)
        cursor.execute(f"UPDATE users SET {', '.join(set_clauses)} WHERE username = %s", params)
        if cursor.rowcount == 0:
            db.rollback()
            cursor.close()
            return jsonify({'error': 'Không tìm thấy tài khoản.'}), 404
        db.commit()
        cursor.close()
        return jsonify({'success': True})

    cursor.execute("SELECT username, role, store_code, full_name, branch FROM users")
    users = [dict(row) for row in cursor.fetchall()]

    # Lấy lượt đăng nhập GẦN NHẤT của mỗi user (DISTINCT ON theo username,
    # sắp theo login_time mới nhất) trong 1 truy vấn duy nhất, ghép vào kết
    # quả trên thay vì query riêng cho từng user (tránh N+1).
    cursor.execute('''
        SELECT DISTINCT ON (username) username, ip_address, login_time, city, region, country
        FROM login_log
        ORDER BY username, login_time DESC
    ''')
    last_login_by_user = {r['username']: r for r in cursor.fetchall()}
    store_ips = get_store_known_ips_map(cursor)
    cursor.close()

    with _online_users_lock:
        now = vn_now()
        for u in users:
            last = last_login_by_user.get(u['username'])
            u['last_login_time'] = last['login_time'].isoformat() if last else None
            u['last_login_ip'] = last['ip_address'] if last else None
            u['last_login_location'] = ', '.join(filter(None, [last['city'], last['region']])) if last else None

            known_ips = store_ips.get(u['store_code'])
            u['is_store_ip'] = (last['ip_address'] in known_ips) if (last and known_ips) else None

            online_info = _online_users.get(u['username'])
            u['online'] = bool(
                online_info and (now - online_info['last_seen']).total_seconds() <= ONLINE_THRESHOLD_SECONDS
            )
            u['current_ip'] = online_info['ip'] if online_info else None

    return jsonify({'success': True, 'users': users})


def get_store_known_ips_map(cursor):
    """Trả về dict store_code -> set(ip_address) đã được admin đăng ký là
    'IP của cửa hàng này'. Dùng để so khớp CHÍNH XÁC (không suy đoán theo
    thành phố như city/region) xem 1 lượt đăng nhập có đúng từ mạng cửa
    hàng hay không."""
    cursor.execute("SELECT store_code, ip_address FROM store_known_ips")
    result = {}
    for row in cursor.fetchall():
        result.setdefault(row['store_code'], set()).add(row['ip_address'])
    return result


@app.route('/api/admin/login-log', methods=['GET'])
def admin_login_log():
    """Lịch sử đăng nhập chi tiết (nhiều lượt) của 1 user cụ thể, hoặc toàn
    bộ hệ thống nếu không truyền ?username=. Giới hạn 200 dòng gần nhất để
    tránh trả về quá nhiều dữ liệu nếu 1 tài khoản đăng nhập rất thường
    xuyên trong thời gian dài."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    username = request.args.get('username')
    db = get_db()
    cursor = db.cursor()
    if username:
        cursor.execute('''
            SELECT l.username, l.ip_address, l.user_agent, l.login_time, l.city, l.region, l.country,
                   u.store_code
            FROM login_log l LEFT JOIN users u ON u.username = l.username
            WHERE l.username = %s
            ORDER BY l.login_time DESC LIMIT 200
        ''', (username,))
    else:
        cursor.execute('''
            SELECT l.username, l.ip_address, l.user_agent, l.login_time, l.city, l.region, l.country,
                   u.store_code
            FROM login_log l LEFT JOIN users u ON u.username = l.username
            ORDER BY l.login_time DESC LIMIT 200
        ''')
    logs = [dict(row) for row in cursor.fetchall()]

    store_ips = get_store_known_ips_map(cursor)
    cursor.close()

    for row in logs:
        row['login_time'] = row['login_time'].isoformat()
        # city/region có thể vẫn NULL nếu thread tra cứu vị trí chưa xong
        # (hoặc lượt đăng nhập rất mới, vừa xảy ra vài giây trước) hoặc IP
        # nội bộ (được gán sẵn 'Mạng nội bộ' ngay khi ghi log).
        row['location'] = ', '.join(filter(None, [row.get('city'), row.get('region')])) or None

        # is_store_ip: True/False nếu cửa hàng NÀY đã đăng ký ít nhất 1 IP,
        # None nếu chưa đăng ký IP nào (chưa có dữ liệu để so sánh, không
        # phải "sai") - để giao diện phân biệt rõ 3 trạng thái này.
        known_ips = store_ips.get(row['store_code'])
        row['is_store_ip'] = (row['ip_address'] in known_ips) if known_ips else None
    return jsonify({'success': True, 'logs': logs})


@app.route('/api/admin/store-ips', methods=['GET', 'POST'])
def admin_store_ips():
    """Quản lý danh sách 'IP đã biết' của từng cửa hàng.
    GET: liệt kê (lọc theo ?store_code= nếu có).
    POST: đăng ký 1 IP mới cho 1 cửa hàng - body {store_code, ip_address, label}.
    Dùng ON CONFLICT DO NOTHING vì (store_code, ip_address) là UNIQUE - đăng
    ký trùng sẽ không báo lỗi, chỉ đơn giản là không thêm dòng mới."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        data = request.json or {}
        store_code = (data.get('store_code') or '').strip()
        ip_address = (data.get('ip_address') or '').strip()
        label = (data.get('label') or '').strip() or None
        if not store_code or not ip_address:
            cursor.close()
            return jsonify({'error': 'Thiếu store_code hoặc ip_address'}), 400

        cursor.execute('''
            INSERT INTO store_known_ips (store_code, ip_address, label, added_by, added_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (store_code, ip_address) DO NOTHING
        ''', (store_code, ip_address, label, session['user'], vn_now()))
        db.commit()
        cursor.close()
        return jsonify({'success': True})

    store_code = request.args.get('store_code')
    if store_code:
        cursor.execute(
            "SELECT id, store_code, ip_address, label, added_by, added_at FROM store_known_ips WHERE store_code = %s ORDER BY added_at DESC",
            (store_code,)
        )
    else:
        cursor.execute(
            "SELECT id, store_code, ip_address, label, added_by, added_at FROM store_known_ips ORDER BY store_code, added_at DESC"
        )
    rows = [dict(r) for r in cursor.fetchall()]
    cursor.close()
    for r in rows:
        r['added_at'] = r['added_at'].isoformat()
    return jsonify({'success': True, 'ips': rows})


@app.route('/api/admin/store-ips/<int:ip_id>', methods=['DELETE'])
def admin_store_ips_delete(ip_id):
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM store_known_ips WHERE id = %s", (ip_id,))
    db.commit()
    cursor.close()
    return jsonify({'success': True})




@app.route('/api/admin/db-size', methods=['GET'])
def admin_db_size():
    """Trả về dung lượng tổng của database và dung lượng từng bảng chính,
    để admin theo dõi mức sử dụng so với hạn mức 500MB của gói Free
    Supabase ngay trên giao diện, không cần vào SQL Editor."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT pg_database_size(current_database()) AS bytes")
    total_bytes = cursor.fetchone()['bytes']

    cursor.execute('''
        SELECT
            relname AS table_name,
            pg_total_relation_size(relid) AS bytes,
            n_live_tup AS row_count
        FROM pg_stat_user_tables
        ORDER BY pg_total_relation_size(relid) DESC
    ''')
    tables = [dict(row) for row in cursor.fetchall()]
    cursor.close()

    # Hạn mức gói Free của Supabase (500 MB) - đổi số này nếu bạn nâng gói.
    LIMIT_BYTES = 500 * 1024 * 1024

    return jsonify({
        'success': True,
        'total_bytes': total_bytes,
        'limit_bytes': LIMIT_BYTES,
        'percent_used': round(total_bytes / LIMIT_BYTES * 100, 2),
        'tables': tables
    })


@app.route('/api/admin/transfer/stats', methods=['GET'])
def admin_transfer_stats():
    """Thống kê số phiếu chuyển kho theo 1 ngày bất kỳ (mặc định hôm nay -
    theo giờ VN), kèm dữ liệu cho 2 biểu đồ:
      - week: 7 ngày (Thứ Hai -> Chủ Nhật) của TUẦN chứa ngày được chọn.
      - month: từng ngày trong THÁNG chứa ngày được chọn.
    Đếm theo created_at (ngày TẠO phiếu) để nhất quán với cách transfer_list
    sắp xếp/lọc lịch sử ở trên. Chỉ tính phiếu còn trong transfer_requests -
    phiếu đã bị lưu trữ (archive, xem run_transfer_archive_job) sẽ không
    được tính, nhưng do chỉ lưu trữ phiếu đã xong việc và cũ hơn 365 ngày,
    thống kê theo ngày/tuần/tháng gần đây gần như không bao giờ bị ảnh
    hưởng bởi việc này."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    date_str = request.args.get('date')
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else vn_now().date()
    except ValueError:
        return jsonify({'error': 'Ngày không hợp lệ. Định dạng đúng: YYYY-MM-DD.'}), 400

    db = get_db()
    cursor = db.cursor()

    # ----- 1. Thống kê NGÀY được chọn: tổng số phiếu + chi tiết theo trạng
    # thái + tổng số dòng mã hàng bên trong các phiếu đó. -----
    cursor.execute('''
        SELECT status, COUNT(*) AS c
        FROM transfer_requests
        WHERE created_at::date = %s
        GROUP BY status
    ''', (target_date,))
    by_status = {r['status']: r['c'] for r in cursor.fetchall()}
    day_total = sum(by_status.values())

    cursor.execute('''
        SELECT COUNT(*) AS c
        FROM transfer_items ti
        JOIN transfer_requests tr ON tr.id = ti.request_id
        WHERE tr.created_at::date = %s
    ''', (target_date,))
    day_item_count = cursor.fetchone()['c']

    # Danh sách phiếu trong ngày (rút gọn) để admin xem nhanh chi tiết bên
    # dưới biểu đồ mà không cần lọc lại ở tab Luân Chuyển Nội Bộ.
    cursor.execute('''
        SELECT id, from_store, to_store, status, created_by, created_employee, created_at
        FROM transfer_requests
        WHERE created_at::date = %s
        ORDER BY created_at DESC
    ''', (target_date,))
    day_requests = [{
        'id': r['id'],
        'from_store': r['from_store'],
        'to_store': r['to_store'],
        'status': r['status'],
        'created_by': r['created_by'],
        'created_employee': r.get('created_employee'),
        'created_at': format_vi_datetime(r['created_at']),
    } for r in cursor.fetchall()]

    # ----- 2. Biểu đồ TUẦN: Thứ Hai -> Chủ Nhật của tuần chứa target_date -----
    week_start = target_date - timedelta(days=target_date.weekday())  # Thứ Hai
    week_end = week_start + timedelta(days=6)  # Chủ Nhật
    cursor.execute('''
        SELECT gs::date AS d, COUNT(tr.id) AS c
        FROM generate_series(%s::date, %s::date, interval '1 day') gs
        LEFT JOIN transfer_requests tr ON tr.created_at::date = gs::date
        GROUP BY gs
        ORDER BY gs
    ''', (week_start, week_end))
    week_rows = cursor.fetchall()
    week_labels = [f"{_WEEKDAY_VI[r['d'].weekday()]} {r['d'].strftime('%d/%m')}" for r in week_rows]
    week_data = [r['c'] for r in week_rows]

    # ----- 3. Biểu đồ THÁNG: từng ngày trong tháng chứa target_date -----
    month_start = target_date.replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    cursor.execute('''
        SELECT gs::date AS d, COUNT(tr.id) AS c
        FROM generate_series(%s::date, %s::date, interval '1 day') gs
        LEFT JOIN transfer_requests tr ON tr.created_at::date = gs::date
        GROUP BY gs
        ORDER BY gs
    ''', (month_start, month_end))
    month_rows = cursor.fetchall()
    month_labels = [str(r['d'].day) for r in month_rows]
    month_data = [r['c'] for r in month_rows]

    cursor.close()

    return jsonify({
        'success': True,
        'date': target_date.isoformat(),
        'day': {
            'total': day_total,
            'pending': by_status.get('pending', 0),
            'approved': by_status.get('approved', 0),
            'rejected': by_status.get('rejected', 0),
            'item_count': day_item_count,
            'requests': day_requests,
        },
        'week': {
            'start_date': week_start.isoformat(),
            'end_date': week_end.isoformat(),
            'labels': week_labels,
            'data': week_data,
            'total': sum(week_data),
        },
        'month': {
            'month': target_date.month,
            'year': target_date.year,
            'label': f"Tháng {target_date.month}/{target_date.year}",
            'labels': month_labels,
            'data': month_data,
            'total': sum(month_data),
        },
    })


# Các giá trị "trạng thái phiếu" hợp lệ cho bộ lọc báo cáo khu vực - tách
# riêng "Đã Đồng Ý" thành 4 lát cắt rõ ràng theo 2 chiều ĐỘC LẬP nhau (soạn
# hàng / nhận hàng) để admin lọc đúng thứ cần xem, thay vì chỉ có 1 mức
# "approved" chung chung. Khớp với nhãn hiển thị REGION_REPORT_STATUS_LABELS
# bên dưới - sửa ở đây thì nhớ sửa cả nhãn.
REGION_REPORT_STATUS_CONDITIONS = {
    'pending': "tr.status = 'pending'",
    # Trước đây có 4 điều kiện tách rời (chưa/đã soạn hàng, chưa/đã nhận đủ
    # hàng) - nhưng đó là 2 CHIỀU khác nhau của cùng 1 phiếu "Đã Đồng Ý", nên
    # khi người dùng tick nhiều ô OR với nhau, tick đủ cả "Chưa Soạn Hàng" +
    # "Đã Soạn Hàng" (2 ô) đã tự động khớp 100% phiếu đã đồng ý (vì 1 phiếu
    # luôn là 1 trong 2), khiến ô "Chưa Nhận Đủ Hàng" tick thêm không còn tác
    # dụng lọc bớt gì cả -> xuất ra thừa cả các phiếu "Đã Nhận Đủ Hàng".
    # Sửa: gộp thành 4 tổ hợp loại trừ lẫn nhau (soạn x nhận), y hệt 4 nhãn
    # "Trạng Thái Phiếu" xuất hiện trong file Excel (_phieu_status_label bên
    # dưới) - mỗi phiếu chỉ khớp ĐÚNG 1 trong 4 tổ hợp này, nên OR nhiều ô mới
    # thực sự thu hẹp kết quả đúng như người dùng tick.
    'approved_np_nr': (  # Đã Đồng Ý - Chưa Soạn Hàng - Chưa Nhận Đủ Hàng
        "tr.status = 'approved' AND tr.prepared = FALSE AND EXISTS ("
        "SELECT 1 FROM transfer_items x WHERE x.request_id = tr.id AND x.received = FALSE)"
    ),
    'approved_np_r': (  # Đã Đồng Ý - Chưa Soạn Hàng - Đã Nhận Đủ Hàng
        "tr.status = 'approved' AND tr.prepared = FALSE"
        " AND EXISTS (SELECT 1 FROM transfer_items x WHERE x.request_id = tr.id)"
        " AND NOT EXISTS (SELECT 1 FROM transfer_items x WHERE x.request_id = tr.id AND x.received = FALSE)"
    ),
    'approved_p_nr': (  # Đã Đồng Ý - Đã Soạn Hàng - Chưa Nhận Đủ Hàng
        "tr.status = 'approved' AND tr.prepared = TRUE AND EXISTS ("
        "SELECT 1 FROM transfer_items x WHERE x.request_id = tr.id AND x.received = FALSE)"
    ),
    'approved_p_r': (  # Đã Đồng Ý - Đã Soạn Hàng - Đã Nhận Đủ Hàng
        "tr.status = 'approved' AND tr.prepared = TRUE"
        " AND EXISTS (SELECT 1 FROM transfer_items x WHERE x.request_id = tr.id)"
        " AND NOT EXISTS (SELECT 1 FROM transfer_items x WHERE x.request_id = tr.id AND x.received = FALSE)"
    ),
    'rejected': "tr.status = 'rejected'",
    'cancelled': "tr.status = 'cancelled'",
}

# Nhãn tiếng Việt hiển thị cho từng trạng thái lọc ở trên - dùng chung cho
# cả báo cáo (breakdown card) lẫn cột "Trạng Thái Phiếu" khi xuất Excel.
REGION_REPORT_STATUS_LABELS = {
    'pending': 'Chờ Xử Lý',
    'approved_np_nr': 'Đã Đồng Ý - Chưa Soạn Hàng - Chưa Nhận Đủ Hàng',
    'approved_np_r': 'Đã Đồng Ý - Chưa Soạn Hàng - Đã Nhận Đủ Hàng',
    'approved_p_nr': 'Đã Đồng Ý - Đã Soạn Hàng - Chưa Nhận Đủ Hàng',
    'approved_p_r': 'Đã Đồng Ý - Đã Soạn Hàng - Đã Nhận Đủ Hàng',
    'rejected': 'Đã Từ Chối',
    'cancelled': 'Đã Huỷ',
}


def _parse_region_report_period(args):
    """Đọc period + ngày/tuần/tháng tương ứng từ query string, trả về
    (start_date, end_date, error_response_hoặc_None). Dùng chung cho cả báo
    cáo khu vực lẫn API xuất Excel chi tiết bên dưới, để 2 nơi luôn hiểu
    "khoảng thời gian đang chọn" giống hệt nhau."""
    period = (args.get('period') or 'month').strip()
    today = vn_now().date()
    try:
        if period == 'day':
            date_str = args.get('date')
            target = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else today
            return target, target, None
        elif period == 'range':
            from_str = args.get('date_from')
            to_str = args.get('date_to')
            if not from_str or not to_str:
                return None, None, (jsonify({'error': 'Vui lòng chọn đủ Từ ngày và Đến ngày.'}), 400)
            start_date = datetime.strptime(from_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(to_str, '%Y-%m-%d').date()
            if start_date > end_date:
                start_date, end_date = end_date, start_date
            return start_date, end_date, None
        elif period == 'week':
            week_str = args.get('week')  # định dạng input type=week: 'YYYY-Www'
            if not week_str or 'W' not in week_str:
                return None, None, (jsonify({'error': 'Vui lòng chọn tuần.'}), 400)
            year_str, wk_str = week_str.split('-W')
            start_date = datetime.strptime(f'{int(year_str)}-W{int(wk_str):02d}-1', '%G-W%V-%u').date()
            end_date = start_date + timedelta(days=6)
            return start_date, end_date, None
        elif period == 'month':
            month_str = args.get('month')  # 'YYYY-MM'
            if month_str:
                y_str, m_str = month_str.split('-')
                start_date = date(int(y_str), int(m_str), 1)
            else:
                start_date = today.replace(day=1)
            next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
            end_date = next_month - timedelta(days=1)
            return start_date, end_date, None
        else:
            return None, None, (jsonify({'error': 'Khoảng thời gian không hợp lệ.'}), 400)
    except (ValueError, TypeError):
        return None, None, (jsonify({'error': 'Ngày/tuần/tháng không hợp lệ.'}), 400)


def _parse_region_report_status_filter(args):
    """Đọc danh sách trạng thái đã chọn (multi-select) từ query string -
    chấp nhận nhiều tham số status=... lặp lại HOẶC 1 tham số duy nhất
    phân tách bằng dấu phẩy (status=a,b,c) - trả về (list_trạng_thái_hợp_lệ,
    error_response_hoặc_None). Danh sách rỗng nghĩa là "không lọc" (lấy mọi
    trạng thái), giữ đúng hành vi cũ khi không truyền status."""
    raw_values = []
    for v in args.getlist('status'):
        raw_values.extend([p.strip() for p in v.split(',') if p.strip()])
    seen = set()
    values = []
    for v in raw_values:
        if v not in seen:
            seen.add(v)
            values.append(v)
    invalid = [v for v in values if v not in REGION_REPORT_STATUS_CONDITIONS]
    if invalid:
        return None, (jsonify({'error': f'Trạng thái không hợp lệ: {", ".join(invalid)}'}), 400)
    return values, None


def _region_report_status_sql(status_values):
    """Ghép điều kiện SQL cho danh sách trạng thái đã chọn (OR với nhau,
    bọc trong ngoặc) - trả về chuỗi rỗng nếu không lọc gì (danh sách rỗng)."""
    if not status_values:
        return ''
    conditions = [REGION_REPORT_STATUS_CONDITIONS[v] for v in status_values]
    return ' AND (' + ' OR '.join(conditions) + ')'


@app.route('/api/admin/transfer/region-report', methods=['GET'])
def admin_transfer_region_report():
    """Báo cáo luân chuyển nội bộ GỘP THEO KHU VỰC (xem STORE_REGIONS) - cho
    admin xem theo 1 trong 4 kiểu khoảng thời gian (ngày / khoảng ngày /
    tuần / tháng), lọc thêm theo trạng thái phiếu chi tiết - CHO PHÉP CHỌN
    NHIỀU trạng thái cùng lúc (xem _parse_region_report_status_filter), và
    trả về:
      - Tổng số phiếu + tổng số dòng mã hàng trong khoảng/trạng thái đã lọc.
      - "status_counts": số phiếu theo TỪNG trạng thái chi tiết, LUÔN tính
        trên toàn bộ khoảng thời gian (không bị ảnh hưởng bởi bộ lọc trạng
        thái) để card tổng quan luôn hiển thị đủ bức tranh, dù đang lọc
        riêng vài trạng thái để xem bảng luồng khu vực bên dưới.
      - Danh sách "luồng" khu vực đi -> khu vực đến (có áp dụng bộ lọc
        trạng thái), mỗi luồng kèm số phiếu và số dòng mã hàng.
    Đếm theo created_at (ngày TẠO phiếu), nhất quán với admin_transfer_stats
    và transfer_list() ở trên."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    status_values, err = _parse_region_report_status_filter(request.args)
    if err:
        return err

    start_date, end_date, err = _parse_region_report_period(request.args)
    if err:
        return err

    range_start = datetime.combine(start_date, datetime.min.time())
    range_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    db = get_db()
    cursor = db.cursor()

    # 1) Toàn bộ phiếu trong khoảng thời gian (KHÔNG áp dụng bộ lọc trạng
    # thái) kèm prepared + số mã hàng đã nhận / tổng số mã hàng - phục vụ
    # tính "status_counts" đầy đủ mọi trạng thái cho card tổng quan.
    cursor.execute('''
        SELECT tr.id, tr.status, tr.prepared,
               COUNT(ti.id) AS item_count,
               COUNT(ti.id) FILTER (WHERE ti.received) AS received_item_count
        FROM transfer_requests tr
        LEFT JOIN transfer_items ti ON ti.request_id = tr.id
        WHERE tr.created_at >= %s AND tr.created_at < %s
        GROUP BY tr.id, tr.status, tr.prepared
    ''', (range_start, range_end))
    all_rows = cursor.fetchall()

    # status_counts dùng cho card tổng quan - đây là bản HIỂN THỊ theo 2
    # CHIỀU riêng (chưa/đã soạn hàng, chưa/đã nhận đủ hàng) để dễ đọc tổng
    # quan tình hình chung, CỐ TÌNH tách biệt với REGION_REPORT_STATUS_CONDITIONS
    # (bộ lọc thực tế) ở trên - 2 khái niệm khác nhau: ở đây chỉ để ĐẾM hiển
    # thị (1 phiếu có thể được cộng vào cả 2 pill khác trục, không dùng để
    # lọc), còn REGION_REPORT_STATUS_CONDITIONS dùng 4 tổ hợp loại trừ lẫn
    # nhau để lọc chính xác.
    status_counts = {
        'pending': 0, 'rejected': 0, 'cancelled': 0,
        'approved_not_prepared': 0, 'approved_prepared': 0, 'approved_total': 0,
        'approved_not_received': 0, 'approved_received': 0,
    }
    for r in all_rows:
        if r['status'] == 'pending':
            status_counts['pending'] += 1
        elif r['status'] == 'rejected':
            status_counts['rejected'] += 1
        elif r['status'] == 'cancelled':
            status_counts['cancelled'] += 1
        elif r['status'] == 'approved':
            status_counts['approved_prepared' if r['prepared'] else 'approved_not_prepared'] += 1
            all_received = r['item_count'] > 0 and r['received_item_count'] == r['item_count']
            status_counts['approved_received' if all_received else 'approved_not_received'] += 1
    status_counts['approved_total'] = status_counts['approved_prepared'] + status_counts['approved_not_prepared']

    # 2) Danh sách phiếu CÓ áp dụng bộ lọc trạng thái (nếu có chọn) - phục
    # vụ tổng số phiếu/mã hàng và bảng luồng khu vực.
    status_sql = _region_report_status_sql(status_values)
    cursor.execute(f'''
        SELECT tr.id, tr.from_store, tr.to_store, COUNT(ti.id) AS item_count
        FROM transfer_requests tr
        LEFT JOIN transfer_items ti ON ti.request_id = tr.id
        WHERE tr.created_at >= %s AND tr.created_at < %s{status_sql}
        GROUP BY tr.id, tr.from_store, tr.to_store
    ''', (range_start, range_end))
    filtered_rows = cursor.fetchall()
    cursor.close()

    flows = {}
    total_items = 0
    for r in filtered_rows:
        total_items += r['item_count']
        from_region = _store_region(r['from_store'])
        to_region = _store_region(r['to_store'])
        key = (from_region, to_region)
        flow = flows.setdefault(key, {
            'from_region': from_region, 'to_region': to_region, 'requests': 0, 'items': 0,
        })
        flow['requests'] += 1
        flow['items'] += r['item_count']

    flow_list = sorted(flows.values(), key=lambda f: f['requests'], reverse=True)

    return jsonify({
        'success': True,
        'period': request.args.get('period') or 'month',
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'status_filter': status_values,
        'total_requests': len(filtered_rows),
        'total_items': total_items,
        'status_counts': status_counts,
        'status_labels': REGION_REPORT_STATUS_LABELS,
        'flows': flow_list,
        'store_regions': STORE_REGIONS,
    })


@app.route('/api/admin/transfer/region-report/export', methods=['GET'])
def admin_transfer_region_report_export():
    """Xuất Excel CHI TIẾT từng mã hàng của các phiếu khớp đúng bộ lọc đang
    chọn ở báo cáo khu vực (khoảng thời gian + trạng thái phiếu, CHO PHÉP
    CHỌN NHIỀU trạng thái cùng lúc) - mỗi dòng Excel là 1 mã hàng, kèm đầy
    đủ thông tin phiếu chứa nó: ngày xin, khu vực xin, cửa hàng xin/cho,
    người tạo... Dùng CHUNG bộ lọc với admin_transfer_region_report() ở
    trên để "xuất đúng những gì đang xem"."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    status_values, err = _parse_region_report_status_filter(request.args)
    if err:
        return err

    start_date, end_date, err = _parse_region_report_period(request.args)
    if err:
        return err

    range_start = datetime.combine(start_date, datetime.min.time())
    range_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    status_sql = _region_report_status_sql(status_values)

    db = get_db()
    cursor = db.cursor()
    cursor.execute(f'''
        SELECT tr.id, tr.from_store, tr.to_store, tr.status, tr.prepared,
               tr.created_at, tr.created_by, tr.created_employee,
               ti.part_code, ti.part_name, ti.quantity, ti.approved_quantity, ti.received
        FROM transfer_requests tr
        LEFT JOIN transfer_items ti ON ti.request_id = tr.id
        WHERE tr.created_at >= %s AND tr.created_at < %s{status_sql}
        ORDER BY tr.created_at DESC, tr.id, ti.id
    ''', (range_start, range_end))
    rows = cursor.fetchall()

    # Cần biết "đã nhận đủ hàng" hay chưa cho TỪNG phiếu (không chỉ từng
    # dòng) để ghép vào nhãn trạng thái - gom theo request_id từ chính các
    # dòng vừa lấy (khỏi truy vấn thêm lần nữa).
    by_request = {}
    for r in rows:
        by_request.setdefault(r['id'], []).append(r)
    all_received_by_request = {}
    for rid, items in by_request.items():
        real_items = [it for it in items if it['part_code'] is not None]
        all_received_by_request[rid] = bool(real_items) and all(it['received'] for it in real_items)
    cursor.close()

    def _phieu_status_label(r):
        if r['status'] == 'pending':
            return REGION_REPORT_STATUS_LABELS['pending']
        if r['status'] == 'rejected':
            return REGION_REPORT_STATUS_LABELS['rejected']
        if r['status'] == 'cancelled':
            return REGION_REPORT_STATUS_LABELS['cancelled']
        # approved: ghép cả 2 chiều (soạn hàng / nhận hàng) vào 1 nhãn duy
        # nhất cho dễ đọc trên Excel, thay vì phải xem thêm cột khác.
        soan = 'Đã Soạn Hàng' if r['prepared'] else 'Chưa Soạn Hàng'
        nhan = 'Đã Nhận Đủ Hàng' if all_received_by_request.get(r['id']) else 'Chưa Nhận Đủ Hàng'
        return f'Đã Đồng Ý - {soan} - {nhan}'

    out_rows = []
    for r in rows:
        out_rows.append({
            'Mã Phiếu': r['id'],
            'Ngày Xin': format_vi_datetime(r['created_at']),
            'Khu Vực Xin': _store_region(r['from_store']),
            'Cửa Hàng Xin': r['from_store'],
            'Cửa Hàng Cho': r['to_store'],
            'Người Tạo': r.get('created_employee') or r.get('created_by') or '',
            'Mã Hàng': r['part_code'] or '',
            'Tên Hàng': r['part_name'] or '',
            'Số Lượng Xin': r['quantity'],
            'Số Lượng Được Cho': r['approved_quantity'],
            'Trạng Thái Phiếu': _phieu_status_label(r),
        })

    if not out_rows:
        out_rows.append({
            'Mã Phiếu': '', 'Ngày Xin': '', 'Khu Vực Xin': '', 'Cửa Hàng Xin': '', 'Cửa Hàng Cho': '',
            'Người Tạo': '', 'Mã Hàng': '', 'Tên Hàng': '', 'Số Lượng Xin': '', 'Số Lượng Được Cho': '',
            'Trạng Thái Phiếu': 'Không có phiếu nào khớp bộ lọc đã chọn.',
        })

    df = pd.DataFrame(out_rows)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Chi Tiet Phieu', index=False)
        # Tự co giãn độ rộng cột theo nội dung dài nhất (kể cả tiêu đề) cho
        # dễ đọc ngay khi mở file, không cần người dùng tự kéo tay.
        ws = writer.sheets['Chi Tiet Phieu']
        for col_idx, col_name in enumerate(df.columns, start=1):
            max_len = max([len(str(col_name))] + [len(str(v)) for v in df[col_name].tolist()])
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max_len + 3, 45)
    buffer.seek(0)

    filename = f"bao_cao_luan_chuyen_{start_date.isoformat()}_{end_date.isoformat()}.xlsx"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@app.route('/api/admin/transfer/archives', methods=['GET'])
def admin_transfer_archives():
    """Danh sách các file Excel đã xuất khi job dọn dẹp tự động (xem
    run_transfer_archive_job) xoá phiếu chuyển kho quá 1 năm khỏi
    transfer_requests - admin tải lại từng file qua route download bên
    dưới nếu cần tra cứu lại dữ liệu cũ."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id, archived_at, cutoff_date, request_count, item_count, filename,
               octet_length(file_data) AS file_size
        FROM transfer_archives
        ORDER BY archived_at DESC
    ''')
    rows = cursor.fetchall()
    cursor.close()

    archives = []
    for r in rows:
        d = dict(r)
        d['archived_at'] = format_vi_datetime(d['archived_at']) if d.get('archived_at') else None
        d['cutoff_date'] = format_vi_datetime(d['cutoff_date']) if d.get('cutoff_date') else None
        archives.append(d)

    return jsonify({'success': True, 'archives': archives})


@app.route('/api/admin/transfer/archives/<int:archive_id>/download', methods=['GET'])
def admin_transfer_archive_download(archive_id):
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT filename, file_data FROM transfer_archives WHERE id = %s', (archive_id,))
    row = cursor.fetchone()
    cursor.close()
    if not row:
        return jsonify({'error': 'Không tìm thấy file lưu trữ này.'}), 404

    return send_file(
        io.BytesIO(bytes(row['file_data'])),
        as_attachment=True,
        download_name=row['filename'],
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@app.route('/api/admin/transfer/archive-now', methods=['POST'])
def admin_transfer_archive_now():
    """Cho phép admin CHỦ ĐỘNG bấm lưu trữ ngay (khác với job tự động
    run_transfer_archive_job chạy định kỳ theo tuổi phiếu - xem trên). Điều
    kiện lưu trữ ở đây KHÁC: chỉ lấy phiếu đã ĐỒNG Ý (approved) và đã nhận
    ĐỦ hàng (mọi mã hàng trong phiếu đều received = TRUE), bất kể phiếu đó
    mới hay cũ - không xét theo _TRANSFER_ARCHIVE_AGE_DAYS. Phiếu "pending",
    "rejected", hoặc "approved" nhưng còn mã hàng chưa nhận thì GIỮ NGUYÊN,
    không bị đụng tới."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute('''
            SELECT * FROM transfer_requests
            WHERE status = 'approved'
              AND EXISTS (
                  SELECT 1 FROM transfer_items ti WHERE ti.request_id = transfer_requests.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM transfer_items ti
                  WHERE ti.request_id = transfer_requests.id AND ti.received = FALSE
              )
            ORDER BY id
        ''')
        rows = cursor.fetchall()

        if not rows:
            return jsonify({
                'success': True,
                'request_count': 0,
                'message': 'Không có phiếu nào đủ điều kiện lưu trữ (đã đồng ý và đã nhận đủ hàng).',
            })

        items_by_request = _fetch_transfer_items(cursor, [r['id'] for r in rows])
        item_count = sum(len(v) for v in items_by_request.values())
        file_bytes = _build_transfer_archive_excel(
            [_serialize_transfer_row(r, items_by_request.get(r['id'], []), None, 'admin') for r in rows],
            items_by_request,
        )
        filename = f"luu_tru_thu_cong_phieu_chuyen_kho_{datetime.now():%Y%m%d_%H%M%S}.xlsx"

        cursor.execute('''
            INSERT INTO transfer_archives (cutoff_date, request_count, item_count, filename, file_data)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        ''', (datetime.now(), len(rows), item_count, filename, psycopg2.Binary(file_bytes)))
        archive_id = cursor.fetchone()['id']

        cursor.execute('DELETE FROM transfer_requests WHERE id = ANY(%s)', ([r['id'] for r in rows],))
        db.commit()

        return jsonify({
            'success': True,
            'archive_id': archive_id,
            'request_count': len(rows),
            'item_count': item_count,
            'filename': filename,
        })
    except Exception:
        db.rollback()
        traceback.print_exc()
        return jsonify({'error': 'Có lỗi xảy ra khi lưu trữ.'}), 500
    finally:
        cursor.close()


@app.route('/api/admin/transfer/remind', methods=['POST'])
def admin_transfer_remind():
    """Admin chọn 1 hoặc nhiều phiếu luân chuyển ĐANG DANG DỞ để nhắc CẢ HAI
    cửa hàng liên quan (cửa hàng CHO - to_store, và cửa hàng XIN - from_store)
    sớm hoàn thành phần việc của mình, đẩy thẳng vào chuông thông báo của cả
    2 cửa hàng:
      - Phiếu 'pending' (chờ xử lý): nhắc to_store sớm phản hồi (đồng ý/từ
        chối), đồng thời báo cho from_store biết admin đã nhắc giúp.
      - Phiếu 'approved' nhưng còn mã hàng CHƯA nhận đủ: nhắc to_store sớm
        giao đủ hàng, và nhắc from_store sớm xác nhận đã nhận khi nhận được.
    Phiếu đã xong việc hẳn (rejected/cancelled, hoặc approved đã nhận đủ
    hàng) thì bỏ qua - không còn gì để "nhắc hoàn thành" nữa."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    ids = data.get('ids')
    if not ids or not isinstance(ids, list):
        return jsonify({'error': 'Vui lòng chọn ít nhất 1 phiếu cần nhắc.'}), 400
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({'error': 'Danh sách phiếu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute('SELECT * FROM transfer_requests WHERE id = ANY(%s)', (ids,))
        rows = {r['id']: r for r in cursor.fetchall()}

        # Với các phiếu 'approved', cần biết phiếu nào còn mã hàng chưa nhận -
        # gộp thành 1 câu truy vấn duy nhất cho toàn bộ id để tránh N+1.
        approved_ids = [rid for rid, r in rows.items() if r['status'] == 'approved']
        not_fully_received = set()
        if approved_ids:
            cursor.execute('''
                SELECT DISTINCT request_id FROM transfer_items
                WHERE request_id = ANY(%s) AND received = FALSE
            ''', (approved_ids,))
            not_fully_received = {r['request_id'] for r in cursor.fetchall()}

        reminded, skipped = [], []
        for rid in ids:
            row = rows.get(rid)
            if not row:
                skipped.append(rid)
                continue
            if row['status'] == 'pending':
                create_notification(
                    cursor, row['to_store'], f'Nhắc hoàn thành phiếu #{rid}',
                    f"Admin nhắc bạn sớm phản hồi (đồng ý/từ chối) phiếu #{rid} xin từ {row['from_store']}.",
                    'warning', rid
                )
                create_notification(
                    cursor, row['from_store'], f'Nhắc hoàn thành phiếu #{rid}',
                    f"Admin đã nhắc {row['to_store']} sớm phản hồi phiếu #{rid} bạn đã gửi.",
                    'info', rid
                )
                reminded.append(rid)
            elif row['status'] == 'approved' and rid in not_fully_received:
                create_notification(
                    cursor, row['to_store'], f'Nhắc hoàn thành phiếu #{rid}',
                    f"Admin nhắc bạn sớm giao đủ hàng cho phiếu #{rid} đến {row['from_store']}.",
                    'warning', rid
                )
                create_notification(
                    cursor, row['from_store'], f'Nhắc hoàn thành phiếu #{rid}',
                    f"Admin nhắc bạn sớm xác nhận đã nhận hàng cho phiếu #{rid} từ {row['to_store']}.",
                    'warning', rid
                )
                reminded.append(rid)
            else:
                skipped.append(rid)

        db.commit()
        return jsonify({'success': True, 'reminded': reminded, 'skipped': skipped})
    except Exception:
        db.rollback()
        traceback.print_exc()
        return jsonify({'error': 'Có lỗi xảy ra khi gửi nhắc nhở.'}), 500
    finally:
        cursor.close()


@app.route('/api/admin/transfer/delete', methods=['POST'])
def admin_transfer_delete():
    """Admin XOÁ HẲN 1 phiếu luân chuyển nội bộ khỏi hệ thống - khác với
    "Huỷ" (transfer_cancel, chỉ đổi status='cancelled' và phiếu vẫn còn để
    xem lại) hay "Đổi Lại" (revert): đây xoá thẳng khỏi bảng
    transfer_requests, không giữ lại bất kỳ trạng thái nào. Các dòng mã
    hàng liên quan (transfer_items) tự động bị xoá theo nhờ ON DELETE
    CASCADE. Dùng cho phiếu tạo nhầm hoặc không còn cần lưu lại. Vẫn ghi 1
    dòng log ra Google Sheets (nếu có cấu hình) trước khi xoá để giữ dấu vết
    kiểm toán độc lập với CSDL."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    req_id = data.get('id')
    if not req_id:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM transfer_requests WHERE id = %s', (req_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiếu.'}), 404

    cursor.execute('DELETE FROM transfer_requests WHERE id = %s', (req_id,))
    # Báo cho CẢ 2 cửa hàng liên quan (bên gửi lẫn bên nhận của phiếu) biết
    # phiếu đã bị admin xoá hẳn - để họ thấy được lý do phiếu tự "biến mất"
    # khỏi danh sách của mình (thay vì chỉ im lặng biến mất, dễ gây thắc
    # mắc/tưởng nhầm là lỗi hệ thống). notif_type='danger' để icon chuông tô
    # đỏ, dễ chú ý. Dùng set() để tránh gửi trùng 2 lần nếu từ_store == to_store.
    for store_code in {row['from_store'], row['to_store']}:
        create_notification(
            cursor, store_code,
            f'Phiếu #{req_id}',
            f'Phiếu #{req_id} đã bị admin xoá hẳn khỏi hệ thống.',
            'danger', req_id
        )
    db.commit()
    cursor.close()

    log_transfer_event('Admin xoá phiếu', row, actor=session['user'])

    return jsonify({'success': True})


@app.route('/api/admin/transfer/dismiss-delete-request', methods=['POST'])
def admin_dismiss_delete_request():
    """Admin BỎ QUA yêu cầu xin xoá phiếu của cửa hàng (không xoá phiếu) -
    chỉ xoá cờ delete_requested + lý do đi, phiếu vẫn được giữ nguyên
    trong hệ thống. Dùng khi admin xem lý do xong và quyết định không cần
    xoá. Sau khi bỏ qua, cửa hàng vẫn có thể gửi yêu cầu xoá khác nếu cần."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    req_id = data.get('id')
    if not req_id:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM transfer_requests WHERE id = %s', (req_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiếu.'}), 404

    cursor.execute('''
        UPDATE transfer_requests
        SET delete_requested = FALSE, delete_request_reason = NULL,
            delete_requested_by = NULL, delete_requested_at = NULL
        WHERE id = %s
    ''', (req_id,))
    # Báo lại cho đúng cửa hàng đã gửi yêu cầu xin xoá biết là admin đã bỏ
    # qua (không xoá) - để họ không phải đoán/hỏi lại. delete_requested_by
    # lưu username của người bấm gửi yêu cầu - với tài khoản cửa hàng trong
    # hệ thống này, username LUÔN trùng với store_code (xem default_users ở
    # init_db), nên dùng thẳng được làm store_code nhận thông báo.
    if row['delete_requested'] and row['delete_requested_by']:
        create_notification(
            cursor, row['delete_requested_by'],
            f'Phiếu #{req_id}',
            f'Admin đã bỏ qua yêu cầu xin xoá phiếu #{req_id} của bạn - phiếu vẫn được giữ nguyên.',
            'info', req_id
        )
    db.commit()
    cursor.close()

    log_transfer_event('Admin bỏ qua yêu cầu xoá phiếu', row, actor=session['user'])

    return jsonify({'success': True})


@app.route('/api/admin/delete-store-data', methods=['POST'])
def delete_store_data():
    """Xoá SẠCH toàn bộ dữ liệu PO đã lưu (Danh sách PO, Chi tiết PO, Chi
    tiết nhận hàng và lịch sử tải lên) của MỘT chi nhánh cụ thể.
    Không xoá tài khoản đăng nhập (bảng users) của chi nhánh đó."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    store_code = (data.get('store_code') or '').strip()

    db = get_db()
    cursor = db.cursor()

    # Chỉ cho phép xoá các chi nhánh (role = 'store') có thật trong hệ thống,
    # tránh xoá nhầm do dữ liệu gửi lên sai hoặc bị chỉnh sửa.
    cursor.execute("SELECT DISTINCT store_code FROM users WHERE role = 'store'")
    valid_stores = {r['store_code'] for r in cursor.fetchall()}

    if not store_code or store_code not in valid_stores:
        cursor.close()
        return jsonify({'error': 'Cửa hàng không hợp lệ'}), 400

    cursor.execute("DELETE FROM po_detail_items WHERE store_code = %s", (store_code,))
    cursor.execute("DELETE FROM latest_uploads WHERE store_code = %s", (store_code,))
    cursor.execute("DELETE FROM upload_log WHERE store_code = %s", (store_code,))
    db.commit()
    cursor.close()

    return jsonify({'success': True, 'store_code': store_code})




# ----------------------------------------------------------------------------
# LUÂN CHUYỂN NỘI BỘ GIỮA CÁC CỬA HÀNG
# ----------------------------------------------------------------------------

def _valid_store_codes(cursor):
    """Danh sách mã cửa hàng hợp lệ (role='store'), dùng để chặn việc gửi
    yêu cầu tới một 'cửa hàng' không tồn tại trong hệ thống."""
    cursor.execute("SELECT DISTINCT store_code FROM users WHERE role = 'store'")
    return {r['store_code'] for r in cursor.fetchall()}


def _fetch_transfer_items(cursor, request_ids):
    """Lấy toàn bộ dòng mã hàng (transfer_items) thuộc danh sách phiếu
    request_ids, trả về dict {request_id: [item, ...]} để gắn vào từng
    phiếu khi serialize - tránh N+1 query (1 query DUY NHẤT cho mọi phiếu)."""
    if not request_ids:
        return {}
    cursor.execute(
        'SELECT * FROM transfer_items WHERE request_id = ANY(%s) ORDER BY id',
        (list(request_ids),)
    )
    rows = cursor.fetchall()

    # Tên hàng lưu trong transfer_items chỉ là tên tại THỜI ĐIỂM tạo phiếu -
    # nếu lúc đó không tra được (mã hàng gõ tay không khớp tồn kho đang tải
    # trên trình duyệt...) thì bị lưu trống VĨNH VIỄN, không tự điền lại dù
    # phiếu đổi trạng thái. Nên ở đây tra bù lại từ bảng tồn kho gốc
    # (inventory_items) cho MỌI dòng đang trống tên - áp dụng chung cho mọi
    # nơi hiển thị phiếu (danh sách, in phiếu...), không cần sửa lại dữ liệu
    # cũ trong transfer_items.
    missing_codes = {r['part_code'] for r in rows if not (r.get('part_name') or '').strip()}
    part_name_lookup = {}
    if missing_codes:
        cursor.execute('''
            SELECT DISTINCT ON (part_code) part_code, part_name
            FROM inventory_items
            WHERE part_code = ANY(%s)
            ORDER BY part_code, id DESC
        ''', (list(missing_codes),))
        part_name_lookup = {r['part_code']: r['part_name'] for r in cursor.fetchall() if r['part_name']}

    out = {}
    for it in rows:
        d = dict(it)
        if not (d.get('part_name') or '').strip():
            d['part_name'] = part_name_lookup.get(d['part_code']) or d.get('part_name')
        if d.get('quantity') is not None:
            d['quantity'] = float(d['quantity'])
        if d.get('approved_quantity') is not None:
            d['approved_quantity'] = float(d['approved_quantity'])
        # Đánh dấu để frontend biết dòng này đã bị bên cho sửa số lượng khi
        # duyệt (khác với số lượng bên xin yêu cầu ban đầu) - cả 2 bên đều
        # cần thấy rõ chỗ này.
        d['quantity_adjusted'] = (
            d.get('approved_quantity') is not None and d['approved_quantity'] != d.get('quantity')
        )
        if d.get('received_at'):
            d['received_at'] = format_vi_datetime(d['received_at'])
        out.setdefault(d['request_id'], []).append(d)
    return out


def _serialize_transfer_row(row, items, session_store, role):
    d = dict(row)
    if d.get('created_at'):
        d['created_at'] = format_vi_datetime(d['created_at'])
    if d.get('responded_at'):
        d['responded_at'] = format_vi_datetime(d['responded_at'])
    if d.get('prepared_at'):
        d['prepared_at'] = format_vi_datetime(d['prepared_at'])
    if d.get('delete_requested_at'):
        d['delete_requested_at'] = format_vi_datetime(d['delete_requested_at'])
    d['items'] = items
    d['item_count'] = len(items)
    d['total_quantity'] = sum((it['quantity'] or 0) for it in items)
    d['total_approved_quantity'] = sum((it.get('approved_quantity') or 0) for it in items)
    d['all_received'] = bool(items) and all(it['received'] for it in items)
    d['any_received'] = any(it['received'] for it in items)
    # Gắn nhãn chiều của yêu cầu so với cửa hàng đang đăng nhập, để frontend
    # tách hiển thị "Đã gửi" / "Nhận được" mà không cần tự so sánh store_code.
    if role == 'store':
        d['direction'] = 'sent' if d['from_store'] == session_store else 'received'
    else:
        d['direction'] = None
    return d


# Mặc định /api/transfer/list chỉ tải phiếu trong N ngày gần đây (cộng với
# các phiếu ĐANG CẦN XỬ LÝ dù cũ hơn - xem transfer_list) thay vì tải TOÀN
# BỘ lịch sử từ trước tới giờ mỗi lần mở trang - lịch sử càng ngày càng
# nhiều phiếu thì càng chậm/tốn băng thông nếu không giới hạn. Khi bấm "Xem
# lịch sử cũ hơn" ở frontend, mỗi lần tải thêm 1 lô _TRANSFER_LIST_PAGE_SIZE
# phiếu cũ hơn (dùng con trỏ updated_at, không phải tải theo ngày, để luôn
# trả về dữ liệu ngay cả khi phiếu tạo thưa).
_TRANSFER_LIST_RECENT_DAYS = 90
_TRANSFER_LIST_PAGE_SIZE = 50
# Giới hạn an toàn khi lọc theo khoảng ngày cụ thể (tránh 1 khoảng ngày quá
# rộng kéo về hàng chục nghìn dòng cùng lúc) - đủ rộng cho nhu cầu xem lại
# theo ngày/tuần/tháng thông thường.
_TRANSFER_LIST_DATE_FILTER_LIMIT = 1000


def _transfer_store_filter(role, store_code, filter_store=None):
    """Trả về (where_sql, params) lọc theo cửa hàng - dùng chung cho cả 2
    nhánh (tải gần đây / tải thêm lịch sử cũ) bên dưới."""
    if role == 'admin':
        if filter_store and filter_store != 'ALL':
            return 'from_store = %s OR to_store = %s', (filter_store, filter_store)
        return None, ()
    return 'from_store = %s OR to_store = %s', (store_code, store_code)


@app.route('/api/transfer/list', methods=['GET'])
def transfer_list():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    role = session['role']
    store_code = session['store_code']
    filter_store = request.args.get('store') if role == 'admin' else None
    before = request.args.get('before')  # ISO timestamp - có nghĩa là đang bấm "xem lịch sử cũ hơn"
    date_from_str = request.args.get('date_from')
    date_to_str = request.args.get('date_to')

    db = get_db()
    cursor = db.cursor()

    store_where, store_params = _transfer_store_filter(role, store_code, filter_store)

    # --- Cache phản hồi CHỈ cho lượt tải mặc định (mở trang / tự cập nhật khi
    # transfer_version đổi) - đây là lượt nặng nhất: TOÀN BỘ phiếu 90 ngày gần
    # đây + mọi dòng mã hàng của chúng, và mọi tab đang mở đều gọi lại mỗi khi
    # có phiếu đổi ở bất kỳ đâu. Chữ ký tính RIÊNG theo phạm vi cửa hàng đang
    # xem nên phiếu đổi ở cửa hàng khác không làm cache này hết hạn. Lọc theo
    # ngày / "xem cũ hơn" (before) không cache (ít gọi, tham số đa dạng).
    tr_key = None
    tr_sig = None
    if not (before or date_from_str or date_to_str):
        tr_key = ('transfer_list', role, store_code if role == 'store' else None,
                  (filter_store or 'ALL') if role == 'admin' else None)
        sig_sql = 'SELECT COUNT(*) AS c, MAX(updated_at) AS v FROM transfer_requests'
        if store_where:
            sig_sql += f' WHERE {store_where}'
        cursor.execute(sig_sql, store_params)
        sig_row = cursor.fetchone()
        tr_sig = (_inventory_epoch, _write_epoch, _transfer_epoch, sig_row['c'], str(sig_row['v']),
                  vn_now().date().isoformat())
        cached_body = _resp_cache_get(tr_key, tr_sig)
        if cached_body is not None:
            cursor.close()
            return app.response_class(cached_body, mimetype='application/json')

    # Sắp xếp/lọc theo created_at (ngày TẠO phiếu) chứ không phải updated_at
    # - nếu dùng updated_at, mỗi lần thao tác (đồng ý/từ chối/nhận hàng) sẽ
    # đổi updated_at của phiếu đó, khiến nó tự nhảy lên đầu danh sách ngay
    # sau khi thao tác dù ngày tạo phiếu không đổi - gây khó chịu, không
    # đúng kỳ vọng "vẫn đứng yên khi thao tác". created_at là bất biến nên
    # thứ tự hiển thị luôn ổn định theo đúng ngày tạo, mới nhất lên đầu.
    if date_from_str or date_to_str:
        # Lọc theo 1 NGÀY CỤ THỂ hoặc 1 KHOẢNG NGÀY do người dùng chọn - khác
        # hẳn 2 nhánh còn lại (vốn chỉ phục vụ hiển thị "gần đây"/"tải thêm
        # lịch sử"): ở đây bỏ qua giới hạn _TRANSFER_LIST_RECENT_DAYS và điều
        # kiện "phiếu còn dang dở" để trả về ĐÚNG mọi phiếu tạo trong khoảng
        # ngày yêu cầu, dù phiếu đó cũ tới đâu hay đã xong việc hay chưa.
        try:
            date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date() if date_from_str else None
            date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date() if date_to_str else None
        except ValueError:
            cursor.close()
            return jsonify({'error': 'Ngày không hợp lệ. Định dạng đúng: YYYY-MM-DD.'}), 400

        conditions = []
        params = []
        if date_from:
            conditions.append('created_at::date >= %s')
            params.append(date_from)
        if date_to:
            conditions.append('created_at::date <= %s')
            params.append(date_to)
        if store_where:
            conditions.append(f'({store_where})')
            params.extend(store_params)
        sql = f'''
            SELECT * FROM transfer_requests
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at DESC
            LIMIT {_TRANSFER_LIST_DATE_FILTER_LIMIT}
        '''
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        # Lọc theo ngày là 1 lượt tải TRỌN VẸN (không phân trang tiếp bằng
        # "before") - has_more ở đây chỉ mang nghĩa "có thể còn bị cắt bớt do
        # chạm giới hạn an toàn", không dùng để bấm "xem lịch sử cũ hơn".
        has_more = len(rows) == _TRANSFER_LIST_DATE_FILTER_LIMIT
    elif before:
        # Tải thêm 1 lô lịch sử CŨ HƠN mốc "before". Chỉ cần lấy phiếu đã
        # xong việc (approved/rejected) vì phiếu "pending" hoặc "approved"
        # còn hàng chưa nhận luôn được tải sẵn ở lượt mặc định bên dưới rồi
        # - tránh bị trùng dòng khi nối 2 danh sách lại ở frontend.
        conditions = ["status IN ('approved', 'rejected')", 'created_at < %s']
        params = [before]
        if store_where:
            conditions.append(f'({store_where})')
            params.extend(store_params)
        sql = f'''
            SELECT * FROM transfer_requests
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
            LIMIT {_TRANSFER_LIST_PAGE_SIZE}
        '''
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        has_more = len(rows) == _TRANSFER_LIST_PAGE_SIZE
    else:
        # Lượt tải mặc định (mở trang / bấm F5): phiếu TẠO trong
        # _TRANSFER_LIST_RECENT_DAYS ngày gần đây, CỘNG THÊM mọi phiếu đang
        # cần xử lý (pending, hoặc approved nhưng còn mã hàng chưa nhận) dù
        # phiếu đó có tạo cũ hơn khoảng thời gian trên hay không - không được
        # để phiếu cần xử lý "biến mất" chỉ vì nó cũ.
        conditions = [
            f"(created_at >= NOW() - INTERVAL '{_TRANSFER_LIST_RECENT_DAYS} days'"
            " OR status = 'pending'"
            " OR (status = 'approved' AND EXISTS ("
            "SELECT 1 FROM transfer_items ti WHERE ti.request_id = transfer_requests.id AND ti.received = FALSE"
            ")))",
        ]
        params = []
        if store_where:
            conditions.append(f'({store_where})')
            params.extend(store_params)
        sql = f'''
            SELECT * FROM transfer_requests
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at DESC
        '''
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        has_more = bool(rows)  # cần ít nhất 1 dòng để có mốc con trỏ "before" cho lần bấm tải thêm

    items_by_request = _fetch_transfer_items(cursor, [r['id'] for r in rows])
    cursor.close()

    requests_out = [
        _serialize_transfer_row(r, items_by_request.get(r['id'], []), store_code, role)
        for r in rows
    ]
    # oldest_created_at: mốc để frontend gọi tiếp ?before=... khi bấm "xem
    # lịch sử cũ hơn" - lấy từ dòng cuối cùng (đã ORDER BY created_at DESC).
    # Không có ý nghĩa khi đang lọc theo ngày (date_from/date_to) nên bỏ qua.
    oldest_created_at = str(rows[-1]['created_at']) if rows and not (date_from_str or date_to_str) else None
    resp = jsonify({
        'success': True,
        'requests': requests_out,
        'has_more': has_more if not (date_from_str or date_to_str) else False,
        'oldest_created_at': oldest_created_at,
    })
    if tr_key is not None:
        _resp_cache_put(tr_key, tr_sig, resp.get_data())
    return resp


def _create_transfer_request(cursor, from_store, to_store, note, created_by, items, created_employee):
    """Tạo 1 phiếu luân chuyển + toàn bộ dòng mã hàng của phiếu đó trong
    CÙNG 1 transaction. `items` là list dict {part_code, part_name, quantity}
    đã được làm sạch/kiểm tra hợp lệ từ trước. Trả về id phiếu vừa tạo."""
    now = vn_now()
    cursor.execute('''
        INSERT INTO transfer_requests (from_store, to_store, note, status, created_by, created_at, updated_at, created_employee)
        VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s)
        RETURNING id
    ''', (from_store, to_store, note, created_by, now, now, created_employee))
    new_id = cursor.fetchone()['id']
    execute_values(
        cursor,
        '''INSERT INTO transfer_items (request_id, part_code, part_name, quantity)
           VALUES %s''',
        [(new_id, it['part_code'], it.get('part_name'), it['quantity']) for it in items]
    )
    return new_id


def _clean_transfer_items(raw_items):
    """Chuẩn hoá + kiểm tra danh sách mã hàng gửi lên (từ form nhập tay hoặc
    từ file Excel import). Trả về (items_sạch, lỗi_hoặc_None). Nếu cùng 1 mã
    hàng xuất hiện nhiều lần thì cộng dồn số lượng lại thay vì tạo 2 dòng."""
    if not raw_items or not isinstance(raw_items, list):
        return None, 'Vui lòng nhập ít nhất 1 mã hàng cần xin.'

    merged = {}
    order = []
    for it in raw_items:
        part_code = str((it.get('part_code') or '')).strip()
        if not part_code:
            continue
        try:
            qty = float(it.get('quantity'))
        except (TypeError, ValueError):
            qty = None
        if qty is None or qty <= 0:
            return None, f'Số lượng không hợp lệ cho mã hàng "{part_code}".'
        part_name = (it.get('part_name') or '').strip() or None
        key = part_code.upper()
        if key in merged:
            merged[key]['quantity'] += qty
            if not merged[key]['part_name'] and part_name:
                merged[key]['part_name'] = part_name
        else:
            merged[key] = {'part_code': part_code, 'part_name': part_name, 'quantity': qty}
            order.append(key)

    if not merged:
        return None, 'Vui lòng nhập ít nhất 1 mã hàng cần xin hợp lệ.'
    return [merged[k] for k in order], None


@app.route('/api/transfer/create', methods=['POST'])
def transfer_create():
    """Cửa hàng tạo 1 phiếu xin luân chuyển - có thể chứa NHIỀU mã hàng
    cùng lúc (mỗi mã hàng kèm số lượng riêng), gửi tới 1 cửa hàng khác."""
    if 'user' not in session or session['role'] != 'store':
        return jsonify({'error': 'Chỉ tài khoản cửa hàng mới được tạo yêu cầu luân chuyển.'}), 403

    data = request.json or {}
    from_store = session['store_code']
    to_store = (data.get('to_store') or '').strip().upper()
    note = (data.get('note') or '').strip() or None
    created_employee = _valid_employee_for_store(data.get('created_employee'), from_store)
    if not created_employee:
        return jsonify({'error': 'Vui lòng chọn nhân viên tạo phiếu thuộc cửa hàng của bạn.'}), 400

    items, err = _clean_transfer_items(data.get('items'))
    if err:
        return jsonify({'error': err}), 400

    if not to_store:
        return jsonify({'error': 'Vui lòng chọn cửa hàng cần xin.'}), 400
    if to_store == from_store:
        return jsonify({'error': 'Không thể tự xin luân chuyển từ chính cửa hàng của mình.'}), 400

    db = get_db()
    cursor = db.cursor()

    if to_store not in _valid_store_codes(cursor):
        cursor.close()
        return jsonify({'error': 'Cửa hàng cần xin không hợp lệ.'}), 400

    new_id = _create_transfer_request(cursor, from_store, to_store, note, session['user'], items, created_employee)

    create_notification(cursor, to_store, f'Phiếu #{new_id}', f'Có phiếu xin luân chuyển mới từ {from_store} gửi đến bạn.', 'info', new_id)
    create_notification(cursor, from_store, f'Phiếu #{new_id}', f'Bạn đã gửi phiếu xin luân chuyển mới đến {to_store}.', 'info', new_id)

    db.commit()
    cursor.close()

    log_transfer_event(
        'Tạo phiếu xin luân chuyển',
        {'id': new_id, 'from_store': from_store, 'to_store': to_store, 'created_employee': created_employee},
        items, note=note, actor=session['user'],
    )

    return jsonify({'success': True, 'id': new_id, 'item_count': len(items)})


@app.route('/api/admin/transfer/create', methods=['POST'])
def admin_transfer_create():
    """Admin tạo hộ 1 phiếu luân chuyển giữa 2 cửa hàng bất kỳ - admin tự
    chọn cả cửa hàng XUẤT (from_store, bên sẽ nhận hàng) lẫn cửa hàng NHẬN
    YÊU CẦU (to_store, bên sẽ gửi hàng đi). Sau khi tạo, phiếu này hiển thị
    y hệt như phiếu do chính 2 cửa hàng đó tự tạo/nhận - từ_store thấy ở
    mục "Phiếu Tôi Đã Gửi", to_store thấy ở mục "Phiếu Cửa Hàng Khác Gửi
    Đến"."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    from_store = (data.get('from_store') or '').strip().upper()
    to_store = (data.get('to_store') or '').strip().upper()
    note = (data.get('note') or '').strip() or None
    #created_employee = _valid_employee_for_store(data.get('created_employee'), from_store)
    #if not created_employee:
        #return jsonify({'error': 'Vui lòng chọn nhân viên tạo phiếu thuộc cửa hàng nhận.'}), 400
    created_employee = (data.get('created_employee') or '').strip()
    if created_employee not in ADMIN_EMPLOYEES:
        return jsonify({'error': 'Vui lòng chọn nhân viên tạo phiếu hợp lệ (Lý Huỳnh Như hoặc Nguyễn Như Ngọc).'}), 400
    #created_employee = _valid_employee_for_store(data.get('created_employee'), from_store)
    #if not created_employee:
        #return jsonify({'error': 'Vui lòng chọn nhân viên tạo phiếu thuộc cửa hàng của bạn.'}), 400
    items, err = _clean_transfer_items(data.get('items'))
    if err:
        return jsonify({'error': err}), 400 

    if not from_store or not to_store:
        return jsonify({'error': 'Vui lòng chọn đủ cả cửa hàng xuất và cửa hàng nhận.'}), 400
    if to_store == from_store:
        return jsonify({'error': 'Cửa hàng xuất và cửa hàng nhận không được trùng nhau.'}), 400

    db = get_db()
    cursor = db.cursor()

    valid_stores = _valid_store_codes(cursor)
    if from_store not in valid_stores or to_store not in valid_stores:
        cursor.close()
        return jsonify({'error': 'Cửa hàng không hợp lệ.'}), 400

    new_id = _create_transfer_request(cursor, from_store, to_store, note, session['user'], items, created_employee)
    db.commit()
    cursor.close()

    log_transfer_event(
        'Tạo phiếu (admin tạo hộ)',
        {'id': new_id, 'from_store': from_store, 'to_store': to_store, 'created_employee': created_employee},
        items, note=note, actor=session['user'],
    )

    return jsonify({'success': True, 'id': new_id, 'item_count': len(items)})


@app.route('/api/admin/transfer/import-excel', methods=['POST'])
def admin_transfer_import_excel():
    """Admin tạo hộ 1 phiếu luân chuyển giữa 2 cửa hàng bất kỳ bằng cách
    IMPORT danh sách mã hàng + số lượng từ 1 file Excel (thay vì gõ tay từng
    dòng) - giống hệt /api/transfer/import-excel của chi nhánh, chỉ khác là
    cả from_store lẫn to_store đều do admin tự chọn (gửi kèm trong form)."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    from_store = (request.form.get('from_store') or '').strip().upper()
    to_store = (request.form.get('to_store') or '').strip().upper()
    note = (request.form.get('note') or '').strip() or None
    #created_employee = _valid_employee_for_store(request.form.get('created_employee'), from_store)
    #if not created_employee:
       # return jsonify({'error': 'Vui lòng chọn nhân viên tạo phiếu thuộc cửa hàng nhận.'}), 400
    created_employee = (request.form.get('created_employee') or '').strip()
    if created_employee not in ADMIN_EMPLOYEES:
     return jsonify({'error': 'Vui lòng chọn nhân viên tạo phiếu hợp lệ (Lý Huỳnh Như hoặc Nguyễn Như Ngọc).'}), 400
    excel_file = request.files.get('file')

    if not excel_file:
        return jsonify({'error': 'Vui lòng chọn file Excel danh sách mã hàng.'}), 400
    if not from_store or not to_store:
        return jsonify({'error': 'Vui lòng chọn đủ cửa hàng chuyển và cửa hàng nhận.'}), 400
    if to_store == from_store:
        return jsonify({'error': 'Cửa hàng chuyển và cửa hàng nhận không được trùng nhau.'}), 400

    try:
        df = read_any(excel_file)
    except Exception as e:
        return jsonify({'error': f'Không đọc được file Excel: {e}'}), 400

    part_col = find_col(df.columns, ['mã phụ tùng', 'mã hàng', 'part code', 'part #', 'part number', 'part#', 'part'])
    qty_col = find_col(df.columns, ['số lượng yêu cầu', 'số lượng', 'sl', 'quantity'])
    name_col = find_col(df.columns, ['tên hàng', 'tên phụ tùng', 'part name', 'description', 'tên'])

    if not part_col or not qty_col:
        return jsonify({'error': 'Không tìm thấy cột "Mã hàng"/"Mã phụ tùng" và "Số lượng" trong file. Vui lòng kiểm tra lại file Excel.'}), 400

    raw_items = []
    skipped = 0
    for _, r in df.iterrows():
        part_code = str(r.get(part_col, '') or '').strip()
        if not part_code or part_code.lower() == 'nan':
            continue
        try:
            qty = float(r.get(qty_col))
            if math.isnan(qty):
                raise ValueError()
        except (TypeError, ValueError):
            skipped += 1
            continue
        if qty <= 0:
            skipped += 1
            continue
        part_name = str(r.get(name_col, '') or '').strip() if name_col else ''
        if part_name.lower() == 'nan':
            part_name = ''
        raw_items.append({'part_code': part_code, 'part_name': part_name, 'quantity': qty})

    items, err = _clean_transfer_items(raw_items)
    if err:
        return jsonify({'error': f'File không có dòng dữ liệu hợp lệ nào. {err}'}), 400

    db = get_db()
    cursor = db.cursor()

    valid_stores = _valid_store_codes(cursor)
    if from_store not in valid_stores or to_store not in valid_stores:
        cursor.close()
        return jsonify({'error': 'Cửa hàng không hợp lệ.'}), 400

    new_id = _create_transfer_request(cursor, from_store, to_store, note, session['user'], items, created_employee)
    db.commit()
    cursor.close()

    log_transfer_event(
        'Tạo phiếu (admin import Excel)',
        {'id': new_id, 'from_store': from_store, 'to_store': to_store, 'created_employee': created_employee},
        items, note=note, actor=session['user'],
    )

    return jsonify({'success': True, 'id': new_id, 'item_count': len(items), 'skipped_rows': skipped})


@app.route('/api/transfer/import-excel', methods=['POST'])
def transfer_import_excel():
    """Cửa hàng tạo 1 phiếu xin luân chuyển bằng cách IMPORT danh sách mã
    hàng + số lượng từ 1 file Excel (thay vì gõ tay từng dòng) - tiện cho
    trường hợp cần xin nhiều mã hàng cùng lúc từ 1 cửa hàng."""
    if 'user' not in session or session['role'] != 'store':
        return jsonify({'error': 'Chỉ tài khoản cửa hàng mới được tạo yêu cầu luân chuyển.'}), 403

    from_store = session['store_code']
    to_store = (request.form.get('to_store') or '').strip().upper()
    note = (request.form.get('note') or '').strip() or None
    created_employee = _valid_employee_for_store(request.form.get('created_employee'), from_store)
    if not created_employee:
        return jsonify({'error': 'Vui lòng chọn nhân viên tạo phiếu thuộc cửa hàng của bạn.'}), 400
    excel_file = request.files.get('file')

    if not excel_file:
        return jsonify({'error': 'Vui lòng chọn file Excel danh sách mã hàng cần xin.'}), 400
    if not to_store:
        return jsonify({'error': 'Vui lòng chọn cửa hàng cần xin.'}), 400
    if to_store == from_store:
        return jsonify({'error': 'Không thể tự xin luân chuyển từ chính cửa hàng của mình.'}), 400

    try:
        df = read_any(excel_file)
    except Exception as e:
        return jsonify({'error': f'Không đọc được file Excel: {e}'}), 400

    part_col = find_col(df.columns, ['mã phụ tùng', 'mã hàng', 'part code', 'part #', 'part number', 'part#', 'part'])
    qty_col = find_col(df.columns, ['số lượng yêu cầu', 'số lượng', 'sl', 'quantity'])
    name_col = find_col(df.columns, ['tên hàng', 'tên phụ tùng', 'part name', 'description', 'tên'])

    if not part_col or not qty_col:
        return jsonify({'error': 'Không tìm thấy cột "Mã hàng"/"Mã phụ tùng" và "Số lượng" trong file. Vui lòng kiểm tra lại file Excel.'}), 400

    raw_items = []
    skipped = 0
    for _, r in df.iterrows():
        part_code = str(r.get(part_col, '') or '').strip()
        if not part_code or part_code.lower() == 'nan':
            continue
        try:
            qty = float(r.get(qty_col))
            if math.isnan(qty):
                raise ValueError()
        except (TypeError, ValueError):
            skipped += 1
            continue
        if qty <= 0:
            skipped += 1
            continue
        part_name = str(r.get(name_col, '') or '').strip() if name_col else ''
        if part_name.lower() == 'nan':
            part_name = ''
        raw_items.append({'part_code': part_code, 'part_name': part_name, 'quantity': qty})

    items, err = _clean_transfer_items(raw_items)
    if err:
        return jsonify({'error': f'File không có dòng dữ liệu hợp lệ nào. {err}'}), 400

    db = get_db()
    cursor = db.cursor()

    if to_store not in _valid_store_codes(cursor):
        cursor.close()
        return jsonify({'error': 'Cửa hàng cần xin không hợp lệ.'}), 400

    new_id = _create_transfer_request(cursor, from_store, to_store, note, session['user'], items, created_employee)
    db.commit()
    cursor.close()

    log_transfer_event(
        'Tạo phiếu (import Excel)',
        {'id': new_id, 'from_store': from_store, 'to_store': to_store, 'created_employee': created_employee},
        items, note=note, actor=session['user'],
    )

    return jsonify({'success': True, 'id': new_id, 'item_count': len(items), 'skipped_rows': skipped})


@app.route('/api/transfer/respond', methods=['POST'])
def transfer_respond():
    """Cửa hàng được xin (to_store) phản hồi CẢ PHIẾU (mọi mã hàng trong
    phiếu): Đồng ý hoặc Từ chối (kèm lý do). Không còn bước "Đã soạn/Chưa
    soạn" trung gian - đồng ý là đồng ý ngay."""
    if 'user' not in session or session['role'] != 'store':
        return jsonify({'error': 'Chỉ tài khoản cửa hàng mới được phản hồi yêu cầu.'}), 403

    data = request.json or {}
    req_id = data.get('id')
    action = data.get('action')
    reason = (data.get('reason') or '').strip()
    confirmed_employee = (data.get('confirmed_employee') or '').strip()
    raw_approved_items = data.get('items')  # chỉ dùng khi action == 'approve'

    if not req_id or action not in ('approve', 'reject'):
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400
    if action == 'reject' and not reason:
        return jsonify({'error': 'Vui lòng nhập lý do từ chối.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM transfer_requests WHERE id = %s', (req_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy yêu cầu.'}), 404
    if row['to_store'] != session['store_code']:
        cursor.close()
        return jsonify({'error': 'Bạn không có quyền xử lý yêu cầu này.'}), 403
    if row['status'] not in ('pending', 'approved', 'rejected'):
        cursor.close()
        return jsonify({'error': 'Yêu cầu này không còn ở trạng thái có thể xử lý.'}), 400

    # Lấy trước danh sách mã hàng của phiếu (kèm id + quantity gốc) - dùng
    # để (a) đối chiếu/validate số lượng đồng ý gửi lên khi approve, và
    # (b) ghi log Google Sheets cho cả 2 trường hợp (đồng ý / từ chối).
    cursor.execute(
        'SELECT id, part_code, part_name, quantity FROM transfer_items WHERE request_id = %s ORDER BY id',
        (req_id,)
    )
    items = cursor.fetchall()

    now = vn_now()
    if action == 'approve':
        confirmed_employee = _valid_employee_for_store(confirmed_employee, row['to_store'])
        if not confirmed_employee:
            cursor.close()
            return jsonify({'error': 'Vui lòng chọn nhân viên xác nhận thuộc cửa hàng của bạn.'}), 400

        # Bên cho (to_store) có thể sửa lại số lượng đồng ý cho từng mã hàng
        # (ví dụ không đủ hàng để cho đúng số lượng bên xin yêu cầu). Nếu
        # frontend không gửi "items" (hoặc thiếu mã nào), mặc định số lượng
        # đồng ý = số lượng yêu cầu ban đầu cho mã đó.
        approved_qty_by_item_id = {}
        if raw_approved_items and isinstance(raw_approved_items, list):
            for it in raw_approved_items:
                try:
                    item_id = int(it.get('id'))
                    qty = float(it.get('approved_quantity'))
                except (TypeError, ValueError):
                    cursor.close()
                    return jsonify({'error': 'Số lượng đồng ý không hợp lệ.'}), 400
                if qty < 0:
                    cursor.close()
                    return jsonify({'error': 'Số lượng đồng ý không được nhỏ hơn 0.'}), 400
                approved_qty_by_item_id[item_id] = qty

        valid_item_ids = {it['id'] for it in items}
        if approved_qty_by_item_id and not set(approved_qty_by_item_id).issubset(valid_item_ids):
            cursor.close()
            return jsonify({'error': 'Có mã hàng không thuộc phiếu này.'}), 400

        for it in items:
            approved_qty = approved_qty_by_item_id.get(it['id'], float(it['quantity'] or 0))
            cursor.execute(
                'UPDATE transfer_items SET approved_quantity = %s WHERE id = %s',
                (approved_qty, it['id'])
            )

        cursor.execute('''
            UPDATE transfer_requests
            SET status = 'approved', reject_reason = NULL,
                responded_by = %s, responded_at = %s, updated_at = %s,
                confirmed_employee = %s
            WHERE id = %s
        ''', (session['user'], now, now, confirmed_employee, req_id))

        # Ghi lại đầy đủ cả 2 số: "quantity" (bên xin yêu cầu, giữ nguyên) và
        # "approved_quantity" (bên cho thực sự đồng ý) - để log ra Google
        # Sheets có đủ 2 cột, tiện đối chiếu khi số lượng bị sửa lại.
        for it in items:
            it['approved_quantity'] = approved_qty_by_item_id.get(it['id'], float(it['quantity'] or 0))
    else:
        # Từ chối (hoặc đổi lại lựa chọn cũ rồi từ chối lại) - xoá số lượng
        # đã đồng ý trước đó (nếu có) vì phiếu không còn ở trạng thái đồng ý.
        cursor.execute('UPDATE transfer_items SET approved_quantity = NULL WHERE request_id = %s', (req_id,))
        cursor.execute('''
            UPDATE transfer_requests
            SET status = 'rejected', reject_reason = %s,
                responded_by = %s, responded_at = %s, updated_at = %s,
                confirmed_employee = NULL, prepared = FALSE, prepared_at = NULL, prepared_by = NULL
            WHERE id = %s
        ''', (reason, session['user'], now, now, req_id))
        confirmed_employee = None

    if action == 'approve':
        create_notification(cursor, row['from_store'], f'Phiếu #{req_id}', f"Phiếu #{req_id} đã được {row['to_store']} đồng ý.", 'success', req_id)
    else:
        create_notification(cursor, row['from_store'], f'Phiếu #{req_id}', f"Phiếu #{req_id} đã được {row['to_store']} từ chối.", 'danger', req_id)

    db.commit()
    cursor.close()

    log_req = dict(row)
    log_req['confirmed_employee'] = confirmed_employee or ''
    if action == 'approve':
        log_transfer_event('Đồng ý', log_req, items, actor=session['user'])
    else:
        log_transfer_event('Từ chối', log_req, items, note=reason, actor=session['user'])

    return jsonify({'success': True})


@app.route('/api/transfer/toggle-prepared', methods=['POST'])
def transfer_toggle_prepared():
    """Cửa hàng CHO (to_store) đánh dấu đã soạn xong hàng (hoặc bỏ đánh
    dấu) cho 1 phiếu ĐÃ ĐỒNG Ý - bước chuẩn bị hàng vật lý sẵn sàng giao,
    tách riêng khỏi hành động Đồng Ý (chỉ là quyết định có cho hay không).
    Chỉ to_store của đúng phiếu đó mới được thao tác."""
    if 'user' not in session or session['role'] != 'store':
        return jsonify({'error': 'Chỉ tài khoản cửa hàng mới được thao tác.'}), 403

    data = request.json or {}
    req_id = data.get('id')
    prepared = bool(data.get('prepared'))
    if not req_id:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM transfer_requests WHERE id = %s', (req_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy yêu cầu.'}), 404
    if row['to_store'] != session['store_code']:
        cursor.close()
        return jsonify({'error': 'Bạn không có quyền xử lý yêu cầu này.'}), 403
    if row['status'] != 'approved':
        cursor.close()
        return jsonify({'error': 'Chỉ có thể cập nhật soạn hàng cho phiếu đã được Đồng Ý.'}), 400

    now = vn_now()
    cursor.execute('''
        UPDATE transfer_requests
        SET prepared = %s, prepared_at = %s, prepared_by = %s, updated_at = %s
        WHERE id = %s
    ''', (prepared, now if prepared else None, session['user'] if prepared else None, now, req_id))

    # Chỉ báo lúc CHUYỂN từ chưa soạn -> đã soạn (không báo khi bỏ đánh dấu,
    # và không báo lặp nếu bấm đi bấm lại nhiều lần) - row['prepared'] ở đây
    # vẫn là giá trị TRƯỚC khi UPDATE (đã SELECT lúc đầu hàm).
    if prepared and not row['prepared']:
        create_notification(
            cursor, row['from_store'], f'Phiếu #{req_id}',
            f"{row['to_store']} đã soạn xong hàng cho phiếu #{req_id}, có thể đi nhận hàng.",
            'success', req_id
        )

    db.commit()
    cursor.close()

    log_transfer_event(
        'Đã soạn hàng' if prepared else 'Bỏ đánh dấu đã soạn hàng',
        {'id': req_id, 'from_store': row['from_store'], 'to_store': row['to_store']},
        actor=session['user'],
    )

    return jsonify({'success': True})


@app.route('/api/transfer/revert', methods=['POST'])
def transfer_revert():
    """Cửa hàng được xin (to_store) ĐỔI LẠI lựa chọn đã đồng ý/từ chối
    trước đó, đưa phiếu về trạng thái "Chờ Xử Lý" để chọn lại. Chỉ cho phép
    khi CHƯA có mã hàng nào trong phiếu được đánh dấu đã nhận hàng (nếu
    hàng đã nhận thực tế rồi thì không thể đổi ý được nữa)."""
    if 'user' not in session or session['role'] != 'store':
        return jsonify({'error': 'Chỉ tài khoản cửa hàng mới được thao tác.'}), 403

    data = request.json or {}
    req_id = data.get('id')
    if not req_id:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM transfer_requests WHERE id = %s', (req_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy yêu cầu.'}), 404
    if row['to_store'] != session['store_code']:
        cursor.close()
        return jsonify({'error': 'Bạn không có quyền thao tác yêu cầu này.'}), 403
    if row['status'] not in ('approved', 'rejected'):
        cursor.close()
        return jsonify({'error': 'Chỉ có thể đổi lại lựa chọn cho yêu cầu đã đồng ý hoặc đã từ chối.'}), 400

    cursor.execute('SELECT COUNT(*) AS c FROM transfer_items WHERE request_id = %s AND received = TRUE', (req_id,))
    if cursor.fetchone()['c'] > 0:
        cursor.close()
        return jsonify({'error': 'Cửa hàng xin đã nhận một phần hàng của phiếu này, không thể đổi lại lựa chọn nữa.'}), 400

    was_approved = (row['status'] == 'approved')
    items = []
    if was_approved:
        cursor.execute(
            'SELECT part_code, part_name, quantity, approved_quantity FROM transfer_items WHERE request_id = %s ORDER BY id',
            (req_id,)
        )
        # Giữ nguyên cả 2 cột: "quantity" (bên xin yêu cầu) và
        # "approved_quantity" (số lượng đã đồng ý trước khi đổi lại) để ghi
        # log đầy đủ.
        items = cursor.fetchall()

    now = vn_now()
    cursor.execute('''
        UPDATE transfer_requests
        SET status = 'pending', reject_reason = NULL, responded_by = NULL, responded_at = NULL,
            confirmed_employee = NULL, prepared = FALSE, prepared_at = NULL, prepared_by = NULL, updated_at = %s
        WHERE id = %s
    ''', (now, req_id))
    # Xoá số lượng đã đồng ý trước đó (nếu có) - phiếu về "Chờ Xử Lý" thì
    # chưa có số lượng đồng ý nào cả, sẽ được chọn lại từ đầu ở lần duyệt kế tiếp.
    cursor.execute('UPDATE transfer_items SET approved_quantity = NULL WHERE request_id = %s', (req_id,))
    db.commit()
    cursor.close()

    log_transfer_event(
        'Đổi lại lựa chọn (về Chờ Xử Lý)',
        row, items if was_approved else None,
        note=f"Trạng thái trước đó: {row['status']}",
        actor=session['user'],
    )

    return jsonify({'success': True})


@app.route('/api/transfer/mark-received', methods=['POST'])
def transfer_mark_received():
    """Cửa hàng đã gửi yêu cầu (from_store) tick "Đã nhận hàng" cho 1 mã
    hàng cụ thể trong phiếu đã được đồng ý. Khi tick, ghi chú tô màu tồn
    kho (đỏ/xanh lá) của đúng mã hàng đó ở cả 2 cửa hàng sẽ tự biến mất."""
    if 'user' not in session or session['role'] != 'store':
        return jsonify({'error': 'Chỉ tài khoản cửa hàng mới được thao tác.'}), 403

    data = request.json or {}
    item_id = data.get('item_id')
    received = data.get('received', True)
    if not item_id:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT ti.*, tr.from_store, tr.to_store, tr.status AS request_status
        FROM transfer_items ti JOIN transfer_requests tr ON tr.id = ti.request_id
        WHERE ti.id = %s
    ''', (item_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy mã hàng trong phiếu.'}), 404
    if row['from_store'] != session['store_code']:
        cursor.close()
        return jsonify({'error': 'Bạn không có quyền thao tác mã hàng này.'}), 403
    if row['request_status'] != 'approved':
        cursor.close()
        return jsonify({'error': 'Chỉ có thể đánh dấu nhận hàng cho phiếu đã được đồng ý.'}), 400

    now = vn_now()
    cursor.execute(
        'UPDATE transfer_items SET received = %s, received_at = %s WHERE id = %s',
        (bool(received), now if received else None, item_id)
    )
    # Cũng cập nhật updated_at của phiếu cha để cơ chế poll-version (dựa
    # trên MAX(updated_at) của transfer_requests) phát hiện được thay đổi.
    cursor.execute('UPDATE transfer_requests SET updated_at = %s WHERE id = %s', (now, row['request_id']))
    db.commit()
    cursor.close()

    log_transfer_event(
        'Đã nhận hàng' if received else 'Bỏ đánh dấu đã nhận hàng',
        {'id': row['request_id'], 'from_store': row['from_store'], 'to_store': row['to_store']},
        [{'part_code': row['part_code'], 'part_name': row['part_name'],
          'quantity': row['quantity'], 'approved_quantity': row.get('approved_quantity')}],
        actor=session['user'],
    )

    return jsonify({'success': True})


@app.route('/api/transfer/mark-all-received', methods=['POST'])
def transfer_mark_all_received():
    """Cửa hàng đã gửi yêu cầu (from_store) tick 1 LẦN DUY NHẤT "Đã Nhận Đủ"
    để đánh dấu TẤT CẢ mã hàng trong phiếu đã được đồng ý là đã nhận (hoặc bỏ
    tick để đưa tất cả về chưa nhận), thay vì phải tick từng mã hàng một."""
    if 'user' not in session or session['role'] != 'store':
        return jsonify({'error': 'Chỉ tài khoản cửa hàng mới được thao tác.'}), 403

    data = request.json or {}
    request_id = data.get('request_id')
    received = data.get('received', True)
    if not request_id:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM transfer_requests WHERE id = %s', (request_id,))
    req_row = cursor.fetchone()
    if not req_row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiếu.'}), 404
    if req_row['from_store'] != session['store_code']:
        cursor.close()
        return jsonify({'error': 'Bạn không có quyền thao tác phiếu này.'}), 403
    if req_row['status'] != 'approved':
        cursor.close()
        return jsonify({'error': 'Chỉ có thể đánh dấu nhận hàng cho phiếu đã được đồng ý.'}), 400

    now = vn_now()
    cursor.execute(
        'UPDATE transfer_items SET received = %s, received_at = %s WHERE request_id = %s',
        (bool(received), now if received else None, request_id)
    )
    cursor.execute('UPDATE transfer_requests SET updated_at = %s WHERE id = %s', (now, request_id))
    db.commit()

    items = _fetch_transfer_items(cursor, [request_id]).get(request_id, [])
    cursor.close()

    log_transfer_event(
        'Đã nhận đủ hàng (toàn bộ phiếu)' if received else 'Bỏ đánh dấu đã nhận đủ hàng (toàn bộ phiếu)',
        {'id': request_id, 'from_store': req_row['from_store'], 'to_store': req_row['to_store']},
        [{'part_code': it['part_code'], 'part_name': it.get('part_name'),
          'quantity': it['quantity'], 'approved_quantity': it.get('approved_quantity')} for it in items],
        actor=session['user'],
    )

    return jsonify({'success': True})


@app.route('/api/transfer/cancel', methods=['POST'])
def transfer_cancel():
    """Cửa hàng đã gửi (from_store) tự huỷ yêu cầu của mình khi còn đang chờ xử lý."""
    if 'user' not in session or session['role'] != 'store':
        return jsonify({'error': 'Chỉ tài khoản cửa hàng mới được huỷ yêu cầu.'}), 403

    data = request.json or {}
    req_id = data.get('id')
    if not req_id:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM transfer_requests WHERE id = %s', (req_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy yêu cầu.'}), 404
    if row['from_store'] != session['store_code']:
        cursor.close()
        return jsonify({'error': 'Bạn không có quyền huỷ yêu cầu này.'}), 403
    if row['status'] != 'pending':
        cursor.close()
        return jsonify({'error': 'Chỉ có thể huỷ yêu cầu đang chờ xử lý.'}), 400

    cursor.execute("UPDATE transfer_requests SET status = 'cancelled', updated_at = %s WHERE id = %s", (vn_now(), req_id))
    create_notification(cursor, row['from_store'], f'Phiếu #{req_id}', f'Phiếu #{req_id} đã bị hủy.', 'warning', req_id)
    db.commit()
    cursor.close()

    log_transfer_event('Huỷ phiếu', row, actor=session['user'])

    return jsonify({'success': True})


# store_code dùng chung cho thông báo gửi tới ADMIN (khác với thông báo gửi
# cho 1 cửa hàng cụ thể) - trùng với store_code='ALL' cố định của tài khoản
# admin (xem default_users trong init_db), nên notifications_list() của
# admin (session['store_code'] == 'ALL' khi KHÔNG mượn quyền cửa hàng nào)
# tự động lọc đúng ra đúng các thông báo này mà không cần thêm cột/bảng
# riêng.
ADMIN_NOTIF_STORE_CODE = 'ALL'


@app.route('/api/transfer/request-delete', methods=['POST'])
def transfer_request_delete():
    """Cửa hàng (bên gửi HOẶC bên nhận của phiếu) nhờ ADMIN xoá hẳn 1 phiếu
    luân chuyển, kèm lý do - dùng cho các trường hợp cửa hàng không tự xử
    lý được nữa (vd phiếu đã Đồng Ý/Từ Chối/Huỷ từ trước, chỉ phiếu
    'pending' mới tự Huỷ được qua transfer_cancel ở trên). KHÔNG xoá ngay:
    chỉ đánh dấu delete_requested=TRUE + lý do, rồi báo cho admin qua icon
    chuông - admin xem chi tiết phiếu và tự quyết định xoá (dùng lại
    admin_transfer_delete) hay bỏ qua (admin_dismiss_delete_request)."""
    if 'user' not in session or session['role'] != 'store':
        return jsonify({'error': 'Chỉ tài khoản cửa hàng mới dùng được chức năng này.'}), 403

    data = request.json or {}
    req_id = data.get('id')
    reason = (data.get('reason') or '').strip()
    if not req_id or not reason:
        return jsonify({'error': 'Vui lòng nhập lý do xin xoá phiếu.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM transfer_requests WHERE id = %s', (req_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiếu.'}), 404
    if session['store_code'] not in (row['from_store'], row['to_store']):
        cursor.close()
        return jsonify({'error': 'Bạn không có quyền thao tác trên phiếu này.'}), 403
    if row['delete_requested']:
        cursor.close()
        return jsonify({'error': 'Phiếu này đã được gửi yêu cầu xoá trước đó, đang chờ admin xử lý.'}), 400

    now = vn_now()
    cursor.execute('''
        UPDATE transfer_requests
        SET delete_requested = TRUE, delete_request_reason = %s,
            delete_requested_by = %s, delete_requested_at = %s
        WHERE id = %s
    ''', (reason, session['user'], now, req_id))
    create_notification(
        cursor, ADMIN_NOTIF_STORE_CODE,
        f'Yêu cầu xoá phiếu #{req_id}',
        f"{session['store_code']} xin xoá phiếu #{req_id}. Lý do: {reason}",
        'warning', req_id
    )
    db.commit()
    cursor.close()

    log_transfer_event('Xin admin xoá phiếu', row, actor=session['user'], note=reason)

    return jsonify({'success': True})


@app.route('/api/transfer/highlights', methods=['GET'])
def transfer_highlights():
    """Trả về dữ liệu để tô màu bảng Tồn Kho Hệ Thống theo các phiếu luân
    chuyển ĐÃ ĐỒNG Ý nhưng CHƯA nhận hàng xong:
      - 'given': ở cửa hàng CHO (to_store) - cột tồn kho của mã hàng đó tại
        cửa hàng này sẽ tô ĐỎ, kèm danh sách "cửa hàng nào xin bao nhiêu".
      - 'receiving': ở cửa hàng XIN (from_store) - cột tồn kho của mã hàng
        đó tại cửa hàng này sẽ tô XANH LÁ, kèm "đang nhận từ cửa hàng nào,
        số lượng bao nhiêu".
    Khi 1 dòng mã hàng được tick "Đã nhận hàng" (received = TRUE) thì dòng
    đó không còn xuất hiện trong dữ liệu trả về nữa -> ghi chú màu tự mất."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    db = get_db()
    cursor = db.cursor()
    # Dùng COALESCE(approved_quantity, quantity): sau khi phiếu đã được
    # đồng ý, số lượng THỰC SỰ sẽ luân chuyển là approved_quantity (bên cho
    # có thể đã sửa lại) - nếu vì lý do gì đó chưa có (dữ liệu cũ), fallback
    # về quantity gốc.
    cursor.execute('''
        SELECT tr.from_store, tr.to_store, ti.part_code,
               COALESCE(ti.approved_quantity, ti.quantity) AS quantity
        FROM transfer_items ti
        JOIN transfer_requests tr ON tr.id = ti.request_id
        WHERE tr.status = 'approved' AND ti.received = FALSE
              AND COALESCE(ti.approved_quantity, ti.quantity) > 0
    ''')
    rows = cursor.fetchall()
    cursor.close()

    given = {}      # given[part_code][to_store] = [{store, quantity}, ...]  (tô đỏ ở to_store)
    receiving = {}  # receiving[part_code][from_store] = [{store, quantity}, ...]  (tô xanh ở from_store)

    for r in rows:
        part_code = r['part_code']
        qty = float(r['quantity']) if r['quantity'] is not None else 0
        given.setdefault(part_code, {}).setdefault(r['to_store'], []).append(
            {'store': r['from_store'], 'quantity': qty}
        )
        receiving.setdefault(part_code, {}).setdefault(r['from_store'], []).append(
            {'store': r['to_store'], 'quantity': qty}
        )

    # Phát hiện "XIN CHÉO": tại 1 cửa hàng S, với 1 mã hàng, nếu S vừa phải
    # CHO đối tác P (given) vừa đang CHỜ NHẬN từ chính đối tác P đó
    # (receiving) - đây là 1 cặp trao đổi 2 chiều giữa S và P, tách riêng
    # ra để tô màu khác (không phải đỏ/xanh thường), tránh gây hiểu nhầm là
    # 2 giao dịch độc lập không liên quan.
    # crossed[part_code][store] = [{store: partner, given_quantity, receiving_quantity}, ...]
    crossed = {}

    for part_code in set(list(given.keys()) + list(receiving.keys())):
        given_by_store = given.get(part_code, {})
        receiving_by_store = receiving.get(part_code, {})
        stores_involved = set(list(given_by_store.keys()) + list(receiving_by_store.keys()))

        for store in stores_involved:
            given_entries = given_by_store.get(store, [])
            receiving_entries = receiving_by_store.get(store, [])

            # Gộp số lượng theo từng đối tác (1 cửa hàng có thể có nhiều
            # phiếu khác nhau với cùng 1 đối tác cho cùng mã hàng).
            given_qty_by_partner = {}
            for g in given_entries:
                given_qty_by_partner[g['store']] = given_qty_by_partner.get(g['store'], 0) + g['quantity']
            receiving_qty_by_partner = {}
            for rcv in receiving_entries:
                receiving_qty_by_partner[rcv['store']] = receiving_qty_by_partner.get(rcv['store'], 0) + rcv['quantity']

            crossed_partners = set(given_qty_by_partner.keys()) & set(receiving_qty_by_partner.keys())
            if not crossed_partners:
                continue

            for partner in crossed_partners:
                crossed.setdefault(part_code, {}).setdefault(store, []).append({
                    'store': partner,
                    'given_quantity': given_qty_by_partner[partner],
                    'receiving_quantity': receiving_qty_by_partner[partner],
                })

            # Loại bỏ các đối tác đã xác định là chéo khỏi given/receiving
            # thường, chỉ giữ lại phần KHÔNG chéo (nếu cửa hàng này còn giao
            # dịch với cửa hàng thứ 3 khác cho cùng mã hàng).
            given[part_code][store] = [g for g in given_entries if g['store'] not in crossed_partners]
            if not given[part_code][store]:
                del given[part_code][store]
            receiving[part_code][store] = [rcv for rcv in receiving_entries if rcv['store'] not in crossed_partners]
            if not receiving[part_code][store]:
                del receiving[part_code][store]

        if part_code in given and not given[part_code]:
            del given[part_code]
        if part_code in receiving and not receiving[part_code]:
            del receiving[part_code]

    return jsonify({'success': True, 'given': given, 'receiving': receiving, 'crossed': crossed})


# ------------------------------------------------------------------
# THÔNG BÁO CHUYỂN KHO (icon chuông) - danh sách bền vững lưu trong
# database (khác với toast cũ chỉ so sánh dữ liệu ở trình duyệt), có đánh
# dấu đã đọc/chưa đọc, và tự động biến mất sau NOTIFICATION_RETENTION_DAYS
# (7) ngày kể từ lúc xuất hiện.
# ------------------------------------------------------------------
@app.route('/api/notifications/list', methods=['GET'])
def notifications_list():
    """Trả về danh sách thông báo của cửa hàng (hoặc của admin) đang đăng
    nhập (mới nhất trước), kèm số lượng chưa đọc. Việc dọn thông báo quá
    hạn 7 ngày KHÔNG chạy ở đây nữa (đã dời qua job nền chạy tối đa 1
    lần/ngày, xem run_notifications_cleanup_job) - route này giờ chỉ còn 2
    câu SELECT nhẹ, nhanh như nhau dù bảng notifications có bao nhiêu dữ
    liệu lịch sử. Admin THẬT (không đang mượn quyền 1 cửa hàng nào) có
    session['store_code'] == ADMIN_NOTIF_STORE_CODE ('ALL', cố định từ lúc
    tạo tài khoản admin) nên dùng chung đúng cột store_code này để nhận
    thông báo riêng của mình (vd yêu cầu xin xoá phiếu) - không cần thêm
    cột/nhánh xử lý riêng. Lúc admin đang mượn quyền 1 cửa hàng,
    session['role'] tạm đổi thành 'store' nên tự rơi vào đúng thông báo của
    cửa hàng đang mượn quyền."""
    if 'user' not in session or session['role'] not in ('store', 'admin'):
        return jsonify({'error': 'Chỉ tài khoản cửa hàng hoặc admin mới có thông báo.'}), 403

    store_code = session['store_code']
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id, title, message, notif_type, transfer_id, is_read, created_at
        FROM notifications
        WHERE store_code = %s
        ORDER BY created_at DESC
        LIMIT 100
    ''', (store_code,))
    rows = cursor.fetchall()

    cursor.execute(
        'SELECT COUNT(*) AS c FROM notifications WHERE store_code = %s AND is_read = FALSE',
        (store_code,)
    )
    unread_count = cursor.fetchone()['c']
    cursor.close()

    notifications = [{
        'id': r['id'],
        'title': r['title'],
        'message': r['message'],
        'notif_type': r['notif_type'],
        'transfer_id': r['transfer_id'],
        'is_read': r['is_read'],
        # Gắn RÕ múi giờ VN (+07:00) vào chuỗi ISO trả về. Cột created_at là
        # TIMESTAMP không kèm time zone và đã được ghi theo giờ VN (xem
        # create_notification), nhưng nếu trả chuỗi "trần" thì trình duyệt sẽ
        # tự hiểu theo múi giờ của MÁY người dùng - máy để lệch múi giờ là
        # chuông hiện sai ngay ("7 giờ trước" dù vừa mới tạo).
        'created_at': r['created_at'].replace(tzinfo=VN_TZ).isoformat() if r['created_at'] else None,
    } for r in rows]

    return jsonify({'success': True, 'notifications': notifications, 'unread_count': unread_count})


@app.route('/api/notifications/mark-read', methods=['POST'])
def notifications_mark_read():
    """Đánh dấu ĐÃ ĐỌC 1 thông báo cụ thể - chỉ được đánh dấu thông báo của
    đúng cửa hàng/admin mình (không đọc/không sửa được thông báo của
    cửa hàng khác dù có biết id)."""
    if 'user' not in session or session['role'] not in ('store', 'admin'):
        return jsonify({'error': 'Chỉ tài khoản cửa hàng hoặc admin mới có thông báo.'}), 403

    data = request.json or {}
    notif_id = data.get('id')
    if not notif_id:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'UPDATE notifications SET is_read = TRUE WHERE id = %s AND store_code = %s',
        (notif_id, session['store_code'])
    )
    db.commit()
    cursor.close()

    return jsonify({'success': True})


@app.route('/api/notifications/mark-all-read', methods=['POST'])
def notifications_mark_all_read():
    """Đánh dấu ĐÃ ĐỌC toàn bộ thông báo hiện có của cửa hàng/admin đang
    đăng nhập (dùng cho nút "Đánh dấu tất cả đã đọc" trên icon chuông)."""
    if 'user' not in session or session['role'] not in ('store', 'admin'):
        return jsonify({'error': 'Chỉ tài khoản cửa hàng hoặc admin mới có thông báo.'}), 403

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'UPDATE notifications SET is_read = TRUE WHERE store_code = %s AND is_read = FALSE',
        (session['store_code'],)
    )
    db.commit()
    cursor.close()

    return jsonify({'success': True})


# ------------------------------------------------------------------
# THÔNG BÁO "NGÀY XE ĐI" (banner đỏ dưới thanh menu) - hiển thị cho TẤT
# CẢ cửa hàng + admin, khác hẳn bảng notifications (icon chuông, riêng
# từng cửa hàng). Chỉ 1 hành động của admin (tạo 1 dòng với ngày xe đi),
# banner tự ẩn khi qua ngày đó mà KHÔNG cần job dọn dẹp / KHÔNG xoá dữ
# liệu - chỉ cần so sánh departure_date với ngày hiện tại (giờ VN) ngay
# trong câu SELECT mỗi lần lấy danh sách đang hiệu lực.
# ------------------------------------------------------------------
def get_active_truck_announcements(cursor):
    """Trả về danh sách các banner "ngày xe đi" ĐANG CÒN HIỆU LỰC (còn
    active = TRUE và departure_date >= ngày hôm nay theo giờ VN) - dùng
    cả khi render trang lần đầu (index()) lẫn trong /api/version (để các
    trình duyệt khác tự ẩn/hiện banner theo thời gian thực mà không cần
    F5, và tự ẩn đúng lúc sang ngày mới mà không cần thao tác gì thêm)."""
    today = vn_now().date()
    cursor.execute('''
        SELECT id, departure_date, message, created_at
        FROM truck_announcements
        WHERE active = TRUE AND departure_date >= %s
        ORDER BY departure_date ASC, id ASC
    ''', (today,))
    rows = cursor.fetchall()
    return [{
        'id': r['id'],
        'departure_date': r['departure_date'].isoformat(),
        'departure_date_vi': f"{r['departure_date'].day:02d}/{r['departure_date'].month:02d}/{r['departure_date'].year}",
        'message': r['message'],
        'created_at': r['created_at'].isoformat() if r['created_at'] else None,
    } for r in rows]


@app.route('/api/admin/truck-announcements', methods=['GET'])
def admin_truck_announcements_list():
    """Admin xem toàn bộ lịch sử thông báo ngày xe đi (kể cả đã qua ngày
    hoặc đã bị huỷ sớm) - dùng cho bảng quản lý trong tab Quản Lý User."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id, departure_date, message, active, created_by, created_at
        FROM truck_announcements
        ORDER BY departure_date DESC, id DESC
        LIMIT 200
    ''')
    rows = cursor.fetchall()
    cursor.close()

    today = vn_now().date()
    items = [{
        'id': r['id'],
        'departure_date': r['departure_date'].isoformat(),
        'departure_date_vi': f"{r['departure_date'].day:02d}/{r['departure_date'].month:02d}/{r['departure_date'].year}",
        'message': r['message'],
        'active': r['active'],
        'is_showing': bool(r['active'] and r['departure_date'] >= today),
        'created_by': r['created_by'],
        'created_at': r['created_at'].isoformat() if r['created_at'] else None,
    } for r in rows]

    return jsonify({'success': True, 'items': items})


@app.route('/api/admin/truck-announcements', methods=['POST'])
def admin_truck_announcements_create():
    """Admin tạo thông báo "ngày xe đi" mới - hiện ngay lập tức thành banner
    đỏ dưới thanh menu cho TẤT CẢ cửa hàng (kể cả admin), tự ẩn khi qua
    ngày departure_date. Không giới hạn chỉ 1 banner tại 1 thời điểm: nếu
    admin tạo nhiều ngày xe đi sắp tới, tất cả sẽ hiện cùng lúc (xếp theo
    ngày gần nhất trước)."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json or {}
    departure_date_str = (data.get('departure_date') or '').strip()
    message = (data.get('message') or '').strip()

    if not departure_date_str:
        return jsonify({'error': 'Vui lòng chọn ngày xe đi.'}), 400
    if not message:
        return jsonify({'error': 'Vui lòng nhập nội dung thông báo.'}), 400
    if len(message) > 500:
        return jsonify({'error': 'Nội dung thông báo tối đa 500 ký tự.'}), 400

    try:
        departure_date = datetime.strptime(departure_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Ngày xe đi không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO truck_announcements (departure_date, message, active, created_by, created_at)
        VALUES (%s, %s, TRUE, %s, %s)
        RETURNING id
    ''', (departure_date, message, session['user'], vn_now()))
    new_id = cursor.fetchone()['id']
    db.commit()
    cursor.close()

    return jsonify({'success': True, 'id': new_id})


@app.route('/api/admin/truck-announcements/<int:announcement_id>/cancel', methods=['POST'])
def admin_truck_announcements_cancel(announcement_id):
    """Admin ẩn sớm 1 banner đang hiển thị (vd huỷ chuyến / thông báo nhầm)
    - chỉ đổi active = FALSE, KHÔNG xoá dòng dữ liệu, nên lịch sử vẫn còn
    nguyên trong CSDL để tra cứu lại (đúng yêu cầu không xoá dữ liệu)."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'UPDATE truck_announcements SET active = FALSE WHERE id = %s',
        (announcement_id,)
    )
    db.commit()
    cursor.close()

    return jsonify({'success': True})


@app.route('/api/admin/truck-announcements/<int:announcement_id>', methods=['PUT'])
def admin_truck_announcements_update(announcement_id):
    """Admin sửa lại ngày xe đi / nội dung của 1 thông báo đã tạo. Sau khi
    sửa, banner tự đặt lại active = TRUE (kể cả nếu trước đó đã bị huỷ/qua
    ngày) vì mục đích sửa thường là để CHO HIỂN THỊ LẠI với thông tin mới,
    ví dụ đổi ngày xe đi sang ngày khác."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json or {}
    departure_date_str = (data.get('departure_date') or '').strip()
    message = (data.get('message') or '').strip()

    if not departure_date_str:
        return jsonify({'error': 'Vui lòng chọn ngày xe đi.'}), 400
    if not message:
        return jsonify({'error': 'Vui lòng nhập nội dung thông báo.'}), 400
    if len(message) > 500:
        return jsonify({'error': 'Nội dung thông báo tối đa 500 ký tự.'}), 400

    try:
        departure_date = datetime.strptime(departure_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Ngày xe đi không hợp lệ.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        UPDATE truck_announcements
        SET departure_date = %s, message = %s, active = TRUE
        WHERE id = %s
        RETURNING id
    ''', (departure_date, message, announcement_id))
    row = cursor.fetchone()
    db.commit()
    cursor.close()

    if not row:
        return jsonify({'error': 'Không tìm thấy thông báo.'}), 404

    return jsonify({'success': True})


@app.route('/api/admin/truck-announcements/<int:announcement_id>', methods=['DELETE'])
def admin_truck_announcements_delete(announcement_id):
    """Admin xoá HẲN 1 thông báo khỏi lịch sử (khác với "Ẩn ngay" chỉ đổi
    active = FALSE) - dùng khi tạo nhầm hoặc muốn dọn bớt lịch sử cũ."""
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    db = get_db()
    cursor = db.cursor()
    cursor.execute('DELETE FROM truck_announcements WHERE id = %s RETURNING id', (announcement_id,))
    row = cursor.fetchone()
    db.commit()
    cursor.close()

    if not row:
        return jsonify({'error': 'Không tìm thấy thông báo.'}), 404

    return jsonify({'success': True})


# Import + đăng ký blueprint stocktake - đặt Ở ĐÂY (cuối file) vì
# stocktake.py cần import ngược lại get_db/vn_now/format_vi_datetime/
# _valid_store_codes/STORE_REGIONS từ app, và tất cả các tên đó (đặc biệt
# là _valid_store_codes) phải đã được định nghĩa xong ở phía trên trước khi
# dòng import này chạy, nếu không sẽ lỗi ImportError.
from stocktake import stocktake_bp, init_stocktake_tables
app.register_blueprint(stocktake_bp)

# Import + đăng ký blueprint price_adjustment (tính năng Đề Xuất Tăng Giá) -
# cùng lý do/vị trí như stocktake_bp ở trên: price_adjustment.py cần import
# ngược lại get_db/vn_now/_current_actor_name từ app, nên phải đặt SAU khi
# các tên đó đã được định nghĩa xong ở phía trên.
from price_adjustment import price_adjustment_bp, init_price_adjustment_tables
app.register_blueprint(price_adjustment_bp)

# Import + đăng ký blueprint dashboard (Gợi Ý Nhập Hàng / Cảnh Báo Hết Hàng /
# Dashboard Tổng Quan) - cùng lý do/vị trí như 2 blueprint ở trên: cần
# get_db/_valid_store_codes/create_notification/ADMIN_NOTIF_STORE_CODE/
# _get_app_setting/_set_app_setting/_compute_sales_frequency_rows/
# SALES_FREQ_LABELS đã định nghĩa xong ở phía trên.
from dashboard import dashboard_bp, init_dashboard_tables
app.register_blueprint(dashboard_bp)

# Import + đăng ký blueprint warehouse3d (Sơ Đồ Kho 3D, route /kho-3d) -
# cùng lý do/vị trí như các blueprint ở trên: warehouse3d.py cần import
# ngược lại get_db/vn_now/format_vi_datetime/_valid_store_codes/
# _current_actor_name từ app, nên phải đặt SAU khi các tên đó đã được định
# nghĩa xong, và TRƯỚC lời gọi _init_db_with_retry() bên dưới (vì init_db()
# có gọi init_warehouse3d_tables()).
from warehouse3d import warehouse3d_bp, init_warehouse3d_tables
app.register_blueprint(warehouse3d_bp)

# Tự động gọi khởi tạo bảng khi chạy app (gọi SAU khi đã đăng ký blueprint
# ở trên, vì init_db() bên trong có gọi init_stocktake_tables()).
#
# BỌC RETRY khi gặp deadlock: Render triển khai kiểu "rolling deploy" -
# instance MỚI (đang chạy init_db() để migrate schema) có thể khởi động
# trong lúc instance CŨ vẫn đang phục vụ traffic thật (đang có transaction
# khác giữ khoá trên cùng bảng/table khác theo thứ tự ngược lại). Postgres
# phát hiện ra vòng chờ chéo đó sẽ tự huỷ 1 trong 2 transaction bằng lỗi
# "deadlock detected" - nếu chẳng may rơi vào transaction migrate của
# init_db(), cả process gunicorn sập ngay lúc khởi động (không tự phục hồi).
# Vì các câu lệnh trong init_db() đều là CREATE/ALTER ... IF NOT EXISTS (an
# toàn để chạy lại nhiều lần), retry lại từ đầu vài lần với khoảng nghỉ tăng
# dần là đủ để vượt qua tình huống va chạm ngắn hạn này mà không cần sửa gì
# thêm ở phần logic migrate.
def _init_db_with_retry(max_attempts=5, base_delay_seconds=3):
    for attempt in range(1, max_attempts + 1):
        try:
            init_db()
            return
        except (psycopg2.errors.DeadlockDetected, psycopg2.errors.LockNotAvailable) as e:
            try:
                get_db().rollback()
            except Exception:
                pass
            if attempt >= max_attempts:
                raise
            wait_seconds = base_delay_seconds * attempt
            print(f'[init_db] Lần {attempt}/{max_attempts} gặp lỗi khoá DB ({e.__class__.__name__}) - '
                  f'thử lại sau {wait_seconds}s...', flush=True)
            time.sleep(wait_seconds)


# Chỉ chạy migrate DB 1 LẦN. Khi chạy local bằng `python3 run.py` với Flask
# debug=True (auto-reloader), Python thực ra IMPORT file app.py này 2 LẦN
# GẦN NHƯ CÙNG LÚC:
#   1) Lần đầu (tiến trình "cha") - lúc run.py import app.py rồi gọi
#      app.run(debug=True). Lúc NÀY app.run() còn chưa được gọi xong nên
#      Flask CHƯA kịp set debug/WERKZEUG_RUN_MAIN gì cả - không thể dựa vào
#      app.debug ở thời điểm này để phân biệt 2 lần import.
#   2) Ngay sau đó, Werkzeug tự exec ra 1 tiến trình CON là bản sao chính
#      nó để làm reloader (theo dõi file thay đổi) - tiến trình con này
#      IMPORT LẠI TOÀN BỘ app.py từ đầu, nhưng lần này có sẵn biến môi
#      trường WERKZEUG_RUN_MAIN='true' do Werkzeug tự set trước khi exec.
# Nếu không chặn lại, _init_db_with_retry() chạy đúng 2 lần GẦN NHƯ CÙNG
# LÚC ở 2 tiến trình khác nhau, cùng migrate 1 database - tranh chấp khoá
# bảng theo thứ tự khác nhau -> Postgres báo "deadlock detected" (đúng lỗi
# đang gặp). Cách chặn ĐÚNG: chỉ bỏ qua nếu ĐANG ở tiến trình con vừa được
# reloader tự exec ra (WERKZEUG_RUN_MAIN == 'true') - tiến trình cha (lần
# import đầu tiên, biến này CHƯA tồn tại) đã migrate xong trước đó rồi.
# Không dùng reloader (production/gunicorn trên Render, hoặc debug=False)
# thì biến này không bao giờ được set -> vẫn chạy migrate bình thường,
# đúng 1 lần duy nhất vì chỉ có 1 tiến trình.
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
    _init_db_with_retry()
# Import + đăng ký blueprint orders (Danh Sách Đặt Hàng) - LƯU Ý: khác
# stocktake_bp/price_adjustment_bp, module này dùng CSDL Supabase RIÊNG
# BIỆT (xem orders.py), nên có pool/khởi tạo bảng/retry độc lập, KHÔNG
# gộp vào init_db() ở trên.
from orders import orders_bp, init_orders_tables
app.register_blueprint(orders_bp)


def _init_orders_with_retry(max_attempts=5, base_delay_seconds=3):
    for attempt in range(1, max_attempts + 1):
        try:
            init_orders_tables()
            return
        except (psycopg2.errors.DeadlockDetected, psycopg2.errors.LockNotAvailable) as e:
            if attempt >= max_attempts:
                raise
            wait_seconds = base_delay_seconds * attempt
            print(f'[init_orders_tables] Lần {attempt}/{max_attempts} gặp lỗi khoá DB '
                  f'({e.__class__.__name__}) - thử lại sau {wait_seconds}s...', flush=True)
            time.sleep(wait_seconds)


_init_orders_with_retry()

# Import + đăng ký blueprint body_kit (Tra Cứu Bảng Giá Bộ Áo Xe) - dùng
# CHUNG pool CSDL đặt hàng riêng với orders.py (get_orders_db()), nên PHẢI
# import SAU khi orders_bp đã đăng ký + _init_orders_with_retry() đã chạy
# xong ở trên (đảm bảo ORDERS_DATABASE_URL/pool đã sẵn sàng).
from body_kit import body_kit_bp, init_body_kit_tables
app.register_blueprint(body_kit_bp)


def _init_body_kit_with_retry(max_attempts=5, base_delay_seconds=3):
    for attempt in range(1, max_attempts + 1):
        try:
            init_body_kit_tables()
            return
        except (psycopg2.errors.DeadlockDetected, psycopg2.errors.LockNotAvailable) as e:
            if attempt >= max_attempts:
                raise
            wait_seconds = base_delay_seconds * attempt
            print(f'[init_body_kit_tables] Lần {attempt}/{max_attempts} gặp lỗi khoá DB '
                  f'({e.__class__.__name__}) - thử lại sau {wait_seconds}s...', flush=True)
            time.sleep(wait_seconds)


_init_body_kit_with_retry()

if __name__ == '__main__':
    # LƯU Ý: không chạy file này trực tiếp (`python3 app.py`) - hãy chạy
    # `python3 run.py` ở thư mục gốc. Xem giải thích chi tiết trong run.py
    # (đây là để tránh app.py bị Python nạp 2 lần, gây lỗi
    # "cannot import name 'stocktake_bp' from 'stocktake'").
    raise SystemExit(
        "Đừng chạy app.py trực tiếp - hãy chạy: python3 run.py"
    )