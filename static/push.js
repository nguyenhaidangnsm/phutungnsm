/* =====================================================================
   push.js - THÔNG BÁO ĐẨY cho Nam Sương Motor (index.html + teamhub.html)
   ---------------------------------------------------------------------
   * Chỉ THÊM: đăng ký service worker /sw.js, xin quyền, gửi đăng ký lên /api/push/subscribe.
   * Không sửa/không gọi bất kỳ hàm nào của app.js. Lỗi gì cũng nuốt im lặng.
   * Bấm vào thông báo -> mở đúng tab bằng cách "bấm hộ" nút trong menu (giống mobile.js).
   * Dùng thủ công: window.nsPush.enable() / .disable() / .test()
   ===================================================================== */
(function () {
    'use strict';

    var SUPPORTED = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
    var IS_IOS = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    var STANDALONE = (window.matchMedia && matchMedia('(display-mode: standalone)').matches) || navigator.standalone === true;
    var LS = { get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
               set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };

    function b64ToU8(s) {
        var pad = '='.repeat((4 - s.length % 4) % 4);
        var raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
        var out = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
        return out;
    }
    function sameKey(buf, u8) {
        if (!buf) return false;
        var a = new Uint8Array(buf);
        if (a.length !== u8.length) return false;
        for (var i = 0; i < a.length; i++) if (a[i] !== u8[i]) return false;
        return true;
    }
    function json(url, body) {
        return fetch(url, { method: body ? 'POST' : 'GET', credentials: 'same-origin',
            headers: body ? { 'Content-Type': 'application/json' } : undefined,
            body: body ? JSON.stringify(body) : undefined });
    }

    async function getRegistration() {
        var reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        if (reg.active) return reg;
        var sw = reg.installing || reg.waiting;
        if (!sw) return reg;
        await new Promise(function (ok) {
            if (sw.state === 'activated') return ok();
            sw.addEventListener('statechange', function () { if (sw.state === 'activated') ok(); });
            setTimeout(ok, 8000);
        });
        return reg;
    }

    // Đăng ký (hoặc đồng bộ lại) thiết bị này cho tài khoản đang đăng nhập.
    async function sync(force) {
        if (!SUPPORTED || Notification.permission !== 'granted') return false;
        var r = await json('/api/push/key');
        if (!r.ok) return false;                                   // chưa đăng nhập
        var info = await r.json();
        if (!info.enabled || !info.key) return false;              // server chưa bật
        var last = LS.get('ns_push_sync') || '';
        var parts = last.split('|');
        var fresh = parts[0] === info.me && (Date.now() - Number(parts[1] || 0)) < 12 * 3600 * 1000;
        if (fresh && !force) return true;
        var reg = await getRegistration();
        var key = b64ToU8(info.key);
        var sub = await reg.pushManager.getSubscription();
        if (sub && !sameKey(sub.options && sub.options.applicationServerKey, key)) { await sub.unsubscribe(); sub = null; }
        if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
        var res = await json('/api/push/subscribe', sub.toJSON());
        if (!res.ok) return false;
        LS.set('ns_push_sync', info.me + '|' + Date.now());
        return true;
    }

    async function enable() {
        if (!SUPPORTED) return false;
        var perm = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission;
        if (perm !== 'granted') return false;
        return sync(true);
    }

    async function disable() {
        if (!SUPPORTED) return;
        var reg = await navigator.serviceWorker.getRegistration('/');
        var sub = reg && await reg.pushManager.getSubscription();
        if (sub) { await json('/api/push/unsubscribe', { endpoint: sub.endpoint }); await sub.unsubscribe(); }
        LS.set('ns_push_sync', '');
    }

    async function test() {
        var r = await json('/api/push/test', {});
        return r.json();
    }

    // ---------- Banner mời bật thông báo ----------
    function banner(html, okLabel, onOk, dismissKey) {
        var d = document.createElement('div');
        d.setAttribute('role', 'dialog');
        d.setAttribute('data-ns-push-banner', '1');
        d.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);top:calc(env(safe-area-inset-top,0px) + 8px);' +
            'z-index:2147483000;max-width:min(94vw,460px);width:max-content;background:#0d6efd;color:#fff;border-radius:12px;' +
            'box-shadow:0 8px 24px rgba(0,0,0,.25);padding:10px 12px;display:flex;gap:10px;align-items:center;font:500 13px/1.35 system-ui,sans-serif';
        var t = document.createElement('span'); t.innerHTML = html; t.style.flex = '1';
        d.appendChild(t);
        function btn(label, bg, fn) {
            var b = document.createElement('button'); b.type = 'button'; b.textContent = label;
            b.style.cssText = 'border:0;border-radius:8px;padding:6px 10px;font:600 12px system-ui;cursor:pointer;background:' + bg + ';color:' + (bg === '#fff' ? '#0d6efd' : '#fff');
            b.onclick = fn; return b;
        }
        if (okLabel) d.appendChild(btn(okLabel, '#fff', function () { d.remove(); onOk(); }));
        d.appendChild(btn('Để sau', 'rgba(255,255,255,.2)', function () { d.remove(); LS.set(dismissKey, String(Date.now())); }));
        document.body.appendChild(d);
    }
    function dismissedRecently(key) { return Date.now() - Number(LS.get(key) || 0) < 7 * 24 * 3600 * 1000; }

    async function offer() {
        if (document.querySelector('[data-ns-push-banner]')) return;
        if (!SUPPORTED) {
            if (IS_IOS && !STANDALONE && !dismissedRecently('ns_push_ios_hint')) {
                banner('Để nhận <b>thông báo đẩy</b> trên iPhone: bấm nút <b>Chia sẻ</b> → <b>Thêm vào Màn hình chính</b>, rồi mở app từ biểu tượng đó.',
                       null, null, 'ns_push_ios_hint');
            }
            return;
        }
        if (Notification.permission !== 'default' || dismissedRecently('ns_push_offer')) return;
        var r = await json('/api/push/key');
        if (!r.ok || !(await r.json()).enabled) return;
        banner('Bật <b>thông báo đẩy</b> để nhận tin về gôm đơn, duyệt đơn, phiếu luân chuyển và tin nhắn Kết nối.',
               'Bật', function () { enable().catch(function () {}); }, 'ns_push_offer');
    }

    // ---------- Bấm thông báo -> mở đúng tab ----------
    var GO = { review: 'order-check-tab', gdh: 'gdh-tab', transfer: 'transfer-tab',
               'transfer-export': 'transfer-export-tab', 'transfer-import': 'transfer-import-tab' };
    function openGo(go, tries) {
        var tab = GO[go];
        if (!tab) return;
        var el = document.querySelector('#mega-nav [data-target-tab="' + tab + '"]');
        if (el) { el.click(); window.scrollTo(0, 0); return; }
        if ((tries || 0) < 12) setTimeout(function () { openGo(go, (tries || 0) + 1); }, 400);   // chờ app dựng xong menu
    }
    function handleUrl(href) {
        try {
            var u = new URL(href, location.origin);
            var go = u.searchParams.get('go');
            if (go && u.pathname.indexOf('/teamhub') !== 0) openGo(go, 0);
        } catch (e) {}
    }
    if (SUPPORTED) navigator.serviceWorker.addEventListener('message', function (e) {
        if (e.data && e.data.type === 'ns-push-open') handleUrl(e.data.url);
    });

    function init() {
        try {
            var go = new URLSearchParams(location.search).get('go');
            if (go) { openGo(go, 0); history.replaceState(null, '', location.pathname + location.hash); }
        } catch (e) {}
        sync(false).catch(function () {});
        setTimeout(function () { offer().catch(function () {}); }, 2500);
    }

    window.nsPush = { enable: enable, disable: disable, test: test, supported: SUPPORTED };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
