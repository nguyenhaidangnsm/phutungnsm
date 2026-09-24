# ----------------------------------------------------------------------------
# TÍNH NĂNG: ĐỀ XUẤT TĂNG GIÁ (theo từng mã hàng) - tách riêng file này để
# khỏi làm app.py dài thêm, giống hệt cách stocktake.py đã làm cho tính năng
# Kiểm Kê Kho. File này import ngược lại get_db/vn_now/_current_actor_name từ
# app, nên PHẢI được import ở app.py SAU KHI những tên đó đã được định nghĩa
# xong (xem dòng "from price_adjustment import ..." ở cuối app.py).
#
# MỤC ĐÍCH NGHIỆP VỤ:
#   - Cho phép BẤT KỲ ai (cả admin lẫn store) đề xuất tăng giá 1 mã hàng bất
#     kỳ: nhập Mã hàng, Tên hàng (tự hiển thị nếu mã đã có trong hệ thống),
#     Thuế (%), Giá đề xuất HVN -> hệ thống tự tính:
#         Giá bán = LÀM TRÒN ĐẾN HÀNG NGHÌN của (Giá đề xuất HVN * Thuế) + Giá đề xuất HVN
#   - Phân loại được mã nào "ĐÃ ĐIỀU CHỈNH" (đã từng được đề xuất tăng giá ít
#     nhất 1 lần) và mã nào "CHƯA ĐIỀU CHỈNH" (có trong danh mục hệ thống -
#     tồn kho hoặc mã mới tự thêm - nhưng chưa từng được đề xuất).
#   - Mã MỚI (chưa có trong bảng tồn kho hệ thống inventory_items) khi được
#     đề xuất lần đầu sẽ TỰ ĐỘNG được lưu vào 1 "danh mục mã mới" riêng
#     (price_adjustment_new_codes) để lần sau nhập lại mã đó, Tên/Thuế tự
#     hiện lại. Khi admin tải file tồn kho mới (upload_inventory) mà trong đó
#     ĐÃ CÓ mã này, mã đó coi như đã "chính thức" vào danh mục hệ thống nên
#     dòng tương ứng trong danh mục mã mới sẽ được tự động dọn bỏ (tránh
#     trùng lặp 2 nơi). Việc dọn dẹp này được làm LƯỜI (lazy): mỗi lần có ai
#     mở/tải danh sách (list_price_adjustments) sẽ tự chạy 1 câu DELETE dọn
#     trùng trước khi trả kết quả - không cần sửa gì vào route
#     upload_inventory() của app.py, giảm rủi ro khi chỉnh sửa app.py (file
#     rất lớn).
# ----------------------------------------------------------------------------

from datetime import datetime

import openpyxl
from flask import Blueprint, request, jsonify, session
from psycopg2.extras import execute_values

from app import get_db, vn_now, _current_actor_name

price_adjustment_bp = Blueprint('price_adjustment', __name__)


def init_price_adjustment_tables(cursor):
    """Tạo các bảng cần thiết cho tính năng Đề Xuất Tăng Giá. Được gọi từ
    init_db() trong app.py (cùng transaction/cursor với các bảng khác), y hệt
    cách init_stocktake_tables(cursor) đang được gọi."""

    # Bảng "danh mục mã mới" - chỉ chứa những mã hàng CHƯA có trong tồn kho
    # hệ thống (inventory_items) tại thời điểm được đề xuất lần đầu. Mỗi mã
    # chỉ có đúng 1 dòng (UPSERT khi đề xuất lại) để lần sau tự hiện lại
    # Tên hàng / Thuế đã nhập trước đó.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_adjustment_new_codes (
            part_code VARCHAR(100) PRIMARY KEY,
            part_name TEXT,
            thue NUMERIC,
            created_by VARCHAR(50),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    ''')

    # Bảng LỊCH SỬ mọi lần đề xuất tăng giá - mỗi dòng là 1 LẦN đề xuất
    # (không ghi đè), vì 1 mã hàng có thể được đề xuất điều chỉnh nhiều lần
    # theo thời gian. Trạng thái "đã điều chỉnh"/"chưa điều chỉnh" của 1 mã
    # hàng được xác định bằng việc mã đó CÓ hay KHÔNG có ít nhất 1 dòng ở
    # đây (xem câu query trong list_price_adjustments()).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_adjustment_proposals (
            id SERIAL PRIMARY KEY,
            part_code VARCHAR(100) NOT NULL,
            part_name TEXT,
            thue NUMERIC NOT NULL,
            gia_de_xuat_hvn NUMERIC NOT NULL,
            gia_ban NUMERIC NOT NULL,
            store_code VARCHAR(20),
            created_by VARCHAR(50),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_adj_proposals_part ON price_adjustment_proposals(part_code)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_adj_proposals_created ON price_adjustment_proposals(created_at DESC)')
    # Index GHÉP (part_code, created_at DESC) - tối ưu riêng cho câu
    # "DISTINCT ON (part_code) ... ORDER BY part_code, created_at DESC" ở CTE
    # `latest` trong list_price_adjustments(): không có index này, Postgres
    # phải tự sort toàn bộ bảng proposals theo (part_code, created_at) mỗi
    # lần tải danh sách; có index này thì chỉ cần index-scan, đặc biệt quan
    # trọng khi bảng phình to sau khi import hàng loạt (hàng chục nghìn dòng
    # / lần import).
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_adj_proposals_part_created ON price_adjustment_proposals(part_code, created_at DESC)')


_GIA_TANG_RATE = 0.05  # Mức % tăng giá MẶC ĐỊNH áp dụng ở Bước 2 của form đề
                        # xuất 1 mã (Giá bán = Giá cũ + X% Giá cũ) khi người
                        # dùng không tự nhập mức khác - xem tham số
                        # "muc_tang_percent" trong price_adjustment_propose().
                        # Đây cũng là ý nghĩa thống nhất của cột "thue" trong
                        # bảng price_adjustment_proposals cho MỌI dòng (thủ
                        # công lẫn import): LUÔN là mức % đã thực sự áp dụng
                        # ở Bước 2, không phải Thuế người dùng gõ ở Bước 1.


def _round_to_thousand(value):
    """Làm tròn đến hàng nghìn (VD: 1607550 -> 1608000), khớp với quy ước
    'GIÁ BÁN (ĐÃ VAT) LÀM TRÒN' luôn là số tròn 1,000đ trong file Excel mẫu
    admin đang dùng."""
    return round(float(value) / 1000.0) * 1000


def _compute_gia_ban(gia_de_xuat_hvn, thue_frac):
    """Gía bán = (Giá đề xuất HVN * Thuế) + Giá đề xuất HVN, làm tròn đến
    hàng nghìn. thue_frac là số thập phân (0.08 = 8%), KHÔNG phải số phần
    trăm nguyên (8). Dùng cho IMPORT HÀNG LOẠT từ file Excel "ĐÃ ĐIỀU
    CHỈNH" (ở đó cột "Giá cũ" trong file + "% tăng giá" đã trực tiếp cho ra
    giá bán lịch sử, đúng 1 bước) - KHÔNG dùng hàm này cho form đề xuất 1 mã
    (xem _compute_gia_cu / _compute_gia_ban_tu_gia_cu bên dưới, dùng công
    thức 2 bước riêng)."""
    raw = (gia_de_xuat_hvn * thue_frac) + gia_de_xuat_hvn
    return _round_to_thousand(raw)


def _compute_gia_cu(gia_de_xuat_hvn, thue_frac):
    """BƯỚC 1 (dùng cho form "Đề Xuất Tăng Giá 1 Mã Hàng"):
    Giá cũ = (Giá đề xuất HVN * Thuế) + Giá đề xuất HVN, làm tròn đến hàng
    nghìn. Đây là giá bán hiện tại (đã gồm thuế) TRƯỚC KHI cộng thêm 5% tăng
    giá ở bước 2."""
    return _compute_gia_ban(gia_de_xuat_hvn, thue_frac)


def _compute_gia_ban_tu_gia_cu(gia_cu, muc_tang_rate=_GIA_TANG_RATE):
    """BƯỚC 2 (dùng cho form "Đề Xuất Tăng Giá 1 Mã Hàng"):
    Giá bán (giá tăng) = (Giá cũ * muc_tang_rate) + Giá cũ, làm tròn đến hàng
    nghìn. muc_tang_rate là số thập phân (0.05 = 5%), do người dùng tự chọn ở
    Bước 2 (mặc định 5% nếu không nhập gì khác) - KHÔNG liên quan tới Thuế
    (%) mà người dùng nhập ở Bước 1, Thuế chỉ dùng để tính ra Giá cũ."""
    raw = (gia_cu * muc_tang_rate) + gia_cu
    return _round_to_thousand(raw)


def _cleanup_new_codes_dedup(cursor):
    """Dọn trùng lặp: xoá khỏi 'danh mục mã mới' những mã hàng mà nay ĐÃ CÓ
    trong tồn kho hệ thống chính thức (do admin tải file tồn kho mới lên có
    chứa mã đó) - lúc này mã hàng nên được coi là thuộc danh mục hệ thống
    (inventory_items), không cần giữ bản ghi tạm ở bảng mã mới nữa."""
    cursor.execute('''
        DELETE FROM price_adjustment_new_codes
        WHERE part_code IN (SELECT DISTINCT part_code FROM inventory_items)
    ''')


def _find_existing_part_name(cursor, part_code):
    """Tìm Tên hàng đã biết cho 1 mã hàng, ưu tiên: đề xuất gần nhất > danh
    mục mã mới > tồn kho hệ thống. Trả về None nếu chưa từng biết Tên hàng
    này ở bất kỳ đâu."""
    cursor.execute('''
        SELECT part_name FROM price_adjustment_proposals
        WHERE part_code = %s AND part_name IS NOT NULL AND part_name <> ''
        ORDER BY created_at DESC LIMIT 1
    ''', (part_code,))
    row = cursor.fetchone()
    if row and row['part_name']:
        return row['part_name']

    cursor.execute('SELECT part_name FROM price_adjustment_new_codes WHERE part_code = %s', (part_code,))
    row = cursor.fetchone()
    if row and row['part_name']:
        return row['part_name']

    cursor.execute('SELECT part_name FROM inventory_items WHERE part_code = %s LIMIT 1', (part_code,))
    row = cursor.fetchone()
    if row and row['part_name']:
        return row['part_name']

    return None


# ----------------------------------------------------------------------------
# TÍNH NĂNG: IMPORT HÀNG LOẠT từ file Excel "ĐÃ ĐIỀU CHỈNH" (file nội bộ
# admin đang dùng để theo dõi các mã đã được tăng giá 5% do HVN thu phí).
# Mục đích: thay vì phải nhập tay từng mã 1 ở form phía trên, admin có thể
# tải thẳng file Excel này lên để nạp hàng loạt vào lịch sử đề xuất
# (price_adjustment_proposals) - các mã này sẽ tự chuyển sang trạng thái
# "ĐÃ ĐIỀU CHỈNH" trong danh sách, kèm đúng NGÀY CẬP NHẬT lấy từ file (không
# phải thời điểm tải file lên) để cột "Ngày Điều Chỉnh" phản ánh đúng lịch sử
# thực tế, và cột "Người Đề Xuất" ghi rõ đây là dữ liệu import + tên admin đã
# thực hiện thao tác (vì bản thân file Excel không có cột định danh người đề
# xuất theo từng dòng).
# ----------------------------------------------------------------------------

_IMPORT_SHEET_NAME_CANDIDATES = ('đã điều chỉnh', 'da dieu chinh')

# Vị trí cột CỐ ĐỊNH trong file Excel mẫu "ĐÃ ĐIỀU CHỈNH ... KHI ĐÃ TẠO
# HÀNG KHẢN..." mà admin dùng để import hàng loạt: cột B = Mã hàng, cột E =
# Giá cũ (có VAT), cột F = % Tăng giá (Thuế). Dùng vị trí cột cố định thay
# vì dò theo tên tiêu đề cho 3 cột này, vì file có nhiều cột trùng/gần
# giống tên (VD cột C cũng chứa Mã hàng nhưng không có tiêu đề) dễ gây dò
# nhầm; Tên hàng/Giá bán/Ngày cập nhật vẫn dò theo tên tiêu đề như cũ vì vị
# trí các cột này có thể đổi chỗ giữa các lần xuất file.
_IMPORT_COL_PART_CODE = openpyxl.utils.column_index_from_string('B')
_IMPORT_COL_GIA_CU = openpyxl.utils.column_index_from_string('E')
_IMPORT_COL_THUE = openpyxl.utils.column_index_from_string('F')


def _norm_header(v):
    return str(v or '').strip().lower()


def _find_header_col(header_cells, keywords):
    """Tìm chỉ số cột (1-based) có nội dung tiêu đề khớp ĐỦ mọi từ khoá
    trong `keywords` (không phân biệt hoa/thường, không phân biệt dấu cách
    thừa). header_cells là dict {col_idx: text_đã_chuẩn_hoá}."""
    for col_idx, text in header_cells.items():
        if all(kw in text for kw in keywords):
            return col_idx
    return None


def _parse_price_adjustment_import(file_storage):
    """Đọc file Excel import, tự dò sheet phù hợp (ưu tiên sheet tên "ĐÃ
    ĐIỀU CHỈNH", nếu không có thì lấy sheet đầu tiên), tự dò dòng tiêu đề và
    các cột cần thiết (Mã hàng, Tên hàng, Giá cũ/Giá đề xuất HVN, % tăng
    giá/Thuế, Giá bán đã làm tròn, Ngày cập nhật). Trả về (rows, skipped_rows,
    sheet_name); rows là list dict đã chuẩn hoá, sẵn sàng để insert."""
    file_storage.stream.seek(0)
    wb = openpyxl.load_workbook(file_storage, data_only=True, read_only=True)

    sheet = None
    for name in wb.sheetnames:
        if _norm_header(name) in _IMPORT_SHEET_NAME_CANDIDATES:
            sheet = wb[name]
            break
    if sheet is None:
        sheet = wb[wb.sheetnames[0]]

    # Dò dòng tiêu đề: quét 10 dòng đầu, lấy dòng đầu tiên có ô chứa "mã hàng".
    header_row_idx = None
    header_cells = {}
    for row in sheet.iter_rows(min_row=1, max_row=10):
        row_map = {}
        for i, c in enumerate(row, start=1):
            if c.value not in (None, ''):
                row_map[i] = _norm_header(c.value)
        if any('mã hàng' in v for v in row_map.values()):
            header_row_idx = row[0].row if hasattr(row[0], 'row') else None
            header_cells = row_map
            break

    if header_row_idx is None:
        raise ValueError('Không tìm thấy dòng tiêu đề (cần có cột "Mã hàng") trong file.')

    col_part_code = _IMPORT_COL_PART_CODE
    col_part_name = _find_header_col(header_cells, ['tên hàng'])
    col_gia_cu = _IMPORT_COL_GIA_CU
    col_thue = _IMPORT_COL_THUE
    col_gia_ban = (_find_header_col(header_cells, ['giá bán', 'làm tròn'])
                   or _find_header_col(header_cells, ['giá bán']))
    col_ngay = (_find_header_col(header_cells, ['ngày cập nhật'])
                or _find_header_col(header_cells, ['ngày']))

    if header_cells.get(col_part_code) is None or 'mã hàng' not in (header_cells.get(col_part_code) or ''):
        raise ValueError('Cột B trong file không phải là "Mã hàng" - kiểm tra lại cấu trúc file.')

    rows = []
    skipped_rows = 0
    for row in sheet.iter_rows(min_row=header_row_idx + 1):
        vals = {i: c.value for i, c in enumerate(row, start=1)}

        part_code = str(vals.get(col_part_code) or '').strip().upper()
        if not part_code:
            continue

        try:
            gia_de_xuat_hvn = float(vals.get(col_gia_cu))
            if gia_de_xuat_hvn <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            skipped_rows += 1
            continue

        try:
            thue_raw = float(vals.get(col_thue))
        except (TypeError, ValueError):
            skipped_rows += 1
            continue
        # Cột % tăng giá trong file lưu dạng thập phân (0.05 = 5%). Đề phòng
        # trường hợp file lưu dạng số nguyên phần trăm (5 nghĩa là 5%), tự
        # quy đổi lại cho khớp quy ước "thue" lưu dạng thập phân trong DB.
        thue_frac = thue_raw / 100.0 if thue_raw > 1 else thue_raw
        if thue_frac < 0 or thue_frac > 1:
            skipped_rows += 1
            continue

        try:
            gia_ban = float(vals.get(col_gia_ban))
            if gia_ban <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            gia_ban = _compute_gia_ban(gia_de_xuat_hvn, thue_frac)

        part_name = str(vals.get(col_part_name) or '').strip() or None

        ngay_raw = vals.get(col_ngay)
        created_at = ngay_raw if isinstance(ngay_raw, datetime) else vn_now()

        rows.append({
            'part_code': part_code,
            'part_name': part_name,
            'thue': thue_frac,
            'gia_de_xuat_hvn': gia_de_xuat_hvn,
            'gia_ban': gia_ban,
            'created_at': created_at,
        })

    return rows, skipped_rows, sheet.title


@price_adjustment_bp.route('/api/price-adjustment/import', methods=['POST'])
def price_adjustment_import():
    """Admin import HÀNG LOẠT các mã đã được đề xuất tăng giá từ 1 file
    Excel (đúng cấu trúc sheet "ĐÃ ĐIỀU CHỈNH" nội bộ). Mỗi dòng hợp lệ được
    lưu thành 1 LẦN đề xuất mới trong lịch sử (giống hệt propose() ở trên,
    chỉ khác là insert hàng loạt bằng execute_values thay vì insert từng
    dòng). KHÔNG giới hạn cho store - chỉ admin mới được import (vì đây là
    thao tác nạp dữ liệu hàng loạt, khác với đề xuất từng mã ở form phía
    trên vốn cho phép cả store)."""
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    excel_file = request.files.get('file')
    if not excel_file:
        return jsonify({'error': 'Vui lòng chọn file Excel để import.'}), 400

    try:
        rows, skipped_rows, sheet_name = _parse_price_adjustment_import(excel_file)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Không đọc được file: {e}'}), 400

    if not rows:
        return jsonify({
            'error': 'Không tìm thấy dòng dữ liệu hợp lệ nào trong file '
                     '(cần đủ Mã hàng, Giá cũ/Giá đề xuất HVN, % tăng giá).'
        }), 400

    actor = _current_actor_name()
    created_by = f'Import Excel ({actor})'

    db = get_db()
    cursor = db.cursor()
    try:
        # Nếu 1 mã hàng xuất hiện nhiều dòng trong file, chỉ giữ dòng CUỐI
        # CÙNG (giống cách import_prices() ở app.py đang xử lý trùng mã) để
        # không lưu nhiều lần đề xuất giống hệt nhau từ cùng 1 lần import.
        dedup = {r['part_code']: r for r in rows}
        rows = list(dedup.values())

        # TỐI ƯU LƯU TRỮ: nếu admin import lại ĐÚNG file này lần nữa (vô
        # tình hoặc để chắc chắn không sót mã), không ghi trùng thêm 1 dòng
        # lịch sử giống hệt cho mỗi mã hàng - so khớp với các dòng do IMPORT
        # tạo ra trước đó (created_by LIKE 'Import Excel%') có CÙNG mã hàng +
        # CÙNG giá + CÙNG ngày điều chỉnh; chỉ những dòng thực sự mới/khác
        # (giá thay đổi, ngày khác, hoặc mã chưa từng import) mới được ghi
        # thêm. Không đụng tới các đề xuất NHẬP TAY (vẫn giữ nguyên hành vi
        # "mỗi lần đề xuất là 1 dòng lịch sử" như trước).
        part_codes = [r['part_code'] for r in rows]
        cursor.execute('''
            SELECT part_code, gia_ban, created_at
            FROM price_adjustment_proposals
            WHERE part_code = ANY(%s) AND created_by LIKE 'Import Excel%%'
        ''', (part_codes,))
        already_imported = {
            (r['part_code'], round(float(r['gia_ban'])), r['created_at'].date())
            for r in cursor.fetchall()
        }
        rows = [
            r for r in rows
            if (r['part_code'], round(float(r['gia_ban'])), r['created_at'].date()) not in already_imported
        ]
        duplicate_skipped = len(part_codes) - len(rows)

        if rows:
            # page_size lớn hơn mặc định (100) để giảm số round-trip DB khi
            # import file lớn (file mẫu ~46,000 dòng) - vẫn dùng execute_values
            # (gửi theo lô) chứ không insert từng dòng 1 (rất chậm với dữ
            # liệu cỡ này).
            execute_values(
                cursor,
                '''INSERT INTO price_adjustment_proposals
                    (part_code, part_name, thue, gia_de_xuat_hvn, gia_ban, store_code, created_by, created_at)
                   VALUES %s''',
                [(r['part_code'], r['part_name'], r['thue'], r['gia_de_xuat_hvn'],
                  r['gia_ban'], None, created_by, r['created_at']) for r in rows],
                page_size=1000
            )

        # Mã nào chưa có trong tồn kho hệ thống -> lưu vào "danh mục mã mới"
        # (giống hệt logic trong price_adjustment_propose()), để lần đề xuất
        # tiếp theo tự điền lại Tên/Thuế.
        cursor.execute('SELECT DISTINCT part_code FROM inventory_items')
        catalog_codes = {r['part_code'] for r in cursor.fetchall()}
        new_code_rows = [
            (r['part_code'], r['part_name'], r['thue'], created_by, r['created_at'])
            for r in rows if r['part_code'] not in catalog_codes
        ]
        if new_code_rows:
            execute_values(
                cursor,
                '''INSERT INTO price_adjustment_new_codes (part_code, part_name, thue, created_by, created_at)
                   VALUES %s
                   ON CONFLICT (part_code) DO UPDATE SET
                       part_name = EXCLUDED.part_name,
                       thue = EXCLUDED.thue''',
                new_code_rows,
                page_size=1000
            )

        db.commit()
        return jsonify({
            'success': True,
            'sheet_name': sheet_name,
            'total_imported': len(rows),
            'duplicate_skipped': duplicate_skipped,
            'skipped_rows': skipped_rows,
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'Lỗi khi lưu dữ liệu import: {e}'}), 500
    finally:
        cursor.close()


@price_adjustment_bp.route('/api/price-adjustment/lookup', methods=['GET'])
def price_adjustment_lookup():
    """Tra cứu nhanh 1 mã hàng khi admin/store gõ vào ô "Mã hàng" của form đề
    xuất: trả về Tên hàng đã biết (nếu có), Thuế đã dùng lần gần nhất (để tự
    điền lại, vẫn cho sửa), mã có thuộc tồn kho hệ thống hay không, mã này
    đã từng được đề xuất tăng giá hay chưa (kèm thông tin lần đề xuất gần
    nhất) để cảnh báo tránh đề xuất trùng, và Giá bán hiện có trong danh mục
    giá (catalog_price) - dùng cho phần "Kiểm Tra Hàng Loạt Mã Hàng" để hiện
    giá ngay cả với mã CHƯA từng được đề xuất tăng giá lần nào."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    part_code = (request.args.get('part_code') or '').strip().upper()
    if not part_code:
        return jsonify({'error': 'Thiếu mã hàng.'}), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT 1 FROM inventory_items WHERE part_code = %s LIMIT 1', (part_code,))
    in_catalog = cursor.fetchone() is not None

    cursor.execute('''
        SELECT part_name, thue, gia_de_xuat_hvn, gia_ban, store_code, created_by, created_at
        FROM price_adjustment_proposals
        WHERE part_code = %s
        ORDER BY created_at DESC LIMIT 1
    ''', (part_code,))
    last_proposal = cursor.fetchone()

    # Giá bán HIỆN CÓ trong danh mục giá (part_prices) - dùng để hiển thị/
    # cộng tổng cho những mã "CHƯA ĐIỀU CHỈNH" (chưa từng có last_proposal
    # nên không có gì để hiện ở đó, nhưng mã vẫn có giá bán hiện tại nếu đã
    # từng được nhập giá qua tính năng khác).
    cursor.execute('SELECT sale_price FROM part_prices WHERE part_code = %s', (part_code,))
    price_row = cursor.fetchone()
    catalog_price = float(price_row['sale_price']) if price_row and price_row['sale_price'] is not None else None

    part_name = _find_existing_part_name(cursor, part_code)

    thue_suggest = None
    if last_proposal and last_proposal['thue'] is not None:
        thue_suggest = float(last_proposal['thue']) * 100  # trả về dạng % (8 nghĩa là 8%) cho khớp ô nhập trên giao diện
    else:
        cursor.execute('SELECT thue FROM price_adjustment_new_codes WHERE part_code = %s', (part_code,))
        row = cursor.fetchone()
        if row and row['thue'] is not None:
            thue_suggest = float(row['thue']) * 100

    cursor.close()

    result = {
        'success': True,
        'part_code': part_code,
        'part_name': part_name,
        'in_catalog': in_catalog,
        'thue_suggest': thue_suggest,
        'already_adjusted': last_proposal is not None,
        'last_proposal': None,
        'catalog_price': catalog_price,
    }
    if last_proposal:
        result['last_proposal'] = {
            'part_name': last_proposal['part_name'],
            'thue': float(last_proposal['thue']) * 100 if last_proposal['thue'] is not None else None,
            'gia_de_xuat_hvn': float(last_proposal['gia_de_xuat_hvn']) if last_proposal['gia_de_xuat_hvn'] is not None else None,
            'gia_ban': float(last_proposal['gia_ban']) if last_proposal['gia_ban'] is not None else None,
            'store_code': last_proposal['store_code'],
            'created_by': last_proposal['created_by'],
            'created_at': last_proposal['created_at'].strftime('%d/%m/%Y %H:%M') if last_proposal['created_at'] else None,
        }
    return jsonify(result)


@price_adjustment_bp.route('/api/price-adjustment/list', methods=['GET'])
def price_adjustment_list():
    """Trả về danh sách mã hàng đã phân loại ĐÃ ĐIỀU CHỈNH / CHƯA ĐIỀU
    CHỈNH, kèm tìm kiếm (q) và lọc theo trạng thái (status). "Danh mục" ở đây
    là hợp (UNION) của mọi mã hàng đang có trong tồn kho hệ thống
    (inventory_items) VÀ mọi mã mới đã được đề xuất nhưng chưa có trong tồn
    kho (price_adjustment_new_codes)."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    status = (request.args.get('status') or 'all').strip().lower()
    if status not in ('all', 'adjusted', 'not_adjusted'):
        status = 'all'
    q = (request.args.get('q') or '').strip()
    try:
        limit = min(max(int(request.args.get('limit', 300)), 1), 2000)
    except (TypeError, ValueError):
        limit = 300
    try:
        offset = max(int(request.args.get('offset', 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    db = get_db()
    cursor = db.cursor()

    # Dọn trùng lặp trước (xem giải thích ở _cleanup_new_codes_dedup) - chạy
    # mỗi lần tải danh sách để danh mục mã mới luôn "sạch", không cần sửa
    # route upload_inventory() của app.py. Commit ngay vì đây là 1 DELETE
    # (khác các API GET khác trong hệ thống vốn chỉ đọc, không cần commit).
    _cleanup_new_codes_dedup(cursor)
    db.commit()

    # CTE dùng chung (catalog = hợp mọi mã hàng cần theo dõi, latest = lần đề
    # xuất gần nhất của mỗi mã) - tách riêng phần "WITH ... )" này ra khỏi
    # phần SELECT cụ thể để dùng lại cho CẢ câu đếm số lượng LẪN câu lấy dữ
    # liệu phân trang bên dưới, tránh lặp lại 2 khối CTE giống hệt nhau.
    catalog_and_latest_cte = '''
        WITH catalog AS (
            SELECT part_code, MIN(part_name) AS part_name
            FROM inventory_items
            GROUP BY part_code
            UNION
            SELECT part_code, part_name FROM price_adjustment_new_codes
        ),
        latest AS (
            SELECT DISTINCT ON (part_code)
                id, part_code, part_name AS proposal_part_name, thue, gia_de_xuat_hvn,
                gia_ban, store_code, created_by, created_at
            FROM price_adjustment_proposals
            ORDER BY part_code, created_at DESC
        )
    '''
    base_cte = catalog_and_latest_cte + '''
        SELECT c.part_code, l.id AS proposal_id,
               COALESCE(l.proposal_part_name, c.part_name) AS part_name,
               l.thue, l.gia_de_xuat_hvn, l.gia_ban, l.store_code, l.created_by, l.created_at
        FROM catalog c
        LEFT JOIN latest l ON l.part_code = c.part_code
    '''

    where_clauses = []
    params = []
    if status == 'adjusted':
        where_clauses.append('l.created_at IS NOT NULL')
    elif status == 'not_adjusted':
        where_clauses.append('l.created_at IS NULL')
    if q:
        where_clauses.append('(c.part_code ILIKE %s OR COALESCE(l.proposal_part_name, c.part_name) ILIKE %s)')
        like_q = f'%{q}%'
        params.extend([like_q, like_q])

    where_sql = (' WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''

    # Đếm tổng số mã ĐÃ/CHƯA điều chỉnh (không phân trang) - dùng cho các
    # badge/tiêu đề trên giao diện, tính RIÊNG với đúng bộ lọc tìm kiếm q
    # (không phụ thuộc status đang chọn) để 2 con số này luôn khớp nhau dù
    # người dùng đang xem tab nào.
    #
    # TỐI ƯU: đếm bằng COUNT(*) FILTER (...) ngay trong SQL thay vì fetchall()
    # toàn bộ danh mục (có thể tới hàng chục nghìn dòng, đủ 8 cột/dòng) về
    # Python rồi mới đếm bằng vòng lặp - vừa tốn băng thông giữa app<->DB, vừa
    # tốn RAM giữ tạm danh sách chỉ để lấy 2 con số. Câu này chỉ trả về đúng
    # 1 dòng kết quả (2 số) bất kể danh mục lớn cỡ nào.
    count_where = []
    count_params = []
    if q:
        count_where.append('(c.part_code ILIKE %s OR COALESCE(l.proposal_part_name, c.part_name) ILIKE %s)')
        like_q = f'%{q}%'
        count_params.extend([like_q, like_q])
    count_where_sql = (' WHERE ' + ' AND '.join(count_where)) if count_where else ''

    cursor.execute(f'''
        {catalog_and_latest_cte}
        SELECT
            COUNT(*) FILTER (WHERE l.created_at IS NOT NULL) AS total_adjusted,
            COUNT(*) FILTER (WHERE l.created_at IS NULL) AS total_not_adjusted
        FROM catalog c
        LEFT JOIN latest l ON l.part_code = c.part_code
        {count_where_sql}
    ''', count_params)
    count_row = cursor.fetchone()
    total_adjusted = count_row['total_adjusted']
    total_not_adjusted = count_row['total_not_adjusted']

    cursor.execute(f'''
        {base_cte}
        {where_sql}
        ORDER BY (l.created_at IS NULL), l.created_at DESC NULLS LAST, c.part_code ASC
        LIMIT %s OFFSET %s
    ''', params + [limit, offset])
    rows = cursor.fetchall()
    cursor.close()

    data = []
    for r in rows:
        data.append({
            'part_code': r['part_code'],
            'proposal_id': r['proposal_id'],
            'part_name': r['part_name'],
            'is_adjusted': r['created_at'] is not None,
            'thue': float(r['thue']) * 100 if r['thue'] is not None else None,
            'gia_de_xuat_hvn': float(r['gia_de_xuat_hvn']) if r['gia_de_xuat_hvn'] is not None else None,
            'gia_ban': float(r['gia_ban']) if r['gia_ban'] is not None else None,
            'store_code': r['store_code'],
            'created_by': r['created_by'],
            'created_at': r['created_at'].strftime('%d/%m/%Y %H:%M') if r['created_at'] else None,
        })

    return jsonify({
        'success': True,
        'data': data,
        'total_adjusted': total_adjusted,
        'total_not_adjusted': total_not_adjusted,
        'limit': limit,
        'offset': offset,
    })


@price_adjustment_bp.route('/api/price-adjustment/propose', methods=['POST'])
def price_adjustment_propose():
    """Lưu 1 đề xuất tăng giá cho 1 mã hàng - dùng chung cho cả admin lẫn
    store (không giới hạn quyền như phần lớn API /api/admin/*)."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json or {}
    part_code = str(data.get('part_code', '') or '').strip().upper()
    if not part_code:
        return jsonify({'error': 'Vui lòng nhập mã hàng.'}), 400

    # CHẾ ĐỘ "LẤY GIÁ TỪ DANH MỤC": mã đã có Giá bán trong bảng part_prices
    # thì chỉ cần Mã hàng - server tự lấy Giá bán hiện có làm Giá cũ rồi
    # cộng 5%, KHÔNG cần (và không dùng) Thuế / Giá đề xuất HVN từ client.
    from_catalog_price = bool(data.get('from_catalog_price'))

    thue_percent = None
    thue_frac = None
    gia_de_xuat_hvn = None
    if not from_catalog_price:
        try:
            thue_percent = float(data.get('thue'))
            if thue_percent < 0 or thue_percent > 100:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({'error': 'Thuế không hợp lệ (phải là số từ 0 đến 100).'}), 400
        thue_frac = thue_percent / 100.0

        try:
            gia_de_xuat_hvn = float(data.get('gia_de_xuat_hvn'))
            if gia_de_xuat_hvn <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({'error': 'Giá đề xuất HVN không hợp lệ.'}), 400

    part_name = str(data.get('part_name', '') or '').strip()

    # Mức tăng giá ở Bước 2 - MẶC ĐỊNH 5% nếu client không gửi gì (giữ hành
    # vi cũ), nhưng cho phép người dùng tự nhập mức khác (VD 8%, 10%...) qua
    # trường "muc_tang_percent" trên form. Cho phép 0 (đề xuất giữ nguyên giá
    # cũ, không tăng) tới 1000% để không giới hạn quá chặt các trường hợp
    # đặc biệt; số âm luôn bị từ chối.
    muc_tang_raw = data.get('muc_tang_percent', None)
    if muc_tang_raw is None or muc_tang_raw == '':
        muc_tang_percent = _GIA_TANG_RATE * 100
    else:
        try:
            muc_tang_percent = float(muc_tang_raw)
            if muc_tang_percent < 0 or muc_tang_percent > 1000:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({'error': 'Mức tăng (%) không hợp lệ (phải là số từ 0 đến 1000).'}), 400
    muc_tang_rate = muc_tang_percent / 100.0

    db = get_db()
    cursor = db.cursor()
    try:
        if not part_name:
            part_name = _find_existing_part_name(cursor, part_code) or ''
        if not part_name:
            return jsonify({'error': 'Mã hàng này chưa có trong hệ thống - vui lòng nhập Tên hàng.'}), 400

        # Công thức 2 bước cho form đề xuất 1 mã (KHÁC với import hàng loạt):
        #   Bước 1 - Giá cũ = (Giá đề xuất HVN * Thuế) + Giá đề xuất HVN
        #   Bước 2 - Giá bán = (Giá cũ * muc_tang_rate) + Giá cũ, làm tròn hàng nghìn
        if from_catalog_price:
            cursor.execute('SELECT sale_price FROM part_prices WHERE part_code = %s', (part_code,))
            price_row = cursor.fetchone()
            catalog_price = float(price_row['sale_price']) if price_row and price_row['sale_price'] is not None else 0
            if catalog_price <= 0:
                return jsonify({'error': 'Mã hàng này chưa có Giá bán trong danh mục - vui lòng nhập Thuế và Giá đề xuất HVN.'}), 400
            gia_cu = _round_to_thousand(catalog_price)
            gia_de_xuat_hvn = gia_cu
            thue_percent = muc_tang_percent
        else:
            gia_cu = _compute_gia_cu(gia_de_xuat_hvn, thue_frac)
        gia_ban = _compute_gia_ban_tu_gia_cu(gia_cu, muc_tang_rate)
        now = vn_now()
        actor = _current_actor_name()
        store_code = session.get('store_code') if session.get('role') == 'store' else None

        # QUAN TRỌNG: bảng price_adjustment_proposals (lịch sử) chỉ có 2 cột
        # số dùng chung cho cả đề xuất thủ công lẫn import hàng loạt: "thue"
        # và "gia_de_xuat_hvn". Để 2 cột này LUÔN mang đúng 1 ý nghĩa nhất
        # quán dù dữ liệu đến từ đâu (tránh hiển thị sai như khi trước: cột
        # "THUẾ" trên UI hoá ra lại là % tăng giá, cột "GIÁ ĐỀ XUẤT HVN" hoá
        # ra lại là Giá cũ), ta LƯU:
        #   - "thue"            = muc_tang_rate (mức % tăng giá THỰC TẾ đã áp
        #                          dụng ở Bước 2 cho lần đề xuất này - mặc
        #                          định 5% nếu người dùng không tự nhập mức
        #                          khác) - KHÔNG lưu Thuế (%) người dùng gõ ở
        #                          Bước 1 (giá trị đó chỉ là bước đệm để tính
        #                          ra Giá cũ, không cần giữ lại).
        #   - "gia_de_xuat_hvn" = gia_cu (Giá cũ đã tính ở Bước 1) - KHÔNG
        #                          lưu Giá đề xuất HVN người dùng gõ.
        cursor.execute('''
            INSERT INTO price_adjustment_proposals
                (part_code, part_name, thue, gia_de_xuat_hvn, gia_ban, store_code, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (part_code, part_name, muc_tang_rate, gia_cu, gia_ban, store_code, actor, now))
        new_id = cursor.fetchone()['id']

        # Mã mới (chưa có trong tồn kho hệ thống) -> tự động lưu vào danh mục
        # mã mới để lần đề xuất sau tự điền lại Tên/Thuế. Mã đã có sẵn trong
        # tồn kho hệ thống thì không cần lưu thêm (đã có danh mục chính thức).
        # (Chế độ lấy giá từ danh mục: mã đã có giá bán nên đã thuộc danh mục,
        # không có Thuế để lưu -> bỏ qua bước này.)
        cursor.execute('SELECT 1 FROM inventory_items WHERE part_code = %s LIMIT 1', (part_code,))
        if not from_catalog_price and cursor.fetchone() is None:
            cursor.execute('''
                INSERT INTO price_adjustment_new_codes (part_code, part_name, thue, created_by, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (part_code) DO UPDATE SET
                    part_name = EXCLUDED.part_name,
                    thue = EXCLUDED.thue
            ''', (part_code, part_name, thue_frac, actor, now))

        db.commit()
        return jsonify({
            'success': True,
            'id': new_id,
            'part_code': part_code,
            'part_name': part_name,
            'thue': thue_percent,
            'gia_de_xuat_hvn': gia_de_xuat_hvn,
            'gia_cu': gia_cu,
            'gia_ban': gia_ban,
            'muc_tang_percent': muc_tang_percent,
            'created_by': actor,
            'created_at': now.strftime('%d/%m/%Y %H:%M'),
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'Lỗi khi lưu đề xuất: {e}'}), 500
    finally:
        cursor.close()


@price_adjustment_bp.route('/api/price-adjustment/proposals/<int:proposal_id>', methods=['PUT'])
def price_adjustment_update_proposal(proposal_id):
    """Cho phép BẤT KỲ user nào đã đăng nhập (cả admin lẫn store, giống hệt
    quyền của propose() ở trên - KHÔNG giới hạn chỉ admin như delete() bên
    dưới) SỬA LẠI 1 lần đề xuất đã lưu, dùng khi Giá cũ / % Tăng giá / Giá
    bán bị nhập sai (gõ nhầm số, lệch % ...). Cho sửa trực tiếp cả 3 giá trị
    (không bắt buộc phải đúng công thức 2 bước của propose()) để xử lý được
    mọi trường hợp sai sót, kể cả những dòng import hàng loạt từ Excel. Vì
    bảng danh sách (list_price_adjustments) luôn lấy 'latest' - lần đề xuất
    gần nhất của mỗi mã - dòng vừa sửa sẽ lập tức phản ánh giá trị mới ngay
    trên bảng, không cần thao tác gì thêm."""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json or {}

    try:
        thue_percent = float(data.get('thue'))
        if thue_percent < 0 or thue_percent > 1000:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({'error': '% Tăng giá không hợp lệ (phải là số từ 0 đến 1000).'}), 400

    try:
        gia_cu = float(data.get('gia_de_xuat_hvn'))
        if gia_cu <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({'error': 'Giá cũ không hợp lệ.'}), 400

    try:
        gia_ban = float(data.get('gia_ban'))
        if gia_ban <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({'error': 'Giá bán không hợp lệ.'}), 400

    part_name = str(data.get('part_name', '') or '').strip() or None

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute('SELECT part_code FROM price_adjustment_proposals WHERE id = %s', (proposal_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Không tìm thấy đề xuất này (có thể đã bị xoá).'}), 404
        part_code = row['part_code']

        cursor.execute('''
            UPDATE price_adjustment_proposals
            SET part_name = COALESCE(%s, part_name),
                thue = %s,
                gia_de_xuat_hvn = %s,
                gia_ban = %s
            WHERE id = %s
        ''', (part_name, thue_percent / 100.0, gia_cu, gia_ban, proposal_id))

        # Đồng bộ lại Tên hàng trong "danh mục mã mới" (nếu mã này có ở đó)
        # để lần đề xuất sau vẫn tự hiện đúng tên vừa sửa.
        if part_name:
            cursor.execute('''
                UPDATE price_adjustment_new_codes SET part_name = %s WHERE part_code = %s
            ''', (part_name, part_code))

        db.commit()
        return jsonify({
            'success': True,
            'part_code': part_code,
            'part_name': part_name,
            'thue': thue_percent,
            'gia_de_xuat_hvn': gia_cu,
            'gia_ban': gia_ban,
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'Lỗi khi sửa đề xuất: {e}'}), 500
    finally:
        cursor.close()


@price_adjustment_bp.route('/api/price-adjustment/proposals/<int:proposal_id>', methods=['DELETE'])
def price_adjustment_delete_proposal(proposal_id):
    """Chỉ admin được xoá 1 lần đề xuất đã lưu (VD: nhập nhầm số). Xoá dòng
    này có thể khiến mã hàng quay lại trạng thái "chưa điều chỉnh" nếu đó là
    lần đề xuất duy nhất của mã đó."""
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    cursor = db.cursor()
    cursor.execute('DELETE FROM price_adjustment_proposals WHERE id = %s RETURNING id', (proposal_id,))
    row = cursor.fetchone()
    db.commit()
    cursor.close()

    if not row:
        return jsonify({'error': 'Không tìm thấy đề xuất này.'}), 404
    return jsonify({'success': True})