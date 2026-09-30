# -*- coding: utf-8 -*-
"""
CHUẨN HOÁ MÃ HÀNG DÙNG CHUNG cho mọi module (app.py, orders.py, body_kit.py...).

Cùng 1 phụ tùng có thể bị gõ/nhập nhiều kiểu: '06410KFL850', '06410-KFL-850',
'06410kfl850', ' 06410KFL850 '. Với người là 1 mã, với database là 4 chuỗi khác
nhau. Quy tắc CHUNG (giống _norm_code sẵn có của orders.py): bỏ mọi ký tự không
phải chữ/số rồi viết HOA.

CHỈ dùng để SO SÁNH/TÌM KIẾM. Mã hiển thị và mã lưu trong bảng gốc giữ nguyên.

Gồm:
  norm_code(c)            - chuẩn hoá 1 mã trong Python.
  n_sql(col)              - biểu thức SQL tương đương (dùng cho WHERE/INDEX).
  norm_index_sql(...)     - câu CREATE INDEX trên biểu thức chuẩn hoá.
  resolve_codes(cur, codes) - đổi mã người dùng gõ -> mã CHÍNH THỨC đang có trong hệ thống.

QUAN TRỌNG: biểu thức trong n_sql() và trong INDEX phải GIỐNG HỆT nhau thì
Postgres mới dùng được index. Đừng sửa 1 nơi mà quên nơi kia.
"""
import re

_NON_ALNUM = re.compile(r'[^A-Za-z0-9]')

# Chỉ cho phép tên bảng/cột nằm trong danh sách này khi ghép vào SQL (tránh
# nhúng chuỗi lạ vào câu lệnh).
_SAFE_IDENT = re.compile(r'^[a-z_][a-z0-9_]*$')
_SAFE_COL = re.compile(r'^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$')   # cho phép alias: bp.part_code


def norm_code(c):
    """'06410-kfl 850' -> '06410KFL850'. None/rỗng -> ''."""
    return _NON_ALNUM.sub('', str(c or '')).upper()


def n_sql(col):
    """Biểu thức SQL chuẩn hoá cột mã hàng - PHẢI khớp với norm_code()."""
    if not _SAFE_COL.match(col):
        raise ValueError('Tên cột không hợp lệ: %r' % (col,))
    return "regexp_replace(upper(%s), '[^A-Z0-9]', '', 'g')" % col


def norm_index_sql(table, col='part_code', name=None):
    """CREATE INDEX trên biểu thức chuẩn hoá, để tra theo mã chuẩn hoá không bị
    quét cả bảng. An toàn khi chạy nhiều lần (IF NOT EXISTS)."""
    if not _SAFE_IDENT.match(table) or not _SAFE_IDENT.match(col):
        raise ValueError('Tên bảng/cột không hợp lệ.')
    name = name or 'idx_%s_%s_norm' % (table, col)
    return 'CREATE INDEX IF NOT EXISTS %s ON %s ((%s))' % (name, table, n_sql(col))


# Nguồn "mã chính thức" theo thứ tự ưu tiên (đều nằm ở CSDL CHÍNH).
DEFAULT_SOURCES = ('inventory_items', 'part_prices', 'order_lock_items')


def resolve_codes(cur, codes, sources=DEFAULT_SOURCES):
    """Đổi các mã người dùng gõ sang mã CHÍNH THỨC đang có trong hệ thống.

    Trả về dict {mã_đã_gõ: mã_chính_thức} CHỈ cho những mã KHÁC nhau (mã đã
    đúng, hoặc không tìm được, thì không có trong dict -> gọi
    `m.get(code, code)` để lấy mã dùng).

    Quy tắc an toàn:
      - Mã đã khớp CHÍNH XÁC 1 mã chính thức -> giữ nguyên (không đổi).
      - Khớp chuẩn hoá đúng 1 mã chính thức -> đổi sang mã đó.
      - Khớp chuẩn hoá NHIỀU mã chính thức khác nhau (mơ hồ) -> KHÔNG đổi,
        tránh chọn nhầm.
    `cur` là cursor của CSDL chính (RealDictCursor).
    """
    typed = [str(c).strip() for c in (codes or []) if c is not None and str(c).strip()]
    if not typed:
        return {}
    norm_of = {c: norm_code(c) for c in typed}
    wanted = sorted({n for n in norm_of.values() if n})
    if not wanted:
        return {}

    # norm -> {mã chính thức}, gom từ các nguồn theo thứ tự ưu tiên; nguồn ưu
    # tiên cao hơn thắng (nếu 1 norm đã có ở nguồn trên thì bỏ qua nguồn dưới).
    canon_by_norm = {}
    for table in sources:
        if not _SAFE_IDENT.match(table):
            raise ValueError('Tên bảng không hợp lệ: %r' % (table,))
        missing = [n for n in wanted if n not in canon_by_norm]
        if not missing:
            break
        cur.execute(
            'SELECT DISTINCT part_code FROM %s WHERE %s = ANY(%%s)' % (table, n_sql('part_code')),
            (missing,))
        found = {}
        for r in cur.fetchall():
            pc = r['part_code'] if isinstance(r, dict) else r[0]
            found.setdefault(norm_code(pc), set()).add(pc)
        for n, s in found.items():
            canon_by_norm.setdefault(n, s)

    out = {}
    for c in typed:
        cands = canon_by_norm.get(norm_of[c])
        if not cands or c in cands:
            continue                      # không tìm thấy, hoặc đã đúng mã chính thức
        if len(cands) == 1:
            out[c] = next(iter(cands))
        # nhiều ứng viên -> mơ hồ, giữ nguyên mã đã gõ
    return out


def group_by_norm(codes):
    """['A-1','a1','B2'] -> {'A1': ['A-1','a1'], 'B2': ['B2']} (gom mã gốc theo dạng
    chuẩn hoá) - dùng để tra DB theo mã chuẩn hoá rồi gán kết quả ngược về từng mã gốc."""
    d = {}
    for c in codes or []:
        if c is None:
            continue
        n = norm_code(c)
        if n and c not in d.setdefault(n, []):
            d[n].append(c)
    return d


def assign_by_norm(out, codes_by_norm, row_code, value):
    """Gán `value` (lấy từ 1 dòng DB có mã row_code) cho mọi mã gốc cùng dạng chuẩn hoá.
    Dòng khớp CHÍNH XÁC mã gốc luôn được ưu tiên hơn dòng chỉ khớp chuẩn hoá."""
    for c in codes_by_norm.get(norm_code(row_code), []):
        if c == row_code or c not in out:
            out[c] = value


def find_norm_collisions_sql(table, col='part_code'):
    """SQL liệt kê các nhóm mã KHÁC nhau nhưng trùng sau khi chuẩn hoá (để rà
    soát trước khi áp dụng). Chỉ trả chuỗi SQL, không chạy."""
    if not _SAFE_IDENT.match(table) or not _SAFE_IDENT.match(col):
        raise ValueError('Tên bảng/cột không hợp lệ.')
    return ('SELECT %s AS norm, array_agg(DISTINCT %s) AS codes FROM %s '
            'GROUP BY 1 HAVING COUNT(DISTINCT %s) > 1 ORDER BY 1' % (n_sql(col), col, table, col))
