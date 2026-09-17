# -*- coding: utf-8 -*-
"""
Module TRA CỨU BẢNG GIÁ BỘ ÁO XE HONDA (theo đời xe + màu, kèm ảnh + danh
sách phụ tùng bộ áo của đúng màu đó). Dữ liệu lưu trên CSDL Supabase RIÊNG
BIỆT dùng chung với orders.py (biến môi trường ORDERS_DATABASE_URL) - KHÔNG
đụng tới CSDL chính của app.py, y hệt cách orders.py đang làm.

CÁCH MÓC NỐI VÀO app.py (chỉ cần thêm đúng 2 chỗ, xem comment "MÓC NỐI"):
    1. Ngay SAU đoạn đăng ký orders_bp + _init_orders_with_retry() ở cuối
       app.py (module này TÁI SỬ DỤNG connection pool của orders.py qua
       get_orders_db()/_get_orders_pool(), nên phải import SAU khi
       orders.py đã được import xong):

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
                       time.sleep(base_delay_seconds * attempt)

           _init_body_kit_with_retry()

    2. KHÔNG cần sửa gì thêm trong init_db() của CSDL chính - bảng của
       module này nằm hoàn toàn trên CSDL đặt hàng riêng.

Ảnh xe được lưu dưới dạng FILE tĩnh trong thư mục static/body_kit_images/
của chính app.py (không lưu bytea trong CSDL) - nhẹ và phục vụ trực tiếp
qua Flask static, giống cách logo.png/font đang được phục vụ (xem
login.html). LƯU Ý: nếu server chạy trên nền tảng có filesystem tạm thời/bị
xoá mỗi lần deploy (vd 1 số gói miễn phí của Render), cần cấu hình 1 ổ đĩa
lưu trữ bền (persistent disk) trỏ vào thư mục static/body_kit_images để ảnh
không mất sau mỗi lần deploy lại - nếu không, chỉ cần import lại file Excel
là ảnh sẽ được tạo lại từ đầu.
"""
import os
import shutil
import time
import traceback

from flask import Blueprint, request, jsonify, session, url_for
from psycopg2.extras import execute_values

from orders import get_orders_db, _get_orders_pool, ORDERS_DATABASE_URL
from body_kit_import import parse_body_kit_excel

body_kit_bp = Blueprint('body_kit', __name__)

_IMAGE_SUBDIR = 'body_kit_images'


def _image_dir():
    """Thư mục vật lý lưu ảnh - dùng static_folder của chính app Flask
    (import trong hàm để tránh vòng import lúc app.py chưa chạy xong)."""
    from app import app
    d = os.path.join(app.static_folder, _IMAGE_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def _image_url(filename):
    if not filename:
        return None
    from app import app
    with app.app_context():
        return url_for('static', filename=f'{_IMAGE_SUBDIR}/{filename}')


# ----------------------------------------------------------------------------
# KHỞI TẠO BẢNG (trên CSDL đặt hàng riêng - xem lý do ở đầu file)
# ----------------------------------------------------------------------------

def init_body_kit_tables():
    if not ORDERS_DATABASE_URL:
        print('[body_kit] Bỏ qua init_body_kit_tables(): chưa cấu hình ORDERS_DATABASE_URL.', flush=True)
        return

    pool = _get_orders_pool()
    db = pool.getconn()
    try:
        cursor = db.cursor()
        # Advisory lock riêng (727270003) - khác mã 727270002 (bo_orders)
        # và 727270001 (CSDL chính) - tránh tranh chấp khoá dù trùng lúc.
        cursor.execute("SELECT pg_advisory_xact_lock(727270003)")
        cursor.execute("SET LOCAL lock_timeout = '10s'")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS body_kit_groups (
                id SERIAL PRIMARY KEY,
                group_label TEXT NOT NULL,
                model_code TEXT,
                vehicle_family TEXT,
                sub_model TEXT,
                image_filename TEXT,
                total_honda_price NUMERIC,
                total_crm_price NUMERIC,
                part_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        ''')
        # ALTER phòng trường hợp bảng đã được tạo từ TRƯỚC khi có 2 cột
        # vehicle_family/sub_model (deploy cũ) - CREATE TABLE IF NOT EXISTS
        # ở trên sẽ không tự thêm cột mới vào bảng đã tồn tại, nên cần ALTER
        # riêng, chạy vô hại (IF NOT EXISTS) kể cả khi bảng đã có đủ cột.
        cursor.execute('ALTER TABLE body_kit_groups ADD COLUMN IF NOT EXISTS vehicle_family TEXT')
        cursor.execute('ALTER TABLE body_kit_groups ADD COLUMN IF NOT EXISTS sub_model TEXT')
        cursor.execute('ALTER TABLE body_kit_groups ADD COLUMN IF NOT EXISTS year INTEGER')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS body_kit_parts (
                id SERIAL PRIMARY KEY,
                group_id INTEGER NOT NULL REFERENCES body_kit_groups(id) ON DELETE CASCADE,
                seq INTEGER,
                part_code VARCHAR(100),
                replacement_code VARCHAR(255),
                part_name TEXT,
                honda_price NUMERIC,
                crm_price NUMERIC,
                stock_qty NUMERIC,
                note TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_body_kit_groups_label ON body_kit_groups(group_label)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_body_kit_groups_family ON body_kit_groups(vehicle_family, sub_model)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_body_kit_parts_group ON body_kit_parts(group_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_body_kit_parts_code ON body_kit_parts(part_code)')
        db.commit()
        cursor.close()
        print('[body_kit] init_body_kit_tables() OK - bảng body_kit_groups/parts đã sẵn sàng.', flush=True)
    finally:
        pool.putconn(db)


# ----------------------------------------------------------------------------
# IMPORT TỪ EXCEL (chỉ admin) - THAY THẾ TOÀN BỘ dữ liệu cũ bằng dữ liệu mới
# (đây là dữ liệu tham khảo bảng giá, không phải dữ liệu giao dịch tích luỹ
# dần - mỗi lần có file bảng giá mới thì nạp lại toàn bộ cho gọn, tránh dữ
# liệu cũ/mới lẫn lộn khi mã nhóm không đảm bảo là khoá duy nhất ổn định).
# ----------------------------------------------------------------------------

@body_kit_bp.route('/api/admin/body-kit/import', methods=['POST'])
def import_body_kit_excel():
    if 'user' not in session or session['role'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'Vui lòng chọn file Excel.'}), 400

    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.xlsx')
    os.close(tmp_fd)
    try:
        file.save(tmp_path)
        try:
            groups, warnings = parse_body_kit_excel(tmp_path)
        except Exception as e:
            return jsonify({'error': f'Lỗi đọc file Excel: {e}'}), 400

        if not groups:
            return jsonify({'error': 'Không đọc được nhóm dữ liệu nào từ file - kiểm tra lại đúng file/sheet.'}), 400

        db = get_orders_db()
        cursor = db.cursor()
        try:
            cursor.execute('TRUNCATE TABLE body_kit_parts, body_kit_groups RESTART IDENTITY')

            # 1) Ghi TOÀN BỘ nhóm trong 1 lệnh (execute_values + RETURNING id)
            # thay vì 1 lệnh INSERT riêng cho từng nhóm (~750 lần) - nhanh
            # hơn nhiều lần vì chỉ 1 lượt round-trip tới CSDL thay vì 750.
            group_rows = execute_values(
                cursor,
                '''INSERT INTO body_kit_groups
                       (group_label, model_code, vehicle_family, sub_model, year,
                        total_honda_price, total_crm_price, part_count)
                   VALUES %s RETURNING id''',
                [(g['group_label'], g['model_code'], g['vehicle_family'], g['sub_model'], g['year'],
                  g['total_honda_price'], g['total_crm_price'], len(g['parts'])) for g in groups],
                fetch=True,
            )
            group_ids = [row['id'] for row in group_rows]
            inserted_groups = len(group_ids)

            # 2) Lưu ảnh xuống đĩa + gom danh sách cần UPDATE image_filename
            # (phải làm SAU khi có id thật, vì tên file ảnh dùng chính id).
            image_dir = _image_dir()
            # Xoá ảnh cũ trước khi nạp ảnh mới, tránh tồn đọng file ảnh mồ côi
            # (nhóm id sẽ đổi hết sau mỗi lần import lại vì bảng bị xoá/tạo mới).
            shutil.rmtree(image_dir, ignore_errors=True)
            os.makedirs(image_dir, exist_ok=True)
            image_updates = []  # [(image_filename, group_id), ...]
            for group_id, g in zip(group_ids, groups):
                if not g['image']:
                    continue
                data, fmt = g['image']
                image_filename = f'{group_id}.{fmt}'
                with open(os.path.join(image_dir, image_filename), 'wb') as f:
                    f.write(data)
                image_updates.append((image_filename, group_id))

            if image_updates:
                execute_values(
                    cursor,
                    '''UPDATE body_kit_groups AS g SET image_filename = v.image_filename
                       FROM (VALUES %s) AS v(image_filename, group_id)
                       WHERE g.id = v.group_id''',
                    image_updates,
                )

            # 3) Gộp TOÀN BỘ ~19.000 dòng phụ tùng của mọi nhóm thành 1 danh
            # sách rồi ghi bằng execute_values theo lô (page_size mặc định
            # 100 dòng/lệnh) - so với ghi từng dòng 1 (19.000 round-trip),
            # cách này nhanh hơn RẤT nhiều, tránh timeout khi file lớn.
            part_rows = []
            for group_id, g in zip(group_ids, groups):
                for p in g['parts']:
                    part_rows.append((
                        group_id, p['seq'], p['part_code'], p['replacement_code'], p['part_name'],
                        p['honda_price'], p['crm_price'], p['stock_qty'], p['note'],
                    ))

            if part_rows:
                execute_values(
                    cursor,
                    '''INSERT INTO body_kit_parts
                           (group_id, seq, part_code, replacement_code, part_name,
                            honda_price, crm_price, stock_qty, note)
                       VALUES %s''',
                    part_rows,
                    page_size=1000,
                )
            inserted_parts = len(part_rows)

            db.commit()
        except Exception:
            db.rollback()
            traceback.print_exc()
            return jsonify({'error': 'Lỗi khi ghi dữ liệu vào CSDL - đã huỷ toàn bộ import, dữ liệu cũ vẫn giữ nguyên.'}), 500
        finally:
            cursor.close()

        return jsonify({
            'success': True,
            'groups_imported': inserted_groups,
            'parts_imported': inserted_parts,
            'with_image': sum(1 for g in groups if g['image']),
            'warnings': warnings[:50],
            'warnings_total': len(warnings),
        })
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ----------------------------------------------------------------------------
# TRA CỨU (admin + cửa hàng đều xem được, để báo giá cho khách)
# ----------------------------------------------------------------------------

def _group_row_to_dict(r):
    return {
        'id': r['id'],
        'group_label': r['group_label'],
        'model_code': r['model_code'],
        'vehicle_family': r['vehicle_family'],
        'sub_model': r['sub_model'],
        'year': r['year'],
        'image_url': _image_url(r['image_filename']),
        'total_honda_price': float(r['total_honda_price']) if r['total_honda_price'] is not None else None,
        'total_crm_price': float(r['total_crm_price']) if r['total_crm_price'] is not None else None,
        'part_count': r['part_count'],
    }


@body_kit_bp.route('/api/body-kit/menu', methods=['GET'])
def get_body_kit_menu():
    """Trả về cây menu 2 tầng Dòng xe -> Đời/kiểu kèm số lượng nhóm, để FE
    dựng menu điều hướng thay vì hiển thị ~750 nhóm dạng danh sách phẳng
    ngay từ đầu. `vehicle_family`/`sub_model` được SUY LUẬN TỰ ĐỘNG lúc
    import từ nhãn nhóm gốc (xem classify_group_label trong
    body_kit_import.py) - không phải cột dữ liệu gốc của Excel, nên có thể
    có vài nhóm rơi vào dòng xe "Khác" nếu nhãn gốc không khớp từ khoá nào."""
    if 'user' not in session or session['role'] not in ('admin', 'store'):
        return jsonify({'error': 'Forbidden'}), 403

    db = get_orders_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT COALESCE(vehicle_family, 'Khác') AS vehicle_family,
               COALESCE(sub_model, group_label) AS sub_model,
               COUNT(*) AS group_count
        FROM body_kit_groups
        GROUP BY 1, 2
        ORDER BY 1, 2
    ''')
    rows = cursor.fetchall()
    cursor.close()

    tree = {}
    for r in rows:
        fam = tree.setdefault(r['vehicle_family'], {'family': r['vehicle_family'], 'group_count': 0, 'sub_models': []})
        fam['group_count'] += r['group_count']
        fam['sub_models'].append({'sub_model': r['sub_model'], 'group_count': r['group_count']})

    # Đưa "Khác" xuống cuối danh sách dòng xe cho gọn, còn lại giữ thứ tự
    # bảng chữ cái (đã ORDER BY ở SQL).
    families = [f for name, f in tree.items() if name != 'Khác']
    if 'Khác' in tree:
        families.append(tree['Khác'])

    return jsonify({'success': True, 'data': families})


@body_kit_bp.route('/api/body-kit/groups', methods=['GET'])
def list_body_kit_groups():
    if 'user' not in session or session['role'] not in ('admin', 'store'):
        return jsonify({'error': 'Forbidden'}), 403

    q = (request.args.get('q') or '').strip()
    family = (request.args.get('family') or '').strip()
    sub_model = (request.args.get('sub_model') or '').strip()
    db = get_orders_db()
    cursor = db.cursor()

    # `family`/`sub_model` chỉ áp dụng khi KHÔNG gõ tìm kiếm - gõ tìm kiếm
    # luôn quét toàn bộ dữ liệu bất kể đang đứng ở menu dòng xe nào, cho
    # đúng kỳ vọng thông thường của người dùng khi gõ vào ô tìm kiếm.
    if q:
        like = f'%{q}%'
        cursor.execute('''
            SELECT DISTINCT g.id, g.group_label, g.model_code, g.vehicle_family, g.sub_model, g.year,
                   g.image_filename, g.total_honda_price, g.total_crm_price, g.part_count
            FROM body_kit_groups g
            LEFT JOIN body_kit_parts p ON p.group_id = g.id
            WHERE g.group_label ILIKE %s OR g.model_code ILIKE %s
               OR p.part_code ILIKE %s OR p.part_name ILIKE %s
            ORDER BY g.vehicle_family, g.year ASC NULLS LAST, g.group_label
            LIMIT 500
        ''', (like, like, like, like))
    else:
        conditions = []
        params = []
        if family:
            conditions.append("COALESCE(vehicle_family, 'Khác') = %s")
            params.append(family)
        if sub_model:
            conditions.append('COALESCE(sub_model, group_label) = %s')
            params.append(sub_model)
        where_sql = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
        cursor.execute(f'''
            SELECT id, group_label, model_code, vehicle_family, sub_model, year,
                   image_filename, total_honda_price, total_crm_price, part_count
            FROM body_kit_groups
            {where_sql}
            ORDER BY year ASC NULLS LAST, group_label
            LIMIT 1000
        ''', params)

    rows = [_group_row_to_dict(r) for r in cursor.fetchall()]

    cursor.execute('SELECT COUNT(*) AS c, MAX(created_at) AS last_import FROM body_kit_groups')
    meta = cursor.fetchone()
    cursor.close()

    return jsonify({
        'success': True,
        'data': rows,
        'total': len(rows),
        'total_groups_all': meta['c'],
        'last_import': meta['last_import'].isoformat() if meta['last_import'] else None,
    })


@body_kit_bp.route('/api/body-kit/groups/<int:group_id>', methods=['GET'])
def get_body_kit_group(group_id):
    if 'user' not in session or session['role'] not in ('admin', 'store'):
        return jsonify({'error': 'Forbidden'}), 403

    db = get_orders_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id, group_label, model_code, vehicle_family, sub_model, year, image_filename, total_honda_price, total_crm_price, part_count
        FROM body_kit_groups WHERE id = %s
    ''', (group_id,))
    g = cursor.fetchone()
    if not g:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy nhóm.'}), 404

    cursor.execute('''
        SELECT seq, part_code, replacement_code, part_name, honda_price, crm_price, stock_qty, note
        FROM body_kit_parts WHERE group_id = %s ORDER BY seq NULLS LAST, id
    ''', (group_id,))
    part_rows = cursor.fetchall()
    cursor.close()

    # Giá bán HIỆN TẠI (bảng part_prices) và tồn kho HIỆN TẠI (bảng
    # inventory_items) - 2 bảng này nằm ở CSDL CHÍNH của app.py (biến
    # DATABASE_URL, nạp qua Admin -> "Nhập Giá Bán" / "Nhập Tồn Kho"), KHÁC
    # với CSDL riêng (ORDERS_DATABASE_URL) mà body_kit.py đang dùng ở trên -
    # nên phải mở 1 kết nối RIÊNG sang CSDL chính (get_db() của app.py) để
    # lấy, KHÔNG thể JOIN thẳng trong cùng 1 câu SQL vì khác database. Import
    # trong hàm (không import ở đầu file) để tránh vòng import lúc app.py
    # chưa chạy xong, giống cách _image_dir() đang làm.
    from app import get_db
    part_codes = list({p['part_code'] for p in part_rows if p['part_code']})
    current_price_by_code = {}
    current_stock_by_code = {}
    if part_codes:
        main_db = get_db()
        main_cursor = main_db.cursor()
        main_cursor.execute(
            'SELECT part_code, sale_price FROM part_prices WHERE part_code = ANY(%s)',
            (part_codes,)
        )
        current_price_by_code = {
            r['part_code']: float(r['sale_price']) if r['sale_price'] is not None else None
            for r in main_cursor.fetchall()
        }
        main_cursor.execute(
            'SELECT part_code, SUM(quantity) AS qty FROM inventory_items WHERE part_code = ANY(%s) GROUP BY part_code',
            (part_codes,)
        )
        current_stock_by_code = {r['part_code']: float(r['qty'] or 0) for r in main_cursor.fetchall()}
        main_cursor.close()

    parts = []
    for p in part_rows:
        honda_price = float(p['honda_price']) if p['honda_price'] is not None else None
        current_price = current_price_by_code.get(p['part_code'])
        # So giá bán HIỆN TẠI với giá Honda GỐC (lúc lập file bảng giá bộ
        # áo) để biết mã hàng này đã tăng/giảm giá hay chưa. Thiếu 1 trong 2
        # số (chưa có giá bán hiện tại, hoặc phụ tùng mới không có giá gốc)
        # thì không kết luận được -> price_status = None.
        price_status = None
        price_diff = None
        if honda_price is not None and current_price is not None:
            price_diff = round(current_price - honda_price, 2)
            if price_diff > 0:
                price_status = 'tang'
            elif price_diff < 0:
                price_status = 'giam'
            else:
                price_status = 'khong_doi'

        # Chỉ cần biết mã hàng ĐÃ điều chỉnh +5% (giống công thức của "Đề
        # Xuất Tăng Giá": giá bán = giá cũ + 5% giá cũ, làm tròn đến hàng
        # nghìn) hay CHƯA - không cần biết tăng bao nhiêu tiền. Giá bán hiện
        # tại lớn hơn hoặc bằng mốc +5% (trừ sai số làm tròn) thì coi là đã
        # điều chỉnh.
        is_adjusted_5pct = None
        if honda_price is not None and current_price is not None:
            expected_5pct = round(honda_price * 1.05 / 1000) * 1000
            is_adjusted_5pct = current_price >= expected_5pct - 0.5

        parts.append({
            'seq': p['seq'],
            'part_code': p['part_code'],
            'replacement_code': p['replacement_code'],
            'part_name': p['part_name'],
            'honda_price': honda_price,
            'crm_price': float(p['crm_price']) if p['crm_price'] is not None else None,
            'stock_qty': float(p['stock_qty']) if p['stock_qty'] is not None else None,
            'note': p['note'],
            'current_price': current_price,
            'current_stock': current_stock_by_code.get(p['part_code']),
            'price_status': price_status,
            'price_diff': price_diff,
            'is_adjusted_5pct': is_adjusted_5pct,
        })

    data = _group_row_to_dict(g)
    data['parts'] = parts
    return jsonify({'success': True, 'data': data})