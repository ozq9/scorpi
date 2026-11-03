// Looks: the active style (design tokens + CSS) and the enabled plugins. Styles and plugins are made in the Studio,
// by hand or from a description in chat; every device applies them live.
//
// Plugins are small mods: CSS and/or JavaScript. The JavaScript is the body of a function called with `scorp`
// (the API below); everything a plugin adds through it is removed again when the plugin is turned off.

import { api } from './api.js';
import { h, icon } from './dom.js';
import { bus, state } from '../store.js';
import { applyTuning, loadFont } from '../theme.js';
import { navigate } from '../router.js';
import { toast } from './ui.js';

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

function stopPlugin(id) {
  const r = running.get(id);
  if (!r) return;
  running.delete(id);
  for (const fn of r.cleanups.reverse()) {
    try {
      fn();
    } catch (err) {
      console.warn('plugin cleanup', id, err);
    }
  }
}

function scorpApi(p, r) {
  const own = (fn) => r.cleanups.push(fn);
  const store = 'scorp.plugin.' + p.id;
  return {
    version: 2,
    id: p.id,
    root: document.documentElement,
    /** Server events: 'agent_job', 'conversation', 'notification', 'looks', 'run_done', 'music', … */
    on: (event, fn) => own(bus.on('server:' + event, fn)),
    /** The page changed: fn({view, id, project}) */
    onRoute: (fn) => own(bus.on('route', fn)),
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
    /** A button in the taskbar tray (desktop) — {icon, title, text, onClick} */
    tray({ icon: ic = 'spark', title = p.name, text = '', onClick } = {}) {
      const btn = h('button.tk-ic.tk-plugin', { type: 'button', title, dataset: { plugin: p.id }, onclick: (e) => onClick && onClick(e) }, icon(ic), text ? h('span', text) : null);
      const tray = document.querySelector('.tk-tray');
      if (tray) tray.insertBefore(btn, tray.querySelector('.tk-clock'));
      own(() => btn.remove());
      return btn;
    },
    /** A floating glass panel — {title, icon, width, render(body, panel)}; returns {open, close, toggle, el} */
    panel({ title = p.name, icon: ic = 'puzzle', width = 320, render, tray = true } = {}) {
      const body = h('div.plg-body');
      const el = h('section.plg-panel.hidden', { style: { width: width + 'px' }, dataset: { plugin: p.id } },
        h('header.plg-head', icon(ic), h('b', title), h('button.icon-btn', { type: 'button', title: 'Close', onclick: () => panel.close() }, icon('x'))), body);
      document.body.appendChild(el);
      const panel = {
        el,
        body,
        open() { el.classList.remove('hidden'); },
        close() { el.classList.add('hidden'); },
        toggle() { el.classList.toggle('hidden'); },
      };
      if (render) render(body, panel);
      if (tray) this.tray({ icon: ic, title, onClick: () => panel.toggle() });
      own(() => el.remove());
      return panel;
    },
    /** A slash command in the prompt box — {name, description, icon, run(args)} */
    command({ name, description = '', icon: ic = 'puzzle', run }) {
      const key = String(name || '').toLowerCase().replace(/[^a-z0-9-]/g, '');
      if (!key || typeof run !== 'function') throw new Error('command needs a name and run(args)');
      const call = (args) => {
        runningCommand = { name: key, args: String(args || '') };
        try {
          return run(args);
        } finally {
          runningCommand = null;
        }
      };
      pluginCommands.set(key, { name: key, description: description || p.description || p.name, icon: ic, run: call, plugin: p.name });
      own(() => pluginCommands.delete(key));
    },
    every(ms, fn) {
      const t = setInterval(fn, Math.max(200, ms));
      own(() => clearInterval(t));
      return t;
    },
    /** Send a message to Scorp in the chat */
    ask: (text) => {
      // asked from a slash command: the chat shows "/name args" like a skill, the full prompt goes to the model
      const shown = runningCommand ? ('/' + runningCommand.name + ' ' + runningCommand.args).trim() : '';
      const skill = runningCommand ? runningCommand.name : '';
      navigate(state.lastChatPath || '/');
      setTimeout(() => bus.emit('compose:send', shown ? { text: String(text), display: shown, command: skill } : String(text)), 80);
    },
    /** Scorpi says something in a little bubble */
    say: (text, ms) => bus.emit('scorpi:say', { text: String(text), ms }),
    toast: (text, opts) => toast(text, opts),
    navigate,
    api: { get: (path) => api.get(path), post: (path, body) => api.post(path, body) },
    storage: {
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
          all[key] = value;
          localStorage.setItem(store, JSON.stringify(all));
        } catch {
          /* private mode */
        }
      },
    },
    state: () => ({ view: state.view, conversation: state.activeId, project: state.project && state.project.slug, online: state.online, model: state.status && state.status.llm.model }),
    onCleanup: own,
  };
}

function startPlugin(p) {
  const r = { updated: p.updated, cleanups: [], js: !!p.js };
  running.set(p.id, r);
  if (p.css) {
    const el = document.createElement('style');
    el.dataset.plugin = p.id;
    el.textContent = p.css;
    document.head.appendChild(el);
    r.cleanups.push(() => el.remove());
  }
  if (!p.js) return;
  try {
    const out = new Function('scorp', '"use strict";\n' + p.js)(scorpApi(p, r)); // eslint-disable-line no-new-func
    if (typeof out === 'function') r.cleanups.push(out);
  } catch (err) {
    console.warn('plugin', p.id, err);
    toast('Plugin “' + p.name + '” failed', { type: 'error', body: err.message });
  }
}

function applyPlugins() {
  const want = new Map(current.plugins.filter((p) => p.enabled).map((p) => [p.id, p]));
  for (const [id, r] of [...running]) if (!want.has(id) || want.get(id).updated !== r.updated) stopPlugin(id);
  for (const p of want.values()) if (!running.has(p.id)) startPlugin(p);
}

export function applyLooks(data) {
  if (data) current = data;
  applyStyle();
  applyPlugins();
  bus.emit('looks', current);
}

export async function loadLooks() {
  try {
    applyLooks(await api.get('/api/looks'));
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
