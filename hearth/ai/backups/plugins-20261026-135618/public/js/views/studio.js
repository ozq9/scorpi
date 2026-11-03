// Studio: make Scorp look and work the way you want. Styles (design tokens + CSS) and plugins (small mods: CSS and
// JavaScript with the scorp API) — made by hand in the editors here, or described in words and built by Scorp.
// Everything applies live on every device; styles and plugins can be exported as files and imported again.

import { api, streamJSON } from '../lib/api.js';
import { clear, h, icon } from '../lib/dom.js';
import { confirmSheet, popover, promptSheet, seg, switchEl, toast } from '../lib/ui.js';
import { looks, previewStyle, styleCss } from '../lib/looks.js';
import { createMascot } from '../lib/mascot.js';
import { loadFont } from '../theme.js';
import { bus, state } from '../store.js';
import { navigate } from '../router.js';

let root;
let tab = 'styles';
let editing = null; // { kind: 'style' | 'plugin', draft }
let fresh = ''; // id of the item Scorp just made (glows once)
const STUDIO_CONV = 'scorp.studio.conv';

// ---------- the design tokens the editor offers ----------

const COLOR_TOKENS = [
  ['--bg', 'Background'], ['--ink', 'Text'], ['--ink-2', 'Secondary text'], ['--muted', 'Quiet text'], ['--faint', 'Faint text'],
  ['--accent', 'Accent'], ['--accent-ink', 'Text on accent'], ['--line', 'Borders'], ['--line-2', 'Dividers'],
  ['--ok', 'Success'], ['--warn', 'Warning'], ['--danger', 'Error'],
];
const GLASS_TOKENS = [
  ['--glass', 'Plates (cards, panels)'], ['--glass-2', 'Plates on hover'], ['--glass-edge', 'Glass edge'], ['--composer-bg', 'Prompt box'],
  ['--menu-bg', 'Menus & taskbar'], ['--sheet-bg', 'Dialogs'], ['--slip-bg', 'Your messages'],
];
const SCORPI_TOKENS = [['--sc-mid', 'Body'], ['--sc-hi', 'Highlight'], ['--sc-lo', 'Shade'], ['--sc-sting', 'Stinger'], ['--sc-cheek', 'Blush'], ['--sc-cloud-hi', 'Cloud'], ['--sc-cloud-lo', 'Cloud shade']];
const FONTS = ['Inter', 'Inter Tight', 'Geist', 'Manrope', 'DM Sans', 'Outfit', 'Space Grotesk', 'IBM Plex Sans', 'Nunito', 'Sora', 'Newsreader', 'Fraunces', 'Instrument Serif', 'Lora', 'JetBrains Mono', 'IBM Plex Mono', 'Fira Code', 'Space Mono'];
const DEFAULTS = {
  '--bg': '#000000', '--ink': '#F4F4F6', '--ink-2': 'rgba(244, 244, 246, .76)', '--muted': 'rgba(244, 244, 246, .54)', '--faint': 'rgba(244, 244, 246, .34)',
  '--accent': '#FFFFFF', '--accent-ink': '#000000', '--line': 'rgba(255, 255, 255, .12)', '--line-2': 'rgba(255, 255, 255, .07)',
  '--ok': '#7CEFB8', '--warn': '#FFC56E', '--danger': '#FF7B7B',
  '--glass': 'rgba(255, 255, 255, .045)', '--glass-2': 'rgba(255, 255, 255, .075)', '--glass-edge': 'rgba(255, 255, 255, .1)', '--composer-bg': 'rgba(20, 20, 24, .58)',
  '--menu-bg': 'rgba(22, 22, 26, .86)', '--sheet-bg': 'rgba(16, 16, 20, .9)', '--slip-bg': 'rgba(255, 255, 255, .085)',
  '--sc-mid': '#EEF0F5', '--sc-hi': '#FFFFFF', '--sc-lo': '#BFC4D0', '--sc-sting': '#FF8FAE', '--sc-cheek': '#FF9DB8', '--sc-cloud-hi': '#FFFFFF', '--sc-cloud-lo': '#DDE2EE',
};

// starting points for a new style
const PRESETS = [
  { name: 'Obsidian', description: 'Black liquid glass (the default)', vars: {} },
  {
    name: 'Porcelain', description: 'Light, airy and calm', fonts: ['Inter', 'Inter Tight'],
    vars: { '--bg': '#ECEDF1', '--ink': '#15161A', '--ink-2': 'rgba(21, 22, 26, .74)', '--muted': 'rgba(21, 22, 26, .54)', '--faint': 'rgba(21, 22, 26, .36)', '--accent': '#15161A', '--accent-ink': '#FFFFFF', '--line': 'rgba(10, 12, 20, .12)', '--line-2': 'rgba(10, 12, 20, .07)', '--glass': 'rgba(255, 255, 255, .6)', '--glass-2': 'rgba(255, 255, 255, .8)', '--glass-edge': 'rgba(255, 255, 255, .9)', '--composer-bg': 'rgba(255, 255, 255, .72)', '--menu-bg': 'rgba(250, 250, 252, .9)', '--sheet-bg': 'rgba(248, 248, 250, .94)', '--slip-bg': 'rgba(21, 22, 26, .07)', '--sc-mid': '#FFFFFF', '--sc-lo': '#C8CCD8' },
  },
  {
    name: 'Paper', description: 'Warm paper with a serif voice', fonts: ['Newsreader', 'Inter'],
    vars: { '--bg': '#EFE9DD', '--ink': '#23201A', '--ink-2': 'rgba(35, 32, 26, .76)', '--muted': 'rgba(35, 32, 26, .55)', '--faint': 'rgba(35, 32, 26, .36)', '--accent': '#9C4A2F', '--accent-ink': '#FFF8F0', '--line': 'rgba(60, 45, 20, .14)', '--line-2': 'rgba(60, 45, 20, .08)', '--glass': 'rgba(255, 251, 242, .7)', '--glass-2': 'rgba(255, 251, 242, .9)', '--glass-edge': 'rgba(255, 255, 255, .7)', '--composer-bg': 'rgba(255, 251, 242, .85)', '--menu-bg': 'rgba(250, 245, 235, .95)', '--sheet-bg': 'rgba(250, 245, 235, .97)', '--slip-bg': 'rgba(156, 74, 47, .1)', '--font-display': '"Newsreader", Georgia, serif', '--round': '.7' },
  },
  {
    name: 'Neon', description: 'Dark with cyan and magenta light', fonts: ['Space Grotesk'],
    vars: { '--bg': '#05030C', '--ink': '#F2F0FF', '--accent': '#5CF6FF', '--accent-ink': '#05030C', '--line': 'rgba(140, 120, 255, .22)', '--line-2': 'rgba(140, 120, 255, .12)', '--glass': 'rgba(120, 90, 255, .07)', '--glass-edge': 'rgba(255, 90, 220, .28)', '--slip-bg': 'rgba(255, 90, 220, .16)', '--font-ui': '"Space Grotesk", system-ui, sans-serif', '--font-display': '"Space Grotesk", system-ui, sans-serif', '--sc-sting': '#5CF6FF', '--sc-cheek': '#FF5ADC' },
  },
  {
    name: 'Mono', description: 'Square, small and code-like', fonts: ['JetBrains Mono'],
    vars: { '--font-ui': '"JetBrains Mono", ui-monospace, monospace', '--font-display': '"JetBrains Mono", ui-monospace, monospace', '--fs': '13.5px', '--round': '.25', '--glass-blur': 'none', '--glass': 'rgba(255, 255, 255, .03)' },
  },
  {
    name: 'Midnight', description: 'Deep blue glass, soft lilac accent', fonts: ['Manrope'],
    vars: { '--bg': '#060A18', '--ink': '#EEF1FF', '--accent': '#C9B8FF', '--accent-ink': '#0B0820', '--glass': 'rgba(140, 160, 255, .06)', '--glass-edge': 'rgba(170, 180, 255, .16)', '--composer-bg': 'rgba(14, 20, 44, .62)', '--menu-bg': 'rgba(16, 22, 46, .9)', '--sheet-bg': 'rgba(12, 18, 40, .94)', '--slip-bg': 'rgba(150, 160, 255, .14)', '--font-ui': '"Manrope", system-ui, sans-serif', '--round': '1.25' },
  },
];

const IDEAS = [
  ['palette', 'Warm paper look with serif headings'],
  ['palette', 'Pure black and white, square corners, small text'],
  ['palette', 'Soft pastel lilac glass with rounder corners'],
  ['puzzle', 'A clock with seconds in the taskbar'],
  ['puzzle', 'A pomodoro timer panel with start and pause'],
  ['puzzle', 'A /dice command that rolls dice in a toast'],
];

const TEMPLATES = [
  {
    name: 'Clock with seconds', description: 'A clock with seconds in the taskbar tray',
    js: `const btn = scorp.tray({ icon: 'clock', title: 'Time' });
const label = scorp.el('span.plg-clock');
btn.append(label);
scorp.css('.tk-plugin .plg-clock { font: 600 12px var(--font-mono); margin-left: 6px; }');
const tick = () => (label.textContent = new Date().toLocaleTimeString());
tick();
scorp.every(1000, tick);`,
  },
  {
    name: 'Pomodoro', description: 'A focus timer panel',
    js: `let left = 25 * 60, timer = null;
const time = scorp.el('div.plg-big', '25:00');
const fmt = (s) => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
const start = scorp.el('button.btn.primary.small', { onclick: () => toggle() }, 'Start');
function toggle() {
  if (timer) { clearInterval(timer); timer = null; start.textContent = 'Start'; return; }
  start.textContent = 'Pause';
  timer = setInterval(() => {
    left = Math.max(0, left - 1);
    time.textContent = fmt(left);
    if (!left) { clearInterval(timer); timer = null; scorp.say('Time for a break!'); scorp.toast('Pomodoro done'); }
  }, 1000);
}
scorp.panel({ title: 'Focus', icon: 'clock', width: 260, render: (body) => body.append(time, scorp.el('div.row', start,
  scorp.el('button.btn.quiet.small', { onclick: () => { left = 25 * 60; time.textContent = fmt(left); } }, 'Reset'))) });
scorp.css('.plg-big { font: 300 54px/1 var(--font-display); letter-spacing: -.04em; text-align: center; margin: 8px 0 14px; font-variant-numeric: tabular-nums; }');
return () => clearInterval(timer);`,
  },
  {
    name: 'Dice', description: 'A /dice command in the prompt box',
    js: `scorp.command({
  name: 'dice',
  description: 'Roll dice, e.g. /dice 2d6',
  icon: 'spark',
  run(args) {
    const [n, sides] = (args.match(/(\\d*)d(\\d+)/) || [0, '1', '6']).slice(1).map((x) => +x || 1);
    const rolls = Array.from({ length: Math.min(n, 20) }, () => 1 + Math.floor(Math.random() * sides));
    scorp.say('🎲 ' + rolls.join(' + ') + ' = ' + rolls.reduce((a, b) => a + b, 0));
  },
});`,
  },
  {
    name: 'Focus mode', description: 'Hide the taskbar until the mouse reaches the bottom',
    css: `@media (min-width: 901px) {
  .taskbar { transform: translateY(calc(100% + 12px)); transition: transform .35s var(--ease-out); }
  .taskbar:hover, .taskbar:focus-within { transform: none; }
  .taskbar::before { content: ""; position: absolute; left: 0; right: 0; bottom: 100%; height: 20px; }
  :root { --tkh: 8px; }
}`,
  },
];

// ---------- helpers ----------

function parseColor(v) {
  const s = String(v || '').trim();
  let m = s.match(/^#([0-9a-f]{3,8})$/i);
  if (m) {
    let x = m[1];
    if (x.length <= 4) x = x.split('').map((c) => c + c).join('');
    return { hex: '#' + x.slice(0, 6), a: x.length === 8 ? parseInt(x.slice(6), 16) / 255 : 1 };
  }
  m = s.match(/^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)(?:[\s,/]+([\d.]+%?))?\s*\)$/i);
  if (m) {
    const hx = (n) => Math.max(0, Math.min(255, +n)).toString(16).padStart(2, '0');
    const a = m[4] === undefined ? 1 : m[4].endsWith('%') ? parseFloat(m[4]) / 100 : +m[4];
    return { hex: '#' + hx(m[1]) + hx(m[2]) + hx(m[3]), a };
  }
  return null;
}

function toCss(hex, a) {
  if (a >= 0.995) return hex.toUpperCase();
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${+a.toFixed(3)})`;
}

function download(name, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const a = h('a', { href: URL.createObjectURL(blob), download: name });
  document.body.appendChild(a);
  a.click();
  setTimeout(() => (URL.revokeObjectURL(a.href), a.remove()), 1000);
}

const slugName = (s) => String(s || 'look').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'look';

async function exportItem(kind, id, name) {
  try {
    download(slugName(name) + '.scorp-' + kind + '.json', await api.get(`/api/looks/export/${kind}/${id}`));
  } catch (err) {
    toast(err.message, 'error');
  }
}

function importFile() {
  const input = h('input', { type: 'file', accept: '.json,application/json', hidden: true });
  input.addEventListener('change', async () => {
    const f = input.files[0];
    if (!f) return;
    try {
      const r = await api.post('/api/looks/import', JSON.parse(await f.text()));
      fresh = r.item.id;
      tab = r.kind === 'style' ? 'styles' : 'plugins';
      toast(`Imported “${r.item.name}”`, { body: r.kind === 'plugin' && r.item.js ? 'Plugins with code start turned off — check it, then switch it on.' : '', timeout: 5000 });
      paint();
    } catch (err) {
      toast('Could not import', { type: 'error', body: err.message });
    }
    input.remove();
  });
  document.body.appendChild(input);
  input.click();
}

// ---------- a style's preview card ----------

function miniPreview(vars) {
  vars = vars || {};
  const v = { ...DEFAULTS, ...vars };
  const font = vars['--font-ui'] || '';
  const disp = vars['--font-display'] || font;
  const r = parseFloat(vars['--round'] ?? 1);
  return h('div.st-mini', { style: { background: v['--bg'], color: v['--ink'], fontFamily: font, '--r': 10 * r + 'px' } },
    h('div.st-mini-title', { style: { fontFamily: disp } }, 'Aa'),
    h('div.st-mini-msg', { style: { background: v['--slip-bg'], borderRadius: `${10 * r}px ${10 * r}px ${3 * r}px ${10 * r}px` } }),
    h('div.st-mini-line', { style: { background: v['--ink'], opacity: 0.8 } }),
    h('div.st-mini-line.short', { style: { background: v['--ink'], opacity: 0.45 } }),
    h('div.st-mini-box', { style: { background: v['--composer-bg'], borderColor: v['--glass-edge'], borderRadius: 12 * r + 'px' } }, h('i', { style: { background: v['--accent'] } })),
  );
}

// ---------- library ----------

function styleCard(st) {
  const data = looks();
  const id = st ? st.id : '';
  const on = data.active === id;
  const more = (e) => popover(e.currentTarget, [
    { label: 'Edit', icon: 'edit', onClick: () => openStyle(st) },
    { label: 'Duplicate', icon: 'copy', onClick: () => openStyle(st ? { ...st, id: '', name: st.name + ' copy' } : { name: 'Obsidian copy', vars: {} }) },
    st ? { label: 'Export', sub: 'Save as a file to share', icon: 'download', onClick: () => exportItem('style', st.id, st.name) } : null,
    st ? 'sep' : null,
    st ? { label: 'Delete', icon: 'trash', danger: true, onClick: async () => {
      if (await confirmSheet('Delete “' + st.name + '”?', 'This style is removed on every device.', { confirm: 'Delete', danger: true })) api.del('/api/looks/styles/' + st.id).catch((err) => toast(err.message, 'error'));
    } } : null,
  ], { align: 'end' });
  return h('article.st-card' + (on ? '.on' : '') + (fresh && fresh === id ? '.fresh' : ''),
    h('button.st-card-preview', { type: 'button', title: on ? 'Active' : 'Use this style', onclick: () => api.post('/api/looks/active', { id }).catch((err) => toast(err.message, 'error')) }, miniPreview(st && st.vars)),
    h('div.st-card-foot',
      h('div.st-card-text', h('div.st-card-name', st ? st.name : 'Obsidian', on ? h('span.badge.accent', 'Active') : null), h('div.st-card-desc', st ? st.description || 'Your style' : 'Black liquid glass — the default')),
      h('button.icon-btn', { type: 'button', title: 'Edit', onclick: () => openStyle(st) }, icon('edit')),
      h('button.icon-btn', { type: 'button', title: 'More', 'data-pop-anchor': '', onclick: more }, icon('more'))),
  );
}

function pluginRow(p) {
  const more = (e) => popover(e.currentTarget, [
    { label: 'Edit', icon: 'edit', onClick: () => openPlugin(p) },
    { label: 'Export', sub: 'Save as a file to share', icon: 'download', onClick: () => exportItem('plugin', p.id, p.name) },
    'sep',
    { label: 'Delete', icon: 'trash', danger: true, onClick: async () => {
      if (await confirmSheet('Delete “' + p.name + '”?', 'The plugin is removed on every device.', { confirm: 'Delete', danger: true })) api.del('/api/looks/plugins/' + p.id).catch((err) => toast(err.message, 'error'));
    } },
  ], { align: 'end' });
  return h('div.st-plugin' + (fresh === p.id ? '.fresh' : ''),
    h('span.st-plugin-ic', icon('puzzle')),
    h('div.st-plugin-text', h('div.st-plugin-name', p.name, p.css ? h('span.badge', 'CSS') : null, p.js ? h('span.badge', 'JS') : null), h('div.st-plugin-desc', p.description || '')),
    switchEl(p.enabled, (on) => api.post('/api/looks/plugins/' + p.id, { enabled: on }).catch((err) => toast(err.message, 'error'))),
    h('button.icon-btn', { type: 'button', title: 'Edit', onclick: () => openPlugin(p) }, icon('edit')),
    h('button.icon-btn', { type: 'button', title: 'More', 'data-pop-anchor': '', onclick: more }, icon('more')),
  );
}

function library() {
  const data = looks();
  const tabs = h('div.st-bar',
    seg([['styles', `Styles · ${data.styles.length + 1}`], ['plugins', `Plugins · ${data.plugins.length}`]], tab, (v) => { tab = v; paint(); }),
    h('span.grow'),
    h('button.btn.quiet.small', { onclick: importFile }, icon('upload'), 'Import'),
    tab === 'styles'
      ? h('button.btn.small', { onclick: (e) => popover(e.currentTarget, [{ title: 'Start from' }, ...PRESETS.map((pr) => ({ label: pr.name, sub: pr.description, icon: 'palette', onClick: () => openStyle({ id: '', name: pr.name === 'Obsidian' ? 'My style' : pr.name, description: pr.description, vars: { ...pr.vars }, fonts: pr.fonts || [], css: '' }) }))], { align: 'end' }), 'data-pop-anchor': '' }, icon('plus'), 'New style')
      : h('button.btn.small', { onclick: (e) => popover(e.currentTarget, [{ title: 'Start from' }, { label: 'Empty plugin', icon: 'puzzle', onClick: () => openPlugin({ id: '', name: 'My plugin', description: '', css: '', js: '', enabled: true }) }, ...TEMPLATES.map((t) => ({ label: t.name, sub: t.description, icon: 'spark', onClick: () => openPlugin({ id: '', enabled: true, css: '', js: '', ...t }) }))], { align: 'end' }), 'data-pop-anchor': '' }, icon('plus'), 'New plugin'),
  );
  const body = tab === 'styles'
    ? h('div.st-grid', styleCard(null), ...data.styles.map(styleCard))
    : data.plugins.length
      ? h('div.st-plugins.panel', ...data.plugins.map(pluginRow))
      : h('div.st-empty.panel', icon('puzzle'), h('b', 'No plugins yet'), h('span.muted', 'Describe one above, start from a template, or import a file someone shared.'));
  return [tabs, body];
}

// ---------- ask Scorp to make it ----------

function askBox() {
  const m = createMascot({ size: 46, sleepAfter: 0 });
  const input = h('textarea', { rows: 1, placeholder: tab === 'plugins' ? 'Describe a plugin — “a clock in the taskbar”, “a /translate command”…' : 'Describe a look — “warm paper with serif headings”, “neon cyberpunk”…', 'aria-label': 'Describe a style or plugin' });
  const status = h('div.st-ask-status');
  const sendBtn = h('button.send', { type: 'button', title: 'Make it' }, icon('arrow-up'));
  const fit = () => { input.style.height = 'auto'; input.style.height = Math.min(160, input.scrollHeight) + 'px'; };
  let busy = false;
  const run = async (text) => {
    text = (text || input.value).trim();
    if (!text || busy) return;
    busy = true;
    input.value = '';
    fit();
    box.classList.add('busy');
    m.set('working');
    clear(status);
    const line = h('span.shimmer', 'Thinking about it…');
    const reply = h('div.st-ask-reply');
    status.append(h('div.st-ask-line', line), reply);
    let full = '';
    let convId = localStorage.getItem(STUDIO_CONV) || null;
    try {
      await streamJSON('/api/chat', {
        body: { conversation_id: convId, text: '[Studio] ' + text, groups: ['looks'], model: state.chatModel || '' },
        onEvent: (ev) => {
          if (ev.type === 'run' && ev.conv_id) localStorage.setItem(STUDIO_CONV, (convId = ev.conv_id));
          else if (ev.type === 'tool_start') line.textContent = ev.name === 'app_plugin' ? 'Writing the plugin…' : ev.name === 'style_app' ? 'Designing the style…' : 'Working…';
          else if (ev.type === 'token') { full += ev.content || ''; reply.textContent = full; }
          else if (ev.type === 'done' && ev.content) { full = ev.content; reply.textContent = full; }
          else if (ev.type === 'tool_end' && ev.display && ev.display.type === 'look') {
            fresh = ev.display.id;
            tab = ev.display.kind === 'plugin' ? 'plugins' : 'styles';
          } else if (ev.type === 'approval') {
            status.append(h('div.st-ask-approval', icon('shield'), h('span', 'Allow ' + String(ev.tool).replace(/_/g, ' ') + '?'),
              h('button.btn.primary.small', { onclick: (e) => { e.currentTarget.parentElement.remove(); api.post('/api/approvals/' + ev.id, { decision: 'allow' }); } }, 'Allow'),
              h('button.btn.quiet.small', { onclick: (e) => { e.currentTarget.parentElement.remove(); api.post('/api/approvals/' + ev.id, { decision: 'deny' }); } }, 'Deny')));
          } else if (ev.type === 'error') reply.textContent = ev.error || 'Something went wrong.';
        },
      });
      line.textContent = 'Done';
      line.classList.remove('shimmer');
      m.flash('happy', 1600).hop();
      status.append(h('button.btn.quiet.small.st-ask-open', { onclick: () => navigate('/c/' + convId) }, icon('chat'), 'Continue in chat'));
    } catch (err) {
      line.textContent = err.message;
      line.classList.remove('shimmer');
      m.flash('sad', 1600);
    }
    m.set('idle');
    busy = false;
    box.classList.remove('busy');
    paintLibrary();
  };
  input.addEventListener('input', fit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      run();
    }
  });
  sendBtn.addEventListener('click', () => run());
  const box = h('div.st-ask',
    h('div.st-ask-field', h('span.st-ask-scorpi', m.el), input, sendBtn),
    h('div.st-ideas', IDEAS.map(([ic, text]) => h('button.chip', { type: 'button', onclick: () => run(text) }, icon(ic), text))),
    status);
  return box;
}

// ---------- style editor ----------

function colorField(key, label, draft, onChange) {
  const cur = () => draft.vars[key] ?? DEFAULTS[key] ?? '';
  const parsed = parseColor(cur()) || { hex: '#000000', a: 1 };
  const swatch = h('i');
  const well = h('input', { type: 'color', value: parsed.hex });
  const alpha = h('input.range.st-alpha', { type: 'range', min: 0, max: 100, value: Math.round(parsed.a * 100), title: 'Opacity' });
  const text = h('input.input.mono.st-color-text', { value: cur(), spellcheck: false });
  const sync = () => {
    swatch.style.background = cur();
    const p = parseColor(cur());
    if (p) {
      well.value = p.hex;
      alpha.value = Math.round(p.a * 100);
    }
  };
  const set = (v) => {
    if (v === DEFAULTS[key] || v === '') delete draft.vars[key];
    else draft.vars[key] = v;
    onChange();
    sync();
  };
  well.addEventListener('input', () => { const v = toCss(well.value, alpha.value / 100); text.value = v; set(v); });
  alpha.addEventListener('input', () => { const v = toCss(well.value, alpha.value / 100); text.value = v; set(v); });
  text.addEventListener('change', () => set(text.value.trim()));
  sync();
  const changed = key in draft.vars;
  return h('div.st-field' + (changed ? '.changed' : ''),
    h('label.st-field-label', label, h('code', key)),
    h('div.st-color', h('span.color-well', swatch, well), alpha, text,
      h('button.icon-btn.st-reset', { type: 'button', title: 'Back to default', onclick: () => { text.value = DEFAULTS[key] || ''; set(''); } }, icon('refresh'))));
}

function fontField(key, label, draft, onChange) {
  const fam = (v) => (String(v || '').match(/^"?([^",]+)"?/) || [])[1] || '';
  const cur = fam(draft.vars[key]);
  const sel = h('select.select', h('option', { value: '' }, 'Default'), FONTS.map((f) => h('option', { value: f, selected: f === cur }, f)), cur && !FONTS.includes(cur) ? h('option', { value: cur, selected: true }, cur) : null, h('option', { value: '__custom' }, 'Other Google font…'));
  sel.addEventListener('change', async () => {
    let f = sel.value;
    if (f === '__custom') {
      f = ((await promptSheet('A Google font', { label: 'Family name, exactly as on fonts.google.com', placeholder: 'Plus Jakarta Sans', confirm: 'Use font' })) || '').trim();
      if (!f) return (sel.value = cur);
    }
    if (!f) delete draft.vars[key];
    else {
      const mono = /mono|code/i.test(f);
      const serif = /serif|news|fraunces|lora|garamond|playfair/i.test(f) && !/sans/i.test(f);
      draft.vars[key] = `"${f}", ${mono ? 'ui-monospace, monospace' : serif ? 'Georgia, serif' : 'system-ui, sans-serif'}`;
      draft.fonts = [...new Set([...(draft.fonts || []), f])].slice(0, 4);
      loadFont(f);
    }
    onChange();
  });
  return h('div.st-field' + (key in draft.vars ? '.changed' : ''), h('label.st-field-label', label, h('code', key)), sel);
}

function rangeField(key, label, draft, onChange, { min, max, step, unit = '', fallback, parse = parseFloat, format = (v) => v + unit }) {
  const val = key in draft.vars ? parse(draft.vars[key]) : fallback;
  const out = h('span.range-val', format(val));
  const input = h('input.range', { type: 'range', min, max, step, value: val });
  input.addEventListener('input', () => {
    const v = +input.value;
    out.textContent = format(v);
    if (v === fallback) delete draft.vars[key];
    else draft.vars[key] = format(v);
    onChange();
  });
  return h('div.st-field' + (key in draft.vars ? '.changed' : ''), h('label.st-field-label', label, h('code', key)), h('div.st-range', input, out));
}

function specimen() {
  const m = createMascot({ size: 54 });
  return h('div.st-specimen.tokens-scope',
    h('div.sp-top', h('span.page-kicker', 'Preview'), h('span.grow'), h('span.badge', 'live')),
    h('div.sp-hero', h('div.sp-title', 'Good evening.'), h('div.sp-sub', 'What shall we make tonight?')),
    h('div.msg.user', h('div.slip', 'Make the canvas show this week’s plan')),
    h('div.sp-answer.md', h('p', 'Here is your week — ', h('strong', 'three focus blocks'), ' and a quiet Friday. ', h('a', { href: '#' }, 'Open the plan'), '.'), h('pre', h('code', 'const week = plan({ focus: 3 });'))),
    h('div.sp-row', h('button.btn.primary.small', 'Primary'), h('button.btn.small', 'Button'), h('button.btn.quiet.small', 'Quiet'), h('label.switch', h('input', { type: 'checkbox', checked: true }), h('span'))),
    h('div.sp-row', h('span.chip', icon('spark'), 'Chip'), h('span.badge.ok', 'ok'), h('span.badge.warn', 'warn'), h('span.badge.err', 'error')),
    h('div.sp-card.card', h('div.card-head', icon('calendar'), h('span.ch-title', 'A card from a tool')), h('div.card-body.muted', 'Plates, borders and the glass edge follow the style.')),
    h('div.sp-composer.composer', h('div.perch.sp-perch', m.el), h('div.sp-input', 'Ask anything…'), h('div.composer-bar', h('span.c-chip', icon('spark'), 'Skill'), h('span.grow'), h('span.send', icon('arrow-up')))),
  );
}

function openStyle(st) {
  const base = st || { id: '', name: 'Obsidian', vars: {}, css: '', fonts: [] };
  editing = { kind: 'style', draft: { id: base.id || '', name: base.name || 'My style', description: base.description || '', vars: { ...(base.vars || {}) }, css: base.css || '', fonts: [...(base.fonts || [])] }, whole: false };
  if (!st) editing.draft.name = 'Obsidian copy';
  paint();
}

function styleEditor() {
  const ed = editing;
  const d = ed.draft;
  let spec = null;
  const live = () => {
    // the specimen always shows the draft; the whole app only when asked
    if (spec) {
      spec.setAttribute('style', Object.entries(d.vars).map(([k, v]) => `${k}: ${v}`).join('; '));
      (d.fonts || []).forEach(loadFont);
    }
    if (ed.whole) previewStyle({ id: 'preview', ...d });
  };
  const onChange = () => live();
  const nameIn = h('input.input.st-name', { value: d.name, placeholder: 'Style name', oninput: () => (d.name = nameIn.value) });
  const descIn = h('input.input', { value: d.description, placeholder: 'One line about it', oninput: () => (d.description = descIn.value) });
  const css = h('textarea.input.mono.st-code', { rows: 10, spellcheck: false, placeholder: '/* extra rules, e.g. */\n.welcome-title { letter-spacing: -.06em; }' }, d.css);
  css.addEventListener('input', () => { d.css = css.value; if (ed.whole) live(); });
  codeKeys(css);
  const section = (title, ...fields) => h('section.st-sec', h('h3.st-sec-title', title), ...fields);
  const blur = rangeField('--glass-blur', 'Glass blur', d, onChange, { min: 0, max: 40, step: 1, fallback: 22, parse: (v) => (v === 'none' ? 0 : parseFloat(String(v).replace(/[^\d.]/g, '')) || 0), format: (v) => (v ? `blur(${v}px) saturate(165%)` : 'none') });
  const controls = h('div.st-controls',
    h('div.st-names', nameIn, descIn),
    section('Colour', ...COLOR_TOKENS.map(([k, l]) => colorField(k, l, d, onChange))),
    section('Glass', ...GLASS_TOKENS.map(([k, l]) => colorField(k, l, d, onChange)), blur),
    section('Type',
      fontField('--font-ui', 'Text font', d, onChange), fontField('--font-display', 'Headline font', d, onChange), fontField('--font-mono', 'Code font', d, onChange),
      rangeField('--fs', 'Text size', d, onChange, { min: 12, max: 19, step: 0.5, fallback: 15, unit: 'px' })),
    section('Shape & layout',
      rangeField('--round', 'Corner roundness', d, onChange, { min: 0, max: 1.6, step: 0.05, fallback: 1, format: (v) => String(+(+v).toFixed(2)) }),
      rangeField('--chat-w', 'Chat width', d, onChange, { min: 560, max: 1100, step: 10, fallback: 760, unit: 'px' })),
    section('Scorpi', ...SCORPI_TOKENS.map(([k, l]) => colorField(k, l, d, onChange))),
    section('Custom CSS', h('p.st-hint', 'For what tokens can’t do. Shown on the whole app while “Preview everywhere” is on.'), css),
  );
  spec = specimen();
  const wholeSw = switchEl(ed.whole, (v) => { ed.whole = v; if (v) live(); else previewStyle(null); });
  const save = async (activate) => {
    try {
      const r = await api.post('/api/looks/styles', { id: d.id || undefined, name: d.name.trim() || 'My style', description: d.description, vars: d.vars, css: d.css, fonts: d.fonts, activate });
      previewStyle(null);
      fresh = r.style.id;
      editing = null;
      tab = 'styles';
      toast(activate ? `“${r.style.name}” is on, everywhere` : `Saved “${r.style.name}”`, { timeout: 2200 });
      paint();
    } catch (err) {
      toast('Could not save', { type: 'error', body: err.message });
    }
  };
  const ask = askInEditor(() => `Change the style "${d.name}"${d.id ? '' : ' (start from these tokens: ' + JSON.stringify(d.vars) + ')'}: `, (data) => {
    const st = data.styles.find((x) => x.id === d.id || x.name === d.name);
    if (st) { openStyle(st); }
  });
  setTimeout(live, 0);
  return h('div.st-editor',
    h('header.st-ed-head',
      h('button.page-back', { type: 'button', onclick: () => { previewStyle(null); editing = null; paint(); } }, icon('left'), 'Studio'),
      h('div.st-ed-title', h('div.page-kicker', d.id ? 'Edit style' : 'New style'), h('h1', d.name || 'Style')),
      h('span.st-whole', 'Preview everywhere', wholeSw),
      h('button.btn.quiet', { onclick: () => { previewStyle(null); editing = null; paint(); } }, 'Cancel'),
      h('button.btn', { onclick: () => save(false) }, 'Save'),
      h('button.btn.primary', { onclick: () => save(true) }, icon('check'), 'Save & use')),
    h('div.st-ed-body', controls, h('aside.st-ed-side', spec, ask)),
  );
}

// ---------- plugin editor ----------

function codeKeys(ta) {
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const { selectionStart: a, selectionEnd: b, value } = ta;
      ta.value = value.slice(0, a) + '  ' + value.slice(b);
      ta.selectionStart = ta.selectionEnd = a + 2;
      ta.dispatchEvent(new Event('input'));
    }
  });
}

function openPlugin(p) {
  editing = { kind: 'plugin', draft: { id: p.id || '', name: p.name || 'My plugin', description: p.description || '', css: p.css || '', js: p.js || '', enabled: p.enabled !== false } };
  paint();
}

const API_REF = [
  ['scorp.css(text)', 'Add CSS'],
  ['scorp.el(tag, props, …kids)', 'Build elements: \'div.class#id\''],
  ['scorp.icon(name)', 'An icon: chat, code, spark, clock, bell, globe, bolt, puzzle…'],
  ['scorp.tray({icon, title, onClick})', 'A button in the taskbar tray'],
  ['scorp.panel({title, icon, render})', 'A floating glass panel'],
  ['scorp.command({name, run(args)})', 'A /command in the prompt box'],
  ['scorp.every(ms, fn)', 'A timer'],
  ['scorp.on(event, fn)', 'Server events: agent_job, conversation, notification, music'],
  ['scorp.onRoute(fn)', 'The page changed'],
  ['scorp.ask(text)', 'Send a message to Scorp'],
  ['scorp.say(text)', 'Scorpi says it in a bubble'],
  ['scorp.toast(text, {body})', 'A small notification'],
  ['scorp.storage.get/set(key, value)', 'Keep data on this device'],
  ['scorp.api.get/post(path, body)', 'Scorp’s own API'],
  ['scorp.state()', '{view, conversation, project, model}'],
  ['return () => {…}', 'Cleanup when turned off'],
];

function pluginEditor() {
  const d = editing.draft;
  const nameIn = h('input.input.st-name', { value: d.name, placeholder: 'Plugin name', oninput: () => (d.name = nameIn.value) });
  const descIn = h('input.input', { value: d.description, placeholder: 'What it does, in one line', oninput: () => (d.description = descIn.value) });
  const js = h('textarea.input.mono.st-code.tall', { rows: 18, spellcheck: false, placeholder: '// runs with `scorp`, e.g.\nscorp.tray({ icon: \'spark\', title: \'Hi\', onClick: () => scorp.say(\'Hello!\') });' }, d.js);
  const css = h('textarea.input.mono.st-code', { rows: 7, spellcheck: false, placeholder: '/* CSS for the whole app */' }, d.css);
  js.addEventListener('input', () => (d.js = js.value));
  css.addEventListener('input', () => (d.css = css.value));
  codeKeys(js);
  codeKeys(css);
  const save = async (run) => {
    try {
      const r = await api.post('/api/looks/plugins', { id: d.id || undefined, name: d.name.trim() || 'My plugin', description: d.description, css: d.css, js: d.js, enabled: run ? true : d.enabled });
      d.id = r.plugin.id;
      d.enabled = r.plugin.enabled;
      fresh = r.plugin.id;
      toast(run ? `“${r.plugin.name}” is running` : `Saved “${r.plugin.name}”`, { timeout: 2000 });
      if (!run) {
        editing = null;
        tab = 'plugins';
      }
      paint();
    } catch (err) {
      toast('Could not save', { type: 'error', body: err.message });
    }
  };
  const ask = askInEditor(() => `Change the plugin "${d.name}" (current JavaScript: ${JSON.stringify(d.js).slice(0, 4000)}; CSS: ${JSON.stringify(d.css).slice(0, 2000)}): `, (data) => {
    const p = data.plugins.find((x) => x.id === d.id || x.name === d.name);
    if (p) openPlugin(p);
  });
  return h('div.st-editor',
    h('header.st-ed-head',
      h('button.page-back', { type: 'button', onclick: () => { editing = null; paint(); } }, icon('left'), 'Studio'),
      h('div.st-ed-title', h('div.page-kicker', d.id ? 'Edit plugin' : 'New plugin'), h('h1', d.name || 'Plugin')),
      h('button.btn.quiet', { onclick: () => { editing = null; paint(); } }, 'Close'),
      h('button.btn', { onclick: () => save(false) }, 'Save'),
      h('button.btn.primary', { onclick: () => save(true) }, icon('play'), 'Save & run')),
    h('div.st-ed-body',
      h('div.st-controls',
        h('div.st-names', nameIn, descIn),
        h('section.st-sec', h('h3.st-sec-title', 'JavaScript'), js),
        h('section.st-sec', h('h3.st-sec-title', 'CSS'), css)),
      h('aside.st-ed-side',
        h('div.st-api.panel', h('div.st-api-head', icon('book'), h('b', 'The scorp API'), h('span.muted', 'everything is cleaned up when the plugin turns off')),
          h('dl', API_REF.flatMap(([sig, what]) => [h('dt', h('code', sig)), h('dd', what)]))),
        ask)),
  );
}

// "make it rounder", "add a pause button": the editor asks Scorp and reloads what it changed
function askInEditor(prefix, reload) {
  const input = h('input.input', { placeholder: 'Ask Scorp to change it…' });
  const line = h('div.st-hint');
  const m = createMascot({ size: 30, sleepAfter: 0 });
  const go = async () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    m.set('working');
    line.textContent = 'Working on it…';
    let convId = localStorage.getItem(STUDIO_CONV) || null;
    try {
      await streamJSON('/api/chat', {
        body: { conversation_id: convId, text: '[Studio] ' + prefix() + text, groups: ['looks'], model: state.chatModel || '' },
        onEvent: (ev) => {
          if (ev.type === 'run' && ev.conv_id) localStorage.setItem(STUDIO_CONV, (convId = ev.conv_id));
          if (ev.type === 'done' && ev.content) line.textContent = ev.content.slice(0, 220);
          if (ev.type === 'error') line.textContent = ev.error;
        },
      });
      m.flash('happy', 1400);
      setTimeout(() => reload(looks()), 300);
    } catch (err) {
      line.textContent = err.message;
    }
    m.set('idle');
  };
  input.addEventListener('keydown', (e) => e.key === 'Enter' && go());
  return h('div.st-ed-ask.panel', h('div.st-ed-ask-row', m.el, input, h('button.btn.small', { onclick: go }, 'Ask')), line);
}

// ---------- page ----------

let libBox = null;
function paintLibrary() {
  if (!libBox || !libBox.isConnected) return;
  clear(libBox);
  libBox.append(...library());
  if (fresh) setTimeout(() => (fresh = ''), 2600);
}

function paint() {
  if (!root || !root.isConnected) return;
  clear(root);
  if (editing) {
    root.appendChild(h('div.page.studio', h('div.page-inner.wide', editing.kind === 'style' ? styleEditor() : pluginEditor())));
    return;
  }
  libBox = h('div.st-library');
  const back = h('button.page-back', { type: 'button', onclick: () => navigate(state.lastChatPath || sessionStorage.getItem('scorp.lastChat') || '/') }, icon('left'), 'Back');
  root.appendChild(h('div.page.studio', h('div.page-inner.wide',
    back,
    h('div.page-head', h('div.ph-text', h('div.page-kicker', 'Studio'), h('h1.page-title', 'Make Scorp ', h('em', 'yours')), h('p.page-sub', 'Design styles and build plugins by hand — or describe what you want and Scorp makes it. Everything applies live on every device.'))),
    askBox(),
    libBox,
  )));
  paintLibrary();
}

// leaving the Studio while a style is previewed everywhere: back to the real one
bus.on('route', (r) => {
  if (r.view !== 'studio' && editing && editing.kind === 'style') {
    previewStyle(null);
    editing = null;
  }
});

export function renderStudio(el) {
  root = el;
  if (editing && editing.kind === 'style') previewStyle(null);
  editing = null;
  paint();
  const off = bus.on('looks', () => {
    if (!root.isConnected) return off();
    if (!editing && state.view === 'studio') paintLibrary();
  });
}

// styleCss is re-exported for plugins that want the CSS of a style
export { styleCss };
