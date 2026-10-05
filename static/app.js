(function () {
    const S = {all: [], view: [], page: 1, size: 100, stores: []};
    const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const $ = (id) => document.getElementById(id);
    async function api(url, opt) {
        const r = await fetch(url, opt);
        let j = {}; try { j = await r.json(); } catch (e) {}
        if (!r.ok || j.error) throw new Error(j.error || ('Lỗi ' + r.status));
        return j;
    }
    function msg(t, ok) { const e = $('ahnMsg'); e.className = 'small ' + (ok ? 'text-success' : 'text-danger'); e.textContent = t || ''; }
    const store = () => $('ahnStore').value;

    async function loadStores() {
        const j = await api('/api/admin-hang-no/stores');
        S.stores = j.stores;
        const cur = store();
        $('ahnStore').innerHTML = '<option value="">Tất cả cửa hàng đã đổ (chỉ xem)</option>' +
            j.stores.map(s => `<option value="${esc(s.store)}">${esc(s.store)}${(s.ds_po || s.po_detail || s.receipt) ? ' •' : ''}</option>`).join('');
        if (cur) $('ahnStore').value = cur;
        renderStatus();
    }
    function renderStatus() {
        const s = S.stores.find(x => x.store === store());
        if (!store()) { $('ahnStatus').textContent = 'Chọn 1 cửa hàng cụ thể để đổ / xoá dữ liệu. "•" = cửa hàng đã có dữ liệu trong vùng của bạn.'; return; }
        const f = (n, x, extra) => `${n}: ` + (x ? `<b>${esc(x.at)}</b>${extra(x)}` : '<span class="text-danger">chưa đổ</span>');
        const grp = s && s.group && s.group.length > 1
            ? `<div class="text-primary mt-1"><i class="bi bi-diagram-2 me-1"></i>${esc(s.group.join(' + '))} dùng chung 1 bảng hàng nợ: dữ liệu đổ của các cửa hàng này được gộp lại khi tính (dòng trạng thái trên chỉ là phần đổ riêng cho ${esc(s.store)}).</div>` : '';
        $('ahnStatus').innerHTML = [
            f('Danh sách PO', s && s.ds_po, x => ` (${esc(x.file)})`),
            f('Chi tiết PO', s && s.po_detail, x => ` (${x.rows} dòng)`),
            f('Chi tiết nhận hàng', s && s.receipt, x => ` (${esc(x.file)})`),
        ].join(' &nbsp;|&nbsp; ') + grp;
    }
    function renderSummary(sm) {
        const card = (t, v, cls) => `<div class="col-6 col-md"><div class="border rounded-3 p-2 bg-white h-100"><div class="small text-muted text-uppercase" style="font-size:.7rem;">${t}</div><div class="fs-5 fw-bold ${cls || ''}">${v}</div></div></div>`;
        $('ahnSummary').innerHTML = card('Tổng dòng', (sm.total || 0).toLocaleString()) + card('Đang nợ', (sm.debt || 0).toLocaleString(), 'text-danger') +
            card('Đang vận chuyển', (sm.shipping || 0).toLocaleString(), 'text-warning') + card('Đã nhận', (sm.received || 0).toLocaleString(), 'text-success') +
            card('Kỳ đặt hàng', `<span class="fs-6">${esc(sm.min_date || '-')} → ${esc(sm.max_date || '-')}</span>`);
    }
    async function loadData() {
        try {
            const j = await api('/api/admin-hang-no/data?store=' + encodeURIComponent(store()));
            S.all = j.data || [];
            renderSummary(j.summary || {});
            const notes = Object.entries(j.notes || {}).map(([k, v]) => `${esc(k)}: ${esc(v)}`);
            $('ahnNotes').innerHTML = notes.length ? '<i class="bi bi-exclamation-triangle me-1"></i>' + notes.join(' &nbsp;|&nbsp; ') : '';
            if (j.truncated) $('ahnNotes').innerHTML += ` &nbsp; (chỉ hiện ${S.all.length.toLocaleString()}/${j.total_rows.toLocaleString()} dòng, hãy chọn 1 cửa hàng hoặc xuất Excel)`;
            S.page = 1; filter();
        } catch (e) { msg(e.message, false); }
    }
    function filter() {
        const q = $('ahnSearch').value.trim().toLowerCase(), st = $('ahnStatusSel').value;
        S.view = S.all.filter(r => (!st || r.status === st) && (!q || String(r.po_code).toLowerCase().includes(q) || String(r.part_code).toLowerCase().includes(q)));
        S.page = 1; render();
    }
    function render() {
        const total = S.view.length, pages = Math.max(1, Math.ceil(total / S.size));
        S.page = Math.min(Math.max(1, S.page), pages);
        const start = (S.page - 1) * S.size, rows = S.view.slice(start, start + S.size);
        $('ahnCount').textContent = total.toLocaleString() + ' dòng';
        $('ahnBody').innerHTML = rows.length ? rows.map((r, i) => {
            const b = r.status === 'Nợ' ? 'badge-debt' : (r.status === 'Đang vận chuyển' ? 'badge-shipping' : 'badge-received');
            const open = r.status !== 'Đã nhận hàng', d = r.qty_debt || 0, t = r.qty_debt_total || 0;
            return `<tr><td class="text-center text-muted small">${start + i + 1}</td><td><span class="badge bg-light text-dark border">${esc(r.store_code)}</span></td>
                <td><strong>${esc(r.po_code)}</strong></td><td class="text-muted">${esc(r.order_date)}</td><td>${esc(r.order_type)}</td><td>${esc(r.part_code)}</td>
                <td class="text-center"><span class="badge-status ${b}">${esc(r.status)}</span></td>
                <td class="text-end">${open ? esc(r.days_debt) + ' ngày' : '-'}</td>
                <td class="text-end fw-semibold ${d > 0 ? 'text-danger' : 'text-muted'}">${d > 0 ? d.toLocaleString() : '-'}</td>
                <td class="text-end fw-bold ${t > 0 ? 'text-danger' : 'text-muted'}">${t > 0 ? t.toLocaleString() : '-'}</td></tr>`;
        }).join('') : '<tr><td colspan="10" class="text-center py-4 text-muted">Không có dữ liệu.</td></tr>';
        $('ahnPager').classList.toggle('d-none', pages <= 1);
        $('ahnPageInfo').textContent = `Trang ${S.page}/${pages}`;
    }

    window.ahnFilter = filter;
    window.ahnPage = (d) => { S.page += d; render(); };
    window.ahnStoreChanged = () => { msg(''); renderStatus(); loadData(); };
    window.ahnTabOpened = async () => { try { await loadStores(); } catch (e) { msg(e.message, false); } loadData(); };
    window.ahnExport = () => { window.location.href = '/api/admin-hang-no/export?store=' + encodeURIComponent(store()); };
    window.ahnUpload = async () => {
        if (!store()) return msg('Vui lòng chọn 1 cửa hàng cụ thể để đổ dữ liệu.', false);
        const fd = new FormData(); fd.append('store', store());
        [['ahnFileDs', 'ds_po_file'], ['ahnFileDet', 'po_detail_file'], ['ahnFileRc', 'receipt_file']].forEach(([id, k]) => { const f = $(id).files[0]; if (f) fd.append(k, f); });
        if ([...fd.keys()].length < 2) return msg('Vui lòng chọn ít nhất một file.', false);
        const btn = $('ahnUploadBtn'); btn.disabled = true; msg('Đang đổ dữ liệu...', true);
        try {
            const j = await api('/api/admin-hang-no/upload', {method: 'POST', body: fd});
            let t = 'Đã đổ: ' + j.done.join(', ') + '.';
            if (j.po_detail_inserted != null) t += ` Chi tiết PO: +${j.po_detail_inserted} dòng mới, bỏ ${j.po_detail_skipped} dòng trùng.`;
            ['ahnFileDs', 'ahnFileDet', 'ahnFileRc'].forEach(id => $(id).value = '');
            await loadStores(); await loadData(); msg(t, true);
        } catch (e) { msg(e.message, false); } finally { btn.disabled = false; }
    };
    window.ahnClear = async () => {
        if (!store()) return msg('Vui lòng chọn 1 cửa hàng cụ thể để xoá.', false);
        if (!await nsConfirm('Xoá toàn bộ dữ liệu hàng nợ đã đổ cho ' + store() + ' trong vùng riêng của bạn? (Dữ liệu thật của cửa hàng không bị ảnh hưởng.)')) return;
        try {
            await api('/api/admin-hang-no/clear', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: 'store=' + encodeURIComponent(store())});
            await loadStores(); await loadData(); msg('Đã xoá dữ liệu của ' + store() + '.', true);
        } catch (e) { msg(e.message, false); }
    };
})();
;

    // Chuẩn hoá mã hàng/mã PO trước khi so khớp tìm kiếm: hạ chữ thường, bỏ
    // hết khoảng trắng và dấu "-" - để gõ "53012-k12-900" hay "53012 k12 900"
    // vẫn tìm ra đúng mã lưu trong hệ thống dạng "53012K12900". Chỉ áp dụng
    // cho các trường MÃ (part_code, po_code, số phiếu...), không áp dụng cho
    // tên hàng/ghi chú/tên nhân viên vì các trường đó cần giữ nguyên khoảng
    // trắng giữa các từ để so khớp đúng nghĩa.
    function normalizeCodeSearch(s) {
        return String(s == null ? '' : s).toLowerCase().replace(/[\s-]+/g, '');
    }

    // ================== ADMIN MƯỢN QUYỀN XEM 1 CỬA HÀNG ==================
    // Reload lại toàn trang sau khi đổi role/store_code trong session (thay
    // vì cố cập nhật lại DOM tại chỗ) - đơn giản và chắc chắn đúng nhất vì
    // gần như MỌI phần giao diện (tab hiện/ẩn theo role, dữ liệu từng tab...)
    // đều phụ thuộc vào role/store_code lúc trang được render.
    async function impersonateStore(storeCode) {
        try {
            const res = await fetch('/api/admin/impersonate-store', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ store_code: storeCode })
            });
            const result = await res.json();
            if (!result.success) {
                alert('Lỗi: ' + (result.error || 'Không thể truy cập cửa hàng này.'));
                return;
            }
            location.reload();
        } catch (e) {
            console.error(e);
            alert('Lỗi kết nối server.');
        }
    }

    async function returnToAdminRole() {
        try {
            const res = await fetch('/api/admin/return-to-admin', { method: 'POST' });
            const result = await res.json();
            if (!result.success) {
                alert('Lỗi: ' + (result.error || 'Không thể quay lại quyền admin.'));
                return;
            }
            location.reload();
        } catch (e) {
            console.error(e);
            alert('Lỗi kết nối server.');
        }
    }

    let globalData = [];
    let filteredData = [];   // globalData sau khi áp bộ lọc/tìm kiếm hiện tại
    let currentPage = 1;
    let pageSize = 100;
    let statusChartInstance = null;
    let pieChartInstance = null;
    let transferWeekChartInstance = null;
    let transferMonthChartInstance = null;

    document.addEventListener("DOMContentLoaded", () => {
        // Ép 2 dropdown lọc theo chi nhánh (báo cáo & luân chuyển) luôn về
        // "ALL" (Nam Sương Motor - toàn bộ chi nhánh) mỗi khi vào trang, vì
        // trình duyệt (nhất là trên mobile) hay tự nhớ lại lựa chọn cũ của
        // lần trước rồi tự chọn sẵn trước khi đoạn code này chạy - khiến
        // báo cáo chỉ hiện đúng 1 chi nhánh còn sót lại, làm tưởng bị lỗi/
        // bị chặn tìm kiếm cho tới khi tự tay đổi lại dropdown.
        const adminStoreFilterEl = document.getElementById('admin-store-filter');
        if (adminStoreFilterEl) adminStoreFilterEl.value = 'ALL';
        const transferAdminStoreFilterEl = document.getElementById('transfer-admin-store-filter');
        if (transferAdminStoreFilterEl) transferAdminStoreFilterEl.value = 'ALL';

        // Ép TẤT CẢ các ô tìm kiếm dạng chữ trong app về rỗng ngay khi vào
        // trang - cùng lý do như trên: trình duyệt (nhất là trên điện
        // thoại) hay tự điền lại chữ đã gõ ở lần trước vào các ô input
        // không có "name" (vd ô tìm PO từng gõ thử "NS1" sẽ tự hiện lại
        // "NS1" lần sau), khiến kết quả trông như trống/bị lọc mất dù dữ
        // liệu vẫn còn đủ.
        ['search-input', 'inventory-search', 'location-search', 'damaged-filter-search',
         'transfer-received-search', 'transfer-sent-search', 'transfer-admin-search',
         'quick-lookup-search'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });

        loadData();
        loadTransfers(); // tải sớm để hiển thị số lượng badge yêu cầu đang chờ
        loadInventory(); // tải sớm để trang "Tổng Quan" có ngay mốc cập nhật tồn kho/giá
        loadOverviewDashboard(); // trang "Tổng Quan" active sẵn từ đầu, không qua sự kiện click tab nên gọi tay ở đây
        _ocGdhRestore(); ocGdhNavBadge(); setInterval(ocGdhNavBadge, 60000);
        restoreOrderCheckSession(); // khôi phục phiên "Kiểm Tra Đơn Hàng" đang duyệt dở (nếu có), xem NHỚ PHIÊN ở trên
        setupDragZones();
        startVersionPolling();
        setupQuickLookupFabDrag();
        setupQuickLookupModalResize();
        setupBodyKitDetailModalResize();
        setupMegaNav();
        setupSidebarHoverExpand();
        syncTopbarHeight();

        // Kích hoạt kéo giãn cột CHỈ khi bảng thực sự đang hiển thị (đo được
        // độ rộng đúng) - bảng Tồn Kho nằm trong tab-pane đang ẩn lúc tải
        // trang, modal Tra Cứu Nhanh cũng ẩn cho tới khi bấm mở.
        const invTabBtn = document.getElementById('inventory-tab');
        if (invTabBtn) {
            invTabBtn.addEventListener('shown.bs.tab', () => makeResizableColumns('inventory-table', 'colWidths_inventoryTable'));
        }
        const quickLookupModalEl = document.getElementById('quick-lookup-modal');
        if (quickLookupModalEl) {
            quickLookupModalEl.addEventListener('shown.bs.modal', () => makeResizableColumns('quick-lookup-table', 'colWidths_quickLookupTable'));
        }

        // Modal "Xem Chi Tiết Phiếu" - khi đóng lại thì thôi không tự làm
        // mới nội dung theo dữ liệu mới nữa (tránh vẽ lại 1 modal đã ẩn).
        const transferDetailModalEl = document.getElementById('transfer-detail-modal');
        if (transferDetailModalEl) {
            transferDetailModalEl.addEventListener('hidden.bs.modal', () => { _openTransferDetail = null; });
        }

        // Thông báo chuyển kho (toast + Desktop notification + âm thanh) chỉ
        // dành cho tài khoản cửa hàng - admin không cần vì không phải là
        // người gửi/nhận phiếu.
        if (CURRENT_ROLE === 'store') {
            requestTransferNotificationPermission();
            // Trình duyệt chỉ cho phép phát âm thanh sau khi có 1 thao tác
            // bấm/gõ phím thật của người dùng (chính sách autoplay) - nên
            // "mở khoá" AudioContext ngay lần đầu người dùng tương tác với
            // trang, để những lần phát "ting" sau đó (phát ra từ polling nền,
            // không phải từ thao tác chuột trực tiếp) vẫn có tiếng.
            document.addEventListener('click', unlockTransferNotificationAudio, { once: true });
            document.addEventListener('keydown', unlockTransferNotificationAudio, { once: true });
        }
    });

    // Font chữ tuỳ chỉnh (SVN-MonaSans) có thể tải xong SAU khi bảng luân
    // chuyển đã render lần đầu, làm chiều cao chữ đo được lúc đó chưa chính
    // xác -> đồng bộ lại 1 lần nữa khi font đã sẵn sàng để đảm bảo các cột
    // (cửa hàng xin / mã hàng / đã nhận) vẫn thẳng hàng.
    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(() => {
            syncTransferRowHeights('transfer-received-body');
            syncTransferRowHeights('transfer-sent-body');
        });
    }

    // Trì hoãn việc lọc lại bảng cho tới khi người dùng ngừng gõ ~250ms,
    // thay vì lọc + vẽ lại toàn bộ bảng trên MỖI phím gõ - với dữ liệu lớn
    // (hàng chục nghìn dòng), gõ nhanh mà không debounce sẽ bị giật/lag rõ rệt.
    let _filterDebounceTimer = null;
    function debouncedFilterData() {
        clearTimeout(_filterDebounceTimer);
        _filterDebounceTimer = setTimeout(filterData, 250);
    }

    function changePageSize() {
        pageSize = parseInt(document.getElementById('page-size-select').value, 10) || 100;
        goToPage(1);
    }

    function goToPage(page) {
        const totalPages = Math.max(1, Math.ceil(filteredData.length / pageSize));
        currentPage = Math.min(Math.max(1, page), totalPages);
        const start = (currentPage - 1) * pageSize;
        renderTablePage(filteredData.slice(start, start + pageSize));

        document.getElementById('page-info').innerText = `Trang ${currentPage}/${totalPages}`;
        document.getElementById('page-prev-btn').disabled = currentPage <= 1;
        document.getElementById('page-next-btn').disabled = currentPage >= totalPages;
        document.getElementById('visible-count').innerText = filteredData.length.toLocaleString();
    }

    async function loadData() {
        const store = document.getElementById('admin-store-filter') ? document.getElementById('admin-store-filter').value : '';
        try {
            const res = await fetch(`/api/data?store=${store}`);
            const result = await res.json();
            if(result.success) {
                globalData = result.data;
                updateSummary(result.summary);
                populateOrderTypeFilter();
                filterData(false); // áp lại bộ lọc/tìm kiếm + GIỮ NGUYÊN trang hiện tại, để không làm mất trạng thái đang xem của user khi tự động làm mới nền
                renderCharts(result.summary);
            }
        } catch(e) { console.error(e); }
    }

    // Danh sách "Loại Đơn Hàng" không cố định (tuỳ dữ liệu import từ Excel,
    // xem map_order_type() ở backend) nên KHÔNG hardcode option trong HTML -
    // mỗi lần tải dữ liệu, dò lại toàn bộ giá trị order_type ĐANG CÓ trong
    // globalData để build lại dropdown, đảm bảo luôn khớp đúng dữ liệu thực
    // tế đang hiển thị (kể cả khi đổi cửa hàng ở dropdown Admin). Giữ
    // nguyên lựa chọn hiện tại của người dùng nếu giá trị đó vẫn còn tồn
    // tại trong danh sách mới.
    function populateOrderTypeFilter() {
        const selectEl = document.getElementById('filter-order-type');
        if (!selectEl) return;
        const currentValue = selectEl.value;

        const types = new Set();
        globalData.forEach(item => types.add(item.order_type || 'Chưa có dữ liệu'));
        const sortedTypes = Array.from(types).sort((a, b) => a.localeCompare(b, 'vi'));

        selectEl.innerHTML = '<option value="">Tất cả loại đơn</option>' +
            sortedTypes.map(t => `<option value="${escapeHtmlAttr(t)}">${escapeHtmlText(t)}</option>`).join('');

        if (currentValue && sortedTypes.includes(currentValue)) {
            selectEl.value = currentValue;
        }
    }

    function updateSummary(summary) {
        document.getElementById('stat-total').innerText = summary.total.toLocaleString();
        document.getElementById('stat-debt').innerText = summary.debt.toLocaleString();
        document.getElementById('stat-shipping').innerText = summary.shipping.toLocaleString();
        document.getElementById('stat-received').innerText = summary.received.toLocaleString();
        
        document.getElementById('report-min-date').innerText = summary.min_date || 'N/A';
        document.getElementById('report-max-date').innerText = summary.max_date || 'N/A';

        // Gương lại cùng số liệu này sang trang "Tổng Quan" (Dashboard Hàng
        // Nợ) - dùng chung 1 nguồn dữ liệu duy nhất (loadData()), không gọi
        // thêm API riêng, tránh sai lệch giữa 2 nơi hiển thị. ov-* chỉ tồn
        // tại ở tab Tổng Quan nên luôn kiểm tra null trước khi gán.
        const ovTotal = document.getElementById('ov-stat-total');
        if (ovTotal) ovTotal.innerText = summary.total.toLocaleString();
        const ovDebt = document.getElementById('ov-stat-debt');
        if (ovDebt) ovDebt.innerText = summary.debt.toLocaleString();
        const ovDebt2 = document.getElementById('ov-stat-debt-2');
        if (ovDebt2) ovDebt2.innerText = summary.debt.toLocaleString();
        const ovShipping = document.getElementById('ov-stat-shipping');
        if (ovShipping) ovShipping.innerText = summary.shipping.toLocaleString();
        const ovReceived = document.getElementById('ov-stat-received');
        if (ovReceived) ovReceived.innerText = summary.received.toLocaleString();
    }

    // Gọi khi bấm vào tab "Tổng Quan" (kể cả khi nó đã active sẵn từ đầu,
    // hàm này KHÔNG được onclick tự gọi lúc đó - xem loadOverviewDashboard()
    // được gọi thêm 1 lần ở khối khởi tạo DOMContentLoaded). Không tự gọi
    // API riêng nào mới - chỉ đảm bảo các nguồn dữ liệu dùng chung
    // (loadData/loadTransfers/loadInventory) đã/đang được tải, rồi vẽ lại
    // phần phiếu chuyển kho từ dữ liệu đã có sẵn trong bộ nhớ.
    function loadOverviewDashboard() {
        if (globalInventory.length === 0) loadInventory();
        renderTransferOverviewStats();
        if (typeof ovLoadOrders === 'function') ovLoadOrders();
    }

    // Đếm nhanh phiếu chuyển kho nội bộ theo trạng thái từ danh sách đã tải
    // sẵn (window._lastTransferRequests, do loadTransfers() nạp - dùng
    // chung, không gọi thêm API riêng) để hiển thị ở trang "Tổng Quan".
    // Lưu ý: đây là danh sách "gần đây + đang cần xử lý" (xem chú thích ở
    // backend _TRANSFER_LIST_RECENT_DAYS), không phải TOÀN BỘ lịch sử từ
    // trước tới giờ - đủ dùng cho mục đích xem nhanh tổng quan.
    function renderTransferOverviewStats() {
        const totalEl = document.getElementById('ov-transfer-total');
        if (!totalEl) return; // đang không ở tab Tổng Quan / chưa render tới đó
        const all = window._lastTransferRequests || [];
        const pending = all.filter(r => r.status === 'pending').length;
        const approved = all.filter(r => r.status === 'approved').length;
        const rejected = all.filter(r => r.status === 'rejected').length;
        const setText = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val.toLocaleString(); };
        setText('ov-transfer-total', all.length);
        setText('ov-transfer-pending', pending);
        setText('ov-transfer-approved', approved);
        setText('ov-transfer-rejected', rejected);
        setText('ov-transfer-pending-highlight', pending);
    }

    // Chỉ nhận và vẽ ĐÚNG 1 TRANG dữ liệu (mảng con đã cắt sẵn theo pageSize)
    // - không phải build HTML cho toàn bộ danh sách rồi gán 1 lần như trước.
    // Với danh sách hàng chục nghìn dòng, gán innerHTML cho ngần ấy <tr> cùng
    // lúc khiến trình duyệt phải parse + reflow rất nhiều DOM node -> giật.
    // Giới hạn số dòng vẽ mỗi lần (mặc định 100) giữ giao diện luôn mượt bất
    // kể tổng số dòng dữ liệu lớn tới đâu.
    function renderTablePage(pageData) {
        const tbody = document.getElementById('result-body');
        const isAdmin = document.getElementById('admin-store-filter') !== null;

        if (pageData.length === 0) {
            tbody.innerHTML = `<tr><td colspan="10" class="text-center py-4 text-muted">Không có dữ liệu.</td></tr>`;
            return;
        }

        const startIndex = (currentPage - 1) * pageSize;

        tbody.innerHTML = pageData.map((item, index) => {
            let badgeClass = item.status === 'Nợ' ? 'badge-debt' : (item.status === 'Đang vận chuyển' ? 'badge-shipping' : 'badge-received');
            
            let orderDateHtml = (!item.order_date || item.order_date === 'N/A' || item.order_date === '') 
                ? `<span class="text-danger fw-semibold">Cần bổ sung</span>` 
                : `<span class="text-muted">${item.order_date}</span>`;

            const qtyDebt = item.qty_debt || 0;
            const qtyDebtTotal = item.qty_debt_total || 0;
            const orderType = item.order_type || 'Chưa có dữ liệu';

            return `
                <tr>
                    <td class="text-center text-muted small">${startIndex + index + 1}</td>
                    ${isAdmin ? `<td><span class="badge bg-light text-dark border">${item.store_code || 'N/A'}</span></td>` : ''}
                    <td><strong>${item.po_code}</strong></td>
                    <td>${orderDateHtml}</td>
                    <td><span class="badge-order-type">${orderType}</span></td>
                    <td><span class="part-code">${item.part_code}</span></td>
                    <td class="text-center"><span class="badge-status ${badgeClass}">${item.status}</span></td>
                    <td class="text-end fw-bold ${item.status === 'Nợ' ? 'text-danger' : (item.status === 'Đang vận chuyển' ? 'text-warning' : 'text-muted')}">
                        ${item.status !== 'Đã nhận hàng' ? `${item.days_debt} ngày` : '-'}
                    </td>
                    <td class="text-end fw-semibold ${qtyDebt > 0 ? 'text-danger' : 'text-muted'}">
                        ${qtyDebt > 0 ? qtyDebt.toLocaleString() : '-'}
                    </td>
                    <td class="text-end fw-bold ${qtyDebtTotal > 0 ? 'text-danger' : 'text-muted'}">
                        ${qtyDebtTotal > 0 ? qtyDebtTotal.toLocaleString() : '-'}
                    </td>
                </tr>
            `;
        }).join('');
    }

    function filterData(resetPage = true) {
        const search = document.getElementById('search-input').value.toLowerCase();
        const searchCode = normalizeCodeSearch(search);
        const status = document.getElementById('filter-status').value;
        const orderType = document.getElementById('filter-order-type') ? document.getElementById('filter-order-type').value : '';
        filteredData = globalData.filter(item => {
            return (normalizeCodeSearch(item.po_code).includes(searchCode) || normalizeCodeSearch(item.part_code).includes(searchCode)) && 
                   (status === '' || item.status === status) &&
                   (orderType === '' || (item.order_type || 'Chưa có dữ liệu') === orderType);
        });
        // Người dùng chủ động gõ tìm kiếm/đổi trạng thái -> về trang 1.
        // Khi loadData() tự làm mới dữ liệu nền (poll định kỳ) -> giữ nguyên
        // trang đang xem (resetPage=false) để không làm gián đoạn người dùng.
        goToPage(resetPage ? 1 : currentPage);
    }

    function renderCharts(summary) {
        const ctxBar = document.getElementById('statusChart').getContext('2d');
        const ctxPie = document.getElementById('pieChart').getContext('2d');

        if (statusChartInstance) statusChartInstance.destroy();
        if (pieChartInstance) pieChartInstance.destroy();

        statusChartInstance = new Chart(ctxBar, {
            type: 'bar',
            data: {
                labels: ['Nợ Hàng', 'Đang Vận Chuyển', 'Đã Nhận Hàng'],
                datasets: [{
                    label: 'Số lượng mã',
                    data: [summary.debt, summary.shipping, summary.received],
                    backgroundColor: ['#dc3545', '#fd7e14', '#198754'],
                    borderRadius: 8
                }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
        });

        pieChartInstance = new Chart(ctxPie, {
            type: 'doughnut',
            data: {
                labels: ['Nợ', 'Vận chuyển', 'Đã nhận'],
                datasets: [{
                    data: [summary.debt, summary.shipping, summary.received],
                    backgroundColor: ['#dc3545', '#fd7e14', '#198754']
                }]
            },
            options: { responsive: true, maintainAspectRatio: false }
        });
    }

    const uploadForm = document.getElementById('upload-form');
    if(uploadForm) {
        uploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const dsPoFile = document.getElementById('ds_po_file').files[0];
            const poDetailFile = document.getElementById('po_detail_file').files[0];
            const receiptFile = document.getElementById('receipt_file').files[0];

            if (!dsPoFile && !poDetailFile && !receiptFile) {
                alert('Vui lòng chọn ít nhất 1 file để tải lên.');
                return;
            }

            const formData = new FormData();
            if (dsPoFile) formData.append('ds_po_file', dsPoFile);
            if (poDetailFile) formData.append('po_detail_file', poDetailFile);
            if (receiptFile) formData.append('receipt_file', receiptFile);

            document.getElementById('loading-overlay').style.display = 'flex';
            try {
                const res = await fetch('/api/upload', { method: 'POST', body: formData });
                const result = await res.json();
                if(res.ok) {
                    let messages = [];
                    if (result.po_detail_inserted !== undefined && result.po_detail_inserted !== null) {
                        messages.push(`Chi tiết PO: đã ghi thêm ${result.po_detail_inserted.toLocaleString()} dòng mới.`);
                        if (result.po_detail_skipped > 0) {
                            messages.push(`Đã bỏ qua ${result.po_detail_skipped.toLocaleString()} dòng trùng lặp (đã tồn tại sẵn theo Mã PO + Mã phụ tùng + Số lượng).`);
                        }
                    }
                    if (result.warning) {
                        messages.push(result.warning);
                    }
                    alert('Tải lên và đối soát thành công!' + (messages.length ? '\n\n' + messages.join('\n') : ''));
                    loadData();
                    new bootstrap.Tab(document.getElementById('report-tab')).show();
                } else { alert('Lỗi: ' + result.error); }
            } catch(e) { alert('Lỗi kết nối server.'); }
            finally { document.getElementById('loading-overlay').style.display = 'none'; }
        });
    }

    let globalInventory = [];
    // Dữ liệu tô màu tồn kho theo các phiếu luân chuyển ĐÃ ĐỒNG Ý nhưng
    // CHƯA nhận hàng xong - xem /api/transfer/highlights. Cấu trúc:
    //   given[part_code][store_code]     = [{store, quantity}, ...]  (tô ĐỎ - cửa hàng đang bị xin)
    //   receiving[part_code][store_code] = [{store, quantity}, ...]  (tô XANH LÁ - cửa hàng đang xin)
    //   crossed[part_code][store_code]   = [{store, given_quantity, receiving_quantity}, ...]
    //     (tô CAM - xin CHÉO qua lại với chính cửa hàng đó, cùng mã hàng)
    let transferHighlights = { given: {}, receiving: {}, crossed: {} };

    // Dữ liệu HÀNG HƯ HỎNG gộp theo mã hàng - xem /api/damaged/summary.
    // Cấu trúc: { part_code: { total: number, entries: [{store, quantity, note, created_at}, ...] } }
    let damagedSummary = {};

    async function loadInventory() {
    try {
        const [res, hlRes, dmgRes] = await Promise.all([
            fetch('/api/inventory'),
            fetch('/api/transfer/highlights'),
            fetch('/api/damaged/summary'),
        ]);
        const result = await res.json();
        const hlResult = await hlRes.json();
        const dmgResult = await dmgRes.json();
        if (hlResult.success) {
            transferHighlights = { given: hlResult.given || {}, receiving: hlResult.receiving || {}, crossed: hlResult.crossed || {} };
        }
        if (dmgResult.success) {
            damagedSummary = dmgResult.data || {};
        }
        if (result.success) {
            globalInventory = result.data;
            if (CURRENT_ROLE === 'store') {
                await loadLocations();
            }
            filterInventory();
            populateTransferPartList();
            refreshAllTransferRowHints();
            refreshAllAdminTransferRowHints();
            const metaEl = document.getElementById('inventory-meta');
            if (result.meta && result.meta.upload_time) {
                metaEl.innerText = `Cập nhật lần cuối: ${result.meta.upload_time} bởi ${result.meta.uploaded_by} · ${result.meta.total_parts.toLocaleString()} mã hàng`;
            } else {
                metaEl.innerText = 'Chưa có dữ liệu tồn kho.';
            }
            const priceMetaEl = document.getElementById('inventory-price-meta');
            if (priceMetaEl) {
                if (result.price_meta && result.price_meta.upload_time) {
                    priceMetaEl.innerHTML = `<i class="bi bi-tag-fill me-1 text-warning"></i>Cập nhật giá bán lần cuối: ${result.price_meta.upload_time} bởi ${result.price_meta.uploaded_by} · ${result.price_meta.total_parts.toLocaleString()} mã hàng`;
                } else {
                    priceMetaEl.innerText = '';
                }
            }
            // Gương lại 2 mốc thời gian này sang trang "Tổng Quan" - cùng 1
            // nguồn dữ liệu (loadInventory()), không gọi thêm API riêng.
            const ovInv = document.getElementById('ov-inventory-updated');
            if (ovInv) {
                ovInv.innerHTML = (result.meta && result.meta.upload_time)
                    ? `Cập nhật lúc <strong>${result.meta.upload_time}</strong><br><span class="text-muted">bởi ${result.meta.uploaded_by} · ${result.meta.total_parts.toLocaleString()} mã hàng</span>`
                    : 'Chưa có dữ liệu tồn kho.';
            }
            const ovPrice = document.getElementById('ov-price-updated');
            if (ovPrice) {
                ovPrice.innerHTML = (result.price_meta && result.price_meta.upload_time)
                    ? `Cập nhật lúc <strong>${result.price_meta.upload_time}</strong><br><span class="text-muted">bởi ${result.price_meta.uploaded_by} · ${result.price_meta.total_parts.toLocaleString()} mã hàng</span>`
                    : 'Chưa có dữ liệu bảng giá.';
            }
        }
    } catch (e) { console.error(e); }
}

    // Chỉ tải lại phần tô màu (nhẹ hơn nhiều so với tải lại toàn bộ tồn kho)
    // - gọi sau mỗi thao tác luân chuyển (đồng ý/từ chối/đổi lại/tick nhận
    // hàng) để bảng tồn kho luôn phản ánh đúng ngay, không cần load lại trang.
    async function refreshTransferHighlights() {
        try {
            const res = await fetch('/api/transfer/highlights');
            const result = await res.json();
            if (result.success) {
                transferHighlights = { given: result.given || {}, receiving: result.receiving || {}, crossed: result.crossed || {} };
                filterInventory(); // vẽ lại bảng tồn kho đang hiển thị với màu mới
                refreshAllTransferRowHints();
                refreshAllAdminTransferRowHints();
            }
        } catch (e) { console.error(e); }
    }

    // Chỉ tải lại phần tô vàng "Hư Hỏng" (nhẹ, giống refreshTransferHighlights)
    // - gọi sau khi tự lưu/xoá/nhập file hàng hư hỏng, VÀ khi polling phát
    // hiện damaged_version đổi (cửa hàng khác vừa báo hư) để mọi user thấy
    // ngay cột "Hư Hỏng" cập nhật mà không cần F5 lại trang.
    async function refreshDamagedHighlights() {
        try {
            const res = await fetch('/api/damaged/summary');
            const result = await res.json();
            if (result.success) {
                damagedSummary = result.data || {};
                filterInventory(); // vẽ lại bảng tồn kho đang hiển thị với màu mới
            }
        } catch (e) { console.error(e); }
    }

    // Ghi chú tô màu tồn kho: hiện khi click/tap vào ô có class "inv-hl" (cả
    // desktop lẫn mobile - không phụ thuộc hover chuột), ẩn khi click ra
    // ngoài hoặc click lại đúng ô đang mở. Dùng UỶ QUYỀN SỰ KIỆN (event
    // delegation) trên toàn document vì các ô này được vẽ lại liên tục mỗi
    // khi lọc/tải lại bảng tồn kho.
    function hideInvTooltip() {
        const tip = document.getElementById('inv-tooltip');
        tip.style.display = 'none';
        tip.dataset.openFor = '';
    }

    function showInvTooltip(cell, text) {
        const tip = document.getElementById('inv-tooltip');
        tip.innerText = text;
        tip.style.display = 'block';

        const rect = cell.getBoundingClientRect();
        // Đo kích thước tooltip SAU khi đã hiển thị nội dung + display:block
        const tipW = tip.offsetWidth;
        const tipH = tip.offsetHeight;
        const margin = 8;

        let top = rect.top - tipH - margin;
        if (top < margin) top = rect.bottom + margin; // không đủ chỗ phía trên -> hiện phía dưới

        let left = rect.left + rect.width / 2 - tipW / 2;
        left = Math.max(margin, Math.min(left, window.innerWidth - tipW - margin));

        tip.style.top = top + 'px';
        tip.style.left = left + 'px';
    }

    document.addEventListener('click', (e) => {
        const cell = e.target.closest('.inv-hl, .freq-badge');
        const tip = document.getElementById('inv-tooltip');
        if (cell) {
            e.stopPropagation();
            const text = cell.getAttribute('data-tip') || '';
            if (tip.dataset.openFor === text && tip.style.display === 'block') {
                hideInvTooltip(); // click lại đúng ô đang mở -> ẩn đi
            } else {
                showInvTooltip(cell, text);
                tip.dataset.openFor = text;
            }
        } else if (!e.target.closest('#inv-tooltip')) {
            hideInvTooltip();
        }
    });
    // Ẩn ghi chú khi cuộn trang (vị trí "fixed" sẽ không còn đúng chỗ ô nữa).
    window.addEventListener('scroll', hideInvTooltip, true);

    // Chỉ vẽ tối đa RENDER_LIMIT dòng ra DOM mỗi lần, dù kết quả lọc khớp nhiều hơn.
    // Lý do: với 10.000+ mã hàng, gõ vài ký tự đầu vẫn khớp hàng nghìn dòng - build
    // innerHTML cho toàn bộ số đó là bước nặng thực sự gây giật/khựng, không phải
    // việc gọi hàm lọc. Giới hạn số dòng vẽ ra giúp mượt ngay cả khi kết quả rất lớn.
    const INVENTORY_RENDER_LIMIT = 200;
    const INVENTORY_STORES = ['NS1', 'NS2', 'NS3', 'NS4', 'NS5', 'NSM1'];

    function fmtInvQty(v) {
        return (v && v !== 0) ? v.toLocaleString() : '-';
    }

    // Định dạng giá bán kiểu tiền Việt Nam (vd 150.000) - trả về "-" nếu mã
    // hàng chưa được nhập giá (null/undefined), khác với 0 (giá bán = 0đ
    // thật sự, vẫn hiện "0" chứ không phải "-").
    function fmtInvPrice(v) {
        return (v === null || v === undefined || v === '') ? '-' : Math.round(v).toLocaleString() + 'đ';
    }

    // Vị trí đã lưu của 1 mã hàng - dùng chung cho bảng Tồn Kho Hệ Thống
    // (#inventory-body) LẪN modal "Tra Cứu Nhanh" nổi góc dưới bên phải
    // (#quick-lookup-body, xem openQuickLookup()) để không lặp code.
    function getLocationHtml(partCode) {
        try {
            const locs = Array.isArray(globalLocations) ? globalLocations : [];
            const locItem = locs.find(l => l.part_code === partCode);
            if (!locItem) return '<span class="text-muted">-</span>';
            const locsArr = [locItem.location_1, locItem.location_2, locItem.location_3].filter(Boolean);
            if (locsArr.length === 0) return '<span class="text-muted">-</span>';
            return locsArr.map(l => `<div style="line-height:1.4; white-space:nowrap;">${escapeHtmlAttr(l)}</div>`).join('');
        } catch(e) {
            console.warn('Lỗi lấy vị trí cho', partCode, e);
            return '<span class="text-muted">-</span>';
        }
    }

    function buildInvCellHtml(item, store) {
        const val = fmtInvQty(item[store]);
        const given = (transferHighlights.given[item.part_code] || {})[store];
        const receiving = (transferHighlights.receiving[item.part_code] || {})[store];
        const crossed = (transferHighlights.crossed[item.part_code] || {})[store];

        const cornerLabel = (list, sign) => {
            if (!list || !list.length) return '';
            const first = list[0];
            const suffix = list.length > 1 ? '…' : '';
            return `<span class="inv-hl-corner">${escapeHtmlAttr(first.store)} ${sign}${first.quantity.toLocaleString()}${suffix}</span>`;
        };

        if (crossed && crossed.length) {
            const crossTip = crossed.map(c => `⇄ ${c.store}: cho ${c.given_quantity.toLocaleString()} / nhận ${c.receiving_quantity.toLocaleString()}`);
            const givenTip = (given && given.length) ? given.map(g => `${g.store} xin ${g.quantity.toLocaleString()}`) : [];
            const receivingTip = (receiving && receiving.length) ? receiving.map(g => `Nhận ${g.store} ${g.quantity.toLocaleString()}`) : [];
            const tip = [...crossTip, ...givenTip, ...receivingTip].join('\n');
            const first = crossed[0];
            const suffix = crossed.length > 1 ? '…' : '';
            const corner = `<span class="inv-hl-corner">${escapeHtmlAttr(first.store)} +${first.receiving_quantity.toLocaleString()}/-${first.given_quantity.toLocaleString()}${suffix}</span>`;
            return `<td class="text-end fw-bold inv-hl inv-hl-cross" data-tip="${escapeHtmlAttr(tip)}">${corner}${val}</td>`;
        }
        if (given && given.length) {
            const tip = given.map(g => `${g.store} xin ${g.quantity.toLocaleString()}`).join('\n');
            return `<td class="text-end text-danger fw-bold inv-hl inv-hl-out" data-tip="${escapeHtmlAttr(tip)}">${cornerLabel(given, '-')}${val}</td>`;
        }
        if (receiving && receiving.length) {
            const tip = receiving.map(g => `Nhận ${g.store} ${g.quantity.toLocaleString()}`).join('\n');
            return `<td class="text-end text-success fw-bold inv-hl inv-hl-in" data-tip="${escapeHtmlAttr(tip)}">${cornerLabel(receiving, '+')}${val}</td>`;
        }
        return `<td class="text-end">${val}</td>`;
    }

    // Ô cột "Tổng Tồn" trên bảng Tồn Kho Hệ Thống - LUÔN hiện tổng số
    // lượng cộng dồn NS1+NS2+NS3+NS4+NS5+NSM1+Kho CB của mã hàng. Nếu mã hàng có
    // ít nhất 1 lần báo hư (bất kỳ cửa hàng nào) thì tô VÀNG (không đổi số
    // hiển thị - vẫn là tổng tồn, không phải số hư hỏng); rê/bấm vào ô
    // hiện ghi chú liệt kê TỪNG LẦN báo hư (cửa hàng nào, bao nhiêu, tình
    // trạng gì) - dùng chung cơ chế tooltip #inv-tooltip đã có sẵn (class
    // inv-hl + data-tip).
    function buildDamagedCellHtml(item) {
        // Tổng hàng ngang = 6 cửa hàng + Kho CB
        const total = INVENTORY_STORES.reduce((sum, s) => sum + (Number(item[s]) || 0), 0)
                    + (Number(item.CB) || 0);
        const totalDisplay = total.toLocaleString();

        const dmg = damagedSummary[item.part_code];
        if (!dmg || !dmg.entries || !dmg.entries.length) {
            return `<td class="text-end fw-bold">${totalDisplay}</td>`;
        }
        const tip = dmg.entries.map(e => {
            const noteText = e.note ? ` (${e.note})` : '';
            return `${e.store}: ${e.quantity.toLocaleString()}${noteText}`;
        }).join('\n');
        return `<td class="text-end fw-bold inv-hl inv-hl-damaged" data-tip="${escapeHtmlAttr(tip)}">${totalDisplay}</td>`;
    }

    // Dựng HTML các dòng <tr> của bảng Tồn Kho - dùng chung cho cả bảng
    // chính (#inventory-body) lẫn modal "Tra Cứu Nhanh" (#quick-lookup-body)
    // để 2 nơi luôn hiển thị giống hệt nhau, không lặp code.
    // Icon nhỏ phân loại tần suất bán (TX/TB/CB) gắn ở góc trên-phải mã
    // hàng - dữ liệu lấy từ field item.sales_freq (hệ thống) và
    // item.sales_freq_by_store (từng kho/cửa hàng) do /api/inventory trả về
    // (rỗng/null nếu chưa import số liệu xuất bán, hoặc mã hàng đó tồn = 0).
    // Icon dùng phân loại HỆ THỐNG để tô màu (đại diện chung cho cả dòng),
    // nhưng bấm vào hiện tooltip so sánh CẢ tần suất của "cửa hàng đang xem"
    // (CURRENT_STORE_CODE, với user cửa hàng) LẪN tần suất hệ thống - dùng
    // chung cơ chế tooltip #inv-tooltip (xem showInvTooltip/hideInvTooltip
    // và phần event delegation ".freq-badge" bên dưới), tránh bị table
    // scroll/sticky header che mất như tooltip CSS lồng trong bảng cũ.
    function _fmtMonthsText(monthsOfStock) {
        return (monthsOfStock === null || monthsOfStock === undefined)
            ? 'không xác định (chưa bán trong kỳ)'
            : `${monthsOfStock} tháng`;
    }

    function buildFreqBadgeHtml(item) {
        const systemFreq = item.sales_freq;
        const storeFreq = (item.sales_freq_by_store || {})[CURRENT_STORE_CODE];
        const isStoreRole = (CURRENT_ROLE === 'store');

        // User cửa hàng: CHỈ xem tần suất của CHÍNH cửa hàng mình, không
        // fallback về hệ thống, không hiện thêm dòng hệ thống trong tooltip
        // - không có số liệu riêng cho cửa hàng đó thì không hiện icon luôn.
        // Admin: giữ nguyên như trước (luôn theo hệ thống, kể cả khi đang
        // mượn quyền xem 1 cửa hàng cụ thể).
        const primary = isStoreRole ? storeFreq : systemFreq;
        if (!primary || !primary.code) return '';
        const cls = 'freq-' + primary.code.toLowerCase();

        const lines = [];
        if (isStoreRole) {
            lines.push(`Cửa hàng ${CURRENT_STORE_CODE}: ${primary.code} - ${primary.label}`);
            lines.push(`  Tồn đủ bán: ${_fmtMonthsText(primary.months_of_stock)} | TB bán/tháng: ${primary.avg_month}`);
        } else {
            lines.push(`Toàn hệ thống: ${primary.code} - ${primary.label}`);
            lines.push(`  Tồn đủ bán: ${_fmtMonthsText(primary.months_of_stock)} | TB bán/tháng: ${primary.avg_month}`);
        }
        const tipText = lines.join('\n');

        return `<span class="freq-badge ${cls}" data-tip="${escapeHtmlAttr(tipText)}">${primary.code}</span>`;
    }

    function buildInventoryRowsHtml(data, renderLimit, totalCols) {
        const isStore = (CURRENT_ROLE === 'store');
        const truncated = !!renderLimit && data.length > renderLimit;
        const displayData = truncated ? data.slice(0, renderLimit) : data;

        let html = displayData.map(item => {
            const partCode = escapeHtmlAttr(item.part_code);
            const locHtml = isStore ? `<td class="loc-col position-relative" style="vertical-align:middle; font-size:0.9rem; padding-right: 28px;">
                ${getLocationHtml(item.part_code)}
                <span class="edit-icon" style="display:none; position:absolute; right:4px; top:50%; transform:translateY(-50%); cursor:pointer; color:#0d6efd;" onclick="editLocationRow(this, '${partCode}')">
                    <i class="bi bi-pencil"></i>
                </span>
            </td>` : '';

            // Cột Giá Bán: admin bấm sửa trực tiếp được (icon bút chì hiện
            // khi rê chuột qua ô), store chỉ xem, không sửa được.
            const priceHtml = !isStore ? `
                <td class="price-col text-end" style="vertical-align:middle;">
                    <span class="price-val fw-semibold ${(item.sale_price === null || item.sale_price === undefined) ? 'text-muted' : 'text-warning-emphasis'}">${fmtInvPrice(item.sale_price)}</span>
                    <span class="edit-icon" style="display:none; position:absolute; right:4px; top:50%; transform:translateY(-50%); cursor:pointer; color:#0d6efd;" onclick="editPriceRow(this, '${partCode}')">
                        <i class="bi bi-pencil"></i>
                    </span>
                </td>` : `<td class="text-end fw-semibold ${(item.sale_price === null || item.sale_price === undefined) ? 'text-muted' : 'text-warning-emphasis'}">${fmtInvPrice(item.sale_price)}</td>`;

            return `
                <tr>
                    <td><span class="part-code">${partCode}${buildFreqBadgeHtml(item)}</span></td>
                    <td class="inv-name-col" title="${escapeHtmlAttr(item.part_name || '')}">${item.part_name || ''}</td>
                    <td class="text-muted small">${item.unit || ''}</td>
                    ${priceHtml}
                    ${locHtml}
                    ${INVENTORY_STORES.map(s => buildInvCellHtml(item, s)).join('')}
                    <td class="text-end text-muted">${fmtInvQty(item.CB)}</td>
                    ${buildDamagedCellHtml(item)}
                </tr>
            `;
        }).join('');

        if (truncated) {
            html += `<tr><td colspan="${totalCols}" class="text-center py-3 text-muted small fst-italic">Đang hiển thị ${renderLimit} / ${data.length.toLocaleString()} kết quả khớp — gõ thêm để thu hẹp tìm kiếm.</td></tr>`;
        }
        return html;
    }

    // Dòng "TỔNG CỘNG" (chiều dọc) ở cuối bảng Tồn Kho Hệ Thống - cộng dồn
    // tồn kho từng cửa hàng (và tổng chung) trên TOÀN BỘ danh sách đang
    // hiển thị (data đã qua bộ lọc tìm kiếm nếu có), không giới hạn theo
    // INVENTORY_RENDER_LIMIT (limit đó chỉ để tránh vẽ quá nhiều <tr> lên
    // HTML cho mượt, không được phép làm sai số liệu tổng).
    function renderInventoryTotalsRow(data) {
        const footRow = document.getElementById('inventory-total-row');
        if (!footRow) return;

        let grandTotal = 0;
        INVENTORY_STORES.forEach(s => {
            const storeTotal = data.reduce((sum, item) => sum + (Number(item[s]) || 0), 0);
            grandTotal += storeTotal;
            const cell = document.getElementById(`inv-total-${s}`);
            if (cell) cell.innerText = storeTotal.toLocaleString();
        });
        const grandCell = document.getElementById('inv-total-grand');
        if (grandCell) grandCell.innerText = grandTotal.toLocaleString();

        // "Kho CB" là kho độc lập - có cột riêng, nhưng vẫn được cộng vào tổng chung bên dưới.
        const cbTotal = data.reduce((sum, item) => sum + (Number(item.CB) || 0), 0);
        const cbCell = document.getElementById('inv-total-CB');
        if (cbCell) cbCell.innerText = cbTotal.toLocaleString();

        // Tổng chung = 6 cửa hàng + Kho CB (khớp với cột Tổng Tồn từng dòng)
        if (grandCell) grandCell.innerText = (grandTotal + cbTotal).toLocaleString();
    }

    function renderInventoryTable(data) {
        const tbody = document.getElementById('inventory-body');
        if (!tbody) {
            console.error('Không tìm thấy tbody #inventory-body');
            return;
        }
        const isStore = (CURRENT_ROLE === 'store');
        const totalCols = isStore ? 13 : 12; // Bỏ cột Thao tác, có thêm cột Giá Bán + cột Kho CB + cột Hư Hỏng - 13 cột (nếu store) hoặc 12 cột (admin)

        document.getElementById('inventory-visible-count').innerText = data.length.toLocaleString();
        renderInventoryTotalsRow(data);

        if (!data || data.length === 0) {
            tbody.innerHTML = `<tr><td colspan="${totalCols}" class="text-center py-4 text-muted">Không có dữ liệu tồn kho.</td></tr>`;
            return;
        }

        tbody.innerHTML = buildInventoryRowsHtml(data, INVENTORY_RENDER_LIMIT, totalCols);
    }

    function filterInventory() {
        const search = document.getElementById('inventory-search').value.toLowerCase();
        const searchCode = normalizeCodeSearch(search);
        const filtered = globalInventory.filter(item =>
            normalizeCodeSearch(item.part_code).includes(searchCode) ||
            (item.part_name || '').toLowerCase().includes(search)
        );
        renderInventoryTable(filtered);
    }

    // Trì hoãn lọc bảng Tồn Kho Hệ Thống (10.000+ dòng) cho tới khi người dùng
    // ngừng gõ ~250ms, tránh lọc + vẽ lại toàn bộ bảng trên MỖI phím gõ gây giật/khựng.
    let _inventoryFilterDebounceTimer = null;
    function debouncedFilterInventory() {
        clearTimeout(_inventoryFilterDebounceTimer);
        _inventoryFilterDebounceTimer = setTimeout(filterInventory, 250);
    }

    // ------------------------------------------------------------------
    // THỐNG KÊ TẦN SUẤT BÁN (TX/TB/CB) - trang riêng cho admin.
    // ------------------------------------------------------------------
    let _salesFreqDebounceTimer = null;
    function debouncedLoadSalesFreqStats() {
        clearTimeout(_salesFreqDebounceTimer);
        _salesFreqDebounceTimer = setTimeout(loadSalesFreqStats, 300);
    }

    function setSalesFreqGroupFilter(groupCode) {
        const sel = document.getElementById('salesFreqGroupFilter');
        if (sel) sel.value = (sel.value === groupCode) ? '' : groupCode;
        loadSalesFreqStats();
    }

    function _buildSalesFreqQuery() {
        const store = document.getElementById('salesFreqStoreFilter')?.value || '';
        const group = document.getElementById('salesFreqGroupFilter')?.value || '';
        const q = document.getElementById('salesFreqSearchInput')?.value.trim() || '';
        const params = new URLSearchParams();
        if (store) params.set('store', store);
        if (group) params.set('group', group);
        if (q) params.set('q', q);
        return params.toString();
    }

    async function loadSalesFreqStats() {
        const tbody = document.getElementById('sales-freq-body');
        if (!tbody) return;
        try {
            const res = await fetch('/api/admin/sales-frequency-stats?' + _buildSalesFreqQuery());
            const json = await res.json();
            if (!json.success) {
                tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-danger">${json.error || 'Lỗi tải dữ liệu.'}</td></tr>`;
                return;
            }

            document.getElementById('freqCountTX').innerText = (json.summary.TX || 0).toLocaleString();
            document.getElementById('freqCountTB').innerText = (json.summary.TB || 0).toLocaleString();
            document.getElementById('freqCountCB').innerText = (json.summary.CB || 0).toLocaleString();
            document.getElementById('salesFreqVisibleCount').innerText = `${json.total.toLocaleString()} mã hàng`;

            const metaInfo = document.getElementById('salesFreqMetaInfo');
            if (metaInfo) {
                metaInfo.innerHTML = `Kỳ số liệu xuất bán: <strong>${json.period_months} tháng</strong> gần nhất` +
                    (json.store ? ` — đang lọc theo kho <strong>${json.store}</strong>` : ' — tổng hợp toàn hệ thống');
            }

            if (!json.data || json.data.length === 0) {
                tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-muted">Không có mã hàng nào khớp bộ lọc.</td></tr>`;
                return;
            }

            const groupBadgeClass = { TX: 'bg-success', TB: 'bg-warning text-dark', CB: 'bg-danger' };
            tbody.innerHTML = json.data.map(r => `
                <tr>
                    <td><span class="part-code">${escapeHtmlAttr(r.part_code)}</span></td>
                    <td class="inv-name-col" title="${escapeHtmlAttr(r.part_name || '')}">${r.part_name || ''}</td>
                    <td class="text-muted small">${r.unit || ''}</td>
                    <td class="text-end">${fmtInvQty(r.qty_on_hand)}</td>
                    <td class="text-end">${fmtInvQty(r.qty_sold_period)}</td>
                    <td class="text-end">${r.avg_month}</td>
                    <td class="text-end">${r.months_of_stock === null ? '—' : r.months_of_stock}</td>
                    <td><span class="badge ${groupBadgeClass[r.group] || 'bg-secondary'}">${r.group} - ${r.group_label}</span></td>
                </tr>
            `).join('');
        } catch (err) {
            console.error('Lỗi loadSalesFreqStats:', err);
            tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-danger">Lỗi kết nối máy chủ.</td></tr>`;
        }
    }

    function exportSalesFreqStats() {
        window.location.href = '/api/admin/sales-frequency-stats/export?' + _buildSalesFreqQuery();
    }

    async function uploadSalesExportFile(input) {
        const file = input.files && input.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('sales_file', file);

        const overlay = document.getElementById('loading-overlay');
        if (overlay) overlay.style.display = 'flex';
        try {
            const res = await fetch('/api/admin/import-sales-export', { method: 'POST', body: formData });
            const json = await res.json();
            input.value = '';
            if (!json.success) {
                alert(json.error || 'Import thất bại.');
                return;
            }
            let msg = `Đã import ${json.total_parts.toLocaleString()} mã hàng (kỳ ${json.period_months} tháng, bỏ qua ${json.skipped_rows.toLocaleString()} dòng không hợp lệ).`;
            if (json.warnings && json.warnings.length) {
                msg += '\n\nCảnh báo:\n' + json.warnings.slice(0, 10).join('\n');
            }
            alert(msg);
            loadSalesFreqStats();
            loadInventory(); // để icon phân loại ở bảng Tồn Kho được cập nhật ngay
        } catch (err) {
            input.value = '';
            console.error('Lỗi uploadSalesExportFile:', err);
            alert('Lỗi kết nối máy chủ khi import.');
        } finally {
            if (overlay) overlay.style.display = 'none';
        }
    }

    // ------------------------------------------------------------------
    // GÔM ĐƠN HÀNG + DASHBOARD ĐƠN HÀNG (/api/gom-don-hang/*)
    // ------------------------------------------------------------------
    let _gdhPage = 1, _gdhTotal = 0, _gdhPageSize = 100, _gdhRows = [], _gdhDirty = false, _gdhEdited = {};
    let _gdhBatchId = null, _gdhTypes = ['Định kỳ', 'Khẩn', 'Đơn 26'];
    const _gdhFmt = (v, d) => (v === null || v === undefined || v === '') ? '' : Number(v).toLocaleString('vi-VN', {maximumFractionDigits: d === undefined ? 2 : d});
    const _gdhEsc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const _GDH_TYPE_NAME = {'Định kỳ': 'định kỳ', 'Khẩn': 'khẩn', 'Đơn 26': '26'};
    const _GDH_TYPE_CLS = {'Định kỳ': 'bg-primary', 'Khẩn': 'bg-danger', 'Đơn 26': 'bg-secondary'};
    function _gdhOrderName(o) {                  // vd "Đơn hàng định kỳ NS3 ngày 02/10/2026" (nhiều loại: "Đơn hàng NS3 ngày ...")
        const types = Object.keys(o.by_type || {}).filter(t => o.by_type[t] && o.by_type[t].parts > 0);
        const d = String(o.submitted_at || '').slice(0, 10);
        const mid = types.length === 1 ? ' ' + _GDH_TYPE_NAME[types[0]] : '';
        return `Đơn hàng${mid} ${o.store}` + (d ? ` ngày ${d}` : '');
    }
    function _gdhTypeChips(o) {
        return Object.keys(o.by_type || {}).filter(t => o.by_type[t] && o.by_type[t].parts > 0)
            .map(t => `<span class="badge ${_GDH_TYPE_CLS[t] || 'bg-secondary'} me-1">${_gdhEsc(t)} ${_gdhFmt(o.by_type[t].parts, 0)} mã</span>`).join('');
    }
    const _gdhDate = (iso) => iso ? iso.split('-').reverse().join('/') : '';
    const _gdhGrpCls = {TX: 'bg-success', TB: 'bg-info text-dark', CB: 'bg-secondary', HET: 'bg-danger'};
    function _gdhStore() { return CURRENT_ROLE === 'admin' ? (document.getElementById('gdhStore')?.value || '') : ''; }
    // Không gian gôm: admin mặc định 'mine' (riêng từng admin); 'store' = bảng gôm chung của chi nhánh. User cửa hàng luôn dùng chung nên không gửi.
    let _gdhSpaceVal = 'mine';
    function _gdhSpace() { return CURRENT_ROLE === 'admin' ? _gdhSpaceVal : ''; }
    async function gdhSpaceChanged(el) {         // 2 ô chọn không gian (bảng gôm + dashboard) luôn đồng bộ với nhau
        if (_gdhDirty && !(await _gdhConfirmLeave())) { el.value = _gdhSpaceVal; return; }
        _gdhSpaceVal = el.value;
        ['gdhSpace', 'gdhDSpace'].forEach(id => { const x = document.getElementById(id); if (x) x.value = _gdhSpaceVal; });
        _gdhBatchId = null; _gdhEdited = {}; _gdhMarkDirty(false);
        if (!document.getElementById('gdhDashView').classList.contains('d-none')) gdhDashLoad(); else gdhLoadBatches();
    }
    function _gdhMarkDirty(f) { _gdhDirty = f; if (f) _gdhBadge('Chưa lưu', 'bg-warning text-dark'); else document.getElementById('gdhDirty').classList.add('d-none'); }
    async function _gdhJson(url, opts) {
        const res = await fetch(url, opts);
        const j = await res.json().catch(() => ({}));
        if (!res.ok || j.error) { const e = new Error(j.error || ('Lỗi ' + res.status)); e.data = j; throw e; }
        return j;
    }
    // Hiệu ứng chờ xử lý cho nút bấm: khoá nút + hiện vòng xoay, trả lại nguyên trạng khi xong (gọi lại với on=false).
    function _gdhBusy(btn, on, text) {
        if (!btn) return;
        if (on) {
            if (btn.dataset.busy === '1') return;
            btn.dataset.busy = '1'; btn.dataset.html = btn.innerHTML; btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>' + text;
        } else {
            if (btn.dataset.busy !== '1') return;
            btn.innerHTML = btn.dataset.html; btn.disabled = false;
            delete btn.dataset.busy; delete btn.dataset.html;
        }
    }
    // Giống _gdhBusy nhưng cho nút dạng <label> chứa <input type=file> (không được thay innerHTML vì sẽ mất ô chọn file).
    function _gdhBusyLabel(lbl, on, text) {
        if (!lbl) return;
        const ico = lbl.querySelector('.gdh-imp-ico'), txt = lbl.querySelector('.gdh-imp-txt'), inp = lbl.querySelector('input[type=file]');
        if (on) {
            if (lbl.dataset.busy === '1') return;
            lbl.dataset.busy = '1';
            if (ico) { lbl.dataset.ico = ico.className; ico.className = 'spinner-border spinner-border-sm me-1 gdh-imp-ico'; }   // GIỮ class gdh-imp-ico, nếu mất thì lúc tắt không tìm lại được icon -> vòng xoay kẹt mãi
            if (txt) { lbl.dataset.txt = txt.textContent; txt.textContent = text; }
            if (inp) inp.disabled = true;
            lbl.classList.add('gdh-busy');
        } else {
            if (lbl.dataset.busy !== '1') return;
            if (ico && lbl.dataset.ico !== undefined) ico.className = lbl.dataset.ico;
            if (txt && lbl.dataset.txt !== undefined) txt.textContent = lbl.dataset.txt;
            if (inp) inp.disabled = false;
            lbl.classList.remove('gdh-busy');
            delete lbl.dataset.busy; delete lbl.dataset.ico; delete lbl.dataset.txt;
        }
    }
    async function _gdhConfirmLeave() {
        return !_gdhDirty || await nsConfirm('Có thay đổi chưa lưu. Tiếp tục sẽ mất các thay đổi này. Tiếp tục?');
    }
    function gdhTabOpened() {
        const now = new Date(), y = now.getFullYear(), m = now.getMonth();
        const iso = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
        if (!document.getElementById('gdhDFrom').value) document.getElementById('gdhDFrom').value = iso(new Date(y, m, 1));
        if (!document.getElementById('gdhDTo').value) document.getElementById('gdhDTo').value = iso(new Date(y, m + 1, 0));
        gdhBundleInfo();
        gdhOrdersBadge();
        gdhUArcAutoDownload();
        if (!document.getElementById('gdhDashView').classList.contains('d-none')) gdhDashLoad();
        else if (!document.getElementById('gdhOrdersView').classList.contains('d-none')) gdhOrdersLoad();
        else gdhLoadBatches(_gdhBatchId);
    }
    async function gdhSwitchView(v) {
        if (v !== 'gom' && (_gdhDirty && !(await _gdhConfirmLeave()))) return;
        if (v !== 'gom') { gdhToggleMax(false); _gdhMarkDirty(false); }
        document.getElementById('gdhGomView').classList.toggle('d-none', v !== 'gom');
        document.getElementById('gdhDashView').classList.toggle('d-none', v !== 'dash');
        document.getElementById('gdhOrdersView').classList.toggle('d-none', v !== 'orders');
        document.getElementById('gdhBtnGom').classList.toggle('active', v === 'gom');
        document.getElementById('gdhBtnDash').classList.toggle('active', v === 'dash');
        document.getElementById('gdhBtnOrders')?.classList.toggle('active', v === 'orders');
        if (v === 'dash') gdhDashLoad(); else if (v === 'orders') gdhOrdersLoad(); else gdhLoadBatches(_gdhBatchId);
    }

    // ---------- Đợt gôm ----------
    async function gdhLoadBatches(selectId) {
        const sel = document.getElementById('gdhBatch');
        if (CURRENT_ROLE === 'admin' && !_gdhStore()) {
            sel.innerHTML = ''; _gdhBatchId = null; _gdhRows = []; _gdhClearStatus();
            document.getElementById('gdh-body').innerHTML = '<tr><td colspan="20" class="text-center py-4 text-muted">Vui lòng chọn chi nhánh.</td></tr>';
            ['gdhSummary', 'gdhInfo', 'gdhWarn', 'gdhCount', 'gdhPageInfo'].forEach(id => document.getElementById(id).innerHTML = '');
            return;
        }
        try {
            const j = await _gdhJson('/api/gom-don-hang/batches?store=' + encodeURIComponent(_gdhStore()) + '&space=' + _gdhSpace());
            _gdhTypes = j.order_types || _gdhTypes;
            sel.innerHTML = j.data.length ? j.data.map(b => `<option value="${b.id}">${_gdhDate(b.from)} - ${_gdhDate(b.to)} · ${b.total_parts} mã · ${b.typed_parts} đã chọn loại đơn${b.space === 'store' && b.status && b.status !== 'draft' ? ' · [' + b.status_label + ']' : ''}</option>`).join('')
                                          : '<option value="">(Chưa có đợt nào - hãy import file tồn kho)</option>';
            const want = j.data.find(b => String(b.id) === String(selectId)) ? selectId : (j.data[0] && j.data[0].id);
            _gdhBatchId = want || null;
            if (want) sel.value = String(want);
            _gdhMarkDirty(false); _gdhEdited = {};
            if (_gdhBatchId) await gdhLoad(1);
            else {
                _gdhRows = []; _gdhClearStatus();
                document.getElementById('gdh-body').innerHTML = '<tr><td colspan="20" class="text-center py-4 text-muted">Chưa có dữ liệu. Bấm "Chọn file Tổng hợp tồn kho" để import.</td></tr>';
                ['gdhSummary', 'gdhInfo', 'gdhWarn', 'gdhCount', 'gdhPageInfo'].forEach(id => document.getElementById(id).innerHTML = '');
            }
        } catch (e) { alert(e.message); }
    }
    async function gdhBatchChanged() {
        const sel = document.getElementById('gdhBatch');
        if (_gdhDirty && !(await _gdhConfirmLeave())) { sel.value = String(_gdhBatchId); return; }
        _gdhBatchId = sel.value || null; _gdhEdited = {}; _gdhMarkDirty(false); gdhLoad(1);
    }
    async function gdhDeleteBatch() {
        if (!_gdhBatchId) return;
        if (!await nsConfirm('Xoá đợt gôm đang chọn cùng toàn bộ loại đơn/số lượng đã chọn? Không khôi phục được.')) return;
        try {
            await _gdhJson('/api/gom-don-hang/delete-batch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: _gdhBatchId})});
            _gdhBatchId = null; _gdhMarkDirty(false); gdhLoadBatches();
        } catch (e) { alert(e.message); }
    }
    async function gdhImport(input) {
        const file = input.files[0]; if (!file) return;
        if (CURRENT_ROLE === 'admin' && !_gdhStore()) { alert('Vui lòng chọn chi nhánh trước.'); input.value = ''; return; }
        if (_gdhDirty && !(await _gdhConfirmLeave())) { input.value = ''; return; }
        const fd = new FormData();
        fd.append('file', file); fd.append('store', _gdhStore()); fd.append('space', _gdhSpace());
        const pf = document.getElementById('gdhImpFrom')?.value, pt = document.getElementById('gdhImpTo')?.value;
        if (pf && pt) { fd.append('period_from', pf); fd.append('period_to', pt); }
        input.value = '';
        const impLbl = input.closest('label');
        _gdhBusyLabel(impLbl, true, 'Đang import...');
        const impCtl = new AbortController(), impTimer = setTimeout(() => impCtl.abort(), 180000);      // quá 3 phút không thấy máy chủ trả lời thì dừng chờ, không để nút xoay mãi
        try {
            const j = await _gdhJson('/api/gom-don-hang/import', {method: 'POST', body: fd, signal: impCtl.signal});
            _gdhBusyLabel(impLbl, false);          // server đã import xong: tắt vòng xoay ngay (bước tải lại bảng bên dưới có hiệu ứng làm mờ riêng)
            await new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));      // cho trình duyệt vẽ lại nút TRƯỚC khi alert() chặn giao diện
            let msg = `Đã import ${j.total_parts} mã, kỳ ${_gdhDate(j.period_from)} - ${_gdhDate(j.period_to)}.\n(${j.period_note})`;
            if (j.template === '1') msg += '\n\nLưu ý: file này chỉ có cột "Xuất kho" (gồm cả xuất chuyển kho...), nên số Xuất có thể lớn hơn số bán thật.';
            alert(msg);
            _gdhMarkDirty(false); await gdhLoadBatches(j.batch_id);
        } catch (e) {
            alert(e.name === 'AbortError' ? 'Máy chủ xử lý file quá lâu (hơn 3 phút) hoặc mất kết nối nên đã dừng chờ. Hãy kiểm tra lại danh sách đợt gôm (file có thể đã import xong), nếu chưa có thì thử lại hoặc báo admin.' : e.message);
            if (e.data && e.data.need_period) { if (CURRENT_ROLE === 'admin') { document.getElementById('gdhImpPeriod').classList.remove('d-none'); document.getElementById('gdhImpFrom').focus(); } }
        } finally {
            clearTimeout(impTimer);
            _gdhBusyLabel(impLbl, false);
        }
    }

    // ---------- Bảng gôm ----------
    let _gdhBatchMeta = null, _gdhSaveT = null, _gdhFlashT = null;
    function _gdhBadge(txt, cls) {
        const el = document.getElementById('gdhDirty');
        el.className = 'badge ' + cls; el.textContent = txt;
    }
    function _gdhScheduleSave() {          // tự lưu sau 1,5 giây ngừng gõ -> không cần bấm Lưu / không mất dữ liệu khi lọc, đổi trang
        clearTimeout(_gdhSaveT);
        _gdhSaveT = setTimeout(() => { if (_gdhDirty) gdhSave(true); }, 1500);
    }
    function gdhToggleMax(force) {         // phóng to khu vực soạn đơn kín màn hình; Esc hoặc bấm lại để thu nhỏ
        const pane = document.getElementById('gdh-pane'); if (!pane) return;
        const on = typeof force === 'boolean' ? force : !pane.classList.contains('gdh-max');
        pane.classList.toggle('gdh-max', on);
        const b = document.getElementById('gdhMaxBtn');
        if (b) { b.querySelector('span').textContent = on ? 'Thu nhỏ (Esc)' : 'Phóng to'; b.querySelector('i').className = 'bi me-1 ' + (on ? 'bi-fullscreen-exit' : 'bi-arrows-fullscreen'); }
    }
    document.addEventListener('keydown', ev => {
        if (ev.key !== 'Escape') return;
        const pane = document.getElementById('gdh-pane');
        if (pane && pane.classList.contains('gdh-max')) { ev.preventDefault(); gdhToggleMax(false); }
    });
    document.getElementById('gdh-tab')?.addEventListener('hidden.bs.tab', () => gdhToggleMax(false));   // rời tab Gôm đơn thì tự thu nhỏ
    function gdhToggleTop() {
        const pane = document.getElementById('gdh-pane');
        const collapsed = pane.classList.toggle('gdh-top-collapsed');
        const b = document.getElementById('gdhTopBtn');
        b.querySelector('span').textContent = collapsed ? 'Mở rộng' : 'Thu gọn';
        b.querySelector('i').className = 'bi me-1 ' + (collapsed ? 'bi-arrows-expand' : 'bi-arrows-collapse');
    }
    async function gdhBundleInfo() {
        try {
            const j = await _gdhJson('/api/gom-don-hang/bundle-info');
            const el = document.getElementById('gdhBundleInfo'); if (!el) return;
            el.textContent = j.count ? `Quy cách: ${_gdhFmt(j.count, 0)} mã con → mã cha · ${j.filename || ''} · ${j.uploaded_at || ''}` : 'Chưa có file quy cách mã con → mã cha';
            el.title = (j.warnings || []).join('\n');
        } catch (e) { /* bỏ qua */ }
    }
    async function gdhBundleImport(input) {
        const file = input.files[0]; if (!file) return;
        const fd = new FormData(); fd.append('file', file); input.value = '';
        try {
            const j = await _gdhJson('/api/gom-don-hang/bundle-import', {method: 'POST', body: fd});
            alert(`Đã import ${j.count} mã quy cách (ghi đè file cũ).` + (j.warnings.length ? '\n\nLưu ý:\n- ' + j.warnings.join('\n- ') : ''));
            gdhBundleInfo(); if (_gdhBatchId) gdhLoad(_gdhPage);
        } catch (e) { alert(e.message); }
    }
    function gdhKey() { /* giữ lại cho onkeydown cũ - việc di chuyển ô giờ do xlGridInit() lo (dùng chung với bảng Duyệt đơn) */ }

    // ===== Bàn phím + tô sáng hàng kiểu Excel cho bảng Gôm đơn (#gdh-body) và Duyệt đơn (#order-check-body) =====
    //  ↑ ↓ ← →: sang ô trên / dưới / trái / phải (các ô nhập của hàng: Gôm đơn = +/- thêm → Loại đơn → Ghi chú; Duyệt đơn = SL Duyệt → Ghi chú)
    //  Enter: xuống ô dưới (Shift+Enter: lên). Trong ô chữ, ←/→ chỉ nhảy ô khi con trỏ đang ở đầu/cuối chữ hoặc chữ đang được bôi đen,
    //  nên vẫn sửa chữ bình thường. Ô "Loại đơn": ↑↓ cũng đổi ô; bấm Space hoặc Alt+↓ để mở danh sách chọn.
    //  Ô Ghi chú có gợi ý mẫu: Alt+↓ để mở danh sách mẫu. Hàng đang chọn được tô xanh cả hàng, ô đang gõ có viền đậm.
    const XL_GRIDS = '#gdh-body, #order-check-body';
    const XL_CELL = 'input:not([disabled]):not([type=hidden]):not([type=checkbox]):not([type=radio]), select:not([disabled])';
    let _xlRow = null, _xlCell = null;
    function _xlMark(tr, td) {
        if (_xlRow && _xlRow !== tr) _xlRow.classList.remove('xl-row');
        if (_xlCell && _xlCell !== td) _xlCell.classList.remove('xl-cell');
        _xlRow = tr || null; _xlCell = td || null;
        if (_xlRow) _xlRow.classList.add('xl-row');
        if (_xlCell) _xlCell.classList.add('xl-cell');
    }
    function xlGridInit() {
        if (window._xlGridOn) return; window._xlGridOn = true;
        document.addEventListener('focusin', e => {
            const g = e.target.closest && e.target.closest(XL_GRIDS); if (!g) return;
            _xlMark(e.target.closest('tr'), e.target.closest('td'));
        });
        document.addEventListener('click', e => {                 // bấm vào ô không nhập được (mã, tên, tồn...) cũng tô sáng hàng, giống Excel
            const g = e.target.closest && e.target.closest(XL_GRIDS); if (!g) return;
            const tr = e.target.closest('tr'); if (tr && tr.querySelector('td:nth-child(2)')) _xlMark(tr, e.target.closest('td') && e.target.matches(XL_CELL) ? e.target.closest('td') : null);
        });
        document.addEventListener('focusout', e => {              // rời hẳn khỏi bảng thì bỏ viền ô (hàng vẫn giữ màu)
            if (_xlCell && e.target.closest && e.target.closest('td') === _xlCell) { _xlCell.classList.remove('xl-cell'); _xlCell = null; }
        });
        document.addEventListener('keydown', e => {
            const el = e.target, k = e.key;
            if (!el.closest || !el.closest(XL_GRIDS) || !el.matches(XL_CELL)) return;
            if (e.isComposing || e.ctrlKey || e.metaKey || e.altKey) return;       // Alt+↓ giữ nguyên để mở danh sách gợi ý của trình duyệt
            let dr = 0, dc = 0;
            if (k === 'Enter') dr = e.shiftKey ? -1 : 1;
            else if (e.shiftKey) return;
            else if (k === 'ArrowDown') dr = 1; else if (k === 'ArrowUp') dr = -1;
            else if (k === 'ArrowRight') dc = 1; else if (k === 'ArrowLeft') dc = -1;
            else return;
            if (dc) {                                                              // ←/→ trong ô chữ: chỉ nhảy ô khi không còn gì để di chuyển con trỏ
                const ss = el.selectionStart, se = el.selectionEnd;
                if (el.tagName === 'INPUT' && typeof ss === 'number' && el.type === 'text') {
                    const len = el.value.length, all = ss === 0 && se === len;
                    if (!all && !(dc < 0 && ss === 0 && se === 0) && !(dc > 0 && ss === len && se === len)) return;
                }
            }
            const tr = el.closest('tr'), cells = [...tr.querySelectorAll(XL_CELL)], ci = cells.indexOf(el);
            let target = null;
            if (dc) target = cells[ci + dc] || null;
            else {
                let r = dr > 0 ? tr.nextElementSibling : tr.previousElementSibling;
                while (r) {
                    const cs = r.offsetParent === null ? [] : [...r.querySelectorAll(XL_CELL)];
                    if (cs.length) { target = cs[Math.min(ci, cs.length - 1)]; break; }
                    r = dr > 0 ? r.nextElementSibling : r.previousElementSibling;
                }
            }
            e.preventDefault();                                                    // chặn cả việc ↑↓ đổi số / đổi lựa chọn của ô
            if (!target) return;                                                   // hết hàng / hết cột: đứng yên như Excel
            target.focus({preventScroll: true});
            if (typeof target.select === 'function' && target.tagName === 'INPUT') target.select();
            target.scrollIntoView({block: 'nearest', inline: 'nearest'});
        }, true);
    }
    xlGridInit();
    function gdhAdjInput(i, el) {          // chỉ cho gõ số nguyên (có thể âm)
        const v = el.value.replace(/[^\d-]/g, '').replace(/(?!^)-/g, '');
        if (v !== el.value) el.value = v;
        const n = parseInt(v, 10);
        gdhEdit(i, 'adj', isNaN(n) ? 0 : n);
    }
    function _gdhCalcAmt(r) {           // cùng công thức với compute_rows: giá vốn (mã cha nếu có) x SL cuối (đã là đơn vị mã cha)
        if (r.ord_cost === null || r.ord_cost === undefined) return null;
        return r.ord_cost * r.qty_final;
    }
    function _gdhOrdHtml(r) {              // ô "Đặt hàng": mã + SL thực tế (đã quy cha / thay thế)
        const code = r.bundle_parent || (r.locked && r.lock_replace ? r.lock_replace : null);
        if (!code) return '<span class="text-muted">–</span>';
        if (r.qty_final <= 0) return `<div class="gdh-ord"><span class="gdh-tag">${r.bundle_parent ? 'Quy cách' : 'Thay thế'}</span> <span class="gdh-pc">${_gdhEsc(code)}</span>` +
                                     (r.bundle_parent ? `<div class="gdh-sub">1 = ${_gdhFmt(r.bundle_ratio, 2)} mã con</div>` : '') + '</div>';
        if (!r.bundle_parent) return `<div class="gdh-ord"><span class="gdh-tag">Thay thế</span> <span class="gdh-pc">${_gdhEsc(code)}</span><div class="gdh-sub">SL <b>${_gdhFmt(r.qty_final, 0)}</b></div></div>`;
        const q = r.qty_final;               // SL cuối của mã có mã cha đã tính bằng đơn vị MÃ CHA
        return `<div class="gdh-ord"><span class="gdh-tag">Quy cách</span> <span class="gdh-pc">${_gdhEsc(code)}</span>` +
               `<div class="gdh-sub">Đặt <b class="text-body">${_gdhFmt(q, 0)}</b> (1 = ${_gdhFmt(r.bundle_ratio, 2)})</div></div>`;
    }
    const _GDH_STORES = ['NS1', 'NS2', 'NS3', 'NS4', 'NS5', 'NSM1'];
    function _gdhStockCells(r) {           // Cột Tồn = Tồn CUỐI KỲ đúng như trong file import gần nhất của chi nhánh (số dùng để tính đề xuất) + tồn hệ thống từng chi nhánh khác (cột của chi nhánh đang gôm bị ẩn)
        const hs = (r.closing === null || r.closing === undefined) ? '<span class="text-muted">–</span>' : _gdhFmt(r.closing);
        return `<td class="gdh-num gdh-hs">${hs}</td>` + _GDH_STORES.map((k, n) => {
            const v = (r.stock_by_store || {})[k];
            const inner = (v === undefined || v === null) ? '<span class="text-muted">·</span>' : (v === 0 ? '<span class="gdh-zero">0</span>' : _gdhFmt(v));
            return `<td class="gdh-num gdh-st-td ${n === 0 ? 'gdh-st0' : ''}" data-st="${k}">${inner}</td>`;
        }).join('');
    }
    function _gdhPoCell(own, par, ratio, cls) {   // số nợ / đang VC của mã; mã con có PO đặt bằng mã cha thì ghi thêm dòng nhỏ
        if (own === null || own === undefined) return '<td class="gdh-num"><span class="text-muted">–</span></td>';
        const main = own > 0 ? `<b class="${cls}">${_gdhFmt(own, 0)}</b>` : '<span class="gdh-zero">0</span>';
        const sub = par > 0 ? `<div class="gdh-sub" title="PO đặt bằng mã cha, quy đổi ra mã con">+cha ${_gdhFmt(par, 0)} (=${_gdhFmt(par * ratio, 0)} con)</div>` : '';
        return `<td class="gdh-num">${main}${sub}</td>`;
    }
    function _gdhPoCells(r) {
        return _gdhPoCell(r.debt_qty, r.parent_debt, r.bundle_ratio, 'text-danger') +
               _gdhPoCell(r.ship_qty, r.parent_ship, r.bundle_ratio, 'text-primary');
    }
    function _gdhBundleTag(r) {            // dưới tên hàng: mã cha + số quy đổi (luôn nhìn thấy, không cần cuộn ngang)
        if (!r.bundle_parent) return '';
        return `<div class="mt-1"><span class="gdh-tag">Quy cách</span> <span class="gdh-sub">→ mã cha <b class="text-primary">${_gdhEsc(r.bundle_parent)}</b> · 1 = ${_gdhFmt(r.bundle_ratio, 2)}</span></div>`;
    }
    function gdhRowHtml(r, i, optT) {
        const ro = _gdhReadOnly ? 'disabled' : '';
        const lock = r.locked
            ? `<div class="mt-1"><span class="badge bg-danger me-1">Khoá đặt hàng</span>` +
              (r.lock_replace ? `<span class="gdh-sub">thay bằng <b class="text-body">${_gdhEsc(r.lock_replace)}</b></span>` : `<span class="text-danger gdh-sub">chưa có mã thay thế</span>`) + `</div>`
            : '';
        const tip = r.sold !== null && r.out_qty !== null ? 'SL bán ' + _gdhFmt(r.sold) + ' · Xuất kho ' + _gdhFmt(r.out_qty)
                  : (r.sold !== null ? 'SL bán hàng' : 'Xuất kho (gồm cả xuất chuyển kho...)');
        return `<tr id="gdh-row-${i}" class="${r.qty_final > 0 ? 'gdh-on' : ''}">
            <td class="gdh-sticky gdh-code">${_gdhEsc(r.part_code)}</td>
            <td class="gdh-name gdh-fz2">${_gdhEsc(r.part_name)}${lock}${_gdhBundleTag(r)}</td>
            <td class="gdh-fz3" style="white-space:nowrap"><span class="badge ${_gdhGrpCls[r.group] || 'bg-secondary'}">${r.group}</span><div class="gdh-sub">${_gdhFmt(r.avg_month, 1)}/tháng · ${_gdhFmt(r.avg_week, 1)}/tuần</div></td>
            ${_gdhStockCells(r)}
            <td class="gdh-num gdh-st0 ${r.sales > 0 ? '' : 'gdh-zero'}" title="${tip}">${_gdhFmt(r.sales)}</td>${_gdhPoCells(r)}
            <td class="gdh-num ${r.suggest > 0 ? 'gdh-cell-sug' : 'gdh-zero'}">${_gdhFmt(r.suggest)}${r.bundle_parent ? `<div class="gdh-sub" title="Đề xuất tính theo mã con, đã quy ra mã cha ở số bên trên">${_gdhFmt(r.suggest_child, 0)} con</div>` : ''}</td>
            <td><input type="text" inputmode="numeric" autocomplete="off" class="form-control form-control-sm gdh-in gdh-in-adj" data-col="adj" data-i="${i}"
                       placeholder="0" ${ro} value="${r.adj ? r.adj : ''}" onfocus="this.select()" oninput="gdhAdjInput(${i},this)" onkeydown="gdhKey(event,this)"></td>
            <td class="gdh-num ${r.qty_final > 0 ? 'gdh-cell-fin' : 'gdh-zero'}" id="gdh-fin-${i}">${_gdhFmt(r.qty_final)}</td>
            <td><select class="form-select form-select-sm gdh-in gdh-in-type" ${ro} onchange="gdhEdit(${i},'order_type',this.value)">${optT(r.order_type)}</select></td>
            <td id="gdh-ord-${i}">${_gdhOrdHtml(r)}</td>
            <td><input type="text" maxlength="500" autocomplete="off" class="form-control form-control-sm gdh-in gdh-in-note" data-col="note" data-i="${i}"
                       placeholder="Ghi chú..." ${ro} title="${_gdhEsc(r.note)}" value="${_gdhEsc(r.note)}" oninput="gdhEdit(${i},'note',this.value)" onkeydown="gdhKey(event,this)"></td>
            <td class="gdh-num"><div id="gdh-amt-${i}" class="${r.amount ? '' : 'gdh-zero'}">${_gdhFmt(r.amount, 0)}</div><div class="gdh-sub">${r.ord_cost === null || r.ord_cost === undefined ? (r.qty_final > 0 ? '<span class="text-danger" title="Không có giá vốn / giá nhập của mã này">Thiếu GV ' + _gdhEsc(r.cost_code || '') + '</span>' : '') : (r.bundle_parent ? 'GV cha ' : 'GV ') + _gdhFmt(r.ord_cost, 0)}</div></td></tr>`;
    }
    function gdhMarkFilters() {          // tô xanh ô lọc đang có giá trị khác mặc định
        const on = (id, v) => { const e = document.getElementById(id); if (e) e.classList.toggle('gdh-f-on', !!v); };
        on('gdhGroup', document.getElementById('gdhGroup').value);
        on('gdhTypeFilter', document.getElementById('gdhTypeFilter').value);
        on('gdhScope', document.getElementById('gdhScope').value !== 'sold');
        on('gdhQ', document.getElementById('gdhQ').value.trim());
    }
    async function gdhLoad(page) {
        if (typeof _gdhShowUndo === 'function') _gdhShowUndo();
        gdhMarkFilters();
        if (!_gdhBatchId) return;
        const tb = document.getElementById('gdh-body');
        if (page < 1) page = 1;
        if (_gdhDirty && !(await gdhSave(true))) return;     // tự lưu thay đổi dở dang thay vì hỏi/mất dữ liệu
        if (_gdhRows && _gdhRows.length) tb.style.opacity = '.45';      // giữ nguyên bảng cũ, chỉ làm mờ trong lúc tải
        else tb.innerHTML = '<tr class="ns-skel-row"><td colspan="20"><div class="ns-skel"></div></td></tr><tr class="ns-skel-row"><td colspan="20"><div class="ns-skel"></div></td></tr><tr class="ns-skel-row"><td colspan="20"><div class="ns-skel"></div></td></tr><tr class="ns-skel-row"><td colspan="20"><div class="ns-skel"></div></td></tr><tr class="ns-skel-row"><td colspan="20"><div class="ns-skel"></div></td></tr>';
        const g = id => document.getElementById(id).value;
        const p = new URLSearchParams({batch_id: _gdhBatchId, page, page_size: 200, q: g('gdhQ').trim(), group: g('gdhGroup'),
                                       order_type: g('gdhTypeFilter'), scope: document.getElementById('gdhScope').value});
        try {
            const j = await _gdhJson('/api/gom-don-hang/lines?' + p);
            _gdhPage = j.page; _gdhTotal = j.total_rows; _gdhPageSize = j.page_size; _gdhTypes = j.order_types;
            _gdhEdited = {}; _gdhMarkDirty(false);
            _gdhBatchMeta = j.batch;
            gdhRenderHeader(j.batch, j.totals);
            _gdhRows = j.data;
            tb.innerHTML = _gdhRows.length ? _gdhRows.map((r, i) => gdhRowHtml(r, i, _gdhOptT)).join('')
                : '<tr><td colspan="20" class="text-center py-4 text-muted">Không có mã nào khớp bộ lọc.</td></tr>';
            document.getElementById('gdhScroll').scrollTop = 0;
            document.getElementById('gdhCount').textContent = _gdhFmt(_gdhTotal, 0) + ' mã';
            document.getElementById('gdhPager').classList.toggle('d-none', _gdhTotal <= _gdhPageSize);
            document.getElementById('gdhPageInfo').textContent = 'Trang ' + _gdhPage + '/' + Math.max(1, Math.ceil(_gdhTotal / _gdhPageSize));
        } catch (e) { tb.innerHTML = `<tr><td colspan="20" class="text-center py-4 text-danger">${_gdhEsc(e.message)}</td></tr>`; }
        finally { tb.style.opacity = ''; }
    }
    const _gdhOptT = v => '<option value=""></option>' + _gdhTypes.map(t => `<option value="${_gdhEsc(t)}" ${t === v ? 'selected' : ''}>${_gdhEsc(t)}</option>`).join('');
    // ---------- Thêm mã ngoài file (mã không có trong file Tổng hợp tồn kho của kỳ) ----------
    function gdhAddToggle(force) {
        const el = document.getElementById('gdhAddPanel');
        const show = force === undefined ? el.classList.contains('d-none') : !!force;
        el.classList.toggle('d-none', !show);
        if (show) { document.getElementById('gdhAddQ').focus(); }
    }
    let _gdhAddT = null;
    function gdhAddSearch() {
        clearTimeout(_gdhAddT);
        const box = document.getElementById('gdhAddRes'), q = document.getElementById('gdhAddQ').value.trim();
        if (q.length < 2) { box.innerHTML = ''; return; }
        _gdhAddT = setTimeout(async () => {
            if (!_gdhBatchId) { box.innerHTML = '<div class="text-muted">Chọn hoặc import một đợt trước.</div>'; return; }
            box.innerHTML = '<div class="text-muted">Đang tìm...</div>';
            try {
                const j = await _gdhJson('/api/gom-don-hang/catalog-search?' + new URLSearchParams({batch_id: _gdhBatchId, q}));
                box.innerHTML = j.data.length ? j.data.map(v => `<div class="d-flex align-items-center gap-2 border-bottom py-1">
                    <span class="fw-semibold">${_gdhEsc(v.code)}</span><span class="text-muted text-truncate flex-grow-1">${_gdhEsc(v.name || '')}</span>
                    ${v.in_batch ? `<button class="btn btn-outline-secondary btn-sm py-0" data-code="${_gdhEsc(v.code)}" onclick="gdhAddShow(this.dataset.code)">Đã có - xem</button>`
                                 : `<button class="btn btn-primary btn-sm py-0" data-code="${_gdhEsc(v.code)}" onclick="gdhAddCodes([this.dataset.code])">Thêm</button>`}</div>`).join('')
                    : '<div class="text-muted">Không thấy trong danh mục. Có thể bấm "Thêm mã đã gõ" để thêm mã mới (sẽ hỏi xác nhận).</div>';
            } catch (e) { box.innerHTML = `<div class="text-danger">${_gdhEsc(e.message)}</div>`; }
        }, 300);
    }
    function gdhAddShow(code) {             // chuyển bảng sang "Cả danh mục" và lọc đúng mã đó để nhập số lượng
        document.getElementById('gdhScope').value = 'all';
        ['gdhGroup', 'gdhTypeFilter'].forEach(id => { document.getElementById(id).value = ''; });
        document.getElementById('gdhQ').value = code;
        gdhLoad(1);
    }
    async function gdhAddTyped() {
        const raw = document.getElementById('gdhAddQ').value.trim();
        if (!raw) return;
        await gdhAddCodes(raw.split(/[\s,;]+/).filter(Boolean));
    }
    async function gdhAddCodes(codes, confirmNew) {
        if (!_gdhBatchId) { alert('Chọn hoặc import một đợt trước.'); return; }
        if (_gdhDirty && !(await gdhSave(true))) return;
        try {
            const j = await _gdhJson('/api/gom-don-hang/add-codes', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                     body: JSON.stringify({batch_id: _gdhBatchId, codes, confirm_new: !!confirmNew})});
            const show = (j.added[0] || j.existing[0] || codes[0]);
            gdhAddToggle(false); document.getElementById('gdhAddQ').value = ''; document.getElementById('gdhAddRes').innerHTML = '';
            gdhAddShow(show);
        } catch (e) {
            if (e.data && e.data.need_confirm) {
                if (await nsConfirm(e.message + '\n\nBạn chắc chắn mã đúng và muốn thêm?')) return gdhAddCodes(codes, true);
                return;
            }
            alert(e.message);
        }
    }
    async function gdhWeeksChanged() {     // đổi SỐ TUẦN DỰ KIẾN: chỉ lưu + tính lại đề xuất ngay trên trình duyệt, KHÔNG tải lại bảng
        const el = document.getElementById('gdhWeeks');
        const fw = parseInt(el.value, 10), old = _gdhBatchMeta ? _gdhBatchMeta.forecast_weeks : 3;
        if (!(fw >= 1 && fw <= 12)) { alert('Số tuần dự kiến phải từ 1 đến 12.'); el.value = old; return; }
        if (fw === old) return;
        if (!(await gdhSave(true))) { el.value = old; return; }
        _gdhBatchMeta.forecast_weeks = fw;
        _gdhRows.forEach(r => {            // cùng công thức với compute_line ở server
            const stock = Math.max(r.closing || 0, 0);
            r.suggest_child = Math.max(0, Math.ceil(r.avg_week * fw - stock - 1e-9));
            r.suggest = r.bundle_parent ? Math.ceil(r.suggest_child / r.bundle_ratio - 1e-9) : r.suggest_child;   // có mã cha: quy ra mã cha
            r.qty_final = Math.max(0, Math.round(r.suggest + r.adj));
            r.amount = _gdhCalcAmt(r);
        });
        const sc = document.getElementById('gdhScroll'), top = sc.scrollTop;
        document.getElementById('gdh-body').innerHTML = _gdhRows.map((r, i) => gdhRowHtml(r, i, _gdhOptT)).join('');
        sc.scrollTop = top;
    }
    function gdhRenderHeader(b, t) {
        document.getElementById('gdhFrom').value = b.from; document.getElementById('gdhTo').value = b.to;
        _gdhLockPeriod();
        document.getElementById('gdhWeeks').value = b.forecast_weeks;
        document.getElementById('gdhInfo').textContent = `Kỳ ${b.days} ngày ≈ ${b.weeks} tuần / ${_gdhFmt(b.months, 2)} tháng · File: ${b.filenames || ''} · ${b.uploaded_by || ''} ${b.uploaded_at || ''}`;
        document.getElementById('gdhThSys').title = 'Tồn kho ' + b.store + (b.inv_at ? ' (cập nhật ' + b.inv_at + ')' : '') + '. Số màu cam gạch chân = khác Tồn cuối kỳ trong file import (Đề xuất tính theo Tồn cuối kỳ của file).';
        let st = document.getElementById('gdhHideStore');
        if (!st) { st = document.createElement('style'); st.id = 'gdhHideStore'; document.head.appendChild(st); }
        st.textContent = `#gdh-pane [data-st="${b.store}"] { display: none; }`;   // chi nhánh đang gôm đã có cột Tồn HT
        gdhRenderStatus(b);
        gdhRenderSummary(b, t);
    }
    function gdhRenderSummary(b, t) {      // cảnh báo + thẻ tổng; gọi lại sau mỗi lần lưu (không đụng tới ô ngày / số tuần đang gõ)
        const warn = [];
        if (!b.sold_lines) warn.push('Chưa có file mẫu "SL bán hàng" cho kỳ này: đang dùng cột Xuất kho (gồm cả xuất chuyển kho...) nên số bán và đề xuất có thể cao hơn thực tế.');
        if (!b.cost_lines) warn.push('Chưa có giá vốn từ file (mẫu có Giá trị): thành tiền lấy theo giá nhập trong hệ thống, mã không có giá sẽ để trống.');
        if (t.no_cost) warn.push(`${_gdhFmt(t.no_cost, 0)} mã cần đặt chưa có giá vốn (vd: ${(t.no_cost_samples || []).join(', ')}). Kiểm tra mã đó đã có trong file giá nhập (Duyệt đơn hàng) hoặc file tồn kho mẫu 1 chưa.`);
        if (t.unassigned_type) warn.push(`${_gdhFmt(t.unassigned_type, 0)} mã cần đặt CHƯA chọn loại đơn (sẽ không vào file đặt hàng / dashboard).`);
        if (t.locked_no_replace) warn.push(`${_gdhFmt(t.locked_no_replace, 0)} mã cần đặt đang bị khoá đặt hàng và chưa có mã thay thế.`);
        document.getElementById('gdhWarn').innerHTML = warn.map(w => `<div class="alert alert-warning py-1 px-2 small mb-1"><i class="bi bi-exclamation-triangle me-1"></i>${_gdhEsc(w)}</div>`).join('');
        const _sCls = {'Định kỳ':'gdh-s-periodic','Khẩn':'gdh-s-urgent','Đơn 26':'gdh-s-d26'};
        const card = (title, main, sub, cls) => `<div class="col-6 col-xl-3"><div class="gdh-stat ${cls || ''}"><div class="gdh-stat-t">${title}</div><div class="gdh-stat-v">${main}</div><div class="gdh-stat-s">${sub}</div></div></div>`;
        const freq = Object.entries(t.freq).map(([k, v]) => `${k} ${_gdhFmt(v, 0)}`).join(' · ');
        document.getElementById('gdhSummary').innerHTML =
            card('Mã cần đặt', `${_gdhFmt(t.to_order, 0)} <small>mã</small>`, freq + ' (toàn đợt)', t.to_order > 0 ? 'gdh-s-total' : 'gdh-s-zero') +
            _gdhTypes.map(x => { const v = t.by_type[x] || {parts: 0, qty: 0, amount: 0}; return card(_gdhEsc(x), `${_gdhFmt(v.parts, 0)} <small>mã</small>`, `SL ${_gdhFmt(v.qty, 0)} · ${_gdhFmt(v.amount, 0)} đ`, v.parts > 0 ? (_sCls[x] || '') : 'gdh-s-zero'); }).join('');
    }
    function gdhEdit(i, field, val) {
        const r = _gdhRows[i]; if (!r) return;
        const e = (_gdhEdited[r.part_code] = _gdhEdited[r.part_code] || {adj: r.adj, order_type: r.order_type, note: r.note || ''});
        if (field === 'adj') { const n = parseFloat(val); e.adj = isNaN(n) ? 0 : n; r.adj = e.adj; }
        else if (field === 'note') { e.note = val; r.note = val; }
        else { e.order_type = val; r.order_type = val; }
        r.qty_final = Math.max(0, Math.round(r.suggest + r.adj));
        r.amount = _gdhCalcAmt(r);
        const fin = document.getElementById('gdh-fin-' + i);
        fin.textContent = _gdhFmt(r.qty_final); fin.classList.toggle('gdh-cell-fin', r.qty_final > 0); fin.classList.toggle('gdh-zero', r.qty_final <= 0);
        const am = document.getElementById('gdh-amt-' + i); am.textContent = _gdhFmt(r.amount, 0); am.classList.toggle('gdh-zero', !r.amount);
        document.getElementById('gdh-ord-' + i).innerHTML = _gdhOrdHtml(r);
        document.getElementById('gdh-row-' + i).classList.toggle('gdh-on', r.qty_final > 0);
        _gdhMarkDirty(true); _gdhScheduleSave();
    }
    async function gdhSave(silent) {
        if (!_gdhBatchId) return false;
        clearTimeout(_gdhSaveT);
        const snap = _gdhEdited; _gdhEdited = {};              // chụp lại để các thay đổi gõ thêm trong lúc lưu không bị mất
        const items = Object.entries(snap).map(([code, e]) => ({part_code: code, adj: e.adj, order_type: e.order_type, note: e.note}));
        const body = {batch_id: _gdhBatchId, items, forecast_weeks: document.getElementById('gdhWeeks').value,
                      period_from: document.getElementById('gdhFrom').value, period_to: document.getElementById('gdhTo').value};
        if (items.length) { clearTimeout(_gdhFlashT); _gdhBadge('Đang lưu…', 'bg-secondary'); }
        try {
            const j = await _gdhJson('/api/gom-don-hang/save', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
            if (j.totals && _gdhBatchMeta) gdhRenderSummary(_gdhBatchMeta, j.totals);
            if (Object.keys(_gdhEdited).length) { _gdhMarkDirty(true); _gdhScheduleSave(); }
            else {
                _gdhDirty = false;
                if (items.length) { _gdhBadge('Đã lưu ✓', 'bg-success'); clearTimeout(_gdhFlashT); _gdhFlashT = setTimeout(() => { if (!_gdhDirty) document.getElementById('gdhDirty').classList.add('d-none'); }, 2000); }
                else document.getElementById('gdhDirty').classList.add('d-none');
            }
            if (!silent) await gdhLoadBatches(_gdhBatchId);
            return true;
        } catch (e) {
            _gdhEdited = Object.assign({}, snap, _gdhEdited); _gdhMarkDirty(true);
            alert(e.message); return false;
        }
    }
    async function gdhSettingsChanged() {
        if (await gdhSave(true)) await gdhLoadBatches(_gdhBatchId);
    }
    // Nhớ lần Gán nhanh gần nhất (trong phiên) để có thể hoàn tác chính xác đúng các mã đó.
    let _gdhLastAssign = null;
    function _gdhShowUndo() {
        const b = document.getElementById('gdhUndoBtn'); if (!b) return;
        if (_gdhLastAssign && _gdhLastAssign.batch === _gdhBatchId && _gdhLastAssign.codes.length) {
            b.textContent = `Hoàn tác (${_gdhLastAssign.codes.length})`; b.classList.remove('d-none');
        } else b.classList.add('d-none');
    }
    async function gdhAssignType() {
        if (!_gdhBatchId) return;
        if (_gdhDirty && !(await gdhSave(true))) return;
        const ot = document.getElementById('gdhBulkType').value;
        if (!await nsConfirm(`Gán loại đơn "${ot}" cho các mã cần đặt (SL cuối > 0) mà chưa chọn loại đơn?\nMã đã có loại đơn (kể cả loại khác) được giữ nguyên.`)) return;
        const asgBtn = document.getElementById('gdhAssignBtn');
        _gdhBusy(asgBtn, true, 'Đang áp dụng...');
        try {
            const j = await _gdhJson('/api/gom-don-hang/assign-type', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: _gdhBatchId, order_type: ot, overwrite: false})});
            _gdhBusy(asgBtn, false);               // server đã gán xong: tắt vòng xoay ngay, không chờ hộp thoại / tải lại bảng
            alert('Đã đặt loại đơn "' + ot + '" cho ' + j.assigned + ' mã.');
            await gdhLoadBatches(_gdhBatchId);
        } catch (e) { alert(e.message); }
        finally { _gdhBusy(asgBtn, false); }
    }
    // Hoàn tác đúng lần Gán nhanh vừa rồi: chỉ bỏ gán các mã vừa được gán, không đụng mã bạn đã chọn tay trước đó.
    async function gdhUndoLastAssign() {
        const la = _gdhLastAssign;
        if (!la || la.batch !== _gdhBatchId || !la.codes.length) return;
        if (_gdhDirty && !(await gdhSave(true))) return;
        if (!await nsConfirm(`Hoàn tác lần gán "${la.type}" vừa rồi (${la.codes.length} mã)?`)) return;
        try {
            const j = await _gdhJson('/api/gom-don-hang/unassign-type', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: _gdhBatchId, order_type: la.type, codes: la.codes})});
            _gdhLastAssign = null;
            alert('Đã bỏ gán ' + j.count + ' mã.'); await gdhLoadBatches(_gdhBatchId); _gdhShowUndo();
        } catch (e) { alert(e.message); }
    }
    // Bỏ gán hàng loạt: xoá loại đơn đang chọn ở ô bên cạnh khỏi MỌI mã của đợt (kể cả mã gán tay).
    async function gdhUnassignType() {
        if (!_gdhBatchId) return;
        if (_gdhDirty && !(await gdhSave(true))) return;
        const ot = document.getElementById('gdhBulkType').value;
        const body = t => JSON.stringify(Object.assign({batch_id: _gdhBatchId, order_type: ot}, t));
        const hdr = {'Content-Type': 'application/json'};
        try {
            const d = await _gdhJson('/api/gom-don-hang/unassign-type', {method: 'POST', headers: hdr, body: body({dry_run: true})});
            if (!d.count) { alert(`Không có mã nào đang gán loại đơn "${ot}".`); return; }
            if (!await nsConfirm(`Bỏ loại đơn "${ot}" của ${d.count} mã trong đợt này (kể cả mã gán tay)?\nCác mã sẽ trở về "chưa chọn loại đơn".`)) return;
            const j = await _gdhJson('/api/gom-don-hang/unassign-type', {method: 'POST', headers: hdr, body: body({})});
            _gdhLastAssign = null;
            alert('Đã bỏ gán ' + j.count + ' mã.'); await gdhLoadBatches(_gdhBatchId); _gdhShowUndo();
        } catch (e) { alert(e.message); }
    }
    async function _gdhDownload(url) {
        const res = await fetch(url);
        if (!res.ok) { const j = await res.json().catch(() => ({})); alert(j.error || 'Không xuất được file.'); return; }
        const blob = await res.blob();
        const cd = res.headers.get('Content-Disposition') || '';
        const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob); a.download = m ? decodeURIComponent(m[1]) : 'gom-don-hang.xlsx';
        document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    }
    async function gdhExport(kind) {
        if (!_gdhBatchId) { alert('Chưa chọn đợt gôm.'); return; }
        if (_gdhDirty && !(await gdhSave(true))) return;      // tự lưu trước khi xuất
        _gdhDownload('/api/gom-don-hang/export?kind=' + kind + '&batch_id=' + _gdhBatchId);
    }

    // =====================================================================
    // DUYỆT ĐƠN GÔM: Nháp -> Chờ duyệt -> Đang duyệt -> Đã duyệt -> Đã xem -> Đã đặt
    // =====================================================================
    let _gdhReadOnly = false;
    const _GDH_ST_CLS = {draft: 'bg-secondary', pending: 'bg-warning text-dark', reviewing: 'bg-info text-dark', approved: 'bg-success', viewed: 'bg-primary', ordered: 'bg-dark'};
    const _gdhStBadge = (st, label) => `<span class="badge ${_GDH_ST_CLS[st] || 'bg-secondary'}">${_gdhEsc(label || st)}</span>`;
    const _gdhBtn = (cls, icon, text, fn) => `<button type="button" class="btn ${cls} btn-sm" onclick="${fn}"><i class="bi ${icon} me-1"></i>${text}</button>`;

    function _gdhClearStatus() {           // tab Gôm không còn đợt nào (vd: vừa đẩy đơn đi): bỏ thanh trạng thái cũ, mở lại các ô nhập
        const bar = document.getElementById('gdhStatusBar'); if (bar) bar.classList.add('d-none');
        _gdhReadOnly = false;
        document.getElementById('gdh-pane').classList.remove('gdh-ro');
        ['gdhFrom', 'gdhTo', 'gdhWeeks'].forEach(id => { const e = document.getElementById(id); if (e) e.disabled = false; });
        _gdhLockPeriod();
    }
    function _gdhLockPeriod() {            // kỳ số bán (từ ngày - đến ngày) do admin quy định: chi nhánh chỉ xem, không sửa
        if (CURRENT_ROLE === 'admin') return;
        ['gdhFrom', 'gdhTo'].forEach(id => { const e = document.getElementById(id); if (e) { e.disabled = true; e.title = 'Kỳ số bán do admin quy định, chi nhánh không sửa được'; } });
    }
    function _gdhGoOrders() { gdhSwitchView('orders'); gdhOrdersBadge(); }      // admin xong việc (duyệt/trả về): sang danh sách đơn
    function gdhRenderStatus(b) {          // thanh trạng thái + nút thao tác của đợt đang mở; khoá bảng khi đơn không còn sửa được
        const bar = document.getElementById('gdhStatusBar'), pane = document.getElementById('gdh-pane');
        _gdhReadOnly = !b.can_edit;
        pane.classList.toggle('gdh-ro', _gdhReadOnly);
        ['gdhFrom', 'gdhTo', 'gdhWeeks'].forEach(id => { const e = document.getElementById(id); if (e) e.disabled = _gdhReadOnly; });
        _gdhLockPeriod();
        const isAdmin = CURRENT_ROLE === 'admin';
        if (isAdmin && b.space !== 'store') { bar.classList.add('d-none'); return; }     // gôm riêng của admin: không qua luồng duyệt
        const st = b.status, e = _gdhEsc;
        const note = (label, v) => v ? ` · ${label}: <i>${e(v)}</i>` : '';
        let info = '', acts = '';
        if (st === 'draft') {
            info = b.reject_reason
                ? `<b class="text-danger">Đơn bị admin từ chối duyệt</b> bởi <b>${e(b.rejected_by)}</b> lúc ${e(b.rejected_at)} · Lý do: <i>${e(b.reject_reason)}</i>. ` + (isAdmin ? 'Đang chờ chi nhánh chỉnh sửa và đẩy lại.' : 'Hãy chỉnh sửa theo lý do trên rồi đẩy lại cho admin.')
                : (isAdmin ? 'Chi nhánh chưa đẩy đơn này (đang là bảng gôm nháp).' : 'Đơn nháp. Soạn xong hãy đẩy cho admin duyệt.');
            if (!isAdmin) acts = _gdhBtn('btn-primary', 'bi-send', 'Đẩy đơn cho admin', 'gdhSubmit()');
        } else if (st === 'pending') {
            info = `Đẩy bởi <b>${e(b.submitted_by)}</b> lúc ${e(b.submitted_at)}${note('Ghi chú', b.submit_note)}. ` + (isAdmin ? 'Duyệt tại menu Quản Trị Hệ Thống > Duyệt Đơn Hàng.' : 'Đang chờ admin lấy về duyệt - đơn đã khoá.');
            acts = isAdmin ? '' : _gdhBtn('btn-outline-danger', 'bi-arrow-counterclockwise', 'Thu hồi đơn', 'gdhRecall()');
        } else if (st === 'reviewing') {
            info = `<b>${e(b.claimed_by)}</b> đang duyệt từ ${e(b.claimed_at)}` + (isAdmin ? ' tại menu Duyệt Đơn Hàng.' : '. Đơn đang khoá.');
        } else {
            info = `Duyệt bởi <b>${e(b.approved_by)}</b> lúc ${e(b.approved_at)}${note('Ghi chú của admin', b.review_note)}`;
            if (b.viewed_at) info += ` · Đã xem lúc ${e(b.viewed_at)}`;
            if (b.ordered_at) info += ` · Đã đặt lúc ${e(b.ordered_at)}${b.ordered_by ? ' (' + e(b.ordered_by) + ')' : ''}`;
            acts = _gdhBtn(st === 'approved' && !isAdmin ? 'btn-warning' : 'btn-outline-secondary', 'bi-eye', 'Xem kết quả duyệt', `gdhOpenCompare(${b.id})`);
            if (!isAdmin) acts += _gdhBtn(st === 'ordered' ? 'btn-outline-success' : 'btn-success', 'bi-download', st === 'ordered' ? 'Tải lại file đặt hàng' : 'Tải đơn về', `gdhDownloadOrder(${b.id})`);
        }
        bar.dataset.st = (st === 'draft' && b.reject_reason) ? 'rejected' : st;
        bar.innerHTML = `<span>${_gdhStBadge(st, b.status_label)}${st === 'draft' && b.reject_reason ? ' <span class="badge bg-danger">Bị từ chối</span>' : ''}</span><span class="gdh-sub flex-grow-1" style="font-size:.85rem">${info}</span><span class="d-flex flex-wrap gap-2">${acts}</span>`;
        bar.classList.remove('d-none');
    }

    async function _gdhAct(action, body, doneMsg) {          // gọi 1 bước chuyển trạng thái; hỏi lại nếu server cần xác nhận (còn mã chưa chọn loại đơn)
        const hdr = {'Content-Type': 'application/json'};
        const go = (extra) => _gdhJson('/api/gom-don-hang/' + action, {method: 'POST', headers: hdr, body: JSON.stringify(Object.assign({batch_id: _gdhBatchId}, body || {}, extra || {}))});
        try {
            try { await go(); }
            catch (e) { if (e.data && e.data.need_confirm && await nsConfirm(e.message)) await go({confirm_unassigned: true}); else throw e; }
            if (doneMsg) alert(doneMsg);
            return true;
        } catch (e) { alert(e.message); return false; }
    }
    async function _gdhAfterAct() {
        if (!document.getElementById('gdhOrdersView').classList.contains('d-none')) await gdhOrdersLoad();
        else await gdhLoadBatches(_gdhBatchId);
        gdhOrdersBadge();
    }
    // ---------- ĐẨY ĐƠN: tóm tắt trước khi đẩy + cảnh báo / chặn trùng kỳ ----------
    async function gdhSubmit() {
        if (!_gdhBatchId) return;
        if (_gdhDirty && !(await gdhSave(true))) return;
        _gdhSubOpen();
    }
    let _gdhSub = null;
    const _gdhSubEl = id => document.getElementById('gdhSub' + id);
    const _GDH_SUB_COLOR = {'Định kỳ': '#2563eb', 'Khẩn': '#dc2626', 'Đơn 26': '#7c3aed'};
    const _GDH_SUB_THEME = {'Định kỳ': 't-blue', 'Khẩn': '', 'Đơn 26': 't-violet'};
    let _GDH_SUB_SKEL = '';
    function _gdhSubEnsure() {
        _ugmCss();
        if (_gdhSubEl('Modal')) return;
        document.body.insertAdjacentHTML('beforeend', `<div class="modal fade ugm t-blue" id="gdhSubModal" data-bs-backdrop="static" tabindex="-1"><div class="modal-dialog modal-lg modal-dialog-scrollable modal-dialog-centered"><div class="modal-content">
  <div class="modal-header ugm-head">
    <div class="ugm-ico"><i class="bi bi-send-check-fill"></i></div>
    <div><div class="ugm-ttl">Đẩy đơn cho admin duyệt <span class="badge bg-light ms-1 d-none" id="gdhSubTag" style="color:inherit"></span></div><div class="ugm-sub" id="gdhSubSub">Đang kiểm tra đơn...</div></div>
    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Đóng"></button>
  </div>
  <div class="ugm-steps" id="gdhSubSteps"><span data-s="1" class="on"><b>1</b>Kiểm tra đơn</span><i class="sep"></i><span data-s="2"><b>2</b>Xác nhận</span><i class="sep"></i><span data-s="3"><b>3</b>Gửi admin</span></div>
  <div class="modal-body p-3">
    <div id="gdhSubLoad" class="ugm-view"><div class="ugm-stats"><div class="ugm-stat"><small>&nbsp;</small><strong>&nbsp;</strong></div><div class="ugm-stat"><small>&nbsp;</small><strong>&nbsp;</strong></div><div class="ugm-stat"><small>&nbsp;</small><strong>&nbsp;</strong></div></div>
      <div class="ugm-box"><table class="table mb-0"><tbody class="ugm-skel"><tr><td><div></div></td></tr><tr><td><div></div></td></tr><tr><td><div></div></td></tr></tbody></table></div></div>
    <div id="gdhSubReview" class="ugm-view d-none"></div>
    <div id="gdhSubConfirm" class="ugm-confirm ugm-view d-none"></div>
    <div id="gdhSubDone" class="ugm-done ugm-view d-none"></div>
  </div>
  <div class="modal-footer py-2" id="gdhSubFootLoad"><button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">Đóng</button></div>
  <div class="modal-footer py-2 d-none" id="gdhSubFootReview"><button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">Huỷ</button>
    <button type="button" class="btn btn-sm px-3 ugm-go" id="gdhSubNext" onclick="_gdhSubAsk()">Tiếp tục <i class="bi bi-arrow-right ms-1"></i></button></div>
  <div class="modal-footer py-2 d-none" id="gdhSubFootConfirm"><button type="button" class="btn btn-outline-secondary btn-sm" id="gdhSubBack" onclick="_gdhSubView('review')"><i class="bi bi-arrow-left me-1"></i>Quay lại</button>
    <button type="button" class="btn btn-sm px-3 ugm-go" id="gdhSubGo" onclick="gdhSubmitConfirm()"><i class="bi bi-send-fill me-1"></i>Xác nhận đẩy đơn</button></div>
  <div class="modal-footer py-2 d-none" id="gdhSubFootDone"><button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">Đóng</button>
    <button type="button" class="btn btn-success btn-sm fw-bold px-3" onclick="_gdhSubToOrders()"><i class="bi bi-list-check me-1"></i>Xem ở Đơn đã đẩy</button></div>
</div></div></div>`);
        _GDH_SUB_SKEL = _gdhSubEl('Load').innerHTML;
    }
    function _gdhSubTheme(t) { const m = _gdhSubEl('Modal'); m.classList.remove('t-blue', 't-violet'); if (t) m.classList.add(t); }
    function _gdhSubView(v) {          // load | review | confirm | done
        const map = {load: 'Load', review: 'Review', confirm: 'Confirm', done: 'Done'};
        Object.keys(map).forEach(k => {
            _gdhSubEl(map[k]).classList.toggle('d-none', k !== v);
            _gdhSubEl('Foot' + map[k]).classList.toggle('d-none', k !== v);
        });
        _ugmSetStep(_gdhSubEl('Steps'), v === 'confirm' ? 2 : v === 'done' ? 4 : 1);
    }
    async function _gdhSubOpen() {
        _gdhSubEnsure();
        _gdhSub = {pv: null, busy: false};
        _gdhSubTheme('t-blue');
        if (!_gdhSubEl('Load').querySelector('.ugm-skel')) _gdhSubEl('Load').innerHTML = _GDH_SUB_SKEL;
        _gdhSubEl('Tag').classList.add('d-none'); _gdhSubEl('Sub').textContent = 'Đang kiểm tra đơn...';
        _gdhSubView('load');
        bootstrap.Modal.getOrCreateInstance(_gdhSubEl('Modal')).show();
        try {
            const pv = await _gdhJson('/api/gom-don-hang/submit-preview?batch_id=' + _gdhBatchId);
            _gdhSub.pv = pv; _gdhSubRender(pv);
        } catch (err) {
            _gdhSubEl('Load').innerHTML = `<div class="ugm-empty text-danger"><i class="bi bi-exclamation-triangle"></i>${_gdhEsc(err.message)}</div>`;
        }
    }
    function _gdhSubRender(pv) {
        const e = _gdhEsc, b = pv.batch;
        const types = ['Định kỳ', 'Khẩn', 'Đơn 26'], bt = pv.by_type || {};
        const total = pv.amount > 0 ? pv.amount : pv.qty;
        _gdhSub.sel = null;
        _gdhSubTheme('t-blue');
        _gdhSubEl('Sub').textContent = 'Chọn loại đơn cần đẩy · ' + (b.store || '');
        _gdhSubEl('Tag').classList.add('d-none');
        const rows = types.map(t => {
            const v = bt[t] || {parts: 0, qty: 0, amount: 0}, val = pv.amount > 0 ? v.amount : v.qty, pct = total > 0 ? Math.round(val * 100 / total) : 0;
            return `<label class="ugm-type ${v.parts ? '' : 'zero'}" style="${v.parts ? 'cursor:pointer' : 'cursor:not-allowed'}"><span class="nm"><input type="radio" class="form-check-input me-2" name="gdhSubType" value="${e(t)}" ${v.parts ? '' : 'disabled'} onchange="_gdhSubPick(this.value)"><span class="dot" style="background:${_GDH_SUB_COLOR[t]}"></span>${e(t)}</span><span class="n">${_gdhFmt(v.parts, 0)} mã</span><span class="n">SL ${_gdhFmt(v.qty, 0)}</span>` +
                `<span><span class="n d-block fw-semibold">${_gdhFmt(v.amount, 0)} đ</span><div class="ugm-bar"><i data-w="${pct}" style="background:${_GDH_SUB_COLOR[t]}"></i></div></span></label>`;
        }).join('');
        const W = (cls, ico, html) => `<div class="ugm-warn ${cls}"><i class="bi ${ico}"></i><div class="flex-grow-1">${html}</div></div>`;
        let warn = '';
        if (pv.unassigned_type > 0) warn += W('m', 'bi-info-circle', `Còn <b>${pv.unassigned_type}</b> mã SL cuối &gt; 0 <b>chưa chọn loại đơn</b>: các mã này ở lại phiên gôm, không được gửi.`);
        if (pv.no_cost > 0) warn += W('m', 'bi-info-circle', `${pv.no_cost} mã chưa có giá nhập nên chưa tính vào giá trị.`);
        _gdhSubEl('Review').innerHTML = `<div class="small text-muted mb-2">Chọn <b>1 loại đơn</b> để đẩy cho admin. Chỉ các mã loại đó (SL cuối &gt; 0) được gửi và bị <b>xoá khỏi phiên gôm</b>; mã các loại khác vẫn ở lại để đẩy sau.</div>` +
            `<div class="ugm-box"><div class="ugm-type head"><span>Loại đơn</span><span class="n">Số mã</span><span class="n">Tổng SL</span><span class="n">Giá trị</span></div>${rows}</div>` +
            `<div id="gdhSubTypeWarn"></div>` + warn +
            `<label class="form-label small fw-semibold mb-1">Ghi chú cho admin <span class="text-muted fw-normal">(không bắt buộc)</span></label><textarea class="form-control form-control-sm" id="gdhSubNote" rows="2" maxlength="500" placeholder="Ví dụ: cần hàng gấp trước thứ Sáu..."></textarea>`;
        _gdhSubEl('Next').disabled = true;
        _gdhSubView('review');
        requestAnimationFrame(() => requestAnimationFrame(() => _gdhSubEl('Review').querySelectorAll('.ugm-bar i').forEach(i => i.style.width = i.dataset.w + '%')));
        const avail = types.filter(t => (bt[t] || {}).parts > 0);
        if (avail.length === 1) {                // chỉ có 1 loại đơn: chọn sẵn
            const r = _gdhSubEl('Review').querySelector(`input[name="gdhSubType"][value="${avail[0]}"]`);
            if (r) { r.checked = true; _gdhSubPick(avail[0]); }
        }
    }
    function _gdhSubPick(t) {           // chọn loại đơn cần đẩy: đổi màu, hiện cảnh báo trùng của RIÊNG loại đó
        const S = _gdhSub, pv = S && S.pv; if (!pv) return;
        const e = _gdhEsc, dups = (pv.dups_by_type || {})[t] || [], blocking = !!(pv.blocking_by_type || {})[t], nonBlock = dups.length > 0 && !blocking;
        S.sel = t;
        _gdhSubTheme(_GDH_SUB_THEME[t] === undefined ? 't-blue' : _GDH_SUB_THEME[t]);
        _gdhSubEl('Sub').textContent = (pv.order_names || {})[t] || ('Đơn hàng ' + pv.batch.store);
        const tag = _gdhSubEl('Tag'); tag.textContent = t; tag.classList.remove('d-none');
        const W = (cls, ico, html) => `<div class="ugm-warn ${cls}"><i class="bi ${ico}"></i><div class="flex-grow-1">${html}</div></div>`;
        let warn = '';
        if (dups.length) warn += W(blocking ? 'd' : 'w', 'bi-copy', (blocking ? `<b>Đã có đơn ${e(t)} đang chờ admin xử lý</b> - hãy chờ admin xử lý xong hoặc Thu hồi đơn đó ở tab Đơn đã đẩy trước khi đẩy thêm.` : `<b>Đã có đơn ${e(t)} được đẩy trước đó cho cùng kỳ.</b> Đẩy thêm có thể làm trùng hàng.`) +
            `<ul>${dups.map(d => `<li>Đợt ${d.id} · ${e(d.status_label)} · đẩy ${e(d.submitted_at)} bởi ${e(d.submitted_by || '')}</li>`).join('')}</ul>` +
            (nonBlock ? `<div class="form-check mt-2"><input class="form-check-input" type="checkbox" id="gdhSubDup" onchange="document.getElementById('gdhSubNext').disabled = !this.checked"><label class="form-check-label fw-semibold" for="gdhSubDup">Tôi vẫn muốn đẩy thêm một đơn ${e(t)} cho cùng kỳ</label></div>` : ''));
        _gdhSubEl('TypeWarn').innerHTML = warn;
        _gdhSubEl('Next').disabled = blocking || nonBlock;
    }
    function _gdhSubAsk() {
        const S = _gdhSub, pv = S && S.pv; if (!pv || !S.sel) return;
        const e = _gdhEsc, t = S.sel, v = (pv.by_type || {})[t] || {parts: 0, qty: 0, amount: 0}, note = (_gdhSubEl('Note').value || '').trim();
        const others = ['Định kỳ', 'Khẩn', 'Đơn 26'].filter(x => x !== t && ((pv.by_type || {})[x] || {}).parts > 0);
        _gdhSubEl('Confirm').innerHTML = `<div class="big"><i class="bi bi-send-exclamation-fill"></i></div>` +
            `<h5 class="fw-bold mb-2">Đẩy đơn <span class="hl">${e(t)}</span>: <span class="hl">${_gdhFmt(v.parts, 0)}</span> mã (SL ${_gdhFmt(v.qty, 0)} · ${_gdhFmt(v.amount, 0)} đ)<br>cho admin duyệt?</h5>` +
            `<div class="small">${note ? 'Ghi chú cho admin: “' + e(note) + '”' : '<span class="text-muted">Không có ghi chú cho admin.</span>'}</div>` +
            `<div class="small mt-1">Các mã này sẽ bị <b>xoá khỏi phiên gôm</b>. ${others.length ? 'Mã ' + e(others.join(', ')) + ' vẫn giữ nguyên trong phiên gôm.' : 'Phiên gôm vẫn được giữ lại.'}</div>` +
            `<div class="ugm-lock"><i class="bi bi-lock-fill me-1"></i>Đơn ${e(t)} sau khi đẩy bị khoá; chỉ thu hồi được khi admin chưa lấy về duyệt (thu hồi sẽ trả các mã về phiên gôm).</div>` +
            `<div class="alert alert-danger py-2 mt-3 mb-0 d-none text-start small" id="gdhSubErr"></div>`;
        _gdhSubView('confirm');
    }
    async function gdhSubmitConfirm() {
        const S = _gdhSub; if (!S || S.busy || !S.sel) return;
        S.busy = true;
        const btn = _gdhSubEl('Go'), back = _gdhSubEl('Back'), err = _gdhSubEl('Err');
        btn.disabled = back.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Đang đẩy đơn...';
        if (err) err.classList.add('d-none');
        const note = (_gdhSubEl('Note')?.value || '').trim();
        const dup = !!_gdhSubEl('Dup')?.checked;
        let j;
        try {
            j = await _gdhJson('/api/gom-don-hang/submit', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: _gdhBatchId, order_type: S.sel, note, confirm_duplicate: dup})});
        } catch (ex) {
            if (err) { err.textContent = ex.message; err.classList.remove('d-none'); }
            S.busy = false; btn.disabled = back.disabled = false; btn.innerHTML = '<i class="bi bi-send-fill me-1"></i>Xác nhận đẩy đơn';
            return;
        }
        S.busy = false; btn.disabled = back.disabled = false; btn.innerHTML = '<i class="bi bi-send-fill me-1"></i>Xác nhận đẩy đơn';
        const e = _gdhEsc, rem = j.remaining || {}, left = Object.keys(rem).filter(k => rem[k] > 0);
        _gdhSubEl('Done').innerHTML = `<svg class="ugm-check" viewBox="0 0 52 52"><circle cx="26" cy="26" r="25"/><path d="M14 27l8 8 16-17"/></svg>` +
            `<h5 class="fw-bold mb-1">Đã đẩy đơn ${e(j.order_type)} cho admin</h5><div class="text-muted">Đơn đang ở trạng thái <b>Chờ duyệt</b> và đã được khoá.</div>` +
            `<div class="ugm-pills"><span class="ugm-pill">${_gdhFmt(j.to_order, 0)} mã</span><span class="ugm-pill">SL ${_gdhFmt(j.qty, 0)}</span><span class="ugm-pill">${_gdhFmt(j.amount, 0)} đ</span></div>` +
            `<div class="small mt-3">Các mã ${e(j.order_type)} đã được xoá khỏi phiên gôm.` + (left.length ? ' Phiên gôm còn: ' + left.map(k => `<b>${e(k)}</b> (${rem[k]} mã)`).join(', ') + '.' : '') + `</div>` +
            `<div class="small text-muted mt-1">Theo dõi tiến độ duyệt ở tab <b>Đơn đã đẩy</b>.</div>`;
        _gdhSubView('done');
        _gdhAfterAct();            // phiên gôm vẫn mở được để sửa / đẩy loại khác: chỉ tải lại bảng, KHÔNG khoá
    }
    function _gdhSubToOrders() {
        bootstrap.Modal.getOrCreateInstance(_gdhSubEl('Modal')).hide();
        gdhSwitchView('orders');
    }
    async function gdhRecall(id) {
        const _o = id ? (_gdhOrdersCache || []).find(x => String(x.id) === String(id)) : null;
        if (_o && _o.kind === 'urgent') {          // đơn khẩn từ danh sách khách hàng: không có bản Nháp, thu hồi = xoá đơn
            if (!await nsConfirm('Thu hồi đơn KHẨN? Đơn sẽ bị xoá và các mã khách được trả về Danh sách đặt hàng để gôm lại. (Chỉ được khi admin chưa lấy về duyệt)')) return;
            if (await _gdhAct('recall', {batch_id: id}, 'Đã thu hồi đơn khẩn. Các mã khách đã về lại Danh sách đặt hàng.')) await _gdhAfterAct();
            return;
        }
        if (!await nsConfirm('Thu hồi đơn để sửa lại? Các mã sẽ được trả về phiên gôm (chỉ được khi admin chưa lấy về duyệt).')) return;
        if (await _gdhAct('recall', id ? {batch_id: id} : {}, id ? 'Đã thu hồi đơn. Các mã được trả về phiên gôm ở tab Gôm đơn hàng để bạn sửa.' : '')) await _gdhAfterAct();
    }
    async function gdhClaim(id) {          // từ bảng gôm hoặc từ danh sách: lấy về duyệt rồi mở thẳng đơn đó để sửa
        if (id) _gdhBatchId = id;
        if (!(await _gdhAct('claim'))) { _gdhAfterAct(); return; }
        const store = _gdhBatchMeta && String(_gdhBatchMeta.id) === String(id) ? _gdhBatchMeta.store : (_gdhOrdersCache.find(o => String(o.id) === String(id)) || {}).store;
        gdhOpenBatch(store, id);
        gdhOrdersBadge();
    }
    async function gdhRelease(id) {
        if (!await nsConfirm('Trả đơn về hàng chờ duyệt? Các chỉnh sửa bạn đã làm vẫn được giữ trong đơn.')) return;
        if (id) _gdhBatchId = id;
        if (_gdhDirty && !(await gdhSave(true))) return;
        if (await _gdhAct('release')) _gdhGoOrders();
    }
    async function _gdhAskReject() {             // hỏi lý do từ chối (bắt buộc); trả null nếu admin huỷ hoặc bỏ trống
        const r = await nsPrompt('Từ chối duyệt đơn này và trả về chi nhánh chỉnh sửa?\nNhập lý do từ chối (bắt buộc) - chi nhánh sẽ thấy lý do này:', '');
        if (r === null) return null;
        const t = r.trim();
        if (!t) { alert('Cần nhập lý do từ chối để chi nhánh biết cần chỉnh sửa gì.'); return null; }
        return t.slice(0, 500);
    }
    async function gdhReject(id, btn) {    // từ danh sách đơn: Từ chối đơn Chờ duyệt / Đang duyệt (của mình)
        const reason = await _gdhAskReject(); if (reason === null) return;
        _gdhBusy(btn, true, 'Đang từ chối...');
        try {
            const j = await _gdhJson('/api/gom-don-hang/reject', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: id, reason})});
            alert(j.urgent ? 'Đã từ chối đơn khẩn. Các dòng được trả về danh sách khách hàng của chi nhánh.' : 'Đã từ chối đơn. Đơn được trả về chi nhánh (Nháp) kèm lý do để chỉnh sửa và đẩy lại.');
        } catch (e) { alert(e.message); }
        finally { _gdhBusy(btn, false); }
        gdhOrdersLoad(); gdhOrdersBadge();
    }
    async function gdhDownloadOrder(id) {
        id = id || _gdhBatchId;
        if (!id) return;
        await _gdhDownload('/api/gom-don-hang/export?kind=hvn&batch_id=' + id);       // server tự chuyển sang "Đã đặt" khi tải thành công
        await _gdhAfterAct();
        const m = document.getElementById('gdhCmpModal');
        if (m && m.classList.contains('show') && _gdhCmp && String(_gdhCmp.batch.id) === String(id)) gdhOpenCompare(id, true);
    }
    async function gdhOpenBatch(store, id) {     // mở 1 đơn trong tab Gôm đơn hàng (admin: sang không gian của chi nhánh)
        if (_gdhDirty && !(await _gdhConfirmLeave())) return;
        _gdhBatchId = id; _gdhEdited = {}; _gdhMarkDirty(false);
        if (CURRENT_ROLE === 'admin') {
            _gdhSpaceVal = 'store';
            ['gdhSpace', 'gdhDSpace'].forEach(x => { const el = document.getElementById(x); if (el) el.value = 'store'; });
            if (store) document.getElementById('gdhStore').value = store;
        }
        gdhSwitchView('gom');
    }

    // ---------- Danh sách đơn đã đẩy / lưu trữ ----------
    let _gdhOrdersCache = [];
    async function gdhOrdersBadge() {      // admin: số đơn Chờ duyệt; cửa hàng: số đơn đã duyệt nhưng chưa tải về
        const el = document.getElementById('gdhOBadge'); if (!el) return;
        try {
            const j = await _gdhJson('/api/gom-don-hang/orders?counts_only=1');
            let n = CURRENT_ROLE === 'admin' ? (j.counts.pending || 0) : ((j.counts.approved || 0) + (j.counts.viewed || 0)), rej = 0;
            if (CURRENT_ROLE !== 'admin') { try { rej = (await _gdhRejFetch('', true)).open || 0; } catch (e2) { rej = 0; } n += rej; }
            el.textContent = n; el.classList.toggle('d-none', !n);
            el.title = CURRENT_ROLE === 'admin' ? n + ' đơn đang chờ duyệt' : (n - rej) + ' đơn đã duyệt, chưa tải về' + (rej ? ' · ' + rej + ' đơn bị từ chối cần sửa' : '');
        } catch (e) { /* không chặn giao diện nếu lỗi */ }
    }
    // ---- User chi nhánh: các tab Đang xử lý / Đã duyệt / Lưu trữ / File Excel (giống màn duyệt của admin) ----
    let _gdhUTabCur = 'active';
    const _GDH_U_STATUS = {active: 'active', done: 'approved,viewed', archive: 'ordered'};
    function gdhUTab(t) { _gdhUTabCur = t; gdhOrdersLoad(); }
    function _gdhUApplyTabs(isFiles) {
        document.querySelectorAll('#gdhUTabs [data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === _gdhUTabCur));
        document.getElementById('gdhOListWrap')?.classList.toggle('d-none', isFiles);
        document.getElementById('gdhUArcPanel')?.classList.toggle('d-none', !isFiles);
        document.getElementById('gdhONote')?.classList.toggle('d-none', isFiles);
        ['gdhOType', 'gdhOFrom', 'gdhOTo'].forEach(id => { const el = document.getElementById(id); if (el) { el.classList.toggle('d-none', isFiles); if (el.previousElementSibling?.tagName === 'LABEL') el.previousElementSibling.classList.toggle('d-none', isFiles); } });
    }
    function _gdhUSetBadge(tab, n) { const b = document.querySelector(`#gdhUTabs [data-tab="${tab}"] .n`); if (b) b.textContent = n; }
    async function gdhUArcLoad() {
        const body = document.getElementById('gdhUArcBody'); if (!body) return;
        const p = new URLSearchParams(); const t = document.getElementById('gdhUArcType')?.value; if (t) p.set('type', t);
        try {
            const j = await _gdhJson('/api/gom-don-hang/archives?' + p), e = _gdhEsc;
            _gdhUSetBadge('files', j.total);
            document.querySelectorAll('.gdhArcDaysTxt').forEach(x => x.textContent = j.days);
            document.getElementById('gdhUArcInfo').textContent = `${j.live} đơn đang chờ tới hạn (${j.due} đơn đã quá hạn, sẽ được lưu trữ ở lần chạy tới) · ${j.total} file đã lưu`;
            body.innerHTML = j.data.length ? j.data.map(r => `<tr><td><div class="fw-semibold">${e(r.type)}</div><div class="gdh-sub">${e(r.filename)}</div></td>` +
                `<td>${e(r.name)}<div class="gdh-sub">Kỳ ${_gdhDate(r.from)} - ${_gdhDate(r.to)} · ${e(r.status_label)}</div></td>` +
                `<td class="text-end">${_gdhFmt(r.parts, 0)}<div class="gdh-sub">SL ${_gdhFmt(r.qty, 0)}</div></td><td>${e(r.archived_at)}</td>` +
                `<td>${r.downloaded_at ? `<span class="text-success small"><i class="bi bi-check2"></i> ${e(r.downloaded_at)}</span>` : '<span class="badge bg-warning text-dark">Chưa tải</span>'}</td>` +
                `<td class="text-end">${_gdhBtn('btn-outline-secondary', 'bi-download', 'Tải', `gdhUArcOne(${r.id})`)}</td></tr>`).join('')
                : '<tr><td colspan="6" class="text-center text-muted py-3">Chưa có file lưu trữ nào.</td></tr>';
        } catch (err) { body.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-3">${_gdhEsc(err.message)}</td></tr>`; }
    }
    async function gdhUArcOne(id) { await _gdhDownload('/api/gom-don-hang/archives/' + id + '/file?mark=1'); gdhUArcLoad(); }
    // Tự tải về máy khi mở trang - mỗi bên tự tải loại đơn của mình (file lưu trữ ghi trạng thái lúc đơn bị lưu trữ + xoá):
    //   • Admin        -> file của đơn ĐÃ DUYỆT (gồm Đã xem: chi nhánh đã xem kết quả duyệt nhưng chưa đặt)
    //   • User chi nhánh -> file của đơn ĐÃ ĐẶT
    // Gom thành 1 file ZIP (<chi nhánh>/<loại đơn>/<file>.xlsx; user chi nhánh chỉ có chi nhánh mình) nên trình duyệt không hỏi "cho phép tải nhiều file".
    // File thuộc loại còn lại vẫn xem/tải thủ công được ở tab "File Excel". Admin đang "mượn quyền" cửa hàng thì bỏ qua,
    // tránh đánh dấu nhầm là chi nhánh đã tải.
    async function gdhUArcAutoDownload() {
        if (window._gdhUAutoDone || GDH_IS_IMPERSONATING || !['store', 'admin'].includes(CURRENT_ROLE)) return;
        window._gdhUAutoDone = true;
        const isAdmin = CURRENT_ROLE === 'admin', st = isAdmin ? 'approved,viewed' : 'ordered';
        try {
            const j = await _gdhJson('/api/gom-don-hang/archives?only_new=1&status=' + st);
            if (!isAdmin) _gdhUSetBadge('files', j.total);
            if (!j.data.length) return;
            await _gdhDownload('/api/gom-don-hang/archives/zip?only_new=1&status=' + st);
            _ocToastSafe(`Đã tự động tải về máy ${j.data.length} file Excel đơn ${isAdmin ? 'đã duyệt' : 'đã đặt'} quá ${j.days} ngày (đã lưu trữ + xoá khỏi hệ thống).`);
            if (!isAdmin) gdhUArcLoad(); else if (typeof ocArcLoad === 'function') ocArcLoad();
        } catch (e) { window._gdhUAutoDone = false; /* lỗi mạng: thử lại lần mở sau */ }
    }
    document.addEventListener('DOMContentLoaded', () => { setTimeout(gdhUArcAutoDownload, 2500); });   // tự tải ngay khi mở trang, không cần vào tab Gôm đơn
    function _ocToastSafe(msg) { try { if (typeof _ocToast === 'function') { _ocToast(msg); return; } } catch (e) { /* bỏ qua */ } console.info(msg); }
    async function gdhUArcZip() {
        const p = new URLSearchParams(); const t = document.getElementById('gdhUArcType')?.value; if (t) p.set('type', t);
        await _gdhDownload('/api/gom-don-hang/archives/zip?' + p);
    }
    // ---- Đơn bị admin từ chối: lịch sử cho admin (mọi chi nhánh) và cho chi nhánh (của mình) ----
    const _GDH_REJ_STATE = {waiting: ['bg-warning text-dark', 'Chờ chi nhánh sửa'], resubmitted: ['bg-info text-dark', 'Đã đẩy lại'], deleted: ['bg-secondary', 'Đã xoá']};
    const _gdhRejStateBadge = st => { const x = _GDH_REJ_STATE[st]; return x ? `<span class="badge ${x[0]}">${x[1]}</span>` : ''; };
    async function _gdhRejFetch(store, countsOnly) {
        const p = new URLSearchParams(); if (store) p.set('store', store); if (countsOnly) p.set('counts_only', '1');
        return _gdhJson('/api/gom-don-hang/rejections?' + p);
    }
    async function _gdhRejOpenBadge() {      // huy hiệu tab "Bị từ chối" của chi nhánh = số đơn đang chờ sửa
        if (CURRENT_ROLE === 'admin') return;
        try { _gdhUSetBadge('rejected', (await _gdhRejFetch('', true)).open || 0); } catch (e) { /* không chặn giao diện */ }
    }
    function _gdhOHeadMode(rej) {            // đổi tiêu đề cột 4 của bảng danh sách đơn: "Admin duyệt" <-> "Admin từ chối"
        const th = document.getElementById('gdhOBody')?.closest('table')?.querySelectorAll('thead th')[3];
        if (th) th.textContent = rej ? 'Admin từ chối · lý do' : 'Admin duyệt';
    }
    async function gdhRejListLoad() {
        const isAdmin = CURRENT_ROLE === 'admin', body = document.getElementById('gdhOBody'), e = _gdhEsc;
        body.closest('table')?.classList.remove('ns-cards');
        const resBox = document.getElementById('gdhOResult'); if (resBox) resBox.innerHTML = '';
        try {
            const j = await _gdhRejFetch(isAdmin ? (document.getElementById('gdhOStore')?.value || '') : '');
            if (!isAdmin) _gdhUSetBadge('rejected', j.open || 0);
            document.getElementById('gdhOCounts').innerHTML = `Chờ chi nhánh sửa: <b>${j.open || 0}</b> · Tổng số lần từ chối: <b>${j.data.length}</b>`;
            body.innerHTML = j.data.length ? j.data.map(r => {
                const acts = (r.waiting && r.batch_id) ? _gdhBtn('btn-outline-primary', isAdmin ? 'bi-eye' : 'bi-pencil-square', isAdmin ? 'Xem đơn' : 'Mở để sửa', `gdhOpenBatch('${e(r.store)}', ${r.batch_id})`) : '';
                return `<tr><td class="fw-semibold">${e(r.name)}${r.kind === 'urgent' ? ' <span class="badge bg-danger-subtle text-danger border border-danger-subtle">Từ danh sách KH</span>' : ''} <span class="gdh-sub fw-normal">#${r.batch_id || ''}${r.from ? ' · Kỳ ' + _gdhDate(r.from) + ' - ' + _gdhDate(r.to) : ''}</span></td>` +
                    `<td><span class="badge bg-danger">Bị từ chối</span> ${_gdhRejStateBadge(r.state)}</td>` +
                    `<td>${e(r.submitted_by)}<div class="gdh-sub">${e(r.submitted_at)}</div></td>` +
                    `<td>${e(r.rejected_by)}<div class="gdh-sub">${e(r.rejected_at)}</div><div class="small text-danger text-wrap" style="min-width:200px;max-width:380px">${e(r.reason)}</div></td>` +
                    `<td class="text-end">${r.parts != null ? _gdhFmt(r.parts, 0) + ' mã · SL ' + _gdhFmt(r.qty, 0) : '<span class="text-muted">–</span>'}</td>` +
                    `<td><div class="d-flex flex-wrap gap-1">${acts}</div></td></tr>`;
            }).join('') : '<tr><td colspan="6" class="text-center text-muted py-3">Chưa có đơn nào bị từ chối.</td></tr>';
        } catch (err) { body.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-3">${e(err.message)}</td></tr>`; }
    }
    async function gdhOrdersLoad() {
        const g = id => document.getElementById(id)?.value || '';
        const isStoreUser = CURRENT_ROLE !== 'admin';
        if (isStoreUser) {
            const files = _gdhUTabCur === 'files';
            _gdhUApplyTabs(files);
            if (files) { gdhUArcLoad(); gdhOrdersBadge(); return; }
        }
        const rejMode = isStoreUser ? _gdhUTabCur === 'rejected' : g('gdhOStatus') === 'rejected';
        _gdhOHeadMode(rejMode);
        if (rejMode) { await gdhRejListLoad(); gdhOrdersBadge(); return; }
        const p = new URLSearchParams({status: isStoreUser ? (_GDH_U_STATUS[_gdhUTabCur] || 'active') : g('gdhOStatus'), from: g('gdhOFrom'), to: g('gdhOTo')});
        if (CURRENT_ROLE === 'admin' && g('gdhOStore')) p.set('store', g('gdhOStore'));
        const body = document.getElementById('gdhOBody');
        try {
            const j = await _gdhJson('/api/gom-don-hang/orders?' + p);
            const tf = g('gdhOType');
            if (tf) j.data = j.data.filter(o => ((o.by_type || {})[tf] || {}).parts > 0);          // lọc theo loại đơn (lưu trữ phân theo loại)
            _gdhOrdersCache = j.data;
            const L = j.status_labels, c = j.counts;
            const _ad = document.getElementById('gdhArcDays'); if (_ad && j.archive_days) _ad.textContent = j.archive_days;
            if (isStoreUser) {
                _gdhUSetBadge('active', (c.pending || 0) + (c.reviewing || 0)); _gdhUSetBadge('done', (c.approved || 0) + (c.viewed || 0));
                _gdhUSetBadge('archive', c.ordered || 0); _gdhUSetBadge('files', j.archive_files || 0);
                _gdhRejOpenBadge();
                document.querySelectorAll('.gdhArcDaysTxt').forEach(x => { if (j.archive_days) x.textContent = j.archive_days; });
            }
            document.getElementById('gdhOCounts').innerHTML = ['pending', 'reviewing', 'approved', 'viewed', 'ordered'].map(k => `${_gdhEsc(L[k])}: <b>${c[k] || 0}</b>`).join(' · ');
            const sum = s => s ? `${_gdhFmt(s.parts, 0)} mã · SL ${_gdhFmt(s.qty, 0)}<div class="gdh-sub">${_gdhFmt(s.amount, 0)} đ</div>` : '<span class="text-muted">–</span>';
            const isAdmin = CURRENT_ROLE === 'admin';
            const resBox = document.getElementById('gdhOResult');          // thẻ kết quả: đơn đã duyệt xong, chờ chi nhánh tải về
            if (resBox) resBox.innerHTML = '';  // thẻ kết quả đã nằm trong danh sách bên dưới
            const _tbl = body.closest('table'); if (_tbl) _tbl.classList.add('ns-cards');
            body.innerHTML = j.data.length ? j.data.map(o => `<tr><td colspan="6" class="ns-cell">${_nsOrderCardHtml(o, 'list')}</td></tr>`).join('')
                : '<tr><td colspan="6" class="text-center text-muted py-3">Không có đơn nào khớp bộ lọc.</td></tr>';
        } catch (e) { body.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-3">${_gdhEsc(e.message)}</td></tr>`; }
        gdhOrdersBadge();
    }

    // ---------- Kết quả duyệt: STT, mã, tên, SL gửi, SL duyệt, ghi chú ----------
    let _gdhCmp = null, _gdhCmpOnlyChg = true;
    function _gdhEnsureCmpModal() {
        if (document.getElementById('gdhCmpModal')) return;
        document.body.insertAdjacentHTML('beforeend', `<div class="modal fade" id="gdhCmpModal" tabindex="-1"><div class="modal-dialog modal-xl modal-dialog-scrollable"><div class="modal-content">
            <div class="modal-header"><h5 class="modal-title" id="gdhCmpTitle"></h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
            <div class="modal-body" id="gdhCmpBody"></div><div class="modal-footer" id="gdhCmpFoot"></div></div></div></div>`);
    }
    async function gdhOpenCompare(id, silentRefresh) {
        id = id || _gdhBatchId;
        if (!id) return;
        try {
            const j = await _gdhJson('/api/gom-don-hang/compare?batch_id=' + id);
            _gdhCmp = j;
            _gdhEnsureCmpModal();
            _gdhCmpRender();
            if (!silentRefresh) bootstrap.Modal.getOrCreateInstance(document.getElementById('gdhCmpModal')).show();
            if (CURRENT_ROLE === 'store' && j.batch.status === 'approved') {       // cửa hàng đã mở xem -> Đã xem
                const r = await _gdhJson('/api/gom-don-hang/mark-viewed', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: id})});
                if (r.changed) { _gdhCmp.batch.status = 'viewed'; _gdhCmp.batch.status_label = 'Đã xem'; _gdhCmpRender(); _gdhAfterAct(); }
            }
        } catch (e) { alert(e.message); }
    }
    function gdhCmpToggle(on) { _gdhCmpOnlyChg = on; _gdhCmpRender(); }
    function _gdhTimeline(b) {                 // Đẩy -> Duyệt -> Xem -> Đặt, kèm thời điểm
        const st = [['Đẩy', b.submitted_at, b.submitted_by], ['Duyệt', b.approved_at, b.approved_by], ['Xem', b.viewed_at, b.viewed_by], ['Đặt', b.ordered_at, b.ordered_by]];
        return '<div class="d-flex flex-wrap align-items-center gap-1 mb-2 small">' + st.map((x, i) => {
            const on = !!x[1];
            return `<span class="badge ${on ? 'bg-success' : 'bg-light text-muted border'}">${x[0]}</span><span class="${on ? '' : 'text-muted'}">${on ? _gdhEsc(x[1]) : 'chưa'}</span>` + (i < 3 ? '<i class="bi bi-chevron-right text-muted mx-1"></i>' : '');
        }).join('') + '</div>';
    }
    function _gdhCmpRender() {                 // kết quả duyệt: STT, mã hàng, tên hàng, SL gửi, SL duyệt, ghi chú của admin
        const j = _gdhCmp, b = j.batch, e = _gdhEsc;
        const oc = (_gdhOrdersCache.concat(typeof _ocGdhCache !== 'undefined' ? _ocGdhCache : [])).find(x => x.id === b.id);
        document.getElementById('gdhCmpTitle').innerHTML = `${e(oc ? _gdhOrderName(oc) : 'Đơn ' + b.store)} ${_gdhStBadge(b.status, b.status_label)}`;
        const info = j.reviewed
            ? `<div class="small text-muted mb-2">Duyệt bởi <b>${e(b.approved_by)}</b> lúc ${e(b.approved_at)}${b.review_note ? ` · Ghi chú chung: <i>${e(b.review_note)}</i>` : ''}</div>`
            : `<div class="alert alert-info py-1 px-2 small">Đơn chưa duyệt xong: SL duyệt và ghi chú sẽ hiện sau khi admin duyệt xong.</div>`;
        const sentTotal = j.rows.reduce((s, r) => s + (r.sent_qty || 0), 0);
        const rows = j.rows.map(r => {
            const diff = r.approved_qty !== null && r.approved_qty !== r.sent_qty;
            return `<tr><td class="text-muted">${r.stt}</td><td><span class="badge ${_GDH_TYPE_CLS[r.order_type] || 'bg-secondary'}">${e(r.order_type || '')}</span></td><td class="fw-semibold">${e(r.part_code)}</td><td style="white-space:normal;min-width:220px">${e(r.part_name)}</td>` +
                `<td class="text-end">${_gdhFmt(r.sent_qty, 0)}</td>` +
                `<td class="text-end ${diff ? 'fw-bold text-warning-emphasis bg-warning-subtle' : ''}">${r.approved_qty === null ? '<span class="text-muted">–</span>' : _gdhFmt(r.approved_qty, 0)}</td>` +
                `<td style="white-space:normal;min-width:200px">${e(r.note)}</td></tr>`;
        }).join('');
        const table = `<div class="small mb-1">${j.rows.length} mã · tổng SL gửi ${_gdhFmt(sentTotal, 0)}</div><div class="table-responsive" style="max-height:60vh"><table class="table table-sm align-middle mb-2 text-nowrap"><thead class="table-light" style="position:sticky;top:0;z-index:2"><tr>` +
            `<th>STT</th><th>Loại đơn</th><th>Mã hàng</th><th>Tên hàng</th><th class="text-end">SL gửi</th><th class="text-end">SL duyệt</th><th>Ghi chú</th></tr></thead><tbody>` +
            (rows || '<tr><td colspan="7" class="text-center text-muted py-3">Đơn không có mã nào.</td></tr>') + `</tbody></table></div>`;
        document.getElementById('gdhCmpBody').innerHTML = `<style>#gdhCmpModal .modal-content{font-family:var(--ns-font-main);font-variant-numeric:tabular-nums slashed-zero;}` +
            `#gdhCmpModal table{font-size:.95rem;}#gdhCmpModal th{font-weight:700;letter-spacing:.02em;color:#334155;}#gdhCmpModal td{color:#0f172a;}</style>` + _gdhTimeline(b) + info + table;
        const canDl = CURRENT_ROLE === 'store' && ['approved', 'viewed', 'ordered'].includes(b.status);
        document.getElementById('gdhCmpFoot').innerHTML = `<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Đóng</button>` +
            (canDl ? `<button type="button" class="btn ${b.status === 'ordered' ? 'btn-outline-success' : 'btn-success'}" onclick="gdhDownloadOrder(${b.id})"><i class="bi bi-download me-1"></i>${b.status === 'ordered' ? 'Tải lại file đặt hàng' : 'Tải đơn về (chuyển Đã đặt)'}</button>` : '');
    }

    // ---------- Dashboard ----------
    function _gdhDashQuery() {
        const g = id => document.getElementById(id)?.value || '';
        const p = new URLSearchParams({from: g('gdhDFrom'), to: g('gdhDTo'), order_type: g('gdhDType')});
        if (CURRENT_ROLE === 'admin' && g('gdhDStore')) p.set('store', g('gdhDStore'));
        if (CURRENT_ROLE === 'admin') p.set('space', _gdhSpace());
        return p;
    }
    async function gdhDashLoad() {
        try {
            const j = await _gdhJson('/api/gom-don-hang/dashboard?' + _gdhDashQuery());
            const sel = j.order_type;
            document.getElementById('gdhDCards').innerHTML = j.by_type.map(t => `<div class="col-6 col-md-3"><div class="p-2 rounded-3 border ${sel === t.order_type ? 'border-primary bg-primary bg-opacity-10' : ''}" style="cursor:pointer" onclick="gdhDashPickType('${_gdhEsc(t.order_type)}')">
                <div class="small text-muted">${_gdhEsc(t.order_type)}</div><div class="fw-bold">${_gdhFmt(t.parts, 0)} mã · SL ${_gdhFmt(t.qty, 0)}</div><div class="small">${_gdhFmt(t.amount, 0)} đ</div></div></div>`).join('') +
                `<div class="col-6 col-md-3"><div class="p-2 rounded-3 border bg-light"><div class="small text-muted">Tổng ${sel ? '(' + _gdhEsc(sel) + ')' : ''}</div><div class="fw-bold">${_gdhFmt(j.totals.parts, 0)} mã · SL ${_gdhFmt(j.totals.qty, 0)}</div><div class="small">${_gdhFmt(j.totals.amount, 0)} đ</div></div></div>`;
            document.getElementById('gdhDBatches').innerHTML = j.batches.length
                ? 'Các đợt gôm được tính: ' + j.batches.map(b => `${_gdhEsc(b.store)} ${_gdhDate(b.from)}-${_gdhDate(b.to)} (${b.parts} mã)`).join(' · ')
                : 'Không có đợt gôm nào có kỳ số bán nằm trong khoảng ngày này.';
            document.getElementById('gdhDStoreBody').innerHTML = j.by_store.length ? j.by_store.map(s => `<tr><td class="fw-semibold">${_gdhEsc(s.store)}</td><td class="text-end">${_gdhFmt(s.parts, 0)}</td><td class="text-end">${_gdhFmt(s.qty, 0)}</td>` +
                j.order_types.map(t => `<td class="text-end">${_gdhFmt(s.types[t].qty, 0)} <span class="text-muted small">(${_gdhFmt(s.types[t].amount, 0)})</span></td>`).join('') +
                `<td class="text-end fw-bold">${_gdhFmt(s.amount, 0)}</td></tr>`).join('') : '<tr><td colspan="7" class="text-center text-muted py-3">Chưa có dữ liệu.</td></tr>';
            const multi = j.stores.length > 1;
            document.getElementById('gdhDPartsTitle').textContent = 'Các mã đã đặt' + (sel ? ' - ' + sel : '') + ' (' + j.parts.length + ')' + (j.parts_truncated ? ' - chỉ hiện các mã giá trị lớn nhất, xuất Excel để xem đủ' : '');
            document.getElementById('gdhDPartsHead').innerHTML = '<th>Loại đơn</th><th>Mã hàng</th><th>Tên hàng</th><th>ĐVT</th>' + (multi ? j.stores.map(s => `<th class="text-end">${_gdhEsc(s)}</th>`).join('') : '') + '<th class="text-end">Tổng SL</th><th class="text-end">Thành tiền</th>';
            document.getElementById('gdhDPartsBody').innerHTML = j.parts.length ? j.parts.map(p => `<tr><td>${_gdhEsc(p.order_type)}</td><td class="fw-semibold">${_gdhEsc(p.part_code)}</td><td>${_gdhEsc(p.part_name)}</td><td>${_gdhEsc(p.unit)}</td>` +
                (multi ? j.stores.map(s => `<td class="text-end">${_gdhFmt(p.stores[s] || 0, 0)}</td>`).join('') : '') +
                `<td class="text-end fw-bold">${_gdhFmt(p.qty, 0)}</td><td class="text-end">${p.no_cost ? '<span class="text-muted" title="Thiếu giá vốn">*</span> ' : ''}${_gdhFmt(p.amount, 0)}</td></tr>`).join('')
                : `<tr><td colspan="${6 + (multi ? j.stores.length : 0)}" class="text-center text-muted py-3">Chưa có mã nào đã đặt.</td></tr>`;
        } catch (e) { alert(e.message); }
    }
    function gdhDashPickType(t) {
        const el = document.getElementById('gdhDType');
        el.value = el.value === t ? '' : t; gdhDashLoad();
    }
    function gdhDashExport() { _gdhDownload('/api/gom-don-hang/dashboard/export?' + _gdhDashQuery()); }
    window.addEventListener('beforeunload', e => { if (_gdhDirty) { e.preventDefault(); e.returnValue = ''; } });

    // ------------------------------------------------------------------
    // GỢI Ý NHẬP HÀNG TỰ ĐỘNG + CÀI ĐẶT NGƯỠNG CẢNH BÁO HẾT HÀNG.
    // ------------------------------------------------------------------
    function _buildReorderQuery() {
        const store = document.getElementById('reorderStoreFilter')?.value || '';
        const buffer = document.getElementById('reorderBufferMonths')?.value || '2';
        const params = new URLSearchParams();
        if (store) params.set('store', store);
        params.set('buffer_months', buffer);
        return params.toString();
    }

    async function loadReorderSuggestions() {
        const tbody = document.getElementById('reorder-body');
        if (!tbody) return;
        try {
            const res = await fetch('/api/admin/reorder-suggestions?' + _buildReorderQuery());
            const json = await res.json();
            if (!json.success) {
                tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-danger">${json.error || 'Lỗi tải dữ liệu.'}</td></tr>`;
                return;
            }

            document.getElementById('reorderVisibleCount').innerText = `${json.total.toLocaleString()} dòng cần đặt thêm`;
            const metaInfo = document.getElementById('reorderMetaInfo');
            if (metaInfo) {
                metaInfo.innerHTML = `Chỉ hiện mã "Thường xuyên (TX)" hoặc "Trung bình (TB)" có tồn dưới <strong>${json.min_months} tháng</strong> bán` +
                    (json.store ? ` — đang xem gợi ý của kho <strong>${json.store}</strong>` : ' — tính riêng cho từng cửa hàng (không gộp toàn hệ thống)') +
                    ` (kỳ số liệu xuất bán: ${json.period_months} tháng gần nhất).`;
            }

            if (!json.data || json.data.length === 0) {
                tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-muted">Không có mã hàng nào cần đặt thêm.</td></tr>`;
                return;
            }

            // Số nguyên khi hiển thị trong bảng Gợi Ý Nhập Hàng (không hiện số lẻ),
            // dù dữ liệu gốc từ server có thể có phần thập phân - làm tròn CHỈ để
            // hiển thị, không đổi số liệu dùng để tính toán ở nơi khác.
            const fmtReorderQty = (v) => (v === null || v === undefined) ? '-' : Math.round(v).toLocaleString();

            const groupBadgeClass = { TX: 'bg-success', TB: 'bg-warning text-dark' };
            tbody.innerHTML = json.data.map(r => {
                let noteHtml = '';
                if (r.blocked_no_replacement) {
                    noteHtml = `<div class="small text-danger mt-1" style="white-space:normal; max-width:260px;"><i class="bi bi-exclamation-triangle-fill me-1"></i>Mã đang bị khoá đặt hàng, chưa có mã thay thế</div>`;
                } else if (r.superseded_from && r.superseded_from.length) {
                    // white-space:normal ghi đè text-nowrap của cả bảng CHỈ cho ô
                    // này, và giới hạn max-width + rút gọn danh sách (nếu dài) để
                    // cột "Mã Hàng" không bị kéo giãn bất thường khi 1 mã thay thế
                    // gộp từ nhiều mã gốc bị khoá (xem title để xem đủ danh sách).
                    const codes = r.superseded_from.map(escapeHtmlAttr);
                    const shown = codes.slice(0, 3).join(', ');
                    const extra = codes.length > 3 ? ` và ${codes.length - 3} mã khác` : '';
                    noteHtml = `<div class="small text-muted mt-1" style="white-space:normal; max-width:260px;" title="${codes.join(', ')}"><i class="bi bi-arrow-left-right me-1"></i>Thay cho mã bị khoá: ${shown}${extra}</div>`;
                }
                return `
                <tr>
                    <td><span class="badge bg-secondary">${escapeHtmlAttr(r.store_code || '')}</span></td>
                    <td><span class="part-code">${escapeHtmlAttr(r.part_code)}</span>${noteHtml}</td>
                    <td class="inv-name-col" title="${escapeHtmlAttr(r.part_name || '')}">${r.part_name || ''}</td>
                    <td class="text-muted small">${r.unit || ''}</td>
                    <td class="text-end">${fmtReorderQty(r.qty_on_hand)}</td>
                    <td class="text-end">${fmtReorderQty(r.avg_month)}</td>
                    <td class="text-end text-danger fw-semibold">${r.months_of_stock}</td>
                    <td><span class="badge ${groupBadgeClass[r.group] || 'bg-secondary'}">${r.group}</span></td>
                    <td class="text-end fw-bold text-primary">${fmtReorderQty(r.suggested_qty)}</td>
                </tr>
            `;
            }).join('');
        } catch (err) {
            console.error('Lỗi loadReorderSuggestions:', err);
            tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-danger">Lỗi kết nối máy chủ.</td></tr>`;
        }
    }

    function exportReorderSuggestions() {
        window.location.href = '/api/admin/reorder-suggestions/export?' + _buildReorderQuery();
    }

    async function loadLowStockSettings() {
        const input = document.getElementById('lowStockMinMonthsInput');
        if (!input) return;
        try {
            const res = await fetch('/api/admin/low-stock-settings');
            const json = await res.json();
            if (json.success) input.value = json.min_months;
        } catch (err) {
            console.error('Lỗi loadLowStockSettings:', err);
        }
    }

    async function saveLowStockSettings() {
        const input = document.getElementById('lowStockMinMonthsInput');
        const status = document.getElementById('lowStockSettingsStatus');
        const val = parseFloat(input?.value);
        if (!val || val <= 0) {
            alert('Vui lòng nhập ngưỡng hợp lệ (lớn hơn 0).');
            return;
        }
        if (status) status.innerText = 'Đang lưu...';
        try {
            const res = await fetch('/api/admin/low-stock-settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ min_months: val }),
            });
            const json = await res.json();
            if (!json.success) {
                if (status) status.innerText = '';
                alert(json.error || 'Lưu thất bại.');
                return;
            }
            if (status) status.innerText = `Đã lưu. (${json.new_alerts || 0} cảnh báo mới, ${json.resolved_alerts || 0} cảnh báo đã hết)`;
            loadReorderSuggestions();
        } catch (err) {
            if (status) status.innerText = '';
            console.error('Lỗi saveLowStockSettings:', err);
            alert('Lỗi kết nối máy chủ khi lưu.');
        }
    }

    // ------------------------------------------------------------------
    // BẢNG GIÁ BỘ ÁO XE HONDA (theo đời xe + màu, kèm ảnh + danh sách phụ
    // tùng) - admin nhập từ Excel, admin + cửa hàng đều tra cứu được.
    //
    // MENU 1 HÀNG: chỉ chọn Dòng xe (Future/Wave/Air Blade/...) - bấm 1 nút
    // là hiện LUÔN tất cả nhóm/đời của dòng đó, KHÔNG có hàng chọn tiếp
    // đời/kiểu. Dòng xe (family) và đời/dung tích (sub_model) ưu tiên lấy
    // từ bảng tra Mã xe -> Dòng xe (nhập riêng, xem body_kit_model_categories
    // trong body_kit.py); nhóm nào không khớp mã nào trong bảng tra đó thì
    // dùng giá trị suy luận tự động lúc import bảng giá (classify_group_label
    // trong body_kit_import.py). Tải 1 lần qua /api/body-kit/menu và cache
    // lại trong _bodyKitMenuCache. Gõ vào ô tìm kiếm sẽ BỎ QUA menu này và
    // quét toàn bộ dữ liệu như trước.
    // ------------------------------------------------------------------
    let _bodyKitSearchTimer = null;
    let _bodyKitMenuCache = null;   // [{family, group_count, sub_models:[{sub_model, group_count}]}]
    let _bodyKitSelectedFamily = null;
    let _bodyKitSelectedSubModel = null;
    // Đặt true nếu muốn bật lại menu con (lọc theo đời/dung tích) của Bảng Giá Bộ Áo.
    const BODYKIT_SUBMENU_ENABLED = false;

    function debouncedLoadBodyKitGroups() {
        clearTimeout(_bodyKitSearchTimer);
        _bodyKitSearchTimer = setTimeout(() => {
            renderBodyKitMenuVisibility();
            loadBodyKitGroups();
        }, 350);
    }

    function _fmtVnd(v) {
        return v === null || v === undefined ? '—' : Math.round(v).toLocaleString('vi-VN') + ' đ';
    }

    function renderBodyKitMenuVisibility() {
        const q = document.getElementById('bodyKitSearchInput')?.value.trim() || '';
        const wrap = document.getElementById('bodyKitMenuWrap');
        if (wrap) wrap.style.display = q ? 'none' : '';
    }

    function bodyKitTabOpened() {
        renderBodyKitMenuVisibility();
        if (!_bodyKitMenuCache) {
            loadBodyKitMenu();
        }
        loadBodyKitGroups();
    }

    async function loadBodyKitMenu() {
        const famEl = document.getElementById('bodyKitFamilyMenu');
        if (!famEl) return;
        try {
            const res = await fetch('/api/body-kit/menu');
            const json = await res.json();
            if (!json.success) return;
            _bodyKitMenuCache = json.data;
            renderBodyKitFamilyMenu();
        } catch (err) {
            console.error('Lỗi loadBodyKitMenu:', err);
        }
    }

    // Menu dòng xe - thiết kế dạng thẻ (card) lớn, dễ bấm. Sau khi chọn 1
    // dòng, hiện thêm menu cấp 2 (đời/dung tích, vd Wave -> Wave S110,
    // RSX110, Wave 100...) ở renderBodyKitSubModelMenu() bên dưới - bấm 1
    // dòng xe vẫn xem luôn TẤT CẢ nhóm/đời của dòng đó ngay, menu cấp 2 chỉ
    // để lọc thêm cho gọn nếu muốn.
    function renderBodyKitFamilyMenu() {
        const famEl = document.getElementById('bodyKitFamilyMenu');
        if (!famEl || !_bodyKitMenuCache) return;
        famEl.innerHTML = _bodyKitMenuCache.map(f => `
            <button type="button" class="bodykit-family-btn ${_bodyKitSelectedFamily === f.family ? 'active' : ''}"
                    onclick="selectBodyKitFamily(${escapeHtmlAttr(JSON.stringify(f.family))})">
                <span class="bk-icon">🏍️</span>
                <span class="bk-name">${_bkEsc(f.family)}</span>
                <span class="bk-count">${f.group_count}</span>
            </button>
        `).join('');
        renderBodyKitSubModelMenu();
    }

    // Menu cấp 2 (đời/dung tích) - chỉ hiện khi đã chọn 1 dòng xe VÀ dòng đó
    // có từ 2 đời/dung tích khác nhau trở lên (chỉ 1 thì không cần lọc thêm).
    function renderBodyKitSubModelMenu() {
        const subEl = document.getElementById('bodyKitSubModelMenu');
        if (!subEl) return;
        const fam = _bodyKitMenuCache && _bodyKitSelectedFamily
            ? _bodyKitMenuCache.find(f => f.family === _bodyKitSelectedFamily) : null;
        const subModels = fam ? fam.sub_models.filter(s => s.sub_model) : [];
        if (!BODYKIT_SUBMENU_ENABLED || !fam || subModels.length < 2) {
            subEl.classList.add('d-none');
            subEl.innerHTML = '';
            return;
        }
        subEl.classList.remove('d-none');
        subEl.innerHTML = `
            <button type="button" class="bodykit-submodel-btn ${!_bodyKitSelectedSubModel ? 'active' : ''}"
                    onclick="selectBodyKitSubModel(null)">
                <span>Tất cả ${_bkEsc(fam.family)}</span>
                <span class="bk-count">${fam.group_count}</span>
            </button>
        ` + subModels.map(s => `
            <button type="button" class="bodykit-submodel-btn ${_bodyKitSelectedSubModel === s.sub_model ? 'active' : ''}"
                    onclick="selectBodyKitSubModel(${escapeHtmlAttr(JSON.stringify(s.sub_model))})">
                <span>${_bkEsc(s.sub_model)}</span>
                <span class="bk-count">${s.group_count}</span>
            </button>
        `).join('');
    }

    function selectBodyKitFamily(family) {
        // Bấm lại đúng dòng xe đang chọn -> bỏ chọn (về lại trạng thái
        // "chưa chọn dòng nào"); bấm dòng khác -> chuyển thẳng sang dòng đó
        // và hiện luôn TẤT CẢ nhóm/đời của dòng này (menu cấp 2 chỉ để lọc
        // thêm nếu muốn).
        _bodyKitSelectedFamily = (_bodyKitSelectedFamily === family) ? null : family;
        _bodyKitSelectedSubModel = null;
        renderBodyKitFamilyMenu();
        loadBodyKitGroups();
    }

    function selectBodyKitSubModel(subModel) {
        // Bấm lại đúng đời/dung tích đang chọn -> bỏ chọn (về lại xem TẤT CẢ
        // nhóm của dòng xe); bấm đời khác -> lọc theo đúng đời đó.
        _bodyKitSelectedSubModel = (_bodyKitSelectedSubModel === subModel) ? null : subModel;
        renderBodyKitSubModelMenu();
        loadBodyKitGroups();
    }

    async function loadBodyKitGroups() {
        const grid = document.getElementById('body-kit-grid');
        if (!grid) return;
        const q = document.getElementById('bodyKitSearchInput')?.value.trim() || '';

        // Chưa gõ tìm kiếm VÀ chưa chọn dòng xe nào - hiện gợi ý thay vì
        // đổ nguyên ~750 nhóm ra 1 lúc (đây chính là lý do có menu này).
        if (!q && !_bodyKitSelectedFamily) {
            grid.innerHTML = `<div class="col-12 text-center py-4 text-muted">Chọn 1 dòng xe ở trên, hoặc gõ tìm kiếm để xem danh sách.</div>`;
            document.getElementById('bodyKitVisibleCount').innerText = '';
            return;
        }

        try {
            const params = new URLSearchParams();
            if (q) {
                params.set('q', q);
            } else {
                if (_bodyKitSelectedFamily) params.set('family', _bodyKitSelectedFamily);
                if (_bodyKitSelectedSubModel) params.set('sub_model', _bodyKitSelectedSubModel);
            }
            const res = await fetch('/api/body-kit/groups?' + params.toString());
            const json = await res.json();
            if (!json.success) {
                grid.innerHTML = `<div class="col-12 text-center py-4 text-danger">${json.error || 'Lỗi tải dữ liệu.'}</div>`;
                return;
            }

            document.getElementById('bodyKitVisibleCount').innerText =
                `${json.total.toLocaleString()} / ${json.total_groups_all.toLocaleString()} nhóm`;
            const lastImportEl = document.getElementById('bodyKitLastImportInfo');
            if (lastImportEl) {
                lastImportEl.innerText = json.last_import
                    ? `Lần nhập gần nhất: ${new Date(json.last_import).toLocaleString('vi-VN')} — hiện có ${json.total_groups_all.toLocaleString()} nhóm.`
                    : 'Chưa có dữ liệu nào được nhập.';
            }

            if (!json.data || json.data.length === 0) {
                grid.innerHTML = `<div class="col-12 text-center py-4 text-muted">Không tìm thấy nhóm nào khớp.</div>`;
                return;
            }

            grid.innerHTML = json.data.map(g => `
                <div class="col-6 col-md-4 col-lg-3">
                    <div class="card card-custom h-100 p-2" style="cursor:pointer;" onclick="openBodyKitDetail(${g.id})">
                        ${g.image_url
                            ? `<div class="w-100 rounded-3 mb-2 bg-light" style="height:120px;"><img src="${g.image_url}" class="w-100 h-100 rounded-3" style="object-fit:contain;" loading="lazy"></div>`
                            : `<div class="w-100 rounded-3 mb-2 bg-light d-flex align-items-center justify-content-center text-muted" style="height:120px;"><i class="bi bi-image fs-2"></i></div>`}
                        <div class="small fw-semibold" style="line-height:1.3;">${_bkEsc(g.group_label)}</div>
                        <div class="small text-muted">${_bkEsc(g.model_code || '')} · ${g.part_count} phụ tùng
                            ${g.is_manual ? '<span class="badge bg-info-subtle text-info-emphasis ms-1">Thủ công</span>' : ''}</div>
                    </div>
                </div>
            `).join('');
        } catch (err) {
            console.error('Lỗi loadBodyKitGroups:', err);
            grid.innerHTML = `<div class="col-12 text-center py-4 text-danger">Lỗi kết nối máy chủ.</div>`;
        }
    }

    // ------------------------------------------------------------------
    // KÉO GIÃN TO/NHỎ modal "Chi Tiết Bộ Áo" bằng tay cầm ở góc dưới-phải.
    // Cùng cơ chế với modal "Tra Cứu Nhanh" ở trên (set width/maxWidth
    // trực tiếp bằng JS khi kéo - đáng tin cậy hơn CSS resize thuần vì
    // modal-dialog nằm trong flex container của Bootstrap).
    // ------------------------------------------------------------------
    function setupBodyKitDetailModalResize() {
        const handle = document.getElementById('bodyKitDetailModal-resize-handle');
        const dialog = document.getElementById('bodyKitDetailDialog');
        if (!handle || !dialog) return;

        const STORAGE_KEY = 'bodyKitDetailModalSize';
        const MIN_W = 360, MIN_H = 300;
        let startX = 0, startY = 0, startW = 0, startH = 0, resizing = false;

        function maxW() { return Math.round(window.innerWidth * 0.97); }
        function maxH() { return Math.round(window.innerHeight * 0.95); }

        function applySize(w, h) {
            w = Math.min(Math.max(w, MIN_W), maxW());
            h = Math.min(Math.max(h, MIN_H), maxH());
            dialog.style.width = w + 'px';
            dialog.style.maxWidth = w + 'px';
            dialog.style.height = h + 'px';
            return { w, h };
        }

        // Khôi phục kích thước đã lưu mỗi khi modal được mở.
        document.getElementById('bodyKitDetailModal').addEventListener('show.bs.modal', () => {
            try {
                const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
                if (saved && saved.w && saved.h) applySize(saved.w, saved.h);
            } catch (e) { /* bỏ qua nếu dữ liệu lưu bị hỏng */ }
        });

        handle.addEventListener('pointerdown', (e) => {
            resizing = true;
            const rect = dialog.getBoundingClientRect();
            startW = rect.width;
            startH = rect.height;
            startX = e.clientX;
            startY = e.clientY;
            handle.setPointerCapture(e.pointerId);
            e.preventDefault();
        });

        handle.addEventListener('pointermove', (e) => {
            if (!resizing) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            applySize(startW + dx, startH + dy);
        });

        function endResize(e) {
            if (!resizing) return;
            resizing = false;
            const rect = dialog.getBoundingClientRect();
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify({ w: Math.round(rect.width), h: Math.round(rect.height) }));
            } catch (err) { /* bỏ qua nếu localStorage đầy/không dùng được */ }
        }
        handle.addEventListener('pointerup', endResize);
        handle.addEventListener('pointercancel', endResize);
    }

    function updateBodyKitSelectedSum() {
        let sum = 0, count = 0;
        document.querySelectorAll('.bodyKitPartCheck:checked').forEach(cb => {
            const price = parseFloat(cb.dataset.price || '');
            if (!isNaN(price)) sum += price;
            count++;
        });
        const sumEl = document.getElementById('bodyKitSelectedSum');
        const countEl = document.getElementById('bodyKitSelectedCount');
        if (sumEl) sumEl.innerText = count > 0 ? _fmtVnd(sum) : '0 đ';
        if (countEl) countEl.innerText = count;
    }

    function toggleAllBodyKitParts(master) {
        document.querySelectorAll('.bodyKitPartCheck').forEach(cb => { cb.checked = master.checked; });
        updateBodyKitSelectedSum();
    }

    // Lightbox phóng to ảnh - bấm vào ảnh (thumbnail lưới hoặc ảnh chi tiết)
    // sẽ hiện ảnh cỡ lớn full màn hình; bấm ra ngoài / nút X / phím Esc để đóng.
    function openImageLightbox(src, ev) {
        if (ev) ev.stopPropagation();
        if (!src) return;
        document.getElementById('imgLightboxImg').src = src;
        document.getElementById('imgLightboxOverlay').classList.add('show');
    }
    function closeImageLightbox(ev) {
        if (ev) ev.stopPropagation();
        document.getElementById('imgLightboxOverlay').classList.remove('show');
        document.getElementById('imgLightboxImg').src = '';
    }
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeImageLightbox();
    });

    async function openBodyKitDetail(groupId) {
        const modalEl = document.getElementById('bodyKitDetailModal');
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
        _bodyKitCurrentGroup = null;
        document.getElementById('bodyKitManualBadge').style.display = 'none';
        document.getElementById('bodyKitManualActions').style.display = 'none';

        document.getElementById('bodyKitDetailTitle').innerText = 'Đang tải...';
        document.getElementById('bodyKitDetailParts').innerHTML =
            '<tr><td colspan="8" class="text-center py-3 text-muted">Đang tải...</td></tr>';
        const selectAllEl = document.getElementById('bodyKitSelectAll');
        if (selectAllEl) selectAllEl.checked = false;
        updateBodyKitSelectedSum();

        try {
            const res = await fetch(`/api/body-kit/groups/${groupId}`);
            const json = await res.json();
            if (!json.success) {
                document.getElementById('bodyKitDetailTitle').innerText = 'Lỗi';
                document.getElementById('bodyKitDetailParts').innerHTML =
                    `<tr><td colspan="8" class="text-center py-3 text-danger">${json.error || 'Không tải được dữ liệu.'}</td></tr>`;
                return;
            }
            const g = json.data;

            document.getElementById('bodyKitDetailTitle').innerText = g.group_label;
            document.getElementById('bodyKitDetailModel').innerText = g.model_code || '—';

            _bodyKitCurrentGroup = g;
            document.getElementById('bodyKitManualBadge').style.display = g.is_manual ? '' : 'none';
            document.getElementById('bodyKitManualBy').innerText = (g.is_manual && g.created_by) ? ` · ${g.created_by}` : '';
            document.getElementById('bodyKitManualActions').style.display = g.can_edit ? '' : 'none';

            const imgWrap = document.getElementById('bodyKitDetailImageWrap');
            const img = document.getElementById('bodyKitDetailImage');
            const noImg = document.getElementById('bodyKitDetailNoImage');
            if (g.image_url) {
                img.src = g.image_url;
                imgWrap.style.display = 'block';
                noImg.style.display = 'none';
            } else {
                imgWrap.style.display = 'none';
                noImg.style.display = '';
            }

            const tbody = document.getElementById('bodyKitDetailParts');
            if (!g.parts || g.parts.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" class="text-center py-3 text-muted">Không có phụ tùng nào.</td></tr>';
            } else {
                // Giá bán HIỆN TẠI (part_prices, Admin nhập) chỉ kèm huy hiệu
                // "đã điều chỉnh +5%" hay "chưa điều chỉnh" so với giá Honda
                // gốc - không hiện số tiền tăng/giảm cụ thể.
                const priceCellHtml = (p) => {
                    const priceTxt = p.current_price != null
                        ? _fmtVnd(p.current_price)
                        : (p.honda_price != null ? `<span class="text-muted">${_fmtVnd(p.honda_price)} <span class="fst-italic">(giá gốc)</span></span>` : '—');
                    let badge = '';
                    if (p.is_adjusted_5pct === true) {
                        badge = `<span class="badge bg-success-subtle text-success ms-1">✓ Đã điều chỉnh +5%</span>`;
                    } else if (p.is_adjusted_5pct === false) {
                        badge = `<span class="badge bg-secondary-subtle text-secondary ms-1">Chưa điều chỉnh</span>`;
                    }
                    return `${priceTxt}${badge}`;
                };
                tbody.innerHTML = g.parts.map(p => {
                    const priceForSum = p.current_price != null ? p.current_price : (p.honda_price != null ? p.honda_price : '');
                    return `
                    <tr>
                        <td><input type="checkbox" class="bodyKitPartCheck" data-price="${priceForSum}" onchange="updateBodyKitSelectedSum()"></td>
                        <td>${p.seq ?? ''}</td>
                        <td><span class="part-code">${_bkEsc(p.part_code || '')}</span></td>
                        <td>${_bkEsc(p.part_name || '')}</td>
                        <td class="text-muted">${_bkEsc(p.replacement_code || '')}</td>
                        <td class="text-end">${priceCellHtml(p)}</td>
                        <td class="text-end">${p.current_stock != null ? p.current_stock : '<span class="text-muted">—</span>'}</td>
                        <td class="text-muted">${_bkEsc(p.note || '')}</td>
                    </tr>
                `;
                }).join('');
            }
            updateBodyKitSelectedSum();
        } catch (err) {
            console.error('Lỗi openBodyKitDetail:', err);
            document.getElementById('bodyKitDetailTitle').innerText = 'Lỗi';
            document.getElementById('bodyKitDetailParts').innerHTML =
                '<tr><td colspan="8" class="text-center py-3 text-danger">Lỗi kết nối máy chủ.</td></tr>';
        }
    }

    // ------------------------------------------------------------------
    // THÊM / SỬA / XOÁ BỘ ÁO THỦ CÔNG (admin + cửa hàng đều thêm được).
    // - Bộ áo thêm ở đây (is_manual) KHÔNG bị xoá khi admin nhập lại Excel.
    // - Sửa/xoá: admin được mọi bộ áo thủ công; cửa hàng chỉ bộ áo do chính
    //   tài khoản mình tạo (server quyết định, trả về g.can_edit).
    // - Mọi chuỗi người dùng nhập đều phải qua _bkEsc() trước khi đưa vào
    //   innerHTML (chống stored XSS).
    // ------------------------------------------------------------------
    let _bodyKitCurrentGroup = null;
    let _bkfImageBlob = null;   // ảnh đã thu nhỏ, sẵn sàng gửi lên (null = không đổi ảnh)

    function _bkEsc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function _bkParseMoney(v) {
        const digits = String(v == null ? '' : v).replace(/[^\d]/g, '');
        return digits ? Number(digits) : null;
    }

    function bkfFormatMoney(el) {
        const n = _bkParseMoney(el.value);
        el.value = n == null ? '' : n.toLocaleString('vi-VN');
    }

    function bkfRenumber() {
        const rows = document.querySelectorAll('#bkfPartsBody tr');
        rows.forEach((tr, i) => { tr.querySelector('.bkf-seq').innerText = i + 1; });
        const cnt = document.getElementById('bkfPartCount');
        if (cnt) cnt.innerText = rows.length ? `(${rows.length} dòng)` : '';
    }

    function addBodyKitFormRow(p) {
        p = p || {};
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="text-muted bkf-seq"></td>
            <td style="min-width:170px;">
                <input type="text" class="form-control form-control-sm bkf-code" maxlength="100"
                       placeholder="Mã hàng" value="${_bkEsc(p.part_code)}" onchange="bkfLookupRows([this.closest('tr')])">
                <div class="bkf-hint small mt-1"></div>
            </td>
            <td style="min-width:220px;"><input type="text" class="form-control form-control-sm bkf-name" maxlength="500" value="${_bkEsc(p.part_name)}"></td>
            <td style="min-width:110px;"><input type="text" class="form-control form-control-sm bkf-repl" maxlength="255" value="${_bkEsc(p.replacement_code)}"></td>
            <td style="min-width:130px;"><input type="text" inputmode="numeric" class="form-control form-control-sm bkf-honda text-end"
                       oninput="bkfFormatMoney(this)" value="${p.honda_price != null ? Math.round(p.honda_price).toLocaleString('vi-VN') : ''}"></td>
            <td style="min-width:150px;"><input type="text" class="form-control form-control-sm bkf-note" maxlength="500" value="${_bkEsc(p.note)}"></td>
            <td><button type="button" class="btn btn-sm btn-outline-danger" title="Xoá dòng" onclick="removeBodyKitFormRow(this)"><i class="bi bi-x-lg"></i></button></td>`;
        document.getElementById('bkfPartsBody').appendChild(tr);
        bkfRenumber();
        return tr;
    }

    function removeBodyKitFormRow(btn) {
        btn.closest('tr').remove();
        bkfRenumber();
    }

    // Tra mã hàng trong hệ thống (tên hàng, giá bán, tồn) -> tự điền tên nếu ô
    // tên đang trống, chuẩn hoá mã về đúng dạng đang lưu, cảnh báo mã lạ.
    async function bkfLookupRows(rows) {
        const codeOf = r => r.querySelector('.bkf-code').value.trim();
        const codes = [...new Set(rows.map(codeOf).filter(Boolean))];
        rows.forEach(r => { r.querySelector('.bkf-hint').innerHTML = codeOf(r) ? '<span class="text-muted">Đang tra...</span>' : ''; });
        if (!codes.length) return;
        try {
            const res = await fetch('/api/body-kit/part-lookup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ codes })
            });
            const json = await res.json();
            if (!json.success) throw new Error(json.error || 'lookup failed');
            rows.forEach(r => {
                const codeEl = r.querySelector('.bkf-code');
                const hint = r.querySelector('.bkf-hint');
                const code = codeEl.value.trim();
                if (!code) { hint.innerHTML = ''; return; }
                const info = json.data[code.toUpperCase()];
                if (!info) {
                    hint.innerHTML = '<span class="text-warning-emphasis">⚠ Chưa có trong hệ thống - kiểm tra lại mã</span>';
                    return;
                }
                codeEl.value = info.part_code;
                const nameEl = r.querySelector('.bkf-name');
                if (!nameEl.value.trim() && info.part_name) nameEl.value = info.part_name;
                const bits = [info.sale_price != null ? 'Giá bán: ' + _fmtVnd(info.sale_price) : 'Chưa có giá bán'];
                if (info.stock != null) bits.push('Tồn: ' + info.stock);
                hint.innerHTML = `<span class="text-success">${_bkEsc(bits.join(' · '))}</span>`;
            });
        } catch (err) {
            console.error('Lỗi bkfLookupRows:', err);
            rows.forEach(r => { r.querySelector('.bkf-hint').innerHTML = ''; });
        }
    }

    function bkfToggleBulk() {
        const box = document.getElementById('bkfBulkBox');
        box.style.display = box.style.display === 'none' ? '' : 'none';
    }

    function bkfBulkAdd() {
        const textEl = document.getElementById('bkfBulkText');
        const existing = new Set([...document.querySelectorAll('#bkfPartsBody .bkf-code')]
            .map(i => i.value.trim().toUpperCase()).filter(Boolean));
        const codes = textEl.value.split(/[\s,;]+/).map(c => c.trim()).filter(Boolean);
        if (codes.some(c => !existing.has(c.toUpperCase()))) {
            // Bỏ các dòng còn trống hoàn toàn (vd dòng trắng mặc định lúc mở form) cho gọn.
            document.querySelectorAll('#bkfPartsBody tr').forEach(tr => {
                const isEmpty = ['.bkf-code', '.bkf-name', '.bkf-repl', '.bkf-honda', '.bkf-note']
                    .every(sel => !tr.querySelector(sel).value.trim());
                if (isEmpty) tr.remove();
            });
        }
        const newRows = [];
        codes.forEach(c => {
            const key = c.toUpperCase();
            if (existing.has(key)) return;
            existing.add(key);
            newRows.push(addBodyKitFormRow({ part_code: c }));
        });
        textEl.value = '';
        if (newRows.length) bkfLookupRows(newRows);
        else alert('Không có mã mới nào để thêm (trống hoặc đã có trong bảng).');
    }

    // Thu nhỏ ảnh ở trình duyệt (ảnh chụp điện thoại thường 3-8MB) - tối đa
    // 1200px cạnh dài, JPEG chất lượng 0.85; server giới hạn 3MB.
    function bkfImageChosen(input) {
        _bkfImageBlob = null;
        const prev = document.getElementById('bkfImagePreview');
        const file = input.files && input.files[0];
        if (!file) { prev.style.display = 'none'; return; }
        if (!file.type.startsWith('image/')) {
            alert('Vui lòng chọn file ảnh.');
            input.value = '';
            prev.style.display = 'none';
            return;
        }
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
            const MAX = 1200;
            const scale = Math.min(1, MAX / Math.max(img.width, img.height));
            if (scale === 1 && file.size <= 1024 * 1024) {
                _bkfImageBlob = file;   // ảnh đã nhỏ: giữ nguyên
            } else {
                const canvas = document.createElement('canvas');
                canvas.width = Math.round(img.width * scale);
                canvas.height = Math.round(img.height * scale);
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#fff';               // nền trắng cho ảnh PNG trong suốt
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                canvas.toBlob(b => { _bkfImageBlob = b || file; }, 'image/jpeg', 0.85);
            }
            prev.src = url;
            prev.style.display = '';
        };
        img.onerror = () => {
            alert('Không đọc được ảnh này.');
            input.value = '';
            prev.style.display = 'none';
        };
        img.src = url;
    }

    async function openBodyKitForm(groupId) {
        const modalEl = document.getElementById('bodyKitFormModal');
        document.getElementById('bkfId').value = groupId || '';
        document.getElementById('bodyKitFormTitle').innerText = groupId ? 'Sửa Bộ Áo' : 'Thêm Bộ Áo';
        ['bkfLabel', 'bkfModel', 'bkfYear', 'bkfFamily', 'bkfSubModel', 'bkfBulkText'].forEach(id => { document.getElementById(id).value = ''; });
        document.getElementById('bkfImage').value = '';
        document.getElementById('bkfImagePreview').style.display = 'none';
        document.getElementById('bkfRemoveImage').checked = false;
        document.getElementById('bkfRemoveImageWrap').style.display = 'none';
        document.getElementById('bkfBulkBox').style.display = 'none';
        document.getElementById('bkfPartsBody').innerHTML = '';
        document.getElementById('bkfStatus').innerText = '';
        _bkfImageBlob = null;

        // Gợi ý dòng xe có sẵn (từ menu) cho ô "Dòng xe".
        if (!_bodyKitMenuCache) await loadBodyKitMenu();
        document.getElementById('bkfFamilyList').innerHTML = (_bodyKitMenuCache || [])
            .filter(f => f.family !== 'Khác')
            .map(f => `<option value="${_bkEsc(f.family)}"></option>`).join('');
        // Gợi ý đời/dung tích có sẵn (gộp tất cả dòng xe) cho ô "Đời/dung
        // tích", để dễ chọn lại ĐÚNG tên đã dùng thay vì gõ tay ra 1 biến
        // thể mới (đây chính là nguyên nhân bị trùng lặp kiểu "Air Blade"
        // vs "Ari Blade" vs "AIRBLADE 110" trước đây).
        const allSubModels = new Set();
        (_bodyKitMenuCache || []).forEach(f => f.sub_models.forEach(s => { if (s.sub_model) allSubModels.add(s.sub_model); }));
        document.getElementById('bkfSubModelList').innerHTML = [...allSubModels].sort()
            .map(s => `<option value="${_bkEsc(s)}"></option>`).join('');

        if (!groupId) {
            addBodyKitFormRow();
            bootstrap.Modal.getOrCreateInstance(modalEl).show();
            return;
        }
        try {
            const res = await fetch(`/api/body-kit/groups/${groupId}`);
            const json = await res.json();
            if (!json.success) { alert(json.error || 'Không tải được bộ áo để sửa.'); return; }
            const g = json.data;
            document.getElementById('bkfLabel').value = g.group_label || '';
            document.getElementById('bkfModel').value = g.model_code || '';
            document.getElementById('bkfYear').value = g.year || '';
            document.getElementById('bkfFamily').value = g.vehicle_family || '';
            document.getElementById('bkfSubModel').value = g.sub_model || '';
            if (g.image_url) {
                const prev = document.getElementById('bkfImagePreview');
                prev.src = g.image_url;
                prev.style.display = '';
                document.getElementById('bkfRemoveImageWrap').style.display = '';
            }
            (g.parts || []).forEach(p => addBodyKitFormRow(p));
            if (!g.parts || !g.parts.length) addBodyKitFormRow();
            bootstrap.Modal.getOrCreateInstance(modalEl).show();
            bkfLookupRows([...document.querySelectorAll('#bkfPartsBody tr')]);
        } catch (err) {
            console.error('Lỗi openBodyKitForm:', err);
            alert('Lỗi kết nối máy chủ.');
        }
    }

    async function saveBodyKitForm() {
        const id = document.getElementById('bkfId').value;
        const label = document.getElementById('bkfLabel').value.trim();
        if (!label) { alert('Vui lòng nhập tên bộ áo (đời xe + màu).'); return; }

        const parts = [];
        const seen = new Map();
        const rows = [...document.querySelectorAll('#bkfPartsBody tr')];
        for (let i = 0; i < rows.length; i++) {
            const tr = rows[i];
            const code = tr.querySelector('.bkf-code').value.trim();
            const name = tr.querySelector('.bkf-name').value.trim();
            const repl = tr.querySelector('.bkf-repl').value.trim();
            const note = tr.querySelector('.bkf-note').value.trim();
            const honda = _bkParseMoney(tr.querySelector('.bkf-honda').value);
            if (!code && !name && !repl && !note && honda == null) continue;   // dòng trống
            if (!code) { alert(`Dòng ${i + 1}: thiếu mã hàng.`); return; }
            const key = code.toUpperCase();
            if (seen.has(key)) { alert(`Mã hàng "${code}" bị trùng (dòng ${seen.get(key)} và dòng ${i + 1}).`); return; }
            seen.set(key, i + 1);
            parts.push({ part_code: code, part_name: name, replacement_code: repl, honda_price: honda, note });
        }
        if (!parts.length) { alert('Bộ áo cần có ít nhất 1 phụ tùng.'); return; }

        const fd = new FormData();
        fd.append('group_label', label);
        fd.append('model_code', document.getElementById('bkfModel').value.trim());
        fd.append('vehicle_family', document.getElementById('bkfFamily').value.trim());
        fd.append('sub_model', document.getElementById('bkfSubModel').value.trim());
        fd.append('year', document.getElementById('bkfYear').value.trim());
        fd.append('parts', JSON.stringify(parts));
        if (_bkfImageBlob) fd.append('image', _bkfImageBlob, 'image.jpg');
        if (document.getElementById('bkfRemoveImage').checked) fd.append('remove_image', '1');

        const btn = document.getElementById('bkfSaveBtn');
        const status = document.getElementById('bkfStatus');
        btn.disabled = true;
        status.innerText = 'Đang lưu...';
        try {
            const res = await fetch(id ? `/api/body-kit/groups/${id}` : '/api/body-kit/groups',
                                    { method: id ? 'PUT' : 'POST', body: fd });
            const json = await res.json();
            if (!json.success) {
                status.innerText = '';
                alert(json.error || 'Lưu bộ áo thất bại.');
                return;
            }
            bootstrap.Modal.getOrCreateInstance(document.getElementById('bodyKitFormModal')).hide();
            // Chuyển thẳng tới dòng xe của bộ áo vừa lưu để người dùng thấy ngay.
            _bodyKitMenuCache = null;
            _bodyKitSelectedFamily = json.vehicle_family || null;
            _bodyKitSelectedSubModel = null;
            const searchEl = document.getElementById('bodyKitSearchInput');
            if (searchEl) searchEl.value = '';
            renderBodyKitMenuVisibility();
            await loadBodyKitMenu();
            loadBodyKitGroups();
        } catch (err) {
            console.error('Lỗi saveBodyKitForm:', err);
            alert('Lỗi kết nối máy chủ khi lưu bộ áo.');
        } finally {
            btn.disabled = false;
            status.innerText = '';
        }
    }

    function editBodyKitFromDetail() {
        if (!_bodyKitCurrentGroup) return;
        const id = _bodyKitCurrentGroup.id;
        const el = document.getElementById('bodyKitDetailModal');
        // Đóng modal chi tiết xong mới mở form (Bootstrap không xếp chồng 2 modal ổn định).
        el.addEventListener('hidden.bs.modal', () => openBodyKitForm(id), { once: true });
        bootstrap.Modal.getOrCreateInstance(el).hide();
    }

    async function deleteBodyKitFromDetail() {
        const g = _bodyKitCurrentGroup;
        if (!g) return;
        if (!await nsConfirm(`Xoá bộ áo "${g.group_label}"? Thao tác này không thể hoàn tác.`)) return;
        try {
            const res = await fetch(`/api/body-kit/groups/${g.id}`, { method: 'DELETE' });
            const json = await res.json();
            if (!json.success) { alert(json.error || 'Xoá thất bại.'); return; }
            bootstrap.Modal.getOrCreateInstance(document.getElementById('bodyKitDetailModal')).hide();
            _bodyKitMenuCache = null;
            await loadBodyKitMenu();
            loadBodyKitGroups();
        } catch (err) {
            console.error('Lỗi deleteBodyKitFromDetail:', err);
            alert('Lỗi kết nối máy chủ khi xoá.');
        }
    }

    async function importBodyKitExcel() {
        const input = document.getElementById('bodyKitImportFile');
        const btn = document.getElementById('bodyKitImportBtn');
        const status = document.getElementById('bodyKitImportStatus');
        if (!input.files || input.files.length === 0) {
            alert('Vui lòng chọn 1 file Excel (.xlsx) trước.');
            return;
        }
        if (!await nsConfirm('Nhập dữ liệu mới sẽ THAY THẾ toàn bộ bảng giá bộ áo đã nhập từ Excel trước đó (bộ áo thêm thủ công được giữ nguyên). Tiếp tục?')) return;

        const formData = new FormData();
        formData.append('file', input.files[0]);

        btn.disabled = true;
        if (status) status.innerText = 'Đang nhập dữ liệu (có thể mất 1-2 phút do file lớn)...';
        try {
            const res = await fetch('/api/admin/body-kit/import', { method: 'POST', body: formData });
            const json = await res.json();
            if (!json.success) {
                if (status) status.innerText = '';
                alert(json.error || 'Nhập dữ liệu thất bại.');
                return;
            }
            if (status) {
                status.innerText = `Đã nhập ${json.groups_imported.toLocaleString()} nhóm, ` +
                    `${json.parts_imported.toLocaleString()} phụ tùng, ${json.with_image.toLocaleString()} nhóm có ảnh` +
                    (json.warnings_total > 0 ? ` (${json.warnings_total} dòng cảnh báo - xem console).` : '.');
            }
            if (json.warnings_total > 0) console.warn('Cảnh báo import bộ áo:', json.warnings);
            input.value = '';
            // Dữ liệu vừa bị THAY THẾ TOÀN BỘ - menu dòng xe/đời cũ (đã cache)
            // có thể không còn đúng nữa, phải tải lại từ đầu.
            _bodyKitMenuCache = null;
            _bodyKitSelectedFamily = null;
            _bodyKitSelectedSubModel = null;
            loadBodyKitMenu();
            loadBodyKitGroups();
        } catch (err) {
            if (status) status.innerText = '';
            console.error('Lỗi importBodyKitExcel:', err);
            alert('Lỗi kết nối máy chủ khi nhập dữ liệu.');
        } finally {
            btn.disabled = false;
        }
    }

    // ---- Duyệt Đơn Hàng: import "Khoá đặt hàng" + kiểm tra đơn hàng -------
    async function importOrderLockExcel() {
        const input = document.getElementById('orderLockImportFile');
        const btn = document.getElementById('orderLockImportBtn');
        const status = document.getElementById('orderLockImportStatus');
        if (!input.files || input.files.length === 0) {
            alert('Vui lòng chọn 1 file trước.');
            return;
        }
        if (!await nsConfirm('Nhập dữ liệu mới sẽ THAY THẾ TOÀN BỘ dữ liệu khoá đặt hàng hiện có. Tiếp tục?')) return;

        const formData = new FormData();
        formData.append('lock_file', input.files[0]);

        btn.disabled = true;
        if (status) status.innerText = 'Đang nhập dữ liệu...';
        try {
            const res = await fetch('/api/admin/import-order-lock', { method: 'POST', body: formData });
            const json = await res.json();
            if (!json.success) {
                if (status) status.innerText = '';
                alert(json.error || 'Nhập dữ liệu thất bại.');
                return;
            }
            if (status) {
                status.innerText = `Đã nhập ${json.total_parts.toLocaleString()} mã hàng` +
                    (json.skipped_rows > 0 ? ` (bỏ qua ${json.skipped_rows} dòng thiếu mã hàng).` : '.');
            }
            input.value = '';
        } catch (err) {
            if (status) status.innerText = '';
            console.error('Lỗi importOrderLockExcel:', err);
            alert('Lỗi kết nối máy chủ khi nhập dữ liệu.');
        } finally {
            btn.disabled = false;
        }
    }

    async function importVehicleModelExcel() {
        const input = document.getElementById('vehicleModelImportFile');
        const btn = document.getElementById('vehicleModelImportBtn');
        const status = document.getElementById('vehicleModelImportStatus');
        if (!input.files || input.files.length === 0) {
            alert('Vui lòng chọn 1 file trước.');
            return;
        }
        if (!await nsConfirm('Nhập dữ liệu mới sẽ THAY THẾ TOÀN BỘ dữ liệu Model xe hiện có. Tiếp tục?')) return;

        const formData = new FormData();
        formData.append('vehicle_model_file', input.files[0]);

        btn.disabled = true;
        if (status) status.innerText = 'Đang nhập dữ liệu...';
        try {
            const res = await fetch('/api/admin/import-vehicle-models', { method: 'POST', body: formData });
            const json = await res.json();
            if (!json.success) {
                if (status) status.innerText = '';
                alert(json.error || 'Nhập dữ liệu thất bại.');
                return;
            }
            if (status) status.innerText = `Đã nhập ${json.total_parts.toLocaleString()} mã hàng.` +
                (json.price_column ? ` Cột giá: "${json.price_column}" · ${json.no_price.toLocaleString()} mã KHÔNG có giá nhập` + (json.no_price ? ` (vd: ${json.no_price_samples.slice(0, 5).join(', ')})` : '') + '.' : ' KHÔNG tìm thấy cột "Giá nhập" trong file.');
            input.value = '';
        } catch (err) {
            if (status) status.innerText = '';
            console.error('Lỗi importVehicleModelExcel:', err);
            alert('Lỗi kết nối máy chủ khi nhập dữ liệu.');
        } finally {
            btn.disabled = false;
        }
    }

    // Dựng HTML hiển thị "Dùng Cho Xe" (danh sách dòng xe/đời xe) cho 1
    // dòng kết quả Kiểm Tra Đơn Hàng - xem 'vehicle_models'/
    // 'vehicle_models_unresolved' trả về từ order_check() trong app.py.
    // Gộp theo Dòng xe (vehicle_family): mỗi dòng xe 1 badge, các Đời/kiểu
    // của dòng đó liệt kê trong tooltip (title) để không chiếm quá nhiều
    // chỗ trên bảng.
    function _orderCheckVehicleModelsHtml(row) {
        const models = row.vehicle_models || [];
        if (models.length) {
            const byFamily = {};
            models.forEach(m => {
                (byFamily[m.vehicle_family] = byFamily[m.vehicle_family] || []).push(m.sub_model);
            });
            return `<div class="d-flex flex-wrap gap-1" style="max-width:200px; white-space:normal;">` +
                Object.keys(byFamily).map(fam => {
                    const subs = [...new Set(byFamily[fam])];
                    return `<span class="badge bg-info-subtle text-info-emphasis border border-info-subtle" title="${escapeHtmlAttr(subs.join(', '))}">${escapeHtmlAttr(fam)}</span>`;
                }).join('') +
                `</div>`;
        }
        if (row.vehicle_models_unresolved) {
            return `<span class="small text-muted fst-italic" title="Không tách được thành mã xe hợp lệ - hiển thị nguyên văn dữ liệu gốc">${escapeHtmlAttr(row.vehicle_models_unresolved)}</span>`;
        }
        return `<span class="small text-muted">—</span>`;
    }

    // Chú thích cho nhãn TX/TB/CB nhỏ cạnh mỗi cửa hàng trong "Tồn Hệ Thống" -
    // giải thích RÕ vì sao cửa hàng đó được/không được xét làm nguồn chuyển.
    function _ocFreqTip(code) {
        if (code === 'TX') return 'Đang bán nhanh TẠI CHÍNH cửa hàng này - không có dư để chuyển đi';
        if (code === 'TB') return 'Bán trung bình tại cửa hàng này - có thể được đề xuất chuyển nếu còn dư sau khi giữ đủ dùng';
        if (code === 'CB') return 'Bán chậm tại cửa hàng này - ưu tiên được đề xuất chuyển nếu còn dư sau khi giữ đủ dùng';
        return '';
    }

    function _orderCheckFreqBadgeHtml(freq) {
        if (!freq || !freq.code) return '<span class="text-muted small">-</span>';
        const cls = 'freq-' + freq.code.toLowerCase();
        const tip = `Tồn đủ bán: ${_fmtMonthsText(freq.months_of_stock)} | TB bán/tháng: ${freq.avg_month}`;
        return `<span class="freq-badge ${cls}" data-tip="${escapeHtmlAttr(tip)}">${freq.code}</span>`;
    }

    function parseOrderCheckInput(raw) {
        // Chấp nhận dán 2 cột (mã hàng \t số lượng) trên CÙNG 1 dòng, HOẶC
        // dán kiểu "mỗi giá trị 1 dòng" (Excel đôi khi dán xuống dòng thay vì
        // tab khi chỉ chọn 2 cột hẹp) - trong trường hợp đó, dòng lẻ là mã
        // hàng (không phải số), dòng chẵn theo sau là số lượng.
        const lines = raw.split(/\r?\n/).map(l => l.trim()).filter(l => l !== '');
        const items = [];
        let i = 0;
        while (i < lines.length) {
            const parts = lines[i].split(/\t|,|;/).map(s => s.trim()).filter(s => s !== '');
            if (parts.length >= 2 && !isNaN(parseFloat(parts[1].replace(',', '.')))) {
                items.push({ part_code: parts[0], qty: parseFloat(parts[1].replace(',', '.')) });
                i += 1;
            } else if (!isNaN(parseFloat(lines[i].replace(',', '.'))) && items.length > 0 && items[items.length - 1].qty === null) {
                items[items.length - 1].qty = parseFloat(lines[i].replace(',', '.'));
                i += 1;
            } else {
                items.push({ part_code: lines[i], qty: null });
                i += 1;
            }
        }
        return items.map(it => ({ part_code: it.part_code, qty: it.qty || 0 })).filter(it => it.part_code);
    }

    // ---- Nguồn dữ liệu Nợ / Đang vận chuyển của Duyệt Đơn Hàng: 'store' (cửa hàng đổ) | 'admin' (admin đổ) ----
    const OC_DEBT_SOURCE_KEY = 'ocDebtSource';
    function _ocDebtSource() {
        const el = document.getElementById('orderCheckDebtSource');
        return el && el.value === 'admin' ? 'admin' : 'store';
    }
    function _ocApplyDebtMeta(meta) {
        const el = document.getElementById('ocDebtSourceNote');
        if (!el) return;
        if (!meta) { el.textContent = ''; return; }
        if (meta.warning) {
            el.className = 'small text-danger';
            el.textContent = '⚠ ' + meta.warning + ' Cột Nợ / Đang VC đang trống.';
        } else {
            el.className = 'small text-muted';
            el.textContent = meta.info || '';
        }
    }
    async function ocDebtSourceChanged() {
        const src = _ocDebtSource();
        try { localStorage.setItem(OC_DEBT_SOURCE_KEY, src); } catch (e) { /* bỏ qua */ }
        const note = document.getElementById('ocDebtSourceNote');
        if (!_orderCheckData || !_orderCheckData.length) { if (note) note.textContent = ''; return; }
        // Đang có kết quả: chỉ nạp lại 3 trường nợ / vận chuyển, KHÔNG chạy lại kiểm tra -> không mất SL đã sửa và ghi chú.
        const store = _orderCheckCurrentStore || document.getElementById('orderCheckStoreSelect').value;
        if (note) { note.className = 'small text-muted'; note.textContent = 'Đang nạp nợ / vận chuyển...'; }
        try {
            const res = await fetch('/api/admin/order-check/debt', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ store_code: store, debt_source: src, part_codes: _orderCheckData.map(r => r.part_code) })
            });
            const j = await res.json();
            if (!j.success) { if (note) note.textContent = ''; alert(j.error || 'Không nạp được dữ liệu nợ.'); return; }
            _orderCheckData.forEach(r => {
                const d = j.debt[r.part_code];
                if (!d) return;
                r.debt_qty = d.debt_qty; r.shipping_qty = d.shipping_qty; r.debt_po_codes = d.debt_po_codes;
            });
            _ocApplyDebtMeta(j.debt_meta);
            renderOrderCheckTable();
            renderOrderCheckDashboardSoon();
            saveOrderCheckSession();
        } catch (err) {
            console.error('Lỗi ocDebtSourceChanged:', err);
            if (note) note.textContent = '';
            alert('Lỗi kết nối máy chủ khi nạp nợ / vận chuyển.');
        }
    }
    (function ocInitDebtSource() {
        try {
            const v = localStorage.getItem(OC_DEBT_SOURCE_KEY);
            const el = document.getElementById('orderCheckDebtSource');
            if (el && (v === 'admin' || v === 'store')) el.value = v;
        } catch (e) { /* bỏ qua */ }
    })();

    async function runOrderCheck() {
        const storeCode = document.getElementById('orderCheckStoreSelect').value;
        const raw = document.getElementById('orderCheckInput').value;
        const items = parseOrderCheckInput(raw);
        const btn = document.getElementById('orderCheckBtn');
        const status = document.getElementById('orderCheckStatus');
        const resultCard = document.getElementById('orderCheckResultCard');
        const body = document.getElementById('order-check-body');

        if (items.length === 0) {
            alert('Vui lòng dán danh sách mã hàng + số lượng trước.');
            return;
        }

        btn.disabled = true;
        if (status) status.innerText = `Đang kiểm tra ${items.length} mã hàng...`;
        try {
            const res = await fetch('/api/admin/order-check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ store_code: storeCode, items, debt_source: _ocDebtSource() })
            });
            const json = await res.json();
            if (!json.success) {
                if (status) status.innerText = '';
                alert(json.error || 'Kiểm tra thất bại.');
                return;
            }
            if (status) status.innerText = `Đã kiểm tra ${json.data.length} mã hàng.`;
            _ocApplyDebtMeta(json.debt_meta);
            if (json.lock_meta && json.lock_meta.upload_time) {
                const lockInfo = document.getElementById('orderLockLastImportInfo');
                if (lockInfo) lockInfo.innerText = `Dữ liệu khoá đặt hàng cập nhật lần cuối: ${json.lock_meta.upload_time} (${json.lock_meta.filename || ''}, bởi ${json.lock_meta.uploaded_by || ''})`;
            }

            resultCard.classList.remove('d-none');
            _orderCheckData = json.data || [];
            _orderCheckData.forEach(r => { r._orig_suggest = r.suggested_approve_qty; });
            _orderCheckCurrentStore = storeCode;
            resetOrderCheckFilters();
            saveOrderCheckSession();
        } catch (err) {
            if (status) status.innerText = '';
            console.error('Lỗi runOrderCheck:', err);
            alert('Lỗi kết nối máy chủ khi kiểm tra đơn hàng.');
        } finally {
            btn.disabled = false;
        }
    }

    // Dữ liệu gốc (chưa lọc) của lần Kiểm Tra Đơn Hàng gần nhất - dùng lại
    // để lọc/render lại bảng nhiều lần mà KHÔNG cần gọi lại API mỗi khi đổi
    // bộ lọc (dữ liệu không đổi, chỉ hiển thị đổi).
    let _orderCheckData = [];
    // Cửa hàng đang được tra ở lần kiểm tra gần nhất - dùng để loại trừ
    // chính cửa hàng đó ra khỏi phần chi tiết "Tồn Hệ Thống" (đã hiển thị
    // riêng ở cột "Tồn CH" rồi, không cần lặp lại).
    let _orderCheckCurrentStore = '';

    // ---------------------------------------------------------------------
    // DUYỆT ĐƠN GÔM CỦA CHI NHÁNH (nằm trong menu Duyệt Đơn Hàng)
    // Danh sách thẻ theo tab -> Lấy về duyệt -> khung duyệt toàn màn hình (thanh công cụ phía trên bảng Kiểm Tra)
    // -> chỉnh SL Duyệt / ghi chú từng mã (tự lưu nháp lên máy chủ) -> Duyệt xong.
    // ---------------------------------------------------------------------
    var OC_GDH_KEY = 'ocGdhActive_v1';
    var _ocGdh = null;            // {id, store, from, to, codes: [mã chi nhánh đã gửi]}
    var _ocQuick = '', _ocGdhTab = 'active', _ocGdhT = null, _ocGdhDraftTxt = '', _ocGdhType = '', _ocGdhCache = [];
    function _ocGdhSave() { try { if (_ocGdh) localStorage.setItem(OC_GDH_KEY, JSON.stringify(_ocGdh)); else localStorage.removeItem(OC_GDH_KEY); } catch (e) { /* bỏ qua */ } }
    function _ocGdhRestore() { try { _ocGdh = JSON.parse(localStorage.getItem(OC_GDH_KEY) || 'null'); } catch (e) { _ocGdh = null; } _ocGdhRenderActive(); _ocGdhRenderBar(); }
    function _ocFmtWait(min) {
        min = Math.max(0, Math.round(min || 0));
        if (min < 1) return 'vừa đẩy';
        if (min < 60) return min + ' phút';
        if (min < 1440) return Math.floor(min / 60) + ' giờ ' + (min % 60) + ' phút';
        return Math.floor(min / 1440) + ' ngày ' + Math.floor((min % 1440) / 60) + ' giờ';
    }
    function _ocGdhRenderActive() {         // dòng nhắc ngắn trong khối danh sách
        document.getElementById('order-check-pane')?.classList.toggle('oc-gdh-on', !!_ocGdh);
        const el = document.getElementById('ocGdhActive'); if (!el) return;
        if (!_ocGdh) { el.classList.add('d-none'); el.innerHTML = ''; return; }
        el.classList.remove('d-none');
        el.innerHTML = `<div class="d-flex flex-wrap align-items-center gap-2"><span class="flex-grow-1"><i class="bi bi-pencil-square me-1"></i>Đang duyệt <b>${_gdhEsc(_ocGdhName())}</b> · <b>${_ocGdh.codes.length}</b> mã.</span>` +
            `<button type="button" class="btn btn-primary btn-sm" onclick="ocToggleMax(true)"><i class="bi bi-arrows-fullscreen me-1"></i>Mở khung duyệt</button></div>`;
    }
    function _ocGdhName() {
        const o = _ocGdhCache.find(x => x.id === _ocGdh.id);
        return o ? _gdhOrderName(o) : 'Đơn ' + _ocGdh.store;
    }
    function _ocGdhStats() {
        let diff = 0, locked = 0, short = 0;
        for (const r of _orderCheckData) {
            if (Number(r.suggested_approve_qty) !== Number(r.qty_order)) diff++;
            if (r.is_locked) locked++;
            if (r.still_needed_after_transfer > 0) short++;
        }
        return {diff, locked, short, total: _orderCheckData.length};
    }
    function _ocGdhRenderBar() {            // thanh công cụ của khung duyệt: tiến độ, xem nhanh, nút Duyệt xong
        const el = document.getElementById('ocGdhBar'); if (!el) return;
        if (!_ocGdh || !_orderCheckData.length || _orderCheckCurrentStore !== _ocGdh.store) { el.classList.add('d-none'); el.innerHTML = ''; return; }
        el.classList.remove('d-none');
        const st = _ocGdhStats(), e = _gdhEsc;
        const isMax = !!document.getElementById('order-check-pane')?.classList.contains('oc-max');
        const chip = (k, label, n) => `<button type="button" class="oc-chip ${_ocQuick === k ? 'on' : ''}" onclick="ocGdhQuick('${k}')">${label}${n === null ? '' : ` <b>${n}</b>`}</button>`;
        el.innerHTML = `<div class="d-flex flex-wrap align-items-center gap-2"><div class="flex-grow-1"><div class="fw-semibold"><i class="bi bi-pencil-square text-primary me-1"></i>${e(_ocGdhName())} · ${_ocGdh.codes.length} mã</div>` +
            `<div class="small text-muted">SL Duyệt khác SL gửi: <b>${st.diff}</b>/${st.total} mã${_ocGdhDraftTxt ? ' · ' + e(_ocGdhDraftTxt) : ''}</div></div>` +
            `<button type="button" class="btn btn-outline-secondary btn-sm" onclick="ocGdhResetSuggest()" title="Đặt lại SL Duyệt của mọi mã về số hệ thống đề xuất"><i class="bi bi-arrow-counterclockwise me-1"></i>Về đề xuất</button>` +
            `<button type="button" class="btn btn-outline-secondary btn-sm" onclick="ocToggleMax()"><i class="bi ${isMax ? 'bi-fullscreen-exit' : 'bi-arrows-fullscreen'} me-1"></i>${isMax ? 'Thu nhỏ' : 'Phóng to'}</button>` +
            `<button type="button" class="btn btn-outline-secondary btn-sm" onclick="ocGdhRelease()"><i class="bi bi-box-arrow-up me-1"></i>Trả về hàng chờ</button>` +
            `<button type="button" class="btn btn-outline-danger btn-sm" onclick="ocGdhReject()" title="Đơn chưa ổn: từ chối duyệt và trả về chi nhánh kèm lý do"><i class="bi bi-x-circle me-1"></i>Từ chối</button>` +
            `<button type="button" class="btn btn-success btn-sm" onclick="ocGdhApprove()"><i class="bi bi-check2-circle me-1"></i>Duyệt xong</button></div>` +
            `<div class="d-flex flex-wrap gap-1 mt-2 align-items-center"><span class="small text-muted me-1">Xem nhanh:</span>${chip('', 'Tất cả', null)}${chip('diff', 'SL duyệt ≠ SL gửi', st.diff)}${chip('locked', 'Bị khoá', st.locked)}${chip('short', 'Vẫn thiếu sau luân chuyển', st.short)}</div>` + _ocUrgPanel();
    }
    function ocGdhQuick(k) { _ocQuick = (_ocQuick === k) ? '' : k; renderOrderCheckTable(); _ocGdhRenderBar(); }
    async function ocGdhResetSuggest() {
        if (!_ocGdh) return;
        const n = _orderCheckData.filter(r => r._orig_suggest !== undefined && r._orig_suggest !== r.suggested_approve_qty).length;
        if (!n) { alert('Chưa có mã nào bị sửa so với đề xuất của hệ thống.'); return; }
        if (!await nsConfirm(`Đặt lại SL Duyệt của ${n} mã đã sửa về số hệ thống đề xuất? (Ghi chú giữ nguyên)`)) return;
        _orderCheckData.forEach(r => { if (r._orig_suggest !== undefined) r.suggested_approve_qty = r._orig_suggest; });
        renderOrderCheckTable(); saveOrderCheckSession(); _ocGdhDirty();
    }
    // Lấy SL Duyệt + ghi chú từng mã của đơn đang duyệt (ưu tiên giá trị đang hiện trên ô, vì ô ghi chú chỉ lưu khi rời ô)
    function _ocGdhCollect() {
        document.querySelectorAll('.order-check-note-input').forEach(el => {
            const r = _ocRowByCode(el.dataset.partCode); if (r) r.note = el.value;
        });
        document.querySelectorAll('.order-check-approve-input').forEach(el => {
            const r = _ocRowByCode(el.dataset.partCode); const v = parseInt(el.value, 10); if (r) r.suggested_approve_qty = isNaN(v) ? 0 : v;
        });
        const byCode = new Map();
        _orderCheckData.forEach(r => { byCode.set(r.part_code, r); if (r.typed_code) byCode.set(r.typed_code, r); });
        return _ocGdh.codes.map(c => { const r = byCode.get(c); return r ? {part_code: c, approved_qty: Number(r.suggested_approve_qty) || 0, note: r.note || ''} : {part_code: c, approved_qty: null, note: ''}; });
    }
    function _ocGdhApplyDraft(draft) {      // draft = {mã: {qty, note}} lưu trên máy chủ
        const byCode = new Map();
        _orderCheckData.forEach(r => { byCode.set(r.part_code, r); if (r.typed_code) byCode.set(r.typed_code, r); });
        Object.keys(draft || {}).forEach(code => {
            const r = byCode.get(code), d = draft[code]; if (!r || !d) return;
            if (d.qty !== null && d.qty !== undefined) r.suggested_approve_qty = Math.round(Number(d.qty)) || 0;
            if (d.note) r.note = d.note;
        });
    }
    function _ocGdhDirty() {                // gọi mỗi khi admin sửa SL Duyệt / ghi chú: cập nhật thanh công cụ + tự lưu nháp sau 1,5 giây
        if (!_ocGdh) return;
        _ocGdhDraftTxt = 'đang chờ lưu nháp...';
        _ocGdhBarSoon();
        clearTimeout(_ocGdhT); _ocGdhT = setTimeout(_ocGdhSaveDraft, 1500);
    }
    async function _ocGdhSaveDraft() {
        if (!_ocGdh || _orderCheckCurrentStore !== _ocGdh.store || !_orderCheckData.length) return;
        const id = _ocGdh.id;
        try {
            const r = await _gdhJson('/api/gom-don-hang/review-draft', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: id, items: _ocGdhCollect()})});
            _ocGdhDraftTxt = 'đã lưu nháp' + (r.saved_at ? ' lúc ' + r.saved_at : '');
        } catch (err) { _ocGdhDraftTxt = 'lưu nháp lỗi: ' + err.message; }
        _ocGdhRenderBar();
    }
    function _ocToast(msg, onClick) { nsToast(msg, 'info', 12000, {title: 'Thông báo', onClick}); }
    async function ocGdhNavBadge() {            // số đơn Chờ duyệt trên menu + báo khi có đơn mới đẩy
        if (CURRENT_ROLE !== 'admin') return;
        try { _ocGdhApplyCounts(await _gdhJson('/api/gom-don-hang/orders?counts_only=1')); } catch (err) { /* không chặn giao diện */ }
    }
    function _ocGdhApplyCounts(j) {             // dùng lại phản hồi /orders đã có, khỏi gọi thêm 1 request
        if (CURRENT_ROLE !== 'admin') return;
        try {
            ocArcBadge(j.archive_pending || 0);
            if (j.archive_pending > 0 && !window._ocArcToasted) { window._ocArcToasted = true; _ocToast(`Có ${j.archive_pending} file Excel lưu trữ chưa tải về máy. Bấm để xem.`, () => { document.querySelector('[data-bs-target="#order-check-pane"]')?.click(); ocGdhTab('files'); }); }
            const n = (j.counts && j.counts.pending) || 0;
            document.querySelectorAll('.oc-nav-badge').forEach(el => { el.textContent = n; el.classList.toggle('d-none', !n); el.title = n + ' đơn gôm đang chờ duyệt'; });
            const last = (j.pending_info && j.pending_info.last_id) || 0;
            let seen = null; try { seen = localStorage.getItem('ocGdhSeenLast'); } catch (err) { /* bỏ qua */ }
            if (seen !== null && last > Number(seen) && n > 0) {
                _ocToast(`Có đơn gôm mới chờ duyệt (${n} đơn chờ). Bấm để xem.`, () => { document.querySelector('[data-bs-target="#order-check-pane"]')?.click(); ocGdhLoad(); });
            }
            try { localStorage.setItem('ocGdhSeenLast', String(Math.max(last, Number(seen || 0)))); } catch (err) { /* bỏ qua */ }
        } catch (err) { /* không chặn giao diện */ }
    }
    function ocGdhTab(t) { _ocGdhTab = t; ocGdhLoad(); }
    function _ocBusy(on, msg) {                 // lớp phủ "đang xử lý" - để mỗi bước chờ máy chủ đều có phản hồi, không bị cảm giác treo
        let el = document.getElementById('ocBusy');
        if (!on) { if (el) el.style.display = 'none'; return; }
        if (!el) {
            el = document.createElement('div'); el.id = 'ocBusy';
            el.style.cssText = 'position:fixed;inset:0;z-index:2100;background:rgba(15,23,42,.35);display:flex;align-items:center;justify-content:center';
            el.innerHTML = '<div style="background:#fff;border-radius:.8rem;padding:1rem 1.4rem;box-shadow:0 10px 30px rgba(0,0,0,.25);display:flex;gap:.7rem;align-items:center"><span class="spinner-border spinner-border-sm text-primary"></span><span id="ocBusyMsg"></span></div>';
            document.body.appendChild(el);
        }
        document.getElementById('ocBusyMsg').textContent = msg || 'Đang xử lý...';
        el.style.display = 'flex';
    }
    function ocGdhTypeFilter(t) {
        _ocGdhType = t;
        document.querySelectorAll('#ocGdhTypes [data-type]').forEach(b => b.classList.toggle('on', b.dataset.type === t));
        ocGdhLoad();
    }
    // ---------------------------------------------------------------------
    // ĐƠN KHẨN TỪ DANH SÁCH KHÁCH HÀNG: bảng thông tin khách của từng dòng (STT, mã đặt, SL đặt, ngày KH yêu cầu,
    // tên KH, số báo giá, loại xe, số khung) - xem ở thẻ đơn (nút "Thông tin khách") và ngay trong khung duyệt.
    // ---------------------------------------------------------------------
    function _urgTableHtml(lines) {
        const e = _gdhEsc;
        if (!lines || !lines.length) return '<div class="text-center text-muted py-3 small">Không có dòng khách nào.</div>';
        return '<table class="table table-sm table-hover mb-0 align-middle" style="font-size:.82rem"><thead class="table-light"><tr>' +
            '<th>STT</th><th>Mã đặt</th><th class="text-end">SL đặt</th><th>Ngày KH yêu cầu</th><th>Tên khách hàng</th><th>Số báo giá</th><th>Loại xe</th><th>Số khung</th></tr></thead><tbody>' +
            lines.map(l => {
                const diff = l.part_code && l.order_code && l.part_code !== l.order_code;
                return `<tr><td class="fw-bold">${e(l.stt)}</td>` +
                    `<td><span class="fw-semibold">${e(l.order_code)}</span>${diff ? `<div class="text-muted" style="font-size:.7rem">KH yêu cầu: ${e(l.part_code)} × ${_gdhFmt(l.qty, 0)}</div>` : ''}</td>` +
                    `<td class="text-end fw-semibold">${_gdhFmt(l.order_qty, 0)}</td><td>${l.request_date ? _gdhDate(l.request_date) : '—'}</td>` +
                    `<td>${e(l.customer_name)}</td><td class="font-monospace">${e(l.quote_no)}</td><td>${e(l.vehicle_type)}</td><td class="font-monospace">${e(l.frame_number)}</td></tr>`;
            }).join('') + '</tbody></table>';
    }
    function _ugmCss() {        // CSS dùng chung cho các khung "chuyên nghiệp" (gôm đơn khẩn, đẩy đơn gôm)
        if (document.getElementById('ugm-css')) return;
        document.head.insertAdjacentHTML('beforeend', `<style id="ugm-css">
    .ugm .modal-content{border:0;border-radius:14px;overflow:hidden;box-shadow:0 20px 60px rgba(15,23,42,.35)}
    .ugm-head{background:linear-gradient(135deg,#b91c1c,#ef4444);color:#fff;border:0;padding:.8rem 1.1rem;align-items:center;gap:.7rem}
    .ugm-head .btn-close{filter:invert(1) grayscale(1) brightness(2);opacity:.85;margin:0 0 0 auto}
    .ugm-ico{width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,.18);display:grid;place-items:center;font-size:1.25rem;flex:none}
    .ugm-ttl{font-weight:800;font-size:1.05rem;line-height:1.2}
    .ugm-sub{font-size:.76rem;opacity:.88}
    .ugm-steps{display:flex;align-items:center;gap:.5rem;padding:.55rem 1.1rem;background:#fef2f2;border-bottom:1px solid #fecaca;font-size:.76rem;font-weight:700;color:#9ca3af}
    .ugm-steps span{display:flex;align-items:center;gap:.35rem}
    .ugm-steps b{width:20px;height:20px;border-radius:50%;background:#e5e7eb;color:#6b7280;display:grid;place-items:center;font-size:.7rem;transition:background .25s,color .25s}
    .ugm-steps i.sep{flex:0 0 28px;height:2px;background:#e5e7eb;border-radius:2px}
    .ugm-steps .on{color:#b91c1c}.ugm-steps .on b{background:#dc2626;color:#fff}
    .ugm-steps .ok{color:#15803d}.ugm-steps .ok b{background:#16a34a;color:#fff}
    .ugm-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem;margin-bottom:.7rem}
    .ugm-stat{border:1px solid #e2e8f0;border-radius:10px;padding:.4rem .75rem;background:#fff}
    .ugm-stat small{display:block;color:#64748b;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
    .ugm-stat strong{display:inline-block;font-size:1.3rem;font-variant-numeric:tabular-nums}
    .ugm-stat.pulse strong{animation:ugmPop .35s}
    @keyframes ugmPop{40%{transform:scale(1.2);color:#dc2626}}
    .ugm-chips{display:flex;flex-wrap:wrap;gap:.35rem}
    .ugm-chip{border:1px solid #cbd5e1;background:#fff;border-radius:999px;padding:.12rem .7rem;font-size:.76rem;font-weight:700;color:#475569;cursor:pointer;transition:background .15s,color .15s}
    .ugm-chip:hover{background:#f1f5f9}
    .ugm-chip.on{background:#dc2626;border-color:#dc2626;color:#fff}
    .ugm-chip em{font-style:normal;opacity:.75;margin-left:.3rem}
    .ugm-search{position:relative;flex:1;max-width:340px;min-width:200px}
    .ugm-search i{position:absolute;left:.6rem;top:50%;transform:translateY(-50%);color:#94a3b8;font-size:.8rem}
    .ugm-search input{padding-left:1.9rem}
    .ugm-wrap{max-height:46vh;overflow:auto;border:1px solid #e2e8f0;border-radius:10px}
    .ugm-t{margin:0;font-size:.82rem}
    .ugm-t th{position:sticky;top:0;z-index:2;background:#f1f5f9;font-size:.7rem;text-transform:uppercase;letter-spacing:.03em;color:#475569;white-space:nowrap;box-shadow:0 1px 0 #e2e8f0}
    .ugm-t td{vertical-align:middle}
    .ugm-t tbody tr{cursor:pointer;transition:background .15s}
    .ugm-t tbody tr:hover{background:#f8fafc}
    .ugm-t tbody tr.sel{background:#fef2f2;box-shadow:inset 3px 0 0 #dc2626}
    .ugm-t tbody tr.off{cursor:not-allowed;opacity:.5}
    .ugm-t tbody tr.gom-out{animation:ugmOut .55s cubic-bezier(.5,0,.75,0) forwards}
    @keyframes ugmOut{to{opacity:0;transform:translateX(70px) scale(.96);background:#fecaca}}
    .ugm-qty{display:inline-flex;align-items:center;border:1px solid #cbd5e1;border-radius:8px;overflow:hidden;background:#fff}
    .ugm-qty button{border:0;background:#f1f5f9;width:24px;height:28px;font-weight:800;color:#475569;line-height:1}
    .ugm-qty button:hover:not(:disabled){background:#fee2e2;color:#b91c1c}
    .ugm-qty input{width:46px;border:0;text-align:center;font-weight:700;font-size:.82rem;padding:0;outline:0;-moz-appearance:textfield;background:transparent}
    .ugm-qty input::-webkit-outer-spin-button,.ugm-qty input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
    .ugm-skel td div{height:12px;border-radius:6px;background:linear-gradient(90deg,#eef2f7 25%,#f8fafc 50%,#eef2f7 75%);background-size:200% 100%;animation:ugmSh 1.1s infinite}
    @keyframes ugmSh{to{background-position:-200% 0}}
    .ugm-empty{padding:2.2rem 1rem;text-align:center;color:#94a3b8}
    .ugm-empty i{font-size:2.2rem;display:block;margin-bottom:.4rem}
    .ugm-confirm,.ugm-done{padding:1.8rem 1rem;text-align:center}
    .ugm-confirm .big{width:64px;height:64px;border-radius:50%;background:#fee2e2;color:#dc2626;display:grid;place-items:center;font-size:1.9rem;margin:0 auto .9rem}
    .ugm-lock{display:inline-block;margin-top:.7rem;background:#fffbeb;border:1px solid #fde68a;color:#92400e;border-radius:8px;padding:.35rem .8rem;font-size:.8rem}
    .ugm-check{width:78px;height:78px;margin:0 auto 1rem;display:block}
    .ugm-check circle{fill:none;stroke:#16a34a;stroke-width:3;stroke-dasharray:158;stroke-dashoffset:158;animation:ugmDraw .6s ease forwards}
    .ugm-check path{fill:none;stroke:#16a34a;stroke-width:4;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:40;stroke-dashoffset:40;animation:ugmDraw .4s .5s ease forwards}
    @keyframes ugmDraw{to{stroke-dashoffset:0}}
    .ugm-pills{display:flex;justify-content:center;flex-wrap:wrap;gap:.5rem;margin-top:.9rem}
    .ugm-pill{background:#f1f5f9;border-radius:999px;padding:.25rem .85rem;font-size:.82rem;font-weight:700}
    .ugm-pill.warn{background:#fef3c7;color:#92400e}
    .ugm-view{animation:ugmIn .25s ease}
    @keyframes ugmIn{from{opacity:0;transform:translateY(8px)}}
    @media (max-width:576px){.ugm-stats{grid-template-columns:repeat(3,1fr);gap:.35rem}.ugm-stat strong{font-size:1.05rem}}
    @media (prefers-reduced-motion:reduce){.ugm *{animation-duration:.01ms!important;animation-delay:0ms!important;transition-duration:.01ms!important}}
    
.ugm .ugm-go{--c:#dc2626;background:var(--c);border-color:var(--c);color:#fff;font-weight:700}
.ugm .ugm-go:hover:not(:disabled){filter:brightness(.92);color:#fff}
.ugm .ugm-go:disabled{opacity:.55;color:#fff;background:var(--c);border-color:var(--c)}
.ugm.t-blue .ugm-go{--c:#2563eb}.ugm.t-violet .ugm-go{--c:#7c3aed}
.ugm.t-blue .ugm-head{background:linear-gradient(135deg,#1d4ed8,#3b82f6)}
.ugm.t-blue .ugm-steps{background:#eff6ff;border-bottom-color:#bfdbfe}
.ugm.t-blue .ugm-steps .on{color:#1d4ed8}.ugm.t-blue .ugm-steps .on b{background:#2563eb}
.ugm.t-blue .ugm-confirm .big{background:#dbeafe;color:#2563eb}.ugm.t-blue .ugm-confirm h5 .hl{color:#2563eb}
.ugm.t-violet .ugm-head{background:linear-gradient(135deg,#6d28d9,#8b5cf6)}
.ugm.t-violet .ugm-steps{background:#f5f3ff;border-bottom-color:#ddd6fe}
.ugm.t-violet .ugm-steps .on{color:#6d28d9}.ugm.t-violet .ugm-steps .on b{background:#7c3aed}
.ugm.t-violet .ugm-confirm .big{background:#ede9fe;color:#7c3aed}.ugm.t-violet .ugm-confirm h5 .hl{color:#7c3aed}
.ugm-confirm h5 .hl{color:#dc2626}
.ugm-type{display:grid;grid-template-columns:minmax(92px,1.1fr) 70px 80px minmax(110px,1.2fr);gap:.5rem;align-items:center;padding:.5rem .8rem;border-bottom:1px solid #eef2f7;font-size:.84rem}
.ugm-type:last-child{border-bottom:0}
.ugm-type.head{background:#f1f5f9;font-size:.68rem;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em}
.ugm-type.zero{opacity:.45}
.ugm-type .nm{font-weight:700;display:flex;align-items:center;gap:.4rem}
.ugm-type .dot{width:10px;height:10px;border-radius:50%;flex:none}
.ugm-type .n{text-align:right;font-variant-numeric:tabular-nums}
.ugm-bar{height:6px;border-radius:6px;background:#e5e7eb;overflow:hidden;margin-top:3px}
.ugm-bar i{display:block;height:100%;border-radius:6px;width:0;transition:width .7s cubic-bezier(.2,.8,.2,1)}
.ugm-box{border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin-bottom:.7rem}
.ugm-warn{display:flex;gap:.6rem;border-radius:10px;padding:.55rem .8rem;font-size:.82rem;margin-bottom:.6rem;border:1px solid}
.ugm-warn>i{font-size:1.05rem;margin-top:.05rem}
.ugm-warn.w{background:#fffbeb;border-color:#fde68a;color:#92400e}
.ugm-warn.d{background:#fef2f2;border-color:#fecaca;color:#991b1b}
.ugm-warn.m{background:#f8fafc;border-color:#e2e8f0;color:#64748b}
.ugm-warn ul{margin:.3rem 0 0;padding-left:1.1rem}
@media (max-width:576px){.ugm-type{grid-template-columns:1fr 54px 64px 1fr;padding:.45rem .5rem;font-size:.78rem}}
</style>`);
    }
    function _ugmSetStep(box, n) {
        box.querySelectorAll('span').forEach(sp => {
            const s = +sp.dataset.s;
            sp.className = s < n ? 'ok' : s === n ? 'on' : '';
            sp.firstChild.innerHTML = s < n ? '<i class="bi bi-check-lg"></i>' : s;
        });
    }
    // ---------- Gôm đơn khẩn từ danh sách khách hàng (bảng chọn mã khách) ----------
    const U_esc = _gdhEsc, U_date = _gdhDate, U_store = () => _gdhStore(), U_isAdmin = () => CURRENT_ROLE === 'admin';
    const U_get = url => _gdhJson(url);
    const U_post = (url, body) => _gdhJson(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const U_warn = m => showToast('Gôm đơn khẩn', m, 'warning');
    const U_onSent = () => gdhOrdersBadge();
    const U_onClosed = () => {      // đóng bảng xong: sang tab Đơn đã đẩy để thấy ngay đơn khẩn vừa gửi (nếu đang có thay đổi chưa lưu thì chỉ làm mới)
        if (!_gdhDirty) gdhSwitchView('orders');
        else if (!document.getElementById('gdhOrdersView').classList.contains('d-none')) gdhOrdersLoad();
    };
    /* ===== Gôm đơn khẩn: chọn mã khách -> xác nhận -> gửi thẳng admin duyệt (khối dùng chung) ===== */
    let _GDH_URG = null;
    const gdhUrgEl = id => document.getElementById('gdhUrg' + id);
    const gdhUrgNum = n => Number(n || 0).toLocaleString('vi-VN');

    function gdhUrgEnsure() {
      if (gdhUrgEl('Modal')) return;
      _ugmCss();
      document.body.insertAdjacentHTML('beforeend', `<div class="modal fade ugm" id="gdhUrgModal" data-bs-backdrop="static" tabindex="-1"><div class="modal-dialog modal-xl modal-dialog-scrollable modal-dialog-centered"><div class="modal-content">
      <div class="modal-header ugm-head">
        <div class="ugm-ico"><i class="bi bi-lightning-charge-fill"></i></div>
        <div><div class="ugm-ttl">Gôm đơn khẩn <span class="badge bg-light text-danger ms-1 d-none" id="gdhUrgStore"></span></div><div class="ugm-sub">Chọn mã khách chưa đặt và gửi thẳng admin duyệt</div></div>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Đóng"></button>
      </div>
      <div class="ugm-steps" id="gdhUrgSteps"><span data-s="1" class="on"><b>1</b>Chọn mã khách</span><i class="sep"></i><span data-s="2"><b>2</b>Xác nhận</span><i class="sep"></i><span data-s="3"><b>3</b>Gửi admin</span></div>
      <div class="modal-body p-3">
        <div id="gdhUrgMain" class="ugm-view">
          <div class="small text-muted mb-2">Chỉ hiện các mã khách <b>Chưa đặt</b>, <b>không xin nội bộ</b> và chưa gửi khẩn. Đơn khẩn được gửi <b>thẳng cho admin</b> ở trạng thái Chờ duyệt.</div>
          <div class="ugm-stats">
            <div class="ugm-stat"><small>Dòng đã chọn</small><strong id="gdhUrgStLines">0</strong></div>
            <div class="ugm-stat"><small>Số khách</small><strong id="gdhUrgStCust">0</strong></div>
            <div class="ugm-stat"><small>Tổng SL</small><strong id="gdhUrgStQty">0</strong></div>
          </div>
          <div class="d-flex flex-wrap gap-2 align-items-center mb-2">
            <div class="ugm-search"><i class="bi bi-search"></i><input type="text" id="gdhUrgSearch" class="form-control form-control-sm" placeholder="Lọc theo khách, mã, số báo giá, số khung..." oninput="gdhUrgRender()"></div>
            <div class="ugm-chips" id="gdhUrgChips"></div>
            <span class="ms-auto small text-muted" id="gdhUrgHint"></span>
          </div>
          <div class="ugm-wrap"><table class="table ugm-t"><thead><tr>
            <th style="width:34px"><input type="checkbox" class="form-check-input" id="gdhUrgAll" onchange="gdhUrgSelectAll(this.checked)" title="Chọn / bỏ chọn tất cả dòng đang lọc"></th>
            <th>STT</th><th>Mã đặt</th><th>Tên hàng</th><th class="text-center">SL đặt</th><th>Ngày KH yêu cầu</th><th>Khách hàng</th><th>Số báo giá</th><th>Loại xe</th><th>Số khung</th>
          </tr></thead><tbody id="gdhUrgBody"></tbody></table></div>
          <input type="text" id="gdhUrgNote" class="form-control form-control-sm mt-2" maxlength="500" placeholder="Ghi chú cho admin (không bắt buộc)" onkeydown="if(event.key==='Enter')gdhUrgAsk()">
          <div class="alert alert-danger py-2 mt-2 mb-0 d-none" id="gdhUrgErr"></div>
          <div class="small text-muted mt-2">Bấm vào dòng để chọn · giữ <b>Shift</b> + bấm để chọn cả khoảng · sửa SL bằng nút − / +</div>
        </div>
        <div id="gdhUrgConfirm" class="ugm-confirm ugm-view d-none">
          <div class="big"><i class="bi bi-send-exclamation-fill"></i></div>
          <h5 class="fw-bold mb-2" id="gdhUrgCfText"></h5>
          <div class="small" id="gdhUrgCfNote"></div>
          <div class="ugm-lock"><i class="bi bi-lock-fill me-1"></i>Sau khi gửi, đơn bị khoá; chỉ thu hồi được khi admin chưa lấy về duyệt.</div>
        </div>
        <div id="gdhUrgDone" class="ugm-done ugm-view d-none"></div>
      </div>
      <div class="modal-footer py-2" id="gdhUrgFootList">
        <button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">Đóng</button>
        <button type="button" class="btn btn-danger btn-sm fw-bold px-3" id="gdhUrgBtnAsk" onclick="gdhUrgAsk()"><i class="bi bi-send-fill me-1"></i>Gửi admin duyệt <span class="badge bg-light text-danger ms-1" id="gdhUrgAskN">0</span></button>
      </div>
      <div class="modal-footer py-2 d-none" id="gdhUrgFootConfirm">
        <button type="button" class="btn btn-outline-secondary btn-sm" id="gdhUrgBtnBack" onclick="gdhUrgView('list')"><i class="bi bi-arrow-left me-1"></i>Quay lại chỉnh</button>
        <button type="button" class="btn btn-danger btn-sm fw-bold px-3" id="gdhUrgBtnSend" onclick="gdhUrgSend()"><i class="bi bi-send-fill me-1"></i>Xác nhận gửi</button>
      </div>
      <div class="modal-footer py-2 d-none" id="gdhUrgFootDone">
        <button type="button" class="btn btn-success btn-sm fw-bold px-4" data-bs-dismiss="modal"><i class="bi bi-check2 me-1"></i>Hoàn tất</button>
      </div>
    </div></div></div>`);
    }

    function gdhUrgStep(n) {
      gdhUrgEl('Steps').querySelectorAll('span').forEach(sp => {
        const s = +sp.dataset.s;
        sp.className = s < n ? 'ok' : s === n ? 'on' : '';
        sp.firstChild.innerHTML = s < n ? '<i class="bi bi-check-lg"></i>' : s;
      });
    }
    function gdhUrgView(v) {          // list | confirm | done
      const show = (id, on) => gdhUrgEl(id).classList.toggle('d-none', !on);
      show('Main', v === 'list'); show('Confirm', v === 'confirm'); show('Done', v === 'done');
      show('FootList', v === 'list'); show('FootConfirm', v === 'confirm'); show('FootDone', v === 'done');
      gdhUrgStep(v === 'list' ? 1 : v === 'confirm' ? 2 : 4);
    }
    function gdhUrgErr(m) { const e = gdhUrgEl('Err'); e.textContent = m || ''; e.classList.toggle('d-none', !m); }
    function gdhUrgSkel() {
      gdhUrgEl('Body').innerHTML = Array.from({ length: 6 }, () => '<tr class="ugm-skel">' + Array.from({ length: 10 }, () => '<td><div></div></td>').join('') + '</tr>').join('');
    }

    async function gdhUrgOpen() {
      if (U_isAdmin() && !U_store()) { U_warn('Admin: hãy chọn 1 chi nhánh ở ô chọn chi nhánh rồi bấm lại Gôm đơn khẩn.'); return; }
      gdhUrgEnsure();
      _GDH_URG = { rows: [], by: {}, sel: new Set(), qty: {}, store: U_store(), view: 'all', last: null, shown: [], busy: false, sent: false, st: {} };
      const st = gdhUrgEl('Store'); st.textContent = U_isAdmin() ? _GDH_URG.store : ''; st.classList.toggle('d-none', !U_isAdmin());
      gdhUrgEl('Note').value = ''; gdhUrgEl('Search').value = ''; gdhUrgErr('');
      gdhUrgView('list'); gdhUrgSkel(); gdhUrgCount();
      const m = gdhUrgEl('Modal');
      m.addEventListener('hidden.bs.modal', () => { if (_GDH_URG && _GDH_URG.sent) U_onClosed(); }, { once: true });
      bootstrap.Modal.getOrCreateInstance(m).show();
      try { await gdhUrgLoad(false); }
      catch (e) { gdhUrgEl('Body').innerHTML = `<tr><td colspan="10"><div class="ugm-empty text-danger"><i class="bi bi-exclamation-triangle"></i>${U_esc(e.message)}</div></td></tr>`; }
    }
    async function gdhUrgLoad(keep) {        // keep = true: nạp lại sau lỗi (dòng bị người khác gửi/đổi trạng thái), giữ lựa chọn còn hợp lệ
      const S = _GDH_URG;
      const j = await U_get('/api/gom-don-hang/urgent/candidates?' + new URLSearchParams(S.store ? { store: S.store } : {}));
      if (keep) S.sel = new Set([...S.sel].filter(id => j.data.some(r => r.id === id)));
      j.data.forEach(r => { if (!keep || !(r.id in S.qty)) S.qty[r.id] = r.qty; });
      S.rows = j.data; S.by = Object.fromEntries(j.data.map(r => [r.id, r]));
      gdhUrgRender();
    }

    function gdhUrgFiltered() {
      const S = _GDH_URG, q = gdhUrgEl('Search').value.trim().toLowerCase(), v = S.view;
      return S.rows.filter(r => (!q || [r.part_code, r.order_code, r.part_name, r.customer_name, r.quote_no, r.vehicle_type, r.frame_number, r.stt].join(' ').toLowerCase().includes(q))
        && (v === 'all' || (v === 'sel' && S.sel.has(r.id)) || (v === 'unsel' && !r.skip && !S.sel.has(r.id)) || (v === 'diff' && r.order_code !== r.part_code) || (v === 'skip' && r.skip)));
    }
    function gdhUrgOQ(r) {            // mã đặt khác mã khách yêu cầu (mã cha / thay thế): ước lượng theo tỉ lệ, số chính xác do máy chủ tính khi gửi
      const q = Number(_GDH_URG.qty[r.id]) || 0;
      return r.order_code === r.part_code ? q : Math.max(1, Math.ceil(q * r.order_qty / r.qty - 1e-9));
    }
    function gdhUrgRender() {
      const S = _GDH_URG, rows = gdhUrgFiltered(), MAXR = 400, shown = rows.slice(0, MAXR);
      S.shown = shown.map(r => r.id);
      gdhUrgEl('Body').innerHTML = shown.length ? shown.map(r => {
        const diff = r.order_code !== r.part_code, off = !!r.skip, on = S.sel.has(r.id);
        return `<tr class="${off ? 'off' : on ? 'sel' : ''}" data-id="${r.id}" onclick="gdhUrgRow(event,${r.id})"><td><input type="checkbox" class="form-check-input" ${off ? 'disabled' : ''} ${on ? 'checked' : ''}></td>`
          + `<td class="fw-bold">${U_esc(r.stt)}</td>`
          + `<td><span class="fw-semibold">${U_esc(r.order_code)}</span>${diff ? `<div class="text-muted" style="font-size:.7rem">khách yêu cầu: ${U_esc(r.part_code)}</div>` : ''}${r.skip ? `<div class="text-danger" style="font-size:.7rem">${U_esc(r.skip)}</div>` : ''}${r.locked_no_rep ? '<div class="text-warning-emphasis" style="font-size:.7rem">Mã đang bị khoá</div>' : ''}</td>`
          + `<td>${U_esc(r.part_name)}</td>`
          + `<td class="text-center"><div class="ugm-qty"><button type="button" ${off ? 'disabled' : ''} onclick="gdhUrgBump(${r.id},-1)">−</button><input type="number" min="1" id="gdhUrgq${r.id}" value="${U_esc(S.qty[r.id])}" ${off ? 'disabled' : ''} onchange="gdhUrgQty(${r.id},this.value)"><button type="button" ${off ? 'disabled' : ''} onclick="gdhUrgBump(${r.id},1)">+</button></div><div class="text-muted" style="font-size:.7rem" id="gdhUrgo${r.id}">${diff ? 'đặt ~' + gdhUrgOQ(r) : ''}</div></td>`
          + `<td>${U_date(r.request_date) || '—'}</td><td>${U_esc(r.customer_name)}</td><td class="font-monospace">${U_esc(r.quote_no)}</td><td>${U_esc(r.vehicle_type)}</td><td class="font-monospace">${U_esc(r.frame_number)}</td></tr>`;
      }).join('') : `<tr><td colspan="10"><div class="ugm-empty"><i class="bi bi-inbox"></i>${S.rows.length ? 'Không có dòng nào khớp bộ lọc.' : 'Không có mã nào đủ điều kiện gôm đơn khẩn.'}</div></td></tr>`;
      gdhUrgEl('Hint').textContent = rows.length > MAXR ? `Đang hiện ${MAXR}/${rows.length} dòng — dùng ô lọc để thu hẹp.` : '';
      gdhUrgCount();
    }

    function gdhUrgMark(id, on) {
      const S = _GDH_URG, r = S.by[id]; if (!r || r.skip) return;
      if (on) S.sel.add(id); else S.sel.delete(id);
      const tr = gdhUrgEl('Body').querySelector(`tr[data-id="${id}"]`);
      if (tr) { tr.classList.toggle('sel', on); const c = tr.querySelector('input[type=checkbox]'); if (c) c.checked = on; }
    }
    function gdhUrgRow(ev, id) {
      if (ev.target.closest('.ugm-qty')) return;
      const S = _GDH_URG, r = S.by[id]; if (!r || r.skip) return;
      if (ev.shiftKey) window.getSelection().removeAllRanges();
      const on = !S.sel.has(id), a = S.shown.indexOf(S.last), b = S.shown.indexOf(id);
      if (ev.shiftKey && a >= 0 && b >= 0 && S.last !== id) S.shown.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(x => gdhUrgMark(x, on));
      else gdhUrgMark(id, on);
      S.last = id; gdhUrgCount();
    }
    function gdhUrgSelectAll(on) { gdhUrgFiltered().forEach(r => { if (!r.skip) { if (on) _GDH_URG.sel.add(r.id); else _GDH_URG.sel.delete(r.id); } }); gdhUrgRender(); }
    function gdhUrgChip(k) { _GDH_URG.view = k; gdhUrgRender(); }
    function gdhUrgQty(id, v) {
      const S = _GDH_URG, n = Math.max(1, Math.ceil(Number(v) || 1)); S.qty[id] = n;
      const inp = gdhUrgEl('q' + id); if (inp) inp.value = n;
      const r = S.by[id], el = gdhUrgEl('o' + id); if (r && el && r.order_code !== r.part_code) el.textContent = 'đặt ~' + gdhUrgOQ(r);
      if (r && !r.skip && !S.sel.has(id)) gdhUrgMark(id, true);          // sửa SL = có ý định đặt dòng đó
      gdhUrgCount();
    }
    function gdhUrgBump(id, d) { gdhUrgQty(id, (Number(_GDH_URG.qty[id]) || 1) + d); }

    function gdhUrgCount() {
      const S = _GDH_URG, sel = S.rows.filter(r => S.sel.has(r.id));
      const qty = sel.reduce((a, r) => a + (Number(S.qty[r.id]) || 0), 0);
      const cust = new Set(sel.map(r => String(r.customer_name || '').trim().toLowerCase())).size;
      const set = (id, key, html, val) => {
        const el = gdhUrgEl(id); el.innerHTML = html;
        if (S.st[key] !== undefined && S.st[key] !== val) { const c = el.closest('.ugm-stat'); c.classList.remove('pulse'); void c.offsetWidth; c.classList.add('pulse'); }
        S.st[key] = val;
      };
      set('StLines', 'l', `${sel.length}<span class="text-muted fs-6 fw-semibold"> / ${S.rows.length}</span>`, sel.length);
      set('StCust', 'c', String(cust), cust);
      set('StQty', 'q', gdhUrgNum(qty), qty);
      const diff = S.rows.filter(r => r.order_code !== r.part_code).length, skip = S.rows.filter(r => r.skip).length;
      const defs = [['all', 'Tất cả', S.rows.length], ['sel', 'Đã chọn', sel.length], ['unsel', 'Chưa chọn', S.rows.filter(r => !r.skip && !S.sel.has(r.id)).length]];
      if (diff) defs.push(['diff', 'Đã quy mã', diff]);
      if (skip) defs.push(['skip', 'Không đặt được', skip]);
      gdhUrgEl('Chips').innerHTML = defs.map(([k, t, n]) => `<button type="button" class="ugm-chip ${S.view === k ? 'on' : ''}" onclick="gdhUrgChip('${k}')">${t}<em>${n}</em></button>`).join('');
      const f = gdhUrgFiltered().filter(r => !r.skip), n = f.filter(r => S.sel.has(r.id)).length, all = gdhUrgEl('All');
      all.checked = f.length > 0 && n === f.length; all.indeterminate = n > 0 && n < f.length;
      gdhUrgEl('AskN').textContent = sel.length; gdhUrgEl('BtnAsk').disabled = !sel.length;
    }

    function gdhUrgAsk() {
      const S = _GDH_URG; if (!S || !S.sel.size) return;
      const sel = S.rows.filter(r => S.sel.has(r.id)), qty = sel.reduce((a, r) => a + (Number(S.qty[r.id]) || 0), 0);
      const cust = new Set(sel.map(r => String(r.customer_name || '').trim().toLowerCase())).size, note = gdhUrgEl('Note').value.trim();
      gdhUrgEl('CfText').innerHTML = `Gửi <span class="text-danger">${sel.length}</span> dòng khách (${cust} khách, tổng SL ${gdhUrgNum(qty)})<br>thành 1 đơn KHẨN cho admin duyệt?`;
      gdhUrgEl('CfNote').innerHTML = note ? `Ghi chú cho admin: “${U_esc(note)}”` : '<span class="text-muted">Không có ghi chú cho admin.</span>';
      gdhUrgErr(''); gdhUrgView('confirm');
    }
    async function gdhUrgSend() {
      const S = _GDH_URG; if (S.busy || !S.sel.size) return;
      S.busy = true;
      const btn = gdhUrgEl('BtnSend'), back = gdhUrgEl('BtnBack');
      btn.disabled = back.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Đang gửi...';
      const items = [...S.sel].map(id => ({ id, qty: S.qty[id] }));
      try {
        const j = await U_post('/api/gom-don-hang/urgent/create', { store: S.store, items, note: gdhUrgEl('Note').value.trim() });
        S.sent = true;
        try { U_onSent(j); } catch (_) { /* không để lỗi làm mới giao diện nền làm hỏng thông báo gửi thành công */ }
        gdhUrgView('list'); gdhUrgStep(3);
        const rows = [...gdhUrgEl('Body').querySelectorAll('tr.sel')];          // hiệu ứng "gôm": các dòng đã chọn trượt đi lần lượt
        rows.forEach((tr, i) => { tr.style.animationDelay = Math.min(i, 14) * 35 + 'ms'; tr.classList.add('gom-out'); });
        if (rows.length) await new Promise(res => setTimeout(res, Math.min(rows.length, 15) * 35 + 600));
        gdhUrgDone(j);
      } catch (e) {
        gdhUrgView('list'); gdhUrgErr(e.message);
        try { await gdhUrgLoad(true); } catch (_) { /* giữ nguyên danh sách */ }
      } finally {
        S.busy = false; btn.disabled = back.disabled = false; btn.innerHTML = '<i class="bi bi-send-fill me-1"></i>Xác nhận gửi';
      }
    }
    function gdhUrgDone(j) {
      const sk = (j.skipped || []).length;
      gdhUrgEl('Done').innerHTML = `<svg class="ugm-check" viewBox="0 0 52 52"><circle cx="26" cy="26" r="25"/><path d="M14 27l8 8 16-17"/></svg>`
        + `<h5 class="fw-bold mb-1">Đã gửi đơn khẩn #${U_esc(j.batch_id)} cho admin</h5><div class="text-muted">Đơn đang ở trạng thái <b>Chờ duyệt</b>.</div>`
        + `<div class="ugm-pills"><span class="ugm-pill">${gdhUrgNum(j.lines)} dòng khách</span><span class="ugm-pill">${gdhUrgNum(j.parts)} mã đặt</span>${sk ? `<span class="ugm-pill warn">Bỏ ${sk} mã không được đặt</span>` : ''}</div>`;
      gdhUrgView('done');
    }

    async function gdhUrgentDetail(id) {
        _ocBusy(true, 'Đang tải thông tin khách...');
        let j;
        try { j = await _gdhJson('/api/gom-don-hang/urgent/lines?batch_id=' + id); }
        catch (err) { _ocBusy(false); alert(err.message); return; }
        _ocBusy(false);
        let el = document.getElementById('urgDetailOv');
        if (el) el.remove();
        el = document.createElement('div'); el.id = 'urgDetailOv';
        el.style.cssText = 'position:fixed;inset:0;z-index:2050;background:rgba(15,23,42,.45);display:flex;align-items:center;justify-content:center;padding:1rem';
        el.onclick = ev => { if (ev.target === el) el.remove(); };
        el.innerHTML = '<div style="background:#fff;border-radius:.8rem;box-shadow:0 10px 30px rgba(0,0,0,.3);width:min(1100px,100%);max-height:88vh;display:flex;flex-direction:column">' +
            `<div class="d-flex align-items-center gap-2 px-3 py-2 border-bottom"><div class="flex-grow-1 fw-semibold"><i class="bi bi-lightning-charge-fill text-danger me-1"></i>Đơn khẩn #${j.batch.id} · ${_gdhEsc(j.batch.store)} · ${j.data.length} dòng khách` +
            `<span class="ms-2 small text-muted fw-normal">${_gdhEsc(j.batch.status_label)}${j.batch.submit_note ? ' · “' + _gdhEsc(j.batch.submit_note) + '”' : ''}</span></div>` +
            '<button type="button" class="btn btn-outline-secondary btn-sm" onclick="document.getElementById(\'urgDetailOv\').remove()">Đóng</button></div>' +
            `<div style="overflow:auto">${_urgTableHtml(j.data)}</div></div>`;
        document.body.appendChild(el);
    }
    let _ocUrg = {id: null, lines: [], open: true, loading: false};
    async function _ocUrgFetch(id) {              // khôi phục sau khi tải lại trang (localStorage chỉ giữ id đơn đang duyệt)
        if (_ocUrg.loading) return;
        _ocUrg.loading = true;
        try {
            const j = await _gdhJson('/api/gom-don-hang/urgent/lines?batch_id=' + id);
            _ocUrg = {id: id, lines: j.data, open: true, loading: false};
        } catch (err) { _ocUrg = {id: id, lines: [], open: true, loading: false}; }
        _ocGdhRenderBar();
    }
    function _ocUrgPanel() {
        if (!_ocGdh || _ocGdh.kind !== 'urgent') return '';
        if (_ocUrg.id !== _ocGdh.id) { _ocUrgFetch(_ocGdh.id); return '<div class="small text-muted mt-2">Đang tải thông tin khách của đơn khẩn...</div>'; }
        return `<details class="mt-2" ${_ocUrg.open ? 'open' : ''} ontoggle="_ocUrg.open = this.open"><summary class="small fw-semibold text-danger" style="cursor:pointer">` +
            `<i class="bi bi-people me-1"></i>Thông tin khách của đơn khẩn · ${_ocUrg.lines.length} dòng</summary>` +
            `<div class="border rounded mt-1" style="max-height:220px;overflow:auto">${_urgTableHtml(_ocUrg.lines)}</div></details>`;
    }
    // ===== Thẻ đơn gôm kiểu mới (dùng chung cho admin + cửa hàng) =====
    const _NS_TYPE_KEY = {'Khẩn': 'k', 'Định kỳ': 'd', 'Đơn 26': 'n'};
    function nsOcMenu(btn, ev) {
        ev.stopPropagation();
        const m = btn.nextElementSibling, was = !m.hidden;
        document.querySelectorAll('.ns-menu').forEach(x => x.hidden = true);
        m.hidden = was;
    }
    window.nsOcMenu = nsOcMenu;
    document.addEventListener('click', () => document.querySelectorAll('.ns-menu').forEach(x => x.hidden = true));
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape') document.querySelectorAll('.ns-menu').forEach(x => x.hidden = true); });
    function _nsOrderCardHtml(o, mode) {
        const e = _gdhEsc, F = n => _gdhFmt(n, 0), list = mode === 'list', isAd = CURRENT_ROLE === 'admin', store = mode === 'store' || (list && !isAd);
        const rs = o.result, sub = o.submitted_sum;
        const mine = o.status === 'reviewing' && o.claimed_by_me;
        const open = ['pending', 'reviewing'].includes(o.status);
        const urgent = open && o.urgent_parts > 0;
        const types = Object.keys(o.by_type || {}).filter(t => o.by_type[t] && o.by_type[t].parts > 0);
        const key = types.length === 1 ? (_NS_TYPE_KEY[types[0]] || 'd') : 'd';
        const badges = types.map(t => `<span class="ns-b ns-b-${_NS_TYPE_KEY[t] || 'd'}">${e(t)}${types.length > 1 ? ' ' + F(o.by_type[t].parts) + ' mã' : ''}</span>`).join(' ');
        const when = e(String(o.submitted_at || '').slice(0, 16));
        const title = `${badges}<span class="ns-dot">·</span>${e(o.store)}<span class="ns-dot">·</span><span class="ns-when">${when}</span>` +
            (o.kind === 'urgent' ? ' <span class="ns-b ns-b-kh">Từ danh sách KH</span>' : '') +
            ((store && !list) ? '' : ((list || ['pending', 'reviewing'].includes(o.status)) ? ' ' + _gdhStBadge(o.status, o.status_label) : ''));
        const apAt = String(o.approved_at || '');
        const apTime = apAt.slice(0, 10) === String(o.submitted_at || '').slice(0, 10) ? apAt.slice(11) : apAt;
        const parts = [];
        if (o.status === 'reviewing') parts.push(`${e(o.claimed_by)} đang duyệt từ ${e(o.claimed_at)}`);
        else {
            if (!store) parts.push(`Đẩy bởi ${e(o.submitted_by)}${o.status === 'pending' ? ' lúc ' + e(o.submitted_at) : ''}`);
            if (o.approved_by) parts.push(`Duyệt bởi ${e(o.approved_by)} lúc ${e(apTime)}`);
            if (list && o.ordered_at) parts.push(`Đặt ${e(o.ordered_at)}`); else if (list && o.viewed_at) parts.push(`Xem ${e(o.viewed_at)}`);
        }
        const notes = (o.submit_note ? `<div class="ns-note ns-note-n" title="${e(o.submit_note)}">“${e(o.submit_note)}”</div>` : '') +
            (o.review_note ? `<div class="ns-note">${store ? 'Admin' : 'Ghi chú của admin'}: “${e(o.review_note)}”</div>` : '');
        let wait = '';
        if (open && o.waiting_min !== null && o.waiting_min !== undefined) {
            const c = o.waiting_min > 1440 ? 'ns-late' : '';
            wait = `<span class="${c}">Chờ ${_ocFmtWait(o.waiting_min)}</span>`;
        }
        let stats = '';
        if (rs) {
            const hasQ = rs.sent_qty != null && rs.approved_qty != null;
            const pct = hasQ && rs.sent_qty ? Math.round((rs.sent_qty - rs.approved_qty) / rs.sent_qty * 100) : 0;
            const dp = rs.total ? Math.round(rs.approved_parts / rs.total * 100) : 0;
            stats = (hasQ ? `<div class="ns-num"><s>${F(rs.sent_qty)}</s> → ${F(rs.approved_qty)}${pct > 0 ? `<em>−${pct}%</em>` : (pct < 0 ? `<em class="up">+${-pct}%</em>` : '')}</div>` : '') +
                `<div class="ns-prog" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${dp}"><div style="width:${dp}%"></div></div>` +
                `<div class="ns-meta"><span>Duyệt ${F(rs.approved_parts)}/${F(rs.total)} mã</span>${rs.zeroed ? `<span class="ns-zero">Về 0: ${F(rs.zeroed)}</span>` : ''}${rs.reduced ? `<span>Giảm SL ${F(rs.reduced)} mã</span>` : ''}${rs.increased ? `<span>Tăng SL ${rs.increased}</span>` : ''}</div>`;
        } else if (sub) {
            stats = `<div class="ns-num">${F(sub.parts)} mã</div><div class="ns-meta"><span>SL ${F(sub.qty)}</span>${list && sub.amount != null ? `<span>${F(sub.amount)} đ</span>` : ''}${wait}</div>`;
        }
        const btn = (cls, icon, text, fn) => `<button type="button" class="ns-btn ${cls}" onclick="${fn}"><i class="bi ${icon}"></i> ${text}</button>`;
        let acts = '', menu = '';
        const mi = (fn, text, cls) => `<button type="button" role="menuitem"${cls ? ' class="' + cls + '"' : ''} onclick="${fn}">${text}</button>`;
        if (list) {
            const done = ['approved', 'viewed', 'ordered'].includes(o.status);
            if (isAd && o.status === 'pending') acts += btn('ns-pri', 'bi-box-arrow-in-down', 'Lấy về duyệt', `gdhClaim(${o.id})`);
            else if (isAd && o.status === 'reviewing' && o.claimed_by_me) acts += btn('ns-pri', 'bi-pencil-square', 'Mở để duyệt', `gdhOpenBatch('${e(o.store)}', ${o.id})`);
            if (o.status !== 'pending' && (isAd || done)) acts += btn('', 'bi-eye', 'Xem kết quả', `gdhOpenCompare(${o.id})`);
            if (!isAd && done) acts += btn(o.status === 'ordered' ? '' : 'ns-ok', 'bi-download', o.status === 'ordered' ? 'Tải lại file đặt hàng' : 'Tải đơn về', `gdhDownloadOrder(${o.id})`);
            if (o.kind === 'urgent') menu += mi(`gdhUrgentDetail(${o.id})`, 'Thông tin khách');
            if (isAd && (o.status === 'pending' || (o.status === 'reviewing' && o.claimed_by_me))) menu += mi(`gdhReject(${o.id}, this)`, 'Từ chối', 'ns-danger');
            if (!isAd && o.status === 'pending') menu += mi(`gdhRecall(${o.id})`, 'Thu hồi', 'ns-danger');
        } else if (store) {
            acts = btn('', 'bi-eye', 'Xem kết quả', `gdhOpenCompare(${o.id})`) + btn('ns-ok', 'bi-download', 'Tải đơn về', `gdhDownloadOrder(${o.id})`);
        } else {
            if (o.status === 'pending') acts += btn('ns-pri', 'bi-box-arrow-in-down', 'Lấy về duyệt', `ocGdhClaim(${o.id})`);
            else if (mine) acts += btn('ns-pri', 'bi-pencil-square', 'Mở để duyệt', `ocGdhOpen(${o.id})`);
            if (o.status !== 'pending') acts += btn('', 'bi-eye', 'Xem kết quả', `gdhOpenCompare(${o.id})`);
            if (o.kind === 'urgent') menu += `<button type="button" role="menuitem" onclick="gdhUrgentDetail(${o.id})">Thông tin khách</button>`;
        }
        const more = menu ? `<span class="ns-mw"><button type="button" class="ns-btn ns-more" aria-label="Thêm thao tác" aria-haspopup="true" onclick="nsOcMenu(this,event)">⋯</button><div class="ns-menu" role="menu" hidden>${menu}</div></span>` : '';
        return `<div class="ns-row ns-t-${key}${urgent ? ' ns-urgent' : ''}${mine ? ' ns-mine' : ''}"><div class="ns-main"><div class="ns-title">${title}</div>` +
            (parts.length ? `<div class="ns-sub">${parts.join(' · ')}</div>` : '') + notes + `</div><div class="ns-stats">${stats}</div><div class="ns-act">${acts}${more}</div></div>`;
    }
    function _ocGdhCardHtml(o) { return _nsOrderCardHtml(o, 'admin'); }
    async function _ocGdhRejBadge() {          // huy hiệu tab "Đã từ chối" = số đơn đang chờ chi nhánh sửa
        try {
            const j = await _gdhRejFetch(document.getElementById('ocGdhStore')?.value || '', true);
            const s = document.querySelector('#ocGdhTabs [data-tab="rejected"] .n'); if (s) s.textContent = j.open || 0;
        } catch (err) { /* không chặn giao diện */ }
    }
    function _ocGdhRejCardHtml(r) {
        const e = _gdhEsc;
        const open = (r.waiting && r.batch_id) ? `<button type="button" class="btn btn-outline-primary btn-sm" onclick="document.getElementById('gdh-tab')?.click(); gdhOpenBatch('${e(r.store)}', ${r.batch_id});"><i class="bi bi-eye me-1"></i>Xem đơn</button>` : '';
        return `<div class="oc-gcard"><div class="oc-g-main">` +
            `<div><span class="oc-g-store">${e(r.name)}</span>${r.kind === 'urgent' ? '<span class="badge bg-danger-subtle text-danger border border-danger-subtle me-1">Từ danh sách KH</span>' : ''}<span class="text-muted small me-2">#${r.batch_id || ''}</span><span class="badge bg-danger me-1">Bị từ chối</span>${_gdhRejStateBadge(r.state)}</div>` +
            `<div class="oc-g-meta">${r.parts != null ? `<b>${_gdhFmt(r.parts, 0)}</b> mã · SL <b>${_gdhFmt(r.qty, 0)}</b> · ` : ''}đẩy bởi ${e(r.submitted_by)} lúc ${e(r.submitted_at)}</div>` +
            `<div class="small text-danger">Từ chối bởi <b>${e(r.rejected_by)}</b> lúc ${e(r.rejected_at)} · Lý do: <i>${e(r.reason)}</i></div>` +
            `</div><div class="d-flex flex-wrap gap-1">${open}</div></div>`;
    }
    async function ocGdhRejLoad() {
        const list = document.getElementById('ocGdhList'); if (!list) return;
        try {
            const j = await _gdhRejFetch(document.getElementById('ocGdhStore')?.value || '');
            const s = document.querySelector('#ocGdhTabs [data-tab="rejected"] .n'); if (s) s.textContent = j.open || 0;
            list.innerHTML = j.data.length ? j.data.map(_ocGdhRejCardHtml).join('')
                : '<div class="text-center text-muted py-4"><i class="bi bi-inbox fs-3 d-block mb-1"></i>Chưa có đơn nào bị từ chối.</div>';
        } catch (err) { list.innerHTML = `<div class="text-center text-danger py-3">${_gdhEsc(err.message)}</div>`; }
    }
    async function ocGdhLoad() {
        const list = document.getElementById('ocGdhList'); if (!list) return;
        const isFiles = _ocGdhTab === 'files';
        document.getElementById('ocArcPanel')?.classList.toggle('d-none', !isFiles);
        list.classList.toggle('d-none', isFiles);
        document.getElementById('ocGdhTypes')?.classList.toggle('d-none', isFiles);
        document.querySelectorAll('#ocGdhTabs [data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === _ocGdhTab));
        if (isFiles) { ocArcLoad(); return; }
        const isRej = _ocGdhTab === 'rejected';
        document.getElementById('ocGdhTypes')?.classList.toggle('d-none', isRej);      // lọc loại đơn không áp dụng cho lịch sử từ chối
        if (isRej) { await ocGdhRejLoad(); return; }
        const store = document.getElementById('ocGdhStore')?.value || '';
        const p = new URLSearchParams({status: {active: 'active', done: 'approved,viewed', archive: 'ordered'}[_ocGdhTab] || 'active'});
        if (store) p.set('store', store);
        try {
            const j = await _gdhJson('/api/gom-don-hang/orders?' + p);
            const c = j.counts, n = {active: (c.pending || 0) + (c.reviewing || 0), done: (c.approved || 0) + (c.viewed || 0), archive: c.ordered || 0};
            document.querySelectorAll('#ocGdhTabs [data-tab]').forEach(b => { const s = b.querySelector('.n'); if (s && n[b.dataset.tab] !== undefined) s.textContent = n[b.dataset.tab]; });
            _ocGdhRejBadge();
            if (!store) _ocGdhApplyCounts(j); else ocArcBadge(j.archive_pending || 0);      // đang lọc theo chi nhánh thì số liệu bị lọc, không dùng cho huy hiệu menu
            const bd = document.getElementById('ocGdhBadge'); if (bd) { bd.textContent = c.pending || 0; bd.classList.toggle('d-none', !(c.pending > 0)); }
            if (_ocGdh) {                                                               // phiên đang duyệt còn thuộc về mình không?
                const j2 = _ocGdhTab === 'active' ? j : await _gdhJson('/api/gom-don-hang/orders?status=reviewing');
                if (!j2.data.some(o => o.id === _ocGdh.id && o.claimed_by_me)) { _ocGdh = null; _ocGdhSave(); _ocGdhRenderActive(); _ocGdhRenderBar(); }
            }
            _ocGdhCache = j.data;
            const data = j.data.filter(o => !_ocGdhType || ((o.by_type || {})[_ocGdhType] || {}).parts > 0);
            if (_ocGdhTab === 'active') {                                               // đơn mình đang duyệt, rồi đơn Khẩn, rồi đơn chờ lâu nhất
                data.sort((a, b) => ((b.status === 'reviewing' && b.claimed_by_me) - (a.status === 'reviewing' && a.claimed_by_me)) ||
                    ((b.urgent_parts > 0) - (a.urgent_parts > 0)) || ((b.waiting_min || 0) - (a.waiting_min || 0)));
            }
            list.innerHTML = data.length ? data.map(_ocGdhCardHtml).join('')
                : `<div class="text-center text-muted py-4"><i class="bi ${_ocGdhTab === 'active' ? 'bi-check2-circle text-success' : 'bi-inbox'} fs-3 d-block mb-1"></i>${_ocGdhTab === 'active' ? 'Không có đơn nào chờ duyệt.' : 'Chưa có đơn nào.'}</div>`;
        } catch (err) { list.innerHTML = `<div class="text-center text-danger py-3">${_gdhEsc(err.message)}</div>`; }
    }
    async function ocGdhClaim(id) {
        if (_ocGdh && _ocGdh.id !== id && !await nsConfirm('Bạn đang duyệt dở một đơn gôm khác trên màn này. Lấy đơn mới sẽ thay phiên đang duyệt (đơn cũ vẫn ở trạng thái Đang duyệt, nháp đã lưu, mở lại được bằng nút "Mở để duyệt"). Tiếp tục?')) return;
        _ocBusy(true, 'Đang lấy đơn về duyệt...');
        let j;
        try { j = await _gdhJson('/api/gom-don-hang/claim', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: id})}); }
        catch (err) { _ocBusy(false); alert(err.message); ocGdhLoad(); return; }
        await ocGdhOpen(id, true, j.review);          // máy chủ trả sẵn danh sách mã -> bớt 1 vòng gọi
    }
    async function ocGdhOpen(id, skipConfirm, pre) {
        if (!skipConfirm && _ocGdh && _ocGdh.id !== id && !await nsConfirm('Mở đơn này sẽ thay phiên đang duyệt trên màn hình (nháp của đơn đang mở đã được lưu). Tiếp tục?')) return;
        _ocBusy(true, 'Đang nạp đơn...');
        if (_ocGdh && _ocGdh.id !== id) { clearTimeout(_ocGdhT); await _ocGdhSaveDraft(); }
        let j = pre || null;
        if (!j) {
            try { j = await _gdhJson('/api/gom-don-hang/review-lines?batch_id=' + id); } catch (err) { _ocBusy(false); alert(err.message); ocGdhLoad(); return; }
        }
        if (!j.items.length) { _ocBusy(false); alert('Đơn này không có mã nào (SL cuối > 0 và đã chọn loại đơn).'); return; }
        const sel = document.getElementById('orderCheckStoreSelect');
        if (sel) sel.value = j.batch.store;
        document.getElementById('orderCheckInput').value = j.items.map(i => i.part_code + '\t' + i.qty).join('\n');
        _ocGdh = {id: id, store: j.batch.store, from: j.batch.from, to: j.batch.to, codes: j.items.map(i => i.part_code), kind: j.batch.kind || 'regular'};
        if (j.urgent_lines) _ocUrg = {id: id, lines: j.urgent_lines, open: true, loading: false};          // đơn khẩn: nạp sẵn thông tin khách
        _ocGdhSave(); _ocGdhRenderActive();
        _ocQuick = ''; _ocGdhDraftTxt = '';
        _ocBusy(true, `Đang kiểm tra ${j.items.length} mã...`);
        await runOrderCheck();
        if (_orderCheckCurrentStore !== _ocGdh.store) { _ocBusy(false); return; }          // kiểm tra lỗi (đã báo ở trên): không dựng bảng của đơn khác
        _orderCheckData.forEach(r => { r.note = ''; });                                  // bỏ ghi chú cũ theo mã: mỗi đơn có ghi chú riêng
        if (j.draft && Object.keys(j.draft).length) {                                    // nạp lại phần đang duyệt dở đã lưu trên máy chủ
            _ocGdhApplyDraft(j.draft);
            _ocGdhDraftTxt = 'đã nạp nháp lưu lúc ' + (j.draft_saved_at || '');
        }
        ocToggleMax(true);                                                                // mở khung duyệt toàn màn hình
        renderOrderCheckTable(); saveOrderCheckSession();                                 // bảng vẽ từng đợt nên khung hiện ra ngay
        _ocGdhRenderBar();
        _ocBusy(false);
        ocGdhLoad();
    }
    async function ocGdhRelease() {
        if (!_ocGdh || !await nsConfirm('Trả đơn về hàng chờ duyệt? SL Duyệt và ghi chú đang chỉnh cho đơn này sẽ không được giữ lại.')) return;
        _ocBusy(true, 'Đang trả đơn về hàng chờ...');
        try { await _gdhJson('/api/gom-don-hang/release', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: _ocGdh.id})}); }
        catch (err) { _ocBusy(false); alert(err.message); return; }
        _ocBusy(false);
        clearTimeout(_ocGdhT);
        _ocGdh = null; _ocGdhSave(); _ocGdhRenderActive(); _ocGdhRenderBar(); ocToggleMax(false); ocGdhLoad();
    }
    async function ocGdhReject() {          // trong khung duyệt: đơn chưa ổn -> từ chối, trả về chi nhánh kèm lý do
        if (!_ocGdh) return;
        const reason = await _gdhAskReject(); if (reason === null) return;
        _ocBusy(true, 'Đang từ chối đơn...');
        try { await _gdhJson('/api/gom-don-hang/reject', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: _ocGdh.id, reason})}); }
        catch (err) { _ocBusy(false); alert(err.message); return; }
        _ocBusy(false);
        _ocToast('Đã từ chối đơn - đơn được trả về chi nhánh để chỉnh sửa.');
        clearTimeout(_ocGdhT);
        _ocGdh = null; _ocGdhSave(); _ocGdhRenderActive(); _ocGdhRenderBar(); _ocGdhDraftTxt = ''; _ocQuick = '';
        ocToggleMax(false);
        clearOrderCheckSession();
        document.getElementById('orderCheckInput').value = '';
        ocGdhLoad();
    }
    async function ocGdhApprove() {
        if (!_ocGdh) return;
        if (!_orderCheckData || !_orderCheckData.length || _orderCheckCurrentStore !== _ocGdh.store) { alert('Bảng Kiểm Tra Đơn Hàng hiện không phải của đơn này. Bấm "Mở để duyệt" ở danh sách để nạp lại đơn rồi duyệt.'); return; }
        const items = _ocGdhCollect();
        const missing = items.filter(i => i.approved_qty === null).length;
        if (missing && !await nsConfirm(`Có ${missing} mã của đơn không còn trong bảng kiểm tra (SL duyệt sẽ để trống). Vẫn duyệt xong?`)) return;
        const note = await nsPrompt('Duyệt xong và trả kết quả về cho chi nhánh?\nChi nhánh sẽ thấy: STT, mã hàng, tên hàng, SL gửi, SL duyệt và ghi chú từng mã của bạn.\n\nGhi chú chung cho chi nhánh (không bắt buộc) - bấm OK để duyệt xong:', '');
        if (note === null) return;
        clearTimeout(_ocGdhT);
        _ocBusy(true, 'Đang lưu kết quả duyệt...');
        try { await _gdhJson('/api/gom-don-hang/approve', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: _ocGdh.id, note: note.trim(), items})}); }
        catch (err) { _ocBusy(false); alert(err.message); ocGdhLoad(); return; }
        _ocBusy(false);
        _ocToast('Đã duyệt xong - chi nhánh sẽ nhận thông báo.');
        _ocGdh = null; _ocGdhSave(); _ocGdhRenderActive(); _ocGdhRenderBar(); _ocGdhDraftTxt = ''; _ocQuick = '';
        ocToggleMax(false);
        clearOrderCheckSession();
        document.getElementById('orderCheckInput').value = '';
        ocGdhLoad();
    }

    // ---------------------------------------------------------------------
    // FILE EXCEL LƯU TRỮ: đơn Đã duyệt / Đã xem / Đã đặt quá N ngày được máy chủ tự xuất Excel rồi xoá.
    // Tải về máy: (1) ghi thẳng vào thư mục đã chọn - tự tạo <chi nhánh>/<loại đơn>/ (Chrome/Edge), hoặc (2) ZIP có sẵn thư mục.
    // Trình duyệt không cho web ghi vào máy mà không bấm gì, nên cần bấm "Lưu vào thư mục trên máy" 1 lần mỗi khi có file mới.
    // ---------------------------------------------------------------------
    function ocArcBadge(n) { const b = document.querySelector('#ocGdhTabs [data-tab="files"] .n'); if (b) { b.textContent = n; b.classList.toggle('d-none', !n); } }
    function _ocArcQuery(extra) {
        const p = new URLSearchParams(extra || {});
        const s = document.getElementById('ocArcStore')?.value, t = document.getElementById('ocArcType')?.value;
        if (s) p.set('store', s);
        if (t) p.set('type', t);
        return p;
    }
    async function ocArcLoad() {
        const body = document.getElementById('ocArcBody'); if (!body) return;
        const p = _ocArcQuery(); if (document.getElementById('ocArcOnlyNew')?.checked) p.set('only_new', '1');
        try {
            const j = await _gdhJson('/api/gom-don-hang/archives?' + p), e = _gdhEsc;
            ocArcBadge(j.pending_download);
            const di = document.getElementById('ocArcDays'); if (di && document.activeElement !== di) di.value = j.days;
            document.getElementById('ocArcInfo').textContent = `${j.live} đơn đang chờ tới hạn (${j.due} đơn đã quá hạn, sẽ lưu trữ ở lần chạy tới) · ${j.total} file đã lưu` + (j.last_run ? ' · job chạy lần cuối ' + String(j.last_run).replace('T', ' ').slice(0, 16) : '');
            body.innerHTML = j.data.length ? j.data.map(r => `<tr><td><div class="fw-semibold">${e(r.folder)}</div><div class="gdh-sub">${e(r.filename)}</div></td>` +
                `<td>${e(r.name)}<div class="gdh-sub">Kỳ ${_gdhDate(r.from)} - ${_gdhDate(r.to)} · ${e(r.status_label)}</div></td>` +
                `<td class="text-end">${_gdhFmt(r.parts, 0)}<div class="gdh-sub">SL ${_gdhFmt(r.qty, 0)}</div></td><td>${e(r.archived_at)}</td>` +
                `<td>${r.downloaded_at ? `<span class="text-success small"><i class="bi bi-check2"></i> ${e(r.downloaded_at)}</span>` : '<span class="badge bg-warning text-dark">Chưa tải</span>'}</td>` +
                `<td class="text-end">${_gdhBtn('btn-outline-secondary', 'bi-download', 'Tải', `ocArcOne(${r.id})`)}</td></tr>`).join('')
                : '<tr><td colspan="6" class="text-center text-muted py-3">Chưa có file lưu trữ nào.</td></tr>';
        } catch (err) { body.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-3">${_gdhEsc(err.message)}</td></tr>`; }
    }
    async function ocArcOne(id) { await _gdhDownload('/api/gom-don-hang/archives/' + id + '/file?mark=1'); ocArcLoad(); }
    async function ocArcZip() {
        const p = _ocArcQuery({only_new: '1'});
        await _gdhDownload('/api/gom-don-hang/archives/zip?' + p);
        ocArcLoad();
    }
    async function ocArcSaveDays() {
        try {
            const j = await _gdhJson('/api/gom-don-hang/archives/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({days: document.getElementById('ocArcDays').value})});
            _ocToast('Đã đặt tự động lưu trữ + xoá sau ' + j.days + ' ngày.');
        } catch (err) { alert(err.message); }
        ocArcLoad();
    }
    async function ocArcRunNow() {
        const v = await nsPrompt('Lưu trữ NGAY các đơn Đã duyệt / Đã xem / Đã đặt quá bao nhiêu ngày?\n(0 = tất cả đơn đủ trạng thái, không đợi hết hạn).\nMỗi đơn sẽ được xuất Excel rồi XOÁ khỏi hệ thống.', '0');
        if (v === null) return;
        _ocBusy(true, 'Đang xuất Excel và lưu trữ...');
        try {
            const j = await _gdhJson('/api/gom-don-hang/archives/run-now', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({days: v.trim() === '' ? 0 : v})});
            _ocBusy(false);
            alert(`Đã lưu trữ ${j.batches} đơn thành ${j.files} file Excel.` + (j.errors && j.errors.length ? '\nLỗi: ' + j.errors.join('; ') : ''));
        } catch (err) { _ocBusy(false); alert(err.message); }
        ocArcLoad(); ocGdhNavBadge();
    }
    function _arcIdb() { return new Promise((res, rej) => { const rq = indexedDB.open('nsm-gdh-archive', 1); rq.onupgradeneeded = () => rq.result.createObjectStore('kv'); rq.onsuccess = () => res(rq.result); rq.onerror = () => rej(rq.error); }); }
    async function _arcIdbGet(k) { try { const db = await _arcIdb(); return await new Promise(r => { const q = db.transaction('kv').objectStore('kv').get(k); q.onsuccess = () => r(q.result || null); q.onerror = () => r(null); }); } catch (e) { return null; } }
    async function _arcIdbSet(k, v) { try { const db = await _arcIdb(); await new Promise(r => { const tx = db.transaction('kv', 'readwrite'); tx.objectStore('kv').put(v, k); tx.oncomplete = r; tx.onerror = r; }); } catch (e) { /* bỏ qua */ } }
    async function ocArcRoot(forcePick) {            // thư mục gốc đã chọn (nhớ lại giữa các lần dùng; mỗi phiên trình duyệt hỏi lại quyền 1 lần)
        let h = forcePick ? null : await _arcIdbGet('root');
        if (h) {
            let perm = await h.queryPermission({mode: 'readwrite'});
            if (perm !== 'granted') perm = await h.requestPermission({mode: 'readwrite'});
            if (perm !== 'granted') h = null;
        }
        if (!h) { h = await window.showDirectoryPicker({mode: 'readwrite', id: 'nsm-don-gom'}); await _arcIdbSet('root', h); }
        return h;
    }
    async function ocArcPickFolder() {
        if (!window.showDirectoryPicker) { alert('Trình duyệt này chưa hỗ trợ chọn thư mục (cần Chrome/Edge trên máy tính). Hãy dùng nút "Tải ZIP".'); return; }
        try { const h = await ocArcRoot(true); document.getElementById('ocArcStatus').textContent = 'Đã chọn thư mục: ' + h.name; } catch (e) { /* người dùng bấm Huỷ */ }
    }
    async function ocArcSaveFolder() {
        const st = document.getElementById('ocArcStatus');
        if (!window.showDirectoryPicker) { alert('Trình duyệt này chưa hỗ trợ ghi thẳng vào thư mục (cần Chrome/Edge trên máy tính). Hãy dùng nút "Tải ZIP" - file ZIP đã có sẵn thư mục chi nhánh / loại đơn.'); return; }
        let root;
        try { root = await ocArcRoot(false); } catch (e) { return; }          // người dùng bấm Huỷ ở hộp chọn thư mục
        if (!root) return;
        st.textContent = 'Đang lấy danh sách file chưa tải về...';
        let list;
        try { list = (await _gdhJson('/api/gom-don-hang/archives?' + _ocArcQuery({only_new: '1'}))).data; } catch (err) { alert(err.message); return; }
        if (!list.length) { st.textContent = 'Không còn file nào chưa tải về máy.'; return; }
        const ok = []; let fail = 0;
        for (let i = 0; i < list.length; i++) {
            const r = list[i];
            st.textContent = `Đang lưu ${i + 1}/${list.length}: ${r.folder}/${r.filename}`;
            try {
                const res = await fetch('/api/gom-don-hang/archives/' + r.id + '/file');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const blob = await res.blob();
                const d1 = await root.getDirectoryHandle(r.store, {create: true});
                const d2 = await d1.getDirectoryHandle(r.type, {create: true});
                const w = await (await d2.getFileHandle(r.filename, {create: true})).createWritable();
                await w.write(blob); await w.close();
                ok.push(r.id);
            } catch (err) { fail++; console.error('Lưu file lưu trữ lỗi:', r.filename, err); }
        }
        if (ok.length) { try { await _gdhJson('/api/gom-don-hang/archives/mark-downloaded', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ids: ok})}); } catch (err) { /* bỏ qua */ } }
        st.textContent = `Đã lưu ${ok.length}/${list.length} file vào thư mục "${root.name}"` + (fail ? ` · ${fail} file lỗi (bấm lại để thử tiếp)` : '') + '.';
        ocArcLoad();
    }

    // ---------------------------------------------------------------------
    // NHỚ PHIÊN ĐANG DUYỆT - lưu lại (cửa hàng đang chọn, danh sách đã dán,
    // kết quả kiểm tra, số lượng duyệt/ghi chú đang sửa dở) vào localStorage,
    // để rời tab/tải lại trang KHÔNG bị mất kết quả đang xem dở. Phiên lưu
    // CHỈ mất đi khi admin chủ động dán + chạy 1 đơn hàng MỚI (ghi đè lên
    // đúng key này), hoặc bấm nút "Xoá Phiên" thủ công - không tự xoá theo
    // thời gian hay khi rời trang.
    // ---------------------------------------------------------------------
    const ORDER_CHECK_SESSION_KEY = 'orderCheckSession_v1';
    let _orderCheckSessionSaveTimer = null;

    function saveOrderCheckSession() {
        // Debounce nhẹ vì hàm này được gọi liên tục khi gõ số lượng duyệt -
        // tránh ghi localStorage dồn dập theo từng phím gõ.
        clearTimeout(_orderCheckSessionSaveTimer);
        _orderCheckSessionSaveTimer = setTimeout(() => {
            if (!_orderCheckData || !_orderCheckData.length) return;
            try {
                const raw = document.getElementById('orderCheckInput');
                const storeSelect = document.getElementById('orderCheckStoreSelect');
                localStorage.setItem(ORDER_CHECK_SESSION_KEY, JSON.stringify({
                    storeCode: storeSelect ? storeSelect.value : _orderCheckCurrentStore,
                    debtSource: _ocDebtSource(),
                    rawInput: raw ? raw.value : '',
                    data: _orderCheckData,
                    savedAt: Date.now(),
                }));
            } catch (err) { /* localStorage đầy/không dùng được - bỏ qua, không chặn thao tác chính */ }
        }, 1200);
    }

    function clearOrderCheckSession(opts) {
        try { localStorage.removeItem(ORDER_CHECK_SESSION_KEY); } catch (err) { /* bỏ qua */ }
        if (!opts || !opts.silent) {
            _orderCheckData = [];
            _orderCheckCurrentStore = '';
            const resultCard = document.getElementById('orderCheckResultCard');
            if (resultCard) resultCard.classList.add('d-none');
            const status = document.getElementById('orderCheckStatus');
            if (status) status.innerText = 'Đã xoá phiên đang duyệt.' + (typeof _ocGdh !== 'undefined' && _ocGdh ? ' Đơn gôm đang duyệt vẫn giữ trạng thái Đang duyệt - bấm "Mở để duyệt" ở danh sách để nạp lại.' : '');
        }
    }

    function restoreOrderCheckSession() {
        let saved = null;
        try { saved = JSON.parse(localStorage.getItem(ORDER_CHECK_SESSION_KEY) || 'null'); } catch (err) { saved = null; }
        if (!saved || !saved.data || !saved.data.length) return;

        const raw = document.getElementById('orderCheckInput');
        const storeSelect = document.getElementById('orderCheckStoreSelect');
        const resultCard = document.getElementById('orderCheckResultCard');
        const status = document.getElementById('orderCheckStatus');
        if (raw && saved.rawInput) raw.value = saved.rawInput;
        if (storeSelect && saved.storeCode) storeSelect.value = saved.storeCode;
        { const ds = document.getElementById('orderCheckDebtSource'); if (ds && (saved.debtSource === 'admin' || saved.debtSource === 'store')) ds.value = saved.debtSource; }
        _orderCheckData = saved.data;
        _orderCheckCurrentStore = saved.storeCode || '';
        if (resultCard) resultCard.classList.remove('d-none');
        if (status) status.innerText = `Đã khôi phục phiên đang duyệt trước đó (${saved.data.length} mã hàng).`;
        resetOrderCheckFilters();
    }

    // Đồng bộ số lượng duyệt (gõ tay, có thể khác số đề xuất ban đầu) và
    // ghi chú vào lại _orderCheckData NGAY khi sửa - trước đây chỉ lưu trong
    // ô input trên màn hình nên: (1) xuất Excel luôn lấy số ĐỀ XUẤT ban đầu
    // dù đã sửa tay, và (2) đổi bộ lọc (render lại bảng) làm mất số đã sửa.
    // Cũng là nơi kích hoạt lưu phiên mỗi khi có thay đổi.
    document.addEventListener('input', (e) => {
        if (e.target.classList && e.target.classList.contains('order-check-note-input')) {
            const nr = _ocRowByCode(e.target.dataset.partCode);
            if (nr) { nr.note = e.target.value; saveOrderCheckSession(); _ocGdhDirty(); }
            return;
        }
        if (!e.target.classList || !e.target.classList.contains('order-check-approve-input')) return;
        const partCode = e.target.dataset.partCode;
        const row = _ocRowByCode(partCode);
        if (!row) return;
        const val = parseInt(e.target.value, 10);
        row.suggested_approve_qty = isNaN(val) ? 0 : val;
        renderOrderCheckDashboardSoon();
        saveOrderCheckSession();
        _ocGdhDirty();
    });

    // Phóng to / thu gọn màn Duyệt Đơn Hàng (giống Gôm Đơn Hàng)
    function ocToggleMax(force) {
        const pane = document.getElementById('order-check-pane'); if (!pane) return;
        const on = typeof force === 'boolean' ? force : !pane.classList.contains('oc-max');
        pane.classList.toggle('oc-max', on);
        if (typeof _ocGdhRenderBar === 'function') _ocGdhRenderBar();
        const b = document.getElementById('ocMaxBtn');
        if (b) { b.querySelector('span').textContent = on ? 'Thu nhỏ (Esc)' : 'Phóng to'; b.querySelector('i').className = 'bi me-1 ' + (on ? 'bi-fullscreen-exit' : 'bi-arrows-fullscreen'); }
    }
    function ocToggleTop() {
        const pane = document.getElementById('order-check-pane'); if (!pane) return;
        const collapsed = pane.classList.toggle('oc-top-collapsed');
        const b = document.getElementById('ocTopBtn');
        b.querySelector('span').textContent = collapsed ? 'Mở rộng' : 'Thu gọn';
        b.querySelector('i').className = 'bi me-1 ' + (collapsed ? 'bi-arrows-expand' : 'bi-arrows-collapse');
    }
    document.addEventListener('keydown', ev => {
        if (ev.key !== 'Escape') return;
        const pane = document.getElementById('order-check-pane');
        if (pane && pane.classList.contains('oc-max')) { ev.preventDefault(); ocToggleMax(false); }
    });
    document.getElementById('order-check-tab')?.addEventListener('hidden.bs.tab', () => ocToggleMax(false));

    function resetOrderCheckFilters() {
        const codeEl = document.getElementById('ocFilterCode');
        if (codeEl) codeEl.value = '';
        ['ocFilterLock', 'ocFilterDebt', 'ocFilterFreq', 'ocFilterChild', 'ocFilterApprove', 'ocFilterFound'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = 'all';
        });
        renderOrderCheckTable();
    }

    function _orderCheckRowMatchesFilters(row) {
        if (_ocQuick === 'diff' && Number(row.suggested_approve_qty) === Number(row.qty_order)) return false;      // xem nhanh khi duyệt đơn gôm
        if (_ocQuick === 'locked' && !row.is_locked) return false;
        if (_ocQuick === 'short' && !(row.still_needed_after_transfer > 0)) return false;
        const fCode = normalizeCodeSearch(document.getElementById('ocFilterCode').value);
        if (fCode && !normalizeCodeSearch(row.part_code).includes(fCode)) return false;

        const fLock = document.getElementById('ocFilterLock').value;
        if (fLock === 'locked' && !row.is_locked) return false;
        if (fLock === 'unlocked' && row.is_locked) return false;

        const fDebt = document.getElementById('ocFilterDebt').value;
        if (fDebt === 'debt' && !(row.debt_qty > 0)) return false;
        if (fDebt === 'shipping' && !(row.shipping_qty > 0)) return false;
        if (fDebt === 'none' && (row.debt_qty > 0 || row.shipping_qty > 0)) return false;

        const fFreq = document.getElementById('ocFilterFreq').value;
        if (fFreq !== 'all') {
            const code = row.sales_freq && row.sales_freq.code;
            if (fFreq === 'none' && code) return false;
            if (fFreq !== 'none' && code !== fFreq) return false;
        }

        const fChild = document.getElementById('ocFilterChild').value;
        if (fChild === 'yes' && !((row.related_child_codes && row.related_child_codes.length) || row.related_parent)) return false;

        const fApp = (document.getElementById('ocFilterApprove') || {}).value;
        if (fApp === 'yes' && !(Number(row.suggested_approve_qty) > 0)) return false;
        if (fApp === 'no' && Number(row.suggested_approve_qty) > 0) return false;

        const fFound = document.getElementById('ocFilterFound').value;
        if (fFound === 'found' && !row.found) return false;
        if (fFound === 'notfound' && row.found) return false;

        return true;
    }

    function renderOrderCheckTable() {
        const body = document.getElementById('order-check-body');
        const countEl = document.getElementById('orderCheckFilterCount');
        const filtered = _orderCheckData.filter(_orderCheckRowMatchesFilters);
        if (countEl) countEl.innerText = `Hiển thị ${filtered.length}/${_orderCheckData.length} mã hàng`;
        // Vẽ theo từng đợt: ~1000 dòng cùng lúc làm trình duyệt đứng hình vài giây. Vẽ ngay 60 dòng đầu rồi bổ sung dần
        // (lần vẽ mới hơn sẽ huỷ phần còn lại của lần cũ nên đổi bộ lọc / mở đơn khác không bị chồng).
        const tok = ++_ocRenderTok, FIRST = 60, STEP = 120;
        body.innerHTML = filtered.slice(0, FIRST).map(_orderCheckRowHtml).join('');
        syncOcCol1Width();
        renderOrderCheckDashboard();
        _ocGdhRenderBar();
        if (filtered.length <= FIRST) return;
        let i = FIRST;
        const more = () => {
            if (tok !== _ocRenderTok) return;
            body.insertAdjacentHTML('beforeend', filtered.slice(i, i + STEP).map(_orderCheckRowHtml).join(''));
            i += STEP;
            if (i < filtered.length) requestAnimationFrame(more); else syncOcCol1Width();
        };
        requestAnimationFrame(more);
    }
    let _ocRenderTok = 0;
    // Tra dòng theo mã bằng Map (trước đây .find() quét cả ~1000 dòng cho MỖI phím gõ / MỖI ô khi lưu nháp)
    let _ocIdxArr = null, _ocIdxMap = null, _ocIdxLen = -1;
    function _ocRowByCode(code) {
        if (_ocIdxArr !== _orderCheckData || _ocIdxLen !== _orderCheckData.length || !_ocIdxMap) {
            _ocIdxArr = _orderCheckData; _ocIdxLen = _orderCheckData.length;
            _ocIdxMap = new Map(_orderCheckData.map(r => [r.part_code, r]));
        }
        return _ocIdxMap.get(code);
    }
    let _ocDashT = null, _ocBarRaf = 0;
    function renderOrderCheckDashboardSoon() { clearTimeout(_ocDashT); _ocDashT = setTimeout(renderOrderCheckDashboard, 150); }
    function _ocGdhBarSoon() { if (_ocBarRaf) return; _ocBarRaf = requestAnimationFrame(() => { _ocBarRaf = 0; _ocGdhRenderBar(); }); }

    // Cột 1 "Mã Hàng" của bảng Kiểm Tra Đơn Hàng giờ tự co giãn theo đúng
    // mã DÀI NHẤT đang hiển thị (xem CSS .oc-result-table th/td:nth-child(1)
    // ở trên - không còn width cố định 150px + ellipsis nữa). Vì cột 2
    // "SL Đặt" sticky ngay sau đó cần biết CHÍNH XÁC cột 1 vừa render rộng
    // bao nhiêu để tự đặt "left" đúng vị trí (không đè lên / không hở),
    // hàm này đo lại độ rộng THỰC TẾ (offsetWidth) của ô cột 1 sau mỗi lần
    // render và gán vào biến CSS --oc-col1-w trên chính <table>. Dùng
    // requestAnimationFrame để chắc chắn trình duyệt đã layout xong với nội
    // dung mới trước khi đo (đo ngay lúc gán innerHTML có thể ra số cũ).
    function syncOcCol1Width() {
        const table = document.querySelector('.oc-result-table');
        if (!table) return;
        requestAnimationFrame(() => {
            const firstCell = table.querySelector('tbody tr td:first-child') || table.querySelector('thead th:first-child');
            if (!firstCell) return;
            const w = firstCell.getBoundingClientRect().width;
            if (w > 0) table.style.setProperty('--oc-col1-w', w + 'px');
        });
    }

    // Dashboard tổng giá trị (theo Giá Nhập) của TOÀN BỘ đơn hàng đang kiểm
    // tra (KHÔNG bị ảnh hưởng bởi bộ lọc trên bảng - dashboard luôn phản
    // ánh cả đơn) - so sánh "Trước Duyệt" (SL Đặt gốc admin đã dán vào)
    // với "Sau Duyệt" (SL Duyệt Đề Xuất hiện tại, có thể admin đã sửa tay
    // trong ô input - xem listener 'input' ở dưới), tổng cộng + chi tiết
    // theo từng "Nhóm hao mòn". Giá nhập/Nhóm hao mòn lấy từ dữ liệu nhập
    // ở khối "Dữ Liệu Model Xe" phía trên (file "output11") - mã hàng nào
    // CHƯA có giá nhập sẽ bị bỏ qua khỏi tổng (đếm riêng để cảnh báo admin
    // biết tổng đang thiếu 1 phần dữ liệu, không phải con số đầy đủ).
    function renderOrderCheckDashboard() {
        const el = document.getElementById('orderCheckDashboard');
        if (!el) return;
        if (!_orderCheckData || !_orderCheckData.length) { el.innerHTML = ''; return; }

        let totalBefore = 0, totalAfter = 0, missingPrice = 0;
        const byGroup = {}; // { nhom: {before, after} }
        _orderCheckData.forEach(row => {
            const gia = row.gia_nhap;
            if (gia === null || gia === undefined) { missingPrice++; return; }
            const before = (row.qty_order || 0) * gia;
            const after = (row.suggested_approve_qty || 0) * gia;
            totalBefore += before;
            totalAfter += after;
            const groupName = row.nhom_hao_mon && String(row.nhom_hao_mon).trim() ? row.nhom_hao_mon : 'Chưa phân loại';
            const g = byGroup[groupName] || (byGroup[groupName] = { before: 0, after: 0 });
            g.before += before;
            g.after += after;
        });

        const fmt = n => Math.round(n).toLocaleString('vi-VN');
        const diff = totalAfter - totalBefore;
        const diffCls = diff > 0 ? 'text-danger' : (diff < 0 ? 'text-success' : 'text-muted');
        const diffSign = diff > 0 ? '+' : '';

        const groupNames = Object.keys(byGroup).sort();
        const groupRows = groupNames.map(name => {
            const g = byGroup[name];
            const gDiff = g.after - g.before;
            const gCls = gDiff > 0 ? 'text-danger' : (gDiff < 0 ? 'text-success' : 'text-muted');
            const gSign = gDiff > 0 ? '+' : '';
            return `<tr>
                <td>${escapeHtmlText(name)}</td>
                <td class="text-end">${fmt(g.before)}</td>
                <td class="text-end">${fmt(g.after)}</td>
                <td class="text-end ${gCls}">${gSign}${fmt(gDiff)}</td>
            </tr>`;
        }).join('');

        el.innerHTML = `
            <div class="border rounded-3 p-3 bg-light-subtle">
                <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
                    <i class="bi bi-graph-up-arrow text-primary"></i>
                    <span class="fw-bold">Tổng Giá Trị Đơn Hàng (theo Giá Nhập)</span>
                    ${missingPrice > 0 ? `<span class="small text-warning-emphasis">(${missingPrice} mã hàng chưa có dữ liệu Giá Nhập - không tính vào tổng)</span>` : ''}
                </div>
                <div class="row g-2 mb-2">
                    <div class="col-6 col-md-3">
                        <div class="small text-muted">Tổng Trước Duyệt</div>
                        <div class="fw-bold fs-6">${fmt(totalBefore)} đ</div>
                    </div>
                    <div class="col-6 col-md-3">
                        <div class="small text-muted">Tổng Sau Duyệt (Đề Xuất)</div>
                        <div class="fw-bold fs-6">${fmt(totalAfter)} đ</div>
                    </div>
                    <div class="col-6 col-md-3">
                        <div class="small text-muted">Chênh Lệch</div>
                        <div class="fw-bold fs-6 ${diffCls}">${diffSign}${fmt(diff)} đ</div>
                    </div>
                </div>
                ${groupNames.length ? `
                <div class="table-responsive" style="max-height:260px;">
                    <table class="table table-sm table-borderless mb-0 small">
                        <thead>
                            <tr class="border-bottom">
                                <th>Nhóm Hao Mòn</th>
                                <th class="text-end">Trước Duyệt</th>
                                <th class="text-end">Sau Duyệt</th>
                                <th class="text-end">Chênh Lệch</th>
                            </tr>
                        </thead>
                        <tbody>${groupRows}</tbody>
                    </table>
                </div>` : ''}
            </div>`;
    }

    function _bacNamText(qtyBac, qtyNam, hasStockBac, hasStockNam) {
        // Ưu tiên số lượng cụ thể (file kiểu cũ); nếu không có thì dùng cờ
        // Y/N còn hàng (file kiểu mới, xem _to_stock_flag ở backend).
        const fmt = (qty, hasStock) => {
            if (qty !== null && qty !== undefined) return String(qty);
            if (hasStock !== null && hasStock !== undefined) return hasStock ? 'Có' : 'Không';
            return null;
        };
        const bac = fmt(qtyBac, hasStockBac);
        const nam = fmt(qtyNam, hasStockNam);
        if (bac === null && nam === null) return null;
        return { bac, nam, noneAvailable: (bac === 'Không' || bac === '0') && (nam === 'Không' || nam === '0') };
    }

    function _ocNum(n) {
        return (n === 0 || n === null || n === undefined) ? '<span class="oc-zero">0</span>' : escapeHtmlText(String(n));
    }

    // Chip tồn theo cửa hàng: "NS3 5 TX" - dùng chung cho Tồn Hệ Thống và mã cha/con.
    function _ocStoreChips(breakdown, freqMap, skipStore) {
        const chips = Object.keys(breakdown || {})
            .filter(sc => sc !== skipStore && breakdown[sc] > 0)
            .map(sc => {
                const f = freqMap && freqMap[sc];
                const fTag = f ? `<span class="oc-stock-freq-text ${f.toLowerCase()}" title="${escapeHtmlAttr(_ocFreqTip(f))}">${f}</span>` : '';
                return `<span class="oc-chip">${sc} <b>${breakdown[sc]}</b>${fTag}</span>`;
            });
        return chips.length ? `<div class="oc-chips">${chips.join('')}</div>` : '';
    }

    // 1 mã cha hoặc 1 mã con (lấy từ bảng quy cách) - hiện mã, số quy đổi, tồn từng cửa hàng.
    function _ocRelatedItemHtml(c, label) {
        const chips = _ocStoreChips(c.store_breakdown, c.store_breakdown_freq, null);
        return `<div class="oc-bundle-item">` +
            `<div><strong>${escapeHtmlText(c.part_code)}</strong> ` +
            (c.is_locked ? `<span class="oc-tag oc-tag-red">khoá</span> ` : '') +
            `<span class="oc-sub">${label}</span></div>` +
            (c.part_name ? `<div class="oc-sub">${escapeHtmlText(c.part_name)}</div>` : '') +
            `<div class="oc-sub">Tổng tồn: <b class="${c.total_qty > 0 ? '' : 'text-danger'}">${c.total_qty}</b></div>` +
            (chips || `<div class="oc-sub">Không còn tồn ở cửa hàng nào</div>`) +
            `</div>`;
    }

    function _orderCheckRowHtml(row) {
        const notFound = !row.found;
        const hasChildren = !!(row.related_child_codes && row.related_child_codes.length);
        const bacNam = _bacNamText(row.lock_qty_bac, row.lock_qty_nam, row.lock_has_stock_bac, row.lock_has_stock_nam);

        // --- Khoá đặt hàng (chỉ khoá + kho HVN + mã thay thế; mã cha/con sang cột riêng) ---
        const lockHtml = `<div class="oc-ctx">` +
            (row.is_locked ? `<span class="badge bg-danger">Đã khoá</span>` : `<span class="oc-sub">Không khoá</span>`) +
            (bacNam ? `<div class="oc-sub mt-1" title="Tồn kho Bắc/Nam của Honda Việt Nam - không liên quan luân chuyển giữa các cửa hàng">KHO HVN · <span class="${bacNam.noneAvailable ? 'text-danger fw-semibold' : ''}">Bắc: ${bacNam.bac ?? '-'} | Nam: ${bacNam.nam ?? '-'}</span></div>` : '') +
            (row.replacement_code ? `<div class="oc-sub">Thay thế: <strong>${escapeHtmlText(row.replacement_code)}</strong></div>` : '') +
            `</div>`;

        // --- Quy cách cha / con: CHỈ từ file quy cách ---
        let bundleHtml = `<span class="oc-zero">—</span>`;
        if (row.related_parent) {
            const p = row.related_parent;
            bundleHtml = `<div class="oc-bundle-box"><span class="oc-tag">MÃ CON</span> <span class="oc-sub">1 mã cha = ${p.ratio} mã này</span>` +
                `<div class="mt-1">${_ocRelatedItemHtml(p, '(mã cha)')}</div></div>`;
        } else if (hasChildren) {
            bundleHtml = `<div class="oc-bundle-box"><span class="oc-tag">MÃ CHA</span> <span class="oc-sub">${row.related_child_codes.length} mã con</span>` +
                `<div class="mt-1">` +
                row.related_child_codes.map(c => _ocRelatedItemHtml(c, `(1 cha = ${c.ratio} con)`)).join('') +
                `</div></div>`;
        }

        // --- Khách chờ ---
        const pc = row.pending_customers || [];
        const pendingHtml = pc.length
            ? pc.map(c => `<div class="oc-sub" style="color:#334155;">` +
                (c.customer_name ? `<b>${escapeHtmlText(c.customer_name)}</b>` : '') +
                (c.quote_no ? ` · BG ${escapeHtmlText(c.quote_no)}` : '') +
                (c.order_date ? ` · ${escapeHtmlText(c.order_date)}` : '') +
                (c.frame_number ? `<div>Khung: ${escapeHtmlText(c.frame_number)}</div>` : '') +
              `</div>`).join('<hr class="oc-pending-sep">')
            : `<span class="oc-zero">—</span>`;

        // --- Nợ / vận chuyển ---
        const debtParts = [];
        if (row.debt_qty > 0) debtParts.push(`<span class="badge bg-danger">Nợ ${row.debt_qty}</span>`);
        if (row.shipping_qty > 0) debtParts.push(`<span class="badge bg-warning text-dark">V/c ${row.shipping_qty}</span>`);
        const debtHtml = debtParts.length ? debtParts.join(' ') : `<span class="oc-zero">0</span>`;

        // --- Luân chuyển ---
        const transferHtml = row.transfer_suggestions && row.transfer_suggestions.length
            ? row.transfer_suggestions.map(t => `<div><strong>${t.store_code}</strong> ${t.qty} <span class="oc-sub">(${t.sales_freq.code})</span></div>`).join('') +
              (row.still_needed_after_transfer > 0 ? `<div class="text-danger fw-semibold oc-sub">Vẫn thiếu ${row.still_needed_after_transfer}</div>` : '')
            : (row.qty_order > row.store_qty
                ? `<span class="text-danger oc-sub">Không có cửa hàng nào dư</span>`
                : `<span class="oc-sub text-success">Đủ tồn tại chỗ</span>`);

        // --- Tình hình bán ---
        const freq = row.sales_freq;
        const fc = freq && freq.code ? freq.code.toLowerCase() : 'none';
        const salesHtml = `<div class="oc-sales"><span class="oc-sales-badge oc-sales-badge-${fc}">${freq && freq.code ? freq.code : '-'}</span>` +
            `<div class="oc-sub" style="line-height:1.25;">${row.period_months} tháng: <b>${row.qty_sold_period ?? 0}</b>` +
            (freq && freq.avg_month !== undefined ? `<br>1 tháng: ${freq.avg_month}` : '') + `</div></div>`;

        // --- Tồn hệ thống (trừ cửa hàng đang đặt) ---
        const breakdown = row.store_breakdown || {};
        const ownQty = _orderCheckCurrentStore ? (breakdown[_orderCheckCurrentStore] || 0) : 0;
        const otherTotal = row.total_qty - ownQty;
        const stockChips = _ocStoreChips(breakdown, row.store_breakdown_freq, _orderCheckCurrentStore);
        const stockHtml = `<div class="oc-ctx"><span class="oc-sub">Tổng:</span> <b class="${otherTotal > 0 ? '' : 'text-danger'}" title="Tổng tồn các cửa hàng khác (đã trừ ${_orderCheckCurrentStore || ''})">${otherTotal}</b>` +
            (stockChips ? `<div class="mt-1">${stockChips}</div>` : `<div class="oc-sub">Không cửa hàng nào khác còn tồn</div>`) + `</div>`;

        const needOrder = !row.is_locked && row.suggested_approve_qty > 0;
        const trCls = [notFound ? 'table-warning' : '', needOrder ? 'oc-on' : '', row.is_locked ? 'oc-locked' : ''].filter(Boolean).join(' ');

        return `<tr${trCls ? ` class="${trCls}"` : ''}>
            <td title="${escapeHtmlAttr(row.part_code)}"><span class="oc-code">${escapeHtmlText(row.part_code)}</span>${notFound ? '<div class="oc-sub text-danger">Không có trong tồn kho</div>' : ''}${row.typed_code ? `<div class="oc-sub">gõ: ${escapeHtmlText(row.typed_code)}</div>` : ''}</td>
            <td class="text-end fw-semibold">${_ocNum(row.qty_order)}</td>
            <td class="oc-name">${escapeHtmlText(row.part_name || '')}<div class="mt-1">${_orderCheckVehicleModelsHtml(row)}</div></td>
            <td>${salesHtml}</td>
            <td class="oc-td-stock">${row.store_qty > 0 ? row.store_qty : '<span class="text-danger">0</span>'}</td>
            <td>${stockHtml}</td>
            <td>${lockHtml}</td>
            <td>${bundleHtml}</td>
            <td class="oc-ctx">${pendingHtml}</td>
            <td>${debtHtml}</td>
            <td class="oc-ctx">${transferHtml}</td>
            <td class="text-end oc-td-final">
                <input type="number" step="1" min="0" class="form-control form-control-sm text-end order-check-approve-input"
                       style="width:84px; display:inline-block; font-weight:700;" data-part-code="${escapeHtmlAttr(row.part_code)}"
                       value="${row.suggested_approve_qty}" onfocus="this.select()">
            </td>
            <td>
                <input type="text" class="form-control form-control-sm order-check-note-input"
                       style="min-width:170px;" data-part-code="${escapeHtmlAttr(row.part_code)}"
                       placeholder="Ghi chú..." list="ocNoteTpl" value="${escapeHtmlAttr(row.note || '')}"
                       onchange="saveOrderCheckNote(this)">
            </td>
        </tr>`;
    }

    // Lưu ghi chú của 1 mã hàng ngay khi rời khỏi ô (onchange) - UPSERT
    // theo part_code, dùng lại cho MỌI lần kiểm tra sau này có mã đó.
    async function saveOrderCheckNote(inputEl) {
        if (_ocGdh && _orderCheckCurrentStore === _ocGdh.store) return;      // đang duyệt đơn gôm: ghi chú chỉ thuộc đơn này (lưu nháp / Duyệt xong), không lưu chung theo mã
        const partCode = inputEl.dataset.partCode;
        const note = inputEl.value;
        inputEl.disabled = true;
        try {
            const res = await fetch('/api/admin/order-check/note', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ part_code: partCode, note })
            });
            const json = await res.json();
            if (!json.success) {
                alert(json.error || 'Lưu ghi chú thất bại.');
                return;
            }
            // Cập nhật lại dữ liệu gốc trong bộ nhớ để lọc/xuất Excel sau
            // đó vẫn thấy đúng ghi chú mới nhất mà không cần gọi lại API.
            const row = _orderCheckData.find(r => r.part_code === partCode);
            if (row) row.note = note;
            saveOrderCheckSession();
        } catch (err) {
            console.error('Lỗi saveOrderCheckNote:', err);
            alert('Lỗi kết nối máy chủ khi lưu ghi chú.');
        } finally {
            inputEl.disabled = false;
        }
    }

    // Xuất bảng kết quả Kiểm Tra Đơn Hàng ra file Excel bằng SheetJS - CHỈ
    // xuất đúng các dòng đang hiển thị theo bộ lọc hiện tại (không xuất cả
    // danh sách gốc nếu đang lọc bớt), để file xuất khớp với những gì admin
    // đang nhìn thấy trên màn hình.
    function exportOrderCheckExcel() {
        if (!_orderCheckData || !_orderCheckData.length) {
            alert('Chưa có dữ liệu để xuất. Vui lòng Kiểm Tra trước.');
            return;
        }
        const rows = _orderCheckData.filter(_orderCheckRowMatchesFilters);
        if (!rows.length) {
            alert('Không có dòng nào khớp bộ lọc hiện tại để xuất.');
            return;
        }

        const sheetData = rows.map(row => {
            const transferText = (row.transfer_suggestions && row.transfer_suggestions.length)
                ? row.transfer_suggestions.map(t => `Lấy ${t.qty} từ ${t.store_code} (đang dư ${t.store_available_qty}, ${t.sales_freq.code})`).join('; ')
                : '';
            const childText = (row.related_child_codes && row.related_child_codes.length)
                ? row.related_child_codes.map(c => `${c.part_code} (1 cha = ${c.ratio} con, tồn ${c.total_qty})`).join('; ')
                : '';
            const parentText = row.related_parent
                ? `${row.related_parent.part_code} (1 cha = ${row.related_parent.ratio} con, tồn ${row.related_parent.total_qty})`
                : '';
            const pendingText = (row.pending_customers && row.pending_customers.length)
                ? row.pending_customers.map(c => {
                    const parts = [];
                    if (c.quote_no) parts.push(`BG ${c.quote_no}`);
                    if (c.customer_name) parts.push(c.customer_name);
                    if (c.order_date) parts.push(`ngày ${c.order_date}`);
                    if (c.frame_number) parts.push(`khung ${c.frame_number}`);
                    return parts.join(', ');
                  }).join(' | ')
                : '';
            return {
                'Mã Hàng': row.part_code,
                'SL Đặt': row.qty_order,
                'Tên Hàng': row.part_name || '',
                'Dùng Cho Xe': (row.vehicle_models && row.vehicle_models.length)
                    ? [...new Set(row.vehicle_models.map(m => m.vehicle_family))].join(', ')
                    : (row.vehicle_models_unresolved || ''),
                'Tình Hình Bán': row.sales_freq ? row.sales_freq.label : '',
                'Đã Bán/Kỳ': row.qty_sold_period ?? 0,
                'Tồn CH': row.store_qty,
                'Tồn Hệ Thống': row.total_qty,
                'Khoá Đặt Hàng': row.is_locked ? 'Đã khoá' : 'Không khoá',
                'Mã Thay Thế': row.replacement_code || '',
                'Mã Con (quy cách)': childText,
                'Mã Cha (quy cách)': parentText,
                'Khách Đang Chờ': pendingText,
                'Đang Nợ': row.debt_qty || 0,
                'Đang Vận Chuyển': row.shipping_qty || 0,
                'Đề Xuất Luân Chuyển': transferText,
                'Vẫn Thiếu Sau Luân Chuyển': row.still_needed_after_transfer || 0,
                'SL Duyệt Đề Xuất': row.suggested_approve_qty,
                'Nhóm Hao Mòn': row.nhom_hao_mon || '',
                'Giá Nhập': row.gia_nhap ?? '',
                'Thành Tiền Trước Duyệt': (row.gia_nhap != null) ? Math.round(row.qty_order * row.gia_nhap) : '',
                'Thành Tiền Sau Duyệt': (row.gia_nhap != null) ? Math.round(row.suggested_approve_qty * row.gia_nhap) : '',
                'Ghi Chú': row.note || '',
                'Không Tìm Thấy': row.found ? '' : 'Không có trong hệ thống',
            };
        });

        const ws = XLSX.utils.json_to_sheet(sheetData);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, 'Duyệt Đơn Hàng');
        const storeCode = document.getElementById('orderCheckStoreSelect').value;
        const now = new Date();
        const stamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}_${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}`;
        XLSX.writeFile(wb, `DuyetDonHang_${storeCode}_${stamp}.xlsx`);
    }

    async function importBodyKitModelCategories() {
        const input = document.getElementById('bodyKitCategoryImportFile');
        const btn = document.getElementById('bodyKitCategoryImportBtn');
        const status = document.getElementById('bodyKitCategoryImportStatus');
        if (!input.files || input.files.length === 0) {
            alert('Vui lòng chọn 1 file Excel (.xlsx) trước.');
            return;
        }
        if (!await nsConfirm('Nhập bảng tra mới sẽ THAY THẾ TOÀN BỘ bảng tra Mã xe → Dòng xe hiện tại. Tiếp tục?')) return;

        const formData = new FormData();
        formData.append('file', input.files[0]);

        btn.disabled = true;
        if (status) status.innerText = 'Đang nhập bảng tra...';
        try {
            const res = await fetch('/api/admin/body-kit/import-model-categories', { method: 'POST', body: formData });
            const json = await res.json();
            if (!json.success) {
                if (status) status.innerText = '';
                alert(json.error || 'Nhập bảng tra thất bại.');
                return;
            }
            if (status) {
                status.innerText = `Đã nhập ${json.codes_imported.toLocaleString()} mã xe, ` +
                    `cập nhật ${json.groups_updated.toLocaleString()} nhóm bộ áo đã có` +
                    (json.conflicts_total > 0 ? ` (${json.conflicts_total} mã xung đột dòng xe - xem console).` : '.') +
                    (json.warnings_total > 0 ? ` (${json.warnings_total} dòng cảnh báo - xem console).` : '');
            }
            if (json.conflicts_total > 0) console.warn('Xung đột bảng tra Mã xe → Dòng xe:', json.conflicts);
            if (json.warnings_total > 0) console.warn('Cảnh báo bảng tra Mã xe → Dòng xe:', json.warnings);
            input.value = '';
            // Menu dòng xe/đời cũ (đã cache) có thể không còn đúng nữa.
            _bodyKitMenuCache = null;
            _bodyKitSelectedFamily = null;
            _bodyKitSelectedSubModel = null;
            loadBodyKitMenu();
            loadBodyKitGroups();
        } catch (err) {
            if (status) status.innerText = '';
            console.error('Lỗi importBodyKitModelCategories:', err);
            alert('Lỗi kết nối máy chủ khi nhập dữ liệu.');
        } finally {
            btn.disabled = false;
        }
    }

    // ------------------------------------------------------------------
    // NÚT "TRA CỨU NHANH" NỔI GÓC DƯỚI BÊN PHẢI (chỉ user cửa hàng) - mở
    // modal hiện lại đúng bảng Tồn Kho & Vị Trí (dùng chung buildInventoryRowsHtml
    // ở trên), để tra cứu ngay từ BẤT KỲ tab nào đang xem, không cần chuyển
    // qua tab "Tồn kho & Vị trí". Tận dụng thẳng globalInventory/globalLocations
    // đã có sẵn trong bộ nhớ (nếu đã từng tải) thay vì luôn gọi lại server.
    // ------------------------------------------------------------------
    // ------------------------------------------------------------------
    // KÉO GIÃN ĐỘ RỘNG TỪNG CỘT (bảng Tồn Kho Hệ Thống & modal Tra Cứu
    // Nhanh) - kéo tay cầm mỏng ở cạnh phải mỗi cột (trừ cột cuối) để
    // chỉnh rộng/hẹp tuỳ ý, giống Excel/Google Sheets.
    // ------------------------------------------------------------------
    // Cách hoạt động: chỉ set width lên các <th> (không đụng tới từng
    // <td>) - nhờ table-layout:fixed, trình duyệt tự áp width của <th> ở
    // hàng đầu cho toàn bộ cột tương ứng ở tbody, kể cả khi tbody được vẽ
    // lại liên tục lúc lọc/tìm kiếm (thead không bị vẽ lại nên không mất
    // độ rộng đã kéo). Độ rộng được lưu vào localStorage theo từng bảng để
    // lần sau vào lại vẫn giữ nguyên.
    function makeResizableColumns(tableId, storageKey, onColumnResize) {
        const table = document.getElementById(tableId);
        if (!table || table.dataset.resizeInit === '1') return;
        const headerRow = table.querySelector('thead tr');
        if (!headerRow) return;
        table.dataset.resizeInit = '1';

        const ths = Array.from(headerRow.children);
        let saved = {};
        try { saved = JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch (e) { /* bỏ qua dữ liệu lưu bị hỏng */ }

        function persistWidths() {
            const widths = {};
            ths.forEach((t, i) => { widths[i] = Math.round(t.getBoundingClientRect().width); });
            try { localStorage.setItem(storageKey, JSON.stringify(widths)); } catch (err) { /* localStorage đầy/không dùng được - bỏ qua */ }
        }

        ths.forEach((th, idx) => {
            // Cột lưu sẵn > cột đang render tự nhiên (giữ nguyên hình dạng
            // ban đầu khi mới chuyển sang table-layout:fixed).
            const initialW = saved[idx] || th.getBoundingClientRect().width;
            th.style.width = Math.round(initialW) + 'px';
            if (onColumnResize) onColumnResize(idx, Math.round(initialW)); // đồng bộ ngay từ đầu (vd offset cột sticky kế bên), không đợi tới lúc kéo

            if (idx === ths.length - 1) return; // cột cuối cùng: không cần tay kéo (không có cột bên phải để mở rộng vào)

            const handle = document.createElement('span');
            handle.className = 'col-resize-handle';
            th.appendChild(handle);

            let startX = 0, startW = 0, resizingCol = false;
            handle.addEventListener('pointerdown', (e) => {
                resizingCol = true;
                startX = e.clientX;
                startW = th.getBoundingClientRect().width;
                handle.classList.add('col-resizing');
                try { handle.setPointerCapture(e.pointerId); } catch (err) {}
                e.preventDefault();
                e.stopPropagation(); // không để bảng bên dưới nhận nhầm sự kiện (vd. sort cột nếu có sau này)
            });
            handle.addEventListener('pointermove', (e) => {
                if (!resizingCol) return;
                const newW = Math.max(40, startW + (e.clientX - startX));
                th.style.width = newW + 'px';
                if (onColumnResize) onColumnResize(idx, newW); // cập nhật NGAY lúc đang kéo (không đợi thả tay) để cột sticky kế bên bám theo mượt
            });
            function endResizeCol() {
                if (!resizingCol) return;
                resizingCol = false;
                handle.classList.remove('col-resizing');
                persistWidths();
            }
            handle.addEventListener('pointerup', endResizeCol);
            handle.addEventListener('pointercancel', endResizeCol);
        });
    }

    // ------------------------------------------------------------------
    // KÉO DI CHUYỂN nút "Tra Cứu Nhanh" đến bất kỳ vị trí nào trên màn hình
    // ------------------------------------------------------------------
    // Cơ chế: dùng Pointer Events (gộp chung chuột + cảm ứng). Khi bấm
    // xuống, chỉ CHUYỂN SANG chế độ kéo nếu người dùng đã di chuyển quá
    // một ngưỡng nhỏ (DRAG_THRESHOLD) - nhờ vậy một cú bấm/chạm bình
    // thường (không di chuyển) vẫn kích hoạt onclick="openQuickLookup()"
    // như cũ, không bị nút "nuốt mất" sự kiện click.
    // Vị trí sau khi kéo được lưu vào localStorage để lần sau vào lại
    // trang vẫn giữ nguyên chỗ người dùng đã đặt.
    function setupQuickLookupFabDrag() {
        const fab = document.getElementById('quick-lookup-fab');
        if (!fab) return;

        const DRAG_THRESHOLD = 6; // px
        const STORAGE_KEY = 'quickLookupFabPos';
        let startX = 0, startY = 0, startLeft = 0, startTop = 0;
        let dragging = false, pointerDown = false, suppressClick = false;

        function clamp(val, min, max) { return Math.min(Math.max(val, min), max); }

        function applyPosition(left, top) {
            const w = fab.offsetWidth, h = fab.offsetHeight;
            left = clamp(left, 4, window.innerWidth - w - 4);
            top = clamp(top, 4, window.innerHeight - h - 4);
            fab.style.left = left + 'px';
            fab.style.top = top + 'px';
            fab.style.right = 'auto';
            fab.style.bottom = 'auto';
        }

        // Khôi phục vị trí đã lưu (nếu có) ngay khi trang tải xong.
        try {
            const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
            if (saved && typeof saved.left === 'number' && typeof saved.top === 'number') {
                applyPosition(saved.left, saved.top);
            }
        } catch (e) { /* bỏ qua nếu dữ liệu lưu bị hỏng */ }

        fab.addEventListener('pointerdown', (e) => {
            if (e.button !== undefined && e.button !== 0) return; // chỉ chuột trái / chạm
            pointerDown = true;
            dragging = false;
            suppressClick = false;
            const rect = fab.getBoundingClientRect();
            startLeft = rect.left;
            startTop = rect.top;
            startX = e.clientX;
            startY = e.clientY;
        });

        fab.addEventListener('pointermove', (e) => {
            if (!pointerDown) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            if (!dragging && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
                dragging = true;
                suppressClick = true;
                fab.classList.add('dragging');
                try { fab.setPointerCapture(e.pointerId); } catch (err) {}
            }
            if (dragging) {
                e.preventDefault();
                applyPosition(startLeft + dx, startTop + dy);
            }
        });

        function endDrag(e) {
            if (!pointerDown) return;
            pointerDown = false;
            if (dragging) {
                dragging = false;
                fab.classList.remove('dragging');
                const rect = fab.getBoundingClientRect();
                try {
                    localStorage.setItem(STORAGE_KEY, JSON.stringify({ left: rect.left, top: rect.top }));
                } catch (err) { /* bỏ qua nếu localStorage đầy/không dùng được */ }
            }
        }
        fab.addEventListener('pointerup', endDrag);
        fab.addEventListener('pointercancel', endDrag);

        // Nếu vừa kéo (dragging=true lúc thả), chặn sự kiện click phát sinh
        // ngay sau đó để không vô tình mở modal khi người dùng chỉ muốn dời vị trí nút.
        fab.addEventListener('click', (e) => {
            if (suppressClick) {
                e.preventDefault();
                e.stopPropagation();
                suppressClick = false;
            }
        }, true);

        // Giữ nút luôn nằm trong màn hình khi resize cửa sổ/trình duyệt.
        window.addEventListener('resize', () => {
            const rect = fab.getBoundingClientRect();
            if (fab.style.left && fab.style.left !== 'auto') {
                applyPosition(rect.left, rect.top);
            }
        });
    }

    // ------------------------------------------------------------------
    // KÉO GIÃN TO/NHỎ modal "Tra Cứu Nhanh" bằng tay cầm ở góc dưới-phải.
    // ------------------------------------------------------------------
    // Kích thước sau khi chỉnh được lưu vào localStorage để lần mở tiếp
    // theo vẫn giữ nguyên kích thước người dùng đã chọn.
    function setupQuickLookupModalResize() {
        const handle = document.getElementById('quick-lookup-modal-resize-handle');
        const dialog = document.getElementById('quick-lookup-modal-dialog');
        if (!handle || !dialog) return;

        const STORAGE_KEY = 'quickLookupModalSize';
        const MIN_W = 340, MIN_H = 260;
        let startX = 0, startY = 0, startW = 0, startH = 0, resizing = false;

        function maxW() { return Math.round(window.innerWidth * 0.97); }
        function maxH() { return Math.round(window.innerHeight * 0.95); }

        function applySize(w, h) {
            w = Math.min(Math.max(w, MIN_W), maxW());
            h = Math.min(Math.max(h, MIN_H), maxH());
            dialog.style.width = w + 'px';
            dialog.style.maxWidth = w + 'px';
            dialog.style.height = h + 'px';
            return { w, h };
        }

        // Khôi phục kích thước đã lưu mỗi khi modal được mở.
        document.getElementById('quick-lookup-modal').addEventListener('show.bs.modal', () => {
            try {
                const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
                if (saved && saved.w && saved.h) applySize(saved.w, saved.h);
            } catch (e) { /* bỏ qua nếu dữ liệu lưu bị hỏng */ }
        });

        handle.addEventListener('pointerdown', (e) => {
            resizing = true;
            const rect = dialog.getBoundingClientRect();
            startW = rect.width;
            startH = rect.height;
            startX = e.clientX;
            startY = e.clientY;
            handle.setPointerCapture(e.pointerId);
            e.preventDefault();
        });

        handle.addEventListener('pointermove', (e) => {
            if (!resizing) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            applySize(startW + dx, startH + dy);
        });

        function endResize(e) {
            if (!resizing) return;
            resizing = false;
            const rect = dialog.getBoundingClientRect();
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify({ w: Math.round(rect.width), h: Math.round(rect.height) }));
            } catch (err) { /* bỏ qua nếu localStorage đầy/không dùng được */ }
        }
        handle.addEventListener('pointerup', endResize);
        handle.addEventListener('pointercancel', endResize);
    }

    async function openQuickLookup() {
        const modalEl = document.getElementById('quick-lookup-modal');
        if (!modalEl) return;
        new bootstrap.Modal(modalEl).show();

        if (globalInventory.length === 0) {
            await loadInventory(); // lần đầu chưa có dữ liệu - tải mới (đã kèm loadLocations() cho store)
        }
        filterQuickLookup();
        setTimeout(() => { const s = document.getElementById('quick-lookup-search'); if (s) s.focus(); }, 300);
    }

    function filterQuickLookup() {
        const searchEl = document.getElementById('quick-lookup-search');
        const search = searchEl ? searchEl.value.toLowerCase() : '';
        const searchCode = normalizeCodeSearch(search);
        const filtered = globalInventory.filter(item =>
            normalizeCodeSearch(item.part_code).includes(searchCode) ||
            (item.part_name || '').toLowerCase().includes(search)
        );
        renderQuickLookupTable(filtered);
    }

    let _quickLookupFilterDebounceTimer = null;
    function debouncedFilterQuickLookup() {
        clearTimeout(_quickLookupFilterDebounceTimer);
        _quickLookupFilterDebounceTimer = setTimeout(filterQuickLookup, 250);
    }

    function renderQuickLookupTable(data) {
        const tbody = document.getElementById('quick-lookup-body');
        if (!tbody) return;
        const countEl = document.getElementById('quick-lookup-visible-count');
        if (countEl) countEl.innerText = data.length.toLocaleString();
        const isStore = (CURRENT_ROLE === 'store');
        const totalCols = isStore ? 13 : 12; // admin không có cột Vị Trí

        if (!data || data.length === 0) {
            tbody.innerHTML = `<tr><td colspan="${totalCols}" class="text-center py-4 text-muted">Không có dữ liệu tồn kho.</td></tr>`;
            return;
        }
        tbody.innerHTML = buildInventoryRowsHtml(data, INVENTORY_RENDER_LIMIT, totalCols);
    }

    // Vẽ lại CẢ bảng Tồn Kho Hệ Thống (nếu có trên trang) LẪN modal Tra Cứu
    // Nhanh (nếu tồn tại) từ dữ liệu ĐÃ CÓ SẴN trong bộ nhớ (không gọi lại
    // server) - gọi sau khi Lưu/Huỷ sửa vị trí tại chỗ để 2 nơi luôn khớp
    // nhau, bất kể đang sửa từ bảng nào trong 2 bảng đó.
    function refreshLocationAwareTables() {
        if (document.getElementById('inventory-body')) filterInventory();
        if (document.getElementById('quick-lookup-body')) filterQuickLookup();
    }

    const inventoryUploadForm = document.getElementById('inventory-upload-form');
    if (inventoryUploadForm) {
        inventoryUploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const file = document.getElementById('inventory_file').files[0];
            if (!file) {
                alert('Vui lòng chọn file tồn kho để tải lên.');
                return;
            }
            const formData = new FormData();
            formData.append('inventory_file', file);

            document.getElementById('loading-overlay').style.display = 'flex';
            try {
                const res = await fetch('/api/admin/upload-inventory', { method: 'POST', body: formData });
                const result = await res.json();
                if (res.ok) {
                    let msg = `Đã cập nhật tồn kho: ${result.total_parts.toLocaleString()} mã hàng (bỏ qua ${result.skipped_rows.toLocaleString()} dòng không thuộc mã kho quy định).`;
                    if (result.warnings && result.warnings.length) {
                        msg += '\n\nCảnh báo:\n' + result.warnings.slice(0, 10).join('\n');
                    }
                    alert(msg);
                    document.getElementById('inventory-file-name').innerText = '';
                    loadInventory();
                } else {
                    alert('Lỗi: ' + result.error);
                }
            } catch (e) { alert('Lỗi kết nối server.'); }
            finally { document.getElementById('loading-overlay').style.display = 'none'; }
        });
    }

    // Tải file mẫu Excel để nhập Giá Bán (chỉ cần đúng 2 cột "Mã hàng" và
    // "Giá bán" - có thể chứa toàn bộ hoặc chỉ 1 phần danh mục cần cập nhật).
    function downloadPriceImportTemplate() {
        const rows = [
            { 'Mã hàng': 'VD0001', 'Giá bán': 150000 },
            { 'Mã hàng': '', 'Giá bán': '' },
        ];
        const ws = XLSX.utils.json_to_sheet(rows);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, "MauGiaBan");
        XLSX.writeFile(wb, `Mau_Gia_Ban.xlsx`);
    }

    const priceUploadForm = document.getElementById('price-upload-form');
    if (priceUploadForm) {
        priceUploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const file = document.getElementById('price_file').files[0];
            if (!file) {
                alert('Vui lòng chọn file giá bán để tải lên.');
                return;
            }
            const formData = new FormData();
            formData.append('price_file', file);

            document.getElementById('loading-overlay').style.display = 'flex';
            try {
                const res = await fetch('/api/admin/import-prices', { method: 'POST', body: formData });
                const result = await res.json();
                if (res.ok && result.success) {
                    alert(`Đã cập nhật giá bán cho ${result.total_parts.toLocaleString()} mã hàng (bỏ qua ${result.skipped_rows.toLocaleString()} dòng không hợp lệ).`);
                    document.getElementById('price-file-name').innerText = '';
                    loadInventory();
                } else {
                    alert('Lỗi: ' + (result.error || 'Không thể nhập file giá bán.'));
                }
            } catch (e) { alert('Lỗi kết nối server.'); }
            finally {
                document.getElementById('loading-overlay').style.display = 'none';
                document.getElementById('price_file').value = '';
            }
        });
    }

    // ------------------------------------------------------------------
    // VỊ TRÍ HÀNG HÓA
    // ------------------------------------------------------------------
    // Store: globalLocations là danh sách phẳng [{part_code, quantity,
    //   location_1/2/3, updated_at, ...}] - y hệt trước đây.
    // Admin: globalLocations là danh sách PIVOT [{part_code, part_name, unit,
    //   stores: {NS1: {location_1/2/3, updated_at}, NS2: {...}, ...}}] - mỗi
    //   mã hàng 1 dòng duy nhất, gộp cả 6 cửa hàng, giống bảng Tồn Kho.
    let globalLocations = [];
    const LOCATION_STORES = ['NS1', 'NS2', 'NS3', 'NS4', 'NS5', 'NSM1'];

    // Chỉ vẽ tối đa LOCATION_RENDER_LIMIT dòng ra DOM mỗi lần, giống hệt cơ
    // chế INVENTORY_RENDER_LIMIT của bảng Tồn Kho Hệ Thống - tránh giật/lag
    // khi số mã hàng có vị trí lên tới hàng nghìn dòng.
    const LOCATION_RENDER_LIMIT = 200;

    async function loadLocations() {
        try {
            const res = await fetch('/api/locations');
            const result = await res.json();
            if (result.success) {
                globalLocations = result.data;
                filterLocations();
            }
        } catch (e) { console.error(e); }
    }

    function renderLocationsTable(data) {
        if (CURRENT_ROLE === 'admin') {
            renderLocationsPivotTable(data);
        } else {
            renderLocationsStoreTable(data);
        }
    }

    // -------- Store: bảng phẳng, sửa/xoá theo TỪNG DÒNG (giữ nguyên hành vi cũ) --------
    function renderLocationsStoreTable(data) {
        const tbody = document.getElementById('location-body');
        document.getElementById('location-visible-count').innerText = data.length.toLocaleString();

        if (data.length === 0) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-muted">Không có mã hàng nào trong dữ liệu tồn kho.</td></tr>`;
            return;
        }

        const locCell = (v) => v ? `<span class="badge bg-light text-dark border">${escapeHtmlAttr(v)}</span>` : '<span class="text-muted">-</span>';

        const truncated = data.length > LOCATION_RENDER_LIMIT;
        const displayData = truncated ? data.slice(0, LOCATION_RENDER_LIMIT) : data;

        let html = displayData.map(item => `
            <tr data-part="${escapeHtmlAttr(item.part_code)}">
                <td><span class="part-code">${escapeHtmlAttr(item.part_code)}</span></td>
                <td>${item.part_name || ''}</td>
                <td class="text-muted small">${item.unit || ''}</td>
                <td class="text-end">${(item.quantity || 0).toLocaleString()}</td>
                <td class="loc-cell loc-cell-1">${locCell(item.location_1)}</td>
                <td class="loc-cell loc-cell-2">${locCell(item.location_2)}</td>
                <td class="loc-cell loc-cell-3">${locCell(item.location_3)}</td>
                <td class="text-muted small">${item.updated_at || '-'}</td>
                <td class="text-center">
                    <button class="btn btn-outline-primary btn-sm" title="Sửa" onclick="editLocationRow(this, '${escapeHtmlAttr(item.part_code)}')"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-outline-danger btn-sm" title="Xoá" onclick="deleteLocationRow('${escapeHtmlAttr(item.part_code)}')"><i class="bi bi-trash"></i></button>
                </td>
            </tr>`
        ).join('');

        if (truncated) {
            html += `<tr><td colspan="9" class="text-center py-3 text-muted small fst-italic">Đang hiển thị ${LOCATION_RENDER_LIMIT} / ${data.length.toLocaleString()} mã hàng — gõ thêm để thu hẹp tìm kiếm.</td></tr>`;
        }

        tbody.innerHTML = html;
    }

    // Sửa nhanh tại chỗ: chuyển 3 ô Vị Trí của dòng đang bấm "Sửa" thành 3 ô
    // nhập liệu, kèm nút Lưu/Huỷ - không cần mở modal riêng.
    function editLocationRow(btn, partCode) {
    // btn là icon <span> đã click
    const locCell = btn.closest('.loc-col');
    if (!locCell) {
        alert('Không tìm thấy ô vị trí!');
        return;
    }
    const item = globalLocations.find(i => i.part_code === partCode);
    const vals = item ? [item.location_1 || '', item.location_2 || '', item.location_3 || ''] : ['', '', ''];
    locCell.innerHTML = `
        <div style="display:flex; flex-direction:column; gap:2px; width:100%;">
            <input type="text" class="form-control form-control-sm loc-edit-input" placeholder="VT1" value="${escapeHtmlAttr(vals[0])}">
            <input type="text" class="form-control form-control-sm loc-edit-input" placeholder="VT2" value="${escapeHtmlAttr(vals[1])}">
            <input type="text" class="form-control form-control-sm loc-edit-input" placeholder="VT3" value="${escapeHtmlAttr(vals[2])}">
            <div class="d-flex gap-1 mt-1">
                <button class="btn btn-success btn-sm flex-fill" title="Lưu" onclick="saveLocationRow(this, '${escapeHtmlAttr(partCode)}')"><i class="bi bi-check-lg"></i></button>
                <button class="btn btn-outline-secondary btn-sm flex-fill" title="Huỷ" onclick="refreshLocationAwareTables()"><i class="bi bi-x-lg"></i></button>
            </div>
        </div>
    `;
}

async function saveLocationRow(btn, partCode) {
    const row = btn.closest('tr');
    const locCell = row.querySelector('.loc-col');
    if (!locCell) {
        alert('Không tìm thấy ô vị trí!');
        return;
    }
    const inputs = locCell.querySelectorAll('.loc-edit-input');
    if (inputs.length < 3) {
        alert('Không tìm thấy đủ ô nhập!');
        return;
    }
    const payload = {
        part_code: partCode,
        location_1: inputs[0].value,
        location_2: inputs[1].value,
        location_3: inputs[2].value,
    };
    try {
        const res = await fetch('/api/locations/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (res.ok && result.success) {
            await loadInventory();
            if (document.getElementById('quick-lookup-body')) filterQuickLookup();
        } else {
            alert('Lỗi: ' + (result.error || 'Không thể lưu vị trí.'));
        }
    } catch (e) {
        alert('Lỗi kết nối server.');
    }
}

    async function deleteLocationRow(partCode) {
        if (!await nsConfirm(`Xoá toàn bộ vị trí đã lưu của mã hàng "${partCode}"?`)) return;
        try {
            const res = await fetch('/api/locations/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ part_code: partCode })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                loadLocations();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể xoá vị trí.'));
            }
        } catch (e) { alert('Lỗi kết nối server.'); }
    }

    // -------- HÀNG HƯ HỎNG (store: báo/xem của mình; admin: xem+sửa+xoá tất cả chi nhánh) --------
    let globalDamaged = [];
    let editingDamagedId = null;
    let damagedStoreSelectFilled = false;

    async function loadDamaged() {
        try {
            const res = await fetch('/api/damaged');
            const result = await res.json();
            if (result.success) {
                globalDamaged = result.data;
                if (CURRENT_ROLE === 'admin' && !damagedStoreSelectFilled) {
                    const sel = document.getElementById('damaged-filter-store');
                    if (sel) {
                        [...new Set(globalDamaged.map(d => d.store_code))].sort().forEach(sc => {
                            const opt = document.createElement('option');
                            opt.value = sc; opt.textContent = sc;
                            sel.appendChild(opt);
                        });
                        damagedStoreSelectFilled = true;
                    }
                }
                filterDamaged();
            }
        } catch (e) { console.error(e); }
    }

    // Lọc theo chi nhánh + tìm mã/tên hàng (chỉ admin có bộ lọc trên giao diện,
    // nhưng hàm dùng chung, store gọi renderDamagedTable trực tiếp với toàn bộ dữ liệu của mình).
    function filterDamaged() {
        let data = globalDamaged;
        // Lọc theo trạng thái xử lý (mọi role): mặc định chỉ hiện "Chưa xử lý".
        const statusEl = document.getElementById('damaged-filter-status');
        const statusVal = statusEl ? statusEl.value : 'pending';
        if (statusVal === 'pending') data = data.filter(item => !item.resolved);
        else if (statusVal === 'resolved') data = data.filter(item => item.resolved);
        if (CURRENT_ROLE === 'admin') {
            const storeSel = document.getElementById('damaged-filter-store');
            const searchEl = document.getElementById('damaged-filter-search');
            const storeVal = storeSel ? storeSel.value : '';
            const searchVal = searchEl ? searchEl.value.trim().toLowerCase() : '';
            data = data.filter(item => {
                if (storeVal && item.store_code !== storeVal) return false;
                if (searchVal) {
                    const hay = `${item.part_code} ${item.part_name || ''}`.toLowerCase();
                    if (!hay.includes(searchVal)) return false;
                }
                return true;
            });
        }
        renderDamagedTable(data);
    }

    function renderDamagedTable(data) {
        const tbody = document.getElementById('damaged-body');
        if (!tbody) return;
        const isAdmin = (CURRENT_ROLE === 'admin');
        const countEl = document.getElementById('damaged-visible-count');
        if (countEl) countEl.innerText = data.length.toLocaleString();

        if (!data || data.length === 0) {
            tbody.innerHTML = `<tr><td colspan="${isAdmin ? 8 : 7}" class="text-center py-4 text-muted">Không có dòng nào ở trạng thái này.</td></tr>`;
            return;
        }

        tbody.innerHTML = data.map(item => {
            const storeCol = isAdmin ? `<td><span class="badge bg-secondary-subtle text-secondary-emphasis">${escapeHtmlAttr(item.store_code)}</span></td>` : '';

            if (item.id === editingDamagedId) {
                // Đang sửa: hiện input cho số lượng + tình trạng, giữ nguyên mã hàng/cửa hàng (không cho đổi)
                return `
                <tr>
                    ${storeCol}
                    <td><span class="part-code">${escapeHtmlAttr(item.part_code)}</span></td>
                    <td>${escapeHtmlAttr(item.part_name || '')}</td>
                    <td class="text-end"><input type="number" id="damaged-edit-qty-${item.id}" class="form-control form-control-sm text-end" min="0.01" step="any" value="${item.quantity}"></td>
                    <td><input type="text" id="damaged-edit-note-${item.id}" class="form-control form-control-sm" value="${escapeHtmlAttr(item.note || '')}"></td>
                    <td class="text-muted small">${escapeHtmlAttr(item.created_by || '')}</td>
                    <td class="text-muted small">${escapeHtmlAttr(item.created_at || '')}</td>
                    <td class="text-center text-nowrap">
                        <button class="btn btn-success btn-sm" title="Lưu" onclick="saveDamagedEdit(${item.id})"><i class="bi bi-check-lg"></i></button>
                        <button class="btn btn-outline-secondary btn-sm" title="Huỷ" onclick="cancelDamagedEdit()"><i class="bi bi-x-lg"></i></button>
                    </td>
                </tr>`;
            }

            const imgCount = item.image_count || 0;
            const imgBtnClass = imgCount > 0 ? 'btn-primary' : 'btn-outline-secondary';
            const resolvedBadge = item.resolved
                ? `<div><span class="badge bg-success-subtle text-success-emphasis"><i class="bi bi-check-circle me-1"></i>Đã xử lý</span> <span class="text-muted small">${escapeHtmlAttr(item.resolved_by || '')}${item.resolved_at ? ' - ' + escapeHtmlAttr(item.resolved_at) : ''}</span></div>`
                : '';
            const resolveBtn = item.resolved
                ? `<button class="btn btn-outline-secondary btn-sm" title="Hoàn tác (chuyển về chưa xử lý)" onclick="resolveDamagedRow(${item.id}, false)"><i class="bi bi-arrow-counterclockwise"></i></button>`
                : `<button class="btn btn-success btn-sm" title="Đánh dấu đã xử lý" onclick="resolveDamagedRow(${item.id}, true)"><i class="bi bi-check2-circle"></i> Đã xử lý</button>`;
            return `
                <tr class="damaged-row${item.resolved ? ' text-muted' : ''}" style="cursor:pointer;${item.resolved ? ' opacity:.65;' : ''}" title="Bấm để xem ảnh đính kèm" onclick="handleDamagedRowClick(event, ${item.id}, '${escapeHtmlAttr(item.part_code)}')"
                    ${imgCount > 0 ? `onmouseenter="showDamagedImageHoverPreview(event, ${item.id})" onmousemove="positionDamagedImageHoverPreview(event)" onmouseleave="hideDamagedImageHoverPreview()"` : ''}
                >
                    ${storeCol}
                    <td><span class="part-code">${escapeHtmlAttr(item.part_code)}</span></td>
                    <td>${escapeHtmlAttr(item.part_name || '')}</td>
                    <td class="text-end fw-semibold text-warning-emphasis">${item.quantity.toLocaleString()}</td>
                    <td>${escapeHtmlAttr(item.note || '')}${resolvedBadge}</td>
                    <td class="text-muted small">${escapeHtmlAttr(item.created_by || '')}</td>
                    <td class="text-muted small">${escapeHtmlAttr(item.created_at || '')}</td>
                    <td class="text-center text-nowrap">
                        ${resolveBtn}
                        <button class="btn ${imgBtnClass} btn-sm" title="Ảnh đính kèm" onclick="openDamagedImages(${item.id}, '${escapeHtmlAttr(item.part_code)}')"><i class="bi bi-camera"></i>${imgCount > 0 ? ' ' + imgCount : ''}</button>
                        <button class="btn btn-outline-primary btn-sm" title="Sửa" onclick="startEditDamaged(${item.id})"><i class="bi bi-pencil"></i></button>
                        <button class="btn btn-outline-danger btn-sm" title="Xoá" onclick="deleteDamagedRow(${item.id})"><i class="bi bi-trash"></i></button>
                    </td>
                </tr>`;
        }).join('');
    }

    function startEditDamaged(id) {
        editingDamagedId = id;
        filterDamaged();
    }

    function cancelDamagedEdit() {
        editingDamagedId = null;
        filterDamaged();
    }

    async function saveDamagedEdit(id) {
        const qtyEl = document.getElementById(`damaged-edit-qty-${id}`);
        const noteEl = document.getElementById(`damaged-edit-note-${id}`);
        const qty = qtyEl ? qtyEl.value : null;
        const note = noteEl ? noteEl.value : '';
        if (!qty || Number(qty) <= 0) { alert('Vui lòng nhập số lượng hư hỏng hợp lệ.'); return; }
        try {
            const res = await fetch('/api/damaged/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: id, quantity: qty, note: note })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                editingDamagedId = null;
                await loadDamaged();
                refreshDamagedHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể lưu thay đổi.'));
            }
        } catch (e) { alert('Lỗi kết nối server.'); }
    }

    async function resolveDamagedRow(id, resolved) {
        if (resolved && !await nsConfirm('Đánh dấu lần báo này là ĐÃ XỬ LÝ? Tồn kho sẽ không còn cảnh báo vàng cho phần này.')) return;
        try {
            const res = await fetch('/api/damaged/resolve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: id, resolved: resolved })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                await loadDamaged();
                refreshDamagedHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể cập nhật trạng thái.'));
            }
        } catch (e) { alert('Lỗi kết nối server.'); }
    }

    async function deleteDamagedRow(id) {
        if (!await nsConfirm('Xoá lần báo hư hỏng này?')) return;
        try {
            const res = await fetch('/api/damaged/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: id })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                await loadDamaged();
                refreshDamagedHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể xoá báo cáo.'));
            }
        } catch (e) { alert('Lỗi kết nối server.'); }
    }

    // ---- Ảnh đính kèm cho "Hàng Cần Xử Lý" ----
    // Ảnh CHỈ được tải khi mở modal này (không tải kèm trong danh sách
    // chính) để tiết kiệm băng thông; số lượng ảnh hiển thị trên nút
    // "Ảnh" ở bảng đã có sẵn từ /api/damaged (image_count), không cần gọi
    // API riêng chỉ để đếm.
    let currentDamagedImagesItemId = null;
    // Cache danh sách URL ảnh theo item id, dùng cho cả modal lẫn popup rê
    // chuột - tránh gọi lại API mỗi lần rê chuột qua cùng 1 dòng.
    const damagedImagesCache = {};

    async function showDamagedImageHoverPreview(ev, itemId) {
        const wrap = document.getElementById('damagedImageHoverPreview');
        const img = document.getElementById('damagedImageHoverPreviewImg');
        positionDamagedImageHoverPreview(ev);

        let images = damagedImagesCache[itemId];
        if (!images) {
            try {
                const res = await fetch(`/api/damaged/${itemId}/images`);
                const result = await res.json();
                if (!res.ok || !result.success) return;
                images = result.data;
                damagedImagesCache[itemId] = images;
            } catch (e) { return; }
        }
        if (!images.length) return;

        // Nếu chuột đã rời nút trước khi ảnh tải xong thì thôi, không hiện.
        if (wrap.dataset.hoverItemId !== undefined && wrap.dataset.hoverItemId != itemId && wrap.style.display === 'none') return;

        img.src = images[0].url;
        wrap.dataset.hoverItemId = itemId;
        wrap.style.display = 'block';
    }

    function positionDamagedImageHoverPreview(ev) {
        const wrap = document.getElementById('damagedImageHoverPreview');
        const margin = 14;
        const size = 280 + 4; // ảnh + viền
        let left = ev.clientX + margin;
        let top = ev.clientY + margin;
        if (left + size > window.innerWidth) left = ev.clientX - size - margin;
        if (top + size > window.innerHeight) top = window.innerHeight - size - margin;
        if (top < 0) top = margin;
        wrap.style.left = left + 'px';
        wrap.style.top = top + 'px';
    }

    function hideDamagedImageHoverPreview() {
        const wrap = document.getElementById('damagedImageHoverPreview');
        wrap.style.display = 'none';
        delete wrap.dataset.hoverItemId;
    }

    // Nhấp vào BẤT KỲ đâu trên hàng ngang (trừ nút/ô nhập bên trong) đều mở
    // modal xem ảnh - tiện hơn phải nhắm đúng nút camera nhỏ.
    function handleDamagedRowClick(ev, itemId, partCode) {
        if (ev.target.closest('button, input, a, select, textarea')) return;
        openDamagedImages(itemId, partCode);
    }

    async function openDamagedImages(itemId, partCode) {
        currentDamagedImagesItemId = itemId;
        document.getElementById('damagedImagesPartCode').innerText = partCode || '';
        new bootstrap.Modal(document.getElementById('damagedImagesModal')).show();
        await loadDamagedImages(itemId);
    }

    async function loadDamagedImages(itemId) {
        const grid = document.getElementById('damagedImagesGrid');
        const emptyEl = document.getElementById('damagedImagesEmpty');
        grid.innerHTML = '<div class="col-12 text-center text-muted small py-3">Đang tải ảnh...</div>';
        try {
            const res = await fetch(`/api/damaged/${itemId}/images`);
            const result = await res.json();
            if (!res.ok || !result.success) {
                grid.innerHTML = '';
                alert('Lỗi: ' + (result.error || 'Không tải được ảnh.'));
                return;
            }
            renderDamagedImagesGrid(result.data);
        } catch (e) {
            grid.innerHTML = '';
            alert('Lỗi kết nối server.');
        }
    }

    function renderDamagedImagesGrid(images) {
        const grid = document.getElementById('damagedImagesGrid');
        const emptyEl = document.getElementById('damagedImagesEmpty');
        if (!images || images.length === 0) {
            grid.innerHTML = '';
            emptyEl.style.display = '';
            return;
        }
        emptyEl.style.display = 'none';
        grid.innerHTML = images.map(img => `
            <div class="col-4 col-sm-3">
                <div style="position:relative;">
                    <img src="${img.url}" class="img-zoomable rounded border" style="width:100%; aspect-ratio:1/1; object-fit:cover; cursor:zoom-in;" onclick="openImageLightbox(this.src, event)">
                    <button class="btn btn-danger btn-sm" title="Xoá ảnh" onclick="deleteDamagedImage(${img.id})"
                        style="position:absolute; top:2px; right:2px; padding:0 5px; line-height:1.6;">
                        <i class="bi bi-x"></i>
                    </button>
                </div>
            </div>
        `).join('');
    }

    const damagedImageUploadInput = document.getElementById('damagedImageUploadInput');
    if (damagedImageUploadInput) {
        damagedImageUploadInput.addEventListener('change', async () => {
            const file = damagedImageUploadInput.files[0];
            damagedImageUploadInput.value = '';
            if (!file || !currentDamagedImagesItemId) return;

            const formData = new FormData();
            formData.append('image', file);
            document.getElementById('loading-overlay').style.display = 'flex';
            try {
                const res = await fetch(`/api/damaged/${currentDamagedImagesItemId}/images/upload`, {
                    method: 'POST',
                    body: formData
                });
                const result = await res.json();
                if (res.ok && result.success) {
                    delete damagedImagesCache[currentDamagedImagesItemId];
                    await loadDamagedImages(currentDamagedImagesItemId);
                    await loadDamaged(); // cập nhật số ảnh hiển thị trên bảng
                } else {
                    alert('Lỗi: ' + (result.error || 'Không thể tải ảnh lên.'));
                }
            } catch (e) {
                alert('Lỗi kết nối server.');
            } finally {
                document.getElementById('loading-overlay').style.display = 'none';
            }
        });
    }

    async function deleteDamagedImage(imageId) {
        if (!await nsConfirm('Xoá ảnh này?')) return;
        try {
            const res = await fetch('/api/damaged/image/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: imageId })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                delete damagedImagesCache[currentDamagedImagesItemId];
                await loadDamagedImages(currentDamagedImagesItemId);
                await loadDamaged();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể xoá ảnh.'));
            }
        } catch (e) { alert('Lỗi kết nối server.'); }
    }

    const damagedManualForm = document.getElementById('damaged-manual-form');
    if (damagedManualForm) {
        damagedManualForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const partCode = document.getElementById('damaged-manual-part').value.trim();
            const qty = document.getElementById('damaged-manual-qty').value;
            const note = document.getElementById('damaged-manual-note').value;
            if (!partCode) { alert('Vui lòng nhập mã hàng.'); return; }
            if (!qty || Number(qty) <= 0) { alert('Vui lòng nhập số lượng hư hỏng hợp lệ.'); return; }

            const payload = { part_code: partCode, quantity: qty, note: note };
            if (CURRENT_ROLE === 'admin') {
                const storeEl = document.getElementById('damaged-manual-store');
                const storeCode = storeEl ? storeEl.value : '';
                if (!storeCode) { alert('Vui lòng chọn chi nhánh.'); return; }
                payload.store_code = storeCode;
            }
            try {
                const res = await fetch('/api/damaged/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const result = await res.json();
                if (res.ok && result.success) {
                    damagedManualForm.reset();
                    await loadDamaged();
                    refreshDamagedHighlights();
                } else {
                    alert('Lỗi: ' + (result.error || 'Không thể lưu báo cáo hư hỏng.'));
                }
            } catch (e) { alert('Lỗi kết nối server.'); }
        });
    }

    const damagedUploadForm = document.getElementById('damaged-upload-form');
    if (damagedUploadForm) {
        damagedUploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const fileInput = document.getElementById('damaged_file');
            const file = fileInput.files[0];
            if (!file) { alert('Vui lòng chọn file hàng cần xử lý để tải lên.'); return; }

            const formData = new FormData();
            formData.append('damaged_file', file);
            if (CURRENT_ROLE === 'admin') {
                const storeEl = document.getElementById('damaged-upload-store');
                const storeCode = storeEl ? storeEl.value : '';
                if (!storeCode) { alert('Vui lòng chọn chi nhánh.'); return; }
                formData.append('store_code', storeCode);
            }

            document.getElementById('loading-overlay').style.display = 'flex';
            try {
                const res = await fetch('/api/damaged/import-excel', { method: 'POST', body: formData });
                const result = await res.json();
                if (res.ok) {
                    alert(`Đã thêm ${result.applied.toLocaleString()} lần báo hư hỏng (bỏ qua ${result.not_in_stock.toLocaleString()} mã hàng chưa có trong danh sách admin đã import, ${result.skipped_rows.toLocaleString()} dòng thiếu mã hàng/số lượng không hợp lệ).`);
                    document.getElementById('damaged-file-name').innerText = '';
                    fileInput.value = '';
                    await loadDamaged();
                    refreshDamagedHighlights();
                } else {
                    alert('Lỗi: ' + result.error);
                }
            } catch (e) { alert('Lỗi kết nối server.'); }
            finally { document.getElementById('loading-overlay').style.display = 'none'; }
        });
    }

    // Sửa giá bán thủ công trực tiếp trên bảng Tồn Kho (chỉ admin) - chuyển
    // ô Giá Bán của dòng đang bấm "Sửa" thành 1 ô nhập số, kèm nút Lưu/Huỷ.
    // Để trống rồi Lưu = xoá giá (mã hàng quay về trạng thái "chưa có giá").
    function editPriceRow(btn, partCode) {
        const priceCell = btn.closest('.price-col');
        if (!priceCell) {
            alert('Không tìm thấy ô giá bán!');
            return;
        }
        const item = (globalInventory || []).find(i => i.part_code === partCode);
        const currentVal = (item && item.sale_price !== null && item.sale_price !== undefined) ? Math.round(item.sale_price) : '';
        priceCell.innerHTML = `
            <div class="price-edit-wrap" style="display:flex; gap:4px; align-items:center; justify-content:flex-end;">
                <input type="number" min="0" step="1" class="form-control form-control-sm price-edit-input" value="${currentVal}" placeholder="Giá bán">
                <button class="btn btn-success btn-sm" title="Lưu" onclick="savePriceRow(this, '${escapeHtmlAttr(partCode)}')"><i class="bi bi-check-lg"></i></button>
                <button class="btn btn-outline-secondary btn-sm" title="Huỷ" onclick="filterInventory()"><i class="bi bi-x-lg"></i></button>
            </div>
        `;
        const priceInput = priceCell.querySelector('.price-edit-input');
        priceInput.focus();
        priceInput.select();
    }

    async function savePriceRow(btn, partCode) {
        const priceCell = btn.closest('.price-col');
        if (!priceCell) {
            alert('Không tìm thấy ô giá bán!');
            return;
        }
        const input = priceCell.querySelector('.price-edit-input');
        const raw = input ? input.value.trim() : '';
        if (raw !== '' && (isNaN(Number(raw)) || Number(raw) < 0)) {
            alert('Giá bán không hợp lệ.');
            return;
        }
        try {
            const res = await fetch('/api/admin/update-price', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ part_code: partCode, sale_price: raw === '' ? null : Number(raw) })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                // Cập nhật thẳng vào globalInventory (đang có sẵn trong bộ
                // nhớ) rồi vẽ lại bảng ngay - KHÔNG gọi lại /api/inventory,
                // để giá mới hiện ra tức thì, không cần tải lại dữ liệu.
                const item = (globalInventory || []).find(i => i.part_code === partCode);
                if (item) item.sale_price = result.sale_price;
                filterInventory();
                if (document.getElementById('quick-lookup-body')) filterQuickLookup();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể lưu giá bán.'));
            }
        } catch (e) {
            alert('Lỗi kết nối server.');
        }
    }

    // ============================================================
    // ĐỀ XUẤT TĂNG GIÁ (1 mã hàng) - dùng chung cho admin lẫn store.
    // ============================================================
    function fmtPriceAdjMoney(v) {
        return (v === null || v === undefined || v === '') ? '-' : Math.round(v).toLocaleString() + 'đ';
    }

    // Debounce ô tìm kiếm mã/tên hàng ở bảng danh sách - giống mẫu
    // debouncedFilterInventory() đang dùng ở tab Tồn Kho.
    let priceAdjSearchTimer = null;
    function debouncedLoadPriceAdjustmentList() {
        clearTimeout(priceAdjSearchTimer);
        priceAdjSearchTimer = setTimeout(loadPriceAdjustmentList, 350);
    }

    let priceAdjStatusFilter = 'all';
    function setPriceAdjStatusFilter(status, btn) {
        priceAdjStatusFilter = status;
        document.querySelectorAll('#price-adj-status-tabs .nav-link').forEach(el => el.classList.remove('active'));
        if (btn) btn.classList.add('active');
        loadPriceAdjustmentList();
    }

    async function loadPriceAdjustmentList() {
        const tbody = document.getElementById('price-adj-body');
        if (!tbody) return;
        const q = (document.getElementById('price-adj-search') || {}).value || '';
        try {
            const params = new URLSearchParams({ status: priceAdjStatusFilter, q: q, limit: 500 });
            const res = await fetch('/api/price-adjustment/list?' + params.toString());
            const result = await res.json();
            if (!res.ok || !result.success) {
                tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-danger">Lỗi: ${escapeHtmlAttr(result.error || 'Không tải được dữ liệu.')}</td></tr>`;
                return;
            }
            renderPriceAdjustmentTable(result.data);
            document.getElementById('price-adj-visible-count').innerText = result.data.length.toLocaleString();
            const countAdjEl = document.getElementById('price-adj-count-adjusted');
            const countNotAdjEl = document.getElementById('price-adj-count-not-adjusted');
            if (countAdjEl) countAdjEl.innerText = result.total_adjusted.toLocaleString();
            if (countNotAdjEl) countNotAdjEl.innerText = result.total_not_adjusted.toLocaleString();
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-danger">Lỗi kết nối server.</td></tr>`;
        }
    }

    // Xuất Excel danh sách mã hàng: backend xuất TOÀN BỘ dòng khớp tab + ô tìm
    // kiếm đang chọn (không bị giới hạn 500 dòng như bảng trên màn hình).
    async function exportPriceAdjList(btn) {
        const q = (document.getElementById('price-adj-search') || {}).value || '';
        const params = new URLSearchParams({ status: priceAdjStatusFilter, q: q });
        const oldHtml = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Đang xuất...'; }
        try {
            const res = await fetch('/api/price-adjustment/export?' + params.toString());
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                showToast('Lỗi', err.error || 'Không xuất được file Excel.', 'danger');
                return;
            }
            const cd = res.headers.get('Content-Disposition') || '';
            const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
            const name = m ? decodeURIComponent(m[1]) : 'de-xuat-tang-gia.xlsx';
            const url = URL.createObjectURL(await res.blob());
            const a = document.createElement('a');
            a.href = url; a.download = name;
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (e) {
            showToast('Lỗi', 'Lỗi kết nối server.', 'danger');
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = oldHtml; }
        }
    }

    // Lưu lại dữ liệu của lần tải bảng gần nhất (theo proposal_id) để mở
    // modal Sửa không cần gọi thêm API - bấm nút Sửa là có sẵn dữ liệu hiện
    // đang hiển thị trên bảng để đổ vào form ngay.
    let priceAdjRowsById = {};

    function renderPriceAdjustmentTable(data) {
        const tbody = document.getElementById('price-adj-body');
        if (data.length === 0) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-muted">Không có mã hàng nào khớp.</td></tr>`;
            return;
        }
        priceAdjRowsById = {};
        data.forEach(r => { if (r.proposal_id) priceAdjRowsById[r.proposal_id] = r; });
        tbody.innerHTML = data.map(r => `
            <tr>
                <td class="fw-semibold">${escapeHtmlAttr(r.part_code)}</td>
                <td>${escapeHtmlAttr(r.part_name || '-')}</td>
                <td class="text-end">${r.thue !== null && r.thue !== undefined ? r.thue + '%' : '-'}</td>
                <td class="text-end">${fmtPriceAdjMoney(r.gia_de_xuat_hvn)}</td>
                <td class="text-end fw-semibold text-success">${fmtPriceAdjMoney(r.gia_ban)}</td>
                <td>${escapeHtmlAttr(r.created_by || '-')}</td>
                <td>${escapeHtmlAttr(r.created_at || '-')}</td>
                <td>${r.is_adjusted
                    ? '<span class="badge bg-success">Đã điều chỉnh</span>'
                    : '<span class="badge bg-secondary">Chưa điều chỉnh</span>'}
                </td>
                <td class="text-center">${r.proposal_id
                    ? `<button type="button" class="btn btn-outline-primary btn-sm me-1" onclick="openPriceAdjEditModal(${r.proposal_id})"><i class="bi bi-pencil-square me-1"></i>Sửa</button>
                       <button type="button" class="btn btn-outline-danger btn-sm" onclick="resetPriceAdjCode('${escapeHtmlAttr(r.part_code)}')"><i class="bi bi-arrow-counterclockwise me-1"></i>Về chưa tăng</button>`
                    : '-'}
                </td>
            </tr>
        `).join('');
    }

    // Mở modal sửa 1 lần đề xuất đã lưu (Tên hàng / % Tăng giá / Giá cũ /
    // Giá bán) - dùng chung cho mọi vai trò (admin lẫn store), để tự sửa
    // được nếu nhập sai, KHÔNG cần quyền admin (khác nút Xoá vốn chỉ admin).
    function openPriceAdjEditModal(proposalId) {
        const r = priceAdjRowsById[proposalId];
        if (!r) { showToast('Lỗi', 'Không tìm thấy dữ liệu dòng này, vui lòng tải lại danh sách.', 'danger'); return; }
        document.getElementById('price-adj-edit-id').value = proposalId;
        document.getElementById('price-adj-edit-part-code').innerText = r.part_code;
        document.getElementById('price-adj-edit-part-name').value = r.part_name || '';
        document.getElementById('price-adj-edit-thue').value = r.thue !== null && r.thue !== undefined ? r.thue : '';
        document.getElementById('price-adj-edit-gia-cu').value = r.gia_de_xuat_hvn !== null && r.gia_de_xuat_hvn !== undefined ? r.gia_de_xuat_hvn : '';
        document.getElementById('price-adj-edit-gia-ban').value = r.gia_ban !== null && r.gia_ban !== undefined ? r.gia_ban : '';
        new bootstrap.Modal(document.getElementById('priceAdjEditModal')).show();
    }

    // Tính lại Giá bán = Giá cũ + (% Tăng giá) * Giá cũ, làm tròn đến hàng
    // nghìn - giống hệt công thức Bước 2 ở server (_compute_gia_ban_tu_gia_cu),
    // dùng để gợi ý nhanh trong modal sửa; người dùng vẫn có thể tự gõ đè lại
    // Giá bán nếu muốn 1 con số khác (VD lệch làm tròn so với Excel gốc).
    function recalcPriceAdjEditGiaBan() {
        const thue = parseFloat(document.getElementById('price-adj-edit-thue').value);
        const giaCu = parseFloat(document.getElementById('price-adj-edit-gia-cu').value);
        if (isNaN(thue) || isNaN(giaCu)) return;
        const raw = giaCu + (giaCu * thue / 100);
        document.getElementById('price-adj-edit-gia-ban').value = Math.round(raw / 1000) * 1000;
    }

    // Xoá TOÀN BỘ lịch sử đề xuất tăng giá của 1 mã hàng, đưa mã đó về lại
    // trạng thái "Chưa điều chỉnh" trên bảng (khác nút Sửa - chỉ sửa số
    // liệu của lần đề xuất gần nhất, không xoá trạng thái). Dùng chung cho
    // mọi vai trò, có xác nhận trước vì đây là thao tác xoá dữ liệu.
    async function resetPriceAdjCode(partCode) {
        if (!await nsConfirm(`Xác nhận đưa mã "${partCode}" về trạng thái CHƯA TĂNG GIÁ?\n\nToàn bộ lịch sử đề xuất tăng giá trước đó của mã này sẽ bị xoá và KHÔNG thể khôi phục lại.`)) {
            return;
        }
        try {
            const res = await fetch('/api/price-adjustment/reset/' + encodeURIComponent(partCode), { method: 'DELETE' });
            const result = await res.json();
            if (!res.ok || !result.success) {
                showToast('Lỗi', result.error || 'Không thể xoá lịch sử điều chỉnh.', 'danger');
                return;
            }
            showToast('Đã xoá', `Mã ${result.part_code} đã quay về trạng thái Chưa Điều Chỉnh.`, 'success');
            loadPriceAdjustmentList();
        } catch (e) {
            showToast('Lỗi', 'Lỗi kết nối server.', 'danger');
        }
    }

    async function submitPriceAdjEdit(event) {
        event.preventDefault();
        const proposalId = document.getElementById('price-adj-edit-id').value;
        const payload = {
            part_name: document.getElementById('price-adj-edit-part-name').value,
            thue: document.getElementById('price-adj-edit-thue').value,
            gia_de_xuat_hvn: document.getElementById('price-adj-edit-gia-cu').value,
            gia_ban: document.getElementById('price-adj-edit-gia-ban').value,
        };
        try {
            const res = await fetch('/api/price-adjustment/proposals/' + proposalId, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const result = await res.json();
            if (!res.ok || !result.success) {
                showToast('Lỗi', result.error || 'Không thể lưu sửa đổi.', 'danger');
                return false;
            }
            bootstrap.Modal.getInstance(document.getElementById('priceAdjEditModal')).hide();
            showToast('Đã lưu', `Đã cập nhật lại giá cho mã ${result.part_code}.`, 'success');
            loadPriceAdjustmentList();
        } catch (e) {
            showToast('Lỗi', 'Lỗi kết nối server.', 'danger');
        }
        return false;
    }

    // Gõ/rời khỏi ô Mã hàng: tự điền Tên hàng (ưu tiên tra nhanh trong
    // globalInventory đang có sẵn ở trình duyệt cho tức thì), sau đó gọi API
    // /api/price-adjustment/lookup để lấy thêm Thuế đã dùng lần gần nhất và
    // cảnh báo nếu mã này đã từng được đề xuất tăng giá trước đó.
    let priceAdjLookupTimer = null;

    // Giá bán hiện có (giá bán cũ) của mã hàng trong danh mục/tồn kho hệ
    // thống - trả về null nếu mã chưa có trong danh mục HOẶC chưa có giá
    // bán (khi đó form quay về chế độ nhập đủ Thuế + Giá đề xuất HVN).
    // Ưu tiên tra nhanh cục bộ trong globalInventory (tồn kho hệ thống);
    // nếu không có, fallback sang priceAdjServerCatalogPriceCache - giá bán
    // gần nhất mà server đã cho biết qua /api/price-adjustment/lookup, áp
    // dụng cho những mã KHÔNG có trong tồn kho hệ thống (mã tự thêm qua
    // chính tính năng Đề Xuất Tăng Giá) - đặc biệt cần thiết để mã vẫn hiện
    // được "giá cũ" ngay cả sau khi bị "Về chưa tăng" xoá sạch lịch sử đề
    // xuất (xem resetPriceAdjCode / last_known_price ở price_adjustment.py).
    let priceAdjServerCatalogPriceCache = {};
    function getPriceAdjCatalogPrice(partCode) {
        const code = (partCode || '').trim().toLowerCase();
        if (!code) return null;
        const found = globalInventory.find(it => (it.part_code || '').toLowerCase() === code);
        if (found && found.sale_price !== null && found.sale_price !== undefined && found.sale_price !== '') {
            const price = Number(found.sale_price);
            if (isFinite(price) && price > 0) return price;
        }
        const serverPrice = priceAdjServerCatalogPriceCache[(partCode || '').trim().toUpperCase()];
        return (typeof serverPrice === 'number' && isFinite(serverPrice) && serverPrice > 0) ? serverPrice : null;
    }

    // Bật/tắt chế độ "mã đã có giá trong danh mục": khoá ô Thuế + Giá đề
    // xuất HVN (không cần nhập), còn lại tự tính Giá bán = Giá bán cũ +
    // Mức tăng (%) (ô Mức tăng vẫn luôn bật, áp dụng cho cả 2 chế độ).
    function updatePriceAdjMode() {
        const partCode = document.getElementById('price-adj-part-code').value;
        const catalogPrice = getPriceAdjCatalogPrice(partCode);
        const thueInput = document.getElementById('price-adj-thue');
        const hvnInput = document.getElementById('price-adj-gia-hvn');
        const fromCatalog = catalogPrice !== null;

        thueInput.disabled = fromCatalog;
        hvnInput.disabled = fromCatalog;
        thueInput.required = !fromCatalog;
        hvnInput.required = !fromCatalog;
        if (fromCatalog) {
            thueInput.value = '';
            hvnInput.value = '';
            thueInput.placeholder = 'Không cần nhập';
            hvnInput.placeholder = 'Không cần nhập';
        } else {
            thueInput.placeholder = 'VD: 8';
            hvnInput.placeholder = '0';
        }
        recalcPriceAdjGiaBan();
    }

    // Gõ/rời khỏi ô Mã hàng: tự điền Tên hàng (ưu tiên tra nhanh trong
    // globalInventory đang có sẵn ở trình duyệt cho tức thì), sau đó gọi API
    // /api/price-adjustment/lookup để lấy thêm Thuế đã dùng lần gần nhất và
    // cảnh báo nếu mã này đã từng được đề xuất tăng giá trước đó.
    function onPriceAdjPartCodeChange() {
        const partCode = (document.getElementById('price-adj-part-code').value || '').trim();
        const nameInput = document.getElementById('price-adj-part-name');
        const hintEl = document.getElementById('price-adj-hint');
        if (!partCode) {
            hintEl.innerHTML = '';
            if (nameInput.dataset.auto === '1') { nameInput.value = ''; nameInput.dataset.auto = '0'; }
            updatePriceAdjMode();
            return;
        }
        // Tra nhanh cục bộ trước (không cần chờ mạng) nếu mã đã có trong tồn kho.
        const localName = (typeof findInventoryPartName === 'function') ? findInventoryPartName(partCode) : '';
        if (localName && (!nameInput.value || nameInput.dataset.auto === '1')) {
            nameInput.value = localName;
            nameInput.dataset.auto = '1';
        } else if (!localName && nameInput.dataset.auto === '1') {
            // Tên đang hiện là do tự điền cho mã trước đó - xoá đi khi mã đã đổi
            nameInput.value = '';
            nameInput.dataset.auto = '0';
        }
        updatePriceAdjMode();

        clearTimeout(priceAdjLookupTimer);
        priceAdjLookupTimer = setTimeout(async () => {
            try {
                // Chuẩn hoá về CHỮ HOA trước khi tra cứu - cùng lý do như ở phần
                // Kiểm Tra Hàng Loạt (xem checkPriceAdjBulk).
                const res = await fetch('/api/price-adjustment/lookup?part_code=' + encodeURIComponent(partCode.toUpperCase()));
                const result = await res.json();
                if (!res.ok || !result.success) return;
                // Người dùng đã gõ sang mã khác trong lúc chờ mạng - bỏ kết quả cũ
                if ((document.getElementById('price-adj-part-code').value || '').trim() !== partCode) return;

                // Cập nhật cache giá từ server rồi áp dụng lại chế độ form -
                // giúp các mã KHÔNG có trong tồn kho hệ thống (nên client-side
                // getPriceAdjCatalogPrice ban đầu không tìm thấy gì) vẫn
                // chuyển được sang chế độ "đã có giá trong danh mục" ngay khi
                // server cho biết mã này đã có last_known_price (VD: vừa bị
                // "Về chưa tăng" xong, gõ lại mã là có sẵn giá cũ luôn).
                const upperCode = partCode.toUpperCase();
                if (typeof result.catalog_price === 'number' && result.catalog_price > 0) {
                    priceAdjServerCatalogPriceCache[upperCode] = result.catalog_price;
                } else {
                    delete priceAdjServerCatalogPriceCache[upperCode];
                }
                updatePriceAdjMode();

                const fromCatalog = getPriceAdjCatalogPrice(partCode) !== null;

                if (result.part_name && !nameInput.value) nameInput.value = result.part_name;
                if (!fromCatalog && result.thue_suggest !== null && result.thue_suggest !== undefined) {
                    const thueInput = document.getElementById('price-adj-thue');
                    if (thueInput && !thueInput.value) {
                        thueInput.value = result.thue_suggest;
                        recalcPriceAdjGiaBan();
                    }
                }

                if (result.already_adjusted && result.last_proposal) {
                    const lp = result.last_proposal;
                    hintEl.innerHTML = `<span class="text-warning-emphasis"><i class="bi bi-exclamation-circle-fill me-1"></i>Mã này đã được đề xuất tăng giá lúc ${escapeHtmlAttr(lp.created_at || '')} bởi ${escapeHtmlAttr(lp.created_by || '')} - Giá bán lần trước: ${fmtPriceAdjMoney(lp.gia_ban)}. Lưu tiếp sẽ ghi thêm 1 lần đề xuất mới.</span>`;
                } else if (fromCatalog && !result.in_catalog) {
                    hintEl.innerHTML = `<span class="text-success"><i class="bi bi-check-circle me-1"></i>${result.in_price_list ? 'Mã có trong Bảng giá' : 'Đã tìm thấy giá'} - Giá bán hiện tại: ${fmtPriceAdjMoney(getPriceAdjCatalogPrice(partCode))}. <strong>Mã này CHƯA được tăng giá</strong>${result.part_name ? '' : ' (chưa có Tên hàng, có thể bỏ trống)'} - bấm Lưu Đề Xuất để cộng thêm Mức tăng (%).</span>`;
                } else if (!result.in_catalog && !fromCatalog) {
                    hintEl.innerHTML = `<span class="text-muted"><i class="bi bi-info-circle me-1"></i>Mã hàng chưa có trong tồn kho hệ thống - vui lòng nhập đủ Tên hàng, Thuế và Giá đề xuất HVN, hệ thống sẽ tự lưu vào danh mục.</span>`;
                } else {
                    hintEl.innerHTML = '';
                }
            } catch (e) { /* bỏ qua lỗi tra cứu - không chặn người dùng nhập tiếp */ }
        }, 300);
    }

    // Tính Giá bán mới. 2 chế độ:
    //  - Mã đã có giá bán trong danh mục: Giá cũ = giá bán hiện có (làm
    //    tròn hàng nghìn), Giá bán = Giá cũ + Mức tăng (%). Không cần Thuế/HVN.
    //  - Mã mới / chưa có giá: Giá cũ = (HVN * Thuế) + HVN, rồi + Mức tăng (%).
    // Mức tăng (%) do người dùng tự nhập ở ô "price-adj-muc-tang" (mặc định
    // 5%, có thể sửa thành số khác) - KHÔNG còn cố định 5% như trước.
    function calcPriceAdjValues() {
        const partCode = document.getElementById('price-adj-part-code').value;
        const catalogPrice = getPriceAdjCatalogPrice(partCode);
        const mucTang = parseFloat(document.getElementById('price-adj-muc-tang').value);
        if (isNaN(mucTang) || mucTang < 0) return null;
        let giaCu;
        if (catalogPrice !== null) {
            giaCu = Math.round(catalogPrice / 1000) * 1000;
        } else {
            const thue = parseFloat(document.getElementById('price-adj-thue').value);
            const giaHvn = parseFloat(document.getElementById('price-adj-gia-hvn').value);
            if (isNaN(thue) || isNaN(giaHvn) || giaHvn <= 0) return null;
            // Bước 1: Giá cũ = (Giá đề xuất HVN * Thuế) + Giá đề xuất HVN
            giaCu = Math.round(((giaHvn * (thue / 100)) + giaHvn) / 1000) * 1000;
        }
        // Bước 2: Giá bán = Giá cũ + Mức tăng (%) Giá cũ (không dùng Thuế)
        const giaBan = Math.round(((giaCu * (mucTang / 100)) + giaCu) / 1000) * 1000;
        return { giaCu, giaBan, mucTang, fromCatalog: catalogPrice !== null };
    }

    function recalcPriceAdjGiaBan() {
        const out = document.getElementById('price-adj-gia-ban');
        const calcHintEl = document.getElementById('price-adj-calc-hint');
        const r = calcPriceAdjValues();
        if (!r) {
            out.value = '-';
            if (calcHintEl) calcHintEl.innerHTML = '';
            return;
        }
        out.value = r.giaBan.toLocaleString() + 'đ';
        if (calcHintEl) {
            const label = r.fromCatalog ? 'Giá bán cũ' : 'Giá cũ';
            const mucTangLabel = Number.isInteger(r.mucTang) ? r.mucTang : r.mucTang.toString();
            calcHintEl.innerHTML = `<span class="text-muted">${label}: <strong>${r.giaCu.toLocaleString()}đ</strong> &rarr; Giá bán mới (+${mucTangLabel}%): <strong class="text-success">${r.giaBan.toLocaleString()}đ</strong></span>`;
        }
    }

    async function submitPriceAdjustmentProposal(event) {
        event.preventDefault();
        const partCode = document.getElementById('price-adj-part-code').value.trim();
        const partName = document.getElementById('price-adj-part-name').value.trim();
        let thue = document.getElementById('price-adj-thue').value;
        let giaHvn = document.getElementById('price-adj-gia-hvn').value;
        const mucTang = document.getElementById('price-adj-muc-tang').value;

        if (!partCode) { alert('Vui lòng nhập mã hàng.'); return false; }
        if (mucTang === '' || isNaN(Number(mucTang)) || Number(mucTang) < 0) { alert('Vui lòng nhập Mức tăng (%) hợp lệ.'); return false; }

        // Mã đã có giá bán trong danh mục: không cần Thuế/HVN. Cờ
        // from_catalog_price báo server tự lấy Giá bán hiện có (part_prices)
        // làm Giá cũ rồi cộng 5%. Vẫn gửi kèm Thuế 0% + HVN = Giá bán cũ để
        // server bản cũ (chưa có chế độ này) cũng tính ra cùng kết quả.
        const catalogCalc = calcPriceAdjValues();
        const fromCatalog = !!(catalogCalc && catalogCalc.fromCatalog);
        if (fromCatalog) {
            thue = '0';
            giaHvn = String(catalogCalc.giaCu);
        } else {
            if (thue === '' || isNaN(Number(thue))) { alert('Vui lòng nhập Thuế hợp lệ.'); return false; }
            if (giaHvn === '' || isNaN(Number(giaHvn)) || Number(giaHvn) <= 0) { alert('Vui lòng nhập Giá đề xuất HVN hợp lệ.'); return false; }
        }

        try {
            const res = await fetch('/api/price-adjustment/propose', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    part_code: partCode,
                    part_name: partName,
                    thue: Number(thue),
                    gia_de_xuat_hvn: Number(giaHvn),
                    from_catalog_price: fromCatalog,
                    muc_tang_percent: Number(mucTang),
                })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                showToast('Đã lưu', `Đã đề xuất tăng giá cho mã ${result.part_code} - Giá cũ: ${fmtPriceAdjMoney(result.gia_cu)} → Giá bán: ${fmtPriceAdjMoney(result.gia_ban)}`, 'success');
                document.getElementById('price-adj-form').reset();
                document.getElementById('price-adj-part-name').dataset.auto = '0';
                document.getElementById('price-adj-gia-ban').value = '-';
                document.getElementById('price-adj-hint').innerHTML = '';
                document.getElementById('price-adj-calc-hint').innerHTML = '';
                updatePriceAdjMode();
                loadPriceAdjustmentList();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể lưu đề xuất.'));
            }
        } catch (e) {
            alert('Lỗi kết nối server.');
        }
        return false;
    }

    // ============================================================
    // KIỂM TRA HÀNG LOẠT MÃ HÀNG (dán từ Excel) - admin dùng để rà soát
    // nhanh 1 danh sách mã bất kỳ đã được đề xuất tăng giá hay chưa, mà
    // không cần gõ tìm từng mã 1 ở bảng "Danh Sách Mã Hàng" bên dưới.
    // Tái dùng API /api/price-adjustment/lookup (đã có sẵn cho form đề
    // xuất 1 mã ở trên) - gọi lặp lại cho từng mã, giới hạn số request
    // chạy song song để không dồn cục server dev.
    // ============================================================
    let priceAdjBulkResult = [];
    let priceAdjBulkRunToken = 0; // đổi giá trị mỗi lần bấm Kiểm Tra để hủy kết quả của lần chạy trước nếu còn dở

    // Tách danh sách mã hàng dán vào: mỗi dòng lấy đúng CỘT ĐẦU TIÊN
    // (phòng trường hợp lỡ dán nhiều cột, ví dụ dán cả cột Mã hàng lẫn
    // Tên hàng cạnh nhau trong Excel), tách cột theo Tab/phẩy/chấm phẩy
    // để chấp nhận cả khi dán từ CSV. Loại bỏ dòng trống và loại trùng
    // (không phân biệt hoa/thường) nhưng vẫn giữ đúng chữ hoa/thường của
    // lần xuất hiện đầu tiên.
    function parsePriceAdjBulkCodes(raw) {
        const codes = raw
            .split(/\r\n|\r|\n/)
            .map(line => line.split(/\t|,|;/)[0].trim())
            .filter(Boolean);
        const seen = new Set();
        const unique = [];
        for (const c of codes) {
            const key = c.toUpperCase();
            if (seen.has(key)) continue;
            seen.add(key);
            unique.push(c);
        }
        return unique;
    }

    function clearPriceAdjBulk() {
        document.getElementById('price-adj-bulk-input').value = '';
        priceAdjBulkResult = [];
        priceAdjBulkRunToken++;
        document.getElementById('price-adj-bulk-progress').style.display = 'none';
        document.getElementById('price-adj-bulk-summary').style.display = 'none';
        document.getElementById('price-adj-bulk-result-wrap').style.display = 'none';
        document.getElementById('price-adj-bulk-body').innerHTML = '';
        document.getElementById('price-adj-bulk-export-btn').disabled = true;
    }

    async function checkPriceAdjBulk() {
        const raw = document.getElementById('price-adj-bulk-input').value || '';
        const codes = parsePriceAdjBulkCodes(raw);
        const progressEl = document.getElementById('price-adj-bulk-progress');
        const summaryEl = document.getElementById('price-adj-bulk-summary');
        const wrapEl = document.getElementById('price-adj-bulk-result-wrap');
        const bodyEl = document.getElementById('price-adj-bulk-body');
        const exportBtn = document.getElementById('price-adj-bulk-export-btn');

        if (codes.length === 0) {
            alert('Vui lòng dán ít nhất 1 mã hàng.');
            return;
        }

        const runToken = ++priceAdjBulkRunToken;
        priceAdjBulkResult = new Array(codes.length).fill(null);
        exportBtn.disabled = true;
        summaryEl.style.display = 'none';
        wrapEl.style.display = 'block';
        bodyEl.innerHTML = codes.map((c, i) => `
            <tr id="price-adj-bulk-row-${i}">
                <td class="text-muted">${i + 1}</td>
                <td class="fw-semibold">${escapeHtmlAttr(c)}</td>
                <td colspan="6" class="text-muted"><span class="spinner-border spinner-border-sm me-2"></span>Đang kiểm tra...</td>
            </tr>
        `).join('');
        progressEl.style.display = 'block';
        progressEl.innerText = `Đang kiểm tra 0/${codes.length} mã...`;

        let done = 0;
        const CONCURRENCY = 6; // số request chạy song song - xem run.py (threaded=True) chịu được mức này thoải mái
        let nextIndex = 0;

        async function worker() {
            while (true) {
                const i = nextIndex++;
                if (i >= codes.length) return;
                if (runToken !== priceAdjBulkRunToken) return; // đã bị hủy vì bấm Kiểm Tra 1 lần khác
                const code = codes[i];
                // Chuẩn hoá về CHỮ HOA trước khi tra cứu - mã hàng trong hệ thống
                // luôn lưu dạng chữ hoa, dán mã chữ thường (copy từ Excel) mà gửi
                // nguyên văn sẽ bị server coi là "không có trong hệ thống" dù mã
                // đó đã tồn tại và đã tăng giá.
                const lookupCode = code.toUpperCase();
                let item;
                try {
                    const res = await fetch('/api/price-adjustment/lookup?part_code=' + encodeURIComponent(lookupCode));
                    const result = await res.json();
                    if (!res.ok || !result.success) {
                        item = { part_code: code, error: result.error || 'Lỗi tra cứu' };
                    } else {
                        item = {
                            part_code: code,
                            part_name: result.part_name || '',
                            in_catalog: !!result.in_catalog,
                            already_adjusted: !!result.already_adjusted,
                            last_proposal: result.last_proposal || null,
                            catalog_price: result.catalog_price != null ? Number(result.catalog_price) : null,
                        };
                    }
                } catch (e) {
                    item = { part_code: code, error: 'Lỗi kết nối server' };
                }
                if (runToken !== priceAdjBulkRunToken) return;
                priceAdjBulkResult[i] = item;
                done++;
                renderPriceAdjBulkRow(i, item);
                progressEl.innerText = `Đang kiểm tra ${done}/${codes.length} mã...`;
            }
        }

        const workers = Array.from({ length: Math.min(CONCURRENCY, codes.length) }, () => worker());
        await Promise.all(workers);

        if (runToken !== priceAdjBulkRunToken) return; // đã bị hủy giữa chừng bởi 1 lần bấm Kiểm Tra khác
        progressEl.style.display = 'none';
        renderPriceAdjBulkSummary();
        exportBtn.disabled = false;
    }

    // Giá hiển thị/cộng tổng cho 1 dòng kết quả kiểm tra: ưu tiên giá bán ở
    // lần đề xuất gần nhất (mã "Đã tăng giá"); nếu mã chưa từng được đề
    // xuất ("Chưa tăng giá") thì dùng giá bán hiện có trong danh mục giá
    // (catalog_price) - mã hoàn toàn chưa có giá ở đâu thì trả về null.
    function priceAdjBulkEffectivePrice(item) {
        if (!item) return null;
        if (item.last_proposal && item.last_proposal.gia_ban != null) return Number(item.last_proposal.gia_ban);
        if (item.catalog_price != null) return Number(item.catalog_price);
        return null;
    }

    // "Dự bán tăng X%": CHỈ các mã "Chưa tăng giá" mới được cộng thêm X%, làm
    // tròn đến hàng nghìn TỪNG MÃ (cùng công thức/cơ chế làm tròn với giá bán
    // ở form đề xuất: Math.round((giá * tỷ lệ + giá) / 1000) * 1000). Mã đã
    // "Đã tăng giá" GIỮ NGUYÊN giá hiện tại (đã là giá sau tăng rồi, không
    // tăng thêm lần nữa). Mã không có giá trả về null. Đổi X ở hằng số dưới.
    const PRICE_ADJ_BULK_PROJECT_PCT = 5;
    function priceAdjBulkProjectedPrice(item, price) {
        if (price == null) return null;
        if (item && item.already_adjusted) return price;
        return Math.round(((price * (PRICE_ADJ_BULK_PROJECT_PCT / 100)) + price) / 1000) * 1000;
    }

    function priceAdjBulkStatusBadge(item) {
        if (item.error) return `<span class="badge bg-danger">${escapeHtmlAttr(item.error)}</span>`;
        if (!item.in_catalog && !item.already_adjusted) return `<span class="badge bg-dark-subtle text-dark-emphasis">Không có trong hệ thống</span>`;
        if (item.already_adjusted) return `<span class="badge bg-success">Đã tăng giá</span>`;
        return `<span class="badge bg-secondary">Chưa tăng giá</span>`;
    }

    function renderPriceAdjBulkRow(i, item) {
        const row = document.getElementById(`price-adj-bulk-row-${i}`);
        if (!row) return;
        const lp = item.last_proposal;
        const price = priceAdjBulkEffectivePrice(item);
        row.innerHTML = `
            <td class="text-muted">${i + 1}</td>
            <td class="fw-semibold">${escapeHtmlAttr(item.part_code)}</td>
            <td>${escapeHtmlAttr(item.part_name || '-')}</td>
            <td>${priceAdjBulkStatusBadge(item)}</td>
            <td class="text-end">${price != null ? fmtPriceAdjMoney(price) : '-'}</td>
            <td class="text-end fw-semibold text-info-emphasis">${price != null ? fmtPriceAdjMoney(priceAdjBulkProjectedPrice(item, price)) : '-'}</td>
            <td>${lp ? escapeHtmlAttr(lp.created_by || '-') : '-'}</td>
            <td>${lp ? escapeHtmlAttr(lp.created_at || '-') : '-'}</td>
        `;
    }

    function renderPriceAdjBulkSummary() {
        const summaryEl = document.getElementById('price-adj-bulk-summary');
        const total = priceAdjBulkResult.length;
        const adjusted = priceAdjBulkResult.filter(r => r && !r.error && r.already_adjusted).length;
        const notAdjusted = priceAdjBulkResult.filter(r => r && !r.error && !r.already_adjusted && r.in_catalog).length;
        const notFound = priceAdjBulkResult.filter(r => r && !r.error && !r.already_adjusted && !r.in_catalog).length;
        const errored = priceAdjBulkResult.filter(r => r && r.error).length;
        // Tổng tiền = cộng dồn giá hiển thị (Đã tăng giá: giá bán lần đề
        // xuất gần nhất; Chưa tăng giá: giá bán hiện có trong danh mục giá)
        // của MỌI mã tra được giá - mã lỗi hoặc hoàn toàn chưa có giá ở đâu
        // (chưa từng đề xuất VÀ chưa có trong danh mục giá) không tính vào
        // tổng. Đếm riêng số mã có giá (priced) để hiển thị rõ tổng này
        // tính trên bao nhiêu mã, tránh hiểu nhầm là tổng của toàn bộ danh
        // sách đã dán vào.
        const priced = priceAdjBulkResult.filter(r => priceAdjBulkEffectivePrice(r) != null);
        const totalMoney = priced.reduce((sum, r) => sum + priceAdjBulkEffectivePrice(r), 0);
        const projectedMoney = priced.reduce((sum, r) => sum + priceAdjBulkProjectedPrice(r, priceAdjBulkEffectivePrice(r)), 0);
        summaryEl.style.display = 'flex';
        summaryEl.innerHTML = `
            <span class="badge bg-primary-subtle text-primary-emphasis fs-6 fw-normal">Tổng: ${total}</span>
            <span class="badge bg-success-subtle text-success-emphasis fs-6 fw-normal">Đã tăng giá: ${adjusted}</span>
            <span class="badge bg-secondary-subtle text-secondary-emphasis fs-6 fw-normal">Chưa tăng giá: ${notAdjusted}</span>
            <span class="badge bg-dark-subtle text-dark-emphasis fs-6 fw-normal">Không có trong hệ thống: ${notFound}</span>
            ${errored ? `<span class="badge bg-danger-subtle text-danger-emphasis fs-6 fw-normal">Lỗi: ${errored}</span>` : ''}
            <span class="badge bg-warning-subtle text-warning-emphasis fs-6 fw-normal">Tổng tiền (${priced.length} mã có giá): ${fmtPriceAdjMoney(totalMoney)}</span>
            <span class="badge bg-info-subtle text-info-emphasis fw-normal d-inline-flex flex-column align-items-start" title="Tổng giá của ${priced.length} mã có giá khi các mã CHƯA tăng giá được tăng ${PRICE_ADJ_BULK_PROJECT_PCT}% (làm tròn đến nghìn từng mã); mã ĐÃ tăng giá giữ nguyên">
                <span class="fs-6">${fmtPriceAdjMoney(projectedMoney)}</span>
                <span class="small">Dự bán tăng ${PRICE_ADJ_BULK_PROJECT_PCT}%</span>
            </span>
        `;
    }

    // Xuất kết quả kiểm tra hàng loạt ra Excel bằng thư viện SheetJS đã
    // nạp sẵn cho toàn trang (xem thẻ <script> xlsx ở đầu <head>).
    function exportPriceAdjBulkResult() {
        if (!priceAdjBulkResult.length || typeof XLSX === 'undefined') return;
        const rows = priceAdjBulkResult.map((item, i) => {
            const lp = item && item.last_proposal;
            const price = priceAdjBulkEffectivePrice(item);
            let statusText = '-';
            if (item && item.error) statusText = 'Lỗi: ' + item.error;
            else if (item && !item.in_catalog && !item.already_adjusted) statusText = 'Không có trong hệ thống';
            else if (item && item.already_adjusted) statusText = 'Đã tăng giá';
            else if (item) statusText = 'Chưa tăng giá';
            return {
                'STT': i + 1,
                'Mã Hàng': item ? item.part_code : '',
                'Tên Hàng': item ? (item.part_name || '') : '',
                'Trạng Thái': statusText,
                'Giá Bán Hiện Tại': price != null ? price : '',
                [`Giá Bán Dự Báo Tăng ${PRICE_ADJ_BULK_PROJECT_PCT}%`]: price != null ? priceAdjBulkProjectedPrice(item, price) : '',
                'Người Đề Xuất': lp ? (lp.created_by || '') : '',
                'Ngày Điều Chỉnh': lp ? (lp.created_at || '') : '',
            };
        });
        const ws = XLSX.utils.json_to_sheet(rows);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, 'Kiem Tra Tang Gia');
        XLSX.writeFile(wb, `kiem-tra-tang-gia-${new Date().toISOString().slice(0,10)}.xlsx`);
    }

    // Import hàng loạt từ file Excel "ĐÃ ĐIỀU CHỈNH" (chỉ admin) - đọc trực
    // tiếp cấu trúc file nội bộ, tự dò sheet/cột phù hợp ở backend, nạp mỗi
    // dòng hợp lệ thành 1 lần đề xuất mới rồi tải lại bảng danh sách.
    const priceAdjImportForm = document.getElementById('price-adj-import-form');
    if (priceAdjImportForm) {
        priceAdjImportForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const file = document.getElementById('price_adj_import_file').files[0];
            if (!file) {
                alert('Vui lòng chọn file Excel để import.');
                return;
            }
            const formData = new FormData();
            formData.append('file', file);

            document.getElementById('loading-overlay').style.display = 'flex';
            try {
                const res = await fetch('/api/price-adjustment/import', { method: 'POST', body: formData });
                const result = await res.json();
                if (res.ok && result.success) {
                    const dupTxt = result.duplicate_skipped ? `, bỏ qua ${result.duplicate_skipped.toLocaleString()} dòng đã import trùng trước đó` : '';
                    showToast('Đã import', `Đã nạp ${result.total_imported.toLocaleString()} mã hàng từ sheet "${result.sheet_name}" (bỏ qua ${result.skipped_rows.toLocaleString()} dòng không hợp lệ${dupTxt}).`, 'success');
                    document.getElementById('price-adj-import-file-name').innerText = '';
                    document.getElementById('price_adj_import_file').value = '';
                    loadPriceAdjustmentList();
                } else {
                    alert('Lỗi: ' + (result.error || 'Không thể import file.'));
                }
            } catch (e) { alert('Lỗi kết nối server.'); }
            finally { document.getElementById('loading-overlay').style.display = 'none'; }
        });
    }

    // -------- Admin: bảng PIVOT (1 dòng/mã hàng, cột theo cửa hàng) --------
    // Mỗi ô hiện các vị trí đang có xếp CHỒNG DỌC: Vị trí 1 trên cùng, Vị
    // trí 2 ở giữa, Vị trí 3 dưới cùng - đủ 3 vị trí thì hiện đủ 3 dòng,
    // thiếu vị trí nào thì chỉ hiện những vị trí đang có (không chừa dòng
    // trống), không cần bấm/chạm để xem như trước nữa.
    function renderLocationsPivotTable(data) {
        const tbody = document.getElementById('location-body');
        document.getElementById('location-visible-count').innerText = data.length.toLocaleString();

        if (data.length === 0) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-muted">Không có mã hàng nào trong dữ liệu tồn kho.</td></tr>`;
            return;
        }

        const storeCell = (item, store) => {
            // Backend giờ luôn trả về đủ ĐỦ 6 cửa hàng cho mọi mã hàng (kể
            // cả cửa hàng chưa từng khai báo mã hàng này trong tồn kho -
            // quantity = null) - nên coi info rỗng ({}) thay vì hiện "-"
            // tĩnh không sửa được, để admin luôn gán vị trí được ở bất kỳ
            // cửa hàng nào.
            const info = (item.stores || {})[store] || {};

            // Chỉ liệt kê những vị trí ĐANG CÓ giá trị, theo đúng thứ tự
            // 1 -> 2 -> 3 (không chừa dòng trống cho vị trí còn thiếu).
            const locs = [info.location_1, info.location_2, info.location_3].filter(Boolean);
            const linesHtml = locs.length
                ? locs.map(l => `<div class="loc-pivot-line">${escapeHtmlAttr(l)}</div>`).join('')
                : '<div class="loc-pivot-line text-muted">-</div>';

            const editBtn = `<button type="button" class="btn btn-link btn-sm loc-pivot-edit-btn p-0" title="Sửa vị trí tại ${escapeHtmlAttr(store)}" onclick="event.stopPropagation(); editLocationCell(this, '${escapeHtmlAttr(item.part_code)}', '${store}')"><i class="bi bi-pencil"></i></button>`;

            return `<td class="loc-pivot-cell">
                <div class="loc-pivot-row">
                    <div class="loc-pivot-stack">${linesHtml}</div>${editBtn}
                </div>
            </td>`;
        };

        const truncated = data.length > LOCATION_RENDER_LIMIT;
        const displayData = truncated ? data.slice(0, LOCATION_RENDER_LIMIT) : data;

        let html = displayData.map(item => `
            <tr>
                <td><span class="part-code">${escapeHtmlAttr(item.part_code)}</span></td>
                <td>${item.part_name || ''}</td>
                <td class="text-muted small">${item.unit || ''}</td>
                ${LOCATION_STORES.map(s => storeCell(item, s)).join('')}
            </tr>`
        ).join('');

        if (truncated) {
            html += `<tr><td colspan="9" class="text-center py-3 text-muted small fst-italic">Đang hiển thị ${LOCATION_RENDER_LIMIT} / ${data.length.toLocaleString()} mã hàng — gõ thêm để thu hẹp tìm kiếm.</td></tr>`;
        }

        tbody.innerHTML = html;
    }

    // Bấm icon bút chì trong 1 ô -> chỉ ô ĐÓ (1 mã hàng, 1 cửa hàng) chuyển
    // thành 3 ô nhập liệu Vị trí 1/2/3 + nút Lưu/Huỷ, không ảnh hưởng các cửa
    // hàng khác của cùng mã hàng trên cùng dòng.
    function editLocationCell(btn, partCode, store) {
        const item = globalLocations.find(i => i.part_code === partCode);
        const info = (item && item.stores && item.stores[store]) || {};
        const td = btn.closest('td');
        td.classList.remove('inv-hl', 'inv-hl-loc3');
        td.removeAttribute('data-tip');
        td.innerHTML = `
            <div class="loc-pivot-edit d-flex flex-column gap-1">
                <input type="text" class="form-control form-control-sm loc-edit-input" placeholder="VT1" value="${escapeHtmlAttr(info.location_1 || '')}">
                <input type="text" class="form-control form-control-sm loc-edit-input" placeholder="VT2" value="${escapeHtmlAttr(info.location_2 || '')}">
                <input type="text" class="form-control form-control-sm loc-edit-input" placeholder="VT3" value="${escapeHtmlAttr(info.location_3 || '')}">
                <div class="d-flex gap-1">
                    <button type="button" class="btn btn-success btn-sm flex-fill py-0" title="Lưu" onclick="saveLocationCell(this, '${escapeHtmlAttr(partCode)}', '${store}')"><i class="bi bi-check-lg"></i></button>
                    <button type="button" class="btn btn-outline-secondary btn-sm flex-fill py-0" title="Huỷ" onclick="loadLocations()"><i class="bi bi-x-lg"></i></button>
                </div>
            </div>`;
    }

    async function saveLocationCell(btn, partCode, store) {
        const td = btn.closest('td');
        const inputs = td.querySelectorAll('.loc-edit-input');
        const payload = {
            part_code: partCode,
            store_code: store,
            location_1: inputs[0].value,
            location_2: inputs[1].value,
            location_3: inputs[2].value,
        };
        try {
            const res = await fetch('/api/locations/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await res.json();
            if (res.ok && result.success) {
                loadLocations();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể lưu vị trí.'));
            }
        } catch (e) { alert('Lỗi kết nối server.'); }
    }

    // Mã hàng "chưa có vị trí" = KHÔNG có bất kỳ vị trí nào được lưu.
    // Store: kiểm tra thẳng 3 cột location_1/2/3 của chính dòng đó.
    // Admin: kiểm tra CẢ 6 cửa hàng trong "stores" - chỉ coi là "chưa có vị
    // trí" khi không cửa hàng nào có bất kỳ vị trí nào (không phải chỉ
    // thiếu ở 1 vài cửa hàng, vì hầu hết mã hàng vốn chỉ bán ở 1-2 cửa hàng).
    function locationIsMissing(item) {
        if (CURRENT_ROLE === 'admin') {
            const stores = item.stores || {};
            return Object.values(stores).every(s => !s.location_1 && !s.location_2 && !s.location_3);
        }
        return !item.location_1 && !item.location_2 && !item.location_3;
    }

    function filterLocations() {
        const search = document.getElementById('location-search').value.toLowerCase();
        const searchCode = normalizeCodeSearch(search);
        const onlyMissing = document.getElementById('location-missing-filter').checked;
        const filtered = globalLocations.filter(item => {
            const matchesSearch = normalizeCodeSearch(item.part_code).includes(searchCode) ||
                (item.part_name || '').toLowerCase().includes(search);
            if (!matchesSearch) return false;
            return !onlyMissing || locationIsMissing(item);
        });
        renderLocationsTable(filtered);
    }

    let _locationFilterDebounceTimer = null;
    function debouncedFilterLocations() {
        clearTimeout(_locationFilterDebounceTimer);
        _locationFilterDebounceTimer = setTimeout(filterLocations, 250);
    }

    const locationManualForm = document.getElementById('location-manual-form');
    if (locationManualForm) {
        locationManualForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const partCode = document.getElementById('location-manual-part').value.trim();
            if (!partCode) { alert('Vui lòng nhập mã hàng.'); return; }
            const payload = {
                part_code: partCode,
                location_1: document.getElementById('location-manual-loc1').value,
                location_2: document.getElementById('location-manual-loc2').value,
                location_3: document.getElementById('location-manual-loc3').value,
            };
            if (CURRENT_ROLE === 'admin') {
                const storeSel = document.getElementById('location-manual-store');
                if (!storeSel.value) { alert('Vui lòng chọn cửa hàng.'); return; }
                payload.store_code = storeSel.value;
            }
            try {
                const res = await fetch('/api/locations/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const result = await res.json();
                if (res.ok && result.success) {
                    locationManualForm.reset();
                    loadLocations();
                } else {
                    alert('Lỗi: ' + (result.error || 'Không thể lưu vị trí.'));
                }
            } catch (e) { alert('Lỗi kết nối server.'); }
        });
    }

    const locationUploadForm = document.getElementById('location-upload-form');
    if (locationUploadForm) {
        locationUploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const file = document.getElementById('location_file').files[0];
            if (!file) { alert('Vui lòng chọn file vị trí để tải lên.'); return; }

            const formData = new FormData();
            formData.append('location_file', file);
            if (CURRENT_ROLE === 'admin') {
                const storeSel = document.getElementById('location-upload-store');
                if (!storeSel.value) { alert('Vui lòng chọn cửa hàng áp dụng.'); return; }
                formData.append('store_code', storeSel.value);
            }

            document.getElementById('loading-overlay').style.display = 'flex';
            try {
                const res = await fetch('/api/locations/import-excel', { method: 'POST', body: formData });
                const result = await res.json();
                if (res.ok) {
                    alert(`Đã áp dụng vị trí cho ${result.applied.toLocaleString()} mã hàng tại ${result.store_code} (bỏ qua ${result.not_in_stock.toLocaleString()} mã hàng chưa có trong danh sách admin đã import, ${result.skipped_rows.toLocaleString()} dòng thiếu mã hàng, ${(result.duplicate_rows || 0).toLocaleString()} dòng trùng mã hàng đã chỉ lấy dòng cuối).`);
                    document.getElementById('location-file-name').innerText = '';
                    loadLocations();
                } else {
                    alert('Lỗi: ' + result.error);
                }
            } catch (e) { alert('Lỗi kết nối server.'); }
            finally { document.getElementById('loading-overlay').style.display = 'none'; }
        });
    }
    // Store import vị trí từ Excel
const storeLocationForm = document.getElementById('location-upload-form-store');
if (storeLocationForm) {
    storeLocationForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fileInput = document.getElementById('location_file_store');
        const file = fileInput.files[0];
        if (!file) {
            alert('Vui lòng chọn file vị trí để tải lên.');
            return;
        }
        const formData = new FormData();
        formData.append('location_file', file);
        formData.append('store_code', CURRENT_STORE_CODE); // tự động áp dụng cho cửa hàng hiện tại

        document.getElementById('loading-overlay').style.display = 'flex';
        try {
            const res = await fetch('/api/locations/import-excel', { method: 'POST', body: formData });
            const result = await res.json();
            if (res.ok) {
                alert(`Đã áp dụng vị trí cho ${result.applied.toLocaleString()} mã hàng (bỏ qua ${result.not_in_stock.toLocaleString()} mã chưa có trong danh sách admin đã import, ${result.skipped_rows.toLocaleString()} dòng lỗi).`);
                document.getElementById('location-file-name-store').innerText = '';
                fileInput.value = '';
                await loadLocations();
                loadInventory();
            } else {
                alert('Lỗi: ' + result.error);
            }
        } catch (e) { alert('Lỗi kết nối server.'); }
        finally { document.getElementById('loading-overlay').style.display = 'none'; }
    });
}
    async function loadHistory() {
        const res = await fetch('/api/history');
        const result = await res.json();
        const tbody = document.getElementById('history-body');
        if(result.success && result.history.length > 0) {
            tbody.innerHTML = result.history.map(h => `
                <tr>
                    <td class="text-center text-muted small">${h.id}</td>
                    <td><span class="badge bg-secondary">${h.store_code}</span></td>
                    <td class="fw-semibold">${h.upload_time}</td>
                    <td class="small text-primary">${h.ds_po_filename || '—'}</td>
                    <td class="small text-warning">${h.po_detail_filename || '—'}</td>
                    <td class="small text-success">${h.receipt_filename || '—'}</td>
                </tr>
            `).join('');
        } else {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center py-4 text-muted">Chưa có lịch sử tải lên.</td></tr>`;
        }
    }

    // ------------------------------------------------------------------
    // LUÂN CHUYỂN NỘI BỘ GIỮA CÁC CỬA HÀNG (1 phiếu có thể chứa nhiều mã hàng)
    // ------------------------------------------------------------------
    const TRANSFER_STATUS_LABELS = {
        pending: '<span class="badge-status badge-shipping">Chờ Xử Lý</span>',
        approved: '<span class="badge-status badge-prepared">Đã Đồng Ý</span>',
        rejected: '<span class="badge-status badge-debt">Đã Từ Chối</span>',
        cancelled: '<span class="badge-status badge-not-prepared">Đã Huỷ</span>',
    };
    const TRANSFER_STATUS_LABELS_PLAIN = {
        pending: 'Chờ Xử Lý', approved: 'Đã Đồng Ý', rejected: 'Đã Từ Chối', cancelled: 'Đã Huỷ',
    };
    //thêm

    function transferStatusBadge(item, editablePrepare) {
        const badges = [TRANSFER_STATUS_LABELS[item.status] || item.status];
        if (item.status === 'approved') {
            if (editablePrepare) {
                // Bên cho (to_store) được BẤM TRỰC TIẾP vào badge để đổi
                // trạng thái soạn hàng, thay vì có nút riêng ở cột Thao Tác -
                // icon bút chỉ hiện khi rê chuột vào để gợi ý có thể sửa.
                const cls = item.prepared ? 'badge-prepared' : 'badge-not-prepared';
                const label = item.prepared ? 'Đã Soạn Hàng' : 'Chưa Soạn Hàng';
                badges.push(`<button type="button" class="badge-status ${cls} prepare-badge-toggle" onclick="toggleTransferPrepared(${item.id}, ${!item.prepared})" title="Bấm để đổi trạng thái soạn hàng">${label}<i class="bi bi-pencil-fill prepare-edit-icon"></i></button>`);
            } else {
                badges.push(item.prepared
                    ? '<span class="badge-status badge-prepared">Đã Soạn Hàng</span>'
                    : '<span class="badge-status badge-not-prepared">Chưa Soạn Hàng</span>');
            }
        }
        if (item.status === 'approved' && item.all_received) {
            badges.push('<span class="badge-status badge-received">Đã Nhận Đủ Hàng</span>');
        }
        // Có yêu cầu xin admin xoá phiếu đang chờ xử lý - hiện thêm badge
        // cảnh báo ở MỌI nơi dùng chung hàm này (2 bảng của cửa hàng + bảng
        // admin + modal chi tiết), để ai nhìn vào phiếu cũng biết ngay.
        if (item.delete_requested) {
            badges.push('<span class="badge-status bg-warning text-dark border"><i class="bi bi-hourglass-split me-1"></i>Đang nhờ xoá</span>');
        }
        // Đã Đồng Ý nằm trên, Soạn Hàng/Đã Nhận Đủ Hàng nằm dưới - xếp dọc
        // thay vì nằm ngang cạnh nhau (dễ đọc hơn khi có nhiều badge).
        if (badges.length === 1) return badges[0];
        return `<div class="d-flex flex-column align-items-start gap-1">${badges.join('')}</div>`;
    }

    // So sánh mã hàng theo kiểu "natural sort" (nhận biết số trong chuỗi,
    // vd 2 đứng trước 10 thay vì so ký tự '1' trước '2') để sắp xếp mã hàng
    // trong 1 phiếu từ nhỏ tới lớn, dễ dò/kiểm soát theo thứ tự quen mắt.
    function comparePartCodes(a, b) {
        return String(a || '').localeCompare(String(b || ''), undefined, { numeric: true, sensitivity: 'base' });
    }

    function escapeHtmlAttr(s) {
        return String(s == null ? '' : s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // Rút gọn cột "Thời Gian Gửi" trong các bảng danh sách phiếu luân chuyển
    // thành 2 dòng (Thứ + Giờ ở trên, Ngày/Tháng/Năm ở dưới) thay vì 1 dòng
    // dài - nhường thêm chỗ ngang cho cột Ghi Chú. Chuỗi gốc có dạng
    // "Thứ Bảy, 12/09/2026 13:01" (xem format_vi_datetime ở backend); nếu
    // không đúng định dạng mong đợi thì hiển thị nguyên văn để không mất
    // dữ liệu.
    function formatTransferListTime(str) {
        if (!str) return '';
        const parts = String(str).split(',');
        if (parts.length < 2) return escapeHtmlAttr(str);
        const weekday = parts[0].trim();
        const rest = parts.slice(1).join(',').trim(); // "12/09/2026 13:01"
        const restParts = rest.split(' ').filter(Boolean);
        const datePart = restParts[0] || '';
        const timePart = restParts.slice(1).join(' ') || '';
        return `<div>${escapeHtmlAttr(weekday)}${timePart ? ' ' + escapeHtmlAttr(timePart) : ''}</div><div>${escapeHtmlAttr(datePart)}</div>`;
    }

    function fillEmployeeSelect(selectEl, storeCode, placeholder) {
        if (!selectEl) return;
        const names = STORE_EMPLOYEES[storeCode] || [];
        const emptyLabel = names.length
            ? (placeholder || '-- Chọn nhân viên --')
            : (storeCode ? 'Không có nhân viên cho cửa hàng này' : (placeholder || '-- Chọn cửa hàng trước --'));
        selectEl.innerHTML = `<option value="">${emptyLabel}</option>` + names.map(n => `<option value="${escapeHtmlAttr(n)}">${escapeHtmlAttr(n)}</option>`).join('');
    }

    //function fillAdminCreatedEmployeeSelect() {
        //const fromStore = document.getElementById('admin-transfer-from-store');
        //fillEmployeeSelect(
            //document.getElementById('admin-transfer-created-employee'),
            //fromStore ? fromStore.value : '',
            //fromStore && fromStore.value ? '-- Chọn nhân viên --' : '-- Chọn cửa hàng nhận trước --'
        //);
    //}
    function fillAdminCreatedEmployeeSelect() {
    const selectEl = document.getElementById('admin-transfer-created-employee');
    const names = ADMIN_EMPLOYEES;
    selectEl.innerHTML = `<option value="">-- Chọn nhân viên --</option>` +
        names.map(n => `<option value="${escapeHtmlAttr(n)}">${escapeHtmlAttr(n)}</option>`).join('');
}
//
    // Badge màu riêng cho từng cửa hàng (xem CSS .store-badge-XX) - dùng
    // thống nhất ở mọi nơi hiển thị mã cửa hàng trong bảng luân chuyển.
    const KNOWN_STORE_CODES = ['NS1', 'NS2', 'NS3', 'NS4', 'NS5', 'NSM1'];
    function storeBadge(code) {
        const cls = KNOWN_STORE_CODES.includes(code) ? `store-badge-${code}` : 'store-badge-default';
        return `<span class="store-badge ${cls}">${escapeHtmlAttr(code || '—')}</span>`;
    }

    // Lặp lại badge cửa hàng ngay trong cột "Cửa Hàng Xin", 1 badge cho mỗi
    // dòng mã hàng (rowCount) - để badge thẳng hàng với từng mã hàng bên
    // cột "Mã Hàng - Tên Hàng - Số Lượng", thay vì chỉ hiện 1 lần rồi trôi giữa cả nhóm.
    function storeBadgeColumn(code, rowCount) {
        const count = Math.max(1, rowCount || 1);
        const badge = storeBadge(code);
        const rows = [];
        for (let i = 0; i < count; i++) {
            const hiddenCls = i >= ITEMS_COLLAPSE_THRESHOLD ? ' js-extra-hidden' : '';
            rows.push(`<div class="transfer-store-row${hiddenCls}">${badge}</div>`);
        }
        return `<div class="transfer-store-list">${rows.join('')}</div>`;
    }

    // Số dòng mã hàng hiển thị mặc định trước khi phải bấm "Xem thêm" - áp
    // dụng thống nhất cho các cột lặp-theo-dòng của 1 phiếu (Mã Hàng, Tên
    // Hàng, Số Lượng, Cửa Hàng Xin, Đã Nhận) để luôn thẳng hàng khi mở/thu gọn.
    const ITEMS_COLLAPSE_THRESHOLD = 5;

    // ------------------------------------------------------------------
    // 3 hàm dưới đây render riêng CỘT MÃ HÀNG / CỘT TÊN HÀNG / CỘT SỐ
    // LƯỢNG của 1 phiếu - dùng cho 3 <td> THẬT RIÊNG trong bảng danh sách
    // phiếu (khác với transferItemsSummary() ở trên vốn gộp chung 1 ô,
    // vẫn giữ lại transferItemsSummary() để dùng cho phần tóm tắt trong
    // modal Từ Chối Phiếu). Tách thành 3 cột thật giúp các dòng mã hàng
    // luôn thẳng hàng với nhau VÀ thẳng với tiêu đề cột, vì lúc này trình
    // duyệt tự canh theo bảng thật thay vì phải tự dàn bằng CSS riêng cho
    // từng ô (mỗi ô trước đây tự tính bề rộng độc lập nên dễ lệch nhau
    // giữa các phiếu khác nhau).
    // Nút "Xem thêm N mã" chỉ cần đặt ở 1 trong 3 cột (đặt ở cột Mã Hàng)
    // vì bấm vào sẽ mở toàn bộ các dòng ẩn trong CẢ 3 cột cùng lúc (xem
    // toggleTransferItemsExpand - dò theo cả <tr>, không riêng cột nào).
    function transferItemsCodeColumn(items) {
        if (!items || !items.length) return '';
        const rows = items.map((it, idx) => {
            const hiddenCls = idx >= ITEMS_COLLAPSE_THRESHOLD ? ' js-extra-hidden' : '';
            return `<div class="transfer-item-row${hiddenCls}"><span class="part-code" title="${escapeHtmlAttr(it.part_code)}">${escapeHtmlAttr(it.part_code)}</span></div>`;
        }).join('');
        let toggle = '';
        if (items.length > ITEMS_COLLAPSE_THRESHOLD) {
            const hiddenCount = items.length - ITEMS_COLLAPSE_THRESHOLD;
            toggle = `<div class="transfer-item-row transfer-item-toggle"><button type="button" class="btn-transfer-toggle" data-hidden-count="${hiddenCount}" onclick="toggleTransferItemsExpand(this)">Xem thêm ${hiddenCount} mã <i class="bi bi-chevron-down"></i></button></div>`;
        }
        return `<div class="transfer-items-list">${rows}${toggle}</div>`;
    }

    function transferItemsNameColumn(items) {
        if (!items || !items.length) return '';
        const rows = items.map((it, idx) => {
            const hiddenCls = idx >= ITEMS_COLLAPSE_THRESHOLD ? ' js-extra-hidden' : '';
            const name = it.part_name || '';
            return `<div class="transfer-item-row${hiddenCls}"><span class="transfer-item-name" title="${escapeHtmlAttr(name)}">${escapeHtmlAttr(name)}</span></div>`;
        }).join('');
        return `<div class="transfer-items-list">${rows}</div>`;
    }

    function transferItemsQtyColumn(items) {
        if (!items || !items.length) return '';
        const rows = items.map((it, idx) => {
            const requested = Number(it.quantity || 0).toLocaleString();
            const hiddenCls = idx >= ITEMS_COLLAPSE_THRESHOLD ? ' js-extra-hidden' : '';
            if (it.quantity_adjusted) {
                const approvedQty = Number(it.approved_quantity || 0);
                const approved = approvedQty.toLocaleString();
                const approvedHtml = approvedQty === 0
                    ? `<span class="text-danger fw-semibold">0 — Hết hàng</span>`
                    : `<span class="text-success fw-semibold">${approved}</span>`;
                return `<div class="transfer-item-row${hiddenCls}"><span class="transfer-item-qty">${approvedHtml} <i class="bi bi-clock-history qty-history-icon" title="Số lượng xin ban đầu: ${requested}"></i></span></div>`;
            }
            return `<div class="transfer-item-row${hiddenCls}"><span class="transfer-item-qty">x ${requested}</span></div>`;
        }).join('');
        return `<div class="transfer-items-list">${rows}</div>`;
    }

    function transferItemsSummary(items) {
        if (!items || !items.length) return '';
        const rows = items.map((it, idx) => {
            const requested = Number(it.quantity || 0).toLocaleString();
            const codeHtml = `<span class="part-code" title="${escapeHtmlAttr(it.part_code)}">${escapeHtmlAttr(it.part_code)}</span>` +
                `<span class="transfer-item-name" title="${it.part_name ? escapeHtmlAttr(it.part_name) : ''}">${it.part_name ? escapeHtmlAttr(it.part_name) : ''}</span>`;
            const hiddenCls = idx >= ITEMS_COLLAPSE_THRESHOLD ? ' js-extra-hidden' : '';
            if (it.quantity_adjusted) {
                // Bên cho đã sửa số lượng lúc Đồng ý - CHỈ hiện số lượng đã
                // đồng ý (gọn, không tốn thêm bề ngang); số lượng xin ban đầu
                // nằm trong icon lịch sử, rê chuột/chạm giữ vào mới hiện.
                // Riêng trường hợp = 0: nghĩa là KHÔNG cho được mã này (hết
                // hàng...) - tô đỏ + ghi rõ "Hết hàng" thay vì màu xanh dễ
                // gây hiểu nhầm là vẫn cho được (dù ít hơn).
                const approvedQty = Number(it.approved_quantity || 0);
                const approved = approvedQty.toLocaleString();
                const approvedHtml = approvedQty === 0
                    ? `<span class="text-danger fw-semibold">0 — Hết hàng</span>`
                    : `<span class="text-success fw-semibold">${approved}</span>`;
                return `<div class="transfer-item-row${hiddenCls}">${codeHtml}<span class="transfer-item-qty">${approvedHtml} <i class="bi bi-clock-history qty-history-icon" title="Số lượng xin ban đầu: ${requested}"></i></span></div>`;
            }
            return `<div class="transfer-item-row${hiddenCls}">${codeHtml}<span class="transfer-item-qty">x ${requested}</span></div>`;
        }).join('');

        let toggle = '';
        if (items.length > ITEMS_COLLAPSE_THRESHOLD) {
            const hiddenCount = items.length - ITEMS_COLLAPSE_THRESHOLD;
            toggle = `<div class="transfer-item-row transfer-item-toggle"><button type="button" class="btn-transfer-toggle" data-hidden-count="${hiddenCount}" onclick="toggleTransferItemsExpand(this)">Xem thêm ${hiddenCount} mã <i class="bi bi-chevron-down"></i></button></div>`;
        }
        return `<div class="transfer-items-list">${rows}${toggle}</div>`;
    }

    // Bấm "Xem thêm N mã" / "Thu gọn" trên 1 phiếu - mở hoặc ẩn các dòng mã
    // hàng vượt ngưỡng gọn ở CÁC cột lặp-theo-dòng trong cùng <tr> (Mã Hàng,
    // Tên Hàng, Số Lượng, Cửa Hàng Xin, Đã Nhận), rồi đồng bộ lại chiều cao dòng vì số
    // dòng hiển thị vừa thay đổi.
    function toggleTransferItemsExpand(btn) {
        const tr = btn.closest('tr');
        if (!tr) return;
        const expanding = btn.dataset.expanded !== 'true';
        tr.querySelectorAll('.js-extra-hidden').forEach(el => {
            el.classList.toggle('js-expanded-visible', expanding);
        });
        btn.dataset.expanded = expanding ? 'true' : 'false';
        btn.innerHTML = expanding
            ? `Thu gọn <i class="bi bi-chevron-up"></i>`
            : `Xem thêm ${btn.dataset.hiddenCount} mã <i class="bi bi-chevron-down"></i>`;
        const tbody = tr.closest('tbody');
        if (tbody && tbody.id) syncTransferRowHeights(tbody.id);
    }

    // Cột "Cửa Hàng Xin/Chuyển" và (ở phiếu Tôi Đã Gửi) cột "Đã Nhận" đều
    // lặp lại 1 dòng cho mỗi mã hàng, nhưng nằm ở các <td> riêng biệt nên
    // CSS không thể tự canh chỉnh chiều cao khớp nhau khi 1 dòng mã hàng
    // (ví dụ dòng có nhãn "Đã sửa") cao hơn dòng bình thường. Hàm này đo
    // chiều cao THỰC TẾ đã render của từng dòng trong CÙNG 1 <tr>, rồi áp
    // chiều cao lớn nhất đó cho tất cả các dòng tương ứng ở mọi cột - đảm
    // bảo luôn thẳng hàng bất kể nội dung dài/ngắn khác nhau.
    function syncTransferRowHeights(tbodyId) {
        const tbody = document.getElementById(tbodyId);
        if (!tbody) return;
        tbody.querySelectorAll('tr').forEach(tr => {
            const groups = [];
            tr.querySelectorAll('.transfer-items-list').forEach(itemsList => groups.push(Array.from(itemsList.children)));
            const storeList = tr.querySelector('.transfer-store-list');
            if (storeList) groups.push(Array.from(storeList.children));
            const receivedList = tr.querySelector('.transfer-received-list');
            if (receivedList) groups.push(Array.from(receivedList.children));
            if (groups.length < 2) return; // chỉ 1 (hoặc 0) danh sách -> không có gì cần đồng bộ

            // Bỏ chiều cao ép buộc từ lần đo trước để đo lại đúng chiều cao
            // tự nhiên của nội dung, tránh cộng dồn qua nhiều lần render.
            groups.forEach(g => g.forEach(el => { el.style.minHeight = ''; }));

            const rowCount = Math.max(...groups.map(g => g.length));
            for (let i = 0; i < rowCount; i++) {
                let maxH = 0;
                groups.forEach(g => { if (g[i]) maxH = Math.max(maxH, g[i].getBoundingClientRect().height); });
                if (maxH > 0) {
                    groups.forEach(g => { if (g[i]) g[i].style.minHeight = maxH + 'px'; });
                }
            }
        });
    }

    // ---- Xây dựng các dòng nhập mã hàng (nhập tay) trong form tạo phiếu ----
    let transferRowSeq = 0;

    function addTransferItemRow(partCode, quantity) {
        const wrap = document.getElementById('transfer-item-rows');
        if (!wrap) return;
        const rid = ++transferRowSeq;
        const row = document.createElement('div');
        row.className = 'row g-2 align-items-center mb-2';
        row.id = `transfer-row-${rid}`;
        row.innerHTML = `
            <div class="col-7 col-md-6">
                <input list="transfer-part-list" class="form-control transfer-row-part" placeholder="Nhập hoặc chọn mã hàng..." value="${escapeHtmlAttr(partCode || '')}" oninput="onTransferRowPartChange(${rid})">
                <div class="small mt-1 transfer-row-hint"></div>
            </div>
            <div class="col-3 col-md-3">
                <input type="number" class="form-control transfer-row-qty" min="1" step="1" placeholder="SL" value="${quantity || ''}" oninput="onTransferRowPartChange(${rid})">
            </div>
            <div class="col-2 col-md-3 text-end">
                <button type="button" class="btn btn-outline-danger btn-sm" onclick="document.getElementById('transfer-row-${rid}').remove()"><i class="bi bi-trash3"></i></button>
            </div>
        `;
        wrap.appendChild(row);
    }

    function onTransferRowPartChange(rid) {
        const row = document.getElementById(`transfer-row-${rid}`);
        if (!row) return;
        const code = row.querySelector('.transfer-row-part').value.trim();
        const qty = row.querySelector('.transfer-row-qty').value;
        const hintEl = row.querySelector('.transfer-row-hint');
        const toStore = document.getElementById('transfer-to-store').value;
        renderTransferRowHint(hintEl, code, qty, toStore);
    }

    function refreshAllTransferRowHints() {
        const wrap = document.getElementById('transfer-item-rows');
        if (!wrap) return;
        Array.from(wrap.children).forEach(row => {
            const code = row.querySelector('.transfer-row-part').value.trim();
            const qty = row.querySelector('.transfer-row-qty').value;
            const hintEl = row.querySelector('.transfer-row-hint');
            const toStore = document.getElementById('transfer-to-store').value;
            renderTransferRowHint(hintEl, code, qty, toStore);
        });
    }

    // Dựng nội dung ghi chú dưới ô nhập mã hàng: tên hàng + tồn kho tại cửa
    // hàng đang bị xin/nhận, cảnh báo nếu số lượng nhập vượt tồn kho, và ghi
    // chú nếu mã hàng đó đã có cửa hàng khác xin (đang chờ duyệt) từ CÙNG
    // cửa hàng này. Dùng chung cho cả form Store và form Admin.
    function renderTransferRowHint(hintEl, code, qty, store) {
        if (!hintEl) return;
        if (!code) { hintEl.innerHTML = ''; return; }
        const found = globalInventory.find(it => (it.part_code || '').toLowerCase() === code.toLowerCase());
        if (!found) { hintEl.innerHTML = ''; return; }

        let lines = [];
        let infoText = found.part_name ? `Tên hàng: ${found.part_name}` : '';

        if (store && found[store] !== undefined && found[store] !== null) {
            const stock = found[store] || 0;
            infoText += (infoText ? ' · ' : '') + `Tồn tại ${store}: ${stock.toLocaleString()}`;
            if (qty && Number(qty) > stock) {
                lines.push(`<div class="text-danger fw-semibold"><i class="bi bi-exclamation-triangle-fill me-1"></i>Vượt tồn kho ${store} (chỉ còn ${stock.toLocaleString()})</div>`);
            }
        }
        if (infoText) lines.unshift(`<div class="text-muted">${escapeHtmlAttr(infoText)}</div>`);

        if (store) {
            const givenList = (transferHighlights.given[found.part_code] || {})[store];
            if (givenList && givenList.length) {
                const text = givenList.map(g => `${g.store} xin ${g.quantity.toLocaleString()}`).join(', ');
                lines.push(`<div class="text-warning-emphasis"><i class="bi bi-info-circle-fill me-1"></i>Đã có CH khác xin mã này từ ${store}: ${escapeHtmlAttr(text)}</div>`);
            }
        }

        hintEl.innerHTML = lines.join('');
    }

    function populateTransferPartList() {
        const list = document.getElementById('transfer-part-list');
        if (!list) return;
        list.innerHTML = globalInventory.slice(0, 3000).map(it =>
            `<option value="${escapeHtmlAttr(it.part_code)}">${escapeHtmlAttr(it.part_name || '')}</option>`
        ).join('');
    }

    function findInventoryPartName(partCode) {
        const found = globalInventory.find(it => (it.part_code || '').toLowerCase() === (partCode || '').toLowerCase());
        return found ? (found.part_name || '') : '';
    }

    function collectTransferItemRows() {
        const rows = Array.from(document.querySelectorAll('#transfer-item-rows > div'));
        const items = [];
        for (const row of rows) {
            const partCode = row.querySelector('.transfer-row-part').value.trim();
            const qty = row.querySelector('.transfer-row-qty').value;
            if (!partCode && !qty) continue; // dòng trống, bỏ qua
            items.push({ part_code: partCode, part_name: findInventoryPartName(partCode), quantity: qty });
        }
        // Sắp xếp từ nhỏ tới lớn ngay khi gửi phiếu, để phiếu lưu lại và
        // in ra luôn theo đúng thứ tự dễ kiểm soát này.
        items.sort((a, b) => comparePartCodes(a.part_code, b.part_code));
        return items;
    }

    // ---- Bản dành cho Admin: tạo phiếu hộ giữa 2 cửa hàng bất kỳ ----
    let adminTransferRowSeq = 0;

    function addAdminTransferItemRow(partCode, quantity) {
        const wrap = document.getElementById('admin-transfer-item-rows');
        if (!wrap) return;
        const rid = ++adminTransferRowSeq;
        const row = document.createElement('div');
        row.className = 'row g-2 align-items-center mb-2';
        row.id = `admin-transfer-row-${rid}`;
        row.innerHTML = `
            <div class="col-7 col-md-6">
                <input list="transfer-part-list" class="form-control admin-transfer-row-part" placeholder="Nhập hoặc chọn mã hàng..." value="${escapeHtmlAttr(partCode || '')}" oninput="onAdminTransferRowChange(${rid})">
                <div class="small mt-1 admin-transfer-row-hint"></div>
            </div>
            <div class="col-3 col-md-3">
                <input type="number" class="form-control admin-transfer-row-qty" min="1" step="1" placeholder="SL" value="${quantity || ''}" oninput="onAdminTransferRowChange(${rid})">
            </div>
            <div class="col-2 col-md-3 text-end">
                <button type="button" class="btn btn-outline-danger btn-sm" onclick="document.getElementById('admin-transfer-row-${rid}').remove()"><i class="bi bi-trash3"></i></button>
            </div>
        `;
        wrap.appendChild(row);
    }

    function onAdminTransferRowChange(rid) {
        const row = document.getElementById(`admin-transfer-row-${rid}`);
        if (!row) return;
        const code = row.querySelector('.admin-transfer-row-part').value.trim();
        const qty = row.querySelector('.admin-transfer-row-qty').value;
        const hintEl = row.querySelector('.admin-transfer-row-hint');
        // Cửa Hàng Chuyển = kho sẽ bị trừ hàng (to_store) - dùng để tra tồn kho & ghi chú.
        const toStore = document.getElementById('admin-transfer-to-store').value;
        renderTransferRowHint(hintEl, code, qty, toStore);
    }

    function refreshAllAdminTransferRowHints() {
        const wrap = document.getElementById('admin-transfer-item-rows');
        if (!wrap) return;
        const toStore = document.getElementById('admin-transfer-to-store').value;
        Array.from(wrap.children).forEach(row => {
            const code = row.querySelector('.admin-transfer-row-part').value.trim();
            const qty = row.querySelector('.admin-transfer-row-qty').value;
            const hintEl = row.querySelector('.admin-transfer-row-hint');
            renderTransferRowHint(hintEl, code, qty, toStore);
        });
    }

    function collectAdminTransferItemRows() {
        const rows = Array.from(document.querySelectorAll('#admin-transfer-item-rows > div'));
        const items = [];
        for (const row of rows) {
            const partCode = row.querySelector('.admin-transfer-row-part').value.trim();
            const qty = row.querySelector('.admin-transfer-row-qty').value;
            if (!partCode && !qty) continue; // dòng trống, bỏ qua
            items.push({ part_code: partCode, part_name: findInventoryPartName(partCode), quantity: qty });
        }
        // Sắp xếp từ nhỏ tới lớn ngay khi tạo phiếu, để phiếu lưu lại và
        // in ra luôn theo đúng thứ tự dễ kiểm soát này.
        items.sort((a, b) => comparePartCodes(a.part_code, b.part_code));
        return items;
    }

    async function submitAdminTransferRequest() {
        const fromStore = document.getElementById('admin-transfer-from-store').value;
        const toStore = document.getElementById('admin-transfer-to-store').value;
        const note = document.getElementById('admin-transfer-note').value.trim();
        const createdEmployee = document.getElementById('admin-transfer-created-employee').value;
        const items = collectAdminTransferItemRows();

        if (!fromStore || !toStore) { alert('Vui lòng chọn đủ cửa hàng xuất và cửa hàng nhận.'); return; }
        if (fromStore === toStore) { alert('Cửa hàng xuất và cửa hàng nhận không được trùng nhau.'); return; }
        if (!createdEmployee) { alert('Vui lòng chọn nhân viên tạo phiếu thuộc cửa hàng nhận.'); return; }
        if (items.length === 0) { alert('Vui lòng thêm ít nhất 1 mã hàng.'); return; }
        for (const it of items) {
            if (!it.part_code) { alert('Vui lòng nhập đầy đủ mã hàng cho mọi dòng.'); return; }
            if (!it.quantity || Number(it.quantity) <= 0) { alert(`Vui lòng nhập số lượng hợp lệ cho mã hàng "${it.part_code}".`); return; }
        }

        try {
            const res = await fetch('/api/admin/transfer/create', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ from_store: fromStore, to_store: toStore, note: note, items: items, created_employee: createdEmployee })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                document.getElementById('admin-transfer-from-store').value = '';
                document.getElementById('admin-transfer-to-store').value = '';
                document.getElementById('admin-transfer-note').value = '';
                fillAdminCreatedEmployeeSelect();
                document.getElementById('admin-transfer-item-rows').innerHTML = '';
                addAdminTransferItemRow();
                loadTransfers();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể tạo phiếu.'));
            }
        } catch (e) {
            console.error(e);
            alert('Lỗi kết nối đến server.');
        }
    }

    // Tạo file Excel mẫu cho admin tạo phiếu chuyển nội bộ hộ 2 cửa hàng -
    // chỉ chứa mã hàng + số lượng, cửa hàng chuyển/nhận vẫn chọn ở 2 ô
    // dropdown phía trên (giống cách import của chi nhánh).
    function downloadAdminTransferTemplate() {
        const rows = [
            { 'Mã hàng': 'VD0001', 'Tên hàng (không bắt buộc)': 'Tên hàng mẫu', 'Số lượng': 10 },
            { 'Mã hàng': '', 'Tên hàng (không bắt buộc)': '', 'Số lượng': '' },
        ];
        const ws = XLSX.utils.json_to_sheet(rows);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, "MauChuyenNoiBoAdmin");
        XLSX.writeFile(wb, `Mau_Chuyen_Noi_Bo_Admin.xlsx`);
    }

    async function submitAdminTransferExcelImport() {
        const fileInput = document.getElementById('admin-transfer-excel-file');
        const file = fileInput.files[0];
        if (!file) return;
        const fromStore = document.getElementById('admin-transfer-from-store').value;
        const toStore = document.getElementById('admin-transfer-to-store').value;
        if (!fromStore || !toStore) {
            alert('Vui lòng chọn đủ Cửa Hàng Chuyển và Cửa Hàng Nhận trước khi nhập từ Excel.');
            fileInput.value = '';
            return;
        }
        if (fromStore === toStore) {
            alert('Cửa hàng chuyển và cửa hàng nhận không được trùng nhau.');
            fileInput.value = '';
            return;
        }
        const createdEmployee = document.getElementById('admin-transfer-created-employee').value;
        if (!createdEmployee) {
            alert('Vui lòng chọn nhân viên tạo phiếu thuộc cửa hàng nhận trước khi nhập từ Excel.');
            fileInput.value = '';
            return;
        }
        const note = document.getElementById('admin-transfer-note').value.trim();
        const formData = new FormData();
        formData.append('file', file);
        formData.append('from_store', fromStore);
        formData.append('to_store', toStore);
        formData.append('note', note);
        formData.append('created_employee', createdEmployee);

        document.getElementById('loading-overlay').style.display = 'flex';
        try {
            const res = await fetch('/api/admin/transfer/import-excel', { method: 'POST', body: formData });
            const result = await res.json();
            if (res.ok && result.success) {
                let msg = `Đã tạo phiếu với ${result.item_count} mã hàng.`;
                if (result.skipped_rows) msg += ` (Bỏ qua ${result.skipped_rows} dòng không hợp lệ.)`;
                alert(msg);
                document.getElementById('admin-transfer-from-store').value = '';
                document.getElementById('admin-transfer-to-store').value = '';
                document.getElementById('admin-transfer-note').value = '';
                fillAdminCreatedEmployeeSelect();
                loadTransfers();
                refreshTransferHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể nhập file Excel.'));
            }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
        finally {
            document.getElementById('loading-overlay').style.display = 'none';
            fileInput.value = '';
        }
    }

    async function submitTransferRequest() {
        const toStore = document.getElementById('transfer-to-store').value;
        const note = document.getElementById('transfer-note').value.trim();
        const createdEmployee = document.getElementById('transfer-created-employee').value;
        const items = collectTransferItemRows();

        if (!toStore) { alert('Vui lòng chọn cửa hàng cần xin.'); return; }
        if (!createdEmployee) { alert('Vui lòng chọn nhân viên tạo phiếu.'); return; }
        if (items.length === 0) { alert('Vui lòng thêm ít nhất 1 mã hàng cần xin.'); return; }
        for (const it of items) {
            if (!it.part_code) { alert('Vui lòng nhập đầy đủ mã hàng cho mọi dòng.'); return; }
            if (!it.quantity || Number(it.quantity) <= 0) { alert(`Vui lòng nhập số lượng hợp lệ cho mã hàng "${it.part_code}".`); return; }
        }

        try {
            const res = await fetch('/api/transfer/create', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ to_store: toStore, note: note, items: items, created_employee: createdEmployee })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                document.getElementById('transfer-to-store').value = '';
                document.getElementById('transfer-note').value = '';
                document.getElementById('transfer-item-rows').innerHTML = '';
                addTransferItemRow();
                loadTransfers();
                refreshTransferHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể gửi phiếu.'));
            }
        } catch (e) {
            console.error(e);
            alert('Lỗi kết nối đến server.');
        }
    }

    // Tạo file Excel mẫu để chi nhánh điền mã hàng cần xin rồi import lại
    // qua nút "Nhập Danh Sách Từ Excel" ở trên.
    function downloadTransferTemplate() {
        const rows = [
            { 'Mã hàng': 'VD0001', 'Tên hàng (không bắt buộc)': 'Tên hàng mẫu', 'Số lượng': 10 },
            { 'Mã hàng': '', 'Tên hàng (không bắt buộc)': '', 'Số lượng': '' },
        ];
        const ws = XLSX.utils.json_to_sheet(rows);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, "MauXinLuanChuyen");
        XLSX.writeFile(wb, `Mau_Xin_Luan_Chuyen.xlsx`);
    }

    async function submitTransferExcelImport() {
        const fileInput = document.getElementById('transfer-excel-file');
        const file = fileInput.files[0];
        if (!file) return;
        const toStore = document.getElementById('transfer-to-store').value;
        if (!toStore) {
            alert('Vui lòng chọn cửa hàng cần xin trước khi nhập từ Excel.');
            fileInput.value = '';
            return;
        }
        const createdEmployee = document.getElementById('transfer-created-employee').value;
        if (!createdEmployee) {
            alert('Vui lòng chọn nhân viên tạo phiếu trước khi nhập từ Excel.');
            fileInput.value = '';
            return;
        }
        const note = document.getElementById('transfer-note').value.trim();
        const formData = new FormData();
        formData.append('file', file);
        formData.append('to_store', toStore);
        formData.append('note', note);
        formData.append('created_employee', createdEmployee);

        document.getElementById('loading-overlay').style.display = 'flex';
        try {
            const res = await fetch('/api/transfer/import-excel', { method: 'POST', body: formData });
            const result = await res.json();
            if (res.ok && result.success) {
                let msg = `Đã gửi phiếu với ${result.item_count} mã hàng.`;
                if (result.skipped_rows) msg += ` (Bỏ qua ${result.skipped_rows} dòng không hợp lệ.)`;
                alert(msg);
                document.getElementById('transfer-to-store').value = '';
                document.getElementById('transfer-note').value = '';
                document.getElementById('transfer-created-employee').value = '';
                loadTransfers();
                refreshTransferHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể nhập file Excel.'));
            }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
        finally {
            document.getElementById('loading-overlay').style.display = 'none';
            fileInput.value = '';
        }
    }

    function renderReceivedItemsChecklist(req) {
        // Ô tick "Đã nhận hàng" cho từng mã - chỉ hiển thị cho phiếu đã được
        // đồng ý (approved), đặt ở cột riêng cạnh "Trạng Thái" cho gọn.
        // Mỗi dòng bọc trong .transfer-received-row (cùng row-gap/chiều cao
        // với .transfer-items-list ở cột "Mã Hàng - Tên Hàng - Số Lượng") để tick "Đã Nhận"
        // luôn nằm NGANG HÀNG với đúng mã hàng tương ứng, dễ kiểm soát.
        if (req.status !== 'approved') return '<span class="text-muted small">—</span>';
        const allReceived = req.items.length > 0 && req.items.every(it => it.received);
        const rows = req.items.map((it, idx) => {
            const qtyLabel = it.quantity_adjusted
                ? ` (đồng ý ${Number(it.approved_quantity || 0).toLocaleString()}, đã sửa từ ${Number(it.quantity || 0).toLocaleString()})`
                : '';
            const hiddenCls = idx >= ITEMS_COLLAPSE_THRESHOLD ? ' js-extra-hidden' : '';
            return `
            <div class="transfer-received-row${hiddenCls}">
                <div class="form-check">
                    <input class="form-check-input" type="checkbox" id="recv-item-${it.id}" ${it.received ? 'checked' : ''} onchange="toggleItemReceived(${it.id}, this.checked)">
                    <label class="form-check-label small ${it.received ? 'text-success text-decoration-line-through' : ''}" for="recv-item-${it.id}">
                        ${escapeHtmlAttr(it.part_code)}${qtyLabel}${it.received ? ' — Đã nhận' : ''}
                    </label>
                </div>
            </div>
        `;
        }).join('');
        // Tick nhanh 1 lần cho CẢ PHIẾU thay vì phải tick từng mã hàng một -
        // đặt RIÊNG bên ngoài .transfer-received-list (không phải 1 dòng
        // trong danh sách), để không làm lệch chỉ số khi syncTransferRowHeights
        // ghép từng dòng "Đã Nhận" với đúng dòng mã hàng tương ứng.
        const allCheckbox = `
            <div class="transfer-received-all mb-2 pb-2 border-bottom">
                <div class="form-check">
                    <input class="form-check-input" type="checkbox" id="recv-all-${req.id}" ${allReceived ? 'checked' : ''} onchange="toggleAllItemsReceived(${req.id}, this.checked)">
                    <label class="form-check-label small fw-bold ${allReceived ? 'text-success' : 'text-primary'}" for="recv-all-${req.id}">
                        Đã Nhận Đủ
                    </label>
                </div>
            </div>
        `;
        return `${allCheckbox}<div class="transfer-received-list">${rows}</div>`;
    }

    // ------------------------------------------------------------------
    // TÌM KIẾM + CHỌN PHIẾU ĐỂ IN (Luân Chuyển Nội Bộ)
    // ------------------------------------------------------------------
    // Danh sách ID phiếu đang được tick chọn để in - dùng chung cho cả 2
    // bảng (Cửa hàng khác gửi đến / Tôi đã gửi), giữ nguyên qua các lần
    // tìm kiếm/tải lại dữ liệu.
    const selectedTransferIds = new Set();

    // Danh sách ID phiếu admin đang tick chọn để gửi "Nhắc Hoàn Thành" -
    // tách riêng khỏi selectedTransferIds (dùng để in phiếu) vì đây là 1
    // thao tác khác hẳn, không nên dùng chung 1 bộ chọn.
    const selectedAdminTransferIds = new Set();

    // Chỉ những phiếu đang DANG DỞ (chờ xử lý, hoặc đã đồng ý nhưng chưa
    // nhận đủ hàng) mới có gì để "nhắc hoàn thành" - phiếu đã từ chối/huỷ/
    // nhận đủ hàng thì bỏ qua.
    function isTransferReminderEligible(r) {
        return r.status === 'pending' || (r.status === 'approved' && !r.all_received);
    }

    function updateAdminTransferSelectedCount() {
        const el = document.getElementById('transfer-admin-selected-count');
        if (el) el.innerText = selectedAdminTransferIds.size;
    }

    function toggleAdminTransferSelect(id, checked) {
        if (checked) selectedAdminTransferIds.add(id); else selectedAdminTransferIds.delete(id);
        updateAdminTransferSelectedCount();
    }

    // Chỉ chọn/bỏ chọn các phiếu ĐỦ ĐIỀU KIỆN đang hiển thị trong bảng admin
    // (đã lọc theo trạng thái/tìm kiếm) - không đụng tới phiếu đã ẩn checkbox.
    function toggleSelectAllAdminTransfers(checked) {
        const checkboxes = document.querySelectorAll('#transfer-admin-body .transfer-admin-select-checkbox');
        checkboxes.forEach(cb => {
            cb.checked = checked;
            const id = Number(cb.value);
            if (checked) selectedAdminTransferIds.add(id); else selectedAdminTransferIds.delete(id);
        });
        updateAdminTransferSelectedCount();
    }

    async function sendAdminTransferReminders() {
        if (selectedAdminTransferIds.size === 0) {
            showToast('Chưa chọn phiếu', 'Vui lòng tick chọn ít nhất 1 phiếu cần nhắc hoàn thành.', 'warning');
            return;
        }
        const ids = Array.from(selectedAdminTransferIds);
        try {
            const res = await fetch('/api/admin/transfer/remind', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ ids: ids })
            });
            const result = await res.json();
            if (!result.success) {
                showToast('Lỗi', result.error || 'Không thể gửi nhắc nhở.', 'danger');
                return;
            }
            selectedAdminTransferIds.clear();
            updateAdminTransferSelectedCount();
            renderAdminTransfers();
            const n = (result.reminded || []).length;
            showToast(
                'Đã gửi nhắc nhở',
                n > 0
                    ? `Đã đẩy thông báo nhắc hoàn thành tới cửa hàng chuyển và cửa hàng nhận của ${n} phiếu.`
                    : 'Không có phiếu nào đủ điều kiện để nhắc (đã xong hoặc không tồn tại).',
                n > 0 ? 'success' : 'warning'
            );
        } catch (e) {
            console.error(e);
            showToast('Lỗi', 'Không thể kết nối máy chủ để gửi nhắc nhở.', 'danger');
        }
    }

    function transferMatchesSearch(r, search) {
        if (!search) return true;
        const s = search.toLowerCase();
        const sCode = normalizeCodeSearch(search);
        if (String(r.id || '').toLowerCase().includes(s)) return true;
        if ((r.to_store || '').toLowerCase().includes(s)) return true;
        if ((r.from_store || '').toLowerCase().includes(s)) return true;
        if ((r.created_employee || '').toLowerCase().includes(s)) return true;
        if ((r.confirmed_employee || '').toLowerCase().includes(s)) return true;
        if ((r.note || '').toLowerCase().includes(s)) return true;
        if ((r.reject_reason || '').toLowerCase().includes(s)) return true;
        if ((r.items || []).some(it => normalizeCodeSearch(it.part_code).includes(sCode))) return true;
        if ((r.items || []).some(it => (it.part_name || '').toLowerCase().includes(s))) return true;
        return false;
    }

    // Lọc theo trạng thái phiếu - tách riêng "Đã Đồng Ý" thành 2 mức để dễ
    // kiểm soát: đã đồng ý nhưng CHƯA nhận đủ hàng (cần theo dõi tiếp), và
    // đã đồng ý và ĐÃ nhận đủ hàng (coi như xong). Cho phép chọn NHIỀU
    // trạng thái cùng lúc (OR với nhau) - values là 1 mảng; mảng rỗng =
    // không lọc gì cả (hiển thị tất cả), giữ đúng hành vi cũ khi chưa chọn.
    function transferMatchesStatusFilter(r, values) {
        if (!values || values.length === 0) return true;
        return values.some(value => {
            if (value === 'approved_not_prepared') return r.status === 'approved' && !r.prepared;
            if (value === 'approved_pending_receipt') return r.status === 'approved' && !r.all_received;
            if (value === 'approved_received') return r.status === 'approved' && !!r.all_received;
            return r.status === value;
        });
    }

    // ------------------------------------------------------------------
    // BỘ LỌC TRẠNG THÁI DẠNG "MULTI-SELECT" (dropdown + checkbox) - dùng
    // chung cho cả 3 bảng phiếu luân chuyển (Nhận, Gửi, Admin). containerId
    // là id của div.dropdown bọc ngoài, chứa các input.status-filter-checkbox.
    // ------------------------------------------------------------------
    function getStatusMultiSelectValues(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return [];
        return Array.from(container.querySelectorAll('.status-filter-checkbox:checked')).map(cb => cb.value);
    }

    function clearStatusMultiSelect(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.querySelectorAll('.status-filter-checkbox').forEach(cb => { cb.checked = false; });
        updateStatusMultiSelectLabel(containerId);
    }

    function updateStatusMultiSelectLabel(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const labelEl = container.querySelector('.status-filter-label');
        if (!labelEl) return;
        const checked = Array.from(container.querySelectorAll('.status-filter-checkbox:checked'));
        if (checked.length === 0) {
            labelEl.textContent = 'Tất cả trạng thái';
        } else if (checked.length === 1) {
            labelEl.textContent = checked[0].closest('label').textContent.trim();
        } else {
            labelEl.textContent = `Đã chọn ${checked.length} trạng thái`;
        }
    }

    // Gọi mỗi khi tick/bỏ tick 1 checkbox trạng thái: cập nhật lại nhãn hiển
    // thị trên nút dropdown rồi mới render lại bảng tương ứng.
    function onTransferStatusFilterChange(containerId, renderFn) {
        updateStatusMultiSelectLabel(containerId);
        renderFn();
    }

    function updateTransferSelectedCount() {
        const n = selectedTransferIds.size;
        ['transfer-selected-count', 'transfer-selected-count-2'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.innerText = n;
        });
    }

    function toggleTransferSelect(id, checked) {
        if (checked) selectedTransferIds.add(id); else selectedTransferIds.delete(id);
        updateTransferSelectedCount();
    }

    // which: 'sent' | 'received' - chỉ chọn/bỏ chọn các phiếu ĐANG hiển thị
    // (đã lọc theo ô tìm kiếm) trong đúng bảng đó, không đụng bảng còn lại.
    function toggleSelectAllTransfers(checked, which) {
        const bodyId = which === 'sent' ? 'transfer-sent-body' : 'transfer-received-body';
        const checkboxes = document.querySelectorAll(`#${bodyId} .transfer-select-checkbox`);
        checkboxes.forEach(cb => {
            cb.checked = checked;
            const id = Number(cb.value);
            if (checked) selectedTransferIds.add(id); else selectedTransferIds.delete(id);
        });
        updateTransferSelectedCount();
    }

    function renderReceivedTransfers() {
        const receivedBody = document.getElementById('transfer-received-body');
        if (!receivedBody) return;
        const searchEl = document.getElementById('transfer-received-search');
        const search = searchEl ? searchEl.value.trim() : '';
        const statusValue = getStatusMultiSelectValues('transfer-received-status-filter');
        const received = (window._lastReceivedRequests || []).filter(r => transferMatchesSearch(r, search) && transferMatchesStatusFilter(r, statusValue));

        receivedBody.innerHTML = received.length ? received.map(r => `
            <tr class="transfer-row-clickable" onclick="handleTransferRowClick(event, ${r.id}, 'received')">
                <td class="text-center"><input type="checkbox" class="form-check-input transfer-select-checkbox" value="${r.id}" ${selectedTransferIds.has(r.id) ? 'checked' : ''} onchange="toggleTransferSelect(${r.id}, this.checked)"></td>
                <td>${storeBadgeColumn(r.from_store, r.items ? r.items.length : 1)}</td>
                <td>${transferItemsCodeColumn(r.items)}</td>
                <td>${transferItemsNameColumn(r.items)}</td>
                <td class="text-end">${transferItemsQtyColumn(r.items)}</td>
                <td class="small transfer-note-cell">${r.note || ''}</td>
                <td>${transferStatusBadge(r, true)}</td>
                <td class="text-muted small">${formatTransferListTime(r.created_at)}</td>
                <td class="text-end text-nowrap">
                    ${r.status === 'pending' ? `
                        <button class="btn btn-success btn-sm d-block w-100 mb-1" onclick="openTransferApproveModal(${r.id})"><i class="bi bi-check-lg"></i> Đồng Ý</button>
                        <button class="btn btn-outline-danger btn-sm d-block w-100" onclick="openTransferRejectModal(${r.id})"><i class="bi bi-x-lg"></i> Từ Chối</button>
                    ` : (r.status === 'approved' || r.status === 'rejected') && !r.any_received ? `
                        <button class="btn btn-outline-secondary btn-sm" onclick="revertTransfer(${r.id})"><i class="bi bi-arrow-counterclockwise"></i> Đổi Lại</button>
                    ` : ''}
                </td>
            </tr>
        `).join('') : `<tr><td colspan="9" class="text-center py-4 text-muted">${(search || statusValue.length) ? 'Không tìm thấy phiếu phù hợp.' : 'Chưa có phiếu nào gửi đến.'}</td></tr>`;

        const pendingCount = (window._lastReceivedRequests || []).filter(r => r.status === 'pending').length;
        // Cập nhật cả badge trên nút tab GỐC (đã ẩn) lẫn 2 badge hiển thị
        // trong mega-nav mới (1 ở nút nhóm cha "Luân Chuyển Nội Bộ" để thấy
        // ngay không cần mở dropdown, 1 ở đúng mục "Xuất Nội Bộ" bên trong).
        ['transfer-badge', 'transfer-badge-mega', 'transfer-badge-mega-2'].forEach(badgeId => {
            const badge = document.getElementById(badgeId);
            if (!badge) return;
            if (pendingCount > 0) { badge.style.display = 'inline-block'; badge.innerText = pendingCount; }
            else { badge.style.display = 'none'; }
        });
        syncTransferRowHeights('transfer-received-body');
    }

    function renderSentTransfers() {
        const sentBody = document.getElementById('transfer-sent-body');
        if (!sentBody) return;
        const searchEl = document.getElementById('transfer-sent-search');
        const search = searchEl ? searchEl.value.trim() : '';
        const statusValue = getStatusMultiSelectValues('transfer-sent-status-filter');
        const sent = (window._lastSentRequests || []).filter(r => transferMatchesSearch(r, search) && transferMatchesStatusFilter(r, statusValue));

        sentBody.innerHTML = sent.length ? sent.map(r => `
            <tr class="transfer-row-clickable" onclick="handleTransferRowClick(event, ${r.id}, 'sent')">
                <td class="text-center"><input type="checkbox" class="form-check-input transfer-select-checkbox" value="${r.id}" ${selectedTransferIds.has(r.id) ? 'checked' : ''} onchange="toggleTransferSelect(${r.id}, this.checked)"></td>
                <td>${storeBadgeColumn(r.to_store, r.items ? r.items.length : 1)}</td>
                <td>${transferItemsCodeColumn(r.items)}</td>
                <td>${transferItemsNameColumn(r.items)}</td>
                <td class="text-end">${transferItemsQtyColumn(r.items)}</td>
                <td>${transferStatusBadge(r)}</td>
                <td>${renderReceivedItemsChecklist(r)}</td>
                <td class="small transfer-note-cell">${r.status === 'rejected' ? (r.reject_reason || '') : (r.note || '')}</td>
                <td class="text-muted small">${formatTransferListTime(r.created_at)}</td>
                <td class="text-end">
                    ${r.status === 'pending' ? `<button class="btn btn-outline-secondary btn-sm" onclick="cancelTransferRequest(${r.id})"><i class="bi bi-x-lg"></i> Huỷ</button>` : ''}
                </td>
            </tr>
        `).join('') : `<tr><td colspan="10" class="text-center py-4 text-muted">${(search || statusValue.length) ? 'Không tìm thấy phiếu phù hợp.' : 'Bạn chưa gửi phiếu nào.'}</td></tr>`;
        syncTransferRowHeights('transfer-sent-body');
    }

    // ------------------------------------------------------------------
    // IN PHIẾU LUÂN CHUYỂN NỘI BỘ (khổ A4 ngang, mỗi phiếu 1 trang)
    // ------------------------------------------------------------------
    function buildPrintPageForTransfer(r) {
        const itemRows = (r.items || []).map((it, idx) => {
            const qty = it.quantity_adjusted
                ? `${Number(it.approved_quantity || 0).toLocaleString()} (xin ${Number(it.quantity || 0).toLocaleString()})`
                : Number(it.quantity || 0).toLocaleString();
            return `
                <tr>
                    <td class="print-stt-cell">${idx + 1}</td>
                    <td>${escapeHtmlAttr(it.part_code)}</td>
                    <td>${escapeHtmlAttr(it.part_name || '')}</td>
                    <td class="print-qty-cell">${qty}</td>
                </tr>
            `;
        }).join('');

        const noteText = r.status === 'rejected' ? (r.reject_reason || '') : (r.note || '');

        return `
            <div class="print-page">
                <div class="print-header">
                    <div class="print-header-logo">
                        <img src="${NS_LOGO_URL}" alt="Nam Sương Motor">
                    </div>
                    <div class="print-header-left">
                        <div class="print-company-name">Cty TNHH Cà Phê Nam Sương</div>
                        <div>Phòng Phụ tùng - Dịch vụ</div>
                    </div>
                </div>
                <div class="print-title">
                    <h3>PHIẾU LUÂN CHUYỂN NỘI BỘ</h3>
                    <div class="print-subtitle">Nam Sương Motor - Số phiếu: ${r.id}</div>
                </div>
                <div class="print-meta-row">
                    <div><b>Cửa hàng xin:</b> ${escapeHtmlAttr(r.from_store || '—')}</div>
                    <div><b>Cửa hàng cho:</b> ${escapeHtmlAttr(r.to_store || '—')}</div>
                    <div><b>Thời gian gửi:</b> ${escapeHtmlAttr(r.created_at || '')}</div>
                </div>
                <div class="print-meta-row">
                    <div><b>Người tạo:</b> ${escapeHtmlAttr(r.created_employee || '—')}</div>
                    <div><b>Người xác nhận:</b> ${escapeHtmlAttr(r.confirmed_employee || '—')}</div>
                    <div><b>Trạng thái:</b> ${TRANSFER_STATUS_LABELS_PLAIN[r.status] || escapeHtmlAttr(r.status || '')}${r.status === 'approved' ? (r.prepared ? ' — Đã Soạn Hàng' : ' — Chưa Soạn Hàng') : ''}</div>
                </div>
                <table class="print-items-table">
                    <thead>
                        <tr><th style="width:50px;">STT</th><th>Mã Hàng</th><th>Tên Hàng</th><th style="width:140px;">Số Lượng</th></tr>
                    </thead>
                    <tbody>${itemRows}</tbody>
                </table>
                ${noteText ? `<div class="print-note"><b>Ghi chú:</b> ${escapeHtmlAttr(noteText)}</div>` : ''}
                <div class="print-signatures">
                    <div><div class="print-sign-label">Người Gửi</div><div class="print-sign-hint">(Ký, ghi rõ họ tên)</div></div>
                    <div><div class="print-sign-label">Người Nhận</div><div class="print-sign-hint">(Ký, ghi rõ họ tên)</div></div>
                </div>
            </div>
        `;
    }

    function printSelectedTransfers() {
        if (selectedTransferIds.size === 0) {
            alert('Vui lòng tick chọn ít nhất 1 phiếu ở cột đầu bảng để in.');
            return;
        }
        const all = window._lastTransferRequests || [];
        const toPrint = all.filter(r => selectedTransferIds.has(r.id));
        if (toPrint.length === 0) {
            alert('Không tìm thấy phiếu đã chọn (dữ liệu có thể vừa được tải lại), vui lòng chọn lại.');
            return;
        }
        const printArea = document.getElementById('print-area');
        printArea.innerHTML = toPrint.map(buildPrintPageForTransfer).join('');
        window.print();
    }

    // Xuất Excel các phiếu luân chuyển nội bộ ĐANG ĐƯỢC TICK CHỌN (dùng
    // chung bộ chọn selectedTransferIds với nút "In Phiếu Đã Chọn" ở trên -
    // tick 1 lần, dùng được cho cả in lẫn xuất Excel). Mỗi mã hàng trong
    // phiếu là 1 dòng riêng (phiếu nào nhiều mã hàng thì các thông tin
    // chung của phiếu lặp lại ở từng dòng) để dễ lọc/tổng hợp trong Excel;
    // phiếu không có mã hàng nào vẫn xuất ra đúng 1 dòng (cột mã hàng để
    // trống) để không bị mất khỏi file.
    function exportSelectedTransfersToExcel() {
        if (selectedTransferIds.size === 0) {
            alert('Vui lòng tick chọn ít nhất 1 phiếu ở cột đầu bảng để xuất Excel.');
            return;
        }
        const all = window._lastTransferRequests || [];
        const toExport = all.filter(r => selectedTransferIds.has(r.id));
        if (toExport.length === 0) {
            alert('Không tìm thấy phiếu đã chọn (dữ liệu có thể vừa được tải lại), vui lòng chọn lại.');
            return;
        }

        const rows = [];
        toExport.forEach(r => {
            const noteText = r.status === 'rejected' ? (r.reject_reason || '') : (r.note || '');
            const baseInfo = {
                'Mã Phiếu': r.id,
                'Cửa Hàng Chuyển': r.to_store || '',
                'Cửa Hàng Nhận': r.from_store || '',
                'Trạng Thái': TRANSFER_STATUS_LABELS_PLAIN[r.status] || r.status || '',
                'Người Tạo': r.created_employee || '',
                'Người Xác Nhận': r.confirmed_employee || '',
                'Thời Gian Gửi': r.created_at || '',
                'Ghi Chú': noteText,
            };
            const items = (r.items && r.items.length) ? r.items : [null];
            items.forEach(it => {
                rows.push({
                    ...baseInfo,
                    'Mã Hàng': it ? it.part_code : '',
                    'Tên Hàng': it ? (it.part_name || '') : '',
                    'Số Lượng Xin': it ? Number(it.quantity || 0) : '',
                    'Số Lượng Duyệt': it
                        ? (it.quantity_adjusted ? Number(it.approved_quantity || 0) : Number(it.quantity || 0))
                        : '',
                    'Đã Nhận': it ? (it.received ? 'Đã nhận' : 'Chưa nhận') : '',
                });
            });
        });

        const ws = XLSX.utils.json_to_sheet(rows);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, 'PhieuLuanChuyen');
        XLSX.writeFile(wb, `Phieu_Luan_Chuyen_Da_Chon_${new Date().toISOString().slice(0,10)}.xlsx`);
    }

    function printSingleTransfer(id) {
        const r = (window._lastTransferRequests || []).find(x => x.id === id);
        if (!r) return;
        const printArea = document.getElementById('print-area');
        printArea.innerHTML = buildPrintPageForTransfer(r);
        window.print();
    }

    // ------------------------------------------------------------------
    // MODAL "XEM CHI TIẾT PHIẾU" - hiển thị 1 phiếu luân chuyển nội bộ
    // phóng to, độc lập trên màn hình (dễ quan sát/thao tác hơn nhìn dồn
    // trong 1 dòng chật hẹp của bảng). Mở khi bấm vào dòng bất kỳ ở 3
    // bảng: Phiếu Cửa Hàng Khác Gửi Đến (received), Phiếu Tôi Đã Gửi
    // (sent), và Toàn Bộ Phiếu (admin).
    // ------------------------------------------------------------------
    // Đang mở phiếu nào (nếu có) - dùng để tự làm mới nội dung modal ngay
    // khi dữ liệu đổi (vd sau khi tick "Đã Nhận", đổi trạng thái Soạn
    // Hàng... ngay TRONG modal), không cần đóng/mở lại mới thấy cập nhật.
    let _openTransferDetail = null;

    function handleTransferRowClick(e, id, direction) {
        // Bỏ qua nếu bấm trúng ô tick, nút, hoặc badge đã có sẵn thao tác
        // riêng trong dòng - để không mở đè modal chi tiết lên trên hành
        // động người dùng đang thực sự muốn làm (tick chọn in, Đồng Ý/Từ
        // Chối, đổi trạng thái Soạn Hàng, Xem thêm mã...).
        if (e.target.closest('button, input, a, label')) return;
        openTransferDetail(id, direction);
    }

    function getTransferListByDirection(direction) {
        if (direction === 'received') return window._lastReceivedRequests || [];
        if (direction === 'sent') return window._lastSentRequests || [];
        return window._lastTransferRequests || [];
    }

    function openTransferDetail(id, direction) {
        const r = getTransferListByDirection(direction).find(x => x.id === id);
        if (!r) return;
        _openTransferDetail = { id: id, direction: direction };
        renderTransferDetailModal(r, direction);
        new bootstrap.Modal(document.getElementById('transfer-detail-modal')).show();
    }

    function renderTransferDetailModal(r, direction) {
        document.getElementById('transfer-detail-title').innerHTML =
            `<i class="bi bi-file-earmark-text me-2"></i>Phiếu Luân Chuyển Nội Bộ <span class="opacity-75">#${r.id}</span>`;
        document.getElementById('transfer-detail-body').innerHTML = buildTransferDetailBody(r, direction);
        document.getElementById('transfer-detail-footer-actions').innerHTML = buildTransferDetailFooterButtons(r, direction);
    }

    // Nếu modal chi tiết đang mở đúng phiếu vừa đổi dữ liệu (loadTransfers()
    // gọi hàm này sau mỗi lần tải lại) - vẽ lại nội dung modal tại chỗ, để
    // người dùng thấy ngay kết quả thao tác (vd vừa tick Đã Nhận) mà không
    // phải đóng rồi mở lại. Nếu phiếu đang xem không còn tồn tại nữa (vd bị
    // admin xoá hẳn trong lúc mình đang mở xem) - tự đóng modal lại kèm
    // thông báo, tránh để modal đứng yên với nội dung/nút bấm đã lỗi thời.
    function refreshOpenTransferDetailIfAny() {
        if (!_openTransferDetail) return;
        const modalEl = document.getElementById('transfer-detail-modal');
        if (!modalEl || !modalEl.classList.contains('show')) return;
        const r = getTransferListByDirection(_openTransferDetail.direction).find(x => x.id === _openTransferDetail.id);
        if (r) {
            renderTransferDetailModal(r, _openTransferDetail.direction);
        } else {
            bootstrap.Modal.getInstance(modalEl)?.hide();
            showToast(`Phiếu #${_openTransferDetail.id}`, 'Phiếu này không còn tồn tại nữa (có thể đã bị admin xoá).', 'warning');
        }
    }

    function buildTransferDetailBody(r, direction) {
        const showReceivedCol = direction === 'sent' && r.status === 'approved';
        const allReceived = showReceivedCol && r.items.length > 0 && r.items.every(it => it.received);

        const itemRows = (r.items || []).map((it, idx) => {
            const requested = Number(it.quantity || 0).toLocaleString();
            let qtyCell;
            if (it.quantity_adjusted) {
                const approvedQty = Number(it.approved_quantity || 0);
                qtyCell = approvedQty === 0
                    ? `<span class="text-danger fw-semibold">0 — Hết hàng</span><br><span class="text-muted small">(xin ${requested})</span>`
                    : `<span class="text-success fw-semibold">${approvedQty.toLocaleString()}</span><br><span class="text-muted small">(xin ${requested})</span>`;
            } else {
                qtyCell = requested;
            }
            const receivedCell = showReceivedCol ? `
                <td class="text-center">
                    <input class="form-check-input" type="checkbox" id="detail-recv-item-${it.id}" ${it.received ? 'checked' : ''} onchange="toggleItemReceived(${it.id}, this.checked)">
                </td>` : '';
            return `
                <tr>
                    <td class="text-center text-muted">${idx + 1}</td>
                    <td><span class="part-code">${escapeHtmlAttr(it.part_code)}</span></td>
                    <td>${escapeHtmlAttr(it.part_name || '')}</td>
                    <td class="text-end">${qtyCell}</td>
                    ${receivedCell}
                </tr>`;
        }).join('');

        const noteText = r.status === 'rejected' ? (r.reject_reason || '') : (r.note || '');

        // Banner cảnh báo nếu phiếu đang có yêu cầu xin admin xoá - hiện cho
        // cả admin (để biết lý do trước khi quyết định xoá/bỏ qua) lẫn 2
        // cửa hàng liên quan (để biết đã gửi yêu cầu, đang chờ admin xử lý).
        const deleteRequestBanner = r.delete_requested ? `
            <div class="alert alert-warning py-2 px-3 mb-3 small d-flex gap-2 align-items-start">
                <i class="bi bi-exclamation-triangle-fill mt-1"></i>
                <div>
                    <b>${escapeHtmlAttr(r.delete_requested_by || '')}</b> đã gửi yêu cầu nhờ admin xoá hẳn phiếu này lúc ${escapeHtmlAttr(r.delete_requested_at || '—')}.
                    <br><span class="text-muted">Lý do:</span> ${escapeHtmlAttr(r.delete_request_reason || '')}
                </div>
            </div>` : '';

        return `
            ${deleteRequestBanner}
            <div class="row g-3 mb-3">
                <div class="col-6 col-md-3">
                    <div class="text-muted small">Cửa Hàng Chuyển</div>
                    <div class="fw-semibold">${storeBadge(r.to_store)}</div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="text-muted small">Cửa Hàng Nhận</div>
                    <div class="fw-semibold">${storeBadge(r.from_store)}</div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="text-muted small">Người Tạo</div>
                    <div class="fw-semibold">${escapeHtmlAttr(r.created_employee || '—')}</div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="text-muted small">Người Xác Nhận</div>
                    <div class="fw-semibold">${escapeHtmlAttr(r.confirmed_employee || '—')}</div>
                </div>
                <div class="col-6 col-md-4">
                    <div class="text-muted small">Thời Gian Gửi</div>
                    <div class="fw-semibold">${escapeHtmlAttr(r.created_at || '—')}</div>
                </div>
                <div class="col-6 col-md-8">
                    <div class="text-muted small">Trạng Thái</div>
                    <div>${transferStatusBadge(r, direction === 'received')}</div>
                </div>
            </div>
            ${showReceivedCol ? `
            <div class="form-check mb-2 pb-2 border-bottom">
                <input class="form-check-input" type="checkbox" id="detail-recv-all-${r.id}" ${allReceived ? 'checked' : ''} onchange="toggleAllItemsReceived(${r.id}, this.checked)">
                <label class="form-check-label small fw-bold ${allReceived ? 'text-success' : 'text-primary'}" for="detail-recv-all-${r.id}">Đánh Dấu Đã Nhận Đủ (tất cả mã hàng)</label>
            </div>` : ''}
            <div class="table-responsive border rounded-3 mb-3">
                <table class="table table-sm align-middle mb-0">
                    <thead class="table-light">
                        <tr>
                            <th style="width:40px;" class="text-center">#</th>
                            <th>Mã Hàng</th>
                            <th>Tên Hàng</th>
                            <th class="text-end">Số Lượng</th>
                            ${showReceivedCol ? '<th class="text-center" style="width:90px;">Đã Nhận</th>' : ''}
                        </tr>
                    </thead>
                    <tbody>${itemRows}</tbody>
                </table>
            </div>
            ${noteText ? `<div><span class="text-muted small">${r.status === 'rejected' ? 'Lý do từ chối' : 'Ghi chú'}:</span> <span class="fw-semibold">${escapeHtmlAttr(noteText)}</span></div>` : ''}
        `;
    }

    function buildTransferDetailFooterButtons(r, direction) {
        const btns = [];
        if (direction === 'received' && r.status === 'pending') {
            btns.push(`<button type="button" class="btn btn-success" onclick="detailApproveTransfer(${r.id})"><i class="bi bi-check-lg me-1"></i>Đồng Ý</button>`);
            btns.push(`<button type="button" class="btn btn-outline-danger" onclick="detailRejectTransfer(${r.id})"><i class="bi bi-x-lg me-1"></i>Từ Chối</button>`);
        } else if (direction === 'received' && (r.status === 'approved' || r.status === 'rejected') && !r.any_received) {
            btns.push(`<button type="button" class="btn btn-outline-secondary" onclick="revertTransfer(${r.id})"><i class="bi bi-arrow-counterclockwise me-1"></i>Đổi Lại</button>`);
        }
        if (direction === 'sent' && r.status === 'pending') {
            btns.push(`<button type="button" class="btn btn-outline-danger" onclick="cancelTransferRequest(${r.id})"><i class="bi bi-x-lg me-1"></i>Huỷ Phiếu</button>`);
        }
        // Cửa hàng (cả bên gửi lẫn bên nhận của phiếu) nhờ admin xoá hẳn
        // phiếu - dùng cho các phiếu đã xong việc (đồng ý/từ chối/huỷ) mà
        // "Huỷ Phiếu" ở trên không xử lý được nữa (chỉ áp dụng cho pending).
        if (direction === 'sent' || direction === 'received') {
            btns.push(r.delete_requested
                ? `<button type="button" class="btn btn-outline-secondary" disabled><i class="bi bi-hourglass-split me-1"></i>Đã Gửi Yêu Cầu Xoá</button>`
                : `<button type="button" class="btn btn-outline-warning" onclick="openTransferRequestDeleteModal(${r.id})"><i class="bi bi-trash3 me-1"></i>Nhờ Admin Xoá Phiếu</button>`);
        }
        // Admin: xử lý trực tiếp yêu cầu xin xoá ngay trong modal chi tiết
        // (mở từ icon chuông hoặc từ bảng "Toàn Bộ Phiếu").
        if (direction === 'admin') {
            if (r.delete_requested) {
                btns.push(`<button type="button" class="btn btn-outline-secondary" onclick="dismissDeleteRequest(${r.id})"><i class="bi bi-x-circle me-1"></i>Bỏ Qua Yêu Cầu Xoá</button>`);
            }
            btns.push(`<button type="button" class="btn btn-outline-danger" onclick="deleteAdminTransfer(${r.id})"><i class="bi bi-trash me-1"></i>Xoá Hẳn Phiếu</button>`);
        }
        btns.push(`<button type="button" class="btn btn-outline-primary" onclick="printSingleTransfer(${r.id})"><i class="bi bi-printer me-1"></i>In Phiếu Này</button>`);
        return btns.join('');
    }

    // Đồng Ý/Từ Chối cần mở modal riêng (đã có sẵn, có form nhập số
    // lượng/lý do) - đóng modal chi tiết lại trước để tránh 2 modal chồng
    // nhau, sau khi hoàn tất thao tác ở đó dữ liệu bảng vẫn được cập nhật
    // bình thường qua loadTransfers() như cũ.
    function detailApproveTransfer(id) {
        bootstrap.Modal.getInstance(document.getElementById('transfer-detail-modal'))?.hide();
        openTransferApproveModal(id);
    }
    function detailRejectTransfer(id) {
        bootstrap.Modal.getInstance(document.getElementById('transfer-detail-modal'))?.hide();
        openTransferRejectModal(id);
    }

    // Đổi ô "Từ ngày"/"Đến ngày" -> tải lại danh sách ngay (server sẽ trả về
    // ĐÚNG các phiếu trong khoảng ngày này, không giới hạn theo _TRANSFER_LIST_RECENT_DAYS).
    function onTransferDateFilterChange() {
        loadTransfers();
    }

    function clearTransferDateFilter() {
        const fromEl = document.getElementById('transfer-admin-date-from');
        const toEl = document.getElementById('transfer-admin-date-to');
        if (fromEl) fromEl.value = '';
        if (toEl) toEl.value = '';
        loadTransfers();
    }

    // Có đang lọc theo ngày hay không - dùng để quyết định có gửi
    // date_from/date_to lên server và có ẩn nút "Xem lịch sử cũ hơn" không.
    function isTransferDateFilterActive() {
        const fromEl = document.getElementById('transfer-admin-date-from');
        const toEl = document.getElementById('transfer-admin-date-to');
        return !!((fromEl && fromEl.value) || (toEl && toEl.value));
    }

    async function loadTransfers() {
        const adminFilter = document.getElementById('transfer-admin-store-filter');
        const dateFromEl = document.getElementById('transfer-admin-date-from');
        const dateToEl = document.getElementById('transfer-admin-date-to');
        const dateHint = document.getElementById('transfer-admin-date-filter-hint');

        const params = new URLSearchParams();
        if (adminFilter) params.set('store', adminFilter.value);
        const dateFiltering = isTransferDateFilterActive();
        if (dateFiltering) {
            if (dateFromEl && dateFromEl.value) params.set('date_from', dateFromEl.value);
            if (dateToEl && dateToEl.value) params.set('date_to', dateToEl.value);
        }
        if (dateHint) dateHint.style.display = dateFiltering ? '' : 'none';

        const url = `/api/transfer/list?${params.toString()}`;

        try {
            const res = await fetch(url);
            const result = await res.json();
            if (!result.success) return;

            const requests = result.requests;
            // Sắp xếp mã hàng trong TỪNG phiếu từ nhỏ tới lớn ngay tại đây,
            // trước khi phân phối cho mọi nơi hiển thị (cột "Mã Hàng - Tên Hàng - Số Lượng",
            // checklist "Đã Nhận", modal đồng ý, phiếu in...) - nhờ vậy tất
            // cả đều dùng chung 1 thứ tự đã sắp xếp, dễ kiểm soát và các cột
            // liên quan (vd tick đã nhận) vẫn thẳng hàng đúng theo mã hàng.
            requests.forEach(r => {
                if (Array.isArray(r.items)) {
                    r.items.sort((a, b) => comparePartCodes(a.part_code, b.part_code));
                }
            });
            window._lastTransferRequests = requests;
            renderTransferOverviewStats(); // cập nhật số liệu ở trang "Tổng Quan" (nếu đang có)
            // Mặc định chỉ tải phiếu gần đây (xem _TRANSFER_LIST_RECENT_DAYS
            // ở backend) - 2 biến này là "con trỏ" để bấm nút "Xem lịch sử
            // cũ hơn" tải tiếp lô cũ hơn, nối vào danh sách đang có. Khi đang
            // lọc theo ngày thì không áp dụng cơ chế "tải thêm" này nữa.
            window._transferOldestCreatedAt = dateFiltering ? null : result.oldest_created_at;
            window._transferHasMore = dateFiltering ? false : !!result.has_more;
            updateTransferLoadMoreButtons();

            const sentBody = document.getElementById('transfer-sent-body');
            const receivedBody = document.getElementById('transfer-received-body');
            const adminBody = document.getElementById('transfer-admin-body');

            if (sentBody && receivedBody) {
                window._lastSentRequests = requests.filter(r => r.direction === 'sent');
                window._lastReceivedRequests = requests.filter(r => r.direction === 'received');

                // Bỏ chọn những phiếu không còn tồn tại nữa (đã bị huỷ/xử lý
                // ở nơi khác) để bộ đếm "Đã chọn" không bị sai.
                const stillExisting = new Set(requests.map(r => r.id));
                Array.from(selectedTransferIds).forEach(id => { if (!stillExisting.has(id)) selectedTransferIds.delete(id); });

                renderReceivedTransfers();
                renderSentTransfers();
                updateTransferSelectedCount();
            }

            if (adminBody) {
                renderAdminTransfers();
            }

            // Modal "Xem Chi Tiết Phiếu" (nếu đang mở) cần vẽ lại theo dữ
            // liệu vừa tải mới nhất - để mọi thao tác thực hiện NGAY trong
            // modal (tick Đã Nhận, đổi Soạn Hàng...) thấy kết quả cập nhật
            // tại chỗ, không cần đóng/mở lại.
            refreshOpenTransferDetailIfAny();
        } catch (e) { console.error(e); }
    }

    // Bấm "Xem lịch sử cũ hơn": tải tiếp 1 lô phiếu ĐÃ XONG (approved/rejected)
    // cũ hơn mốc window._transferOldestCreatedAt, nối vào danh sách đang có
    // rồi render lại - không gọi lại loadTransfers() vì sẽ làm mất phần lịch
    // sử cũ vừa tải thêm (loadTransfers() luôn chỉ trả về lô gần đây nhất).
    async function loadMoreTransferHistory(btn) {
        if (!window._transferOldestCreatedAt || isTransferDateFilterActive()) return;
        const adminFilter = document.getElementById('transfer-admin-store-filter');
        const params = new URLSearchParams({ before: window._transferOldestCreatedAt });
        if (adminFilter) params.set('store', adminFilter.value);

        const originalHtml = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Đang tải...'; }

        try {
            const res = await fetch(`/api/transfer/list?${params.toString()}`);
            const result = await res.json();
            if (!result.success) return;

            const newRequests = result.requests || [];
            newRequests.forEach(r => {
                if (Array.isArray(r.items)) {
                    r.items.sort((a, b) => comparePartCodes(a.part_code, b.part_code));
                }
            });

            const existingIds = new Set((window._lastTransferRequests || []).map(r => r.id));
            const merged = (window._lastTransferRequests || []).concat(newRequests.filter(r => !existingIds.has(r.id)));
            window._lastTransferRequests = merged;
            window._transferOldestCreatedAt = result.oldest_created_at || window._transferOldestCreatedAt;
            window._transferHasMore = !!result.has_more && newRequests.length > 0;
            updateTransferLoadMoreButtons();

            window._lastSentRequests = merged.filter(r => r.direction === 'sent');
            window._lastReceivedRequests = merged.filter(r => r.direction === 'received');
            if (document.getElementById('transfer-sent-body')) renderSentTransfers();
            if (document.getElementById('transfer-received-body')) renderReceivedTransfers();
            if (document.getElementById('transfer-admin-body')) renderAdminTransfers();
        } catch (e) {
            console.error(e);
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = originalHtml; }
        }
    }

    // Hiện/ẩn các nút "Xem lịch sử cũ hơn" (admin + đã gửi) tuỳ theo còn
    // lịch sử cũ hơn để tải hay không.
    function updateTransferLoadMoreButtons() {
        ['transfer-admin-load-more-btn', 'transfer-sent-load-more-btn'].forEach(id => {
            const btn = document.getElementById(id);
            if (btn) btn.classList.toggle('d-none', !window._transferHasMore);
        });
    }

    // Render lại riêng bảng admin theo bộ lọc trạng thái + ô tìm kiếm, KHÔNG
    // gọi lại API - dùng danh sách đã tải sẵn ở window._lastTransferRequests,
    // nên gõ tìm kiếm mượt, không giật/lag chờ mạng.
    function renderAdminTransfers() {
        const adminBody = document.getElementById('transfer-admin-body');
        if (!adminBody) return;

        const requests = window._lastTransferRequests || [];
        const adminStatusValue = getStatusMultiSelectValues('transfer-admin-status-filter');
        const adminSearchEl = document.getElementById('transfer-admin-search');
        const adminSearchValue = adminSearchEl ? adminSearchEl.value.trim() : '';

        const adminRequests = requests.filter(r =>
            transferMatchesStatusFilter(r, adminStatusValue) && transferMatchesSearch(r, adminSearchValue)
        );
        // Bỏ chọn khỏi bộ nhắc nhở những phiếu không còn hiển thị/đủ điều
        // kiện nữa (đã bị xử lý xong ở nơi khác trong lúc admin đang xem).
        const stillEligibleIds = new Set(adminRequests.filter(isTransferReminderEligible).map(r => r.id));
        Array.from(selectedAdminTransferIds).forEach(id => { if (!stillEligibleIds.has(id)) selectedAdminTransferIds.delete(id); });

        adminBody.innerHTML = adminRequests.length ? adminRequests.map(r => `
            <tr class="transfer-row-clickable" onclick="handleTransferRowClick(event, ${r.id}, 'admin')">
                <td class="text-center">${isTransferReminderEligible(r) ? `<input type="checkbox" class="form-check-input transfer-admin-select-checkbox" value="${r.id}" ${selectedAdminTransferIds.has(r.id) ? 'checked' : ''} onchange="toggleAdminTransferSelect(${r.id}, this.checked)">` : ''}</td>
                <td class="text-center text-muted small">${r.id}</td>
                <td>${storeBadge(r.to_store)}</td>
                <td>${storeBadge(r.from_store)}</td>
                <td>${transferItemsCodeColumn(r.items)}</td>
                <td>${transferItemsNameColumn(r.items)}</td>
                <td class="text-end">${transferItemsQtyColumn(r.items)}</td>
                <td>${transferStatusBadge(r)}</td>
                <td class="small transfer-note-cell">${r.status === 'rejected' ? (r.reject_reason || '') : (r.note || '')}</td>
                <td class="text-muted small">${formatTransferListTime(r.created_at)}</td>
                <td class="text-end" onclick="event.stopPropagation()">
                    <button class="btn btn-outline-danger btn-sm" title="Xoá hẳn phiếu này" onclick="deleteAdminTransfer(${r.id})"><i class="bi bi-trash"></i></button>
                </td>
            </tr>
        `).join('') : `<tr><td colspan="11" class="text-center py-4 text-muted">${(adminStatusValue.length || adminSearchValue) ? 'Không có phiếu phù hợp với bộ lọc/tìm kiếm.' : 'Chưa có phiếu luân chuyển nào.'}</td></tr>`;
        updateAdminTransferSelectedCount();
        syncTransferRowHeights('transfer-admin-body');
    }

    // Admin XOÁ HẲN 1 phiếu luân chuyển khỏi hệ thống (khác với Huỷ - vốn
    // chỉ đổi trạng thái và vẫn còn phiếu để xem lại) - dùng khi phiếu tạo
    // nhầm hoặc không còn cần lưu nữa. Xoá xong tải lại danh sách ngay.
    async function deleteAdminTransfer(id) {
        if (!await nsConfirm(`Xoá HẲN phiếu #${id}? Thao tác này không thể hoàn tác.`)) return;
        try {
            const res = await fetch('/api/admin/transfer/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                // Đóng modal chi tiết (nếu đang mở đúng phiếu vừa xoá) trước
                // khi tải lại - phiếu không còn tồn tại nữa nên không thể vẽ
                // lại nội dung modal như các thao tác khác. Xoá luôn
                // _openTransferDetail NGAY (không đợi animation đóng modal
                // chạy xong mới tự xoá qua sự kiện 'hidden.bs.modal') để
                // refreshOpenTransferDetailIfAny() (gọi ngay sau trong
                // loadTransfers()) không hiểu nhầm đây là phiếu "biến mất
                // ngoài ý muốn" rồi hiện thêm 1 toast cảnh báo thừa - admin
                // đã biết rõ mình vừa tự xoá phiếu này.
                bootstrap.Modal.getInstance(document.getElementById('transfer-detail-modal'))?.hide();
                if (_openTransferDetail && _openTransferDetail.id === id) _openTransferDetail = null;
                loadTransfers();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể xoá phiếu.'));
            }
        } catch (e) {
            alert('Lỗi kết nối server.');
        }
    }

    async function respondTransfer(id, action, extra) {
        try {
            const res = await fetch('/api/transfer/respond', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(Object.assign({ id: id, action: action }, extra || {}))
            });
            const result = await res.json();
            if (res.ok && result.success) {
                loadTransfers();
                refreshTransferHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể xử lý phiếu.'));
            }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
    }

    function openTransferApproveModal(id) {
        document.getElementById('transfer-approve-id').value = id;
        const req = (window._lastTransferRequests || []).find(r => r.id === id);
        const itemsBox = document.getElementById('transfer-approve-items');
        itemsBox.innerHTML = (req && req.items ? req.items : []).map(it => `
            <div class="transfer-approve-item">
                <div class="transfer-approve-item-info">
                    <span class="part-code">${escapeHtmlAttr(it.part_code)}</span>
                    ${it.part_name ? `<div class="transfer-approve-item-name">${escapeHtmlAttr(it.part_name)}</div>` : ''}
                    <div class="transfer-approve-item-requested"><b>Đã xin: ${Number(it.quantity || 0).toLocaleString()}</b></div>
                </div>
                <div class="transfer-approve-item-input">
                    <label class="transfer-approve-input-label">Số lượng đồng ý</label>
                    <input type="number" class="form-control transfer-approve-qty-input"
                           min="0" step="1" data-item-id="${it.id}"
                           value="${Number(it.approved_quantity != null ? it.approved_quantity : it.quantity || 0)}">
                </div>
            </div>
        `).join('');
        fillEmployeeSelect(document.getElementById('transfer-approve-employee'), CURRENT_STORE);
        new bootstrap.Modal(document.getElementById('transferApproveModal')).show();
    }

    async function submitTransferApprove() {
        const id = document.getElementById('transfer-approve-id').value;
        const confirmedEmployee = document.getElementById('transfer-approve-employee').value;
        if (!confirmedEmployee) { alert('Vui lòng chọn nhân viên xác nhận.'); return; }

        const qtyInputs = document.querySelectorAll('#transfer-approve-items .transfer-approve-qty-input');
        const items = [];
        for (const input of qtyInputs) {
            const qty = Number(input.value);
            if (input.value === '' || isNaN(qty) || qty < 0) { alert('Số lượng đồng ý không hợp lệ cho mọi mã hàng (có thể nhập 0 nếu không cho được mã đó).'); return; }
            items.push({ id: Number(input.dataset.itemId), approved_quantity: qty });
        }

        await respondTransfer(Number(id), 'approve', { confirmed_employee: confirmedEmployee, items: items });
        bootstrap.Modal.getInstance(document.getElementById('transferApproveModal'))?.hide();
    }

    function openTransferRejectModal(id) {
        document.getElementById('transfer-reject-id').value = id;
        const req = (window._lastTransferRequests || []).find(r => r.id === id);
        document.getElementById('transfer-reject-summary').innerHTML = req ? `Mã hàng: ${transferItemsSummary(req.items)}` : '';
        document.getElementById('transfer-reject-reason').value = '';
        new bootstrap.Modal(document.getElementById('transferRejectModal')).show();
    }

    async function submitTransferReject() {
        const id = document.getElementById('transfer-reject-id').value;
        const reason = document.getElementById('transfer-reject-reason').value.trim();
        if (!reason) { alert('Vui lòng nhập lý do từ chối.'); return; }
        try {
            const res = await fetch('/api/transfer/respond', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: Number(id), action: 'reject', reason: reason })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                bootstrap.Modal.getInstance(document.getElementById('transferRejectModal'))?.hide();
                loadTransfers();
                refreshTransferHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể xử lý phiếu.'));
            }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
    }

    async function revertTransfer(id) {
        if (!await nsConfirm('Đổi lại lựa chọn và đưa phiếu này về trạng thái Chờ Xử Lý?')) return;
        try {
            const res = await fetch('/api/transfer/revert', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: id })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                loadTransfers();
                refreshTransferHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể đổi lại lựa chọn.'));
            }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
    }

    async function toggleTransferPrepared(id, prepared) {
        try {
            const res = await fetch('/api/transfer/toggle-prepared', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: id, prepared: prepared })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                loadTransfers();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể cập nhật trạng thái soạn hàng.'));
            }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
    }

    async function toggleItemReceived(itemId, received) {
        try {
            const res = await fetch('/api/transfer/mark-received', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ item_id: itemId, received: received })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                loadTransfers();
                refreshTransferHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể cập nhật.'));
            }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
    }

    // Tick 1 lần "Đã Nhận Đủ" cho CẢ PHIẾU - đánh dấu toàn bộ mã hàng trong
    // phiếu là đã nhận (hoặc bỏ hết nếu bỏ tick), thay vì phải tick từng mã.
    async function toggleAllItemsReceived(requestId, received) {
        try {
            const res = await fetch('/api/transfer/mark-all-received', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ request_id: requestId, received: received })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                loadTransfers();
                refreshTransferHighlights();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể cập nhật.'));
            }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
    }

    async function cancelTransferRequest(id) {
        if (!await nsConfirm('Bạn có chắc muốn huỷ phiếu này?')) return;
        try {
            const res = await fetch('/api/transfer/cancel', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: id })
            });
            const result = await res.json();
            if (res.ok && result.success) { loadTransfers(); }
            else { alert('Lỗi: ' + (result.error || 'Không thể huỷ phiếu.')); }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
    }

    // ------------------------------------------------------------------
    // CỬA HÀNG NHỜ ADMIN XOÁ HẲN 1 PHIẾU (kèm lý do) - khác với "Huỷ Phiếu"
    // (chỉ tự làm được khi phiếu còn 'pending'): dùng cho phiếu đã xong
    // việc mà cửa hàng không tự xử lý được nữa, phải chờ admin xem lý do
    // rồi quyết định xoá hay bỏ qua (xem buildTransferDetailFooterButtons).
    // ------------------------------------------------------------------
    function openTransferRequestDeleteModal(id) {
        bootstrap.Modal.getInstance(document.getElementById('transfer-detail-modal'))?.hide();
        document.getElementById('transfer-request-delete-id').value = id;
        document.getElementById('transfer-request-delete-reason').value = '';
        new bootstrap.Modal(document.getElementById('transferRequestDeleteModal')).show();
    }

    async function submitTransferRequestDelete() {
        const id = document.getElementById('transfer-request-delete-id').value;
        const reason = document.getElementById('transfer-request-delete-reason').value.trim();
        if (!reason) { alert('Vui lòng nhập lý do xin xoá phiếu.'); return; }
        try {
            const res = await fetch('/api/transfer/request-delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: Number(id), reason: reason })
            });
            const result = await res.json();
            if (res.ok && result.success) {
                bootstrap.Modal.getInstance(document.getElementById('transferRequestDeleteModal'))?.hide();
                loadTransfers();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể gửi yêu cầu xoá phiếu.'));
            }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
    }

    // Admin bỏ qua yêu cầu xin xoá (phiếu vẫn được giữ nguyên) - dùng khi
    // admin xem lý do xong và thấy không cần xoá.
    async function dismissDeleteRequest(id) {
        if (!await nsConfirm('Bỏ qua yêu cầu xin xoá phiếu này? Phiếu sẽ được giữ nguyên, không bị xoá.')) return;
        try {
            const res = await fetch('/api/admin/transfer/dismiss-delete-request', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: id })
            });
            const result = await res.json();
            if (res.ok && result.success) { loadTransfers(); }
            else { alert('Lỗi: ' + (result.error || 'Không thể bỏ qua yêu cầu.')); }
        } catch (e) { console.error(e); alert('Lỗi kết nối đến server.'); }
    }

    async function loadDbSize() {
        const summaryEl = document.getElementById('db-size-summary');
        const barEl = document.getElementById('db-size-bar');
        summaryEl.innerText = 'Đang tải...';
        try {
            const res = await fetch('/api/admin/db-size');
            const result = await res.json();
            if (!result.success) {
                summaryEl.innerText = 'Không thể tải dung lượng.';
                return;
            }

            const fmtMB = (bytes) => (bytes / (1024 * 1024)).toFixed(2) + ' MB';
            const limitMB = (result.limit_bytes / (1024 * 1024)).toFixed(0);
            const pct = result.percent_used;

            summaryEl.innerHTML = `Đã dùng <strong>${fmtMB(result.total_bytes)}</strong> / ${limitMB} MB (<strong>${pct}%</strong>)`;

            barEl.style.width = Math.min(pct, 100) + '%';
            barEl.innerText = pct + '%';
            barEl.className = 'progress-bar ' + (pct > 90 ? 'bg-danger' : pct > 70 ? 'bg-warning' : 'bg-success');
        } catch (e) {
            console.error(e);
            summaryEl.innerText = 'Lỗi kết nối đến server.';
        }
    }

    async function archiveTransferNow() {
        if (!await nsConfirm('Lưu trữ ngay các phiếu ĐÃ ĐỒNG Ý và ĐÃ NHẬN ĐỦ HÀNG? Các phiếu này sẽ được xuất ra Excel rồi xoá khỏi hệ thống. Phiếu ở trạng thái khác sẽ được giữ nguyên.')) return;
        const btn = document.getElementById('btn-archive-now');
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Đang lưu trữ...';
        try {
            const res = await fetch('/api/admin/transfer/archive-now', { method: 'POST' });
            const result = await res.json();
            if (!result.success) {
                alert('Lỗi: ' + (result.error || 'Không thể lưu trữ.'));
                return;
            }
            if (result.request_count === 0) {
                alert(result.message || 'Không có phiếu nào đủ điều kiện lưu trữ.');
                return;
            }
            alert(`Đã lưu trữ thành công ${result.request_count} phiếu (${result.item_count} mã hàng).`);
            loadTransferArchives();
        } catch (e) {
            console.error(e);
            alert('Lỗi kết nối server.');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    }

    // ================== THỐNG KÊ PHIẾU CHUYỂN KHO (NGÀY/TUẦN/THÁNG) ==================
    const TRANSFER_STATUS_LABEL_VI = { pending: 'Chờ Xử Lý', approved: 'Đã Đồng Ý', rejected: 'Đã Từ Chối' };
    const TRANSFER_STATUS_BADGE_VI = { pending: 'bg-warning text-dark', approved: 'bg-success', rejected: 'bg-danger' };

    function shiftTransferStatsDate(deltaDays) {
        const input = document.getElementById('transfer-stats-date');
        if (!input || !input.value) return;
        const d = new Date(input.value + 'T00:00:00');
        d.setDate(d.getDate() + deltaDays);
        input.value = d.toISOString().slice(0, 10);
        loadTransferStats();
    }

    function setTransferStatsToday() {
        // Không tự tính "hôm nay" ở trình duyệt (có thể lệch múi giờ) - gọi
        // API không kèm ?date để server trả về hôm nay theo giờ VN, rồi lấy
        // đúng ngày đó gán lại vào ô input.
        const input = document.getElementById('transfer-stats-date');
        if (input) input.value = '';
        loadTransferStats();
    }

    async function loadTransferStats() {
        const dateInput = document.getElementById('transfer-stats-date');
        const dayLabelEl = document.getElementById('transfer-stats-day-label');
        const selectedDate = dateInput ? dateInput.value : '';

        try {
            const url = selectedDate ? `/api/admin/transfer/stats?date=${selectedDate}` : '/api/admin/transfer/stats';
            const res = await fetch(url);
            const result = await res.json();
            if (!result.success) {
                if (dayLabelEl) dayLabelEl.textContent = 'Lỗi: ' + (result.error || 'Không thể tải thống kê.');
                return;
            }

            // Đồng bộ ô chọn ngày với ngày server đang trả về (lần đầu vào
            // tab / khi bấm "Hôm Nay") để tránh lệch múi giờ trình duyệt.
            if (dateInput && dateInput.value !== result.date) dateInput.value = result.date;

            renderTransferDayStats(result.day, result.date);
            renderTransferStatsCharts(result.week, result.month);
        } catch (e) {
            console.error(e);
            if (dayLabelEl) dayLabelEl.textContent = 'Lỗi kết nối server.';
        }
    }

    function renderTransferDayStats(day, dateStr) {
        document.getElementById('stats-day-total').textContent = day.total.toLocaleString();
        document.getElementById('stats-day-pending').textContent = day.pending.toLocaleString();
        document.getElementById('stats-day-approved').textContent = day.approved.toLocaleString();
        document.getElementById('stats-day-rejected').textContent = day.rejected.toLocaleString();

        const [y, m, d] = dateStr.split('-').map(Number);
        const dateObj = new Date(y, m - 1, d);
        const weekdayVi = ['Chủ Nhật', 'Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy'][dateObj.getDay()];
        document.getElementById('transfer-stats-day-label').innerHTML =
            `<i class="bi bi-calendar3 me-1"></i>${weekdayVi}, ${String(d).padStart(2,'0')}/${String(m).padStart(2,'0')}/${y}` +
            ` &nbsp;•&nbsp; <strong>${day.item_count.toLocaleString()}</strong> dòng mã hàng trong các phiếu của ngày này.`;

        const tbody = document.getElementById('transfer-stats-day-body');
        if (!day.requests.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-muted">Không có phiếu chuyển kho nào được tạo trong ngày này.</td></tr>';
            return;
        }
        tbody.innerHTML = day.requests.map(r => `
            <tr>
                <td class="text-center fw-semibold">#${r.id}</td>
                <td><span class="badge bg-light text-dark border">${r.from_store}</span></td>
                <td><span class="badge bg-light text-dark border">${r.to_store}</span></td>
                <td>${r.created_employee || r.created_by || '-'}</td>
                <td><span class="badge ${TRANSFER_STATUS_BADGE_VI[r.status] || 'bg-secondary'}">${TRANSFER_STATUS_LABEL_VI[r.status] || r.status}</span></td>
                <td class="text-muted small">${r.created_at || '-'}</td>
            </tr>
        `).join('');
    }

    function renderTransferStatsCharts(week, month) {
        document.getElementById('stats-week-total-badge').textContent = `${week.total} phiếu`;
        document.getElementById('stats-week-range').textContent =
            `${week.start_date.split('-').reverse().join('/')} - ${week.end_date.split('-').reverse().join('/')}`;
        document.getElementById('stats-month-total-badge').textContent = `${month.total} phiếu`;
        document.getElementById('stats-month-range').textContent = month.label;

        const chartFont = { family: "'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif" };

        if (transferWeekChartInstance) transferWeekChartInstance.destroy();
        const ctxWeek = document.getElementById('transferWeekChart').getContext('2d');
        const weekGradient = ctxWeek.createLinearGradient(0, 0, 0, 260);
        weekGradient.addColorStop(0, 'rgba(13, 110, 253, 0.85)');
        weekGradient.addColorStop(1, 'rgba(13, 110, 253, 0.25)');
        transferWeekChartInstance = new Chart(ctxWeek, {
            type: 'bar',
            data: {
                labels: week.labels,
                datasets: [{
                    label: 'Số phiếu tạo',
                    data: week.data,
                    backgroundColor: weekGradient,
                    borderRadius: 6,
                    maxBarThickness: 46,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: (ctx) => `${ctx.parsed.y} phiếu chuyển kho` } },
                },
                scales: {
                    y: { beginAtZero: true, ticks: { precision: 0, font: chartFont }, grid: { color: 'rgba(0,0,0,0.06)' } },
                    x: { ticks: { font: chartFont }, grid: { display: false } },
                },
            }
        });

        if (transferMonthChartInstance) transferMonthChartInstance.destroy();
        const ctxMonth = document.getElementById('transferMonthChart').getContext('2d');
        const monthGradient = ctxMonth.createLinearGradient(0, 0, 0, 260);
        monthGradient.addColorStop(0, 'rgba(25, 135, 84, 0.35)');
        monthGradient.addColorStop(1, 'rgba(25, 135, 84, 0.02)');
        transferMonthChartInstance = new Chart(ctxMonth, {
            type: 'line',
            data: {
                labels: month.labels,
                datasets: [{
                    label: 'Số phiếu tạo',
                    data: month.data,
                    borderColor: '#198754',
                    backgroundColor: monthGradient,
                    fill: true,
                    tension: 0.3,
                    pointRadius: 2,
                    pointHoverRadius: 5,
                    pointBackgroundColor: '#198754',
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: (ctx) => `Ngày ${ctx.label}: ${ctx.parsed.y} phiếu` } },
                },
                scales: {
                    y: { beginAtZero: true, ticks: { precision: 0, font: chartFont }, grid: { color: 'rgba(0,0,0,0.06)' } },
                    x: { title: { display: true, text: 'Ngày trong tháng', font: chartFont }, ticks: { font: chartFont, maxTicksLimit: 16 }, grid: { display: false } },
                },
            }
        });
    }

    // ================== BÁO CÁO LUÂN CHUYỂN THEO KHU VỰC ==================
    // Trả về chuỗi tuần ISO "YYYY-Www" của 1 ngày (dùng làm giá trị mặc định
    // cho input type="week" - trình duyệt không có API dựng sẵn cho việc
    // này nên phải tự tính theo chuẩn ISO-8601, tuần chứa thứ Năm quyết
    // định số tuần/năm).
    function _isoWeekString(d) {
        const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
        const dayNum = (date.getUTCDay() + 6) % 7; // Thứ Hai = 0
        date.setUTCDate(date.getUTCDate() - dayNum + 3); // ngày Thứ Năm của tuần đó
        const firstThursday = new Date(Date.UTC(date.getUTCFullYear(), 0, 4));
        const weekNum = 1 + Math.round(((date - firstThursday) / 86400000 - 3 + ((firstThursday.getUTCDay() + 6) % 7)) / 7);
        return `${date.getUTCFullYear()}-W${String(weekNum).padStart(2, '0')}`;
    }

    // Hiện/ẩn đúng ô nhập ngày/khoảng ngày/tuần/tháng tương ứng với kiểu
    // khoảng thời gian đang chọn, rồi tải lại báo cáo.
    function onRegionReportPeriodChange() {
        const period = document.getElementById('region-report-period').value;
        document.getElementById('region-report-day-wrap').style.display = (period === 'day') ? '' : 'none';
        document.getElementById('region-report-range-wrap').style.display = (period === 'range') ? '' : 'none';
        document.getElementById('region-report-week-wrap').style.display = (period === 'week') ? '' : 'none';
        document.getElementById('region-report-month-wrap').style.display = (period === 'month') ? '' : 'none';
        loadRegionReport();
    }

    // ============== Dropdown chọn NHIỀU trạng thái cùng lúc ==============
    // Checkbox "Tất cả trạng thái" hoạt động như 1 nút "reset": bấm vào nó
    // sẽ bỏ chọn mọi trạng thái lẻ (tương đương "không lọc gì"). Bấm vào 1
    // trạng thái lẻ sẽ tự bỏ tick "Tất cả"; nếu sau đó không còn trạng thái
    // lẻ nào được chọn, tự động tick lại "Tất cả" để không bao giờ rơi vào
    // trạng thái "0 lựa chọn" (dễ hiểu nhầm là "không có gì" thay vì "tất cả").
    function getSelectedRegionReportStatuses() {
        return Array.from(document.querySelectorAll('.rr-status-chk:checked')).map(el => el.value);
    }

    function updateRegionReportStatusButtonLabel() {
        const btn = document.getElementById('region-report-status-btn');
        if (!btn) return;
        const selected = getSelectedRegionReportStatuses();
        if (!selected.length) {
            btn.textContent = 'Tất cả trạng thái';
        } else if (selected.length === 1) {
            const chk = document.querySelector(`.rr-status-chk[value="${selected[0]}"]`);
            btn.textContent = chk ? chk.closest('label').textContent.trim() : `${selected.length} trạng thái đã chọn`;
        } else {
            btn.textContent = `${selected.length} trạng thái đã chọn`;
        }
    }

    function onRegionReportStatusAllToggle() {
        document.getElementById('rr-status-chk-all').checked = true;
        document.querySelectorAll('.rr-status-chk').forEach(el => { el.checked = false; });
        updateRegionReportStatusButtonLabel();
        loadRegionReport();
    }

    function onRegionReportStatusItemToggle() {
        const selected = getSelectedRegionReportStatuses();
        document.getElementById('rr-status-chk-all').checked = selected.length === 0;
        updateRegionReportStatusButtonLabel();
        loadRegionReport();
    }

    // Chỉ gán giá trị mặc định (hôm nay/tuần này/tháng này) MỘT LẦN DUY
    // NHẤT lúc mới vào - nếu gán lại mỗi lần loadRegionReport() chạy sẽ đè
    // mất ngày/tuần/tháng mà admin vừa tự chọn.
    let _regionReportDefaultsSet = false;
    function _setRegionReportDefaults() {
        if (_regionReportDefaultsSet) return;
        _regionReportDefaultsSet = true;
        const today = new Date();
        const iso = today.toISOString().slice(0, 10);
        const dayEl = document.getElementById('region-report-date');
        const fromEl = document.getElementById('region-report-date-from');
        const toEl = document.getElementById('region-report-date-to');
        const weekEl = document.getElementById('region-report-week');
        const monthEl = document.getElementById('region-report-month');
        if (dayEl && !dayEl.value) dayEl.value = iso;
        if (fromEl && !fromEl.value) fromEl.value = iso;
        if (toEl && !toEl.value) toEl.value = iso;
        if (weekEl && !weekEl.value) weekEl.value = _isoWeekString(today);
        if (monthEl && !monthEl.value) monthEl.value = iso.slice(0, 7);
    }

    async function loadRegionReport() {
        const labelEl = document.getElementById('region-report-range-label');
        if (!labelEl) return; // Tab chưa render (chỉ admin mới có pane này)
        _setRegionReportDefaults();

        const period = document.getElementById('region-report-period').value;
        const statuses = getSelectedRegionReportStatuses();
        const params = new URLSearchParams({ period });
        if (statuses.length) params.set('status', statuses.join(','));

        if (period === 'day') {
            params.set('date', document.getElementById('region-report-date').value);
        } else if (period === 'range') {
            params.set('date_from', document.getElementById('region-report-date-from').value);
            params.set('date_to', document.getElementById('region-report-date-to').value);
        } else if (period === 'week') {
            params.set('week', document.getElementById('region-report-week').value);
        } else if (period === 'month') {
            params.set('month', document.getElementById('region-report-month').value);
        }

        try {
            const res = await fetch(`/api/admin/transfer/region-report?${params.toString()}`);
            const result = await res.json();
            if (!result.success) {
                labelEl.textContent = 'Lỗi: ' + (result.error || 'Không thể tải báo cáo.');
                return;
            }
            renderRegionReport(result);
        } catch (e) {
            console.error(e);
            labelEl.textContent = 'Lỗi kết nối server.';
        }
    }

    function renderRegionReport(result) {
        const labelEl = document.getElementById('region-report-range-label');
        const fromVi = result.start_date.split('-').reverse().join('/');
        const toVi = result.end_date.split('-').reverse().join('/');
        labelEl.innerHTML = (result.start_date === result.end_date)
            ? `<i class="bi bi-calendar3 me-1"></i>Ngày ${fromVi}`
            : `<i class="bi bi-calendar3 me-1"></i>Từ ${fromVi} đến ${toVi}`;

        document.getElementById('region-report-flow-count').textContent = result.flows.filter(f => f.requests > 0).length.toLocaleString();
        document.getElementById('region-report-total-requests').textContent = result.total_requests.toLocaleString();
        document.getElementById('region-report-total-items').textContent = result.total_items.toLocaleString();

        renderRegionReportStatusBreakdown(result.status_counts || {}, result.status_filter);

        const tbody = document.getElementById('region-report-body');
        if (!result.flows.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-muted">Không có phiếu luân chuyển nào trong khoảng thời gian/trạng thái đã chọn.</td></tr>';
            return;
        }
        tbody.innerHTML = result.flows.map(f => `
            <tr>
                <td><span class="badge bg-light text-dark border">${escapeHtmlAttr(f.from_region)}</span></td>
                <td class="text-center text-muted"><i class="bi bi-arrow-right"></i></td>
                <td><span class="badge bg-light text-dark border">${escapeHtmlAttr(f.to_region)}</span></td>
                <td class="text-end ${f.requests ? 'fw-semibold' : 'text-muted'}">${f.requests.toLocaleString()}</td>
                <td class="text-end ${f.requests ? 'fw-semibold' : 'text-muted'}">${f.items.toLocaleString()}</td>
            </tr>
        `).join('');
    }

    // Vẽ card "Tổng Quan Trạng Thái" dạng các pill nhỏ gọn (chấm màu + nhãn
    // + số đếm) - status_counts LUÔN tính trên toàn bộ khoảng thời gian
    // (không bị ảnh hưởng bởi bộ lọc trạng thái, xem backend), nên card này
    // luôn cho thấy đủ bức tranh. Pill của trạng thái ĐANG được chọn lọc
    // (nếu có) được tô đậm, các pill còn lại làm mờ nhẹ để dễ phân biệt.
    const RR_STATUS_PILLS = [
        { key: 'pending', dot: 'rr-dot-pending', label: 'Chờ Xử Lý' },
        { key: 'approved_not_prepared', dot: 'rr-dot-not-prepared', label: 'Đồng Ý — Chưa Soạn' },
        { key: 'approved_prepared', dot: 'rr-dot-prepared', label: 'Đồng Ý — Đã Soạn' },
        { key: 'approved_not_received', dot: 'rr-dot-not-received', label: 'Đồng Ý — Chưa Nhận Đủ' },
        { key: 'approved_received', dot: 'rr-dot-received', label: 'Đồng Ý — Đã Nhận Đủ' },
        { key: 'rejected', dot: 'rr-dot-rejected', label: 'Từ Chối' },
        { key: 'cancelled', dot: 'rr-dot-cancelled', label: 'Huỷ' },
    ];
    // Card tổng quan hiển thị 2 CHIỀU riêng (soạn hàng / nhận hàng) bằng các
    // key "phẳng" cũ (approved_not_prepared, approved_received...), trong
    // khi bộ lọc thực tế (activeFilters, xem REGION_REPORT_STATUS_CONDITIONS
    // ở backend) giờ dùng 4 tổ hợp loại trừ lẫn nhau (approved_np_nr...) -
    // ánh xạ ngược ở đây chỉ để BIẾT pill nào nên tô đậm cho khớp bộ lọc
    // đang chọn, không ảnh hưởng tới việc lọc dữ liệu thực tế.
    const RR_COMBO_TO_FLAT_KEYS = {
        approved_np_nr: ['approved_not_prepared', 'approved_not_received'],
        approved_np_r: ['approved_not_prepared', 'approved_received'],
        approved_p_nr: ['approved_prepared', 'approved_not_received'],
        approved_p_r: ['approved_prepared', 'approved_received'],
    };
    function renderRegionReportStatusBreakdown(statusCounts, activeFilters) {
        const el = document.getElementById('region-report-status-breakdown');
        if (!el) return;
        const rawActive = activeFilters || [];
        const active = new Set();
        rawActive.forEach(v => {
            (RR_COMBO_TO_FLAT_KEYS[v] || [v]).forEach(k => active.add(k));
        });
        el.innerHTML = `<div class="rr-status-grid">${RR_STATUS_PILLS.map(p => {
            const count = statusCounts[p.key] || 0;
            const isActive = active.has(p.key);
            const muted = active.size && !isActive ? ' rr-muted' : '';
            return `<span class="rr-status-pill${muted}"${isActive ? ' style="border-color:#0d6efd;box-shadow:0 0 0 2px rgba(13,110,253,0.15);"' : ''}>
                <span class="rr-dot ${p.dot}"></span>${p.label} <span class="rr-count">${count.toLocaleString()}</span>
            </span>`;
        }).join('')}</div>`;
    }

    // Tải file Excel chi tiết từng mã hàng của các phiếu khớp đúng bộ lọc
    // (khoảng thời gian + các trạng thái đã chọn) đang chọn ở báo cáo khu
    // vực - dùng lại y hệt các tham số mà loadRegionReport() đang gửi lên,
    // để "xuất đúng những gì đang xem trên màn hình".
    function exportRegionReport() {
        const period = document.getElementById('region-report-period').value;
        const statuses = getSelectedRegionReportStatuses();
        const params = new URLSearchParams({ period });
        if (statuses.length) params.set('status', statuses.join(','));
        if (period === 'day') {
            params.set('date', document.getElementById('region-report-date').value);
        } else if (period === 'range') {
            params.set('date_from', document.getElementById('region-report-date-from').value);
            params.set('date_to', document.getElementById('region-report-date-to').value);
        } else if (period === 'week') {
            params.set('week', document.getElementById('region-report-week').value);
        } else if (period === 'month') {
            params.set('month', document.getElementById('region-report-month').value);
        }
        window.location.href = `/api/admin/transfer/region-report/export?${params.toString()}`;
    }

    async function loadTransferArchives() {
        const tbody = document.getElementById('transfer-archives-body');
        if (!tbody) return;
        try {
            const res = await fetch('/api/admin/transfer/archives');
            const result = await res.json();
            if (!result.success) {
                tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-muted">Không thể tải danh sách.</td></tr>';
                return;
            }
            const archives = result.archives || [];
            const fmtKB = (bytes) => (bytes / 1024).toFixed(0) + ' KB';
            tbody.innerHTML = archives.length ? archives.map(a => `
                <tr>
                    <td>${a.archived_at || ''}</td>
                    <td class="text-muted small">${a.cutoff_date || ''}</td>
                    <td class="text-center">${a.request_count}</td>
                    <td class="text-center">${a.item_count}</td>
                    <td class="text-muted small">${fmtKB(a.file_size)}</td>
                    <td class="text-end">
                        <a class="btn btn-outline-primary btn-sm" href="/api/admin/transfer/archives/${a.id}/download">
                            <i class="bi bi-download me-1"></i> Tải Về
                        </a>
                    </td>
                </tr>
            `).join('') : '<tr><td colspan="6" class="text-center py-4 text-muted">Chưa có lần lưu trữ nào (job tự động chạy hàng tháng, chỉ xử lý phiếu đã xong việc quá 1 năm).</td></tr>';
        } catch (e) {
            console.error(e);
            tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-muted">Lỗi kết nối đến server.</td></tr>';
        }
    }

    function fmtDateTimeVN(isoString) {
        if (!isoString) return '--';
        const d = new Date(isoString);
        if (isNaN(d.getTime())) return '--';
        return d.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    }

    // Rút gọn User-Agent thô thành tên trình duyệt + hệ điều hành dễ đọc,
    // không cần thư viện ngoài - chỉ đủ để admin phân biệt "máy tính hay
    // điện thoại, Chrome hay Safari", không cần chính xác tuyệt đối.
    function parseUserAgent(ua) {
        if (!ua) return 'Không rõ';
        let os = 'Không rõ hệ điều hành';
        if (/windows/i.test(ua)) os = 'Windows';
        else if (/android/i.test(ua)) os = 'Android';
        else if (/iphone|ipad|ios/i.test(ua)) os = 'iOS';
        else if (/mac os/i.test(ua)) os = 'macOS';
        else if (/linux/i.test(ua)) os = 'Linux';

        let browser = 'Không rõ trình duyệt';
        if (/edg\//i.test(ua)) browser = 'Edge';
        else if (/chrome\//i.test(ua)) browser = 'Chrome';
        else if (/safari\//i.test(ua) && !/chrome/i.test(ua)) browser = 'Safari';
        else if (/firefox\//i.test(ua)) browser = 'Firefox';

        return `${browser} · ${os}`;
    }

    async function loadAdminUsers() {
        const res = await fetch('/api/admin/users');
        const result = await res.json();
        const tbody = document.getElementById('admin-users-body');
        if(result.success) {
            tbody.innerHTML = result.users.map(u => `
                <tr>
                    <td class="fw-bold">${u.username}</td>
                    <td>${u.full_name ? escapeHtmlText(u.full_name) : '<span class="text-muted fst-italic">Chưa cập nhật</span>'}</td>
                    <td><span class="badge ${u.role === 'admin' ? 'bg-danger' : 'bg-primary'}">${u.role}</span></td>
                    <td>${u.store_code}</td>
                    <td>${u.branch ? escapeHtmlText(u.branch) : '<span class="text-muted fst-italic">Chưa cập nhật</span>'}</td>
                    <td>
                        ${u.online
                            ? `<span class="badge bg-success"><i class="bi bi-circle-fill me-1" style="font-size:6px;"></i>Đang online</span><div class="small text-muted mt-1">${u.current_ip || ''}</div>`
                            : '<span class="badge bg-secondary">Offline</span>'}
                    </td>
                    <td class="small">
                        ${u.last_login_time ? fmtDateTimeVN(u.last_login_time) : '<span class="text-muted">Chưa từng đăng nhập</span>'}
                        ${u.last_login_ip ? `<div class="text-muted">IP: ${u.last_login_ip}${u.last_login_location ? ` · ${u.last_login_location}` : ''}</div>` : ''}
                        ${renderGeoInfo(u.last_geo)}
                        ${u.is_store_ip === true ? '<span class="badge bg-success mt-1"><i class="bi bi-shield-check me-1"></i>Đúng IP cửa hàng</span>'
                            : u.is_store_ip === false ? '<span class="badge bg-warning text-dark mt-1"><i class="bi bi-exclamation-triangle me-1"></i>IP khác cửa hàng</span>'
                            : ''}
                    </td>
                    <td class="text-end">
                        <button class="btn btn-outline-secondary btn-sm me-1 mb-1" onclick="openLoginHistory('${u.username}')">
                            <i class="bi bi-clock-history me-1"></i> <span class="d-none d-sm-inline">Lịch Sử</span>
                        </button>
                        <button class="btn btn-outline-dark btn-sm me-1 mb-1" data-username="${escapeHtmlAttr(u.username)}" data-fullname="${escapeHtmlAttr(u.full_name || '')}" data-branch="${escapeHtmlAttr(u.branch || '')}" onclick="openInfoModal(this)">
                            <i class="bi bi-pencil-square me-1"></i> <span class="d-none d-sm-inline">Thông Tin</span>
                        </button>
                        <button class="btn btn-outline-primary btn-sm mb-1" onclick="openPassModal('${u.username}')" ${(u.role === 'admin' && CURRENT_USER !== 'admin') ? 'disabled title="Chỉ tài khoản admin gốc mới được đổi mật khẩu của 1 tài khoản admin khác."' : ''}>
                            <i class="bi bi-key me-1"></i> <span class="d-none d-sm-inline">Đổi Mật Khẩu</span>
                        </button>
                        ${(u.role === 'admin' && CURRENT_USER !== 'admin') ? '<div class="small text-muted mt-1" style="max-width:220px;">Chỉ admin gốc mới đổi được mật khẩu tài khoản admin.</div>' : ''}
                    </td>
                </tr>
            `).join('');
        }
    }

    // Hiển thị vị trí CHÍNH XÁC lúc đăng nhập (GPS/Wi-Fi từ thiết bị) - dùng cho cả
    // danh sách user lẫn lịch sử đăng nhập. g: {lat,lng,accuracy_m,geo_address,geo_status}
    function renderGeoInfo(g) {
        if (!g) return '';
        if (g.lat != null && g.lng != null) {
            const acc = g.accuracy_m != null ? ` (sai số ±${Math.round(g.accuracy_m)} m)` : '';
            const url = `https://www.google.com/maps?q=${Number(g.lat)},${Number(g.lng)}`;
            const addr = g.geo_address
                ? escapeHtmlText(g.geo_address)
                : `Toạ độ ${Number(g.lat).toFixed(5)}, ${Number(g.lng).toFixed(5)}`;
            return `<div class="mt-1"><i class="bi bi-geo-alt-fill text-danger me-1"></i>${addr}${acc} <a href="${url}" target="_blank" rel="noopener">Xem bản đồ</a></div>`;
        }
        const labels = {
            denied: 'Từ chối chia sẻ vị trí',
            unavailable: 'Thiết bị không hỗ trợ định vị',
            timeout: 'Không lấy được vị trí (hết thời gian)',
        };
        return labels[g.geo_status]
            ? `<div class="mt-1 text-muted"><i class="bi bi-geo-alt me-1"></i>${labels[g.geo_status]}</div>` : '';
    }

    let _currentHistoryStoreCode = null;

    async function openLoginHistory(username, options = {}) {
        const { skipShow = false } = options;
        document.getElementById('login-history-username').innerText = username;
        const tbody = document.getElementById('login-history-body');
        tbody.innerHTML = '<tr class="ns-skel-row"><td colspan="5"><div class="ns-skel"></div></td></tr><tr class="ns-skel-row"><td colspan="5"><div class="ns-skel"></div></td></tr><tr class="ns-skel-row"><td colspan="5"><div class="ns-skel"></div></td></tr><tr class="ns-skel-row"><td colspan="5"><div class="ns-skel"></div></td></tr><tr class="ns-skel-row"><td colspan="5"><div class="ns-skel"></div></td></tr>';
        document.getElementById('store-ip-list').innerHTML = 'Đang tải...';

        // CHỈ mở modal ở lần gọi ĐẦU TIÊN (khi bấm nút "Lịch Sử"). Khi hàm
        // này được gọi lại để REFRESH dữ liệu (từ markAsStoreIp/removeStoreIp
        // lúc modal đang mở sẵn), gọi lại .show() sẽ khiến Bootstrap tạo
        // thêm 1 lớp "backdrop" (nền mờ) chồng lên, và lớp cũ không được
        // dọn khi đóng modal - gây ra tình trạng bị 1 lớp che chặn thao tác
        // trên toàn trang. dùng getOrCreateInstance thay vì new Modal() để
        // không tạo thêm instance/backdrop trùng lặp.
        if (!skipShow) {
            bootstrap.Modal.getOrCreateInstance(document.getElementById('loginHistoryModal')).show();
        }

        try {
            const res = await fetch(`/api/admin/login-log?username=${encodeURIComponent(username)}`);
            const result = await res.json();
            const logs = result.success ? result.logs : [];
            _currentHistoryStoreCode = logs.length ? logs[0].store_code : null;

            tbody.innerHTML = logs.length ? logs.map(log => `
                <tr>
                    <td class="small">${fmtDateTimeVN(log.login_time)}</td>
                    <td class="small">${log.ip_address || '--'}${log.location ? `<div class="text-muted">${log.location}</div>` : ''}${renderGeoInfo(log)}</td>
                    <td class="small text-muted">${parseUserAgent(log.user_agent)}</td>
                    <td class="small">
                        ${log.is_store_ip === true ? '<span class="badge bg-success">Đúng</span>'
                            : log.is_store_ip === false ? '<span class="badge bg-warning text-dark">Khác</span>'
                            : '<span class="text-muted">Chưa rõ</span>'}
                    </td>
                    <td class="text-end">
                        ${log.ip_address && log.is_store_ip !== true ? `
                            <button class="btn btn-outline-success btn-sm" onclick="markAsStoreIp('${log.store_code}', '${log.ip_address}')">
                                <i class="bi bi-pin-angle me-1"></i>Đánh dấu
                            </button>` : ''}
                    </td>
                </tr>
            `).join('') : '<tr><td colspan="5" class="text-center py-4 text-muted">Chưa có lượt đăng nhập nào được ghi nhận.</td></tr>';

            await loadStoreIpList(_currentHistoryStoreCode);
        } catch (e) {
            console.error(e);
            tbody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-muted">Lỗi kết nối đến server.</td></tr>';
        }
    }

    async function loadStoreIpList(storeCode) {
        const box = document.getElementById('store-ip-list');
        if (!storeCode) { box.innerHTML = '<span class="text-muted">Không xác định được cửa hàng của tài khoản này.</span>'; return; }
        try {
            const res = await fetch(`/api/admin/store-ips?store_code=${encodeURIComponent(storeCode)}`);
            const result = await res.json();
            box.innerHTML = (result.success && result.ips.length) ? result.ips.map(ip => `
                <span class="badge bg-primary-subtle text-dark border me-1 mb-1">
                    ${ip.ip_address}${ip.label ? ` (${ip.label})` : ''}
                    <a href="#" class="text-danger ms-1" onclick="removeStoreIp(${ip.id}, '${storeCode}'); return false;" title="Xoá"><i class="bi bi-x-circle"></i></a>
                </span>
            `).join('') : '<span class="text-muted">Chưa đăng ký IP nào cho cửa hàng này - dùng nút "Đánh dấu" ở bảng bên dưới.</span>';
        } catch (e) {
            box.innerHTML = '<span class="text-muted">Lỗi tải danh sách IP.</span>';
        }
    }

    async function markAsStoreIp(storeCode, ipAddress) {
        try {
            const res = await fetch('/api/admin/store-ips', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ store_code: storeCode, ip_address: ipAddress })
            });
            const result = await res.json();
            if (result.success) {
                await openLoginHistory(document.getElementById('login-history-username').innerText, { skipShow: true });
                loadAdminUsers();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể đánh dấu IP'));
            }
        } catch (e) {
            alert('Lỗi kết nối đến server.');
        }
    }

    async function removeStoreIp(ipId, storeCode) {
        if (!await nsConfirm('Xoá IP này khỏi danh sách IP đã biết của cửa hàng?')) return;
        try {
            await fetch(`/api/admin/store-ips/${ipId}`, { method: 'DELETE' });
            await openLoginHistory(document.getElementById('login-history-username').innerText, { skipShow: true });
            loadAdminUsers();
        } catch (e) {
            alert('Lỗi kết nối đến server.');
        }
    }

    function openCreateUserModal() {
        document.getElementById('create-username-input').value = '';
        document.getElementById('create-password-input').value = '';
        document.getElementById('create-role-input').value = 'store';
        document.getElementById('create-store-code-input').value = '';
        document.getElementById('create-full-name-input').value = '';
        document.getElementById('create-branch-input').value = '';
        const errBox = document.getElementById('create-user-error');
        errBox.classList.add('d-none');
        errBox.innerText = '';
        toggleCreateStoreCodeField();
        new bootstrap.Modal(document.getElementById('createUserModal')).show();
    }

    function toggleCreateStoreCodeField() {
        const isStore = document.getElementById('create-role-input').value === 'store';
        document.getElementById('create-store-code-wrap').classList.toggle('d-none', !isStore);
    }

    async function submitCreateUser() {
        const username = document.getElementById('create-username-input').value.trim();
        const password = document.getElementById('create-password-input').value;
        const role = document.getElementById('create-role-input').value;
        const storeCode = document.getElementById('create-store-code-input').value.trim();
        const fullName = document.getElementById('create-full-name-input').value.trim();
        const branch = document.getElementById('create-branch-input').value.trim();
        const errBox = document.getElementById('create-user-error');

        const showError = (msg) => {
            errBox.innerText = msg;
            errBox.classList.remove('d-none');
        };
        errBox.classList.add('d-none');

        if (!username || !password) {
            showError('Vui lòng nhập tên đăng nhập và mật khẩu.');
            return;
        }
        if (password.length < 4) {
            showError('Mật khẩu phải có ít nhất 4 ký tự.');
            return;
        }
        if (role === 'store' && !storeCode) {
            showError('Vui lòng nhập mã cửa hàng cho tài khoản quyền Cửa hàng.');
            return;
        }

        try {
            const res = await fetch('/api/admin/users/create', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    username, password, role,
                    store_code: storeCode,
                    full_name: fullName,
                    branch: branch
                })
            });
            const result = await res.json();

            if (res.ok && result.success) {
                const modalEl = document.getElementById('createUserModal');
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
                loadAdminUsers();
            } else {
                showError(result.error || 'Không thể tạo tài khoản.');
            }
        } catch (e) {
            console.error(e);
            showError('Lỗi kết nối đến server.');
        }
    }

    function openInfoModal(btn) {
        const username = btn.dataset.username;
        const fullName = btn.dataset.fullname;
        const branch = btn.dataset.branch;
        document.getElementById('info-target-username').value = username;
        document.getElementById('info-modal-username').innerText = username;
        document.getElementById('info-full-name-input').value = fullName || '';
        document.getElementById('info-branch-input').value = branch || '';
        new bootstrap.Modal(document.getElementById('editUserInfoModal')).show();
    }

    async function submitUserInfo() {
        const username = document.getElementById('info-target-username').value;
        const fullName = document.getElementById('info-full-name-input').value.trim();
        const branch = document.getElementById('info-branch-input').value.trim();

        try {
            const res = await fetch('/api/admin/users', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ username, full_name: fullName, branch: branch })
            });
            const result = await res.json();

            if (res.ok && result.success) {
                const modalEl = document.getElementById('editUserInfoModal');
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
                loadAdminUsers();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể lưu thông tin'));
            }
        } catch (e) {
            console.error(e);
            alert('Lỗi kết nối đến server.');
        }
    }

    function openPassModal(username) {
        document.getElementById('target-username').value = username;
        document.getElementById('modal-username').innerText = username;
        document.getElementById('new-password-input').value = '';
        new bootstrap.Modal(document.getElementById('changePassModal')).show();
    }

    async function submitNewPassword() {
        const username = document.getElementById('target-username').value;
        const passwordInput = document.getElementById('new-password-input');
        
        if (!passwordInput) {
            alert('Không tìm thấy ô nhập mật khẩu!');
            return;
        }

        const password = passwordInput.value;
        if (!password) { 
            alert('Vui lòng nhập mật khẩu mới!'); 
            return; 
        }

        try {
            const res = await fetch('/api/admin/users', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ username, password })
            });
            const result = await res.json();
            
            if (res.ok && (result.ok || result.success)) {
                alert('Đổi mật khẩu thành công!');
                const modalEl = document.getElementById('changePassModal');
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
                passwordInput.value = '';
            } else { 
                alert('Lỗi: ' + (result.error || 'Không thể đổi mật khẩu')); 
            }
        } catch (e) {
            console.error(e);
            alert('Lỗi kết nối đến server.');
        }
    }

    function openSelfPassModal() {
        document.getElementById('self-current-password-input').value = '';
        document.getElementById('self-new-password-input').value = '';
        document.getElementById('self-new-password-confirm-input').value = '';
        const errBox = document.getElementById('self-change-pass-error');
        errBox.classList.add('d-none');
        errBox.innerText = '';
        new bootstrap.Modal(document.getElementById('selfChangePassModal')).show();
    }

    async function submitSelfChangePassword() {
        const currentPassword = document.getElementById('self-current-password-input').value;
        const newPassword = document.getElementById('self-new-password-input').value;
        const confirmPassword = document.getElementById('self-new-password-confirm-input').value;
        const errBox = document.getElementById('self-change-pass-error');

        const showError = (msg) => {
            errBox.innerText = msg;
            errBox.classList.remove('d-none');
        };
        errBox.classList.add('d-none');

        if (!currentPassword || !newPassword || !confirmPassword) {
            showError('Vui lòng nhập đầy đủ các ô bên trên.');
            return;
        }
        if (newPassword !== confirmPassword) {
            showError('Mật khẩu mới nhập lại không khớp.');
            return;
        }
        if (newPassword.length < 4) {
            showError('Mật khẩu mới phải có ít nhất 4 ký tự.');
            return;
        }

        try {
            const res = await fetch('/api/change-password', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
            });
            const result = await res.json();

            if (res.ok && result.success) {
                alert('Đổi mật khẩu thành công! Lần đăng nhập sau hãy dùng mật khẩu mới.');
                const modalEl = document.getElementById('selfChangePassModal');
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
            } else {
                showError(result.error || 'Không thể đổi mật khẩu.');
            }
        } catch (e) {
            console.error(e);
            showError('Lỗi kết nối đến server.');
        }
    }

    let pendingDeleteStore = '';

    function openDeleteDataModal() {
        const select = document.getElementById('delete-store-select');
        const store = select.value;
        if (!store) {
            alert('Vui lòng chọn chi nhánh cần xoá dữ liệu!');
            return;
        }
        pendingDeleteStore = store;
        document.getElementById('delete-modal-store-name').innerText = store;
        const confirmInput = document.getElementById('delete-confirm-input');
        confirmInput.value = '';
        document.getElementById('delete-confirm-btn').disabled = true;
        new bootstrap.Modal(document.getElementById('deleteDataModal')).show();
    }

    document.addEventListener('DOMContentLoaded', () => {
        const confirmInput = document.getElementById('delete-confirm-input');
        if (confirmInput) {
            confirmInput.addEventListener('input', () => {
                document.getElementById('delete-confirm-btn').disabled =
                    confirmInput.value.trim().toUpperCase() !== pendingDeleteStore.toUpperCase();
            });
        }
        // Luôn có sẵn 1 dòng nhập mã hàng trống khi mở form tạo phiếu luân chuyển.
        addTransferItemRow();
        addAdminTransferItemRow();
        fillEmployeeSelect(document.getElementById('transfer-created-employee'), CURRENT_STORE);
        fillAdminCreatedEmployeeSelect();
    });

    async function submitDeleteStoreData() {
        if (!pendingDeleteStore) return;
        try {
            const res = await fetch('/api/admin/delete-store-data', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ store_code: pendingDeleteStore })
            });
            const result = await res.json();

            if (res.ok && result.success) {
                alert(`Đã xoá sạch dữ liệu của chi nhánh ${pendingDeleteStore}.`);
                const modalEl = document.getElementById('deleteDataModal');
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
                document.getElementById('delete-store-select').value = '';
                loadData();
                loadHistory();
            } else {
                alert('Lỗi: ' + (result.error || 'Không thể xoá dữ liệu'));
            }
        } catch (e) {
            console.error(e);
            alert('Lỗi kết nối đến server.');
        }
    }

    function setupDragZones() {
        const fileInputs = [
    { id: 'ds_po_file', nameId: 'ds-po-file-name' },
    { id: 'po_detail_file', nameId: 'po-detail-file-name' },
    { id: 'receipt_file', nameId: 'receipt-file-name' },
    { id: 'inventory_file', nameId: 'inventory-file-name' },
    { id: 'price_file', nameId: 'price-file-name' },
    { id: 'location_file', nameId: 'location-file-name' },
    { id: 'location_file_store', nameId: 'location-file-name-store' },  // <-- thêm dòng này
    { id: 'damaged_file', nameId: 'damaged-file-name' },
    { id: 'price_adj_import_file', nameId: 'price-adj-import-file-name' }
];

        fileInputs.forEach(item => {
            const input = document.getElementById(item.id);
            const nameDisplay = document.getElementById(item.nameId);
            if(!input || !nameDisplay) return;
            const dropZone = input.closest('.drop-zone');
            if(!dropZone) return;

            input.addEventListener('change', () => {
                if(input.files.length > 0) {
                    nameDisplay.innerText = "✓ " + input.files[0].name;
                }
            });

            ['dragenter', 'dragover'].forEach(eventName => {
                dropZone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    dropZone.classList.add('border-primary', 'bg-light');
                });
            });

            ['dragleave', 'drop'].forEach(eventName => {
                dropZone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    dropZone.classList.remove('border-primary', 'bg-light');
                });
            });

            dropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                    const dt = new DataTransfer();
                    dt.items.add(e.dataTransfer.files[0]);
                    input.files = dt.files;
                    nameDisplay.innerText = "✓ " + input.files[0].name;
                }
            });
        });
    }

    function exportToExcel() {
        if(globalData.length === 0) { alert("Không có dữ liệu!"); return; }
        const ws = XLSX.utils.json_to_sheet(globalData);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, "DoiSoat");
        XLSX.writeFile(wb, `Bao_Cao_PO_NamSuong_${new Date().toISOString().slice(0,10)}.xlsx`);
    }

    // Xuất Tồn Kho Hệ Thống ra Excel - lấy đúng phần đang được lọc bởi ô tìm
    // kiếm (giống những gì đang hiển thị trên bảng), không giới hạn theo
    // INVENTORY_RENDER_LIMIT (giới hạn đó chỉ để tránh vẽ quá nhiều dòng lên
    // HTML cho mượt, không áp dụng khi xuất file).
    function exportInventoryToExcel() {
        if (globalInventory.length === 0) { alert("Không có dữ liệu tồn kho!"); return; }

        const search = (document.getElementById('inventory-search').value || '').toLowerCase();
        const searchCode = normalizeCodeSearch(search);
        const filtered = globalInventory.filter(item =>
            normalizeCodeSearch(item.part_code).includes(searchCode) ||
            (item.part_name || '').toLowerCase().includes(search)
        );
        if (filtered.length === 0) { alert("Không có dữ liệu tồn kho khớp với tìm kiếm hiện tại!"); return; }

        const rows = filtered.map(item => ({
            'Mã Hàng': item.part_code,
            'Tên Hàng': item.part_name || '',
            'ĐVT': item.unit || '',
            'Giá Bán': (item.sale_price === null || item.sale_price === undefined) ? '' : item.sale_price,
            'NS1': item.NS1 || 0,
            'NS2': item.NS2 || 0,
            'NS3': item.NS3 || 0,
            'NS4': item.NS4 || 0,
            'NS5': item.NS5 || 0,
            'NSM1': item.NSM1 || 0,
            'Kho CB': item.CB || 0,
        }));

        const ws = XLSX.utils.json_to_sheet(rows);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, "TonKho");
        XLSX.writeFile(wb, `Ton_Kho_NamSuong_${new Date().toISOString().slice(0,10)}.xlsx`);
    }

    // ------------------------------------------------------------------
    // TỰ ĐỘNG CẬP NHẬT (không cần F5 lại trang)
    // ------------------------------------------------------------------
    // Cơ chế: định kỳ (20 giây/lần) gọi /api/version - endpoint rất nhẹ,
    // chỉ trả về vài "chữ ký" (timestamp/hash) của từng mảng dữ liệu. So
    // sánh với lần gọi trước đó; mảng nào đổi mới tải lại đúng mảng đó
    // (loadData / loadHistory / loadInventory / loadAdminUsers) - không tải
    // lại toàn trang, không tải những phần chưa đổi, và không làm phiền
    // user bằng thông báo hay reload cứng.
    const isAdminUI = document.getElementById('admin-tab') !== null;
    let lastVersions = null;
    let versionPollTimer = null;

    async function pollVersion() {
    try {
        const res = await fetch('/api/version');
        if (!res.ok) return;
        const v = await res.json();
        if (!v.success) return;

        if (lastVersions === null) {
            // Lần đầu CHỈ lưu lại mốc để so sánh cho các lần sau - KHÔNG
            // vẽ lại banner "Ngày Xe Đi" ở đây nữa. Trước đây có gọi
            // renderTruckAnnouncements() ngay tại bước này, khiến banner đã
            // được server render sẵn (đúng) trong HTML ban đầu bị GHI ĐÈ
            // gần như ngay lập tức bằng dữ liệu từ chính API này - nếu vì
            // lý do gì đó (lệch thời điểm tính toán, cache...) 2 nguồn dữ
            // liệu không khớp nhau, banner đúng bị xoá mất chỉ sau vài giây
            // trang vừa tải xong, tạo cảm giác "bị che/mất" dù thực ra là
            // bị JS tự xoá. Giữ nguyên bản server render cho tới khi có
            // thay đổi THẬT SỰ ở lần poll sau.
            lastVersions = v;
            updateNotifBadge(v.unread_notifications);
            return;
        }

        if (v.data_version !== lastVersions.data_version) { loadData(); }
        if (v.history_version !== lastVersions.history_version) { loadHistory(); }
        if (v.inventory_version !== lastVersions.inventory_version) { loadInventory(); }
        if (v.transfer_version !== lastVersions.transfer_version) {
            // Lưu danh sách cũ trước khi tải mới
            const prev = JSON.parse(JSON.stringify(window._lastTransferRequests || []));
            await loadTransfers(); // loadTransfers đã là async, đợi hoàn tất
            const current = window._lastTransferRequests || [];
            checkTransferChanges(prev, current);
        }
        if (isAdminUI && v.users_version !== lastVersions.users_version) { loadAdminUsers(); }
        if (v.locations_version !== lastVersions.locations_version) {
            const locTab = document.getElementById('location-tab');
            if (locTab && locTab.classList.contains('active')) { loadLocations(); }
        }
        if (v.damaged_version !== lastVersions.damaged_version) {
            refreshDamagedHighlights();
            const dmgTab = document.getElementById('damaged-tab');
            if (dmgTab && dmgTab.classList.contains('active')) { loadDamaged(); }
        }
        if (v.unread_notifications !== lastVersions.unread_notifications) {
            updateNotifBadge(v.unread_notifications);
            // Nếu chuông đang mở sẵn (người dùng đang xem), tải lại danh sách
            // ngay để thấy thông báo mới nhất mà không cần đóng/mở lại.
            const notifDropdownEl = document.getElementById('notif-dropdown');
            if (notifDropdownEl && notifDropdownEl.classList.contains('show')) {
                loadNotificationsList();
            }
        }
        // Banner "ngày xe đi": so sánh nội dung (không chỉ 1 "chữ ký") vì
        // danh sách rất nhỏ - vẽ lại mỗi khi khác lần trước, kể cả trường
        // hợp KHÔNG có gì đổi trong DB nhưng đã sang ngày mới (server tự
        // loại banner đã quá hạn ra khỏi kết quả trả về).
        if (JSON.stringify(v.truck_announcements) !== JSON.stringify(lastVersions.truck_announcements)) {
            renderTruckAnnouncements(v.truck_announcements);
        }

        lastVersions = v;
    } catch (e) { /* im lặng */ }
}

    function startVersionPolling() {
        if (versionPollTimer) clearInterval(versionPollTimer);
        versionPollTimer = setInterval(pollVersion, 20000); // 20 giây/lần
        // Khi user quay lại tab trình duyệt sau khi rời đi 1 lúc, kiểm tra
        // ngay thay vì đợi hết chu kỳ 20s tiếp theo.
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') pollVersion();
        });
    }
    // ------------------------------------------------------------------
// THÔNG BÁO CHUYỂN KHO - Desktop Notification (kể cả khi không mở tab)
// và âm thanh "ting" - đi kèm với toast trong trang đã có sẵn.
// ------------------------------------------------------------------
function requestTransferNotificationPermission() {
    if (!('Notification' in window)) return; // trình duyệt/thiết bị không hỗ trợ - bỏ qua trong im lặng
    if (Notification.permission === 'default') {
        Notification.requestPermission();
    }
}

// AudioContext dùng chung, chỉ tạo 1 lần và "mở khoá" (resume) ngay lần đầu
// người dùng bấm/gõ phím bất kỳ đâu trên trang - nhờ vậy những lần phát
// "ting" tiếp theo (kích hoạt từ việc polling ngầm, không phải thao tác
// chuột trực tiếp) mới thực sự phát ra tiếng, tránh bị trình duyệt chặn vì
// chính sách "chỉ phát âm thanh sau khi có tương tác của người dùng".
let _transferAudioCtx = null;
function unlockTransferNotificationAudio() {
    try {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;
        if (!_transferAudioCtx) _transferAudioCtx = new AudioContextClass();
        if (_transferAudioCtx.state === 'suspended') _transferAudioCtx.resume();
    } catch (e) { /* một số trình duyệt/thiết bị không hỗ trợ Web Audio - bỏ qua */ }
}

// Phát 1 tiếng "ting" ngắn, nhẹ nhàng (2 nốt cao dần) bằng Web Audio API -
// không cần file âm thanh riêng, tự tạo ra âm nên luôn có sẵn, không phụ
// thuộc mạng/đường dẫn file.
function playTransferNotificationSound() {
    try {
        unlockTransferNotificationAudio();
        if (!_transferAudioCtx) return;
        const ctx = _transferAudioCtx;
        const now = ctx.currentTime;
        [{ freq: 880, start: 0, dur: 0.16 }, { freq: 1320, start: 0.12, dur: 0.24 }].forEach(note => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.value = note.freq;
            gain.gain.setValueAtTime(0.0001, now + note.start);
            gain.gain.exponentialRampToValueAtTime(0.22, now + note.start + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + note.start + note.dur);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(now + note.start);
            osc.stop(now + note.start + note.dur + 0.05);
        });
    } catch (e) { /* im lặng - không để lỗi âm thanh làm gián đoạn thông báo khác */ }
}

// Thông báo Desktop (Notification API của trình duyệt) - vẫn hiện được kể
// cả khi tab web đang ở nền/không active/thu nhỏ (miễn trình duyệt chưa bị
// tắt hẳn), miễn người dùng đã cấp quyền.
function showDesktopTransferNotification(title, body) {
    if (!('Notification' in window)) return;
    if (Notification.permission !== 'granted') return;
    try {
        const n = new Notification(title, {
            body: body,
            icon: NS_LOGO_URL,
            tag: 'transfer-' + Date.now(), // tag khác nhau mỗi lần để không bị gộp/đè thông báo cũ
        });
        n.onclick = () => { window.focus(); n.close(); };
        setTimeout(() => n.close(), 15000); // tự đóng sau 15s nếu người dùng không bấm vào
    } catch (e) { /* một số trình duyệt/thiết bị không hỗ trợ - bỏ qua */ }
}

// Gọi chung 3 kênh thông báo cùng lúc (toast trong trang + Desktop + âm
// thanh) cho 1 sự kiện phiếu chuyển kho.
// ------------------------------------------------------------------
// MEGA NAV (menu chính gộp nhóm) - lớp giao diện MỚI đặt "lên trên" các
// nút tab GỐC (id="...-tab", đã ẩn bằng d-none trong #mainTab). Bấm 1
// mục trong mega-nav chỉ đơn giản "bấm hộ" (.click()) đúng nút gốc tương
// ứng - Bootstrap Tab vẫn hoạt động y hệt như trước (chuyển pane, phát
// sự kiện shown.bs.tab, các đoạn code cũ check classList.contains('active')
// trên nút gốc...) vì nút gốc vẫn tồn tại thật trong DOM, chỉ là không
// hiển thị cho mắt thường. Nhờ vậy không phải sửa lại hàng trăm chỗ JS
// khác đang tham chiếu tới các id tab cũ.
// ------------------------------------------------------------------
function megaNavClick(tabId, itemEl) {
    const originalBtn = document.getElementById(tabId);
    if (originalBtn) originalBtn.click();

    // Nếu bấm từ trong 1 dropdown-menu, tự đóng dropdown đó lại ngay sau
    // khi chọn (giống hành vi menu chuyên nghiệp thông thường).
    const openDropdown = itemEl.closest ? itemEl.closest('.dropdown') : null;
    if (openDropdown) {
        const toggleBtn = openDropdown.querySelector('[data-bs-toggle="dropdown"]');
        const inst = toggleBtn && bootstrap.Dropdown.getInstance(toggleBtn);
        if (inst) inst.hide();
    }
}

// Tô đậm (mega-active) đúng mục đang được chọn trong mega-nav - cả mục
// con trong dropdown LẪN nút nhóm cha (dropdown-toggle) hoặc nút đơn lẻ -
// dựa theo nút tab GỐC nào đang có class "active" tại thời điểm gọi.
// Được gọi lại mỗi khi có 1 tab được chuyển tới (xem sự kiện shown.bs.tab
// gắn ở setupMegaNav bên dưới), nên luôn đồng bộ dù người dùng chuyển tab
// bằng cách nào (mega-nav, hoặc code JS khác tự bấm tab gốc).
function syncMegaNavActiveState() {
    const megaNav = document.getElementById('mega-nav');
    if (!megaNav) return;

    const activeOriginal = document.querySelector('#mainTab .nav-link.active');
    const activeTabId = activeOriginal ? activeOriginal.id : null;

    megaNav.querySelectorAll('.mega-active').forEach(el => el.classList.remove('mega-active'));
    if (!activeTabId) return;

    megaNav.querySelectorAll(`[data-target-tab="${activeTabId}"]`).forEach(item => {
        item.classList.add('mega-active');
        const parentDropdown = item.closest('.dropdown');
        if (parentDropdown) {
            const toggleBtn = parentDropdown.querySelector('.dropdown-toggle');
            if (toggleBtn) toggleBtn.classList.add('mega-active');
        }
    });
}

// Đo chiều cao THẬT của banner trên cùng (.brand-banner) và ghi vào biến
// CSS --ns-topbar-h - dùng để (1) cho ô logo góc vuông cao bằng đúng
// banner, (2) đẩy sidebar xuống bắt đầu ngay dưới banner, (3) đẩy nội
// dung trang xuống dưới banner. Không hardcode 1 số cố định vì nội dung
// banner có thể xuống dòng khác nhau tuỳ vai trò (admin có thêm nút "Xem
// Như Cửa Hàng") hay tuỳ bề rộng màn hình (badge/tiêu đề tự xuống dòng
// trên điện thoại nhỏ). Gọi lại khi resize (debounce) và khi web font tải
// xong (có thể đổi chiều cao chữ) để luôn khớp.
function syncTopbarHeight() {
    const topbar = document.querySelector('.brand-banner');
    if (!topbar) return;
    // +16px đệm an toàn (tăng từ +2px vì thực tế vẫn còn bị đè 1 phần) -
    // chừa dư hẳn ra một khoảng, thà dư 1 chút khoảng trắng dưới header
    // còn hơn để sót dù chỉ vài px khiến banner bên dưới bị cắt/che mất.
    const h = Math.ceil(topbar.getBoundingClientRect().height) + 16;
    if (h > 16) document.documentElement.style.setProperty('--ns-topbar-h', h + 'px');
}
// Gọi NGAY LẬP TỨC (không đợi sự kiện nào) - script này nằm ở cuối trang,
// nên tại thời điểm chạy tới đây, phần HTML của .brand-banner phía trên
// chắc chắn đã có trong DOM rồi, đo được luôn không cần đợi DOMContentLoaded
// hay setTimeout. Đây là lần đo ĐẦU TIÊN, sớm nhất có thể, để hạn chế tối
// đa khoảng thời gian --ns-topbar-h còn ở giá trị mặc định (76px) - vốn có
// thể nhỏ hơn chiều cao banner thật, khiến nội dung bên dưới bị che ngay ở
// lần vẽ đầu tiên của trang.
syncTopbarHeight();
window.addEventListener('resize', function() {
    clearTimeout(window._nsTopbarResizeTimer);
    window._nsTopbarResizeTimer = setTimeout(syncTopbarHeight, 150);
});
if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(syncTopbarHeight).catch(function() {});
}
// GỌI LẠI NHIỀU LẦN sau khi tải trang (0ms, 300ms, 800ms, 1500ms): banner
// trên cùng có thể đổi chiều cao trễ hơn cả DOMContentLoaded lẫn
// fonts.ready (ví dụ: icon font Bootstrap Icons load xong nhưng chưa kịp
// bắn sự kiện được trình duyệt hỗ trợ, hoặc banner đổi xuống dòng do ảnh
// logo/badge tải xong sau). Việc gọi lại vài lần trong khoảng thời gian
// ngắn sau khi trang sẵn sàng gần như không tốn chi phí gì nhưng loại bỏ
// hẳn khả năng --ns-topbar-h bị "đóng băng" ở giá trị đo quá sớm/quá thấp
// khiến nội dung bên dưới (banner Ngày Xe Đi, banner cảnh báo mượn quyền...)
// bị thanh header cố định đè lên che mất.
[0, 300, 800, 1500].forEach(function(delay) {
    setTimeout(syncTopbarHeight, delay);
});
// PHÒNG TRƯỜNG HỢP banner đổi chiều cao vì lý do KHÁC ngoài resize/font
// (ví dụ: admin bấm "Xem Như Cửa Hàng"/"Quay Lại Quyền Admin" khiến nút
// bên phải banner có/mất đi, admin có thêm nút mà cửa hàng không có nên
// banner có thể xuống dòng khác nhau giữa 2 chế độ) - dùng ResizeObserver
// theo dõi TRỰC TIẾP kích thước thật của .brand-banner, hễ đổi là tự
// syncTopbarHeight() ngay, không phụ thuộc phải có sự kiện resize/font
// mới chạy. Đây là nguyên nhân khiến trước đây quay lại quyền Admin xong
// banner "Ngày Xe Đi" bị header đè lên che mất: --ns-topbar-h bị lệch so
// với chiều cao banner thật lúc đó.
if (window.ResizeObserver) {
    const topbarEl = document.querySelector('.brand-banner');
    if (topbarEl) {
        new ResizeObserver(function() { syncTopbarHeight(); }).observe(topbarEl);
    }
}

// Sidebar dọc thu gọn/mở rộng: trên máy tính (có hover thật) việc mở rộng
// khi rê chuột vào đã do CSS `:hover` đảm nhiệm hoàn toàn, không cần JS.
// Trên thiết bị cảm ứng (không có hover thật) thì KHÔNG có sự kiện hover,
// nên cần JS: chạm vào bên trong sidebar để "ghim" mở rộng (thêm class
// ns-sidebar-pinned), chạm ra ngoài để tự thu gọn lại.
function setupSidebarHoverExpand() {
    const sidebar = document.getElementById('mega-nav');
    if (!sidebar) return;

    const supportsHover = window.matchMedia('(hover: hover)').matches;
    if (supportsHover) return; // Máy tính: đã có :hover thuần CSS, không cần JS.

    sidebar.addEventListener('click', function() {
        sidebar.classList.add('ns-sidebar-pinned');
    });
    document.addEventListener('click', function(e) {
        if (sidebar.classList.contains('ns-sidebar-pinned') && !sidebar.contains(e.target)) {
            sidebar.classList.remove('ns-sidebar-pinned');
        }
    });
}

function setupMegaNav() {
    const mainTabEl = document.getElementById('mainTab');
    if (mainTabEl) {
        // Sự kiện Bootstrap 'shown.bs.tab' nổi bọt (bubbles) lên tới #mainTab
        // dù được phát ra từ đúng nút <button id="...-tab"> con bên trong,
        // nên chỉ cần gắn 1 listener duy nhất ở đây cho TẤT CẢ các tab.
        mainTabEl.addEventListener('shown.bs.tab', syncMegaNavActiveState);
    }
    syncMegaNavActiveState(); // đồng bộ trạng thái ban đầu (mặc định Dashboard đang active)
}

function escapeHtmlText(s) {
    const div = document.createElement('div');
    div.textContent = String(s == null ? '' : s);
    return div.innerHTML;
}

// ------------------------------------------------------------------
// BANNER "NGÀY XE ĐI" - vẽ lại từ dữ liệu server trả về (được nhúng
// thẳng trong /api/version, không cần gọi API riêng). Trang vừa tải sẽ
// có sẵn banner render từ server (Jinja) trong #truck-announcement-wrap;
// hàm này chỉ GHI ĐÈ lại đúng nội dung đó mỗi lần poll để cập nhật real-
// time (thêm/huỷ banner, hoặc tự ẩn khi qua ngày) mà không cần F5.
// ------------------------------------------------------------------
function renderTruckAnnouncements(list) {
    const wrap = document.getElementById('truck-announcement-wrap');
    if (!wrap) return;
    list = list || [];
    if (list.length === 0) {
        wrap.innerHTML = '';
        return;
    }
    wrap.innerHTML = list.map(ann => `
        <div class="d-flex align-items-center gap-2 gap-md-3 px-3 py-2 mb-2 rounded-3 shadow-sm text-white"
             style="background: linear-gradient(135deg, #dc3545 0%, #b02a37 100%);" data-ann-id="${ann.id}">
            <i class="bi bi-truck fs-5 flex-shrink-0"></i>
            <div class="small fw-semibold" style="line-height: 1.4;">
                <span class="badge bg-white text-dark me-2 fs-5">Xe đi ngày ${escapeHtmlText(ann.departure_date_vi)}</span>${escapeHtmlText(ann.message)}
            </div>
        </div>
    `).join('');
}

// ---- Quản lý (Admin) ----
async function loadTruckAnnouncementsAdmin() {
    const listEl = document.getElementById('truck-ann-admin-list');
    if (!listEl) return;
    try {
        const res = await fetch('/api/admin/truck-announcements');
        const result = await res.json();
        if (!result.success) { listEl.innerHTML = `<tr><td colspan="4" class="text-center text-danger py-3">${escapeHtmlText(result.error || 'Lỗi tải dữ liệu')}</td></tr>`; return; }

        if (result.items.length === 0) {
            listEl.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3">Chưa có thông báo nào.</td></tr>';
            return;
        }
        listEl.innerHTML = result.items.map(it => `
            <tr>
                <td class="fw-semibold text-nowrap">${escapeHtmlText(it.departure_date_vi)}</td>
                <td>${escapeHtmlText(it.message)}</td>
                <td class="text-center">
                    ${it.is_showing
                        ? '<span class="badge bg-danger">Đang hiển thị</span>'
                        : (it.active ? '<span class="badge bg-secondary">Đã qua ngày</span>' : '<span class="badge bg-light text-muted border">Đã huỷ</span>')}
                </td>
                <td class="text-end text-nowrap">
                    ${it.is_showing ? `<button class="btn btn-outline-danger btn-sm me-1" onclick="cancelTruckAnnouncement(${it.id})" title="Ẩn ngay"><i class="bi bi-eye-slash"></i></button>` : ''}
                    <button class="btn btn-outline-primary btn-sm me-1" onclick="editTruckAnnouncement(${it.id}, '${it.departure_date}', '${escapeHtmlAttr(it.message)}')" title="Sửa"><i class="bi bi-pencil-square"></i></button>
                    <button class="btn btn-outline-secondary btn-sm" onclick="deleteTruckAnnouncement(${it.id})" title="Xoá hẳn"><i class="bi bi-trash3"></i></button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        listEl.innerHTML = '<tr><td colspan="4" class="text-center text-danger py-3">Lỗi kết nối.</td></tr>';
    }
}

function editTruckAnnouncement(id, departure_date, message) {
    document.getElementById('truck-ann-edit-id').value = id;
    document.getElementById('truck-ann-date').value = departure_date;
    document.getElementById('truck-ann-message').value = message;
    document.getElementById('truck-ann-submit-btn').innerHTML = '<i class="bi bi-save2-fill me-1"></i> Cập Nhật';
    document.getElementById('truck-ann-cancel-edit-btn').classList.remove('d-none');
    document.getElementById('truck-ann-date').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function resetTruckAnnouncementForm() {
    document.getElementById('truck-ann-edit-id').value = '';
    document.getElementById('truck-ann-date').value = '';
    document.getElementById('truck-ann-message').value = '';
    document.getElementById('truck-ann-submit-btn').innerHTML = '<i class="bi bi-megaphone-fill me-1"></i> Gửi Thông Báo';
    document.getElementById('truck-ann-cancel-edit-btn').classList.add('d-none');
}

async function submitTruckAnnouncement() {
    const editId = document.getElementById('truck-ann-edit-id').value;
    const dateEl = document.getElementById('truck-ann-date');
    const msgEl = document.getElementById('truck-ann-message');
    const departure_date = dateEl.value;
    const message = msgEl.value.trim();

    if (!departure_date) { showToast('Thiếu thông tin', 'Vui lòng chọn ngày xe đi.', 'warning'); return; }
    if (!message) { showToast('Thiếu thông tin', 'Vui lòng nhập nội dung thông báo.', 'warning'); return; }

    try {
        const res = await fetch(
            editId ? `/api/admin/truck-announcements/${editId}` : '/api/admin/truck-announcements',
            {
                method: editId ? 'PUT' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ departure_date, message }),
            }
        );
        const result = await res.json();
        if (!result.success) { showToast('Lỗi', result.error || 'Có lỗi xảy ra.', 'danger'); return; }

        resetTruckAnnouncementForm();
        showToast('Thành công', editId ? 'Đã cập nhật thông báo.' : 'Đã tạo thông báo ngày xe đi.', 'success');
        loadTruckAnnouncementsAdmin();
        pollVersion(); // cập nhật banner ngay cho chính admin, không cần đợi 20s
    } catch (e) {
        showToast('Lỗi', 'Lỗi kết nối.', 'danger');
    }
}

async function deleteTruckAnnouncement(id) {
    if (!await nsConfirm('Xoá HẲN thông báo này khỏi lịch sử? Hành động này không thể hoàn tác.')) return;
    try {
        const res = await fetch(`/api/admin/truck-announcements/${id}`, { method: 'DELETE' });
        const result = await res.json();
        if (!result.success) { showToast('Lỗi', result.error || 'Có lỗi xảy ra.', 'danger'); return; }
        if (document.getElementById('truck-ann-edit-id').value == id) resetTruckAnnouncementForm();
        loadTruckAnnouncementsAdmin();
        pollVersion();
    } catch (e) {
        showToast('Lỗi', 'Lỗi kết nối.', 'danger');
    }
}

async function cancelTruckAnnouncement(id) {
    if (!await nsConfirm('Ẩn ngay thông báo này khỏi tất cả cửa hàng? (dữ liệu vẫn được giữ lại trong lịch sử)')) return;
    try {
        const res = await fetch(`/api/admin/truck-announcements/${id}/cancel`, { method: 'POST' });
        const result = await res.json();
        if (!result.success) { showToast('Lỗi', result.error || 'Có lỗi xảy ra.', 'danger'); return; }
        loadTruckAnnouncementsAdmin();
        pollVersion();
    } catch (e) {
        showToast('Lỗi', 'Lỗi kết nối.', 'danger');
    }
}

function notifyTransferEvent(title, message, toastType) {
    showToast(title, message, toastType);
    showDesktopTransferNotification(title, message);
    playTransferNotificationSound();
}

// ------------------------------------------------------------------
// ICON CHUÔNG - danh sách thông báo bền vững (lưu ở server, đọc lại được
// bất cứ lúc nào, khác với toast chỉ thoáng qua) + đánh dấu đã đọc.
// Thông báo tự động biến mất sau 7 ngày (server tự dọn, xem app.py).
// ------------------------------------------------------------------
function updateNotifBadge(count) {
    const badge = document.getElementById('notif-badge');
    const n = Number(count) || 0;
    if (badge) {
        if (n > 0) {
            badge.textContent = n > 99 ? '99+' : String(n);
            badge.style.display = '';
        } else {
            badge.style.display = 'none';
        }
    }
    // Gương lại số này sang trang "Tổng Quan".
    const ovNotif = document.getElementById('ov-unread-notif');
    if (ovNotif) {
        ovNotif.innerText = n > 0 ? `${n.toLocaleString()} thông báo chưa đọc` : 'Không có thông báo mới';
    }
}

// Hiển thị thời gian dạng "x phút/giờ/ngày trước" cho gọn, thay vì hiện
// nguyên timestamp đầy đủ - dễ đọc lướt qua trong danh sách thông báo.
//
// Backend trả created_at kèm RÕ múi giờ VN (vd "2026-09-16T14:05:00+07:00")
// nên new Date() hiểu đúng mốc tuyệt đối dù máy người dùng để múi giờ nào.
// Vẫn giữ nhánh xử lý chuỗi "trần" (không có offset) cho các bản ghi cũ:
// lúc đó coi như giờ VN và tự bù chênh lệch với múi giờ của máy, thay vì để
// trình duyệt hiểu nhầm thành giờ máy (nguyên nhân cũ làm phiếu vừa tạo đã
// hiện "7 giờ trước").
const VN_UTC_OFFSET_MINUTES = 7 * 60;

function parseServerTime(isoString) {
    const s = String(isoString).trim().replace(' ', 'T');
    const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(s);
    const d = new Date(hasZone ? s : s + 'Z');
    if (isNaN(d.getTime())) return null;
    // Chuỗi "trần" vừa được ép đọc như UTC -> trừ đi 7 tiếng để về đúng mốc
    // thật (vì giá trị đó vốn là giờ VN).
    if (!hasZone) d.setMinutes(d.getMinutes() - VN_UTC_OFFSET_MINUTES);
    return d;
}

function formatRelativeTime(isoString) {
    if (!isoString) return '';
    const then = parseServerTime(isoString);
    if (!then) return '';
    // Math.max(0, ...): nếu đồng hồ máy người dùng chạy chậm hơn server một
    // chút thì diff có thể âm - hiện "Vừa xong" thay vì số âm khó hiểu.
    const diffSec = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
    if (diffSec < 60) return 'Vừa xong';
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)} phút trước`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} giờ trước`;
    if (diffSec < 7 * 86400) return `${Math.floor(diffSec / 86400)} ngày trước`;
    return then.toLocaleDateString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' });
}

const NOTIF_TYPE_ICON = {
    success: 'bi-check-circle-fill text-success',
    danger: 'bi-x-circle-fill text-danger',
    warning: 'bi-exclamation-triangle-fill text-warning',
    info: 'bi-info-circle-fill text-primary',
};

async function loadNotificationsList() {
    const listEl = document.getElementById('notif-list');
    if (!listEl) return;
    try {
        const res = await fetch('/api/notifications/list');
        const result = await res.json();
        if (!res.ok || !result.success) {
            listEl.innerHTML = '<div class="text-center text-muted small py-4">Không tải được thông báo.</div>';
            return;
        }
        window._lastNotifications = result.notifications;
        updateNotifBadge(result.unread_count);
        if (result.notifications.length === 0) {
            listEl.innerHTML = '<div class="text-center text-muted small py-4"><i class="bi bi-bell-slash d-block fs-4 mb-1"></i>Chưa có thông báo nào.</div>';
            return;
        }
        listEl.innerHTML = result.notifications.map(n => {
            const iconClass = NOTIF_TYPE_ICON[n.notif_type] || NOTIF_TYPE_ICON.info;
            return `
                <div class="notif-item px-3 py-2 border-bottom ${n.is_read ? '' : 'notif-unread'}" style="cursor:pointer;" onclick="handleNotificationClick(${n.id}, ${n.transfer_id ?? 'null'})">
                    <div class="d-flex justify-content-between align-items-start gap-2">
                        <div class="d-flex gap-2">
                            <i class="bi ${iconClass} mt-1"></i>
                            <div>
                                <div class="small fw-semibold">${escapeHtmlAttr(n.title)}</div>
                                <div class="small text-muted">${escapeHtmlAttr(n.message)}</div>
                                <div class="text-muted" style="font-size:0.72rem;">${formatRelativeTime(n.created_at)}</div>
                            </div>
                        </div>
                        ${n.is_read ? '' : '<span class="notif-unread-dot"></span>'}
                    </div>
                </div>`;
        }).join('');
    } catch (e) {
        listEl.innerHTML = '<div class="text-center text-muted small py-4">Lỗi kết nối đến server.</div>';
    }
}

async function markNotificationRead(id) {
    try {
        await fetch('/api/notifications/mark-read', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: id }),
        });
    } catch (e) { /* im lặng - không để lỗi mạng làm gián đoạn việc xem chi tiết phiếu */ }
}

async function markAllNotificationsRead() {
    try {
        await fetch('/api/notifications/mark-all-read', { method: 'POST' });
        loadNotificationsList();
    } catch (e) { alert('Lỗi kết nối đến server.'); }
}

// Bấm vào 1 thông báo: đánh dấu đã đọc, và nếu thông báo có gắn với 1
// phiếu chuyển kho cụ thể, mở luôn modal xem chi tiết phiếu đó (tìm trong
// dữ liệu phiếu đã tải - nếu phiếu quá cũ không còn trong danh sách hiện
// tại thì bỏ qua, không báo lỗi làm phiền).
function handleNotificationClick(id, transferId) {
    markNotificationRead(id);
    // Cập nhật ngay trên giao diện (không đợi tải lại) để phản hồi tức thì.
    const item = (window._lastNotifications || []).find(n => n.id === id);
    if (item) item.is_read = true;
    loadNotificationsList();

    if (!transferId) return;
    bootstrap.Dropdown.getInstance(document.getElementById('notif-bell-btn'))?.hide();

    // Admin THẬT (vd thông báo "xin xoá phiếu"): chỉ có đúng 1 bảng "Toàn
    // Bộ Phiếu" (tab transfer-tab), dữ liệu nằm ở window._lastTransferRequests
    // (đã tải sẵn từ lúc vào trang - xem loadTransfers() gọi ở DOMContentLoaded),
    // nên mở thẳng modal chi tiết với direction 'admin' (khớp với cách các
    // dòng trong bảng admin tự mở modal - xem handleTransferRowClick).
    if (CURRENT_ROLE === 'admin') {
        const adminTabBtn = document.getElementById('transfer-tab');
        if (adminTabBtn && !adminTabBtn.classList.contains('active')) {
            bootstrap.Tab.getOrCreateInstance(adminTabBtn).show();
        }
        openTransferDetail(transferId, 'admin');
        return;
    }

    const inReceived = (window._lastReceivedRequests || []).some(r => r.id === transferId);
    const inSent = (window._lastSentRequests || []).some(r => r.id === transferId);
    if (!inReceived && !inSent) return; // phiếu không còn trong danh sách hiện tại - bỏ qua

    // Phiếu người khác gửi đến mình (received) -> tab "Xuất Nội Bộ";
    // phiếu mình đã gửi đi (sent) -> tab "Nhận Nội Bộ" - tab cũ "transfer-tab"
    // đã được tách làm 2 nên phải chọn đúng tab tương ứng loại phiếu.
    const targetTabId = inReceived ? 'transfer-export-tab' : 'transfer-import-tab';
    const transferTabBtn = document.getElementById(targetTabId);
    if (transferTabBtn && !transferTabBtn.classList.contains('active')) {
        bootstrap.Tab.getOrCreateInstance(transferTabBtn).show();
    }
    openTransferDetail(transferId, inReceived ? 'received' : 'sent');
}

// Hàm hiển thị toast thông báo
function showToast(title, message, type = 'info') {
    const map = {danger: 'error', success: 'success', warning: 'warning', info: 'info'};
    nsToast(message, map[type] || 'info', 8000, {title});
}

// Hàm so sánh danh sách phiếu cũ và mới, hiển thị thông báo
function checkTransferChanges(prev, current) {
    const store = CURRENT_STORE_CODE;
    const role = CURRENT_ROLE;
    // Chỉ thông báo cho tài khoản cửa hàng (bỏ qua admin để tránh nhiều thông báo)
    if (role !== 'store') return;

    const prevMap = {};
    prev.forEach(r => prevMap[r.id] = r);
    const currentMap = {};
    current.forEach(r => currentMap[r.id] = r);

    // Phiếu mới: có trong current, không trong prev
    const newRequests = current.filter(r => !prevMap[r.id]);
    // Phiếu thay đổi trạng thái: có trong cả hai nhưng status khác hoặc updated_at khác
    const changedRequests = current.filter(r => {
        if (!prevMap[r.id]) return false;
        return prevMap[r.id].status !== r.status || prevMap[r.id].updated_at !== r.updated_at;
    });
    // Phiếu bị XOÁ HẲN (admin xoá): có trong prev, KHÔNG còn trong current.
    // CHỈ coi là "bị xoá" với phiếu mà lượt tải mặc định của server LUÔN
    // trả về bất kể tạo lâu chưa (status='pending', hoặc 'approved' còn mã
    // hàng chưa nhận - xem điều kiện WHERE trong transfer_list() ở app.py)
    // - nếu không giới hạn thế này, 1 phiếu đã xong việc (đồng ý đủ/từ
    // chối/huỷ) quá 90 ngày tự "rơi" khỏi danh sách gần đây theo đúng thiết
    // kế (không phải bị xoá) cũng sẽ bị hiểu NHẦM thành vừa bị admin xoá.
    const removedRequests = prev.filter(r => {
        if (currentMap[r.id]) return false;
        return r.status === 'pending' || (r.status === 'approved' && !r.all_received);
    });

    // Chỉ thông báo cho các phiếu liên quan đến cửa hàng của user (to_store hoặc from_store)
    const relevant = (r) => r.to_store === store || r.from_store === store;

    // Thông báo phiếu mới
    newRequests.filter(relevant).forEach(r => {
        let msg = '';
        if (r.to_store === store) {
            msg = `Có phiếu xin luân chuyển mới từ ${r.from_store} gửi đến bạn.`;
        } else if (r.from_store === store) {
            msg = `Bạn đã gửi phiếu xin luân chuyển mới đến ${r.to_store}.`;
        }
        if (msg) {
            notifyTransferEvent(`Phiếu #${r.id}`, msg, 'info');
        }
    });

    // Thông báo phiếu đã bị admin xoá hẳn (xem removedRequests ở trên) -
    // bảng/modal đã tự cập nhật hết theo dữ liệu mới (loadTransfers() vừa
    // tải xong trước khi hàm này chạy), toast này chỉ để BÁO thêm cho user
    // biết lý do phiếu tự biến mất, không phải tự dưng mất do lỗi.
    removedRequests.filter(relevant).forEach(r => {
        notifyTransferEvent(`Phiếu #${r.id}`, `Phiếu #${r.id} đã bị admin xoá hẳn khỏi hệ thống.`, 'danger');
    });

    // Thông báo phiếu thay đổi trạng thái (chỉ thông báo cho phiếu tôi đã gửi)
    changedRequests.filter(relevant).forEach(r => {
        const prevStatus = prevMap[r.id].status;
        const newStatus = r.status;
        if (prevStatus === newStatus) return;

        let msg = '';
        // Chỉ thông báo khi phiếu do tôi gửi đi và bên kia đã đồng ý/từ chối
        if (r.from_store === store) {
            const statusLabel = newStatus === 'approved' ? 'đồng ý' : (newStatus === 'rejected' ? 'từ chối' : newStatus);
            if (newStatus === 'approved' || newStatus === 'rejected') {
                msg = `Phiếu #${r.id} đã được ${r.to_store} ${statusLabel}.`;
            } else if (newStatus === 'cancelled') {
                msg = `Phiếu #${r.id} đã bị hủy.`;
            }
            if (msg) {
                const toastType = newStatus === 'approved' ? 'success' : (newStatus === 'rejected' ? 'danger' : 'warning');
                notifyTransferEvent(`Cập nhật phiếu`, msg, toastType);
            }
        }
    });

    // Thông báo cửa hàng cho ĐÃ SOẠN HÀNG XONG (chỉ báo cho cửa hàng đã
    // gửi phiếu xin - bên đang chờ để đi lấy/nhận hàng, không phải chính
    // cửa hàng vừa bấm đánh dấu soạn hàng).
    changedRequests.filter(relevant).forEach(r => {
        const wasPrepared = !!prevMap[r.id].prepared;
        const isPrepared = !!r.prepared;
        if (wasPrepared || !isPrepared) return; // chỉ báo lúc chuyển từ CHƯA soạn -> ĐÃ soạn
        if (r.from_store === store) {
            notifyTransferEvent(`Phiếu #${r.id}`, `${r.to_store} đã soạn xong hàng cho phiếu #${r.id}, có thể đi nhận hàng.`, 'success');
        }
    });
}

    // ================== GỬI VỊ TRÍ THIẾT BỊ SAU KHI ĐĂNG NHẬP ==================
    // Server chỉ xin vị trí cho lượt đăng nhập MỚI (GET báo pending=true); trình duyệt
    // sẽ hiện hộp thoại xin quyền. Từ chối/không hỗ trợ cũng được ghi lại để admin biết.
    async function reportLoginLocation() {
        try {
            const r = await fetch('/api/login-geo');
            if (!r.ok || !(await r.json()).pending) return;
        } catch (e) { return; }
        const send = (payload) => fetch('/api/login-geo', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).catch(() => {});
        if (!navigator.geolocation) { send({ status: 'unavailable' }); return; }
        navigator.geolocation.getCurrentPosition(
            pos => send({ status: 'ok', lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy }),
            err => send({ status: err.code === 1 ? 'denied' : (err.code === 3 ? 'timeout' : 'unavailable') }),
            { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
        );
    }
    document.addEventListener('DOMContentLoaded', reportLoginLocation);
// ===== TRANG TỔNG QUAN: đơn hàng gôm (admin: chờ duyệt theo cửa hàng; cửa hàng: đã duyệt / đã xem / chưa đặt) =====
function ovEsc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function ovWait(m) { if (m == null) return ''; if (m < 60) return m + ' phút'; if (m < 1440) return Math.floor(m / 60) + ' giờ'; return Math.floor(m / 1440) + ' ngày'; }
function ovSet(id, html) { const el = document.getElementById(id); if (el) el.innerHTML = html; }
function ovGo(kind, arg) {
    if (kind === 'review') {        // admin -> Duyệt Đơn Hàng, lọc đúng cửa hàng vừa bấm
        const sel = document.getElementById('ocGdhStore'); if (sel) sel.value = arg || '';
        if (typeof _ocGdhTab !== 'undefined') _ocGdhTab = 'active';
        megaNavClick('order-check-tab', document.body);
    } else {                        // cửa hàng -> Gôm Đơn Hàng > Đơn đã đẩy (tab active | done | archive)
        megaNavClick('gdh-tab', document.body);
        if (typeof _gdhUTabCur !== 'undefined') _gdhUTabCur = arg || 'done';
        gdhSwitchView('orders');
    }
}
let _ovBusy = false;
async function ovLoadOrders() {
    if (!document.getElementById('ov-ord-body') || _ovBusy) return;
    _ovBusy = true;
    const isAdmin = CURRENT_ROLE === 'admin';
    try {
        const res = await fetch('/api/gom-don-hang/orders?status=' + (isAdmin ? 'active' : 'approved,viewed'));
        const j = await res.json();
        if (!res.ok || j.error) throw new Error(j.error || res.status);
        const c = j.counts || {}, d = j.data || [];
        if (isAdmin) {
            const by = {};
            d.forEach(o => {
                const s = by[o.store] = by[o.store] || {store: o.store, pend: 0, rev: 0, urgent: 0, wait: 0};
                if (o.status === 'reviewing') s.rev++; else s.pend++;
                if (o.urgent_parts > 0) s.urgent++;
                if (o.status === 'pending') s.wait = Math.max(s.wait, o.waiting_min || 0);
            });
            const rows = Object.values(by).sort((a, b) => (b.pend + b.rev) - (a.pend + a.rev) || b.wait - a.wait);
            const max = Math.max(1, ...rows.map(r => r.pend + r.rev)), total = (c.pending || 0) + (c.reviewing || 0);
            const old = j.pending_info && j.pending_info.oldest_min;
            ovSet('ov-ord-main', total);
            ovSet('ov-ord-sub', total ? `${c.pending || 0} chờ duyệt · ${c.reviewing || 0} đang được duyệt` + (old != null ? ` · đơn chờ lâu nhất ${ovWait(old)}` : '') : 'Không có đơn nào cần duyệt');
            ovSet('ov-ord-body', rows.length ? '<div class="ov-rank">' + rows.map(r => `
                <button type="button" class="ov-row" onclick="ovGo('review','${ovEsc(r.store)}')" title="Mở danh sách đơn của ${ovEsc(r.store)}">
                    <span class="ov-store">${ovEsc(r.store)}</span>
                    <span class="ov-track"><i class="s-pend" style="width:${r.pend / max * 100}%"></i><i class="s-rev" style="width:${r.rev / max * 100}%"></i></span>
                    <span class="ov-cnt">${r.pend + r.rev}</span>
                    <span class="ov-meta">${r.pend} chờ · ${r.rev} đang duyệt${r.urgent ? ` · <span class="u">${r.urgent} đơn khẩn</span>` : ''}${r.wait ? ' · chờ lâu nhất ' + ovWait(r.wait) : ''}</span>
                </button>`).join('') + '</div>' : '<div class="ov-empty"><i class="bi bi-check2-circle fs-3 d-block mb-1"></i>Tất cả cửa hàng đã được duyệt xong.</div>');
        } else {
            const need = (c.approved || 0) + (c.viewed || 0);
            ovSet('ov-ord-main', need);
            ovSet('ov-ord-sub', need ? `${c.approved || 0} chưa xem · ${c.viewed || 0} đã xem nhưng chưa tải đơn đặt hàng` : 'Không có đơn nào đang chờ bạn đặt');
            const st = [['pending','Chờ duyệt','active'], ['reviewing','Đang duyệt','active'], ['approved','Đã duyệt','done'], ['viewed','Đã xem','done'], ['ordered','Đã đặt','archive']];
            const flow = '<div class="ov-flow">' + st.map(s => `<div class="ov-step${s[0] === 'approved' || s[0] === 'viewed' ? ' hot' : ''}" role="button" tabindex="0" onclick="ovGo('orders','${s[2]}')"><b>${c[s[0]] || 0}</b><span>${s[1]}</span></div>`).join('') + '</div>';
            const list = d.slice(0, 4).map(o => {
                const types = Object.keys(o.by_type || {}).join(', ');
                return `<button type="button" class="ov-ord" onclick="ovGo('orders','done')"><span class="ov-chip${o.status === 'viewed' ? ' seen' : ''}">${ovEsc(o.status_label)}</span><span class="grow"><b>Đơn #${o.id}</b>${types ? ' · ' + ovEsc(types) : ''}<br><small>Duyệt lúc ${ovEsc(o.approved_at || '')}</small></span><i class="bi bi-chevron-right"></i></button>`;
            }).join('');
            ovSet('ov-ord-body', flow + (list ? '<div class="d-flex flex-column gap-2">' + list + '</div>' : ''));
        }
        const t = new Date(); ovSet('ov-updated', 'Cập nhật ' + String(t.getHours()).padStart(2, '0') + ':' + String(t.getMinutes()).padStart(2, '0'));
    } catch (e) { ovSet('ov-ord-sub', 'Không tải được số liệu đơn hàng. Bấm làm mới để thử lại.'); }
    _ovBusy = false;
}
function ovRefresh() { ovLoadOrders(); if (typeof renderTransferOverviewStats === 'function') renderTransferOverviewStats(); }

// Vẽ thanh tỷ lệ từ chính các con số mà code cũ đã đổ vào (không phải sửa loadData/loadTransfers)
function ovNum(id) { const el = document.getElementById(id); return el ? (parseInt((el.textContent || '').replace(/\D/g, ''), 10) || 0) : 0; }
function ovPaintBars() {
    [['ov-transfer-bar', 'ov-transfer-total', [['ov-transfer-pending', 's-pend'], ['ov-transfer-approved', 's-ok'], ['ov-transfer-rejected', 's-bad']]],
     ['ov-po-bar', 'ov-stat-total', [['ov-stat-debt-2', 's-bad'], ['ov-stat-shipping', 's-pend'], ['ov-stat-received', 's-ok']]]].forEach(([bar, totId, parts]) => {
        const el = document.getElementById(bar); if (!el) return;
        const tot = Math.max(ovNum(totId), parts.reduce((a, p) => a + ovNum(p[0]), 0), 1);
        el.innerHTML = parts.map(p => `<i class="${p[1]}" style="width:${ovNum(p[0]) / tot * 100}%"></i>`).join('');
    });
}
(function () {
    function init() {
        let raf; const run = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(ovPaintBars); };
        const mo = new MutationObserver(run);
        ['ov-transfer-total','ov-transfer-pending','ov-transfer-approved','ov-transfer-rejected','ov-stat-total','ov-stat-debt-2','ov-stat-shipping','ov-stat-received']
            .forEach(id => { const el = document.getElementById(id); if (el) mo.observe(el, {childList: true, characterData: true, subtree: true}); });
        ovPaintBars();
        setInterval(() => { const p = document.getElementById('overview-pane'); if (p && p.classList.contains('active') && !document.hidden) ovLoadOrders(); }, 60000);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();