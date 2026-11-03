// Studio: make Scorp look and work the way you want. Styles (design tokens + CSS) and plugins (small mods: CSS and
// JavaScript with the scorp API) — made by hand in the editors here, or described in words and built by Scorp.
// Everything applies live on every device; styles and plugins can be exported as files and imported again.

import { api, streamJSON } from '../lib/api.js';
import { clear, debounce, h, icon } from '../lib/dom.js';
import { closePop, confirmSheet, popover, promptSheet, seg, selectEl, sheet, switchEl, toast } from '../lib/ui.js';
import { looks, pluginLog, previewStyle, styleCss } from '../lib/looks.js';
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

const IDEAS = {
  styles: [
    ['palette', 'Warm paper look with serif headings'],
    ['palette', 'Pure black and white, square corners, small text'],
    ['palette', 'Soft pastel lilac glass with rounder corners'],
  ],
  plugins: [
    ['puzzle', 'A habit tracker pane beside the chat, synced to all my devices'],
    ['puzzle', 'A button under answers that turns them into a checklist'],
    ['puzzle', 'Always answer like a calm coach, with a setting for how strict'],
    ['puzzle', 'A /weather command with my city as a setting'],
  ],
};

// Examples: real plugins, one click to add. Each shows off part of the scorp API.
const TEMPLATES = [
  {
    name: 'Scratchpad', icon: 'edit', shows: ['pane', 'shared', 'messageAction', 'key'],
    description: 'A notes pane beside the chat, the same on every device. “Keep” under any answer saves it there (Ctrl+Shift+E)',
    css: '.plg-scratch .plg-item .grow { white-space: pre-wrap; font-size: var(--t-md); line-height: 1.5; }\n.plg-scratch textarea { width: 100%; resize: none; }',
    js: String.raw`// notes live on the server, so every device shows the same list, live
const notes = scorp.signal(scorp.shared.get('notes', []));
scorp.shared.watch('notes', (v) => notes.set(v || []));
const save = (list) => { notes.set(list); scorp.shared.set('notes', list); };
const add = (text) => save([{ id: Date.now(), text }, ...notes.get()]);

const input = scorp.el('textarea.input', { rows: 2, placeholder: 'Write a note… (Enter keeps it)' });
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && input.value.trim()) {
    e.preventDefault();
    add(input.value.trim());
    input.value = '';
  }
});

const list = scorp.el('ul.plg-list');
scorp.mount(list, () => notes.get().length
  ? notes.get().map((n) => scorp.el('li.plg-item',
      scorp.el('span.grow', n.text),
      scorp.el('button.icon-btn', { title: 'Ask Scorp about it', onclick: () => scorp.ask(n.text) }, scorp.icon('chat')),
      scorp.el('button.icon-btn', { title: 'Done', onclick: () => save(notes.get().filter((x) => x.id !== n.id)) }, scorp.icon('check'))))
  : scorp.el('p.muted', 'Nothing yet. Notes kept here follow you to every device.'), notes);

const pane = scorp.pane({
  title: 'Scratchpad', icon: 'edit', width: 340,
  render: (body) => { body.classList.add('plg-scratch'); body.append(input, scorp.el('div.plg-kicker', 'Notes'), list); },
});

scorp.messageAction({
  icon: 'pin', label: 'Keep', title: 'Keep this answer in the scratchpad',
  run(text) {
    add(text.length > 800 ? text.slice(0, 800) + '…' : text);
    pane.open();
    scorp.sound('pop');
  },
});

scorp.key('ctrl+shift+e', () => { pane.toggle(); if (pane.isOpen) input.focus(); });`,
  },
  {
    name: 'Focus timer', icon: 'clock', shows: ['status', 'band', 'options', 'sound'],
    description: 'A pomodoro in the prompt box: click the clock, and a band counts down above it',
    options: [
      { key: 'minutes', label: 'Focus minutes', type: 'range', min: 5, max: 90, step: 5, default: 25 },
      { key: 'breakMinutes', label: 'Break minutes', type: 'range', min: 1, max: 30, step: 1, default: 5 },
      { key: 'sound', label: 'Chime at the end', type: 'toggle', default: true },
    ],
    css: '.plg-track { display: inline-block; width: 140px; height: 4px; border-radius: 4px; background: var(--fill-2); overflow: hidden; }\n.plg-track i { display: block; height: 100%; width: 0; background: var(--accent); border-radius: inherit; transition: width 1s linear; }',
    js: String.raw`let left = 0, total = 1, mode = 'idle', timer = null;
const fmt = (s) => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');

const status = scorp.status({ icon: 'clock', text: 'Focus', title: 'Focus timer: click to start or stop', onClick: () => (mode === 'idle' ? start('focus') : stop()) });
const band = scorp.band({ hidden: true });
const label = scorp.el('span.grow');
const fill = scorp.el('i');
band.el.append(scorp.icon('clock'), label, scorp.el('span.plg-track', fill), scorp.el('button.btn.quiet.small', { onclick: () => stop() }, 'Stop'));

function start(kind) {
  mode = kind;
  total = left = (kind === 'focus' ? scorp.options.minutes : scorp.options.breakMinutes) * 60;
  clearInterval(timer);
  timer = setInterval(() => { left -= 1; draw(); if (left <= 0) done(); }, 1000);
  draw();
  band.show();
}
function draw() {
  status.set(fmt(Math.max(0, left)));
  label.textContent = (mode === 'focus' ? 'Focus' : 'Break') + ' · ' + fmt(Math.max(0, left)) + ' left';
  fill.style.width = (100 * (1 - left / total)) + '%';
}
function done() {
  clearInterval(timer);
  if (scorp.options.sound) scorp.sound('chime');
  if (mode === 'focus') {
    scorp.say('Time for a break!');
    start('break');
  } else {
    scorp.say('Break is over. Ready when you are.');
    stop();
  }
}
function stop() {
  clearInterval(timer);
  mode = 'idle';
  band.hide();
  status.set('Focus');
}
return () => clearInterval(timer);`,
  },
  {
    name: 'Translate', icon: 'globe', shows: ['messageAction', 'llm', 'options'],
    description: 'A Translate button under every answer; the translation appears right below it',
    options: [{ key: 'language', label: 'Translate into', type: 'text', default: 'German', help: 'Any language the model knows' }],
    js: String.raw`scorp.messageAction({
  icon: 'globe', label: 'Translate', title: 'Translate this answer',
  async run(text, msg) {
    const lang = scorp.options.language || 'English';
    let box = msg.querySelector('.plg-translation');
    if (!box) {
      box = scorp.el('div.plg-note.plg-translation', { dataset: { plugin: scorp.id } });
      msg.querySelector('.turn').append(box);
    }
    box.replaceChildren(scorp.el('div.plg-kicker', lang), scorp.el('span.muted', 'Translating…'));
    try {
      const out = await scorp.llm('Translate into ' + lang + '. Keep the formatting. Reply with the translation only:\n\n' + text);
      box.replaceChildren(scorp.el('div.plg-kicker', lang), scorp.el('div', { style: { whiteSpace: 'pre-wrap' } }, out));
    } catch (err) {
      box.replaceChildren(scorp.el('div.plg-kicker', lang), scorp.el('span.muted', 'Could not translate: ' + err.message));
    }
  },
});`,
  },
  {
    name: 'Text expander', icon: 'wand', shows: ['hook send', 'status', 'options'],
    description: 'Type ;sig or ;tldr and it expands when you send. Snippets are a setting',
    options: [{
      key: 'snippets', label: 'Snippets', type: 'textarea',
      default: ';tldr = Summarise this in three short bullet points:\n;eli5 = Explain it like I am five:\n;fix = Find and fix the bugs in this code, then explain what was wrong:',
      help: 'One per line: ;name = text. \\n makes a new line.',
    }],
    js: String.raw`const snippets = () => Object.fromEntries(String(scorp.options.snippets || '').split('\n')
  .map((line) => line.match(/^\s*(;[\w-]+)\s*=\s*(.*)$/)).filter(Boolean)
  .map((m) => [m[1], m[2].replace(/\\n/g, '\n')]));

// middleware on every message you send: rewrite it, then pass it on
scorp.hook('send', (e, next) => next({ ...e, text: e.text.replace(/;[\w-]+/g, (k) => snippets()[k] ?? k) }));

// a hint in the prompt box while you type one
const hint = scorp.status({ title: 'Text expander' });
const input = document.getElementById('input');
const onInput = () => {
  const k = (input.value.match(/;[\w-]+$/) || [])[0];
  const s = k && snippets()[k];
  hint.set(s ? k + ' → ' + (s.length > 28 ? s.slice(0, 28) + '…' : s) : '');
};
input.addEventListener('input', onInput);
return () => input.removeEventListener('input', onInput);`,
  },
  {
    name: 'Persona', icon: 'user', shows: ['prompt', 'options', 'status'],
    description: 'Scorp answers in a voice you choose. Adds to its instructions; the voice is a setting',
    prompt: 'Answer in the voice of {{character}}, at a {{intensity}} level of character. Stay accurate and helpful: the voice is flavour, never a reason to drop facts or steps.',
    options: [
      { key: 'character', label: 'Voice', type: 'text', default: 'a friendly pirate captain' },
      { key: 'intensity', label: 'How strong', type: 'select', choices: [['subtle', 'Subtle'], ['clear', 'Clear'], ['full', 'Full character']], default: 'clear' },
    ],
    js: String.raw`const chip = scorp.status({ icon: 'user', text: scorp.options.character, title: 'Persona plugin: Scorp speaks as this' });
scorp.onOptions((o) => chip.set(o.character));`,
  },
  {
    name: 'Tool ticker', icon: 'bolt', shows: ['hook tool', 'band', 'signal'],
    description: 'Shows the tools Scorp is using right now in a band above the prompt box',
    options: [{ key: 'sound', label: 'Tick when a tool starts', type: 'toggle', default: false }],
    css: '.plg-band .chip.plg-live { animation: plg-pulse 1.2s ease-in-out infinite; }\n@keyframes plg-pulse { 50% { opacity: .45; } }\n.plg-band .chip { height: 24px; font-size: var(--t-xs); }',
    js: String.raw`const band = scorp.band({ hidden: true });
const tools = scorp.signal([]);
scorp.mount(band.el, () => [
  scorp.icon('bolt'),
  ...tools.get().slice(-5).map((t) => scorp.el('span.chip' + (t.done ? '' : '.plg-live'), (t.done ? (t.ok ? '✓ ' : '✕ ') : '') + t.name.replace(/_/g, ' '))),
], tools);

scorp.hook('tool', (e) => {
  if (e.phase === 'start') {
    tools.update((list) => [...list, { id: e.id, name: e.name }]);
    band.show();
    if (scorp.options.sound) scorp.sound('tick');
  } else {
    tools.update((list) => list.map((t) => (t.id === e.id ? { ...t, done: true, ok: e.ok } : t)));
  }
});
scorp.on('run_done', () => scorp.after(2500, () => { tools.set([]); band.hide(); }));`,
  },
  {
    name: 'Dice', icon: 'cube', shows: ['command'],
    description: 'A /dice command in the prompt box, e.g. /dice 2d6',
    js: String.raw`scorp.command({
  name: 'dice',
  description: 'Roll dice, e.g. /dice 2d6',
  icon: 'cube',
  run(args) {
    const [n, sides] = (args.match(/(\d*)d(\d+)/) || [0, '1', '6']).slice(1).map((x) => +x || 1);
    const rolls = Array.from({ length: Math.min(n, 20) }, () => 1 + Math.floor(Math.random() * sides));
    scorp.sound('pop');
    scorp.say('🎲 ' + rolls.join(' + ') + ' = ' + rolls.reduce((a, b) => a + b, 0));
  },
});`,
  },
  {
    name: 'Clock with seconds', icon: 'clock', shows: ['tray'],
    description: 'A clock with seconds in the taskbar tray',
    css: '.tk-plugin .plg-clock { font: 600 12px var(--font-mono); margin-left: 6px; }',
    js: String.raw`const btn = scorp.tray({ icon: 'clock', title: 'Time' });
const label = scorp.el('span.plg-clock');
btn.append(label);
const tick = () => (label.textContent = new Date().toLocaleTimeString());
tick();
scorp.every(1000, tick);`,
  },
  {
    name: 'Focus mode', icon: 'eye', shows: ['css'],
    description: 'Hide the taskbar until the mouse reaches the bottom',
    css: `@media (min-width: 901px) {
  .taskbar { transform: translateY(calc(100% + 12px)); transition: transform .35s var(--ease-out); }
  .taskbar:hover, .taskbar:focus-within { transform: none; }
  .taskbar::before { content: ""; position: absolute; left: 0; right: 0; bottom: 100%; height: 20px; }
  :root { --tkh: 8px; }
}`,
  },
];

const PLUGIN_ICONS = ['puzzle', 'spark', 'bolt', 'wand', 'clock', 'calendar', 'chat', 'edit', 'globe', 'palette', 'speaker', 'radio', 'star', 'eye', 'user', 'cube', 'chart', 'book', 'code', 'terminal', 'compass', 'sun', 'moon', 'bell', 'pin', 'shield'];

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

const hasErrors = (id) => pluginLog(id).some((l) => l.level === 'error');

function pluginRow(p) {
  const more = (e) => popover(e.currentTarget, [
    { label: 'Edit', icon: 'edit', onClick: () => openPlugin(p) },
    (p.options || []).length ? { label: 'Settings', icon: 'sliders', onClick: () => optionsSheet(p) } : null,
    { label: 'Export', sub: 'Save as a file to share', icon: 'download', onClick: () => exportItem('plugin', p.id, p.name) },
    'sep',
    { label: 'Delete', icon: 'trash', danger: true, onClick: async () => {
      if (await confirmSheet('Delete “' + p.name + '”?', 'The plugin and what it keeps are removed on every device.', { confirm: 'Delete', danger: true })) api.del('/api/looks/plugins/' + p.id).catch((err) => toast(err.message, 'error'));
    } },
  ], { align: 'end' });
  const err = p.enabled && hasErrors(p.id);
  return h('div.st-plugin' + (fresh === p.id ? '.fresh' : '') + (p.enabled ? '' : '.off'),
    h('span.st-plugin-ic', icon(p.icon || 'puzzle'), err ? h('i.st-err-dot', { title: 'It reported errors: open it to see its console' }) : null),
    h('div.st-plugin-text', h('div.st-plugin-name', p.name, ...pluginBadges(p)), h('div.st-plugin-desc', p.description || '')),
    (p.options || []).length ? h('button.icon-btn', { type: 'button', title: 'Settings', onclick: () => optionsSheet(p) }, icon('sliders')) : null,
    h('button.icon-btn', { type: 'button', title: 'Edit', onclick: () => openPlugin(p) }, icon('edit')),
    h('button.icon-btn', { type: 'button', title: 'More', 'data-pop-anchor': '', onclick: more }, icon('more')),
    switchEl(p.enabled, (on) => api.post('/api/looks/plugins/' + p.id, { enabled: on }).catch((e) => toast(e.message, 'error'))),
  );
}

function pluginBadges(p) {
  return [p.js ? h('span.badge', 'JS') : null, p.css ? h('span.badge', 'CSS') : null, p.prompt ? h('span.badge', { title: 'Adds to Scorp’s instructions' }, 'Prompt') : null];
}

/** The form for a plugin's settings, drawn from its options */
function optionsSheet(p) {
  const values = { ...(p.settings || {}) };
  const push = debounce(() => api.post(`/api/looks/plugins/${p.id}/values`, { values }).catch((err) => toast(err.message, 'error')), 300);
  const set = (k, v) => {
    values[k] = v;
    push();
  };
  const control = (o) => {
    const v = values[o.key] ?? o.default;
    switch (o.type) {
      case 'toggle':
        return switchEl(v, (on) => set(o.key, on));
      case 'select':
        return selectEl(o.choices || [], v, (x) => set(o.key, x));
      case 'color': {
        const well = h('input', { type: 'color', value: /^#[0-9a-f]{6}$/i.test(v) ? v : '#ffffff', oninput: () => set(o.key, well.value) });
        return h('span.color-well', h('i', { style: { background: v } }), well);
      }
      case 'range': {
        const out = h('span.range-val', String(v));
        return h('div.set-range', h('input.range', { type: 'range', min: o.min ?? 0, max: o.max ?? 100, step: o.step ?? 1, value: v, oninput: (e) => { out.textContent = e.target.value; set(o.key, +e.target.value); } }), out);
      }
      case 'number':
        return h('input.input', { type: 'number', min: o.min, max: o.max, step: o.step, value: v, oninput: (e) => set(o.key, +e.target.value) });
      case 'textarea':
        return h('textarea.input.mono', { rows: 5, spellcheck: false, oninput: (e) => set(o.key, e.target.value) }, v);
      default:
        return h('input.input', { value: v, oninput: (e) => set(o.key, e.target.value) });
    }
  };
  sheet({
    title: p.name,
    body: h('div.st-opts',
      h('p.muted', 'Changes apply right away, on every device.'),
      h('div.set-card', (p.options || []).map((o) => h('div.set-row' + (o.type === 'textarea' ? '.stack' : ''),
        h('div', h('div.set-label', o.label || o.key), o.help ? h('div.set-desc', o.help) : null),
        h('div.set-control', control(o)))))),
    foot: (close) => [h('button.btn.primary', { onclick: () => close() }, 'Done')],
  });
}

async function addTemplate(t) {
  try {
    const r = await api.post('/api/looks/plugins', { name: t.name, description: t.description, icon: t.icon, css: t.css || '', js: t.js || '', prompt: t.prompt || '', options: t.options || [], enabled: true });
    fresh = r.plugin.id;
    toast(`“${r.plugin.name}” is on`, { body: 'Everywhere, right now. Open it to see how it works.', timeout: 3000 });
    paintLibrary();
  } catch (err) {
    toast('Could not add it', { type: 'error', body: err.message });
  }
}

function gallery() {
  const have = new Set(looks().plugins.map((p) => p.name.toLowerCase()));
  return h('section.st-gallery',
    h('div.st-gallery-head', h('h2', 'Examples'), h('span.muted', 'Real plugins to start from — add one, then open it to see how it is made.')),
    h('div.st-gallery-grid', TEMPLATES.map((t) => {
      const added = have.has(t.name.toLowerCase());
      return h('article.st-tpl',
        h('div.st-tpl-top', h('span.st-plugin-ic', icon(t.icon || 'puzzle')), h('div.st-tpl-name', t.name)),
        h('p.st-tpl-desc', t.description),
        h('div.st-tpl-tags', (t.shows || []).map((s) => h('code', s))),
        h('div.st-tpl-foot',
          h('button.btn.quiet.small', { onclick: () => openPlugin({ id: '', enabled: true, css: '', js: '', ...t }) }, 'Open in editor'),
          h('span.grow'),
          added ? h('span.badge', icon('check'), 'Added') : h('button.btn.small', { onclick: () => addTemplate(t) }, icon('plus'), 'Add')));
    })));
}

function library() {
  const data = looks();
  const tabs = h('div.st-bar',
    seg([['styles', `Styles · ${data.styles.length + 1}`], ['plugins', `Plugins · ${data.plugins.length}`]], tab, (v) => { tab = v; paint(); }),
    h('span.grow'),
    h('button.btn.quiet.small', { onclick: importFile }, icon('upload'), 'Import'),
    tab === 'styles'
      ? h('button.btn.small', { onclick: (e) => popover(e.currentTarget, [{ title: 'Start from' }, ...PRESETS.map((pr) => ({ label: pr.name, sub: pr.description, icon: 'palette', onClick: () => openStyle({ id: '', name: pr.name === 'Obsidian' ? 'My style' : pr.name, description: pr.description, vars: { ...pr.vars }, fonts: pr.fonts || [], css: '' }) }))], { align: 'end' }), 'data-pop-anchor': '' }, icon('plus'), 'New style')
      : h('button.btn.small', { onclick: () => openPlugin({ id: '', name: 'My plugin', description: '', css: '', js: STARTER_JS, enabled: true }) }, icon('plus'), 'New plugin'),
  );
  const body = tab === 'styles'
    ? h('div.st-grid', styleCard(null), ...data.styles.map(styleCard))
    : [
        data.plugins.length
          ? h('div.st-plugins.panel', ...data.plugins.map(pluginRow))
          : h('div.st-empty.panel', icon('puzzle'), h('b', 'No plugins yet'), h('span.muted', 'Describe one above, add an example below, or import a file someone shared.')),
        gallery(),
      ];
  return [tabs, body];
}

const STARTER_JS = String.raw`// Everything you add through scorp goes away again when the plugin is turned off.
const count = scorp.signal(0);

scorp.pane({
  title: 'My plugin', icon: 'spark',
  render(body) {
    const n = scorp.el('div.plg-big');
    scorp.mount(n, () => count.get(), count);
    body.append(n, scorp.el('button.btn.primary.small', { onclick: () => count.update((c) => c + 1) }, 'Count'));
  },
});`;

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
    h('div.st-ideas', IDEAS[tab === 'plugins' ? 'plugins' : 'styles'].map(([ic, text]) => h('button.chip', { type: 'button', onclick: () => run(text) }, icon(ic), text))),
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
  editing = {
    kind: 'plugin',
    draft: {
      id: p.id || '', name: p.name || 'My plugin', description: p.description || '', icon: p.icon || 'puzzle', css: p.css || '', js: p.js || '',
      prompt: p.prompt || '', options: (p.options || []).length ? JSON.stringify(p.options, null, 2) : '', enabled: p.enabled !== false,
    },
  };
  paint();
}

const API_REF = [
  ['Surfaces', [
    ['scorp.pane({title, icon, width, render(body, pane)})', 'A glass pane docked beside the chat · pane.open/close/toggle'],
    ['scorp.band({render(el, band)})', 'A strip above the prompt box · band.show/hide'],
    ['scorp.status({icon, text, onClick})', 'A live item in the prompt box bar · status.set(text)'],
    ['scorp.messageAction({icon, label, run(text, el)})', 'A button under every answer'],
    ['scorp.command({name, description, run(args)})', 'A /command in the prompt box'],
    ['scorp.tray({icon, title, onClick})', 'A button in the taskbar tray'],
    ['scorp.panel({title, icon, render})', 'A floating glass panel'],
    ['scorp.key(\'ctrl+shift+k\', fn)', 'A keyboard shortcut'],
    ['scorp.toast(text, {body}) · scorp.say(text)', 'A notification · Scorpi speaks'],
    ['scorp.sound(\'ding\')', 'ding, chime, pop, tick, success, error or a URL'],
  ]],
  ['Hooks', [
    ['scorp.hook(\'send\', (e, next) => next({...e, text}))', 'Rewrite a message before it goes; return null to stop it'],
    ['scorp.hook(\'answer\', (e) => e.add(node))', 'An answer is drawn: {text, el, live, add}'],
    ['scorp.hook(\'tool\', (e) => …)', 'Scorp uses a tool: {phase, name, args, ok, result}'],
    ['scorp.hook(\'route\', (e) => …)', 'The page changed: {view, id}'],
    ['scorp.on(event, fn)', 'Server events: run_done, agent_job, notification, music…'],
  ]],
  ['State & data', [
    ['scorp.signal(v) · scorp.mount(el, render, …signals)', 'A value, and a view that redraws when it changes'],
    ['scorp.options · scorp.onOptions(fn)', 'Its settings (declared under Settings)'],
    ['scorp.shared.get/set/watch(key)', 'Kept on the server, live on every device'],
    ['scorp.storage.get/set(key, value)', 'Kept on this device only'],
  ]],
  ['AI', [
    ['await scorp.llm(prompt, {system, json})', 'Ask the model directly (no chat message)'],
    ['scorp.ask(text)', 'Send a message in the chat'],
    ['Prompt (below)', 'Adds to Scorp’s instructions while on; {{key}} = a setting'],
  ]],
  ['Helpers', [
    ['scorp.el(\'div.class\', props, …kids) · scorp.icon(name)', 'Build elements · an icon'],
    ['scorp.css(text)', 'Add CSS'],
    ['scorp.ui.confirm/prompt/switch/seg/select', 'The app’s own dialogs and controls'],
    ['scorp.every(ms, fn) · scorp.after(ms, fn)', 'Timers'],
    ['scorp.api.get/post(path) · scorp.state()', 'Scorp’s API · {view, conversation, model}'],
    ['scorp.log(…) · console.log(…)', 'Writes to the console here'],
    ['return () => {…}', 'Cleanup when turned off'],
  ]],
];

const OPTIONS_HINT = `[
  { "key": "city", "label": "City", "type": "text", "default": "Vienna" },
  { "key": "sound", "label": "Play a sound", "type": "toggle", "default": true },
  { "key": "size", "label": "Size", "type": "range", "min": 1, "max": 10, "default": 5 },
  { "key": "mode", "label": "Mode", "type": "select", "choices": [["a", "First"], ["b", "Second"]] }
]`;

function consolePanel(id) {
  const box = h('div.st-console-body');
  const draw = () => {
    const lines = id ? pluginLog(id) : [];
    clear(box);
    if (!lines.length) box.append(h('div.st-console-empty', id ? 'Quiet. console.log and errors from this plugin show up here.' : 'Save & run it to see its output here.'));
    for (const l of lines.slice(-60)) {
      box.append(h('div.st-log.' + l.level, h('time', new Date(l.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })), h('span', l.text)));
    }
    box.scrollTop = box.scrollHeight;
  };
  draw();
  const off = bus.on('plugin:log', (pid) => {
    if (!box.isConnected) return off();
    if (pid === id) draw();
  });
  return { el: h('div.st-console.panel', h('div.st-api-head', icon('terminal'), h('b', 'Console'), h('span.grow'), h('span.muted', 'this device')), box), draw };
}

function pluginEditor() {
  const d = editing.draft;
  const nameIn = h('input.input.st-name', { value: d.name, placeholder: 'Plugin name', oninput: () => (d.name = nameIn.value) });
  const descIn = h('input.input', { value: d.description, placeholder: 'What it does, in one line', oninput: () => (d.description = descIn.value) });
  const iconBtn = h('button.st-icon-pick', { type: 'button', title: 'Icon', 'data-pop-anchor': '', onclick: (e) => popover(e.currentTarget, [{ node: h('div.st-icon-grid', PLUGIN_ICONS.map((n) => h('button.icon-btn' + (n === d.icon ? '.on' : ''), { type: 'button', title: n, onclick: () => { d.icon = n; iconBtn.replaceChildren(icon(n)); closePop(); } }, icon(n)))) }]) }, icon(d.icon));
  const js = h('textarea.input.mono.st-code.tall', { rows: 18, spellcheck: false, placeholder: '// runs with `scorp` (await works), e.g.\nscorp.tray({ icon: \'spark\', title: \'Hi\', onClick: () => scorp.say(\'Hello!\') });' }, d.js);
  const css = h('textarea.input.mono.st-code', { rows: 6, spellcheck: false, placeholder: '/* CSS for the whole app */' }, d.css);
  const prompt = h('textarea.input.st-code.prose', { rows: 4, placeholder: 'e.g. Always end answers with one practical next step. Speak like {{character}}.' }, d.prompt);
  const opts = h('textarea.input.mono.st-code', { rows: 6, spellcheck: false, placeholder: OPTIONS_HINT }, d.options);
  const optsNote = h('div.st-hint');
  const checkOpts = () => {
    if (!d.options.trim()) return (optsNote.textContent = 'Settings the user can change; your code reads scorp.options.key.');
    try {
      const list = JSON.parse(d.options);
      if (!Array.isArray(list)) throw new Error('a list [ … ]');
      optsNote.textContent = `${list.length} setting${list.length === 1 ? '' : 's'} · types: toggle, text, textarea, number, range, select, color`;
      optsNote.classList.remove('bad');
    } catch (err) {
      optsNote.textContent = 'Not valid JSON yet: ' + err.message;
      optsNote.classList.add('bad');
    }
  };
  js.addEventListener('input', () => (d.js = js.value));
  css.addEventListener('input', () => (d.css = css.value));
  prompt.addEventListener('input', () => (d.prompt = prompt.value));
  opts.addEventListener('input', () => { d.options = opts.value; checkOpts(); });
  checkOpts();
  [js, css, opts].forEach(codeKeys);
  const consoleBox = consolePanel(d.id);
  const save = async (run) => {
    let options = [];
    try {
      options = d.options.trim() ? JSON.parse(d.options) : [];
    } catch (err) {
      return toast('Settings are not valid JSON', { type: 'error', body: err.message });
    }
    try {
      const r = await api.post('/api/looks/plugins', { id: d.id || undefined, name: d.name.trim() || 'My plugin', description: d.description, icon: d.icon, css: d.css, js: d.js, prompt: d.prompt, options, enabled: run ? true : d.enabled });
      d.id = r.plugin.id;
      d.enabled = r.plugin.enabled;
      d.options = r.plugin.options.length ? JSON.stringify(r.plugin.options, null, 2) : '';
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
  const ask = askInEditor(() => `Change the plugin "${d.name}" (read it first with manage_looks plugin_code if it is saved; the editor holds: JavaScript ${JSON.stringify(d.js).slice(0, 6000)}; CSS ${JSON.stringify(d.css).slice(0, 2000)}; prompt ${JSON.stringify(d.prompt).slice(0, 1000)}; options ${d.options.slice(0, 1500) || '[]'}). Errors from its console: ${JSON.stringify(pluginLog(d.id).filter((l) => l.level === 'error').slice(-5).map((l) => l.text))}. The change: `, (data) => {
    const p = data.plugins.find((x) => x.id === d.id || x.name === d.name);
    if (p) openPlugin(p);
  });
  const saved = d.id && looks().plugins.find((x) => x.id === d.id);
  return h('div.st-editor',
    h('header.st-ed-head',
      h('button.page-back', { type: 'button', onclick: () => { editing = null; paint(); } }, icon('left'), 'Studio'),
      h('div.st-ed-title', h('div.page-kicker', d.id ? 'Edit plugin' : 'New plugin'), h('h1', d.name || 'Plugin')),
      saved && (saved.options || []).length ? h('button.btn.quiet', { onclick: () => optionsSheet(saved) }, icon('sliders'), 'Settings') : null,
      h('button.btn.quiet', { onclick: () => { editing = null; paint(); } }, 'Close'),
      h('button.btn', { onclick: () => save(false) }, 'Save'),
      h('button.btn.primary', { onclick: () => save(true) }, icon('play'), 'Save & run')),
    h('div.st-ed-body',
      h('div.st-controls',
        h('div.st-names.with-icon', iconBtn, nameIn, descIn),
        h('section.st-sec', h('h3.st-sec-title', 'JavaScript'), js),
        h('section.st-sec', h('h3.st-sec-title', 'Prompt'), h('p.st-hint', 'Added to Scorp’s own instructions while the plugin is on. {{key}} is replaced with a setting.'), prompt),
        h('section.st-sec', h('h3.st-sec-title', 'Settings'), optsNote, opts),
        h('section.st-sec', h('h3.st-sec-title', 'CSS'), css)),
      h('aside.st-ed-side',
        consoleBox.el,
        ask,
        h('div.st-api.panel', h('div.st-api-head', icon('book'), h('b', 'The scorp API'), h('span.muted', 'everything is cleaned up when the plugin turns off')),
          API_REF.map(([group, rows]) => h('details.st-api-group', { open: group === 'Surfaces' || group === 'Hooks' }, h('summary', group),
            h('dl', rows.flatMap(([sig, what]) => [h('dt', h('code', sig)), h('dd', what)]))))))),
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
  libBox.append(...library().flat());
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
  // /studio?plugin=id opens that plugin (a failing plugin's toast links here)
  const want = new URLSearchParams(location.search).get('plugin');
  const openWanted = () => {
    const p = looks().plugins.find((x) => x.id === want);
    if (!p) return false;
    tab = 'plugins';
    history.replaceState({}, '', '/studio');
    openPlugin(p);
    return true;
  };
  if (!want || !openWanted()) {
    paint();
    // on a fresh load the plugins arrive a moment later
    if (want) {
      const once = bus.on('looks', () => {
        once();
        if (state.view === 'studio' && !editing) openWanted();
      });
    }
  }
  const off = bus.on('looks', () => {
    if (!root.isConnected) return off();
    if (!editing && state.view === 'studio') paintLibrary();
  });
}

// styleCss is re-exported for plugins that want the CSS of a style
export { styleCss };
