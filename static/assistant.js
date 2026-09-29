/* Trợ lý AI Nam Sương Motor - nút chat nổi, kéo thả được. Tự chèn bởi assistant.py */
(function () {
  if (window.__nsAssistant) return;
  window.__nsAssistant = true;

  const LS_POS = 'nsai_pos', SS_MSG = 'nsai_msgs';
  const BTN = 56, MARGIN = 8;
  const $ = (r, s) => r.querySelector(s);
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
    sget(k, d) { try { return JSON.parse(sessionStorage.getItem(k)) ?? d; } catch (e) { return d; } },
    sset(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };

  const host = document.createElement('div');
  host.id = 'ns-assistant';
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: 'open' });

  /* ---------- biểu tượng (SVG nội tuyến, thay cho emoji) ---------- */
  const SV = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
  const I = {
    mark:  c => `<svg class="${c}" ${SV} stroke-width="1.7"><path d="M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-7l-4.5 3.5V17H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/><path class="st" d="M12 6l1.1 3.4 3.4 1.1-3.4 1.1L12 15l-1.1-3.4L7.5 10.5l3.4-1.1L12 6z"/></svg>`,
    close: c => `<svg class="${c}" ${SV} stroke-width="2"><path d="M6 6l12 12M18 6L6 18"/></svg>`,
    trash: c => `<svg class="${c}" ${SV} stroke-width="1.8"><path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6.5 7l.8 12a2 2 0 0 0 2 1.9h5.4a2 2 0 0 0 2-1.9l.8-12"/><path d="M10 11v5M14 11v5"/></svg>`,
    send:  c => `<svg class="${c}" ${SV} stroke-width="2.2"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>`,
  };

  root.innerHTML = `
<style>
  :host { all: initial; }
  * { box-sizing: border-box; font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
  #wrap { --acc: #22d3ee; --ink: #0b1220; --ink2: #16294a; }
  .st { fill: var(--acc); stroke: none; }
  button { font: inherit; }

  /* nút nổi */
  .fab { position: fixed; z-index: 2147483000; width: ${BTN}px; height: ${BTN}px; border-radius: 18px; cursor: grab;
         background: linear-gradient(160deg, var(--ink2), var(--ink)); color: #fff; display: grid; place-items: center;
         border: 1px solid rgba(34,211,238,.38); padding: 0;
         box-shadow: 0 10px 26px rgba(11,18,32,.42), 0 0 20px rgba(34,211,238,.18), inset 0 1px 0 rgba(255,255,255,.08);
         touch-action: none; user-select: none; -webkit-user-select: none; transition: border-color .15s, box-shadow .15s; }
  .fab:hover { border-color: rgba(34,211,238,.7); box-shadow: 0 10px 26px rgba(11,18,32,.42), 0 0 26px rgba(34,211,238,.32), inset 0 1px 0 rgba(255,255,255,.1); }
  .fab:active { cursor: grabbing; }
  .fab:focus { outline: none; }
  .fab:focus-visible { outline: 2px solid var(--acc); outline-offset: 3px; }
  .fab svg { width: 30px; height: 30px; pointer-events: none; }
  .fab .x { display: none; width: 22px; height: 22px; } .open .fab .chat { display: none; } .open .fab .x { display: block; }

  /* khung chat */
  .panel { position: fixed; z-index: 2147482999; display: none; flex-direction: column; overflow: hidden;
           background: #fff; color: #0f172a; border: 1px solid #d3dcec; border-radius: 18px;
           box-shadow: 0 18px 50px rgba(11,18,32,.30), 0 2px 8px rgba(11,18,32,.10); }
  .open .panel { display: flex; animation: pop .16s ease-out; }
  @keyframes pop { from { opacity: 0; transform: translateY(8px) scale(.98); } to { opacity: 1; transform: none; } }

  .head { position: relative; flex: none; display: flex; align-items: center; gap: 10px; padding: 12px 12px 12px 14px; cursor: grab; touch-action: none;
          background: linear-gradient(135deg, var(--ink), #13233f); color: #fff; user-select: none; -webkit-user-select: none;
          border-bottom: 1px solid rgba(34,211,238,.28); }
  .head::before { content: ""; position: absolute; inset: 0; pointer-events: none;
          background-image: linear-gradient(rgba(148,163,184,.08) 1px, transparent 1px), linear-gradient(90deg, rgba(148,163,184,.08) 1px, transparent 1px);
          background-size: 14px 14px; -webkit-mask-image: linear-gradient(90deg, transparent 20%, #000); mask-image: linear-gradient(90deg, transparent 20%, #000); }
  .head > * { position: relative; }
  .head:active { cursor: grabbing; }
  .logo { flex: none; width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center;
          background: rgba(34,211,238,.10); border: 1px solid rgba(34,211,238,.32); }
  .logo svg { width: 22px; height: 22px; }
  .ttl { flex: 1; min-width: 0; }
  .name { display: flex; align-items: center; gap: 7px; font-size: 14.5px; font-weight: 650; letter-spacing: .1px; }
  .tag { font-size: 10.5px; font-weight: 700; line-height: 1; padding: 3px 6px; border-radius: 6px; color: #67e8f9;
         background: rgba(34,211,238,.14); border: 1px solid rgba(34,211,238,.32); }
  .sub { margin-top: 2px; font-size: 11.5px; color: rgba(226,232,240,.72); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ib { flex: none; width: 32px; height: 32px; display: grid; place-items: center; cursor: pointer; padding: 0;
        color: #cbd5e1; background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.12); border-radius: 10px;
        transition: background .12s, color .12s, border-color .12s; }
  .ib:hover { background: rgba(255,255,255,.14); color: #fff; border-color: rgba(255,255,255,.22); }
  .ib:focus { outline: none; } .ib:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px; }
  .ib svg { width: 16px; height: 16px; pointer-events: none; }

  .msgs { flex: 1; overflow-y: auto; padding: 14px 12px; display: flex; flex-direction: column; gap: 8px; background: #f3f6fb;
          scrollbar-width: thin; scrollbar-color: #c3cee0 transparent; }
  .m { max-width: 88%; padding: 9px 12px; border-radius: 14px; font-size: 14px; line-height: 1.5; word-wrap: break-word; overflow-wrap: anywhere; }
  .m.u { align-self: flex-end; background: #1d4ed8; color: #fff; border-bottom-right-radius: 5px; white-space: pre-wrap; }
  .m.a { align-self: flex-start; background: #fff; border: 1px solid #dbe3ef; border-bottom-left-radius: 5px; box-shadow: 0 1px 2px rgba(11,18,32,.05); }
  .m.a p { margin: 0 0 6px; } .m.a p:last-child { margin: 0; } .m.a ul, .m.a ol { margin: 4px 0 6px; padding-left: 20px; }
  .m.a code { background: #eaf0f8; padding: 1px 5px; border-radius: 5px; font-size: 13px; }
  .m.err { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { background: #fff; border: 1px solid #cfd9ea; color: #17356b; border-radius: 10px; padding: 7px 11px; font-size: 12.5px; text-align: left; cursor: pointer;
          transition: border-color .12s, background .12s; }
  .chip:hover { border-color: #0891b2; background: #ecfeff; }
  .chip:focus { outline: none; } .chip:focus-visible { outline: 2px solid #0891b2; outline-offset: 2px; }
  .dots { display: inline-flex; gap: 4px; padding: 4px 0; } .dots i { width: 7px; height: 7px; border-radius: 50%; background: #0891b2; animation: b 1s infinite; }
  .dots i:nth-child(2) { animation-delay: .15s; } .dots i:nth-child(3) { animation-delay: .3s; }
  @keyframes b { 0%,80%,100% { transform: scale(.6); opacity: .45; } 40% { transform: scale(1); opacity: 1; } }

  .in { flex: none; display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e1e8f3; background: #fff; align-items: flex-end; }
  textarea { flex: 1; resize: none; max-height: 96px; min-height: 40px; border: 1px solid #c9d4e6; border-radius: 12px; padding: 9px 12px;
             font-size: 14px; outline: none; background: #fff; color: #0f172a; }
  textarea::placeholder { color: #7b8aa3; }
  textarea:focus { border-color: #0891b2; box-shadow: 0 0 0 3px rgba(8,145,178,.18); }
  .send { flex: none; width: 40px; height: 40px; display: grid; place-items: center; padding: 0; border: 1px solid rgba(34,211,238,.35); border-radius: 12px;
          background: linear-gradient(160deg, var(--ink2), var(--ink)); color: var(--acc); cursor: pointer; transition: border-color .12s; }
  .send:hover { border-color: rgba(34,211,238,.75); }
  .send:focus { outline: none; } .send:focus-visible { outline: 2px solid #0891b2; outline-offset: 2px; }
  .send svg { width: 18px; height: 18px; pointer-events: none; }
  .send:disabled { background: #dbe3ef; color: #94a3b8; border-color: #dbe3ef; cursor: default; }
  .foot { flex: none; text-align: center; font-size: 11px; color: #64748b; padding: 0 10px 8px; background: #fff; }

  @media (prefers-reduced-motion: reduce) { .open .panel { animation: none; } .dots i { animation: none; } .fab, .ib, .chip, .send { transition: none; } }
  @media (prefers-color-scheme: dark) {
    .panel { background: #0b1220; color: #e2e8f0; border-color: #24365a; } .msgs { background: #08101d; scrollbar-color: #2b3f66 transparent; }
    .m.a { background: #14213a; border-color: #24365a; color: #e2e8f0; box-shadow: none; } .m.a code { background: #0b1220; }
    .m.u { background: #2563eb; }
    .in, .foot { background: #0b1220; border-color: #1e2d4a; } .foot { color: #8b9bb5; }
    textarea { background: #14213a; color: #e2e8f0; border-color: #2b3f66; } textarea::placeholder { color: #8b9bb5; }
    .chip { background: #14213a; color: #bfdbfe; border-color: #2b3f66; } .chip:hover { background: #10304a; border-color: var(--acc); }
    .send:disabled { background: #1e2d4a; border-color: #1e2d4a; color: #5b6b86; }
  }
</style>
<div id="wrap">
  <div class="panel" id="panel" role="dialog" aria-label="Trợ lý AI">
    <div class="head" id="head">
      <div class="logo">${I.mark('')}</div>
      <div class="ttl">
        <div class="name">Trợ lý Nam Sương <span class="tag">AI</span></div>
        <div class="sub">Tra giá, tồn kho, đơn hàng, cách dùng</div>
      </div>
      <button class="ib" id="clear" title="Xoá cuộc trò chuyện" aria-label="Xoá cuộc trò chuyện">${I.trash('')}</button>
      <button class="ib" id="close" title="Đóng" aria-label="Đóng">${I.close('')}</button>
    </div>
    <div class="msgs" id="msgs"></div>
    <div class="in"><textarea id="q" rows="1" placeholder="Nhập câu hỏi... (Enter để gửi)" maxlength="1500"></textarea>
      <button class="send" id="send" aria-label="Gửi">${I.send('')}</button></div>
    <div class="foot">Trợ lý chỉ xem dữ liệu, không sửa được. Có thể sai — hãy kiểm tra số quan trọng.</div>
  </div>
  <button class="fab" id="fab" aria-label="Mở trợ lý AI (kéo để di chuyển)" title="Kéo để di chuyển">
    ${I.mark('chat')}
    ${I.close('x')}
  </button>
</div>`;

  const wrap = $(root, '#wrap'), fab = $(root, '#fab'), panel = $(root, '#panel'), head = $(root, '#head');
  const msgsEl = $(root, '#msgs'), q = $(root, '#q'), sendBtn = $(root, '#send');

  /* ---------- vị trí + kéo thả ---------- */
  const vw = () => window.innerWidth, vh = () => window.innerHeight;
  let pos = store.get(LS_POS, null) || { x: vw() - BTN - 16, y: vh() - BTN - 90 };
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function place() {
    pos.x = clamp(pos.x, MARGIN, vw() - BTN - MARGIN);
    pos.y = clamp(pos.y, MARGIN, vh() - BTN - MARGIN);
    fab.style.left = pos.x + 'px'; fab.style.top = pos.y + 'px';
    const W = Math.min(390, vw() - 16), H = Math.min(560, vh() - 24);
    panel.style.width = W + 'px'; panel.style.height = H + 'px';
    panel.style.left = clamp(pos.x + BTN - W, 8, vw() - W - 8) + 'px';
    let top = pos.y - H - 8; if (top < 8) top = pos.y + BTN + 8;
    panel.style.top = clamp(top, 8, vh() - H - 8) + 'px';
  }
  function makeDraggable(el, onTap) {
    let sx, sy, ox, oy, moved, id;
    el.addEventListener('pointerdown', e => {
      if (e.target.closest('button') && el !== fab) return;
      id = e.pointerId; el.setPointerCapture(id);
      sx = e.clientX; sy = e.clientY; ox = pos.x; oy = pos.y; moved = false;
    });
    el.addEventListener('pointermove', e => {
      if (id == null || !el.hasPointerCapture(id)) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (!moved && Math.hypot(dx, dy) < 5) return;
      moved = true; pos.x = ox + dx; pos.y = oy + dy; place();
    });
    const end = e => {
      if (id == null) return;
      try { el.releasePointerCapture(id); } catch (_) {}
      id = null;
      if (moved) store.set(LS_POS, pos); else if (onTap) onTap();
    };
    el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end);
  }
  const toggle = open => {
    wrap.classList.toggle('open', open ?? !wrap.classList.contains('open'));
    if (wrap.classList.contains('open')) { place(); setTimeout(() => q.focus({ preventScroll: true }), 50); scrollEnd(); }
  };
  makeDraggable(fab, () => toggle());
  makeDraggable(head, null);
  $(root, '#close').onclick = () => toggle(false);
  window.addEventListener('resize', place);
  place();

  /* ---------- hiển thị tin nhắn ---------- */
  const esc = s => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function md(text) {           // markdown rút gọn, an toàn (escape trước)
    const lines = esc(text).split('\n'); let html = '', list = null;
    const inline = s => s.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`(.+?)`/g, '<code>$1</code>');
    const close = () => { if (list) { html += `</${list}>`; list = null; } };
    for (const ln of lines) {
      let m;
      if ((m = ln.match(/^\s*(\d+)[.)]\s+(.*)/))) { if (list !== 'ol') { close(); html += '<ol>'; list = 'ol'; } html += `<li>${inline(m[2])}</li>`; }
      else if ((m = ln.match(/^\s*[-*•]\s+(.*)/))) { if (list !== 'ul') { close(); html += '<ul>'; list = 'ul'; } html += `<li>${inline(m[1])}</li>`; }
      else if (ln.trim() === '') close();
      else { close(); html += `<p>${inline(ln)}</p>`; }
    }
    close(); return html;
  }
  const scrollEnd = () => { msgsEl.scrollTop = msgsEl.scrollHeight; };
  function add(role, text, cls) {
    const d = document.createElement('div');
    d.className = 'm ' + (role === 'user' ? 'u' : 'a') + (cls ? ' ' + cls : '');
    if (role === 'user') d.textContent = text; else d.innerHTML = md(text);
    msgsEl.appendChild(d); scrollEnd(); return d;
  }

  let history = store.sget(SS_MSG, []);
  const path = location.pathname;
  const SUGGEST = path.startsWith('/orders')
    ? ['Mã 45290KPH951 giá bao nhiêu, tồn ở đâu?', 'Cách dán danh sách mã từ Excel?', 'Xin hàng nội bộ từ chi nhánh khác thế nào?']
    : path.startsWith('/teamhub')
      ? ['Cách gửi file trong chat?', 'Cách tạo nhóm mới?', 'Làm sao quay lại trang tra cứu?']
      : ['Mã 45290KPH951 giá bao nhiêu, tồn ở đâu?', 'Tìm giúp mình "chốt trượt"', 'Đơn của khách tên Linh đang ở trạng thái nào?'];

  function welcome() {
    msgsEl.innerHTML = '';
    add('assistant', 'Xin chào! Mình hướng dẫn cách dùng phần mềm và **tra giá, tồn kho, đơn đặt hàng** giúp bạn.');
    const c = document.createElement('div'); c.className = 'chips';
    SUGGEST.forEach(t => { const b = document.createElement('button'); b.className = 'chip'; b.textContent = t; b.onclick = () => ask(t); c.appendChild(b); });
    msgsEl.appendChild(c);
  }
  function restore() {
    if (!history.length) return welcome();
    msgsEl.innerHTML = ''; history.forEach(m => add(m.role, m.content));
  }
  restore();

  $(root, '#clear').onclick = () => { history = []; store.sset(SS_MSG, history); welcome(); };

  /* ---------- gửi câu hỏi ---------- */
  let busy = false;
  async function ask(text) {
    text = (text || '').trim(); if (!text || busy) return;
    if (!history.length) msgsEl.innerHTML = '';
    busy = true; sendBtn.disabled = true; q.value = ''; q.style.height = 'auto';
    add('user', text); history.push({ role: 'user', content: text });
    const wait = document.createElement('div'); wait.className = 'm a'; wait.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
    msgsEl.appendChild(wait); scrollEnd();
    try {
      const r = await fetch('/api/assistant/chat', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: history.slice(-12), page: location.pathname }),
      });
      let d = {}; try { d = await r.json(); } catch (_) {}
      wait.remove();
      if (!r.ok) { add('assistant', d.error || 'Có lỗi xảy ra, thử lại sau nhé.', 'err'); history.pop(); }
      else { add('assistant', d.reply); history.push({ role: 'assistant', content: d.reply }); }
    } catch (e) {
      wait.remove(); add('assistant', 'Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.', 'err'); history.pop();
    }
    store.sset(SS_MSG, history.slice(-20));
    busy = false; sendBtn.disabled = false; q.focus({ preventScroll: true });
  }
  sendBtn.onclick = () => ask(q.value);
  q.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(q.value); } });
  q.addEventListener('input', () => { q.style.height = 'auto'; q.style.height = Math.min(q.scrollHeight, 96) + 'px'; });
  // ngăn phím tắt của trang (Ctrl+S, Ctrl+Enter...) chạy khi đang gõ trong khung chat
  ['keydown', 'keyup', 'keypress'].forEach(t => host.addEventListener(t, e => e.stopPropagation()));
})();