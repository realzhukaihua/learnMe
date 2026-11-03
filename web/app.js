'use strict';

const MANAGER_ID = 'main';
const POPOUT = location.pathname.startsWith('/task/') ? decodeURIComponent(location.pathname.slice(6)) : null;

const STATUS = {
  running: tr('Running'), waiting: tr('Waiting for you'), idle: tr('Turn done · needs a decision'),
  interrupted: tr('Interrupted · needs a decision'), error: tr('Error · needs a decision'), done: tr('Ended'),
};
const MANAGER_STATUS = { running: tr('Working'), waiting: tr('Waiting for you'), idle: tr('Online'), interrupted: tr('Interrupted'), error: tr('Error'), done: tr('Online') };
const ACTIVE = new Set(['running', 'waiting']);
const NEEDS = new Set(['idle', 'interrupted', 'error']); // turn stopped; the next call is the user's
const ACTION_LABEL = { new: tr('Task'), append: tr('Append'), correct: tr('Correct'), continue: tr('Continue'), reopen: tr('Reopen'), history: tr('History') };
const END_REASONS = [tr('Goal reached'), tr('Wrong direction'), tr('Poor results'), tr('Not worth continuing'), tr('Parking it'), tr('Duplicate / done elsewhere')];
const FILTERS = [
  ['open', tr('Not ended'), x => x.status !== 'done'],
  ['needs', tr('Needs me'), x => x.status === 'waiting' || NEEDS.has(x.status)],
  ['active', tr('Running'), x => ACTIVE.has(x.status)],
  ['all', tr('All'), () => true],
];
const MGR_PREFIX = 'mcp__learnme__';
const MGR_TOOLS = {
  list_projects: tr('List projects'), dispatch_task: tr('Dispatch a task'), list_tasks: tr('List tasks'),
  task_status: tr('Check a task'), message_task: tr('Message a task'), interrupt_task: tr('Interrupt a task'), end_task: tr('End a task'),
};

const S = { tasks: new Map(), wins: new Map(), projects: [], filter: 'open', booted: false, settings: {}, act: new Map() };

const COMPACT_UI = matchMedia('(max-width: 720px) and (pointer: coarse), (max-width: 480px)');

function setupCompactTools() {
  const tools = document.getElementById('console-tools');
  const panel = tools.querySelector('.console-tools-panel');
  const counters = document.getElementById('counters');
  const right = document.querySelector('.top-right');
  const connection = document.getElementById('conn');
  const apply = () => {
    tools.open = false;
    if (COMPACT_UI.matches) {
      panel.append(counters, right);
      tools.after(connection);
    } else {
      tools.before(counters, right);
      right.append(connection);
    }
    for (const w of S.wins.values()) w.layoutTools();
  };
  COMPACT_UI.addEventListener('change', apply);
  document.addEventListener('click', event => {
    if (!COMPACT_UI.matches || event.target.closest('summary')) return;
    if (event.target.closest('#console-tools button, #console-tools .counter, .win-options button')
        || !event.target.closest('#console-tools, .win-options')) {
      tools.open = false;
      for (const w of S.wins.values()) w.q('.win-options').open = false;
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      tools.open = false;
      for (const w of S.wins.values()) w.q('.win-options').open = false;
    }
  });
  apply();
}

// ---------- helpers ----------

const NO_GRAMMARLY = { 'data-gramm': 'false', 'data-gramm_editor': 'false', 'data-enable-grammarly': 'false' };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  // Grammarly's overlays can end up over another window's input and swallow clicks.
  if (tag === 'textarea' || tag === 'input') attrs = { ...NO_GRAMMARLY, ...attrs };
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
  return el;
}

async function api(path, body) {
  const opts = { method: body === undefined ? 'GET' : 'POST', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch { /* keep statusText */ }
    throw new Error(msg);
  }
  return r.json();
}

let toastTimer;
function toast(msg, isError = false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = isError ? 'error' : '';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 5000 : 2500);
}

marked.setOptions({ gfm: true, breaks: true });
DOMPurify.addHook('afterSanitizeAttributes', node => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});
function md(text) {
  return h('div', { class: 'md', html: DOMPurify.sanitize(marked.parse(text || '')) });
}

function fmtDur(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m${String(sec % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}m`;
}
function fmtTok(n) {
  if (n >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${Math.round(n / 1e3)}k`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}
function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return tr('just now');
  if (s < 3600) return tr('{n} min ago', { n: Math.floor(s / 60) });
  if (s < 86400) return tr('{n} h ago', { n: Math.floor(s / 3600) });
  return tr('{n} d ago', { n: Math.floor(s / 86400) });
}
const baseName = p => (p || '').replace(/\/+$/, '').split('/').pop();
function oneLine(s, n = 160) {
  s = String(s ?? '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n) + '…' : s;
}
const pretty = v => (typeof v === 'string' ? v : JSON.stringify(v, null, 2));
const taskName = id => S.tasks.get(id)?.title || id;

function toolBrief(name, i = {}, cwd = '') {
  const rel = p => (p && cwd && p.startsWith(cwd + '/') ? p.slice(cwd.length + 1) : p);
  switch (name) {
    case 'Bash': return '$ ' + oneLine(i.command);
    case 'Read': case 'Write': case 'Edit': case 'MultiEdit': return rel(i.file_path);
    case 'ApplyPatch': return (i.changes || []).map(c => rel(c.path)).join(', ');
    case 'NotebookEdit': return rel(i.notebook_path);
    case 'Glob': return i.pattern;
    case 'Grep': return `${i.pattern}${i.path ? '  in ' + rel(i.path) : ''}`;
    case 'WebFetch': return i.url;
    case 'WebSearch': return i.query;
    case 'Agent': case 'Task': return i.description || i.subagent_type;
    case 'Skill': return i.skill;
    default: return oneLine(Object.values(i).find(v => typeof v === 'string') || '', 120);
  }
}

function mgrBrief(name, i = {}) {
  switch (name) {
    case 'dispatch_task': return `${i.project ? baseName(i.project) : 'workspace'} · ${i.title || oneLine(i.prompt, 80)}`;
    case 'list_projects': return i.query || '';
    case 'list_tasks': return [i.filter, i.project].filter(Boolean).join(' · ');
    case 'task_status': case 'interrupt_task': return taskName(i.task_id);
    case 'message_task': return `${taskName(i.task_id)}${i.correct ? tr(' (correction)') : ''}: ${oneLine(i.text, 80)}`;
    case 'end_task': return `${taskName(i.task_id)}: ${i.reason || ''}`;
    default: return '';
  }
}

function toolInput(name, i) {
  if (!i) return '';
  if (name === 'Bash') return `$ ${i.command}` + (i.description ? `\n# ${i.description}` : '');
  if (name === 'Write') return `${i.file_path}\n\n${i.content ?? ''}`;
  if (name === 'Edit') return `${i.file_path}\n\n--- ${tr('before')}\n${i.old_string ?? ''}\n+++ ${tr('after')}\n${i.new_string ?? ''}`;
  if (name === 'ApplyPatch' && Array.isArray(i.changes)) return i.changes.map(c => `${c.path}\n${c.diff || ''}`).join('\n\n');
  return pretty(i);
}

// ---------- attachments ----------

const UPLOAD_MAX_MB = 200;
const UPLOAD_PATH_RE = /\/uploads\/(\d{4}-\d{2}-\d{2})\/([^/]+)$/;

function fmtSize(n) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)}MB` : n >= 1024 ? `${Math.round(n / 1024)}KB` : `${n}B`;
}

// Screenshots come off the clipboard as "image.png"; give them a name that says when.
function pastedFile(file) {
  if (file.name && !/^image\.\w+$/i.test(file.name)) return file;
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  const ext = (file.type.split('/')[1] || 'png').replace('jpeg', 'jpg');
  return new File([file], `paste-${stamp}.${ext}`, { type: file.type });
}

// Files go to the server first (📎, drag-drop, pasted screenshots); the message then ends with
// their absolute paths, which the Claude session opens with Read.
class Attach {
  constructor(ta, zone, btnRow) {
    this.items = [];
    this.tray = h('div', { class: 'att-tray', hidden: true });
    ta.before(this.tray);
    const pick = h('input', { type: 'file', multiple: true, hidden: true,
      onchange: () => { this.add([...pick.files]); pick.value = ''; } });
    btnRow.prepend(h('button', {
      type: 'button', class: 'ghost att-btn', text: '📎', onclick: () => pick.click(),
      title: tr('Attach files (or drag them in, or paste a screenshot; up to {mb}MB each)', { mb: UPLOAD_MAX_MB }),
    }), pick);
    ta.addEventListener('paste', e => {
      const dt = e.clipboardData;
      if (!dt?.files.length || dt.getData('text/plain')) return; // copied text stays text, even when Excel adds a picture
      e.preventDefault();
      this.add([...dt.files].map(pastedFile));
    });
    zone.addEventListener('dragover', e => {
      if (!e.dataTransfer?.types.includes('Files')) return;
      e.preventDefault();
      zone.classList.add('dropping');
    });
    zone.addEventListener('dragleave', e => { if (!zone.contains(e.relatedTarget)) zone.classList.remove('dropping'); });
    zone.addEventListener('drop', e => {
      zone.classList.remove('dropping');
      if (!e.dataTransfer?.files.length) return;
      e.preventDefault();
      this.add([...e.dataTransfer.files]);
    });
  }

  get count() { return this.items.length; }

  add(files) {
    for (const file of files) {
      const it = { file, name: file.name, state: 'up', pct: 0, el: h('span', { class: 'att' }) };
      this.items.push(it);
      this.tray.append(it.el);
      if (file.size > UPLOAD_MAX_MB * 1048576) this.fail(it, tr('Over the {mb}MB limit ({size})', { mb: UPLOAD_MAX_MB, size: fmtSize(file.size) }));
      else this.upload(it);
    }
    this.tray.hidden = !this.items.length;
  }

  upload(it) {
    const xhr = it.xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/uploads?name=${encodeURIComponent(it.file.name)}`);
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) { it.pct = Math.floor((e.loaded / e.total) * 100); this.draw(it); }
    };
    xhr.onload = () => {
      let r = null;
      try { r = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status < 300 && r?.path) {
        Object.assign(it, { state: 'ok', path: r.path, url: r.url, name: r.name });
        this.draw(it);
      } else {
        this.fail(it, typeof r?.detail === 'string' ? r.detail : `HTTP ${xhr.status}`);
      }
    };
    xhr.onerror = () => this.fail(it, tr('Network error or connection lost'));
    this.draw(it);
    xhr.send(it.file);
  }

  fail(it, why) {
    Object.assign(it, { state: 'err', err: why });
    this.draw(it);
    toast(tr('Upload of "{name}" failed: {why}', { name: it.file.name, why }), true);
  }

  draw(it) {
    it.el.className = `att ${it.state}`;
    it.el.title = it.path || it.err || it.file.name;
    it.el.replaceChildren(
      it.url ? h('a', { href: it.url, target: '_blank', rel: 'noopener', text: it.name }) : h('span', { text: it.name }),
      h('i', { text: it.state === 'up' ? `${it.pct}%` : it.state === 'err' ? tr('Failed: {err}', { err: it.err }) : fmtSize(it.file.size) }),
      h('button', { type: 'button', class: 'x', text: '×', title: tr('Remove'), onclick: () => this.remove(it) }));
  }

  remove(it) {
    it.xhr?.abort();
    this.items = this.items.filter(x => x !== it);
    it.el.remove();
    this.tray.hidden = !this.items.length;
  }

  clear() {
    for (const it of this.items) it.el.remove();
    this.items = [];
    this.tray.hidden = true;
  }

  // The text to send with the uploaded paths appended, or null (after saying why) if it can't go yet.
  compose(text) {
    if (this.items.some(x => x.state === 'up')) { toast(tr('Attachments are still uploading; send when they finish')); return null; }
    if (this.items.some(x => x.state === 'err')) { toast(tr('An attachment failed to upload; remove it with × before sending'), true); return null; }
    if (!this.items.length) return text;
    return [text, tr('Attachments:') + '\n' + this.items.map(x => `- ${x.path}`).join('\n')].filter(Boolean).join('\n\n');
  }
}

// A sent message's trailing attachments block shows as clickable file names.
function userBody(text) {
  const m = /(?:^|\n\n)(?:Attachments:|附件：)\n((?:- \/.+(?:\n|$))+)$/.exec(text || '');
  if (!m) return text;
  return [text.slice(0, m.index), h('div', { class: 'atts' }, m[1].trim().split('\n').map(line => {
    const path = line.slice(2);
    const u = UPLOAD_PATH_RE.exec(path);
    const name = '📎 ' + path.split('/').pop();
    return u ? h('a', { class: 'att', href: `/api/uploads/${u[1]}/${encodeURIComponent(u[2])}`, target: '_blank', rel: 'noopener', title: path, text: name })
      : h('span', { class: 'att', title: path, text: name });
  }))];
}

// A file dropped outside a message box would otherwise navigate the tab away from the console.
for (const type of ['dragover', 'drop']) {
  document.addEventListener(type, e => { if (e.dataTransfer?.types.includes('Files')) e.preventDefault(); });
}

// ---------- task window ----------

class Win {
  constructor(id) {
    this.id = id;
    this.manager = id === MANAGER_ID;
    this.lastSeq = 0;
    this.syncing = false;
    this.followLatest = true;
    this.followScrollTop = null;
    this.scrollFrame = null;
    this.buffer = [];
    this.tools = new Map();
    this.perms = new Map();
    this.live = null;
    this.liveThink = null;
    this.thinkChars = 0;

    this.el = document.getElementById('tpl-win').content.firstElementChild.cloneNode(true);
    this.el.dataset.id = id;
    this.q = sel => this.el.querySelector(sel);
    this.layoutTools();
    this.log = this.q('.log');
    this.log.addEventListener('scroll', () => {
      if (this.followLatest && this.log.scrollTop === this.followScrollTop) return;
      this.followLatest = this.atBottom();
    }, { passive: true });
    this.scrollResize = new ResizeObserver(() => this.queueScroll());
    this.scrollResize.observe(this.log);
    this.scrollMutation = new MutationObserver(() => this.queueScroll());
    this.scrollMutation.observe(this.log, { childList: true, subtree: true, characterData: true });
    this.log.addEventListener('load', () => this.queueScroll(), true);
    this.msg = this.q('.msg');
    this.todosEl = this.q('.todos');
    this.decideEl = this.q('.decide');
    this.compactEl = this.q('.compact-panel');
    if (!this.manager) {
      this.fighter = new Fighter(id);
      this.q('.win-title').before(this.fighter.el);
    }

    this.el.addEventListener('click', e => {
      const b = e.target.closest('[data-act]');
      if (b && this.el.contains(b)) this.act(b.dataset.act, b);
    });
    this.msg.addEventListener('keydown', e => this.onKey(e));
    this.att = new Attach(this.msg, this.el, this.q('.btns'));

    const chips = this.q('.chips');
    for (const reason of END_REASONS) {
      chips.append(h('button', {
        type: 'button', text: reason,
        onclick: e => {
          const on = !e.currentTarget.classList.contains('on');
          chips.querySelectorAll('button').forEach(b => b.classList.remove('on'));
          e.currentTarget.classList.toggle('on', on);
        },
      }));
    }
    this.q('.decide-reason').addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.isComposing) this.act('end-confirm');
    });
    this.q('.compact-focus').addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.isComposing) this.act('compact-confirm');
      else if (e.key === 'Escape') this.act('compact-cancel');
    });

    if (this.manager) {
      this.el.classList.add('manager');
      this.q('[data-act="reset"]').hidden = false;
      this.q('[data-act="hide"]').hidden = true;
    }
    if (POPOUT) {
      this.q('[data-act="pop"]').hidden = true;
      this.q('[data-act="hide"]').hidden = true;
      this.q('[data-act="max"]').hidden = true;
    }
    this.refresh();
    this.load();
  }

  get task() { return S.tasks.get(this.id) || { id: this.id, status: 'idle', title: '…', cwd: '', pending: [] }; }
  get active() { return ACTIVE.has(this.task.status); }

  // --- event stream ---

  async load() {
    if (this.syncing) return;
    this.syncing = true;
    const first = this.lastSeq === 0;
    if (first) this.followLatest = true;
    try {
      const evs = await api(`/api/tasks/${encodeURIComponent(this.id)}/events?after=${this.lastSeq}`);
      for (const ev of evs) this.apply(ev);
      for (const ev of this.buffer) this.apply(ev);
    } catch (e) {
      toast(tr('Failed to load the window: {msg}', { msg: e.message }), true);
    } finally {
      this.buffer = [];
      this.syncing = false;
    }
    this.refreshPerms();
    if (this.followLatest) { this.scrollBottom(); this.queueScroll(); }
  }

  receive(ev) {
    if (this.syncing) {
      if (ev.seq != null) this.buffer.push(ev);
      return;
    }
    if (ev.seq != null && ev.seq > this.lastSeq + 1) { // missed something: refetch the gap
      this.buffer.push(ev);
      this.load();
      return;
    }
    const stick = this.followLatest;
    this.apply(ev);
    if (stick) { this.scrollBottom(); this.queueScroll(); }
  }

  apply(ev) {
    if (ev.seq != null) {
      if (ev.seq <= this.lastSeq) return;
      this.lastSeq = ev.seq;
    }
    try { this.render(ev); } catch (e) { console.error('render failed', ev, e); }
  }

  atBottom() { return this.log.scrollHeight - this.log.scrollTop - this.log.clientHeight < 80; }
  scrollBottom() {
    this.log.scrollTop = this.log.scrollHeight;
    this.followScrollTop = this.log.scrollTop;
  }
  queueScroll() {
    if (this.scrollFrame != null) return;
    this.scrollFrame = requestAnimationFrame(() => {
      this.scrollFrame = null;
      if (this.followLatest && this.el.isConnected) this.scrollBottom();
    });
  }
  showLatest() {
    this.followLatest = true;
    this.scrollBottom();
    this.queueScroll();
  }
  layoutTools() {
    const menu = this.q('.win-options');
    const actions = this.q('.win-actions');
    menu.open = false;
    if (COMPACT_UI.matches) menu.append(actions);
    else this.q('.win-head').append(actions);
  }
  add(el) { this.log.append(el); return el; }

  clearLog() { // "clear conversation": only the screen; the events stay in the DB
    this.log.replaceChildren();
    this.tools.clear();
    this.perms.clear();
    this.live = this.liveThink = null;
    this.thinkChars = 0;
    this.setTodos(null);
  }

  render(ev) {
    const t = ev.type;
    if (t === 'delta') return this.renderDelta(ev);
    if (this.liveThink) { this.liveThink.remove(); this.liveThink = null; this.thinkChars = 0; }
    if (this.live && ['text', 'result', 'tool_use', 'user', 'error'].includes(t) && !ev.parent) {
      this.live.remove();
      this.live = null;
    }
    const sub = ev.parent ? ' sub' : '';
    switch (t) {
      case 'user': {
        const fromSlack = this.manager && ev.actor === 'system' && (ev.text || '').startsWith('[Slack]');
        const who = fromSlack ? 'Slack' : { manager: tr('Manager'), auto: tr('Manager · auto-supervision'), system: this.manager ? tr('Auto-supervision') : tr('System'), event: tr('Outside input') }[ev.actor] || tr('You');
        const cls = { manager: ' by-manager', auto: ' by-manager', system: ' by-system' }[ev.actor] || '';
        const showBadge = this.manager ? ['append', 'correct'].includes(ev.action) : !!ev.action;
        this.add(h('div', { class: 'ev user' + cls },
          h('div', { class: 'who' }, who,
            showBadge ? h('span', { class: `badge ${ev.action}`, text: ACTION_LABEL[ev.action] || ev.action }) : null),
          userBody(ev.text)));
        break;
      }
      case 'init':
        this.add(h('div', { class: 'ev sys', text: [ev.backend, ev.model, ev.effort && `effort ${ev.effort}`, ev.permission_mode && tr('permission {mode}', { mode: ev.permission_mode })].filter(Boolean).join(' · ') }));
        break;
      case 'text':
        this.add(h('div', { class: 'ev text' + sub }, md(ev.text)));
        break;
      case 'thinking':
        this.add(h('details', { class: 'ev thinking' + sub },
          h('summary', { text: tr('💭 Thinking') }), h('div', { class: 'body', text: ev.text })));
        break;
      case 'tool_use': this.renderTool(ev); break;
      case 'tool_result': this.renderToolResult(ev); break;
      case 'permission': this.renderPerm(ev); break;
      case 'permission_done': this.resolvePerm(ev); break;
      case 'result': this.renderResult(ev); break;
      case 'notice': this.add(h('div', { class: 'ev sys', text: ev.text })); break;
      case 'clear': this.clearLog(); break;
      case 'slack':
        this.add(h('div', { class: 'ev sys slack' }, ev.text,
          ev.permalink ? [' ', h('a', { href: ev.permalink, target: '_blank', rel: 'noopener', text: tr('Open in Slack') })] : null));
        break;
      case 'compact': {
        const parts = [ev.trigger === 'auto' ? tr('🗜 Context nearly full; compacted automatically') : tr('🗜 Context compacted')];
        if (ev.pre_tokens) parts.push(tr('{n} tokens before', { n: fmtTok(ev.pre_tokens) }));
        if (ev.duration_ms) parts.push(tr('took {d}', { d: fmtDur(ev.duration_ms / 1000) }));
        this.add(h('div', { class: 'ev sys compact', text: parts.join(' · ') }));
        break;
      }
      case 'compact_summary':
        this.add(h('details', { class: 'ev compact-sum' }, h('summary', { text: tr('View the summary') }),
          md(ev.text.replace(/^This session is being continued[^\n]*\n+/, ''))));
        break;
      case 'history_tool':
        this.add(h('div', { class: 'ev htool', text: `↳ ${ev.name}  ${ev.brief || ''}`, title: ev.brief || '' }));
        break;
      case 'memory':
        this.add(h('details', { class: 'ev mem' },
          h('summary', {}, `🧠 ${{ created: tr('Memory created'), deleted: tr('Memory deleted'), modified: tr('Memory updated') }[ev.change_kind] || tr('Memory updated')}: ${ev.scope_label} / ${ev.name}`,
            h('span', { class: 'grow' }),
            h('button', { class: 'ghost', text: tr('Undo'), onclick: e => { e.preventDefault(); revertMemory(ev.change_id); } })),
          diffView(ev.diff)));
        break;
      case 'error':
        this.add(h('div', { class: 'ev err' }, ev.message,
          ev.detail ? h('details', {}, h('summary', { text: tr('Details') }), h('pre', { text: ev.detail })) : null));
        break;
    }
  }

  renderDelta(ev) {
    if (ev.parent) return;
    if (ev.kind === 'text') {
      if (this.liveThink) { this.liveThink.remove(); this.liveThink = null; this.thinkChars = 0; }
      if (!this.live) this.live = this.add(h('div', { class: 'ev text live' }));
      this.live.textContent += ev.text;
    } else {
      if (!this.liveThink) this.liveThink = this.add(h('div', { class: 'thinking-live' }));
      this.thinkChars += ev.text.length;
      this.liveThink.textContent = tr('💭 Thinking… {n} chars', { n: this.thinkChars });
    }
  }

  renderTool(ev) {
    const sub = ev.parent ? ' sub' : '';
    if (ev.name === 'TodoWrite') {
      if (!ev.parent) this.setTodos(ev.input?.todos);
      this.add(h('div', { class: 'ev sys' + sub, text: tr('☑ Updated the task list') }));
      this.tools.set(ev.id, { quiet: true });
      return;
    }
    const mgr = ev.name.startsWith(MGR_PREFIX) ? ev.name.slice(MGR_PREFIX.length) : null;
    let label = ev.name;
    let brief = toolBrief(ev.name, ev.input, this.task.cwd);
    if (mgr) {
      label = MGR_TOOLS[mgr] || mgr;
      brief = mgrBrief(mgr, ev.input);
    } else if (ev.name.startsWith('mcp__')) {
      label = ev.name.split('__').slice(1).join(' · ');
    }
    const summary = h('summary', {},
      h('span', { class: 'ti' }), h('span', { class: 'tn', text: label }), h('span', { class: 'tb', text: brief, title: brief }));
    const body = h('div', { class: 'tool-body' }, h('pre', { text: toolInput(ev.name, ev.input) }));
    const el = this.add(h('details', {
      class: 'ev tool' + sub + (mgr === 'dispatch_task' ? ' dispatch' : ''), 'data-state': 'run',
    }, summary, body));
    this.tools.set(ev.id, { el, body, summary, mgr });
  }

  renderToolResult(ev) {
    const t = this.tools.get(ev.tool_use_id);
    if (t?.quiet) return;
    const out = h('pre', { class: ev.is_error ? 'err' : '', text: ev.content || tr('(no output)') });
    if (!t) {
      this.add(h('details', { class: 'ev tool' + (ev.parent ? ' sub' : ''), 'data-state': ev.is_error ? 'err' : 'ok' },
        h('summary', {}, h('span', { class: 'ti' }), h('span', { class: 'tn', text: tr('Result') }),
          h('span', { class: 'tb', text: oneLine(ev.content, 120) })),
        h('div', { class: 'tool-body' }, out)));
      return;
    }
    t.el.dataset.state = ev.is_error ? 'err' : 'ok';
    t.body.append(out);
    if (t.mgr === 'dispatch_task' && !ev.is_error) {
      try {
        const r = JSON.parse(ev.content);
        if (r.task_id) {
          t.summary.append(h('button', {
            class: 'open-task', text: tr('Open window'),
            onclick: e => { e.preventDefault(); openWin(r.task_id, { focus: true }); },
          }));
        }
      } catch { /* not JSON: nothing to link */ }
    }
  }

  renderPerm(ev) {
    const card = h('div', { class: 'ev perm' + (ev.parent ? ' sub' : '') });
    const actions = h('div', { class: 'perm-actions' });
    const done = h('div', { class: 'perm-done', hidden: true });
    const setBusy = busy => actions.querySelectorAll('button, input').forEach(x => { x.disabled = busy; });
    const reply = async body => {
      setBusy(true);
      try {
        await api(`/api/tasks/${encodeURIComponent(this.id)}/permissions/${ev.req_id}`, body);
      } catch (e) {
        toast(e.message, true);
        setBusy(false);
      }
    };
    const i = ev.input || {};

    if (ev.tool === 'AskUserQuestion' && Array.isArray(i.questions)) {
      card.append(h('div', { class: 'perm-title', text: tr('❓ The agent has a question for you') }));
      const picks = i.questions.map(q => ({ q, sel: new Set(), other: null }));
      for (const p of picks) {
        const opts = h('div', { class: 'q-opts' });
        for (const o of p.q.options || []) {
          const b = h('button', { type: 'button' }, o.label, o.description ? h('small', { text: o.description }) : null);
          b.onclick = () => {
            const on = !p.sel.has(o.label);
            if (!p.q.multiSelect) {
              p.sel.clear();
              opts.querySelectorAll('button').forEach(x => x.classList.remove('on'));
            }
            if (on) p.sel.add(o.label); else p.sel.delete(o.label);
            b.classList.toggle('on', on);
          };
          opts.append(b);
        }
        p.other = h('input', { placeholder: tr('Other (type your own)') });
        card.append(h('div', { class: 'q' },
          h('div', { class: 'q-head' }, p.q.header ? h('span', { class: 'badge', text: p.q.header }) : null, p.q.question),
          opts, p.other));
      }
      const why = h('input', { placeholder: tr('Skip and explain instead…') });
      actions.append(
        h('button', {
          class: 'primary', text: tr('Submit'),
          onclick: () => {
            const answers = {};
            for (const p of picks) {
              const vals = [...p.sel];
              if (p.other.value.trim()) vals.push(p.other.value.trim());
              answers[p.q.question] = vals.join(', ');
            }
            if (Object.values(answers).some(v => !v)) return toast(tr('Every question needs an answer'), true);
            reply({ action: 'answer', answers });
          },
        }),
        why,
        h('button', { text: tr("Don't answer"), onclick: () => reply({ action: 'deny', message: why.value.trim() || null }) }));
    } else {
      const plan = ev.tool === 'ExitPlanMode';
      card.append(h('div', { class: 'perm-title', text: plan ? tr('📋 The agent proposed a plan for your approval') : `🔐 ${ev.title || tr('The agent wants to use {tool}', { tool: ev.tool })}` }));
      if (ev.description) card.append(h('div', { class: 'perm-desc', text: ev.description }));
      card.append(plan && i.plan ? md(i.plan) : h('pre', { text: toolInput(ev.tool, i) }));
      const why = h('input', { placeholder: plan ? tr('Feedback (sent to the agent if you reject)') : tr('Reason (optional)') });
      why.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.isComposing && why.value.trim()) reply({ action: 'deny', message: why.value.trim() });
      });
      actions.append(
        h('button', { class: 'primary', text: plan ? tr('Approve plan') : tr('Allow'), onclick: () => reply({ action: 'allow' }) }),
        ev.can_always ? h('button', { text: tr('Always allow'), onclick: () => reply({ action: 'allow_always' }) }) : null,
        why,
        h('button', { class: 'danger', text: tr('Deny'), onclick: () => reply({ action: 'deny', message: why.value.trim() || null }) }));
    }
    card.append(actions, done);
    this.add(card);
    this.perms.set(ev.req_id, { card, actions, done, resolved: false });
  }

  resolvePerm(ev) {
    const p = this.perms.get(ev.req_id);
    if (!p) return;
    p.resolved = true;
    p.card.classList.add('resolved');
    p.actions.hidden = true;
    p.done.hidden = false;
    const labels = { allow: tr('✓ Allowed'), allow_always: tr('✓ Always allowed'), deny: tr('✗ Denied'), answer: tr('✓ Answered'), cancelled: tr('— Cancelled') };
    let txt = labels[ev.action] || ev.action;
    if (ev.answers) txt += ': ' + Object.values(ev.answers).join(' / ');
    else if (ev.message) txt += ': ' + ev.message;
    p.done.textContent = txt;
  }

  refreshPerms() {
    const pending = new Set(this.task.pending || []);
    for (const [id, p] of this.perms) {
      if (p.resolved) continue;
      const live = pending.has(id);
      p.actions.hidden = !live;
      p.done.hidden = live;
      if (!live) p.done.textContent = tr('— Expired');
      p.card.classList.toggle('resolved', !live);
    }
  }

  renderResult(ev) {
    const cls = ev.status === 'interrupted' ? 'interrupted' : ev.status === 'error' ? 'error' : 'ok';
    const parts = [{ ok: ev.compact ? tr('✓ Compacted') : tr('✓ Turn done'), interrupted: tr('⏹ Interrupted'), error: tr('✗ Error') }[cls]];
    if (ev.num_turns) parts.push(tr('{n} steps', { n: ev.num_turns }));
    if (ev.duration_ms) parts.push(fmtDur(ev.duration_ms / 1000));
    if (ev.cost_usd) parts.push(tr('total ${cost}', { cost: ev.cost_usd.toFixed(2) }));
    this.add(h('div', { class: `ev result ${cls}`, text: parts.join(' · ') }));
    const errs = [...(ev.errors || []), ev.text].filter(Boolean);
    if (cls === 'error' && errs.length) this.add(h('div', { class: 'ev err', text: errs.join('\n') }));
  }

  setTodos(todos) {
    if (!Array.isArray(todos) || !todos.length) { this.todosEl.hidden = true; return; }
    const done = todos.filter(t => t.status === 'completed').length;
    const cur = todos.find(t => t.status === 'in_progress');
    const wasOpen = this.todosEl.querySelector('details')?.open;
    this.todosEl.replaceChildren(h('details', { open: wasOpen || null },
      h('summary', {},
        h('span', { class: 'bar' }, h('i', { style: `width:${(done / todos.length) * 100}%` })),
        h('span', { text: `${done}/${todos.length}` }),
        h('span', { class: 'cur', text: cur ? cur.activeForm || cur.content : done === todos.length ? tr('All done') : '' })),
      h('ul', {}, todos.map(t => h('li', { class: t.status, text: t.content })))));
    this.todosEl.hidden = false;
  }

  // --- header + controls ---

  refresh() {
    const task = this.task;
    const st = task.status;
    const active = ACTIVE.has(st);
    this.q('.dot').className = 'dot ' + st;
    this.fighter?.set(st === 'running');
    this.q('.win-title .t').textContent = this.manager ? tr('Main window · manager') : task.title;
    const proj = this.q('.proj');
    proj.textContent = this.manager ? tr("Talk to me here; I'll open windows for independent work") : baseName(task.cwd);
    proj.title = task.cwd || '';
    this.updateMeta();
    this.el.classList.toggle('needs', !this.manager && (st === 'waiting' || NEEDS.has(st)));

    const show = (act, on) => { this.q(`[data-act="${act}"]`).hidden = !on; };
    this.q('[data-act="send"]').textContent = active ? tr('Append') : st === 'done' ? tr('Reopen') : tr('Send');
    show('correct', active);
    show('interrupt', active);
    show('continue', !active && st !== 'done' && !this.manager);
    show('end', !this.manager && st !== 'done');
    show('compact', !active && st !== 'done' && !!(task.session_id || task.fork_from) && task.capabilities?.compact !== false);
    if (st === 'done') this.decideEl.hidden = true;
    if (active || st === 'done') this.compactEl.hidden = true;
    this.renderCtx(task);
    this.msg.placeholder = active
      ? tr('Enter appends to the running task · Ctrl+Enter interrupts and corrects · Esc interrupts')
      : this.manager ? tr('Talk to the manager… (Enter to send · Shift+Enter for a new line)')
        : st === 'done' ? tr('The task has ended; a message reopens it')
          : tr('Continue or give a new direction… (Enter to send · Shift+Enter for a new line)');
    this.refreshPerms();
  }

  renderCtx(t) {
    const el = this.q('.ctx');
    el.hidden = !t.ctx_tokens;
    if (!t.ctx_tokens) return;
    const pct = t.ctx_window ? (t.ctx_tokens / t.ctx_window) * 100 : null;
    el.className = 'ctx' + (pct >= 85 ? ' hot' : pct >= 60 ? ' warm' : '');
    this.q('.ctx-bar').hidden = pct == null;
    this.q('.ctx-bar i').style.width = `${Math.min(100, pct || 0)}%`;
    this.q('.ctx-num').textContent = fmtTok(t.ctx_tokens) + (t.ctx_window ? ` / ${fmtTok(t.ctx_window)}` : '');
    el.title = tr('Context: {n} tokens', { n: t.ctx_tokens.toLocaleString() }) + (pct == null
      ? tr(' (window size known once the session connects)')
      : tr(' of {w} ({pct}%). It compacts automatically when nearly full, or click "Compact"', { w: t.ctx_window.toLocaleString(), pct: pct.toFixed(1) }));
  }

  updateMeta() {
    const t = this.task;
    const parts = [(this.manager ? MANAGER_STATUS : STATUS)[t.status] || t.status];
    if (ACTIVE.has(t.status) && t.turn_started_at) parts.push(fmtDur(Date.now() / 1000 - t.turn_started_at));
    if (t.cost_usd) parts.push(`$${t.cost_usd.toFixed(2)}`);
    if (t.backend) parts.push(t.backend);
    if (t.status === 'done' && t.end_reason) parts.push(t.end_reason);
    this.q('.meta').textContent = parts.join(' · ');
  }

  // --- actions ---

  act(name, btn) {
    switch (name) {
      case 'send': return this.send(false);
      case 'correct': return this.send(true);
      case 'interrupt': return this.post('interrupt');
      case 'continue': return this.sendText(tr('Continue'), false);
      case 'end':
        this.decideEl.hidden = !this.decideEl.hidden;
        if (!this.decideEl.hidden) this.q('.decide-reason').focus();
        return;
      case 'end-cancel':
        this.decideEl.hidden = true;
        return;
      case 'compact':
        this.compactEl.hidden = !this.compactEl.hidden;
        if (!this.compactEl.hidden) this.q('.compact-focus').focus();
        return;
      case 'compact-cancel':
        this.compactEl.hidden = true;
        return;
      case 'compact-confirm': {
        const focus = this.q('.compact-focus').value.trim();
        this.compactEl.hidden = true;
        this.q('.compact-focus').value = '';
        return this.post('compact', { focus: focus || null });
      }
      case 'end-confirm': {
        const chip = this.q('.chips .on')?.textContent;
        const extra = this.q('.decide-reason').value.trim();
        this.decideEl.hidden = true;
        this.q('.decide-reason').value = '';
        this.q('.chips .on')?.classList.remove('on');
        return this.post('end', { reason: [chip, extra].filter(Boolean).join('：') || null });
      }
      case 'reset':
        if (!btn.dataset.armed) {
          btn.dataset.armed = '1';
          btn.textContent = tr('Clear for sure?');
          setTimeout(() => { delete btn.dataset.armed; btn.textContent = tr('Clear'); }, 3000);
          return;
        }
        delete btn.dataset.armed;
        btn.textContent = tr('Clear');
        return api('/api/manager/reset', {}).catch(e => toast(e.message, true));
      case 'max':
        this.el.classList.toggle('max');
        return;
      case 'pop':
        window.open(`/task/${encodeURIComponent(this.id)}`, `learnme-${this.id}`, 'width=920,height=960');
        return;
      case 'hide':
        closeWin(this.id);
    }
  }

  async send(correct) {
    const typed = this.msg.value.trim();
    if (!typed && !this.att.count) {
      if (correct) toast(tr('Write the new direction first, then "Interrupt & correct"'));
      this.msg.focus();
      return;
    }
    const text = this.att.compose(typed);
    if (text == null) return;
    if (await this.sendText(text, correct)) {
      this.msg.value = '';
      this.att.clear();
    }
  }

  async sendText(text, correct) {
    this.showLatest();
    try {
      await api(`/api/tasks/${encodeURIComponent(this.id)}/messages`, { text, correct });
      return true;
    } catch (e) {
      toast(tr('Send failed: {msg}', { msg: e.message }), true);
      return false;
    }
  }

  async post(what, body = {}) {
    try {
      await api(`/api/tasks/${encodeURIComponent(this.id)}/${what}`, body);
    } catch (e) {
      toast(e.message, true);
    }
  }

  onKey(e) {
    if (e.isComposing || e.keyCode === 229) return; // IME is composing Chinese input
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this.send((e.ctrlKey || e.metaKey) && this.active);
    } else if (e.key === 'Escape') {
      if (this.active) {
        e.preventDefault();
        this.post('interrupt');
      } else {
        this.el.classList.remove('max');
      }
    }
  }
}

// ---------- windows ----------

const OPEN_KEY = 'learnme.open';

function saveOpen() {
  if (POPOUT) return;
  const ids = [...document.querySelectorAll('#windows .win')].map(el => el.dataset.id);
  try { localStorage.setItem(OPEN_KEY, JSON.stringify(ids)); } catch { /* storage unavailable */ }
}
function savedOpen() {
  try { return JSON.parse(localStorage.getItem(OPEN_KEY)); } catch { return null; }
}

function openWin(id, { focus = false, prepend = true } = {}) {
  let w = S.wins.get(id);
  if (!w) {
    w = new Win(id);
    S.wins.set(id, w);
    if (id === MANAGER_ID && !POPOUT) {
      document.getElementById('main-col').append(w.el);
    } else {
      const grid = document.getElementById('windows');
      if (prepend) grid.prepend(w.el); else grid.append(w.el);
    }
    saveOpen();
    updateEmpty();
    scheduleSidebar();
  }
  if (focus) {
    w.showLatest();
    w.el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    w.el.classList.remove('flash');
    void w.el.offsetWidth;
    w.el.classList.add('flash');
    w.msg.focus({ preventScroll: true });
  }
  return w;
}

// Bring a window to the front of the grid and make it noticed, without taking the keyboard
// from what the user is typing in (the main window has its own column and never moves).
// `quiet`: it just came alive rather than needing the user, so no "needs you" and one flash at most.
function raiseWin(id, { quiet = false } = {}) {
  if (POPOUT || id === MANAGER_ID) return S.wins.get(id);
  const grid = document.getElementById('windows');
  const col = document.getElementById('windows-col');
  const sc = getComputedStyle(col).overflowY === 'auto' ? col : document.scrollingElement;
  // The window the user is typing in stays where it is on screen (where the mouse rests doesn't
  // matter). While the page is in the background an input only counts if it holds a draft.
  const here = document.hasFocus();
  const typing = document.activeElement?.closest('#windows .win');
  const busy = typing && (here || document.activeElement.value?.trim()) ? typing : null;
  const anchor = busy?.getBoundingClientRect().top;
  const fresh = !S.wins.has(id); // openWin puts it first
  const w = openWin(id);
  const moved = !fresh && grid.firstElementChild !== w.el;
  if (moved) {
    // Taking out the window the user is in would blur its input (caret, a half-typed IME word),
    // so then the windows in front of it go behind it instead and it never leaves the page.
    const inside = w.el.contains(document.activeElement), ahead = [];
    if (inside) for (let e = grid.firstElementChild; e !== w.el; e = e.nextElementSibling) ahead.push(e);
    keepScroll(inside ? ahead : [w.el], () => inside ? w.el.after(...ahead) : grid.prepend(w.el));
    saveOpen();
  }
  if (busy) sc.scrollTop += busy.getBoundingClientRect().top - anchor;
  if (!quiet || moved || fresh) {
    const cls = quiet ? 'flash' : 'attn';
    w.el.classList.remove(cls);
    void w.el.offsetWidth;
    w.el.classList.add(cls);
  }
  const r = w.el.getBoundingClientRect(), c = col.getBoundingClientRect();
  if (r.top >= Math.max(c.top, 0) - 1 && r.top < Math.min(c.bottom, innerHeight) - 60) return w; // in view
  if (!busy && sc === col) col.scrollBy({ top: r.top - c.top - 10, behavior: here ? 'smooth' : 'instant' });
  else if (!quiet) showJump(w);
  return w;
}

// A moved element loses its scroll: put each moved window's log back where it was (or at the bottom).
function keepScroll(els, move) {
  const pos = els.map(e => S.wins.get(e.dataset.id)).filter(Boolean).map(x => [x, x.atBottom(), x.log.scrollTop]);
  move();
  for (const [x, stick, top] of pos) if (stick) x.scrollBottom(); else x.log.scrollTop = top;
}

// The main window has its own column and never moves: floating it means bringing that column into
// view (narrow screens scroll the whole page) and a flash. Like raiseWin, a draft being typed in
// another window keeps its place on screen.
function raiseMain({ quiet = false } = {}) {
  const w = S.wins.get(MANAGER_ID);
  if (!w) return;
  const here = document.hasFocus();
  const typing = document.activeElement?.closest('.win');
  const busy = typing && typing !== w.el && (here || document.activeElement.value?.trim());
  const r = w.el.getBoundingClientRect();
  if (!busy && (r.top < -1 || r.top > innerHeight - 60)) {
    w.el.scrollIntoView({ block: 'start', behavior: here ? 'smooth' : 'instant' });
  }
  const cls = quiet ? 'flash' : 'attn';
  w.el.classList.remove('flash', 'attn');
  void w.el.offsetWidth;
  w.el.classList.add(cls);
}

function showJump(w) {
  let b = document.getElementById('win-jump');
  if (!b) document.body.append(b = h('button', { id: 'win-jump', type: 'button' }));
  b.textContent = tr('↑ Needs you: {title}', { title: w.task.title });
  b.hidden = false;
  b.onclick = () => {
    b.hidden = true;
    w.el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    w.el.classList.remove('attn');
    void w.el.offsetWidth;
    w.el.classList.add('attn');
  };
  clearTimeout(showJump.timer);
  removeEventListener('focus', showJump.arm);
  // Counted from when the user is back on this page, or it would be gone before they see it.
  showJump.arm = () => { showJump.timer = setTimeout(() => { b.hidden = true; }, 30000); };
  if (document.hasFocus()) showJump.arm(); else addEventListener('focus', showJump.arm, { once: true });
}

// Windows that need the user now float to the front: new ones, ones waiting for a yes/no, and
// open ones whose turn just stopped after replying (a compaction's end has nothing to read).
// Otherwise only messages move windows (see liven), never a bare status change.
function attend(prev, t, created) {
  if (POPOUT) return;
  const changed = !prev || prev.status !== t.status;
  if (t.id === MANAGER_ID) { // its turn stopped after replying
    if (prev && changed && ACTIVE.has(prev.status) && NEEDS.has(t.status) && replied.get(t.id) === t.turn_started_at) raiseMain();
    return;
  }
  if (created || (changed && t.status === 'waiting')) raiseWin(t.id);
  else if (prev && changed && ACTIVE.has(prev.status) && NEEDS.has(t.status) && S.wins.has(t.id)
           && replied.get(t.id) === t.turn_started_at) raiseWin(t.id);
  else if (prev && changed && !ACTIVE.has(prev.status) && ACTIVE.has(t.status) && livened.get(t.id) === NEXT_TURN) {
    livened.set(t.id, t.turn_started_at); // the message that started this turn already floated it
  }
  if (created) markSeen();
}

// A conversation with a new message (from the user, the manager or Slack) or a new reply floats to
// the front. Once per turn, so streaming replies don't reshuffle the grid. A new message opens a
// closed window; a reply only floats an open one, so a window closed mid-turn stays closed.
const livened = new Map(), replied = new Map(), NEXT_TURN = 'next';
function liven(t, ev) {
  if (POPOUT) return;
  const msg = ev.type === 'user' && ev.actor !== 'history'
    && (ev.actor !== 'system' || (ev.text || '').startsWith('[Slack]')); // not "resume after restart"
  const turn = ACTIVE.has(t.status) ? t.turn_started_at : null;
  if (ev.type === 'text' && turn) replied.set(t.id, turn);
  if (t.id === MANAGER_ID) { // the manager's first reply of a turn
    if (ev.type === 'text' && turn && livened.get(t.id) !== turn) { livened.set(t.id, turn); raiseMain({ quiet: true }); }
    return;
  }
  if (!(msg || (ev.type === 'text' && turn && S.wins.has(t.id)))) return;
  if (turn && livened.get(t.id) === turn) return;
  livened.set(t.id, turn ?? NEXT_TURN); // sent while idle: the turn it starts counts as floated
  raiseWin(t.id, { quiet: true });
}

// Newest window this browser has seen, so a reload can open the ones created while no page was live.
const SEEN_KEY = 'learnme.seenAt';
function lastSeen() {
  try { return Number(localStorage.getItem(SEEN_KEY)) || 0; } catch { return 0; }
}
function markSeen() {
  if (POPOUT) return;
  const at = Math.max(0, ...subTasks().map(t => t.created_at || 0));
  try { if (at > lastSeen()) localStorage.setItem(SEEN_KEY, String(at)); } catch { /* storage unavailable */ }
}

function closeWin(id) {
  const w = S.wins.get(id);
  if (!w) return;
  w.scrollResize.disconnect();
  w.scrollMutation.disconnect();
  if (w.scrollFrame != null) cancelAnimationFrame(w.scrollFrame);
  w.el.remove();
  S.wins.delete(id);
  saveOpen();
  updateEmpty();
  scheduleSidebar();
}

function updateEmpty() {
  document.getElementById('empty-hint').hidden = POPOUT || [...S.wins.keys()].some(id => id !== MANAGER_ID);
}

// ---------- sidebar + counters ----------

let sidePending = false;
function scheduleSidebar() {
  if (sidePending) return;
  sidePending = true;
  requestAnimationFrame(() => {
    sidePending = false;
    renderSidebar();
    renderCounters();
  });
}

function setFilter(k) {
  S.filter = k;
  document.body.classList.add('side-open');
  renderSidebar();
}

function subTasks() {
  return [...S.tasks.values()].filter(t => t.id !== MANAGER_ID);
}

// Latest message or status change: a message added mid-turn changes no status. Streamed replies
// don't count, or two busy windows would keep swapping places.
const actAt = t => Math.max(t.updated_at || 0, S.act.get(t.id) || 0);

function renderSidebar() {
  if (POPOUT) return;
  const tasks = subTasks();
  document.getElementById('filters').replaceChildren(...FILTERS.map(([k, label, fn]) =>
    h('button', { class: S.filter === k ? 'on' : '', onclick: () => { S.filter = k; renderSidebar(); } },
      label, h('span', { class: 'n', text: tasks.filter(fn).length }))));

  const fn = FILTERS.find(f => f[0] === S.filter)[2];
  const shown = tasks.filter(fn).sort((a, b) => actAt(b) - actAt(a));
  const list = document.getElementById('task-list');
  if (!shown.length) {
    list.replaceChildren(h('div', { class: 'side-empty', text: S.filter === 'needs' ? tr('Nothing waiting for you') : tr('No tasks yet') }));
    return;
  }
  const groups = new Map();
  for (const t of shown) {
    if (!groups.has(t.cwd)) groups.set(t.cwd, []);
    groups.get(t.cwd).push(t);
  }
  list.replaceChildren(...[...groups].flatMap(([cwd, ts]) => [
    h('div', { class: 'group-h' }, h('span', { text: baseName(cwd), title: cwd }), h('span', { text: ts.length })),
    ...ts.map(t => h('div', {
      class: 'task-row' + (S.wins.has(t.id) ? ' open' : ''), title: t.title,
      onclick: () => { openWin(t.id, { focus: true }); document.body.classList.remove('side-open'); },
    },
    h('span', { class: 'dot ' + t.status }),
    h('div', { style: 'min-width:0' },
      h('div', { class: 'tt' }, t.origin === 'manager' ? h('span', { class: 'by', text: '↳', title: tr('Dispatched by the manager') })
        : t.origin === 'slack' ? h('span', { class: 'by', text: '💬', title: tr('Dispatched for a Slack request') })
          : t.origin === 'event' ? h('span', { class: 'by', text: '📥', title: tr('Opened by outside input') }) : null, t.title),
      h('div', { class: 'st', text: `${STATUS[t.status] || t.status} · ${ago(actAt(t))}` })))),
  ]));
}

function renderCounters() {
  const tasks = subTasks();
  const n = pred => tasks.filter(pred).length;
  const running = n(t => t.status === 'running');
  const waiting = n(t => t.status === 'waiting');
  const needs = n(t => NEEDS.has(t.status));
  if (!POPOUT) {
    document.getElementById('counters').replaceChildren(
      h('span', { class: 'counter', onclick: () => setFilter('active') }, tr('Running') + ' ', h('b', { text: running })),
      h('span', { class: 'counter' + (waiting ? ' hot' : ''), onclick: () => setFilter('needs') }, tr('Waiting for you') + ' ', h('b', { text: waiting })),
      h('span', { class: 'counter' + (needs ? ' hot' : ''), onclick: () => setFilter('needs') }, tr('Needs a decision') + ' ', h('b', { text: needs })));
  }
  const attention = waiting + needs;
  const name = POPOUT ? (POPOUT === MANAGER_ID ? tr('Main window') : S.tasks.get(POPOUT)?.title || 'learnMe') : 'learnMe';
  document.title = (attention && !POPOUT ? `(${attention}) ` : '') + name;
}

// ---------- notifications when the tab is in the background ----------

function notifyChange(prev, t) {
  if (!prev || t.id === MANAGER_ID || prev.status === t.status) return;
  if (!document.hidden || !('Notification' in window) || Notification.permission !== 'granted') return;
  let body = null;
  if (t.status === 'waiting') body = tr('Waiting for you');
  else if (ACTIVE.has(prev.status) && NEEDS.has(t.status)) body = STATUS[t.status];
  if (body) new Notification(t.title, { body: `${baseName(t.cwd)} · ${body}`, tag: t.id });
}
document.addEventListener('click', () => {
  if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
}, { once: true });

// ---------- state sync ----------

function upsertTask(t) {
  S.tasks.set(t.id, t);
  S.wins.get(t.id)?.refresh();
  scheduleSidebar();
}

async function refreshState() {
  const st = await api('/api/state');
  const before = new Map(S.tasks);
  const seen = new Set([MANAGER_ID]);
  S.tasks.set(MANAGER_ID, st.manager);
  for (const t of st.tasks) {
    S.tasks.set(t.id, t);
    seen.add(t.id);
  }
  for (const id of [...S.tasks.keys()]) if (!seen.has(id)) S.tasks.delete(id);
  for (const w of S.wins.values()) w.refresh();
  applySettings(st.settings);
  document.getElementById('demo-tag').hidden = !st.demo;
  renderBackends(st.backends || [], st.manager);
  scheduleSidebar();
  // After a reconnect or resync: the created/waiting messages sent in between never arrived.
  if (S.booted) {
    for (const t of subTasks().sort((a, b) => a.updated_at - b.updated_at)) {
      attend(before.get(t.id), t, !before.has(t.id) && t.status !== 'done');
    }
    markSeen();
  }
}

// Backend pickers: the new-task dialog's, and the manager's in the top bar.
function renderBackends(list, manager) {
  S.backends = list;
  const label = b => b.name + (b.problems.length ? ` (${tr('unavailable')})` : '');
  const nt = document.getElementById('nt-backend');
  const keep = nt.value;
  nt.replaceChildren(h('option', { value: '', text: `${tr('Default')} (${(list.find(b => b.default) || {}).name || ''})` }),
    ...list.map(b => h('option', { value: b.name, text: label(b), disabled: b.problems.length > 0, title: b.problems.join('; ') })));
  nt.value = keep;
  updateModels();
  const mb = document.getElementById('manager-backend');
  mb.replaceChildren(...list.map(b => h('option', { value: b.name, text: label(b), disabled: b.problems.length > 0, title: b.problems.join('; ') })));
  mb.value = manager?.backend || (list.find(b => b.manager) || {}).name || '';
}

function updateModels() {
  const name = document.getElementById('nt-backend').value || (S.backends?.find(b => b.default) || {}).name;
  const b = (S.backends || []).find(x => x.name === name);
  document.getElementById('model-list').replaceChildren(...((b && b.models) || []).map(m => h('option', { value: m })));
}

function applySettings(settings) {
  S.settings = settings || {};
  document.getElementById('supervise').checked = !!S.settings.supervise;
}

function onMessage(msg) {
  if (msg.kind === 'event') {
    const ev = msg.event;
    S.wins.get(ev.task_id)?.receive(ev);
    const t = S.tasks.get(ev.task_id);
    if (t && ev.seq != null && (ev.type === 'text' || ev.type === 'user')) {
      liven(t, ev);
      if (ev.type === 'user') { S.act.set(t.id, ev.ts); scheduleSidebar(); }
    }
    // A new Slack message for a conversation that already has its window.
    if (ev.type === 'user' && ev.actor === 'system' && (ev.text || '').startsWith('[Slack]')
        && S.tasks.get(ev.task_id)?.origin === 'slack') raiseWin(ev.task_id);
  } else if (msg.kind === 'task') {
    const prev = S.tasks.get(msg.task.id);
    upsertTask(msg.task);
    attend(prev, msg.task, !!msg.created);
    notifyChange(prev, msg.task);
  } else if (msg.kind === 'memory_change') {
    onMemoryChange(msg.change);
  } else if (msg.kind === 'settings') {
    applySettings(msg.settings);
  } else if (msg.kind === 'resync') {
    refreshState().catch(() => {});
    for (const w of S.wins.values()) w.load();
  }
}

function setConn(ok) {
  const el = document.getElementById('conn');
  el.className = 'conn' + (ok ? '' : ' off');
  el.textContent = ok ? tr('● Connected') : tr('● Reconnecting…');
}

function openInitial() {
  if (POPOUT) {
    document.body.classList.add('popout');
    openWin(POPOUT);
    return;
  }
  const saved = savedOpen(); // read before openWin() saves the (still empty) grid
  openWin(MANAGER_ID);
  // First visit in this browser: show whatever is still in flight.
  const ids = saved ?? subTasks().filter(t => t.status !== 'done')
    .sort((a, b) => b.updated_at - a.updated_at).slice(0, 6).map(t => t.id);
  for (const id of ids) if (S.tasks.has(id)) openWin(id, { prepend: false });
  // Windows created while no page was connected (closed, asleep, or reloaded before reconnecting).
  const since = lastSeen();
  if (since) {
    for (const t of subTasks().filter(t => t.created_at > since && t.status !== 'done')
      .sort((a, b) => a.created_at - b.created_at)) raiseWin(t.id);
  }
  markSeen();
  updateEmpty();
  S.wins.get(MANAGER_ID).msg.focus();
}

function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = async () => {
    setConn(true);
    try {
      await refreshState();
    } catch (e) {
      toast(tr('Failed to load state: {msg}', { msg: e.message }), true);
      return;
    }
    if (!S.booted) {
      S.booted = true;
      openInitial();
    } else {
      for (const w of S.wins.values()) w.load();
    }
  };
  ws.onmessage = m => onMessage(JSON.parse(m.data));
  ws.onclose = () => {
    setConn(false);
    setTimeout(connect, 1500);
  };
  clearInterval(connect.ping);
  connect.ping = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send('ping'); }, 25000);
}

// ---------- direct dispatch dialog ----------

const LAST_CWD_KEY = 'learnme.lastCwd';

async function loadProjects() {
  try {
    S.projects = await api('/api/projects');
  } catch (e) {
    toast(tr('Failed to load projects: {msg}', { msg: e.message }), true);
    return;
  }
  document.getElementById('project-list').replaceChildren(...S.projects.map(p =>
    h('option', { value: p.path, label: p.uses ? tr('{name} · used {n}×', { name: p.name, n: p.uses }) : p.name })));
}

function setupDialog() {
  const dlg = document.getElementById('new-task');
  const form = document.getElementById('new-task-form');
  const cwd = document.getElementById('nt-cwd');
  const prompt = document.getElementById('nt-prompt');
  const att = new Attach(prompt, form, form.querySelector('.row.end'));
  document.getElementById('new-task-btn').onclick = () => {
    if (!cwd.value) { try { cwd.value = localStorage.getItem(LAST_CWD_KEY) || ''; } catch { /* ignore */ } }
    dlg.showModal();
    prompt.focus();
    loadProjects();
  };
  document.getElementById('nt-cancel').onclick = () => dlg.close();
  prompt.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing) form.requestSubmit();
  });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const text = att.compose(prompt.value.trim());
    if (text == null) return;
    const body = {
      cwd: cwd.value.trim(),
      prompt: text,
      model: document.getElementById('nt-model').value || null,
      permission_mode: document.getElementById('nt-perm').value || null,
      backend: document.getElementById('nt-backend').value || null,
    };
    if (!body.cwd || !body.prompt) return;
    try {
      const t = await api('/api/tasks', body);
      try { localStorage.setItem(LAST_CWD_KEY, body.cwd); } catch { /* ignore */ }
      prompt.value = '';
      att.clear();
      dlg.close();
      upsertTask(t);
      openWin(t.id, { focus: true });
    } catch (err) {
      toast(tr('Dispatch failed: {msg}', { msg: err.message }), true);
    }
  });
}

// ---------- memory ----------

const ACTOR_LABEL = { user: tr('You'), manager: tr('Manager'), task: tr('Task'), external: tr('External') };
const M = { tab: 'files', groups: [], selected: null, editing: false, unseen: 0 };

function diffView(diff) {
  const pre = h('pre', { class: 'diff' });
  for (const line of (diff || '').split('\n')) {
    const cls = line.startsWith('@@') ? 'hunk'
      : line.startsWith('+') && !line.startsWith('+++') ? 'add'
        : line.startsWith('-') && !line.startsWith('---') ? 'del' : null;
    pre.append(h('span', { class: cls }, line + '\n'));
  }
  return pre;
}

async function revertMemory(id) {
  try {
    await api(`/api/memory/revert/${id}`, {});
    toast(tr('Reverted this memory change'));
  } catch (e) {
    toast(e.message, true);
  }
}

function onMemoryChange(ch) {
  const by = ACTOR_LABEL[ch.actor] || ch.actor;
  if (ch.actor !== 'user') toast(tr('🧠 Memory updated: {where} ({by})', { where: `${ch.scope_label} / ${ch.name}`, by }));
  const drawer = document.getElementById('memory-drawer');
  if (drawer.hidden) {
    M.unseen += 1;
    renderMemoryBadge();
  } else if (!M.editing) {
    loadMemory();
  }
}

function renderMemoryBadge() {
  const b = document.getElementById('memory-badge');
  b.hidden = !M.unseen;
  b.textContent = M.unseen;
}

async function loadMemory() {
  try {
    const [mem, changes] = await Promise.all([api('/api/memory'), api('/api/memory/changes?limit=100')]);
    M.groups = mem.groups;
    M.over = new Set(mem.over_budget.map(o => o.path));
    document.getElementById('memory-budget').textContent = mem.over_budget.length
      ? tr('Always-loaded memory needs layering: ') + mem.over_budget.map(o => `${o.scope_label} ${o.name} ${o.size}/${o.limit}${o.unit}`).join(', ') : '';
    renderMemoryList();
    renderMemoryChanges(changes);
  } catch (e) {
    toast(tr('Failed to load memory: {msg}', { msg: e.message }), true);
  }
}

function memoryFile(path) {
  for (const g of M.groups) for (const f of g.files) if (f.path === path) return f;
  return null;
}

function groupMeta(g) {
  const st = g.stats || {};
  const lint = g.lint || {};
  const fmt = n => fmtTok(n);
  const bits = [h('span', { text: tr('{n} chars total', { n: fmt(st.chars || 0) }) })];
  if (st.loaded_chars) bits.push(h('span', { text: tr('{n} chars always loaded', { n: fmt(st.loaded_chars) }) }));
  if (lint.orphans) bits.push(h('span', { class: 'warn', text: tr('{n} orphans', { n: lint.orphans }), title: tr('Memory files in no index') }));
  if (lint.dead_links) bits.push(h('span', { class: 'warn', text: tr('{n} dead links', { n: lint.dead_links }) }));
  if (lint.stale) bits.push(h('span', { class: 'warn', text: tr('{n} possibly stale', { n: lint.stale }), title: tr('In-progress-type entries not updated for 30+ days') }));
  if (g.restructurable) {
    bits.push(h('button', {
      class: 'ghost', text: tr('Layer'), title: tr('Dispatch a task into this project: reorganize and archive stale state, delete no knowledge'),
      onclick: async e => {
        e.stopPropagation();
        try {
          const t = await api('/api/memory/restructure', { scope: g.scope });
          toast(tr('Dispatched a layering task'));
          document.getElementById('memory-drawer').hidden = true;
          upsertTask(t);
          openWin(t.id, { focus: true });
        } catch (err) {
          toast(err.message, true);
        }
      },
    }));
  }
  return h('div', { class: 'mg-meta' }, bits);
}

function renderMemoryList() {
  const nav = document.getElementById('memory-list');
  nav.replaceChildren(...M.groups.flatMap(g => [
    h('div', { class: 'group-h', title: g.dir }, h('span', { text: g.label }), h('span', { text: tr('{n} files', { n: g.files.length }) })),
    groupMeta(g),
    ...g.files.map(f => h('div', {
      class: 'mf' + (f.path === M.selected ? ' on' : '') + (M.over.has(f.path) ? ' over' : ''),
      title: f.path, text: f.name,
      onclick: () => { M.selected = f.path; M.editing = false; renderMemoryList(); renderMemoryView(); },
    })),
  ]));
  renderMemoryView();
}

async function searchMemory(q) {
  const view = document.getElementById('memory-view');
  if (!q.trim()) { renderMemoryView(); return; }
  try {
    const hits = await api(`/api/memory/search?q=${encodeURIComponent(q)}&limit=30`);
    M.selected = null;
    view.replaceChildren(
      h('div', { class: 'mv-head' }, h('span', { class: 'p', text: tr('"{q}": {n} hits', { q, n: hits.length }) })),
      ...(hits.length ? hits.map(x => h('div', {
        class: 'mr', title: x.path,
        onclick: () => { M.selected = x.path; M.editing = false; renderMemoryList(); },
      },
      h('div', { class: 'mr-h' }, h('b', { text: x.scope }), ` / ${x.path.split('/').pop()}${x.section ? ' · ' + x.section : ''}`),
      h('div', { class: 'mr-s', text: x.snippet }))) : [h('div', { class: 'mv-empty', text: tr('Nothing found') })]));
  } catch (e) {
    toast(tr('Search failed: {msg}', { msg: e.message }), true);
  }
}

function renderMemoryView() {
  const view = document.getElementById('memory-view');
  const f = M.selected && memoryFile(M.selected);
  if (!f) {
    view.replaceChildren(h('div', { class: 'mv-empty', text: tr('Pick a memory file on the left') }));
    return;
  }
  const head = h('div', { class: 'mv-head' }, h('span', { class: 'p', text: f.path, title: f.path }));
  if (M.editing) {
    const ta = h('textarea', {}, f.content);
    head.append(
      h('button', { class: 'ghost', text: tr('Cancel'), onclick: () => { M.editing = false; renderMemoryView(); } }),
      h('button', {
        class: 'primary', text: tr('Save'),
        onclick: async () => {
          try {
            await api('/api/memory/file', { path: f.path, content: ta.value });
            M.editing = false;
            toast(tr('Saved; this edit is remembered as your correction'));
            loadMemory();
          } catch (e) {
            toast(e.message, true);
          }
        },
      }));
    view.replaceChildren(head, ta);
    ta.focus();
  } else {
    head.append(h('span', { class: 'st', text: ago(f.mtime) }),
      h('button', { text: tr('Edit'), onclick: () => { M.editing = true; renderMemoryView(); } }));
    view.replaceChildren(head, md(f.content));
  }
}

function renderMemoryChanges(changes) {
  const box = document.getElementById('memory-changes');
  if (!changes.length) {
    box.replaceChildren(h('div', { class: 'mv-empty', text: tr('No memory changes yet') }));
    return;
  }
  box.replaceChildren(...changes.map(c => h('div', { class: 'mc' },
    h('div', { class: 'mc-head' },
      h('span', { class: 'when', text: new Date(c.ts * 1000).toLocaleString('zh-CN', { hour12: false }) }),
      h('span', { class: `actor ${c.actor}`, text: ACTOR_LABEL[c.actor] || c.actor }),
      h('span', { class: 'file', text: `${c.scope_label} / ${c.name}` }),
      h('span', { class: 'badge', text: { created: tr('Created'), deleted: tr('Deleted'), modified: tr('Modified') }[c.kind] }),
      c.task_id && S.tasks.has(c.task_id) ? h('a', {
        href: '#', text: c.task_id === MANAGER_ID ? tr('Main window') : taskName(c.task_id),
        onclick: e => { e.preventDefault(); openWin(c.task_id, { focus: true }); },
      }) : null,
      h('span', { class: 'grow' }),
      h('button', { class: 'ghost', text: tr('Revert'), onclick: () => revertMemory(c.id) })),
    diffView(c.diff))));
}

function setupMemory() {
  const drawer = document.getElementById('memory-drawer');
  document.getElementById('memory-btn').onclick = () => {
    const open = drawer.hidden;
    closeDrawers();
    drawer.hidden = !open;
    if (!drawer.hidden) {
      M.unseen = 0;
      renderMemoryBadge();
      loadMemory();
    }
  };
  document.getElementById('memory-close').onclick = () => { drawer.hidden = true; };
  let searchTimer;
  document.getElementById('memory-q').addEventListener('input', e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => searchMemory(e.target.value), 250);
  });
  document.getElementById('memory-tabs').addEventListener('click', e => {
    const b = e.target.closest('[data-tab]');
    if (!b) return;
    M.tab = b.dataset.tab;
    document.querySelectorAll('#memory-tabs button').forEach(x => x.classList.toggle('on', x === b));
    document.getElementById('memory-files').hidden = M.tab !== 'files';
    document.getElementById('memory-changes').hidden = M.tab !== 'changes';
  });
}

// ---------- history ----------

const HZ = { tab: 'windows', seq: 0 };
const ENTRY_LABEL = { 'claude-vscode': 'VSCode', cli: tr('Terminal'), 'sdk-py': 'SDK', 'sdk-ts': 'SDK' };

function bucketOf(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = (today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000;
  return diff <= 0 ? tr('Today') : diff === 1 ? tr('Yesterday') : diff < 7 ? tr('This week') : tr('Earlier');
}

function historyRow({ title, status, meta, preview, action, onOpen }) {
  return h('div', { class: 'hr', onclick: onOpen },
    h('div', { class: 'hr-main' },
      h('div', { class: 'hr-t' }, status ? h('span', { class: 'dot ' + status }) : null, h('span', { class: 'tt', text: title })),
      h('div', { class: 'hr-m' }, meta),
      preview ? h('div', { class: 'hr-p', text: preview }) : null),
    h('button', { class: 'ghost', text: action, onclick: e => { e.stopPropagation(); onOpen(); } }));
}

async function loadHistory() {
  const list = document.getElementById('history-list');
  const q = document.getElementById('history-q').value.trim();
  const seq = ++HZ.seq;
  list.replaceChildren(h('div', { class: 'mv-empty', text: HZ.tab === 'sessions' ? tr('Scanning Claude Code sessions…') : tr('Loading…') }));
  let rows;
  try {
    rows = await api(`/api/history/${HZ.tab}?q=${encodeURIComponent(q)}`);
  } catch (e) {
    toast(tr('Failed to load history: {msg}', { msg: e.message }), true);
    return;
  }
  if (seq !== HZ.seq) return; // a newer search is on its way
  document.getElementById('history-count').textContent = tr('{n} items', { n: rows.length });
  const items = HZ.tab === 'windows' ? rows.map(t => ({
    ts: t.updated_at,
    row: historyRow({
      title: t.title, status: t.status,
      meta: [h('span', { class: 'proj', text: t.project }),
        ` · ${ago(t.updated_at)} · ${STATUS[t.status] || t.status}${t.turns ? ` · ${tr('{n} steps', { n: t.turns })}` : ''}${t.cost_usd ? ` · $${t.cost_usd.toFixed(2)}` : ''}`],
      preview: t.end_reason ? tr('Ended: {reason}', { reason: t.end_reason }) : t.preview,
      action: tr('Open'),
      onOpen: () => { closeDrawers(); upsertTask(t); openWin(t.id, { focus: true }); },
    }),
  })) : rows.map(r => {
    const ts = Date.parse(r.ended || r.started) / 1000;
    return {
      ts,
      row: historyRow({
        title: r.title || (r.first_prompt || '').split('\n')[0] || r.session_id,
        meta: [h('span', { class: 'proj', text: r.project || '?' }),
          ` · ${ago(ts)} · ${ENTRY_LABEL[r.entrypoint] || r.entrypoint || 'cli'} · ${tr('{n} prompts', { n: r.prompts })}`],
        preview: r.last_prompt ? tr('Last: {text}', { text: r.last_prompt }) : r.first_prompt,
        action: tr('Restore as window'),
        onOpen: async () => {
          try {
            const t = await api('/api/history/restore', { path: r.path });
            closeDrawers();
            upsertTask(t);
            openWin(t.id, { focus: true });
            toast(tr('Restored: your next message continues from the original context'));
          } catch (e) {
            toast(e.message, true);
          }
        },
      }),
    };
  });
  if (!items.length) {
    list.replaceChildren(h('div', { class: 'mv-empty', text: q ? tr('Nothing found') : tr('No history yet') }));
    return;
  }
  const out = [];
  let last = null;
  for (const it of items) {
    const b = bucketOf(it.ts);
    if (b !== last) { out.push(h('div', { class: 'group-h' }, h('span', { text: b }))); last = b; }
    out.push(it.row);
  }
  list.replaceChildren(...out);
}

function closeDrawers() {
  document.getElementById('history-drawer').hidden = true;
  document.getElementById('memory-drawer').hidden = true;
}

function setupHistory() {
  const drawer = document.getElementById('history-drawer');
  document.getElementById('history-btn').onclick = () => {
    const open = drawer.hidden;
    closeDrawers();
    drawer.hidden = !open;
    if (open) {
      loadHistory();
      document.getElementById('history-q').focus();
    }
  };
  document.getElementById('history-close').onclick = () => { drawer.hidden = true; };
  document.getElementById('history-tabs').addEventListener('click', e => {
    const b = e.target.closest('[data-tab]');
    if (!b) return;
    HZ.tab = b.dataset.tab;
    document.querySelectorAll('#history-tabs button').forEach(x => x.classList.toggle('on', x === b));
    loadHistory();
  });
  let timer;
  document.getElementById('history-q').addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(loadHistory, 250);
  });
}

// ---------- boot ----------

document.getElementById('side-toggle').onclick = () => document.body.classList.toggle('side-open');
document.getElementById('supervise').addEventListener('change', async e => {
  try {
    applySettings(await api('/api/settings', { supervise: e.target.checked }));
    toast(e.target.checked ? tr('Auto-supervision on') : tr('Auto-supervision off'));
  } catch (err) {
    toast(err.message, true);
    applySettings(S.settings);
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !e.target.closest('textarea, input')) {
    document.querySelectorAll('.win.max').forEach(x => x.classList.remove('max'));
    closeDrawers();
  }
});
setInterval(() => {
  for (const w of S.wins.values()) if (w.active) w.updateMeta();
}, 1000);
setInterval(scheduleSidebar, 30000);

document.getElementById('nt-backend').addEventListener('change', updateModels);
document.getElementById('manager-backend').addEventListener('change', async e => {
  const name = e.target.value;
  try {
    upsertTask(await api('/api/manager/backend', { backend: name }));
    toast(tr('The manager now runs on {name}', { name }));
  } catch (err) {
    toast(err.message, true);
    e.target.value = S.tasks.get(MANAGER_ID)?.backend || '';
  }
});

setupCompactTools();
setupDialog();
setupMemory();
setupHistory();
connect();
