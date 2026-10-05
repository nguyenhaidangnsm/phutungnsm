/* Nam Sương - Hộp thoại & thông báo dùng chung (thay alert / confirm / prompt gốc của trình duyệt)
 *
 *  nsAlert(msg, {type, title, okText})      -> Promise<void>      (hộp thoại 1 nút)
 *  nsConfirm(msg, {title, okText, cancelText, danger}) -> Promise<boolean>
 *  nsPrompt(msg, defaultValue, {title, okText, placeholder, multiline}) -> Promise<string|null>
 *  nsToast(msg, type, ms, {title, onClick}) -> thông báo nhỏ góc phải dưới, tự tắt (onClick: bấm vào để làm gì đó)
 *  window.alert(msg)  được thay bằng bản KHÔNG chặn trình duyệt: thông báo ngắn thành công -> toast,
 *                     còn lại (lỗi / cảnh báo / nhiều dòng) -> hộp thoại. Các hộp thoại xếp hàng, hiện lần lượt.
 *
 * Mã gọi confirm()/prompt() cũ phải đổi thành:  await nsConfirm(...)  /  await nsPrompt(...)
 * (confirm/prompt gốc không thể thay bằng bản không chặn mà giữ nguyên cách gọi đồng bộ).
 */
(function () {
  'use strict';
  if (window.nsDialog) return;

  var CSS = '' +
    '.nsd-ov{position:fixed;inset:0;z-index:20000;display:flex;align-items:center;justify-content:center;padding:16px;' +
    'background:rgba(15,23,42,.45);backdrop-filter:blur(2px);opacity:0;transition:opacity .15s ease}' +
    '.nsd-ov.in{opacity:1}' +
    '.nsd-box{width:100%;max-width:440px;background:#fff;border-radius:16px;box-shadow:0 24px 60px rgba(15,23,42,.35);' +
    'overflow:hidden;transform:translateY(8px) scale(.97);transition:transform .15s ease;font-family:inherit;color:#1e293b}' +
    '.nsd-ov.in .nsd-box{transform:none}' +
    '.nsd-bar{height:5px;background:var(--nsd-c)}' +
    '.nsd-body{display:flex;gap:14px;padding:22px 22px 8px}' +
    '.nsd-ico{flex:0 0 44px;width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;' +
    'background:var(--nsd-bg);color:var(--nsd-c)}' +
    '.nsd-ico svg{width:24px;height:24px}' +
    '.nsd-txt{min-width:0;flex:1}' +
    '.nsd-title{font-weight:700;font-size:1.05rem;line-height:1.3;margin:2px 0 6px;color:#0f172a}' +
    '.nsd-msg{font-size:.93rem;line-height:1.5;color:#475569;white-space:pre-line;word-break:break-word;max-height:55vh;overflow:auto}' +
    '.nsd-in{display:block;width:100%;margin-top:12px;padding:.5rem .7rem;border:1px solid #cbd5e1;border-radius:10px;font-size:.93rem;' +
    'font-family:inherit;outline:none;resize:vertical}' +
    '.nsd-in:focus{border-color:var(--nsd-c);box-shadow:0 0 0 3px var(--nsd-bg)}' +
    '.nsd-foot{display:flex;justify-content:flex-end;gap:10px;padding:16px 22px 20px}' +
    '.nsd-btn{border:1px solid transparent;border-radius:10px;padding:.5rem 1.15rem;font-size:.92rem;font-weight:600;cursor:pointer;' +
    'font-family:inherit;transition:filter .12s,background .12s}' +
    '.nsd-btn:focus-visible{outline:3px solid var(--nsd-bg);outline-offset:1px}' +
    '.nsd-ok{background:var(--nsd-c);color:#fff}.nsd-ok:hover{filter:brightness(.92)}' +
    '.nsd-cancel{background:#f1f5f9;color:#334155;border-color:#e2e8f0}.nsd-cancel:hover{background:#e2e8f0}' +
    '.nsd-t-info{--nsd-c:#2563eb;--nsd-bg:#dbeafe}.nsd-t-success{--nsd-c:#16a34a;--nsd-bg:#dcfce7}' +
    '.nsd-t-warning{--nsd-c:#d97706;--nsd-bg:#fef3c7}.nsd-t-error{--nsd-c:#dc2626;--nsd-bg:#fee2e2}' +
    '.nsd-t-question{--nsd-c:#2563eb;--nsd-bg:#dbeafe}' +
    '#nsd-toasts{position:fixed;right:16px;bottom:16px;z-index:20001;display:flex;flex-direction:column;gap:10px;' +
    'width:min(380px,calc(100vw - 32px));pointer-events:none}' +
    '.nsd-toast{pointer-events:auto;display:flex;align-items:flex-start;gap:10px;background:#fff;border-radius:12px;padding:12px 12px 12px 14px;' +
    'border-left:5px solid var(--nsd-c);box-shadow:0 10px 30px rgba(15,23,42,.22);font-size:.9rem;line-height:1.45;color:#1e293b;' +
    'transform:translateX(24px);opacity:0;transition:transform .2s ease,opacity .2s ease;cursor:default;white-space:pre-line;word-break:break-word}' +
    '.nsd-toast.in{transform:none;opacity:1}' +
    '.nsd-toast .nsd-ti{flex:0 0 20px;width:20px;height:20px;color:var(--nsd-c);margin-top:1px}' +
    '.nsd-toast .nsd-tx{flex:1;min-width:0}' +
    '.nsd-toast .nsd-x{flex:0 0 auto;border:0;background:transparent;color:#94a3b8;font-size:1.2rem;line-height:1;cursor:pointer;padding:0 4px}' +
    '.nsd-toast .nsd-x:hover{color:#475569}' +
    '@media (max-width:480px){.nsd-body{padding:18px 16px 4px}.nsd-foot{padding:14px 16px 16px}.nsd-foot .nsd-btn{flex:1}}';

  var SVG = {
    success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/></svg>',
    error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/></svg>',
    warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5l9 16H3l9-16z"/><path d="M12 10v4.5M12 17.4v.1"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.6v.1"/></svg>',
    question: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 114 2c-.9.6-1.6 1.1-1.6 2.1M12 16.8v.1"/></svg>'
  };
  var TITLES = { success: 'Thành công', error: 'Có lỗi xảy ra', warning: 'Lưu ý', info: 'Thông báo', question: 'Xác nhận' };

  function injectCss() {
    if (document.getElementById('nsd-css')) return;
    var st = document.createElement('style'); st.id = 'nsd-css'; st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function text(m) { return m == null ? '' : String(m); }

  // ---- đoán loại thông báo từ nội dung tiếng Việt ----
  var RE_ERR = /(lỗi|thất bại|không thể|không hợp lệ|không đọc được|không tìm thấy|không có quyền|quá lâu|mất kết nối|forbidden|failed|error)/i;
  var RE_WARN = /(vui lòng|hãy |chưa |cần |lưu ý|không được|bắt buộc|trùng|quá hạn|cảnh báo|phải )/i;
  var RE_OK = /^\s*(đã |thành công|hoàn tất|xong)/i;
  function detect(msg) {
    var m = text(msg);
    if (RE_OK.test(m) && !RE_ERR.test(m.split('\n')[0])) return 'success';
    if (RE_ERR.test(m)) return 'error';
    if (RE_WARN.test(m)) return 'warning';
    return 'info';
  }

  // ---- hàng đợi hộp thoại: hiện lần lượt, không chồng lên nhau ----
  var queue = [], showing = false;
  function enqueue(job) {
    return new Promise(function (resolve) { queue.push({ job: job, resolve: resolve }); pump(); });
  }
  function pump() {
    if (showing || !queue.length) return;
    showing = true;
    var it = queue.shift();
    openModal(it.job, function (v) { showing = false; it.resolve(v); setTimeout(pump, 60); });
  }

  function openModal(o, done) {
    injectCss();
    var type = o.type || 'info', isConf = o.kind === 'confirm', isPrompt = o.kind === 'prompt';
    var ov = document.createElement('div');
    ov.className = 'nsd-ov nsd-t-' + type;
    ov.setAttribute('role', isConf || isPrompt ? 'dialog' : 'alertdialog');
    ov.setAttribute('aria-modal', 'true');
    var title = o.title || TITLES[type] || 'Thông báo';
    var inputHtml = '';
    if (isPrompt) {
      inputHtml = o.multiline
        ? '<textarea class="nsd-in" rows="3" placeholder="' + esc(o.placeholder || '') + '">' + esc(o.defaultValue || '') + '</textarea>'
        : '<input class="nsd-in" type="text" placeholder="' + esc(o.placeholder || '') + '" value="' + esc(o.defaultValue || '') + '">';
    }
    ov.innerHTML =
      '<div class="nsd-box"><div class="nsd-bar"></div>' +
      '<div class="nsd-body"><div class="nsd-ico">' + (SVG[type] || SVG.info) + '</div>' +
      '<div class="nsd-txt"><div class="nsd-title">' + esc(title) + '</div><div class="nsd-msg">' + esc(o.message) + '</div>' + inputHtml + '</div></div>' +
      '<div class="nsd-foot">' +
      (isConf || isPrompt ? '<button type="button" class="nsd-btn nsd-cancel">' + esc(o.cancelText || 'Huỷ bỏ') + '</button>' : '') +
      '<button type="button" class="nsd-btn nsd-ok">' + esc(o.okText || (isConf || isPrompt ? 'Đồng ý' : 'Đã hiểu')) + '</button>' +
      '</div></div>';

    var prevFocus = document.activeElement, closed = false;
    var okBtn = ov.querySelector('.nsd-ok'), cancelBtn = ov.querySelector('.nsd-cancel'), inp = ov.querySelector('.nsd-in');
    function close(val) {
      if (closed) return; closed = true;
      document.removeEventListener('keydown', onKey, true);
      ov.classList.remove('in');
      setTimeout(function () { if (ov.parentNode) ov.parentNode.removeChild(ov); }, 160);
      try { if (prevFocus && prevFocus.focus) prevFocus.focus(); } catch (e) { /* bỏ qua */ }
      done(val);
    }
    function accept() { close(isPrompt ? inp.value : (isConf ? true : undefined)); }
    function reject() { close(isPrompt ? null : (isConf ? false : undefined)); }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); reject(); }
      else if (e.key === 'Enter' && !(e.target && e.target.tagName === 'TEXTAREA' && !e.ctrlKey)) {
        if (e.target === cancelBtn) return;            // Enter trên nút Huỷ = bấm Huỷ (để trình duyệt tự xử lý)
        e.preventDefault(); e.stopPropagation(); accept();
      } else if (e.key === 'Tab') {                    // giữ focus trong hộp thoại
        var f = ov.querySelectorAll('button,input,textarea'); if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    okBtn.addEventListener('click', accept);
    if (cancelBtn) cancelBtn.addEventListener('click', reject);
    if (!isConf && !isPrompt) ov.addEventListener('mousedown', function (e) { if (e.target === ov) reject(); });   // thông báo thường: bấm nền để đóng
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(ov);
    requestAnimationFrame(function () { ov.classList.add('in'); });
    setTimeout(function () { (inp || (o.danger && cancelBtn) || okBtn).focus(); if (inp) inp.select(); }, 30);
  }

  // ---- toast ----
  function toastBox() {
    injectCss();
    var b = document.getElementById('nsd-toasts');
    if (!b) { b = document.createElement('div'); b.id = 'nsd-toasts'; b.setAttribute('aria-live', 'polite'); document.body.appendChild(b); }
    return b;
  }
  function nsToast(msg, type, ms, opts) {
    opts = opts || {};
    type = type || detect(msg);
    var b = toastBox(), t = document.createElement('div');
    t.className = 'nsd-toast nsd-t-' + type;
    t.setAttribute('role', 'status');
    t.innerHTML = '<span class="nsd-ti">' + (SVG[type] || SVG.info) + '</span><div class="nsd-tx">' +
      (opts.title ? '<div style="font-weight:700;color:#0f172a">' + esc(text(opts.title)) + '</div>' : '') + esc(text(msg)) +
      '</div><button type="button" class="nsd-x" aria-label="Đóng">&times;</button>';
    var timer = null;
    function kill() { clearTimeout(timer); t.classList.remove('in'); setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 220); }
    t.querySelector('.nsd-x').addEventListener('click', function (e) { e.stopPropagation(); kill(); });
    if (typeof opts.onClick === 'function') {          // thông báo bấm được (vd "Có đơn mới, bấm để xem")
      t.style.cursor = 'pointer';
      t.addEventListener('click', function () { kill(); try { opts.onClick(); } catch (e) { console.error(e); } });
    }
    t.addEventListener('mouseenter', function () { clearTimeout(timer); });
    t.addEventListener('mouseleave', function () { timer = setTimeout(kill, 2000); });
    b.appendChild(t);
    while (b.children.length > 4) b.removeChild(b.firstChild);   // tối đa 4 thông báo cùng lúc
    requestAnimationFrame(function () { t.classList.add('in'); });
    timer = setTimeout(kill, ms || (type === 'error' || type === 'warning' ? 7000 : 3800));
    return kill;
  }

  // ---- API công khai ----
  function nsAlert(msg, opts) {
    opts = opts || {};
    return enqueue({ kind: 'alert', message: text(msg), type: opts.type || detect(msg), title: opts.title, okText: opts.okText });
  }
  var RE_DANGER = /(xoá|xóa|không thể hoàn tác|không thể khôi phục|không khôi phục|thay thế toàn bộ|thay thế|huỷ phiếu|thu hồi|mất)/i;
  function nsConfirm(msg, opts) {
    opts = opts || {};
    var m = text(msg), danger = opts.danger != null ? opts.danger : RE_DANGER.test(m);
    var ok = opts.okText || (/^\s*xoá|^\s*xóa/i.test(m) ? 'Xoá' : 'Đồng ý');
    return enqueue({ kind: 'confirm', message: m, type: opts.type || (danger ? 'warning' : 'question'), title: opts.title || (danger ? 'Xác nhận thao tác' : 'Xác nhận'),
                     okText: ok, cancelText: opts.cancelText, danger: danger });
  }
  function nsPrompt(msg, defaultValue, opts) {
    opts = opts || {};
    var m = text(msg);
    return enqueue({ kind: 'prompt', message: m, defaultValue: defaultValue == null ? '' : String(defaultValue), type: opts.type || 'question',
                     title: opts.title || 'Nhập thông tin', okText: opts.okText, cancelText: opts.cancelText, placeholder: opts.placeholder,
                     multiline: opts.multiline != null ? opts.multiline : m.length > 160 });
  }

  // alert() KHÔNG chặn: thông báo ngắn thành công -> toast; còn lại -> hộp thoại
  window.alert = function (msg) {
    var m = text(msg), type = detect(m);
    if (type === 'success' && m.length <= 140 && m.indexOf('\n') < 0) { nsToast(m, 'success'); return; }
    nsAlert(m, { type: type });
  };

  window.nsDialog = true;
  window.nsAlert = nsAlert; window.nsConfirm = nsConfirm; window.nsPrompt = nsPrompt; window.nsToast = nsToast;
})();
