# -*- coding: utf-8 -*-
"""
Entry point để CHẠY server - dùng file này thay vì chạy trực tiếp app.py.

TẠI SAO CẦN FILE NÀY:
Khi chạy `python3 app.py` trực tiếp, Python nạp app.py với tên module là
`__main__` (KHÔNG phải `app`). Nhưng stocktake.py lại có dòng
`from app import get_db, ...`, tức là nó cần 1 module tên `app` trong
sys.modules. Vì không có, Python sẽ NẠP LẠI app.py lần thứ 2 từ đầu (lần
này với tên đúng là `app`) để lấy các hàm đó. Lần nạp thứ 2 này chạy lại
toàn bộ app.py từ đầu, kể cả dòng `from stocktake import stocktake_bp`
-- nhưng lúc đó module stocktake vẫn đang dở dang (đang ở dòng
`from app import ...` của chính nó, chưa chạy tới dòng định nghĩa
`stocktake_bp`), nên gây ra lỗi:

    ImportError: cannot import name 'stocktake_bp' from 'stocktake'

Bằng cách chạy qua run.py, app.py CHỈ ĐƯỢC IMPORT (không bao giờ chạy như
__main__), nên chỉ có ĐÚNG 1 bản app.py được nạp, và stocktake.py có thể
lấy get_db/vn_now/... từ module `app` đang nạp dở đó (các hàm này đã được
định nghĩa ở phía trên dòng import stocktake trong app.py) mà không cần
nạp lại từ đầu.

CÁCH DÙNG: thay vì `python3 app.py`, chạy `python3 run.py`.
"""
import os
from app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # threaded=True: server dev mặc định của Flask chỉ xử lý ĐÚNG 1 request
    # tại 1 thời điểm. Trang stocktake.html gọi loadCurrent() và
    # loadHistory() gần như cùng lúc (không đợi nhau), nên nếu 1 trong 2
    # request đó bị chậm vì bất kỳ lý do gì (mạng tới Supabase chậm, v.v.),
    # MỌI request khác - kể cả request "Bắt Đầu Kiểm Kê" - sẽ bị "xếp hàng"
    # chờ vô thời hạn, y hệt triệu chứng "Pending" mãi không xong đã gặp.
    # Bật threaded=True để mỗi request được xử lý trên 1 luồng riêng.
    app.run(host="0.0.0.0", port=port, threaded=True)