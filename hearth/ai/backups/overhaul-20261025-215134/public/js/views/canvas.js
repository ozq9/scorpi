// The live canvas: a visual board beside the chat. The assistant builds it with the canvas tool and keeps changing it
// as the conversation goes on; blocks glide in, light up when they change and fade out when they go.

import { fileUrl } from '../lib/api.js';
import { copyText, h, icon, isMobile } from '../lib/dom.js';
import { highlight, renderMarkdown } from '../lib/md.js';
import { openViewer, renderMermaid } from '../lib/viewer.js';
import { toast } from '../lib/ui.js';
import { prefs } from '../store.js';
import { renderCard } from './cards.js';

const PALETTE = ['#FFFFFF', '#9FD4FF', '#C4B5FD', '#8FF0C8', '#FFC978', '#FF9FB2'];

const cv = { title: '', blocks: [], dismissed: false };
const els = {};

// ---------- helpers ----------

const str = (v) => (v === undefined || v === null ? '' : String(v));
const arr = (v) => (Array.isArray(v) ? v : v === undefined || v === null || v === '' ? [] : [v]);
const num = (v) => {
  const n = typeof v === 'number' ? v : parseFloat(String(v).replace(/[^0-9.\-eE]/g, ''));
  return Number.isFinite(n) ? n : 0;
};
const src = (s) => {
  s = str(s).trim();
  if (!s) return '';
  if (/^(https?:|data:image\/|blob:|\/api\/)/i.test(s)) return s;
  return fileUrl(s.replace(/^file:\/\//, ''));
};
const safeUrl = (u) => (/^https?:\/\//i.test(str(u)) ? str(u) : '');
const svgEl = (tag, attrs = {}, ...kids) => {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, v);
  kids.flat().forEach((k) => k && el.appendChild(typeof k === 'string' ? document.createTextNode(k) : k));
  return el;
};
const fmt = (n) => (Math.abs(n) >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M' : Math.abs(n) >= 1e4 ? (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k' : String(Math.round(n * 100) / 100));

function deltaEl(d) {
  if (d === undefined || d === null || d === '') return null;
  const s = str(d);
  const dir = /^\s*[-−↓▼]/.test(s) ? 'down' : /^\s*[+↑▲]/.test(s) ? 'up' : '';
  return h('span.cv-delta' + (dir ? '.' + dir : ''), s);
}

function md(text) {
  const box = h('div.md.cv-md', { html: renderMarkdown(str(text)) });
  queueMicrotask(() => highlight(box));
  return box;
}

function smooth(pts) {
  if (pts.length < 2) return '';
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p2[0]},${p2[1]}`;
  }
  return d;
}

function spark(values, color = '#fff') {
  const vs = arr(values).map(num);
  if (vs.length < 2) return null;
  const W = 120;
  const H = 34;
  const min = Math.min(...vs);
  const max = Math.max(...vs);
  const pts = vs.map((v, i) => [(i / (vs.length - 1)) * W, H - 3 - ((v - min) / (max - min || 1)) * (H - 6)]);
  const line = smooth(pts);
  return svgEl('svg', { class: 'cv-spark', viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none' },
    svgEl('path', { d: `${line} L${W},${H} L0,${H} Z`, fill: 'rgba(255,255,255,.07)', stroke: 'none' }),
    svgEl('path', { d: line, fill: 'none', stroke: color, 'stroke-width': 1.6, 'vector-effect': 'non-scaling-stroke', class: 'cv-draw' }));
}

// ---------- charts ----------

function seriesOf(b) {
  if (Array.isArray(b.series) && b.series.length) return b.series.map((s, i) => ({ name: str(s.name), values: arr(s.values ?? s.data).map(num), color: s.color || PALETTE[i % PALETTE.length] }));
  if (b.values) return [{ name: '', values: arr(b.values).map(num), color: PALETTE[0] }];
  if (Array.isArray(b.data)) return [{ name: '', values: b.data.map((d) => num(typeof d === 'object' ? d.value : d)), color: PALETTE[0] }];
  return [];
}

function legend(items) {
  if (items.length < 2) return null;
  return h('div.cv-legend', items.map((s) => h('span', h('i', { style: { background: s.color } }), s.name)));
}

function pie(b, series) {
  const labels = arr(b.labels).length ? arr(b.labels) : (Array.isArray(b.data) ? b.data.map((d) => d.label) : []);
  const vals = series[0] ? series[0].values : [];
  const total = vals.reduce((a, v) => a + Math.max(0, v), 0) || 1;
  const R = 80;
  const r = b.kind === 'donut' ? 50 : 0;
  const svg = svgEl('svg', { class: 'cv-pie', viewBox: '-100 -100 200 200' });
  let a0 = -Math.PI / 2;
  vals.forEach((v, i) => {
    const a1 = a0 + (Math.max(0, v) / total) * Math.PI * 2;
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (rad, ang) => [Math.cos(ang) * rad, Math.sin(ang) * rad];
    const [x0, y0] = p(R, a0);
    const [x1, y1] = p(R, a1);
    const d = r
      ? (() => { const [x2, y2] = p(r, a1); const [x3, y3] = p(r, a0); return `M${x0},${y0} A${R},${R} 0 ${large} 1 ${x1},${y1} L${x2},${y2} A${r},${r} 0 ${large} 0 ${x3},${y3} Z`; })()
      : `M0,0 L${x0},${y0} A${R},${R} 0 ${large} 1 ${x1},${y1} Z`;
    const color = PALETTE[i % PALETTE.length];
    svg.appendChild(svgEl('path', { d, fill: color, 'fill-opacity': 0.82 - (i % 3) * 0.12, stroke: '#000', 'stroke-width': 1.2, class: 'cv-slice', style: `--i:${i}` }, svgEl('title', {}, `${str(labels[i])}: ${fmt(v)}`)));
    a0 = a1;
  });
  if (r && b.center !== undefined) svg.appendChild(svgEl('text', { x: 0, y: 7, 'text-anchor': 'middle', class: 'cv-pie-center' }, str(b.center)));
  const leg = h('div.cv-legend.col', vals.map((v, i) => h('span', h('i', { style: { background: PALETTE[i % PALETTE.length] } }), str(labels[i]), h('b', fmt(v) + (b.unit ? ' ' + b.unit : '')))));
  return h('div.cv-pie-wrap', svg, leg);
}

function chart(b) {
  const kind = (b.kind || b.chart || 'bar').toLowerCase();
  const series = seriesOf(b);
  if (!series.length) return h('div.muted', 'No data.');
  if (kind === 'pie' || kind === 'donut') return pie({ ...b, kind }, series);
  const labels = arr(b.labels).length ? arr(b.labels) : (Array.isArray(b.data) ? b.data.map((d) => d.label) : series[0].values.map((_, i) => String(i + 1)));
  const n = Math.max(...series.map((s) => s.values.length), labels.length);
  const all = series.flatMap((s) => s.values);
  const max = Math.max(0, ...all);
  const min = Math.min(0, ...all);
  const W = 640;
  const H = 260;
  const L = 40;
  const B = 28;
  const T = 12;
  const iw = W - L - 8;
  const ih = H - B - T;
  const y = (v) => T + ih - ((v - min) / (max - min || 1)) * ih;
  const svg = svgEl('svg', { class: 'cv-chart', viewBox: `0 0 ${W} ${H}` });
  for (let i = 0; i <= 4; i++) {
    const v = min + ((max - min) * i) / 4;
    svg.appendChild(svgEl('line', { x1: L, x2: W - 8, y1: y(v), y2: y(v), class: 'cv-grid' }));
    svg.appendChild(svgEl('text', { x: L - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'cv-axis' }, fmt(v)));
  }
  const step = iw / Math.max(1, n);
  const every = Math.ceil(n / 10);
  labels.forEach((lab, i) => {
    if (i % every) return;
    const x = kind === 'bar' ? L + step * i + step / 2 : L + (iw * i) / Math.max(1, n - 1);
    svg.appendChild(svgEl('text', { x, y: H - 8, 'text-anchor': 'middle', class: 'cv-axis' }, (() => { const t = str(lab); const max = Math.max(6, Math.floor(step / 7)); return t.length > max ? t.slice(0, max - 1) + '…' : t; })()));
  });
  if (kind === 'bar') {
    const gw = step * 0.7;
    const bw = gw / series.length;
    series.forEach((s, si) => s.values.forEach((v, i) => {
      const x = L + step * i + (step - gw) / 2 + bw * si;
      const top = y(Math.max(v, 0));
      const hgt = Math.abs(y(v) - y(0));
      svg.appendChild(svgEl('rect', { x: x + 1, y: top, width: Math.max(1, bw - 2), height: Math.max(1, hgt), rx: Math.min(6, bw / 3), fill: s.color, 'fill-opacity': 0.85, class: 'cv-bar', style: `--i:${i + si}` }, svgEl('title', {}, `${str(labels[i])}${s.name ? ' · ' + s.name : ''}: ${fmt(v)}`)));
      if (n * series.length <= 12) svg.appendChild(svgEl('text', { x: x + bw / 2, y: top - 6, 'text-anchor': 'middle', class: 'cv-val' }, fmt(v)));
    }));
  } else {
    series.forEach((s, si) => {
      const pts = s.values.map((v, i) => [L + (iw * i) / Math.max(1, n - 1), y(v)]);
      const line = smooth(pts);
      if (kind === 'area' || series.length === 1) {
        const gid = 'g' + Math.random().toString(36).slice(2);
        svg.appendChild(svgEl('defs', {}, svgEl('linearGradient', { id: gid, x1: 0, x2: 0, y1: 0, y2: 1 }, svgEl('stop', { offset: 0, 'stop-color': s.color, 'stop-opacity': kind === 'area' ? 0.35 : 0.16 }), svgEl('stop', { offset: 1, 'stop-color': s.color, 'stop-opacity': 0 }))));
        svg.appendChild(svgEl('path', { d: `${line} L${pts[pts.length - 1][0]},${y(min)} L${pts[0][0]},${y(min)} Z`, fill: `url(#${gid})`, class: 'cv-area' }));
      }
      svg.appendChild(svgEl('path', { d: line, fill: 'none', stroke: s.color, 'stroke-width': 2.4, 'stroke-linecap': 'round', class: 'cv-draw', style: `--i:${si}` }));
      if (pts.length <= 16) pts.forEach((p, i) => svg.appendChild(svgEl('circle', { cx: p[0], cy: p[1], r: 3.4, fill: '#000', stroke: s.color, 'stroke-width': 2, class: 'cv-dot', style: `--i:${i}` }, svgEl('title', {}, `${str(labels[i])}: ${fmt(s.values[i])}`))));
    });
  }
  return h('div', svg, legend(series));
}

// ---------- blocks ----------

const R = {
  hero: (b) => h('div.cv-hero',
    h('div.cv-hero-main', b.kicker ? h('div.cv-kicker', b.kicker) : null, h('div.cv-hero-title', str(b.title || b.text)), b.title && b.text ? h('div.cv-hero-text', b.text) : null),
    b.value !== undefined ? h('div.cv-hero-value', str(b.value)) : null),

  stat: (b) => h('div.cv-stat',
    h('div.cv-stat-label', str(b.label || '')),
    h('div.cv-stat-row', h('span.cv-stat-value', str(b.value)), b.unit ? h('span.cv-unit', b.unit) : null, deltaEl(b.delta)),
    spark(b.trend)),

  stats: (b) => h('div.cv-stats', arr(b.items).map((it, i) => h('div.cv-stat', { style: `--i:${i}` },
    h('div.cv-stat-label', str(it.label)),
    h('div.cv-stat-row', h('span.cv-stat-value', str(it.value)), it.unit ? h('span.cv-unit', it.unit) : null, deltaEl(it.delta)),
    spark(it.trend)))),

  text: (b) => md(b.text || b.content || ''),

  list: (b) => h((b.ordered ? 'ol' : 'ul') + '.cv-list', arr(b.items).map((it, i) => {
    const o = typeof it === 'object' && it ? it : { title: it };
    return h('li' + (o.done ? '.done' : '') + (o.done !== undefined ? '.check' : ''), { style: `--i:${i}` },
      h('span.cv-li-mark', o.done !== undefined ? (o.done ? icon('check') : null) : b.ordered ? String(i + 1) : null),
      h('div', h('div.cv-li-title', str(o.title ?? o.text)), o.sub || o.detail ? h('div.cv-li-sub', str(o.sub || o.detail)) : null));
  })),

  steps: (b) => h('div.cv-steps', arr(b.items).map((it, i) => {
    const o = typeof it === 'object' && it ? it : { title: it };
    const st = ['done', 'active', 'todo'].includes(o.status) ? o.status : 'todo';
    return h('div.cv-step.' + st, { style: `--i:${i}` }, h('span.cv-step-dot', st === 'done' ? icon('check') : String(i + 1)), h('div', h('div.cv-li-title', str(o.title)), o.detail ? h('div.cv-li-sub', str(o.detail)) : null));
  })),

  timeline: (b) => h('div.cv-timeline', arr(b.items).map((it, i) => h('div.cv-tl', { style: `--i:${i}` },
    h('div.cv-tl-time', str(it.time ?? it.date)), h('span.cv-tl-dot'),
    h('div', h('div.cv-li-title', str(it.title)), it.detail ? h('div.cv-li-sub', str(it.detail)) : null)))),

  table: (b) => {
    const cols = arr(b.columns);
    const rows = arr(b.rows);
    return h('div.cv-table-wrap', h('table.cv-table',
      cols.length ? h('thead', h('tr', cols.map((c) => h('th', str(c))))) : null,
      h('tbody', rows.map((r, i) => h('tr', { style: `--i:${i}` }, (Array.isArray(r) ? r : cols.map((c) => r[c])).map((c) => h('td', str(c))))))));
  },

  compare: (b) => h('div.cv-compare', arr(b.items).map((it, i) => h('div.cv-opt' + (it.best ? '.best' : ''), { style: `--i:${i}` },
    h('div.cv-opt-head', h('span.cv-opt-name', str(it.name ?? it.title)), it.best ? h('span.cv-badge', 'Best') : it.tag ? h('span.cv-tag', str(it.tag)) : null),
    it.best && it.tag ? h('div.cv-li-sub', str(it.tag)) : null,
    it.score !== undefined ? h('div.cv-score', h('div.cv-meter', h('i', { style: { width: Math.max(0, Math.min(100, num(it.score) * 10)) + '%' } })), h('b', str(it.score))) : null,
    h('ul.cv-points', arr(it.points ?? it.pros).map((p) => h('li', str(p)))),
    arr(it.cons).length ? h('ul.cv-points.cons', arr(it.cons).map((p) => h('li', str(p)))) : null))),

  chart,

  progress: (b) => h('div.cv-progress', arr(b.items).map((it, i) => {
    const max = num(it.max) || 100;
    const pct = Math.max(0, Math.min(100, (num(it.value) / max) * 100));
    return h('div.cv-prog', { style: `--i:${i}` }, h('div.cv-prog-head', h('span', str(it.label)), h('b', it.max ? `${str(it.value)} / ${str(it.max)}` : Math.round(pct) + '%')), h('div.cv-meter', h('i', { style: { width: pct + '%' } })));
  })),

  facts: (b) => h('dl.cv-facts', arr(b.items).map((it, i) => [h('dt', { style: `--i:${i}` }, str(it.label ?? it.key)), h('dd', { style: `--i:${i}` }, str(it.value))])),

  image: (b) => {
    const url = src(b.src || b.url || b.path);
    return h('figure.cv-figure', h('img', { src: url, alt: str(b.caption), loading: 'lazy', onclick: () => openViewer({ url, kind: 'image', caption: b.caption }) }), b.caption ? h('figcaption', str(b.caption)) : null);
  },

  gallery: (b) => {
    const items = arr(b.items).map((it) => (typeof it === 'string' ? { src: it } : it));
    const all = items.map((it) => ({ url: src(it.src || it.url || it.path), kind: 'image', caption: it.caption }));
    return h('div.cv-gallery', items.map((it, i) => h('figure', { style: `--i:${i}` }, h('img', { src: all[i].url, alt: str(it.caption), loading: 'lazy', onclick: () => openViewer(all, i) }), it.caption ? h('figcaption', str(it.caption)) : null)));
  },

  code: (b) => {
    const box = md('```' + str(b.language || b.lang) + '\n' + str(b.code) + '\n```');
    box.querySelectorAll('.copy-code').forEach((btn) => btn.addEventListener('click', () => copyText(str(b.code)).then(() => toast('Code copied'))));
    box.querySelectorAll('.preview-code').forEach((btn) => btn.remove());
    return box;
  },

  quote: (b) => h('blockquote.cv-quote', h('div', str(b.text)), b.by ? h('cite', '— ' + str(b.by)) : null),

  diagram: (b) => {
    const box = h('div.cv-diagram');
    renderMermaid(box, str(b.mermaid || b.code || b.source));
    return box;
  },

  links: (b) => h('div.cv-links', arr(b.items).map((it, i) => {
    const url = safeUrl(it.url);
    let host = '';
    try { host = new URL(url).hostname.replace(/^www\./, ''); } catch { /* not a URL */ }
    return h(url ? 'a.cv-link' : 'div.cv-link', { href: url || null, target: '_blank', rel: 'noopener', style: `--i:${i}` },
      h('div', h('div.cv-li-title', str(it.title || host || url)), h('div.cv-li-sub', str(it.sub || host))), url ? icon('external') : null);
  })),

  cards: (b) => h('div.cv-cards', arr(b.items).map((it, i) => {
    const url = safeUrl(it.url);
    const img = src(it.image);
    return h(url ? 'a.cv-card' : 'div.cv-card', { href: url || null, target: '_blank', rel: 'noopener', style: `--i:${i}` },
      img ? h('div.cv-card-img', h('img', { src: img, alt: '', loading: 'lazy' })) : null,
      h('div.cv-card-body', it.tag ? h('span.cv-tag', str(it.tag)) : null, h('div.cv-li-title', str(it.title)), it.sub ? h('div.cv-li-sub', str(it.sub)) : null));
  })),

  map: (b) => renderCard({ type: 'map', markers: arr(b.markers || b.places).map((m) => ({ lat: num(m.lat), lon: num(m.lon ?? m.lng), label: m.label || m.name })), line: b.line }) || h('div.muted', 'No map.'),
};

function blockEl(b, i = 0) {
  const render = R[b.type];
  let body;
  try {
    body = render ? render(b) : md(b.text || '```json\n' + JSON.stringify(b, null, 2) + '\n```');
  } catch (err) {
    console.error(err);
    body = h('div.muted', 'Could not show this block.');
  }
  const showTitle = b.title && b.type !== 'hero';
  const el = h('section.cv-block', { dataset: { id: b.id, type: b.type || 'text', size: b.size || (b.type === 'hero' || b.type === 'table' || b.type === 'timeline' ? 'full' : 'm'), tone: b.tone || '' }, style: `--n:${i}` },
    showTitle ? h('header.cv-block-head', h('span', str(b.title))) : null,
    h('div.cv-block-body', body));
  return el;
}

// ---------- the pane ----------

function animateIn(el, delay) {
  el.classList.add('enter');
  el.style.animationDelay = delay + 'ms';
  el.addEventListener('animationend', () => el.classList.remove('enter'), { once: true });
}

function glow(el) {
  el.classList.remove('lit');
  void el.offsetWidth;
  el.classList.add('lit');
  el.addEventListener('animationend', (e) => e.animationName === 'cvLit' && el.classList.remove('lit'));
}

function paint({ changed = [], removed = [], animate = false, focus = '' } = {}) {
  const grid = els.grid;
  const before = new Map();
  if (animate) grid.querySelectorAll('.cv-block').forEach((n) => before.set(n.dataset.id, n.getBoundingClientRect()));
  const existing = new Map([...grid.querySelectorAll('.cv-block:not(.leaving)')].map((n) => [n.dataset.id, n]));
  // leaving blocks fade out where they were
  for (const [id, node] of existing) {
    if (cv.blocks.some((b) => b.id === id)) continue;
    if (!animate) { node.remove(); continue; }
    const r = node.getBoundingClientRect();
    const g = grid.getBoundingClientRect();
    Object.assign(node.style, { position: 'absolute', left: r.left - g.left + 'px', top: r.top - g.top + grid.scrollTop + 'px', width: r.width + 'px', height: r.height + 'px' });
    node.classList.add('leaving');
    node.addEventListener('animationend', () => node.remove(), { once: true });
  }
  let fresh = 0;
  cv.blocks.forEach((b, i) => {
    let node = existing.get(b.id);
    if (!node || changed.includes(b.id)) {
      const next = blockEl(b, i);
      if (node) {
        node.replaceWith(next);
        if (animate) glow(next);
      } else if (animate) animateIn(next, 90 * fresh++);
      node = next;
    }
    grid.appendChild(node); // keeps the order
  });
  // everything else glides to its new place
  if (animate) {
    grid.querySelectorAll('.cv-block:not(.leaving):not(.enter)').forEach((n) => {
      const r0 = before.get(n.dataset.id);
      if (!r0) return;
      const r1 = n.getBoundingClientRect();
      const dx = r0.left - r1.left;
      const dy = r0.top - r1.top;
      if (Math.abs(dx) + Math.abs(dy) < 1) return;
      n.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], { duration: 600, easing: 'cubic-bezier(.16, 1, .3, 1)' });
    });
  }
  els.title.textContent = cv.title || 'Canvas';
  els.count.textContent = cv.blocks.length ? String(cv.blocks.length) : '';
  els.fabCount.textContent = String(cv.blocks.length);
  const target = focus || (animate && changed.length ? changed[changed.length - 1] : '');
  if (target) {
    const n = grid.querySelector(`.cv-block[data-id="${CSS.escape(target)}"]`);
    if (n) setTimeout(() => n.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 160);
  }
  layout();
}

function layout() {
  const has = cv.blocks.length > 0;
  els.view.classList.toggle('has-canvas', has && !cv.dismissed);
  els.view.classList.toggle('canvas-available', has);
  els.fab.classList.toggle('hidden', !has || !cv.dismissed);
  els.btn.classList.toggle('hidden', !has);
  els.btn.classList.toggle('on', has && !cv.dismissed);
}

export function openCanvas() {
  cv.dismissed = false;
  layout();
}

export function closeCanvas() {
  cv.dismissed = true;
  els.view.classList.remove('canvas-wide');
  layout();
}

export function toggleCanvas() {
  if (cv.dismissed || !els.view.classList.contains('has-canvas')) openCanvas();
  else closeCanvas();
}

/** Show a canvas update from the canvas tool. live: it just happened, so it animates (and opens, if allowed). */
export function canvasApply(display, { live = false } = {}) {
  cv.title = display.title || '';
  cv.blocks = arr(display.blocks).filter((b) => b && b.id);
  if (live && prefs.canvasAuto !== false) cv.dismissed = false;
  paint({ changed: display.cleared ? cv.blocks.map((b) => b.id) : arr(display.changed), removed: arr(display.removed), animate: live, focus: display.focus });
  if (live && cv.dismissed) {
    els.fab.classList.remove('ping');
    void els.fab.offsetWidth;
    els.fab.classList.add('ping');
  }
}

/** The canvas as a stored conversation left it. */
export function canvasLoad(messages) {
  let last = null;
  for (const m of messages || []) {
    for (const p of (m.data && m.data.parts) || []) if (p.display && p.display.type === 'canvas') last = p.display;
  }
  cv.title = last ? last.title || '' : '';
  cv.blocks = last ? arr(last.blocks).filter((b) => b && b.id) : [];
  cv.dismissed = isMobile();
  els.grid.replaceChildren();
  paint();
}

export function canvasReset() {
  cv.title = '';
  cv.blocks = [];
  cv.dismissed = false;
  els.view.classList.remove('canvas-wide');
  if (els.grid) els.grid.replaceChildren();
  if (els.view) paint();
}

/** The quiet line in the conversation where the canvas changed; it opens the canvas at what changed. */
export function canvasChip(display) {
  const names = (id) => {
    const b = arr(display.blocks).find((x) => x.id === id);
    return b ? str(b.title) || (b.type === 'hero' ? str(b.title) : '') || id.replace(/-/g, ' ') : id.replace(/-/g, ' ');
  };
  const changed = arr(display.changed).map(names).filter(Boolean);
  const what = display.cleared ? 'New canvas' + (display.title ? ': ' + display.title : '') : changed.length ? changed.slice(0, 3).join(', ') + (changed.length > 3 ? ` +${changed.length - 3}` : '') : arr(display.removed).length ? 'Tidied up' : 'Updated';
  return h('button.cv-chip', { type: 'button', title: 'Show on the canvas', onclick: () => { openCanvas(); paint({ focus: arr(display.changed).slice(-1)[0] || '' }); } }, icon('canvas'), h('span.cv-chip-label', 'Canvas'), h('span.cv-chip-what', what));
}

export function initCanvas({ view, pane, fab, btn }) {
  els.view = view;
  els.pane = pane;
  els.fab = fab;
  els.btn = btn;
  els.title = h('h2.cv-title', 'Canvas');
  els.count = h('span.cv-count');
  els.grid = h('div.cv-grid');
  const wide = h('button.icon-btn.desk-only', { title: 'Wider canvas', onclick: () => view.classList.toggle('canvas-wide') }, icon('expand'));
  const close = h('button.icon-btn', { title: 'Hide the canvas', onclick: closeCanvas }, icon('x'));
  pane.replaceChildren(
    h('div.cv-head', h('div.cv-head-text', h('div.cv-kicker', icon('canvas'), 'Live canvas', els.count), els.title), wide, close),
    h('div.cv-scroll', els.grid),
  );
  els.fabCount = h('b');
  fab.replaceChildren(icon('canvas'), h('span', 'Canvas'), els.fabCount);
  fab.addEventListener('click', openCanvas);
  btn.addEventListener('click', toggleCanvas);
  layout();
}
