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
    BQ bán / tuần         = 0 nếu tần suất CB, ngược lại ROUNDUP(số bán / số tuần)
    Đề xuất đặt           = max(0, ROUNDUP(BQ/tuần x số tuần dự kiến - tồn cuối))
    SL cuối               = max(0, đề xuất + (cộng/trừ thêm))
    Thành tiền            = SL cuối x giá vốn   (giá vốn: file mẫu 1 -> part_vehicle_models.gia_nhap)
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

from app import (get_db, _valid_store_codes, classify_sales_frequency,
                 get_store_data_version, compute_result_for_store_cached)

gom_don_hang_bp = Blueprint('gom_don_hang', __name__)
_VN_TZ = ZoneInfo('Asia/Ho_Chi_Minh')

ORDER_TYPES = ['Định kỳ', 'Khẩn', 'Đơn 26']
DEFAULT_FORECAST_WEEKS = 3
DAYS_PER_MONTH = 30.0
STORE_COLS = ['NS1', 'NS2', 'NS3', 'NS4', 'NS5', 'NSM1']      # thứ tự cột tồn từng chi nhánh
GROUP_LABELS = {'TX': 'Thường xuyên', 'TB': 'Trung bình', 'CB': 'Chậm bán', 'HET': 'Hết tồn'}
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
                    period_from DATE NOT NULL,
                    period_to DATE NOT NULL,
                    forecast_weeks INTEGER NOT NULL DEFAULT 3,
                    filenames TEXT,
                    uploaded_by TEXT,
                    uploaded_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE (store_code, period_from, period_to)
                )''')
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
    avg_week = 0 if group == 'CB' or sales <= 0 else int(math.ceil(sales / weeks - 1e-9))
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
    lines = cur.fetchall()
    codes = [l['part_code'] for l in lines]
    locks = {}
    if codes:
        cur.execute('SELECT part_code, is_locked, replacement_code FROM order_lock_items WHERE part_code = ANY(%s)',
                    (codes,))
        for r in cur.fetchall():
            locks[r['part_code']] = (bool(r['is_locked']), (r['replacement_code'] or '').strip() or None)

    # Tồn kho HỆ THỐNG (bảng inventory_items) của TẤT CẢ chi nhánh + quy cách mã con -> mã cha (chỉ khi with_extra)
    stock_map, has_inv, bundles = {}, False, {}
    if with_extra and codes:
        cur.execute('SELECT 1 FROM inventory_items WHERE store_code = %s LIMIT 1', (batch['store_code'],))
        has_inv = cur.fetchone() is not None
        cur.execute('SELECT part_code, store_code, SUM(quantity) AS q FROM inventory_items '
                    'WHERE part_code = ANY(%s) GROUP BY part_code, store_code', (codes,))
        for r in cur.fetchall():
            stock_map.setdefault(r['part_code'], {})[r['store_code']] = float(r['q'] or 0)
        bundles = _load_bundles(cur)

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
    need_cost = [r['part_code'] for r in rows if r['cost'] is None and (r['qty_final'] > 0 or r['suggest'] > 0)]
    if need_cost:      # thiếu giá vốn trong file (mẫu 2) -> lấy giá nhập trong DB
        cur.execute('SELECT part_code, gia_nhap FROM part_vehicle_models WHERE part_code = ANY(%s) '
                    'AND gia_nhap IS NOT NULL', (need_cost,))
        fb = {r['part_code']: float(r['gia_nhap']) for r in cur.fetchall()}
        for r in rows:
            if r['cost'] is None:
                r['cost'] = fb.get(r['part_code'])
    for r in rows:
        r['amount'] = None if r['cost'] is None else r['cost'] * r['qty_final']
        base = (r['lock_replace'] if r['locked'] and r['lock_replace'] else r['part_code'])
        r['hvn_part'] = base
        b = bundles.get(_bkey(base))             # mã con -> tự quy thành mã cha (làm tròn LÊN)
        r['bundle_parent'] = b[0] if b else None
        r['bundle_ratio'] = b[1] if b else None
        r['debt_qty'] = r['ship_qty'] = r['parent_debt'] = r['parent_ship'] = None
        if po_map is not None:
            own = po_map.get(r['part_code']) or [0.0, 0.0]
            r['debt_qty'], r['ship_qty'] = own
            if b:                                # PO đặt bằng mã CHA: trả về số của mã cha để nhìn thấy
                par = po_map.get(b[0]) or [0.0, 0.0]
                r['parent_debt'], r['parent_ship'] = par
        r['order_code'] = b[0] if b else base
        r['order_qty'] = int(math.ceil(r['qty_final'] / b[1] - 1e-9)) if b else r['qty_final']
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
    return {'to_order': to_order, 'by_type': by_type, 'unassigned_type': unassigned,
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
    if role == 'store' and b['store_code'] != session.get('store_code'):
        return None, (jsonify({'error': 'Forbidden'}), 403)
    return b, None


def _fmt_dt(d):
    return d.strftime('%d/%m/%Y %H:%M') if d else None


def _batch_json(b, extra=None):
    days, weeks, months = period_metrics(b['period_from'], b['period_to'])
    out = {'id': b['id'], 'store': b['store_code'], 'from': b['period_from'].isoformat(),
           'to': b['period_to'].isoformat(), 'days': days, 'weeks': weeks, 'months': round(months, 2),
           'forecast_weeks': b['forecast_weeks'], 'filenames': b['filenames'],
           'uploaded_by': b['uploaded_by'], 'uploaded_at': _fmt_dt(b['uploaded_at'])}
    if extra:
        out.update(extra)
    return out


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
                   (SELECT COUNT(*) FROM gdh_lines l WHERE l.batch_id = b.id) AS total_parts,
                   (SELECT COUNT(*) FROM gdh_lines l WHERE l.batch_id = b.id AND l.order_type IS NOT NULL) AS typed_parts,
                   EXISTS (SELECT 1 FROM gdh_lines l WHERE l.batch_id = b.id AND l.sold IS NOT NULL) AS has_sold
            FROM gdh_batches b WHERE b.store_code = %s ORDER BY b.period_to DESC, b.id DESC''', (store,))
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
    rows = parsed['rows']
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
            INSERT INTO gdh_batches (store_code, period_from, period_to, filenames, uploaded_by, uploaded_at)
            VALUES (%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (store_code, period_from, period_to) DO UPDATE SET
                filenames = EXCLUDED.filenames, uploaded_by = EXCLUDED.uploaded_by, uploaded_at = NOW()
            RETURNING id''', (store, pf, pt, f.filename, _actor_name()))
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
                       FROM gdh_lines WHERE batch_id = %s''', (batch['id'],))
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
    if scope not in ('sold', 'all'):                      # tương thích bản cũ (only_order)
        scope = 'sold' if request.args.get('only_order') == '1' else 'all'
    only = scope == 'sold'
    view = [r for r in rows
            if (not q or q in r['part_code'].lower() or q in (r['part_name'] or '').lower()
                or q in (r['note'] or '').lower())
            and (not group or r['group'] == group)
            and (not otype or (r['order_type'] == otype if otype != '-' else not r['order_type']))
            and (not only or r['sales'] > 0 or r['qty_final'] > 0 or r['adj'] or r['order_type'] or r['note'])]
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
        bid = int(batch['id'])
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
                    return jsonify({'error': 'Chi nhánh này đã có đợt gôm khác cùng kỳ (từ ngày - đến ngày).'}), 400
                raise
        db.commit()
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
        rows = compute_rows(cur, batch)
        codes = [r['part_code'] for r in rows if r['qty_final'] > 0 and (overwrite or not r['order_type'])]
        if codes:
            cur.execute('''UPDATE gdh_lines SET order_type = %s, updated_by = %s, updated_at = NOW()
                           WHERE batch_id = %s AND part_code = ANY(%s)''', (ot, _actor_name(), batch['id'], codes))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'assigned': len(codes)})


@gom_don_hang_bp.route('/api/gom-don-hang/delete-batch', methods=['POST'])
def gdh_delete_batch():
    payload = request.get_json(silent=True) or {}
    db, cur = _ctx()
    try:
        batch, err = _get_batch(cur, payload.get('batch_id'))
        if err:
            return err
        cur.execute('DELETE FROM gdh_batches WHERE id = %s', (batch['id'],))   # gdh_lines xoá theo (CASCADE)
        db.commit()
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
        rows = compute_rows(cur, batch, with_extra=True)
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
        'Ghi chú': r['note'], 'Giá vốn': r['cost'], 'Thành tiền': r['amount'],
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

    sql = 'SELECT * FROM gdh_batches WHERE period_from >= %s AND period_to <= %s'
    params = [d_from, d_to]
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