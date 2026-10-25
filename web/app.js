/* Telegram-style account dashboard (vanilla JS, no inline code, no innerHTML: all chat text goes through textContent). */
(function () {
  'use strict';

  // ------------------------------------------------------------------ small helpers (also exported for tests)
  function h(tag, props) {
    var el = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (k) {
        var v = props[k];
        if (v === undefined || v === null || v === false) return;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k === 'style') String(v).split(';').forEach(function (decl) { var i = decl.indexOf(':'); if (i > 0) el.style.setProperty(decl.slice(0, i).trim(), decl.slice(i + 1).trim()); });
        else if (k.slice(0, 2) === 'on') el[k] = v;
        else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'hidden' || k === 'src' || k === 'href' || k === 'type' ||
                 k === 'placeholder' || k === 'title' || k === 'rows' || k === 'accept' || k === 'maxLength' || k === 'autocomplete') el[k] = v;
        else el.setAttribute(k, v);
      });
    }
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c === null || c === undefined || c === false) continue;
      if (Array.isArray(c)) c.forEach(function (x) { if (x) el.appendChild(typeof x === 'string' ? document.createTextNode(x) : x); });
      else el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return el;
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function sameDay(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
  function fmtTime(iso, now) {
    if (!iso) return '';
    var d = new Date(iso); if (isNaN(d)) return '';
    now = now || new Date();
    if (sameDay(d, now)) return pad(d.getHours()) + ':' + pad(d.getMinutes());
    if (now - d < 6 * 864e5) return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()];
    return d.getDate() + ' ' + MONTHS[d.getMonth()] + (d.getFullYear() !== now.getFullYear() ? ' ' + d.getFullYear() : '');
  }
  function fmtClock(iso) { var d = new Date(iso); return isNaN(d) ? '' : pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function fmtDay(iso, now) {
    var d = new Date(iso); if (isNaN(d)) return '';
    now = now || new Date();
    if (sameDay(d, now)) return 'Today';
    var y = new Date(now.getTime() - 864e5); if (sameDay(d, y)) return 'Yesterday';
    return d.getDate() + ' ' + MONTHS[d.getMonth()] + (d.getFullYear() !== now.getFullYear() ? ' ' + d.getFullYear() : '');
  }
  function fmtFull(iso) { var d = new Date(iso); return isNaN(d) ? '' : d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear() + ', ' + pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function initials(name) {
    var parts = String(name || '?').replace(/[^\p{L}\p{N} ]/gu, '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
  }
  var COLORS = ['#e17076', '#7bc862', '#e5ca77', '#65aadd', '#a695e7', '#ee7aae', '#6ec9cb', '#faa774'];
  function colorFor(id) { var n = Math.abs(parseInt(id, 10) || 0); return COLORS[n % COLORS.length]; }
  function humanSize(n) {
    if (!n && n !== 0) return '';
    var u = ['B', 'KB', 'MB', 'GB']; var i = 0; n = Number(n);
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n : n.toFixed(1)) + ' ' + u[i];
  }
  var URL_RE = /(https?:\/\/[^\s<>"']+[^\s<>"'.,;:!?)\]])/g;
  function linkify(text) {
    var out = [], last = 0, m; URL_RE.lastIndex = 0;
    while ((m = URL_RE.exec(text)) !== null) {
      if (m.index > last) out.push({ t: 'text', v: text.slice(last, m.index) });
      out.push({ t: 'url', v: m[0] }); last = m.index + m[0].length;
    }
    if (last < text.length) out.push({ t: 'text', v: text.slice(last) });
    return out;
  }
  function statusText(st) {
    return { online: 'online', offline: 'last seen recently', recently: 'last seen recently', last_week: 'last seen within a week',
             last_month: 'last seen within a month', long_ago: 'last seen a long time ago' }[st] || '';
  }
  function debounce(fn, ms) { var t; return function () { var a = arguments, s = this; clearTimeout(t); t = setTimeout(function () { fn.apply(s, a); }, ms); }; }
  function mergeMessages(list, incoming) {
    var byId = {}; list.forEach(function (m) { byId[m.id] = m; });
    incoming.forEach(function (m) { byId[m.id] = m; });
    return Object.keys(byId).map(function (k) { return byId[k]; }).sort(function (a, b) { return a.id - b.id; });
  }
  window.TGU = { fmtTime: fmtTime, fmtDay: fmtDay, initials: initials, colorFor: colorFor, humanSize: humanSize, linkify: linkify,
                 statusText: statusText, mergeMessages: mergeMessages };
  if (window.__NO_BOOT__) return;

  // ------------------------------------------------------------------ api
  var CSRF = (document.querySelector('meta[name="csrf"]') || {}).content || '';
  function request(url, opts) {
    opts = opts || {}; opts.credentials = 'same-origin';
    return fetch(url, opts).then(function (r) {
      if (r.status === 401) { location.href = '/admin'; throw new Error('login required'); }
      return r.json().catch(function () { return null; }).then(function (d) {
        if (!r.ok) { var e = new Error((d && d.error) || ('HTTP ' + r.status)); e.status = r.status; throw e; }
        return d;
      });
    });
  }
  function get(op, p) { return request('/admin/api/tg/' + op + '?' + new URLSearchParams(p || {}).toString()); }
  function post(op, body) {
    return request('/admin/api/tg/' + op, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF }, body: JSON.stringify(body || {}) });
  }
  function upload(kind, fd) { return request('/admin/api/upload/' + kind, { method: 'POST', headers: { 'X-CSRF-Token': CSRF }, body: fd }); }
  function mediaUrl(m, thumb, dl) {
    return '/admin/api/media?chat_id=' + encodeURIComponent(m.chat_id) + '&id=' + m.id + (thumb ? '&thumb=1' : '') + (dl ? '&dl=1' : '');
  }

  // ------------------------------------------------------------------ state + prefs
  var PREFS_KEY = 'tgdash.prefs';
  var prefs = { theme: 'auto', fs: 'm', enterSend: true, readOnOpen: false };
  try { Object.assign(prefs, JSON.parse(localStorage.getItem(PREFS_KEY) || '{}')); } catch (e) { /* ignore */ }
  function savePrefs() { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) { /* ignore */ } applyPrefs(); }
  function applyPrefs() {
    var dark = prefs.theme === 'dark' || (prefs.theme === 'auto' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-fs', prefs.fs);
  }
  var FILTERS = [['all', 'All'], ['personal', 'Personal'], ['bots', 'Bots'], ['groups', 'Groups'], ['channels', 'Channels'], ['blocked', 'Blocked'], ['deleted', 'Deleted']];
  var S = { me: null, filter: 'all', q: '', dialogs: [], counts: {}, next: null, loading: false, found: [], chat: null, info: null, msgs: [], hasMore: false,
            reply: null, edit: null, aiChat: null };
  var el = {};

  // ------------------------------------------------------------------ ui primitives
  function toast(msg, isErr) {
    var t = h('div', { class: 'toast' + (isErr ? ' err' : ''), text: msg });
    document.getElementById('toasts').appendChild(t);
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, isErr ? 6000 : 2800);
  }
  function fail(e) { toast(e && e.message ? e.message : 'Error', true); }
  function modal(build) {
    var close = function () { if (ov.parentNode) ov.parentNode.removeChild(ov); };
    var box = h('div', { class: 'dialog' });
    var ov = h('div', { class: 'overlay', onclick: function (ev) { if (ev.target === ov) close(); } }, box);
    build(box, close); document.body.appendChild(ov); return close;
  }
  function ask(title, text, okLabel, danger) {
    return new Promise(function (resolve) {
      modal(function (box, close) {
        box.appendChild(h('h3', { text: title }));
        if (text) box.appendChild(h('div', { class: 'mute', text: text }));
        box.appendChild(h('div', { class: 'actions' },
          h('button', { class: 'btn', text: 'Cancel', onclick: function () { close(); resolve(false); } }),
          h('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), text: okLabel || 'OK', onclick: function () { close(); resolve(true); } })));
      });
    });
  }
  function askText(title, label, value, okLabel) {
    return new Promise(function (resolve) {
      modal(function (box, close) {
        var input = h('input', { value: value || '', placeholder: label });
        box.appendChild(h('h3', { text: title }));
        box.appendChild(h('div', { class: 'field' }, input));
        box.appendChild(h('div', { class: 'actions' },
          h('button', { class: 'btn', text: 'Cancel', onclick: function () { close(); resolve(null); } }),
          h('button', { class: 'btn primary', text: okLabel || 'OK', onclick: function () { close(); resolve(input.value); } })));
        setTimeout(function () { input.focus(); }, 30);
        input.onkeydown = function (e) { if (e.key === 'Enter') { close(); resolve(input.value); } };
      });
    });
  }
  function showMenu(x, y, items) {
    var menu = h('div', { class: 'menu' });
    function close() { if (menu.parentNode) menu.parentNode.removeChild(menu); document.removeEventListener('mousedown', outside, true); }
    function outside(e) { if (!menu.contains(e.target)) close(); }
    items.forEach(function (it) {
      if (it === '-') return menu.appendChild(h('hr'));
      menu.appendChild(h('button', { class: it.danger ? 'danger' : '', text: it.label, onclick: function () { close(); it.fn(); } }));
    });
    document.body.appendChild(menu);
    var w = menu.offsetWidth || 200, hh = menu.offsetHeight || 200;
    menu.style.left = Math.max(6, Math.min(x, window.innerWidth - w - 6)) + 'px';
    menu.style.top = Math.max(6, Math.min(y, window.innerHeight - hh - 6)) + 'px';
    setTimeout(function () { document.addEventListener('mousedown', outside, true); }, 0);
  }
  function menuAt(ev, items) { var r = ev.currentTarget.getBoundingClientRect(); showMenu(ev.clientX || r.left, ev.clientY || r.bottom, items); }

  function avatar(id, name, hasPhoto, cls) {
    var a = h('div', { class: 'avatar ' + (cls || '') }, h('span', { text: initials(name) }));
    a.style.background = colorFor(id);
    if (hasPhoto) {
      var img = h('img', { src: '/admin/api/avatar?id=' + encodeURIComponent(id), loading: 'lazy' });
      img.onerror = function () { if (img.parentNode) img.parentNode.removeChild(img); };
      a.appendChild(img);
    }
    return a;
  }
  function field(label, input, hint) { return h('div', { class: 'field' }, h('label', { text: label }), input, hint || null); }
  function toggle(on, fn) {
    var sw = h('button', { class: 'sw' + (on ? ' on' : ''), role: 'switch', 'aria-checked': on ? 'true' : 'false' });
    sw.onclick = function () { var v = !sw.classList.contains('on'); sw.classList.toggle('on', v); sw.setAttribute('aria-checked', v ? 'true' : 'false'); fn(v, sw); };
    return sw;
  }
  function select(options, value, fn) {
    var s = h('select');
    options.forEach(function (o) { var opt = h('option', { value: String(o[0]), text: o[1] }); if (String(o[0]) === String(value)) opt.selected = true; s.appendChild(opt); });
    s.onchange = function () { fn(s.value, s); };
    return s;
  }

  // ------------------------------------------------------------------ layout
  function buildApp() {
    var app = clear(document.getElementById('app'));
    el.root = h('div', { class: 'app' });
    el.search = h('input', { class: 'search', placeholder: 'Search', type: 'search', autocomplete: 'off' });
    el.search.oninput = debounce(function () { S.q = el.search.value.trim(); loadDialogs(true); }, 300);
    el.tabs = h('div', { class: 'tabs' });
    el.list = h('div', { class: 'list' });
    el.list.onscroll = function () { if (S.next !== null && !S.loading && el.list.scrollTop + el.list.clientHeight > el.list.scrollHeight - 200) loadDialogs(false); };
    el.side = h('div', { class: 'side' },
      h('div', { class: 'topbar' }, h('button', { class: 'iconbtn', title: 'Menu', text: '☰', onclick: openDrawer }), el.search),
      el.tabs, el.list);
    el.main = h('div', { class: 'main' }, h('div', { class: 'empty-main', text: 'Select a chat to start messaging' }));
    el.root.appendChild(el.side); el.root.appendChild(el.main); app.appendChild(el.root);
    renderTabs();
  }
  function renderTabs() {
    clear(el.tabs);
    FILTERS.forEach(function (f) {
      var c = S.counts[f[0]];
      el.tabs.appendChild(h('button', { class: 'tab' + (S.filter === f[0] ? ' active' : ''), onclick: function () { S.filter = f[0]; renderTabs(); loadDialogs(true); } },
        f[1], (c ? h('span', { class: 'cnt', text: String(c) }) : null)));
    });
  }

  // ------------------------------------------------------------------ chat list
  function loadDialogs(reset) {
    if (S.loading) { if (reset) S.pendingReset = true; return Promise.resolve(); }
    S.loading = true;
    var offset = reset ? 0 : (S.next || 0);
    return get('dialogs', { filter: S.filter, q: S.q, offset: offset, limit: 60 }).then(function (d) {
      S.dialogs = reset ? d.items : S.dialogs.concat(d.items);
      S.next = d.next; S.counts = d.counts || S.counts;
      renderTabs();
      if (reset && S.q.length >= 3) return get('find', { q: S.q }).then(function (f) { S.found = f.items || []; }).catch(function () { S.found = []; });
      if (reset) S.found = [];
    }).catch(fail).then(function () {
      S.loading = false; renderDialogs();
      if (S.pendingReset) { S.pendingReset = false; loadDialogs(true); }
    });
  }
  function dialogRow(d) {
    var active = S.chat && S.chat.id === d.id;
    var last = d.last || {};
    var prefix = last.out ? 'You: ' : ((d.type === 'group' || d.type === 'supergroup') && last.from ? last.from.split(' ')[0] + ': ' : '');
    var tags = [];
    if (d.type === 'bot') tags.push(h('span', { class: 'tag', text: 'bot' }));
    if (d.blocked) tags.push(h('span', { class: 'tag red', text: 'blocked' }));
    if (d.deleted) tags.push(h('span', { class: 'tag red', text: 'deleted' }));
    var badge = d.unread ? h('span', { class: 'badge', text: String(d.unread) }) : (d.unread_mark ? h('span', { class: 'badge', text: '•' }) : (d.pinned ? h('span', { class: 'mute', text: '📌' }) : null));
    var row = h('div', { class: 'row' + (active ? ' active' : '') },
      avatar(d.id, d.title, d.avatar),
      h('div', { class: 'rbody' },
        h('div', { class: 'rtop' }, h('span', { class: 'name', text: (d.saved ? '🔖 ' : '') + d.title + (d.verified ? ' ✓' : '') }), tags, h('span', { class: 'time', text: fmtTime(last.date) })),
        h('div', { class: 'rbot' }, h('span', { class: 'prev', text: prefix + (last.text || (d.username ? '@' + d.username : '')) }), badge)));
    row.onclick = function () { openChat(d); };
    row.oncontextmenu = function (e) { e.preventDefault(); showMenu(e.clientX, e.clientY, chatMenuItems(d)); };
    return row;
  }
  function renderDialogs() {
    clear(el.list);
    if (!S.dialogs.length && !S.found.length) el.list.appendChild(h('div', { class: 'empty', text: S.loading ? 'Loading…' : (S.q ? 'Nothing found' : 'No chats here') }));
    S.dialogs.forEach(function (d) { el.list.appendChild(dialogRow(d)); });
    if (S.found.length) {
      el.list.appendChild(h('div', { class: 'empty', text: 'Global search' }));
      S.found.forEach(function (f) {
        var row = h('div', { class: 'row' }, avatar(f.id, f.title, false), h('div', { class: 'rbody' },
          h('div', { class: 'name', text: f.title }), h('div', { class: 'prev', text: f.username ? '@' + f.username : f.type })));
        row.onclick = function () { openChat({ id: f.id, type: f.type, title: f.title, username: f.username, avatar: false, last: {} }); };
        el.list.appendChild(row);
      });
    }
    if (S.next !== null) el.list.appendChild(h('button', { class: 'more', text: 'Load more', onclick: function () { loadDialogs(false); } }));
  }

  // ------------------------------------------------------------------ drawer (menu)
  function openDrawer() {
    var me = S.me || {};
    var close = function () { if (dr.parentNode) dr.parentNode.removeChild(dr); };
    function item(icon, label, fn) { return h('button', { class: 'ditem', onclick: function () { close(); fn(); } }, h('span', { class: 'ic', text: icon }), h('span', { text: label })); }
    var name = ((me.first_name || '') + ' ' + (me.last_name || '')).trim() || 'Me';
    var dr = h('div', { class: 'drawer' },
      h('div', { class: 'sheet' },
        h('div', { class: 'dhead' }, avatar(me.id || 0, name, me.has_photo), h('div', { class: 'name', text: name }),
          h('div', { text: [me.phone ? '+' + String(me.phone).replace(/^\+/, '') : '', me.username ? '@' + me.username : ''].filter(Boolean).join('  ·  ') })),
        item('👤', 'My Profile', function () { openSettings('profile'); }),
        item('👥', 'Contacts', function () { openSettings('contacts'); }),
        item('🔖', 'Saved Messages', function () { openChat({ id: me.id, type: 'private', title: 'Saved Messages', saved: true, last: {} }); }),
        item('⚙️', 'Settings', function () { openSettings('notifications'); }),
        item('🤖', 'AI bot (' + ((window.__info && window.__info.persona) || 'Lisa') + ')', function () { openSettings('bot'); }),
        item('📜', 'AI chat log', function () { openSettings('ailog'); }),
        item('🌓', 'Night mode', function () { prefs.theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'; savePrefs(); }),
        item('🚪', 'Log out of dashboard', function () { fetch('/admin/logout', { method: 'POST', credentials: 'same-origin' }).then(function () { location.href = '/admin'; }); })),
      h('div', { class: 'scrim', onclick: close }));
    el.side.appendChild(dr);
  }

  // ------------------------------------------------------------------ chat view
  function chatMenuItems(d) {
    var items = [
      { label: 'Info', fn: function () { showInfo(d); } },
      { label: 'Mark as read', fn: function () { act(d, 'read'); } },
      { label: 'Mark as unread', fn: function () { act(d, 'mark_unread'); } },
      { label: d.pinned ? 'Unpin' : 'Pin', fn: function () { act(d, d.pinned ? 'unpin' : 'pin'); } },
      { label: 'Mute', fn: function () { act(d, 'mute'); } },
      { label: 'Unmute', fn: function () { act(d, 'unmute'); } },
      { label: 'Archive', fn: function () { act(d, 'archive'); } }
    ];
    if (d.type === 'private' || d.type === 'bot') items.push({ label: d.blocked ? 'Unblock' : 'Block', fn: function () { act(d, d.blocked ? 'unblock' : 'block'); } });
    items.push('-');
    items.push({ label: 'Clear history', danger: true, fn: function () { danger(d, 'clear', 'Clear history?', 'Is chat ke saare messages sirf tumhare side se hat jayenge.'); } });
    if (d.type === 'group' || d.type === 'supergroup' || d.type === 'channel') items.push({ label: 'Leave', danger: true, fn: function () { danger(d, 'leave', 'Leave this chat?', d.title); } });
    else items.push({ label: 'Delete chat', danger: true, fn: function () { danger(d, 'delete', 'Delete chat?', 'Pura chat tumhare account se delete ho jayega.'); } });
    return items;
  }
  function act(d, action, extra) {
    var body = Object.assign({ chat_id: d.id, action: action }, extra || {});
    if (action === 'read') return post('read', { chat_id: d.id }).then(function () { toast('Marked as read'); loadDialogs(true); }).catch(fail);
    return post('chat_action', body).then(function () {
      toast('Done');
      if (action === 'block' || action === 'unblock') d.blocked = action === 'block';
      if (action === 'pin' || action === 'unpin') d.pinned = action === 'pin';
      if (S.chat && S.chat.id === d.id && (action === 'clear' || action === 'delete' || action === 'leave')) { closeChat(); }
      loadDialogs(true);
    }).catch(fail);
  }
  function danger(d, action, title, text) {
    ask(title, text, 'Yes, do it', true).then(function (ok) { if (ok) act(d, action, { confirm: true, revoke: false }); });
  }
  function showInfo(d) {
    get('chat', { chat_id: d.id }).then(function (c) {
      modal(function (box, close) {
        box.appendChild(h('div', { style: 'display:none' }));
        box.appendChild(h('div', { class: 'line' }, avatar(c.id, c.title, c.avatar, 'lg'), h('div', { class: 'grow' },
          h('h3', { text: c.title }), h('div', { class: 'mute', text: c.username ? '@' + c.username : c.type }))));
        [['ID', String(c.id)], ['Type', c.type], ['Bio', c.bio], ['Description', c.description], ['Members', c.members], ['Status', statusText(c.status)],
         ['Muted', c.muted === null ? '' : (c.muted ? 'yes' : 'no')], ['Contact', c.contact ? 'yes' : ''], ['Blocked', c.blocked ? 'yes' : '']].forEach(function (r) {
          if (r[1] !== '' && r[1] !== null && r[1] !== undefined) box.appendChild(h('div', { class: 'line' }, h('span', { class: 'mute', text: r[0] }), h('span', { text: String(r[1]) })));
        });
        box.appendChild(h('div', { class: 'actions' }, h('button', { class: 'btn', text: 'Close', onclick: close })));
      });
    }).catch(fail);
  }
  function closeChat() {
    S.chat = null; S.msgs = []; S.reply = null; S.edit = null;
    clear(el.main).appendChild(h('div', { class: 'empty-main', text: 'Select a chat to start messaging' }));
    el.root.classList.remove('chat-open'); renderDialogs();
  }
  function openChat(d) {
    S.chat = d; S.msgs = []; S.reply = null; S.edit = null; S.hasMore = false; S.aiChat = null;
    el.root.classList.add('chat-open'); renderDialogs();
    el.head = h('div', { class: 'chead' });
    el.msgs = h('div', { class: 'msgs' });
    el.msgs.onscroll = function () { if (el.msgs.scrollTop < 60 && S.hasMore && !S.loadingOlder) loadOlder(); };
    el.composer = h('div', { class: 'composer' });
    clear(el.main).appendChild(el.head); el.main.appendChild(el.msgs); el.main.appendChild(el.composer);
    renderHead(); renderComposer();
    el.msgs.appendChild(h('div', { class: 'spin', text: 'Loading…' }));
    var id = d.id;
    get('history', { chat_id: id, limit: 40 }).then(function (r) {
      if (!S.chat || S.chat.id !== id) return;
      S.msgs = r.messages; S.hasMore = r.has_more; renderMessages(true);
      if (prefs.readOnOpen && d.unread) post('read', { chat_id: id }).catch(function () {});
    }).catch(function (e) { clear(el.msgs).appendChild(h('div', { class: 'empty', text: e.message })); });
    get('chat', { chat_id: id }).then(function (c) { if (S.chat && S.chat.id === id) { S.info = c; Object.assign(S.chat, { blocked: c.blocked, title: c.title }); renderHead(); } }).catch(function () {});
    get('ai', { chat_id: id }).then(function (a) { if (S.chat && S.chat.id === id) { S.aiChat = a.chat; S.aiGlobal = a.global; window.__info = a.info; renderHead(); } }).catch(function () {});
  }
  function renderHead() {
    var d = S.chat, i = S.info && S.info.id === d.id ? S.info : null;
    var sub = i ? (i.type === 'private' || i.type === 'bot' ? (i.deleted ? 'deleted account' : (i.type === 'bot' ? 'bot' : statusText(i.status))) : (i.members ? i.members + ' members' : i.type)) : '';
    if (i && i.muted) sub += (sub ? ' · ' : '') + 'muted';
    var chip = null;
    if (S.aiChat !== null && S.aiChat !== undefined) {
      chip = h('button', { class: 'chip' + (S.aiChat && S.aiGlobal !== false ? ' on' : ''), title: 'AI replies in this chat', text: 'AI ' + (S.aiChat ? 'on' : 'off'),
        onclick: function () { var v = !S.aiChat; post('ai_chat', { chat_id: d.id, enabled: v }).then(function () { S.aiChat = v; renderHead(); toast('AI ' + (v ? 'on' : 'off') + ' in this chat'); }).catch(fail); } });
    }
    clear(el.head).appendChild(h('button', { class: 'iconbtn back', text: '←', onclick: closeChat }));
    el.head.appendChild(avatar(d.id, d.title, d.avatar, 'sm'));
    el.head.appendChild(h('div', { class: 'grow', onclick: function () { showInfo(d); } }, h('div', { class: 'name', text: d.title }), h('div', { class: 'sub', text: sub })));
    if (chip) el.head.appendChild(chip);
    el.head.appendChild(h('button', { class: 'iconbtn', title: 'Search in chat', text: '🔍', onclick: searchInChat }));
    el.head.appendChild(h('button', { class: 'iconbtn', title: 'More', text: '⋮', onclick: function (e) { menuAt(e, chatMenuItems(d)); } }));
  }
  function searchInChat() {
    askText('Search in this chat', 'Text…', '', 'Search').then(function (q) {
      if (!q || !q.trim()) return;
      get('search', { chat_id: S.chat.id, q: q.trim() }).then(function (r) {
        modal(function (box, close) {
          box.appendChild(h('h3', { text: 'Results for “' + q.trim() + '”' }));
          if (!r.messages.length) box.appendChild(h('div', { class: 'empty', text: 'Nothing found' }));
          r.messages.forEach(function (m) {
            box.appendChild(h('div', { class: 'line' }, h('div', { class: 'grow' }, h('div', { class: 'mute', text: (m.from || '') + ' · ' + fmtFull(m.date) }), h('div', { text: m.text || m.kind || '' }))));
          });
          box.appendChild(h('div', { class: 'actions' }, h('button', { class: 'btn', text: 'Close', onclick: close })));
        });
      }).catch(fail);
    });
  }
  function loadOlder() {
    if (!S.msgs.length) return;
    S.loadingOlder = true; var id = S.chat.id, oldest = S.msgs[0].id;
    get('history', { chat_id: id, before: oldest, limit: 40 }).then(function (r) {
      if (!S.chat || S.chat.id !== id) return;
      var prevH = el.msgs.scrollHeight;
      S.msgs = mergeMessages(S.msgs, r.messages); S.hasMore = r.has_more && r.messages.length > 0; renderMessages(false);
      el.msgs.scrollTop = el.msgs.scrollHeight - prevH;
    }).catch(fail).then(function () { S.loadingOlder = false; });
  }
  function refreshTail() {
    if (!S.chat) return; var id = S.chat.id;
    get('history', { chat_id: id, limit: 30 }).then(function (r) {
      if (!S.chat || S.chat.id !== id) return;
      var nearBottom = el.msgs.scrollHeight - el.msgs.scrollTop - el.msgs.clientHeight < 140;
      S.msgs = mergeMessages(S.msgs, r.messages); renderMessages(nearBottom);
    }).catch(function () {});
  }
  var isGroupish = function () { return S.chat && (S.chat.type === 'group' || S.chat.type === 'supergroup' || S.chat.type === 'channel'); };

  function mediaNode(m) {
    var url = mediaUrl(m, false), thumb = mediaUrl(m, true), k = m.kind;
    if (k === 'photo') { var img = h('img', { class: 'media', src: mediaUrl(m, true), loading: 'lazy' }); img.onclick = function () { window.open(url, '_blank', 'noopener'); }; return img; }
    if (k === 'sticker') {
      if (m.animated) return h('div', { text: m.emoji || '🎴', style: 'font-size:64px' });
      return h('img', { class: 'media sticker', src: url, loading: 'lazy' });
    }
    if (k === 'video' || k === 'gif' || k === 'round video') {
      var wrap = h('div'); var poster = h('img', { class: 'media', src: thumb, loading: 'lazy' });
      poster.onerror = function () { clear(wrap).appendChild(h('div', { class: 'file' }, h('div', { class: 'ficon', text: '▶' }), h('div', {}, h('div', { text: m.file_name || k }), h('div', { class: 'mute', text: humanSize(m.size) })))); };
      poster.onclick = function () { clear(wrap).appendChild(h('video', { src: url, controls: 'controls', autoplay: 'autoplay', playsinline: 'playsinline' })); };
      wrap.appendChild(poster); return wrap;
    }
    if (k === 'voice' || k === 'audio') {
      var box = h('div'); var btn = h('button', { class: 'btn', text: '▶ ' + (k === 'voice' ? 'Voice message' : (m.title || m.file_name || 'Audio')) + (m.duration ? ' (' + m.duration + 's)' : '') });
      btn.onclick = function () { clear(box).appendChild(h('audio', { src: url, controls: 'controls', autoplay: 'autoplay' })); };
      box.appendChild(btn); return box;
    }
    if (k === 'file') {
      return h('a', { class: 'file', href: mediaUrl(m, false, true), style: 'text-decoration:none;color:inherit' }, h('div', { class: 'ficon', text: '⬇' }),
        h('div', {}, h('div', { text: m.file_name || 'File' }), h('div', { class: 'mute', text: humanSize(m.size) })));
    }
    if (k === 'contact' && m.contact) return h('div', {}, h('div', { text: '👤 ' + m.contact.name }), h('div', { class: 'mute', text: m.contact.phone || '' }));
    if ((k === 'location' || k === 'venue') && m.location) {
      return h('a', { href: 'https://www.openstreetmap.org/?mlat=' + m.location.lat + '&mlon=' + m.location.lon + '#map=16/' + m.location.lat + '/' + m.location.lon, target: '_blank', rel: 'noopener noreferrer',
        text: '📍 ' + (m.location.title || (m.location.lat + ', ' + m.location.lon)) });
    }
    if (k === 'poll' && m.poll) { var p = h('div', { class: 'poll' }, h('b', { text: '📊 ' + m.poll.question })); m.poll.options.forEach(function (o) { p.appendChild(h('div', { text: '◯ ' + o })); }); return p; }
    if (k === 'dice') return h('div', { text: (m.emoji || '🎲') + ' ' + (m.value || ''), style: 'font-size:40px' });
    return null;
  }
  function textNode(text) {
    var span = h('div', { class: 'txt' });
    linkify(text).forEach(function (p) {
      if (p.t === 'url') span.appendChild(h('a', { href: p.v, target: '_blank', rel: 'noopener noreferrer', text: p.v }));
      else span.appendChild(document.createTextNode(p.v));
    });
    return span;
  }
  function bubble(m) {
    if (m.service) return h('div', { class: 'b svc', text: m.text });
    var stk = m.kind === 'sticker';
    var b = h('div', { class: 'b' + (m.out ? ' out' : '') + (stk ? ' stk' : ''), 'data-id': String(m.id) });
    if (!m.out && isGroupish() && m.from) b.appendChild(h('div', { class: 'who', text: m.from }));
    if (m.fwd) b.appendChild(h('div', { class: 'fwd', text: '↪ Forwarded from ' + m.fwd }));
    if (m.reply_to) {
      var q = S.msgs.filter(function (x) { return x.id === m.reply_to; })[0];
      b.appendChild(h('div', { class: 'quote', text: q ? ((q.from || '') + ': ' + (q.text || q.kind || '')).slice(0, 90) : 'Reply to #' + m.reply_to,
        onclick: function (ev) { ev.stopPropagation(); var t = el.msgs.querySelector('[data-id="' + m.reply_to + '"]'); if (t) { t.scrollIntoView({ block: 'center' }); t.classList.add('hl'); setTimeout(function () { t.classList.remove('hl'); }, 1200); } } }));
    }
    var mn = mediaNode(m); if (mn) b.appendChild(mn);
    if (m.text && !(m.kind === 'sticker')) b.appendChild(textNode(m.text));
    b.appendChild(h('div', { class: 'meta', text: (m.edited ? 'edited ' : '') + (m.views ? '👁 ' + m.views + ' ' : '') + fmtClock(m.date) }));
    b.onclick = function (e) { if (e.target.tagName === 'A' || e.target.tagName === 'IMG' || e.target.tagName === 'VIDEO' || e.target.tagName === 'AUDIO' || e.target.tagName === 'BUTTON') return; menuAt(e, messageMenu(m)); };
    return b;
  }
  function messageMenu(m) {
    var items = [{ label: 'Reply', fn: function () { S.reply = m; S.edit = null; renderComposer(true); } }];
    if (m.text) items.push({ label: 'Copy text', fn: function () { if (navigator.clipboard) navigator.clipboard.writeText(m.text).then(function () { toast('Copied'); }); } });
    if (m.out && m.text && !m.kind) items.push({ label: 'Edit', fn: function () { S.edit = m; S.reply = null; renderComposer(true); } });
    if (m.kind && m.kind !== 'service' && ['photo', 'video', 'gif', 'file', 'audio', 'voice', 'sticker'].indexOf(m.kind) >= 0) items.push({ label: 'Download', fn: function () { window.open(mediaUrl(m, false, true), '_blank', 'noopener'); } });
    items.push({ label: 'Forward…', fn: function () { pickChat('Forward to…').then(function (to) { if (to) post('forward', { to_chat: to.id, from_chat: S.chat.id, ids: [m.id] }).then(function () { toast('Forwarded'); }).catch(fail); }); } });
    items.push('-');
    items.push({ label: 'Delete for me', danger: true, fn: function () { delMsg(m, false); } });
    if (m.out || (S.chat && S.chat.type === 'private')) items.push({ label: 'Delete for everyone', danger: true, fn: function () { delMsg(m, true); } });
    return items;
  }
  function delMsg(m, revoke) {
    ask('Delete message?', revoke ? 'Dono taraf se delete hoga.' : 'Sirf tumhare account se hatega.', 'Delete', true).then(function (ok) {
      if (!ok) return;
      post('delete', { chat_id: S.chat.id, ids: [m.id], revoke: revoke }).then(function () { S.msgs = S.msgs.filter(function (x) { return x.id !== m.id; }); renderMessages(false); loadDialogs(true); }).catch(fail);
    });
  }
  function pickChat(title) {
    return new Promise(function (resolve) {
      modal(function (box, close) {
        var list = h('div', { style: 'max-height:50vh;overflow:auto' }); var q = h('input', { placeholder: 'Search chats…' });
        function fill() { clear(list); S.dialogs.filter(function (d) { return !q.value || d.title.toLowerCase().indexOf(q.value.toLowerCase()) >= 0; }).slice(0, 60).forEach(function (d) {
          list.appendChild(h('div', { class: 'row', onclick: function () { close(); resolve(d); } }, avatar(d.id, d.title, d.avatar, 'sm'), h('span', { class: 'name', text: d.title }))); }); }
        q.oninput = fill; fill();
        box.appendChild(h('h3', { text: title })); box.appendChild(h('div', { class: 'field' }, q)); box.appendChild(list);
        box.appendChild(h('div', { class: 'actions' }, h('button', { class: 'btn', text: 'Cancel', onclick: function () { close(); resolve(null); } })));
      });
    });
  }
  function renderMessages(toBottom) {
    clear(el.msgs);
    if (!S.msgs.length) { el.msgs.appendChild(h('div', { class: 'empty', text: 'No messages yet' })); return; }
    var lastDay = '';
    S.msgs.forEach(function (m) {
      var day = fmtDay(m.date); if (day !== lastDay) { lastDay = day; el.msgs.appendChild(h('div', { class: 'dsep', text: day })); }
      el.msgs.appendChild(bubble(m));
    });
    if (toBottom) el.msgs.scrollTop = el.msgs.scrollHeight;
  }

  // ------------------------------------------------------------------ composer
  function renderComposer(focus) {
    clear(el.composer);
    var d = S.chat;
    if (d.type === 'channel' && d.admin === false) { el.composer.appendChild(h('div', { class: 'ro', text: 'Channel' })); }
    var ta = h('textarea', { rows: 1, placeholder: S.edit ? 'Edit message…' : 'Message' });
    var bar = S.reply || S.edit;
    if (bar) el.composer.appendChild(h('div', { class: 'cbar' }, h('div', { class: 'grow', text: (S.edit ? 'Editing: ' : 'Reply to ' + (bar.from || '') + ': ') + (bar.text || bar.kind || '') }),
      h('button', { class: 'iconbtn', text: '✕', onclick: function () { S.reply = null; S.edit = null; renderComposer(); } })));
    if (S.edit) ta.value = S.edit.text;
    var file = h('input', { type: 'file', class: 'hidden' });
    file.onchange = function () { if (file.files && file.files[0]) sendFile(file.files[0], ta.value); file.value = ''; };
    var sendBtn = h('button', { class: 'iconbtn send', title: 'Send', text: '➤' });
    function grow() { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'; }
    ta.oninput = grow;
    ta.onkeydown = function (e) { if (e.key === 'Enter' && !e.shiftKey && prefs.enterSend) { e.preventDefault(); doSend(); } };
    function doSend() {
      var text = ta.value; if (!text.trim()) return;
      sendBtn.disabled = true;
      var p = S.edit ? post('edit', { chat_id: d.id, message_id: S.edit.id, text: text }) : post('send', { chat_id: d.id, text: text, reply_to: S.reply ? S.reply.id : null });
      p.then(function (r) {
        S.msgs = mergeMessages(S.msgs, [r.message]); S.reply = null; S.edit = null; renderComposer(true); renderMessages(true); loadDialogs(true);
      }).catch(function (e) { fail(e); sendBtn.disabled = false; });
    }
    sendBtn.onclick = doSend;
    el.composer.appendChild(h('div', { class: 'crow' }, h('button', { class: 'iconbtn', title: 'Attach', text: '📎', onclick: function () { file.click(); } }), file, ta, sendBtn));
    if (focus) setTimeout(function () { ta.focus(); grow(); }, 30);
  }
  function sendFile(f, caption) {
    var fd = new FormData(); fd.append('file', f, f.name); fd.append('chat_id', String(S.chat.id)); fd.append('caption', caption || '');
    fd.append('as_photo', /^image\/(jpeg|png|webp)$/.test(f.type) ? '1' : '0'); if (S.reply) fd.append('reply_to', String(S.reply.id));
    toast('Uploading…');
    upload('file', fd).then(function (r) { S.msgs = mergeMessages(S.msgs, [r.message]); S.reply = null; renderComposer(); renderMessages(true); loadDialogs(true); }).catch(fail);
  }

  // ------------------------------------------------------------------ settings
  var SECTIONS = [['profile', '👤', 'Edit profile'], ['notifications', '🔔', 'Notifications'], ['privacy', '🔒', 'Privacy and security'], ['chatset', '💬', 'Chat settings'],
                  ['contacts', '👥', 'Contacts'], ['bot', '🤖', 'AI bot'], ['ailog', '📜', 'AI chat log']];
  function openSettings(section) {
    var body = h('div', { class: 'sbody' });
    var nav = h('div', { class: 'snav' });
    var sheet = h('div', { class: 'sheetfull' }, nav, body);
    var close = function () { if (sheet.parentNode) sheet.parentNode.removeChild(sheet); };
    function go(key) {
      sheet.classList.add('in-section');
      Array.prototype.forEach.call(nav.querySelectorAll('.ditem'), function (n) { n.classList.toggle('active', n.getAttribute('data-k') === key); });
      clear(body).appendChild(h('div', { class: 'spin', text: 'Loading…' }));
      var inner = h('div', { class: 'sinner' });
      inner.appendChild(h('button', { class: 'iconbtn back', text: '←', onclick: function () { sheet.classList.remove('in-section'); } }));
      Promise.resolve(RENDER[key](inner, close)).then(function () { clear(body).appendChild(inner); }).catch(function (e) { clear(body).appendChild(h('div', { class: 'empty', text: e.message })); });
    }
    nav.appendChild(h('div', { class: 'topbar' }, h('button', { class: 'iconbtn', text: '←', onclick: close }), h('b', { text: 'Settings' })));
    SECTIONS.forEach(function (s) { nav.appendChild(h('button', { class: 'ditem', 'data-k': s[0], onclick: function () { go(s[0]); } }, h('span', { class: 'ic', text: s[1] }), h('span', { text: s[2] }))); });
    document.body.appendChild(sheet); go(section);
  }
  function card(title) { var c = h('div', { class: 'card' }); if (title) c.appendChild(h('h4', { text: title })); return c; }
  function reloadMe() { return get('me').then(function (m) { S.me = m; return m; }); }

  var RENDER = {};
  RENDER.profile = function (root) {
    return reloadMe().then(function (me) {
      root.appendChild(h('h2', { text: 'Edit profile' }));
      var photoCard = card();
      var fileIn = h('input', { type: 'file', accept: 'image/*', class: 'hidden' });
      fileIn.onchange = function () { if (!fileIn.files[0]) return; var fd = new FormData(); fd.append('file', fileIn.files[0], fileIn.files[0].name); toast('Uploading…'); upload('photo', fd).then(function (m) { S.me = m; toast('Photo updated'); }).catch(fail); };
      photoCard.appendChild(h('div', { class: 'line' }, avatar(me.id, me.first_name, me.has_photo, 'lg'), h('div', { class: 'grow' }),
        h('button', { class: 'btn primary', text: 'Set photo', onclick: function () { fileIn.click(); } }), fileIn,
        h('button', { class: 'btn danger', text: 'Remove', onclick: function () { ask('Remove profile photo?', '', 'Remove', true).then(function (ok) { if (ok) post('photo_delete').then(function (m) { S.me = m; toast('Photo removed'); }).catch(fail); }); } })));
      root.appendChild(photoCard);

      var first = h('input', { value: me.first_name, maxLength: 64 }), last = h('input', { value: me.last_name, maxLength: 64 });
      var bio = h('textarea', { rows: 3, maxLength: 140 }); bio.value = me.bio;
      var count = h('span', { class: 'hint', text: bio.value.length + '/70 (Premium 140)' }); bio.oninput = function () { count.textContent = bio.value.length + '/70 (Premium 140)'; };
      var c1 = card('Name and bio');
      c1.appendChild(field('First name', first)); c1.appendChild(field('Last name', last)); c1.appendChild(field('Bio', bio, count));
      c1.appendChild(h('button', { class: 'btn primary', text: 'Save', onclick: function () {
        post('profile', { first_name: first.value, last_name: last.value, bio: bio.value }).then(function (m) { S.me = m; toast('Profile saved'); }).catch(fail); } }));
      root.appendChild(c1);

      var uname = h('input', { value: me.username, placeholder: 'username', maxLength: 32 }); var uh = h('div', { class: 'hint', text: 'a-z, 0-9 and _ , minimum 5 characters. People can find you as t.me/username' });
      uname.oninput = debounce(function () {
        var v = uname.value.trim().replace(/^@/, ''); if (v === me.username) { uh.className = 'hint'; uh.textContent = 'Current username'; return; }
        get('username_check', { username: v }).then(function (r) { uh.className = 'hint ' + (r.available ? 'ok' : 'bad'); uh.textContent = r.empty ? 'Username will be removed' : (r.available ? v + ' is available' : (r.reason || v + ' is not available')); }).catch(function (e) { uh.className = 'hint bad'; uh.textContent = e.message; });
      }, 500);
      var c2 = card('Username'); c2.appendChild(field('Username', uname, uh));
      c2.appendChild(h('button', { class: 'btn primary', text: 'Save username', onclick: function () { post('username', { username: uname.value }).then(function (m) { S.me = m; toast('Username saved'); }).catch(fail); } }));
      root.appendChild(c2);

      var c3 = card('Phone number'); var body = h('div'); c3.appendChild(body);
      function phoneStart() {
        clear(body).appendChild(h('div', { class: 'line' }, h('div', { class: 'grow' }, h('div', { text: me.phone ? '+' + String(me.phone).replace(/^\+/, '') : 'No number' }), h('div', { class: 'hint', text: 'Telegram naye number pe code bhejega' })),
          h('button', { class: 'btn primary', text: 'Change number', onclick: phoneAsk })));
      }
      function phoneAsk() {
        var ph = h('input', { placeholder: '+91 98765 43210', type: 'tel' });
        clear(body).appendChild(field('New phone number (country code ke saath)', ph));
        body.appendChild(h('button', { class: 'btn primary', text: 'Send code', onclick: function () {
          post('phone_request', { phone: ph.value }).then(function (r) { phoneCode(r.phone); }).catch(fail); } }));
        body.appendChild(h('button', { class: 'btn', text: 'Cancel', onclick: phoneStart }));
      }
      function phoneCode(phone) {
        var code = h('input', { placeholder: 'Code', autocomplete: 'one-time-code' });
        clear(body).appendChild(field('Code sent to ' + phone, code));
        body.appendChild(h('button', { class: 'btn primary', text: 'Confirm', onclick: function () {
          ask('Change phone number?', 'Account ka number ' + phone + ' ho jayega.', 'Change', true).then(function (ok) { if (!ok) return;
            post('phone_confirm', { phone: phone, code: code.value }).then(function (m) { S.me = m; me.phone = m.phone; toast('Number changed'); phoneStart(); }).catch(fail); }); } }));
        body.appendChild(h('button', { class: 'btn', text: 'Cancel', onclick: phoneStart }));
      }
      phoneStart(); root.appendChild(c3);
    });
  };

  RENDER.notifications = function (root) {
    return get('notify').then(function (d) {
      root.appendChild(h('h2', { text: 'Notifications' }));
      var names = { private: 'Private chats', groups: 'Groups', channels: 'Channels' };
      var c = card('Default settings for all chats');
      d.items.forEach(function (it) {
        var state = { muted: it.muted, previews: it.previews };
        function save() { post('notify', { scope: it.scope, muted: state.muted, previews: state.previews }).then(function () { toast('Saved'); }).catch(fail); }
        c.appendChild(h('div', { class: 'line' }, h('div', { class: 'grow' }, h('b', { text: names[it.scope] || it.scope }), h('div', { class: 'hint', text: 'Notifications' })), toggle(!it.muted, function (v) { state.muted = !v; save(); })));
        c.appendChild(h('div', { class: 'line' }, h('div', { class: 'grow hint', text: 'Message preview' }), toggle(it.previews, function (v) { state.previews = v; save(); })));
      });
      root.appendChild(c);
      root.appendChild(h('div', { class: 'hint', text: 'Kisi ek chat ko mute karne ke liye chat ke ⋮ menu me Mute/Unmute use karo.' }));
    });
  };

  RENDER.privacy = function (root) {
    return Promise.all([get('privacy'), get('blocked'), get('sessions'), get('ttl')]).then(function (res) {
      var priv = res[0], blocked = res[1], sess = res[2], ttl = res[3];
      root.appendChild(h('h2', { text: 'Privacy and security' }));
      var c = card('Who can see or do what');
      var labels = { everybody: 'Everybody', contacts: 'My Contacts', nobody: 'Nobody' };
      priv.items.forEach(function (it) {
        var sel = select(priv.options.map(function (o) { return [o, labels[o]]; }), it.value, function (v, s) {
          function send(force) {
            return post('privacy', { key: it.key, value: v, force: force }).then(function () { it.value = v; toast('Saved'); }).catch(function (e) {
              if (e.status === 409) return ask('Exceptions will be removed', e.message, 'Change', true).then(function (ok) { if (ok) return send(true); s.value = it.value; });
              s.value = it.value; fail(e);
            });
          }
          send(false);
        });
        c.appendChild(h('div', { class: 'line' }, h('div', { class: 'grow' }, h('div', { text: it.label }), it.exceptions ? h('div', { class: 'hint', text: it.exceptions + ' exceptions' }) : null), sel));
      });
      root.appendChild(c);

      var b = card('Blocked users (' + blocked.count + ')'); var bl = h('div'); b.appendChild(bl);
      function drawBlocked(items) {
        clear(bl); if (!items.length) bl.appendChild(h('div', { class: 'hint', text: 'No blocked users' }));
        items.forEach(function (u) { bl.appendChild(h('div', { class: 'line' }, avatar(u.id, u.name, false, 'sm'), h('div', { class: 'grow' }, h('div', { text: u.deleted ? 'Deleted Account' : u.name }), h('div', { class: 'hint', text: u.username ? '@' + u.username : String(u.id) })),
          h('button', { class: 'btn', text: 'Unblock', onclick: function () { post('unblock', { user_id: u.id }).then(function () { drawBlocked(items.filter(function (x) { return x.id !== u.id; })); loadDialogs(true); }).catch(fail); } }))); });
      }
      drawBlocked(blocked.items);
      var bu = h('input', { placeholder: '@username or user id' });
      b.appendChild(h('div', { class: 'line' }, bu, h('button', { class: 'btn danger', text: 'Block', onclick: function () { if (!bu.value.trim()) return;
        post('block', { user_id: bu.value.trim() }).then(function () { toast('Blocked'); bu.value = ''; get('blocked').then(function (r) { drawBlocked(r.items); }); loadDialogs(true); }).catch(fail); } })));
      root.appendChild(b);

      var d = card('Devices (active sessions)'); var sl = h('div'); d.appendChild(sl);
      function drawSessions(items) {
        clear(sl);
        items.forEach(function (s) {
          sl.appendChild(h('div', { class: 'line' }, h('div', { class: 'grow' }, h('b', { text: (s.device || s.platform || 'Device') + (s.current ? ' (this bot)' : '') }),
            h('div', { class: 'hint', text: [s.app, s.system, s.ip, s.country].filter(Boolean).join(' · ') }), h('div', { class: 'hint', text: 'Last active: ' + fmtFull(s.active) })),
            s.current ? null : h('button', { class: 'btn danger', text: 'End', onclick: function () { ask('End this session?', s.device + ' ' + s.ip, 'End session', true).then(function (ok) { if (ok)
              post('session_end', { hash: s.hash, confirm: true }).then(function () { return get('sessions'); }).then(function (r) { drawSessions(r.items); toast('Session ended'); }).catch(fail); }); } })));
        });
        if (items.some(function (s) { return !s.current; })) sl.appendChild(h('button', { class: 'btn danger', text: 'Terminate all other sessions', onclick: function () { ask('Terminate all other sessions?', 'Baaki sab devices logout ho jayenge.', 'Terminate', true).then(function (ok) { if (ok)
          post('sessions_end_others', { confirm: true }).then(function () { return get('sessions'); }).then(function (r) { drawSessions(r.items); toast('Done'); }).catch(fail); }); } }));
      }
      drawSessions(sess.items); root.appendChild(d);

      var t = card('Delete my account if away for');
      var tl = { 30: '1 month', 90: '3 months', 180: '6 months', 365: '12 months' };
      t.appendChild(field('Account self-destruct', select(ttl.options.map(function (o) { return [o, tl[o]]; }), ttl.days, function (v) { post('ttl', { days: parseInt(v, 10) }).then(function () { toast('Saved'); }).catch(fail); })));
      root.appendChild(t);
    });
  };

  RENDER.chatset = function (root) {
    return get('history_ttl').then(function (ht) {
      root.appendChild(h('h2', { text: 'Chat settings' }));
      var c = card('Appearance (this browser)');
      c.appendChild(field('Theme', select([['auto', 'System'], ['light', 'Light'], ['dark', 'Dark']], prefs.theme, function (v) { prefs.theme = v; savePrefs(); })));
      c.appendChild(field('Text size', select([['s', 'Small'], ['m', 'Medium'], ['l', 'Large']], prefs.fs, function (v) { prefs.fs = v; savePrefs(); })));
      c.appendChild(h('div', { class: 'line' }, h('div', { class: 'grow', text: 'Send with Enter (Shift+Enter = new line)' }), toggle(prefs.enterSend, function (v) { prefs.enterSend = v; savePrefs(); })));
      c.appendChild(h('div', { class: 'line' }, h('div', { class: 'grow' }, h('div', { text: 'Mark chat as read when I open it' }), h('div', { class: 'hint', text: 'On karoge toh dusre ko blue tick/read dikhega' })),
        toggle(prefs.readOnOpen, function (v) { prefs.readOnOpen = v; savePrefs(); })));
      root.appendChild(c);
      var t = card('Auto-delete messages');
      var lab = { 0: 'Off', 86400: '1 day', 604800: '1 week', 2678400: '1 month' };
      t.appendChild(field('Default timer for new chats', select(ht.options.map(function (o) { return [o, lab[o]]; }), ht.period, function (v) { post('history_ttl', { period: parseInt(v, 10) }).then(function () { toast('Saved'); }).catch(fail); })));
      root.appendChild(t);
    });
  };

  RENDER.contacts = function (root) {
    root.appendChild(h('h2', { text: 'Contacts' }));
    var q = h('input', { class: 'search', placeholder: 'Search contacts', type: 'search' }); var listBox = h('div');
    function draw() {
      return get('contacts', { q: q.value }).then(function (r) {
        clear(listBox); if (!r.items.length) listBox.appendChild(h('div', { class: 'empty', text: 'No contacts' }));
        r.items.forEach(function (u) {
          listBox.appendChild(h('div', { class: 'line' }, avatar(u.id, u.name, true, 'sm'), h('div', { class: 'grow' }, h('div', { text: u.name }), h('div', { class: 'hint', text: [u.username ? '@' + u.username : '', u.phone ? '+' + u.phone : '', statusText(u.status)].filter(Boolean).join(' · ') })),
            h('button', { class: 'btn', text: 'Message', onclick: function () { document.querySelector('.sheetfull').remove(); openChat({ id: u.id, type: 'private', title: u.name, username: u.username, avatar: true, last: {} }); } }),
            h('button', { class: 'btn danger', text: 'Delete', onclick: function () { ask('Delete contact?', u.name, 'Delete', true).then(function (ok) { if (ok) post('contact_delete', { user_id: u.id }).then(draw).catch(fail); }); } })));
        });
      });
    }
    q.oninput = debounce(draw, 300);
    var fn = h('input', { placeholder: 'First name' }), ln = h('input', { placeholder: 'Last name' }), ph = h('input', { placeholder: 'Phone (+91…) or @username' });
    var add = card('Add contact'); add.appendChild(field('First name', fn)); add.appendChild(field('Last name', ln)); add.appendChild(field('Phone number or @username', ph));
    add.appendChild(h('button', { class: 'btn primary', text: 'Add', onclick: function () {
      var v = ph.value.trim(); var isUser = /^@?[A-Za-z]/.test(v);
      post('contact_add', { first_name: fn.value, last_name: ln.value, phone: isUser ? '' : v, user: isUser ? v : '' }).then(function () { toast('Contact added'); fn.value = ln.value = ph.value = ''; draw(); }).catch(fail); } }));
    root.appendChild(q); root.appendChild(listBox); root.appendChild(add);
    return draw();
  };

  RENDER.bot = function (root) {
    return get('ai').then(function (a) {
      window.__info = a.info;
      root.appendChild(h('h2', { text: 'AI bot' }));
      var c = card('Auto replies');
      c.appendChild(h('div', { class: 'line' }, h('div', { class: 'grow' }, h('b', { text: 'AI replies (master switch)' }), h('div', { class: 'hint', text: 'Off karoge toh kisi bhi chat me AI reply nahi karega' })),
        toggle(a.global, function (v) { post('ai_global', { enabled: v }).then(function () { toast('AI ' + (v ? 'on' : 'off')); }).catch(fail); })));
      c.appendChild(h('div', { class: 'hint', text: 'Kisi ek chat me band/chalu karne ke liye us chat ke header me "AI on/off" button use karo.' }));
      root.appendChild(c);
      var i = a.info, l = i.lisa || {}; var inf = card('Status');
      [['Mode', i.mode === 'userbot' ? 'STRING_SESSION (your account)' : 'BOT_TOKEN (bot)'], ['Persona', i.persona],
       ['Lisa dataset', l.enabled ? (l.loaded ? l.examples + ' example chats loaded, ' + l.per_reply + ' per reply' : 'enabled but file missing') : 'off']].forEach(function (r) {
        inf.appendChild(h('div', { class: 'line' }, h('span', { class: 'mute', text: r[0] }), h('span', { text: r[1] }))); });
      (i.providers || []).forEach(function (p) { inf.appendChild(h('div', { class: 'line' }, h('span', { class: 'mute', text: 'AI provider: ' + p.name }), h('span', { text: p.ready ? 'ready' : 'cooling down' }))); });
      root.appendChild(inf);
    });
  };

  RENDER.ailog = function (root) {
    root.appendChild(h('h2', { text: 'AI chat log' }));
    root.appendChild(h('div', { class: 'hint', text: 'Bot ne kis se kya baat ki (masked, auto-delete hota hai).' }));
    var q = h('input', { class: 'search', placeholder: 'Search name or message', type: 'search' }); var box = h('div');
    function bubbles(msgs) {
      msgs.forEach(function (m) {
        box.appendChild(h('div', { class: 'b' }, h('div', { class: 'who', text: m.name || 'Unknown' }), h('div', { class: 'txt', text: m.user_text }), h('div', { class: 'meta', text: fmtFull(m.ts) })));
        if (m.bot_text) box.appendChild(h('div', { class: 'b out', style: 'margin-left:auto' }, h('div', { class: 'txt', text: m.bot_text })));
        if (m.flags && m.flags.length) box.appendChild(h('div', {}, m.flags.map(function (f) { return h('span', { class: 'tag red', text: f }); })));
      });
    }
    function users() {
      return request('/admin/api/users?q=' + encodeURIComponent(q.value)).then(function (d) {
        clear(box); if (!d.users.length) box.appendChild(h('div', { class: 'empty', text: 'No chats yet' }));
        d.users.forEach(function (u) {
          box.appendChild(h('div', { class: 'row', onclick: function () {
            request('/admin/api/messages?chat_id=' + u.chat_id + '&user_id=' + u.user_id).then(function (r) { clear(box).appendChild(h('button', { class: 'btn', text: '← Back', onclick: users })); bubbles(r.messages.slice().reverse()); }).catch(fail); } },
            avatar(u.user_id, u.name, false, 'sm'), h('div', { class: 'rbody' }, h('div', { class: 'name', text: (u.name || 'Unknown') + (u.chat_title ? ' · ' + u.chat_title : '') }),
              h('div', { class: 'prev', text: u.count + ' msgs · ' + (u.last_text || '') })), h('span', { class: 'time', text: fmtTime(u.last_ts) })));
        });
      });
    }
    q.oninput = debounce(users, 300); root.appendChild(q); root.appendChild(box); return users();
  };

  // ------------------------------------------------------------------ live updates + boot
  function startEvents() {
    if (!window.EventSource) return;
    var refresh = debounce(function () { if (S.next === null || S.dialogs.length <= 60) loadDialogs(true); }, 1500);
    var es = new EventSource('/admin/api/events');
    es.onmessage = function (ev) {
      var d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.t === 'message') { refresh(); if (S.chat && d.chat_id === S.chat.id) refreshTail(); }
    };
  }
  function boot() {
    applyPrefs(); buildApp();
    if (window.matchMedia) { try { window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyPrefs); } catch (e) { /* old browser */ } }
    reloadMe().then(function () { return loadDialogs(true); }).catch(fail);
    get('ai').then(function (a) { window.__info = a.info; }).catch(function () {});
    startEvents();
    setInterval(function () { if (!document.hidden) { loadDialogs(true); if (S.chat) refreshTail(); } }, 45000);
  }
  boot();
})();
