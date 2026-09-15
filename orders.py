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

# Các cột dạng text/chuỗi thường (đọc/ghi trực tiếp, strip() -> None nếu rỗng)
ORDER_TEXT_FIELDS = [
    'status', 'customer_name', 'customer_address', 'customer_phone',
    'license_plate', 'frame_number', 'vehicle_type', 'vehicle_color',
    'part_name', 'part_code', 'po_code', 'call_note',
]
# Các cột ngày (input type="date" -> 'YYYY-MM-DD')
ORDER_DATE_FIELDS = [
    'customer_request_date', 'order_date', 'expected_delivery_date',
    'actual_delivery_date', 'customer_call_date',
]
# Các cột tiền/số
ORDER_NUMBER_FIELDS = ['order_value', 'deposit_amount']

ALL_EDITABLE_FIELDS = ORDER_TEXT_FIELDS + ORDER_DATE_FIELDS + ORDER_NUMBER_FIELDS


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
    """Tạo bảng bo_orders trên CSDL Supabase riêng nếu chưa có. Gọi 1 lần
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
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bo_orders_store ON bo_orders(store_code)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bo_orders_status ON bo_orders(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bo_orders_part_code ON bo_orders(part_code)')
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


def _row_to_dict(r):
    d = dict(r)
    for f in ORDER_DATE_FIELDS:
        d[f] = r[f].isoformat() if r[f] else None
    for f in ORDER_NUMBER_FIELDS:
        d[f] = float(r[f]) if r[f] is not None else None
    d['created_at'] = format_vi_datetime(r['created_at']) if r['created_at'] else None
    d['updated_at'] = format_vi_datetime(r['updated_at']) if r['updated_at'] else None
    return d


def _collect_values_from_payload(data):
    """Gom + chuẩn hoá toàn bộ trường có thể sửa từ JSON body gửi lên."""
    values = {}
    for f in ORDER_TEXT_FIELDS:
        v = data.get(f)
        values[f] = v.strip() if isinstance(v, str) and v.strip() else None
    if not values.get('status'):
        values['status'] = STATUS_OPTIONS[0]
    for f in ORDER_DATE_FIELDS:
        values[f] = _parse_date(data.get(f))
    for f in ORDER_NUMBER_FIELDS:
        values[f] = _parse_number(data.get(f))
    return values


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

    search = (request.args.get('search') or '').strip()
    if search:
        conditions.append('''(
            customer_name ILIKE %s OR customer_phone ILIKE %s OR
            part_code ILIKE %s OR part_name ILIKE %s OR po_code ILIKE %s
        )''')
        like = f'%{search}%'
        params.extend([like, like, like, like, like])

    where_clause = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''

    # PHÂN TRANG: sau khi import hàng loạt (~3.700+ dòng), tải và dựng lại
    # TOÀN BỘ bảng mỗi lần (kể cả mỗi lần tự động làm mới ngầm) làm giao
    # diện rất chậm - cả vì payload JSON lớn lẫn vì phải build lại hàng
    # nghìn <tr> trong DOM. Giới hạn số dòng trả về mỗi lần bằng LIMIT/OFFSET,
    # kèm tổng số dòng (total) để giao diện hiển thị điều hướng trang.
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.args.get('page_size', 100))
    except (TypeError, ValueError):
        page_size = 100
    page_size = max(1, min(page_size, 300))  # chặn client xin quá nhiều 1 lần

    cursor.execute(f'SELECT COUNT(*) AS c FROM bo_orders {where_clause}', params)
    total = cursor.fetchone()['c']

    # Sắp xếp chính vẫn theo created_at DESC (mới thêm/sửa lên trên - đúng
    # thói quen dùng hàng ngày). Các khoá phụ (customer_name, order_date, id)
    # chỉ có tác dụng "phá vỡ đồng hạng" (tie-break): khi import hàng loạt,
    # nhiều dòng có created_at giống hệt nhau (cùng 1 lần import) - lúc đó
    # Postgres không đảm bảo giữ đúng thứ tự chèn, nên cần khoá phụ để các
    # dòng của CÙNG 1 khách + CÙNG 1 ngày đặt luôn nằm liền kề nhau; còn
    # nếu khác ngày đặt thì vẫn tách thành nhóm riêng (order_date khác nhau).
    cursor.execute(
        f'''SELECT * FROM bo_orders {where_clause}
            ORDER BY created_at DESC, customer_name ASC, order_date ASC NULLS LAST, id ASC
            LIMIT %s OFFSET %s''',
        params + [page_size, (page - 1) * page_size]
    )
    rows = cursor.fetchall()
    cursor.close()

    return jsonify({
        'success': True,
        'data': [_row_to_dict(r) for r in rows],
        'total': total,
        'page': page,
        'page_size': page_size,
    })




@orders_bp.route('/api/orders/save', methods=['POST'])
def save_order():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    customer_name = (data.get('customer_name') or '').strip()
    if not customer_name:
        return jsonify({'error': 'Vui lòng nhập tên khách hàng.'}), 400

    if role == 'store':
        store_code = session['store_code']
    else:
        store_code = (data.get('store_code') or '').strip()
        # _valid_store_codes cần cursor của CSDL CHÍNH (Neon - nơi có bảng
        # users), KHÔNG PHẢI CSDL đặt hàng (Supabase) - nên phải mở riêng.
        main_cursor = get_main_db().cursor()
        valid_stores = _valid_store_codes(main_cursor)
        main_cursor.close()
        if not store_code or store_code not in valid_stores:
            return jsonify({'error': 'Vui lòng chọn chi nhánh hợp lệ.'}), 400

    values = _collect_values_from_payload(data)
    values['customer_name'] = customer_name
    values['store_code'] = store_code

    now = vn_now()
    db = get_orders_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO bo_orders (
            store_code, status, customer_name, customer_address, customer_phone,
            license_plate, frame_number, vehicle_type, vehicle_color,
            part_name, part_code, order_value, deposit_amount, po_code,
            customer_request_date, order_date, expected_delivery_date,
            actual_delivery_date, customer_call_date, call_note,
            created_by, created_at, updated_at
        ) VALUES (
            %(store_code)s, %(status)s, %(customer_name)s, %(customer_address)s, %(customer_phone)s,
            %(license_plate)s, %(frame_number)s, %(vehicle_type)s, %(vehicle_color)s,
            %(part_name)s, %(part_code)s, %(order_value)s, %(deposit_amount)s, %(po_code)s,
            %(customer_request_date)s, %(order_date)s, %(expected_delivery_date)s,
            %(actual_delivery_date)s, %(customer_call_date)s, %(call_note)s,
            %(created_by)s, %(created_at)s, %(updated_at)s
        ) RETURNING id
    ''', {**values, 'created_by': _current_actor_name(), 'created_at': now, 'updated_at': now})
    new_id = cursor.fetchone()['id']
    db.commit()
    cursor.close()

    return jsonify({'success': True, 'id': new_id})


@orders_bp.route('/api/orders/update', methods=['POST'])
def update_order():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    try:
        order_id = int(data.get('id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    customer_name = (data.get('customer_name') or '').strip()
    if not customer_name:
        return jsonify({'error': 'Vui lòng nhập tên khách hàng.'}), 400

    values = _collect_values_from_payload(data)
    values['customer_name'] = customer_name
    values['id'] = order_id
    values['updated_at'] = vn_now()

    set_clause = ', '.join(f'{f} = %({f})s' for f in ['customer_name'] + ALL_EDITABLE_FIELDS)

    db = get_orders_db()
    cursor = db.cursor()
    if role == 'admin':
        cursor.execute(
            f'UPDATE bo_orders SET {set_clause}, updated_at = %(updated_at)s WHERE id = %(id)s',
            values
        )
    else:
        values['store_code'] = session['store_code']
        cursor.execute(
            f'UPDATE bo_orders SET {set_clause}, updated_at = %(updated_at)s '
            f'WHERE id = %(id)s AND store_code = %(store_code)s',
            values
        )
    updated = cursor.rowcount
    db.commit()
    cursor.close()

    if not updated:
        return jsonify({'error': 'Không tìm thấy bản ghi (hoặc không thuộc cửa hàng của bạn).'}), 404
    return jsonify({'success': True})


@orders_bp.route('/api/orders/import', methods=['POST'])
def import_orders_excel():
    """Nhập hàng loạt từ file Excel báo cáo gốc (sheet "THEO DÕI B0 KHÁCH
    HÀNG-2026") vào 1 chi nhánh cụ thể. CHỈ admin được dùng vì đây là thao
    tác ghi số lượng lớn dữ liệu lịch sử, không phải nghiệp vụ hàng ngày.

    Toàn bộ các dòng được ghi trong 1 transaction duy nhất: nếu có lỗi ở
    bất kỳ dòng nào, TOÀN BỘ sẽ được rollback (không tạo ra import dở
    dang, tránh trùng lặp nếu người dùng thử chạy lại)."""
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

    try:
        kwargs = {'store_code': store_code, 'created_by': _current_actor_name()}
        if sheet_name:
            kwargs['sheet_name'] = sheet_name
        rows, skipped = parse_bo_orders_excel(file.stream, **kwargs)
    except Exception as e:
        return jsonify({'error': f'Không đọc được file: {e}'}), 400

    if not rows:
        return jsonify({'error': 'Không tìm thấy dòng dữ liệu hợp lệ nào để import (thiếu Tên khách hàng?).'}), 400

    db = get_orders_db()
    cursor = db.cursor()
    inserted = 0
    # Ghi theo LÔ (batch insert bằng execute_values) thay vì từng dòng một:
    # với ~3.700 dòng, insert tuần tự (mỗi dòng 1 round-trip riêng tới
    # Supabase) rất chậm vì độ trễ mạng cộng dồn theo từng dòng. Gộp thành
    # 1 câu lệnh VALUES nhiều dòng giúp giảm số round-trip xuống chỉ còn vài
    # lượt (execute_values tự chia trang theo page_size), nhanh hơn rất
    # nhiều mà vẫn giữ nguyên hành vi "tất cả hoặc không có gì" (1 transaction).
    insert_columns = [
        'store_code', 'status', 'customer_name', 'customer_address', 'customer_phone',
        'license_plate', 'frame_number', 'vehicle_type', 'vehicle_color',
        'part_name', 'part_code', 'order_value', 'deposit_amount', 'po_code',
        'customer_request_date', 'order_date', 'expected_delivery_date',
        'actual_delivery_date', 'customer_call_date', 'call_note',
        'created_by', 'created_at', 'updated_at',
    ]
    try:
        values_list = [
            tuple(row.get(col) for col in insert_columns)
            for row in rows
        ]
        execute_values(
            cursor,
            f'INSERT INTO bo_orders ({", ".join(insert_columns)}) VALUES %s',
            values_list,
            page_size=500,
        )
        inserted = len(values_list)
        db.commit()
    except Exception as e:
        db.rollback()
        cursor.close()
        return jsonify({'error': f'Lỗi khi ghi CSDL - ĐÃ HUỶ TOÀN BỘ, chưa có dòng nào được lưu: {e}'}), 500
    cursor.close()

    return jsonify({
        'success': True,
        'inserted': inserted,
        'skipped': len(skipped),
        'skipped_rows_excel': [s['row'] for s in skipped][:100],
    })


@orders_bp.route('/api/orders/delete', methods=['POST'])
def delete_order():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    role = session['role']
    if role not in ('store', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    try:
        order_id = int(data.get('id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400

    db = get_orders_db()
    cursor = db.cursor()
    if role == 'admin':
        cursor.execute('DELETE FROM bo_orders WHERE id = %s', (order_id,))
    else:
        cursor.execute('DELETE FROM bo_orders WHERE id = %s AND store_code = %s', (order_id, session['store_code']))
    deleted = cursor.rowcount
    db.commit()
    cursor.close()

    if not deleted:
        return jsonify({'error': 'Không tìm thấy bản ghi (hoặc không thuộc cửa hàng của bạn).'}), 404
    return jsonify({'success': True})