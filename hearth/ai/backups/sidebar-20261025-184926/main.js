// Hearth app shell: auth gate, routing between views, live server events, PWA.

import { api, connectEvents, onAuthLost, setToken } from './lib/api.js';
import { $, $$, clear, fmtRelative, h, icon, isMobile } from './lib/dom.js';
import { popover, sheet, toast } from './lib/ui.js';
import { createMascot } from './lib/mascot.js';
import { initWindowFrame } from './lib/frame.js';
import { TUNING_KEYS, applyAppearance, applyResponseStyle } from './theme.js';
import { loadLooks } from './lib/looks.js';
import { initBackdrop } from './lib/backdrop.js';
import { initSidebarLayout } from './lib/sidebar.js';
import { initWake, takeHandOff } from './wake.js';
import { bus, prefs, setPref, state } from './store.js';
import { navigate, parse } from './router.js';
import * as chat from './views/chat.js';
import { refreshCode, renderCode } from './views/code.js';
import { renderLibrary } from './views/library.js';
import { renderAgenda } from './views/agenda.js';
import { renderRoutines } from './views/routines.js';
import { loadJobCount, renderAgents } from './views/agents.js';
import { renderMemory } from './views/memory.js';
import { renderConnections } from './views/connections.js';
import { renderSettings } from './views/settings.js';

applyAppearance();
initWindowFrame();
initBackdrop($('#backdrop'));

const app = $('#app');

// ---------- auth gate ----------

async function ensureAuth() {
  let st;
  try {
    st = await api.get('/api/auth/status');
  } catch {
    st = { required: false, authed: true };
  }
  if (!st.required || st.authed) return true;
  return new Promise((resolve) => showGate(resolve));
}

function showGate(resolve) {
  const gate = $('#gate');
  const input = $('#pinInput');
  const boxes = $('#pinBoxes');
  const err = $('#gateError');
  gate.classList.remove('hidden');
  // Ember politely covers its eyes while the PIN is typed
  const ember = createMascot({ size: 96 });
  $('#gateMascot').replaceChildren(ember.el);
  const paint = () => {
    ember.set(input.value ? 'shy' : 'idle');
    clear(boxes);
    for (let i = 0; i < 6; i++) {
      boxes.appendChild(h('i' + (input.value[i] ? '.filled' : '') + (i === input.value.length ? '.cur' : ''), input.value[i] ? '•' : ''));
    }
  };
  boxes.addEventListener('click', () => input.focus());
  gate.addEventListener('click', () => input.focus());
  input.addEventListener('input', async () => {
    input.value = input.value.replace(/\D/g, '').slice(0, 6);
    err.textContent = '';
    paint();
    if (input.value.length === 1) ember.hop();
    if (input.value.length === 6) {
      try {
        const res = await api.post('/api/auth/login', { pin: input.value });
        setToken(res.token);
        ember.set('happy').hop();
        await new Promise((r) => setTimeout(r, 650));
        gate.classList.add('hidden');
        ember.destroy();
        resolve(true);
      } catch (e) {
        err.textContent = e.message;
        ember.flash('sad', 1800);
        gate.classList.add('shake');
        setTimeout(() => gate.classList.remove('shake'), 500);
        input.value = '';
        paint();
      }
    }
  });
  paint();
  setTimeout(() => input.focus(), 100);
}

onAuthLost(() => {
  if (!$('#gate').classList.contains('hidden')) return;
  setToken('');
  showGate(() => location.reload());
});

// ---------- data ----------

async function refreshStatus() {
  try {
    state.status = await api.get('/api/status');
    state.online = true;
  } catch {
    state.online = false;
  }
  const pulse = $('#statusPulse');
  const llmOk = state.status && state.status.llm.online;
  pulse.className = 'pulse ' + (state.online ? (llmOk ? 'on' : 'off') : 'off');
  const tip = !state.online ? 'Scorp server unreachable' : llmOk ? `Model online · ${state.status.llm.model}` : 'Model offline: ' + (state.status.llm.error || 'start Ollama');
  $('#sbStatus').title = tip;
  $('#statusText').textContent = !state.online ? 'Offline' : llmOk ? state.status.llm.model : 'Model offline';
  $('#bellDot').classList.toggle('hidden', !(state.status && state.status.counts.unread));
  chat.updateModelChip();
  bus.emit('status', state.status);
}

async function loadBasics() {
  const [settings, skills] = await Promise.all([api.get('/api/settings'), api.get('/api/skills')]);
  state.settings = settings;
  state.skills = skills.skills;
  styleChanged();
  await refreshStatus();
}

// ---------- views ----------

const renderers = {
  code: (el) => renderCode(el),
  library: (el, r) => renderLibrary(el, r.sub),
  agenda: (el) => renderAgenda(el),
  agents: (el) => renderAgents(el),
  routines: (el) => renderRoutines(el),
  memory: (el) => renderMemory(el),
  connections: (el, r) => renderConnections(el, r.hash),
  settings: (el) => renderSettings(el),
};

// ---------- sidebar: full (navigation + conversations) or a slim icon rail ----------

function setSidebar(collapsed, remember = true) {
  // a sidebar that slides in is always the full one
  const beside = !['top', 'bottom'].includes(prefs.sbDock); // the slim rail only exists beside the stage
  app.classList.toggle('sidebar-collapsed', collapsed && beside && prefs.autoHideSidebar === false);
  const btn = $('#sidebarToggle');
  btn.title = collapsed ? 'Expand sidebar (Ctrl+B)' : 'Collapse sidebar (Ctrl+B)';
  btn.setAttribute('aria-expanded', String(!collapsed));
  if (remember) setPref('sidebarCollapsed', collapsed);
}

function toggleSidebar() {
  if (isMobile()) return app.classList.toggle('threads-open');
  if (document.documentElement.classList.contains('auto-sidebar')) return app.classList.toggle('sb-open');
  setSidebar(!app.classList.contains('sidebar-collapsed'));
}

// Clean chat: the sidebar waits off-screen; the menu button (or Ctrl+B) slides it in, and only that button (or the
// close button in the sidebar) puts it away: clicking elsewhere or picking a conversation leaves it open.
function initAutoSidebar() {
  $('#sbPeek').addEventListener('click', () => app.classList.toggle('sb-open'));
}

// visual answers (canvas + spoken caption) or written ones, from the host's settings
function styleChanged() {
  const style = (state.settings && state.settings.response_style) || 'visual';
  applyResponseStyle(style);
  try {
    localStorage.setItem('hearth.style', style);
  } catch {
    /* ignore */
  }
}

// ---------- Chat / Code: the sidebar lists conversations or projects ----------

function setMode(mode) {
  const changed = state.mode !== mode;
  state.mode = mode;
  app.dataset.mode = mode;
  setPref('mode', mode);
  $$('#modeSwitch [data-mode]').forEach((b) => {
    b.classList.toggle('on', b.dataset.mode === mode);
    b.setAttribute('aria-selected', String(b.dataset.mode === mode));
  });
  $('#newLabel').textContent = mode === 'code' ? 'New project' : 'New conversation';
  $('#newChatBtn').title = mode === 'code' ? 'New project (Ctrl+Shift+O)' : 'New conversation (Ctrl+Shift+O)';
  $('#threadsTitle').textContent = mode === 'code' ? 'Projects' : 'Recent';
  $('#threadSearch').placeholder = mode === 'code' ? 'Search projects' : 'Search';
  if (changed) {
    if (mode === 'code') chat.loadProjects();
    chat.renderThreads();
  }
}

function modeFor(route) {
  if (route.view === 'code' || route.project) return 'code';
  if (route.view === 'chat') return 'chat';
  return state.mode; // other pages keep the sidebar as it was
}

function show(route) {
  const view = route.view;
  state.view = view;
  app.dataset.view = view;
  app.classList.remove('threads-open');
  if (view === 'settings') app.classList.remove('sb-open'); // Settings gets the whole window
  setMode(modeFor(route));
  $$('.view').forEach((v) => v.classList.toggle('active', v.dataset.view === view));
  // Connections has two entries: "MCP servers" (Code mode) and the footer icon (everything else)
  const mcpPage = view === 'connections' && route.hash === 'mcp' && state.mode === 'code';
  $$('[data-nav]').forEach((n) => {
    const isMcp = n.getAttribute('href') === '/connections#mcp';
    n.classList.toggle('active', !route.project && n.dataset.nav === view && (view !== 'connections' || isMcp === mcpPage));
  });
  $('#topbarTitle').textContent = view === 'chat' ? 'Scorp' : view[0].toUpperCase() + view.slice(1);
  if (view === 'chat') {
    state.lastChatPath = location.pathname; // where the pages' back buttons return to
    try {
      sessionStorage.setItem('hearth.lastChat', location.pathname);
    } catch {
      /* ignore */
    }
    if (route.project) chat.openProject(route.project);
    else if (route.id) chat.openConversation(route.id);
    else chat.showWelcome();
    // "Hey Hearth, …" heard by Ember's overlay arrives here with what was said after it
    if (route.voice) setTimeout(() => chat.wakeVoice(takeHandOff()), 120);
    return;
  }
  document.title = (view === 'code' ? 'Code' : view[0].toUpperCase() + view.slice(1)) + ' · Scorp';
  renderers[view]($('#view-' + view), route);
}

bus.on('route', show);

// ---------- live events ----------

// a project's agent moved on: refresh the project list (sidebar, Code page) now and then, not on every step
let projectsTimer = null;
function projectsChanged() {
  if (projectsTimer) return;
  projectsTimer = setTimeout(() => {
    projectsTimer = null;
    if (state.mode === 'code') chat.loadProjects();
    if (state.view === 'code') refreshCode();
  }, 2500);
}

let notifyPermission = typeof Notification !== 'undefined' ? Notification.permission : 'denied';

function onServerEvent(ev) {
  const d = ev.data || {};
  switch (ev.type) {
    case 'notification':
      $('#bellDot').classList.remove('hidden');
      toast(d.title, {
        body: d.body,
        type: d.level === 'urgent' ? 'urgent' : d.level === 'warning' ? 'warn' : 'info',
        timeout: d.level === 'urgent' ? 12000 : 6000,
        action: d.link ? { label: 'Open', run: () => navigate(d.link) } : null,
      });
      if (document.hidden && notifyPermission === 'granted') {
        try {
          const n = new Notification(d.title, { body: d.body, icon: '/icons/icon-192.png', tag: 'hearth-' + d.id });
          n.onclick = () => {
            window.focus();
            if (d.link) navigate(d.link);
          };
        } catch {
          /* some platforms need the service worker */
          navigator.serviceWorker && navigator.serviceWorker.ready.then((reg) => reg.showNotification(d.title, { body: d.body, icon: '/icons/icon-192.png', data: { link: d.link } }));
        }
      }
      break;
    case 'approval':
      // the conversation's own stream usually shows the card; only toast when it isn't on screen
      setTimeout(() => {
        if (document.querySelector(`[data-approval="${d.id}"]`)) return;
        toast('Approval needed', {
          body: d.summary ? d.summary.title : d.tool,
          type: 'warn',
          timeout: 15000,
          action: { label: 'Review', run: () => navigate('/c/' + d.conv_id) },
        });
      }, 900);
      break;
    case 'approval_done':
      chat.resolveApprovalCard(d.id, d.decision);
      break;
    case 'conversation':
      chat.onConversationEvent(d);
      break;
    case 'agent_update':
      chat.followAgentUpdate(d);
      break;
    case 'agent_job':
      bus.emit('server:agent_job', d);
      if (d.project) projectsChanged();
      break;
    case 'run_done':
      chat.loadThreads();
      refreshStatus();
      break;
    case 'settings':
      api.get('/api/settings').then((s) => {
        state.settings = s;
        styleChanged();
      });
      break;
    default:
      bus.emit('server:' + ev.type, d);
  }
}

// ---------- notifications panel ----------

async function openBell() {
  let data = { notifications: [] };
  try {
    data = await api.get('/api/notifications');
  } catch {
    /* offline */
  }
  const list = h(
    'div.notif-list',
    data.notifications.length
      ? data.notifications.slice(0, 40).map((n) =>
          h('div.notif' + (n.read ? '' : '.unread'), { onclick: () => n.link && navigate(n.link) }, h('span.n-dot'), h('div', h('div.n-title', n.title), n.body ? h('div.n-body', n.body) : null, h('div.n-time', fmtRelative(n.created))))
        )
      : h('div.muted', { style: { padding: '18px', textAlign: 'center', fontSize: '13px' } }, 'All quiet.'),
  );
  popover($('#bellBtn'), [{ title: 'Notifications' }, { node: list }], { className: 'wide' });
  api.post('/api/notifications/read').then(() => $('#bellDot').classList.add('hidden'));
}

function moreSheet() {
  const go = (path) => () => {
    close();
    navigate(path);
  };
  const item = (ic, label, sub, path) => h('button.pop-item', { onclick: go(path) }, icon(ic), h('span.pi-main', label, h('span.pi-sub', sub)));
  const close = sheet({
    title: 'More',
    body: h(
      'div',
      item('agent', 'Agents', 'Your team and what it is working on', '/agents'),
      item('voice', 'Talk', 'A new conversation with voice on', '/voice'),
      item('routine', 'Routines', 'Automations and background tasks', '/routines'),
      item('memory', 'Memory', 'What Scorp remembers, and records', '/memory'),
      item('plug', 'Connections', 'Model, programs, permissions, MCP', '/connections'),
      item('settings', 'Settings', 'Appearance, voice, devices', '/settings'),
      h('div.pop-sep'),
      h('button.pop-item', { onclick: () => { close(); openBell(); } }, icon('bell'), h('span.pi-main', 'Notifications')),
    ),
  });
}

// ---------- boot ----------

async function boot() {
  setSidebar(!!prefs.sidebarCollapsed, false);
  initSidebarLayout({ sidebar: $('#threads'), head: $('#threads .sb-head'), onDock: () => setSidebar(!!prefs.sidebarCollapsed, false) });
  await ensureAuth();
  chat.initChat();
  try {
    await loadBasics();
  } catch (err) {
    toast('Cannot reach Scorp', { type: 'error', body: err.message });
  }
  loadLooks();
  chat.loadThreads();
  setMode(prefs.mode === 'code' ? 'code' : 'chat');
  show(parse());
  connectEvents(onServerEvent, (on) => {
    if (on) refreshStatus();
  });
  setInterval(refreshStatus, 30000);
  bus.on('settings', () => {
    styleChanged();
    chat.refreshWelcome();
  });
  bus.on('prefs', ({ key }) => {
    if (['autoHideSidebar', 'hideStarters', 'hideEmber', 'minimalComposer', 'refraction', 'accent', ...TUNING_KEYS].includes(key)) applyAppearance();
    if (key === 'autoHideSidebar') {
      app.classList.remove('sb-open');
      setSidebar(!!prefs.sidebarCollapsed, false);
    }
  });
  initAutoSidebar();
  // how many agents are working, on the sidebar item and the phone's More tab
  bus.on('agents:count', (n) => {
    $$('.agents-count').forEach((b) => {
      b.textContent = n || '';
      b.classList.toggle('hidden', !n);
    });
  });
  loadJobCount();
  bus.on('skills', () => chat.refreshWelcome());

  $('#bellBtn').addEventListener('click', openBell);
  $('#bellBtn').dataset.popAnchor = '';
  $('#moreTab').addEventListener('click', moreSheet);
  $('#threadsBtn').addEventListener('click', () => app.classList.add('threads-open'));
  // the phone drawer stays open until its close button (tapping beside it does nothing)
  $('#sidebarToggle').addEventListener('click', toggleSidebar);
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === 'b' || e.key === 'B' || e.key === '\\')) {
      e.preventDefault();
      toggleSidebar();
    }
  });
  // wake words ("Hey Scorp", "Hey Scorpi"): a voice conversation; in the desktop app Scorpi's overlay window listens
  if (!(window.chrome && window.chrome.webview)) initWake((word, rest) => chat.wakeVoice(rest));
  // hide the tab bar while the on-screen keyboard is up (detected from the visual viewport)
  if (window.visualViewport) {
    const vv = window.visualViewport;
    const onVV = () => document.body.classList.toggle('keyboard-open', isMobile() && vv.height < window.innerHeight * 0.78);
    vv.addEventListener('resize', onVV);
  }

  // swipe from the left edge opens conversations on phones
  let sx = null;
  document.addEventListener('touchstart', (e) => {
    const t = e.touches[0];
    sx = t.clientX < 24 && state.view === 'chat' ? t.clientX : null;
  }, { passive: true });
  document.addEventListener('touchmove', (e) => {
    if (sx !== null && e.touches[0].clientX - sx > 60) {
      app.classList.add('threads-open');
      sx = null;
    }
  }, { passive: true });

  if (state.status && !state.status.llm.online) {
    toast('Model offline', {
      type: 'warn',
      body: 'Start Ollama on the host, or add a free cloud model (Groq, Gemini, OpenRouter…) in Connections.',
      timeout: 9000,
      action: { label: 'Set up', run: () => navigate('/connections') },
    });
  }

  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
  if (isMobile()) {
    document.body.classList.add('mobile');
    keepFullscreen();
  }
}

// Hide the Android system bars. An installed PWA made before the manifest asked for fullscreen
// keeps its old display mode, so also enter fullscreen on the first tap (a user gesture is required),
// and again after any swipe/back gesture drops out of it.
function keepFullscreen() {
  const root = document.documentElement;
  if (!root.requestFullscreen || matchMedia('(display-mode: fullscreen)').matches) return;
  const enter = () => {
    if (document.fullscreenElement) return;
    root.requestFullscreen({ navigationUI: 'hide' }).catch(() => {});
  };
  document.addEventListener('pointerup', enter, { passive: true });
}

boot();
