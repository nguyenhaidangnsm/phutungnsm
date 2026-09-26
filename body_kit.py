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
import json
import os
import re
import shutil
import time
import traceback

from flask import Blueprint, request, jsonify, session, url_for, Response
from psycopg2 import Binary
from psycopg2.extras import execute_values

from orders import get_orders_db, _get_orders_pool, ORDERS_DATABASE_URL
from body_kit_import import (
    parse_body_kit_excel, parse_model_category_excel,
    classify_group_label, _extract_year,
)

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


def _load_model_category_map(cursor):
    """Trả về dict {model_code: (vehicle_family, sub_model)} từ bảng
    body_kit_model_categories (xem parse_model_category_excel) - dùng để
    GHI ĐÈ vehicle_family/sub_model suy luận tự động (classify_group_label)
    của body_kit_groups theo đúng model_code, vì nguồn này chính xác hơn."""
    cursor.execute('SELECT model_code, vehicle_family, sub_model FROM body_kit_model_categories')
    return {r['model_code']: (r['vehicle_family'], r['sub_model']) for r in cursor.fetchall()}


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
        # Bộ áo THÊM THỦ CÔNG (admin + cửa hàng nhập tay trên giao diện):
        #   is_manual   - TRUE = nhập tay, KHÔNG bị xoá khi admin nhập lại file Excel.
        #   created_by  - tên đăng nhập người tạo (cửa hàng chỉ sửa/xoá được bộ áo do mình tạo).
        #   image_data/image_mime - ẢNH LƯU THẲNG TRONG CSDL (bytea) thay vì file tĩnh, vì
        #     dữ liệu nhập tay KHÔNG thể "import lại từ Excel" để dựng lại ảnh nếu filesystem
        #     bị xoá sau mỗi lần deploy (xem cảnh báo ở đầu file).
        cursor.execute('ALTER TABLE body_kit_groups ADD COLUMN IF NOT EXISTS is_manual BOOLEAN NOT NULL DEFAULT FALSE')
        cursor.execute('ALTER TABLE body_kit_groups ADD COLUMN IF NOT EXISTS created_by TEXT')
        cursor.execute('ALTER TABLE body_kit_groups ADD COLUMN IF NOT EXISTS image_data BYTEA')
        cursor.execute('ALTER TABLE body_kit_groups ADD COLUMN IF NOT EXISTS image_mime TEXT')
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

        # Bảng tra "Mã xe" -> "Dòng xe/Đời xe" CHUẨN (nhập riêng từ 1 file
        # Excel khác - xem parse_model_category_excel trong body_kit_import.py),
        # DÙNG ĐỂ GHI ĐÈ vehicle_family/sub_model suy luận tự động từ
        # classify_group_label() ở mọi nhóm có model_code khớp - vì nguồn
        # này chính xác hơn (không lẫn biến thể STD/DX/Magnet vào sub_model).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS body_kit_model_categories (
                model_code TEXT PRIMARY KEY,
                vehicle_family TEXT NOT NULL,
                sub_model TEXT NOT NULL,
                raw_header TEXT,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        ''')
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
            # CHỈ xoá các nhóm nhập từ Excel (is_manual = FALSE) - bộ áo THÊM
            # THỦ CÔNG được giữ nguyên. body_kit_parts tự xoá theo (ON DELETE
            # CASCADE). Không còn RESTART IDENTITY: id nhóm thủ công phải giữ
            # nguyên, id nhóm Excel mới chỉ đơn giản nối tiếp dãy số.
            cursor.execute('DELETE FROM body_kit_groups WHERE is_manual = FALSE')

            # Mã xe nào có trong bảng tra category (nhập riêng qua
            # /api/admin/body-kit/import-model-categories) thì DÙNG NGUỒN
            # ĐÓ thay vì vehicle_family/sub_model suy luận tự động - xem
            # comment ở _load_model_category_map().
            category_map = _load_model_category_map(cursor)
            for g in groups:
                override = category_map.get(g['model_code']) if g['model_code'] else None
                if override:
                    g['vehicle_family'], g['sub_model'] = override

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
# IMPORT BẢNG TRA "MÃ XE" -> "DÒNG XE/ĐỜI XE" (chỉ admin) - file RIÊNG với
# bảng giá bộ áo ở trên (xem parse_model_category_excel trong
# body_kit_import.py). Sau khi nhập, GHI ĐÈ NGAY vehicle_family/sub_model
# của MỌI nhóm bộ áo hiện có khớp model_code - không cần nhập lại nguyên
# file bảng giá (~750 nhóm) chỉ để cập nhật menu dòng xe.
# ----------------------------------------------------------------------------

@body_kit_bp.route('/api/admin/body-kit/import-model-categories', methods=['POST'])
def import_body_kit_model_categories():
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
            mapping, conflicts, warnings = parse_model_category_excel(tmp_path)
        except Exception as e:
            return jsonify({'error': f'Lỗi đọc file Excel: {e}'}), 400

        if not mapping:
            return jsonify({'error': 'Không đọc được mã xe nào từ file - kiểm tra lại đúng file/sheet.'}), 400

        db = get_orders_db()
        cursor = db.cursor()
        try:
            cursor.execute('TRUNCATE TABLE body_kit_model_categories')
            execute_values(
                cursor,
                '''INSERT INTO body_kit_model_categories (model_code, vehicle_family, sub_model, raw_header)
                   VALUES %s''',
                [(code, v['vehicle_family'], v['sub_model'], v['raw_header']) for code, v in mapping.items()],
            )

            # Áp ngay cho các nhóm bộ áo ĐÃ CÓ SẴN (nếu đã từng nhập bảng
            # giá trước đó) - khớp theo model_code, KHÔNG đụng tới nhóm nào
            # có model_code không nằm trong bảng tra vừa nhập (giữ nguyên
            # giá trị suy luận tự động cũ của chúng).
            cursor.execute('''
                UPDATE body_kit_groups AS g
                SET vehicle_family = c.vehicle_family, sub_model = c.sub_model
                FROM body_kit_model_categories AS c
                WHERE g.model_code = c.model_code AND g.is_manual = FALSE
            ''')
            updated_groups = cursor.rowcount

            db.commit()
        except Exception:
            db.rollback()
            traceback.print_exc()
            return jsonify({'error': 'Lỗi khi ghi dữ liệu vào CSDL - đã huỷ toàn bộ import, dữ liệu cũ vẫn giữ nguyên.'}), 500
        finally:
            cursor.close()

        return jsonify({
            'success': True,
            'codes_imported': len(mapping),
            'groups_updated': updated_groups,
            'conflicts': conflicts[:50],
            'conflicts_total': len(conflicts),
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

def _can_edit_group(r):
    """Chỉ bộ áo THÊM THỦ CÔNG mới sửa/xoá được (bộ áo nhập từ Excel sẽ bị
    thay khi nhập lại nên không cho sửa tay). Admin sửa/xoá được mọi bộ áo
    thủ công; cửa hàng chỉ sửa/xoá bộ áo do chính tài khoản đó tạo."""
    if not r['is_manual']:
        return False
    if session.get('role') == 'admin':
        return True
    return bool(r['created_by']) and r['created_by'] == session.get('user')


def _group_image_url(r):
    # Bộ áo thủ công: ảnh nằm trong CSDL, phục vụ qua route riêng (tham số v =
    # mốc cập nhật để trình duyệt tải lại ảnh khi vừa đổi ảnh).
    if r['is_manual'] and r['has_db_image']:
        v = int(r['updated_at'].timestamp()) if r['updated_at'] else 0
        return url_for('body_kit.get_body_kit_group_image', group_id=r['id'], v=v)
    return _image_url(r['image_filename'])


def _group_row_to_dict(r):
    return {
        'id': r['id'],
        'group_label': r['group_label'],
        'model_code': r['model_code'],
        'vehicle_family': r['vehicle_family'],
        'sub_model': r['sub_model'],
        'year': r['year'],
        'is_manual': bool(r['is_manual']),
        'created_by': r['created_by'],
        'can_edit': _can_edit_group(r),
        'image_url': _group_image_url(r),
        'total_honda_price': float(r['total_honda_price']) if r['total_honda_price'] is not None else None,
        'total_crm_price': float(r['total_crm_price']) if r['total_crm_price'] is not None else None,
        'part_count': r['part_count'],
    }


@body_kit_bp.route('/api/body-kit/menu', methods=['GET'])
def get_body_kit_menu():
    """Trả về cây menu 2 tầng Dòng xe -> Đời/kiểu kèm số lượng nhóm, để FE
    dựng menu điều hướng thay vì hiển thị ~750 nhóm dạng danh sách phẳng
    ngay từ đầu. `vehicle_family`/`sub_model` ưu tiên lấy từ bảng tra
    body_kit_model_categories (nhập qua /api/admin/body-kit/import-model-
    categories, khớp theo model_code - xem parse_model_category_excel);
    nhóm nào có model_code KHÔNG có trong bảng tra đó thì vẫn dùng giá trị
    SUY LUẬN TỰ ĐỘNG lúc import bảng giá (xem classify_group_label trong
    body_kit_import.py) - nên có thể có vài nhóm rơi vào dòng xe "Khác"
    nếu cả 2 nguồn đều không khớp được."""
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
        # Với các trường MÃ (model_code/part_code), bỏ dấu "-" và khoảng trắng ở
        # cả 2 phía trước khi so khớp, để gõ "53012-K12-900", "53012 k12 900"
        # hay "53012K12900" đều ra cùng 1 kết quả (ILIKE đã tự bỏ qua hoa/thường
        # sẵn). KHÔNG áp dụng cho group_label/part_name vì đó là tên/nhãn tự do,
        # cần giữ nguyên khoảng trắng giữa các từ để so khớp đúng nghĩa.
        q_code = re.sub(r'[\s-]+', '', q)
        like_code = f'%{q_code}%'
        cursor.execute('''
            SELECT DISTINCT g.id, g.group_label, g.model_code, g.vehicle_family, g.sub_model, g.year,
                   g.image_filename, g.total_honda_price, g.total_crm_price, g.part_count,
                   g.is_manual, g.created_by, g.updated_at, (g.image_data IS NOT NULL) AS has_db_image
            FROM body_kit_groups g
            LEFT JOIN body_kit_parts p ON p.group_id = g.id
            WHERE g.group_label ILIKE %s
               OR REGEXP_REPLACE(g.model_code, '[\\s-]+', '', 'g') ILIKE %s
               OR REGEXP_REPLACE(p.part_code, '[\\s-]+', '', 'g') ILIKE %s
               OR p.part_name ILIKE %s
            ORDER BY g.vehicle_family, g.year ASC NULLS LAST, g.group_label
            LIMIT 500
        ''', (like, like_code, like_code, like))
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
                   image_filename, total_honda_price, total_crm_price, part_count,
                   is_manual, created_by, updated_at, (image_data IS NOT NULL) AS has_db_image
            FROM body_kit_groups
            {where_sql}
            ORDER BY year ASC NULLS LAST, group_label
            LIMIT 1000
        ''', params)

    rows = [_group_row_to_dict(r) for r in cursor.fetchall()]

    cursor.execute('SELECT COUNT(*) AS c, MAX(created_at) FILTER (WHERE NOT is_manual) AS last_import FROM body_kit_groups')
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
        SELECT id, group_label, model_code, vehicle_family, sub_model, year, image_filename, total_honda_price, total_crm_price, part_count,
               is_manual, created_by, updated_at, (image_data IS NOT NULL) AS has_db_image
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

    # Giá Honda gốc DỰ PHÒNG cho bộ áo thủ công: nếu dòng đang xem không có
    # honda_price (người thêm bỏ trống, không muốn gõ tay), tra xem MÃ HÀNG
    # đó đã có honda_price ở bất kỳ bộ áo nào khác trong hệ thống chưa (ưu
    # tiên nhóm nhập từ Excel - g.is_manual ASC - vì đáng tin hơn nhóm thủ
    # công khác) - cùng CSDL/connection nên tận dụng luôn cursor này, không
    # cần mở thêm kết nối. Nhờ vậy huy hiệu "Đã điều chỉnh +5%" tự động hoạt
    # động cho bộ áo thủ công MÀ KHÔNG cần người dùng tự điền Giá Honda.
    manual_codes = list({p['part_code'] for p in part_rows if p['part_code'] and p['honda_price'] is None})
    fallback_honda_by_code = {}
    if manual_codes:
        cursor.execute('''
            SELECT DISTINCT ON (bp.part_code) bp.part_code, bp.honda_price
            FROM body_kit_parts bp
            JOIN body_kit_groups g ON g.id = bp.group_id
            WHERE bp.part_code = ANY(%s) AND bp.honda_price IS NOT NULL
            ORDER BY bp.part_code, g.is_manual ASC, bp.id DESC
        ''', (manual_codes,))
        fallback_honda_by_code = {r['part_code']: float(r['honda_price']) for r in cursor.fetchall()}
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
    fallback_name_by_code = {}
    adjusted_flag_by_code = {}
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
        # Bộ áo thủ công: nếu người nhập bỏ trống "Tên hàng" lúc thêm, tra
        # tạm tên theo mã hàng trong inventory_items (CSDL chính) - phòng
        # trường hợp mã đó đã có tên sẵn trong hệ thống (đã từng nhập tồn/
        # bán hàng) mà người nhập không biết/không gõ lại.
        main_cursor.execute(
            '''SELECT DISTINCT ON (UPPER(part_code)) part_code, part_name
               FROM inventory_items WHERE UPPER(part_code) = ANY(%s) AND part_name IS NOT NULL AND part_name <> ''
               ORDER BY UPPER(part_code), id DESC''',
            ([c.upper() for c in part_codes],)
        )
        # Khoá bằng chữ HOA - vì mã hàng nhập tay trong bộ áo thủ công có thể
        # khác cách viết hoa/thường so với mã đã lưu trong tồn kho.
        fallback_name_by_code = {r['part_code'].upper(): r['part_name'] for r in main_cursor.fetchall()}

        # Nguồn dự phòng THỨ 2 (đáng tin hơn tồn kho, vì là chính nơi lưu Tên
        # hàng + lịch sử tăng giá của mã đó): bảng price_adjustment_proposals
        # của tính năng "Đề Xuất Tăng Giá" (price_adjustment.py) - cùng CSDL
        # chính, tận dụng luôn main_cursor này. Lấy dòng đề xuất GẦN NHẤT của
        # mỗi mã (created_at DESC) để có Tên hàng mới nhất, và sự TỒN TẠI của
        # ít nhất 1 dòng cho biết mã đó ĐÃ từng được đề xuất tăng giá hay
        # chưa - đây chính xác là cách tính năng Đề Xuất Tăng Giá đang định
        # nghĩa "Đã điều chỉnh"/"Chưa điều chỉnh" cho TOÀN hệ thống, nên dùng
        # lại luôn thay vì phải có Giá Honda gốc mới so sánh được (điều mà
        # bộ áo thêm thủ công thường không có).
        main_cursor.execute(
            '''SELECT DISTINCT ON (UPPER(part_code)) part_code, part_name
               FROM price_adjustment_proposals WHERE UPPER(part_code) = ANY(%s)
               ORDER BY UPPER(part_code), created_at DESC''',
            ([c.upper() for c in part_codes],)
        )
        proposal_rows = main_cursor.fetchall()
        adjusted_flag_by_code = {r['part_code'].upper(): True for r in proposal_rows}
        # Tên hàng từ đề xuất tăng giá ưu tiên CAO HƠN tồn kho (thường mới/
        # sát thực tế hơn) - ghi đè lên fallback_name_by_code cho mã nào có.
        fallback_name_by_code.update(
            {r['part_code'].upper(): r['part_name'] for r in proposal_rows if r['part_name']})
        main_cursor.close()

    parts = []
    for p in part_rows:
        honda_price = float(p['honda_price']) if p['honda_price'] is not None else fallback_honda_by_code.get(p['part_code'])
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
        elif current_price is not None and p['part_code']:
            # Không đủ Giá Honda gốc để so 5% (mã thêm thủ công, hoàn toàn
            # mới) -> dùng thẳng dữ liệu "Đề Xuất Tăng Giá": mã ĐÃ từng được
            # đề xuất tăng giá ít nhất 1 lần thì coi là "Đã điều chỉnh", chưa
            # từng đề xuất thì "Chưa điều chỉnh".
            is_adjusted_5pct = adjusted_flag_by_code.get(p['part_code'].upper(), False)

        # Trước đây badge đã/chưa điều chỉnh +5% bị ẩn cho MỌI bộ áo thủ công
        # (kể cả khi dòng đó có đủ honda_price để so sánh), vì lo honda_price
        # người nhập tay không đáng tin. Giờ chỉ ẩn khi hoàn toàn không có gì
        # để kết luận (không có honda_price LẪN không có giá bán hiện tại) -
        # còn lại luôn tính được nhờ 1 trong 2 nguồn ở trên.

        parts.append({
            'seq': p['seq'],
            'part_code': p['part_code'],
            'replacement_code': p['replacement_code'],
            'part_name': p['part_name'] or fallback_name_by_code.get((p['part_code'] or '').upper()),
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


# ----------------------------------------------------------------------------
# THÊM / SỬA / XOÁ BỘ ÁO THỦ CÔNG (admin + cửa hàng đều được thêm; sửa/xoá xem
# _can_edit_group). Bộ áo thủ công có is_manual = TRUE và KHÔNG bị xoá khi
# admin nhập lại file Excel (xem import_body_kit_excel).
# ----------------------------------------------------------------------------

_MAX_MANUAL_PARTS = 300
_MAX_IMAGE_BYTES = 3 * 1024 * 1024


def _kit_user_allowed():
    return 'user' in session and session.get('role') in ('admin', 'store')


def _sniff_image_mime(data):
    """Nhận diện định dạng ảnh theo NỘI DUNG file (không tin đuôi file/Content-Type
    do trình duyệt gửi lên)."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _read_uploaded_image():
    """Trả về (bytes, mime) hoặc None nếu không có ảnh mới. Ném ValueError nếu ảnh không hợp lệ."""
    f = request.files.get('image')
    if not f or not f.filename:
        return None
    data = f.read(_MAX_IMAGE_BYTES + 1)
    if not data:
        return None
    if len(data) > _MAX_IMAGE_BYTES:
        raise ValueError('Ảnh quá lớn (tối đa 3MB).')
    mime = _sniff_image_mime(data)
    if not mime:
        raise ValueError('Ảnh phải là định dạng JPG, PNG, GIF hoặc WEBP.')
    return data, mime


def _price_or_none(v, row_no):
    if v is None or v == '':
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ValueError(f'Dòng {row_no}: giá không hợp lệ.')
    if n != n or n < 0 or n > 1e12:
        raise ValueError(f'Dòng {row_no}: giá không hợp lệ.')
    return n


def _parse_group_form():
    """Đọc + kiểm tra dữ liệu form (multipart) của 1 bộ áo thủ công. Ném
    ValueError (thông báo tiếng Việt, hiển thị thẳng cho người dùng) nếu sai."""
    form = request.form
    label = (form.get('group_label') or '').strip()
    if not label:
        raise ValueError('Vui lòng nhập tên bộ áo (đời xe + màu).')
    if len(label) > 300:
        raise ValueError('Tên bộ áo quá dài (tối đa 300 ký tự).')

    model_code = (form.get('model_code') or '').strip()[:50] or None
    family = (form.get('vehicle_family') or '').strip()[:100] or None
    sub_model = (form.get('sub_model') or '').strip()[:150] or None

    year = None
    year_raw = (form.get('year') or '').strip()
    if year_raw:
        try:
            year = int(year_raw)
        except ValueError:
            raise ValueError('Năm không hợp lệ.')
        if not 1950 <= year <= 2100:
            raise ValueError('Năm phải nằm trong khoảng 1950 - 2100.')

    try:
        raw_parts = json.loads(form.get('parts') or '[]')
    except ValueError:
        raise ValueError('Dữ liệu phụ tùng không hợp lệ.')
    if not isinstance(raw_parts, list):
        raise ValueError('Dữ liệu phụ tùng không hợp lệ.')
    if len(raw_parts) > _MAX_MANUAL_PARTS:
        raise ValueError(f'Tối đa {_MAX_MANUAL_PARTS} phụ tùng cho 1 bộ áo.')

    parts = []
    seen = {}
    for i, rp in enumerate(raw_parts, start=1):
        if not isinstance(rp, dict):
            raise ValueError('Dữ liệu phụ tùng không hợp lệ.')
        code = str(rp.get('part_code') or '').strip()
        name = str(rp.get('part_name') or '').strip()
        repl = str(rp.get('replacement_code') or '').strip()
        note = str(rp.get('note') or '').strip()
        honda_raw = rp.get('honda_price')
        if not any([code, name, repl, note]) and honda_raw in (None, ''):
            continue  # dòng trống hoàn toàn -> bỏ qua
        if not code:
            raise ValueError(f'Dòng {i}: thiếu mã hàng.')
        if len(code) > 100 or len(repl) > 255:
            raise ValueError(f'Dòng {i}: mã hàng/thay thế quá dài.')
        key = code.upper()
        if key in seen:
            raise ValueError(f"Mã hàng '{code}' bị trùng (dòng {seen[key]} và dòng {i}).")
        seen[key] = i
        parts.append({
            'seq': len(parts) + 1,
            'part_code': code,
            'replacement_code': repl or None,
            'part_name': name[:500] or None,
            'honda_price': _price_or_none(honda_raw, i),
            'note': note[:500] or None,
        })
    if not parts:
        raise ValueError('Bộ áo cần có ít nhất 1 phụ tùng.')

    return {
        'group_label': label,
        'model_code': model_code,
        'vehicle_family': family,
        'sub_model': sub_model,
        'year': year,
        'parts': parts,
        'remove_image': form.get('remove_image') == '1',
    }


def _resolve_family(cursor, data):
    """Dòng xe/đời của bộ áo thủ công: ưu tiên người dùng nhập tay; nếu để trống
    thì tra theo mã model trong bảng tra chuẩn; cuối cùng mới suy luận từ tên."""
    family = data['vehicle_family']
    sub_model = data['sub_model']
    if family:
        return family, (sub_model or family)
    if data['model_code']:
        mapped = _load_model_category_map(cursor).get(data['model_code'])
        if mapped:
            return mapped[0], (sub_model or mapped[1])
    guessed_family, guessed_sub = classify_group_label(data['group_label'])
    return guessed_family, (sub_model or guessed_sub)


def _total_honda(parts):
    prices = [p['honda_price'] for p in parts if p['honda_price'] is not None]
    return sum(prices) if prices else None


def _insert_manual_parts(cursor, group_id, parts):
    execute_values(
        cursor,
        '''INSERT INTO body_kit_parts
               (group_id, seq, part_code, replacement_code, part_name, honda_price, note)
           VALUES %s''',
        [(group_id, p['seq'], p['part_code'], p['replacement_code'], p['part_name'],
          p['honda_price'], p['note']) for p in parts],
    )


@body_kit_bp.route('/api/body-kit/groups', methods=['POST'])
def create_body_kit_group():
    if not _kit_user_allowed():
        return jsonify({'error': 'Forbidden'}), 403
    try:
        data = _parse_group_form()
        image = _read_uploaded_image()
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    db = get_orders_db()
    cursor = db.cursor()
    try:
        family, sub_model = _resolve_family(cursor, data)
        year = data['year'] if data['year'] is not None else _extract_year(data['group_label'])
        cursor.execute(
            '''INSERT INTO body_kit_groups
                   (group_label, model_code, vehicle_family, sub_model, year,
                    total_honda_price, part_count, is_manual, created_by, image_data, image_mime)
               VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
               RETURNING id''',
            (data['group_label'], data['model_code'], family, sub_model, year,
             _total_honda(data['parts']), len(data['parts']), session.get('user'),
             Binary(image[0]) if image else None, image[1] if image else None),
        )
        group_id = cursor.fetchone()['id']
        _insert_manual_parts(cursor, group_id, data['parts'])
        db.commit()
    except Exception:
        db.rollback()
        traceback.print_exc()
        return jsonify({'error': 'Lỗi khi lưu bộ áo vào CSDL.'}), 500
    finally:
        cursor.close()

    return jsonify({'success': True, 'id': group_id, 'vehicle_family': family})


def _load_editable_group(cursor, group_id):
    """Trả về (row, error_response). Chỉ cho sửa/xoá bộ áo thủ công + đúng quyền."""
    cursor.execute(
        'SELECT id, is_manual, created_by FROM body_kit_groups WHERE id = %s', (group_id,))
    row = cursor.fetchone()
    if not row:
        return None, (jsonify({'error': 'Không tìm thấy bộ áo.'}), 404)
    if not row['is_manual']:
        return None, (jsonify({'error': 'Chỉ sửa/xoá được bộ áo thêm thủ công. '
                                        'Bộ áo nhập từ Excel sẽ được thay khi admin nhập lại file.'}), 403)
    if not _can_edit_group(row):
        return None, (jsonify({'error': 'Bạn chỉ được sửa/xoá bộ áo do chính mình thêm.'}), 403)
    return row, None


@body_kit_bp.route('/api/body-kit/groups/<int:group_id>', methods=['PUT'])
def update_body_kit_group(group_id):
    if not _kit_user_allowed():
        return jsonify({'error': 'Forbidden'}), 403
    try:
        data = _parse_group_form()
        image = _read_uploaded_image()
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    db = get_orders_db()
    cursor = db.cursor()
    try:
        row, err = _load_editable_group(cursor, group_id)
        if err:
            return err
        family, sub_model = _resolve_family(cursor, data)
        year = data['year'] if data['year'] is not None else _extract_year(data['group_label'])

        image_sql = ''
        image_params = []
        if image:
            image_sql = ', image_data = %s, image_mime = %s'
            image_params = [Binary(image[0]), image[1]]
        elif data['remove_image']:
            image_sql = ', image_data = NULL, image_mime = NULL'

        cursor.execute(
            f'''UPDATE body_kit_groups
                   SET group_label = %s, model_code = %s, vehicle_family = %s, sub_model = %s,
                       year = %s, total_honda_price = %s, part_count = %s, updated_at = NOW()
                       {image_sql}
                   WHERE id = %s''',
            [data['group_label'], data['model_code'], family, sub_model, year,
             _total_honda(data['parts']), len(data['parts'])] + image_params + [group_id],
        )
        cursor.execute('DELETE FROM body_kit_parts WHERE group_id = %s', (group_id,))
        _insert_manual_parts(cursor, group_id, data['parts'])
        db.commit()
    except Exception:
        db.rollback()
        traceback.print_exc()
        return jsonify({'error': 'Lỗi khi cập nhật bộ áo.'}), 500
    finally:
        cursor.close()

    return jsonify({'success': True, 'id': group_id, 'vehicle_family': family})


@body_kit_bp.route('/api/body-kit/groups/<int:group_id>', methods=['DELETE'])
def delete_body_kit_group(group_id):
    if not _kit_user_allowed():
        return jsonify({'error': 'Forbidden'}), 403
    db = get_orders_db()
    cursor = db.cursor()
    try:
        row, err = _load_editable_group(cursor, group_id)
        if err:
            return err
        cursor.execute('DELETE FROM body_kit_groups WHERE id = %s', (group_id,))  # parts xoá theo (CASCADE)
        db.commit()
    except Exception:
        db.rollback()
        traceback.print_exc()
        return jsonify({'error': 'Lỗi khi xoá bộ áo.'}), 500
    finally:
        cursor.close()
    return jsonify({'success': True})


@body_kit_bp.route('/api/body-kit/groups/<int:group_id>/image', methods=['GET'])
def get_body_kit_group_image(group_id):
    if not _kit_user_allowed():
        return jsonify({'error': 'Forbidden'}), 403
    db = get_orders_db()
    cursor = db.cursor()
    cursor.execute('SELECT image_data, image_mime FROM body_kit_groups WHERE id = %s', (group_id,))
    row = cursor.fetchone()
    cursor.close()
    if not row or not row['image_data']:
        return jsonify({'error': 'Không có ảnh.'}), 404
    resp = Response(bytes(row['image_data']), mimetype=row['image_mime'] or 'image/jpeg')
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


@body_kit_bp.route('/api/body-kit/part-lookup', methods=['POST'])
def body_kit_part_lookup():
    """Tra nhanh danh sách mã hàng trong CSDL CHÍNH (tên hàng từ tồn kho, giá bán
    từ part_prices) để form thêm bộ áo tự điền tên + báo mã gõ sai. Khớp không
    phân biệt hoa/thường, trả về mã CHUẨN như đang lưu trong hệ thống."""
    if not _kit_user_allowed():
        return jsonify({'error': 'Forbidden'}), 403
    payload = request.get_json(silent=True) or {}
    raw_codes = payload.get('codes')
    if not isinstance(raw_codes, list):
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400
    upper_codes = sorted({str(c).strip().upper() for c in raw_codes if str(c).strip()})[:_MAX_MANUAL_PARTS]
    if not upper_codes:
        return jsonify({'success': True, 'data': {}})

    from app import get_db
    main_db = get_db()
    cur = main_db.cursor()
    cur.execute(
        '''SELECT MIN(part_code) AS part_code, MAX(part_name) AS part_name, SUM(quantity) AS qty
           FROM inventory_items WHERE UPPER(part_code) = ANY(%s) GROUP BY UPPER(part_code)''',
        (upper_codes,))
    result = {}
    for r in cur.fetchall():
        result[r['part_code'].upper()] = {
            'part_code': r['part_code'], 'part_name': r['part_name'],
            'sale_price': None, 'stock': float(r['qty'] or 0),
        }
    cur.execute(
        'SELECT part_code, sale_price FROM part_prices WHERE UPPER(part_code) = ANY(%s)',
        (upper_codes,))
    for r in cur.fetchall():
        entry = result.setdefault(r['part_code'].upper(), {
            'part_code': r['part_code'], 'part_name': None, 'sale_price': None, 'stock': None})
        entry['sale_price'] = float(r['sale_price']) if r['sale_price'] is not None else None
    cur.close()
    return jsonify({'success': True, 'data': result})