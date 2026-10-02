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
from datetime import datetime, date
from zoneinfo import ZoneInfo

from flask import Blueprint, request, jsonify, session, send_file
from psycopg2.extras import execute_values
from audit_log import audit_record

from app import (get_db, _valid_store_codes, classify_sales_frequency,
                 get_store_data_version, compute_result_for_store_cached,
                 _is_excluded_from_reorder, EXCLUDED_REORDER_PART_CODE_PREFIXES,
                 create_notification)

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
#   -> viewed (Đã xem: cửa hàng mở bảng so sánh) -> ordered (Đã đặt: cửa hàng bấm Tải đơn về)
# Đơn gôm riêng của admin (owner <> '') luôn là draft, không đi qua luồng này.
STATUS_LABELS = {'draft': 'Nháp', 'pending': 'Chờ duyệt', 'reviewing': 'Đang duyệt',
                 'approved': 'Đã duyệt', 'viewed': 'Đã xem', 'ordered': 'Đã đặt'}
POST_SUBMIT_STATUSES = ['pending', 'reviewing', 'approved', 'viewed', 'ordered']
_MAX_ITEMS = 6000
_MAX_DASH_PARTS = 5000

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
                    uploaded_at TIMESTAMP NOT NULL DEFAULT NOW()
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
                             ('ordered_by', 'TEXT'), ('ordered_at', 'TIMESTAMP')):
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
            # Nhật ký chuyển trạng thái (lưu trữ: ai làm gì lúc nào, kèm ghi chú)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS gdh_batch_events (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER NOT NULL REFERENCES gdh_batches(id) ON DELETE CASCADE,
                    at TIMESTAMP NOT NULL DEFAULT NOW(),
                    actor TEXT, from_status VARCHAR(20), to_status VARCHAR(20), note TEXT
                )''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_gdh_events_batch ON gdh_batch_events (batch_id, id)')
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
    for i in range(first, len(raw)):
        r = raw.iloc[i].tolist()
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
    out = {'id': b['id'], 'store': b['store_code'], 'space': 'mine' if b['owner'] else 'store', 'from': b['period_from'].isoformat(),
           'to': b['period_to'].isoformat(), 'days': days, 'weeks': weeks, 'months': round(months, 2),
           'forecast_weeks': b['forecast_weeks'], 'filenames': b['filenames'],
           'uploaded_by': b['uploaded_by'], 'uploaded_at': _fmt_dt(b['uploaded_at'])}
    st = b.get('status') or 'draft'
    out.update({
        'status': st, 'status_label': STATUS_LABELS.get(st, st),
        'submitted_by': b.get('submitted_by'), 'submitted_at': _fmt_dt(b.get('submitted_at')), 'submit_note': b.get('submit_note') or '',
        'claimed_by': b.get('claimed_by'), 'claimed_at': _fmt_dt(b.get('claimed_at')),
        'claimed_by_me': bool(b.get('claimed_by')) and b.get('claimed_by') == str(session.get('user') or ''),
        'approved_by': b.get('approved_by'), 'approved_at': _fmt_dt(b.get('approved_at')), 'review_note': b.get('review_note') or '',
        'viewed_by': b.get('viewed_by'), 'viewed_at': _fmt_dt(b.get('viewed_at')),
        'ordered_by': b.get('ordered_by'), 'ordered_at': _fmt_dt(b.get('ordered_at')),
        'can_edit': _can_edit(b),
    })
    if extra:
        out.update(extra)
    return out


def _can_edit(b):
    """Đợt gôm có được sửa lúc này không. Đơn riêng của admin: luôn được. Đơn chung của chi nhánh: chỉ khi còn Nháp,
    hoặc đang 'Đang duyệt' và người sửa là CHÍNH admin đã lấy đơn về duyệt."""
    if b.get('owner'):
        return True
    st = b.get('status') or 'draft'
    if st == 'draft':
        return True
    return (session.get('role') == 'admin' and st == 'reviewing'
            and (b.get('claimed_by') or '') == str(session.get('user') or ''))


def _edit_block(b):
    """None nếu được sửa; ngược lại trả (response, 409) kèm lý do theo trạng thái."""
    if _can_edit(b):
        return None
    st = b.get('status') or 'draft'
    if st == 'reviewing':
        who = b.get('claimed_by') or 'admin khác'
        msg = f'Đơn đang được {who} duyệt, bạn không sửa được.'
    elif st == 'pending':
        msg = 'Đơn đã đẩy cho admin (Chờ duyệt) nên đã khoá. Muốn sửa, hãy Thu hồi đơn trước.'
    else:
        msg = f'Đơn đang ở trạng thái "{STATUS_LABELS.get(st, st)}" nên đã khoá và được lưu trữ, không sửa được nữa.'
    return jsonify({'error': msg, 'status': st}), 409


def _log_event(cur, bid, frm, to, note=''):
    cur.execute('INSERT INTO gdh_batch_events (batch_id, actor, from_status, to_status, note) VALUES (%s,%s,%s,%s,%s)',
                (bid, _actor_name(), frm, to, (note or '')[:500]))


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
              AND (b.owner <> '' OR b.status = 'draft' OR (b.status = 'reviewing' AND b.claimed_by = %s))
            ORDER BY b.period_to DESC, b.id DESC''',
                    (_EXCL_LIKE, _EXCL_LIKE, _EXCL_LIKE, store, _owner_for(request.args.get('space')),
                     str(session.get('user') or '') if session.get('role') == 'admin' else ''))
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
    try:
        parsed = parse_stock_file(f)
    except Exception as e:
        return jsonify({'error': f'Không đọc được file: {e}'}), 400
    rows = {c: r for c, r in parsed['rows'].items() if not _is_excluded_from_reorder(c)}
    if not rows:
        return jsonify({'error': 'File không có dòng dữ liệu hợp lệ.'}), 400
    pf, pt, note = parsed['period']
    try:
        man_from, man_to = _parse_date(request.form.get('period_from')), _parse_date(request.form.get('period_to'))
    except ValueError:
        return jsonify({'error': 'Ngày kỳ báo cáo không hợp lệ.'}), 400
    if man_from and man_to:
        pf, pt, note = man_from, man_to, 'Nhập tay'
    if not (pf and pt):
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
            VALUES (%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (store_code, owner, period_from, period_to) WHERE status = 'draft' DO UPDATE SET
                filenames = EXCLUDED.filenames, uploaded_by = EXCLUDED.uploaded_by, uploaded_at = NOW()
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
                out_qty = COALESCE(EXCLUDED.out_qty, gdh_lines.out_qty),
                sold = COALESCE(EXCLUDED.sold, gdh_lines.sold),
                unit_cost = COALESCE(EXCLUDED.unit_cost, gdh_lines.unit_cost)''', data, page_size=1000)
        db.commit()
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
        if vals:
            execute_values(cur, f'''
                UPDATE gdh_lines l SET adj_qty = v.adj, order_type = NULLIF(v.ot, ''),
                       note = NULLIF(v.note, ''), updated_by = v.actor, updated_at = NOW()
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
        if payload.get('period_from') and payload.get('period_to'):
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


@gom_don_hang_bp.route('/api/gom-don-hang/assign-type', methods=['POST'])
def gdh_assign_type():
    """Gán 1 loại đơn cho MỌI mã có SL cuối > 0 của đợt (mặc định chỉ mã chưa có loại đơn)."""
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
            cur.execute('''UPDATE gdh_lines SET order_type = %s, updated_by = %s, updated_at = NOW()
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
            cur.execute('UPDATE gdh_lines SET order_type = NULL, updated_by = %s, updated_at = NOW() WHERE ' + where,
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
            VALUES (1, %s, %s, NOW(), %s, %s)
            ON CONFLICT (id) DO UPDATE SET filename = EXCLUDED.filename, uploaded_by = EXCLUDED.uploaded_by,
                uploaded_at = NOW(), total = EXCLUDED.total, warnings = EXCLUDED.warnings''',
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
    return bool(r['qty_final'] > 0 or r['adj'] or r['order_type'] or r['note'])


def _snapshot(cur, batch, stage, rows=None):
    """Chụp các dòng đang có nội dung của đợt (SL cuối > 0, +/- thêm, loại đơn hoặc ghi chú) vào gdh_batch_snap."""
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
    """Chi nhánh đẩy đơn gôm cho admin: Nháp -> Chờ duyệt. Chụp lại 'đơn lúc đẩy' để so sánh về sau."""
    blk = _need_role('store')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    note = str(payload.get('note') or '').strip()[:500]
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        if (batch.get('status') or 'draft') != 'draft':
            return _bad_state(batch, 'Nháp')
        rows = compute_rows(cur, batch)
        t = _totals(rows)
        if t['to_order'] <= 0:
            return jsonify({'error': 'Đơn chưa có mã nào cần đặt (SL cuối > 0) nên chưa đẩy được.'}), 400
        if t['unassigned_type'] and not payload.get('confirm_unassigned'):
            return jsonify({'error': f"Còn {t['unassigned_type']} mã cần đặt CHƯA chọn loại đơn. Vẫn đẩy đơn cho admin?",
                            'need_confirm': True}), 409
        cur.execute('''UPDATE gdh_batches SET status = 'pending', submitted_by = %s, submitted_at = NOW(), submit_note = %s,
                              claimed_by = NULL, claimed_at = NULL, approved_by = NULL, approved_at = NULL, review_note = NULL,
                              viewed_by = NULL, viewed_at = NULL
                       WHERE id = %s AND status = 'draft' ''', (_actor_name(), note, batch['id']))
        if cur.rowcount != 1:
            db.rollback()
            return _bad_state(batch, 'Nháp')
        n = _snapshot(cur, batch, 'submitted', rows)
        _log_event(cur, batch['id'], 'draft', 'pending', note or 'Chi nhánh đẩy đơn')
        db.commit()
        audit_record('Đẩy đơn gôm cho admin duyệt', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                     summary=f"Đợt {batch['id']} ({batch['store_code']}): đẩy đơn, {t['to_order']} mã cần đặt",
                     after={'status': 'pending', 'note': note}, extra={'so_dong_luu': n})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': 'pending', 'to_order': t['to_order']})


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
        if st == 'reviewing':
            return jsonify({'error': f"Admin {batch.get('claimed_by') or ''} đang duyệt đơn này nên không thu hồi được. "
                                     'Hãy nhờ admin trả đơn về hàng chờ.'}), 409
        if st != 'pending':
            return _bad_state(batch, 'Chờ duyệt')
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
    return jsonify({'success': True, 'status': 'draft'})


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
        cur.execute('''UPDATE gdh_batches SET status = 'reviewing', claimed_by = %s, claimed_at = NOW()
                       WHERE id = %s AND owner = '' AND status = 'pending' ''', (str(session.get('user') or ''), batch['id']))
        if cur.rowcount != 1:
            db.rollback()
            cur.execute('SELECT * FROM gdh_batches WHERE id = %s', (batch['id'],))
            return _bad_state(cur.fetchone(), 'Chờ duyệt')
        _log_event(cur, batch['id'], 'pending', 'reviewing', 'Admin lấy đơn về duyệt')
        db.commit()
        audit_record('Lấy đơn về duyệt', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                     summary=f"Đợt {batch['id']} ({batch['store_code']}): Chờ duyệt -> Đang duyệt", after={'status': 'reviewing'})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': 'reviewing'})


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


@gom_don_hang_bp.route('/api/gom-don-hang/approve', methods=['POST'])
def gdh_approve():
    """Admin duyệt xong: Đang duyệt -> Đã duyệt (chỉ chính admin đã lấy đơn). Chụp 'đơn sau duyệt' và báo cho chi nhánh."""
    blk = _need_role('admin')
    if blk:
        return blk
    payload = request.get_json(silent=True) or {}
    note = str(payload.get('note') or '').strip()[:500]
    me = str(session.get('user') or '')
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        if (batch.get('status') or 'draft') != 'reviewing':
            return _bad_state(batch, 'Đang duyệt')
        if (batch.get('claimed_by') or '') != me:
            return jsonify({'error': f"Đơn đang do {batch.get('claimed_by') or 'admin khác'} duyệt, bạn không duyệt xong thay được."}), 403
        rows = compute_rows(cur, batch)
        t = _totals(rows)
        if t['unassigned_type'] and not payload.get('confirm_unassigned'):
            return jsonify({'error': f"Còn {t['unassigned_type']} mã cần đặt CHƯA chọn loại đơn (sẽ không vào file đặt hàng). Vẫn duyệt xong?",
                            'need_confirm': True}), 409
        cur.execute('''UPDATE gdh_batches SET status = 'approved', approved_by = %s, approved_at = NOW(), review_note = %s
                       WHERE id = %s AND status = 'reviewing' AND claimed_by = %s''', (_actor_name(), note, batch['id'], me))
        if cur.rowcount != 1:
            db.rollback()
            return _bad_state(batch, 'Đang duyệt')
        n = _snapshot(cur, batch, 'approved', rows)
        _log_event(cur, batch['id'], 'reviewing', 'approved', note or 'Admin duyệt xong')
        try:                                    # thông báo chuông cho chi nhánh; lỗi thông báo không được làm hỏng việc duyệt
            cur.execute('SAVEPOINT gdh_notif')
            create_notification(cur, batch['store_code'], 'Đơn gôm đã được duyệt',
                                f"Đơn gôm kỳ {batch['period_from']:%d/%m/%Y} - {batch['period_to']:%d/%m/%Y} đã được admin duyệt. "
                                'Vào Gôm đơn hàng để xem những thay đổi và tải đơn về.', 'info')
            cur.execute('RELEASE SAVEPOINT gdh_notif')
        except Exception:
            cur.execute('ROLLBACK TO SAVEPOINT gdh_notif')
        db.commit()
        audit_record('Duyệt xong đơn gôm', 'Gôm đơn hàng', target=f"đợt {batch['id']}",
                     summary=f"Đợt {batch['id']} ({batch['store_code']}): Đang duyệt -> Đã duyệt, {t['to_order']} mã",
                     after={'status': 'approved', 'note': note}, extra={'so_dong_luu': n})
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'status': 'approved'})


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
        cur.execute('''UPDATE gdh_batches SET status = 'viewed', viewed_by = %s, viewed_at = NOW()
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
    """Bảng so sánh 'đơn lúc chi nhánh đẩy' với 'đơn sau khi admin duyệt' + nhật ký trạng thái.
    Admin xem được cả lúc Chờ duyệt / Đang duyệt (bản mới = bảng gôm hiện tại); cửa hàng chỉ xem khi đã duyệt xong."""
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, request.args.get('batch_id'))
        if err:
            return err
        st = batch.get('status') or 'draft'
        if batch['owner'] or st == 'draft':
            return jsonify({'error': 'Đợt gôm này chưa đẩy đi duyệt nên chưa có gì để so sánh.'}), 409
        if session.get('role') == 'store' and st not in ('approved', 'viewed', 'ordered'):
            return jsonify({'error': 'Đơn chưa được admin duyệt xong nên chưa xem được thay đổi.'}), 409
        old = _snap_load(cur, batch['id'], 'submitted')
        if st in ('pending', 'reviewing'):
            new = {r['part_code']: _snap_item(r) for r in compute_rows(cur, batch) if _snap_relevant(r)}
        else:
            new = _snap_load(cur, batch['id'], 'approved')
        rows, counts = _compare_items(old, new)
        cur.execute('SELECT at, actor, from_status, to_status, note FROM gdh_batch_events WHERE batch_id = %s ORDER BY id',
                    (batch['id'],))
        events = [{'at': _fmt_dt(e['at']), 'actor': e['actor'], 'to': e['to_status'],
                   'to_label': STATUS_LABELS.get(e['to_status'], e['to_status']), 'note': e['note'] or ''} for e in cur.fetchall()]
        meta = _batch_json(batch)
    finally:
        cur.close()
    return jsonify({'success': True, 'batch': meta, 'rows': rows, 'counts': counts, 'live': st in ('pending', 'reviewing'),
                    'old_totals': _items_totals(old), 'new_totals': _items_totals(new), 'events': events,
                    'order_types': ORDER_TYPES})


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
        sts = ['pending', 'reviewing'] if want == 'active' else ([want] if want in POST_SUBMIT_STATUSES else POST_SUBMIT_STATUSES)
        try:
            d_from, d_to = _parse_date(request.args.get('from')), _parse_date(request.args.get('to'))
        except ValueError:
            return jsonify({'error': 'Ngày không hợp lệ (định dạng YYYY-MM-DD).'}), 400
        sql, params = "SELECT * FROM gdh_batches WHERE owner = '' AND status = ANY(%s)", [sts]
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
        csql, cparams = "SELECT status, COUNT(*) AS n FROM gdh_batches WHERE owner = '' AND status <> 'draft'", []
        if store:
            csql += ' AND store_code = %s'; cparams.append(store)
        cur.execute(csql + ' GROUP BY status', cparams)
        counts = {r['status']: int(r['n']) for r in cur.fetchall()}
        data = [_batch_json(b, {'submitted_sum': (sums.get(b['id']) or {}).get('submitted'),
                                'approved_sum': (sums.get(b['id']) or {}).get('approved')}) for b in batches]
    finally:
        cur.close()
    return jsonify({'success': True, 'data': data, 'counts': counts, 'status_labels': STATUS_LABELS})


# ----------------------------------------------------------------------------
# 5. XUẤT EXCEL CỦA 1 ĐỢT
# ----------------------------------------------------------------------------
def _autosize(ws):
    for col in ws.columns:
        w = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(w + 2, 10), 50)


def _send_xlsx(sheets, filename, freeze='A2'):
    import pandas as pd
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        for name, df in sheets.items():
            df.to_excel(w, index=False, sheet_name=name[:31])
        for ws in w.book.worksheets:
            _autosize(ws)
            ws.freeze_panes = freeze
    out.seek(0)
    return send_file(out, as_attachment=True, download_name=filename,
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
        if is_store_hvn and (batch.get('status') or 'draft') not in ('approved', 'viewed', 'ordered'):
            return jsonify({'error': 'Đơn chưa được admin duyệt xong nên chưa tải file đặt hàng được. '
                                     'Hãy bấm \"Đẩy đơn cho admin\" và chờ duyệt.'}), 409
        rows = compute_rows(cur, batch, with_extra=True)
        if is_store_hvn and (batch.get('status') or 'draft') in ('approved', 'viewed') \
                and any(r['order_type'] in ORDER_TYPES and r['qty_final'] > 0 for r in rows):
            # Cửa hàng bấm "Tải đơn về" -> Đã đặt (đồng thời coi như đã xem nếu chưa mở bảng so sánh)
            try:
                cur.execute('''UPDATE gdh_batches SET status = 'ordered', ordered_by = %s, ordered_at = NOW(),
                                      viewed_by = COALESCE(viewed_by, %s), viewed_at = COALESCE(viewed_at, NOW())
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
        for t in ([only] if only in ORDER_TYPES else ORDER_TYPES):
            merged = {}
            for r in rows:
                if r['order_type'] == t and r['qty_final'] > 0:
                    merged[r['order_code']] = merged.get(r['order_code'], 0) + r['order_qty']
            if merged:
                sheets[t] = pd.DataFrame([{'Line#': i, 'Order Number': '', 'Part#': p, 'Quantity Requested': q}
                                          for i, (p, q) in enumerate(merged.items(), 1)])
        if not sheets:
            return jsonify({'error': 'Chưa có mã hàng nào có Loại đơn và SL cuối > 0.'}), 400
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