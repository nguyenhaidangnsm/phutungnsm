# -*- coding: utf-8 -*-
"""
Đọc sheet "THEO DÕI B0 KHÁCH HÀNG-2026" trong file báo cáo Excel gốc
(BA_O_CA_O_TO__NG_HO__P.xlsx) và chuẩn hoá thành danh sách dict khớp với
các cột có thể ghi của bảng bo_orders (xem orders.py).

ĐÃ KIỂM TRA THỰC TẾ TRÊN FILE MẪU (dòng 1 = header, tô nền xanh #0070C0,
chữ đậm - xác nhận đây đúng là hàng tiêu đề dù 2 ô A1/C1 bị lỗi nội dung):

  Cột | Tiêu đề gốc                          | -> Trường bo_orders
  ----|---------------------------------------|---------------------------
   A  | (lỗi - còn sót số liệu cũ)            | bỏ qua
   B  | Tháng                                  | bỏ qua (đã suy ra từ ngày)
   C  | (lỗi - đúng ra là TRẠNG THÁI)          | status
   D  | NGÀY YÊU CẦU                           | customer_request_date
   E  | TÊN KHÁCH HÀNG                         | customer_name  (*bắt buộc*)
   F  | SĐT                                    | customer_phone
   G  | LOẠI XE                                | vehicle_type
   H  | SỐ KHUNG                               | frame_number
   I  | MÀU                                    | vehicle_color
   J  | ĐỜI XE                                 | vehicle_year
   K  | MÃ HÀNG                                | part_code
   L  | TÊN HÀNG                               | part_name
   M  | SL                                     | quantity
   N  | GIÁ TRỊ ĐƠN (cả đơn, ô gộp dọc)       | order_value
   O  | ĐẶT CỌC     (cả đơn)                   | deposit_amount
   P  | NGÀY ĐẶT/ XIN NỘI BỘ/ SOẠN             | order_date
   Q  | LOẠI ĐƠN                               | order_type
   R  | NGÀY PT VỀ                             | expected_delivery_date
   S  | NGÀY GỌI K/H                           | customer_call_date
   T  | NGÀY GIAO PT                           | actual_delivery_date

GOM DÒNG THÀNH "YÊU CẦU ĐẶT" (request_id / seq_no):
Mỗi dòng Excel là 1 mặt hàng. Các dòng thuộc cùng 1 vùng merge của cột TÊN
KHÁCH HÀNG (E) được gom thành 1 yêu cầu đặt (cùng request_id, cùng seq_no =
số thứ tự). Dòng không merge: gom các dòng LIỀN NHAU cùng tên khách + cùng
ngày yêu cầu. Nếu trong 1 nhóm có thông tin khách/xe MÂU THUẪN (vd 2 số
khung khác nhau) thì tách thành yêu cầu riêng để không mất dữ liệu.

GHI CHÚ QUAN TRỌNG VỀ ĐỘ CHÍNH XÁC:
1. File gốc có ~8.400 vùng merge cell dọc (nhiều dòng gộp thành 1 ô cho
   cùng 1 khách hàng có nhiều mặt hàng). `_build_merge_map()` "trải" giá
   trị ô gộp xuống mọi dòng thuộc vùng đó trước khi đọc - nếu bỏ qua bước
   này sẽ mất ~2.000 tên khách hàng/SĐT do rơi vào dòng "rỗng".
2. Ngày tháng trong file lưu lẫn lộn 3 kiểu (datetime / số serial Excel /
   chuỗi "DD/MM/YYYY") - `_parse_any_date()` xử lý cả 3, phần không phải
   ngày hợp lệ (vd "GIAO RỒI") được giữ nguyên trong call_note thay vì bị
   âm thầm bỏ.
3. Một số dòng trong file gốc đã bị nhập NGƯỢC cột (MÃ HÀNG chứa tên hàng,
   ngược lại) - đây là lỗi có sẵn trong dữ liệu gốc. Để đúng nguyên tắc
   "chính xác theo nguồn", script CHỈ chuyển đúng theo cột đã khai báo ở
   trên, KHÔNG tự đoán/sửa lại nội dung - việc tự "sửa" sẽ làm sai lệch so
   với bản gốc mà người nhập liệu (bạn) mới là người biết đúng-sai.
4. Một vài cột có dữ liệu dài hơn giới hạn VARCHAR hiện tại của bảng
   bo_orders (customer_phone VARCHAR(30), frame_number VARCHAR(50),
   part_code/po_code VARCHAR(100)...). Để KHÔNG mất dữ liệu, nếu giá trị
   vượt giới hạn, script sẽ cắt bớt phần lưu vào đúng cột đó NHƯNG lưu
   nguyên văn đầy đủ vào call_note kèm nhãn. Khuyến nghị chạy migration
   nới rộng cột (xem widen_columns.sql) để không cần cắt nữa.
"""
import re
import uuid
from datetime import datetime, date, timedelta

import openpyxl

SHEET_NAME = 'THEO DÕI B0 KHÁCH HÀNG-2026'

# Dùng để CẢNH BÁO (không tự sửa) khi nghi ngờ 2 cột MÃ HÀNG/TÊN HÀNG bị
# nhập đảo ngược ở 1 dòng cụ thể - việc này có sẵn trong file gốc (~15%
# số dòng, do người nhập liệu gõ nhầm cột qua nhiều năm).
_CODE_PATTERN = re.compile(r'^[0-9]{4,6}[A-Z0-9]{3,8}$')


def _looks_like_code(val):
    if not isinstance(val, str) or not val.strip():
        return False
    first = val.split(',')[0].split('\n')[0].strip().upper().replace(' ', '')
    return bool(_CODE_PATTERN.match(first))

EXCEL_EPOCH = datetime(1899, 12, 30)

# Giới hạn độ dài theo đúng schema bo_orders (xem CREATE TABLE trong orders.py)
FIELD_MAX_LEN = {
    'status': 50,
    'customer_phone': 30,
    'license_plate': 30,
    'frame_number': 50,
    'vehicle_type': 100,
    'vehicle_color': 50,
    'vehicle_year': 50,
    'quantity': 50,
    'order_type': 100,
    'part_code': 100,
    'po_code': 100,
}

DATE_FMTS = ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y')


def _build_merge_map(ws):
    """Trả về dict {(row, col): value} cho MỌI ô nằm trong 1 vùng merge,
    lấy giá trị từ ô góc trên-trái của vùng đó (trừ chính ô góc)."""
    merge_map = {}
    for rng in ws.merged_cells.ranges:
        top_val = ws.cell(row=rng.min_row, column=rng.min_col).value
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                if (r, c) == (rng.min_row, rng.min_col):
                    continue
                merge_map[(r, c)] = top_val
    return merge_map


def _build_block_map(ws, col):
    """{row: dòng_đầu_vùng_merge} cho các ô của cột `col` nằm trong vùng merge
    dọc (>1 dòng). Dùng để biết những dòng nào thuộc cùng 1 khách."""
    block_of = {}
    for rng in ws.merged_cells.ranges:
        if rng.min_col <= col <= rng.max_col and rng.max_row > rng.min_row:
            for r in range(rng.min_row, rng.max_row + 1):
                block_of[r] = rng.min_row
    return block_of


# Thông tin cấp "yêu cầu" (chung cho mọi mặt hàng của 1 khách)
HEADER_FIELDS = ('customer_phone', 'vehicle_type', 'frame_number', 'vehicle_color',
                 'vehicle_year', 'customer_request_date')


def _header_conflict(header, row):
    for f in HEADER_FIELDS:
        a, b = header.get(f), row.get(f)
        if a is not None and b is not None and a != b:
            return True
    return False


def _parse_any_date(val):
    """-> (date | None, chuỗi_gốc_nếu_không_parse_được | None)"""
    if val is None:
        return None, None
    if isinstance(val, datetime):
        return val.date(), None
    if isinstance(val, date):
        return val, None
    if isinstance(val, (int, float)):
        try:
            return (EXCEL_EPOCH + timedelta(days=float(val))).date(), None
        except (OverflowError, ValueError):
            return None, str(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None, None
        for fmt in DATE_FMTS:
            try:
                return datetime.strptime(s, fmt).date(), None
            except ValueError:
                continue
        return None, s  # chuỗi không phải ngày -> giữ nguyên văn để đưa vào call_note
    return None, None


def _parse_money(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        s = val.strip().replace(',', '').replace('.', '').replace(' ', '').replace('đ', '').replace('₫', '')
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _norm_status(raw):
    if not isinstance(raw, str):
        return None
    s = raw.strip().upper()
    if not s:
        return None
    if 'HUỶ' in s or 'HỦY' in s:
        return 'Đã huỷ'
    if 'CHƯA' in s and 'GIAO' in s:
        return 'Đã đặt'  # "chưa giao" = đã đặt nhưng chưa giao khách
    if 'GIAO' in s:  # bắt cả lỗi chính tả 'ĐÃ GIÁO'
        return 'Đã giao'
    return None


def _s(val):
    if val is None:
        return None
    if isinstance(val, str):
        v = val.strip()
        return v or None
    return str(val).strip() or None


def _truncate_with_note(field, value, notes):
    if value is None:
        return None
    limit = FIELD_MAX_LEN.get(field)
    if limit and len(value) > limit:
        notes.append(f'{field} (đầy đủ): {value}')
        return value[:limit]
    return value


def parse_bo_orders_excel(path, store_code, created_by='Import Excel', sheet_name=SHEET_NAME, start_seq=1):
    """Trả về (rows, skipped) - rows: list[dict] sẵn sàng insert vào bo_orders;
    skipped: list[dict] các dòng bị bỏ qua kèm lý do (để người dùng soát lại).
    Mỗi row có thêm request_id (uuid) + seq_no (STT, bắt đầu từ start_seq)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"Không tìm thấy sheet '{sheet_name}' trong file. Các sheet có: {wb.sheetnames}")
    ws = wb[sheet_name]
    merge_map = _build_merge_map(ws)
    block_of = _build_block_map(ws, 5)  # cột E = TÊN KHÁCH HÀNG

    def getval(r, c):
        return merge_map.get((r, c), ws.cell(row=r, column=c).value)

    rows = []
    skipped = []
    requests = []  # mỗi phần tử: {'block','name','header','rows'}
    cur = None
    now = datetime.utcnow()

    for r in range(2, ws.max_row + 1):
        vals = [getval(r, c) for c in range(1, 21)]  # A..T
        if all(v is None for v in vals):
            continue  # dòng trắng hoàn toàn (do gộp ô) -> bỏ qua, không phải lỗi

        (_a, _b, raw_status, raw_request_date, raw_name, raw_phone, raw_vehicle_type,
         raw_frame, raw_color, raw_doi_xe, raw_part_code, raw_part_name, raw_sl,
         raw_order_value, raw_deposit, raw_order_date, raw_loai_don,
         raw_expected, raw_call_date, raw_actual) = vals

        customer_name = _s(raw_name)
        if not customer_name:
            skipped.append({'row': r, 'reason': 'Thiếu TÊN KHÁCH HÀNG', 'raw': vals})
            continue

        notes = []
        req_date, req_extra = _parse_any_date(raw_request_date)
        order_date, order_extra = _parse_any_date(raw_order_date)
        expected_date, expected_extra = _parse_any_date(raw_expected)
        call_date, call_extra = _parse_any_date(raw_call_date)
        actual_date, actual_extra = _parse_any_date(raw_actual)

        for label, extra in (
            ('NGÀY YÊU CẦU', req_extra), ('NGÀY ĐẶT', order_extra),
            ('NGÀY PT VỀ', expected_extra), ('NGÀY GỌI K/H', call_extra),
            ('NGÀY GIAO PT', actual_extra),
        ):
            if extra:
                notes.append(f'{label} (không parse được): {extra}')

        if _looks_like_code(raw_part_name) and not _looks_like_code(raw_part_code):
            notes.append('⚠ Nghi ngờ cột MÃ HÀNG/TÊN HÀNG bị nhập đảo ở dòng này - vui lòng kiểm tra lại')

        status = _norm_status(raw_status) or 'Chưa đặt'

        row = {
            'store_code': store_code,
            'status': _truncate_with_note('status', status, notes),
            'customer_name': customer_name,
            'customer_address': None,
            'customer_phone': _truncate_with_note('customer_phone', _s(raw_phone), notes),
            'license_plate': None,
            'frame_number': _truncate_with_note('frame_number', _s(raw_frame), notes),
            'vehicle_type': _truncate_with_note('vehicle_type', _s(raw_vehicle_type), notes),
            'vehicle_color': _truncate_with_note('vehicle_color', _s(raw_color), notes),
            'vehicle_year': _truncate_with_note('vehicle_year', _s(raw_doi_xe), notes),
            'quantity': _truncate_with_note('quantity', _s(raw_sl), notes),
            'order_type': _truncate_with_note('order_type', _s(raw_loai_don), notes),
            'part_name': _s(raw_part_name),
            'part_code': _truncate_with_note('part_code', _s(raw_part_code), notes),
            'order_value': _parse_money(raw_order_value),
            'deposit_amount': _parse_money(raw_deposit),
            'po_code': None,
            'customer_request_date': req_date,
            'order_date': order_date,
            'expected_delivery_date': expected_date,
            'actual_delivery_date': actual_date,
            'customer_call_date': call_date,
            'call_note': ' | '.join(notes) if notes else None,
            'created_by': created_by,
            'created_at': now,
            'updated_at': now,
            '_source_row': r,
        }
        rows.append(row)

        # --- Gom vào "yêu cầu đặt" ---
        block = block_of.get(r)
        same = False
        if cur is not None:
            if block is not None and block == cur['block']:
                same = True
            elif (block is None and cur['block'] is None
                  and customer_name.casefold() == cur['name']
                  and req_date == cur['header'].get('customer_request_date')):
                same = True
            if same and _header_conflict(cur['header'], row):
                same = False
        if not same:
            cur = {'block': block, 'name': customer_name.casefold(),
                   'header': {f: None for f in HEADER_FIELDS}, 'rows': []}
            requests.append(cur)
        for f in HEADER_FIELDS:
            if cur['header'][f] is None and row.get(f) is not None:
                cur['header'][f] = row[f]
        cur['rows'].append(row)

    # Áp thông tin chung của yêu cầu xuống mọi dòng + cấp request_id / STT
    for idx, rq in enumerate(requests):
        rid = uuid.uuid4().hex
        # GIÁ TRỊ ĐƠN / ĐẶT CỌC là số của cả đơn (ô gộp dọc trong Excel): lấy giá
        # trị khác rỗng đầu tiên. Nếu dòng nào có số RIÊNG khác đi thì giữ nguyên
        # văn trong call_note của dòng đó thay vì âm thầm bỏ.
        for f, label in (('order_value', 'GIÁ TRỊ ĐƠN'), ('deposit_amount', 'ĐẶT CỌC')):
            common = next((row[f] for row in rq['rows'] if row.get(f) is not None), None)
            for row in rq['rows']:
                own = row.get(f)
                if own is not None and common is not None and own != common:
                    extra = f'{label} (gốc, khác số chung của đơn): {own:,.0f}'
                    row['call_note'] = f"{row['call_note']} | {extra}" if row.get('call_note') else extra
                row[f] = common
        for row in rq['rows']:
            for f in HEADER_FIELDS:
                row[f] = rq['header'][f]
            row['request_id'] = rid
            row['seq_no'] = start_seq + idx

    return rows, skipped


if __name__ == '__main__':
    import sys
    from collections import Counter

    path = sys.argv[1] if len(sys.argv) > 1 else \
        '/mnt/user-data/uploads/BA_O_CA_O_TO__NG_HO__P.xlsx'
    rows, skipped = parse_bo_orders_excel(path, store_code='NS3')
    n_req = len({r['request_id'] for r in rows})
    print(f'Đọc OK: {len(rows)} mặt hàng ({n_req} yêu cầu đặt) sẽ được import, {len(skipped)} dòng bị bỏ qua (thiếu tên KH).')
    status_c = Counter(r['status'] for r in rows)
    print('Phân bố trạng thái:', dict(status_c))
    print('\n--- 5 dòng mẫu đầu ---')
    for r in rows[:5]:
        r2 = {k: v for k, v in r.items() if k != '_source_row'}
        print(r['_source_row'], r2)