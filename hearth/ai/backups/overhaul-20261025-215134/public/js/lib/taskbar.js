// Desktop: Scorp's own taskbar along the bottom of the screen, in place of a sidebar. Pages are icons, chats and
// projects you open become tabs (reorder by dragging, middle-click or × to close), the search field finds chats,
// projects and pages or asks Scorp straight away, and the start button lists everything. Phones keep the drawer.

import { $, clear, fmtRelative, h, icon, isMobile } from './dom.js';
import { bus, prefs, setPref, state } from '../store.js';
import { navigate } from '../router.js';

const MAX_TABS = 14;
const PAGES = [
  ['chat', 'Chat', '/', 'New chat'],
  ['code', 'Code', '/code', 'Coding projects'],
  ['agent', 'Agents', '/agents', 'Your team and what it is working on'],
  ['library', 'Library', '/library', 'Documents and files'],
  ['calendar', 'Agenda', '/agenda', 'Events and reminders'],
  ['routine', 'Routines', '/routines', 'Automations and background tasks'],
  ['memory', 'Memory', '/memory', 'What Scorp remembers'],
  ['plug', 'Connections', '/connections', 'Model, programs, permissions, MCP'],
  ['settings', 'Settings', '/settings', 'Appearance, voice, devices'],
];

let els = {};
let results = []; // what the panel lists, in keyboard order: { run }
let sel = 0;

const tabs = () => (Array.isArray(prefs.tabs) ? prefs.tabs : []);
const saveTabs = (list) => setPref('tabs', list.slice(-MAX_TABS));

/** The chat or project on screen as a tab path ('/' for a new chat), or null on other pages. */
function currentPath() {
  if (state.view !== 'chat') return null;
  if (state.project) return '/code/' + encodeURIComponent(state.project.slug);
  if (state.activeId) return '/c/' + state.activeId;
  return '/';
}

function ensureTab(path) {
  if (!path || path === '/' || tabs().includes(path)) return;
  saveTabs([...tabs(), path]);
}

function describe(path) {
  if (path.startsWith('/c/')) {
    const conv = state.conversations.find((c) => c.id === path.slice(3));
    return conv ? { title: conv.title || 'Untitled', live: !!conv.running, ic: 'chat', found: true } : { title: 'Conversation', ic: 'chat', found: false };
  }
  const slug = decodeURIComponent(path.slice(6));
  const proj = (state.project && state.project.slug === slug && state.project) || state.projects.find((p) => p.slug === slug);
  return { title: proj ? proj.name : slug, live: !!(proj && proj.job && proj.job.status === 'running'), ic: 'code', found: true };
}

function closeTab(path) {
  const list = tabs();
  const i = list.indexOf(path);
  if (i < 0) return;
  const rest = list.filter((p) => p !== path);
  saveTabs(rest);
  if (path === currentPath()) navigate(rest[Math.min(i, rest.length - 1)] || '/');
  else renderTabs();
}

function renderTabs() {
  const cur = currentPath();
  // forget tabs whose conversation was deleted (once the list has loaded; never the one on screen)
  if (state.conversations.length) {
    const kept = tabs().filter((p) => p === cur || !p.startsWith('/c/') || describe(p).found);
    if (kept.length !== tabs().length) saveTabs(kept);
  }
  const list = [...tabs()];
  if (cur === '/') list.push('/'); // a new chat shows as a tab until its first message makes it real
  clear(els.tabs);
  list.forEach((path) => {
    const d = path === '/' ? { title: state.mode === 'code' ? 'New project' : 'New chat', ic: 'compose', ghost: true } : describe(path);
    const x = h('span.tk-x', { role: 'button', title: 'Close tab', onclick: (e) => (e.stopPropagation(), path === '/' ? navigate(tabs().at(-1) || '/') : closeTab(path)) }, icon('x'));
    const tab = h(
      'button.tk-tab' + (path === cur ? '.on' : '') + (d.ghost ? '.ghost' : ''),
      {
        type: 'button',
        role: 'tab',
        title: d.title,
        draggable: !d.ghost,
        dataset: { path },
        'aria-selected': String(path === cur),
        onclick: () => path !== cur && navigate(path),
        onauxclick: (e) => e.button === 1 && !d.ghost && closeTab(path),
      },
      d.live ? h('span.tk-live', { title: 'Working' }) : icon(d.ic, 'tk-tic'),
      h('span.tk-tl', d.title),
      d.ghost && !tabs().length ? null : x,
    );
    els.tabs.appendChild(tab);
  });
  const on = els.tabs.querySelector('.tk-tab.on');
  if (on) on.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

// drag a tab sideways to reorder
function initTabDrag() {
  let dragged = null;
  els.tabs.addEventListener('dragstart', (e) => {
    dragged = e.target.closest('.tk-tab');
    if (!dragged) return;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragged.dataset.path);
    requestAnimationFrame(() => dragged && dragged.classList.add('dragging'));
  });
  els.tabs.addEventListener('dragover', (e) => {
    if (!dragged) return;
    e.preventDefault();
    const over = e.target.closest('.tk-tab:not(.ghost)');
    if (!over || over === dragged) return;
    const r = over.getBoundingClientRect();
    els.tabs.insertBefore(dragged, e.clientX < r.left + r.width / 2 ? over : over.nextSibling);
  });
  els.tabs.addEventListener('dragend', () => {
    if (!dragged) return;
    dragged.classList.remove('dragging');
    dragged = null;
    saveTabs([...els.tabs.querySelectorAll('.tk-tab:not(.ghost)')].map((t) => t.dataset.path));
  });
  // the mouse wheel scrolls a crowded tab strip sideways
  els.tabs.addEventListener('wheel', (e) => {
    if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
      els.tabs.scrollLeft += e.deltaY;
      e.preventDefault();
    }
  }, { passive: false });
}

// ---------- search / start panel ----------

function openPanel() {
  renderPanel();
  // the panel rises from the search field
  els.panel.style.left = Math.max(6, Math.min($('#tkSearchBox').offsetLeft - 4, els.bar.clientWidth - 466)) + 'px';
  els.panel.classList.remove('hidden');
  els.start.setAttribute('aria-expanded', 'true');
  els.bar.classList.add('searching');
}

function closePanel() {
  els.panel.classList.add('hidden');
  els.start.setAttribute('aria-expanded', 'false');
  els.bar.classList.remove('searching');
}

export function togglePanel() {
  if (!els.panel.classList.contains('hidden')) {
    closePanel();
    els.input.blur();
    return;
  }
  if (document.activeElement === els.input) openPanel();
  else els.input.focus();
}

function item(ic, title, sub, run, extra = '') {
  const i = results.length;
  results.push({ run });
  return h(
    'button.tk-item' + extra + (i === sel ? '.sel' : ''),
    { type: 'button', dataset: { i: String(i) }, onmousemove: () => select(i), onclick: () => pick(i) },
    typeof ic === 'string' ? icon(ic) : ic,
    h('span.tk-it', title),
    sub ? h('span.tk-is', sub) : null,
  );
}

const when = (ts) => fmtRelative(ts).replace(' ago', '');

function renderPanel() {
  const q = els.input.value.trim();
  const ql = q.toLowerCase();
  results = [];
  sel = 0;
  clear(els.panel);
  const section = (title, nodes) => nodes.length && els.panel.append(h('div.tk-sec', title), ...nodes);
  const go = (path) => () => navigate(path);

  const convs = state.conversations.filter((c) => !ql || (c.title || '').toLowerCase().includes(ql));
  const projects = state.projects.filter((p) => !ql || p.name.toLowerCase().includes(ql) || p.slug.includes(ql));
  const pages = PAGES.filter(([, name]) => ql && name.toLowerCase().includes(ql));
  // a question goes to Scorp first; a word or two is more likely a chat you are looking for
  const question = q && (q.split(/\s+/).length >= 3 || q.endsWith('?'));
  const ask = () =>
    item(h('span.tk-ask-ic', icon('spark')), h('span', 'Ask Scorp ', h('b', `“${q}”`)), results.length ? '' : 'Enter', () => {
      navigate('/');
      setTimeout(() => bus.emit('compose:send', q), 60);
    }, '.ask');

  if (question) section('Ask', [ask()]);
  if (!q) {
    const pinned = convs.filter((c) => c.pinned);
    section('Pinned', pinned.map((c) => item(c.running ? h('span.tk-live') : 'pin', c.title || 'Untitled', when(c.updated), go('/c/' + c.id))));
    section('Recent chats', convs.filter((c) => !c.pinned).slice(0, 30).map((c) => item(c.running ? h('span.tk-live') : 'chat', c.title || 'Untitled', when(c.updated), go('/c/' + c.id))));
    if (state.mode === 'code') section('Projects', projects.slice(0, 12).map((p) => item('code', p.name, when(p.updated), go('/code/' + encodeURIComponent(p.slug)))));
  } else {
    section('Chats', convs.slice(0, 8).map((c) => item(c.running ? h('span.tk-live') : 'chat', c.title || 'Untitled', when(c.updated), go('/c/' + c.id))));
    section('Projects', projects.slice(0, 5).map((p) => item('code', p.name, 'Project', go('/code/' + encodeURIComponent(p.slug)))));
    section('Pages', pages.map(([ic, name, path, sub]) => item(ic, name, sub, go(path))));
    if (!question) section('Ask', [ask()]);
  }
  if (!results.length) els.panel.appendChild(h('div.tk-empty', 'Your conversations will gather here.'));
}

function select(i) {
  if (!results.length) return;
  sel = (i + results.length) % results.length;
  els.panel.querySelectorAll('.tk-item').forEach((n) => n.classList.toggle('sel', Number(n.dataset.i) === sel));
  const n = els.panel.querySelector('.tk-item.sel');
  if (n) n.scrollIntoView({ block: 'nearest' });
}

function pick(i) {
  const r = results[i];
  if (!r) return;
  els.input.value = '';
  closePanel();
  els.input.blur();
  r.run();
}

// ---------- clock ----------

function tick() {
  const now = new Date();
  els.clock.dateTime = now.toISOString();
  els.clock.replaceChildren(
    h('b', now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })),
    h('span', now.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })),
  );
}

export function initTaskbar() {
  els = { bar: $('#taskbar'), tabs: $('#tkTabs'), input: $('#tkSearch'), panel: $('#tkPanel'), start: $('#tkStart'), clock: $('#tkClock') };
  initTabDrag();
  tick();
  setInterval(tick, 15000);

  els.start.addEventListener('click', togglePanel);
  $('#tkNew').addEventListener('click', () => $('#newChatBtn').click());
  els.input.addEventListener('focus', openPanel);
  els.input.addEventListener('click', () => els.panel.classList.contains('hidden') && openPanel());
  els.input.addEventListener('input', renderPanel);
  els.input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      select(sel + (e.key === 'ArrowDown' ? 1 : -1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      pick(sel);
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      if (els.input.value) {
        els.input.value = '';
        renderPanel();
      } else {
        closePanel();
        els.input.blur();
      }
    }
  });
  document.addEventListener('pointerdown', (e) => {
    if (!els.panel.classList.contains('hidden') && !e.target.closest('.tk-panel, .tk-search, .tk-start')) closePanel();
  });

  // Ctrl+Tab / Ctrl+Shift+Tab walk the tabs; Ctrl+W closes one (desktop app only: a browser keeps Ctrl+W)
  document.addEventListener('keydown', (e) => {
    if (isMobile() || !(e.ctrlKey || e.metaKey)) return;
    const list = tabs();
    const cur = currentPath();
    if (e.key === 'Tab' && list.length) {
      e.preventDefault();
      const i = list.indexOf(cur);
      navigate(list[(i + (e.shiftKey ? -1 : 1) + list.length) % list.length] || '/');
    } else if (e.key.toLowerCase() === 'w' && document.documentElement.classList.contains('framed') && cur && cur !== '/') {
      e.preventDefault();
      closeTab(cur);
    }
  });

  bus.on('route', (r) => {
    if (r.project) ensureTab('/code/' + encodeURIComponent(r.project));
    else if (r.view === 'chat' && r.id) ensureTab('/c/' + r.id);
    closePanel();
    renderTabs();
  });
  // the thread list changed (titles, running state, a new chat got its id)
  bus.on('threads', () => {
    if (state.view === 'chat' && !state.project && state.activeId) ensureTab('/c/' + state.activeId);
    renderTabs();
    if (!els.panel.classList.contains('hidden')) renderPanel();
  });
  renderTabs();
}

/** Model status for the tray: short name, with the provider in the tooltip. */
export function setTrayStatus(online, llmOk, tip, name) {
  $('#tkPulse').className = 'pulse ' + (online && llmOk ? 'on' : 'off');
  $('#tkModel').title = tip;
  $('#tkModelName').textContent = name;
}
