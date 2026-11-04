// Rich cards rendered from tool results (images, maps, diagrams, quizzes, plans...).

import { api, fileUrl } from '../lib/api.js';
import { baseName, extOf, fmtDate, fmtSize, fmtTime, h, icon, loadScript, loadStyle } from '../lib/dom.js';
import { toast } from '../lib/ui.js';
import { diffList, diffStat } from '../lib/diff.js';
import { fileChip, kindOf, linkEmbed, openViewer } from '../lib/viewer.js';
import { bus } from '../store.js';
import { agentCard, jobPlaceholder } from './agents.js';

const isDark = () => document.documentElement.classList.contains('is-dark') || document.documentElement.dataset.theme === 'dark';

function head(iconName, title, ...extra) {
  return h('div.card-head', icon(iconName), h('span.ch-title', title), ...extra);
}

function fileActions(path) {
  return [
    h('a.icon-btn', { href: fileUrl(path, true), title: 'Download', download: baseName(path) }, icon('download')),
    h('button.icon-btn', { title: 'Open on the PC', onclick: () => api.post('/api/file/open', { path }).then(() => toast('Opened on the host PC')).catch((e) => toast(e.message, 'error')) }, icon('external')),
  ];
}

const renderers = {
  image(d) {
    const src = d.url || fileUrl(d.path);
    const open = () => openViewer(d.path ? { path: d.path, kind: 'image', caption: d.caption } : { url: d.url, kind: 'image', caption: d.caption });
    return h(
      'div.card.image',
      h('img', { src, alt: d.caption || '', loading: 'lazy', onclick: open }),
      h('div.cap', h('span', d.caption || baseName(d.path || d.url)), ...(d.path ? fileActions(d.path) : [])),
    );
  },

  video(d) {
    return h(
      'div.card.video',
      h('video', { controls: true, src: d.url || fileUrl(d.path), preload: 'metadata', playsInline: true }),
      h('div.cap', h('span', d.caption || baseName(d.path || d.url)), ...(d.path ? fileActions(d.path) : [])),
    );
  },

  file(d) {
    const ext = extOf(d.name || d.path);
    const kind = kindOf(d.name || d.path);
    const open = () => openViewer({ path: d.path, name: d.name, size: d.size });
    return h(
      'div.card.file.clickable',
      { onclick: (e) => !e.target.closest('a, button') && open(), title: 'Preview' },
      h('div.file-glyph', ext.slice(0, 4) || 'file'),
      h('div.f-meta', h('div.f-name', d.caption || d.name), h('div.f-sub', (d.size ? fmtSize(d.size) + ' · ' : '') + d.path)),
      h('button.icon-btn', { title: kind === 'html' ? 'Preview the page' : 'Preview', onclick: open }, icon('eye')),
      ...fileActions(d.path),
    );
  },

  audio(d) {
    return h('div.card.audio', head('speaker', d.caption || baseName(d.path || d.url), ...(d.path ? fileActions(d.path) : [])), h('audio', { controls: true, src: d.url || fileUrl(d.path), preload: 'none' }));
  },

  link(d) {
    return h('div.card.link', linkEmbed(d.url, { caption: d.caption, title: d.title }));
  },

  code(d) {
    const out = (d.stdout || '').trim();
    const err = (d.stderr || '').trim();
    const status = d.exit === 0 ? h('span.badge.ok', 'exit 0') : h('span.badge.err', 'exit ' + d.exit);
    return h(
      'div.card.code',
      head('terminal', (d.language || 'code').replace(/^./, (c) => c.toUpperCase()), status),
      h('pre', d.code),
      out || err ? h('div.out', out ? h('pre', out) : null, err ? h('pre.err', err) : null) : null,
      (d.images || []).length
        ? h('div.imgs', d.images.map((p, i) => h('img', { src: fileUrl(p), onclick: () => openViewer(d.images.map((x) => ({ path: x, kind: 'image' })), i) })))
        : null,
      (d.files || []).length ? h('div.out.files', d.files.map((p) => fileChip({ path: p }))) : null,
    );
  },

  diff(d) {
    const files = d.files || [];
    const add = files.reduce((n, f) => n + (f.add || 0), 0);
    const del = files.reduce((n, f) => n + (f.del || 0), 0);
    const title = files.length === 1 ? files[0].path.split('/').pop() : files.length + ' files';
    const problems = (d.checks || '').startsWith('PROBLEMS');
    return h(
      'div.card.diff' + (files.length === 1 ? '.single' : ''),
      files.length === 1 ? null : head('diff', title, diffStat(add, del)), // one file: its own header says it all
      diffList(files, { open: files.length <= 2 }),
      d.checks ? h('div.diff-checks' + (problems ? '.bad' : '.good'), icon(problems ? 'x' : 'check'), h('span', d.checks.replace(/^PROBLEMS FOUND — fix them now:\n/, ''))) : null,
    );
  },

  table(d) {
    return h(
      'div.card.table',
      head('table', d.caption || 'Table'),
      h(
        'div.tbl',
        h('table', h('thead', h('tr', (d.columns || []).map((c) => h('th', c)))), h('tbody', (d.rows || []).map((r) => h('tr', r.map((c) => h('td', { title: c }, c)))))),
      ),
    );
  },

  sources(d) {
    const rows = (d.results || []).slice(0, 8);
    return h(
      'div.card.sources',
      head('globe', 'Sources · ' + (d.query || '')),
      rows.map((r, i) =>
        h(
          r.url ? 'a.src' : 'div.src',
          r.url ? { href: r.url, target: '_blank', rel: 'noopener noreferrer' } : {},
          h('span.src-n', String(i + 1).padStart(2, '0')),
          h('span', h('span.src-t', r.title || r.url), r.url ? h('span.src-u', r.url.replace(/^https?:\/\/(www\.)?/, '')) : null, r.snippet ? h('span.src-s', r.snippet) : null),
        ),
      ),
    );
  },

  plan(d) {
    const steps = d.steps || [];
    const done = steps.filter((s) => s.status === 'done').length;
    return h(
      'div.card.plan',
      head('route', 'Plan', h('span.mono', `${done}/${steps.length}`)),
      h('div.plan-progress', h('i', { style: { width: (steps.length ? (done / steps.length) * 100 : 0) + '%' } })),
      h(
        'div.card-body',
        steps.map((s) => h('div.plan-step.' + (s.status || 'pending'), h('span.ps-box', icon(s.status === 'failed' ? 'x' : 'check')), h('span.ps-t', s.title))),
        d.note ? h('div.muted', { style: { fontSize: '13px', marginTop: '6px' } }, d.note) : null,
      ),
    );
  },

  quiz(d, ctx) {
    const answers = {};
    let score = 0;
    let graded = 0;
    const mcCount = d.questions.filter((q) => q.options && q.options.length).length;
    const scoreEl = h('span.score');
    const updateScore = () => {
      scoreEl.textContent = mcCount ? `${score} / ${mcCount}` : '';
    };
    const qNodes = d.questions.map((q, qi) => {
      const block = h('div.q', h('div.q-t', h('span.n', String(qi + 1).padStart(2, '0')), h('span', q.question)));
      if (q.options && q.options.length) {
        const opts = q.options.map((o, oi) =>
          h(
            'button.opt',
            {
              type: 'button',
              onclick: () => {
                answers[qi] = o;
                opts.forEach((b) => (b.disabled = true));
                const right = typeof q.answer === 'number' ? q.answer === oi : null;
                if (right !== null) {
                  graded++;
                  if (right) score++;
                  opts[oi].classList.add(right ? 'right' : 'wrong');
                  if (!right && opts[q.answer]) opts[q.answer].classList.add('right');
                  updateScore();
                }
                if (q.explanation) block.appendChild(h('div.expl', q.explanation));
              },
            },
            h('span.k', String.fromCharCode(65 + oi)),
            h('span', o),
          ),
        );
        block.append(...opts);
      } else {
        const ta = h('textarea', { placeholder: 'Your answer…', oninput: (e) => (answers[qi] = e.target.value) });
        block.appendChild(ta);
      }
      return block;
    });
    const submit = h(
      'button.btn.primary.small',
      {
        type: 'button',
        onclick: () => {
          const lines = d.questions.map((q, i) => `${i + 1}. ${q.question}\n   My answer: ${answers[i] ?? '(skipped)'}`);
          const summary = mcCount ? `\n\nMultiple-choice score: ${score}/${mcCount}.` : '';
          bus.emit('compose:send', `Here are my answers to "${d.title}":\n\n${lines.join('\n')}${summary}\n\nPlease evaluate them and explain anything I got wrong.`);
          submit.disabled = true;
        },
      },
      'Send answers for feedback',
    );
    updateScore();
    void ctx;
    return h('div.card.quiz', head('book', d.title || 'Practice'), qNodes, h('div.quiz-foot', scoreEl, h('span', { style: { flex: 1 } }), submit));
  },

  map(d) {
    const box = h('div.map-box');
    const card = h('div.card.map', head('map', d.summary ? 'Route' : (d.markers || []).length + ' place(s)'), box, d.summary ? h('div.map-summary', d.summary) : null);
    loadStyle('https://unpkg.com/leaflet@1.9.4/dist/leaflet.css');
    loadScript('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js')
      .then(() => {
        const L = window.L;
        const map = L.map(box, { zoomControl: true, attributionControl: true, scrollWheelZoom: false });
        // OpenStreetMap's own tiles need no key (CARTO's basemaps now answer "API key required"); dark styles get them
        // tinted dark with a filter (chat.css)
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
          attribution: '&copy; OpenStreetMap contributors',
          maxZoom: 19,
          className: isDark() ? 'map-tiles-dark' : '',
        }).addTo(map);
        const bounds = [];
        for (const m of d.markers || []) {
          const marker = L.marker([m.lat, m.lon], { icon: L.divIcon({ className: '', html: `<div class="scorpi-pin ${m.kind || ''}"></div>`, iconSize: [14, 14] }) }).addTo(map);
          if (m.label) marker.bindPopup(m.label);
          bounds.push([m.lat, m.lon]);
        }
        if (d.line && d.line.length) {
          const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#3D6B58';
          L.polyline(d.line, { color: accent, weight: 4, opacity: 0.85 }).addTo(map);
          d.line.forEach((p) => bounds.push(p));
        }
        if (bounds.length === 1) map.setView(bounds[0], 15);
        else if (bounds.length) map.fitBounds(bounds, { padding: [28, 28] });
        else map.setView([20, 0], 2);
        setTimeout(() => map.invalidateSize(), 200);
      })
      .catch(() => box.replaceChildren(h('div.muted', { style: { padding: '20px' } }, 'Map tiles need internet access.')));
    return card;
  },

  mermaid(d) {
    const box = h('div.mermaid-box', h('div.waiting', h('i'), h('i'), h('i')));
    const card = h('div.card.mermaid', head('route', d.caption || 'Diagram', d.path ? fileActions(d.path) : null), box);
    import('https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.esm.min.mjs')
      .then(async ({ default: mermaid }) => {
        mermaid.initialize({ startOnLoad: false, theme: isDark() ? 'dark' : 'neutral', securityLevel: 'strict', fontFamily: 'Geist, system-ui, sans-serif' });
        const id = 'mmd' + Math.random().toString(36).slice(2);
        const { svg } = await mermaid.render(id, d.code);
        box.innerHTML = svg;
      })
      .catch((err) => {
        box.replaceChildren(h('pre', { style: { margin: 0, fontSize: '12px' } }, d.code + '\n\n(' + (err.message || 'could not render') + ')'));
      });
    return card;
  },

  weather(d) {
    return h(
      'div.card.weather',
      head('sun', d.place || 'Weather'),
      h('div.wx', h('div.wx-now', Math.round(d.now.temp) + '°'), h('div', h('div', d.now.sky), h('div.wx-sky', 'wind ' + d.now.wind + ' km/h'))),
      h(
        'div.wx-days',
        (d.days || []).map((x) =>
          h('div.wx-day', h('b', new Date(x.date).toLocaleDateString([], { weekday: 'short', day: 'numeric' })), h('div', `${Math.round(x.min)}° – ${Math.round(x.max)}°`), h('div.muted', x.sky), h('div.muted', x.rain + '% rain')),
        ),
      ),
    );
  },

  agenda(d) {
    return h(
      'div.card.agenda',
      head('calendar', 'Agenda', h('a.btn.quiet.small', { href: '/agenda', 'data-link': '' }, 'Open')),
      (d.items || []).map((it) =>
        h('div.ag-row', h('span.ag-kind.' + it.kind), h('span.ag-time', fmtDate(it.time, { weekday: 'short', day: 'numeric', month: 'short' }) + ' ' + fmtTime(it.time)), h('span', it.title, it.location ? h('span.muted', ' · ' + it.location) : null)),
      ),
    );
  },

  memory(d) {
    return h('div.card.chip-card', icon('memory'), h('span', h('span.muted', 'Remembered · '), d.text));
  },

  task(d) {
    return h('div.card.chip-card', icon('routine'), h('span', { style: { flex: 1 } }, 'Background task started: ', h('b', d.title)), h('a.btn.small', { href: '/c/' + d.conv_id, 'data-link': '' }, 'Follow'));
  },

  agent_job(d) {
    return jobPlaceholder(d);
  },

  agent(d) {
    return agentCard(d);
  },

  look(d) {
    // a style or plugin Scorpi just made: a few swatches of its colours and a way back
    const v = d.vars || {};
    const sw = ['--bg', '--ink', '--accent', '--glass'].map((k) => v[k]).filter(Boolean);
    const undo = () => api.post('/api/looks/active', { id: '' }).then(() => toast('Default look')).catch((e) => toast(e.message, 'error'));
    return h(
      'div.card.chip-card.look-card',
      icon(d.kind === 'plugin' ? 'bolt' : 'spark'),
      sw.length ? h('span.look-sw', sw.map((c) => h('i', { style: { background: c } }))) : null,
      h('span', { style: { flex: 1 } }, h('span.muted', d.kind === 'plugin' ? 'Plugin on · ' : 'New style · '), h('b', d.name)),
      d.kind === 'style' ? h('button.btn.small.quiet', { onclick: undo }, 'Default look') : null,
      h('a.btn.small', { href: '/settings#looks', 'data-link': '' }, 'Styles'),
    );
  },

  automation(d) {
    return h('div.card.chip-card', icon('routine'), h('span', { style: { flex: 1 } }, h('b', d.name), h('span.muted', ' · ' + d.schedule)), h('a.btn.small', { href: '/routines', 'data-link': '' }, 'Routines'));
  },
};

export function renderCard(display, ctx) {
  if (!display || !renderers[display.type]) return null;
  try {
    return renderers[display.type](display, ctx);
  } catch (err) {
    console.error(err);
    return null;
  }
}
