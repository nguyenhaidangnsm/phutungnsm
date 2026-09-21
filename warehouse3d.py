"""
=============================================================================
TÍNH NĂNG SƠ ĐỒ KHO 3D (Warehouse 3D Layout)
=============================================================================
Tách riêng ra file này (giống stocktake.py/price_adjustment.py/dashboard.py)
để khỏi làm app.py dài thêm thêm. File này gồm:
  - init_warehouse3d_tables(cursor): tạo 3 bảng mới, gọi từ init_db() trong
    app.py (xem HƯỚNG DẪN ĐĂNG KÝ ở cuối docstring này).
  - warehouse3d_bp: Blueprint gồm 1 route trang HTML (sơ đồ kho 3D) + các
    API JSON để CRUD sàn kho / kệ / gán mã hàng vào từng tầng kệ.

MÔ HÌNH DỮ LIỆU  (Lầu -> Kệ -> Tầng -> Ô -> Ngăn)
  - warehouse_floors: 1 CỬA HÀNG CÓ THỂ CÓ NHIỀU LẦU - mỗi lầu 1 dòng, có
    tên riêng (vd "Tầng trệt", "Lầu 1"), thứ tự hiển thị (floor_order) và
    kích thước sàn riêng (rộng x sâu, đơn vị mét) và HÌNH DẠNG sàn: mặc định
    là hình chữ nhật; nếu cột floor_shape có giá trị (JSON [[x, z], ...] - các
    đỉnh đa giác đi vòng quanh mép sàn, mét, gốc (0, 0) là góc trái-trong) thì
    sàn có hình đó (chữ L, U, T, hình bất kỳ...) và floor_width/floor_depth tự
    được đặt bằng hộp bao ngoài của các đỉnh. Cửa hàng nào chưa từng
    lưu thì tự tạo 1 lầu mặc định "Tầng trệt" 20 x 15 khi truy cập lần đầu
    (xem _ensure_floors()). LƯU Ý: đây là "lầu" của toà nhà kho (building
    floor) - khác với "Tầng" (level) của 1 cái kệ nhiều tầng bên dưới.
  - warehouse_shelves: từng "khối" đặt trong kho (kệ nhiều tầng / khu để
    hàng dưới sàn / vách ngăn / cửa / cầu thang - 3 loại cuối chỉ để vẽ sơ đồ,
    không gán được mã hàng) - có toạ độ TÂM khối (pos_x, pos_z, tính từ
    góc trái-trong của sàn kho khi nhìn từ trên xuống), kích thước
    (width/height/depth), góc xoay quanh trục đứng (rotation_y, đơn vị
    RADIAN) và cấu trúc bên trong (chỉ có ý nghĩa với loại "shelf"):
      levels          = số TẦNG (chia đều theo chiều cao, Tầng 1 = thấp nhất)
      cells_per_level = số Ô mỗi tầng (chia đều theo chiều rộng, Ô 1 = bên
                        trái khi nhìn từ mặt trước kệ - mặt hướng +Z)
      slots_per_cell  = số NGĂN mặc định của mỗi ô (chia đều theo chiều cao
                        của tầng, Ngăn 1 = thấp nhất)
  - warehouse_shelf_cells: CHỈ lưu những ô có số ngăn KHÁC mặc định của kệ
    (ô nào không có dòng ở đây thì dùng slots_per_cell). Nhờ vậy 1 kệ có thể
    có ô 1 ngăn, ô 3 ngăn... trên cùng 1 tầng.
  - warehouse_shelf_items: mã hàng nào đang được gán vào (tầng, ô, ngăn) nào
    của kệ nào. 1 mã hàng có thể gán ở nhiều vị trí khác nhau (hàng nhiều,
    để rải ra nhiều chỗ) - không giới hạn số lượng. Dữ liệu cũ (trước khi có
    ô/ngăn) tự mặc định nằm ở Ô 1 - Ngăn 1.

  Hệ toạ độ: trục X sang phải, trục Z vào sâu trong kho, khớp với mặt
  phẳng đáy (X-Z) thường dùng trong Three.js (Y là chiều cao).

TÍCH HỢP VỚI HỆ THỐNG VỊ TRÍ (part_locations) ĐANG CÓ:
  Khi gán 1 mã hàng vào 1 vị trí trên kệ qua sơ đồ 3D, _sync_legacy_location()
  TỰ ĐỘNG ghi location_1 = mã kệ, location_2 = "Tầng {N}", và (chỉ với loại
  "shelf") location_3 = "Ô {C} - Ngăn {S}" vào bảng part_locations hiện có -
  để các màn hình tra cứu vị trí cũ (bảng Tồn Kho Hệ Thống, tra cứu vị trí...)
  hiển thị đúng ngay mà không cần sửa gì thêm ở những nơi đó. LƯU Ý: location_3
  nhập tay trước đó của mã hàng đó sẽ bị ghi đè (giống location_1/2). Với khu
  để hàng dưới sàn thì location_3 được giữ nguyên. Khi gỡ mã hàng khỏi kệ,
  _clear_legacy_location() CHỈ xoá location_1/location_2 nếu chúng vẫn ĐANG
  khớp đúng kệ/tầng vừa gỡ, và CHỈ xoá location_3 nếu nó vẫn đúng bằng
  "Ô/Ngăn" vừa gỡ (tránh xoá nhầm nếu người dùng đã tự sửa tay sau đó). Nếu
  không muốn đồng bộ 2 chiều này, bỏ các lời gọi hàm đó trong
  assign_item()/unassign_item().

QUYỀN: giống hệt mô hình đang dùng cho part_locations (save_location() ở
app.py) - role 'store' chỉ xem/sửa kho của ĐÚNG cửa hàng mình, role
'admin' xem/sửa được kho của MỌI cửa hàng hợp lệ trong hệ thống.

HƯỚNG DẪN ĐĂNG KÝ (2 chỗ cần thêm vào app.py, không cần sửa gì khác):

  1) Trong init_db(), đặt CẠNH 3 dòng init_stocktake_tables(cursor)/
     init_price_adjustment_tables(cursor)/init_dashboard_tables(cursor) đã
     có sẵn (khoảng dòng 1135-1144), thêm:

        init_warehouse3d_tables(cursor)

  2) Ở CUỐI app.py, đặt CẠNH các đoạn đăng ký blueprint khác (vd ngay sau
     đoạn đăng ký dashboard_bp), thêm:

        from warehouse3d import warehouse3d_bp, init_warehouse3d_tables
        app.register_blueprint(warehouse3d_bp)

  Lưu ý: dòng "from warehouse3d import ..." phải đặt ở PHẦN CUỐI app.py
  (sau khi get_db/vn_now/format_vi_datetime/_valid_store_codes/
  _current_actor_name đã được định nghĩa xong ở phía trên) - giống hệt lý
  do stocktake.py/dashboard.py phải import ngược lại từ app.py.

  3) Đặt file warehouse3d.html (đi kèm) vào cùng thư mục templates/ với
     index.html/login.html - route /kho-3d bên dưới tự render nó. Có thể
     thêm 1 link/nút "Sơ Đồ Kho 3D" trỏ tới url_for('warehouse3d.warehouse3d_page')
     ở menu chính trong index.html nếu muốn (không bắt buộc để chạy được).
=============================================================================
"""
import json
import math

from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for

from app import get_db, vn_now, format_vi_datetime, _valid_store_codes, _current_actor_name

warehouse3d_bp = Blueprint('warehouse3d', __name__)

_VALID_SHELF_TYPES = {'shelf', 'floor_area', 'wall', 'door', 'stairs'}
# Các loại khối KHÔNG dùng để chứa hàng: không gán được mã hàng, không có ô/ngăn.
_NON_STORAGE_TYPES = {'wall', 'door', 'stairs'}
_NON_STORAGE_LABELS = {'wall': 'vách ngăn', 'door': 'cửa', 'stairs': 'cầu thang'}
_MAX_DIM = 30          # kích thước tối đa 1 chiều (mét) - chặn nhập nhầm số quá lớn
_MAX_LEVELS = 20
_MAX_CELLS = 20         # số ô tối đa mỗi tầng
_MAX_SLOTS = 20         # số ngăn tối đa mỗi ô


# ---------------------------------------------------------------------------
# KHỞI TẠO BẢNG - gọi từ init_db() trong app.py
# ---------------------------------------------------------------------------
def init_warehouse3d_tables(cursor):
    # warehouse_floors: 1 CỬA HÀNG CÓ THỂ CÓ NHIỀU LẦU (mỗi lầu 1 dòng, kích
    # thước sàn riêng). Phiên bản trước chỉ có 1 sàn / cửa hàng với store_code
    # làm PRIMARY KEY - khối DO $$ bên dưới nâng cấp DB cũ đó lên mô hình
    # nhiều lầu (thêm id/name/floor_order, đổi PK sang id), dòng cũ tự thành
    # lầu "Tầng trệt" thứ tự 1 - không mất dữ liệu, kệ cũ vẫn thấy như trước.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warehouse_floors (
            id SERIAL PRIMARY KEY,
            store_code VARCHAR(20) NOT NULL,
            name VARCHAR(100) NOT NULL DEFAULT 'Tầng trệt',
            floor_order INTEGER NOT NULL DEFAULT 1,
            floor_width NUMERIC NOT NULL DEFAULT 20,
            floor_depth NUMERIC NOT NULL DEFAULT 15,
            updated_by VARCHAR(50),
            updated_at TIMESTAMP
        )
    ''')
    cursor.execute('''
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'warehouse_floors' AND column_name = 'id'
            ) THEN
                ALTER TABLE warehouse_floors ADD COLUMN id SERIAL;
                ALTER TABLE warehouse_floors ADD COLUMN name VARCHAR(100) NOT NULL DEFAULT 'Tầng trệt';
                ALTER TABLE warehouse_floors ADD COLUMN floor_order INTEGER NOT NULL DEFAULT 1;
                ALTER TABLE warehouse_floors DROP CONSTRAINT warehouse_floors_pkey;
                ALTER TABLE warehouse_floors ADD PRIMARY KEY (id);
            END IF;
        END $$;
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_warehouse_floors_store ON warehouse_floors(store_code)')
    # Hình dạng sàn (đa giác) - NULL = hình chữ nhật floor_width x floor_depth như cũ.
    cursor.execute('ALTER TABLE warehouse_floors ADD COLUMN IF NOT EXISTS floor_shape TEXT')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warehouse_shelves (
            id SERIAL PRIMARY KEY,
            store_code VARCHAR(20) NOT NULL,
            floor_id INTEGER REFERENCES warehouse_floors(id) ON DELETE CASCADE,
            code VARCHAR(50) NOT NULL,
            label VARCHAR(100),
            shelf_type VARCHAR(20) NOT NULL DEFAULT 'shelf',
            pos_x NUMERIC NOT NULL DEFAULT 0,
            pos_z NUMERIC NOT NULL DEFAULT 0,
            width NUMERIC NOT NULL DEFAULT 1.2,
            height NUMERIC NOT NULL DEFAULT 2,
            depth NUMERIC NOT NULL DEFAULT 0.6,
            rotation_y NUMERIC NOT NULL DEFAULT 0,
            levels INTEGER NOT NULL DEFAULT 4,
            cells_per_level INTEGER NOT NULL DEFAULT 1,
            slots_per_cell INTEGER NOT NULL DEFAULT 1,
            color VARCHAR(20),
            created_by VARCHAR(50),
            updated_by VARCHAR(50),
            created_at TIMESTAMP,
            updated_at TIMESTAMP,
            UNIQUE(store_code, code)
        )
    ''')
    # DB đã tạo từ phiên bản trước (chưa có ô/ngăn, chưa có nhiều lầu) -> thêm
    # các cột mới, mặc định 1 ô / 1 ngăn nên kệ cũ vẫn chạy y như cũ.
    cursor.execute('ALTER TABLE warehouse_shelves ADD COLUMN IF NOT EXISTS cells_per_level INTEGER NOT NULL DEFAULT 1')
    cursor.execute('ALTER TABLE warehouse_shelves ADD COLUMN IF NOT EXISTS slots_per_cell INTEGER NOT NULL DEFAULT 1')
    cursor.execute('ALTER TABLE warehouse_shelves ADD COLUMN IF NOT EXISTS floor_id INTEGER REFERENCES warehouse_floors(id) ON DELETE CASCADE')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_warehouse_shelves_store ON warehouse_shelves(store_code)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_warehouse_shelves_floor ON warehouse_shelves(floor_id)')

    # Mỗi cửa hàng phải có ÍT NHẤT 1 lầu. Cửa hàng nào chưa từng lưu sàn kho
    # (cài mới) sẽ tự được tạo lầu mặc định khi gọi API lần đầu (xem
    # _ensure_floors()); ở đây chỉ xử lý cửa hàng đã có kệ nhưng kệ đó chưa
    # gắn floor_id (nâng cấp từ DB cũ 1-sàn) - tạo lầu "Tầng trệt" rồi gán
    # toàn bộ kệ hiện có của cửa hàng đó vào lầu này.
    cursor.execute('''
        INSERT INTO warehouse_floors (store_code, name, floor_order, floor_width, floor_depth)
        SELECT DISTINCT s.store_code, 'Tầng trệt', 1,
               COALESCE(f.floor_width, 20), COALESCE(f.floor_depth, 15)
        FROM warehouse_shelves s
        LEFT JOIN warehouse_floors f ON f.store_code = s.store_code
        WHERE s.floor_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM warehouse_floors f2 WHERE f2.store_code = s.store_code)
    ''')
    cursor.execute('''
        UPDATE warehouse_shelves s
        SET floor_id = (
            SELECT f.id FROM warehouse_floors f
            WHERE f.store_code = s.store_code
            ORDER BY f.floor_order, f.id LIMIT 1
        )
        WHERE s.floor_id IS NULL
    ''')

    # Vị trí chi tiết = (kệ, tầng, ô, ngăn). Ràng buộc duy nhất được tạo bằng
    # UNIQUE INDEX (bên dưới) thay vì UNIQUE(...) trong CREATE TABLE, để dùng
    # chung được cho cả DB mới lẫn DB cũ cần nâng cấp.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warehouse_shelf_items (
            id SERIAL PRIMARY KEY,
            shelf_id INTEGER NOT NULL REFERENCES warehouse_shelves(id) ON DELETE CASCADE,
            level_index INTEGER NOT NULL DEFAULT 1,
            cell_index INTEGER NOT NULL DEFAULT 1,
            slot_index INTEGER NOT NULL DEFAULT 1,
            part_code VARCHAR(100) NOT NULL,
            updated_by VARCHAR(50),
            updated_at TIMESTAMP
        )
    ''')
    cursor.execute('ALTER TABLE warehouse_shelf_items ADD COLUMN IF NOT EXISTS cell_index INTEGER NOT NULL DEFAULT 1')
    cursor.execute('ALTER TABLE warehouse_shelf_items ADD COLUMN IF NOT EXISTS slot_index INTEGER NOT NULL DEFAULT 1')
    # Bỏ ràng buộc UNIQUE(shelf_id, level_index, part_code) của phiên bản cũ
    # (đúng 3 cột) - nếu giữ lại thì không thể gán cùng 1 mã hàng vào 2 ô
    # khác nhau của cùng 1 tầng. Chỉ chạy DROP khi thật sự còn ràng buộc đó,
    # để những lần khởi động sau không phải xin khoá bảng vô ích.
    cursor.execute('''
        DO $$
        DECLARE c RECORD;
        BEGIN
            FOR c IN
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                WHERE rel.relname = 'warehouse_shelf_items'
                  AND rel.relnamespace = current_schema()::regnamespace
                  AND con.contype = 'u'
                  AND array_length(con.conkey, 1) = 3
            LOOP
                EXECUTE 'ALTER TABLE warehouse_shelf_items DROP CONSTRAINT ' || quote_ident(c.conname);
            END LOOP;
        END $$
    ''')
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS uq_wh_shelf_items_pos
        ON warehouse_shelf_items(shelf_id, level_index, cell_index, slot_index, part_code)
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_wh_shelf_items_shelf ON warehouse_shelf_items(shelf_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_wh_shelf_items_part ON warehouse_shelf_items(part_code)')

    # Số ngăn RIÊNG của từng ô (chỉ lưu ô khác mặc định slots_per_cell của kệ).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warehouse_shelf_cells (
            shelf_id INTEGER NOT NULL REFERENCES warehouse_shelves(id) ON DELETE CASCADE,
            level_index INTEGER NOT NULL,
            cell_index INTEGER NOT NULL,
            slots INTEGER NOT NULL,
            PRIMARY KEY (shelf_id, level_index, cell_index)
        )
    ''')


# ---------------------------------------------------------------------------
# HELPERS DÙNG CHUNG
# ---------------------------------------------------------------------------
def _check_store_access(cursor, store_code):
    """Trả về (ok, error_response_or_None). role='store' chỉ truy cập đúng
    cửa hàng mình; role='admin' truy cập được mọi cửa hàng hợp lệ."""
    if 'user' not in session:
        return False, (jsonify({'error': 'Unauthorized'}), 401)
    role = session['role']
    if role == 'store':
        if store_code != session['store_code']:
            return False, (jsonify({'error': 'Forbidden'}), 403)
        return True, None
    if role != 'admin':
        return False, (jsonify({'error': 'Forbidden'}), 403)
    if store_code not in _valid_store_codes(cursor):
        return False, (jsonify({'error': 'Cửa hàng không hợp lệ.'}), 400)
    return True, None


_MAX_FLOOR_POINTS = 60    # số đỉnh tối đa của 1 sàn đa giác
_MAX_FLOOR_SIZE = 200     # mét - khớp giới hạn rộng/sâu của sàn chữ nhật


def _load_shape(raw):
    """Đọc cột floor_shape (chuỗi JSON) -> [[x, z], ...] hoặc None (sàn chữ nhật)."""
    if not raw:
        return None
    try:
        pts = [[float(p[0]), float(p[1])] for p in json.loads(raw)]
    except (ValueError, TypeError, IndexError, KeyError):
        return None
    return pts if len(pts) >= 3 else None


def _orient(p, q, r):
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _on_segment(p, q, r):
    """r nằm trong hộp bao của đoạn pq (chỉ dùng khi đã biết 3 điểm thẳng hàng)."""
    return (min(p[0], q[0]) <= r[0] <= max(p[0], q[0])
            and min(p[1], q[1]) <= r[1] <= max(p[1], q[1]))


def _segments_intersect(p1, p2, p3, p4):
    d1, d2 = _orient(p3, p4, p1), _orient(p3, p4, p2)
    d3, d4 = _orient(p1, p2, p3), _orient(p1, p2, p4)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return ((d1 == 0 and _on_segment(p3, p4, p1)) or (d2 == 0 and _on_segment(p3, p4, p2))
            or (d3 == 0 and _on_segment(p1, p2, p3)) or (d4 == 0 and _on_segment(p1, p2, p4)))


def _point_in_polygon(x, z, pts):
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, zi = pts[i]
        xj, zj = pts[j]
        if (zi > z) != (zj > z) and x < (xj - xi) * (z - zi) / (zj - zi) + xi:
            inside = not inside
        j = i
    return inside


def _parse_floor_shape(raw):
    """Kiểm tra danh sách đỉnh sàn client gửi lên. Trả về (points, error).
    Yêu cầu: 3-60 đỉnh, toạ độ 0..200 m, không có 2 đỉnh liền kề trùng nhau,
    các cạnh không tự cắt nhau, diện tích tối thiểu 1 m2."""
    if not isinstance(raw, list):
        return None, 'Hình dạng sàn không hợp lệ.'
    pts = []
    for p in raw:
        try:
            x, z = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            return None, 'Toạ độ đỉnh sàn không hợp lệ.'
        if not (math.isfinite(x) and math.isfinite(z)) or not (0 <= x <= _MAX_FLOOR_SIZE and 0 <= z <= _MAX_FLOOR_SIZE):
            return None, f'Toạ độ đỉnh sàn phải từ 0 đến {_MAX_FLOOR_SIZE} mét.'
        pts.append([round(x, 2), round(z, 2)])
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()  # người dùng lặp lại đỉnh đầu để "đóng" đa giác
    if len(pts) < 3 or len(pts) > _MAX_FLOOR_POINTS:
        return None, f'Sàn kho cần từ 3 đến {_MAX_FLOOR_POINTS} đỉnh.'
    n = len(pts)
    for i in range(n):
        if pts[i] == pts[(i + 1) % n]:
            return None, 'Có 2 đỉnh liền kề trùng nhau.'
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # 2 cạnh kề nhau luôn chung 1 đỉnh
            if _segments_intersect(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n]):
                return None, 'Các cạnh của sàn đang cắt nhau - kiểm tra lại thứ tự các đỉnh.'
    area = abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))) / 2
    if area < 1:
        return None, 'Diện tích sàn quá nhỏ (tối thiểu 1 m²).'
    return pts, None


def _read_floor_geometry(data, current=None):
    """Đọc kích thước / hình dạng sàn từ payload. Trả về (width, depth, shape, error).
    shape = list đỉnh hoặc None (hình chữ nhật). Quy tắc:
      - 'shape' là list  -> sàn đa giác; rộng/sâu = hộp bao ngoài (từ gốc 0,0)
      - 'shape' = null, hoặc chỉ gửi width/depth -> sàn chữ nhật (thay thế đa giác cũ)
      - không gửi gì về kích thước (vd chỉ đổi tên lầu) -> giữ nguyên hiện tại.
    current: dòng warehouse_floors hiện có khi SỬA."""
    if data.get('shape') is not None:
        shape, err = _parse_floor_shape(data['shape'])
        if err:
            return None, None, None, err
        width = max(p[0] for p in shape)
        depth = max(p[1] for p in shape)
    else:
        if current is not None and not any(k in data for k in ('shape', 'width', 'depth')):
            return float(current['floor_width']), float(current['floor_depth']), _load_shape(current['floor_shape']), None
        try:
            width = float(data.get('width', current['floor_width'] if current is not None else 20))
            depth = float(data.get('depth', current['floor_depth'] if current is not None else 15))
        except (TypeError, ValueError):
            return None, None, None, 'Kích thước sàn kho không hợp lệ.'
        shape = None
    if not (2 <= width <= _MAX_FLOOR_SIZE and 2 <= depth <= _MAX_FLOOR_SIZE):
        return None, None, None, f'Kích thước sàn kho phải từ 2 đến {_MAX_FLOOR_SIZE} mét.'
    return width, depth, shape, None


def _floor_to_dict(row):
    return {
        'id': row['id'],
        'name': row['name'],
        'floor_order': row['floor_order'],
        'width': float(row['floor_width']),
        'depth': float(row['floor_depth']),
        'shape': _load_shape(row['floor_shape']),
    }


def _ensure_floors(cursor, store_code):
    """Trả về danh sách lầu (đã sắp xếp) của 1 cửa hàng, tự tạo lầu mặc định
    "Tầng trệt" (20x15) nếu cửa hàng đó chưa có lầu nào (cài mới / cửa hàng
    chưa từng dùng sơ đồ kho)."""
    cursor.execute(
        'SELECT * FROM warehouse_floors WHERE store_code = %s ORDER BY floor_order, id', (store_code,)
    )
    rows = cursor.fetchall()
    if rows:
        return rows
    cursor.execute('''
        INSERT INTO warehouse_floors (store_code, name, floor_order, floor_width, floor_depth)
        VALUES (%s, 'Tầng trệt', 1, 20, 15)
        RETURNING *
    ''', (store_code,))
    row = cursor.fetchone()
    cursor.connection.commit()
    return [row]


def _get_floor(cursor, store_code, floor_id):
    """Lấy 1 lầu cụ thể của đúng cửa hàng, hoặc None nếu không có / sai cửa hàng."""
    cursor.execute(
        'SELECT * FROM warehouse_floors WHERE id = %s AND store_code = %s', (floor_id, store_code)
    )
    return cursor.fetchone()


def _shelf_to_dict(row, overrides=None):
    """overrides: {(level, cell): slots} - số ngăn riêng của từng ô (nếu có),
    trả ra client dạng {"level:cell": slots}."""
    return {
        'id': row['id'],
        'floor_id': row['floor_id'],
        'code': row['code'],
        'label': row['label'],
        'shelf_type': row['shelf_type'],
        'pos_x': float(row['pos_x']),
        'pos_z': float(row['pos_z']),
        'width': float(row['width']),
        'height': float(row['height']),
        'depth': float(row['depth']),
        'rotation_y': float(row['rotation_y']),
        'levels': row['levels'],
        'cells_per_level': row['cells_per_level'],
        'slots_per_cell': row['slots_per_cell'],
        'cell_slots': {f'{lv}:{ce}': n for (lv, ce), n in (overrides or {}).items()},
        'color': row['color'],
        'updated_at': format_vi_datetime(row['updated_at']) if row.get('updated_at') else None,
    }


def _validate_shelf_payload(data, floor_width, floor_depth, existing=None):
    """Kiểm tra dữ liệu kệ gửi lên. Trả về (dict đã làm sạch, error_message
    hoặc None). Chặn bằng kiểm tra biên KHÔNG tính đến góc xoay (đơn giản
    hoá) - chỉ để tránh nhập số vô lý / đặt kệ ra ngoài sàn kho quá rõ,
    không đảm bảo tuyệt đối không chồng lấn khi kệ bị xoay.

    existing: dòng kệ hiện có khi SỬA - nếu client không gửi cells_per_level/
    slots_per_cell (vd trang cũ còn nằm trong cache trình duyệt) thì GIỮ NGUYÊN
    giá trị hiện có thay vì tụt về 1 (tránh vô tình gỡ hết mã hàng ở ô/ngăn)."""
    code = (data.get('code') or '').strip()
    if not code:
        return None, 'Vui lòng nhập mã kệ.'
    if len(code) > 50:
        return None, 'Mã kệ quá dài (tối đa 50 ký tự).'

    shelf_type = (data.get('shelf_type') or 'shelf').strip()
    if shelf_type not in _VALID_SHELF_TYPES:
        return None, 'Loại kệ không hợp lệ.'

    def _num(key, default, min_v, max_v):
        try:
            v = float(data.get(key, default))
        except (TypeError, ValueError):
            return None
        if v < min_v or v > max_v:
            return None
        return v

    width = _num('width', 1.2, 0.1, _MAX_DIM)
    height = _num('height', 2, 0.1, _MAX_DIM)
    depth = _num('depth', 0.6, 0.1, _MAX_DIM)
    pos_x = _num('pos_x', floor_width / 2, -_MAX_DIM, _MAX_DIM + floor_width)
    pos_z = _num('pos_z', floor_depth / 2, -_MAX_DIM, _MAX_DIM + floor_depth)
    try:
        rotation_y = float(data.get('rotation_y', 0))
    except (TypeError, ValueError):
        rotation_y = 0

    if None in (width, height, depth, pos_x, pos_z):
        return None, 'Kích thước hoặc vị trí không hợp lệ.'

    levels = 1
    cells_per_level = 1
    slots_per_cell = 1
    if shelf_type == 'shelf':
        try:
            levels = int(data.get('levels', 4))
        except (TypeError, ValueError):
            return None, 'Số tầng không hợp lệ.'
        if levels < 1 or levels > _MAX_LEVELS:
            return None, f'Số tầng phải từ 1 đến {_MAX_LEVELS}.'

        try:
            cells_per_level = int(data.get('cells_per_level', existing['cells_per_level'] if existing else 1))
            slots_per_cell = int(data.get('slots_per_cell', existing['slots_per_cell'] if existing else 1))
        except (TypeError, ValueError):
            return None, 'Số ô / số ngăn không hợp lệ.'
        if cells_per_level < 1 or cells_per_level > _MAX_CELLS:
            return None, f'Số ô mỗi tầng phải từ 1 đến {_MAX_CELLS}.'
        if slots_per_cell < 1 or slots_per_cell > _MAX_SLOTS:
            return None, f'Số ngăn mỗi ô phải từ 1 đến {_MAX_SLOTS}.'

    color = (data.get('color') or '').strip() or None
    if color and not (color.startswith('#') and len(color) in (4, 7)):
        color = None

    label = (data.get('label') or '').strip() or None

    return {
        'code': code,
        'label': label,
        'shelf_type': shelf_type,
        'pos_x': pos_x,
        'pos_z': pos_z,
        'width': width,
        'height': height,
        'depth': depth,
        'rotation_y': rotation_y,
        'levels': levels,
        'cells_per_level': cells_per_level,
        'slots_per_cell': slots_per_cell,
        'color': color,
    }, None


def _fetch_cell_overrides(cursor, shelf_id):
    """{(level, cell): slots} - số ngăn riêng của các ô khác mặc định."""
    cursor.execute(
        'SELECT level_index, cell_index, slots FROM warehouse_shelf_cells WHERE shelf_id = %s',
        (shelf_id,)
    )
    return {(r['level_index'], r['cell_index']): r['slots'] for r in cursor.fetchall()}


def _slots_for(shelf_type, default_slots, overrides, level, cell):
    """Số ngăn của 1 ô: số riêng của ô đó nếu có, không thì mặc định của kệ.
    Khu để sàn / vách ngăn luôn chỉ có 1 ngăn."""
    if shelf_type != 'shelf':
        return 1
    return overrides.get((level, cell), default_slots)


def _location_3_label(shelf_type, cell_index, slot_index):
    """Nhãn ghi vào location_3 (part_locations). Chỉ loại 'shelf' mới có
    ô/ngăn; loại khác trả None nghĩa là KHÔNG đụng tới location_3."""
    if shelf_type != 'shelf':
        return None
    return f'Ô {cell_index} - Ngăn {slot_index}'


def _sync_legacy_location(cursor, store_code, part_code, shelf_code, level_index, actor, loc3=None):
    """Đồng bộ 1 chiều sang bảng part_locations cũ (xem giải thích ở đầu
    file) để các màn hình tra cứu vị trí hiện có tự hiển thị đúng.
    loc3=None -> giữ nguyên location_3 hiện có."""
    now = vn_now()
    cursor.execute('''
        INSERT INTO part_locations (store_code, part_code, location_1, location_2, location_3, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (store_code, part_code) DO UPDATE SET
            location_1 = EXCLUDED.location_1,
            location_2 = EXCLUDED.location_2,
            location_3 = COALESCE(EXCLUDED.location_3, part_locations.location_3),
            updated_by = EXCLUDED.updated_by,
            updated_at = EXCLUDED.updated_at
    ''', (store_code, part_code, shelf_code, f'Tầng {level_index}', loc3, actor, now))


def _clear_legacy_location(cursor, store_code, part_code, shelf_code, level_index, loc3=None):
    """Chỉ xoá location_1/location_2 trong part_locations nếu chúng vẫn
    ĐANG khớp đúng kệ/tầng vừa gỡ (tránh xoá nhầm giá trị người dùng đã tự
    sửa tay sau khi gán qua sơ đồ 3D). location_3 chỉ bị xoá khi nó vẫn đúng
    bằng nhãn Ô/Ngăn (loc3) vừa gỡ."""
    cursor.execute('''
        UPDATE part_locations
        SET location_3 = CASE WHEN location_3 = %s THEN NULL ELSE location_3 END,
            location_1 = NULL, location_2 = NULL
        WHERE store_code = %s AND part_code = %s
          AND location_1 = %s AND location_2 = %s
    ''', (loc3, store_code, part_code, shelf_code, f'Tầng {level_index}'))


def _prune_orphans(cursor, store_code, shelf_id, old_code, old_type,
                   new_type, levels, cells, default_slots):
    """Sau khi đổi cấu trúc kệ (giảm số tầng / ô / ngăn, hoặc đổi loại kệ),
    gỡ (kèm đồng bộ ngược part_locations) mọi mã hàng đang nằm ở vị trí không
    còn tồn tại, và xoá các số-ngăn-riêng-của-ô nằm ngoài cấu trúc mới - để
    không có dữ liệu \"ma\" (vd Ô 5 có hàng nhưng kệ chỉ còn 3 ô).
    Gọi TRƯỚC khi UPDATE mã kệ (old_code) để xoá đúng location_1 cũ.
    Trả về số mã hàng đã bị gỡ."""
    if new_type != 'shelf':
        cursor.execute('DELETE FROM warehouse_shelf_cells WHERE shelf_id = %s', (shelf_id,))
    else:
        cursor.execute(
            'DELETE FROM warehouse_shelf_cells WHERE shelf_id = %s AND (level_index > %s OR cell_index > %s)',
            (shelf_id, levels, cells)
        )
    overrides = _fetch_cell_overrides(cursor, shelf_id)

    cursor.execute(
        'SELECT id, level_index, cell_index, slot_index, part_code FROM warehouse_shelf_items WHERE shelf_id = %s',
        (shelf_id,)
    )
    removed = 0
    for it in cursor.fetchall():
        allowed = _slots_for(new_type, default_slots, overrides, it['level_index'], it['cell_index'])
        if (new_type in _NON_STORAGE_TYPES or it['level_index'] > levels
                or it['cell_index'] > cells or it['slot_index'] > allowed):
            _clear_legacy_location(
                cursor, store_code, it['part_code'], old_code, it['level_index'],
                _location_3_label(old_type, it['cell_index'], it['slot_index'])
            )
            cursor.execute('DELETE FROM warehouse_shelf_items WHERE id = %s', (it['id'],))
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# TRANG HTML
# ---------------------------------------------------------------------------
@warehouse3d_bp.route('/kho-3d')
def warehouse3d_page():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template(
        'warehouse3d.html',
        user=session['user'],
        full_name=session.get('full_name') or session['user'],
        role=session['role'],
        store_code=session['store_code'],
    )


# ---------------------------------------------------------------------------
# API: DANH SÁCH CỬA HÀNG (cho admin chọn kho cần xem)
# ---------------------------------------------------------------------------
@warehouse3d_bp.route('/api/warehouse3d/stores', methods=['GET'])
def wh3d_stores():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor()
    if session['role'] == 'admin':
        stores = sorted(_valid_store_codes(cursor))
    else:
        stores = [session['store_code']]
    cursor.close()
    return jsonify({'success': True, 'stores': stores})


# ---------------------------------------------------------------------------
# API: DANH SÁCH LẦU CỦA 1 CỬA HÀNG (thêm / sửa / xoá)
# ---------------------------------------------------------------------------
@warehouse3d_bp.route('/api/warehouse3d/<store_code>/floors', methods=['GET'])
def wh3d_list_floors(store_code):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err
    floors = _ensure_floors(cursor, store_code)
    cursor.close()
    return jsonify({'success': True, 'floors': [_floor_to_dict(r) for r in floors]})


@warehouse3d_bp.route('/api/warehouse3d/<store_code>/floors', methods=['POST'])
def wh3d_create_floor(store_code):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        cursor.close()
        return jsonify({'error': 'Vui lòng nhập tên lầu.'}), 400
    if len(name) > 100:
        cursor.close()
        return jsonify({'error': 'Tên lầu quá dài (tối đa 100 ký tự).'}), 400
    width, depth, shape, geo_err = _read_floor_geometry(data)
    if geo_err:
        cursor.close()
        return jsonify({'error': geo_err}), 400

    existing = _ensure_floors(cursor, store_code)
    next_order = max((r['floor_order'] for r in existing), default=0) + 1
    now = vn_now()
    actor = _current_actor_name()
    cursor.execute('''
        INSERT INTO warehouse_floors (store_code, name, floor_order, floor_width, floor_depth, floor_shape, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
    ''', (store_code, name, next_order, width, depth, json.dumps(shape) if shape else None, actor, now))
    row = cursor.fetchone()
    db.commit()
    cursor.close()
    return jsonify({'success': True, 'floor': _floor_to_dict(row)})


@warehouse3d_bp.route('/api/warehouse3d/<store_code>/floors/<int:floor_id>', methods=['PUT'])
def wh3d_update_floor(store_code, floor_id):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    floor = _get_floor(cursor, store_code, floor_id)
    if not floor:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy lầu.'}), 404

    data = request.json or {}
    name = (data.get('name') or '').strip() or floor['name']
    if len(name) > 100:
        cursor.close()
        return jsonify({'error': 'Tên lầu quá dài (tối đa 100 ký tự).'}), 400
    width, depth, shape, geo_err = _read_floor_geometry(data, floor)
    if geo_err:
        cursor.close()
        return jsonify({'error': geo_err}), 400

    now = vn_now()
    actor = _current_actor_name()
    cursor.execute('''
        UPDATE warehouse_floors SET name = %s, floor_width = %s, floor_depth = %s,
            floor_shape = %s, updated_by = %s, updated_at = %s
        WHERE id = %s
        RETURNING *
    ''', (name, width, depth, json.dumps(shape) if shape else None, actor, now, floor_id))
    row = cursor.fetchone()

    # Kệ nào có tâm nằm NGOÀI hình dạng sàn mới thì báo cho client cảnh báo
    # (không tự dời/xoá kệ - người dùng tự kéo lại vào trong sàn).
    outside_shelves = []
    if shape:
        cursor.execute('SELECT code, pos_x, pos_z FROM warehouse_shelves WHERE floor_id = %s ORDER BY code', (floor_id,))
        outside_shelves = [
            r['code'] for r in cursor.fetchall()
            if not _point_in_polygon(float(r['pos_x']), float(r['pos_z']), shape)
        ]
    db.commit()
    cursor.close()
    return jsonify({'success': True, 'floor': _floor_to_dict(row), 'outside_shelves': outside_shelves})


@warehouse3d_bp.route('/api/warehouse3d/<store_code>/floors/<int:floor_id>', methods=['DELETE'])
def wh3d_delete_floor(store_code, floor_id):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    floor = _get_floor(cursor, store_code, floor_id)
    if not floor:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy lầu.'}), 404

    all_floors = _ensure_floors(cursor, store_code)
    if len(all_floors) <= 1:
        cursor.close()
        return jsonify({'error': 'Kho phải có ít nhất 1 lầu, không thể xoá lầu cuối cùng.'}), 400

    # Gỡ đồng bộ ngược part_locations cho mọi mã hàng đang nằm trên các kệ
    # của lầu này TRƯỚC khi xoá (ON DELETE CASCADE sẽ tự xoá kệ/ô/mã hàng).
    cursor.execute('SELECT * FROM warehouse_shelves WHERE floor_id = %s', (floor_id,))
    shelves_on_floor = cursor.fetchall()
    for shelf in shelves_on_floor:
        cursor.execute(
            'SELECT level_index, cell_index, slot_index, part_code FROM warehouse_shelf_items WHERE shelf_id = %s',
            (shelf['id'],)
        )
        for it in cursor.fetchall():
            _clear_legacy_location(
                cursor, store_code, it['part_code'], shelf['code'], it['level_index'],
                _location_3_label(shelf['shelf_type'], it['cell_index'], it['slot_index'])
            )

    cursor.execute('DELETE FROM warehouse_floors WHERE id = %s', (floor_id,))
    db.commit()
    cursor.close()
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# API: TOÀN BỘ SƠ ĐỒ (sàn + danh sách kệ + mã hàng theo từng tầng) CỦA 1 LẦU
# ---------------------------------------------------------------------------
@warehouse3d_bp.route('/api/warehouse3d/<store_code>/layout', methods=['GET'])
def wh3d_get_layout(store_code):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    floors = _ensure_floors(cursor, store_code)
    floor_id_param = request.args.get('floor_id', type=int)
    floor_row = next((f for f in floors if f['id'] == floor_id_param), None) if floor_id_param else None
    if floor_row is None:
        floor_row = floors[0]
    floor = _floor_to_dict(floor_row)

    cursor.execute(
        'SELECT * FROM warehouse_shelves WHERE store_code = %s AND floor_id = %s ORDER BY code',
        (store_code, floor_row['id'])
    )
    shelf_rows = cursor.fetchall()
    shelves = {row['id']: _shelf_to_dict(row) for row in shelf_rows}
    for s in shelves.values():
        s['items'] = []

    if shelves:
        # Số ngăn riêng của từng ô (nếu có) - gom 1 query cho mọi kệ.
        cursor.execute('''
            SELECT shelf_id, level_index, cell_index, slots
            FROM warehouse_shelf_cells
            WHERE shelf_id = ANY(%s)
        ''', (list(shelves.keys()),))
        for r in cursor.fetchall():
            shelves[r['shelf_id']]['cell_slots'][f"{r['level_index']}:{r['cell_index']}"] = r['slots']

        cursor.execute('''
            SELECT shelf_id, level_index, cell_index, slot_index, part_code
            FROM warehouse_shelf_items
            WHERE shelf_id = ANY(%s)
            ORDER BY level_index, cell_index, slot_index, part_code
        ''', (list(shelves.keys()),))
        item_rows = cursor.fetchall()

        part_codes = list({r['part_code'] for r in item_rows})
        part_names = {}
        if part_codes:
            cursor.execute('''
                SELECT DISTINCT ON (part_code) part_code, part_name
                FROM inventory_items
                WHERE part_code = ANY(%s)
                ORDER BY part_code, id DESC
            ''', (part_codes,))
            part_names = {r['part_code']: r['part_name'] for r in cursor.fetchall()}

        for r in item_rows:
            shelf = shelves.get(r['shelf_id'])
            if not shelf:
                continue
            shelf['items'].append({
                'level_index': r['level_index'],
                'cell_index': r['cell_index'],
                'slot_index': r['slot_index'],
                'part_code': r['part_code'],
                'part_name': part_names.get(r['part_code']),
            })

    cursor.close()
    return jsonify({
        'success': True,
        'store_code': store_code,
        'floor': floor,
        'floors': [_floor_to_dict(f) for f in floors],
        'shelves': list(shelves.values()),
    })


# ---------------------------------------------------------------------------
# API: TẠO KỆ MỚI
# ---------------------------------------------------------------------------
@warehouse3d_bp.route('/api/warehouse3d/<store_code>/shelves', methods=['POST'])
def wh3d_create_shelf(store_code):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    data = request.json or {}
    try:
        floor_id = int(data.get('floor_id'))
    except (TypeError, ValueError):
        cursor.close()
        return jsonify({'error': 'Vui lòng chọn lầu để đặt kệ.'}), 400
    floor_row = _get_floor(cursor, store_code, floor_id)
    if not floor_row:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy lầu.'}), 404
    floor_width = float(floor_row['floor_width'])
    floor_depth = float(floor_row['floor_depth'])

    clean, error = _validate_shelf_payload(data, floor_width, floor_depth)
    if error:
        cursor.close()
        return jsonify({'error': error}), 400

    cursor.execute(
        'SELECT 1 FROM warehouse_shelves WHERE store_code = %s AND code = %s',
        (store_code, clean['code'])
    )
    if cursor.fetchone():
        cursor.close()
        return jsonify({'error': f'Mã kệ "{clean["code"]}" đã tồn tại trong kho này.'}), 400

    now = vn_now()
    actor = _current_actor_name()
    cursor.execute('''
        INSERT INTO warehouse_shelves
            (store_code, floor_id, code, label, shelf_type, pos_x, pos_z, width, height, depth,
             rotation_y, levels, cells_per_level, slots_per_cell, color,
             created_by, updated_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
    ''', (
        store_code, floor_id, clean['code'], clean['label'], clean['shelf_type'],
        clean['pos_x'], clean['pos_z'], clean['width'], clean['height'], clean['depth'],
        clean['rotation_y'], clean['levels'], clean['cells_per_level'], clean['slots_per_cell'],
        clean['color'], actor, actor, now, now,
    ))
    row = cursor.fetchone()
    db.commit()
    cursor.close()

    result = _shelf_to_dict(row)
    result['items'] = []
    return jsonify({'success': True, 'shelf': result})


# ---------------------------------------------------------------------------
# API: SỬA / XOÁ 1 KỆ
# ---------------------------------------------------------------------------
@warehouse3d_bp.route('/api/warehouse3d/<store_code>/shelves/<int:shelf_id>', methods=['PUT'])
def wh3d_update_shelf(store_code, shelf_id):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    cursor.execute(
        'SELECT * FROM warehouse_shelves WHERE id = %s AND store_code = %s', (shelf_id, store_code)
    )
    existing = cursor.fetchone()
    if not existing:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy kệ.'}), 404

    floor_row = _get_floor(cursor, store_code, existing['floor_id'])
    floor_width = float(floor_row['floor_width']) if floor_row else 20.0
    floor_depth = float(floor_row['floor_depth']) if floor_row else 15.0

    data = request.json or {}
    clean, error = _validate_shelf_payload(data, floor_width, floor_depth, existing)
    if error:
        cursor.close()
        return jsonify({'error': error}), 400

    if clean['code'] != existing['code']:
        cursor.execute(
            'SELECT 1 FROM warehouse_shelves WHERE store_code = %s AND code = %s AND id != %s',
            (store_code, clean['code'], shelf_id)
        )
        if cursor.fetchone():
            cursor.close()
            return jsonify({'error': f'Mã kệ "{clean["code"]}" đã tồn tại trong kho này.'}), 400

    # Nếu số tầng / số ô / số ngăn bị giảm (hoặc đổi loại kệ), gỡ (kèm đồng
    # bộ ngược part_locations) các mã hàng đang gán ở những vị trí không còn
    # tồn tại nữa để tránh dữ liệu "ma".
    _prune_orphans(
        cursor, store_code, shelf_id, existing['code'], existing['shelf_type'],
        clean['shelf_type'], clean['levels'], clean['cells_per_level'], clean['slots_per_cell']
    )

    now = vn_now()
    actor = _current_actor_name()
    cursor.execute('''
        UPDATE warehouse_shelves SET
            code = %s, label = %s, shelf_type = %s, pos_x = %s, pos_z = %s,
            width = %s, height = %s, depth = %s, rotation_y = %s, levels = %s,
            cells_per_level = %s, slots_per_cell = %s,
            color = %s, updated_by = %s, updated_at = %s
        WHERE id = %s
        RETURNING *
    ''', (
        clean['code'], clean['label'], clean['shelf_type'], clean['pos_x'], clean['pos_z'],
        clean['width'], clean['height'], clean['depth'], clean['rotation_y'], clean['levels'],
        clean['cells_per_level'], clean['slots_per_cell'],
        clean['color'], actor, now, shelf_id,
    ))
    row = cursor.fetchone()

    # Mã kệ có thể vừa đổi tên -> cập nhật lại location_1 tương ứng trong
    # part_locations cho mọi mã hàng đang gán trên kệ này để không bị lệch.
    if clean['code'] != existing['code']:
        cursor.execute(
            'SELECT level_index, cell_index, slot_index, part_code FROM warehouse_shelf_items WHERE shelf_id = %s',
            (shelf_id,)
        )
        for it in cursor.fetchall():
            _sync_legacy_location(
                cursor, store_code, it['part_code'], clean['code'], it['level_index'], actor,
                _location_3_label(clean['shelf_type'], it['cell_index'], it['slot_index'])
            )

    overrides = _fetch_cell_overrides(cursor, shelf_id)
    db.commit()
    cursor.close()
    return jsonify({'success': True, 'shelf': _shelf_to_dict(row, overrides)})


@warehouse3d_bp.route('/api/warehouse3d/<store_code>/shelves/<int:shelf_id>', methods=['DELETE'])
def wh3d_delete_shelf(store_code, shelf_id):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    cursor.execute(
        'SELECT * FROM warehouse_shelves WHERE id = %s AND store_code = %s', (shelf_id, store_code)
    )
    existing = cursor.fetchone()
    if not existing:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy kệ.'}), 404

    cursor.execute(
        'SELECT level_index, cell_index, slot_index, part_code FROM warehouse_shelf_items WHERE shelf_id = %s',
        (shelf_id,)
    )
    for it in cursor.fetchall():
        _clear_legacy_location(
            cursor, store_code, it['part_code'], existing['code'], it['level_index'],
            _location_3_label(existing['shelf_type'], it['cell_index'], it['slot_index'])
        )

    # ON DELETE CASCADE tự xoá warehouse_shelf_items / warehouse_shelf_cells liên quan.
    cursor.execute('DELETE FROM warehouse_shelves WHERE id = %s', (shelf_id,))
    db.commit()
    cursor.close()
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# API: SỐ NGĂN RIÊNG CỦA TỪNG Ô
# ---------------------------------------------------------------------------
def _parse_position(data, shelf, cursor):
    """Đọc + kiểm tra (tầng, ô, ngăn) từ payload. Trả về ((l, c, s), None)
    hoặc (None, (response, status)). Ngăn mặc định 1, ô mặc định 1 (tương
    thích client cũ chỉ gửi level_index)."""
    def _int(key):
        try:
            return int(data.get(key, 1))
        except (TypeError, ValueError):
            return None

    level, cell, slot = _int('level_index'), _int('cell_index'), _int('slot_index')
    if None in (level, cell, slot):
        return None, (jsonify({'error': 'Tầng / ô / ngăn không hợp lệ.'}), 400)
    if level < 1 or level > shelf['levels']:
        return None, (jsonify({'error': f'Kệ này chỉ có {shelf["levels"]} tầng.'}), 400)
    if cell < 1 or cell > shelf['cells_per_level']:
        return None, (jsonify({'error': f'Kệ này chỉ có {shelf["cells_per_level"]} ô mỗi tầng.'}), 400)
    overrides = _fetch_cell_overrides(cursor, shelf['id'])
    max_slots = _slots_for(shelf['shelf_type'], shelf['slots_per_cell'], overrides, level, cell)
    if slot < 1 or slot > max_slots:
        return None, (jsonify({'error': f'Ô này chỉ có {max_slots} ngăn.'}), 400)
    return (level, cell, slot), None


@warehouse3d_bp.route('/api/warehouse3d/<store_code>/shelves/<int:shelf_id>/cells', methods=['PUT'])
def wh3d_set_cell_slots(store_code, shelf_id):
    """Đặt số ngăn cho 1 ô cụ thể. Nếu bằng mặc định của kệ thì xoá dòng
    riêng (ô quay về dùng mặc định). Giảm số ngăn -> gỡ mã hàng ở các ngăn
    không còn tồn tại."""
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    cursor.execute(
        'SELECT * FROM warehouse_shelves WHERE id = %s AND store_code = %s', (shelf_id, store_code)
    )
    shelf = cursor.fetchone()
    if not shelf:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy kệ.'}), 404
    if shelf['shelf_type'] != 'shelf':
        cursor.close()
        return jsonify({'error': 'Chỉ kệ nhiều tầng mới chia được ô / ngăn.'}), 400

    data = request.json or {}
    try:
        level = int(data.get('level_index'))
        cell = int(data.get('cell_index'))
        slots = int(data.get('slots'))
    except (TypeError, ValueError):
        cursor.close()
        return jsonify({'error': 'Tầng / ô / số ngăn không hợp lệ.'}), 400
    if level < 1 or level > shelf['levels'] or cell < 1 or cell > shelf['cells_per_level']:
        cursor.close()
        return jsonify({'error': 'Tầng hoặc ô không tồn tại trên kệ này.'}), 400
    if slots < 1 or slots > _MAX_SLOTS:
        cursor.close()
        return jsonify({'error': f'Số ngăn phải từ 1 đến {_MAX_SLOTS}.'}), 400

    if slots == shelf['slots_per_cell']:
        cursor.execute(
            'DELETE FROM warehouse_shelf_cells WHERE shelf_id = %s AND level_index = %s AND cell_index = %s',
            (shelf_id, level, cell)
        )
    else:
        cursor.execute('''
            INSERT INTO warehouse_shelf_cells (shelf_id, level_index, cell_index, slots)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (shelf_id, level_index, cell_index) DO UPDATE SET slots = EXCLUDED.slots
        ''', (shelf_id, level, cell, slots))

    removed = _prune_orphans(
        cursor, store_code, shelf_id, shelf['code'], shelf['shelf_type'],
        shelf['shelf_type'], shelf['levels'], shelf['cells_per_level'], shelf['slots_per_cell']
    )
    db.commit()
    cursor.close()
    return jsonify({'success': True, 'removed_items': removed})


# ---------------------------------------------------------------------------
# API: GÁN / GỠ MÃ HÀNG VÀO 1 NGĂN (tầng - ô - ngăn) CỦA KỆ
# ---------------------------------------------------------------------------
@warehouse3d_bp.route('/api/warehouse3d/<store_code>/shelves/<int:shelf_id>/items', methods=['POST'])
def wh3d_assign_item(store_code, shelf_id):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    cursor.execute(
        'SELECT * FROM warehouse_shelves WHERE id = %s AND store_code = %s', (shelf_id, store_code)
    )
    shelf = cursor.fetchone()
    if not shelf:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy kệ.'}), 404
    if shelf['shelf_type'] in _NON_STORAGE_TYPES:
        cursor.close()
        return jsonify({'error': f"Không thể gán mã hàng vào {_NON_STORAGE_LABELS[shelf['shelf_type']]}."}), 400

    data = request.json or {}
    part_code = (data.get('part_code') or '').strip()
    if not part_code:
        cursor.close()
        return jsonify({'error': 'Vui lòng nhập mã hàng.'}), 400
    pos, pos_err = _parse_position(data, shelf, cursor)
    if pos_err:
        cursor.close()
        return pos_err
    level_index, cell_index, slot_index = pos

    # Mã hàng chỉ cần tồn tại ở BẤT KỲ đâu trong hệ thống (giống điều kiện
    # của save_location() trong app.py) - không bắt buộc phải có/còn hàng
    # đúng tại store_code này.
    cursor.execute(
        '''SELECT 1 FROM inventory_items WHERE part_code = %s
           UNION SELECT 1 FROM part_locations WHERE part_code = %s''',
        (part_code, part_code)
    )
    if not cursor.fetchone():
        cursor.close()
        return jsonify({'error': f'Mã hàng "{part_code}" không tồn tại trong danh sách tồn kho admin đã import.'}), 400

    now = vn_now()
    actor = _current_actor_name()
    cursor.execute('''
        INSERT INTO warehouse_shelf_items (shelf_id, level_index, cell_index, slot_index, part_code, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (shelf_id, level_index, cell_index, slot_index, part_code) DO UPDATE SET
            updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
    ''', (shelf_id, level_index, cell_index, slot_index, part_code, actor, now))

    _sync_legacy_location(
        cursor, store_code, part_code, shelf['code'], level_index, actor,
        _location_3_label(shelf['shelf_type'], cell_index, slot_index)
    )

    db.commit()
    cursor.close()
    return jsonify({'success': True})


@warehouse3d_bp.route('/api/warehouse3d/<store_code>/shelves/<int:shelf_id>/items', methods=['DELETE'])
def wh3d_unassign_item(store_code, shelf_id):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    cursor.execute(
        'SELECT * FROM warehouse_shelves WHERE id = %s AND store_code = %s', (shelf_id, store_code)
    )
    shelf = cursor.fetchone()
    if not shelf:
        cursor.close()
        return jsonify({'error': 'Không tìm thấy kệ.'}), 404

    data = request.json or {}
    part_code = (data.get('part_code') or '').strip()
    if not part_code:
        cursor.close()
        return jsonify({'error': 'Dữ liệu không hợp lệ.'}), 400
    try:
        level_index = int(data.get('level_index', 1))
        cell_index = int(data.get('cell_index', 1))
        slot_index = int(data.get('slot_index', 1))
    except (TypeError, ValueError):
        cursor.close()
        return jsonify({'error': 'Tầng / ô / ngăn không hợp lệ.'}), 400

    cursor.execute(
        '''DELETE FROM warehouse_shelf_items
           WHERE shelf_id = %s AND level_index = %s AND cell_index = %s AND slot_index = %s AND part_code = %s''',
        (shelf_id, level_index, cell_index, slot_index, part_code)
    )
    _clear_legacy_location(
        cursor, store_code, part_code, shelf['code'], level_index,
        _location_3_label(shelf['shelf_type'], cell_index, slot_index)
    )
    db.commit()
    cursor.close()
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# API: TÌM MÃ HÀNG ĐANG NẰM Ở KỆ NÀO (dùng để focus trên sơ đồ 3D)
# ---------------------------------------------------------------------------
@warehouse3d_bp.route('/api/warehouse3d/<store_code>/find/<part_code>', methods=['GET'])
def wh3d_find_part(store_code, part_code):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    cursor.execute('''
        SELECT s.id AS shelf_id, s.code AS shelf_code, s.label AS shelf_label,
               s.floor_id, f.name AS floor_name,
               i.level_index, i.cell_index, i.slot_index
        FROM warehouse_shelf_items i
        JOIN warehouse_shelves s ON s.id = i.shelf_id
        LEFT JOIN warehouse_floors f ON f.id = s.floor_id
        WHERE s.store_code = %s AND i.part_code = %s
        ORDER BY s.code, i.level_index, i.cell_index, i.slot_index
    ''', (store_code, part_code.strip()))
    rows = cursor.fetchall()
    cursor.close()
    return jsonify({'success': True, 'results': [dict(r) for r in rows]})


# ---------------------------------------------------------------------------
# API: TÌM NHANH MÃ HÀNG (autocomplete khi gán hàng vào kệ)
# ---------------------------------------------------------------------------
@warehouse3d_bp.route('/api/warehouse3d/<store_code>/search-parts', methods=['GET'])
def wh3d_search_parts(store_code):
    store_code = store_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    ok, err = _check_store_access(cursor, store_code)
    if not ok:
        cursor.close()
        return err

    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        cursor.close()
        return jsonify({'success': True, 'results': []})

    like = f'%{q}%'
    cursor.execute('''
        SELECT DISTINCT ON (part_code) part_code, part_name
        FROM inventory_items
        WHERE part_code ILIKE %s OR part_name ILIKE %s
        ORDER BY part_code
        LIMIT 20
    ''', (like, like))
    rows = cursor.fetchall()
    cursor.close()
    return jsonify({'success': True, 'results': [dict(r) for r in rows]})