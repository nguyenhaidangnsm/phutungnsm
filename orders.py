# -*- coding: utf-8 -*-
"""
Module DANH SÁCH ĐẶT HÀNG (theo dõi đặt hàng cho khách - kiểu "Nợ BO
khách hàng", xem sheet "THEO DÕI BO KHÁCH HÀNG" trong file Excel gốc).

QUAN TRỌNG - CSDL RIÊNG BIỆT:
Khác với stocktake.py / price_adjustment.py (dùng CHUNG 1 CSDL Neon với
app.py chính, xem init_db() gọi init_stocktake_tables(cursor) bằng đúng
cursor của CSDL chính), module NÀY lưu dữ liệu vào 1 CSDL POSTGRES RIÊNG
BIỆT, host trên Supabase - hoàn toàn TÁCH KHỎI CSDL Neon đang phục vụ các
tính năng lõi (tồn kho, luân chuyển nội bộ, kiểm kê...).

Vì vậy module này có:
  - Pool kết nối RIÊNG (_get_orders_pool) tới ORDERS_DATABASE_URL.
  - Hàm khởi tạo bảng RIÊNG (init_orders_tables) - có retry riêng ở cuối
    app.py, KHÔNG gộp vào init_db() của app.py chính.
  - Vòng đời connection RIÊNG (get_orders_db / teardown riêng qua
    g._orders_database) - không đụng tới g._database của CSDL chính.

CÁCH TÍCH HỢP VÀO app.py: xem file HUONG_DAN_TICH_HOP.md đi kèm.

CẤU HÌNH (Render > Settings > Environment):
    ORDERS_DATABASE_URL = postgresql://postgres.xxxx:<password>@aws-0-....pooler.supabase.com:5432/postgres

Nếu thiếu biến này, các route bên dưới sẽ trả lỗi rõ ràng cho người dùng
(và init_orders_tables() sẽ tự bỏ qua, in cảnh báo) thay vì làm app chính
bị crash lúc khởi động vì thiếu cấu hình CSDL phụ này.
"""
import os
import uuid
from datetime import datetime

from flask import Blueprint, request, jsonify, session, g, render_template, redirect, url_for
import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor, execute_values

# Import lại vài hàm/hằng số dùng chung từ app.py chính. AN TOÀN vì
# orders.py chỉ được `from orders import ...` Ở CUỐI app.py (sau khi các
# tên này đã định nghĩa xong) - giống hệt cách stocktake.py/price_adjustment.py
# đang làm (xem giải thích chi tiết trong run.py). CHỈ chạy đúng khi khởi
# động qua `python3 run.py`, không chạy trực tiếp `python3 app.py`.
from app import (
    _valid_store_codes,
    _current_actor_name,
    vn_now,
    format_vi_datetime,
    get_db as get_main_db,
    STORE_EMPLOYEES,
)

# bo_import.py: module riêng đọc + chuẩn hoá dữ liệu từ file Excel báo cáo
# gốc (sheet "THEO DÕI B0 KHÁCH HÀNG-2026") sang định dạng bảng bo_orders.
# Xem chi tiết quy ước cột trong docstring đầu file bo_import.py.
from bo_import import parse_bo_orders_excel

orders_bp = Blueprint('orders', __name__)

ORDERS_DATABASE_URL = os.environ.get('ORDERS_DATABASE_URL')

_orders_pool = None

STATUS_OPTIONS = ['Chưa đặt', 'Đã đặt', 'Đang về', 'Đã về kho', 'Đã giao', 'Đã huỷ']

# Thông tin cấp "YÊU CẦU ĐẶT" - chung cho mọi mặt hàng của 1 khách (mỗi dòng
# bo_orders đều lưu bản sao, nhưng luôn sửa đồng loạt qua /api/orders/save).
HEADER_TEXT_FIELDS = ['customer_name', 'customer_phone', 'vehicle_type', 'frame_number',
                      'vehicle_color', 'vehicle_year']
HEADER_DATE_FIELDS = ['customer_request_date']
# Giá trị đơn / Đặt cọc là số của CẢ ĐƠN (trong Excel gộp ô dọc qua mọi mặt hàng)
HEADER_NUMBER_FIELDS = ['order_value', 'deposit_amount']

# Thông tin cấp MẶT HÀNG - mỗi mã hàng 1 dòng, có thể khác nhau trong cùng 1 yêu cầu.
ITEM_TEXT_FIELDS = ['status', 'part_code', 'part_name', 'quantity', 'order_type']
ITEM_DATE_FIELDS = ['order_date', 'expected_delivery_date', 'customer_call_date',
                    'actual_delivery_date']
ITEM_NUMBER_FIELDS = []  # (tiền đã chuyển lên cấp đơn - xem HEADER_NUMBER_FIELDS)

# Độ dài tối đa theo schema (kiểm tra trước để báo lỗi rõ ràng thay vì lỗi 500)
FIELD_LIMITS = {
    'customer_name': 255, 'customer_phone': 30, 'vehicle_type': 100, 'frame_number': 50,
    'vehicle_color': 50, 'vehicle_year': 50, 'status': 50, 'part_code': 100,
    'quantity': 50, 'order_type': 100,
}
FIELD_LABELS = {
    'customer_name': 'Tên khách hàng', 'customer_phone': 'SĐT', 'vehicle_type': 'Loại xe',
    'frame_number': 'Số khung', 'vehicle_color': 'Màu', 'vehicle_year': 'Đời xe',
    'status': 'Trạng thái', 'part_code': 'Mã hàng', 'quantity': 'SL', 'order_type': 'Loại đơn',
}

# Các cột đọc ra cho danh sách (không lấy created_by/created_at... cho nhẹ payload)
LIST_COLUMNS = (
    'id, request_id, store_code, seq_no, status, customer_name, customer_phone, '
    'vehicle_type, frame_number, vehicle_color, vehicle_year, part_code, part_name, '
    'quantity, order_value, deposit_amount, order_date, order_type, '
    'customer_request_date, expected_delivery_date, customer_call_date, '
    'actual_delivery_date, call_note'
)


# ----------------------------------------------------------------------------
# POOL + VÒNG ĐỜI CONNECTION RIÊNG CHO CSDL ĐẶT HÀNG (SUPABASE)
# ----------------------------------------------------------------------------

def _get_orders_pool():
    """Connection pool LƯỜI BIẾNG (lazy) riêng cho CSDL đặt hàng - cùng lý
    do lazy-init như _get_pool() trong app.py (tránh treo lúc khởi động
    nếu Supabase đang "ngủ"/chưa kịp thức)."""
    global _orders_pool
    if _orders_pool is None:
        if not ORDERS_DATABASE_URL:
            raise RuntimeError(
                "Thiếu biến môi trường ORDERS_DATABASE_URL - chưa cấu hình CSDL "
                "Supabase riêng cho tính năng Danh Sách Đặt Hàng. Hãy thêm biến "
                "này trên Render (Settings > Environment)."
            )
        # max=5: đây là tính năng phụ, không cần pool lớn như CSDL chính (10).
        _orders_pool = pg_pool.SimpleConnectionPool(1, 5, ORDERS_DATABASE_URL, cursor_factory=RealDictCursor)
    return _orders_pool


def get_orders_db():
    db = getattr(g, '_orders_database', None)
    if db is None:
        pool = _get_orders_pool()
        db = pool.getconn()
        # Pre-ping: giống hệt lý do trong get_db() của app.py - Supabase
        # cũng có thể tự đóng kết nối rảnh lâu.
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
        g._orders_database = db
    return db


@orders_bp.teardown_app_request
def _close_orders_connection(exception):
    """teardown_app_request (không phải teardown_request) để hàm này luôn
    chạy sau MỌI request của cả app, kể cả các request không thuộc route
    của blueprint này - đúng như teardown_appcontext của CSDL chính."""
    db = getattr(g, '_orders_database', None)
    if db is not None:
        pool = _get_orders_pool()
        if exception is not None:
            try:
                db.rollback()
            except Exception:
                pass
        try:
            pool.putconn(db)
        except Exception:
            pass

def init_orders_tables():
    """Tạo bảng bo_orders trên CSDL Supabase riêng nếu chưa có (và tự nâng cấp
    bảng cũ: thêm cột mới + gom các dòng cũ thành "yêu cầu đặt"). Gọi 1 lần
    lúc khởi động app, TÁCH RIÊNG khỏi init_db() của CSDL chính (xem
    _init_orders_with_retry() ở cuối app.py)."""
    if not ORDERS_DATABASE_URL:
        print('[orders] Bỏ qua init_orders_tables(): chưa cấu hình ORDERS_DATABASE_URL.', flush=True)
        return

    pool = _get_orders_pool()
    db = pool.getconn()
    try:
        cursor = db.cursor()
        # Advisory lock mã riêng (727270002) - khác mã 727270001 của CSDL
        # chính - để không tranh chấp khoá lẫn nhau dù có trùng thời điểm.
        cursor.execute("SELECT pg_advisory_xact_lock(727270002)")
        cursor.execute("SET LOCAL lock_timeout = '10s'")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bo_orders (
                id SERIAL PRIMARY KEY,
                store_code VARCHAR(20) NOT NULL,
                status VARCHAR(50) NOT NULL DEFAULT 'Chưa đặt',
                customer_name VARCHAR(255) NOT NULL,
                customer_address TEXT,
                customer_phone VARCHAR(30),
                license_plate VARCHAR(30),
                frame_number VARCHAR(50),
                vehicle_type VARCHAR(100),
                vehicle_color VARCHAR(50),
                part_name TEXT,
                part_code VARCHAR(100),
                order_value NUMERIC,
                deposit_amount NUMERIC,
                po_code VARCHAR(100),
                customer_request_date DATE,
                order_date DATE,
                expected_delivery_date DATE,
                actual_delivery_date DATE,
                customer_call_date DATE,
                call_note TEXT,
                created_by VARCHAR(50),
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        ''')
        # --- Nâng cấp cấu trúc (idempotent): "yêu cầu đặt" có nhiều mặt hàng ---
        for col_def in (
            'request_id VARCHAR(40)',   # các dòng cùng request_id = cùng 1 yêu cầu của 1 khách
            'seq_no INTEGER',           # số thứ tự (STT) của yêu cầu, tính riêng từng chi nhánh
            'vehicle_year VARCHAR(50)', # ĐỜI XE
            'quantity VARCHAR(50)',     # SL (giữ dạng text để không mất dữ liệu kiểu "2 bộ")
            'order_type VARCHAR(100)',  # LOẠI ĐƠN
        ):
            cursor.execute(f'ALTER TABLE bo_orders ADD COLUMN IF NOT EXISTS {col_def}')

        # Dòng cũ chưa có request_id: gom các dòng cùng chi nhánh + khách + SĐT +
        # xe + ngày yêu cầu + ngày đặt + cùng lần tạo (vd cùng 1 lần import).
        cursor.execute('''
            UPDATE bo_orders b SET request_id = 'L' || g.min_id::text
            FROM (
                SELECT id, MIN(id) OVER (
                    PARTITION BY store_code, customer_name, COALESCE(customer_phone, ''),
                                 COALESCE(vehicle_type, ''), COALESCE(frame_number, ''),
                                 COALESCE(vehicle_color, ''), customer_request_date,
                                 order_date, created_at
                ) AS min_id
                FROM bo_orders WHERE request_id IS NULL
            ) g
            WHERE b.id = g.id AND b.request_id IS NULL
        ''')
        # Cấp STT cho yêu cầu cũ theo thứ tự nhập (id nhỏ = cũ hơn), từng chi nhánh.
        cursor.execute('''
            UPDATE bo_orders b SET seq_no = s.rn + s.base
            FROM (
                SELECT r.request_id, r.store_code,
                       ROW_NUMBER() OVER (PARTITION BY r.store_code ORDER BY r.first_id) AS rn,
                       COALESCE((SELECT MAX(x.seq_no) FROM bo_orders x
                                 WHERE x.store_code = r.store_code), 0) AS base
                FROM (SELECT request_id, store_code, MIN(id) AS first_id
                      FROM bo_orders WHERE seq_no IS NULL
                      GROUP BY request_id, store_code) r
            ) s
            WHERE b.seq_no IS NULL AND b.request_id = s.request_id AND b.store_code = s.store_code
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bo_orders_store ON bo_orders(store_code)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bo_orders_status ON bo_orders(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bo_orders_part_code ON bo_orders(part_code)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bo_orders_request ON bo_orders(request_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bo_orders_store_seq ON bo_orders(store_code, seq_no)')
        db.commit()
        cursor.close()
        print('[orders] init_orders_tables() OK - bảng bo_orders đã sẵn sàng trên Supabase.', flush=True)
    finally:
        pool.putconn(db)


# ----------------------------------------------------------------------------
# TIỆN ÍCH CHUYỂN ĐỔI DỮ LIỆU
# ----------------------------------------------------------------------------

def _parse_date(val):
    """Chuyển chuỗi 'YYYY-MM-DD' (input type=date của HTML) thành date;
    trả None nếu rỗng/không hợp lệ thay vì làm lỗi cả request."""
    if not val:
        return None
    try:
        return datetime.strptime(str(val).strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _parse_number(val):
    """Chuyển giá trị tiền nhập từ form (có thể có dấu phẩy ngăn cách
    nghìn kiểu '4,375,000') thành number; trả None nếu rỗng/không hợp lệ."""
    if val is None or val == '':
        return None
    if isinstance(val, (int, float)):
        return val
    cleaned = str(val).replace(',', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _iso(d):
    return d.isoformat() if d else None


def _num(v):
    return float(v) if v is not None else None


def _text_values(data, fields):
    out = {}
    for f in fields:
        v = data.get(f)
        out[f] = v.strip() if isinstance(v, str) and v.strip() else None
    return out


def _check_lengths(values):
    """Trả thông báo lỗi nếu có trường vượt độ dài cho phép, ngược lại None."""
    for f, limit in FIELD_LIMITS.items():
        v = values.get(f)
        if v and len(v) > limit:
            return f'{FIELD_LABELS.get(f, f)} quá dài (tối đa {limit} ký tự).'
    return None


def _collect_header(data):
    values = _text_values(data, HEADER_TEXT_FIELDS)
    for f in HEADER_DATE_FIELDS:
        values[f] = _parse_date(data.get(f))
    for f in HEADER_NUMBER_FIELDS:
        values[f] = _parse_number(data.get(f))
    return values


def _collect_item(data):
    values = _text_values(data, ITEM_TEXT_FIELDS)
    if not values.get('status'):
        values['status'] = STATUS_OPTIONS[0]
    for f in ITEM_DATE_FIELDS:
        values[f] = _parse_date(data.get(f))
    for f in ITEM_NUMBER_FIELDS:
        values[f] = _parse_number(data.get(f))
    return values


def _item_is_blank(values):
    """Mặt hàng mới hoàn toàn trống (chỉ có trạng thái mặc định) -> bỏ qua."""
    for f in ['part_code', 'part_name', 'quantity', 'order_type'] + ITEM_DATE_FIELDS:
        if values.get(f) not in (None, ''):
            return False
    return True


def _item_to_dict(r):
    return {
        'id': r['id'],
        'status': r['status'],
        'part_code': r['part_code'],
        'part_name': r['part_name'],
        'quantity': r['quantity'],
        'order_value': _num(r['order_value']),
        'deposit_amount': _num(r['deposit_amount']),
        'order_date': _iso(r['order_date']),
        'order_type': r['order_type'],
        'expected_delivery_date': _iso(r['expected_delivery_date']),
        'customer_call_date': _iso(r['customer_call_date']),
        'actual_delivery_date': _iso(r['actual_delivery_date']),
        'call_note': r['call_note'],
    }


def _rows_to_request(rid, items):
    """Gộp các dòng cùng request_id thành 1 yêu cầu. Thông tin khách/xe lấy giá
    trị đầu tiên khác rỗng trong các dòng (khớp cách Excel gộp ô)."""
    def first(field):
        for it in items:
            if it[field] not in (None, ''):
                return it[field]
        return None

    req = {
        'request_id': rid,
        'store_code': items[0]['store_code'],
        'seq_no': items[0]['seq_no'],
        'customer_name': items[0]['customer_name'],
        'customer_request_date': _iso(first('customer_request_date')),
    }
    for f in ['customer_phone', 'vehicle_type', 'frame_number', 'vehicle_color', 'vehicle_year']:
        req[f] = first(f)
    req['order_value'] = _num(first('order_value'))
    req['deposit_amount'] = _num(first('deposit_amount'))
    req['items'] = [_item_to_dict(it) for it in items]
    return req


# ----------------------------------------------------------------------------
# TRANG GIAO DIỆN
# ----------------------------------------------------------------------------

@orders_bp.route('/orders')
def orders_page():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template(
        'orders.html',
        status_options=STATUS_OPTIONS,
        role=session.get('role'),
        store_code=session.get('store_code'),
        store_list=sorted(STORE_EMPLOYEES.keys()),
    )


# ----------------------------------------------------------------------------
# API
# ----------------------------------------------------------------------------

@orders_bp.route('/api/orders', methods=['GET'])
def list_orders():
    """Danh sách theo YÊU CẦU ĐẶT (mỗi yêu cầu kèm danh sách mặt hàng). Phân
    trang theo yêu cầu nên 1 khách không bao giờ bị cắt đôi giữa 2 trang. Lọc
    theo trạng thái/từ khoá: yêu cầu nào có ÍT NHẤT 1 mặt hàng khớp sẽ hiện,
    và hiện đủ mọi mặt hàng của yêu cầu đó."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    db = get_orders_db()
    cursor = db.cursor()

    conditions = []
    params = []

    if role == 'store':
        conditions.append('store_code = %s')
        params.append(session['store_code'])
    else:
        store_filter = (request.args.get('store_code') or '').strip()
        if store_filter:
            conditions.append('store_code = %s')
            params.append(store_filter)

    status_filter = (request.args.get('status') or '').strip()
    if status_filter:
        conditions.append('status = %s')
        params.append(status_filter)

    # Lọc theo đúng 1 yêu cầu (dùng để tải lại 1 dòng sau khi sửa trực tiếp
    # trên giao diện kiểu bảng tính, không cần tải lại cả trang).
    request_id_filter = (request.args.get('request_id') or '').strip()
    if request_id_filter:
        conditions.append('request_id = %s')
        params.append(request_id_filter)

    search = (request.args.get('search') or '').strip()
    if search:
        conditions.append('''(
            customer_name ILIKE %s OR customer_phone ILIKE %s OR frame_number ILIKE %s OR
            part_code ILIKE %s OR part_name ILIKE %s OR po_code ILIKE %s
        )''')
        like = f'%{search}%'
        params.extend([like] * 6)

    where_clause = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''

    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.args.get('page_size', 50))
    except (TypeError, ValueError):
        page_size = 50
    page_size = max(1, min(page_size, 200))

    cursor.execute(f'SELECT COUNT(DISTINCT request_id) AS c FROM bo_orders {where_clause}', params)
    total = cursor.fetchone()['c']

    # Bước 1: chọn các yêu cầu của trang này (mới nhất = STT lớn nhất lên trên).
    cursor.execute(
        f'''SELECT request_id FROM bo_orders {where_clause}
            GROUP BY request_id
            ORDER BY MAX(store_code) ASC, MAX(seq_no) DESC NULLS LAST, request_id DESC
            LIMIT %s OFFSET %s''',
        params + [page_size, (page - 1) * page_size]
    )
    rids = [r['request_id'] for r in cursor.fetchall()]

    # Bước 2: lấy đủ mặt hàng của các yêu cầu đó.
    by_req = {rid: [] for rid in rids}
    if rids:
        cursor.execute(
            f'SELECT {LIST_COLUMNS} FROM bo_orders WHERE request_id = ANY(%s) ORDER BY id ASC',
            (rids,)
        )
        for r in cursor.fetchall():
            by_req[r['request_id']].append(r)
    cursor.close()

    data = [_rows_to_request(rid, by_req[rid]) for rid in rids if by_req[rid]]

    return jsonify({
        'success': True,
        'data': data,
        'total': total,
        'page': page,
        'page_size': page_size,
    })


@orders_bp.route('/api/orders/save', methods=['POST'])
def save_order():
    """Tạo mới hoặc cập nhật 1 YÊU CẦU ĐẶT cùng toàn bộ mặt hàng của nó.

    Body: {request_id?, store_code? (admin, khi tạo mới), header: {...}, items: [{id?, ...}]}
      - item có id  -> cập nhật; item không id -> thêm mới;
      - mặt hàng cũ không còn trong danh sách gửi lên -> bị xoá.
    Các cột không hiển thị trên giao diện (địa chỉ, biển số, PO, ghi chú) được
    giữ nguyên, không bị ghi đè."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    header = _collect_header(data.get('header') or {})
    if not header.get('customer_name'):
        return jsonify({'error': 'Vui lòng nhập tên khách hàng.'}), 400
    err = _check_lengths(header)
    if err:
        return jsonify({'error': err}), 400

    raw_items = data.get('items') or []
    items = []  # list[(id|None, values)]
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        values = _collect_item(raw)
        item_id = None
        if raw.get('id') not in (None, ''):
            try:
                item_id = int(raw['id'])
            except (TypeError, ValueError):
                return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400
        if item_id is None and _item_is_blank(values):
            continue
        err = _check_lengths(values)
        if err:
            return jsonify({'error': err}), 400
        items.append((item_id, values))
    if not items:
        return jsonify({'error': 'Vui lòng nhập ít nhất 1 mặt hàng.'}), 400

    request_id = (data.get('request_id') or '').strip() or None
    now = vn_now()
    db = get_orders_db()
    cursor = db.cursor()

    try:
        if request_id:
            # ---- SỬA yêu cầu có sẵn ----
            if role == 'admin':
                cursor.execute('SELECT id, store_code, seq_no FROM bo_orders WHERE request_id = %s', (request_id,))
            else:
                cursor.execute('SELECT id, store_code, seq_no FROM bo_orders WHERE request_id = %s AND store_code = %s',
                               (request_id, session['store_code']))
            existing = cursor.fetchall()
            if not existing:
                db.rollback()
                cursor.close()
                return jsonify({'error': 'Không tìm thấy đơn (hoặc không thuộc cửa hàng của bạn).'}), 404
            store_code = existing[0]['store_code']
            seq_no = existing[0]['seq_no']
            existing_ids = {r['id'] for r in existing}
            keep_ids = {i for i, _ in items if i is not None}
            if not keep_ids <= existing_ids:
                db.rollback()
                cursor.close()
                return jsonify({'error': 'Có mặt hàng không thuộc đơn này.'}), 400
        else:
            # ---- TẠO MỚI ----
            if role == 'store':
                store_code = session['store_code']
            else:
                store_code = (data.get('store_code') or '').strip()
                main_cursor = get_main_db().cursor()
                valid_stores = _valid_store_codes(main_cursor)
                main_cursor.close()
                if not store_code or store_code not in valid_stores:
                    db.rollback()
                    cursor.close()
                    return jsonify({'error': 'Vui lòng chọn chi nhánh hợp lệ.'}), 400
            request_id = uuid.uuid4().hex
            cursor.execute('SELECT COALESCE(MAX(seq_no), 0) + 1 AS n FROM bo_orders WHERE store_code = %s', (store_code,))
            seq_no = cursor.fetchone()['n']
            existing_ids = set()
            keep_ids = set()

        # Xoá mặt hàng đã bị gỡ khỏi danh sách
        removed = list(existing_ids - keep_ids)
        if removed:
            cursor.execute('DELETE FROM bo_orders WHERE id = ANY(%s) AND request_id = %s', (removed, request_id))

        actor = _current_actor_name()
        for item_id, values in items:
            row = {**header, **values, 'request_id': request_id, 'store_code': store_code,
                   'seq_no': seq_no, 'updated_at': now}
            if item_id is not None:
                row['id'] = item_id
                cols = HEADER_TEXT_FIELDS + HEADER_DATE_FIELDS + HEADER_NUMBER_FIELDS \
                    + ITEM_TEXT_FIELDS + ITEM_DATE_FIELDS + ITEM_NUMBER_FIELDS
                set_clause = ', '.join(f'{c} = %({c})s' for c in cols)
                cursor.execute(
                    f'UPDATE bo_orders SET {set_clause}, updated_at = %(updated_at)s '
                    f'WHERE id = %(id)s AND request_id = %(request_id)s', row)
            else:
                row['created_by'] = actor
                row['created_at'] = now
                cols = ['request_id', 'store_code', 'seq_no'] + HEADER_TEXT_FIELDS + HEADER_DATE_FIELDS \
                    + HEADER_NUMBER_FIELDS + ITEM_TEXT_FIELDS + ITEM_DATE_FIELDS + ITEM_NUMBER_FIELDS \
                    + ['created_by', 'created_at', 'updated_at']
                cursor.execute(
                    f'INSERT INTO bo_orders ({", ".join(cols)}) VALUES ({", ".join(f"%({c})s" for c in cols)})', row)
        db.commit()
    except Exception as e:
        db.rollback()
        cursor.close()
        return jsonify({'error': f'Lỗi lưu dữ liệu: {e}'}), 500
    cursor.close()

    return jsonify({'success': True, 'request_id': request_id, 'seq_no': seq_no})


@orders_bp.route('/api/orders/import', methods=['POST'])
def import_orders_excel():
    """Nhập hàng loạt từ file Excel báo cáo gốc (sheet "THEO DÕI B0 KHÁCH
    HÀNG-2026") vào 1 chi nhánh cụ thể. CHỈ admin được dùng vì đây là thao
    tác ghi số lượng lớn dữ liệu lịch sử, không phải nghiệp vụ hàng ngày.

    Toàn bộ các dòng được ghi trong 1 transaction duy nhất: nếu có lỗi ở
    bất kỳ dòng nào, TOÀN BỘ sẽ được rollback (không tạo ra import dở
    dang, tránh trùng lặp nếu người dùng thử chạy lại).

    Tuỳ chọn replace=1: xoá TOÀN BỘ đơn hiện có của chi nhánh đó ngay trong
    cùng transaction trước khi ghi (dùng khi nhập lại từ Excel)."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    if session.get('role') != 'admin':
        return jsonify({'error': 'Chỉ admin mới được import hàng loạt.'}), 403

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'Vui lòng chọn file Excel (.xlsx).'}), 400

    store_code = (request.form.get('store_code') or '').strip()
    main_cursor = get_main_db().cursor()
    valid_stores = _valid_store_codes(main_cursor)
    main_cursor.close()
    if not store_code or store_code not in valid_stores:
        return jsonify({'error': 'Vui lòng chọn chi nhánh hợp lệ.'}), 400

    sheet_name = (request.form.get('sheet_name') or '').strip() or None
    replace = (request.form.get('replace') or '').strip() in ('1', 'true', 'on')

    db = get_orders_db()
    cursor = db.cursor()

    # STT bắt đầu từ 1 nếu thay thế toàn bộ, ngược lại nối tiếp STT hiện có.
    start_seq = 1
    if not replace:
        cursor.execute('SELECT COALESCE(MAX(seq_no), 0) + 1 AS n FROM bo_orders WHERE store_code = %s', (store_code,))
        start_seq = cursor.fetchone()['n']

    try:
        kwargs = {'store_code': store_code, 'created_by': _current_actor_name(), 'start_seq': start_seq}
        if sheet_name:
            kwargs['sheet_name'] = sheet_name
        rows, skipped = parse_bo_orders_excel(file.stream, **kwargs)
    except Exception as e:
        db.rollback()
        cursor.close()
        return jsonify({'error': f'Không đọc được file: {e}'}), 400

    if not rows:
        db.rollback()
        cursor.close()
        return jsonify({'error': 'Không tìm thấy dòng dữ liệu hợp lệ nào để import (thiếu Tên khách hàng?).'}), 400

    # Ghi theo LÔ (execute_values) thay vì từng dòng: ít round-trip tới Supabase
    # hơn hẳn, vẫn giữ nguyên "tất cả hoặc không có gì" (1 transaction).
    insert_columns = [
        'store_code', 'request_id', 'seq_no', 'status', 'customer_name', 'customer_address',
        'customer_phone', 'license_plate', 'frame_number', 'vehicle_type', 'vehicle_color',
        'vehicle_year', 'part_name', 'part_code', 'quantity', 'order_type',
        'order_value', 'deposit_amount', 'po_code',
        'customer_request_date', 'order_date', 'expected_delivery_date',
        'actual_delivery_date', 'customer_call_date', 'call_note',
        'created_by', 'created_at', 'updated_at',
    ]
    try:
        if replace:
            cursor.execute('DELETE FROM bo_orders WHERE store_code = %s', (store_code,))
        values_list = [tuple(row.get(col) for col in insert_columns) for row in rows]
        execute_values(
            cursor,
            f'INSERT INTO bo_orders ({", ".join(insert_columns)}) VALUES %s',
            values_list,
            page_size=500,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        cursor.close()
        return jsonify({'error': f'Lỗi khi ghi CSDL - ĐÃ HUỶ TOÀN BỘ, chưa có dòng nào được lưu: {e}'}), 500
    cursor.close()

    return jsonify({
        'success': True,
        'inserted': len(rows),
        'requests': len({r['request_id'] for r in rows}),
        'skipped': len(skipped),
        'skipped_rows_excel': [s['row'] for s in skipped][:100],
    })


@orders_bp.route('/api/orders/delete', methods=['POST'])
def delete_order():
    """Xoá cả 1 yêu cầu đặt (mọi mặt hàng của nó). Muốn xoá riêng 1 mặt hàng:
    mở đơn > bấm xoá mặt hàng > Lưu (xử lý trong /api/orders/save)."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    request_id = (data.get('request_id') or '').strip()
    if not request_id:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_orders_db()
    cursor = db.cursor()
    if role == 'admin':
        cursor.execute('DELETE FROM bo_orders WHERE request_id = %s', (request_id,))
    else:
        cursor.execute('DELETE FROM bo_orders WHERE request_id = %s AND store_code = %s',
                       (request_id, session['store_code']))
    deleted = cursor.rowcount
    db.commit()
    cursor.close()

    if not deleted:
        return jsonify({'error': 'Không tìm thấy đơn (hoặc không thuộc cửa hàng của bạn).'}), 404
    return jsonify({'success': True, 'deleted_items': deleted})