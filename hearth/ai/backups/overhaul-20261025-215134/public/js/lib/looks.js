// Looks: the active style (design tokens + CSS) and the enabled plugins. Scorp makes them from a vibe in chat
// ("a clean black and white style with small font"); every device applies them live.

import { api } from './api.js';
import { bus } from '../store.js';
import { loadFont } from '../theme.js';
import { navigate } from '../router.js';
import { toast } from './ui.js';

let current = { active: '', styles: [], plugins: [] };
const running = new Map(); // plugin id -> { updated, style, cleanup, offs }

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

function applyStyle() {
  const root = document.documentElement;
  const style = current.styles.find((s) => s.id === current.active);
  if (!style) {
    tag('look-style').textContent = '';
    delete root.dataset.look;
    return;
  }
  (style.fonts || []).forEach(loadFont);
  const vars = Object.entries(style.vars || {}).map(([k, v]) => `  ${k}: ${v};`).join('\n');
  tag('look-style').textContent = `:root:root:root {\n${vars}\n}\n${style.css || ''}`;
  root.dataset.look = style.id;
}

function stopPlugin(id) {
  const r = running.get(id);
  if (!r) return;
  running.delete(id);
  if (r.style) r.style.remove();
  r.offs.forEach((off) => off());
  if (r.cleanup) {
    try {
      r.cleanup();
    } catch (err) {
      console.warn('plugin cleanup', id, err);
    }
  } else if (r.js) toast('Plugin turned off', { body: 'Reload Scorp to remove everything it added.', timeout: 3500 });
}

function startPlugin(p) {
  const r = { updated: p.updated, style: null, cleanup: null, offs: [], js: !!p.js };
  running.set(p.id, r);
  if (p.css) {
    r.style = document.createElement('style');
    r.style.dataset.plugin = p.id;
    r.style.textContent = p.css;
    document.head.appendChild(r.style);
  }
  if (!p.js) return;
  const scorp = {
    root: document.documentElement,
    on: (event, fn) => r.offs.push(bus.on('server:' + event, fn)),
    toast: (text, opts) => toast(text, opts),
    navigate,
    api: { get: (path) => api.get(path), post: (path, body) => api.post(path, body) },
  };
  try {
    const out = new Function('scorp', '"use strict";\n' + p.js)(scorp); // eslint-disable-line no-new-func
    if (typeof out === 'function') r.cleanup = out;
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
  if (data.active !== before) {
    const s = data.styles.find((x) => x.id === data.active);
    toast(s ? 'Style: ' + s.name : 'Default look', { timeout: 1800 });
  }
});
