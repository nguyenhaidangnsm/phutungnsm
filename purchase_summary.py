# -*- coding: utf-8 -*-
"""
Module TỔNG HỢP ĐẶT HÀNG (bám sát sheet "Tổng hợp" / "Đặt HVN" / "DASHBOARD" trong file
"Quản lý kho và đặt phụ tùng HNS3 - T09 2026.xlsx").

Blueprint độc lập, cùng kiểu branch_orders.py. Bảng tự tạo ở lần dùng đầu tiên
(CREATE TABLE IF NOT EXISTS) nên KHÔNG cần sửa init_db().

MÓC NỐI VÀO app.py (cạnh branch_orders, TRƯỚC _init_db_with_retry()):

    from purchase_summary import purchase_summary_bp
    app.register_blueprint(purchase_summary_bp)

NGUỒN DỮ LIỆU
    - Danh mục       : LẤY TỪ DATABASE HIỆN CÓ - tên/ĐVT (inventory_items), giá vốn (part_vehicle_models.gia_nhap),
                       khoá đặt hàng + mã thay thế (order_lock_items, cùng nguồn trang Duyệt Đơn Hàng).
    - Tiêu chuẩn bán P, Min/TB/Max : chưa có trong DB -> bảng parts_catalog (tuỳ chọn, admin import 1 lần
                       từ Danh mục A; thiếu thì coi như không có P, giá vốn lấy từ đây nếu DB không có)
    - 1.TồnAmis      : bảng amis_stock/amis_meta (admin upload, hoặc cửa hàng upload/nhập tay) <- MỚI
    - 2.BO (Nợ BO)   : lấy từ PO đang ở trạng thái "Nợ" (branch_orders._load_po_lines)
    - Tồn hệ thống   : inventory_items (đã có)
    - Tần suất TX/TB/CB: cách app đang tính (branch_orders._load_part_info)
    - Nhập tay       : bảng purchase_summary_lines ((+)/(-), chuyển nội bộ, loại đơn, lý do, ghi chú)

CÔNG THỨC (theo file gốc, có 2 chỗ chỉnh đã thống nhất):
    Số tuần kỳ AMIS       = INT((đến ngày - từ ngày) / 7)                  (AB3)
    Bình quân bán/tuần    = 0 nếu CB, ngược lại ROUNDUP(Xuất / số tuần)    (AL)
    Tồn so sánh           = Tồn chi nhánh đang xem + Nợ BO                 (file gốc lệch sang cột HNS1)
    Nhu cầu N tuần        = BQ/tuần x N tuần, chỉ tính khi Tồn so sánh < Xuất   (AM)
    Đề xuất đặt (đơn vị lẻ): nếu có Tiêu chuẩn bán P > 0 thì đưa (tồn + số đặt) lên đúng bội của P,
                            chọn số nhỏ nhất mà vẫn đủ nhu cầu N tuần.     (theo ví dụ P=4, tồn 3 -> 1 hoặc 5)
    SL đặt hàng           = ROUNDUP((đề xuất + (+)/(-)) / Quy cách bộ) nếu có quy cách,
                            ngược lại đề xuất + (+)/(-) - tổng chuyển nội bộ  (AX)
    Thành tiền            = SL đặt x Quy cách x Giá vốn DNP (hoặc SL x Giá vốn)  (AZ)
"""
import io
import math
import re
import threading
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, request, jsonify, session, send_file
from psycopg2.extras import execute_values

from app import get_db, _valid_store_codes
from branch_orders import (
    _load_po_lines, _load_part_info, ORDER_TYPES, ORDER_REASONS, _REASON_LABEL,
    _resolve_store, _actor_name,
)

purchase_summary_bp = Blueprint('purchase_summary', __name__)
_VN_TZ = ZoneInfo('Asia/Ho_Chi_Minh')

BRANCH_COLS = ['NS1', 'NS2', 'NS3', 'NS4', 'NS5', 'NSM1']          # HNS1..HNS5, NSM1
BRANCH_LABEL = {'NS1': 'HNS1', 'NS2': 'HNS2', 'NS3': 'HNS3', 'NS4': 'HNS4', 'NS5': 'HNS5', 'NSM1': 'NSM1'}
DEFAULT_FORECAST_WEEKS = 3
_MAX_LINES = 5000

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
                CREATE TABLE IF NOT EXISTS parts_catalog (
                    part_code VARCHAR(100) PRIMARY KEY,
                    part_name TEXT, unit VARCHAR(50),
                    replace_code VARCHAR(100),      -- Mã mới thay thế
                    convert_code VARCHAR(100),      -- Mã quy đổi
                    convert_name TEXT,
                    pack_size NUMERIC,              -- Quy cách (số cái / bộ)
                    convert_unit VARCHAR(50),       -- Đơn vị mã quy đổi
                    part_age TEXT, shelf_life TEXT,
                    cost_dnp NUMERIC,               -- Giá vốn từ Honda DNP
                    min_qty NUMERIC, avg_qty NUMERIC, max_qty NUMERIC,
                    std_sale NUMERIC                -- Tiêu chuẩn bán / xe (cột P)
                )''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS parts_catalog_meta (
                    id INTEGER PRIMARY KEY DEFAULT 1, filename TEXT, uploaded_by TEXT,
                    upload_time TIMESTAMP, total_parts INTEGER)''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS amis_stock (
                    store_code VARCHAR(20) NOT NULL, part_code VARCHAR(100) NOT NULL,
                    opening NUMERIC, purchase NUMERIC, sold NUMERIC, closing NUMERIC,
                    PRIMARY KEY (store_code, part_code))''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS amis_meta (
                    store_code VARCHAR(20) PRIMARY KEY, filename TEXT,
                    period_from DATE, period_to DATE,
                    uploaded_by TEXT, upload_time TIMESTAMP, total_parts INTEGER)''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS purchase_summary_lines (
                    store_code VARCHAR(20) NOT NULL, part_code VARCHAR(100) NOT NULL,
                    adj_qty NUMERIC NOT NULL DEFAULT 0,        -- (+)/(-) Số lượng
                    transfer_qty NUMERIC NOT NULL DEFAULT 0,   -- Đề nghị chuyển nội bộ
                    order_type VARCHAR(50), reason VARCHAR(10), note TEXT,
                    updated_by TEXT, updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (store_code, part_code))''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS purchase_summary_settings (
                    store_code VARCHAR(20) PRIMARY KEY,
                    forecast_weeks INTEGER NOT NULL DEFAULT 3, send_date DATE)''')
            db.commit()
        finally:
            cur.close()
        _tables_ready = True


# ----------------------------------------------------------------------------
# 1. PARSE FILE
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


# Tạm thời KHÔNG dùng "Mã quy đổi" và "Quy cách" (bỏ qua khi import). Đổi thành False để dùng lại:
# khi đó Part# xuất HVN lấy Mã quy đổi và SL đặt được làm tròn theo bộ.
IGNORE_PACK_CONVERT = True

# Vị trí cột (bắt đầu từ 0) khi file Danh mục KHÔNG có dòng tiêu đề - đúng thứ tự sheet "Danh mục(A)".
_CAT_POS = {'code': 0, 'name': 1, 'unit': 2, 'rep': 4, 'conv': 5, 'cname': 6, 'pack': 7, 'cunit': 8,
            'age': 11, 'life': 12, 'cost': 16, 'min': 18, 'avg': 19, 'max': 20, 'std': 21}


def _parse_catalog(file_storage):
    """Danh mục (A): file có dòng tiêu đề 'Mã hàng' (sheet Danh mục(A)) hoặc KHÔNG có tiêu đề
    (dữ liệu bắt đầu ngay dòng 1, cột theo đúng thứ tự Danh mục(A)).
    Trả về (list tuple theo thứ tự cột bảng parts_catalog, số dòng bỏ qua)."""
    raw = _read_raw(file_storage)
    hdr = next((i for i in range(min(20, len(raw)))
                if any(_norm(x) == 'mã hàng' for x in raw.iloc[i].tolist())), None)
    pos = dict(_CAT_POS)
    first = 0
    if hdr is not None:
        first = hdr + 1
        cols = {}
        for c, v in enumerate(raw.iloc[hdr].tolist()):
            cols.setdefault(_norm(v), c)
        alias = {'code': ('mã hàng',), 'name': ('tên hàng',), 'unit': ('đvt',), 'rep': ('mã mới thay thế',),
                 'conv': ('mã quy đổi',), 'cname': ('tên phụ tùng quy đổi',), 'pack': ('quy cách',),
                 'cunit': ('đơn vị mã quy đổi',), 'age': ('tuổi phụ tùng',), 'life': ('hạn sử dụng',),
                 'cost': ('giá vốn từ honda dnp', 'giá vốn'), 'min': ('min',), 'avg': ('tb', 'trung bình'),
                 'max': ('max',), 'std': ('tiêu chuẩn bán/xe', 'tiêu chuẩn bán')}
        for k, names in alias.items():
            pos[k] = next((cols[n] for n in names if n in cols), None)
    if IGNORE_PACK_CONVERT:
        for k in ('conv', 'cname', 'pack', 'cunit'):
            pos[k] = None

    def cell(row, k, num=False):
        c = pos[k]
        if c is None or c >= len(row):
            return None
        v = row[c]
        if num:
            return _num(v)
        s = '' if v is None else str(v).strip()
        return None if s.lower() in ('', 'nan', '-', '0') else s

    out, skipped = {}, 0
    for i in range(first, len(raw)):
        row = raw.iloc[i].tolist()
        code = cell(row, 'code')
        if not code:
            skipped += 1
            continue
        out[code] = (code, cell(row, 'name'), cell(row, 'unit'), cell(row, 'rep'), cell(row, 'conv'),
                     cell(row, 'cname'), cell(row, 'pack', True), cell(row, 'cunit'),
                     cell(row, 'age'), cell(row, 'life'), cell(row, 'cost', True),
                     cell(row, 'min', True), cell(row, 'avg', True), cell(row, 'max', True),
                     cell(row, 'std', True))
    return list(out.values()), skipped


def _parse_amis(file_storage):
    """Sheet "1.TồnAmis" hoặc file "Tổng hợp tồn kho" xuất từ AMIS.
    Trả về (rows{part: (opening, purchase, sold, closing)}, period_from, period_to)."""
    raw = _read_raw(file_storage)
    pf = pt = None
    for i in range(min(6, len(raw))):
        m = re.search(r'(\d{2})/(\d{2})/(\d{4}).*?(\d{2})/(\d{2})/(\d{4})',
                      ' '.join(str(x) for x in raw.iloc[i].tolist() if x is not None))
        if m:
            d1, m1, y1, d2, m2, y2 = (int(x) for x in m.groups())
            pf, pt = date(y1, m1, d1), date(y2, m2, d2)
            break
    hdr = next((i for i in range(min(20, len(raw)))
                if any(_norm(x) == 'mã hàng' for x in raw.iloc[i].tolist())), None)
    if hdr is None:
        raise ValueError('Không tìm thấy dòng tiêu đề "Mã hàng" trong file tồn AMIS.')
    grp = raw.iloc[hdr - 1].tolist() if hdr > 0 else [None] * raw.shape[1]
    top, sub = raw.iloc[hdr].tolist(), (raw.iloc[hdr + 1].tolist() if hdr + 1 < len(raw) else [None] * raw.shape[1])
    labels, cur_group = [], ''
    for c in range(raw.shape[1]):
        g = _norm(top[c]) if _norm(top[c]) not in ('', 'nan') else ''
        if g:
            cur_group = g
        labels.append(' '.join(x for x in (cur_group, _norm(sub[c]) if sub[c] is not None else '') if x))

    def find(*keys, exclude=()):
        for c, lb in enumerate(labels):
            if any(k in lb for k in keys) and not any(e in lb for e in exclude):
                return c
        return None

    c_code = next((c for c, v in enumerate(top) if _norm(v) == 'mã hàng'), None)
    c_open = find('đầu kỳ')
    c_buy = find('mua hàng', 'nhập kho')
    c_sold = find('bán hàng', 'xuất kho')
    c_close = find('cuối kỳ')
    if c_sold is None or c_close is None:
        raise ValueError('Không tìm thấy cột "Bán hàng/Xuất" hoặc "Cuối kỳ" trong file tồn AMIS.')

    rows, first = {}, hdr + 1
    for i in range(first, len(raw)):
        r = raw.iloc[i].tolist()
        code = str(r[c_code]).strip() if r[c_code] is not None else ''
        if not code or code.lower() == 'nan' or code.lower().startswith(('tổng', 'mã hàng')):
            continue
        vals = [(_num(r[c], 0.0) if c is not None else 0.0) for c in (c_open, c_buy, c_sold, c_close)]
        old = rows.get(code)
        rows[code] = tuple(a + b for a, b in zip(old, vals)) if old else tuple(vals)   # gộp nhiều kho
    return rows, pf, pt


# ----------------------------------------------------------------------------
# 2. TÍNH BẢNG TỔNG HỢP
# ----------------------------------------------------------------------------
def _settings(cur, store):
    cur.execute('SELECT forecast_weeks, send_date FROM purchase_summary_settings WHERE store_code=%s', (store,))
    r = cur.fetchone()
    return (int(r['forecast_weeks']) if r else DEFAULT_FORECAST_WEEKS,
            r['send_date'] if r else None)


def suggest_qty(stock_bo, sold, avg_week, forecast_weeks, std_sale):
    """Đề xuất đặt (đơn vị lẻ) - xem docstring đầu file."""
    need = avg_week * forecast_weeks if stock_bo < sold else 0
    if need <= 0:
        return 0.0, 0.0
    p = int(std_sale or 0)
    if p > 0:
        s = int(math.floor(stock_bo))
        q = (-s) % p                        # bù cho tròn lô P
        while s + q < need:                 # chưa đủ nhu cầu N tuần -> thêm lô
            q += p
        return float(q), float(need)
    return float(math.ceil(need)), float(need)


def compute_rows(cur, store, weeks_forecast=None):
    weeks_forecast = weeks_forecast or DEFAULT_FORECAST_WEEKS
    cur.execute('SELECT * FROM amis_meta WHERE store_code=%s', (store,))
    meta = cur.fetchone()
    weeks = 1
    if meta and meta['period_from'] and meta['period_to']:
        weeks = max(1, (meta['period_to'] - meta['period_from']).days // 7)

    cur.execute('SELECT part_code, opening, purchase, sold, closing FROM amis_stock WHERE store_code=%s', (store,))
    amis = {r['part_code']: r for r in cur.fetchall()}
    cur.execute('SELECT * FROM purchase_summary_lines WHERE store_code=%s', (store,))
    lines = {r['part_code']: r for r in cur.fetchall()}
    cur.execute('SELECT * FROM parts_catalog')
    catalog = {r['part_code']: r for r in cur.fetchall()}

    cur.execute('SELECT part_code, store_code, SUM(quantity) q FROM inventory_items GROUP BY part_code, store_code')
    stock = {}
    for r in cur.fetchall():
        stock.setdefault(r['part_code'], {})[r['store_code']] = float(r['q'] or 0)
    cur.execute('SELECT part_code, store_code, SUM(qty_sold) q FROM sales_export_items GROUP BY part_code, store_code')
    sold_by_store = {}
    for r in cur.fetchall():
        sold_by_store.setdefault(r['part_code'], {})[r['store_code']] = float(r['q'] or 0)
    cur.execute('SELECT part_code, gia_nhap FROM part_vehicle_models WHERE gia_nhap IS NOT NULL')
    fallback_price = {r['part_code']: float(r['gia_nhap']) for r in cur.fetchall()}

    cur.execute('SELECT part_code, is_locked, replacement_code FROM order_lock_items WHERE is_locked')
    locks = {r['part_code']: (r['replacement_code'] or '').strip() or None for r in cur.fetchall()}

    bo = {}
    for ln in _load_po_lines(cur, store):
        if ln['status'] == 'Nợ':
            bo[ln['part_code']] = bo.get(ln['part_code'], 0.0) + ln['qty']

    codes = set(amis) | set(lines) | set(bo) | {c for c, d in stock.items() if d.get(store, 0) > 0}
    info, _ = _load_part_info(cur, store, sorted(codes))

    rows = []
    for code in sorted(codes):
        cat, a, ln, pi = catalog.get(code), amis.get(code), lines.get(code), info.get(code, {})
        f = lambda v: float(v) if v is not None else None
        sold = f(a['sold']) if a else None
        on_hand = stock.get(code, {}).get(store, 0.0)
        bo_qty = bo.get(code, 0.0)
        stock_bo = on_hand + bo_qty
        group = pi.get('group') or 'NA'
        avg_week = 0 if group in ('CB', 'NA') else (math.ceil(sold / weeks) if sold and sold > 0 else 0)
        std = f(cat['std_sale']) if cat else None
        sug, need = suggest_qty(stock_bo, sold or 0, avg_week, weeks_forecast, std)
        adj = f(ln['adj_qty']) if ln else 0.0
        transfer = f(ln['transfer_qty']) if ln else 0.0
        pack = f(cat['pack_size']) if cat else None
        total_units = sug + adj
        if pack and pack > 0:
            qty_order = math.ceil(total_units / pack - 1e-9)
        else:
            qty_order = math.ceil(total_units - transfer - 1e-9)
        qty_order = max(0, qty_order)
        cost = fallback_price.get(code)          # giá nhập trong DB trước, thiếu thì lấy giá vốn từ Danh mục import
        if cost is None and cat and cat['cost_dnp'] is not None:
            cost = f(cat['cost_dnp'])
        locked = code in locks
        lock_rep = locks.get(code)
        amount = None if cost is None else (pack * cost * qty_order if pack and pack > 0 else cost * qty_order)
        unit = pi.get('unit') or (cat['unit'] if cat else None)
        conv = cat['convert_code'] if cat else None
        idle = {}
        for b in BRANCH_COLS:
            q = stock.get(code, {}).get(b, 0.0)
            idle[b] = q if q > 0 and sold_by_store.get(code, {}).get(b, 0.0) <= 0 else 0.0
        rows.append({
            'part_code': code,
            'part_name': (cat['part_name'] if cat and cat['part_name'] else None) or pi.get('part_name'),
            'unit': unit, 'replace_code': cat['replace_code'] if cat else None,
            'convert_code': conv, 'convert_name': cat['convert_name'] if cat else None,
            'pack_size': pack, 'convert_unit': cat['convert_unit'] if cat else None,
            'cost': cost, 'group': group, 'min': f(cat['min_qty']) if cat else None,
            'avg': f(cat['avg_qty']) if cat else None, 'max': f(cat['max_qty']) if cat else None,
            'std_sale': std,
            'opening': f(a['opening']) if a else None, 'purchase': f(a['purchase']) if a else None,
            'sold': sold, 'closing': f(a['closing']) if a else None,
            'bo': bo_qty, 'on_hand': on_hand, 'stock_bo': stock_bo,
            'branch_stock': {b: stock.get(code, {}).get(b, 0.0) for b in BRANCH_COLS},
            'idle_stock': idle,
            'avg_week': avg_week, 'need': need, 'suggest': sug,
            'adj': adj, 'transfer': transfer,
            'order_type': (ln['order_type'] if ln else None) or '', 'reason': (ln['reason'] if ln else None) or '',
            'note': (ln['note'] if ln else None) or '',
            'qty_order': qty_order,
            'order_unit': (f"Bộ ({int(pack) if pack == int(pack) else pack}{cat['convert_unit'] or ''})"
                           if pack and pack > 0 else unit) if qty_order > 0 else '-',
            'amount': amount,
            'locked': locked, 'lock_replace': lock_rep,
            'hvn_part': conv or lock_rep or code,      # mã bị khoá -> đặt bằng mã thay thế (nếu có)
        })
    period = {'from': meta['period_from'].isoformat() if meta and meta['period_from'] else None,
              'to': meta['period_to'].isoformat() if meta and meta['period_to'] else None,
              'weeks': weeks, 'filename': meta['filename'] if meta else None,
              'uploaded_by': meta['uploaded_by'] if meta else None,
              'upload_time': meta['upload_time'].strftime('%d/%m/%Y %H:%M') if meta and meta['upload_time'] else None}
    return rows, period


def _totals(rows):
    tot = {k: 0.0 for k in ('opening', 'purchase', 'sold', 'closing', 'bo', 'transfer')}
    for r in rows:
        for k in tot:
            tot[k] += r[k] or 0.0
    by_type = {t: {'qty': 0.0, 'amount': 0.0} for t in ORDER_TYPES}
    by_reason = {c: {'label': l, 'qty': 0.0} for c, l in ORDER_REASONS}
    for r in rows:
        if r['qty_order'] > 0 and r['order_type'] in by_type:
            by_type[r['order_type']]['qty'] += r['qty_order']
            by_type[r['order_type']]['amount'] += r['amount'] or 0.0
        if r['qty_order'] > 0 and r['reason'] in by_reason:
            by_reason[r['reason']]['qty'] += r['qty_order']
    freq = {g: sum(1 for r in rows if r['group'] == g) for g in ('TX', 'TB', 'CB', 'HET', 'NA')}
    unassigned = sum(1 for r in rows if r['qty_order'] > 0 and r['order_type'] not in by_type)
    locked_no_rep = sum(1 for r in rows if r['qty_order'] > 0 and r['locked'] and not r['lock_replace'])
    return {'locked_no_replace': locked_no_rep,'sums': tot, 'by_type': by_type, 'by_reason': by_reason, 'freq': freq,
            'unassigned_type': unassigned}


# ----------------------------------------------------------------------------
# 3. API
# ----------------------------------------------------------------------------
def _ctx(store_arg):
    db = get_db()
    _ensure_tables(db)
    cur = db.cursor()
    store, err = _resolve_store(cur, store_arg)
    return db, cur, store, err


@purchase_summary_bp.route('/api/purchase-summary', methods=['GET'])
def ps_list():
    db, cur, store, err = _ctx(request.args.get('store'))
    try:
        if err:
            return err
        fw, send_date = _settings(cur, store)
        rows, period = compute_rows(cur, store, fw)
        totals = _totals(rows)
        q = (request.args.get('q') or '').strip().lower()
        group = (request.args.get('group') or '').strip().upper()
        only_order = request.args.get('only_order') == '1'
        view = [r for r in rows
                if (not q or q in r['part_code'].lower() or q in (r['part_name'] or '').lower())
                and (not group or r['group'] == group)
                and (not only_order or r['qty_order'] > 0 or r['suggest'] > 0)]
        page = max(1, int(request.args.get('page') or 1))
        size = min(500, max(1, int(request.args.get('page_size') or 100)))
        cur.execute('SELECT filename, upload_time, total_parts FROM parts_catalog_meta WHERE id=1')
        cm = cur.fetchone()
    finally:
        cur.close()
    return jsonify({
        'success': True, 'store': store, 'period': period, 'forecast_weeks': fw,
        'send_date': send_date.isoformat() if send_date else None,
        'options': {'order_types': ORDER_TYPES, 'reasons': [{'code': c, 'label': l} for c, l in ORDER_REASONS]},
        'catalog': {'filename': cm['filename'], 'total_parts': cm['total_parts'],
                    'upload_time': cm['upload_time'].strftime('%d/%m/%Y %H:%M')} if cm else None,
        'total_rows': len(view), 'page': page, 'page_size': size,
        'data': view[(page - 1) * size: page * size], 'totals': totals,
        'branches': [{'code': b, 'label': BRANCH_LABEL[b]} for b in BRANCH_COLS],
    })


@purchase_summary_bp.route('/api/purchase-summary/save', methods=['POST'])
def ps_save():
    payload = request.get_json(silent=True) or {}
    items = payload.get('items') or []
    if not isinstance(items, list) or len(items) > _MAX_LINES:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400
    lines, amis_manual = [], []
    for it in items:
        code = str((it or {}).get('part_code') or '').strip()
        if not code:
            continue
        adj, tr = _num(it.get('adj'), 0.0), _num(it.get('transfer'), 0.0)
        if abs(adj) > 1_000_000 or tr < 0 or tr > 1_000_000:
            return jsonify({'error': f'Số lượng của mã {code} không hợp lệ.'}), 400
        ot = str(it.get('order_type') or '').strip()
        rs = str(it.get('reason') or '').strip()
        if ot and ot not in ORDER_TYPES:
            return jsonify({'error': f'Loại đơn của mã {code} không hợp lệ.'}), 400
        if rs and rs not in _REASON_LABEL:
            return jsonify({'error': f'Lý do của mã {code} không hợp lệ.'}), 400
        lines.append((code, adj, tr, ot or None, rs or None, (str(it.get('note') or '').strip()[:500]) or None))
        if it.get('sold_manual') is not None or it.get('closing_manual') is not None:
            amis_manual.append((code, _num(it.get('sold_manual')), _num(it.get('closing_manual'))))
    db, cur, store, err = _ctx(payload.get('store'))
    try:
        if err:
            return err
        actor = _actor_name()
        for code, adj, tr, ot, rs, note in lines:
            cur.execute('''
                INSERT INTO purchase_summary_lines (store_code, part_code, adj_qty, transfer_qty, order_type, reason, note, updated_by, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (store_code, part_code) DO UPDATE SET adj_qty=EXCLUDED.adj_qty,
                  transfer_qty=EXCLUDED.transfer_qty, order_type=EXCLUDED.order_type, reason=EXCLUDED.reason,
                  note=EXCLUDED.note, updated_by=EXCLUDED.updated_by, updated_at=NOW()''',
                        (store, code, adj, tr, ot, rs, note, actor))
        for code, sold, closing in amis_manual:      # cửa hàng nhập tay số Xuất / Tồn cuối
            cur.execute('''
                INSERT INTO amis_stock (store_code, part_code, sold, closing) VALUES (%s,%s,%s,%s)
                ON CONFLICT (store_code, part_code) DO UPDATE SET
                  sold=COALESCE(EXCLUDED.sold, amis_stock.sold), closing=COALESCE(EXCLUDED.closing, amis_stock.closing)''',
                        (store, code, sold, closing))
        if amis_manual:
            cur.execute('''INSERT INTO amis_meta (store_code, filename, uploaded_by, upload_time)
                           VALUES (%s,'(nhập tay)',%s,NOW()) ON CONFLICT (store_code) DO NOTHING''', (store, actor))
        if 'forecast_weeks' in payload or 'send_date' in payload:
            fw = int(_num(payload.get('forecast_weeks'), DEFAULT_FORECAST_WEEKS))
            if not 1 <= fw <= 12:
                return jsonify({'error': 'Số tuần dự kiến phải từ 1 đến 12.'}), 400
            sd = (payload.get('send_date') or '').strip() or None
            cur.execute('''INSERT INTO purchase_summary_settings (store_code, forecast_weeks, send_date) VALUES (%s,%s,%s)
                           ON CONFLICT (store_code) DO UPDATE SET forecast_weeks=EXCLUDED.forecast_weeks, send_date=EXCLUDED.send_date''',
                        (store, fw, sd))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'saved': len(lines)})


@purchase_summary_bp.route('/api/purchase-summary/upload-amis', methods=['POST'])
def ps_upload_amis():
    """Admin (chọn chi nhánh) hoặc cửa hàng (chi nhánh của mình) tải file tồn AMIS."""
    f = request.files.get('amis_file')
    if not f:
        return jsonify({'error': 'Vui lòng chọn file tồn AMIS.'}), 400
    try:
        rows, pf, pt = _parse_amis(f)
    except Exception as e:
        return jsonify({'error': f'Không đọc được file: {e}'}), 400
    if not rows:
        return jsonify({'error': 'File không có dòng dữ liệu hợp lệ.'}), 400
    if request.form.get('period_from') and request.form.get('period_to'):    # cho phép nhập tay kỳ báo cáo
        try:
            pf = date.fromisoformat(request.form['period_from'])
            pt = date.fromisoformat(request.form['period_to'])
        except ValueError:
            return jsonify({'error': 'Ngày kỳ báo cáo không hợp lệ.'}), 400
    if not (pf and pt) or pt <= pf:
        return jsonify({'error': 'Không dò được kỳ báo cáo ("Từ ngày ... đến ngày ..."). Vui lòng nhập Từ ngày / Đến ngày.'}), 400
    db, cur, store, err = _ctx(request.form.get('store'))
    try:
        if err:
            return err
        cur.execute('DELETE FROM amis_stock WHERE store_code=%s', (store,))
        execute_values(cur, 'INSERT INTO amis_stock (store_code, part_code, opening, purchase, sold, closing) VALUES %s',
                       [(store, c, *v) for c, v in rows.items()])
        cur.execute('''INSERT INTO amis_meta (store_code, filename, period_from, period_to, uploaded_by, upload_time, total_parts)
                       VALUES (%s,%s,%s,%s,%s,NOW(),%s)
                       ON CONFLICT (store_code) DO UPDATE SET filename=EXCLUDED.filename, period_from=EXCLUDED.period_from,
                         period_to=EXCLUDED.period_to, uploaded_by=EXCLUDED.uploaded_by, upload_time=NOW(),
                         total_parts=EXCLUDED.total_parts''', (store, f.filename, pf, pt, _actor_name(), len(rows)))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'total_parts': len(rows), 'period_from': pf.isoformat(), 'period_to': pt.isoformat()})


@purchase_summary_bp.route('/api/admin/purchase-summary/import-catalog', methods=['POST'])
def ps_import_catalog():
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    f = request.files.get('catalog_file')
    if not f:
        return jsonify({'error': 'Vui lòng chọn file Danh mục (A).'}), 400
    try:
        rows, skipped = _parse_catalog(f)
    except Exception as e:
        return jsonify({'error': f'Không đọc được file: {e}'}), 400
    if not rows:
        return jsonify({'error': 'File không có mã hàng hợp lệ.'}), 400
    db = get_db()
    _ensure_tables(db)
    cur = db.cursor()
    try:
        cur.execute('TRUNCATE TABLE parts_catalog')
        execute_values(cur, '''INSERT INTO parts_catalog (part_code, part_name, unit, replace_code, convert_code,
            convert_name, pack_size, convert_unit, part_age, shelf_life, cost_dnp, min_qty, avg_qty, max_qty, std_sale)
            VALUES %s''', rows)
        cur.execute('''INSERT INTO parts_catalog_meta (id, filename, uploaded_by, upload_time, total_parts)
                       VALUES (1,%s,%s,NOW(),%s) ON CONFLICT (id) DO UPDATE SET filename=EXCLUDED.filename,
                       uploaded_by=EXCLUDED.uploaded_by, upload_time=NOW(), total_parts=EXCLUDED.total_parts''',
                    (f.filename, _actor_name(), len(rows)))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return jsonify({'success': True, 'total_parts': len(rows), 'skipped_rows': skipped})


def _autosize(ws):
    for col in ws.columns:
        w = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(w + 2, 10), 50)


@purchase_summary_bp.route('/api/purchase-summary/export-hvn', methods=['GET'])
def ps_export_hvn():
    """Sheet "Đặt HVN": mỗi loại đơn 1 sheet với Line#, Order Number, Part#, Quantity Requested.
    Part# = Mã quy đổi (nếu bật) > Mã thay thế của mã đang khoá đặt hàng > Mã hàng."""
    import pandas as pd
    db, cur, store, err = _ctx(request.args.get('store'))
    try:
        if err:
            return err
        fw, _ = _settings(cur, store)
        rows, _p = compute_rows(cur, store, fw)
    finally:
        cur.close()
    only = (request.args.get('order_type') or '').strip()
    types = [only] if only in ORDER_TYPES else ORDER_TYPES
    sheets = {}
    for t in types:
        sel = [r for r in rows if r['order_type'] == t and r['qty_order'] > 0]
        if sel:
            merged = {}                      # nhiều mã khoá cùng thay bằng 1 mã -> cộng dồn SL
            for r in sel:
                merged[r['hvn_part']] = merged.get(r['hvn_part'], 0) + int(r['qty_order'])
            sheets[t] = pd.DataFrame([{'Line#': i, 'Order Number': '', 'Part#': p, 'Quantity Requested': q}
                                      for i, (p, q) in enumerate(merged.items(), 1)])
    if not sheets:
        return jsonify({'error': 'Chưa có mã hàng nào có Loại đơn và SL đặt > 0.'}), 400
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        for t, df in sheets.items():
            df.to_excel(w, index=False, sheet_name=t[:31])
        for ws in w.book.worksheets:
            _autosize(ws)
            ws.freeze_panes = 'A2'
    out.seek(0)
    today = datetime.now(_VN_TZ).strftime('%Y-%m-%d')
    return send_file(out, as_attachment=True, download_name=f'dat-hang-HVN-{store}-{today}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@purchase_summary_bp.route('/api/purchase-summary/export', methods=['GET'])
def ps_export():
    """Xuất toàn bộ bảng Tổng hợp (các dòng có đề xuất/SL đặt) ra Excel để đối chiếu."""
    import pandas as pd
    db, cur, store, err = _ctx(request.args.get('store'))
    try:
        if err:
            return err
        fw, _ = _settings(cur, store)
        rows, period = compute_rows(cur, store, fw)
    finally:
        cur.close()
    sel = [r for r in rows if r['qty_order'] > 0 or r['suggest'] > 0 or r['adj']]
    if not sel:
        return jsonify({'error': 'Chưa có dòng nào có đề xuất đặt hàng.'}), 400
    df = pd.DataFrame([{
        'Mã hàng': r['part_code'], 'Tên hàng': r['part_name'], 'ĐVT': r['unit'], 'Mã quy đổi': r['convert_code'],
        'Quy cách': r['pack_size'], 'Giá vốn DNP': r['cost'], 'Tần suất': r['group'],
        'Tiêu chuẩn bán': r['std_sale'], 'Tồn đầu': r['opening'], 'Mua hàng': r['purchase'], 'Xuất': r['sold'],
        'Tồn cuối': r['closing'], 'Nợ BO': r['bo'], 'Tồn + BO': r['stock_bo'],
        'BQ bán/tuần': r['avg_week'], f'Đề xuất đặt {fw} tuần': r['suggest'], '(+)/(-)': r['adj'],
        'Chuyển nội bộ': r['transfer'], 'Loại đơn': r['order_type'], 'Lý do': r['reason'],
        'SL đặt hàng': r['qty_order'], 'ĐVT đặt': r['order_unit'], 'Thành tiền': r['amount'], 'Ghi chú': r['note'],
    } for r in sel])
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name='Tổng hợp')
        _autosize(w.book.worksheets[0])
        w.book.worksheets[0].freeze_panes = 'C2'
    out.seek(0)
    today = datetime.now(_VN_TZ).strftime('%Y-%m-%d')
    return send_file(out, as_attachment=True, download_name=f'tong-hop-dat-hang-{store}-{today}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')