# -*- coding: utf-8 -*-
"""
Đọc NHANH 1 file "PHIẾU THU ĐẶT CỌC / BẢNG BÁO GIÁ" (PDF xuất từ phần mềm
CRM đang dùng ở cửa hàng) để trích ra: Số phiếu (dùng làm "Số báo giá"),
Tên khách hàng, Điện thoại, danh sách mặt hàng (Mã hàng/Tên hàng/SL/Đơn giá)
và Số tiền đặt cọc - phục vụ điền NHANH vào form "Thêm đơn mới" của
orders.py (route /api/orders/import_quote).

QUAN TRỌNG: hàm ở đây CHỈ ĐỌC và trả về dict, KHÔNG tự ghi vào CSDL - người
dùng vẫn phải rà soát lại trên form rồi tự bấm "Lưu đơn" như bình thường.
Khác với bo_import.py (import hàng loạt trực tiếp vào CSDL, chỉ admin
dùng), tính năng này dùng hằng ngày cho cả nhân viên cửa hàng nên ưu tiên
an toàn (không tự lưu) hơn là nhanh nhưng dễ sai.

ĐÃ KIỂM TRA THỰC TẾ TRÊN FILE MẪU (CRM_Ba_o_gia_....pdf, 37 mặt hàng, 2
trang) - đọc đúng 37/37 mặt hàng + đủ Số phiếu/Tên KH/SĐT/Tiền cọc.

CÁCH ĐỌC BẢNG MẶT HÀNG - DÙNG page.extract_tables(), KHÔNG dùng
extract_text() rồi tự ghép dòng:
Ban đầu thử ghép theo text thô (extract_text) bị SAI vì khi 1 dòng "Tên
hàng" dài phải xuống dòng trong ô, pdfplumber xuất text theo thứ tự
toạ độ y trên trang - dòng "thừa" của Tên hàng có thể trồi lên NẰM TRƯỚC
cả dòng chứa STT/Mã hàng/SL/Giá của chính mặt hàng đó, khiến ghép sai lẫn
tên hàng giữa 2 mặt hàng liền kề. `extract_tables()` đọc theo đúng cấu
trúc bảng (dựa viền kẻ), mỗi ô của 1 hàng đã đúng vào đúng cột kể cả khi ô
đó có xuống dòng (giữ nguyên dạng chuỗi có `\n` bên trong) - ĐÁNG TIN CẬY
HƠN NHIỀU so với ghép theo thứ tự dòng text thô.

Cách nhận diện 1 hàng là "mặt hàng" trong bảng đã trích (`_parse_item_row`):
  - Ô đầu tiên (STT) là số nguyên thuần (vd '9', '37').
  - Ô thứ 2 là Mã hàng.
  - Số cột giữa "Mã hàng" và "3 số cuối" có thể lẫn None (do PDF có ô gộp/
    viền lệch) - nên LỌC BỎ None trong các ô còn lại, rồi lấy 3 ô cuối
    cùng còn lại là SL/Đơn giá/Thành tiền, phần trước đó là Tên hàng (nối
    lại, thay '\n' bằng khoảng trắng). Cách này không phụ thuộc số cột
    chính xác của từng trang (đã thấy khác nhau giữa trang 1 và 2 của file
    mẫu: 7 cột có None đệm vs 6 cột liền, do dòng tiêu đề "Đơn giá thời
    điểm đặt cọc" chiếm 2 dòng ở trang có header bảng).
"""
import re

try:
    import pdfplumber
except ImportError:  # báo lỗi rõ ràng khi thực sự gọi hàm, không chặn lúc import module
    pdfplumber = None

_QUOTE_NO_RE = re.compile(r'S[ốô]\s*phi[ếe]u\s*:?\s*([A-Za-z0-9\-/]+)', re.IGNORECASE)
_NAME_RE = re.compile(r'Kh[áa]ch\s*[Hh]àng\s*:\s*(.+?)\s+[ĐĐ]i[ệe]n\s*tho[ạa]i\s*:', re.IGNORECASE)
_PHONE_RE = re.compile(r'[ĐĐ]i[ệe]n\s*tho[ạa]i\s*:\s*(0\d{8,10})\b')
_DEPOSIT_RE = re.compile(r'S[ốô]\s*ti[ềe]n\s*[đđ][ặa]t\s*c[ọo]c\s*:\s*([\d.,]+)', re.IGNORECASE)
_TOTAL_RE = re.compile(r'T[ổo]ng\s*c[ộo]ng\s+\d+\s+([\d.,]+)', re.IGNORECASE)


def _to_number(s):
    if not s and s != 0:
        return None
    s = str(s).strip().replace('đ', '').replace('₫', '').strip()
    if not s:
        return None
    s = s.replace('.', '').replace(',', '')  # "5.237.000" | "5,237,000" -> "5237000"
    try:
        return float(s)
    except ValueError:
        return None


def _find_header(full_text):
    m = _QUOTE_NO_RE.search(full_text)
    quote_no = m.group(1).strip() if m else None

    m = _NAME_RE.search(full_text)
    customer_name = re.sub(r'\s+', ' ', m.group(1)).strip() if m else None

    m = _PHONE_RE.search(full_text)
    customer_phone = m.group(1) if m else None

    m = _DEPOSIT_RE.search(full_text)
    deposit_amount = _to_number(m.group(1)) if m else None

    m = _TOTAL_RE.search(full_text)
    order_value = _to_number(m.group(1)) if m else deposit_amount

    return quote_no, customer_name, customer_phone, deposit_amount, order_value


def _parse_item_row(row):
    """row: 1 hàng (list ô) từ page.extract_tables(). -> dict mặt hàng hoặc
    None nếu không phải hàng mặt hàng hợp lệ (thiếu SL/Đơn giá/Thành tiền)."""
    if len(row) < 3 or not row[1]:
        return None
    part_code = str(row[1]).strip()
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9\-]{2,}$', part_code):
        return None

    rest = [c for c in row[2:] if c not in (None, '')]
    if len(rest) < 3:
        return None
    tail = rest[-3:]
    tail_nums = [_to_number(x) for x in tail]
    if any(n is None for n in tail_nums):
        return None  # 3 ô cuối không phải số hợp lệ -> không đúng khuôn dạng mặt hàng, bỏ qua
    part_name = ' '.join(str(x).replace('\n', ' ').strip() for x in rest[:-3]).strip()
    part_name = re.sub(r'\s+', ' ', part_name)
    if not part_name:
        return None
    qty_num, unit_price = tail_nums[0], tail_nums[1]
    qty_str = str(tail[0]).strip()
    return {
        'part_code': part_code,
        'part_name': part_name,
        'quantity': qty_str if qty_str else ('%g' % qty_num),
        'unit_price': unit_price,
    }


def parse_quote_pdf(file_stream):
    """-> dict {quote_no, customer_name, customer_phone, deposit_amount,
    order_value, items:[{part_code,part_name,quantity,unit_price}], unmatched}

    `unmatched`: số hàng trong bảng trích được có STT là số nguyên và có mã
    hàng ở cột 2, nhưng KHÔNG tách được đủ SL/Đơn giá/Thành tiền hợp lệ -
    để orders.py cảnh báo người dùng tự kiểm tra lại phần đó trên PDF gốc
    thay vì âm thầm bỏ sót.

    Ném ValueError/RuntimeError kèm thông báo tiếng Việt nếu không đọc được
    gì hữu ích, để route Flask trả lỗi 400 rõ ràng cho người dùng."""
    if pdfplumber is None:
        raise RuntimeError(
            'Thiếu thư viện pdfplumber trên máy chủ - cần cài đặt '
            '(pip install pdfplumber) để dùng tính năng đọc báo giá PDF.'
        )

    full_text_parts = []
    items, unmatched = [], 0
    with pdfplumber.open(file_stream) as pdf:
        for page in pdf.pages:
            full_text_parts.append(page.extract_text() or '')
            for table in page.extract_tables():
                for row in table:
                    if not row or row[0] is None or not str(row[0]).strip().isdigit():
                        continue  # không phải hàng mặt hàng (header bảng, "Tổng cộng", hàng chữ dài...)
                    it = _parse_item_row(row)
                    if it:
                        items.append(it)
                    else:
                        unmatched += 1
    full_text = '\n'.join(full_text_parts)
    if not full_text.strip():
        raise ValueError('Không đọc được nội dung PDF (có thể là file ảnh/scan, chưa hỗ trợ OCR).')

    quote_no, customer_name, customer_phone, deposit_amount, order_value = _find_header(full_text)

    if not customer_name and not items:
        raise ValueError(
            'Không nhận diện được dữ liệu báo giá trong file này - vui lòng '
            'kiểm tra lại đúng file "Phiếu thu đặt cọc/Báo giá".'
        )

    return {
        'quote_no': quote_no,
        'customer_name': customer_name,
        'customer_phone': customer_phone,
        'deposit_amount': deposit_amount,
        'order_value': order_value,
        'items': items,
        'unmatched': unmatched,
    }


if __name__ == '__main__':
    import sys
    import json

    path = sys.argv[1] if len(sys.argv) > 1 else \
        '/mnt/user-data/uploads/CRM_Ba_o_gia__24_09_2026_11_07_58_IBLRDNNI_.pdf'
    with open(path, 'rb') as f:
        result = parse_quote_pdf(f)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n-> {len(result['items'])} mặt hàng đọc được, {result['unmatched']} dòng bị bỏ qua.")
