// Looks: the active style (design tokens + CSS) and the enabled plugins. Styles and plugins are made in the Studio,
// by hand or from a description in chat; every device applies them live.
//
// Plugins are mods: CSS, JavaScript, an addition to Scorp's instructions (server side) and settings. The JavaScript is
// the body of an async function called with `scorp` (the API below); everything a plugin adds through it is removed
// again when the plugin is turned off.

import { api } from './api.js';
import { h, icon } from './dom.js';
import { bus, state } from '../store.js';
import { applyTuning, loadFont } from '../theme.js';
import { navigate } from '../router.js';
import { confirmSheet, popover, promptSheet, seg, selectEl, sheet, switchEl, toast } from './ui.js';

let current = { active: '', styles: [], plugins: [] };
let preview = null; // a style being edited in the Studio, shown live without saving
const running = new Map(); // plugin id -> { updated, cleanups[], js }
export const pluginCommands = new Map(); // slash commands added by plugins: name -> { name, description, icon, run, plugin }
let runningCommand = null; // the plugin command whose run() is under way: what scorp.ask shows in the chat

export const looks = () => current;

function tag(id) {
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('style');
    el.id = id;
    document.head.appendChild(el); // after the app's stylesheets, so a style wins over them
  }
  return el;
}

/** CSS text for a style: its tokens on :root, then its own rules. */
export function styleCss(style) {
  const vars = Object.entries(style.vars || {}).map(([k, v]) => `  ${k}: ${v};`).join('\n');
  return `:root:root:root {\n${vars}\n}\n${style.css || ''}`;
}

function applyStyle() {
  const root = document.documentElement;
  const style = preview || current.styles.find((s) => s.id === current.active);
  if (!style) {
    tag('look-style').textContent = '';
    delete root.dataset.look;
  } else {
    (style.fonts || []).forEach(loadFont);
    tag('look-style').textContent = styleCss(style);
    root.dataset.look = style.id || 'preview';
  }
  applyTuning(); // the glass tint is worked out from the style's own colours
  applyScheme();
  if (!preview) {
    try {
      // index.html applies this before first paint next time
      localStorage.setItem('scorp.look', JSON.stringify(style ? { id: style.id, css: styleCss(style), scheme: root.dataset.scheme } : null));
    } catch {
      /* private mode */
    }
  }
}

/** Light or dark native controls (dropdown lists, date pickers, scrollbars) follow how light the style's page is. */
function applyScheme() {
  const root = document.documentElement;
  const probe = document.createElement('i');
  probe.style.cssText = 'position:absolute;width:0;height:0;color:var(--bg)';
  document.body.appendChild(probe);
  const bg = getComputedStyle(probe).color;
  probe.remove();
  // a canvas turns any colour syntax (rgb, oklab, color-mix…) into plain sRGB
  const ctx = (applyScheme.ctx ||= Object.assign(document.createElement('canvas'), { width: 1, height: 1 }).getContext('2d', { willReadFrequently: true }));
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillStyle = '#000';
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  const light = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.55;
  root.dataset.scheme = light ? 'light' : 'dark';
  root.dataset.theme = light ? 'light' : 'dark';
  root.classList.toggle('is-dark', !light);
  document.querySelectorAll('meta[name="theme-color"]').forEach((el) => el.setAttribute('content', getComputedStyle(root).getPropertyValue('--bg').trim() || '#000'));
}

/** Show a style live while it is edited (null: back to the active one). */
export function previewStyle(style) {
  preview = style;
  applyStyle();
}

// ---------- plugins ----------
//
// Like mods: surfaces to draw on (pane beside the chat, band above the prompt box, status in its bar, tray buttons,
// floating panels, actions under answers, /commands, shortcuts), hooks that run as middleware on what the app does
// (send, answer, tool, route), live state (signals, settings the user edits, data synced through the server), and the
// model itself (scorp.llm). Every callback runs guarded: errors go to the plugin's console in the Studio and to the
// server, where Scorp can read them to fix the plugin.

const AsyncFunction = (async () => {}).constructor;
const hooks = { send: [], answer: [], tool: [], route: [] }; // event -> [{ p, fn }]
const msgActions = new Map(); // key -> { p, icon, label, title, run }
const answers = new WeakMap(); // answer element -> { text, done: Set of hook fns already run on it }
const logs = new Map(); // plugin id -> [{ time, level, text }]
let shared = {}; // plugin id -> { key: value }, kept on the server (scorp.shared)
const watchers = new Map(); // plugin id -> Set of { key, fn }
const reported = new Map(); // plugin id -> time an error was last sent to the server

/** A plugin's console: what it logged and what failed (the Studio shows it). */
export const pluginLog = (id) => logs.get(id) || [];

function log(p, level, parts) {
  const text = parts.map((x) => (x instanceof Error ? x.message : typeof x === 'string' ? x : (() => {
    try {
      return JSON.stringify(x);
    } catch {
      return String(x);
    }
  })())).join(' ');
  const list = logs.get(p.id) || [];
  list.push({ time: Date.now(), level, text: text.slice(0, 2000) });
  logs.set(p.id, list.slice(-120));
  bus.emit('plugin:log', p.id);
}

function fail(p, err, where) {
  const msg = (where ? where + ': ' : '') + ((err && err.message) || String(err));
  // a line number in the plugin's own code, when the browser gives one
  const line = err && err.stack && (err.stack.match(/<anonymous>:(\d+):(\d+)/) || [])[1];
  const text = msg + (line ? ` (line ${Math.max(1, line - 3)})` : '');
  console.warn('plugin', p.id, err);
  log(p, 'error', [text]);
  const last = reported.get(p.id) || 0;
  if (Date.now() - last > 4000) {
    reported.set(p.id, Date.now());
    api.post(`/api/plugins/${p.id}/errors`, { text }).catch(() => {});
  }
}

/** fn, but errors (thrown or rejected) land in the plugin's console instead of breaking the app */
function guarded(p, fn, where) {
  if (typeof fn !== 'function') return fn;
  return function (...args) {
    try {
      const out = fn.apply(this, args);
      if (out && typeof out.then === 'function') return out.catch((err) => fail(p, err, where));
      return out;
    } catch (err) {
      fail(p, err, where);
      return undefined;
    }
  };
}

/** Hooks run in order like middleware: each gets (e, next); next(e2) runs the rest. A hook that returns without
 *  calling next answers for itself (null stops it; undefined passes e on unchanged). */
export async function runHooks(event, e) {
  const list = [...(hooks[event] || [])];
  const step = async (i, value) => {
    if (i >= list.length || value === null || value === false) return value;
    const { p, fn } = list[i];
    let downstream = null;
    const next = (v) => (downstream ||= step(i + 1, v === undefined ? value : v));
    try {
      const out = await fn(value, next);
      if (downstream) return out === undefined ? downstream : out;
      return out === undefined ? step(i + 1, value) : out;
    } catch (err) {
      fail(p, err, 'hook ' + event);
      return downstream || step(i + 1, value);
    }
  };
  return step(0, e);
}
export const hasHooks = (event) => !!(hooks[event] && hooks[event].length);

/** Called by the chat when an answer is drawn (and again when it is redrawn): plugin buttons and 'answer' hooks. */
export function decorateAnswer(el, text, { live = false } = {}) {
  const rec = answers.get(el) || { text, done: new Set() };
  rec.text = text;
  answers.set(el, rec);
  const bar = el.querySelector('.msg-actions');
  if (bar && text) {
    for (const [key, a] of msgActions) {
      if (bar.querySelector(`[data-pact="${CSS.escape(key)}"]`)) continue;
      bar.appendChild(h('button.act', { type: 'button', title: a.title || a.label, dataset: { plugin: a.p.id, pact: key }, onclick: () => a.run(rec.text, el) }, icon(a.icon), a.label || null));
    }
  }
  if (!text) return;
  for (const hk of hooks.answer) {
    if (rec.done.has(hk)) continue;
    rec.done.add(hk);
    runOne(hk, answerEvent(hk.p, el, rec, live));
  }
}

function answerEvent(p, el, rec, live) {
  return {
    text: rec.text,
    el,
    conversation: state.activeId,
    live,
    /** add a node to the answer; it goes away with the plugin */
    add(node, where = 'end') {
      node.dataset.plugin = p.id;
      const turn = el.querySelector('.turn') || el;
      if (where === 'start') turn.prepend(node);
      else turn.appendChild(node);
      return node;
    },
  };
}

async function runOne(hk, e) {
  try {
    await hk.fn(e, (v) => v);
  } catch (err) {
    fail(hk.p, err, 'hook answer');
  }
}

const toolNames = new Map(); // tool call id -> { name, args }
/** The chat's stream events: tool calls go to 'tool' hooks. */
export function toolEvent(ev) {
  if (!hooks.tool.length) return;
  if (ev.type === 'tool_start') {
    toolNames.set(ev.id, { name: ev.name, args: ev.args });
    runHooks('tool', { phase: 'start', id: ev.id, name: ev.name, args: ev.args || {} });
  } else if (ev.type === 'tool_end') {
    const t = toolNames.get(ev.id) || {};
    toolNames.delete(ev.id);
    runHooks('tool', { phase: 'end', id: ev.id, name: ev.name || t.name, args: t.args || {}, ok: ev.ok !== false, result: ev.result, display: ev.display });
  }
}

bus.on('route', (r) => hooks.route.length && runHooks('route', { ...r }));

// ---------- sounds (scorp.sound) ----------

let audioCtx = null;
const TONES = {
  ding: [[880, 0, 0.5, 'sine']],
  chime: [[659, 0, 0.35, 'sine'], [988, 0.12, 0.6, 'sine']],
  pop: [[420, 0, 0.09, 'triangle']],
  tick: [[1800, 0, 0.025, 'square']],
  error: [[330, 0, 0.16, 'sawtooth'], [220, 0.13, 0.22, 'sawtooth']],
  success: [[523, 0, 0.18, 'sine'], [659, 0.09, 0.2, 'sine'], [784, 0.18, 0.4, 'sine']],
};
function playSound(what, volume = 0.5) {
  if (!TONES[what]) {
    const a = new Audio(what);
    a.volume = Math.max(0, Math.min(1, volume));
    return a.play().catch(() => {});
  }
  try {
    audioCtx ||= new AudioContext();
    const now = audioCtx.currentTime;
    for (const [freq, at, len, type] of TONES[what]) {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = type;
      osc.frequency.value = freq;
      const peak = (type === 'sine' ? 0.22 : 0.07) * volume;
      gain.gain.setValueAtTime(0, now + at);
      gain.gain.linearRampToValueAtTime(peak, now + at + 0.008);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + at + len);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(now + at);
      osc.stop(now + at + len + 0.05);
    }
  } catch {
    /* no audio */
  }
  return Promise.resolve();
}

// ---------- the panes docked beside the chat ----------

function dockEl() {
  let d = document.getElementById('plgDock');
  const view = document.getElementById('view-chat');
  if (!d && view) {
    d = h('aside.plg-dock#plgDock', { 'aria-label': 'Plugin panes' });
    view.appendChild(d);
  }
  return d;
}

function syncDock() {
  const d = document.getElementById('plgDock');
  if (!d) return;
  const open = [...d.querySelectorAll('.plg-pane:not(.hidden)')];
  d.classList.toggle('open', open.length > 0);
  d.style.width = open.length ? Math.max(...open.map((x) => parseFloat(x.dataset.width) || 340)) + 'px' : '';
  document.getElementById('view-chat')?.classList.toggle('has-plg-pane', open.length > 0);
}

function stopPlugin(id) {
  const r = running.get(id);
  if (!r) return;
  running.delete(id);
  r.stopped = true;
  for (const fn of r.cleanups.reverse()) {
    try {
      fn();
    } catch (err) {
      console.warn('plugin cleanup', id, err);
    }
  }
  // whatever it still left in the page
  document.querySelectorAll(`[data-plugin="${CSS.escape(id)}"]`).forEach((n) => n.remove());
  syncDock();
}

function scorpApi(p, r) {
  const own = (fn) => (r.cleanups.push(fn), fn);
  const g = (fn, where) => guarded(p, fn, where);
  const store = 'scorp.plugin.' + p.id;
  const local = {
    get(key, fallback = null) {
      try {
        const all = JSON.parse(localStorage.getItem(store) || '{}');
        return key in all ? all[key] : fallback;
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try {
        const all = JSON.parse(localStorage.getItem(store) || '{}');
        if (value === undefined || value === null) delete all[key];
        else all[key] = value;
        localStorage.setItem(store, JSON.stringify(all));
      } catch {
        /* private mode */
      }
    },
  };
  const tray = ({ icon: ic = p.icon || 'spark', title = p.name, text = '', onClick } = {}) => {
    const btn = h('button.tk-ic.tk-plugin', { type: 'button', title, dataset: { plugin: p.id }, onclick: g((e) => onClick && onClick(e), 'tray') }, icon(ic), text ? h('span', text) : null);
    const bar = document.querySelector('.tk-tray');
    if (bar) bar.insertBefore(btn, bar.querySelector('.tk-clock'));
    own(() => btn.remove());
    return btn;
  };
  const api3 = {
    version: 3,
    id: p.id,
    name: p.name,
    root: document.documentElement,

    // ----- surfaces -----
    css(text) {
      const el = document.createElement('style');
      el.dataset.plugin = p.id;
      el.textContent = text;
      document.head.appendChild(el);
      own(() => el.remove());
      return el;
    },
    el: h,
    icon,
    tray,
    /** A floating glass panel — {title, icon, width, render(body, panel)}; returns {open, close, toggle, el, body} */
    panel({ title = p.name, icon: ic = p.icon || 'puzzle', width = 320, render, tray: withTray = true } = {}) {
      const body = h('div.plg-body');
      const el = h('section.plg-panel.hidden', { style: { width: width + 'px' }, dataset: { plugin: p.id } },
        h('header.plg-head', icon(ic), h('b', title), h('button.icon-btn', { type: 'button', title: 'Close', onclick: () => panel.close() }, icon('x'))), body);
      document.body.appendChild(el);
      const panel = {
        el,
        body,
        get isOpen() { return !el.classList.contains('hidden'); },
        open() { el.classList.remove('hidden'); },
        close() { el.classList.add('hidden'); },
        toggle() { el.classList.toggle('hidden'); },
      };
      if (render) g(render, 'panel render')(body, panel);
      if (withTray) tray({ icon: ic, title, onClick: () => panel.toggle() });
      own(() => el.remove());
      return panel;
    },
    /** A glass pane docked beside the chat — {title, icon, width, open, render(body, pane), onToggle(open)} */
    pane({ title = p.name, icon: ic = p.icon || 'puzzle', width = 340, render, open: startOpen = false, onToggle, tray: withTray = true } = {}) {
      const dock = dockEl();
      const body = h('div.plg-pane-body');
      const titleEl = h('b', title);
      const memo = 'pane:' + title;
      const el = h('section.plg-pane.hidden', { dataset: { plugin: p.id, width: Math.max(240, Math.min(640, width)) } },
        h('header.plg-head', icon(ic), titleEl, h('button.icon-btn', { type: 'button', title: 'Close', onclick: () => pane.close() }, icon('x'))), body);
      if (dock) dock.appendChild(el);
      const buttons = [];
      const set = (on) => {
        el.classList.toggle('hidden', !on);
        buttons.forEach((b) => b.classList.toggle('on', on));
        local.set(memo, on);
        syncDock();
        if (onToggle) g(onToggle, 'pane onToggle')(on);
      };
      const pane = {
        el,
        body,
        get isOpen() { return !el.classList.contains('hidden'); },
        open() { set(true); },
        close() { set(false); },
        toggle() { set(el.classList.contains('hidden')); },
        setTitle(t) { titleEl.textContent = t; },
      };
      if (withTray) buttons.push(tray({ icon: ic, title, onClick: () => pane.toggle() }));
      // also in the conversation's header, for phones and when the taskbar is hidden
      const head = document.querySelector('#convoHead .convo-actions');
      if (head) {
        const b = h('button.icon-btn', { type: 'button', title, dataset: { plugin: p.id }, onclick: () => pane.toggle() }, icon(ic));
        head.prepend(b);
        buttons.push(b);
        own(() => b.remove());
      }
      if (render) g(render, 'pane render')(body, pane);
      own(() => {
        el.remove();
        syncDock();
      });
      if (local.get(memo, startOpen)) set(true);
      return pane;
    },
    /** A strip just above the prompt box — {render(el, band), hidden} */
    band({ render, hidden = false } = {}) {
      const el = h('div.plg-band' + (hidden ? '.hidden' : ''), { dataset: { plugin: p.id } });
      const form = document.getElementById('composer');
      if (form) form.parentNode.insertBefore(el, form);
      const band = {
        el,
        show() { el.classList.remove('hidden'); },
        hide() { el.classList.add('hidden'); },
        toggle(on) { el.classList.toggle('hidden', on === undefined ? undefined : !on); },
      };
      if (render) g(render, 'band render')(el, band);
      own(() => el.remove());
      return band;
    },
    /** A small live item in the prompt box's bar — {icon, text, title, onClick}; returns {set(text), setIcon, el} */
    status({ icon: ic = '', text = '', title = '', onClick } = {}) {
      const label = h('span', text);
      const glyph = ic ? icon(ic) : null;
      const el = h((onClick ? 'button' : 'span') + '.c-chip.ghost.c-status', { type: onClick ? 'button' : undefined, title: title || p.name, dataset: { plugin: p.id }, onclick: onClick ? g(onClick, 'status') : undefined }, glyph, label);
      const bar = document.querySelector('#composer .composer-bar');
      if (bar) bar.insertBefore(el, bar.querySelector('.grow'));
      el.classList.toggle('hidden', !text && !ic);
      own(() => el.remove());
      return {
        el,
        set(t) {
          label.textContent = t == null ? '' : String(t);
          el.classList.toggle('hidden', !label.textContent && !ic);
        },
        setTitle(t) { el.title = t; },
        setIcon(name) { if (glyph) glyph.firstChild.setAttribute('href', '#i-' + name); },
      };
    },
    /** A button under every answer — {icon, label, title, run(text, msgEl)} */
    messageAction({ icon: ic = p.icon || 'puzzle', label = '', title = '', run } = {}) {
      if (typeof run !== 'function') throw new Error('messageAction needs run(text, el)');
      const key = p.id + ':' + (label || title || ic);
      msgActions.set(key, { p, icon: ic, label, title: title || label || p.name, run: g(run, 'messageAction') });
      own(() => {
        msgActions.delete(key);
        document.querySelectorAll(`[data-pact="${CSS.escape(key)}"]`).forEach((n) => n.remove());
      });
      document.querySelectorAll('.msg.assistant').forEach((el) => answers.has(el) && decorateAnswer(el, answers.get(el).text));
    },
    /** A slash command in the prompt box — {name, description, icon, run(args)} */
    command({ name, description = '', icon: ic = p.icon || 'puzzle', run }) {
      const key = String(name || '').toLowerCase().replace(/[^a-z0-9-]/g, '');
      if (!key || typeof run !== 'function') throw new Error('command needs a name and run(args)');
      const safe = g(run, '/' + key);
      const call = (args) => {
        runningCommand = { name: key, args: String(args || '') };
        try {
          return safe(args);
        } finally {
          runningCommand = null;
        }
      };
      pluginCommands.set(key, { name: key, description: description || p.description || p.name, icon: ic, run: call, plugin: p.name });
      own(() => pluginCommands.delete(key));
    },
    /** A keyboard shortcut — scorp.key('ctrl+shift+k', fn) */
    key(combo, fn) {
      const parts = String(combo).toLowerCase().split('+').map((s) => s.trim());
      const k = parts.pop();
      const want = { ctrl: parts.includes('ctrl') || parts.includes('mod'), shift: parts.includes('shift'), alt: parts.includes('alt'), meta: parts.includes('meta') || parts.includes('cmd') };
      const safe = g(fn, 'key ' + combo);
      const onKey = (e) => {
        if ((e.key || '').toLowerCase() !== k || e.ctrlKey !== want.ctrl || e.shiftKey !== want.shift || e.altKey !== want.alt || e.metaKey !== want.meta) return;
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
        if (typing && !want.ctrl && !want.alt && !want.meta) return;
        e.preventDefault();
        safe(e);
      };
      document.addEventListener('keydown', onKey);
      return own(() => document.removeEventListener('keydown', onKey));
    },
    toast: (text, opts) => toast(text, opts),
    /** Scorpi says something in a little bubble */
    say: (text, ms) => bus.emit('scorpi:say', { text: String(text), ms }),
    sound: (what = 'ding', volume) => playSound(what, volume),

    // ----- hooks -----
    /** Middleware on what the app does: 'send' {text}, 'answer' {text, el, add(node), live}, 'tool' {phase, name, args, ok, result}, 'route' {view, id} */
    hook(event, fn) {
      if (!hooks[event]) throw new Error(`no hook "${event}" (there are: ${Object.keys(hooks).join(', ')})`);
      const hk = { p, fn };
      hooks[event].push(hk);
      own(() => {
        const i = hooks[event].indexOf(hk);
        if (i >= 0) hooks[event].splice(i, 1);
      });
      // answers already on screen get it too
      if (event === 'answer') setTimeout(() => document.querySelectorAll('.msg.assistant').forEach((el) => answers.has(el) && decorateAnswer(el, answers.get(el).text)), 0);
      return hk;
    },
    /** Server events: 'agent_job', 'conversation', 'notification', 'looks', 'run_done', 'music', … */
    on: (event, fn) => own(bus.on('server:' + event, g(fn, 'on ' + event))),
    /** The page changed: fn({view, id, project}) */
    onRoute: (fn) => own(bus.on('route', g(fn, 'onRoute'))),

    // ----- state -----
    /** A value that redraws what reads it: {get, set, update(fn), subscribe(fn)} */
    signal(value) {
      const subs = new Set();
      const s = {
        get: () => value,
        set(v) {
          if (Object.is(v, value) && (v === null || typeof v !== 'object')) return;
          value = v;
          subs.forEach((fn) => fn(value));
        },
        update(fn) { s.set(fn(value)); },
        subscribe(fn) {
          const safe = g(fn, 'signal');
          subs.add(safe);
          return own(() => subs.delete(safe));
        },
        get value() { return value; },
        set value(v) { s.set(v); },
      };
      return s;
    },
    /** Draw el from render() now and again whenever one of the signals changes */
    mount(el, render, ...signals) {
      let queued = false;
      const draw = () => {
        queued = false;
        if (r.stopped) return;
        try {
          const out = [render()].flat(Infinity).filter((x) => x !== null && x !== undefined && x !== false);
          el.replaceChildren(...out.map((x) => (x instanceof Node ? x : document.createTextNode(String(x)))));
        } catch (err) {
          fail(p, err, 'mount');
        }
      };
      draw();
      signals.forEach((s) => s.subscribe(() => {
        if (!queued) {
          queued = true;
          queueMicrotask(draw);
        }
      }));
      return { redraw: draw };
    },
    /** The plugin's settings (Studio → the plugin → Settings) */
    get options() { return { ...r.settings }; },
    onOptions: (fn) => {
      const safe = g(fn, 'onOptions');
      r.optionFns.add(safe);
      return own(() => r.optionFns.delete(safe));
    },
    /** Data kept on the server, the same on every device, live */
    shared: {
      get(key, fallback = null) {
        const mine = shared[p.id] || {};
        return key in mine ? mine[key] : fallback;
      },
      all: () => ({ ...(shared[p.id] || {}) }),
      async set(key, value) {
        const mine = (shared[p.id] ||= {});
        if (value === undefined || value === null) delete mine[key];
        else mine[key] = value;
        notifyShared(p.id, key, value);
        await api.post(`/api/plugins/${p.id}/data`, { key, value: value === undefined ? null : value });
      },
      update(key, fn, fallback = null) { return api3.shared.set(key, fn(api3.shared.get(key, fallback))); },
      /** fn(value, key) when key changes here or on another device ('*' for every key) */
      watch(key, fn) {
        const w = { key, fn: g(fn, 'shared.watch') };
        if (!watchers.has(p.id)) watchers.set(p.id, new Set());
        watchers.get(p.id).add(w);
        return own(() => watchers.get(p.id).delete(w));
      },
    },
    /** Data kept on this device only */
    storage: local,
    state: () => ({ view: state.view, conversation: state.activeId, project: state.project && state.project.slug, online: state.online, model: state.status && state.status.llm.model }),

    // ----- AI -----
    /** Ask the model directly (no chat message). {system, json: true for parsed JSON, model} */
    async llm(prompt, { system = '', json = false, model = '' } = {}) {
      const res = await api.post(`/api/plugins/${p.id}/llm`, { prompt: String(prompt), system, json, model: model || state.chatModel || '' });
      return json ? res.data : res.text;
    },
    /** Send a message to Scorp in the chat */
    ask: (text) => {
      // asked from a slash command: the chat shows "/name args" like a skill, the full prompt goes to the model
      const shown = runningCommand ? ('/' + runningCommand.name + ' ' + runningCommand.args).trim() : '';
      const skill = runningCommand ? runningCommand.name : '';
      navigate(state.lastChatPath || '/');
      setTimeout(() => bus.emit('compose:send', shown ? { text: String(text), display: shown, command: skill } : String(text)), 80);
    },

    // ----- helpers -----
    ui: { confirm: confirmSheet, prompt: promptSheet, sheet, popover, switch: switchEl, seg, select: selectEl },
    every(ms, fn) {
      const t = setInterval(g(fn, 'every'), Math.max(100, ms));
      own(() => clearInterval(t));
      return t;
    },
    after(ms, fn) {
      const t = setTimeout(g(fn, 'after'), ms);
      own(() => clearTimeout(t));
      return t;
    },
    navigate,
    api: { get: (path) => api.get(path), post: (path, body) => api.post(path, body), del: (path) => api.del(path) },
    log: (...parts) => log(p, 'log', parts),
    onCleanup: own,
  };
  return api3;
}

function notifyShared(id, key, value) {
  for (const w of watchers.get(id) || []) if (w.key === key || w.key === '*') w.fn(value, key);
}

bus.on('server:plugin_data', ({ id, key, value }) => {
  const mine = (shared[id] ||= {});
  if (JSON.stringify(mine[key] ?? null) === JSON.stringify(value ?? null)) return; // this device set it
  if (value === null || value === undefined) delete mine[key];
  else mine[key] = value;
  notifyShared(id, key, value);
});

/** console.* inside a plugin writes to its own console too */
function pluginConsole(p) {
  const out = {};
  for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
    out[level] = (...parts) => {
      console[level]('[' + p.id + ']', ...parts);
      log(p, level === 'error' || level === 'warn' ? level : 'log', parts);
    };
  }
  return out;
}

function startPlugin(p) {
  const r = { updated: p.updated, cleanups: [], js: !!p.js, settings: { ...(p.settings || {}) }, optionFns: new Set(), stopped: false };
  running.set(p.id, r);
  if (p.css) {
    const el = document.createElement('style');
    el.dataset.plugin = p.id;
    el.textContent = p.css;
    document.head.appendChild(el);
    r.cleanups.push(() => el.remove());
  }
  if (!p.js) return;
  log(p, 'info', ['started']);
  let out;
  try {
    out = new AsyncFunction('scorp', 'console', '"use strict";\n' + p.js)(scorpApi(p, r), pluginConsole(p));
  } catch (err) {
    // a syntax error: the code never ran
    fail(p, err, 'could not start');
    toast('Plugin “' + p.name + '” failed', { type: 'error', body: err.message, action: { label: 'Open', run: () => navigate('/studio?plugin=' + p.id) } });
    return;
  }
  out.then((cleanup) => {
    if (typeof cleanup !== 'function') return;
    if (r.stopped) cleanup();
    else r.cleanups.push(cleanup);
  }, (err) => {
    fail(p, err, 'start');
    toast('Plugin “' + p.name + '” failed', { type: 'error', body: err.message, action: { label: 'Open', run: () => navigate('/studio?plugin=' + p.id) } });
  });
}

function applyPlugins() {
  const want = new Map(current.plugins.filter((p) => p.enabled).map((p) => [p.id, p]));
  for (const [id, r] of [...running]) if (!want.has(id) || want.get(id).updated !== r.updated) stopPlugin(id);
  for (const p of want.values()) {
    const r = running.get(p.id);
    if (!r) startPlugin(p);
    else if (JSON.stringify(r.settings) !== JSON.stringify(p.settings || {})) {
      // new settings: the plugin keeps running and hears about them
      r.settings = { ...(p.settings || {}) };
      r.optionFns.forEach((fn) => fn({ ...r.settings }));
    }
  }
}

export function applyLooks(data) {
  if (data) current = data;
  applyStyle();
  applyPlugins();
  bus.emit('looks', current);
}

export async function loadLooks() {
  try {
    const [data, stored] = await Promise.all([api.get('/api/looks'), api.get('/api/plugins/data').catch(() => ({}))]);
    shared = stored || {};
    applyLooks(data);
  } catch {
    /* offline: keep the default look */
  }
}

bus.on('server:looks', (data) => {
  const before = current.active;
  applyLooks(data);
  if (data.active !== before && !preview) {
    const s = data.styles.find((x) => x.id === data.active);
    toast(s ? 'Style: ' + s.name : 'Default look', { timeout: 1800 });
  }
});
