"""
=============================================================================
DỰNG SƠ ĐỒ KHO 3D TỰ ĐỘNG TỪ "VỊ TRÍ HÀNG HÓA" (part_locations)
=============================================================================
File này KHÔNG import app/flask nên chạy/kiểm thử độc lập được. Được gọi từ
warehouse3d.py (route build-from-locations).

QUY TẮC ĐỌC MÃ VỊ TRÍ  (chữ cái = KỆ)
  B321      -> Kệ B, Tầng 3, Ô 2, Ngăn 1      (1 số = 1 chiều)
  B-3-2-1   -> như trên  (B3-2-1, B 3-2-1 cũng được)
  B32       -> Kệ B, Tầng 3, Ô 2              (không ghi ngăn -> Ngăn 1)
  B3        -> Kệ B, Tầng 3, Ô 1              (kệ chỉ có 1 ô mỗi tầng)
  C1117     -> Kệ C, Tầng 1, Ô 1, Ngăn 17     (từ chữ số thứ 3 trở đi = số ngăn)
  B1-2-15   -> Kệ B, Tầng 1, Ô 2, Ngăn 15     (có dấu '-' thì mỗi nhóm là 1 số)
  LẦU 1 B54 -> Kệ B của LẦU 1, Tầng 5, Ô 4
  Không ghi "LẦU n" ở đầu  -> Tầng trệt (hoặc lầu do admin chọn làm mặc định)

NGĂN = 0 (VD B310, C210, F330): chưa rõ nghĩa -> đặt vào Ngăn 1 của đúng ô đó.

BỎ QUA (không phải kệ nhiều tầng/ô): mã có từ 2 chữ cái trở lên (CB19, CB-9,
THÙNG, KỆ NHỚT, TRUNGBAY...), mã không có chữ cái, và mã có tầng/ô/ngăn vượt
giới hạn của sơ đồ 3D (tối đa 20). Danh sách bị bỏ qua được trả về để xem lại.

CÙNG MÃ Ở NHIỀU LẦU (B321 ở trệt và B321 ở Lầu 1): bảng warehouse_shelves có
ràng buộc UNIQUE(store_code, code) nên mã kệ phải khác nhau giữa các lầu:
  Tầng trệt -> "B"       Lầu 1 -> "L1-B"       Lầu 2 -> "L2-B" ...

KHÔNG GHI NGƯỢC part_locations: chỉ thêm kệ/mã hàng vào các bảng warehouse_*.
=============================================================================
"""
import re
import unicodedata
from collections import Counter, defaultdict


MAX_LEVELS = 20
MAX_CELLS = 20
MAX_SLOTS = 20
MAX_FLOOR_SIZE = 200

CELL_W = 0.8        # bề rộng mỗi ô (m)
LEVEL_H = 0.45      # chiều cao mỗi tầng (m)
SHELF_DEPTH = 0.6
GAP_X = 0.3         # khoảng hở giữa 2 kệ cùng hàng
AISLE_Z = 1.2       # lối đi giữa 2 hàng kệ
MARGIN = 1.0

_FLOOR_PREFIX = re.compile(r'^(?:LẦU|LAU)\s*(\d+)\s*[-:]?\s*(.*)$')
_GROUND_PREFIX = re.compile(r'^(?:TẦNG\s+)?TRỆT\s*[-:]?\s*(.*)$')
_SHELF_CODE = re.compile(r'^([A-Z])\s*-?\s*(\d+)(?:\s*-\s*(\d+))?(?:\s*-\s*(\d+))?$')


def _norm(raw):
    s = unicodedata.normalize('NFC', str(raw or '')).upper()
    return re.sub(r'\s+', ' ', s).strip()


def parse_location(raw, default_floor=0):
    """Trả về (dict, None) nếu đọc được, hoặc (None, lý do) nếu bỏ qua.
    dict: floor (0 = trệt), shelf (chữ cái), level, cell, slot."""
    s = _norm(raw)
    if not s:
        return None, 'trống'
    floor = default_floor
    m = _FLOOR_PREFIX.match(s)
    if m:
        floor, s = int(m.group(1)), m.group(2).strip()
    else:
        m = _GROUND_PREFIX.match(s)
        if m:
            floor, s = 0, m.group(1).strip()
    m = _SHELF_CODE.match(s)
    if not m:
        return None, 'không phải mã kệ (chữ cái + số)'
    letter, a, b, c = m.groups()
    if b is None:
        level = int(a[0])
        cell = int(a[1]) if len(a) > 1 else 1
        slot = int(a[2:]) if len(a) > 2 else 1
    else:
        level, cell, slot = int(a), int(b), int(c) if c else 1
    if level < 1 or cell < 1:
        return None, 'tầng hoặc ô bằng 0'
    # Ngăn = 0 (VD B310, C210): chưa rõ nghĩa -> đặt vào Ngăn 1 của đúng ô đó
    # (tầng/ô vẫn đúng), có đánh dấu slot_zero để báo cáo số lượng.
    slot_zero = slot == 0
    if slot_zero:
        slot = 1
    if level > MAX_LEVELS or cell > MAX_CELLS or slot > MAX_SLOTS:
        return None, 'vượt giới hạn %d tầng/%d ô/%d ngăn' % (MAX_LEVELS, MAX_CELLS, MAX_SLOTS)
    return {'floor': floor, 'shelf': letter, 'level': level, 'cell': cell, 'slot': slot,
            'slot_zero': slot_zero}, None


def shelf_code(floor, letter):
    return letter if floor == 0 else 'L%d-%s' % (floor, letter)


def floor_name(floor):
    return 'Tầng trệt' if floor == 0 else 'Lầu %d' % floor


def build_plan(rows, default_floor=0):
    """rows: iterable (part_code, location_1, location_2, location_3).
    Trả về plan: {'shelves': {(floor, letter): {...}}, 'skipped': Counter, ...}"""
    shelves = {}
    skipped = Counter()
    reasons = Counter()
    total = mapped = zero_slot = 0
    for part_code, *locs in rows:
        for raw in locs:
            if not raw or not str(raw).strip():
                continue
            total += 1
            parsed, why = parse_location(raw, default_floor)
            if not parsed:
                skipped[_norm(raw)] += 1
                reasons[why] += 1
                continue
            mapped += 1
            zero_slot += parsed['slot_zero']
            key = (parsed['floor'], parsed['shelf'])
            sh = shelves.setdefault(key, {'levels': 0, 'cells': 0, 'need': defaultdict(int), 'items': set()})
            sh['levels'] = max(sh['levels'], parsed['level'])
            sh['cells'] = max(sh['cells'], parsed['cell'])
            ck = (parsed['level'], parsed['cell'])
            sh['need'][ck] = max(sh['need'][ck], parsed['slot'])
            sh['items'].add((parsed['level'], parsed['cell'], parsed['slot'], part_code))
    return {'shelves': shelves, 'skipped': skipped, 'reasons': reasons,
            'total': total, 'mapped': mapped, 'zero_slot': zero_slot}


def _default_slots(need):
    """Số ngăn mặc định của kệ = giá trị phổ biến nhất giữa các ô (hoà -> lớn hơn)."""
    if not need:
        return 1
    cnt = Counter(need.values())
    return max(cnt, key=lambda v: (cnt[v], v))


def _shelf_size(levels, cells):
    return (min(30.0, max(0.6, round(cells * CELL_W, 2))),
            min(30.0, max(1.0, round(levels * LEVEL_H, 2))))


def _find_floor(floors, floor_no):
    """Tìm lầu có sẵn theo tên: 'trệt' / 'Lầu n'."""
    for f in floors:
        name = _norm(f['name'])
        if floor_no == 0 and 'TRỆT' in name:
            return f
        if floor_no > 0 and re.fullmatch(r'(?:LẦU|LAU)\s*%d' % floor_no, name):
            return f
    return None


def run_import(cursor, store_code, plan, actor, now, apply=False):
    """Xem trước (apply=False) hoặc ghi vào warehouse_floors/shelves/items.
    Chỉ THÊM và MỞ RỘNG: không xoá, không thu nhỏ, không dời kệ có sẵn."""
    cursor.execute('SELECT * FROM warehouse_floors WHERE store_code = %s ORDER BY floor_order, id', (store_code,))
    floors = cursor.fetchall()
    cursor.execute('SELECT * FROM warehouse_shelves WHERE store_code = %s', (store_code,))
    shelf_by_code = {r['code']: r for r in cursor.fetchall()}

    by_floor = defaultdict(list)
    for (fl, letter), sh in sorted(plan['shelves'].items()):
        by_floor[fl].append((letter, sh))

    report_floors = []
    totals = Counter()
    created_codes = set()

    for fl in sorted(by_floor):
        frow = _find_floor(floors, fl)
        entries = by_floor[fl]
        floor_info = {'floor': fl, 'name': frow['name'] if frow else floor_name(fl),
                      'exists': bool(frow), 'shelves': []}

        # --- phân loại từng kệ: mới / mở rộng / xung đột mã
        new_shelves = []
        for letter, sh in entries:
            code = shelf_code(fl, letter)
            existing = shelf_by_code.get(code)
            item_n = len(sh['items'])
            info = {'code': code, 'levels': sh['levels'], 'cells': sh['cells'],
                    'items': item_n, 'action': 'new'}
            if existing is not None:
                if frow is None or existing['floor_id'] != frow['id'] or existing['shelf_type'] != 'shelf':
                    info['action'] = 'conflict'
                    info['note'] = 'Mã kệ này đã tồn tại ở lầu/loại khác - bỏ qua'
                    totals['conflict_shelves'] += 1
                else:
                    info['action'] = 'extend'
                    info['levels'] = max(sh['levels'], existing['levels'])
                    info['cells'] = max(sh['cells'], existing['cells_per_level'])
            else:
                new_shelves.append((letter, sh, info))
            floor_info['shelves'].append(info)

        if apply:
            # --- lầu: tạo nếu chưa có
            if frow is None:
                cursor.execute('''
                    INSERT INTO warehouse_floors (store_code, name, floor_order, floor_width, floor_depth, updated_by, updated_at)
                    VALUES (%s, %s, %s, 20, 15, %s, %s) RETURNING *
                ''', (store_code, floor_name(fl), fl + 1, actor, now))
                frow = cursor.fetchone()
                floors.append(frow)
                floor_info['exists'] = True
                totals['floors_created'] += 1

            # --- đặt chỗ cho kệ mới (xếp hàng ngang, hết chiều rộng thì xuống hàng)
            cursor.execute('SELECT pos_z, depth FROM warehouse_shelves WHERE floor_id = %s', (frow['id'],))
            ext = cursor.fetchall()
            z_cursor = max([float(r['pos_z']) + float(r['depth']) / 2 for r in ext] + [0]) + (AISLE_Z if ext else MARGIN)
            floor_w = float(frow['floor_width'])
            sizes = {letter: _shelf_size(sh['levels'], sh['cells']) for letter, sh, _ in new_shelves}
            if frow['floor_shape'] is None and sizes:
                floor_w = max(floor_w, max(w for w, _ in sizes.values()) + 2 * MARGIN)
            x = MARGIN
            row_h = 0.0
            placed = []
            for letter, sh, info in new_shelves:
                w, h = sizes[letter]
                if x + w > floor_w - MARGIN and x > MARGIN:
                    x = MARGIN
                    z_cursor += row_h + AISLE_Z
                    row_h = 0.0
                placed.append((letter, sh, info, x + w / 2, z_cursor + SHELF_DEPTH / 2, w, h))
                x += w + GAP_X
                row_h = max(row_h, SHELF_DEPTH)
            need_depth = z_cursor + row_h + MARGIN

            if frow['floor_shape'] is None:
                new_w = min(float(MAX_FLOOR_SIZE), round(max(floor_w, float(frow['floor_width'])), 1))
                new_d = min(float(MAX_FLOOR_SIZE), round(max(need_depth, float(frow['floor_depth'])), 1))
                if new_w != float(frow['floor_width']) or new_d != float(frow['floor_depth']):
                    cursor.execute('UPDATE warehouse_floors SET floor_width=%s, floor_depth=%s, updated_by=%s, updated_at=%s WHERE id=%s',
                                   (new_w, new_d, actor, now, frow['id']))

            for letter, sh, info, px, pz, w, h in placed:
                code = info['code']
                slots = _default_slots(sh['need'])
                label = 'Kệ %s%s' % (letter, '' if fl == 0 else ' - ' + floor_name(fl))
                cursor.execute('''
                    INSERT INTO warehouse_shelves
                        (store_code, floor_id, code, label, shelf_type, pos_x, pos_z, width, height, depth,
                         rotation_y, levels, cells_per_level, slots_per_cell,
                         created_by, updated_by, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,'shelf',%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s,%s) RETURNING *
                ''', (store_code, frow['id'], code, label, round(px, 2), round(pz, 2), w, h, SHELF_DEPTH,
                      sh['levels'], sh['cells'], slots, actor, actor, now, now))
                shelf_by_code[code] = cursor.fetchone()
                created_codes.add(code)
                totals['shelves_created'] += 1

            # --- mở rộng kệ có sẵn (chỉ tăng)
            for letter, sh in entries:
                code = shelf_code(fl, letter)
                existing = shelf_by_code.get(code)
                if (existing is None or code in created_codes
                        or existing['floor_id'] != frow['id'] or existing['shelf_type'] != 'shelf'):
                    continue
                lv, ce = max(sh['levels'], existing['levels']), max(sh['cells'], existing['cells_per_level'])
                if lv != existing['levels'] or ce != existing['cells_per_level']:
                    cursor.execute('UPDATE warehouse_shelves SET levels=%s, cells_per_level=%s, updated_by=%s, updated_at=%s WHERE id=%s',
                                   (lv, ce, actor, now, existing['id']))
                    existing['levels'], existing['cells_per_level'] = lv, ce
                    totals['shelves_extended'] += 1

            # --- số ngăn riêng từng ô + gán mã hàng
            for letter, sh in entries:
                code = shelf_code(fl, letter)
                srow = shelf_by_code.get(code)
                if srow is None or srow['floor_id'] != frow['id'] or srow['shelf_type'] != 'shelf':
                    continue
                is_new = code in created_codes
                cursor.execute('SELECT level_index, cell_index, slots FROM warehouse_shelf_cells WHERE shelf_id = %s', (srow['id'],))
                ov = {(r['level_index'], r['cell_index']): r['slots'] for r in cursor.fetchall()}
                default = srow['slots_per_cell']
                for (lv, ce), need in sh['need'].items():
                    cur = ov.get((lv, ce), default)
                    # Kệ mới: ô nào khác mặc định thì lưu số ngăn riêng.
                    # Kệ có sẵn: chỉ TĂNG số ngăn khi dữ liệu cần nhiều hơn.
                    if (is_new and need != default) or (not is_new and need > cur):
                        cursor.execute('''
                            INSERT INTO warehouse_shelf_cells (shelf_id, level_index, cell_index, slots)
                            VALUES (%s,%s,%s,%s)
                            ON CONFLICT (shelf_id, level_index, cell_index) DO UPDATE SET slots = EXCLUDED.slots
                        ''', (srow['id'], lv, ce, need))
                rows_ins = [(srow['id'], lv, ce, sl, pc, actor, now) for (lv, ce, sl, pc) in sh['items']]
                if rows_ins:
                    from psycopg2.extras import execute_values
                    execute_values(cursor, '''
                        INSERT INTO warehouse_shelf_items (shelf_id, level_index, cell_index, slot_index, part_code, updated_by, updated_at)
                        VALUES %s
                        ON CONFLICT (shelf_id, level_index, cell_index, slot_index, part_code) DO NOTHING
                    ''', rows_ins, page_size=2000)
                    totals['items_processed'] += len(rows_ins)

        report_floors.append(floor_info)

    return {
        'floors': report_floors,
        'totals': dict(totals),
        'stats': {'locations': plan['total'], 'mapped': plan['mapped'],
                  'skipped': plan['total'] - plan['mapped'],
                  'slot_zero_as_1': plan['zero_slot']},
        'skipped_top': [{'text': t, 'count': n} for t, n in plan['skipped'].most_common(15)],
        'skipped_reasons': dict(plan['reasons']),
    }
