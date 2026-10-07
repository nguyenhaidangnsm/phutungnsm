/* =====================================================================
   mobile.js - "VỎ APP" ĐIỆN THOẠI cho index.html
   ---------------------------------------------------------------------
   * Chỉ dựng thêm giao diện (thanh tab dưới, menu kéo lên, tiêu đề màn hình).
   * KHÔNG sửa logic: mọi nút đều "bấm hộ" vào đúng nút gốc trong #mega-nav
     (megaNavClick / onclick cũ vẫn chạy y nguyên).
   * Mọi phần tử tạo ra chỉ hiện khi màn hình <= 767.98px (xem mobile.css).
   * Lỗi bất kỳ -> tự gỡ class html.nsm-ready, giao diện quay về như cũ.
   ===================================================================== */
(function () {
    'use strict';

    var root = document.documentElement;
    var $ = function (s, r) { return (r || document).querySelector(s); };
    var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

    function labelOf(item) {                       // nhãn mục menu, bỏ huy hiệu số
        var lab = $('.ns-label', item);
        if (!lab) return '';
        var c = lab.cloneNode(true);
        $$('.badge', c).forEach(function (b) { b.remove(); });
        return c.textContent.replace(/\s+/g, ' ').trim();
    }

    function mk(html) {
        var d = document.createElement('div');
        d.innerHTML = html.trim();
        return d.firstChild;
    }

    function build() {
        var nav = document.getElementById('mega-nav');
        var banner = $('.brand-banner');
        if (!nav || !banner) return;                           // trang phụ: không dựng vỏ app

        /* ---------- 1) Tiêu đề thanh trên: tên màn hình đang mở ---------- */
        var chip = $('.badge.rounded-pill.text-truncate', banner);
        var code = '', person = '';
        if (chip) {
            var cb = $('.badge', chip);
            code = cb ? cb.textContent.trim() : '';
            var c2 = chip.cloneNode(true);
            $$('.badge, strong, i', c2).forEach(function (n) { n.remove(); });
            person = c2.textContent.replace(/\s+/g, ' ').trim();
        }
        var firstDiv = banner.firstElementChild;
        if (firstDiv) firstDiv.classList.add('nsm-hide');      // tiêu đề dài cũ: ẩn trên điện thoại
        var title = mk('<div class="nsm-title"><b>Nam Sương</b><small></small></div>');
        var titleB = $('b', title), titleS = $('small', title);
        titleS.textContent = [code, person].filter(Boolean).join(' · ');
        banner.insertBefore(title, banner.firstChild);

        /* ---------- 2) Thanh tab dưới ---------- */
        var defs = [
            { sel: '[data-target-tab="overview-tab"]',  icon: 'bi-house-door-fill',     label: 'Tổng quan' },
            { sel: '[data-target-tab="inventory-tab"]', icon: 'bi-boxes',               label: 'Kho' },
            { sel: '[data-target-tab="gdh-tab"]',       icon: 'bi-box-seam-fill',       label: 'Gôm đơn' },
            { sel: '[onclick*="/teamhub"]',             icon: 'bi-chat-dots-fill',      label: 'Kết nối' }
        ];
        var bar = document.createElement('nav');
        bar.className = 'nsm-tabbar';
        bar.setAttribute('aria-label', 'Thanh điều hướng');
        var tabs = [];
        defs.forEach(function (d) {
            var src = $(d.sel, nav);
            if (!src) return;                                  // vai trò này không có mục đó
            var b = mk('<button type="button" class="nsm-tab"><i class="bi ' + d.icon + '"></i><span class="t">' + d.label + '</span></button>');
            b.addEventListener('click', function () {
                if (b.classList.contains('on')) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
                closeMenu();
                src.click();                                   // bấm hộ nút gốc -> logic cũ chạy như thường
                window.scrollTo(0, 0);
            });
            tabs.push({ el: b, src: src });
            bar.appendChild(b);
        });
        var menuTab = mk('<button type="button" class="nsm-tab" aria-haspopup="dialog"><i class="bi bi-grid-3x3-gap-fill"></i><span class="t">Menu</span><span class="dot"></span></button>');
        menuTab.addEventListener('click', function () { openMenu(); });
        bar.appendChild(menuTab);
        document.body.appendChild(bar);

        /* ---------- 3) Menu kéo lên (chính là #mega-nav) ---------- */
        var scrim = document.createElement('div');
        scrim.className = 'nsm-scrim';
        document.body.appendChild(scrim);

        var initial = (person || code || 'N').trim().charAt(0).toUpperCase();
        var profile = mk('<div class="nsm-profile"><div class="nsm-avatar"></div><div><b></b><small></small></div>' +
            '<button type="button" class="nsm-x" aria-label="Đóng menu"><i class="bi bi-x-lg"></i></button></div>');
        $('.nsm-avatar', profile).textContent = initial;
        $('b', profile).textContent = person || 'Tài khoản';
        $('small', profile).textContent = code ? 'Cửa hàng / chi nhánh: ' + code : '';
        $('.nsm-x', profile).addEventListener('click', closeMenu);
        $$('.ns-sidebar-group', nav).slice(0, 2).forEach(function (g) { g.classList.add('nsm-pair'); });
        nav.insertBefore(profile, nav.firstChild);

        // Nhóm "Tài khoản": Đổi mật khẩu + Đăng xuất (bấm hộ nút gốc trên thanh trên)
        var acct = mk('<div class="ns-sidebar-group nsm-acct"><div class="ns-sidebar-group-title"><span class="ns-label">Tài khoản</span></div>' +
            '<button type="button" class="ns-sidebar-item" data-nsm="pass"><span class="ns-sidebar-icon"><i class="bi bi-key-fill"></i></span><span class="ns-label">Đổi mật khẩu</span></button>' +
            '<button type="button" class="ns-sidebar-item nsm-danger" data-nsm="logout"><span class="ns-sidebar-icon"><i class="bi bi-box-arrow-right"></i></span><span class="ns-label">Đăng xuất</span></button>' +
            '</div>');
        nav.appendChild(acct);

        function openMenu() {
            nav.classList.add('nsm-open'); scrim.classList.add('on'); root.classList.add('nsm-lock');
            nav.scrollTop = 0;
        }
        function closeMenu() {
            nav.classList.remove('nsm-open'); scrim.classList.remove('on'); root.classList.remove('nsm-lock');
        }
        scrim.addEventListener('click', closeMenu);
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && nav.classList.contains('nsm-open')) closeMenu(); });

        nav.addEventListener('click', function (e) {
            var it = e.target.closest ? e.target.closest('.ns-sidebar-item') : null;
            if (!it || !nav.classList.contains('nsm-open')) return;
            var act = it.getAttribute('data-nsm');
            if (act === 'pass') { var p = $('.brand-banner [onclick*="openSelfPassModal"]'); if (p) p.click(); }
            else if (act === 'logout') { var l = $('.brand-banner a[href="/logout"]'); if (l) l.click(); }
            else window.scrollTo(0, 0);
            closeMenu();
        });

        // Vuốt xuống để đóng menu (khi đang ở đầu danh sách)
        var sy = null;
        nav.addEventListener('touchstart', function (e) { sy = nav.scrollTop <= 0 ? e.touches[0].clientY : null; }, { passive: true });
        nav.addEventListener('touchmove', function (e) {
            if (sy == null) return;
            if (e.touches[0].clientY - sy > 90) { sy = null; closeMenu(); }
        }, { passive: true });

        /* ---------- 4) Đồng bộ: tab đang chọn, tiêu đề, chấm đỏ ---------- */
        function visibleBadge(b) {
            var t = (b.textContent || '').trim();
            return t && t !== '0' && getComputedStyle(b).display !== 'none';
        }
        function sync() {
            var active = $('.ns-sidebar-item.mega-active[data-target-tab]', nav);
            var matched = false;
            tabs.forEach(function (t) {
                var on = t.src.classList.contains('mega-active');
                t.el.classList.toggle('on', on);
                if (on) matched = true;
            });
            menuTab.classList.toggle('on', !!active && !matched);
            var name = active ? labelOf(active) : '';
            if (name && titleB.textContent !== name) titleB.textContent = name;
            else if (!name && !titleB.textContent) titleB.textContent = 'Nam Sương';
            var dot = $$('.ns-sidebar-badge-dot, .oc-nav-badge', nav).some(visibleBadge);
            menuTab.classList.toggle('has-dot', dot);
        }
        sync();
        try {
            new MutationObserver(sync).observe(nav, {
                subtree: true, childList: true, characterData: true,
                attributes: true, attributeFilter: ['class', 'style']
            });
        } catch (e) { /* trình duyệt cũ: vẫn có sync lần đầu + định kỳ bên dưới */ }
        setTimeout(sync, 400); setTimeout(sync, 1500);

        /* ---------- 5) Đang gõ phím: ẩn thanh tab cho rộng chỗ ---------- */
        var kbT;
        function isText(el) {
            if (!el || !el.tagName) return false;
            var t = el.tagName.toLowerCase();
            if (t === 'textarea' || t === 'select') return true;
            if (t !== 'input') return false;
            return !/^(checkbox|radio|button|submit|file|range|color)$/i.test(el.type || 'text');
        }
        document.addEventListener('focusin', function (e) { if (isText(e.target)) { clearTimeout(kbT); root.classList.add('nsm-kb'); } });
        document.addEventListener('focusout', function () { clearTimeout(kbT); kbT = setTimeout(function () { root.classList.remove('nsm-kb'); }, 120); });

        /* ---------- 6) Xoay màn hình / chuyển sang máy tính: đóng menu ---------- */
        var mq = window.matchMedia('(max-width: 767.98px)');
        var onChange = function () { if (!mq.matches) closeMenu(); };
        if (mq.addEventListener) mq.addEventListener('change', onChange); else if (mq.addListener) mq.addListener(onChange);

        root.classList.add('nsm-ready');
    }

    function init() {
        try { build(); }
        catch (err) {
            root.classList.remove('nsm-ready');
            if (window.console) console.warn('[mobile.js] tắt vỏ app điện thoại:', err);
        }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
