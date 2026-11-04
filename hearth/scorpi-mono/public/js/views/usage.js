// Usage: what Scorpi used (tokens and cost per model) and how you use it (activity over the year, the hours you
// work, streaks, sessions). Everything is counted on this PC (scorpi/usage.py); nothing is sent anywhere.

import { api } from '../lib/api.js';
import { clear, h, icon } from '../lib/dom.js';
import { field, seg, sheet, toast } from '../lib/ui.js';
import { bus, state } from '../store.js';
import { openRecap } from './recap.js';
import { scoreboardSection } from './scoreboard.js';

const RANGES = [['7', '7 days'], ['30', '30 days'], ['90', '90 days'], ['0', 'All time']];
const KINDS = { local: 'local', free: 'free tier', plan: 'plan', paid: 'pay as you go', custom: 'own endpoint' };
const SOURCES = [['chat', 'Chat'], ['agents', 'Agents'], ['code', 'Code projects'], ['routines', 'Routines'], ['other', 'Titles & helpers']];
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
// books by their length in tokens (about 1.33 tokens a word), for a sense of scale
const BOOKS = [['The Great Gatsby', 64e3], ['Pride and Prejudice', 160e3], ['Moby-Dick', 280e3], ['War and Peace', 780e3], ['the complete works of Shakespeare', 1.18e6]];

let root;
let range = '30';
let tab = 'overview';
let data = null;
let tip = null;

// ---------- formatting ----------

export function fmtTokens(n) {
  n = Number(n) || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1).replace(/\.0$/, '') + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'K';
  return String(Math.round(n));
}

export function fmtCost(c) {
  c = Number(c) || 0;
  if (!c) return '$0';
  if (c < 0.01) return '<$0.01';
  return '$' + (c >= 100 ? c.toFixed(0) : c.toFixed(2));
}

export function fmtDuration(s) {
  s = Math.round(Number(s) || 0);
  if (s < 60) return s ? s + ' s' : '—';
  const m = Math.round(s / 60);
  if (m < 60) return m + ' min';
  return Math.floor(m / 60) + ' h ' + String(m % 60).padStart(2, '0') + ' min';
}

const hourLabel = (hr) => String(hr).padStart(2, '0') + ':00';
const dayName = (key, opts = { weekday: 'short', day: 'numeric', month: 'short' }) => new Date(key + 'T12:00:00').toLocaleDateString(undefined, opts);
const localKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

export function bookFact(tokens) {
  if (!tokens) return '';
  const fits = BOOKS.filter(([, n]) => tokens >= n);
  if (!fits.length) return `about ${Math.max(1, Math.round((tokens / BOOKS[0][1]) * 100))}% of ${BOOKS[0][0]}`;
  const [name, n] = fits[fits.length - 1];
  const times = tokens / n;
  return `about ${times >= 10 ? Math.round(times) : times.toFixed(1).replace(/\.0$/, '')}× ${name}`;
}

// ---------- tooltip (one for the page; marks set it on hover and focus) ----------

function showTip(e, value, label) {
  if (!tip) return;
  clear(tip);
  tip.append(h('b', value), h('span', label));
  tip.classList.add('on');
  const r = (e.currentTarget || e.target).getBoundingClientRect();
  const w = tip.offsetWidth;
  tip.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + 'px';
  tip.style.top = Math.max(8, r.top - tip.offsetHeight - 8) + 'px';
}
const hideTip = () => tip && tip.classList.remove('on');
const hover = (value, label) => ({
  tabindex: 0,
  onpointerenter: (e) => showTip(e, value, label),
  onfocus: (e) => showTip(e, value, label),
  onpointerleave: hideTip,
  onblur: hideTip,
  'aria-label': value + ', ' + label,
});

// ---------- charts ----------

/** The year as a grid of days (weeks are columns, Monday on top), shaded by how many prompts you wrote. */
function heatmap(heat) {
  const counts = new Map(heat);
  const values = [...counts.values()].filter(Boolean).sort((a, b) => a - b);
  const q = (p) => values[Math.min(values.length - 1, Math.floor(p * values.length))] || 1;
  const steps = [q(0.25), q(0.5), q(0.75)];
  const level = (n) => (!n ? 0 : n <= steps[0] ? 1 : n <= steps[1] ? 2 : n <= steps[2] ? 3 : 4);
  const today = new Date();
  today.setHours(12, 0, 0, 0);
  const start = new Date(today);
  start.setDate(start.getDate() - 52 * 7 - ((today.getDay() + 6) % 7)); // a Monday, 53 columns back
  const cells = [];
  const months = [];
  let lastMonth = -1;
  for (let d = new Date(start), i = 0; d <= today; d.setDate(d.getDate() + 1), i++) {
    const key = localKey(d);
    const n = counts.get(key) || 0;
    const col = Math.floor(i / 7);
    if (d.getDate() <= 7 && d.getMonth() !== lastMonth && (d.getDay() + 6) % 7 === 0) {
      months.push(h('span', { style: { gridColumn: col + 1 + ' / span 4' } }, d.toLocaleDateString(undefined, { month: 'short' })));
      lastMonth = d.getMonth();
    }
    cells.push(h('i.u-cell.l' + level(n), hover(n ? `${n} prompt${n === 1 ? '' : 's'}` : 'No prompts', dayName(key, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }))));
  }
  const cols = Math.ceil(cells.length / 7);
  const scroller = h('div.u-heat-scroll',
    h('div.u-heat',
      h('div.u-heat-months', { style: { gridTemplateColumns: `repeat(${cols}, var(--cell))` } }, months),
      h('div.u-heat-days', ['Mon', '', 'Wed', '', 'Fri', '', ''].map((d) => h('span', d))),
      h('div.u-heat-grid', cells)));
  requestAnimationFrame(() => (scroller.scrollLeft = scroller.scrollWidth)); // the latest weeks first on narrow screens
  return h('div.u-heat-wrap', scroller,
    h('div.u-legend', h('span', 'Less'), [0, 1, 2, 3, 4].map((l) => h('i.u-cell.l' + l)), h('span', 'More')));
}

/** Columns along an axis: one series, capped bar width, the highest one labelled. */
function columns(items, { height = 132, labelEvery = 1, axis = true } = {}) {
  const max = Math.max(1, ...items.map((it) => it.value));
  const top = items.reduce((a, b) => (b.value > a.value ? b : a), items[0] || { value: 0 });
  // the peak carries its own label, so one gridline at half of it is enough scale
  const ticks = axis ? [h('div.u-grid', { style: { bottom: '50%' } }, h('span', fmtTokens(max / 2)))] : [];
  return h('div.u-cols' + (axis ? '.with-axis' : ''),
    h('div.u-cols-plot', { style: { height: height + 'px' } }, ticks,
      items.map((it) => h('div.u-col' + (it === top && it.value ? '.peak' : ''), hover(it.tip, it.label),
        h('i', { style: { height: (it.value / max) * 100 + '%' } }, it === top && it.value ? h('em', it.short || fmtTokens(it.value)) : null)))),
    h('div.u-cols-x', items.map((it, i) => h('span', i % labelEvery === 0 ? it.x : ''))));
}

/** Ranked horizontal bars with the value at the tip. */
function bars(items, fmt = fmtTokens) {
  const max = Math.max(1, ...items.map((it) => it.value));
  return h('div.u-bars', items.map((it) => h('div.u-bar', hover(it.tip || fmt(it.value), it.label),
    h('span.u-bar-label', it.label),
    h('span.u-bar-track', h('i', { style: { width: Math.max(it.value ? 1.5 : 0, (it.value / max) * 100) + '%' } })),
    h('span.u-bar-val', fmt(it.value)))));
}

const tile = (label, value, sub) => h('div.u-tile', h('div.u-tile-label', label), h('div.u-tile-value', value), sub ? h('div.u-tile-sub', sub) : null);

// ---------- overview ----------

function overview(d) {
  const t = d.totals;
  const peak = d.hours.indexOf(Math.max(...d.hours));
  const weekTotals = d.week.map((row) => row.reduce((a, b) => a + b, 0));
  const fav = d.favorite;
  const hero = h('section.u-hero',
    h('div.u-hero-num', fmtTokens(t.tokens), h('span', ' tokens')),
    h('div.u-hero-side',
      h('div', t.tokens ? `> ${bookFact(t.tokens)}` : '> Tokens are counted from the first answer after this update.'),
      h('div.muted', t.cost ? `${fmtCost(t.cost)} on pay-as-you-go keys` : t.calls ? 'Nothing spent: local models, free tiers and plans' : ''),
      t.estimated ? h('div.faint', `~ ${Math.round((t.estimated / t.calls) * 100)}% estimated from the text (the provider did not report tokens)`) : null));
  const tiles = h('section.u-tiles',
    tile('Favorite model', fav ? fav.label.replace(/^.* · /, '') : '—', fav ? (fav.tokens ? `${fmtTokens(fav.tokens)} tokens` : `${fav.answers} answers`) + (fav.label.includes(' · ') ? ' · ' + fav.label.split(' · ')[0] : '') : 'no answers yet'),
    tile('Prompts', d.prompts.toLocaleString(), `in ${d.conversations} conversation${d.conversations === 1 ? '' : 's'}`),
    tile('Sessions', d.sessions.toLocaleString(), 'longest ' + fmtDuration(d.longest_session)),
    tile('Active days', d.active_days.toLocaleString(), d.range ? `of the last ${d.range}` : d.first_chat ? 'since ' + new Date(d.first_chat * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : ''),
    tile('Current streak', d.streak.current + (d.streak.current === 1 ? ' day' : ' days'), 'longest ' + d.streak.longest + (d.streak.longest === 1 ? ' day' : ' days')),
    tile('Peak hour', d.prompts ? hourLabel(peak) : '—', d.prompts ? `${d.hours[peak]} prompts in that hour` : ''),
    tile('Busiest day', d.busiest_day ? dayName(d.busiest_day.day, { day: 'numeric', month: 'short' }) : '—', d.busiest_day ? `${d.busiest_day.prompts} prompts` : ''),
    tile('Longest chat', d.longest_chat ? d.longest_chat.n + ' messages' : '—', d.longest_chat ? h('a', { href: '/c/' + d.longest_chat.id, 'data-link': '' }, d.longest_chat.title || 'Untitled') : ''),
  );
  return [
    hero,
    tiles,
    h('section.u-section', h('div.u-section-head', h('h2', 'Activity'), h('span.muted', 'Prompts per day, past year')), heatmap(d.heat)),
    h('div.u-two',
      h('section.u-section', h('div.u-section-head', h('h2', 'When you work'), h('span.muted', 'Prompts by hour of the day')),
        columns(d.hours.map((n, hr) => ({ value: n, x: String(hr).padStart(2, '0'), label: `${hourLabel(hr)}–${hourLabel((hr + 1) % 24)}`, tip: `${n} prompt${n === 1 ? '' : 's'}`, short: hourLabel(hr) })), { labelEvery: 3, axis: false, height: 120 })),
      h('section.u-section', h('div.u-section-head', h('h2', 'By weekday'), h('span.muted', 'Prompts')),
        bars(weekTotals.map((n, i) => ({ value: n, label: DAYS[i], tip: `${n} prompt${n === 1 ? '' : 's'}` })), (n) => n.toLocaleString()))),
  ];
}

// ---------- models ----------

function priceSheet(m) {
  const pin = h('input.input', { type: 'number', min: 0, step: 0.01, value: m.price ? m.price.input : '' });
  const pout = h('input.input', { type: 'number', min: 0, step: 0.01, value: m.price ? m.price.output : '' });
  sheet({
    title: 'Price of ' + m.label,
    body: h('div', h('p.muted', { style: { marginTop: 0 } }, 'US$ per million tokens, as on the provider\'s price page. Everything counted for this model is priced again.'),
      h('div.row', h('div.grow', field('Input', pin)), h('div.grow', field('Output', pout)))),
    foot: (close) => [
      m.price && m.price.own ? h('button.btn.quiet', { onclick: async () => { await api.post('/api/usage/price', { model: m.model, reset: true }); close(); load(); } }, 'Use the estimate') : null,
      h('span.grow'),
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h('button.btn.primary', { onclick: async () => {
        try {
          await api.post('/api/usage/price', { model: m.model, input: pin.value || 0, output: pout.value || 0 });
          close();
          load();
        } catch (err) {
          toast(err.message, 'error');
        }
      } }, 'Save'),
    ],
  });
}

function models(d) {
  const t = d.totals;
  const days = d.range || Math.min(90, Math.max(7, Math.ceil((Date.now() / 1000 - (d.counting_since || Date.now() / 1000)) / 86400) + 1));
  const byDay = new Map(d.daily.map((x) => [x.day, x]));
  const series = [];
  for (let i = days - 1; i >= 0; i--) {
    const day = new Date();
    day.setDate(day.getDate() - i);
    const key = localKey(day);
    const x = byDay.get(key) || { tokens: 0, cost: 0 };
    series.push({ value: x.tokens, x: day.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }), label: dayName(key), tip: fmtTokens(x.tokens) + ' tokens' + (x.cost ? ' · ' + fmtCost(x.cost) : '') });
  }
  const share = (m) => (t.tokens ? Math.round((m.tokens / t.tokens) * 100) : 0);
  const priceCell = (m) => {
    if (m.kind === 'local' || m.kind === 'free') return h('span.faint', 'free');
    if (m.kind === 'plan') return h('span.faint', 'in your plan');
    return h('button.u-price', { type: 'button', title: 'Set the real price', onclick: () => priceSheet(m) },
      m.price ? `$${m.price.input} / $${m.price.output}` : 'set price', m.price && !m.price.own ? h('span.faint', ' est.') : null, icon('edit'));
  };
  const table = d.models.length
    ? h('div.u-table-wrap', h('table.u-table',
        h('thead', h('tr', ['Model', 'Calls', 'Input', 'Output', 'Cached', 'Price / 1M', 'Cost', 'Share'].map((c) => h('th', c)))),
        h('tbody', d.models.map((m) => h('tr',
          h('td', h('div.u-model', m.label), h('span.badge', KINDS[m.kind] || m.kind)),
          h('td.num', m.calls.toLocaleString()),
          h('td.num', (m.estimated ? '~' : '') + fmtTokens(m.input)),
          h('td.num', (m.estimated ? '~' : '') + fmtTokens(m.output)),
          h('td.num', fmtTokens(m.cached)),
          h('td', priceCell(m)),
          h('td.num', m.kind === 'paid' || m.kind === 'custom' ? fmtCost(m.cost) : '—'),
          h('td', h('div.u-share', h('i', { style: { width: share(m) + '%' } }), h('span', share(m) + '%'))))))))
    : h('div.u-empty', 'No tokens counted in this period yet.', d.answers.length ? ' Earlier answers by model:' : '');
  return [
    h('section.u-tiles.three',
      tile('Tokens', fmtTokens(t.tokens), `${fmtTokens(t.input)} in · ${fmtTokens(t.output)} out · ${fmtTokens(t.cached)} cached`),
      tile('Spent', fmtCost(t.cost), t.cost ? 'estimated from prices' : 'local, free tiers and plans cost nothing extra'),
      tile('Model calls', t.calls.toLocaleString(), t.calls ? `${fmtTokens(t.tokens / t.calls)} tokens each on average` : '')),
    scoreboardSection(),
    h('section.u-section', h('div.u-section-head', h('h2', 'Tokens per day'), h('span.muted', d.range ? `Last ${d.range} days` : `Last ${days} days`)),
      columns(series, { labelEvery: Math.ceil(series.length / 8), height: 150 })),
    h('section.u-section', h('div.u-section-head', h('h2', 'Models'), h('span.muted', 'Prices are estimates; click one to set the real price')), table,
      !d.models.length && d.answers.length ? bars(d.answers.map((a) => ({ value: a.answers, label: a.label, tip: a.answers + ' answers' })), (n) => n + ' answers') : null),
    h('section.u-section', h('div.u-section-head', h('h2', 'Where the tokens go')),
      bars(SOURCES.map(([k, label]) => ({ value: d.sources[k] || 0, label })))),
    limitsSection(d.limits),
  ];
}

// Free tiers' limits: what is resting until its reset, and requests left where providers report it (scorpi/limits.py).
function limitsSection(lim) {
  if (!lim || (!lim.resting.length && !lim.left.length)) return null;
  const tryNow = (key) => api.post('/api/limits/clear', { key }).then(load).catch((e) => toast(e.message, 'error'));
  return h('section.u-section', h('div.u-section-head', h('h2', 'Free limits'), h('span.muted', 'Scorpi works around models at their limit and continues when they reset')),
    h('div.u-limits',
      lim.resting.map((r) => h('div.u-limit.resting', icon('clock'), h('div.grow', h('div.u-model', r.label), h('div.faint', `${r.why} · back at ${r.at}`)),
        h('button.btn.small.quiet', { type: 'button', onclick: () => tryNow(r.key) }, 'Try now'))),
      lim.left.slice(0, 8).map((x) => h('div.u-limit', icon('bolt'), h('div.grow', h('div.u-model', x.label),
        h('div.faint', x.limit ? `${x.remaining.toLocaleString()} of ${x.limit.toLocaleString()} requests left` : `${x.remaining.toLocaleString()} requests left`)),
        x.limit ? h('div.u-share', h('i', { style: { width: Math.round((x.remaining / x.limit) * 100) + '%' } })) : null))));
}

// ---------- page ----------

function paint() {
  if (!root || !root.isConnected) return;
  clear(root);
  tip = h('div.u-tip', { role: 'tooltip' });
  const body = h('div.u-body' + (data ? '' : '.loading'));
  root.appendChild(h('div.page.usage', { onscroll: hideTip }, h('div.page-inner',
    h('div.page-head',
      h('div.ph-text', h('div.page-kicker', 'Usage'), h('h1.page-title', 'How you use ', h('em', 'Scorpi')),
        h('p.page-sub', 'Tokens, cost and the hours you work. Counted on this PC, never sent anywhere.')),
      h('button.btn', { type: 'button', onclick: () => data && openRecap(data), disabled: !data || !data.prompts }, icon('play'), 'Recap')),
    h('div.u-filters',
      seg(RANGES, range, (v) => { range = v; load(); }),
      h('span.grow'),
      seg([['overview', 'Overview'], ['models', 'Models']], tab, (v) => { tab = v; paint(); })),
    body,
    data ? h('p.u-foot.faint', data.counting_since
      ? `Tokens counted since ${new Date(data.counting_since * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })}. Activity comes from your chats and goes back further.`
      : 'Tokens are counted from the next answer on. Activity comes from your chats.') : null,
  ), tip));
  if (data) body.append(...(tab === 'models' ? models(data) : overview(data)).flat());
}

async function load() {
  const want = range;
  const box = root && root.querySelector('.u-body');
  if (box) box.classList.add('loading'); // the old numbers stay, dimmed, until the new ones arrive
  try {
    const d = await api.get('/api/usage?days=' + want);
    if (want !== range) return;
    data = d;
    paint();
  } catch (err) {
    toast('Could not load usage', { type: 'error', body: err.message });
  }
}

let refreshTimer = 0;
bus.on('server:usage', () => {
  if (state.view !== 'usage') return;
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(load, 1500); // a busy agent answers many times a minute
});

export function renderUsage(el) {
  root = el;
  paint();
  load();
}
