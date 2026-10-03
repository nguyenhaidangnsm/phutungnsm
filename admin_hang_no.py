# -*- coding: utf-8 -*-
"""
Module VÙNG TÍNH TOÁN HÀNG NỢ RIÊNG CỦA ADMIN (menu "Hàng Nợ (Admin)").

Cùng kiểu gom_don_hang.py: blueprint độc lập, bảng tự tạo ở lần dùng đầu tiên
(CREATE TABLE IF NOT EXISTS) nên KHÔNG cần sửa init_db().

MÓC NỐI VÀO app.py (cạnh gom_don_hang, TRƯỚC _init_db_with_retry()):

    from admin_hang_no import admin_hang_no_bp
    app.register_blueprint(admin_hang_no_bp)

Ý TƯỞNG
    Bảng đối soát PO "hàng nợ" của cửa hàng (app.py: process_data) dựa trên 3 file cửa hàng tự tải lên:
    Danh sách PO / Chi tiết PO / Chi tiết nhận hàng. Admin không tải file thay cửa hàng được.
    Module này cho admin 1 VÙNG RIÊNG (owner = tên đăng nhập admin): admin đổ 3 file cho TỪNG cửa hàng
    vào vùng của mình rồi tự tính / tự xem bảng hàng nợ bằng ĐÚNG hàm process_data của app.py.
    Dữ liệu ở vùng này tách hoàn toàn với dữ liệu thật của cửa hàng (latest_uploads, po_detail_items):
    đổ / xoá ở đây không ảnh hưởng cửa hàng, cửa hàng cũng không thấy. Mỗi admin 1 vùng riêng.

CÁCH LƯU (giống app.py)
    - Danh sách PO, Chi tiết nhận hàng: mỗi lần đổ file mới thì THAY THẾ bản cũ của cửa hàng đó.
    - Chi tiết PO: GHI THÊM (cộng dồn), tự bỏ qua dòng trùng (Mã PO + Mã phụ tùng + Số lượng);
      tự dọn dòng quá PO_DETAIL_RETENTION_DAYS ngày.
"""
import threading
import traceback

from flask import Blueprint, request, jsonify, session
import pandas as pd
from psycopg2.extras import execute_values

from audit_log import audit_record
from app import (get_db, _valid_store_codes, process_data, read_any, find_col, clean_str,
                 dumps_json, loads_json, get_summary_from_data, vn_now, PO_DETAIL_RETENTION_DAYS)
from gom_don_hang import _send_xlsx

admin_hang_no_bp = Blueprint('admin_hang_no', __name__)

_MAX_ROWS = 20000          # số dòng tối đa trả về 1 lần cho giao diện


# ----------------------------------------------------------------------------
# BẢNG
# ----------------------------------------------------------------------------
_tables_ready = False
_tables_lock = threading.Lock()


def _ensure_tables(db):
    global _tables_ready
    if _tables_ready:
        return
    with _tables_lock:
        if _tables_ready:
            return
        cur = db.cursor()
        try:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS ahn_uploads (
                    owner VARCHAR(100) NOT NULL,              -- tên đăng nhập admin (vùng riêng của admin đó)
                    store_code VARCHAR(20) NOT NULL,
                    ds_po_filename TEXT, ds_po_json TEXT, ds_po_time TIMESTAMP,
                    receipt_filename TEXT, receipt_json TEXT, receipt_time TIMESTAMP,
                    PRIMARY KEY (owner, store_code)
                )''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS ahn_po_detail (
                    id SERIAL PRIMARY KEY,
                    owner VARCHAR(100) NOT NULL,
                    store_code VARCHAR(20) NOT NULL,
                    po_code VARCHAR(100) NOT NULL,
                    part_code VARCHAR(100) NOT NULL,
                    quantity NUMERIC NOT NULL DEFAULT 0,
                    filename TEXT,
                    upload_time TIMESTAMP NOT NULL DEFAULT NOW()
                )''')
            cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS ahn_po_detail_uq
                           ON ahn_po_detail (owner, store_code, po_code, part_code, quantity)''')
            cur.execute('CREATE INDEX IF NOT EXISTS ahn_po_detail_owner_store ON ahn_po_detail (owner, store_code)')
            db.commit()
            _tables_ready = True
        except Exception:
            db.rollback()
            raise
        finally:
            cur.close()


def _ctx():
    db = get_db()
    _ensure_tables(db)
    return db, db.cursor()


def _need_admin():
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Chỉ admin mới dùng được chức năng này.'}), 403
    return None


def _owner():
    return str(session.get('user') or '')


def _fmt_dt(v):
    return v.strftime('%d/%m/%Y %H:%M') if v else None


def _store_arg(cur, allow_all=False):
    """(store_code, None) hoặc (None, (response, code)). allow_all: để trống = tất cả cửa hàng đã đổ ('')."""
    code = (request.values.get('store') or '').strip().upper()
    if not code:
        if allow_all:
            return '', None
        return None, (jsonify({'error': 'Vui lòng chọn cửa hàng.'}), 400)
    if code not in _valid_store_codes(cur):
        return None, (jsonify({'error': 'Cửa hàng không hợp lệ.'}), 400)
    return code, None


# ----------------------------------------------------------------------------
# TÍNH BẢNG HÀNG NỢ CỦA 1 CỬA HÀNG TRONG VÙNG ADMIN
# ----------------------------------------------------------------------------
def _compute_store(cur, owner, store):
    """(list dòng, None) hoặc ([], thông báo lý do chưa tính được)."""
    cur.execute('SELECT ds_po_json, receipt_json FROM ahn_uploads WHERE owner = %s AND store_code = %s', (owner, store))
    up = cur.fetchone()
    ds_rec = loads_json(up['ds_po_json']) if up and up['ds_po_json'] else []
    rc_rec = loads_json(up['receipt_json']) if up and up['receipt_json'] else []
    cur.execute('SELECT po_code, part_code, quantity FROM ahn_po_detail WHERE owner = %s AND store_code = %s '
                'ORDER BY upload_time DESC', (owner, store))
    det = cur.fetchall()
    missing = [n for n, ok in (('Danh sách PO', ds_rec), ('Chi tiết PO', det), ('Chi tiết nhận hàng', rc_rec)) if not ok]
    if missing:
        return [], 'Chưa đổ: ' + ', '.join(missing) + '.'
    detail_df = pd.DataFrame(det, columns=['po_code', 'part_code', 'quantity'])
    detail_df['quantity'] = pd.to_numeric(detail_df['quantity'], errors='coerce').fillna(0.0).astype(float)
    try:
        df = process_data(pd.DataFrame(ds_rec), detail_df, pd.DataFrame(rc_rec))
    except ValueError as e:
        return [], str(e)
    return df.to_dict(orient='records'), None


def _compute(cur, owner, store):
    """store = '' -> gộp mọi cửa hàng admin đã đổ. Trả (rows, notes {store: lý do}, stores_used)."""
    if store:
        stores = [store]
    else:
        cur.execute('SELECT store_code FROM ahn_uploads WHERE owner = %s UNION '
                    'SELECT store_code FROM ahn_po_detail WHERE owner = %s ORDER BY 1', (owner, owner))
        stores = [r['store_code'] for r in cur.fetchall()]
    rows, notes = [], {}
    for sc in stores:
        data, why = _compute_store(cur, owner, sc)
        if why:
            notes[sc] = why
        for r in data:
            r['store_code'] = sc
            rows.append(r)
    return rows, notes, stores


# ----------------------------------------------------------------------------
# API
# ----------------------------------------------------------------------------
@admin_hang_no_bp.route('/api/admin-hang-no/stores', methods=['GET'])
def ahn_stores():
    """Danh sách cửa hàng + tình trạng dữ liệu admin đã đổ ở vùng riêng của mình."""
    blk = _need_admin()
    if blk:
        return blk
    db, cur = _ctx()
    try:
        owner = _owner()
        cur.execute('SELECT * FROM ahn_uploads WHERE owner = %s', (owner,))
        up = {r['store_code']: r for r in cur.fetchall()}
        cur.execute('SELECT store_code, COUNT(*) AS n, MAX(upload_time) AS at FROM ahn_po_detail '
                    'WHERE owner = %s GROUP BY store_code', (owner,))
        det = {r['store_code']: r for r in cur.fetchall()}
        out = []
        for sc in sorted(_valid_store_codes(cur)):
            u, d = up.get(sc), det.get(sc)
            out.append({'store': sc,
                        'ds_po': {'file': u['ds_po_filename'], 'at': _fmt_dt(u['ds_po_time'])} if u and u['ds_po_json'] else None,
                        'po_detail': {'rows': d['n'], 'at': _fmt_dt(d['at'])} if d else None,
                        'receipt': {'file': u['receipt_filename'], 'at': _fmt_dt(u['receipt_time'])} if u and u['receipt_json'] else None})
    finally:
        cur.close()
    return jsonify({'success': True, 'stores': out})


@admin_hang_no_bp.route('/api/admin-hang-no/upload', methods=['POST'])
def ahn_upload():
    """Admin đổ 1 / 2 / 3 file cho 1 cửa hàng vào VÙNG RIÊNG của mình (không đụng dữ liệu thật của cửa hàng)."""
    blk = _need_admin()
    if blk:
        return blk
    ds_f, det_f, rc_f = (request.files.get(k) for k in ('ds_po_file', 'po_detail_file', 'receipt_file'))
    if not (ds_f or det_f or rc_f):
        return jsonify({'error': 'Vui lòng chọn ít nhất một file để đổ.'}), 400
    db, cur = _ctx()
    try:
        store, err = _store_arg(cur)
        if err:
            return err
        owner, now = _owner(), vn_now()
        done, inserted, skipped = [], None, None

        if ds_f:                                    # Danh sách PO: thay thế
            df = read_any(ds_f)
            cur.execute('''INSERT INTO ahn_uploads (owner, store_code, ds_po_filename, ds_po_json, ds_po_time)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (owner, store_code) DO UPDATE SET ds_po_filename = EXCLUDED.ds_po_filename,
                               ds_po_json = EXCLUDED.ds_po_json, ds_po_time = EXCLUDED.ds_po_time''',
                        (owner, store, ds_f.filename, dumps_json(df.to_dict(orient='records')), now))
            done.append('Danh sách PO')
        if rc_f:                                    # Chi tiết nhận hàng: thay thế
            df = read_any(rc_f)
            cur.execute('''INSERT INTO ahn_uploads (owner, store_code, receipt_filename, receipt_json, receipt_time)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (owner, store_code) DO UPDATE SET receipt_filename = EXCLUDED.receipt_filename,
                               receipt_json = EXCLUDED.receipt_json, receipt_time = EXCLUDED.receipt_time''',
                        (owner, store, rc_f.filename, dumps_json(df.to_dict(orient='records')), now))
            done.append('Chi tiết nhận hàng')
        if det_f:                                   # Chi tiết PO: cộng dồn, bỏ dòng trùng
            inserted, skipped = _append_po_detail(cur, owner, store, det_f, read_any(det_f), now)
            done.append('Chi tiết PO')
        cur.execute("DELETE FROM ahn_po_detail WHERE upload_time < NOW() - (%s || ' days')::interval",
                    (PO_DETAIL_RETENTION_DAYS,))
        db.commit()
        audit_record('Đổ dữ liệu hàng nợ (vùng admin)', 'Hàng nợ admin', target=store,
                     summary=f"{store}: đã đổ {', '.join(done)}")
    except ValueError as e:                         # thiếu cột bắt buộc...
        db.rollback()
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.rollback()
        _log_error('upload', e)
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
    return jsonify({'success': True, 'done': done, 'po_detail_inserted': inserted, 'po_detail_skipped': skipped})


def _append_po_detail(cur, owner, store, f, df, now):
    """Giống app.append_po_detail nhưng ghi vào ahn_po_detail của vùng admin. Trả (số dòng ghi mới, số dòng bỏ qua)."""
    po_c = find_col(df.columns, ['order number', 'mã đơn hàng mua', 'mã đơn hàng', 'mã po', 'po'])
    part_c = find_col(df.columns, ['part#', 'part #', 'part number', 'mã phụ tùng', 'part'])
    qty_c = find_col(df.columns, ['quantity requested', 'số lượng yêu cầu', 'số lượng', 'quantity'])
    if not po_c or not part_c:
        raise ValueError("File Chi tiết PO thiếu cột 'Mã PO' hoặc 'Mã phụ tùng'.")
    if not qty_c:
        raise ValueError("File Chi tiết PO thiếu cột 'Số lượng' (Quantity Requested).")
    d = df.copy()
    d['_po'] = d[po_c].map(clean_str)
    d['_part'] = d[part_c].map(clean_str)
    d['_qty'] = pd.to_numeric(d[qty_c], errors='coerce').fillna(0.0).astype(float)
    d = d[(d['_po'] != '') & (d['_part'] != '')].drop_duplicates(subset=['_po', '_part', '_qty'], keep='first')
    total = len(d)
    if total == 0:
        return 0, 0
    ins = execute_values(
        cur, '''INSERT INTO ahn_po_detail (owner, store_code, po_code, part_code, quantity, filename, upload_time)
                VALUES %s ON CONFLICT (owner, store_code, po_code, part_code, quantity) DO NOTHING RETURNING id''',
        [(owner, store, po, pt, q, f.filename, now) for po, pt, q in zip(d['_po'], d['_part'], d['_qty'])],
        fetch=True)
    return len(ins), total - len(ins)


@admin_hang_no_bp.route('/api/admin-hang-no/data', methods=['GET'])
def ahn_data():
    """Bảng hàng nợ của 1 cửa hàng (hoặc tất cả cửa hàng đã đổ) trong vùng riêng của admin."""
    blk = _need_admin()
    if blk:
        return blk
    db, cur = _ctx()
    try:
        store, err = _store_arg(cur, allow_all=True)
        if err:
            return err
        rows, notes, stores = _compute(cur, _owner(), store)
    finally:
        cur.close()
    truncated = len(rows) > _MAX_ROWS
    return jsonify({'success': True, 'store': store, 'stores': stores, 'notes': notes,
                    'summary': get_summary_from_data(rows), 'data': rows[:_MAX_ROWS], 'truncated': truncated,
                    'total_rows': len(rows)})


@admin_hang_no_bp.route('/api/admin-hang-no/clear', methods=['POST'])
def ahn_clear():
    """Xoá dữ liệu đã đổ của 1 cửa hàng trong vùng riêng của admin (không ảnh hưởng dữ liệu thật của cửa hàng)."""
    blk = _need_admin()
    if blk:
        return blk
    db, cur = _ctx()
    try:
        store, err = _store_arg(cur)
        if err:
            return err
        cur.execute('DELETE FROM ahn_uploads WHERE owner = %s AND store_code = %s', (_owner(), store))
        cur.execute('DELETE FROM ahn_po_detail WHERE owner = %s AND store_code = %s', (_owner(), store))
        db.commit()
        audit_record('Xoá dữ liệu hàng nợ (vùng admin)', 'Hàng nợ admin', target=store,
                     summary=f'{store}: xoá dữ liệu đã đổ ở vùng riêng của admin')
    except Exception as e:
        db.rollback()
        _log_error('clear', e)
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
    return jsonify({'success': True})


@admin_hang_no_bp.route('/api/admin-hang-no/export', methods=['GET'])
def ahn_export():
    blk = _need_admin()
    if blk:
        return blk
    db, cur = _ctx()
    try:
        store, err = _store_arg(cur, allow_all=True)
        if err:
            return err
        rows, _notes, _stores = _compute(cur, _owner(), store)
    finally:
        cur.close()
    if not rows:
        return jsonify({'error': 'Chưa có dữ liệu hàng nợ để xuất.'}), 400
    df = pd.DataFrame([{
        'Cửa hàng': r['store_code'], 'Mã PO': r['po_code'], 'Ngày đặt': r['order_date'], 'Loại đơn': r['order_type'],
        'Mã phụ tùng': r['part_code'], 'Trạng thái': r['status'], 'Số ngày nợ': r['days_debt'],
        'SL nợ': r['qty_debt'], 'Tổng SL nợ của mã': r['qty_debt_total'],
    } for r in rows])
    return _send_xlsx({'Hàng nợ': df}, f"hang-no-admin-{store or 'tat-ca'}.xlsx")


def _log_error(where, e):
    try:
        from app import app as _app
        _app.logger.error('Lỗi admin_hang_no/%s: %s\n%s', where, e, traceback.format_exc())
    except Exception:
        pass
