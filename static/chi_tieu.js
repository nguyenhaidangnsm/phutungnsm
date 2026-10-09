/* =====================================================================
   chi_tieu.js - tab "Chỉ Tiêu" (admin: tổng hợp mọi cửa hàng | cửa hàng: kết quả của chính mình)
   API: /api/chi-tieu/*   (chi_tieu.py)
   Realtime: hỏi /api/chi-tieu/version mỗi 4 giây khi tab đang mở, đổi số phiên bản -> tự tải lại.
   ===================================================================== */
(function () {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const fmt = (n) => (n == null || isNaN(n)) ? '-' : Math.round(n).toLocaleString('vi-VN');
    const pct = (n, d = 1) => (n == null || isNaN(n)) ? '-' : (n * 100).toLocaleString('vi-VN', {maximumFractionDigits: d}) + '%';
    const S = {role: null, own: null, stores: [], heads: {}, store: '', month: '', view: '', ver: null, data: null, ov: null, timer: null, busy: false, refInfo: {}};
    const VIEW_NAMES = {overview: 'Tổng hợp', detail: 'Kết quả chi tiết', upload: 'Tải dữ liệu', targets: 'Chỉ tiêu & tỉ lệ', ref: 'Bảng tra', kpi: 'KPI Quý/Năm'};

    async function api(url, opt) {
        const r = await fetch(url, opt);
        let j = {}; try { j = await r.json(); } catch (e) {}
        if (!r.ok || j.error) throw new Error(j.error || ('Lỗi ' + r.status));
        return j;
    }
    const qs = (extra) => new URLSearchParams(Object.assign({month: S.month}, S.role === 'admin' && S.store ? {store: S.store} : {}, extra || {})).toString();
    function msg(t, ok) { const e = $('ctMsg'); if (!e) return; e.className = 'small ' + (ok === false ? 'text-danger' : 'text-success'); e.textContent = t || ''; }
    const cls = (ratio) => ratio == null ? '' : (ratio >= 1 ? 'ct-good' : 'ct-bad');
    /* ---- thành phần giao diện dùng chung ---- */
    const tier = (p, good = 1) => p == null || isNaN(p) ? '' : p >= good ? 'ok' : p >= 0.6 * good ? 'mid' : 'low';
    const bar = (p, good = 1) => {
        if (p == null || isNaN(p)) return '<span class="text-muted">-</span>';
        const t = tier(p, good), w = Math.max(0, Math.min(100, p * 100));
        return `<div class="ct-prog"><div class="ct-prog-track"><div class="ct-prog-fill ${t}" style="width:${w}%"></div></div><b class="ct-prog-n ${t}">${pct(p)}</b></div>`;
    };
    const ratioBadge = (r) => {
        if (r == null || isNaN(r)) return '<span class="text-muted">-</span>';
        const ok = r >= 0.95 && r <= 1.05;
        return `<span class="ct-pill ${ok ? 'ok' : 'bad'}"><i class="bi bi-${ok ? 'check-circle-fill' : r > 1.05 ? 'arrow-up-circle-fill' : 'arrow-down-circle-fill'}"></i> ${pct(r)}</span>`;
    };
    const PAL = [[37, 99, 235], [13, 148, 136], [234, 88, 12], [124, 58, 237], [219, 39, 119], [8, 145, 178], [22, 163, 74], [220, 38, 38]];
    const gcol = (k) => PAL[k % PAL.length];
    /* Bỏ bản đồ nhiệt nhiều màu (khó so sánh): giữ hàm để các chỗ gọi cũ không lỗi, nhưng không tô nền */
    const heat = () => '';
    const sec = (tone, icon, title, sub, body) => `<div class="card card-custom ct-card ct-t-${tone} mb-3" data-xls="${esc(title)}"><div class="ct-sec"><span class="ct-sec-ico"><i class="bi bi-${icon}"></i></span><h6 class="ct-sec-t">${title}</h6>${sub ? `<span class="ct-sec-s">${sub}</span>` : ''}<span class="ct-sec-x"><button type="button" class="ct-xls" onclick="ctXlsCard(this)" title="Tải bảng này về Excel"><i class="bi bi-file-earmark-excel me-1"></i>Excel</button></span></div>${body}</div>`;

    /* ---- Xuất Excel từng khối (đọc thẳng từ bảng đang hiển thị, số được đổi lại thành số thật) ---- */
    window.ctXlsCard = (btn) => {
        const card = btn.closest('.ct-card');
        if (!card) return;
        if (!window.XLSX) return alert('Thư viện Excel đang tải, thử lại sau vài giây.');
        const wb = XLSX.utils.book_new(), used = {};
        const base = (card.dataset.xls || 'Bang').replace(/[\\\/?*\[\]:]/g, ' ').trim();
        card.querySelectorAll('table').forEach((tb, i) => {
            const ws = XLSX.utils.table_to_sheet(tb, {raw: true});
            Object.keys(ws).forEach(k => {
                if (k[0] === '!') return;
                const c = ws[k]; if (c.t !== 's') return;
                const s = String(c.v).replace(/\s+/g, ' ').trim();
                if (/^[–-]$/.test(s)) { delete ws[k]; return; }
                if (/^-?\d{1,3}(\.\d{3})*$/.test(s) || /^-?\d+$/.test(s)) { c.t = 'n'; c.v = Number(s.replace(/\./g, '')); c.z = '#,##0'; }
                else if (/^-?[\d.]+(,\d+)?%$/.test(s)) { c.t = 'n'; c.v = Number(s.replace('%', '').replace(/\./g, '').replace(',', '.')) / 100; c.z = '0.0%'; }
                else c.v = s;
            });
            const nCols = XLSX.utils.decode_range(ws['!ref'] || 'A1').e.c + 1;
            ws['!cols'] = Array.from({length: nCols}, (_, ci) => ({wch: ci === 0 ? 22 : 16}));
            let nm = (i ? base + ' ' + (i + 1) : base).slice(0, 28); if (used[nm]) nm = nm.slice(0, 25) + '_' + i; used[nm] = 1;
            XLSX.utils.book_append_sheet(wb, ws, nm);
        });
        const store = S.store ? '_' + S.store : '';
        XLSX.writeFile(wb, `chi_tieu_${(card.dataset.xls || 'bang').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-zA-Z0-9]+/g, '_')}${store}_${S.month}.xlsx`);
    };
    const kpi = (label, val, c, icon, tone, extra) => `<div class="col-6 col-lg-3"><div class="card card-custom ct-kpi ct-t-${tone || 'blue'} h-100"><div class="ct-kpi-ico"><i class="bi bi-${icon || 'bar-chart-line'}"></i></div><div class="ct-kpi-b"><div class="ct-kpi-l">${label}</div><div class="ct-kpi-v ${c || ''}">${val}</div>${extra || ''}</div></div></div>`;

    /* ------------------------------ khởi tạo ------------------------------ */
    async function init() {
        if (S.role) return;
        const me = await api('/api/chi-tieu/me');
        S.role = me.role; S.own = me.store; S.stores = me.stores; S.heads = me.heads || {}; S.refInfo = me.ref || {};
        S.month = me.month; S.store = me.role === 'admin' ? '' : me.store;
        $('ctMonth').value = S.month;
        const sel = $('ctStore');
        if (S.role === 'admin') {
            sel.innerHTML = '<option value="">Tất cả cửa hàng</option>' + S.stores.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
            sel.classList.remove('d-none');
        } else sel.classList.add('d-none');
        const views = S.role === 'admin' ? ['overview', 'detail', 'kpi', 'upload', 'targets', 'ref'] : ['detail', 'kpi', 'upload'];
        $('ctViews').innerHTML = views.map(v => `<button class="btn btn-sm btn-outline-primary" data-v="${v}" onclick="ctView('${v}')">${VIEW_NAMES[v]}</button>`).join('');
        S.view = views[0];
    }

    window.ctTabOpened = async () => {
        try { await init(); highlight(); await refresh(); startPolling(); } catch (e) { msg(e.message, false); }
    };
    function highlight() {
        document.querySelectorAll('#ctViews button').forEach(b => b.classList.toggle('active', b.dataset.v === S.view));
        $('ctStore').classList.toggle('d-none', S.role !== 'admin' || S.view === 'ref' || S.view === 'targets' || S.view === 'overview');
        $('ctMonthWrap').classList.toggle('d-none', S.view === 'ref' || S.view === 'kpi');
        $('ctExport').classList.toggle('d-none', !(S.view === 'overview' || S.view === 'detail'));
    }
    window.ctView = async (v) => {
        S.view = v; highlight();
        if (v === 'detail' && S.role === 'admin' && !S.store) {   // chi tiết cần 1 cửa hàng cụ thể
            S.store = S.stores[0] || ''; $('ctStore').value = S.store;
        }
        await refresh(true);
    };
    window.ctChange = async () => {
        S.month = $('ctMonth').value || S.month;
        if (S.role === 'admin') S.store = $('ctStore').value;
        if (S.role === 'admin' && !S.store && (S.view === 'detail' || S.view === 'upload')) { S.store = S.stores[0] || ''; $('ctStore').value = S.store; }
        await refresh(true);
    };
    window.ctOpenStore = async (s) => { S.store = s; $('ctStore').value = s; S.view = 'detail'; highlight(); await refresh(true); };
    window.ctExport = () => {
        const p = new URLSearchParams({month: S.month});
        if (S.view === 'detail' && S.store) p.set('store', S.store);
        window.location.href = '/api/chi-tieu/export?' + p.toString();
    };

    /* ------------------------------ realtime ------------------------------ */
    function startPolling() {
        if (S.timer) return;
        S.timer = setInterval(async () => {
            const pane = $('ct-pane');
            if (!pane || !pane.classList.contains('active') || document.hidden || S.busy || S.view === 'targets') return;
            try {
                const scopeStore = S.view === 'overview' ? '' : S.store;
                const p = new URLSearchParams({month: S.month}); if (scopeStore && S.role === 'admin') p.set('store', scopeStore);
                const j = await api((S.view === 'kpi' ? '/api/chi-tieu/kpi/version?' : '/api/chi-tieu/version?') + p.toString());
                if (S.ver !== null && j.v !== S.ver) { S.ver = j.v; await refresh(false, true); } else S.ver = j.v;
            } catch (e) { /* mạng chập chờn: bỏ qua, lần sau thử lại */ }
        }, 4000);
    }
    function live(on) { const e = $('ctLive'); if (e) e.innerHTML = `<span class="ct-dot ${on ? 'on' : ''}"></span> Trực tiếp · ${new Date().toLocaleTimeString('vi-VN')}`; }

    async function refresh(showLoading, silent) {
        if (S.busy) return; S.busy = true;
        const box = $('ctBody');
        try {
            if (showLoading) box.innerHTML = '<div class="text-center text-muted py-5"><div class="spinner-border spinner-border-sm me-2"></div>Đang tính...</div>';
            const v = S.view;
            if (v === 'overview') { S.ov = await api('/api/chi-tieu/overview?' + qs()); renderOverview(); }
            else if (v === 'detail') { if (!S.store) throw new Error('Chọn cửa hàng.'); S.data = await api('/api/chi-tieu/data?' + qs()); renderDetail(); }
            else if (v === 'upload') { if (!S.store) throw new Error('Chọn cửa hàng.'); S.data = await api('/api/chi-tieu/data?' + qs()); renderUpload(); }
            else if (v === 'targets') { const j = await api('/api/chi-tieu/targets?month=' + S.month); renderTargets(j.rows); }
            else if (v === 'ref') { await renderRef(); }
            else if (v === 'kpi') { await loadKpi(); }
            const p = new URLSearchParams({month: S.month}); if (S.view !== 'overview' && S.role === 'admin' && S.store) p.set('store', S.store);
            if (v !== 'targets') { try { S.ver = (await api((v === 'kpi' ? '/api/chi-tieu/kpi/version?' : '/api/chi-tieu/version?') + p.toString())).v; } catch (e) {} }
            live(true);
        } catch (e) { box.innerHTML = `<div class="alert alert-warning">${esc(e.message)}</div>`; }
        finally { S.busy = false; }
    }

    /* ------------------------------ tổng hợp (admin) ------------------------------ */
    function renderOverview() {
        const o = S.ov, rows = o.rows, t = o.total;
        const warn = rows.filter(r => r.flags.length).length;
        let h = `<div class="row g-3 mb-3">
            ${kpi('Tổng chỉ tiêu Honda', fmt(t.target), '', 'bullseye', 'blue')}${kpi('Thực tế xuất', fmt(t.xuat), '', 'cash-stack', 'teal')}
            ${kpi('% đạt toàn hệ thống', pct(t.pct), 'ct-c-' + tier(t.pct), 'speedometer2', {ok: 'green', mid: 'amber', low: 'red'}[tier(t.pct)] || 'blue')}
            ${kpi('Cửa hàng cần chú ý', warn + '/' + rows.length, warn ? 'ct-c-mid' : 'ct-c-ok', 'exclamation-triangle', warn ? 'amber' : 'green')}</div>
        <div class="card card-custom p-2"><div class="table-responsive"><table class="table table-sm table-hover align-middle mb-0 ct-table">
        <thead class="table-light"><tr><th rowspan="2">#</th><th rowspan="2">Cửa hàng</th><th rowspan="2" class="text-end">Chỉ tiêu Honda</th><th rowspan="2" class="text-end">KH xuất</th><th rowspan="2" class="text-end">Thực tế xuất</th><th rowspan="2" style="min-width:150px">% đạt</th><th rowspan="2" class="text-end">Đặt</th><th rowspan="2" class="text-end">Nhận</th><th rowspan="2" class="text-center">Nhận/Bán<div class="fw-normal small">95–105%</div></th><th colspan="4" class="text-center ct-gfirst ct-grp">Hao mòn nhóm 1</th><th colspan="4" class="text-center ct-gfirst ct-grp">Hao mòn nhóm 2</th><th rowspan="2">Cảnh báo</th></tr><tr><th class="text-end ct-sub ct-gfirst">Chỉ tiêu</th><th class="text-end ct-sub">Đã xuất</th><th class="ct-sub" style="min-width:130px">% đạt</th><th class="text-center ct-sub">Nhận/Bán</th><th class="text-end ct-sub ct-gfirst">Chỉ tiêu</th><th class="text-end ct-sub">Đã xuất</th><th class="ct-sub" style="min-width:130px">% đạt</th><th class="text-center ct-sub">Nhận/Bán</th></tr></thead><tbody>`;
        const sorted = rows.slice().sort((a, b) => a.store.localeCompare(b.store, 'vi', {numeric: true}));
        sorted.forEach((r, i) => {
            h += `<tr class="ct-click" onclick="ctOpenStore('${esc(r.store)}')"><td class="text-muted">${i + 1}</td><td><strong>${esc(r.store)}</strong></td>
                <td class="text-end">${fmt(r.target)}</td><td class="text-end text-muted">${fmt(r.plan)}</td><td class="text-end fw-semibold">${fmt(r.xuat)}</td><td>${r.has_target ? bar(r.pct) : '<span class="text-muted small">Chưa có chỉ tiêu</span>'}</td>
                <td class="text-end">${fmt(r.dat)}</td><td class="text-end">${fmt(r.nhan)}</td><td class="text-center">${ratioBadge(r.ratio)}</td>
                <td class="text-end ct-gfirst">${fmt(r.hm1_target)}</td><td class="text-end fw-semibold">${fmt(r.hm1_xuat)}</td><td>${bar(r.hm1_done)}</td><td class="text-center">${ratioBadge(r.hm1_ratio)}</td>
                <td class="text-end ct-gfirst">${fmt(r.hm2_target)}</td><td class="text-end fw-semibold">${fmt(r.hm2_xuat)}</td><td>${bar(r.hm2_done)}</td><td class="text-center">${ratioBadge(r.hm2_ratio)}</td>
                <td class="small">${r.flags.map(f => `<span class="badge bg-warning text-dark me-1">${esc(f)}</span>`).join('') || '<span class="text-success">✓</span>'}</td></tr>`;
        });
        h += '</tbody></table></div></div><div class="small text-muted mt-2">Bấm vào một cửa hàng để xem chi tiết. Số liệu tự cập nhật khi cửa hàng tải dữ liệu mới.</div>';
        $('ctBody').innerHTML = h;
    }

    /* ------------------------------ chi tiết 1 cửa hàng ------------------------------ */
    function renderDetail() {
        const fz = (n) => (!n || isNaN(n)) ? '<span class="ct-z">–</span>' : fmt(n);
        const d = S.data, t = d.target, a = d.actual, p = d.plan, g = d.gap, G = d.groups;
        let h = '';
        if (!d.has_target) h += `<div class="alert alert-warning py-2">Admin chưa nhập chỉ tiêu tháng ${esc(d.month)} cho ${esc(d.store)}. ${S.role === 'admin' ? 'Vào mục "Chỉ tiêu & tỉ lệ" để nhập.' : 'Vui lòng báo admin.'}</div>`;
        if (d.warn.ref_empty) h += '<div class="alert alert-danger py-2">Chưa có bảng tra (Part Category). Admin cần nạp ở mục "Bảng tra" thì phụ tùng mới được phân loại.</div>';
        if (d.warn.other_count) h += `<div class="alert alert-warning py-2 small"><b>${d.warn.other_count} mã chưa có trong Part Category</b> (${fmt(d.warn.other_amount)} đ) nên chưa được tính vào nhóm nào: ${esc(d.warn.other_codes.slice(0, 10).join(', '))}${d.warn.other_count > 10 ? '...' : ''}</div>`;
        const missing = ['otc', 'jc', 'nhan', 'dat'].filter(k => !d.uploads[k]).map(k => ({otc: 'Xuất bán lẻ', jc: 'Xuất dịch vụ', nhan: 'Nhận hàng', dat: 'Đặt hàng'}[k]));
        if (missing.length) h += `<div class="alert alert-info py-2 small">Chưa tải: ${missing.join(', ')}. ${S.role === 'store' ? 'Vào "Tải dữ liệu" để tải.' : ''}</div>`;

        /* ---- 4 thẻ chỉ số ---- */
        const tp = tier(d.pct_done), tpTone = tp === 'ok' ? 'green' : tp === 'mid' ? 'amber' : tp === 'low' ? 'red' : 'blue';
        h += `<div class="row g-3 mb-3">
            ${kpi('Chỉ tiêu Honda (tổng)', fmt(t.total), '', 'bullseye', 'blue', `<div class="ct-kpi-sub">PT ${fmt(t.pt)} · Nhớt ${fmt(t.oil)} · PG ${fmt(t.pg)}</div>`)}
            ${kpi('Thực tế xuất', fmt(a.xuat), '', 'cash-stack', 'teal', `<div class="ct-kpi-sub">Bán lẻ ${fmt(a.xuat_otc)} · Dịch vụ ${fmt(a.xuat_jc)}</div>`)}
            ${kpi('% đạt so chỉ tiêu', pct(d.pct_done), 'ct-c-' + tp, 'speedometer2', tpTone, `<div class="ct-mini"><div class="ct-prog-fill ${tp}" style="width:${Math.max(0, Math.min(100, (d.pct_done || 0) * 100))}%"></div></div>`)}
            ${kpi('Tỉ lệ nhận/bán', ratioBadge(d.ratio_nhan_ban), '', 'arrow-left-right', 'indigo', '<div class="ct-kpi-sub">Chuẩn 95–105%</div>')}</div>`;

        /* ---- Doanh thu hao mòn ---- */
        h += sec('orange', 'tools', 'Doanh thu hao mòn', '', `<div class="table-responsive"><table class="table align-middle mb-0 ct-table ct-narrow">
            <thead><tr><th>Nhóm</th><th class="text-end">Nhận</th><th class="text-end">Đặt</th><th class="text-end ct-hl">Xuất</th><th class="text-center">Nhận/Bán</th><th class="text-end">Mục tiêu</th><th style="min-width:230px">% đạt mục tiêu</th></tr></thead><tbody>
            ${hmRow('Nhóm 1', 'Má phanh, Lọc gió, Dây đai, Nhông xích', d.hm1)}${hmRow('Nhóm 2', 'Lốp, Bình điện, Nước làm mát, Bugi', d.hm2)}</tbody></table></div>`);

        /* ---- Kế hoạch HEAD & thực tế ---- */
        h += sec('blue', 'bullseye', 'Kế hoạch HEAD & thực tế', '', `<div class="table-responsive"><table class="table align-middle mb-0 ct-table ct-narrow">
            <thead><tr><th>Hạng mục</th><th class="text-end">Tỉ lệ KH</th><th class="text-end">Kế hoạch</th><th class="text-end ct-hl">Thực tế</th><th class="text-end">Còn thiếu</th><th style="min-width:230px">% đạt KH</th></tr></thead><tbody>
            ${planRow('1. Xuất bán', t.pct_xuat, p.xuat, a.xuat, g.xuat)}${planRow('2. Đặt hàng', t.pct_dat, p.dat, a.dat, g.dat)}${planRow('3. Nhận hàng', t.pct_nhan, p.nhan, a.nhan, g.nhan)}
            </tbody></table></div><div class="ct-note">Lượt xe đến: <b>${fmt(t.visits)}</b> · Xuất = bán lẻ ${fmt(a.xuat_otc)} + dịch vụ ${fmt(a.xuat_jc)} · Tỉ lệ KH do admin đặt.</div>`);

        /* ---- Chi tiết theo nhóm (bản đồ nhiệt theo từng hàng) ---- */
        const ct = d.chi_tiet, cn = {BP: 'BP nhựa', GR: 'GR sửa chữa', PM: 'PM', OIL: 'OIL dầu nhớt', PK: 'PK phụ kiện', PG: 'PG phụ gia', HM: 'HM hao mòn'};
        const CTC = {ban: [37, 99, 235], dat: [217, 119, 6], nhan: [22, 163, 74]}, CTN = {ban: 'Bán hàng', dat: 'Đặt hàng', nhan: 'Nhận hàng'};
        h += sec('teal', 'grid-3x3-gap', 'Chi tiết theo nhóm', '', `<div class="table-responsive"><table class="table mb-0 ct-table ct-narrow"><thead><tr><th></th>${ct.cols.map(c => `<th class="text-end">${cn[c]}</th>`).join('')}</tr></thead><tbody>
            ${['ban', 'dat', 'nhan'].map(k => { const mx = Math.max(0, ...ct[k].map(v => v || 0)); return `<tr><td><span class="ct-chip ${k}">${CTN[k]}</span></td>${ct[k].map(v => `<td class="text-end" ${heat(v, mx, CTC[k])}>${fz(v)}</td>`).join('')}</tr>`; }).join('')}
            </tbody></table></div><div class="ct-note"><b>Bán lẻ:</b> PT ${fmt(d.ban_le_sc.ban_le.pt)} · Nhớt ${fmt(d.ban_le_sc.ban_le.oil)} · Hao mòn ${fmt(d.ban_le_sc.ban_le.hm)} &nbsp;|&nbsp; <b>Sửa chữa:</b> PT ${fmt(d.ban_le_sc.sua_chua.pt)} · Nhớt ${fmt(d.ban_le_sc.sua_chua.oil)} · Hao mòn ${fmt(d.ban_le_sc.sua_chua.hm)}</div>`);

        /* ---- Dầu máy sửa chữa dịch vụ ---- */
        const n = d.nhot, nr = (k) => {
            const x = n[k]; return `<tr><td><span class="ct-chip ${k === 'phuy' ? 'dat' : 'ban'}">${k === 'phuy' ? 'Phuy' : 'Chai'}</span></td><td class="text-end">${fmt(x.ga[0])}</td><td class="text-end">${fmt(x.ga[1])}</td><td class="text-end">${fmt(x.so[0])}</td><td class="text-end">${fmt(x.so[1])}</td><td class="text-end ct-hl">${fmt(x.ga[1] + x.so[1])}</td></tr>`;
        };
        h += sec('amber', 'droplet-half', 'Dầu máy sửa chữa dịch vụ', '', `<div class="table-responsive"><table class="table mb-0 ct-table ct-narrow"><thead><tr><th></th><th class="text-end">Số lượng nhớt xe ga</th><th class="text-end">Tiền nhớt xe ga</th><th class="text-end">Số lượng nhớt xe số</th><th class="text-end">Tiền nhớt xe số</th><th class="text-end ct-hl">Tổng tiền</th></tr></thead><tbody>${nr('phuy')}${nr('chai')}</tbody></table></div>`);

        /* ---- VAP (nhóm PG): số chai xuất / mục tiêu lượt xe ---- */
        if (d.vap) {
            const v = d.vap, vr = (label, cls, x) => `<tr><td><span class="ct-chip ${cls}">${label}</span></td><td class="text-end">${fmt(x.chai)}</td><td class="text-end">${fmt(x.tien)}</td></tr>`;
            h += sec('indigo', 'capsule', 'VAP (nhóm PG) · chai xuất / lượt xe', 'số chai xuất ÷ mục tiêu lượt xe đến', `<div class="table-responsive"><table class="table mb-0 ct-table ct-narrow"><thead><tr><th></th><th class="text-end">Số chai xuất</th><th class="text-end">Tiền VAP</th></tr></thead><tbody>
                ${vr('Bán lẻ', 'ban', v.ban_le)}${vr('Sửa chữa', 'dat', v.sua_chua)}
                <tr><td><b>Tổng</b></td><td class="text-end ct-hl">${fmt(v.chai)}</td><td class="text-end ct-hl">${fmt(v.tien)}</td></tr>
                <tr><td>Mục tiêu lượt xe</td><td class="text-end">${fmt(v.visits)}</td><td></td></tr>
                <tr><td><b>% chai / lượt xe</b></td><td class="text-end ct-hl"><b>${v.visits ? pct(v.pct, 2) : '-'}</b></td><td class="text-muted small">${v.visits ? '' : 'Chưa nhập lượt xe'}</td></tr></tbody></table></div>
                <div class="ct-note">Thùng được quy ra chai (vd "BOX95" = 95 chai). Xuất = bán lẻ + sửa chữa. % = tổng chai ÷ mục tiêu lượt xe.</div>`);
        }

        /* ---- Xuất / nhận theo tuần ---- */
        const lastIdx = (arr) => { let k = -1; arr.forEach((w, i) => { if (w.tong) k = i; }); return k; };
        const lx = lastIdx(d.tuan_xuat), ln = lastIdx(d.tuan_nhan);
        const gmax = G.map(x => Math.max(0, ...d.tuan_xuat.map(w => w.nhom[x] || 0)));
        const wk = (w, i, cur) => `<td>Tuần ${w.tuan}${i === cur ? ' <span class="ct-chip warn">mới nhất</span>' : ''}</td>`;
        const xuatRows = d.tuan_xuat.map((w, i) => `<tr class="${w.tong ? '' : 'ct-dim'} ${i === lx ? 'ct-cur' : ''}">${wk(w, i, lx)}<td class="text-end">${fz(w.ban_le)}</td><td class="text-end">${fz(w.dich_vu)}</td><td class="text-end">${fz(w.hao_mon)}</td><td class="text-end ct-hl">${fz(w.tong)}</td>${G.map((x, k) => `<td class="text-end" ${heat(w.nhom[x], gmax[k], gcol(k))}>${fz(w.nhom[x])}</td>`).join('')}</tr>`).join('');
        const nhanRows = d.tuan_nhan.map((w, i) => `<tr class="${w.tong ? '' : 'ct-dim'} ${i === ln ? 'ct-cur' : ''}">${wk(w, i, ln)}<td class="text-end">${fz(w.pt)}</td><td class="text-end">${fz(w.oil)}</td><td class="text-end">${fz(w.pg)}</td><td class="text-end">${fz(w.hao_mon)}</td><td class="text-end ct-hl">${fz(w.tong)}</td></tr>`).join('');
        h += sec('indigo', 'calendar-week', 'Xuất hàng theo tuần', 'tuần Thứ Hai–Chủ Nhật', `<div class="table-responsive"><table class="table mb-0 ct-table ct-sticky"><thead><tr><th>Tuần</th><th class="text-end">Bán lẻ</th><th class="text-end">Dịch vụ</th><th class="text-end">Hao mòn</th><th class="text-end ct-hl">Tổng xuất</th>${G.map((x, k) => `<th class="text-end${k === 0 ? ' ct-gfirst' : ''}">${esc(x)}</th>`).join('')}</tr></thead><tbody>${xuatRows}</tbody></table></div>
            <div class="ct-subh"><i class="bi bi-box-seam"></i> Nhận hàng theo tuần</div><div class="table-responsive"><table class="table mb-0 ct-table ct-narrow"><thead><tr><th>Tuần</th><th class="text-end">Phụ tùng</th><th class="text-end">Dầu nhớt</th><th class="text-end">Phụ gia</th><th class="text-end">Hao mòn</th><th class="text-end ct-hl">Tổng nhận</th></tr></thead><tbody>${nhanRows}</tbody></table></div>`);

        /* ---- Bán theo ngày (bản đồ nhiệt + hàng hôm nay / ngày cao nhất) ---- */
        const days = d.ngay.filter(x => x.ban_le || x.dich_vu), THU = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];
        const tot = {bl: 0, dv: 0, g: G.map(() => [0, 0])}, mx = {bl: 0, dv: 0, g: G.map(() => [0, 0])};
        days.forEach(x => {
            tot.bl += x.ban_le || 0; tot.dv += x.dich_vu || 0; mx.bl = Math.max(mx.bl, x.ban_le || 0); mx.dv = Math.max(mx.dv, x.dich_vu || 0);
            G.forEach((gn, k) => { const [b, v] = x.nhom[gn]; tot.g[k][0] += b || 0; tot.g[k][1] += v || 0; mx.g[k][0] = Math.max(mx.g[k][0], b || 0); mx.g[k][1] = Math.max(mx.g[k][1], v || 0); });
        });
        const dayTot = (x) => (x.ban_le || 0) + (x.dich_vu || 0);
        const best = days.length ? days.reduce((b, x) => dayTot(x) > dayTot(b) ? x : b, days[0]) : null;
        const now = new Date(), todayD = S.month === now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') ? now.getDate() : null;
        const dayRow = (x) => {
            const we = x.thu >= 5, isT = todayD != null && Number(x.ngay) === todayD;
            return `<tr class="${we ? 'ct-we' : ''} ${isT ? 'ct-today' : ''}"><td class="ct-daycell"><b>${x.ngay}</b> <span class="small ${we ? 'text-danger fw-semibold' : 'text-muted'}">${THU[x.thu]}</span>${best && x === best ? ' <i class="bi bi-trophy-fill ct-trophy" title="Ngày bán cao nhất"></i>' : ''}${isT ? ' <span class="ct-chip warn">hôm nay</span>' : ''}</td>
                <td class="text-end" ${heat(x.ban_le, mx.bl, [37, 99, 235])}>${fz(x.ban_le)}</td><td class="text-end" ${heat(x.dich_vu, mx.dv, [22, 163, 74])}>${fz(x.dich_vu)}</td>
                ${G.map((gn, k) => `<td class="text-end ct-gfirst" ${heat(x.nhom[gn][0], mx.g[k][0], gcol(k))}>${x.nhom[gn][0] ? fmt(x.nhom[gn][0]) : ''}</td><td class="text-end" ${heat(x.nhom[gn][1], mx.g[k][1], gcol(k))}>${x.nhom[gn][1] ? fmt(x.nhom[gn][1]) : ''}</td>`).join('')}</tr>`;
        };
        const totRow = `<tr class="ct-totrow"><td class="ct-daycell">Cộng</td><td class="text-end">${fmt(tot.bl)}</td><td class="text-end">${fmt(tot.dv)}</td>${G.map((gn, k) => `<td class="text-end ct-gfirst">${fz(tot.g[k][0])}</td><td class="text-end">${fz(tot.g[k][1])}</td>`).join('')}</tr>`;
        const nd = days.length, strip = nd ? `<div class="ct-strip"><span class="ct-chip"><i class="bi bi-calendar-check"></i> ${nd} ngày có bán</span><span class="ct-chip"><i class="bi bi-trophy-fill ct-trophy"></i> Cao nhất: ngày ${best.ngay} (${THU[best.thu]}) · ${fmt(dayTot(best))}</span><span class="ct-chip"><i class="bi bi-graph-up"></i> Trung bình/ngày: ${fmt((tot.bl + tot.dv) / nd)}</span></div>` : '';
        h += sec('green', 'calendar3', 'Bán theo ngày', 'BL = bán lẻ · DV = dịch vụ · kéo ngang để xem hết nhóm', `${strip}<div class="table-responsive ct-daywrap"><table class="table mb-0 ct-table ct-sticky ct-day"><thead>
            <tr><th rowspan="2" class="ct-daycell">Ngày</th><th colspan="2" class="text-center ct-grp0">Tổng</th>${G.map((x, k) => `<th colspan="2" class="text-center ct-gfirst ct-grp">${esc(x)}</th>`).join('')}</tr>
            <tr><th class="text-end ct-sub">Bán lẻ</th><th class="text-end ct-sub">Dịch vụ</th>${G.map(() => `<th class="text-end ct-gfirst ct-sub">BL</th><th class="text-end ct-sub">DV</th>`).join('')}</tr></thead>
            <tbody>${days.length ? days.map(dayRow).join('') + totRow : `<tr><td colspan="${3 + G.length * 2}" class="text-center text-muted py-4">Chưa có dữ liệu bán.</td></tr>`}</tbody></table></div>`);
        h += `<div class="small text-muted">Cập nhật lúc ${esc(d.computed_at)} · Tất cả tính theo giá nhập (DNP).</div>`;
        $('ctBody').innerHTML = h;
    }
    const planRow = (name, rate, plan, act, gap) => `<tr><td class="fw-semibold">${name}</td><td class="text-end"><span class="ct-chip muted">${pct(rate, 3)}</span></td><td class="text-end">${fmt(plan)}</td><td class="text-end ct-hl">${fmt(act)}</td><td class="text-end">${!plan ? '<span class="ct-chip muted">Chưa có KH</span>' : gap > 0 ? `<span class="ct-chip bad">Thiếu ${fmt(gap)}</span>` : '<span class="ct-chip ok"><i class="bi bi-check-lg"></i> Đã đạt</span>'}</td><td>${bar(plan ? act / plan : null)}</td></tr>`;
    const hmRow = (name, desc, x) => `<tr><td><span class="fw-semibold">${name}</span><div class="small text-muted">${desc}</div></td><td class="text-end">${fmt(x.nhan)}</td><td class="text-end">${fz0(x.dat)}</td><td class="text-end ct-hl">${fmt(x.xuat)}</td><td class="text-center">${ratioBadge(x.ratio)}</td><td class="text-end">${fmt(x.target)}</td><td>${bar(x.done)}</td></tr>`;
    const fz0 = (n) => (!n || isNaN(n)) ? '<span class="ct-z">–</span>' : fmt(n);

    /* ------------------------------ tải dữ liệu ------------------------------ */
    function renderUpload() {
        const d = S.data, ups = d.uploads;
        const defs = [['otc', 'Xuất bán lẻ', 'Xuất từ HMS: chi tiết đơn bán lẻ (OTC)'], ['jc', 'Xuất dịch vụ', 'Xuất từ HMS: chi tiết đơn dịch vụ (JobCard)'],
                      ['nhan', 'Nhận hàng', 'Xuất từ HMS: MRN / nhận hàng'], ['dat', 'Đặt hàng', 'Xuất từ HMS: chi tiết đặt hàng (PO)']];
        let h = `<div class="card card-custom p-3"><div class="d-flex flex-wrap justify-content-between mb-2"><h6 class="fw-bold mb-0">Tải dữ liệu HMS - ${esc(d.store)} - tháng ${esc(d.month.slice(5))}/${esc(d.month.slice(0, 4))}</h6>
        <span class="small text-muted">Mỗi lần tải sẽ <b>thay thế</b> dữ liệu cùng loại của tháng này (như dán đè trong Excel). Tải từ đầu tháng đến hôm nay.</span></div><div class="row g-2">`;
        defs.forEach(([k, name, hint]) => {
            const u = ups[k];
            h += `<div class="col-12 col-md-6"><div class="border rounded p-2 h-100"><div class="fw-semibold">${name} ${u ? '<span class="badge bg-success">đã có</span>' : '<span class="badge bg-secondary">chưa có</span>'}</div>
                <div class="small text-muted mb-1">${hint}</div><input type="file" class="form-control form-control-sm" id="ctF_${k}" accept=".xlsx,.xls,.csv">
                ${u ? `<div class="small mt-1 text-muted">${esc(u.file)} · ${fmt(u.rows)} dòng${u.skipped ? ` (bỏ ${fmt(u.skipped)} dòng ngoài tháng)` : ''} · ${esc(u.by)} lúc ${esc(u.at)} <a href="#" class="text-danger ms-1" onclick="ctClear('${k}');return false;">xoá</a></div>` : ''}</div></div>`;
        });
        h += `</div><div class="mt-3 d-flex gap-2 align-items-center"><button class="btn btn-primary btn-sm" id="ctUpBtn" onclick="ctUpload()"><i class="bi bi-cloud-upload me-1"></i>Tải lên</button><span id="ctMsg" class="small"></span></div>
        <div class="small text-muted mt-2">Nhận cả file xuất HMS lẫn file Excel theo dõi chỉ tiêu cũ (tự chọn đúng sheet). Giá nhập tự nhân 1.000 theo quy tắc của Excel.</div></div>`;
        $('ctBody').innerHTML = h;
    }
    window.ctUpload = async () => {
        const fd = new FormData(); fd.append('store', S.store); let n = 0;
        ['otc', 'jc', 'nhan', 'dat'].forEach(k => { const f = $('ctF_' + k) && $('ctF_' + k).files[0]; if (f) { fd.append(k, f); n++; } });
        if (!n) return msg('Chọn ít nhất một file.', false);
        const btn = $('ctUpBtn'); btn.disabled = true; msg('Đang xử lý...', true);
        try {
            const j = await api('/api/chi-tieu/upload?month=' + S.month, {method: 'POST', body: fd});
            S.busy = false; await refresh(false);
            msg([...j.done, ...j.errors.map(e => '⚠ ' + e)].join(' | '), !j.errors.length);
        } catch (e) { msg(e.message, false); } finally { const b = $('ctUpBtn'); if (b) b.disabled = false; }
    };
    window.ctClear = async (k) => {
        const ok = window.nsConfirm ? await nsConfirm('Xoá dữ liệu loại này của tháng đang xem?') : confirm('Xoá dữ liệu loại này của tháng đang xem?');
        if (!ok) return;
        try { await api('/api/chi-tieu/clear?month=' + S.month, {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: `store=${encodeURIComponent(S.store)}&kind=${k}`}); await refresh(false); } catch (e) { msg(e.message, false); }
    };

    /* ------------------------------ chỉ tiêu & tỉ lệ (admin) ------------------------------ */
    /* Ô nhập tiền/số lượng: tự thêm dấu chấm ngăn cách hàng nghìn (190.408.869) khi gõ/dán, giữ nguyên vị trí con trỏ */
    const fmtIn = (v) => { const d = String(v).replace(/\D/g, ''); return d ? d.replace(/\B(?=(\d{3})+(?!\d))/g, '.') : ''; };
    window.ctFmtIn = (el) => {
        const pos = el.selectionStart, digitsBefore = el.value.slice(0, pos).replace(/\D/g, '').length;
        el.value = fmtIn(el.value);
        let n = 0, i = 0;
        for (; i < el.value.length && n < digitsBefore; i++) if (/\d/.test(el.value[i])) n++;
        el.setSelectionRange(i, i);
    };
    const TF = [['visits', 'Lượt xe đến'], ['pt', 'Phụ tùng'], ['oil', 'Dầu nhớt'], ['pg', 'Phụ gia'], ['hm1', 'HM nhóm 1'], ['hm2', 'HM nhóm 2'], ['pct_xuat', '% xuất/CT'], ['pct_dat', '% đặt/xuất'], ['pct_nhan', '% nhận/xuất']];
    function renderTargets(rows) {
        let h = `<div class="card card-custom p-3"><div class="d-flex flex-wrap gap-2 align-items-end mb-2"><h6 class="fw-bold mb-0 me-auto">Chỉ tiêu & tỉ lệ kế hoạch - tháng ${esc(S.month.slice(5))}/${esc(S.month.slice(0, 4))}</h6>
        <div><label class="form-label small mb-0">Nạp chỉ tiêu gốc từ Excel của Honda (tất cả tháng trong file)</label><div class="input-group input-group-sm"><select id="ctImpStore" class="form-select"><option value="__all__">Cả file - mỗi sheet 1 cửa hàng (5 cửa hàng)</option>${S.stores.map(s => `<option value="${esc(s)}">Chỉ ${esc(s)}</option>`).join('')}</select><input type="file" id="ctImpFile" class="form-control" accept=".xlsx,.xls"><button class="btn btn-outline-primary" onclick="ctImportTargets()">Nạp</button></div></div></div>
        <div class="table-responsive"><table class="table table-sm align-middle ct-table"><thead class="table-light"><tr><th>Cửa hàng</th>${TF.map(f => `<th>${f[1]}</th>`).join('')}</tr></thead><tbody>`;
        rows.forEach(r => {
            h += `<tr data-s="${esc(r.store)}"><td><strong>${esc(r.store)}</strong>${r.has ? '' : ' <span class="badge bg-warning text-dark">mới</span>'}</td>${TF.map(([k]) => {
                const pct = k.startsWith('pct_'), v = pct ? (r[k] * 100).toLocaleString('en-US', {maximumFractionDigits: 3}) : fmtIn(Math.round(r[k] || 0));
                return `<td><input class="form-control form-control-sm ct-in${pct ? '' : ' ct-money'}" data-k="${k}" value="${v}" inputmode="${pct ? 'decimal' : 'numeric'}" ${pct ? '' : 'oninput="ctFmtIn(this)"'} style="min-width:${pct ? 84 : 150}px"></td>`;
            }).join('')}</tr>`;
        });
        h += `</tbody></table></div><div class="d-flex gap-2 align-items-center"><button class="btn btn-primary btn-sm" onclick="ctSaveTargets()"><i class="bi bi-save me-1"></i>Lưu tất cả</button><span id="ctMsg" class="small"></span></div>
        <div class="small text-muted mt-2">Các cột % nhập dạng phần trăm (vd 102.077). Chỉ tiêu: tổng = Phụ tùng + Dầu nhớt + Phụ gia. Cửa hàng thấy số mới ngay sau khi lưu.</div></div>`;
        $('ctBody').innerHTML = h;
    }
    window.ctSaveTargets = async () => {
        const rows = [...document.querySelectorAll('#ctBody tr[data-s]')].map(tr => {
            const o = {store: tr.dataset.s}; tr.querySelectorAll('.ct-in').forEach(i => { o[i.dataset.k] = i.classList.contains('ct-money') ? i.value.replace(/\D/g, '') : i.value; }); return o;
        });
        try { const j = await api('/api/chi-tieu/targets?month=' + S.month, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({rows})}); msg(`Đã lưu ${j.saved} cửa hàng.`, true); }
        catch (e) { msg(e.message, false); }
    };
    window.ctImportTargets = async () => {
        const f = $('ctImpFile').files[0]; if (!f) return msg('Chọn file Excel.', false);
        const fd = new FormData(); fd.append('file', f); fd.append('store', $('ctImpStore').value);
        try {
            const j = await api('/api/chi-tieu/targets/import', {method: 'POST', body: fd});
            await refresh(false);
            if (j.results) {
                const sk = j.skipped && j.skipped.length ? ` · Bỏ qua: ${j.skipped.map(x => x.sheet + ' (' + x.reason + ')').join('; ')}` : '';
                msg(`Đã nạp ${j.results.length} cửa hàng: ${j.results.map(r => `${r.sheet} → ${r.store} (HEAD ${r.head_code || '?'}, ${r.months} tháng)`).join(' | ')}${sk}`, !sk);
            } else msg(`Đã nạp ${j.months} tháng${j.head_code ? ' (mã HEAD ' + j.head_code + ')' : ''}.`, true);
        }
        catch (e) { msg(e.message, false); }
    };

    /* ------------------------------ bảng tra (admin) ------------------------------ */
    async function renderRef() {
        const [me, un] = await Promise.all([api('/api/chi-tieu/me'), api('/api/chi-tieu/ref/unclassified?month=' + S.month)]);
        const r = me.ref;
        $('ctBody').innerHTML = `<div class="card card-custom p-3 mb-3"><h6 class="fw-bold">Bảng tra dùng chung cho mọi cửa hàng</h6>
            <div class="small mb-2">Hiện có: Part Category <b>${fmt(r.part)}</b> mã · Hao Mòn <b>${fmt(r.hm)}</b> mã · Oil <b>${fmt(r.oil)}</b> mã</div>
            <div class="input-group input-group-sm" style="max-width:560px"><input type="file" id="ctRefFile" class="form-control" accept=".xlsx,.xls"><button class="btn btn-primary" onclick="ctImportRef()">Nạp bảng tra</button></div>
            <div class="small text-muted mt-1">Chọn file Excel chỉ tiêu - tự lấy 3 sheet Part Category, Hao Mòn, Oil và thay bản cũ. <span id="ctMsg"></span></div></div>
            <div class="card card-custom p-3"><h6 class="fw-bold">Mã chưa phân loại (tháng ${esc(S.month)})</h6>
            ${un.rows.length ? `<div class="table-responsive" style="max-height:50vh"><table class="table table-sm mb-0 ct-table"><thead class="table-light"><tr><th>Mã phụ tùng</th><th class="text-end">Giá trị</th><th class="text-end">Số cửa hàng</th></tr></thead><tbody>${un.rows.map(x => `<tr><td>${esc(x.code)}</td><td class="text-end">${fmt(x.amount)}</td><td class="text-end">${x.stores}</td></tr>`).join('')}</tbody></table></div>
            <div class="small text-muted mt-1">Thêm các mã này vào sheet Part Category rồi nạp lại - số liệu tự cập nhật.</div>` : '<div class="text-success small">Không có mã nào chưa phân loại.</div>'}</div>`;
    }
    window.ctImportRef = async () => {
        const f = $('ctRefFile').files[0]; if (!f) return msg('Chọn file Excel.', false);
        const fd = new FormData(); fd.append('file', f); msg('Đang nạp (file lớn có thể mất vài giây)...', true);
        try { const j = await api('/api/chi-tieu/ref/import', {method: 'POST', body: fd}); await renderRef(); msg('Đã nạp: ' + j.done.join(', '), true); }
        catch (e) { msg(e.message, false); }
    };

    /* ------------------------------ KPI Honda theo quý / năm ------------------------------ */
    /* Admin nạp file KPI Honda gửi hàng tháng (xoá cũ - ghi mới). Tháng đã có trong file lấy số Honda,
       tháng sau đó lấy từ dữ liệu HMS ở mục "Tải dữ liệu" và cộng vào quý/năm. API: /api/chi-tieu/kpi/* */
    const KQ = [['1', 'Quý 1'], ['2', 'Quý 2'], ['3', 'Quý 3'], ['4', 'Quý 4'], ['all', 'Cả năm']];
    const KSRC = {honda: 'Honda', mixed: 'Honda + HMS', hms: 'HMS đang dùng', none: 'Chưa có số'};
    const kEv = (ev, prov) => !ev ? '<span class="text-muted">-</span>' : `<span class="ct-pill ${ev === 'OK' ? 'ok' : 'bad'}" title="Nhận/Bán trong khoảng 95–105% là OK, ngoài khoảng là NG">${ev}</span>`;
    const kRatio = (r, ev, prov) => r == null ? '<span class="ct-z">–</span>' : `${pct(r)} ${kEv(ev, prov)}`;
    const kSrc = (s) => `<span class="ct-chip ${s === 'none' ? 'muted' : ''}">${KSRC[s] || s}</span>`;
    const kz = (p, html) => p.src === 'none' ? '<span class="ct-z">–</span>' : html;
    const kPct = (r) => r == null ? '<span class="ct-z">–</span>' : `<b class="ct-c-${tier(r)}">${pct(r)}</b>`;

    async function loadKpi() {
        if (S.role === 'admin' && !S.store) {
            S.kpiOv = await api('/api/chi-tieu/kpi/overview'); S.kpi = null;
            if (!S.kq) S.kq = S.kpiOv.cur_q || 'all';
        } else {
            S.kpi = await api('/api/chi-tieu/kpi/data' + (S.role === 'admin' ? '?store=' + encodeURIComponent(S.store) : '')); S.kpiOv = null;
            if (!S.kq) S.kq = (S.kpi.has && S.kpi.cur_q) || 'all';
        }
        renderKpi();
    }
    function renderKpi() {
        const overview = S.role === 'admin' && !S.store;
        let info = '';
        const src = overview ? S.kpiOv : S.kpi;
        if (src && (overview ? src.upto_label : src.has)) info = `Honda cập nhật đến <b>${esc(src.upto_label)}</b> · tháng đang theo dõi tiếp theo: <b>${esc(src.next_label)}</b> (lấy từ dữ liệu HMS ở mục "Tải dữ liệu").`;
        $('ctBody').innerHTML = kpiUploadPanel(info) + (overview ? kpiOverviewHtml() : kpiDetailHtml());
    }
    function kpiUploadPanel(info) {
        if (S.role !== 'admin') return '';
        return `<div class="card card-custom p-3 mb-3"><div class="d-flex flex-wrap justify-content-between gap-2 mb-2"><h6 class="fw-bold mb-0"><i class="bi bi-cloud-arrow-up me-2"></i>Nạp file KPI sau bán hàng của Honda</h6>
            <span class="small text-muted">Chọn cùng lúc các file Honda gửi (mỗi file 1 cửa hàng, tự nhận theo mã HEAD). Mỗi lần nạp sẽ <b>xoá dữ liệu KPI cũ</b> rồi ghi số mới.</span></div>
            <div class="d-flex flex-wrap gap-2 align-items-center"><input type="file" class="form-control form-control-sm" style="max-width:420px" id="ctKpiFiles" accept=".xlsx" multiple>
            <div class="form-check small mb-0"><input class="form-check-input" type="checkbox" id="ctKpiReplace" checked><label class="form-check-label" for="ctKpiReplace">Xoá luôn KPI cũ của cửa hàng không có trong lần nạp này</label></div>
            <button class="btn btn-primary btn-sm" id="ctKpiBtn" onclick="ctKpiUpload()"><i class="bi bi-cloud-upload me-1"></i>Nạp KPI</button><span id="ctMsg" class="small"></span></div>
            ${info ? `<div class="small text-muted mt-2">${info}</div>` : ''}</div>`;
    }
    window.ctKpiUpload = async () => {
        const fl = $('ctKpiFiles') && $('ctKpiFiles').files;
        if (!fl || !fl.length) return msg('Chọn ít nhất một file.', false);
        const fd = new FormData(); [...fl].forEach(f => fd.append('files', f)); fd.append('replace_all', $('ctKpiReplace').checked ? '1' : '0');
        const btn = $('ctKpiBtn'); btn.disabled = true; msg('Đang xử lý...', true);
        try {
            const j = await api('/api/chi-tieu/kpi/upload', {method: 'POST', body: fd});
            S.busy = false; await refresh(false);
            msg('Đã nạp: ' + j.done.join(' | ') + (j.errors.length ? ' · ⚠ ' + j.errors.join(' | ') : ''), !j.errors.length);
        } catch (e) { msg(e.message, false); } finally { const b = $('ctKpiBtn'); if (b) b.disabled = false; }
    };
    window.ctKpiPeriod = (k) => { S.kq = k; renderKpi(); };
    window.ctKpiOpen = async (s) => { S.store = s; $('ctStore').value = s; await refresh(true); };
    window.ctKpiBack = async () => { S.store = ''; $('ctStore').value = ''; await refresh(true); };
    const kPeriodBar = (ranges, back) => `<div class="ct-kq mb-3"><div class="btn-group flex-wrap">${KQ.map(([k, n]) => `<button type="button" class="btn btn-sm btn-outline-primary ${S.kq === k ? 'active' : ''}" onclick="ctKpiPeriod('${k}')">${n}${ranges && ranges[k] ? `<span class="small ms-1 opacity-75">${esc(ranges[k])}</span>` : ''}</button>`).join('')}</div>${back ? '<button type="button" class="btn btn-sm btn-outline-secondary" onclick="ctKpiBack()"><i class="bi bi-arrow-left me-1"></i>Tất cả cửa hàng</button>' : ''}</div>`;

    /* ---- tổng hợp mọi cửa hàng (admin) ---- */
    function kpiOverviewHtml() {
        const o = S.kpiOv, k = S.kq, have = o.rows.filter(r => r.has);
        if (!have.length) return '<div class="alert alert-info">Chưa nạp file KPI Honda nào. Chọn các file ở khung phía trên rồi bấm "Nạp KPI".</div>';
        const T = {total: 0, target: 0, recv: 0, b1: 0, t1: 0, n1: 0, b2: 0, t2: 0, n2: 0};
        const body = o.rows.map(r => {
            if (!r.has) return `<tr><td><strong>${esc(r.store)}</strong></td><td colspan="13" class="text-muted small">Chưa có file KPI Honda</td></tr>`;
            const p = r.periods[k], a = p.g1, b = p.g2;
            T.total += p.total; T.target += p.target; T.recv += p.recv; T.b1 += a.ban; T.t1 += a.target; T.n1 += a.nhan; T.b2 += b.ban; T.t2 += b.target; T.n2 += b.nhan;
            return `<tr class="ct-click" onclick="ctKpiOpen('${esc(r.store)}')"><td><strong>${esc(r.store)}</strong></td>
                <td class="text-end ct-hl">${kz(p, fmt(p.total))}</td><td class="text-end">${fmt(p.target)}</td><td>${kz(p, bar(p.rate))}</td><td class="text-end">${kz(p, fmt(p.recv))}</td><td class="text-center">${kz(p, kRatio(p.ratio, p.eval, p.prov))}</td>
                <td class="text-end ct-gfirst">${kz(p, fmt(a.ban))}</td><td class="text-end">${fmt(a.target)}</td><td>${kz(p, bar(a.rate))}</td><td class="text-center">${kz(p, kRatio(a.ratio, a.eval, a.prov))}</td>
                <td class="text-end ct-gfirst">${kz(p, fmt(b.ban))}</td><td class="text-end">${fmt(b.target)}</td><td>${kz(p, bar(b.rate))}</td><td class="text-center">${kz(p, kRatio(b.ratio, b.eval, b.prov))}</td></tr>`;
        }).join('');
        const rt = (n, d) => d ? n / d : null;
        const tot = `<tr class="fw-bold"><td>Tổng</td><td class="text-end ct-hl">${fmt(T.total)}</td><td class="text-end">${fmt(T.target)}</td><td>${bar(rt(T.total, T.target))}</td><td class="text-end">${fmt(T.recv)}</td><td class="text-center">${pct(rt(T.recv, T.total))}</td>
            <td class="text-end ct-gfirst">${fmt(T.b1)}</td><td class="text-end">${fmt(T.t1)}</td><td>${bar(rt(T.b1, T.t1))}</td><td class="text-center">${pct(rt(T.n1, T.b1))}</td>
            <td class="text-end ct-gfirst">${fmt(T.b2)}</td><td class="text-end">${fmt(T.t2)}</td><td>${bar(rt(T.b2, T.t2))}</td><td class="text-center">${pct(rt(T.n2, T.b2))}</td></tr>`;
        return kPeriodBar(o.ranges) + `<div class="card card-custom p-2"><div class="table-responsive"><table class="table table-sm table-hover align-middle mb-0 ct-table">
            <thead><tr><th rowspan="2">Cửa hàng</th><th rowspan="2" class="text-end ct-hl">Doanh thu thực tế</th><th rowspan="2" class="text-end">Mục tiêu</th><th rowspan="2" style="min-width:170px">% đạt</th><th rowspan="2" class="text-end">Tổng nhận</th><th rowspan="2" class="text-center">Nhận/Bán</th>
            <th colspan="4" class="text-center ct-gfirst ct-grp">Hao mòn nhóm 1</th><th colspan="4" class="text-center ct-gfirst ct-grp">Hao mòn nhóm 2</th></tr>
            <tr><th class="text-end ct-sub ct-gfirst">DT thực tế</th><th class="text-end ct-sub">Mục tiêu</th><th class="ct-sub" style="min-width:150px">% đạt</th><th class="text-center ct-sub">Nhận/Bán</th><th class="text-end ct-sub ct-gfirst">DT thực tế</th><th class="text-end ct-sub">Mục tiêu</th><th class="ct-sub" style="min-width:150px">% đạt</th><th class="text-center ct-sub">Nhận/Bán</th></tr></thead>
            <tbody>${body}${tot}</tbody></table></div></div>
            <div class="small text-muted mt-2">Bấm một cửa hàng để xem chi tiết từng quý. Đánh giá: Nhận/Bán trong khoảng 95–105% là OK, ngoài khoảng là NG. Dòng "Tổng" cộng các cửa hàng đã nạp KPI.</div>`;
    }

    /* ---- chi tiết 1 cửa hàng (cửa hàng xem của mình / admin xem từng cửa hàng) ---- */
    function kTable(d, head, rows) {
        const K = ['1', '2', '3', '4', 'all'], sel = S.kq, cl = (k) => 'text-end' + (k === sel ? ' ct-hl' : '');
        return `<div class="table-responsive"><table class="table align-middle mb-0 ct-table ct-narrow"><thead><tr><th>${head}</th>${K.map(k => `<th class="${cl(k)}">${esc(d.periods[k].label)}<div class="fw-normal small">${esc(d.periods[k].range)}</div></th>`).join('')}</tr></thead><tbody>
            <tr><td class="text-muted small">Nguồn số liệu</td>${K.map(k => `<td class="${cl(k)}">${kSrc(d.periods[k].src)}</td>`).join('')}</tr>
            ${rows.map(([label, fn]) => `<tr><td class="fw-semibold">${label}</td>${K.map(k => `<td class="${cl(k)}">${fn(d.periods[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    }
    function kpiDetailHtml() {
        const d = S.kpi;
        if (!d || !d.has) return `<div class="alert alert-info">Chưa có file KPI Honda cho ${esc(S.store || 'cửa hàng này')}. ${S.role === 'admin' ? 'Nạp file ở khung phía trên.' : 'Vui lòng báo admin nạp file.'}</div>`;
        const sel = d.periods[S.kq] ? S.kq : 'all', c = d.periods[sel], rg = {};
        Object.keys(d.periods).forEach(k => { rg[k] = d.periods[k].range; });
        const tp = tier(c.rev.rate), tone = tp === 'ok' ? 'green' : tp === 'mid' ? 'amber' : tp === 'low' ? 'red' : 'blue';
        let h = kPeriodBar(rg, S.role === 'admin');
        h += `<div class="ct-strip"><span class="ct-chip"><i class="bi bi-file-earmark-excel"></i> Honda cập nhật đến ${esc(d.upto_label)}</span>
            <span class="ct-chip"><i class="bi bi-plus-circle"></i> Cộng thêm HMS: ${d.live_labels.length ? esc(d.live_labels.join(', ')) : 'chưa có (tải dữ liệu HMS tháng ' + esc(d.next_label) + ')'}</span>
            <span class="ct-chip muted">${esc(d.file)}${d.at ? ' · ' + esc(d.at) : ''}</span></div>`;
        h += `<div class="row g-3 mb-3">
            ${kpi('Doanh thu thực tế · ' + esc(c.label), kz(c, fmt(c.rev.total)), '', 'cash-stack', 'teal', `<div class="ct-kpi-sub">Mục tiêu ${fmt(c.rev.target)}</div>`)}
            ${kpi('% đạt mục tiêu', kz(c, pct(c.rev.rate)), 'ct-c-' + tp, 'speedometer2', tone, `<div class="ct-mini"><div class="ct-prog-fill ${tp}" style="width:${Math.max(0, Math.min(100, (c.rev.rate || 0) * 100))}%"></div></div>`)}
            ${kpi('Nhận/Bán', kz(c, pct(c.recv.ratio)), '', 'arrow-left-right', 'indigo', `<div class="ct-kpi-sub">${kEv(c.recv.eval, c.recv.prov)} Nhận ${fmt(c.recv.total)}</div>`)}
            ${kpi('Hao mòn nhóm 1 · nhóm 2', kz(c, pct(c.g1.rate, 0) + ' · ' + pct(c.g2.rate, 0)), '', 'tools', 'orange', `<div class="ct-kpi-sub">Nhận/Bán ${kz(c, pct(c.g1.ratio, 0) + ' · ' + pct(c.g2.ratio, 0))}</div>`)}</div>`;
        h += sec('blue', 'bullseye', 'Doanh thu & nhận/bán theo quý', 'Honda + HMS tháng đang dùng', kTable(d, 'Hạng mục', [
            ['Doanh thu phụ tùng', p => kz(p, fmt(p.rev.pt))], ['Doanh thu dầu', p => kz(p, fmt(p.rev.oil))], ['Doanh thu VAP', p => kz(p, fmt(p.rev.vap))],
            ['Tổng doanh thu', p => kz(p, '<b>' + fmt(p.rev.total) + '</b>')], ['Mục tiêu', p => fmt(p.rev.target)], ['% đạt mục tiêu', p => kz(p, kPct(p.rev.rate))],
            ['Nhận phụ tùng', p => kz(p, fmt(p.recv.pt))], ['Nhận dầu', p => kz(p, fmt(p.recv.oil))], ['Tổng nhận', p => kz(p, fmt(p.recv.total))],
            ['Nhận/Bán', p => kz(p, pct(p.recv.ratio))], ['Đánh giá', p => kz(p, kEv(p.recv.eval, p.recv.prov))]])
            + '<div class="ct-note">Đánh giá: Nhận/Bán trong khoảng 95–105% là OK, ngoài khoảng là NG. Quý/năm đang chạy đã cộng dữ liệu HMS của tháng đang dùng.</div>');
        [['g1', 'orange', 'Hao mòn nhóm 1'], ['g2', 'teal', 'Hao mòn nhóm 2']].forEach(([g, tn, title]) => {
            const its = d.periods.all[g].items;
            const rows = its.map((it, i) => [esc(it.name), p => kz(p, `<div>${fmt(p[g].items[i].ban)}</div><div class="small text-muted">nhận ${fmt(p[g].items[i].nhan)}</div>`)]);
            rows.push(['DT thực tế', p => kz(p, '<b>' + fmt(p[g].ban) + '</b>')], ['Mục tiêu', p => fmt(p[g].target)], ['% đạt mục tiêu', p => kz(p, kPct(p[g].rate))],
                ['Tổng nhận', p => kz(p, fmt(p[g].nhan))], ['Nhận/Bán', p => kz(p, pct(p[g].ratio))], ['Đánh giá', p => kz(p, kEv(p[g].eval, p[g].prov))]);
            h += sec(tn, 'tools', title, esc((d.groups[g.slice(1)] || {}).names || ''), kTable(d, 'Hạng mục (bán / nhận)', rows));
        });
        const mr = d.monthly.map(m => `<tr class="${m.src === 'hms' ? 'ct-cur' : ''} ${m.src === 'none' ? 'ct-dim' : ''}"><td>${esc(m.label)}${m.in_year ? '' : ' <span class="ct-chip muted">ngoài năm</span>'}</td><td>${kSrc(m.src)}</td>
            <td class="text-end ct-hl">${kz(m, fmt(m.rev.total))}</td><td class="text-end">${fmt(m.rev.target)}</td><td class="text-end">${kz(m, kPct(m.rev.rate))}</td><td class="text-end">${kz(m, kRatio(m.recv.ratio, m.recv.eval, m.recv.prov))}</td>
            <td class="text-end ct-gfirst">${kz(m, fmt(m.g1.ban))}</td><td class="text-end">${kz(m, kPct(m.g1.rate))}</td><td class="text-end ct-gfirst">${kz(m, fmt(m.g2.ban))}</td><td class="text-end">${kz(m, kPct(m.g2.rate))}</td></tr>`).join('');
        h += sec('indigo', 'calendar-week', 'Theo tháng', 'tháng có nền xanh là tháng đang dùng (lấy từ HMS)', `<div class="table-responsive"><table class="table align-middle mb-0 ct-table ct-sticky"><thead><tr><th rowspan="2">Tháng</th><th rowspan="2">Nguồn</th><th rowspan="2" class="text-end ct-hl">Doanh thu</th><th rowspan="2" class="text-end">Mục tiêu</th><th rowspan="2" class="text-end">% đạt</th><th rowspan="2" class="text-end">Nhận/Bán</th><th colspan="2" class="text-center ct-gfirst ct-grp">Hao mòn nhóm 1</th><th colspan="2" class="text-center ct-gfirst ct-grp">Hao mòn nhóm 2</th></tr>
            <tr><th class="text-end ct-sub ct-gfirst">DT thực tế</th><th class="text-end ct-sub">% đạt</th><th class="text-end ct-sub ct-gfirst">DT thực tế</th><th class="text-end ct-sub">% đạt</th></tr></thead><tbody>${mr}</tbody></table></div>`);
        return h;
    }
})();