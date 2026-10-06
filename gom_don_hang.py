# -*- coding: utf-8 -*-
"""
Module GÔM ĐƠN HÀNG (thay thế "Lập đơn nháp" của branch_orders.py và toàn bộ purchase_summary.py).

Blueprint độc lập, cùng kiểu branch_orders.py. Bảng tự tạo ở lần dùng đầu tiên
(CREATE TABLE IF NOT EXISTS) nên KHÔNG cần sửa init_db().

MÓC NỐI VÀO app.py (cạnh branch_orders, TRƯỚC _init_db_with_retry()):

    from gom_don_hang import gom_don_hang_bp
    app.register_blueprint(gom_don_hang_bp)

LUỒNG DÙNG
    1. Chi nhánh import file "Tổng hợp tồn kho" của mình (2 mẫu):
         - Mẫu 1: Đầu kỳ / Nhập kho / Xuất kho / Cuối kỳ, mỗi nhóm có Số lượng + Giá trị
                  -> có giá vốn (Giá trị / Số lượng). Cột "Xuất kho" gồm cả xuất chuyển kho... nên thường
                  LỚN HƠN số bán thật.
         - Mẫu 2: Đầu kỳ / SL mua hàng / SL bán hàng / Cuối kỳ -> số bán chính xác, không có giá vốn.
       Import cả 2 mẫu cho cùng một kỳ thì được GỘP vào cùng 1 đợt gôm (số bán lấy từ mẫu 2, giá vốn từ mẫu 1).
    2. Kỳ (từ ngày - đến ngày) tự đọc từ dòng tiêu đề file ("Tháng 9 năm 2026" hoặc "từ ngày .. đến ngày ..");
       không đọc được thì nhập tay. Từ kỳ này chia ra số bán TB / tháng và / tuần.
    3. Mỗi mã: tần suất bán, nhập xuất tồn, SL đề xuất đặt, +/- thêm, SL cuối, LOẠI ĐƠN, thành tiền theo giá vốn.
    4. Dashboard: tổng hợp các đợt gôm có kỳ bán nằm trong khoảng ngày chọn; chọn loại đơn để liệt kê mọi mã đã đặt.

CÔNG THỨC (giữ tinh thần file gốc, đã chỉnh cho gọn - xem ghi chú trong tin nhắn bàn giao)
    Số ngày của kỳ        = đến - từ + 1
    Số tuần               = max(1, INT(số ngày / 7))
    Số tháng              = số ngày / 30
    Số bán                = SL bán hàng (mẫu 2) nếu có, ngược lại Xuất kho (mẫu 1)
    BQ bán / tuần         = 0 nếu tần suất CB, ngược lại số bán / số tuần (làm tròn 1 chữ số thập phân, vd 0,31 -> 0,3)
    Đề xuất đặt           = max(0, ROUNDUP(BQ/tuần x số tuần dự kiến - tồn cuối))
    SL cuối               = max(0, đề xuất + (cộng/trừ thêm))
    MÃ CÓ MÃ CHA (quy cách)  : đề xuất vẫn TÍNH BẰNG MÃ CON (số bán + tồn của mã con), rồi quy ra MÃ CHA:
                             đề xuất (cha) = ROUNDUP(đề xuất con / số quy đổi). Từ đó SL đề xuất, cộng/trừ thêm,
                             SL cuối và SL đặt đều tính bằng ĐƠN VỊ MÃ CHA.
    Thành tiền            = SL cuối x giá vốn   (mã có mã cha: giá vốn của MÃ CHA, SL cuối đã là mã cha; giá vốn: file mẫu 1 -> part_vehicle_models.gia_nhap)
"""
import io
import math
import re
import threading
import calendar
import time
import traceback
import zipfile
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, request, jsonify, session, send_file
import psycopg2
from psycopg2.extras import execute_values
from audit_log import audit_record

from app import (get_db, _valid_store_codes, classify_sales_frequency,
                 get_store_data_version, compute_result_for_store_cached,
                 _is_excluded_from_reorder, EXCLUDED_REORDER_PART_CODE_PREFIXES,
                 create_notification, app as _flask_app, _get_app_setting, _set_app_setting)

# Mã không được đặt (vd khung xe 50100...) - dùng CHUNG quy tắc với app.py, sửa 1 chỗ là áp dụng cho cả hai.
_EXCL_LIKE = [p + '%' for p in EXCLUDED_REORDER_PART_CODE_PREFIXES]

gom_don_hang_bp = Blueprint('gom_don_hang', __name__)
_VN_TZ = ZoneInfo('Asia/Ho_Chi_Minh')

ORDER_TYPES = ['Định kỳ', 'Khẩn', 'Đơn 26']
DEFAULT_FORECAST_WEEKS = 3
DAYS_PER_MONTH = 30.0
STORE_COLS = ['NS1', 'NS2', 'NS3', 'NS4', 'NS5', 'NSM1']      # thứ tự cột tồn từng chi nhánh
GROUP_LABELS = {'TX': 'Thường xuyên', 'TB': 'Trung bình', 'CB': 'Chậm bán', 'HET': 'Hết tồn'}

# Vòng đời của đơn gôm CHUNG của chi nhánh (owner = ''):
#   draft (Nháp) -> pending (Chờ duyệt) -> reviewing (Đang duyệt) -> approved (Đã duyệt)
#   -> viewed (Đã xem: cửa hàng mở kết quả duyệt) -> ordered (Đã đặt: cửa hàng bấm Tải đơn về)
# Admin duyệt ở menu "Duyệt Đơn Hàng" (vùng quản trị): lấy đơn về -> bảng kiểm tra -> Duyệt xong.
# Đơn chung của chi nhánh bị KHOÁ từ lúc đẩy; SL duyệt + ghi chú từng mã của admin lưu ở gdh_batch_review
# (chỉ để cửa hàng tham khảo, file đặt hàng vẫn theo SL cuối của gôm).
# Đơn gôm riêng của admin (owner <> '') luôn là draft, không đi qua luồng này.
STATUS_LABELS = {'draft': 'Nháp', 'pending': 'Chờ duyệt', 'reviewing': 'Đang duyệt',
                 'approved': 'Đã duyệt', 'viewed': 'Đã xem', 'ordered': 'Đã đặt'}
POST_SUBMIT_STATUSES = ['pending', 'reviewing', 'approved', 'viewed', 'ordered']
_MAX_ITEMS = 6000
_MAX_DASH_PARTS = 5000

# LƯU TRỮ TỰ ĐỘNG: đơn Đã duyệt / Đã xem / Đã đặt quá ARCHIVE_DEFAULT_DAYS ngày (tính từ lúc admin duyệt xong)
# -> xuất Excel (mỗi loại đơn 1 file) lưu vào bảng gdh_archives rồi XOÁ đơn khỏi bảng chính.
# 40 ngày = 1 tháng 10 ngày. Admin đổi được ở tab "File lưu trữ" (lưu ở app_settings.gdh_archive_days).
ARCHIVE_STATUSES = ('approved', 'viewed', 'ordered')
REREVIEW_HOURS = 24          # cửa hàng chỉ được nhờ duyệt lại trong vòng 24 giờ kể từ lúc admin duyệt xong
ARCHIVE_DEFAULT_DAYS = 40
_ARCHIVE_LOCK_KEY = 918273647            # khác các khoá advisory khác trong app.py
_ARCHIVE_MIN_GAP_HOURS = 20              # job nền chỉ chạy thật tối đa ~1 lần/ngày
_TYPE_SLUG = {'Định kỳ': 'Dinh-ky', 'Khẩn': 'Khan', 'Đơn 26': 'Don-26', 'Khác': 'Khac'}

# ----------------------------------------------------------------------------
# 0. BẢNG
# ----------------------------------------------------------------------------
_tables_ready = False
_tables_lock = threading.Lock()


def _ensure_tables(db):
    global _tables_ready
    if _tables_ready:
        return
    with _tables_lock:
        if _tables_ready:
            return
        cur = db.cursor()
        try:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_batches (
                    id SERIAL PRIMARY KEY,
                    store_code VARCHAR(20) NOT NULL,
                    owner VARCHAR(100) NOT NULL DEFAULT '',   -- '' = không gian chung của chi nhánh; khác '' = riêng của 1 admin
                    period_from DATE NOT NULL,
                    period_to DATE NOT NULL,
                    forecast_weeks INTEGER NOT NULL DEFAULT 3,
                    filenames TEXT,
                    uploaded_by TEXT,
                    uploaded_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\')
                )''')
            # Nâng cấp bảng cũ: thêm owner, bỏ ràng buộc UNIQUE (store_code, period_from, period_to) cũ,
            # thay bằng unique có owner (dữ liệu cũ có owner = '' nên vẫn là không gian chung của chi nhánh).
            cur.execute("ALTER TABLE gdh_batches ADD COLUMN IF NOT EXISTS owner VARCHAR(100) NOT NULL DEFAULT ''")
            cur.execute('''
                DO $$
                DECLARE c text;
                BEGIN
                  FOR c IN SELECT con.conname FROM pg_constraint con
                           WHERE con.conrelid = 'gdh_batches'::regclass AND con.contype = 'u'
                             AND (SELECT array_agg(att.attname::text ORDER BY att.attname) FROM pg_attribute att
                                  WHERE att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey))
                                 = ARRAY['period_from','period_to','store_code']
                  LOOP
                    EXECUTE 'ALTER TABLE gdh_batches DROP CONSTRAINT ' || quote_ident(c);
                  END LOOP;
                END $$''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_lines (
                    batch_id INTEGER NOT NULL REFERENCES gdh_batches(id) ON DELETE CASCADE,
                    part_code VARCHAR(100) NOT NULL,
                    part_name TEXT, unit VARCHAR(50),
                    opening NUMERIC, purchase NUMERIC,
                    out_qty NUMERIC,            -- Xuất kho (mẫu 1, gồm cả xuất chuyển kho...)
                    sold NUMERIC,               -- SL bán hàng (mẫu 2)
                    closing NUMERIC,
                    unit_cost NUMERIC,          -- giá vốn = Giá trị / Số lượng (mẫu 1)
                    adj_qty NUMERIC NOT NULL DEFAULT 0,   -- Số lượng cộng/trừ thêm
                    order_type VARCHAR(50),
                    updated_by TEXT, updated_at TIMESTAMP,
                    PRIMARY KEY (batch_id, part_code)
                )''')
            cur.execute('ALTER TABLE gdh_lines ADD COLUMN IF NOT EXISTS note TEXT')
            # Quy cách: mã CON -> mã CHA (1 mã cha = ratio mã con). Ghi đè toàn bộ mỗi lần import.
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_bundles (
                    child_code VARCHAR(100) PRIMARY KEY,
                    parent_code VARCHAR(100) NOT NULL,
                    ratio NUMERIC NOT NULL CHECK (ratio > 0)
                )''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_bundle_meta (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    filename TEXT, uploaded_by TEXT, uploaded_at TIMESTAMP,
                    total INTEGER, warnings TEXT
                )''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_gdh_lines_typed ON gdh_lines (batch_id) '
                        'WHERE order_type IS NOT NULL')
            # ---- DUYỆT ĐƠN: trạng thái + người/thời điểm của từng bước ----
            cur.execute("ALTER TABLE gdh_batches ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'draft'")
            for col, typ in (('submitted_by', 'TEXT'), ('submitted_at', 'TIMESTAMP'), ('submit_note', 'TEXT'),
                             ('claimed_by', 'TEXT'), ('claimed_at', 'TIMESTAMP'),
                             ('approved_by', 'TEXT'), ('approved_at', 'TIMESTAMP'), ('review_note', 'TEXT'),
                             ('viewed_by', 'TEXT'), ('viewed_at', 'TIMESTAMP'),
                             ('ordered_by', 'TEXT'), ('ordered_at', 'TIMESTAMP'),
                             ('rejected_by', 'TEXT'), ('rejected_at', 'TIMESTAMP'), ('reject_reason', 'TEXT'),
                             # Cửa hàng nhờ admin DUYỆT LẠI (trong REREVIEW_HOURS giờ kể từ lúc duyệt xong)
                             ('rereview_open', 'BOOLEAN NOT NULL DEFAULT FALSE'), ('rereview_count', 'INTEGER NOT NULL DEFAULT 0'),
                             ('rereview_at', 'TIMESTAMP'), ('rereview_by', 'TEXT'), ('rereview_note', 'TEXT')):
                cur.execute(f'ALTER TABLE gdh_batches ADD COLUMN IF NOT EXISTS {col} {typ}')
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gdh_batches_status ON gdh_batches (status) "
                        "WHERE owner = '' AND status <> 'draft'")
            # Mỗi (chi nhánh, chủ, kỳ) chỉ có 1 đợt NHÁP. Đơn đã đẩy đi (Chờ duyệt trở đi) được lưu trữ riêng nên
            # không chiếm chỗ: sau khi đẩy, chi nhánh import lại cùng kỳ để làm đơn mới vẫn được.
            cur.execute('DROP INDEX IF EXISTS gdh_batches_owner_uq')
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS gdh_batches_draft_uq "
                        "ON gdh_batches (store_code, owner, period_from, period_to) WHERE status = 'draft'")
            # Ảnh chụp các dòng của đơn tại 2 thời điểm: 'submitted' (lúc chi nhánh đẩy) và 'approved' (lúc admin duyệt xong).
            # Dùng để cửa hàng xem "đơn cũ -> đơn mới" và để lưu trữ lâu dài kể cả khi bảng quy cách / khoá đặt hàng đổi sau này.
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_batch_snap (
                    batch_id INTEGER NOT NULL REFERENCES gdh_batches(id) ON DELETE CASCADE,
                    stage VARCHAR(12) NOT NULL,
                    part_code VARCHAR(100) NOT NULL,
                    part_name TEXT, unit VARCHAR(50),
                    adj NUMERIC NOT NULL DEFAULT 0,
                    order_type VARCHAR(50) NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    suggest NUMERIC,
                    qty_final NUMERIC NOT NULL DEFAULT 0,
                    order_code VARCHAR(100), order_qty NUMERIC,
                    unit_cost NUMERIC, amount NUMERIC,
                    PRIMARY KEY (batch_id, stage, part_code)
                )''')
            # Kết quả admin duyệt ở menu Duyệt Đơn Hàng: SL duyệt + ghi chú của TỪNG MÃ trong đơn (tham khảo cho cửa hàng)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_batch_review (
                    batch_id INTEGER NOT NULL REFERENCES gdh_batches(id) ON DELETE CASCADE,
                    part_code VARCHAR(100) NOT NULL,
                    approved_qty NUMERIC,
                    note TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (batch_id, part_code)
                )''')
            # Nhật ký chuyển trạng thái (lưu trữ: ai làm gì lúc nào, kèm ghi chú)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_batch_events (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER NOT NULL REFERENCES gdh_batches(id) ON DELETE CASCADE,
                    at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'),
                    actor TEXT, from_status VARCHAR(20), to_status VARCHAR(20), note TEXT
                )''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_gdh_events_batch ON gdh_batch_events (batch_id, id)')
            # Nháp của admin khi đang duyệt dở (SL duyệt + ghi chú từng mã): lưu trên máy chủ để đổi máy / xoá cache không mất.
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_review_draft (
                    batch_id INTEGER NOT NULL REFERENCES gdh_batches(id) ON DELETE CASCADE,
                    part_code VARCHAR(100) NOT NULL,
                    approved_qty NUMERIC,
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'),
                    PRIMARY KEY (batch_id, part_code)
                )''')
            # File Excel lưu trữ của đơn đã xoá (mỗi đơn x loại đơn 1 dòng). Thư mục khi tải về: <chi nhánh>/<loại đơn>/<file>.
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_archives (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER, store_code VARCHAR(20) NOT NULL, order_type VARCHAR(50) NOT NULL,
                    order_name TEXT, status VARCHAR(20),
                    period_from DATE, period_to DATE,
                    submitted_at TIMESTAMP, approved_at TIMESTAMP, ordered_at TIMESTAMP,
                    parts INTEGER, qty NUMERIC, amount NUMERIC,
                    filename TEXT NOT NULL, file_data BYTEA NOT NULL,
                    archived_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'),
                    downloaded_at TIMESTAMP, downloaded_by TEXT
                )''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_gdh_archives_sto ON gdh_archives (store_code, order_type, archived_at DESC)')
            # Cờ "chi nhánh đã tải về máy" - TÁCH RIÊNG với downloaded_at của admin, để 2 bên không đè trạng thái của nhau.
            cur.execute('ALTER TABLE gdh_archives ADD COLUMN IF NOT EXISTS store_downloaded_at TIMESTAMP')
            cur.execute('ALTER TABLE gdh_archives ADD COLUMN IF NOT EXISTS store_downloaded_by TEXT')
            # ---- ĐƠN KHẨN TỪ DANH SÁCH KHÁCH HÀNG: đơn tạo nhanh từ bảng bo_orders, đẩy thẳng sang admin (Chờ duyệt) ----
            cur.execute("ALTER TABLE gdh_batches ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'regular'")
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_urgent_lines (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER NOT NULL REFERENCES gdh_batches(id) ON DELETE CASCADE,
                    bo_order_id INTEGER NOT NULL,          -- id dòng trong bo_orders (CSDL Supabase)
                    request_id VARCHAR(40),
                    seq_no INTEGER,                         -- STT yêu cầu của khách (theo chi nhánh)
                    part_code VARCHAR(100),                 -- mã khách yêu cầu
                    part_name TEXT,
                    qty NUMERIC,                            -- SL khách yêu cầu
                    order_code VARCHAR(100),                -- MÃ ĐẶT (đã quy mã thay thế / mã cha)
                    order_qty NUMERIC,                      -- SL ĐẶT (đã quy ra đơn vị mã đặt)
                    customer_request_date DATE,
                    customer_name VARCHAR(255),
                    quote_no VARCHAR(50),
                    vehicle_type VARCHAR(100),
                    frame_number VARCHAR(50),
                    UNIQUE (batch_id, bo_order_id)
                )''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_gdh_urgent_batch ON gdh_urgent_lines (batch_id, seq_no)')
            # ---- LỊCH SỬ TỪ CHỐI: mỗi lần admin từ chối 1 dòng (không gắn khoá ngoại để đơn khẩn bị xoá / đơn bị chi nhánh xoá vẫn còn lịch sử) ----
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_rejections (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER, store_code VARCHAR(20) NOT NULL, kind VARCHAR(20) NOT NULL DEFAULT 'regular',
                    order_name TEXT, period_from DATE, period_to DATE,
                    parts INTEGER, qty NUMERIC,
                    submitted_by TEXT, submitted_at TIMESTAMP,
                    rejected_by TEXT, rejected_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), reason TEXT
                )''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_gdh_rejections_sto ON gdh_rejections (store_code, rejected_at DESC)')
            # GIỜ VIỆT NAM: các cột TIMESTAMP (không múi giờ) lưu đúng giờ Việt Nam bất kể múi giờ cấu hình của Postgres
            # (thường là UTC -> trước đây NOW() ghi lệch -7 giờ). Bảng cũ vẫn giữ DEFAULT NOW() cũ nên đặt lại ở đây.
            for _t, _c in (('gdh_batches', 'uploaded_at'), ('gdh_batch_events', 'at'), ('gdh_review_draft', 'updated_at'),
                           ('gdh_archives', 'archived_at'), ('gdh_rejections', 'rejected_at')):
                cur.execute(f"ALTER TABLE {_t} ALTER COLUMN {_c} SET DEFAULT (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')")
            db.commit()
        finally:
            cur.close()
        _tables_ready = True


# ----------------------------------------------------------------------------
# 1. ĐỌC FILE "TỔNG HỢP TỒN KHO" (2 mẫu)
# ----------------------------------------------------------------------------
def _norm(s):
    return re.sub(r'\s+', ' ', str(s if s is not None else '')).strip().lower()


def _num(v, default=None):
    if v is None:
        return default
    if isinstance(v, str):
        v = v.strip().replace(',', '')
        if v in ('', '-', 'nan'):
            return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def _read_raw(file_storage):
    import pandas as pd
    file_storage.seek(0)
    name = (getattr(file_storage, 'filename', '') or '').lower()
    if name.endswith('.csv'):
        return pd.read_csv(file_storage, header=None, dtype=object)
    try:                                   # nếu đã cài python-calamine thì đọc Excel nhanh hơn nhiều; không có / lỗi thì dùng cách cũ
        import importlib.util
        if importlib.util.find_spec('python_calamine') is not None:
            return pd.read_excel(file_storage, header=None, dtype=object, engine='calamine')
    except Exception:
        file_storage.seek(0)
    return pd.read_excel(file_storage, header=None, dtype=object)


def _detect_period(raw, today):
    """Đọc kỳ từ vài dòng đầu file. Trả về (từ, đến, ghi chú) hoặc (None, None, None)."""
    parts = []
    for i in range(min(6, len(raw))):
        for x in raw.iloc[i].tolist():
            if x is not None and str(x).lower() != 'nan':
                parts.append(str(x))
    text = ' '.join(parts)
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4}).{0,40}?(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if m:
        d1, m1, y1, d2, m2, y2 = (int(x) for x in m.groups())
        try:
            return date(y1, m1, d1), date(y2, m2, d2), 'Đọc từ file (từ ngày - đến ngày)'
        except ValueError:
            pass
    m = re.search(r'tháng\s*(\d{1,2})\s*(?:năm|/|-)?\s*(\d{4})', text.lower())
    if m:
        mo, yr = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            first, last = date(yr, mo, 1), date(yr, mo, calendar.monthrange(yr, mo)[1])
            if first <= today <= last:          # tháng đang chạy: chỉ tính đến hôm nay
                return first, today, f'File ghi "Tháng {mo}/{yr}" - tháng đang chạy nên tính đến hôm nay'
            return first, last, f'File ghi "Tháng {mo}/{yr}" - tính cả tháng'
    return None, None, None


def _unit_cost(op, opv, buy, buyv, out, outv, cl, clv):
    for q, v in ((cl, clv), (buy, buyv), (out, outv), (op, opv)):
        if q and q > 0 and v is not None and v > 0:
            return v / q
    return None


def parse_stock_file(file_storage, today=None):
    """Trả về dict: template ('1' có giá trị | '2' SL bán hàng), rows{part: {...}}, period(from,to,note)."""
    today = today or datetime.now(_VN_TZ).date()
    raw = _read_raw(file_storage)
    hdr = next((i for i in range(min(20, len(raw)))
                if any(_norm(x) == 'mã hàng' for x in raw.iloc[i].tolist())), None)
    if hdr is None:
        raise ValueError('Không tìm thấy dòng tiêu đề "Mã hàng" - đây có đúng là file "Tổng hợp tồn kho" không?')
    ncol = raw.shape[1]
    top = raw.iloc[hdr].tolist()
    c_code = next(c for c, v in enumerate(top) if _norm(v) == 'mã hàng')
    nxt = raw.iloc[hdr + 1].tolist() if hdr + 1 < len(raw) else [None] * ncol
    has_sub = _norm(nxt[c_code]) in ('', 'nan') and any(
        k in _norm(x) for x in nxt for k in ('số lượng', 'giá trị', 'sl '))
    sub = nxt if has_sub else [None] * ncol
    first = hdr + (2 if has_sub else 1)

    group, cur_g = [], ''
    for c in range(ncol):
        g = _norm(top[c])
        if g and g != 'nan':
            cur_g = g
        group.append(cur_g)

    def cols_of(prefix):
        return [c for c in range(ncol) if group[c].startswith(prefix)]

    def pick(prefix):
        """(cột số lượng, cột giá trị) của 1 nhóm (Đầu kỳ / Nhập kho / ...)."""
        cs = cols_of(prefix)
        val = next((c for c in cs if 'giá trị' in _norm(sub[c])), None)
        qty_c = [c for c in cs if c != val]
        qty = next((c for c in qty_c if 'số lượng' in _norm(sub[c]) or _norm(sub[c]).startswith('sl')),
                   qty_c[0] if qty_c else None)
        return qty, val

    c_name = next((c for c, v in enumerate(top) if _norm(v) == 'tên hàng'), None)
    c_unit = next((c for c, v in enumerate(top) if _norm(v) in ('đvt', 'đơn vị tính')), None)
    c_kho = next((c for c, v in enumerate(top) if _norm(v) == 'mã kho'), None)
    q_open, v_open = pick('đầu kỳ')
    q_buy, v_buy = pick('nhập kho')
    q_out, v_out = pick('xuất kho')
    q_close, v_close = pick('cuối kỳ')
    if q_out is None or q_close is None:
        raise ValueError('Không tìm thấy cột "Xuất kho" hoặc "Cuối kỳ" trong file.')
    is_sold_col = 'bán' in _norm(sub[q_out])          # mẫu 2: "SL bán hàng"
    template = '2' if is_sold_col else '1'

    def cell(r, c):
        return r[c] if c is not None and c < len(r) else None

    acc = {}
    all_rows = raw.values.tolist()          # 1 lần cho cả bảng (raw.iloc[i] từng dòng rất chậm với file nhiều chục nghìn dòng)
    for i in range(first, len(all_rows)):
        r = all_rows[i]
        code = str(cell(r, c_code)).strip() if cell(r, c_code) is not None else ''
        if not code or code.lower() == 'nan' or _norm(code).startswith(('tổng', 'mã hàng')):
            continue
        if _norm(cell(r, c_kho)).startswith('tổng'):
            continue
        a = acc.setdefault(code, {'name': None, 'unit': None, 'v': [0.0] * 8})
        if a['name'] is None and c_name is not None and _norm(cell(r, c_name)) not in ('', 'nan'):
            a['name'] = str(cell(r, c_name)).strip()
        if a['unit'] is None and c_unit is not None and _norm(cell(r, c_unit)) not in ('', 'nan'):
            a['unit'] = str(cell(r, c_unit)).strip()
        for k, c in enumerate((q_open, v_open, q_buy, v_buy, q_out, v_out, q_close, v_close)):
            a['v'][k] += _num(cell(r, c), 0.0) if c is not None else 0.0     # gộp nhiều kho

    rows = {}
    for code, a in acc.items():
        op, opv, buy, buyv, out, outv, cl, clv = a['v']
        rows[code] = {
            'name': a['name'], 'unit': a['unit'], 'opening': op, 'purchase': buy, 'closing': cl,
            'out_qty': None if is_sold_col else out,
            'sold': out if is_sold_col else None,
            'cost': _unit_cost(op, opv, buy, buyv, out, outv, cl, clv) if v_close is not None else None,
        }
    pf, pt, note = _detect_period(raw, today)
    return {'template': template, 'rows': rows, 'period': (pf, pt, note)}


def parse_bundle_file(file_storage):
    """File quy cách: 3 cột Mã cha | Mã con | Số lượng quy đổi (1 mã cha = N mã con).
    Trả về (map {mã con: (mã cha, N)}, danh sách cảnh báo)."""
    import pandas as pd
    file_storage.seek(0)
    name = (getattr(file_storage, 'filename', '') or '').lower()
    df = pd.read_csv(file_storage, dtype=object) if name.endswith('.csv') else pd.read_excel(file_storage, dtype=object)
    cols = {c: _norm(c) for c in df.columns}
    c_par = next((c for c, n in cols.items() if n == 'mã cha' or 'cha' in n.split()), None)
    c_chi = next((c for c, n in cols.items() if n == 'mã con' or 'con' in n.split()), None)
    c_qty = next((c for c, n in cols.items() if 'quy đổi' in n or 'quy doi' in n or 'quy cách' in n), None)
    if not (c_par and c_chi and c_qty):
        raise ValueError('Không tìm thấy 3 cột "Mã cha", "Mã con", "Số lượng quy đổi" trong file.')
    warns, mp = [], {}
    for _, r in df.iterrows():
        par = str(r[c_par]).strip() if r[c_par] is not None else ''
        chi = str(r[c_chi]).strip() if r[c_chi] is not None else ''
        q = _num(r[c_qty])
        if not par or not chi or par.lower() == 'nan' or chi.lower() == 'nan':
            continue
        if q is None or q <= 0:
            warns.append(f'Bỏ qua {chi}: số lượng quy đổi không hợp lệ.')
            continue
        if par == chi:
            warns.append(f'Bỏ qua {chi}: mã cha trùng mã con.')
            continue
        if chi in mp:
            warns.append(f'Mã con {chi} xuất hiện nhiều lần - lấy dòng cuối.')
        mp[chi] = (par, q)
    # mã cha <-> con tréo nhau (A là con của B và B là con của A): không biết bên nào là cha -> bỏ cả hai để khỏi quy đổi sai
    loops = sorted(c for c, (p, _) in mp.items() if p in mp and mp[p][0] == c)
    if loops:
        for c in loops:
            mp.pop(c, None)
        warns.append('Bỏ qua các mã bị khai báo cha-con tréo nhau (không xác định được mã cha, hãy sửa file): '
                     + ', '.join(loops))
    return mp, warns


def _bkey(code):
    return str(code or '').strip().upper()


def _load_bundles(cur, codes=None):
    """{MÃ CON (viết hoa) -> (mã cha, số quy đổi)}. Bảng nhỏ nên nạp hết, so khớp không phân biệt hoa/thường."""
    cur.execute('SELECT child_code, parent_code, ratio FROM gdh_bundles')
    return {_bkey(r['child_code']): (r['parent_code'], float(r['ratio'])) for r in cur.fetchall()}


# ----------------------------------------------------------------------------
# 2. TÍNH BẢNG GÔM
# ----------------------------------------------------------------------------
def period_metrics(pf, pt):
    days = max(1, (pt - pf).days + 1)
    return days, max(1, days // 7), days / DAYS_PER_MONTH


def _f(v):
    return None if v is None else float(v)


def compute_line(ln, weeks, months, forecast_weeks):
    """Tính 1 dòng từ số liệu thô. ln: dict có opening/purchase/out_qty/sold/closing/adj_qty."""
    sold, out_qty = _f(ln.get('sold')), _f(ln.get('out_qty'))
    sales = sold if sold is not None else (out_qty or 0.0)
    closing = _f(ln.get('closing')) or 0.0
    stock = max(closing, 0.0)
    if closing <= 0:
        group = 'HET'
    else:
        c = classify_sales_frequency(closing, sales, months)
        group = c['code'] if c else 'HET'
    avg_month = sales / months if months else 0.0
    avg_week = 0 if group == 'CB' or sales <= 0 else math.floor(sales / weeks * 10 + 0.5 + 1e-9) / 10   # 1 chữ số thập phân, KHÔNG làm tròn lên
    suggest = max(0, int(math.ceil(avg_week * forecast_weeks - stock - 1e-9)))
    adj = _f(ln.get('adj_qty')) or 0.0
    qty_final = max(0, int(round(suggest + adj)))
    return {'sales': sales, 'group': group, 'avg_month': round(avg_month, 1), 'avg_week': avg_week,
            'suggest': suggest, 'adj': adj, 'qty_final': qty_final}


def compute_rows(cur, batch, order_only=False, with_extra=False):
    """Toàn bộ dòng của 1 đợt gôm, đã tính đề xuất / SL cuối / thành tiền."""
    days, weeks, months = period_metrics(batch['period_from'], batch['period_to'])
    fw = int(batch['forecast_weeks'] or DEFAULT_FORECAST_WEEKS)
    sql = 'SELECT * FROM gdh_lines WHERE batch_id = %s'
    if order_only:
        sql += ' AND order_type IS NOT NULL'
    cur.execute(sql + ' ORDER BY part_code', (batch['id'],))
    lines = [l for l in cur.fetchall() if not _is_excluded_from_reorder(l['part_code'])]
    codes = [l['part_code'] for l in lines]
    locks = {}
    if codes:
        cur.execute('SELECT part_code, is_locked, replacement_code FROM order_lock_items WHERE part_code = ANY(%s)',
                    (codes,))
        for r in cur.fetchall():
            locks[r['part_code']] = (bool(r['is_locked']), (r['replacement_code'] or '').strip() or None)

    # Tồn kho HỆ THỐNG (bảng inventory_items) của TẤT CẢ chi nhánh + quy cách mã con -> mã cha (chỉ khi with_extra)
    stock_map, has_inv = {}, False
    bundles = _load_bundles(cur) if codes else {}      # luôn nạp: thành tiền của mã có mã cha tính theo giá vốn mã cha
    if with_extra and codes:
        cur.execute('SELECT 1 FROM inventory_items WHERE store_code = %s LIMIT 1', (batch['store_code'],))
        has_inv = cur.fetchone() is not None
        cur.execute('SELECT part_code, store_code, SUM(quantity) AS q FROM inventory_items '
                    'WHERE part_code = ANY(%s) GROUP BY part_code, store_code', (codes,))
        for r in cur.fetchall():
            stock_map.setdefault(r['part_code'], {})[r['store_code']] = float(r['q'] or 0)

    # Hàng NỢ / ĐANG VẬN CHUYỂN theo bảng đối soát PO của chi nhánh đang gôm (chỉ để HIỂN THỊ, không trừ vào đề xuất).
    # Dùng lại cache của bảng đối soát nên không tính lại; lỗi thì bỏ qua (cột hiện "–"), không làm hỏng bảng gôm.
    po_map = None
    if with_extra and codes:
        try:
            version = get_store_data_version(cur, batch['store_code'])
            po_map = {}
            for d in compute_result_for_store_cached(cur, batch['store_code'], version):
                st, pc = d.get('status'), d.get('part_code')
                if st not in ('Nợ', 'Đang vận chuyển') or not pc:
                    continue
                slot = po_map.setdefault(pc, [0.0, 0.0])
                slot[0 if st == 'Nợ' else 1] += float(d.get('qty_debt') or 0)
        except Exception:
            cur.connection.rollback()
            po_map = None

    rows = []
    for l in lines:
        c = compute_line(l, weeks, months, fw)
        locked, rep = locks.get(l['part_code'], (False, None))
        rows.append({
            'part_code': l['part_code'], 'part_name': l['part_name'], 'unit': l['unit'],
            'locked': locked, 'lock_replace': rep,
            'group': c['group'], 'avg_month': c['avg_month'], 'avg_week': c['avg_week'],
            'opening': _f(l['opening']), 'purchase': _f(l['purchase']), 'out_qty': _f(l['out_qty']),
            'sold': _f(l['sold']), 'sales': c['sales'], 'closing': _f(l['closing']),
            'suggest': c['suggest'], 'adj': c['adj'], 'qty_final': c['qty_final'],
            'order_type': l['order_type'] or '', 'cost': _f(l['unit_cost']), 'amount': None,
            'note': l.get('note') or '',
            'sys_stock': ((stock_map.get(l['part_code']) or {}).get(batch['store_code'], 0.0) if has_inv else None),
            'stock_by_store': {k: v for k, v in (stock_map.get(l['part_code']) or {}).items()},
        })
    need_cost = [r['part_code'] for r in rows if r['cost'] is None]      # mọi dòng (kể cả SL 0): người dùng có thể cộng thêm số lượng ngay trên bảng
    if need_cost:      # thiếu giá vốn trong file (mẫu 2) -> lấy giá nhập trong DB
        cur.execute('SELECT part_code, gia_nhap FROM part_vehicle_models WHERE UPPER(TRIM(part_code)) = ANY(%s) '
                    'AND gia_nhap > 0', ([_bkey(c) for c in need_cost],))     # không phân biệt hoa/thường, khoảng trắng
        fb = {_bkey(r['part_code']): float(r['gia_nhap']) for r in cur.fetchall()}
        for r in rows:
            if r['cost'] is None:
                r['cost'] = fb.get(_bkey(r['part_code']))
    # Giá vốn MÃ CHA (mã có quy cách): ưu tiên giá vốn trong file import của đợt này, thiếu thì lấy gia_nhap trong DB
    parent_cost = {}
    parents = {_bkey((bundles.get(_bkey(r['lock_replace'] if r['locked'] and r['lock_replace'] else r['part_code'])) or [None])[0])
               for r in rows}                    # mọi dòng (kể cả SL 0), để cộng/trừ tay trên bảng vẫn ra tiền
    parents.discard('')
    if parents:
        cur.execute('SELECT part_code, unit_cost FROM gdh_lines WHERE batch_id = %s AND unit_cost IS NOT NULL '
                    'AND UPPER(TRIM(part_code)) = ANY(%s)', (batch['id'], list(parents)))
        for x in cur.fetchall():
            parent_cost[_bkey(x['part_code'])] = float(x['unit_cost'])
        miss = [c for c in parents if c not in parent_cost]
        if miss:
            cur.execute('SELECT part_code, gia_nhap FROM part_vehicle_models WHERE UPPER(TRIM(part_code)) = ANY(%s) '
                        'AND gia_nhap > 0', (miss,))
            for x in cur.fetchall():
                parent_cost.setdefault(_bkey(x['part_code']), float(x['gia_nhap']))
    for r in rows:
        base = (r['lock_replace'] if r['locked'] and r['lock_replace'] else r['part_code'])
        r['hvn_part'] = base
        b = bundles.get(_bkey(base))             # mã con -> tự quy thành mã cha (làm tròn LÊN)
        r['bundle_parent'] = b[0] if b else None
        r['bundle_ratio'] = b[1] if b else None
        r['suggest_child'] = r['suggest']        # đề xuất tính theo MÃ CON (giữ lại để hiển thị / tham khảo)
        if b:                                    # có mã cha: quy đề xuất ra MÃ CHA (làm tròn LÊN); SL cuối cũng tính bằng mã cha
            r['suggest'] = int(math.ceil(r['suggest_child'] / b[1] - 1e-9))
            r['qty_final'] = max(0, int(round(r['suggest'] + r['adj'])))
        # Thành tiền = giá vốn x SL CUỐI. Có mã cha: giá vốn của MÃ CHA x SL cuối (SL cuối đã quy ra mã cha ở trên); không thì giá vốn mã đó.
        if b:
            r['ord_cost'] = parent_cost.get(_bkey(b[0]))
            r['cost_code'] = b[0]                # mã đã dùng để tra giá vốn (hiện ở giao diện khi thiếu giá)
        else:
            r['ord_cost'] = r['cost']
            r['cost_code'] = r['part_code']
        r['amount'] = None if r['ord_cost'] is None else r['ord_cost'] * r['qty_final']
        r['debt_qty'] = r['ship_qty'] = r['parent_debt'] = r['parent_ship'] = None
        if po_map is not None:
            own = po_map.get(r['part_code']) or [0.0, 0.0]
            r['debt_qty'], r['ship_qty'] = own
            if b:                                # PO đặt bằng mã CHA: trả về số của mã cha để nhìn thấy
                par = po_map.get(b[0]) or [0.0, 0.0]
                r['parent_debt'], r['parent_ship'] = par
        r['order_code'] = b[0] if b else base
        r['order_qty'] = r['qty_final']          # SL cuối đã là đơn vị của mã đặt (mã cha nếu có quy cách)
    return rows


def _totals(rows):
    by_type = {t: {'parts': 0, 'qty': 0, 'amount': 0.0} for t in ORDER_TYPES}
    unassigned = locked_no_rep = 0
    to_order = 0
    for r in rows:
        if r['qty_final'] <= 0:
            continue
        to_order += 1
        if r['order_type'] in by_type:
            t = by_type[r['order_type']]
            t['parts'] += 1
            t['qty'] += r['qty_final']
            t['amount'] += r['amount'] or 0.0
        else:
            unassigned += 1
        if r['locked'] and not r['lock_replace']:
            locked_no_rep += 1
    freq = {g: sum(1 for r in rows if r['group'] == g) for g in GROUP_LABELS}
    no_cost = [r['cost_code'] for r in rows if r['qty_final'] > 0 and r['amount'] is None]
    return {'no_cost': len(no_cost), 'no_cost_samples': no_cost[:8], 'to_order': to_order, 'by_type': by_type, 'unassigned_type': unassigned,
            'locked_no_replace': locked_no_rep, 'freq': freq, 'total_parts': len(rows)}


# ----------------------------------------------------------------------------
# 3. TIỆN ÍCH API
# ----------------------------------------------------------------------------
def _actor_name():
    return session.get('full_name') or session.get('user')


_NAME_CACHE = {}          # username -> (họ tên, thời điểm lấy)
_NAME_CACHE_TTL = 60      # giây


def _display_name(username):
    """Họ tên nhân viên (cột users.full_name) của 1 tên đăng nhập; chưa đặt họ tên / không tra được thì dùng lại
    chính tên đăng nhập. CHỈ để HIỂN THỊ - các cột như claimed_by vẫn lưu tên đăng nhập để so sánh quyền."""
    import time
    u = str(username or '')
    if not u:
        return ''
    hit = _NAME_CACHE.get(u)
    if hit and time.time() - hit[1] < _NAME_CACHE_TTL:
        return hit[0]
    name = u
    try:
        cur = get_db().cursor()
        try:
            cur.execute('SELECT full_name FROM users WHERE username = %s', (u,))
            row = cur.fetchone()
            if row and (row['full_name'] or '').strip():
                name = row['full_name'].strip()
        finally:
            cur.close()
    except Exception:
        pass
    _NAME_CACHE[u] = (name, time.time())
    return name


def _resolve_store(cur, store_arg, allow_all=False):
    """(store_code, None) hoặc (None, (response, code)). User cửa hàng luôn bị khoá vào chi nhánh của mình.
    allow_all: admin được để trống = tất cả chi nhánh (trả về '')."""
    role = session.get('role')
    if 'user' not in session or role not in ('admin', 'store'):
        return None, (jsonify({'error': 'Forbidden'}), 403)
    if role == 'store':
        return session.get('store_code'), None
    code = (store_arg or '').strip().upper()
    if not code:
        if allow_all:
            return '', None
        return None, (jsonify({'error': 'Vui lòng chọn chi nhánh.'}), 400)
    if code not in _valid_store_codes(cur):
        return None, (jsonify({'error': 'Chi nhánh không hợp lệ.'}), 400)
    return code, None


def _owner_for(space):
    """Chủ của đợt gôm đang thao tác. User cửa hàng luôn ở không gian chung ('').
    Admin mặc định ở không gian RIÊNG của mình (owner = username); space='store' để xem/sửa không gian chung của chi nhánh."""
    if session.get('role') != 'admin':
        return ''
    if (space or '').strip().lower() == 'store':
        return ''
    return str(session.get('user') or '')


def _ctx():
    db = get_db()
    _ensure_tables(db)
    return db, db.cursor()


def _get_batch(cur, batch_id):
    """(batch, None) hoặc (None, (response, code)); user cửa hàng chỉ mở được đợt của chi nhánh mình."""
    role = session.get('role')
    if 'user' not in session or role not in ('admin', 'store'):
        return None, (jsonify({'error': 'Forbidden'}), 403)
    try:
        bid = int(batch_id)
    except (TypeError, ValueError):
        return None, (jsonify({'error': 'Đợt gôm không hợp lệ.'}), 400)
    cur.execute('SELECT * FROM gdh_batches WHERE id = %s', (bid,))
    b = cur.fetchone()
    if not b:
        return None, (jsonify({'error': 'Không tìm thấy đợt gôm.'}), 404)
    if role == 'store' and (b['store_code'] != session.get('store_code') or b['owner']):
        return None, (jsonify({'error': 'Forbidden'}), 403)
    if role == 'admin' and b['owner'] and b['owner'] != str(session.get('user') or ''):
        return None, (jsonify({'error': 'Đợt gôm này là của admin khác.'}), 403)      # không gian riêng của admin khác
    return b, None


def _fmt_dt(d):
    return d.strftime('%d/%m/%Y %H:%M') if d else None


def _batch_json(b, extra=None):
    days, weeks, months = period_metrics(b['period_from'], b['period_to'])
    out = {'id': b['id'], 'store': b['store_code'], 'kind': b.get('kind') or 'regular',
           'space': 'mine' if b['owner'] else 'store', 'from': b['period_from'].isoformat(),
           'to': b['period_to'].isoformat(), 'days': days, 'weeks': weeks, 'months': round(months, 2),
           'forecast_weeks': b['forecast_weeks'], 'filenames': b['filenames'],
           'uploaded_by': b['uploaded_by'], 'uploaded_at': _fmt_dt(b['uploaded_at'])}
    st = b.get('status') or 'draft'
    out.update({
        'status': st, 'status_label': STATUS_LABELS.get(st, st),
        'submitted_by': b.get('submitted_by'), 'submitted_at': _fmt_dt(b.get('submitted_at')), 'submit_note': b.get('submit_note') or '',
        'claimed_by': b.get('claimed_by'), 'claimed_by_name': _display_name(b.get('claimed_by')),
        'claimed_at': _fmt_dt(b.get('claimed_at')),
        'claimed_by_me': bool(b.get('claimed_by')) and b.get('claimed_by') == str(session.get('user') or ''),
        'approved_by': b.get('approved_by'), 'approved_at': _fmt_dt(b.get('approved_at')), 'review_note': b.get('review_note') or '',
        'viewed_by': b.get('viewed_by'), 'viewed_at': _fmt_dt(b.get('viewed_at')),
        'ordered_by': b.get('ordered_by'), 'ordered_at': _fmt_dt(b.get('ordered_at')),
        'rejected_by': b.get('rejected_by'), 'rejected_at': _fmt_dt(b.get('rejected_at')), 'reject_reason': b.get('reject_reason') or '',
        'can_edit': _can_edit(b),
    })
    # Nhờ duyệt lại: còn được nhờ không (đơn Đã duyệt / Đã xem, trong 24 giờ kể từ approved_at; Đã đặt thì không)
    ap = b.get('approved_at')
    until = (ap + timedelta(hours=REREVIEW_HOURS)) if ap else None
    left = (until - datetime.now(_VN_TZ).replace(tzinfo=None)).total_seconds() / 60 if until else None
    can_re = bool(not b.get('owner') and st in ('approved', 'viewed') and left is not None and left > 0)
    out.update({
        'rereview_open': bool(b.get('rereview_open')) and st in ('pending', 'reviewing'),
        'rereview_count': int(b.get('rereview_count') or 0),
        'rereview_at': _fmt_dt(b.get('rereview_at')), 'rereview_by': b.get('rereview_by'),
        'rereview_note': b.get('rereview_note') or '',
        'can_rereview': can_re, 'rereview_until': _fmt_dt(until) if can_re else None,
        'rereview_left_min': int(left) if can_re else None,
    })
    if extra:
        out.update(extra)
    return out


_TYPE_WORD = {'Định kỳ': 'định kỳ', 'Khẩn': 'khẩn', 'Đơn 26': '26'}


def _order_name(b, types=None):
    """Tên đơn đã đẩy: 'Đơn hàng định kỳ NS3 ngày 02/10/2026' (chỉ lấy ngày đẩy, không kèm kỳ số bán)."""
    ts = [t for t in ORDER_TYPES if t in (types or [])]
    mid = (' ' + _TYPE_WORD[ts[0]]) if len(ts) == 1 else ''
    d = b.get('submitted_at')
    return f"Đơn hàng{mid} {b['store_code']}" + (f" ngày {d:%d/%m/%Y}" if d else '')


def _kinds(rows):
    """Các loại đơn đang có ở những mã SL cuối > 0 (theo thứ tự chuẩn). Quy tắc: mỗi đơn chỉ được gôm 1 loại đơn."""
    have = {r['order_type'] for r in rows if r['qty_final'] > 0 and r['order_type'] in ORDER_TYPES}
    return [t for t in ORDER_TYPES if t in have]


def _mixed_msg(kinds):
    return ('Mỗi đơn hàng chỉ được gôm 1 loại đơn, nhưng đơn này đang có: ' + ', '.join(kinds) +
            '. Hãy chọn lại cùng một loại cho cả đơn (ô "Loại đơn của đơn này"), mã khác loại thì để SL cuối = 0 rồi làm đơn riêng.')


def _can_edit(b):
    """Đợt gôm có được sửa lúc này không. Đơn riêng của admin: luôn được. Đơn chung của chi nhánh: chỉ khi còn Nháp.
    Từ lúc đẩy đi, đơn bị khoá; admin duyệt ở menu Duyệt Đơn Hàng (không sửa trong bảng gôm)."""
    if b.get('owner'):
        return True
    return (b.get('status') or 'draft') == 'draft'


def _edit_block(b):
    """None nếu được sửa; ngược lại trả (response, 409) kèm lý do theo trạng thái."""
    if _can_edit(b):
        return None
    st = b.get('status') or 'draft'
    if st == 'reviewing':
        who = _display_name(b.get('claimed_by')) or 'admin'
        msg = f'Đơn đang được {who} duyệt nên đã khoá, không sửa được.'
    elif st == 'pending':
        msg = 'Đơn đã đẩy cho admin (Chờ duyệt) nên đã khoá. Muốn sửa, hãy Thu hồi đơn trước.'
    else:
        msg = f'Đơn đang ở trạng thái "{STATUS_LABELS.get(st, st)}" nên đã khoá và được lưu trữ, không sửa được nữa.'
    return jsonify({'error': msg, 'status': st}), 409


def _log_event(cur, bid, frm, to, note=''):
    cur.execute('INSERT INTO gdh_batch_events (batch_id, actor, from_status, to_status, note) VALUES (%s,%s,%s,%s,%s)',
                (bid, _actor_name(), frm, to, (note or '')[:500]))


def _find_draft(cur, batch, exclude_id=None):
    """Đợt NHÁP (đơn thường) đang có của cùng chi nhánh + cùng kỳ số bán, khoá dòng để tránh 2 người thao tác cùng lúc."""
    cur.execute("""SELECT id FROM gdh_batches WHERE store_code = %s AND owner = '' AND period_from = %s AND period_to = %s
                     AND status = 'draft' AND COALESCE(kind, 'regular') = 'regular' AND id <> %s LIMIT 1 FOR UPDATE""",
                (batch['store_code'], batch['period_from'], batch['period_to'], exclude_id or batch['id']))
    return cur.fetchone()


def _merge_lines_back(cur, src_id, dst_id):
    """Trả các mã ĐÃ GẮN LOẠI ĐƠN của đợt src (đơn đã đẩy) về đợt Nháp dst. Mã đã có ở dst: ghi đè +/- thêm, loại đơn, ghi chú.
    (Dòng chỉ chứa giá vốn mã cha, không gắn loại đơn, không cần trả về.)"""
    cur.execute('''
        INSERT INTO gdh_lines (batch_id, part_code, part_name, unit, opening, purchase, out_qty, sold, closing, unit_cost,
                               adj_qty, order_type, note, updated_by, updated_at)
        SELECT %s, part_code, part_name, unit, opening, purchase, out_qty, sold, closing, unit_cost,
               adj_qty, order_type, note, %s, (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\')
        FROM gdh_lines WHERE batch_id = %s AND order_type IS NOT NULL
        ON CONFLICT (batch_id, part_code) DO UPDATE SET
            adj_qty = EXCLUDED.adj_qty, order_type = EXCLUDED.order_type, note = EXCLUDED.note,
            updated_by = EXCLUDED.updated_by, updated_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\')''', (dst_id, _actor_name(), src_id))
    return cur.rowcount


def _parse_date(s):
    s = (s or '').strip()
    return datetime.strptime(s, '%Y-%m-%d').date() if s else None


# ----------------------------------------------------------------------------
# 4. API: ĐỢT GÔM, IMPORT, BẢNG, LƯU
# ----------------------------------------------------------------------------
@gom_don_hang_bp.route('/api/gom-don-hang/batches', methods=['GET'])
def gdh_batches():
    db, cur = _ctx()
    try:
        store, err = _resolve_store(cur, request.args.get('store'))
        if err:
            return err
        cur.execute('''
            SELECT b.*,
                   (SELECT COUNT(*) FROM gdh_lines l WHERE l.batch_id = b.id AND NOT (l.part_code LIKE ANY(%s))) AS total_parts,
                   (SELECT COUNT(*) FROM gdh_lines l WHERE l.batch_id = b.id AND l.order_type IS NOT NULL AND NOT (l.part_code LIKE ANY(%s))) AS typed_parts,
                   EXISTS (SELECT 1 FROM gdh_lines l WHERE l.batch_id = b.id AND l.sold IS NOT NULL AND NOT (l.part_code LIKE ANY(%s))) AS has_sold
            FROM gdh_batches b WHERE b.store_code = %s AND b.owner = %s
              AND (b.owner <> '' OR b.status = 'draft')
            ORDER BY b.period_to DESC, b.id DESC''',
                    (_EXCL_LIKE, _EXCL_LIKE, _EXCL_LIKE, store, _owner_for(request.args.get('space'))))
        data = [_batch_json(b, {'total_parts': b['total_parts'], 'typed_parts': b['typed_parts'],
                                'has_sold': bool(b['has_sold'])}) for b in cur.fetchall()]
    finally:
        cur.close()
    return jsonify({'success': True, 'store': store, 'data': data, 'order_types': ORDER_TYPES})


@gom_don_hang_bp.route('/api/gom-don-hang/import', methods=['POST'])
def gdh_import():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Vui lòng chọn file Tổng hợp tồn kho.'}), 400
    _t0 = time.perf_counter()
    try:
        parsed = parse_stock_file(f)
    except Exception as e:
        return jsonify({'error': f'Không đọc được file: {e}'}), 400
    _t_parse = time.perf_counter() - _t0
    rows = {c: r for c, r in parsed['rows'].items() if not _is_excluded_from_reorder(c)}
    if not rows:
        return jsonify({'error': 'File không có dòng dữ liệu hợp lệ.'}), 400
    pf, pt, note = parsed['period']
    try:
        man_from, man_to = _parse_date(request.form.get('period_from')), _parse_date(request.form.get('period_to'))
    except ValueError:
        return jsonify({'error': 'Ngày kỳ báo cáo không hợp lệ.'}), 400
    is_admin = session.get('role') == 'admin'
    if man_from and man_to and is_admin:                 # chỉ admin được nhập tay kỳ số bán; chi nhánh dùng đúng kỳ dò từ file
        pf, pt, note = man_from, man_to, 'Nhập tay'
    if not (pf and pt):
        if not is_admin:
            return jsonify({'error': 'Không dò được kỳ số bán từ file. Chi nhánh không tự nhập kỳ được, vui lòng báo admin.'}), 400
        return jsonify({'error': 'Không dò được kỳ báo cáo từ file. Vui lòng nhập Từ ngày / Đến ngày rồi import lại.',
                        'need_period': True}), 400
    if pt < pf:
        return jsonify({'error': 'Đến ngày phải sau hoặc bằng Từ ngày.'}), 400

    db, cur = _ctx()
    try:
        store, err = _resolve_store(cur, request.form.get('store'))
        if err:
            return err
        cur.execute('''
            INSERT INTO gdh_batches (store_code, owner, period_from, period_to, filenames, uploaded_by, uploaded_at)
            VALUES (%s,%s,%s,%s,%s,%s,(NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'))
            ON CONFLICT (store_code, owner, period_from, period_to) WHERE status = 'draft' DO UPDATE SET
                filenames = EXCLUDED.filenames, uploaded_by = EXCLUDED.uploaded_by, uploaded_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\')
            RETURNING id''', (store, _owner_for(request.form.get('space')), pf, pt, f.filename, _actor_name()))
        bid = cur.fetchone()['id']
        data = [(bid, code, r['name'], r['unit'], r['opening'], r['purchase'], r['out_qty'], r['sold'],
                 r['closing'], r['cost']) for code, r in rows.items()]
        execute_values(cur, '''
            INSERT INTO gdh_lines (batch_id, part_code, part_name, unit, opening, purchase, out_qty, sold, closing, unit_cost)
            VALUES %s
            ON CONFLICT (batch_id, part_code) DO UPDATE SET
                part_name = COALESCE(EXCLUDED.part_name, gdh_lines.part_name),
                unit = COALESCE(EXCLUDED.unit, gdh_lines.unit),
                opening = EXCLUDED.opening, purchase = EXCLUDED.purchase, closing = EXCLUDED.closing,
                out_qty = EXCLUDED.out_qty, sold = EXCLUDED.sold, unit_cost = EXCLUDED.unit_cost''', data, page_size=1000)
        # MỖI LẦN IMPORT = ĐÚNG SỐ LIỆU CỦA FILE VỪA IMPORT (mã, xuất, tồn): số cũ không còn sót lại.
        #  - Mã có trong file: ghi đè Đầu kỳ / Nhập / Xuất / Bán / Cuối kỳ / Giá vốn bằng số của file (kể cả khi file không có cột đó).
        #  - Mã KHÔNG còn trong file: xoá dòng; riêng dòng người dùng đã nhập tay (cộng/trừ thêm, loại đơn, ghi chú) thì giữ lại
        #    nhưng đưa số liệu file về 0 để không hiện tồn/xuất của lần import cũ.
        codes_in_file = list(rows.keys())
        cur.execute("""DELETE FROM gdh_lines WHERE batch_id = %s AND part_code <> ALL(%s)
                       AND COALESCE(adj_qty, 0) = 0 AND COALESCE(order_type, '') = '' AND COALESCE(note, '') = ''""",
                    (bid, codes_in_file))
        cur.execute("""UPDATE gdh_lines SET opening = 0, purchase = 0, closing = 0, out_qty = 0, sold = NULL, unit_cost = NULL
                       WHERE batch_id = %s AND part_code <> ALL(%s)""", (bid, codes_in_file))
        db.commit()
        print(f"[gdh_import] {f.filename}: đọc+phân tích file {_t_parse:.1f}s, ghi {len(rows)} mã vào CSDL {time.perf_counter() - _t0 - _t_parse:.1f}s")
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'batch_id': bid, 'total_parts': len(rows), 'template': parsed['template'],
                    'period_from': pf.isoformat(), 'period_to': pt.isoformat(), 'period_note': note})


@gom_don_hang_bp.route('/api/gom-don-hang/lines', methods=['GET'])
def gdh_lines():
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, request.args.get('batch_id'))
        if err:
            return err
        rows = compute_rows(cur, batch, with_extra=True)
        cur.execute('''SELECT COUNT(*) FILTER (WHERE sold IS NOT NULL) AS sold_lines,
                              COUNT(*) FILTER (WHERE unit_cost IS NOT NULL) AS cost_lines
                       FROM gdh_lines WHERE batch_id = %s AND NOT (part_code LIKE ANY(%s))''', (batch['id'], _EXCL_LIKE))
        cnt = cur.fetchone()
        inv_at = None
        try:
            cur.execute('SELECT upload_time FROM inventory_meta ORDER BY id LIMIT 1')
            m = cur.fetchone()
            inv_at = _fmt_dt(m['upload_time']) if m and m.get('upload_time') else None
        except Exception:
            db.rollback()
    finally:
        cur.close()
    totals = _totals(rows)
    q = (request.args.get('q') or '').strip().lower()
    group = (request.args.get('group') or '').strip().upper()
    otype = (request.args.get('order_type') or '').strip()
    scope = (request.args.get('scope') or '').strip().lower()
    if scope not in ('sold', 'all', 'order'):             # tương thích bản cũ (only_order)
        scope = 'sold' if request.args.get('only_order') == '1' else 'all'
    only = scope == 'sold'
    need = scope == 'order'                               # "Cần đặt": mã hệ thống ĐỀ XUẤT đặt (đề xuất > 0) hoặc có SL CUỐI > 0
    view = [r for r in rows
            if (not q or q in r['part_code'].lower() or q in (r['part_name'] or '').lower()
                or q in (r['note'] or '').lower())
            and (not group or r['group'] == group)
            and (not otype or (r['order_type'] == otype if otype != '-' else not r['order_type']))
            and (not only or r['sales'] > 0 or r['qty_final'] > 0 or r['adj'] or r['order_type'] or r['note'])
            and (not need or r['suggest'] > 0 or r['qty_final'] > 0)]
    try:
        page = max(1, int(request.args.get('page') or 1))
        size = min(2000, max(1, int(request.args.get('page_size') or 100)))
    except ValueError:
        page, size = 1, 100
    meta = _batch_json(batch, {'sold_lines': cnt['sold_lines'], 'cost_lines': cnt['cost_lines'], 'inv_at': inv_at})
    return jsonify({'success': True, 'batch': meta, 'order_types': ORDER_TYPES, 'group_labels': GROUP_LABELS,
                    'total_rows': len(view), 'page': page, 'page_size': size,
                    'data': view[(page - 1) * size: page * size], 'totals': totals})


@gom_don_hang_bp.route('/api/gom-don-hang/save', methods=['POST'])
def gdh_save():
    payload = request.get_json(silent=True) or {}
    items = payload.get('items') or []
    if not isinstance(items, list) or len(items) > _MAX_ITEMS:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400
    vals = []
    actor = _actor_name()
    for it in items:
        code = str((it or {}).get('part_code') or '').strip()
        if not code:
            continue
        adj = _num(it.get('adj'), 0.0)
        ot = str(it.get('order_type') or '').strip()
        if abs(adj) > 1_000_000:
            return jsonify({'error': f'Số lượng cộng/trừ của mã {code} không hợp lệ.'}), 400
        if ot and ot not in ORDER_TYPES:
            return jsonify({'error': f'Loại đơn của mã {code} không hợp lệ.'}), 400
        note = str(it.get('note') or '').strip()[:500]
        vals.append((code, adj, ot, note, actor))
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        blk = _edit_block(batch)
        if blk:
            return blk
        bid = int(batch['id'])
        _before = {}
        if vals:
            cur.execute('SELECT part_code, adj_qty, order_type, note FROM gdh_lines WHERE batch_id = %s AND part_code = ANY(%s)',
                        (bid, [v[0] for v in vals]))
            _before = {r['part_code']: r for r in cur.fetchall()}
        _type_changed = any((v[2] or '') != ((_before.get(v[0]) or {}).get('order_type') or '') for v in vals)
        if vals:
            execute_values(cur, f'''
                UPDATE gdh_lines l SET adj_qty = v.adj, order_type = NULLIF(v.ot, ''),
                       note = NULLIF(v.note, ''), updated_by = v.actor, updated_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\')
                FROM (VALUES %s) AS v(part_code, adj, ot, note, actor)
                WHERE l.batch_id = {bid} AND l.part_code = v.part_code''',
                           vals, template='(%s, %s::numeric, %s, %s, %s)')
        sets, args = [], []
        if 'forecast_weeks' in payload:
            fw = int(_num(payload.get('forecast_weeks'), DEFAULT_FORECAST_WEEKS))
            if not 1 <= fw <= 12:
                db.rollback()
                return jsonify({'error': 'Số tuần dự kiến phải từ 1 đến 12.'}), 400
            sets.append('forecast_weeks = %s'); args.append(fw)
        if payload.get('period_from') and payload.get('period_to') and session.get('role') != 'admin':
            try:
                same = (_parse_date(payload['period_from']), _parse_date(payload['period_to'])) == (batch['period_from'], batch['period_to'])
            except ValueError:
                same = False
            if not same:
                db.rollback()
                return jsonify({'error': 'Kỳ số bán (từ ngày - đến ngày) do admin quy định, chi nhánh không sửa được.'}), 403
        elif payload.get('period_from') and payload.get('period_to'):
            try:
                pf, pt = _parse_date(payload['period_from']), _parse_date(payload['period_to'])
            except ValueError:
                db.rollback()
                return jsonify({'error': 'Ngày không hợp lệ.'}), 400
            if pt < pf:
                db.rollback()
                return jsonify({'error': 'Đến ngày phải sau hoặc bằng Từ ngày.'}), 400
            sets.append('period_from = %s, period_to = %s'); args += [pf, pt]
        if sets:
            try:
                cur.execute(f'UPDATE gdh_batches SET {", ".join(sets)} WHERE id = %s', args + [bid])
            except Exception as e:
                db.rollback()
                if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                    return jsonify({'error': 'Đã có đợt gôm khác cùng chi nhánh, cùng kỳ (từ ngày - đến ngày) trong không gian này.'}), 400
                raise
        db.commit()
        _changes = []
        for code, adj, ot, note, _a in vals:
            o = _before.get(code)
            if not o:
                continue
            ch = {}
            if float(o['adj_qty'] or 0) != float(adj): ch['adj'] = [float(o['adj_qty'] or 0), float(adj)]
            if (o['order_type'] or '') != (ot or ''): ch['order_type'] = [o['order_type'] or '', ot or '']
            if (o['note'] or '') != (note or ''): ch['note'] = [o['note'] or '', note or '']
            if ch:
                _changes.append(dict(ma=code, **ch))
        if _changes or sets:
            audit_record('Lưu chỉnh sửa gôm đơn', 'Gôm đơn hàng', target=f'đợt {bid}',
                         summary=f'Đợt {bid}: {len(_changes)} mã thay đổi' + (' + đổi cấu hình đợt' if sets else ''),
                         after={'changes': _changes}, extra={'cau_hinh_dot': {k: payload.get(k) for k in ('forecast_weeks', 'period_from', 'period_to') if k in payload}})
        cur.execute('SELECT * FROM gdh_batches WHERE id = %s', (bid,))
        totals = _totals(compute_rows(cur, cur.fetchone()))      # để giao diện cập nhật thẻ tổng ngay sau khi lưu
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'saved': len(vals), 'totals': totals})


# ----------------------------------------------------------------------------
# 4b. THÊM MÃ NGOÀI FILE: đặt được cả mã chi nhánh KHÔNG có trong file Tổng hợp tồn kho của kỳ
#     (không có nhập/xuất/tồn trong kỳ nên file không liệt kê). Tìm trong TOÀN DANH MỤC của hệ thống rồi
#     thêm vào đợt gôm với số liệu 0; người dùng cộng SL ở cột "+/- thêm" như mọi mã khác.
# ----------------------------------------------------------------------------
def _catalog_find(cur, q, limit=30):
    """Tìm mã trong toàn danh mục: tồn kho hệ thống (mọi chi nhánh), bảng giá nhập/dòng xe, quy cách mã cha-con,
    mã thay thế của khoá đặt hàng và các đợt gôm cũ. Ưu tiên mã khớp từ đầu."""
    q = (q or '').strip()
    if len(q) < 2:
        return []
    like_any = f'%{q}%'
    found = {}

    def put(code, name=None, unit=None):
        k = _bkey(code)
        if not k or _is_excluded_from_reorder(k):
            return
        v = found.setdefault(k, {'code': str(code).strip(), 'name': None, 'unit': None})
        v['name'] = v['name'] or (name or None)
        v['unit'] = v['unit'] or (unit or None)

    queries = [
        ('SELECT part_code, MAX(part_name) AS n, MAX(unit) AS u FROM inventory_items '
         'WHERE part_code ILIKE %s OR part_name ILIKE %s GROUP BY part_code LIMIT 200', (like_any, like_any)),
        ('SELECT part_code, NULL AS n, NULL AS u FROM part_vehicle_models WHERE part_code ILIKE %s LIMIT 100', (like_any,)),
        ('SELECT child_code AS part_code, NULL AS n, NULL AS u FROM gdh_bundles WHERE child_code ILIKE %s '
         'UNION SELECT parent_code, NULL, NULL FROM gdh_bundles WHERE parent_code ILIKE %s LIMIT 100', (like_any, like_any)),
        ('SELECT part_code, NULL AS n, NULL AS u FROM order_lock_items WHERE part_code ILIKE %s LIMIT 100', (like_any,)),
        ('SELECT part_code, MAX(part_name) AS n, MAX(unit) AS u FROM gdh_lines '
         'WHERE part_code ILIKE %s OR part_name ILIKE %s GROUP BY part_code LIMIT 200', (like_any, like_any)),
    ]
    for sql, args in queries:
        try:
            cur.execute(sql, args)
            for r in cur.fetchall():
                put(r['part_code'], r.get('n'), r.get('u'))
        except Exception:                      # bảng nào chưa có thì bỏ qua, không làm hỏng tìm kiếm
            cur.connection.rollback()
    ql = q.lower()
    out = sorted(found.values(), key=lambda v: (0 if v['code'].lower().startswith(ql) else 1, v['code']))
    return out[:limit]


@gom_don_hang_bp.route('/api/gom-don-hang/catalog-search', methods=['GET'])
def gdh_catalog_search():
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, request.args.get('batch_id'))
        if err:
            return err
        res = _catalog_find(cur, request.args.get('q'))
        have = set()
        if res:
            cur.execute('SELECT UPPER(TRIM(part_code)) AS c FROM gdh_lines WHERE batch_id = %s AND UPPER(TRIM(part_code)) = ANY(%s)',
                        (batch['id'], [_bkey(v['code']) for v in res]))
            have = {r['c'] for r in cur.fetchall()}
        for v in res:
            v['in_batch'] = _bkey(v['code']) in have
    finally:
        cur.close()
    return jsonify({'success': True, 'data': res})


@gom_don_hang_bp.route('/api/gom-don-hang/add-codes', methods=['POST'])
def gdh_add_codes():
    """Thêm mã (không có trong file import) vào đợt gôm với nhập/xuất/tồn = 0. Mã đã có trong đợt thì bỏ qua.
    Mã KHÔNG có trong danh mục hệ thống chỉ được thêm khi giao diện gửi confirm_new=true (người dùng đã xác nhận)."""
    payload = request.get_json(silent=True) or {}
    raw = payload.get('codes') or []
    if not isinstance(raw, list) or not raw or len(raw) > 200:
        return jsonify({'error': 'Danh sách mã không hợp lệ (tối đa 200 mã mỗi lần).'}), 400
    codes, seen = [], set()
    for c in raw:
        c = re.sub(r'\s+', '', str(c or ''))[:100]
        if c and _bkey(c) not in seen:
            seen.add(_bkey(c))
            codes.append(c)
    if not codes:
        return jsonify({'error': 'Chưa nhập mã nào.'}), 400
    bad = [c for c in codes if _is_excluded_from_reorder(c)]
    if bad:
        return jsonify({'error': 'Mã không được phép đặt (khung xe...): ' + ', '.join(bad[:5])}), 400
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        blk = _edit_block(batch)
        if blk:
            return blk
        bid = int(batch['id'])
        cur.execute('SELECT UPPER(TRIM(part_code)) AS c FROM gdh_lines WHERE batch_id = %s AND UPPER(TRIM(part_code)) = ANY(%s)',
                    (bid, [_bkey(c) for c in codes]))
        exists = {r['c'] for r in cur.fetchall()}
        todo = [c for c in codes if _bkey(c) not in exists]
        info = {}
        for c in todo:                          # tra danh mục theo ĐÚNG mã (không phân biệt hoa/thường)
            for v in _catalog_find(cur, c, limit=60):
                if _bkey(v['code']) == _bkey(c):
                    info[_bkey(c)] = v
                    break
        unknown = [c for c in todo if _bkey(c) not in info]
        if unknown and not payload.get('confirm_new'):
            return jsonify({'error': 'Mã chưa có trong danh mục hệ thống: ' + ', '.join(unknown[:10]) +
                            '. Kiểm tra lại mã; nếu đúng thì xác nhận để thêm mã mới.',
                            'need_confirm': True, 'unknown': unknown}), 409
        actor = _actor_name()
        data = []
        for c in todo:
            v = info.get(_bkey(c)) or {}
            data.append((bid, v.get('code') or c, v.get('name'), v.get('unit'), actor))
        if data:
            execute_values(cur, '''
                INSERT INTO gdh_lines (batch_id, part_code, part_name, unit, opening, purchase, out_qty, sold, closing, updated_by, updated_at)
                SELECT v.b, v.c, v.n, v.u, 0, 0, 0, NULL, 0, v.a, (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\')
                FROM (VALUES %s) AS v(b, c, n, u, a)
                ON CONFLICT (batch_id, part_code) DO NOTHING''', data, template='(%s::int, %s, %s, %s, %s)')
        db.commit()
        if data:
            audit_record('Thêm mã ngoài file vào gôm đơn', 'Gôm đơn hàng', target=f'đợt {bid}',
                         summary=f'Đợt {bid}: thêm {len(data)} mã không có trong file tồn kho',
                         after={'codes': [d[1] for d in data], 'ma_moi_ngoai_danh_muc': unknown})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'added': [d[1] for d in data],
                    'existing': [c for c in codes if _bkey(c) in exists]})


@gom_don_hang_bp.route('/api/gom-don-hang/assign-type', methods=['POST'])
def gdh_assign_type():
    """Gán 1 loại đơn cho các mã có SL cuối > 0 của đợt (mặc định chỉ mã CHƯA có loại đơn; overwrite=true thì đổi cả mã đã có loại khác).
    Một phiên gôm được chứa nhiều loại đơn; khi đẩy, chi nhánh chọn đẩy loại nào."""
    payload = request.get_json(silent=True) or {}
    ot = str(payload.get('order_type') or '').strip()
    if ot not in ORDER_TYPES:
        return jsonify({'error': 'Loại đơn không hợp lệ.'}), 400
    overwrite = bool(payload.get('overwrite'))
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        blk = _edit_block(batch)
        if blk:
            return blk
        rows = compute_rows(cur, batch)
        codes = [r['part_code'] for r in rows if r['qty_final'] > 0 and (overwrite or not r['order_type'])]
        if codes:
            cur.execute('''UPDATE gdh_lines SET order_type = %s, updated_by = %s, updated_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\')
                           WHERE batch_id = %s AND part_code = ANY(%s)''', (ot, _actor_name(), batch['id'], codes))
        db.commit()
        audit_record('Gán loại đơn hàng loạt', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                     summary=f"Đợt {batch['id']}: gán loại '{ot}' cho {len(codes)} mã" + (' (ghi đè)' if overwrite else ''),
                     after={'order_type': ot, 'overwrite': overwrite}, extra={'so_ma': len(codes), 'codes': codes})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'assigned': len(codes), 'codes': codes})


@gom_don_hang_bp.route('/api/gom-don-hang/unassign-type', methods=['POST'])
def gdh_unassign_type():
    """Bỏ gán loại đơn hàng loạt (khi lỡ bấm Gán nhầm). Chỉ xoá loại đơn ĐÚNG BẰNG order_type gửi lên.
    - codes (tuỳ chọn): chỉ bỏ gán trong danh sách mã này (dùng cho 'Hoàn tác lần gán vừa rồi').
    - dry_run=true: chỉ đếm số mã sẽ bị bỏ gán, không ghi gì."""
    payload = request.get_json(silent=True) or {}
    ot = str(payload.get('order_type') or '').strip()
    if ot not in ORDER_TYPES:
        return jsonify({'error': 'Loại đơn không hợp lệ.'}), 400
    codes = payload.get('codes')
    if codes is not None:
        codes = [str(c).strip() for c in codes if str(c).strip()]
    dry = bool(payload.get('dry_run'))
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        blk = _edit_block(batch)
        if blk:
            return blk
        where, args = 'batch_id = %s AND order_type = %s', [batch['id'], ot]
        if codes is not None:
            where += ' AND part_code = ANY(%s)'
            args.append(codes)
        if dry:
            cur.execute('SELECT COUNT(*) AS n FROM gdh_lines WHERE ' + where, args)
            n = cur.fetchone()['n']
        else:
            cur.execute('UPDATE gdh_lines SET order_type = NULL, updated_by = %s, updated_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\') WHERE ' + where,
                        [_actor_name()] + args)
            n = cur.rowcount
            db.commit()
            audit_record('Bỏ gán loại đơn hàng loạt', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                         summary=f"Đợt {batch['id']}: bỏ gán loại '{ot}' của {n} mã",
                         before={'order_type': ot}, extra={'so_ma': n, 'codes': codes})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'count': n, 'dry_run': dry})


@gom_don_hang_bp.route('/api/gom-don-hang/delete-batch', methods=['POST'])
def gdh_delete_batch():
    payload = request.get_json(silent=True) or {}
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        if not batch['owner'] and (batch.get('status') or 'draft') != 'draft':      # đơn đã đẩy đi: lưu trữ, không xoá
            return jsonify({'error': 'Đơn đã đẩy đi duyệt được lưu trữ nên không xoá được. '
                                     'Nếu còn "Chờ duyệt", hãy Thu hồi đơn trước.'}), 409
        cur.execute('SELECT COUNT(*) AS n FROM gdh_lines WHERE batch_id = %s', (batch['id'],))
        _n_lines = cur.fetchone()['n']
        cur.execute('DELETE FROM gdh_batches WHERE id = %s', (batch['id'],))   # gdh_lines xoá theo (CASCADE)
        db.commit()
        audit_record('Xoá đợt gôm', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                     summary=f"Xoá đợt gôm {batch['id']} ({_n_lines} dòng)",
                     before={k: batch[k] for k in ('id', 'store_code', 'owner', 'period_from', 'period_to', 'forecast_weeks') if k in batch})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True})


@gom_don_hang_bp.route('/api/gom-don-hang/bundle-info', methods=['GET'])
def gdh_bundle_info():
    if 'user' not in session or session.get('role') not in ('admin', 'store'):
        return jsonify({'error': 'Forbidden'}), 403
    db, cur = _ctx()
    try:
        cur.execute('SELECT * FROM gdh_bundle_meta WHERE id = 1')
        m = cur.fetchone()
    finally:
        cur.close()
    return jsonify({'success': True, 'count': (m['total'] if m else 0) or 0,
                    'filename': m['filename'] if m else None, 'uploaded_by': m['uploaded_by'] if m else None,
                    'uploaded_at': _fmt_dt(m['uploaded_at']) if m else None,
                    'warnings': [w for w in ((m['warnings'] if m else '') or '').split('\n') if w]})


@gom_don_hang_bp.route('/api/gom-don-hang/bundle-import', methods=['POST'])
def gdh_bundle_import():
    """Chỉ admin. Ghi đè toàn bộ bảng quy cách bằng file mới."""
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Chỉ admin được import file quy cách.'}), 403
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Vui lòng chọn file quy cách (Mã cha / Mã con / Số lượng quy đổi).'}), 400
    try:
        mp, warns = parse_bundle_file(f)
    except Exception as e:
        return jsonify({'error': f'Không đọc được file: {e}'}), 400
    if not mp:
        return jsonify({'error': 'File không có dòng quy cách hợp lệ.'}), 400
    db, cur = _ctx()
    try:
        cur.execute('DELETE FROM gdh_bundles')
        execute_values(cur, 'INSERT INTO gdh_bundles (child_code, parent_code, ratio) VALUES %s',
                       [(c, p, q) for c, (p, q) in mp.items()], page_size=1000)
        cur.execute('''
            INSERT INTO gdh_bundle_meta (id, filename, uploaded_by, uploaded_at, total, warnings)
            VALUES (1, %s, %s, (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), %s, %s)
            ON CONFLICT (id) DO UPDATE SET filename = EXCLUDED.filename, uploaded_by = EXCLUDED.uploaded_by,
                uploaded_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), total = EXCLUDED.total, warnings = EXCLUDED.warnings''',
                    (f.filename, _actor_name(), len(mp), '\n'.join(warns[:50])))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'count': len(mp), 'warnings': warns})


# ----------------------------------------------------------------------------
# 4b. DUYỆT ĐƠN: chi nhánh đẩy đơn -> admin lấy về duyệt -> trả về -> cửa hàng xem thay đổi & tải đơn
# ----------------------------------------------------------------------------
def _snap_item(r):
    return {'part_name': r['part_name'], 'unit': r['unit'], 'adj': r['adj'] or 0.0, 'order_type': r['order_type'] or '',
            'note': r['note'] or '', 'suggest': r['suggest'], 'qty_final': r['qty_final'],
            'order_code': r['order_code'], 'order_qty': r['order_qty'], 'unit_cost': r['ord_cost'], 'amount': r['amount']}


def _snap_relevant(r):
    """Chỉ các mã thật sự vào đơn: SL cuối > 0 VÀ đã gắn loại đơn."""
    return bool(r['qty_final'] > 0 and r['order_type'] in ORDER_TYPES)


def _snapshot(cur, batch, stage, rows=None):
    """Chụp các mã vào đơn (SL cuối > 0 và đã có loại đơn) vào gdh_batch_snap."""
    rows = rows if rows is not None else compute_rows(cur, batch)
    cur.execute('DELETE FROM gdh_batch_snap WHERE batch_id = %s AND stage = %s', (batch['id'], stage))
    data = []
    for r in rows:
        if not _snap_relevant(r):
            continue
        data.append((batch['id'], stage, r['part_code'], r['part_name'], r['unit'], r['adj'] or 0.0, r['order_type'] or '',
                     r['note'] or '', r['suggest'], r['qty_final'], r['order_code'], r['order_qty'], r['ord_cost'], r['amount']))
    if data:
        execute_values(cur, '''INSERT INTO gdh_batch_snap (batch_id, stage, part_code, part_name, unit, adj, order_type, note,
                                                           suggest, qty_final, order_code, order_qty, unit_cost, amount)
                               VALUES %s''', data, page_size=1000)
    return len(data)


def _snap_load(cur, bid, stage):
    cur.execute('SELECT * FROM gdh_batch_snap WHERE batch_id = %s AND stage = %s', (bid, stage))
    return {r['part_code']: {
        'part_name': r['part_name'], 'unit': r['unit'], 'adj': float(r['adj'] or 0), 'order_type': r['order_type'] or '',
        'note': r['note'] or '', 'suggest': _f(r['suggest']), 'qty_final': float(r['qty_final'] or 0),
        'order_code': r['order_code'], 'order_qty': _f(r['order_qty']), 'unit_cost': _f(r['unit_cost']), 'amount': _f(r['amount'])
    } for r in cur.fetchall()}


def _items_totals(items):
    by_type = {t: {'parts': 0, 'qty': 0, 'amount': 0.0} for t in ORDER_TYPES}
    parts, qty, amount = 0, 0.0, 0.0
    for it in items.values():
        q = it['qty_final']
        if q <= 0:
            continue
        amt = it['amount'] or 0.0
        parts += 1; qty += q; amount += amt
        t = by_type.get(it['order_type'])
        if t:
            t['parts'] += 1; t['qty'] += q; t['amount'] += amt
    return {'parts': parts, 'qty': qty, 'amount': amount, 'by_type': by_type}


def _compare_items(old, new):
    """So 'đơn lúc đẩy' (old) với 'đơn sau duyệt' (new). kind: added / removed / changed / same."""
    rows, counts = [], {'added': 0, 'removed': 0, 'changed': 0, 'qty': 0, 'type': 0, 'note': 0}
    for code in sorted(set(old) | set(new)):
        o, n = old.get(code), new.get(code)
        qo, qn = (o['qty_final'] if o else 0), (n['qty_final'] if n else 0)
        to, tn = (o['order_type'] if o else ''), (n['order_type'] if n else '')
        no_, nn = (o['note'] if o else ''), (n['note'] if n else '')
        ch = []
        if qo != qn:
            ch.append('qty')
        if (qo > 0 or qn > 0) and to != tn:
            ch.append('type')
        if no_ != nn:
            ch.append('note')
        if qo <= 0 < qn:
            kind = 'added'
        elif qn <= 0 < qo:
            kind = 'removed'
        elif ch:
            kind = 'changed'
        else:
            kind = 'same'
        if kind == 'same' and qo <= 0 and qn <= 0:
            continue                                    # không nằm trong đơn ở cả 2 bản và không đổi gì
        if kind in counts:
            counts[kind] += 1
        for c in ch:
            counts[c] += 1
        base = n or o
        rows.append({'part_code': code, 'part_name': base['part_name'], 'unit': base['unit'],
                     'old': o, 'new': n, 'kind': kind, 'changes': ch})
    return rows, counts


def _need_role(role):
    if 'user' not in session or session.get('role') != role:
        who = 'user chi nhánh' if role == 'store' else 'admin'
        return jsonify({'error': f'Chỉ {who} mới thực hiện được thao tác này.'}), 403
    return None


def _bad_state(batch, expect_label):
    st = batch.get('status') or 'draft'
    return jsonify({'error': f'Đơn đang ở trạng thái "{STATUS_LABELS.get(st, st)}", không còn ở "{expect_label}" '
                             '(có thể người khác vừa thao tác). Hãy tải lại danh sách.', 'status': st}), 409


@gom_don_hang_bp.route('/api/gom-don-hang/submit', methods=['POST'])
def gdh_submit():
    """Chi nhánh đẩy 1 LOẠI ĐƠN của phiên gôm cho admin (payload.order_type).
    Một phiên gôm (Nháp) có thể chứa nhiều loại đơn. Đẩy loại nào thì TÁCH các mã loại đó (SL cuối > 0) ra thành một đơn riêng
    ở trạng thái Chờ duyệt (đơn đẩy đi chỉ có 1 loại), rồi XOÁ các mã đó khỏi phiên gôm. Phiên gôm vẫn giữ nguyên với các mã loại khác.
    Chụp lại 'đơn lúc đẩy' để so sánh về sau."""
    blk = _need_role('store')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    note = str(payload.get('note') or '').strip()[:500]
    ot = str(payload.get('order_type') or '').strip()
    if ot not in ORDER_TYPES:
        return jsonify({'error': 'Hãy chọn loại đơn cần đẩy cho admin (Định kỳ / Khẩn / Đơn 26).'}), 400
    _t0 = time.perf_counter()
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        cur.execute("SELECT 1 FROM gdh_batches WHERE id = %s AND status = 'draft' FOR UPDATE", (batch['id'],))   # khoá: 2 lần bấm không đẩy trùng
        if not cur.fetchone():
            db.rollback()
            return _bad_state(batch, 'Nháp')
        rows = compute_rows(cur, batch)
        pick = [r for r in rows if r['order_type'] == ot and r['qty_final'] > 0]
        if not pick:
            db.rollback()
            return jsonify({'error': f'Phiên gôm chưa có mã nào loại "{ot}" có SL cuối > 0 nên chưa đẩy được.'}), 400
        # Cho phép đẩy NHIỀU đơn cùng loại cho 1 phiên gôm: mỗi lần đẩy, các mã đã đẩy bị xoá khỏi phiên gôm
        # nên đơn sau chỉ gồm mã MỚI/còn lại, không trùng đơn trước -> không chặn và không hỏi xác nhận trùng nữa.
        codes = [r['part_code'] for r in pick]
        old_id = int(batch['id'])
        # 1) đơn mới (Chờ duyệt) = bản sao cấu hình của phiên gôm
        cur.execute('''INSERT INTO gdh_batches (store_code, owner, period_from, period_to, forecast_weeks, filenames, uploaded_by, uploaded_at,
                                              kind, status, submitted_by, submitted_at, submit_note)
                       SELECT store_code, owner, period_from, period_to, forecast_weeks, filenames, uploaded_by, uploaded_at,
                              'regular', 'pending', %s, (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), %s
                       FROM gdh_batches WHERE id = %s RETURNING id, submitted_at''', (_actor_name(), note, old_id))
        nb = cur.fetchone()
        new_id = int(nb['id'])
        # 2) chép các mã được đẩy sang đơn mới
        cols = 'part_code, part_name, unit, opening, purchase, out_qty, sold, closing, unit_cost, adj_qty, order_type, note, updated_by, updated_at'
        cur.execute(f'INSERT INTO gdh_lines (batch_id, {cols}) SELECT %s, {cols} FROM gdh_lines WHERE batch_id = %s AND part_code = ANY(%s)',
                    (new_id, old_id, codes))
        # 2b) mã cha chỉ để tra giá vốn (không có SL, không có loại đơn) -> chép dạng dòng rỗng để thành tiền của đơn mới không đổi
        parents = list({_bkey(r['bundle_parent']) for r in pick if r.get('bundle_parent')})
        if parents:
            cur.execute('''INSERT INTO gdh_lines (batch_id, part_code, part_name, unit, unit_cost)
                           SELECT %s, part_code, part_name, unit, unit_cost FROM gdh_lines
                           WHERE batch_id = %s AND unit_cost IS NOT NULL AND UPPER(TRIM(part_code)) = ANY(%s)
                             AND NOT (part_code = ANY(%s))
                           ON CONFLICT DO NOTHING''', (new_id, old_id, parents, codes))
        # 3) ảnh chụp 'đơn lúc đẩy' lấy đúng số đã tính ở phiên gôm (giữ nguyên thành tiền)
        n = _snapshot(cur, {'id': new_id}, 'submitted', pick)
        # 4) xoá các mã đã đẩy khỏi phiên gôm; mã loại này nhưng SL cuối = 0 (không vào đơn) thì chỉ bỏ gán loại
        cur.execute('DELETE FROM gdh_lines WHERE batch_id = %s AND part_code = ANY(%s)', (old_id, codes))
        cur.execute('UPDATE gdh_lines SET order_type = NULL, updated_by = %s, updated_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\') WHERE batch_id = %s AND order_type = %s',
                    (_actor_name(), old_id, ot))
        released = cur.rowcount
        cur.execute('UPDATE gdh_batches SET rejected_by = NULL, rejected_at = NULL, reject_reason = NULL WHERE id = %s', (old_id,))
        _log_event(cur, new_id, 'draft', 'pending', note or f'Chi nhánh đẩy đơn {ot} (tách từ phiên gôm {old_id})')
        _log_event(cur, old_id, 'draft', 'draft', f'Đẩy {len(codes)} mã {ot} sang đơn {new_id}, đã xoá khỏi phiên gôm')
        db.commit()
        remaining = {t: sum(1 for r in rows if r['order_type'] == t and r['qty_final'] > 0) for t in ORDER_TYPES if t != ot}
        print(f"[gdh] submit {ot}: phiên {old_id} -> đơn {new_id}, {len(codes)} mã, {(time.perf_counter() - _t0) * 1000:.0f} ms")
        audit_record('Đẩy đơn gôm cho admin duyệt', 'Gôm đơn hàng', target=f"đợt {new_id}",
                     summary=f"Phiên gôm {old_id} ({batch['store_code']}): đẩy {len(codes)} mã loại {ot} thành đơn {new_id}, xoá các mã này khỏi phiên gôm",
                     after={'status': 'pending', 'order_type': ot, 'note': note}, extra={'so_dong_luu': n, 'phien_gom': old_id})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': 'pending', 'batch_id': new_id, 'draft_id': old_id, 'order_type': ot,
                    'to_order': len(codes), 'qty': sum(r['qty_final'] for r in pick),
                    'amount': sum(r['amount'] or 0.0 for r in pick), 'released_zero_qty': released, 'remaining': remaining})


@gom_don_hang_bp.route('/api/gom-don-hang/recall', methods=['POST'])
def gdh_recall():
    """Chi nhánh thu hồi đơn khi admin CHƯA lấy về duyệt: Chờ duyệt -> Nháp."""
    blk = _need_role('store')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        st = batch.get('status') or 'draft'
        if batch.get('rereview_open') and st in ('pending', 'reviewing'):
            return jsonify({'error': 'Đơn này đã được duyệt và đang chờ admin duyệt lại nên không thu hồi được.'}), 409
        if (batch.get('kind') or 'regular') == 'urgent' and st == 'pending':
            # Đơn khẩn không có bản Nháp để quay về: thu hồi = xoá đơn + trả các dòng khách về trạng thái chưa gửi khẩn
            bid = batch['id']
            cur.execute("DELETE FROM gdh_batches WHERE id = %s AND status = 'pending' AND kind = 'urgent'", (bid,))
            if cur.rowcount != 1:
                db.rollback()
                return _bad_state(batch, 'Chờ duyệt')
            db.commit()
            _urgent_release_marks(bid)
            audit_record('Thu hồi đơn khẩn', 'Gôm đơn hàng', target=f"đợt {bid}",
                         summary=f"Đợt {bid} ({batch['store_code']}): thu hồi đơn khẩn (xoá đơn, trả các dòng về danh sách khách hàng)",
                         after={'status': 'deleted'})
            return jsonify({'success': True, 'status': 'deleted', 'urgent': True})
        if st == 'reviewing':
            return jsonify({'error': f"Admin {_display_name(batch.get('claimed_by'))} đang duyệt đơn này nên không thu hồi được. "
                                     'Hãy nhờ admin trả đơn về hàng chờ.'}), 409
        if st != 'pending':
            return _bad_state(batch, 'Chờ duyệt')
        draft = _find_draft(cur, batch)
        if draft:
            # Phiên gôm Nháp cùng kỳ vẫn còn (vì đẩy từng loại đơn) -> gộp các mã của đơn này về lại phiên gôm rồi xoá đơn đã đẩy
            n = _merge_lines_back(cur, batch['id'], draft['id'])
            cur.execute("DELETE FROM gdh_batches WHERE id = %s AND status = 'pending' AND owner = ''", (batch['id'],))
            if cur.rowcount != 1:
                db.rollback()
                return _bad_state(batch, 'Chờ duyệt')
            _log_event(cur, draft['id'], 'pending', 'draft', f"Thu hồi đơn {batch['id']}: trả {n} mã về phiên gôm")
            db.commit()
            audit_record('Thu hồi đơn gôm', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                         summary=f"Đơn {batch['id']} ({batch['store_code']}): thu hồi, trả {n} mã về phiên gôm {draft['id']}",
                         after={'status': 'merged_to_draft', 'draft_id': draft['id']})
            return jsonify({'success': True, 'status': 'draft', 'merged': True, 'batch_id': draft['id'], 'moved': n})
        cur.execute('''UPDATE gdh_batches SET status = 'draft', submitted_by = NULL, submitted_at = NULL, submit_note = NULL
                       WHERE id = %s AND status = 'pending' ''', (batch['id'],))
        if cur.rowcount != 1:
            db.rollback()
            return _bad_state(batch, 'Chờ duyệt')
        cur.execute("DELETE FROM gdh_batch_snap WHERE batch_id = %s AND stage = 'submitted'", (batch['id'],))
        _log_event(cur, batch['id'], 'pending', 'draft', 'Chi nhánh thu hồi đơn')
        db.commit()
        audit_record('Thu hồi đơn gôm', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                     summary=f"Đợt {batch['id']} ({batch['store_code']}): thu hồi về Nháp", after={'status': 'draft'})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': 'draft', 'batch_id': batch['id']})


@gom_don_hang_bp.route('/api/gom-don-hang/claim', methods=['POST'])
def gdh_claim():
    """Admin lấy đơn chờ duyệt về duyệt: Chờ duyệt -> Đang duyệt. UPDATE có điều kiện nên 2 admin không lấy trùng 1 đơn."""
    blk = _need_role('admin')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        if batch['owner']:
            return jsonify({'error': 'Đây là đơn gôm riêng của admin, không nằm trong luồng duyệt.'}), 400
        cur.execute('''UPDATE gdh_batches SET status = 'reviewing', claimed_by = %s, claimed_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\')
                       WHERE id = %s AND owner = '' AND status = 'pending' ''', (str(session.get('user') or ''), batch['id']))
        if cur.rowcount != 1:
            db.rollback()
            cur.execute('SELECT * FROM gdh_batches WHERE id = %s', (batch['id'],))
            return _bad_state(cur.fetchone(), 'Chờ duyệt')
        _log_event(cur, batch['id'], 'pending', 'reviewing', 'Admin lấy đơn về duyệt')
        db.commit()
        audit_record('Lấy đơn về duyệt', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                     summary=f"Đợt {batch['id']} ({batch['store_code']}): Chờ duyệt -> Đang duyệt", after={'status': 'reviewing'})
        review = None
        try:            # trả luôn dữ liệu khung duyệt để trình duyệt khỏi gọi thêm review-lines (lỗi thì trình duyệt tự gọi lại)
            cur.execute('SELECT * FROM gdh_batches WHERE id = %s', (batch['id'],))
            review = _review_payload(cur, cur.fetchone())
        except Exception:
            db.rollback()
            traceback.print_exc()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': 'reviewing', 'review': review})


@gom_don_hang_bp.route('/api/gom-don-hang/release', methods=['POST'])
def gdh_release():
    """Admin trả đơn về hàng chờ (không duyệt nữa): Đang duyệt -> Chờ duyệt. Các chỉnh sửa đã làm vẫn được giữ."""
    blk = _need_role('admin')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        cur.execute('''UPDATE gdh_batches SET status = 'pending', claimed_by = NULL, claimed_at = NULL
                       WHERE id = %s AND owner = '' AND status = 'reviewing' ''', (batch['id'],))
        if cur.rowcount != 1:
            db.rollback()
            return _bad_state(batch, 'Đang duyệt')
        _log_event(cur, batch['id'], 'reviewing', 'pending', 'Admin trả đơn về hàng chờ')
        db.commit()
        audit_record('Trả đơn về hàng chờ duyệt', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                     summary=f"Đợt {batch['id']} ({batch['store_code']}): Đang duyệt -> Chờ duyệt", after={'status': 'pending'})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': 'pending'})


def _rejection_log(cur, batch, parts, qty, name, reason):
    cur.execute('''INSERT INTO gdh_rejections (batch_id, store_code, kind, order_name, period_from, period_to, parts, qty,
                                               submitted_by, submitted_at, rejected_by, rejected_at, reason)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,(NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'),%s)''',
                (batch['id'], batch['store_code'], batch.get('kind') or 'regular', name, batch['period_from'], batch['period_to'],
                 parts, qty, batch.get('submitted_by'), batch.get('submitted_at'), _actor_name(), reason))


@gom_don_hang_bp.route('/api/gom-don-hang/rejections', methods=['GET'])
def gdh_rejections():
    """Lịch sử đơn bị admin từ chối. Admin: mọi chi nhánh (lọc được); user cửa hàng: chỉ chi nhánh mình.
    open = số đơn đang chờ chi nhánh chỉnh sửa (đã bị từ chối, chưa đẩy lại)."""
    db, cur = _ctx()
    try:
        store, err = _resolve_store(cur, request.args.get('store'), allow_all=True)
        if err:
            return err
        try:
            d_from, d_to = _parse_date(request.args.get('from')), _parse_date(request.args.get('to'))
        except ValueError:
            return jsonify({'error': 'Ngày không hợp lệ (định dạng YYYY-MM-DD).'}), 400
        sql = ('SELECT r.*, b.status AS b_status, b.reject_reason AS b_reject, b.rejected_at AS b_rejected_at '
               'FROM gdh_rejections r LEFT JOIN gdh_batches b ON b.id = r.batch_id WHERE 1=1')
        params = []
        if store:
            sql += ' AND r.store_code = %s'; params.append(store)
        if d_from:
            sql += ' AND r.rejected_at::date >= %s'; params.append(d_from)
        if d_to:
            sql += ' AND r.rejected_at::date <= %s'; params.append(d_to)
        data = []
        rej_rows = []
        if request.args.get('counts_only') != '1':          # chỉ cần số đếm (huy hiệu) thì khỏi nạp danh sách
            cur.execute(sql + ' ORDER BY r.rejected_at DESC, r.id DESC LIMIT 500', params)
            rej_rows = cur.fetchall()
        for r in rej_rows:
            # Đơn còn là Nháp và dấu từ chối vẫn là của chính lần này -> đang chờ chi nhánh sửa; đã đẩy lại / đã xoá thì không.
            waiting = (r['b_status'] == 'draft' and r['b_rejected_at'] is not None and r['rejected_at'] is not None
                       and abs((r['b_rejected_at'] - r['rejected_at']).total_seconds()) < 5)
            data.append({'id': r['id'], 'batch_id': r['batch_id'], 'store': r['store_code'], 'kind': r['kind'],
                         'name': r['order_name'] or ('Đơn hàng ' + r['store_code']),
                         'from': r['period_from'].isoformat() if r['period_from'] else None,
                         'to': r['period_to'].isoformat() if r['period_to'] else None,
                         'parts': r['parts'], 'qty': _f(r['qty']),
                         'submitted_by': r['submitted_by'], 'submitted_at': _fmt_dt(r['submitted_at']),
                         'rejected_by': r['rejected_by'], 'rejected_at': _fmt_dt(r['rejected_at']), 'reason': r['reason'] or '',
                         'waiting': waiting,
                         'state': 'waiting' if waiting else ('deleted' if r['b_status'] is None else 'resubmitted')})
        osql, oparams = "SELECT COUNT(*) AS n FROM gdh_batches WHERE owner = '' AND status = 'draft' AND rejected_at IS NOT NULL", []
        if store:
            osql += ' AND store_code = %s'; oparams.append(store)
        cur.execute(osql, oparams)
        open_n = int(cur.fetchone()['n'] or 0)
    finally:
        cur.close()
    return jsonify({'success': True, 'data': data, 'open': open_n})


@gom_don_hang_bp.route('/api/gom-don-hang/reject', methods=['POST'])
def gdh_reject():
    """Admin TỪ CHỐI duyệt đơn chưa ổn: Chờ duyệt / Đang duyệt -> Nháp (trả về chi nhánh kèm lý do bắt buộc để sửa rồi đẩy lại).
    Đơn Đang duyệt chỉ chính admin đã lấy đơn mới từ chối được. Đơn khẩn (tạo từ danh sách khách hàng) không có bản Nháp để quay về:
    từ chối = xoá đơn + trả các dòng về danh sách khách hàng (giống chi nhánh thu hồi đơn khẩn)."""
    blk = _need_role('admin')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get('reason') or '').strip()[:500]
    if not reason:
        return jsonify({'error': 'Cần nhập lý do từ chối để chi nhánh biết cần chỉnh sửa gì.'}), 400
    me = str(session.get('user') or '')
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        if batch['owner']:
            return jsonify({'error': 'Đây là đơn gôm riêng của admin, không nằm trong luồng duyệt.'}), 400
        st = batch.get('status') or 'draft'
        if st not in ('pending', 'reviewing'):
            return _bad_state(batch, 'Chờ duyệt hoặc Đang duyệt')
        if batch.get('rereview_open'):
            return jsonify({'error': 'Đơn này đã duyệt xong và cửa hàng chỉ nhờ duyệt lại SL, không từ chối về Nháp được. '
                                     'Hãy duyệt lại (giữ nguyên SL cũ nếu không đồng ý, kèm ghi chú chung giải thích cho cửa hàng).'}), 409
        if st == 'reviewing' and (batch.get('claimed_by') or '') != me:
            return jsonify({'error': f"Đơn đang do {_display_name(batch.get('claimed_by')) or 'admin khác'} duyệt, bạn không từ chối thay được."}), 403
        bid = batch['id']
        if (batch.get('kind') or 'regular') == 'urgent':
            cur.execute('SELECT COUNT(*) AS n, COALESCE(SUM(order_qty), 0) AS q FROM gdh_urgent_lines WHERE batch_id = %s', (bid,))
            u = cur.fetchone()
            _rejection_log(cur, batch, int(u['n'] or 0), float(u['q'] or 0), _order_name(batch, ['Khẩn']), reason)
            cur.execute("""DELETE FROM gdh_batches WHERE id = %s AND kind = 'urgent' AND owner = '' AND status = %s
                             AND (status = 'pending' OR claimed_by = %s)""", (bid, st, me))
            if cur.rowcount != 1:
                db.rollback()
                return _bad_state(batch, 'Chờ duyệt hoặc Đang duyệt')
            db.commit()
            _urgent_release_marks(bid)
            audit_record('Từ chối đơn khẩn', 'Gôm đơn hàng', target=f"đợt {bid}",
                         summary=f"Đợt {bid} ({batch['store_code']}): admin từ chối đơn khẩn (xoá đơn, trả các dòng về danh sách khách hàng). Lý do: {reason}",
                         after={'status': 'deleted', 'reason': reason})
            return jsonify({'success': True, 'status': 'deleted', 'urgent': True})
        conflict_msg = ('Chi nhánh vừa tạo một đơn Nháp khác cùng kỳ số bán nên chưa trả đơn này về được. Hãy thử từ chối lại.')
        items = _snap_load(cur, bid, 'submitted')
        tot = _items_totals(items)
        draft = _find_draft(cur, batch)
        if draft:
            # Phiên gôm Nháp cùng kỳ vẫn còn (chi nhánh đẩy từng loại đơn) -> trả các mã về phiên gôm đó, xoá đơn bị từ chối
            n = _merge_lines_back(cur, bid, draft['id'])
            cur.execute("""DELETE FROM gdh_batches WHERE id = %s AND owner = '' AND status = %s
                             AND (status = 'pending' OR claimed_by = %s)""", (bid, st, me))
            if cur.rowcount != 1:
                db.rollback()
                return _bad_state(batch, 'Chờ duyệt hoặc Đang duyệt')
            cur.execute("UPDATE gdh_batches SET rejected_by = %s, rejected_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), reject_reason = %s WHERE id = %s",
                        (_actor_name(), reason, draft['id']))
            _rejection_log(cur, batch, tot['parts'], tot['qty'],
                           _order_name(batch, [t for t in ORDER_TYPES if tot['by_type'][t]['parts'] > 0]), reason)
            _log_event(cur, draft['id'], st, 'draft', f'Admin từ chối đơn {bid}: ' + reason)
            db.commit()
            audit_record('Từ chối duyệt đơn gôm', 'Gôm đơn hàng', target=f"đợt {bid}",
                         summary=f"Đơn {bid} ({batch['store_code']}): từ chối, trả {n} mã về phiên gôm {draft['id']}. Lý do: {reason}",
                         after={'status': 'merged_to_draft', 'draft_id': draft['id'], 'reason': reason})
            return jsonify({'success': True, 'status': 'draft', 'merged': True, 'batch_id': draft['id']})
        try:
            cur.execute("""UPDATE gdh_batches SET status = 'draft', submitted_by = NULL, submitted_at = NULL, submit_note = NULL,
                                  claimed_by = NULL, claimed_at = NULL,
                                  rejected_by = %s, rejected_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), reject_reason = %s
                           WHERE id = %s AND owner = '' AND status = %s AND (status = 'pending' OR claimed_by = %s)""",
                        (_actor_name(), reason, bid, st, me))
        except psycopg2.IntegrityError:          # vừa có đơn Nháp cùng kỳ được tạo xen vào
            db.rollback()
            return jsonify({'error': conflict_msg}), 409
        if cur.rowcount != 1:
            db.rollback()
            return _bad_state(batch, 'Chờ duyệt hoặc Đang duyệt')
        _rejection_log(cur, batch, tot['parts'], tot['qty'],
                       _order_name(batch, [t for t in ORDER_TYPES if tot['by_type'][t]['parts'] > 0]), reason)
        cur.execute("DELETE FROM gdh_batch_snap WHERE batch_id = %s AND stage = 'submitted'", (bid,))
        _log_event(cur, bid, st, 'draft', 'Admin từ chối duyệt: ' + reason)
        db.commit()
        audit_record('Từ chối duyệt đơn gôm', 'Gôm đơn hàng', target=f"đợt {bid}",
                     summary=f"Đợt {bid} ({batch['store_code']}): {STATUS_LABELS.get(st, st)} -> Nháp (từ chối duyệt). Lý do: {reason}",
                     after={'status': 'draft', 'reason': reason})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': 'draft'})


def _review_map(cur, bid, codes=None):
    """{mã: (SL duyệt, ghi chú)} CỦA RIÊNG đơn này. Ghi chú không lấy từ ghi chú chung theo mã ở menu Duyệt Đơn Hàng
    nữa, để ghi chú của đơn khác không bị kéo sang."""
    cur.execute('SELECT part_code, approved_qty, note FROM gdh_batch_review WHERE batch_id = %s', (bid,))
    return {r['part_code']: (_f(r['approved_qty']), r['note'] or '') for r in cur.fetchall()}


def _review_payload(cur, batch):
    """Dữ liệu nạp vào khung duyệt: danh sách mã + SL cuối chi nhánh gửi + nháp đã lưu. Dùng chung cho claim và review-lines."""
    snap = _snap_load(cur, batch['id'], 'submitted')
    items = [{'part_code': c, 'part_name': v['part_name'], 'qty': v['qty_final'], 'order_type': v['order_type'], 'note': v['note']}
             for c, v in sorted(snap.items()) if v['qty_final'] > 0 and v['order_type'] in ORDER_TYPES]
    cur.execute('SELECT part_code, approved_qty, note, updated_at FROM gdh_review_draft WHERE batch_id = %s', (batch['id'],))
    drows = cur.fetchall()
    draft = {r['part_code']: {'qty': _f(r['approved_qty']), 'note': r['note'] or ''} for r in drows}
    out = {'batch': _batch_json(batch), 'items': items, 'draft': draft,
           'draft_saved_at': _fmt_dt(max((r['updated_at'] for r in drows), default=None))}
    if batch.get('rereview_open'):
        # Cửa hàng nhờ duyệt lại: kèm SL + ghi chú admin đã duyệt ở lần trước (gdh_batch_review chỉ bị thay khi admin duyệt xong lần mới)
        out['prev_review'] = {c: {'qty': q, 'note': n} for c, (q, n) in _review_map(cur, batch['id']).items()}
    if (batch.get('kind') or 'regular') == 'urgent':
        out['urgent_lines'] = _urgent_lines(cur, batch['id'])        # thông tin khách để admin xem ngay trong khung duyệt
    return out


@gom_don_hang_bp.route('/api/gom-don-hang/review-lines', methods=['GET'])
def gdh_review_lines():
    """Admin (người đã lấy đơn về duyệt): danh sách mã + SL cuối của đơn để nạp vào menu Duyệt Đơn Hàng.
    Chỉ gồm các mã chi nhánh đã gửi (SL cuối > 0 và đã có loại đơn)."""
    blk = _need_role('admin')
    if blk:
        return blk
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, request.args.get('batch_id'))
        if err:
            return err
        if batch['owner'] or (batch.get('status') or 'draft') != 'reviewing':
            return _bad_state(batch, 'Đang duyệt')
        if (batch.get('claimed_by') or '') != str(session.get('user') or ''):
            return jsonify({'error': f"Đơn đang do {_display_name(batch.get('claimed_by')) or 'admin khác'} duyệt."}), 403
        payload = _review_payload(cur, batch)
    finally:
        cur.close()
    return jsonify(dict(success=True, **payload))


@gom_don_hang_bp.route('/api/gom-don-hang/approve', methods=['POST'])
def gdh_approve():
    """Admin duyệt xong ở menu Duyệt Đơn Hàng: Đang duyệt -> Đã duyệt (chỉ chính admin đã lấy đơn).
    items = [{part_code, approved_qty, note}] lấy từ bảng kiểm tra (SL Duyệt + ghi chú từng mã) - lưu để cửa hàng xem.
    SL cuối / loại đơn của chi nhánh KHÔNG bị đổi: file đặt hàng vẫn theo SL cuối của gôm."""
    blk = _need_role('admin')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    note = str(payload.get('note') or '').strip()[:500]
    items = payload.get('items')
    if not isinstance(items, list):
        return jsonify({'error': 'Thiếu kết quả duyệt (SL duyệt / ghi chú từng mã). Hãy Duyệt xong từ menu Duyệt Đơn Hàng.'}), 400
    me = str(session.get('user') or '')
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        if (batch.get('status') or 'draft') != 'reviewing':
            return _bad_state(batch, 'Đang duyệt')
        if (batch.get('claimed_by') or '') != me:
            return jsonify({'error': f"Đơn đang do {_display_name(batch.get('claimed_by')) or 'admin khác'} duyệt, bạn không duyệt xong thay được."}), 403
        sent = _snap_load(cur, batch['id'], 'submitted')
        sent_codes = {c for c, v in sent.items() if v['qty_final'] > 0 and v['order_type'] in ORDER_TYPES}
        given = {}
        for it in items:
            code = str((it or {}).get('part_code') or '').strip()
            if code not in sent_codes:
                continue                                    # mã không thuộc đơn này: bỏ qua
            try:
                q = (it or {}).get('approved_qty')
                q = None if q is None or q == '' else max(0.0, float(q))
            except (TypeError, ValueError):
                q = None
            given[code] = (q, str((it or {}).get('note') or '').strip()[:500])
        cur.execute('''UPDATE gdh_batches SET status = 'approved', approved_by = %s, approved_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), review_note = %s,
                              rereview_open = FALSE
                       WHERE id = %s AND status = 'reviewing' AND claimed_by = %s''', (_actor_name(), note, batch['id'], me))
        if cur.rowcount != 1:
            db.rollback()
            return _bad_state(batch, 'Đang duyệt')
        cur.execute('DELETE FROM gdh_batch_review WHERE batch_id = %s', (batch['id'],))
        cur.execute('DELETE FROM gdh_review_draft WHERE batch_id = %s', (batch['id'],))
        if given:
            execute_values(cur, 'INSERT INTO gdh_batch_review (batch_id, part_code, approved_qty, note) VALUES %s',
                           [(batch['id'], c, q, n) for c, (q, n) in given.items()], page_size=1000)
        rows = compute_rows(cur, batch)
        n = _snapshot(cur, batch, 'approved', rows)
        was_re = bool(batch.get('rereview_open'))
        _log_event(cur, batch['id'], 'reviewing', 'approved', ('Admin duyệt lại xong. ' if was_re else '') + (note or 'Admin duyệt xong'))
        try:                                    # thông báo chuông cho chi nhánh; lỗi thông báo không được làm hỏng việc duyệt
            cur.execute('SAVEPOINT gdh_notif')
            create_notification(cur, batch['store_code'], 'Đơn gôm đã được duyệt lại' if was_re else 'Đơn gôm đã được duyệt',
                                f"{_order_name(batch, [v['order_type'] for v in sent.values() if v['qty_final'] > 0])} đã được admin {'duyệt lại' if was_re else 'duyệt'}. "
                                'Vào Gôm đơn hàng > Đơn đã đẩy để xem SL duyệt, ghi chú và tải đơn về.', 'info')
            cur.execute('RELEASE SAVEPOINT gdh_notif')
        except Exception:
            cur.execute('ROLLBACK TO SAVEPOINT gdh_notif')
        db.commit()
        audit_record('Duyệt xong đơn gôm', 'Duyệt Đơn Hàng', target=f"đợt {batch['id']}",
                     summary=f"Đợt {batch['id']} ({batch['store_code']}): Đang duyệt -> Đã duyệt, {len(sent_codes)} mã gửi, {len(given)} mã có kết quả duyệt",
                     after={'status': 'approved', 'note': note}, extra={'so_dong_luu': n})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': 'approved', 'saved': len(given), 'missing': len(sent_codes) - len(given)})


@gom_don_hang_bp.route('/api/gom-don-hang/request-rereview', methods=['POST'])
def gdh_request_rereview():
    """Chi nhánh NHỜ ADMIN DUYỆT LẠI đơn đã duyệt xong mà SL duyệt chưa hài lòng: Đã duyệt / Đã xem -> Chờ duyệt.
    Chỉ được trong REREVIEW_HOURS giờ kể từ lúc admin duyệt xong (approved_at) và khi chưa tải đơn về (Đã đặt thì không).
    Bắt buộc ghi rõ nhờ duyệt lại điều gì. SL duyệt + ghi chú cũ được nạp sẵn vào nháp của admin để chỉ sửa những mã cần đổi.
    Điều kiện 24 giờ kiểm ngay trong câu UPDATE (giờ Việt Nam) nên không bị lách khi bấm sát giờ hoặc 2 nơi cùng bấm."""
    blk = _need_role('store')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    note = str(payload.get('note') or '').strip()[:500]
    if not note:
        return jsonify({'error': 'Cần ghi rõ mã nào / vì sao chưa hài lòng để admin duyệt lại.'}), 400
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        st = batch.get('status') or 'draft'
        if batch['owner']:
            return jsonify({'error': 'Đây là đơn gôm riêng của admin, không nằm trong luồng duyệt.'}), 400
        if st in ('pending', 'reviewing') and batch.get('rereview_open'):
            return jsonify({'error': 'Đơn này đã được nhờ duyệt lại, đang chờ admin xử lý.', 'status': st}), 409
        if st == 'ordered':
            return jsonify({'error': 'Đơn đã tải về và chuyển sang "Đã đặt" nên không nhờ duyệt lại được.', 'status': st}), 409
        if st not in ('approved', 'viewed'):
            return _bad_state(batch, 'Đã duyệt')
        cur.execute("""UPDATE gdh_batches SET status = 'pending', claimed_by = NULL, claimed_at = NULL,
                              viewed_by = NULL, viewed_at = NULL,
                              rereview_open = TRUE, rereview_count = COALESCE(rereview_count, 0) + 1,
                              rereview_at = (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh'), rereview_by = %s, rereview_note = %s
                       WHERE id = %s AND owner = '' AND status IN ('approved', 'viewed') AND approved_at IS NOT NULL
                         AND (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh') <= approved_at + make_interval(hours => %s)""",
                    (_actor_name(), note, batch['id'], REREVIEW_HOURS))
        if cur.rowcount != 1:
            db.rollback()
            cur.execute('SELECT * FROM gdh_batches WHERE id = %s', (batch['id'],))
            nb = cur.fetchone() or batch
            if (nb.get('status') or '') in ('approved', 'viewed'):
                return jsonify({'error': f'Đã quá {REREVIEW_HOURS} giờ kể từ lúc admin duyệt xong ({_fmt_dt(nb.get("approved_at"))}) nên không nhờ duyệt lại được.',
                                'status': nb.get('status'), 'expired': True}), 409
            return _bad_state(nb, 'Đã duyệt')
        # nháp của admin = kết quả duyệt cũ, để chỉ sửa những mã cần đổi
        cur.execute('DELETE FROM gdh_review_draft WHERE batch_id = %s', (batch['id'],))
        cur.execute('INSERT INTO gdh_review_draft (batch_id, part_code, approved_qty, note) '
                    'SELECT batch_id, part_code, approved_qty, note FROM gdh_batch_review WHERE batch_id = %s', (batch['id'],))
        _log_event(cur, batch['id'], st, 'pending', 'Chi nhánh nhờ duyệt lại: ' + note)
        db.commit()
        audit_record('Nhờ duyệt lại đơn gôm', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                     summary=f"Đợt {batch['id']} ({batch['store_code']}): {STATUS_LABELS.get(st, st)} -> Chờ duyệt (nhờ duyệt lại). Nội dung: {note}",
                     after={'status': 'pending', 'rereview_note': note})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': 'pending'})


@gom_don_hang_bp.route('/api/gom-don-hang/mark-viewed', methods=['POST'])
def gdh_mark_viewed():
    """Cửa hàng mở bảng so sánh của đơn đã duyệt: Đã duyệt -> Đã xem (không làm gì nếu đơn đã qua bước này)."""
    blk = _need_role('store')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        cur.execute('''UPDATE gdh_batches SET status = 'viewed', viewed_by = %s, viewed_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\')
                       WHERE id = %s AND status = 'approved' ''', (_actor_name(), batch['id']))
        changed = cur.rowcount == 1
        if changed:
            _log_event(cur, batch['id'], 'approved', 'viewed', 'Cửa hàng đã xem thay đổi')
        db.commit()
        cur.execute('SELECT status FROM gdh_batches WHERE id = %s', (batch['id'],))
        st = cur.fetchone()['status']
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': st, 'changed': changed})


@gom_don_hang_bp.route('/api/gom-don-hang/compare', methods=['GET'])
def gdh_compare():
    """Kết quả duyệt của 1 đơn: STT, mã hàng, tên hàng, SL gửi (SL cuối chi nhánh gửi), SL duyệt, ghi chú của admin (từng mã).
    Cả cửa hàng lẫn admin đều xem được lúc Chờ duyệt / Đang duyệt (SL duyệt chưa có, reviewed=False). Cửa hàng chỉ xem đơn của chi nhánh mình (_get_batch)."""
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, request.args.get('batch_id'))
        if err:
            return err
        st = batch.get('status') or 'draft'
        if batch['owner'] or st == 'draft':
            return jsonify({'error': 'Đợt gôm này chưa đẩy đi duyệt nên chưa có kết quả duyệt.'}), 409
        # Cửa hàng cũng xem lại được đơn đã đẩy khi còn Chờ duyệt / Đang duyệt (reviewed=False -> SL duyệt hiện "–")
        sent = _snap_load(cur, batch['id'], 'submitted')
        rev = _review_map(cur, batch['id'], list(sent))
        rows = []
        for i, (code, v) in enumerate(sorted(((c, v) for c, v in sent.items()
                                              if v['qty_final'] > 0 and v['order_type'] in ORDER_TYPES),
                                             key=lambda cv: (ORDER_TYPES.index(cv[1]['order_type']), cv[0])), 1):
            aq, nt = rev.get(code, (None, ''))
            rows.append({'stt': i, 'part_code': code, 'part_name': v['part_name'], 'order_type': v['order_type'],
                         'sent_qty': v['qty_final'], 'approved_qty': aq, 'note': nt})
        meta = _batch_json(batch)
    finally:
        cur.close()
    return jsonify({'success': True, 'batch': meta, 'rows': rows, 'reviewed': st in ('approved', 'viewed', 'ordered')})


@gom_don_hang_bp.route('/api/gom-don-hang/orders', methods=['GET'])
def gdh_orders():
    """Danh sách đơn đã đẩy đi duyệt (kiêm LƯU TRỮ). Admin: mọi chi nhánh (lọc được); user cửa hàng: chỉ đơn của chi nhánh mình.
    status: active (Chờ duyệt + Đang duyệt) | all | pending | reviewing | approved | viewed | ordered."""
    db, cur = _ctx()
    try:
        store, err = _resolve_store(cur, request.args.get('store'), allow_all=True)
        if err:
            return err
        want = (request.args.get('status') or '').strip().lower()
        wants = [w for w in want.split(',') if w in POST_SUBMIT_STATUSES]
        sts = ['pending', 'reviewing'] if want == 'active' else (wants or POST_SUBMIT_STATUSES)
        try:
            d_from, d_to = _parse_date(request.args.get('from')), _parse_date(request.args.get('to'))
        except ValueError:
            return jsonify({'error': 'Ngày không hợp lệ (định dạng YYYY-MM-DD).'}), 400
        sql, params = ("SELECT *, EXTRACT(EPOCH FROM ((NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\') - (CASE WHEN rereview_open THEN rereview_at ELSE submitted_at END))) / 60 AS waiting_min "
                       "FROM gdh_batches WHERE owner = '' AND status = ANY(%s)"), [sts]
        if store:
            sql += ' AND store_code = %s'; params.append(store)
        if d_from:
            sql += ' AND submitted_at::date >= %s'; params.append(d_from)
        if d_to:
            sql += ' AND submitted_at::date <= %s'; params.append(d_to)
        if request.args.get('counts_only') == '1':         # chỉ cần số đếm (huy hiệu trên nút), khỏi nạp danh sách
            batches = []
        else:
            cur.execute(sql + ' ORDER BY submitted_at DESC NULLS LAST, id DESC LIMIT 500', params)
            batches = cur.fetchall()
        sums = {}
        if batches:
            cur.execute('''SELECT batch_id, stage, COUNT(*) FILTER (WHERE qty_final > 0) AS parts,
                                  COALESCE(SUM(qty_final), 0) AS qty, COALESCE(SUM(amount), 0) AS amount
                           FROM gdh_batch_snap WHERE batch_id = ANY(%s) GROUP BY batch_id, stage''', ([b['id'] for b in batches],))
            for r in cur.fetchall():
                sums.setdefault(r['batch_id'], {})[r['stage']] = {'parts': int(r['parts']), 'qty': float(r['qty']),
                                                                   'amount': float(r['amount'])}
        types, results = {}, {}
        if batches:
            ids = [b['id'] for b in batches]
            cur.execute('''SELECT batch_id, order_type, COUNT(*) AS parts, COALESCE(SUM(qty_final), 0) AS qty
                           FROM gdh_batch_snap WHERE batch_id = ANY(%s) AND stage = 'submitted' AND qty_final > 0 AND order_type <> ''
                           GROUP BY batch_id, order_type''', (ids,))
            for r in cur.fetchall():
                types.setdefault(r['batch_id'], {})[r['order_type']] = {'parts': int(r['parts']), 'qty': float(r['qty'])}
            done_ids = [b['id'] for b in batches if b['status'] in ('approved', 'viewed', 'ordered') or b.get('rereview_open')]
            if done_ids:
                cur.execute('''SELECT s.batch_id, COUNT(*) AS total,
                                      COUNT(*) FILTER (WHERE r.approved_qty > 0) AS approved_parts,
                                      COUNT(*) FILTER (WHERE r.approved_qty IS NOT NULL AND r.approved_qty < s.qty_final) AS reduced,
                                      COUNT(*) FILTER (WHERE r.approved_qty = 0) AS zeroed,
                                      COUNT(*) FILTER (WHERE r.approved_qty IS NOT NULL AND r.approved_qty > s.qty_final) AS increased,
                                      COALESCE(SUM(s.qty_final), 0) AS sent_qty, COALESCE(SUM(r.approved_qty), 0) AS approved_qty
                               FROM gdh_batch_snap s
                               LEFT JOIN gdh_batch_review r ON r.batch_id = s.batch_id AND r.part_code = s.part_code
                               WHERE s.batch_id = ANY(%s) AND s.stage = 'submitted' AND s.qty_final > 0 AND s.order_type <> ''
                               GROUP BY s.batch_id''', (done_ids,))
                for r in cur.fetchall():
                    results[r['batch_id']] = {k: (float(v) if k in ('sent_qty', 'approved_qty') else int(v)) for k, v in r.items() if k != 'batch_id'}
        csql, cparams = "SELECT status, COUNT(*) AS n FROM gdh_batches WHERE owner = '' AND status <> 'draft'", []
        if store:
            csql += ' AND store_code = %s'; cparams.append(store)
        cur.execute(csql + ' GROUP BY status', cparams)
        counts = {r['status']: int(r['n']) for r in cur.fetchall()}
        data = [_batch_json(b, {'submitted_sum': (sums.get(b['id']) or {}).get('submitted'),
                                'approved_sum': (sums.get(b['id']) or {}).get('approved'),
                                'by_type': types.get(b['id'], {}),
                                'urgent_parts': (types.get(b['id'], {}).get('Khẩn') or {}).get('parts', 0),
                                'waiting_min': int(b['waiting_min']) if b.get('waiting_min') is not None else None,
                                'result': results.get(b['id'])}) for b in batches]
        if want in ('active', 'pending'):               # đơn Khẩn lên đầu, trong cùng nhóm thì đơn chờ lâu nhất lên trước
            data.sort(key=lambda o: (o['urgent_parts'] <= 0, -(o['waiting_min'] or 0)))
        arch_days, arch_pending = None, 0
        if request.args.get('counts_only') != '1':
            arch_days = _archive_days(cur)               # số ngày tự lưu trữ + xoá (hiện ở chú thích dưới danh sách)
        if session.get('role') == 'admin':               # số file Excel lưu trữ chưa tải về máy (huy hiệu tab "File Excel")
            try:
                cur.execute('SELECT COUNT(*) AS n FROM gdh_archives WHERE downloaded_at IS NULL')
                arch_pending = int(cur.fetchone()['n'])
            except Exception:
                db.rollback()
        arch_files = 0
        if session.get('role') == 'store':               # huy hiệu tab "File Excel" của user chi nhánh = tổng số file của chi nhánh mình
            try:
                cur.execute('SELECT COUNT(*) AS n FROM gdh_archives WHERE store_code = %s', (session.get('store_code'),))
                arch_files = int(cur.fetchone()['n'])
            except Exception:
                db.rollback()
        pcsql, pcparams = ("SELECT MAX(id) AS last_id, MAX(EXTRACT(EPOCH FROM ((NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\') - (CASE WHEN rereview_open THEN rereview_at ELSE submitted_at END))) / 60) AS oldest_min "
                           "FROM gdh_batches WHERE owner = '' AND status = 'pending'"), []
        if store:
            pcsql += ' AND store_code = %s'; pcparams.append(store)
        cur.execute(pcsql, pcparams)
        pr = cur.fetchone() or {}
        pending_info = {'last_id': pr.get('last_id'), 'oldest_min': int(pr['oldest_min']) if pr.get('oldest_min') is not None else None}
    finally:
        cur.close()
    return jsonify({'success': True, 'data': data, 'counts': counts, 'status_labels': STATUS_LABELS, 'pending_info': pending_info,
                    'archive_days': arch_days, 'archive_pending': arch_pending, 'archive_files': arch_files})


# ----------------------------------------------------------------------------
# 4a. ĐƠN KHẨN TỪ DANH SÁCH KHÁCH HÀNG
# Chi nhánh chọn các dòng khách "Chưa đặt" (không xin nội bộ) trong Danh sách đặt hàng (bo_orders - Supabase) rồi bấm
# "Gôm đơn khẩn": máy chủ tạo NGAY 1 đơn loại Khẩn ở trạng thái Chờ duyệt (không qua Nháp) trong CSDL chính (Neon).
# Admin thấy đơn ở Duyệt Đơn Hàng như mọi đơn đã đẩy, kèm bảng thông tin khách của từng dòng (gdh_urgent_lines).
# Dòng bo_orders được đánh dấu urgent_batch_id để không gửi khẩn trùng; thu hồi đơn / xoá đơn thì gỡ dấu.
# ----------------------------------------------------------------------------
def _orders_conn():
    """(odb, ocur) của CSDL đặt hàng khách (Supabase); lỗi cấu hình thì trả (None, None)."""
    try:
        from orders import get_orders_db
        odb = get_orders_db()
        return odb, odb.cursor()
    except Exception:
        traceback.print_exc()
        return None, None


def _urgent_release_marks(batch_id=None, bo_ids=None):
    """Gỡ dấu 'đã gửi khẩn' trên bo_orders: theo đơn (thu hồi/xoá) hoặc theo danh sách dòng đang giữ chỗ (urgent_batch_id = 0)."""
    odb, ocur = _orders_conn()
    if not odb:
        return
    try:
        if batch_id is not None:
            ocur.execute('UPDATE bo_orders SET urgent_batch_id = NULL WHERE urgent_batch_id = %s', (batch_id,))
        if bo_ids:
            ocur.execute('UPDATE bo_orders SET urgent_batch_id = NULL WHERE id = ANY(%s) AND urgent_batch_id = 0', (list(bo_ids),))
        odb.commit()
    except Exception:
        odb.rollback()
        traceback.print_exc()
    finally:
        ocur.close()


def _qty_int(q):
    """SL dạng chữ của khách ('2', '2 bộ', '1,5') -> số nguyên >= 1 (làm tròn lên)."""
    m = re.search(r'\d+(?:[.,]\d+)?', str(q or ''))
    v = float(m.group().replace(',', '.')) if m else 1.0
    return max(1, int(math.ceil(v - 1e-9)))


def _urgent_resolve(cur, items):
    """items = [(mã khách yêu cầu, SL)] -> [{order_code, order_qty, skip, locked_no_rep}] theo CÙNG quy tắc của bảng gôm:
    mã bị khoá có mã thay thế -> đặt bằng mã thay thế; mã CON -> mã CHA (làm tròn LÊN); mã không được đặt thì bỏ (skip)."""
    keys = list({_bkey(c) for c, _ in items if c})
    locks = {}
    if keys:
        cur.execute('SELECT part_code, is_locked, replacement_code FROM order_lock_items WHERE UPPER(TRIM(part_code)) = ANY(%s)', (keys,))
        for r in cur.fetchall():
            locks[_bkey(r['part_code'])] = (bool(r['is_locked']), (r['replacement_code'] or '').strip() or None)
    bundles = _load_bundles(cur)
    out = []
    for code, qty in items:
        base = (code or '').strip()
        skip = 'Mã không được đặt hàng' if _is_excluded_from_reorder(base) else None
        lk = locks.get(_bkey(base))
        if lk and lk[0] and lk[1]:
            base = lk[1]
        b = bundles.get(_bkey(base))
        if b:
            oc, oq = b[0], int(math.ceil(qty / b[1] - 1e-9))
        else:
            oc, oq = base, int(qty)
        out.append({'order_code': oc, 'order_qty': max(1, oq), 'skip': skip, 'locked_no_rep': bool(lk and lk[0] and not lk[1])})
    return out


_URGENT_BO_COLS = ('id, request_id, seq_no, part_code, part_name, quantity, customer_request_date, customer_name, '
                   'quote_no, vehicle_type, frame_number')
_URGENT_BO_WHERE = ("status = 'Chưa đặt' AND source IS DISTINCT FROM 'Xin nội bộ' "
                    "AND COALESCE(TRIM(part_code), '') <> '' AND urgent_batch_id IS NULL")


@gom_don_hang_bp.route('/api/gom-don-hang/urgent/candidates', methods=['GET'])
def gdh_urgent_candidates():
    """Các dòng khách đủ điều kiện gôm đơn khẩn của 1 chi nhánh: Chưa đặt, KHÔNG xin nội bộ, có mã hàng, chưa gửi khẩn.
    Kèm 'mã đặt' / 'SL đặt' dự kiến (đã quy mã thay thế / mã cha) để chi nhánh biết admin sẽ nhận gì."""
    db, cur = _ctx()
    try:
        store, err = _resolve_store(cur, request.args.get('store'))
        if err:
            return err
        odb, ocur = _orders_conn()
        if not odb:
            return jsonify({'error': 'Chưa kết nối được CSDL danh sách đặt hàng (kiểm tra ORDERS_DATABASE_URL).'}), 503
        try:
            ocur.execute(f'SELECT {_URGENT_BO_COLS} FROM bo_orders WHERE store_code = %s AND {_URGENT_BO_WHERE} '
                         'ORDER BY seq_no ASC NULLS LAST, id ASC LIMIT 1000', (store,))
            rows = ocur.fetchall()
        finally:
            ocur.close()
        qtys = [_qty_int(r['quantity']) for r in rows]
        res = _urgent_resolve(cur, [(r['part_code'], q) for r, q in zip(rows, qtys)]) if rows else []
        data = []
        for r, q, x in zip(rows, qtys, res):
            data.append({'id': r['id'], 'stt': r['seq_no'], 'part_code': (r['part_code'] or '').strip(), 'part_name': r['part_name'],
                         'qty': q, 'order_code': x['order_code'], 'order_qty': x['order_qty'], 'skip': x['skip'],
                         'locked_no_rep': x['locked_no_rep'],
                         'request_date': r['customer_request_date'].isoformat() if r['customer_request_date'] else None,
                         'customer_name': r['customer_name'], 'quote_no': r['quote_no'],
                         'vehicle_type': r['vehicle_type'], 'frame_number': r['frame_number']})
    finally:
        cur.close()
    return jsonify({'success': True, 'store': store, 'data': data})


@gom_don_hang_bp.route('/api/gom-don-hang/urgent/create', methods=['POST'])
def gdh_urgent_create():
    """Gôm đơn khẩn: items = [{id (dòng bo_orders), qty?}] -> tạo đơn Khẩn Chờ duyệt gửi thẳng admin.
    Máy chủ đọc lại dữ liệu khách từ bo_orders (không tin dữ liệu trình duyệt gửi lên) và giữ chỗ từng dòng bằng UPDATE có điều kiện
    nên 2 người bấm cùng lúc không gửi trùng 1 dòng."""
    payload = request.get_json(silent=True) or {}
    raw = payload.get('items')
    if not isinstance(raw, list) or not raw:
        return jsonify({'error': 'Chưa chọn dòng nào để gôm đơn khẩn.'}), 400
    if len(raw) > 500:
        return jsonify({'error': 'Mỗi đơn khẩn tối đa 500 dòng. Hãy chia thành nhiều đơn.'}), 400
    note = str(payload.get('note') or '').strip()[:500]
    want = {}
    for it in raw:
        try:
            bid = int((it or {}).get('id'))
            q = (it or {}).get('qty')
            want[bid] = None if q in (None, '') else int(math.ceil(float(q) - 1e-9))
        except (TypeError, ValueError):
            continue
    if not want:
        return jsonify({'error': 'Danh sách dòng không hợp lệ.'}), 400
    db, cur = _ctx()
    claimed, done = [], False
    try:
        store, err = _resolve_store(cur, payload.get('store'))
        if err:
            return err
        odb, ocur = _orders_conn()
        if not odb:
            return jsonify({'error': 'Chưa kết nối được CSDL danh sách đặt hàng (kiểm tra ORDERS_DATABASE_URL).'}), 503
        try:     # giữ chỗ: urgent_batch_id = 0 (tạm) cho các dòng còn đủ điều kiện, đọc lại thông tin khách từ chính CSDL
            ocur.execute(f'UPDATE bo_orders SET urgent_batch_id = 0 WHERE id = ANY(%s) AND store_code = %s AND {_URGENT_BO_WHERE} '
                         f'RETURNING {_URGENT_BO_COLS}', (list(want), store))
            rows = ocur.fetchall()
            odb.commit()
        except Exception:
            odb.rollback()
            raise
        finally:
            ocur.close()
        claimed = [r['id'] for r in rows]
        if not rows:
            return jsonify({'error': 'Các dòng đã chọn không còn đủ điều kiện (đã đặt, xin nội bộ hoặc đã gửi khẩn). Hãy tải lại danh sách.'}), 409
        rows.sort(key=lambda r: (r['seq_no'] is None, r['seq_no'] or 0, r['id']))
        qtys = [want[r['id']] if (want.get(r['id']) or 0) > 0 else _qty_int(r['quantity']) for r in rows]
        res = _urgent_resolve(cur, [(r['part_code'], q) for r, q in zip(rows, qtys)])
        skipped = [{'id': r['id'], 'part_code': r['part_code'], 'reason': x['skip']} for r, x in zip(rows, res) if x['skip']]
        keep = [(r, q, x) for r, q, x in zip(rows, qtys, res) if not x['skip']]
        if skipped:
            _urgent_release_marks(bo_ids=[s['id'] for s in skipped])
            claimed = [r['id'] for r, _, _ in keep]
        if not keep:
            return jsonify({'error': 'Các mã đã chọn đều thuộc nhóm không được đặt hàng nên không gôm được.', 'skipped': skipped}), 400
        agg = {}                                              # 1 mã đặt = 1 dòng trong đơn (nhiều khách cùng mã -> cộng SL)
        for r, q, x in keep:
            a = agg.setdefault(_bkey(x['order_code']), {'code': x['order_code'], 'name': r['part_name'], 'qty': 0})
            a['qty'] += x['order_qty']
            a['name'] = a['name'] or r['part_name']
        today = datetime.now(_VN_TZ).date()
        actor = _actor_name()
        cur.execute('''INSERT INTO gdh_batches (store_code, owner, period_from, period_to, forecast_weeks, filenames, uploaded_by,
                                                kind, status, submitted_by, submitted_at, submit_note)
                       VALUES (%s, '', %s, %s, %s, %s, %s, 'urgent', 'pending', %s, (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), %s) RETURNING id''',
                    (store, today, today, DEFAULT_FORECAST_WEEKS, 'Đơn khẩn từ danh sách khách hàng', actor, actor, note))
        bid = cur.fetchone()['id']
        execute_values(cur, '''INSERT INTO gdh_lines (batch_id, part_code, part_name, adj_qty, order_type, updated_by, updated_at)
                               VALUES %s''', [(bid, a['code'], a['name'], a['qty'], 'Khẩn', actor) for a in agg.values()],
                       template='(%s, %s, %s, %s, %s, %s, (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'))', page_size=500)
        execute_values(cur, '''INSERT INTO gdh_urgent_lines (batch_id, bo_order_id, request_id, seq_no, part_code, part_name, qty,
                                                            order_code, order_qty, customer_request_date, customer_name,
                                                            quote_no, vehicle_type, frame_number) VALUES %s''',
                       [(bid, r['id'], r['request_id'], r['seq_no'], (r['part_code'] or '').strip(), r['part_name'], q,
                         x['order_code'], x['order_qty'], r['customer_request_date'], r['customer_name'],
                         r['quote_no'], r['vehicle_type'], r['frame_number']) for r, q, x in keep], page_size=500)
        cur.execute('SELECT * FROM gdh_batches WHERE id = %s', (bid,))
        batch = cur.fetchone()
        n = _snapshot(cur, batch, 'submitted')                # 'đơn lúc đẩy' để admin duyệt / so sánh như đơn thường
        _log_event(cur, bid, 'draft', 'pending', note or 'Chi nhánh gôm đơn khẩn từ danh sách khách hàng')
        db.commit()
        done = True
        odb2, oc2 = _orders_conn()                            # gắn mã đơn khẩn vào các dòng khách (thay dấu giữ chỗ 0)
        if odb2:
            for attempt in (1, 2):
                try:
                    oc2.execute('UPDATE bo_orders SET urgent_batch_id = %s WHERE id = ANY(%s) AND urgent_batch_id = 0', (bid, claimed))
                    odb2.commit()
                    break
                except Exception:
                    odb2.rollback()
                    traceback.print_exc()
            oc2.close()
        audit_record('Gôm đơn khẩn gửi admin duyệt', 'Gôm đơn hàng', target=f'đợt {bid}',
                     summary=f'Đợt {bid} ({store}): đơn khẩn từ danh sách khách hàng, {len(keep)} dòng khách -> {n} mã đặt',
                     after={'status': 'pending', 'note': note}, extra={'so_dong_khach': len(keep), 'bo_skipped': len(skipped)})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
        if not done and claimed:                              # lỗi giữa chừng: trả các dòng đã giữ chỗ về trạng thái chưa gửi
            _urgent_release_marks(bo_ids=claimed)
    return jsonify({'success': True, 'batch_id': bid, 'status': 'pending', 'lines': len(keep), 'parts': n, 'skipped': skipped})


def _urgent_lines(cur, bid):
    cur.execute('SELECT * FROM gdh_urgent_lines WHERE batch_id = %s ORDER BY seq_no ASC NULLS LAST, id ASC', (bid,))
    return [{'stt': r['seq_no'], 'order_code': r['order_code'], 'order_qty': _f(r['order_qty']),
             'part_code': r['part_code'], 'part_name': r['part_name'], 'qty': _f(r['qty']),
             'request_date': r['customer_request_date'].isoformat() if r['customer_request_date'] else None,
             'customer_name': r['customer_name'], 'quote_no': r['quote_no'],
             'vehicle_type': r['vehicle_type'], 'frame_number': r['frame_number']} for r in cur.fetchall()]


@gom_don_hang_bp.route('/api/gom-don-hang/urgent/lines', methods=['GET'])
def gdh_urgent_lines_api():
    """Thông tin khách của đơn khẩn: STT, mã đặt, SL đặt, ngày khách yêu cầu, tên khách, số báo giá, loại xe, số khung.
    Admin xem mọi đơn; user chi nhánh chỉ xem đơn của chi nhánh mình (_get_batch kiểm tra)."""
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, request.args.get('batch_id'))
        if err:
            return err
        if (batch.get('kind') or 'regular') != 'urgent':
            return jsonify({'error': 'Đây không phải đơn khẩn từ danh sách khách hàng.'}), 400
        data = _urgent_lines(cur, batch['id'])
        info = _batch_json(batch)
    finally:
        cur.close()
    return jsonify({'success': True, 'batch': info, 'data': data})


# ----------------------------------------------------------------------------
# 4b. ĐẨY ĐƠN: TÓM TẮT TRƯỚC KHI ĐẨY + CHẶN TRÙNG KỲ; NHÁP DUYỆT CỦA ADMIN
# ----------------------------------------------------------------------------
def _dup_batches(cur, batch, kinds=None):
    """Các đơn KHÁC của cùng chi nhánh + cùng kỳ + CÙNG LOẠI ĐƠN đã đẩy đi (mỗi loại là một đơn riêng nên khác loại không tính trùng).
    blocking = đang Chờ duyệt / Đang duyệt (chặn hẳn); còn lại (Đã duyệt / Đã xem / Đã đặt) chỉ cảnh báo."""
    if not kinds:
        return []
    cur.execute('''SELECT DISTINCT b.id, b.status, b.submitted_at, b.submitted_by FROM gdh_batches b
                   JOIN gdh_batch_snap s ON s.batch_id = b.id AND s.stage = 'submitted' AND s.qty_final > 0 AND s.order_type = ANY(%s)
                   WHERE b.owner = '' AND b.store_code = %s AND b.period_from = %s AND b.period_to = %s AND b.id <> %s
                     AND COALESCE(b.kind, 'regular') = 'regular'
                     AND b.status = ANY(%s) ORDER BY b.id DESC''',
                (list(kinds), batch['store_code'], batch['period_from'], batch['period_to'], batch['id'], POST_SUBMIT_STATUSES))
    return [{'id': r['id'], 'status': r['status'], 'status_label': STATUS_LABELS.get(r['status'], r['status']),
             'submitted_at': _fmt_dt(r['submitted_at']), 'submitted_by': r['submitted_by'],
             'blocking': r['status'] in ('pending', 'reviewing')} for r in cur.fetchall()]


@gom_don_hang_bp.route('/api/gom-don-hang/submit-preview', methods=['GET'])
def gdh_submit_preview():
    """Chi nhánh xem tóm tắt TRƯỚC khi đẩy: số mã / SL / giá trị của TỪNG loại đơn trong phiên gôm (để chọn loại cần đẩy),
    đơn trùng kỳ của từng loại, số mã chưa chọn loại đơn (các mã này ở lại phiên gôm)."""
    blk = _need_role('store')
    if blk:
        return blk
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, request.args.get('batch_id'))
        if err:
            return err
        if (batch.get('status') or 'draft') != 'draft':
            return _bad_state(batch, 'Nháp')
        rows = compute_rows(cur, batch)
        t = _totals(rows)
        by_type = {k: {'parts': v['parts'], 'qty': v['qty'], 'amount': v['amount'],
                       'no_cost': sum(1 for r in rows if r['order_type'] == k and r['qty_final'] > 0 and r['amount'] is None)}
                   for k, v in t['by_type'].items()}
        dups_by_type = {}        # không còn cảnh báo/chặn đơn trùng loại: cho đẩy nhiều đơn cùng loại từ 1 phiên gôm
    finally:
        cur.close()
    now = datetime.now(_VN_TZ)
    names = {k: _order_name({'store_code': batch['store_code'], 'submitted_at': now}, [k]) for k in ORDER_TYPES}
    return jsonify({'success': True, 'batch': _batch_json(batch), 'by_type': by_type, 'dups_by_type': dups_by_type,
                    'blocking_by_type': {},
                    'order_names': names, 'typed': sum(v['parts'] for v in by_type.values()),
                    'qty': sum(v['qty'] for v in by_type.values()), 'amount': sum(v['amount'] for v in by_type.values()),
                    'unassigned_type': t['unassigned_type'], 'locked_no_replace': t['locked_no_replace'], 'no_cost': t['no_cost']})


@gom_don_hang_bp.route('/api/gom-don-hang/review-draft', methods=['POST'])
def gdh_review_draft():
    """Admin đang duyệt: lưu NHÁP SL duyệt + ghi chú từng mã lên máy chủ (không đổi trạng thái đơn)."""
    blk = _need_role('admin')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    items = payload.get('items')
    if not isinstance(items, list):
        return jsonify({'error': 'Thiếu dữ liệu nháp.'}), 400
    me = str(session.get('user') or '')
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        if (batch.get('status') or 'draft') != 'reviewing' or (batch.get('claimed_by') or '') != me:
            return _bad_state(batch, 'Đang duyệt')
        sent = _snap_load(cur, batch['id'], 'submitted')
        rows = []
        for it in items:
            code = str((it or {}).get('part_code') or '').strip()
            if code not in sent:
                continue
            try:
                q = (it or {}).get('approved_qty')
                q = None if q is None or q == '' else max(0.0, float(q))
            except (TypeError, ValueError):
                q = None
            rows.append((batch['id'], code, q, str((it or {}).get('note') or '').strip()[:500]))
        cur.execute('DELETE FROM gdh_review_draft WHERE batch_id = %s', (batch['id'],))
        if rows:
            execute_values(cur, 'INSERT INTO gdh_review_draft (batch_id, part_code, approved_qty, note) VALUES %s', rows, page_size=1000)
        db.commit()
        cur.execute('SELECT MAX(updated_at) AS at FROM gdh_review_draft WHERE batch_id = %s', (batch['id'],))
        at = _fmt_dt((cur.fetchone() or {}).get('at'))
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'saved': len(rows), 'saved_at': at})


# ----------------------------------------------------------------------------
# 5. XUẤT EXCEL CỦA 1 ĐỢT
# ----------------------------------------------------------------------------
def _autosize(ws):
    for col in ws.columns:
        w = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(w + 2, 10), 50)


def _xlsx_bytes(sheets, freeze='A2'):
    import pandas as pd
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        for name, df in sheets.items():
            df.to_excel(w, index=False, sheet_name=name[:31])
        for ws in w.book.worksheets:
            _autosize(ws)
            ws.freeze_panes = freeze
    return out.getvalue()


def _send_xlsx(sheets, filename, freeze='A2'):
    return send_file(io.BytesIO(_xlsx_bytes(sheets, freeze)), as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@gom_don_hang_bp.route('/api/gom-don-hang/export', methods=['GET'])
def gdh_export():
    """kind=full: bảng gôm đầy đủ (các mã có đề xuất / SL cuối / +-);  kind=hvn: file đặt hàng, mỗi loại đơn 1 sheet
    (Line#, Order Number, Part#, Quantity Requested; mã bị khoá -> đặt bằng mã thay thế)."""
    import pandas as pd
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, request.args.get('batch_id'))
        if err:
            return err
        is_store_hvn = session.get('role') == 'store' and request.args.get('kind') == 'hvn'
        if is_store_hvn and batch.get('rereview_open') and (batch.get('status') or 'draft') in ('pending', 'reviewing'):
            return jsonify({'error': 'Đơn đang chờ admin duyệt lại theo yêu cầu của bạn nên chưa tải file đặt hàng được. '
                                     'Hãy chờ admin duyệt xong rồi tải để đặt đúng SL mới.'}), 409
        if is_store_hvn and (batch.get('status') or 'draft') not in ('approved', 'viewed', 'ordered'):
            return jsonify({'error': 'Đơn chưa được admin duyệt xong nên chưa tải file đặt hàng được. '
                                     'Hãy bấm \"Đẩy đơn cho admin\" và chờ duyệt.'}), 409
        rows = compute_rows(cur, batch, with_extra=True)
        review = {}
        if request.args.get('kind') == 'hvn' and not batch['owner'] and (batch.get('status') or 'draft') in ('approved', 'viewed', 'ordered'):
            review = _review_map(cur, batch['id'], [r['part_code'] for r in rows if r['qty_final'] > 0])
            review['__has__'] = True
        if is_store_hvn and (batch.get('status') or 'draft') in ('approved', 'viewed') \
                and any(r['order_type'] in ORDER_TYPES and r['qty_final'] > 0 for r in rows):
            # Cửa hàng bấm "Tải đơn về" -> Đã đặt (đồng thời coi như đã xem nếu chưa mở bảng so sánh)
            try:
                cur.execute('''UPDATE gdh_batches SET status = 'ordered', ordered_by = %s, ordered_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'),
                                      viewed_by = COALESCE(viewed_by, %s), viewed_at = COALESCE(viewed_at, (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'))
                               WHERE id = %s AND status IN ('approved', 'viewed')''',
                            (_actor_name(), _actor_name(), batch['id']))
                if cur.rowcount == 1:
                    _log_event(cur, batch['id'], batch['status'], 'ordered', 'Tải file đặt hàng')
                    db.commit()
                    audit_record('Tải đơn đã duyệt (Đã đặt)', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                                 summary=f"Đợt {batch['id']} ({batch['store_code']}): cửa hàng tải đơn về -> Đã đặt",
                                 before={'status': batch['status']}, after={'status': 'ordered'})
                else:
                    db.rollback()
            except Exception:
                db.rollback()
                raise
    finally:
        cur.close()
    stamp = f"{batch['store_code']}-{batch['period_from']:%Y%m%d}-{batch['period_to']:%Y%m%d}"
    if request.args.get('kind') == 'hvn':
        only = (request.args.get('order_type') or '').strip()
        sheets = {}
        has_review = bool(review.get('__has__'))
        for t in ([only] if only in ORDER_TYPES else ORDER_TYPES):
            merged = {}
            for r in rows:
                if r['order_type'] == t and r['qty_final'] > 0:
                    if has_review:
                        # Đơn đã duyệt: Quantity Requested = SL admin duyệt (cùng đơn vị với SL đặt / mã đặt);
                        # chỉ lấy mã có SL duyệt > 0 (mã admin không nhập SL duyệt coi như không duyệt).
                        q = review.get(r['part_code'], (None, ''))[0]
                        if q is None or q <= 0:
                            continue
                    else:
                        q = r['order_qty']
                    merged[r['order_code']] = merged.get(r['order_code'], 0) + q
            if merged:
                sheets[t] = pd.DataFrame([{'Line#': i, 'Order Number': '', 'Part#': p, 'Quantity Requested': q}
                                          for i, (p, q) in enumerate(merged.items(), 1)])
        if not sheets:
            return jsonify({'error': ('Admin chưa duyệt mã nào có SL duyệt > 0.' if has_review
                                      else 'Chưa có mã hàng nào có Loại đơn và SL cuối > 0.')}), 400
        if review.get('__has__'):                       # đơn đã duyệt: kèm SL gửi / SL đặt / SL duyệt / ghi chú của admin
            lst = []
            for t in ([only] if only in ORDER_TYPES else ORDER_TYPES):
                for r in sorted((x for x in rows if x['order_type'] == t and x['qty_final'] > 0), key=lambda x: x['part_code']):
                    aq, nt = review.get(r['part_code'], (None, ''))
                    lst.append({'STT': len(lst) + 1, 'Loại đơn': t, 'Mã hàng': r['part_code'], 'Tên hàng': r['part_name'],
                                'Mã đặt': r['order_code'], 'SL gửi (SL cuối)': r['qty_final'], 'SL đặt': r['order_qty'],
                                'SL duyệt': aq, 'Ghi chú của admin': nt})
            sheets['Kết quả duyệt'] = pd.DataFrame(lst)
        return _send_xlsx(sheets, f'dat-hang-{stamp}.xlsx')
    sel = [r for r in rows if r['suggest'] > 0 or r['qty_final'] > 0 or r['adj'] or r['order_type'] or r['note']]
    if not sel:
        return jsonify({'error': 'Chưa có dòng nào có đề xuất đặt hàng.'}), 400
    has_inv_any = any(r['stock_by_store'] for r in sel)
    df = pd.DataFrame([{
        'Mã hàng': r['part_code'], 'Tên hàng': r['part_name'], 'ĐVT': r['unit'],
        'Khoá đặt hàng': 'Khoá' if r['locked'] else '', 'Mã thay thế': r['lock_replace'] or '',
        'Tần suất': r['group'], 'TB bán/tháng': r['avg_month'], 'TB bán/tuần': r['avg_week'],
        'Xuất/Bán': r['sales'], 'Cuối kỳ': r['closing'],
        'SL đề xuất': r['suggest'], 'Cộng/trừ thêm': r['adj'], 'SL cuối': r['qty_final'],
        'Loại đơn': r['order_type'], 'Mã đặt': r['order_code'] if r['qty_final'] > 0 else '',
        'SL đặt': r['order_qty'] if r['qty_final'] > 0 else '',
        'Quy cách': (f"1 {r['bundle_parent']} = {r['bundle_ratio']:g}" if r['bundle_parent'] else ''),
        'Ghi chú': r['note'], 'Giá vốn': r['ord_cost'], 'Thành tiền': r['amount'],
        **{f'Tồn {k}': (r['stock_by_store'].get(k, 0) if has_inv_any else None) for k in STORE_COLS},
    } for r in sel])
    return _send_xlsx({'Gôm đơn hàng': df}, f'gom-don-hang-{stamp}.xlsx', freeze='C2')


# ----------------------------------------------------------------------------
# 5b. LƯU TRỮ TỰ ĐỘNG: xuất Excel -> lưu gdh_archives -> xoá đơn (Đã duyệt / Đã xem / Đã đặt quá N ngày)
#     Tải về máy: thư mục <chi nhánh>/<loại đơn>/<file>.xlsx (ZIP, hoặc ghi thẳng vào thư mục đã chọn ở trình duyệt)
# ----------------------------------------------------------------------------
def _archive_days(cur):
    try:
        v = int(float(_get_app_setting(cur, 'gdh_archive_days') or ARCHIVE_DEFAULT_DAYS))
    except (TypeError, ValueError):
        v = ARCHIVE_DEFAULT_DAYS
    return min(3650, max(1, v))


def _archive_files_for_batch(cur, b):
    """[(loại đơn, tên đơn, tên file, bytes, số mã, tổng SL, giá trị)] - mỗi loại đơn 1 file Excel.
    Lấy từ ảnh chụp lúc chi nhánh đẩy (gdh_batch_snap) + kết quả duyệt + nhật ký, nên không phụ thuộc bảng quy cách / khoá hiện tại."""
    import pandas as pd
    snap = _snap_load(cur, b['id'], 'submitted')
    rev = _review_map(cur, b['id'])
    cur.execute('SELECT at, actor, from_status, to_status, note FROM gdh_batch_events WHERE batch_id = %s ORDER BY id', (b['id'],))
    events = cur.fetchall()
    items = [(c, v) for c, v in sorted(snap.items()) if v['qty_final'] > 0 and v['order_type'] in ORDER_TYPES]
    types = [t for t in ORDER_TYPES if any(v['order_type'] == t for _, v in items)] or ['Khác']
    oname = _order_name(b, types if types != ['Khác'] else [])
    stamp = b.get('approved_at') or b.get('submitted_at') or datetime.now(_VN_TZ).replace(tzinfo=None)
    out = []
    for t in types:
        its = [(c, v) for c, v in items if v['order_type'] == t]
        qty = sum(v['qty_final'] for _, v in its)
        amount = sum(v['amount'] or 0.0 for _, v in its)
        approved_qty = sum((rev.get(c, (None, ''))[0] or 0) for c, _ in its)
        info = [
            ('Tên đơn', oname), ('Chi nhánh', b['store_code']), ('Loại đơn', t), ('Mã đợt gôm', b['id']),
            ('Kỳ số bán', f"{b['period_from']:%d/%m/%Y} - {b['period_to']:%d/%m/%Y}"),
            ('Trạng thái lúc lưu trữ', STATUS_LABELS.get(b.get('status'), b.get('status'))),
            ('Đẩy bởi', b.get('submitted_by')), ('Đẩy lúc', _fmt_dt(b.get('submitted_at'))), ('Ghi chú của chi nhánh', b.get('submit_note') or ''),
            ('Admin lấy về duyệt', _display_name(b.get('claimed_by'))), ('Lấy về lúc', _fmt_dt(b.get('claimed_at'))),
            ('Duyệt bởi', b.get('approved_by')), ('Duyệt lúc', _fmt_dt(b.get('approved_at'))), ('Ghi chú của admin', b.get('review_note') or ''),
            ('Chi nhánh xem lúc', _fmt_dt(b.get('viewed_at'))), ('Đặt (tải file) bởi', b.get('ordered_by')), ('Đặt lúc', _fmt_dt(b.get('ordered_at'))),
            ('Số mã', len(its)), ('Tổng SL gửi', qty), ('Tổng SL duyệt', approved_qty), ('Giá trị theo giá vốn (đ)', amount),
            ('Lưu trữ lúc', datetime.now(_VN_TZ).strftime('%d/%m/%Y %H:%M')),
        ]
        result = [{'STT': i, 'Mã hàng': c, 'Tên hàng': v['part_name'], 'Mã đặt': v['order_code'], 'SL gửi (SL cuối)': v['qty_final'],
                   'SL đặt': v['order_qty'], 'SL duyệt': rev.get(c, (None, ''))[0], 'Ghi chú của admin': rev.get(c, (None, ''))[1],
                   'Ghi chú của chi nhánh': v['note'], 'Giá vốn': v['unit_cost'], 'Thành tiền': v['amount']}
                  for i, (c, v) in enumerate(its, 1)]
        merged = {}
        for c, v in its:
            k = v['order_code'] or c
            if rev:                    # đơn đã duyệt: Quantity Requested = SL admin duyệt (giống file cửa hàng tải về); mã không có SL duyệt > 0 thì bỏ
                q = rev.get(c, (None, ''))[0]
                if q is None or q <= 0:
                    continue
            else:
                q = v['order_qty'] if v['order_qty'] is not None else v['qty_final']
            merged[k] = merged.get(k, 0) + q
        hvn = [{'Line#': i, 'Order Number': '', 'Part#': p, 'Quantity Requested': q} for i, (p, q) in enumerate(merged.items(), 1)]
        hist = [{'Lúc': _fmt_dt(e['at']), 'Người thao tác': e['actor'], 'Từ': STATUS_LABELS.get(e['from_status'], e['from_status']),
                 'Sang': STATUS_LABELS.get(e['to_status'], e['to_status']), 'Ghi chú': e['note']} for e in events]
        sheets = {'Thông tin': pd.DataFrame(info, columns=['Mục', 'Giá trị']),
                  'Kết quả duyệt': pd.DataFrame(result, columns=['STT', 'Mã hàng', 'Tên hàng', 'Mã đặt', 'SL gửi (SL cuối)', 'SL đặt', 'SL duyệt',
                                                                  'Ghi chú của admin', 'Ghi chú của chi nhánh', 'Giá vốn', 'Thành tiền']),
                  'File đặt hàng': pd.DataFrame(hvn, columns=['Line#', 'Order Number', 'Part#', 'Quantity Requested']),
                  'Lịch sử': pd.DataFrame(hist, columns=['Lúc', 'Người thao tác', 'Từ', 'Sang', 'Ghi chú'])}
        fname = f"{b['store_code']}_{_TYPE_SLUG.get(t, 'Khac')}_{stamp:%Y%m%d-%H%M}_don{b['id']}.xlsx"
        out.append((t, oname, fname, _xlsx_bytes(sheets), len(its), qty, amount))
    return out


def _archive_batch(db, cur, b):
    """Xuất Excel + lưu + xoá đơn trong CÙNG 1 transaction: lưu lỗi thì đơn còn nguyên, không bao giờ xoá khi chưa có file."""
    files = _archive_files_for_batch(cur, b)
    for t, oname, fname, data, parts, qty, amount in files:
        cur.execute('''INSERT INTO gdh_archives (batch_id, store_code, order_type, order_name, status, period_from, period_to,
                                                 submitted_at, approved_at, ordered_at, parts, qty, amount, filename, file_data)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (b['id'], b['store_code'], t, oname, b.get('status'), b['period_from'], b['period_to'],
                     b.get('submitted_at'), b.get('approved_at'), b.get('ordered_at'), parts, qty, amount, fname, psycopg2.Binary(data)))
    cur.execute('DELETE FROM gdh_batches WHERE id = %s', (b['id'],))        # gdh_lines / snap / review / events xoá theo (CASCADE)
    db.commit()
    return len(files)


def run_gdh_archive_job(force=False, days=None):
    """Lưu trữ + xoá đơn gôm Đã duyệt / Đã xem (phía admin) và Đã đặt (phía chi nhánh) đã quá hạn (mặc định 40 ngày kể từ lúc admin duyệt xong).
    File Excel xuất ra gắn status lúc lưu trữ: admin tự tải file Đã duyệt/Đã xem, chi nhánh tự tải file Đã đặt (xem gdhUArcAutoDownload).
    days=0: lưu trữ NGAY mọi đơn đủ trạng thái. Đơn Nháp / Chờ duyệt / Đang duyệt không bao giờ bị đụng tới."""
    with _flask_app.app_context():
        db = get_db()
        _ensure_tables(db)
        cur = db.cursor()
        got = False
        try:
            cur.execute('SELECT pg_try_advisory_lock(%s) AS locked', (_ARCHIVE_LOCK_KEY,))
            if not cur.fetchone()['locked']:
                return {'status': 'skipped', 'reason': 'another worker is already running this job'}
            got = True
            if not force:
                last = _get_app_setting(cur, 'gdh_archive_last_run')
                if last:
                    try:
                        if datetime.now() - datetime.fromisoformat(last) < timedelta(hours=_ARCHIVE_MIN_GAP_HOURS):
                            return {'status': 'skipped', 'reason': 'not due yet', 'last_run': last}
                    except ValueError:
                        pass
            d = _archive_days(cur) if days is None else max(0, int(days))
            n_batch = n_file = 0
            errors, skip = [], [0]
            while n_batch + len(errors) < 500:
                cur.execute('''SELECT * FROM gdh_batches
                               WHERE owner = '' AND status = ANY(%s) AND id <> ALL(%s)
                                 AND COALESCE(approved_at, submitted_at) < (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\') - make_interval(days => %s)
                               ORDER BY id LIMIT 25''', (list(ARCHIVE_STATUSES), skip, d))
                batches = cur.fetchall()
                if not batches:
                    break
                for b in batches:
                    try:
                        n_file += _archive_batch(db, cur, b)
                        n_batch += 1
                    except Exception as e:
                        db.rollback()
                        traceback.print_exc()
                        errors.append(f"đợt {b['id']}: {e}")
                        skip.append(b['id'])
            _set_app_setting(cur, 'gdh_archive_last_run', datetime.now().isoformat())
            db.commit()
            if n_batch or errors:
                print(f'[gdh] lưu trữ tự động: {n_batch} đơn -> {n_file} file Excel, {len(errors)} lỗi (quá {d} ngày)')
            return {'status': 'ok', 'days': d, 'batches': n_batch, 'files': n_file, 'errors': errors}
        except Exception as e:
            db.rollback()
            traceback.print_exc()
            return {'status': 'error', 'error': str(e)}
        finally:
            if got:
                try:
                    cur.execute('SELECT pg_advisory_unlock(%s)', (_ARCHIVE_LOCK_KEY,))
                    db.commit()
                except Exception:
                    pass


def _gdh_archive_scheduler_loop():
    """Nền: sau khi app khởi động rồi cứ mỗi 6 giờ xem đã tới hạn chưa (job tự bỏ qua nếu mới chạy trong ~20 giờ qua)."""
    time.sleep(150)
    while True:
        try:
            run_gdh_archive_job()
        except Exception:
            traceback.print_exc()
        time.sleep(6 * 60 * 60)


threading.Thread(target=_gdh_archive_scheduler_loop, daemon=True).start()


def _arch_row(r):
    return {'id': r['id'], 'batch_id': r['batch_id'], 'store': r['store_code'], 'type': r['order_type'],
            'folder': f"{r['store_code']}/{r['order_type']}", 'name': r['order_name'], 'status': r['status'],
            'status_label': STATUS_LABELS.get(r['status'], r['status']),
            'from': r['period_from'].isoformat() if r['period_from'] else None, 'to': r['period_to'].isoformat() if r['period_to'] else None,
            'approved_at': _fmt_dt(r['approved_at']), 'ordered_at': _fmt_dt(r['ordered_at']), 'archived_at': _fmt_dt(r['archived_at']),
            'parts': r['parts'], 'qty': float(r['qty'] or 0), 'amount': float(r['amount'] or 0), 'filename': r['filename'],
            'size': int(r['size'] or 0), 'downloaded_at': _fmt_dt(r['downloaded_at']), 'downloaded_by': r['downloaded_by']}


def _arch_access():
    """(store_code_bị_khoá, None) hoặc (None, (response, code)).
    Admin xem được file lưu trữ của mọi chi nhánh (store_code_bị_khoá = ''); user chi nhánh chỉ xem/tải file của CHI NHÁNH MÌNH."""
    role = session.get('role')
    if 'user' not in session or role not in ('admin', 'store'):
        return None, (jsonify({'error': 'Forbidden'}), 403)
    if role == 'store':
        code = (session.get('store_code') or '').strip().upper()
        if not code:
            return None, (jsonify({'error': 'Tài khoản chưa gắn chi nhánh.'}), 403)
        return code, None
    return '', None


def _arch_filters(args, lock_store=''):
    sql, params = '', []
    st = (lock_store or args.get('store') or '').strip().upper()      # user chi nhánh: luôn bị khoá vào chi nhánh của mình
    if st:
        sql += ' AND store_code = %s'; params.append(st)
    ty = (args.get('type') or '').strip()
    if ty:
        sql += ' AND order_type = %s'; params.append(ty)
    sts = [x for x in (args.get('status') or '').lower().split(',') if x.strip() in STATUS_LABELS]   # vd status=ordered | approved,viewed
    if sts:
        sql += ' AND status = ANY(%s)'; params.append([x.strip() for x in sts])
    if args.get('only_new') == '1':
        sql += ' AND store_downloaded_at IS NULL' if lock_store else ' AND downloaded_at IS NULL'
    for key, op in (('from', '>='), ('to', '<=')):          # lọc theo THÁNG/NGÀY ĐẨY ĐƠN (submitted_at); thiếu thì lấy ngày duyệt / ngày lưu trữ
        v = (args.get(key) or '').strip()
        if v:
            try:
                d = _parse_date(v)
            except ValueError:
                continue
            sql += f' AND COALESCE(submitted_at, approved_at, archived_at)::date {op} %s'; params.append(d)
    return sql, params


@gom_don_hang_bp.route('/api/gom-don-hang/archives', methods=['GET'])
def gdh_archives():
    """Danh sách file Excel lưu trữ. Admin: mọi chi nhánh + cài đặt số ngày + số đơn sắp bị lưu trữ; user chi nhánh: chỉ file của chi nhánh mình."""
    lock, blk = _arch_access()
    if blk:
        return blk
    db, cur = _ctx()
    try:
        fsql, fparams = _arch_filters(request.args, lock)
        cur.execute('''SELECT id, batch_id, store_code, order_type, order_name, status, period_from, period_to, approved_at, ordered_at,
                              parts, qty, amount, filename, archived_at,
                              CASE WHEN %s THEN store_downloaded_at ELSE downloaded_at END AS downloaded_at,
                              CASE WHEN %s THEN store_downloaded_by ELSE downloaded_by END AS downloaded_by, octet_length(file_data) AS size
                       FROM gdh_archives WHERE TRUE''' + fsql + ' ORDER BY archived_at DESC, id DESC LIMIT 500', [bool(lock), bool(lock)] + fparams)
        data = [_arch_row(r) for r in cur.fetchall()]
        if lock:                                    # user chi nhánh: đếm theo cờ "chi nhánh đã tải" riêng, không đụng cờ của admin
            cur.execute('SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE store_downloaded_at IS NULL) AS pending FROM gdh_archives WHERE store_code = %s', (lock,))
        else:
            cur.execute('SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE downloaded_at IS NULL) AS pending FROM gdh_archives')
        c = cur.fetchone()
        days = _archive_days(cur)
        lsql, lparams = '', [days, list(ARCHIVE_STATUSES)]
        if lock:
            lsql = ' AND store_code = %s'; lparams.append(lock)
        cur.execute('''SELECT COUNT(*) AS live,
                              COUNT(*) FILTER (WHERE COALESCE(approved_at, submitted_at) < (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\') - make_interval(days => %s)) AS due
                       FROM gdh_batches WHERE owner = '' AND status = ANY(%s)''' + lsql, lparams)
        lv = cur.fetchone()
        last = _get_app_setting(cur, 'gdh_archive_last_run')
    finally:
        cur.close()
    if lock:                                        # user không cần biết giờ chạy job nội bộ
        last = None
    return jsonify({'success': True, 'data': data, 'total': int(c['total']), 'pending_download': int(c['pending']),
                    'days': days, 'live': int(lv['live']), 'due': int(lv['due']), 'last_run': last})


@gom_don_hang_bp.route('/api/gom-don-hang/archives/<int:aid>/file', methods=['GET'])
def gdh_archive_file(aid):
    lock, blk = _arch_access()
    if blk:
        return blk
    db, cur = _ctx()
    try:
        cur.execute('SELECT filename, file_data, store_code FROM gdh_archives WHERE id = %s', (aid,))
        r = cur.fetchone()
        if not r or (lock and r['store_code'] != lock):          # file của chi nhánh khác -> coi như không tồn tại
            return jsonify({'error': 'Không tìm thấy file lưu trữ.'}), 404
        if request.args.get('mark') == '1' and lock:               # chi nhánh: đánh dấu cờ riêng của chi nhánh
            cur.execute('UPDATE gdh_archives SET store_downloaded_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), store_downloaded_by = %s WHERE id = %s', (_actor_name(), aid))
            db.commit()
        elif request.args.get('mark') == '1':
            cur.execute('UPDATE gdh_archives SET downloaded_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), downloaded_by = %s WHERE id = %s', (_actor_name(), aid))
            db.commit()
        data = bytes(r['file_data'])
    finally:
        cur.close()
    return send_file(io.BytesIO(data), as_attachment=True, download_name=r['filename'],
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@gom_don_hang_bp.route('/api/gom-don-hang/archives/zip', methods=['GET'])
def gdh_archive_zip():
    """ZIP có sẵn cấu trúc thư mục <chi nhánh>/<loại đơn>/<file>.xlsx (giải nén là đủ thư mục). ids=1,2,3 hoặc only_new=1."""
    lock, blk = _arch_access()
    if blk:
        return blk
    ids = [int(x) for x in (request.args.get('ids') or '').split(',') if x.strip().isdigit()][:1000]
    db, cur = _ctx()
    try:
        if ids:
            lsql, lparams = ('', [ids]) if not lock else (' AND store_code = %s', [ids, lock])
            cur.execute('SELECT id, store_code, order_type, filename, file_data FROM gdh_archives WHERE id = ANY(%s)' + lsql + ' ORDER BY id', lparams)
        else:
            fsql, fparams = _arch_filters(request.args, lock)
            cur.execute('SELECT id, store_code, order_type, filename, file_data FROM gdh_archives WHERE TRUE' + fsql + ' ORDER BY id LIMIT 1000', fparams)
        rows = cur.fetchall()
        if not rows:
            return jsonify({'error': 'Không có file lưu trữ nào để tải.'}), 404
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as z:           # .xlsx vốn đã nén, khỏi nén lần nữa cho nhanh
            for r in rows:
                z.writestr(f"{r['store_code']}/{r['order_type']}/{r['filename']}", bytes(r['file_data']))
        if lock:
            cur.execute('UPDATE gdh_archives SET store_downloaded_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), store_downloaded_by = %s WHERE id = ANY(%s) AND store_code = %s', (_actor_name(), [r['id'] for r in rows], lock))
        else:
            cur.execute('UPDATE gdh_archives SET downloaded_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), downloaded_by = %s WHERE id = ANY(%s)', (_actor_name(), [r['id'] for r in rows]))
        db.commit()
    finally:
        cur.close()
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"luu-tru-don-gom-{datetime.now(_VN_TZ):%Y%m%d-%H%M}.zip", mimetype='application/zip')


@gom_don_hang_bp.route('/api/gom-don-hang/archives/mark-downloaded', methods=['POST'])
def gdh_archive_mark():
    """Trình duyệt báo đã ghi xong file vào thư mục trên máy (File System Access API)."""
    lock, blk = _arch_access()
    if blk:
        return blk
    ids = [int(x) for x in ((request.get_json(silent=True) or {}).get('ids') or []) if str(x).isdigit()][:2000]
    db, cur = _ctx()
    try:
        if ids and lock:
            cur.execute('UPDATE gdh_archives SET store_downloaded_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), store_downloaded_by = %s WHERE id = ANY(%s) AND store_code = %s', (_actor_name(), ids, lock))
            db.commit()
        elif ids:
            cur.execute('UPDATE gdh_archives SET downloaded_at = (NOW() AT TIME ZONE \'Asia/Ho_Chi_Minh\'), downloaded_by = %s WHERE id = ANY(%s)', (_actor_name(), ids))
            db.commit()
    finally:
        cur.close()
    return jsonify({'success': True, 'marked': len(ids)})


@gom_don_hang_bp.route('/api/gom-don-hang/archives/run-now', methods=['POST'])
def gdh_archive_run_now():
    """Lưu trữ ngay (admin). days=0: mọi đơn Đã duyệt / Đã xem / Đã đặt; bỏ trống: theo số ngày đã cài."""
    blk = _need_role('admin')
    if blk:
        return blk
    raw = (request.get_json(silent=True) or {}).get('days')
    try:
        days = None if raw in (None, '') else min(3650, max(0, int(raw)))
    except (TypeError, ValueError):
        return jsonify({'error': 'Số ngày không hợp lệ.'}), 400
    res = run_gdh_archive_job(force=True, days=days)
    if res.get('status') == 'ok':
        audit_record('Lưu trữ ngay đơn gôm', 'Gôm đơn hàng', target='lưu trữ',
                     summary=f"Lưu trữ {res['batches']} đơn -> {res['files']} file Excel (quá {res['days']} ngày), {len(res['errors'])} lỗi")
    return jsonify(dict(success=res.get('status') == 'ok', **res))


@gom_don_hang_bp.route('/api/gom-don-hang/archives/settings', methods=['POST'])
def gdh_archive_settings():
    blk = _need_role('admin')
    if blk:
        return blk
    try:
        days = int(float((request.get_json(silent=True) or {}).get('days')))
    except (TypeError, ValueError):
        return jsonify({'error': 'Số ngày không hợp lệ.'}), 400
    if not 1 <= days <= 3650:
        return jsonify({'error': 'Số ngày phải từ 1 đến 3650.'}), 400
    db, cur = _ctx()
    try:
        _set_app_setting(cur, 'gdh_archive_days', str(days))
        db.commit()
    finally:
        cur.close()
    audit_record('Đổi số ngày lưu trữ đơn gôm', 'Gôm đơn hàng', target='lưu trữ', summary=f'Số ngày tự lưu trữ + xoá: {days}')
    return jsonify({'success': True, 'days': days})


# ----------------------------------------------------------------------------
# 6. DASHBOARD ĐƠN HÀNG
# ----------------------------------------------------------------------------
def _dashboard(cur, args):
    """(dict, None) hoặc (None, (response, code)). Chỉ tính các đợt có kỳ bán NẰM TRONG [from, to]."""
    store, err = _resolve_store(cur, args.get('store'), allow_all=True)
    if err:
        return None, err
    try:
        d_from, d_to = _parse_date(args.get('from')), _parse_date(args.get('to'))
    except ValueError:
        return None, (jsonify({'error': 'Ngày không hợp lệ (định dạng YYYY-MM-DD).'}), 400)
    if not d_from and not d_to:
        today = datetime.now(_VN_TZ).date()
        d_from, d_to = today.replace(day=1), today.replace(day=calendar.monthrange(today.year, today.month)[1])
    d_from, d_to = d_from or date.min, d_to or date.max
    if d_from > d_to:
        return None, (jsonify({'error': 'Từ ngày phải trước Đến ngày.'}), 400)
    otype = (args.get('order_type') or '').strip()
    if otype and otype not in ORDER_TYPES:
        return None, (jsonify({'error': 'Loại đơn không hợp lệ.'}), 400)

    sql = 'SELECT * FROM gdh_batches WHERE period_from >= %s AND period_to <= %s AND owner = %s'
    params = [d_from, d_to, _owner_for(args.get('space'))]
    if store:
        sql += ' AND store_code = %s'
        params.append(store)
    cur.execute(sql + ' ORDER BY store_code, period_from', params)
    batches = cur.fetchall()

    by_type = {t: {'codes': set(), 'qty': 0, 'amount': 0.0} for t in ORDER_TYPES}
    by_store, parts, blist = {}, {}, []
    for b in batches:
        b_parts = b_qty = 0
        b_amount = 0.0
        for r in compute_rows(cur, b, order_only=True):
            if r['qty_final'] <= 0 or r['order_type'] not in by_type:
                continue
            t = by_type[r['order_type']]                       # thẻ theo loại đơn: không lọc theo loại đã chọn
            t['codes'].add(r['part_code']); t['qty'] += r['qty_final']; t['amount'] += r['amount'] or 0.0
            if otype and r['order_type'] != otype:
                continue
            amt = r['amount'] or 0.0
            s = by_store.setdefault(b['store_code'], {'codes': set(), 'qty': 0, 'amount': 0.0,
                                                      'types': {x: {'qty': 0, 'amount': 0.0} for x in ORDER_TYPES}})
            s['codes'].add(r['part_code']); s['qty'] += r['qty_final']; s['amount'] += amt
            s['types'][r['order_type']]['qty'] += r['qty_final']; s['types'][r['order_type']]['amount'] += amt
            p = parts.setdefault((r['order_type'], r['part_code']), {
                'order_type': r['order_type'], 'part_code': r['part_code'], 'part_name': r['part_name'],
                'unit': r['unit'], 'qty': 0, 'amount': 0.0, 'no_cost': False, 'stores': {}})
            p['qty'] += r['qty_final']; p['amount'] += amt
            p['no_cost'] = p['no_cost'] or r['amount'] is None
            p['stores'][b['store_code']] = p['stores'].get(b['store_code'], 0) + r['qty_final']
            b_parts += 1; b_qty += r['qty_final']; b_amount += amt
        blist.append({'id': b['id'], 'store': b['store_code'], 'from': b['period_from'].isoformat(),
                      'to': b['period_to'].isoformat(), 'filenames': b['filenames'],
                      'parts': b_parts, 'qty': b_qty, 'amount': b_amount})
    part_list = sorted(parts.values(), key=lambda p: (-p['amount'], p['part_code']))
    return {
        'store': store, 'from': None if d_from == date.min else d_from.isoformat(),
        'to': None if d_to == date.max else d_to.isoformat(), 'order_type': otype,
        'by_type': [{'order_type': t, 'parts': len(v['codes']), 'qty': v['qty'], 'amount': v['amount']}
                    for t, v in by_type.items()],
        'by_store': [{'store': k, 'parts': len(v['codes']), 'qty': v['qty'], 'amount': v['amount'],
                      'types': v['types']} for k, v in sorted(by_store.items())],
        'stores': sorted(by_store), 'batches': blist,
        'totals': {'parts': len({p['part_code'] for p in part_list}), 'qty': sum(p['qty'] for p in part_list),
                   'amount': sum(p['amount'] for p in part_list)},
        'parts': part_list[:_MAX_DASH_PARTS], 'parts_truncated': len(part_list) > _MAX_DASH_PARTS,
    }, None


@gom_don_hang_bp.route('/api/gom-don-hang/dashboard', methods=['GET'])
def gdh_dashboard():
    db, cur = _ctx()
    try:
        data, err = _dashboard(cur, request.args)
        if err:
            return err
    finally:
        cur.close()
    return jsonify({'success': True, 'order_types': ORDER_TYPES, **data})


@gom_don_hang_bp.route('/api/gom-don-hang/dashboard/export', methods=['GET'])
def gdh_dashboard_export():
    import pandas as pd
    db, cur = _ctx()
    try:
        data, err = _dashboard(cur, request.args)
        if err:
            return err
    finally:
        cur.close()
    if not data['parts']:
        return jsonify({'error': 'Không có mã hàng nào đã đặt trong khoảng ngày này.'}), 400
    stores = data['stores']
    df = pd.DataFrame([{
        'Loại đơn': p['order_type'], 'Mã hàng': p['part_code'], 'Tên hàng': p['part_name'], 'ĐVT': p['unit'],
        **{s: p['stores'].get(s, 0) for s in stores}, 'Tổng SL': p['qty'], 'Thành tiền': p['amount'],
    } for p in data['parts']])
    df2 = pd.DataFrame([{'Chi nhánh': s['store'], 'Số mã': s['parts'], 'Tổng SL': s['qty'],
                         **{f'{t} - Thành tiền': s['types'][t]['amount'] for t in ORDER_TYPES},
                         'Thành tiền': s['amount']} for s in data['by_store']])
    return _send_xlsx({'Mã đã đặt': df, 'Theo chi nhánh': df2},
                      f"dashboard-don-hang-{data['from'] or 'all'}_{data['to'] or 'all'}.xlsx", freeze='A2')