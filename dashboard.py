# -*- coding: utf-8 -*-
"""
Module GỢI Ý NHẬP HÀNG + CẢNH BÁO HẾT HÀNG + DASHBOARD TỔNG QUAN CHO ADMIN.
Tách riêng khỏi app.py thành 1 Blueprint độc lập, giống hệt cách
stocktake.py/price_adjustment.py đang làm (xem hướng dẫn ở đầu 2 file đó).

CÁCH MÓC NỐI VÀO app.py (chỉ cần thêm đúng 3 chỗ, xem comment "MÓC NỐI"):
    1. Ngay TRƯỚC dòng gọi _init_db_with_retry() ở cuối app.py (cùng chỗ
       với stocktake_bp/price_adjustment_bp - PHẢI đặt sau khi get_db,
       vn_now, _valid_store_codes, create_notification, ADMIN_NOTIF_STORE_CODE,
       _get_app_setting, _set_app_setting, _compute_sales_frequency_rows đã
       được định nghĩa xong ở phía trên):

           from dashboard import dashboard_bp, init_dashboard_tables
           app.register_blueprint(dashboard_bp)

    2. Bên trong init_db(), ngay cạnh init_stocktake_tables(cursor) /
       init_price_adjustment_tables(cursor):

           init_dashboard_tables(cursor)

    3. Trong upload_inventory() và import_sales_export() (app.py), NGAY SAU
       khi db.commit() dữ liệu tồn kho/xuất bán mới - gọi để cảnh báo hết
       hàng được cập nhật ngay, không phải đợi tới lượt kiểm tra nền hằng
       ngày:

           try:
               from dashboard import check_and_notify_low_stock
               check_and_notify_low_stock(cursor)
               db.commit()
           except Exception:
               traceback.print_exc()

Đặt file này cùng cấp với app.py. KHÔNG cần thêm bảng nào trên CSDL
Supabase riêng của orders.py - phần đọc số liệu B0 (đơn đặt hàng khách) cho
dashboard tổng quan CHỈ ĐỌC (không ghi), qua get_orders_db() nhập lười
(import trong hàm) để tránh phụ thuộc thứ tự import với orders.py.
"""
import math
import threading
import time
import traceback

from flask import Blueprint, request, jsonify, session

# Import lại đúng những gì app.py đã có sẵn - KHÔNG định nghĩa lại (xem lý do
# trong stocktake.py). Đây là import "vòng" nhưng an toàn vì app.py chỉ
# `from dashboard import ...` SAU KHI toàn bộ các tên này đã định nghĩa xong.
from app import (
    app,
    get_db,
    _valid_store_codes,
    _get_app_setting,
    _set_app_setting,
    _compute_sales_frequency_rows,
    _compute_sales_frequency_rows_cached,
    create_notification,
    ADMIN_NOTIF_STORE_CODE,
)

dashboard_bp = Blueprint('dashboard', __name__)

# Ngưỡng mặc định (tính bằng THÁNG tồn kho còn lại) để 1 mã TX (bán nhanh)
# được coi là "sắp hết hàng" - dùng CHUNG cho cả gợi ý nhập hàng (lọc ra mã
# cần đặt) lẫn cảnh báo tự động. Admin chỉnh được qua /api/admin/low-stock-settings,
# lưu trong app_settings (bảng key-value đã có sẵn) - không cần thêm cột/bảng
# cấu hình riêng.
DEFAULT_LOW_STOCK_MIN_MONTHS = 1.0
_LOW_STOCK_SETTING_KEY = 'low_stock_min_months'

# Khoá advisory riêng cho job nền kiểm tra hết hàng (khác mã của job dọn dẹp
# phiếu chuyển kho đang có trong app.py) - đảm bảo chỉ 1 worker chạy job này
# tại 1 thời điểm nếu chạy nhiều worker (gunicorn -w N).
_LOW_STOCK_LOCK_KEY = 918273646


# ----------------------------------------------------------------------------
# KHỞI TẠO BẢNG
# ----------------------------------------------------------------------------

def init_dashboard_tables(cursor):
    """Gọi 1 lần trong init_db() của app.py. Bảng low_stock_alerts lưu các
    mã hàng ĐANG được coi là "sắp hết hàng" (dưới ngưỡng) tại từng cửa hàng -
    dùng để so sánh mỗi lần kiểm tra: mã MỚI rơi vào danh sách này mới tạo
    thông báo (tránh gửi lặp lại thông báo mỗi ngày cho cùng 1 mã chưa được
    nhập thêm); mã không còn dưới ngưỡng nữa (đã được nhập/luân chuyển thêm)
    sẽ tự bị xoá khỏi bảng, không cần thông báo gì thêm."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS low_stock_alerts (
            id SERIAL PRIMARY KEY,
            store_code VARCHAR(20) NOT NULL,
            part_code VARCHAR(100) NOT NULL,
            part_name TEXT,
            months_of_stock NUMERIC,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (store_code, part_code)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_low_stock_alerts_store ON low_stock_alerts(store_code)')


# ----------------------------------------------------------------------------
# CẤU HÌNH NGƯỠNG CẢNH BÁO (lưu trong app_settings đã có sẵn)
# ----------------------------------------------------------------------------

def get_low_stock_min_months(cursor):
    raw = _get_app_setting(cursor, _LOW_STOCK_SETTING_KEY)
    if not raw:
        return DEFAULT_LOW_STOCK_MIN_MONTHS
    try:
        val = float(raw)
        return val if val > 0 else DEFAULT_LOW_STOCK_MIN_MONTHS
    except (TypeError, ValueError):
        return DEFAULT_LOW_STOCK_MIN_MONTHS


# ----------------------------------------------------------------------------
# 1. GỢI Ý NHẬP HÀNG TỰ ĐỘNG (dựa trên số liệu TX/TB/CB đã có sẵn)
# ----------------------------------------------------------------------------

# Mã hàng bắt đầu bằng tiền tố này (Nhóm khung xe) KHÔNG được đưa vào gợi ý
# nhập hàng tự động, dù đang được phân loại TX/TB - theo yêu cầu người dùng
# (khung xe không đặt theo kiểu "cứ thiếu là nhập thêm" như phụ tùng thường).
REORDER_EXCLUDED_PART_PREFIX = '50100'

# Các nhóm phân loại được đưa vào gợi ý nhập hàng tự động - TX (bán nhanh) VÀ
# TB (bán trung bình). KHÔNG gồm CB (chậm bán) vì tồn CB vốn đã dư, gợi ý nhập
# thêm không có ý nghĩa.
REORDER_INCLUDED_GROUPS = ('TX', 'TB')


def _load_order_lock_map(cursor):
    """Trả về {part_code: {'replacement_code': ..., 'is_locked': ...}} cho
    TOÀN BỘ mã đang bị khoá đặt hàng (order_lock_items, import ở
    /api/admin/import-order-lock) - dùng để tự động thay mã khi tính gợi ý
    nhập hàng (xem _apply_order_lock_substitution). Bảng này nhỏ (1 dòng/mã
    hàng, không nhân theo cửa hàng) nên đọc trọn 1 lần rồi dùng lại cho mọi
    cửa hàng trong 1 lượt tính, thay vì query lặp lại nhiều lần."""
    cursor.execute('SELECT part_code, is_locked, replacement_code FROM order_lock_items WHERE is_locked = TRUE')
    return {r['part_code']: r for r in cursor.fetchall()}


def _apply_order_lock_substitution(suggestions, lock_map):
    """Mã đang bị "Khoá đặt hàng" (Block for Order = Y) không đặt trực tiếp
    được nữa - nếu có mã thay thế (Superseeded Part), tự động đổi sang mã đó
    trong gợi ý (số lượng giữ nguyên, tính theo nhu cầu bán thực tế của mã
    gốc). Nếu bị khoá mà KHÔNG có mã thay thế, vẫn giữ lại dòng gợi ý (để
    không giấu nhu cầu thực tế) nhưng đánh dấu blocked_no_replacement=True
    để FE cảnh báo, không tự ý gộp/xoá.

    Sau khi đổi mã, nhiều dòng gốc có thể trùng ra CÙNG 1 mã thay thế (hoặc
    trùng với 1 mã vốn đã có sẵn trong danh sách) - gộp lại bằng cách CỘNG
    dồn suggested_qty, giữ months_of_stock nhỏ nhất (cấp bách nhất) để sắp
    xếp đúng, và liệt kê lại các mã gốc đã gộp vào 'superseded_from'."""
    merged = {}
    for s in suggestions:
        lock = lock_map.get(s['part_code'])
        key_store = s.get('store_code')
        final_code = s['part_code']
        original_code = None
        blocked_no_replacement = False

        if lock:
            replacement = lock.get('replacement_code')
            if replacement:
                original_code = s['part_code']
                final_code = replacement
            else:
                blocked_no_replacement = True

        key = (key_store, final_code)
        if key not in merged:
            row = dict(s)
            row['part_code'] = final_code
            row['blocked_no_replacement'] = blocked_no_replacement
            row['superseded_from'] = [original_code] if original_code else []
            merged[key] = row
        else:
            row = merged[key]
            row['suggested_qty'] += s['suggested_qty']
            row['qty_on_hand'] = (row.get('qty_on_hand') or 0) + (s.get('qty_on_hand') or 0)
            row['avg_month'] = round((row.get('avg_month') or 0) + (s.get('avg_month') or 0), 2)
            row['months_of_stock'] = min(row['months_of_stock'], s['months_of_stock'])
            row['blocked_no_replacement'] = row['blocked_no_replacement'] or blocked_no_replacement
            if original_code:
                row['superseded_from'].append(original_code)
            elif not lock:
                # Dòng thứ 2 trở đi không bị khoá cũng trùng mã (vd chính mã
                # thay thế đó cũng tự nằm trong nhóm TX/TB) - giữ tên hàng
                # gốc, không cần thêm vào superseded_from.
                pass

    return list(merged.values())


def compute_reorder_suggestions(cursor, store_code, buffer_months):
    """Trả về (suggestions, period_months). Xét các mã đang phân loại TX
    (bán nhanh) hoặc TB (bán trung bình) - KHÔNG gồm mã khung xe (tiền tố
    50100) - và gợi ý số lượng cần đặt thêm để đủ bán trong buffer_months
    tháng kể từ bây giờ (dựa trên TB bán/tháng đã tính sẵn ở
    classify_sales_frequency). Mã chỉ được đưa vào danh sách nếu tồn hiện
    tại THẤP HƠN mức mục tiêu đó (suggested_qty > 0) - tự nhiên phù hợp cho
    cả 2 nhóm: TX thường xuyên lọt vào vì tồn vốn chỉ đủ dùng ngắn hạn, còn
    TB chỉ lọt vào khi tồn thực sự thấp so với buffer_months đã chọn.

    QUAN TRỌNG: tần suất bán/tồn LUÔN được tính RIÊNG cho TỪNG CỬA HÀNG
    (giống hệt cách check_and_notify_low_stock đang làm), KHÔNG gộp số liệu
    bán của mọi cửa hàng lại rồi tính 1 lần - vì 1 mã có thể bán chạy (TX) ở
    kho này nhưng lại chậm bán (CB) ở kho khác, gộp chung sẽ ra gợi ý sai
    thực tế cho từng nơi. Nếu store_code=None (xem "toàn hệ thống"), lặp
    qua TỪNG cửa hàng hợp lệ và tính riêng, mỗi dòng kết quả có kèm
    'store_code' để phân biệt; nếu store_code cụ thể, chỉ tính đúng 1 cửa
    hàng đó (mỗi dòng vẫn có 'store_code' = store_code cho nhất quán)."""
    stores = [store_code] if store_code else sorted(_valid_store_codes(cursor))
    lock_map = _load_order_lock_map(cursor)

    all_suggestions = []
    period_months = 3
    for sc in stores:
        rows, period_months = _compute_sales_frequency_rows_cached(cursor, sc)
        for r in rows:
            if r['group'] not in REORDER_INCLUDED_GROUPS:
                continue
            if r['part_code'].upper().startswith(REORDER_EXCLUDED_PART_PREFIX):
                continue
            mos = r['months_of_stock']
            if mos is None:
                continue
            target_qty = (r['avg_month'] or 0) * buffer_months
            suggested_qty = target_qty - (r['qty_on_hand'] or 0)
            suggested_qty = math.ceil(suggested_qty) if suggested_qty > 0 else 0
            if suggested_qty <= 0:
                continue
            all_suggestions.append({
                'part_code': r['part_code'],
                'part_name': r['part_name'],
                'unit': r['unit'],
                'store_code': sc,
                'qty_on_hand': r['qty_on_hand'],
                'avg_month': r['avg_month'],
                'months_of_stock': mos,
                'group': r['group'],
                'suggested_qty': suggested_qty,
            })

    suggestions = _apply_order_lock_substitution(all_suggestions, lock_map)

    # Ưu tiên hiển thị mã SẮP HẾT NHẤT (số tháng tồn còn lại thấp nhất) lên đầu.
    suggestions.sort(key=lambda x: x['months_of_stock'])
    return suggestions, period_months


def _resolve_reorder_store_filter():
    """Admin được chọn xem theo bất kỳ kho nào (hoặc để trống = toàn hệ
    thống); tài khoản cửa hàng CHỈ được xem đúng kho của mình - bỏ qua/ghi
    đè tham số ?store= nếu có, không cho xem chéo dữ liệu cửa hàng khác."""
    role = session.get('role')
    if role == 'admin':
        return (request.args.get('store') or '').strip().upper() or None
    return session.get('store_code')


@dashboard_bp.route('/api/admin/reorder-suggestions', methods=['GET'])
def reorder_suggestions():
    """?store=NS1 (chỉ admin được chọn; bỏ trống = toàn hệ thống) &buffer_months=2 (mặc định 1).
    Tài khoản cửa hàng xem được, nhưng luôn bị khoá đúng kho của mình."""
    if 'user' not in session or session['role'] not in ('admin', 'store'):
        return jsonify({'error': 'Forbidden'}), 403

    store_code = _resolve_reorder_store_filter()
    try:
        buffer_months = float(request.args.get('buffer_months') or 1)
    except ValueError:
        buffer_months = 1.0
    buffer_months = max(0.5, min(12.0, buffer_months))

    db = get_db()
    cursor = db.cursor()
    suggestions, period_months = compute_reorder_suggestions(cursor, store_code, buffer_months)
    cursor.close()

    return jsonify({
        'success': True,
        'data': suggestions,
        'total': len(suggestions),
        'buffer_months': buffer_months,
        'period_months': period_months,
        'store': store_code,
    })


@dashboard_bp.route('/api/admin/reorder-suggestions/export', methods=['GET'])
def reorder_suggestions_export():
    if 'user' not in session or session['role'] not in ('admin', 'store'):
        return jsonify({'error': 'Forbidden'}), 403

    import io
    import pandas as pd
    from flask import send_file

    store_code = _resolve_reorder_store_filter()
    try:
        buffer_months = float(request.args.get('buffer_months') or 1)
    except ValueError:
        buffer_months = 1.0
    buffer_months = max(0.5, min(12.0, buffer_months))

    db = get_db()
    cursor = db.cursor()
    suggestions, period_months = compute_reorder_suggestions(cursor, store_code, buffer_months)
    cursor.close()

    df = pd.DataFrame([{
        'Kho/Cửa hàng': r.get('store_code') or '',
        'Mã hàng': r['part_code'],
        'Tên hàng': r['part_name'],
        'ĐVT': r['unit'],
        'Phân loại': r['group'],
        'Tồn hiện tại': round(r['qty_on_hand'] or 0),
        'TB bán/tháng': round(r['avg_month'] or 0),
        'Số tháng tồn còn lại': r['months_of_stock'],
        f'Gợi ý đặt thêm (đủ bán {buffer_months} tháng)': r['suggested_qty'],
        'Mã gốc bị khoá (đã thay bằng mã thay thế)': ', '.join(r.get('superseded_from') or []),
        'Đang bị khoá, chưa có mã thay thế': 'Có' if r.get('blocked_no_replacement') else '',
    } for r in suggestions])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Gợi ý nhập hàng')
    output.seek(0)

    filename_suffix = store_code or 'tat-ca'
    return send_file(
        output,
        as_attachment=True,
        download_name=f'goi-y-nhap-hang-{filename_suffix}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


# ----------------------------------------------------------------------------
# 2. CẢNH BÁO HẾT HÀNG CHO MÃ BÁN CHẠY (dùng chung hệ thống notification)
# ----------------------------------------------------------------------------

def check_and_notify_low_stock(cursor):
    """So sánh danh sách mã TX đang dưới ngưỡng (tính RIÊNG cho từng cửa
    hàng, vì 1 mã có thể ổn ở kho này nhưng sắp hết ở kho khác) với bảng
    low_stock_alerts đang lưu - mã MỚI rơi vào danh sách mới tạo thông báo
    (gửi cho đúng cửa hàng đó + admin); mã không còn dưới ngưỡng (đã được bổ
    sung hàng) sẽ được âm thầm xoá khỏi bảng, không thông báo gì thêm.
    KHÔNG tự commit - người gọi (route hoặc job nền) tự chịu trách nhiệm
    commit/rollback transaction hiện tại."""
    min_months = get_low_stock_min_months(cursor)
    stores = _valid_store_codes(cursor)

    cursor.execute('SELECT store_code, part_code FROM low_stock_alerts')
    existing = {(r['store_code'], r['part_code']) for r in cursor.fetchall()}

    current = {}
    for store in stores:
        rows, _ = _compute_sales_frequency_rows(cursor, store)
        for r in rows:
            if r['group'] == 'TX' and r['months_of_stock'] is not None and r['months_of_stock'] < min_months:
                current[(store, r['part_code'])] = r

    new_keys = set(current) - existing
    resolved_keys = existing - set(current)

    for store, part_code in new_keys:
        r = current[(store, part_code)]
        cursor.execute('''
            INSERT INTO low_stock_alerts (store_code, part_code, part_name, months_of_stock, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (store_code, part_code) DO NOTHING
        ''', (store, part_code, r['part_name'], r['months_of_stock']))

        qty_txt = f"{r['qty_on_hand']:g}" if r['qty_on_hand'] is not None else '0'
        avg_txt = f"{r['avg_month']:g}" if r['avg_month'] is not None else '0'
        mos_txt = f"{r['months_of_stock']:.1f}"
        part_label = f"{part_code} - {r['part_name']}" if r['part_name'] else part_code
        msg = (f"Mã {part_label} chỉ còn đủ bán khoảng {mos_txt} tháng "
               f"(tồn {qty_txt}, TB bán {avg_txt}/tháng). Nên đặt thêm hàng.")

        create_notification(cursor, store, 'Sắp hết hàng bán chạy', msg, 'warning')
        create_notification(cursor, ADMIN_NOTIF_STORE_CODE, 'Sắp hết hàng bán chạy', f'[{store}] {msg}', 'warning')

    for store, part_code in resolved_keys:
        cursor.execute('DELETE FROM low_stock_alerts WHERE store_code = %s AND part_code = %s', (store, part_code))

    return {'new_alerts': len(new_keys), 'resolved_alerts': len(resolved_keys)}


@dashboard_bp.route('/api/admin/low-stock-settings', methods=['GET'])
def low_stock_settings_get():
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    db = get_db()
    cursor = db.cursor()
    min_months = get_low_stock_min_months(cursor)
    cursor.close()
    return jsonify({'success': True, 'min_months': min_months})


@dashboard_bp.route('/api/admin/low-stock-settings', methods=['POST'])
def low_stock_settings_set():
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json or {}
    try:
        min_months = float(data.get('min_months'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Giá trị ngưỡng không hợp lệ.'}), 400
    if min_months <= 0:
        return jsonify({'error': 'Ngưỡng phải lớn hơn 0.'}), 400

    db = get_db()
    cursor = db.cursor()
    _set_app_setting(cursor, _LOW_STOCK_SETTING_KEY, str(min_months))
    # Áp dụng ngay ngưỡng mới, không đợi lượt kiểm tra nền tiếp theo.
    result = check_and_notify_low_stock(cursor)
    db.commit()
    cursor.close()

    return jsonify({'success': True, 'min_months': min_months, **result})


def _low_stock_scheduler_loop():
    """Vòng lặp nền: mỗi ngày tự kiểm tra lại 1 lần (phòng trường hợp không
    có upload tồn kho/xuất bán mới nào trong ngày nhưng cần dữ liệu vẫn được
    rà soát lại - ví dụ ngưỡng vừa bị admin đổi, hoặc dữ liệu bán ra thay đổi
    gián tiếp qua đường khác). Việc kiểm tra NGAY sau khi có upload mới đã
    được gọi trực tiếp trong upload_inventory()/import_sales_export() của
    app.py - vòng lặp này chỉ là lưới an toàn bổ sung."""
    time.sleep(90)
    while True:
        try:
            with app.app_context():
                db = get_db()
                cursor = db.cursor()
                cursor.execute('SELECT pg_try_advisory_lock(%s) AS locked', (_LOW_STOCK_LOCK_KEY,))
                got_lock = cursor.fetchone()['locked']
                if got_lock:
                    try:
                        check_and_notify_low_stock(cursor)
                        db.commit()
                    finally:
                        cursor.execute('SELECT pg_advisory_unlock(%s)', (_LOW_STOCK_LOCK_KEY,))
                        db.commit()
                cursor.close()
        except Exception:
            traceback.print_exc()
        time.sleep(24 * 60 * 60)


threading.Thread(target=_low_stock_scheduler_loop, daemon=True).start()


# ----------------------------------------------------------------------------
# 5. DASHBOARD TỔNG QUAN CHO ADMIN (gộp số liệu rải rác vào 1 API duy nhất)
# ----------------------------------------------------------------------------

@dashboard_bp.route('/api/admin/dashboard-summary', methods=['GET'])
def dashboard_summary():
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    cursor = db.cursor()

    # Tổng giá trị tồn kho = tồn x giá bán (mã chưa có giá bán tính là 0,
    # không làm sai lệch/loại bỏ các mã khác khỏi tổng).
    cursor.execute('''
        SELECT COALESCE(SUM(i.quantity * COALESCE(p.sale_price, 0)), 0) AS total_value
        FROM inventory_items i
        LEFT JOIN part_prices p ON p.part_code = i.part_code
    ''')
    inventory_value = float(cursor.fetchone()['total_value'] or 0)

    # Số phiếu luân chuyển nội bộ đang chờ xử lý (toàn hệ thống).
    cursor.execute("SELECT COUNT(*) AS c FROM transfer_requests WHERE status = 'pending'")
    transfer_pending = cursor.fetchone()['c']

    # Số mã CB (chậm bán) đang tồn đọng - toàn hệ thống.
    freq_rows, period_months = _compute_sales_frequency_rows_cached(cursor, None)
    cb_count = sum(1 for r in freq_rows if r['group'] == 'CB')
    tx_count = sum(1 for r in freq_rows if r['group'] == 'TX')

    # Số mã đang được cảnh báo sắp hết hàng (bảng low_stock_alerts).
    cursor.execute('SELECT COUNT(*) AS c FROM low_stock_alerts')
    low_stock_count = cursor.fetchone()['c']
    min_months = get_low_stock_min_months(cursor)

    cursor.close()

    # Số đơn B0 (đặt hàng cho khách) sắp tới hạn giao - đọc từ CSDL Supabase
    # riêng của orders.py, import trong hàm để không phụ thuộc thứ tự import
    # module. Nếu chưa cấu hình ORDERS_DATABASE_URL hoặc lỗi kết nối, trả về
    # null cho 2 trường bo_* kèm bo_error để FE tự hiển thị "chưa có dữ liệu"
    # thay vì làm hỏng cả API tổng quan.
    bo_due_soon = None
    bo_overdue = None
    bo_error = None
    try:
        from orders import get_orders_db
        odb = get_orders_db()
        ocursor = odb.cursor()
        ocursor.execute('''
            SELECT COUNT(*) AS c FROM bo_orders
            WHERE status NOT IN ('Đã giao', 'Đã huỷ')
              AND expected_delivery_date IS NOT NULL
              AND expected_delivery_date <= CURRENT_DATE + INTERVAL '7 days'
              AND expected_delivery_date >= CURRENT_DATE
        ''')
        bo_due_soon = ocursor.fetchone()['c']
        ocursor.execute('''
            SELECT COUNT(*) AS c FROM bo_orders
            WHERE status NOT IN ('Đã giao', 'Đã huỷ')
              AND expected_delivery_date IS NOT NULL
              AND expected_delivery_date < CURRENT_DATE
        ''')
        bo_overdue = ocursor.fetchone()['c']
        ocursor.close()
    except Exception as e:
        bo_error = str(e)

    return jsonify({
        'success': True,
        'inventory_value': inventory_value,
        'transfer_pending': transfer_pending,
        'cb_count': cb_count,
        'tx_count': tx_count,
        'low_stock_count': low_stock_count,
        'low_stock_min_months': min_months,
        'bo_due_soon': bo_due_soon,
        'bo_overdue': bo_overdue,
        'bo_error': bo_error,
        'period_months': period_months,
    })