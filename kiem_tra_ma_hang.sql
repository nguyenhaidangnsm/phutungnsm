-- =====================================================================
-- KIỂM TRA MÃ HÀNG TRƯỚC/SAU KHI CHUẨN HOÁ  (chỉ ĐỌC, không sửa dữ liệu)
-- Quy tắc chuẩn hoá: bỏ ký tự không phải chữ/số + viết HOA
--   regexp_replace(upper(part_code), '[^A-Z0-9]', '', 'g')
-- =====================================================================

-- ---------- A. CHẠY TRÊN DB CHÍNH (DATABASE_URL) ----------

-- A1. Các nhóm mã KHÁC nhau nhưng TRÙNG sau khi chuẩn hoá (đây là nhóm "mơ hồ":
--     resolve_codes sẽ KHÔNG tự đổi các mã này). Kết quả lý tưởng: 0 dòng.
SELECT 'inventory_items' AS bang,
       regexp_replace(upper(part_code), '[^A-Z0-9]', '', 'g') AS ma_chuan_hoa,
       array_agg(DISTINCT part_code) AS cac_ma
FROM inventory_items
GROUP BY 2 HAVING COUNT(DISTINCT part_code) > 1
UNION ALL
SELECT 'part_prices', regexp_replace(upper(part_code), '[^A-Z0-9]', '', 'g'),
       array_agg(DISTINCT part_code)
FROM part_prices
GROUP BY 2 HAVING COUNT(DISTINCT part_code) > 1
ORDER BY 1, 2;

-- A2. Mã trong bảng khác có trong tồn kho theo dạng chuẩn hoá nhưng KHÁC cách viết
--     (nếu có dòng = bấm nút so khớp chính xác cũ sẽ trượt, bản mới thì trúng).
SELECT 'part_prices' AS bang, p.part_code AS ma_trong_bang, i.ma_ton_kho
FROM part_prices p
JOIN (SELECT DISTINCT part_code AS ma_ton_kho,
             regexp_replace(upper(part_code), '[^A-Z0-9]', '', 'g') AS n
      FROM inventory_items) i
  ON i.n = regexp_replace(upper(p.part_code), '[^A-Z0-9]', '', 'g')
WHERE p.part_code <> i.ma_ton_kho
LIMIT 200;

-- A3. Kiểm tra Postgres có dùng index chuẩn hoá không (phải thấy "Index Scan"/"Bitmap"
--     với tên idx_..._norm, KHÔNG phải Seq Scan trên bảng lớn).
EXPLAIN SELECT part_code FROM inventory_items
WHERE regexp_replace(upper(part_code), '[^A-Z0-9]', '', 'g') = ANY(ARRAY['06410KFL850']);

-- ---------- B. ĐỐI CHIẾU bo_orders (DB ĐẶT HÀNG) VỚI TỒN KHO (DB CHÍNH) ----------
-- Hai DB khác nhau nên không JOIN trực tiếp được. Làm 2 bước:

-- B1. CHẠY TRÊN DB ĐẶT HÀNG (ORDERS_DATABASE_URL): lấy danh sách mã đang dùng.
--     Copy kết quả (1 ô duy nhất) để dán vào B2.
SELECT string_agg(quote_literal(part_code), ',') AS danh_sach
FROM (SELECT DISTINCT part_code FROM bo_orders WHERE part_code IS NOT NULL AND part_code <> '') t;

-- B2. CHẠY TRÊN DB CHÍNH: dán danh sách từ B1 vào chỗ <DAN_DANH_SACH_VAO_DAY>.
--     Kết quả: mã trong bo_orders KHÔNG có trong tồn kho theo cách viết gốc.
--       - cot ma_chinh_thuc có giá trị  => chỉ khác cách viết (bản mới sẽ tự khớp)
--       - cot ma_chinh_thuc rỗng        => mã không có trong tồn kho (mã mới/gõ sai)
/*
WITH bo(code) AS (SELECT unnest(ARRAY[<DAN_DANH_SACH_VAO_DAY>]::text[]))
SELECT bo.code AS ma_trong_bo_orders,
       (SELECT string_agg(DISTINCT i.part_code, ', ')
        FROM inventory_items i
        WHERE regexp_replace(upper(i.part_code), '[^A-Z0-9]', '', 'g')
            = regexp_replace(upper(bo.code), '[^A-Z0-9]', '', 'g')) AS ma_chinh_thuc
FROM bo
WHERE NOT EXISTS (SELECT 1 FROM inventory_items i WHERE i.part_code = bo.code)
ORDER BY 2 NULLS LAST, 1;
*/
