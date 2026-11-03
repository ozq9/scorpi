// The gallery page: browse what people shared from Scorpi (functions/api/gallery.js), look inside an item and install
// it in the Scorpi on this PC (its Studio opens the item at /studio?gallery=<id> and asks before adding it).

const APP = 'http://localhost:8000';
const KIND = { style: 'style', plugin: 'plugin', skill: 'skill', agent: 'agent' };
const grid = document.querySelector('[data-grid]');
const status = document.querySelector('[data-status]');
const moreBtn = document.querySelector('[data-more]');
const dialog = document.querySelector('[data-dialog]');
const q = document.querySelector('[data-q]');
const state = { kind: '', sort: 'popular', q: '', items: [], more: false, seq: 0 };

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
};
// a shared style's colours come from strangers: nothing that loads from elsewhere reaches the page
const safe = (v) => (typeof v === 'string' && !/url\(|image|@|\\|;/i.test(v) ? v : '');

function preview(it) {
  const box = el('div', 'gal-prev');
  if (it.kind === 'style') {
    const v = (it.preview && it.preview.vars) || {};
    box.classList.add('style');
    box.style.background = safe(v['--bg']) || '#000';
    box.style.color = safe(v['--ink']) || '#ededed';
    const aa = el('b', '', 'Aa');
    if (safe(v['--font-display'] || v['--font-ui'])) aa.style.fontFamily = safe(v['--font-display'] || v['--font-ui']);
    const slip = el('i', 'slip');
    slip.style.background = safe(v['--slip-bg']) || '#161616';
    const line = el('i', 'line');
    const box2 = el('i', 'box');
    box2.style.background = safe(v['--composer-bg']) || '#0a0a0a';
    box2.style.borderColor = safe(v['--glass-edge']) || '#232323';
    const dot = el('i', 'dot');
    dot.style.background = safe(v['--accent']) || '#ededed';
    box2.append(dot);
    box.append(aa, slip, line, box2);
  } else {
    box.append(el('span', 'gal-glyph', { plugin: '{ }', skill: '/', agent: '[>]' }[it.kind] || '·'));
  }
  return box;
}

function card(it) {
  const c = el('button', 'gal-card');
  c.type = 'button';
  const meta = el('div', 'gal-meta');
  meta.append(el('span', 'tag', KIND[it.kind] || it.kind));
  if (it.has_code) meta.append(el('span', 'tag warn', 'runs code'));
  meta.append(el('span', 'gal-installs', `${it.installs} install${it.installs === 1 ? '' : 's'}`));
  c.append(preview(it), el('span', 'gal-name', it.name), el('span', 'gal-desc', it.description || ''), meta);
  if (it.author) c.append(el('span', 'gal-by', 'by ' + it.author));
  c.addEventListener('click', () => open(it.id));
  return c;
}

async function load(more = false) {
  const seq = ++state.seq;
  const qs = new URLSearchParams({ sort: state.sort, offset: more ? state.items.length : 0 });
  if (state.kind) qs.set('kind', state.kind);
  if (state.q) qs.set('q', state.q);
  status.textContent = 'Loading…';
  grid.classList.add('busy');
  try {
    const res = await fetch('/api/gallery?' + qs);
    const out = await res.json();
    if (seq !== state.seq) return;
    if (!res.ok) throw new Error(out.error || 'The server answered ' + res.status + '.');
    state.items = more ? state.items.concat(out.items) : out.items;
    state.more = !!out.more;
    if (!more) grid.replaceChildren();
    grid.append(...out.items.map(card));
    status.textContent = state.items.length ? '' : state.q ? 'Nothing matches. Try other words.' : 'Nothing here yet. Be the first to share something from your Scorpi.';
    moreBtn.hidden = !state.more;
  } catch (err) {
    if (seq === state.seq) status.textContent = err instanceof TypeError ? "Couldn't reach the gallery." : err.message;
  }
  grid.classList.remove('busy');
}

function code(label, text) {
  if (!text) return null;
  const d = el('details', 'gal-code');
  d.append(el('summary', '', label), el('pre', '', String(text)));
  return d;
}

function facts(list) {
  const p = el('p', 'gal-facts');
  for (const f of list.filter(Boolean)) p.append(el('span', '', f));
  return p;
}

async function open(id) {
  let it;
  try {
    const res = await fetch('/api/gallery?id=' + encodeURIComponent(id));
    it = await res.json();
    if (!res.ok) throw new Error(it.error || 'Not found.');
  } catch (err) {
    status.textContent = err.message;
    return;
  }
  const p = it.payload || {};
  dialog.querySelector('[data-d-title]').textContent = it.name;
  const meta = dialog.querySelector('[data-d-meta]');
  meta.replaceChildren(el('span', 'tag', KIND[it.kind] || it.kind), el('span', '', it.author ? 'by ' + it.author : 'anonymous'),
    el('span', '', `${it.installs} install${it.installs === 1 ? '' : 's'}`), el('span', '', new Date(it.created).toLocaleDateString(undefined, { dateStyle: 'medium' })));
  dialog.querySelector('[data-d-desc]').textContent = it.description || '';
  const body = dialog.querySelector('[data-d-body]');
  const parts = {
    style: () => [preview(it), facts([`${Object.keys(p.vars || {}).length} design tokens`, p.css && 'custom CSS', (p.fonts || []).length && 'fonts: ' + p.fonts.join(', ')]), code('CSS', p.css)],
    plugin: () => [facts([p.js && 'runs JavaScript (added turned off)', p.css && 'CSS', p.prompt && "adds to Scorpi's instructions", (p.options || []).length && `${p.options.length} settings`]),
      code('JavaScript', p.js), code('Prompt', p.prompt), code('CSS', p.css)],
    skill: () => [facts([(p.groups || []).length ? 'tools: ' + p.groups.join(', ') : 'no extra tools']), code('Instructions', p.instructions)],
    agent: () => [facts([p.action === 'remind' ? 'sends a reminder' : 'does a task', 'added paused']), code('Instructions', p.instructions), code('Task', p.task)],
  }[it.kind];
  body.replaceChildren(...(parts ? parts() : []).filter(Boolean));
  dialog.querySelector('[data-install]').href = APP + '/studio?gallery=' + encodeURIComponent(it.id);
  const file = dialog.querySelector('[data-file]');
  file.hidden = !(it.kind === 'style' || it.kind === 'plugin');
  if (!file.hidden) {
    URL.revokeObjectURL(file.href);
    file.href = URL.createObjectURL(new Blob([JSON.stringify(p, null, 2)], { type: 'application/json' }));
    file.download = it.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + '.scorpi-' + it.kind + '.json';
  }
  dialog.querySelector('[data-report]').onclick = async () => {
    const reason = prompt('What is wrong with “' + it.name + '”?');
    if (reason === null) return;
    await fetch('/api/gallery?id=' + encodeURIComponent(it.id) + '&action=report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason }) });
    alert('Thanks. It will be looked at.');
  };
  history.replaceState(null, '', '#' + it.id);
  if (!dialog.open) dialog.showModal();
}

function tabs(sel, key) {
  document.querySelectorAll(sel + ' button').forEach((b) => b.addEventListener('click', () => {
    state[key] = b.dataset[key];
    document.querySelectorAll(sel + ' button').forEach((x) => x.setAttribute('aria-selected', x === b));
    load();
  }));
}

tabs('[data-kinds]', 'kind');
tabs('[data-sorts]', 'sort');
let typing = 0;
q.addEventListener('input', () => {
  clearTimeout(typing);
  typing = setTimeout(() => { state.q = q.value.trim(); load(); }, 300);
});
moreBtn.addEventListener('click', () => load(true));
dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
dialog.addEventListener('close', () => history.replaceState(null, '', location.pathname));
load();
if (/^#[a-z0-9]{4,24}$/.test(location.hash)) open(location.hash.slice(1));
