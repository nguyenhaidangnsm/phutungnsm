# -*- coding: utf-8 -*-
"""
CHỨC NĂNG KIỂM KÊ KHO - tách riêng khỏi app.py thành 1 Blueprint độc lập để
không phải nhét thêm ~500 dòng vào app.py vốn đã rất dài.

CÁCH MÓC NỐI VÀO app.py (chỉ cần thêm đúng 3 chỗ, xem comment "MÓC NỐI" bên
dưới mỗi đoạn):
    1. Sau khi app.py đã định nghĩa xong get_db/vn_now/format_vi_datetime/
       _valid_store_codes và biến `app` (khoảng dòng 560-600 hiện tại):

           from stocktake import stocktake_bp, init_stocktake_tables
           app.register_blueprint(stocktake_bp)

    2. Bên trong init_db(), SAU khi đã tạo xong các bảng cũ (trước dòng
       cursor.close() hoặc db.commit() cuối hàm):

           init_stocktake_tables(cursor)

    3. Trong index.html, thêm 1 link/nút (chỉ hiện với admin) trỏ tới
       /stocktake - xem gợi ý ở cuối file này.

Đặt file này cùng cấp với app.py, và templates/stocktake.html cùng thư mục
templates/ với index.html.
"""
import io
from datetime import timedelta

import pandas as pd
from flask import Blueprint, render_template, request, jsonify, session, send_file
from psycopg2.extras import execute_values

# Import lại đúng những gì app.py đã có sẵn - KHÔNG định nghĩa lại, để tránh
# 2 nguồn sự thật khác nhau về cách kết nối DB / format ngày giờ. Đây là
# import "vòng" nhưng an toàn vì app.py import stocktake SAU KHI các hàm này
# đã được định nghĩa (xem hướng dẫn ở đầu file).
from app import (
    get_db, vn_now, format_vi_datetime, _valid_store_codes, STORE_REGIONS,
    _INVENTORY_ALLOWED_PREFIXES, _INVENTORY_ALLOWED_STORES, _SUFFIX_ALIAS_TO_STORE,
)

stocktake_bp = Blueprint('stocktake', __name__)

# Sau khi CHỐT, admin còn được mở lại sửa trong vòng bao nhiêu giờ (yêu cầu
# của người dùng: không khoá vĩnh viễn, nhưng cũng không mở vô hạn).
REOPEN_WINDOW_HOURS = 48

# Nhật ký đếm chi tiết (stocktake_counts) của 1 phiên ĐÃ CHỐT chỉ giữ trong
# DB bấy nhiêu ngày rồi tự xoá - vì lúc chốt/xuất Excel đã có sheet riêng
# "Nhat Ky Dem" lưu lại đầy đủ, giữ mãi trong DB không cần thiết và làm
# bảng phình to theo thời gian. KHÔNG đụng tới stocktake_adjustments (biên
# bản kết quả cuối cùng) hay bản thân stocktake_sessions - chỉ xoá dòng log
# thô của stocktake_counts.
LOG_RETENTION_DAYS = 5


def init_stocktake_tables(cursor):
    """Gọi 1 lần trong init_db() của app.py - tạo toàn bộ bảng cho tính
    năng kiểm kê. Dùng IF NOT EXISTS nên gọi lại nhiều lần vẫn an toàn."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stocktake_sessions (
            id SERIAL PRIMARY KEY,
            store_code VARCHAR(20) NOT NULL,
            cutoff_time TIMESTAMP NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            created_by VARCHAR(50),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            closed_by VARCHAR(50),
            closed_at TIMESTAMP,
            note TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_stocktake_sessions_store ON stocktake_sessions(store_code)')

    # Chụp CỨNG tồn sổ sách tại T0 - không đổi dù sau đó admin có upload lại
    # file tồn kho mới trong lúc phiên đang mở.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stocktake_snapshot_items (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES stocktake_sessions(id) ON DELETE CASCADE,
            part_code VARCHAR(100) NOT NULL,
            part_name TEXT,
            unit VARCHAR(50),
            book_quantity NUMERIC NOT NULL DEFAULT 0,
            UNIQUE(session_id, part_code)
        )
    ''')

    # Mỗi lần đếm là 1 dòng riêng (không ghi đè) để cộng dồn được khi đếm
    # nhiều lần/nhiều khu vực, và giữ được counted_at của TỪNG lần đếm.
    # LƯU Ý: dữ liệu bảng này của các phiên ĐÃ CHỐT tự động bị xoá sau
    # LOG_RETENTION_DAYS ngày - xem _cleanup_old_logs().
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stocktake_counts (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES stocktake_sessions(id) ON DELETE CASCADE,
            part_code VARCHAR(100) NOT NULL,
            counted_quantity NUMERIC NOT NULL,
            area_note VARCHAR(100),
            counted_by VARCHAR(50),
            counted_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_stocktake_counts_session ON stocktake_counts(session_id, part_code)')

    # Kết quả CUỐI khi chốt phiên - lưu vĩnh viễn để làm biên bản, tách khỏi
    # stocktake_counts (vốn có thể bị sửa lại nếu phiên được mở lại).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stocktake_adjustments (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES stocktake_sessions(id) ON DELETE CASCADE,
            part_code VARCHAR(100) NOT NULL,
            part_name TEXT,
            book_quantity NUMERIC NOT NULL DEFAULT 0,
            known_movement_qty NUMERIC NOT NULL DEFAULT 0,
            expected_quantity NUMERIC NOT NULL DEFAULT 0,
            counted_quantity NUMERIC NOT NULL DEFAULT 0,
            diff_quantity NUMERIC NOT NULL DEFAULT 0,
            note TEXT,
            UNIQUE(session_id, part_code)
        )
    ''')

    # Lịch sử mỗi lần admin mở lại 1 phiên đã chốt - phục vụ minh bạch/audit
    # vì tính năng "cho sửa sau khi chốt" vốn dễ bị lạm dụng nếu không ghi lại.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stocktake_reopen_log (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES stocktake_sessions(id) ON DELETE CASCADE,
            reopened_by VARCHAR(50),
            reopened_at TIMESTAMP NOT NULL DEFAULT NOW(),
            reason TEXT
        )
    ''')

    # Hệ thống KHÔNG có bảng giao dịch bán hàng/nhập hàng từ NCC (chỉ có
    # inventory_items được TRUNCATE + nạp lại toàn bộ mỗi lần admin upload
    # file tồn kho - xem app.py::upload_inventory) - nên không thể tự động
    # tính được phần phát sinh này như đã làm với chuyển kho/báo hư. Bảng
    # này cho phép nhân viên TỰ BÁO nhanh 1 mã đã bán/nhập thêm SAU khi đã
    # đếm mã đó trong phiên đang mở, để "Chênh Lệch" tự cập nhật lại đúng
    # mà không cần đếm lại vật lý. Cho phép báo NHIỀU LẦN cho cùng 1 mã
    # (giữ lịch sử từng lần, không UPSERT ghi đè) - vd bán rải rác nhiều
    # đợt trong ngày.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stocktake_manual_adjustments (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES stocktake_sessions(id) ON DELETE CASCADE,
            part_code VARCHAR(100) NOT NULL,
            adjustment_type VARCHAR(20) NOT NULL CHECK (adjustment_type IN ('sold', 'received_other')),
            quantity NUMERIC NOT NULL CHECK (quantity > 0),
            note TEXT,
            created_by VARCHAR(50),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_stocktake_manual_adj_session '
        'ON stocktake_manual_adjustments(session_id, part_code)'
    )

    # Index cho inventory_items.store_code (bảng này định nghĩa bên app.py,
    # vốn chỉ có sẵn index trên part_code) - câu SELECT lấy tồn kho theo
    # từng cửa hàng lúc "Bắt Đầu Kiểm Kê" (stocktake_start) đang phải quét
    # toàn bộ bảng nếu thiếu index này, càng chậm khi tồn kho hệ thống càng
    # lớn. TRUNCATE (dùng khi admin tải lại file tồn kho) không xoá index,
    # nên chỉ cần tạo 1 lần là dùng mãi.
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_inventory_store ON inventory_items(store_code)')


def _cleanup_old_logs(db, cursor):
    """Xoá nhật ký đếm thô (stocktake_counts) của các phiên ĐÃ CHỐT quá
    LOG_RETENTION_DAYS ngày kể từ lúc chốt. Gọi ở đầu các route có liên
    quan tới log/lịch sử (không cần cron riêng) - rẻ vì chỉ 1 câu DELETE
    có điều kiện, và closed_at đã có index qua session nên không quét
    toàn bộ bảng lớn."""
    cursor.execute('''
        DELETE FROM stocktake_counts c
        USING stocktake_sessions s
        WHERE c.session_id = s.id
          AND s.status = 'closed'
          AND s.closed_at IS NOT NULL
          AND s.closed_at < NOW() - (%s * INTERVAL '1 day')
    ''', (LOG_RETENTION_DAYS,))
    db.commit()


def _require_admin():
    """Trả về response lỗi (jsonify, status) nếu không phải admin, hoặc
    None nếu hợp lệ - gọi ở đầu mỗi route, return luôn nếu khác None."""
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Chỉ Admin mới được thao tác kiểm kê kho.'}), 403
    return None


def _get_session_or_404(cursor, session_id):
    cursor.execute('SELECT * FROM stocktake_sessions WHERE id = %s', (session_id,))
    return cursor.fetchone()


def _parse_xuat_kho_excel(file_storage):
    """Đọc CÙNG định dạng file "Tổng hợp tồn kho" mà app.py::parse_inventory_excel
    đọc (dùng để nạp Tồn Sổ Sách lúc bắt đầu kiểm), nhưng lấy cột "Xuất kho -
    Số lượng" thay vì "Cuối kỳ - Số lượng" - phục vụ import số bán ra
    (phát sinh) vào phiên kiểm kê SAU KHI đã đếm xong, TRƯỚC khi chốt.

    Tái dùng ĐÚNG bộ tiền tố/hậu tố kho hợp lệ (_INVENTORY_ALLOWED_PREFIXES /
    _INVENTORY_ALLOWED_STORES / _SUFFIX_ALIAS_TO_STORE) import từ app.py -
    để không lệch với cách hệ thống đang lọc "KPT/KPK/KPTN <cửa hàng>" khi
    nạp tồn kho gốc, chỉ khác đúng 1 chỗ là cột số lượng đọc ra.

    Trả về (rows, period_note):
      - rows: list dict {part_code, store_code, quantity} - đã cộng dồn
        theo (mã hàng, cửa hàng) qua các nhóm kho KPT/KPK/KPTN, chỉ giữ
        dòng có xuất > 0.
      - period_note: dòng text mô tả khoảng ngày ghi trong file (dòng 2),
        trả về để FE hiển thị cho admin TỰ đối chiếu với khoảng thời gian
        của phiên kiểm kê - hệ thống KHÔNG tự chặn nếu không khớp, vì định
        dạng ngày trong dòng này không cố định (do người dùng tự chọn lúc
        xuất báo cáo), tự parse rồi chặn nhầm còn rủi ro hơn để admin tự
        nhìn bằng mắt.
    """
    file_storage.seek(0)
    try:
        raw = pd.read_excel(
            file_storage, header=None, dtype=object,
            engine='openpyxl', engine_kwargs={'read_only': True},
        )
    except TypeError:
        file_storage.seek(0)
        raw = pd.read_excel(file_storage, header=None, dtype=object)

    period_note = None
    if len(raw) > 1 and raw.iat[1, 0] is not None:
        period_note = str(raw.iat[1, 0]).strip()

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

    qty_col = None
    current_group = ''
    for c in range(raw.shape[1]):
        cell = group_row.iat[c]
        if cell is not None and str(cell).strip() != '' and str(cell).strip().lower() != 'nan':
            current_group = str(cell).strip().lower()
        if 'xuất kho' in current_group and sub_row is not None:
            sub_cell = sub_row.iat[c]
            if sub_cell is not None and 'số lượng' in str(sub_cell).strip().lower():
                qty_col = c
                break
    if qty_col is None:
        raise ValueError('Không tìm thấy cột "Xuất kho - Số lượng" trong file.')

    data_start = header_row + 2
    kho_col, part_col = 0, 1

    data = raw.iloc[data_start:, [kho_col, part_col, qty_col]].copy()
    data.columns = ['kho', 'part_code', 'qty']
    data['kho'] = data['kho'].astype(str).str.strip()
    data['part_code'] = data['part_code'].astype(str).str.strip()
    valid_mask = (
        data['kho'].notna() & data['part_code'].notna()
        & (data['kho'] != '') & (data['kho'].str.lower() != 'none')
        & (data['part_code'] != '') & (data['part_code'].str.lower() != 'none')
    )
    data = data[valid_mask]

    # Tách "KPT NS1" -> prefix="KPT", suffix="NS1" - bỏ qua kho 1-từ như
    # "KHANGCHAMBAN" (không phải cửa hàng thật, không liên quan kiểm kê) và
    # dòng "Tổng cộng" - cả 2 đều không tách được đúng 2 từ nên tự bị loại.
    kho_parts = data['kho'].str.upper().str.split()
    valid_len_mask = kho_parts.str.len() == 2
    data = data[valid_len_mask]
    kho_parts = kho_parts[valid_len_mask]
    data['prefix'] = kho_parts.str[0]
    data['suffix'] = kho_parts.str[1]
    data['suffix'] = data['suffix'].replace(_SUFFIX_ALIAS_TO_STORE)

    allowed_mask = data['prefix'].isin(_INVENTORY_ALLOWED_PREFIXES) & data['suffix'].isin(_INVENTORY_ALLOWED_STORES)
    data = data[allowed_mask]
    data = data.rename(columns={'suffix': 'store_code'})

    data['qty'] = pd.to_numeric(data['qty'], errors='coerce').fillna(0.0)
    data = data[data['qty'] > 0]  # xuất = 0 thì khỏi tạo báo phát sinh rỗng

    if data.empty:
        return [], period_note

    grouped = data.groupby(['part_code', 'store_code'], sort=False)['qty'].sum().reset_index()
    return grouped.to_dict(orient='records'), period_note


@stocktake_bp.route('/stocktake')
def stocktake_page():
    if 'user' not in session or session.get('role') != 'admin':
        return render_template('login.html', error='Chỉ Admin mới được truy cập trang này.')
    # Tiện dịp trang được mở, dọn luôn log quá hạn - khỏi cần cron riêng.
    db = get_db()
    _cleanup_old_logs(db, db.cursor())
    return render_template('stocktake.html', user=session['user'], stores=sorted(STORE_REGIONS.keys()))


@stocktake_bp.route('/api/stocktake/start', methods=['POST'])
def stocktake_start():
    err = _require_admin()
    if err:
        return err

    data = request.json or {}
    store_code = (data.get('store_code') or '').strip().upper()

    db = get_db()
    cursor = db.cursor()

    valid_stores = _valid_store_codes(cursor)
    if not store_code or store_code not in valid_stores:
        cursor.close()
        return jsonify({'error': 'Vui lòng chọn cửa hàng hợp lệ.'}), 400

    cursor.execute(
        "SELECT id FROM stocktake_sessions WHERE store_code = %s AND status IN ('open', 'reopened')",
        (store_code,)
    )
    if cursor.fetchone():
        cursor.close()
        return jsonify({'error': 'Cửa hàng này đang có 1 phiên kiểm kê chưa chốt.'}), 400

    # T0 = thời điểm hiện tại, kèm chụp CỨNG toàn bộ tồn kho hệ thống hiện có
    # của cửa hàng này - đây chính là "tồn sổ sách" dùng để đối chiếu.
    cutoff_time = vn_now()
    cursor.execute(
        "INSERT INTO stocktake_sessions (store_code, cutoff_time, created_by) VALUES (%s, %s, %s) RETURNING id",
        (store_code, cutoff_time, session['user'])
    )
    new_id = cursor.fetchone()['id']

    cursor.execute(
        "SELECT part_code, part_name, unit, quantity FROM inventory_items WHERE store_code = %s",
        (store_code,)
    )
    items = cursor.fetchall()
    if items:
        # Insert gộp toàn bộ trong 1 câu lệnh (execute_values) thay vì
        # executemany (vốn gửi 1 round-trip riêng cho từng dòng tới DB -
        # rất chậm khi cửa hàng có vài nghìn mã hàng trở lên). Cùng dữ
        # liệu, cùng bảng, chỉ khác cách gửi xuống DB nên không đổi hành vi.
        execute_values(
            cursor,
            "INSERT INTO stocktake_snapshot_items (session_id, part_code, part_name, unit, book_quantity) "
            "VALUES %s",
            [(new_id, it['part_code'], it['part_name'], it['unit'], it['quantity']) for it in items]
        )

    db.commit()
    cursor.close()
    return jsonify({'success': True, 'session_id': new_id, 'total_parts': len(items)})


@stocktake_bp.route('/api/stocktake/current', methods=['GET'])
def stocktake_current():
    err = _require_admin()
    if err:
        return err

    store_code = (request.args.get('store_code') or '').strip().upper()
    if not store_code:
        return jsonify({'error': 'Thiếu store_code.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM stocktake_sessions WHERE store_code = %s AND status IN ('open', 'reopened') "
        "ORDER BY id DESC LIMIT 1",
        (store_code,)
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        return jsonify({'session': None})

    cursor.execute("SELECT COUNT(*) AS c FROM stocktake_snapshot_items WHERE session_id = %s", (row['id'],))
    total_parts = cursor.fetchone()['c']
    cursor.execute(
        "SELECT COUNT(DISTINCT part_code) AS c FROM stocktake_counts WHERE session_id = %s", (row['id'],)
    )
    counted_parts = cursor.fetchone()['c']
    cursor.close()

    return jsonify({'session': {
        'id': row['id'],
        'store_code': row['store_code'],
        'cutoff_time': format_vi_datetime(row['cutoff_time']),
        'status': row['status'],
        'created_by': row['created_by'],
        'created_at': format_vi_datetime(row['created_at']),
        'total_parts': total_parts,
        'counted_parts': counted_parts,
    }})


@stocktake_bp.route('/api/stocktake/count', methods=['POST'])
def stocktake_count():
    err = _require_admin()
    if err:
        return err

    data = request.json or {}
    session_id = data.get('session_id')
    part_code = (data.get('part_code') or '').strip()
    quantity = data.get('quantity')
    area_note = (data.get('area_note') or '').strip() or None

    if not session_id or not part_code or quantity is None:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400
    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        return jsonify({'error': 'Số lượng không hợp lệ.'}), 400
    if quantity < 0:
        return jsonify({'error': 'Số lượng không được âm.'}), 400

    db = get_db()
    cursor = db.cursor()
    row = _get_session_or_404(cursor, session_id)
    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiên kiểm kê.'}), 404
    if row['status'] not in ('open', 'reopened'):
        cursor.close()
        return jsonify({'error': 'Phiên kiểm kê đã chốt, không thể nhập thêm số đếm.'}), 400

    cursor.execute(
        "SELECT 1 FROM stocktake_snapshot_items WHERE session_id = %s AND part_code = %s",
        (session_id, part_code)
    )
    if not cursor.fetchone():
        cursor.close()
        return jsonify({'error': f'Mã hàng "{part_code}" không có trong tồn kho hệ thống của cửa hàng này.'}), 400

    cursor.execute(
        "INSERT INTO stocktake_counts (session_id, part_code, counted_quantity, area_note, counted_by) "
        "VALUES (%s, %s, %s, %s, %s)",
        (session_id, part_code, quantity, area_note, session['user'])
    )
    db.commit()
    cursor.close()
    return jsonify({'success': True})


@stocktake_bp.route('/api/stocktake/adjustment', methods=['POST'])
def stocktake_adjustment_create():
    """Nhân viên tự báo 1 lần bán ra/nhập thêm PHÁT SINH SAU KHI ĐÃ ĐẾM 1 mã
    trong phiên đang mở - vì hệ thống không có bảng giao dịch bán hàng để tự
    tính (xem comment ở init_stocktake_tables). Cho báo cả với mã CHƯA đếm
    cũng được (không chặn) - lỡ nhân viên muốn ghi chú trước cũng không sao,
    phần "known_movement" sẽ tự cộng vào bất kể đã đếm hay chưa."""
    err = _require_admin()
    if err:
        return err

    data = request.json or {}
    session_id = data.get('session_id')
    part_code = (data.get('part_code') or '').strip()
    adjustment_type = (data.get('adjustment_type') or '').strip()
    quantity = data.get('quantity')
    note = (data.get('note') or '').strip() or None

    if not session_id or not part_code:
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400
    if adjustment_type not in ('sold', 'received_other'):
        return jsonify({'error': 'Loại phát sinh không hợp lệ.'}), 400
    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        return jsonify({'error': 'Số lượng không hợp lệ.'}), 400
    if quantity <= 0:
        return jsonify({'error': 'Số lượng phải lớn hơn 0.'}), 400

    db = get_db()
    cursor = db.cursor()
    row = _get_session_or_404(cursor, session_id)
    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiên kiểm kê.'}), 404
    if row['status'] not in ('open', 'reopened'):
        cursor.close()
        return jsonify({'error': 'Phiên kiểm kê đã chốt, không thể báo phát sinh.'}), 400

    cursor.execute(
        "SELECT 1 FROM stocktake_snapshot_items WHERE session_id = %s AND part_code = %s",
        (session_id, part_code)
    )
    if not cursor.fetchone():
        cursor.close()
        return jsonify({'error': f'Mã hàng "{part_code}" không có trong tồn kho hệ thống của cửa hàng này.'}), 400

    cursor.execute(
        "INSERT INTO stocktake_manual_adjustments "
        "(session_id, part_code, adjustment_type, quantity, note, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (session_id, part_code, adjustment_type, quantity, note, session['user'])
    )
    db.commit()
    cursor.close()
    return jsonify({'success': True})


@stocktake_bp.route('/api/stocktake/<int:session_id>/import-sales', methods=['POST'])
def stocktake_import_sales(session_id):
    """Import số 'Xuất kho' (bán ra) từ file "Tổng hợp tồn kho" - xuất RIÊNG
    cho đúng khoảng ngày của phiên kiểm kê này (từ lúc bắt đầu tới lúc sắp
    chốt) - cộng dồn thẳng vào "Bán ra" (stocktake_manual_adjustments,
    adjustment_type='sold') CẠNH các lần báo tay của nhân viên, KHÔNG thay
    thế/ghi đè (theo yêu cầu: giữ cả 2, cộng dồn lại).

    Chỉ cho làm khi phiên còn MỞ (giống mọi thao tác báo phát sinh khác) -
    phải import xong rồi mới bấm Chốt, để số vừa import được tính vào Tồn
    Kỳ Vọng lúc chốt."""
    err = _require_admin()
    if err:
        return err

    db = get_db()
    cursor = db.cursor()
    sess = _get_session_or_404(cursor, session_id)
    if not sess:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiên kiểm kê.'}), 404
    if sess['status'] not in ('open', 'reopened'):
        cursor.close()
        return jsonify({'error': 'Phiên kiểm kê đã chốt, không thể import thêm phát sinh.'}), 400

    file = request.files.get('file')
    if not file:
        cursor.close()
        return jsonify({'error': 'Chưa chọn file.'}), 400

    try:
        rows, period_note = _parse_xuat_kho_excel(file)
    except ValueError as e:
        cursor.close()
        return jsonify({'error': str(e)}), 400
    except Exception:
        cursor.close()
        return jsonify({'error': 'Không đọc được file. Kiểm tra lại đúng file "Tổng hợp tồn kho" (.xlsx).'}), 400

    # File là báo cáo TOÀN CHUỖI (nhiều cửa hàng) - chỉ lấy đúng cửa hàng
    # của phiên đang import, các cửa hàng khác trong file không liên quan.
    store_rows = [r for r in rows if r['store_code'] == sess['store_code']]

    # Chỉ nhận mã hàng CÓ trong snapshot của phiên (đúng mã hàng cửa hàng
    # đó đang kiểm tại thời điểm bắt đầu) - mã lạ thì bỏ qua, đếm lại để
    # báo cho admin biết (không chặn cả file vì vài mã lạ).
    cursor.execute(
        "SELECT part_code FROM stocktake_snapshot_items WHERE session_id = %s",
        (session_id,)
    )
    valid_parts = {r['part_code'] for r in cursor.fetchall()}
    accepted = [r for r in store_rows if r['part_code'] in valid_parts]
    skipped = len(store_rows) - len(accepted)

    if accepted:
        note = 'Import từ file Tổng Hợp Tồn Kho (cột Xuất kho)'
        if period_note:
            note += f' - {period_note}'
        execute_values(
            cursor,
            "INSERT INTO stocktake_manual_adjustments "
            "(session_id, part_code, adjustment_type, quantity, note, created_by) VALUES %s",
            [(session_id, r['part_code'], 'sold', r['qty'], note, session['user']) for r in accepted]
        )
        db.commit()
    cursor.close()

    return jsonify({
        'success': True,
        'imported': len(accepted),
        'skipped': skipped,
        'period_note': period_note,
    })


@stocktake_bp.route('/api/stocktake/adjustment/<int:session_id>/<path:part_code>', methods=['GET'])
def stocktake_adjustment_list(session_id, part_code):
    """Trả về lịch sử các lần báo phát sinh của 1 mã trong 1 phiên - dùng để
    hiển thị/quản lý (xoá nếu báo nhầm) trong modal "Báo phát sinh"."""
    err = _require_admin()
    if err:
        return err

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, adjustment_type, quantity, note, created_by, created_at "
        "FROM stocktake_manual_adjustments WHERE session_id = %s AND part_code = %s "
        "ORDER BY created_at DESC",
        (session_id, part_code)
    )
    rows = cursor.fetchall()
    cursor.close()
    return jsonify({'success': True, 'adjustments': [{
        'id': r['id'], 'adjustment_type': r['adjustment_type'], 'quantity': float(r['quantity']),
        'note': r['note'], 'created_by': r['created_by'], 'created_at': format_vi_datetime(r['created_at']),
    } for r in rows]})


@stocktake_bp.route('/api/stocktake/adjustment/<int:adjustment_id>', methods=['DELETE'])
def stocktake_adjustment_delete(adjustment_id):
    """Xoá 1 lần báo phát sinh đã ghi nhầm - chỉ cho phép khi phiên tương
    ứng vẫn đang mở (đã chốt thì không cho sửa số liệu quá khứ nữa)."""
    err = _require_admin()
    if err:
        return err

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT a.id, s.status FROM stocktake_manual_adjustments a "
        "JOIN stocktake_sessions s ON s.id = a.session_id WHERE a.id = %s",
        (adjustment_id,)
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy lần báo phát sinh này.'}), 404
    if row['status'] not in ('open', 'reopened'):
        cursor.close()
        return jsonify({'error': 'Phiên kiểm kê đã chốt, không thể xoá.'}), 400

    cursor.execute("DELETE FROM stocktake_manual_adjustments WHERE id = %s", (adjustment_id,))
    db.commit()
    cursor.close()
    return jsonify({'success': True})


def _fetch_summary_rows(cursor, session_id, store_code, cutoff_time):
    """1 câu truy vấn duy nhất tính đủ: book_quantity, phát sinh đã biết
    (nhận chuyển kho / gửi chuyển kho / báo hư) trong khoảng [cutoff_time,
    lần đếm GẦN NHẤT của từng mã], số đếm cộng dồn, và chênh lệch. Mã nào
    chưa có lần đếm nào thì received/sent/damaged/counted đều là NULL/0 -
    frontend tự hiển thị "Chưa đếm"."""
    cursor.execute('''
        WITH counts AS (
            SELECT part_code, SUM(counted_quantity) AS counted_quantity, MAX(counted_at) AS counted_until
            FROM stocktake_counts WHERE session_id = %(sid)s GROUP BY part_code
        ),
        received AS (
            SELECT ti.part_code, SUM(COALESCE(ti.approved_quantity, ti.quantity)) AS qty
            FROM transfer_items ti
            JOIN transfer_requests tr ON tr.id = ti.request_id
            JOIN counts c ON c.part_code = ti.part_code
            WHERE tr.to_store = %(store)s AND ti.received = TRUE
              AND ti.received_at >= %(cutoff)s AND ti.received_at <= c.counted_until
            GROUP BY ti.part_code
        ),
        sent AS (
            SELECT ti.part_code, SUM(COALESCE(ti.approved_quantity, ti.quantity)) AS qty
            FROM transfer_items ti
            JOIN transfer_requests tr ON tr.id = ti.request_id
            JOIN counts c ON c.part_code = ti.part_code
            WHERE tr.from_store = %(store)s AND tr.prepared = TRUE
              AND tr.prepared_at >= %(cutoff)s AND tr.prepared_at <= c.counted_until
            GROUP BY ti.part_code
        ),
        damaged AS (
            SELECT d.part_code, SUM(d.quantity) AS qty
            FROM damaged_items d
            JOIN counts c ON c.part_code = d.part_code
            WHERE d.store_code = %(store)s
              AND d.created_at >= %(cutoff)s AND d.created_at <= c.counted_until
            GROUP BY d.part_code
        ),
        manual_sold AS (
            SELECT part_code, SUM(quantity) AS qty FROM stocktake_manual_adjustments
            WHERE session_id = %(sid)s AND adjustment_type = 'sold' GROUP BY part_code
        ),
        manual_received AS (
            SELECT part_code, SUM(quantity) AS qty FROM stocktake_manual_adjustments
            WHERE session_id = %(sid)s AND adjustment_type = 'received_other' GROUP BY part_code
        ),
        areas AS (
            -- Gộp TẤT CẢ khu vực đã ghi nhận đếm cho 1 mã (có thể đếm ở
            -- nhiều khu vực khác nhau) thành 1 chuỗi hiển thị, bỏ khu vực
            -- trống/NULL và không lặp lại tên khu vực giống nhau.
            SELECT part_code, STRING_AGG(DISTINCT NULLIF(TRIM(area_note), ''), ', ') AS area_list
            FROM stocktake_counts WHERE session_id = %(sid)s GROUP BY part_code
        )
        SELECT s.part_code, s.part_name, s.unit, s.book_quantity,
               COALESCE(r.qty, 0) AS received_qty,
               COALESCE(se.qty, 0) AS sent_qty,
               COALESCE(dm.qty, 0) AS damaged_qty,
               COALESCE(ms.qty, 0) AS manual_sold_qty,
               COALESCE(mr.qty, 0) AS manual_received_qty,
               c.counted_quantity, c.counted_until, ar.area_list
        FROM stocktake_snapshot_items s
        LEFT JOIN counts c ON c.part_code = s.part_code
        LEFT JOIN received r ON r.part_code = s.part_code
        LEFT JOIN sent se ON se.part_code = s.part_code
        LEFT JOIN damaged dm ON dm.part_code = s.part_code
        LEFT JOIN manual_sold ms ON ms.part_code = s.part_code
        LEFT JOIN manual_received mr ON mr.part_code = s.part_code
        LEFT JOIN areas ar ON ar.part_code = s.part_code
        WHERE s.session_id = %(sid)s
        ORDER BY s.part_code
    ''', {'sid': session_id, 'store': store_code, 'cutoff': cutoff_time})
    return cursor.fetchall()


def _build_summary(rows):
    """Ghép known_movement/expected/diff/trạng thái từ dữ liệu thô của
    _fetch_summary_rows() - tách riêng để dùng chung cho cả API summary lẫn
    lúc chốt phiên (close), khỏi lặp code."""
    out = []
    for r in rows:
        book = float(r['book_quantity'] or 0)
        manual_qty = float(r['manual_received_qty'] or 0) - float(r['manual_sold_qty'] or 0)
        has_manual = float(r['manual_received_qty'] or 0) > 0 or float(r['manual_sold_qty'] or 0) > 0
        counted = r['counted_quantity']
        areas = r['area_list'] if 'area_list' in r.keys() else None
        if counted is None:
            # Chưa đếm mã này - theo yêu cầu: khi CHỐT sẽ coi như tồn = 0.
            # manual_qty vẫn được cộng vào known_movement dù chưa đếm - lỡ
            # nhân viên báo trước cũng không sao, không ảnh hưởng gì vì mã
            # này vẫn đang ở trạng thái "chưa đếm" bất kể có báo hay không.
            out.append({
                'part_code': r['part_code'], 'part_name': r['part_name'], 'unit': r['unit'],
                'book_quantity': book, 'known_movement_qty': manual_qty, 'expected_quantity': book + manual_qty,
                'counted_quantity': None, 'diff_quantity': None, 'has_manual_adjustment': has_manual,
                'areas': areas, 'counted': False, 'status': 'not_counted',
            })
            continue
        movement = float(r['received_qty'] or 0) - float(r['sent_qty'] or 0) - float(r['damaged_qty'] or 0) + manual_qty
        expected = book + movement
        counted = float(counted)
        diff = counted - expected
        # Trạng thái theo đúng 3 mức người dùng cần, dựa thẳng vào dấu của
        # chênh lệch thực tế - không còn khái niệm "trong ngưỡng dung sai"
        # (dễ gây hiểu lầm là khớp dù còn lệch): "Đủ" khi lệch đúng bằng 0,
        # "Dư" khi đếm được NHIỀU hơn kỳ vọng, "Thiếu" khi đếm được ÍT hơn.
        if diff == 0:
            status = 'matched'
        elif diff > 0:
            status = 'surplus'
        else:
            status = 'shortage'
        out.append({
            'part_code': r['part_code'], 'part_name': r['part_name'], 'unit': r['unit'],
            'book_quantity': book, 'known_movement_qty': movement, 'expected_quantity': expected,
            'counted_quantity': counted, 'diff_quantity': diff, 'has_manual_adjustment': has_manual,
            'areas': areas, 'counted': True, 'status': status,
        })
    return out


@stocktake_bp.route('/api/stocktake/<int:session_id>/summary', methods=['GET'])
def stocktake_summary(session_id):
    err = _require_admin()
    if err:
        return err

    db = get_db()
    cursor = db.cursor()
    sess = _get_session_or_404(cursor, session_id)
    if not sess:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiên kiểm kê.'}), 404

    rows = _fetch_summary_rows(cursor, session_id, sess['store_code'], sess['cutoff_time'])
    cursor.close()

    summary = _build_summary(rows)
    return jsonify({
        'success': True,
        'session': {
            'id': sess['id'], 'store_code': sess['store_code'], 'status': sess['status'],
            'cutoff_time': format_vi_datetime(sess['cutoff_time']),
        },
        'items': summary,
        'total_parts': len(summary),
        'counted_parts': sum(1 for x in summary if x['counted']),
        'surplus_count': sum(1 for x in summary if x['status'] == 'surplus'),
        'shortage_count': sum(1 for x in summary if x['status'] == 'shortage'),
    })


@stocktake_bp.route('/api/stocktake/<int:session_id>/log', methods=['GET'])
def stocktake_log(session_id):
    """Nhật ký TỪNG LẦN đếm (không gộp) của 1 phiên - trả lời khiếu nại
    "chưa có log hiển thị lịch sử các mã đã kiểm". Đọc thẳng từ bảng
    stocktake_counts (mỗi lần đếm là 1 dòng riêng, không bị ghi đè), nên
    dữ liệu này KHÔNG mất kể cả sau khi phiên đã chốt - khác với bảng đối
    chiếu (summary) vốn chỉ hiện số đã CỘNG DỒN theo mã."""
    err = _require_admin()
    if err:
        return err

    db = get_db()
    cursor = db.cursor()
    sess = _get_session_or_404(cursor, session_id)
    if not sess:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiên kiểm kê.'}), 404
    _cleanup_old_logs(db, cursor)

    cursor.execute('''
        SELECT c.id, c.part_code, s.part_name, c.counted_quantity, c.area_note,
               c.counted_by, c.counted_at
        FROM stocktake_counts c
        LEFT JOIN stocktake_snapshot_items s
               ON s.session_id = c.session_id AND s.part_code = c.part_code
        WHERE c.session_id = %s
        ORDER BY c.counted_at DESC, c.id DESC
    ''', (session_id,))
    rows = cursor.fetchall()
    cursor.close()

    return jsonify({'success': True, 'log': [{
        'id': r['id'], 'part_code': r['part_code'], 'part_name': r['part_name'],
        'counted_quantity': float(r['counted_quantity']), 'area_note': r['area_note'] or '',
        'counted_by': r['counted_by'], 'counted_at': format_vi_datetime(r['counted_at']),
    } for r in rows]})


@stocktake_bp.route('/api/stocktake/<int:session_id>/close', methods=['POST'])
def stocktake_close(session_id):
    err = _require_admin()
    if err:
        return err

    data = request.json or {}
    note = (data.get('note') or '').strip() or None

    db = get_db()
    cursor = db.cursor()
    sess = _get_session_or_404(cursor, session_id)
    if not sess:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiên kiểm kê.'}), 404
    if sess['status'] not in ('open', 'reopened'):
        cursor.close()
        return jsonify({'error': 'Phiên kiểm kê này đã chốt.'}), 400

    rows = _fetch_summary_rows(cursor, session_id, sess['store_code'], sess['cutoff_time'])
    summary = _build_summary(rows)

    now = vn_now()
    adjustment_rows = []
    for it in summary:
        # Đếm thiếu -> tồn = 0 (theo yêu cầu), không tính phát sinh vì
        # không có mốc thời gian đếm để quy đổi.
        counted = it['counted_quantity'] if it['counted'] else 0
        movement = it['known_movement_qty'] if it['counted'] else 0
        expected = it['expected_quantity'] if it['counted'] else it['book_quantity']
        diff = it['diff_quantity'] if it['counted'] else (counted - expected)
        adjustment_rows.append((
            session_id, it['part_code'], it['part_name'], it['book_quantity'], movement,
            expected, counted, diff
        ))

        # LƯU Ý: theo yêu cầu, chốt phiên KHÔNG tự động cập nhật tồn kho hệ
        # thống (inventory_items) nữa - chỉ lưu kết quả đối chiếu vào
        # stocktake_adjustments và cho xuất Excel. Nếu muốn áp số liệu kiểm
        # kê vào tồn hệ thống, admin phải tự làm việc đó bằng cách khác
        # (vd upload lại file tồn kho), không phải qua thao tác chốt này.

    if adjustment_rows:
        # Gộp toàn bộ vào 1 câu lệnh (execute_values) thay vì 1 INSERT
        # riêng cho từng mã hàng trong vòng lặp - đây chính là lý do nút
        # "Chốt" bị chậm khi cửa hàng có vài trăm/nghìn mã: mỗi INSERT
        # riêng là 1 round-trip mạng tới DB, cộng dồn lại rất lâu. Cùng dữ
        # liệu, cùng bảng, chỉ khác cách gửi xuống DB nên không đổi hành vi.
        execute_values(
            cursor,
            '''
            INSERT INTO stocktake_adjustments
                (session_id, part_code, part_name, book_quantity, known_movement_qty,
                 expected_quantity, counted_quantity, diff_quantity)
            VALUES %s
            ON CONFLICT (session_id, part_code) DO UPDATE SET
                book_quantity = EXCLUDED.book_quantity,
                known_movement_qty = EXCLUDED.known_movement_qty,
                expected_quantity = EXCLUDED.expected_quantity,
                counted_quantity = EXCLUDED.counted_quantity,
                diff_quantity = EXCLUDED.diff_quantity
            ''',
            adjustment_rows
        )

    cursor.execute(
        "UPDATE stocktake_sessions SET status = 'closed', closed_by = %s, closed_at = %s, note = %s WHERE id = %s",
        (session['user'], now, note, session_id)
    )
    db.commit()
    cursor.close()
    return jsonify({'success': True})


@stocktake_bp.route('/api/stocktake/<int:session_id>/reopen', methods=['POST'])
def stocktake_reopen(session_id):
    err = _require_admin()
    if err:
        return err

    data = request.json or {}
    reason = (data.get('reason') or '').strip()
    if not reason:
        return jsonify({'error': 'Vui lòng nhập lý do mở lại phiên kiểm kê.'}), 400

    db = get_db()
    cursor = db.cursor()
    sess = _get_session_or_404(cursor, session_id)
    if not sess:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiên kiểm kê.'}), 404
    if sess['status'] != 'closed':
        cursor.close()
        return jsonify({'error': 'Chỉ mở lại được phiên đã chốt.'}), 400

    deadline = sess['closed_at'] + timedelta(hours=REOPEN_WINDOW_HOURS)
    if vn_now() > deadline:
        cursor.close()
        return jsonify({
            'error': f'Đã quá {REOPEN_WINDOW_HOURS} giờ kể từ lúc chốt, không thể mở lại phiên này nữa.'
        }), 400

    cursor.execute(
        "UPDATE stocktake_sessions SET status = 'reopened' WHERE id = %s", (session_id,)
    )
    cursor.execute(
        "INSERT INTO stocktake_reopen_log (session_id, reopened_by, reason) VALUES (%s, %s, %s)",
        (session_id, session['user'], reason)
    )
    db.commit()
    cursor.close()
    return jsonify({'success': True})


@stocktake_bp.route('/api/stocktake/store/<store_code>/purge', methods=['GET'])
def stocktake_purge_preview(store_code):
    """Xem trước sẽ xoá bao nhiêu trước khi admin bấm xoá thật - tránh
    xoá nhầm mà không biết mình sắp mất gì."""
    err = _require_admin()
    if err:
        return err
    store_code = store_code.strip().upper()

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE status != 'closed') AS open_count, "
        "MIN(created_at) AS oldest, MAX(created_at) AS newest "
        "FROM stocktake_sessions WHERE store_code = %s",
        (store_code,)
    )
    row = cursor.fetchone()
    cursor.close()
    return jsonify({
        'success': True,
        'store_code': store_code,
        'session_count': row['total'] or 0,
        'open_session_count': row['open_count'] or 0,
        'oldest': row['oldest'].strftime('%d/%m/%Y %H:%M') if row['oldest'] else None,
        'newest': row['newest'].strftime('%d/%m/%Y %H:%M') if row['newest'] else None,
    })


@stocktake_bp.route('/api/stocktake/store/<store_code>/purge', methods=['DELETE'])
def stocktake_purge(store_code):
    """XOÁ VĨNH VIỄN toàn bộ dữ liệu kiểm kê (mọi phiên, kể cả nhật ký
    đếm/biên bản kết quả/lịch sử mở lại đi kèm) của 1 cửa hàng - dùng khi
    cần giải phóng bớt dữ liệu cũ cho nhẹ DB. Xoá ở stocktake_sessions là
    đủ, các bảng con đều có ON DELETE CASCADE nên tự dọn theo.

    An toàn:
    - Từ chối nếu cửa hàng đang có phiên CHƯA CHỐT (tránh mất dữ liệu
      đang đếm dở, chưa kịp chốt/xuất Excel) - phải chốt hết trước.
    - Bắt gõ đúng store_code để xác nhận, vì thao tác này không thể
      hoàn tác (không giống chốt/mở lại vẫn còn sửa được).
    """
    err = _require_admin()
    if err:
        return err
    store_code = store_code.strip().upper()
    data = request.get_json(silent=True) or {}
    confirm_code = (data.get('confirm_code') or '').strip().upper()
    if confirm_code != store_code:
        return jsonify({'error': 'Mã cửa hàng xác nhận không khớp.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS c FROM stocktake_sessions WHERE store_code = %s AND status != 'closed'",
        (store_code,)
    )
    open_count = cursor.fetchone()['c']
    if open_count:
        cursor.close()
        return jsonify({
            'error': f'Cửa hàng này đang có {open_count} phiên kiểm kê CHƯA CHỐT. '
                     'Phải chốt hết mới được xoá dữ liệu.'
        }), 400

    cursor.execute("DELETE FROM stocktake_sessions WHERE store_code = %s", (store_code,))
    deleted = cursor.rowcount
    db.commit()
    cursor.close()
    return jsonify({'success': True, 'deleted_sessions': deleted})


@stocktake_bp.route('/api/stocktake/history', methods=['GET'])
def stocktake_history():
    err = _require_admin()
    if err:
        return err

    store_code = (request.args.get('store_code') or '').strip().upper()
    db = get_db()
    cursor = db.cursor()
    _cleanup_old_logs(db, cursor)
    if store_code:
        cursor.execute(
            "SELECT * FROM stocktake_sessions WHERE store_code = %s ORDER BY id DESC LIMIT 100", (store_code,)
        )
    else:
        cursor.execute("SELECT * FROM stocktake_sessions ORDER BY id DESC LIMIT 100")
    rows = cursor.fetchall()
    cursor.close()

    return jsonify({'success': True, 'sessions': [{
        'id': r['id'], 'store_code': r['store_code'], 'status': r['status'],
        'cutoff_time': format_vi_datetime(r['cutoff_time']),
        'created_by': r['created_by'], 'created_at': format_vi_datetime(r['created_at']),
        'closed_by': r['closed_by'],
        'closed_at': format_vi_datetime(r['closed_at']) if r['closed_at'] else None,
    } for r in rows]})


@stocktake_bp.route('/api/stocktake/<int:session_id>/export', methods=['GET'])
def stocktake_export(session_id):
    err = _require_admin()
    if err:
        return err

    db = get_db()
    cursor = db.cursor()
    sess = _get_session_or_404(cursor, session_id)
    if not sess:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy phiên kiểm kê.'}), 404
    _cleanup_old_logs(db, cursor)

    # Nhật ký TỪNG LẦN đếm - đọc trước, dùng chung để: (1) làm sheet riêng
    # trong file Excel (log này đọc thẳng từ stocktake_counts, nên nếu xuất
    # trong vòng LOG_RETENTION_DAYS ngày kể từ lúc chốt thì còn đầy đủ; xuất
    # trễ hơn thì sheet này sẽ trống vì đã tới hạn tự xoá), và (2) suy ra
    # "Khu Vực Đã Đếm" cho từng mã ở sheet chính.
    cursor.execute('''
        SELECT c.id, c.part_code, s.part_name, c.counted_quantity, c.area_note,
               c.counted_by, c.counted_at
        FROM stocktake_counts c
        LEFT JOIN stocktake_snapshot_items s
               ON s.session_id = c.session_id AND s.part_code = c.part_code
        WHERE c.session_id = %s
        ORDER BY c.counted_at ASC, c.id ASC
    ''', (session_id,))
    log_rows = cursor.fetchall()

    area_map = {}
    for lr in log_rows:
        note = (lr['area_note'] or '').strip()
        if not note:
            continue
        existing = area_map.setdefault(lr['part_code'], [])
        if note not in existing:
            existing.append(note)

    if sess['status'] == 'closed':
        cursor.execute(
            "SELECT * FROM stocktake_adjustments WHERE session_id = %s ORDER BY part_code", (session_id,)
        )
        rows = cursor.fetchall()
        cursor.close()
        out_rows = [{
            'Mã Hàng': r['part_code'], 'Tên Hàng': r['part_name'],
            'Tồn Sổ Sách': r['book_quantity'], 'Phát Sinh Đã Biết': r['known_movement_qty'],
            'Tồn Kỳ Vọng': r['expected_quantity'], 'Số Đếm Thực Tế': r['counted_quantity'],
            'Chênh Lệch': r['diff_quantity'],
            'Vị Trí Đã Đếm': ', '.join(area_map.get(r['part_code'], [])),
        } for r in rows]
    else:
        rows = _fetch_summary_rows(cursor, session_id, sess['store_code'], sess['cutoff_time'])
        cursor.close()
        summary = _build_summary(rows)
        status_label = {
            'matched': 'Đủ', 'surplus': 'Dư',
            'shortage': 'Thiếu', 'not_counted': 'Chưa đếm',
        }
        out_rows = [{
            'Mã Hàng': it['part_code'], 'Tên Hàng': it['part_name'],
            'Tồn Sổ Sách': it['book_quantity'], 'Phát Sinh Đã Biết': it['known_movement_qty'],
            'Tồn Kỳ Vọng': it['expected_quantity'],
            'Số Đếm Thực Tế': it['counted_quantity'] if it['counted'] else 'Chưa đếm',
            'Chênh Lệch': it['diff_quantity'] if it['counted'] else '',
            'Trạng Thái': status_label.get(it['status'], it['status']),
            'Vị Trí Đã Đếm': ', '.join(area_map.get(it['part_code'], [])),
        } for it in summary]

    log_out_rows = [{
        'Thời Gian': format_vi_datetime(lr['counted_at']), 'Mã Hàng': lr['part_code'],
        'Tên Hàng': lr['part_name'], 'Số Lượng Đếm': float(lr['counted_quantity']),
        'Vị Trí': lr['area_note'] or '', 'Người Đếm': lr['counted_by'] or '',
    } for lr in log_rows]

    df = pd.DataFrame(out_rows)
    df_log = pd.DataFrame(log_out_rows)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Kiem Ke', index=False)
        ws = writer.sheets['Kiem Ke']
        for col_idx, col_name in enumerate(df.columns, start=1):
            max_len = max([len(str(col_name))] + [len(str(v)) for v in df[col_name].tolist()] or [0])
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max_len + 3, 45)

        # Sheet riêng cho log từng lần đếm - giữ nguyên TOÀN BỘ lịch sử,
        # không gộp theo mã, để không mất dấu vết ai đếm lúc nào ở đâu.
        df_log.to_excel(writer, sheet_name='Nhat Ky Dem', index=False)
        ws_log = writer.sheets['Nhat Ky Dem']
        for col_idx, col_name in enumerate(df_log.columns, start=1):
            max_len = max([len(str(col_name))] + [len(str(v)) for v in df_log[col_name].tolist()] or [0])
            ws_log.column_dimensions[ws_log.cell(row=1, column=col_idx).column_letter].width = min(max_len + 3, 45)
    buffer.seek(0)

    filename = f"kiem_ke_{sess['store_code']}_{session_id}.xlsx"
    return send_file(buffer, as_attachment=True, download_name=filename,
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')