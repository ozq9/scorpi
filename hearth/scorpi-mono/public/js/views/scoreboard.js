// The model scoreboard: how well each model actually worked here (jobs, checks, tests, undo, parallel attempts) and the
// built-in benchmark ("Test my free models"). Automatic coding picks follow it (scorpi/scores.py, scorpi/bench.py).

import { api } from '../lib/api.js';
import { h, icon } from '../lib/dom.js';
import { sheet, switchEl, toast } from '../lib/ui.js';
import { bus, state } from '../store.js';

let box = null;
let data = null;

const pct = (x) => Math.round(x * 100) + '%';

export function scoreboardSection() {
  box = h('section.u-section.sb');
  paint();
  load();
  return box;
}

async function load() {
  try {
    data = await api.get('/api/scores');
  } catch (e) {
    data = { error: e.message };
  }
  paint();
}

bus.on('server:bench', (d) => {
  if (!box || !box.isConnected || !data) return;
  const finished = data.bench && data.bench.running && !d.running;
  data.bench = d;
  if (finished) load(); // the scores changed
  else paint();
});

function paint() {
  if (!box) return;
  box.replaceChildren(
    h('div.u-section-head', h('h2', 'Which models work best here'), h('span.muted', 'From your own jobs, checks, tests and undos. Counted on this PC.')),
    !data ? h('div.u-empty', 'Loading…') : data.error ? h('div.u-empty', data.error) : body(),
  );
}

function body() {
  const b = data.bench || {};
  const rows = data.board || [];
  return h('div.sb-body',
    h('div.sb-actions',
      h('button.btn.small.primary', { type: 'button', disabled: b.running, onclick: pickModels }, icon('play'), b.running ? 'Testing…' : 'Test my free models'),
      h('label.sb-learn', switchEl(data.learn, async (on) => {
        state.settings = await api.post('/api/settings', { learn_models: on }).catch((e) => toast(e.message, 'error')) || state.settings;
        data.learn = on;
        toast(on ? 'Automatic picks follow the scoreboard' : 'Automatic picks follow the catalog order');
      }), h('span', 'Let results steer the automatic coding model'))),
    b.running || (b.finished && Object.keys(b.results || {}).length) ? benchCard(b) : null,
    rows.length
      ? h('div.sb-rows', rows.map((r, i) => row(r, i)))
      : h('div.u-empty', 'No results yet. Scorpi learns from every coding job; or test your models now (five small tasks each, checked by hidden tests).'),
  );
}

function row(r, i) {
  const proven = r.evidence >= data.min_evidence;
  const chips = Object.entries(r.signals).map(([k, s]) => h('span.sb-chip' + (s.rate >= 0.7 ? '.good' : s.rate < 0.4 ? '.bad' : ''), { title: s.label }, `${s.label}: ${s.good}/${s.good + s.bad}`));
  return h('div.sb-row' + (proven ? '' : '.unproven'),
    h('span.sb-rank', proven ? '#' + (i + 1) : '–'),
    h('div.sb-main',
      h('div.sb-name', r.label),
      h('div.sb-chips', chips, r.bench ? h('span.sb-chip.bench', icon('bolt'), `Benchmark ${r.bench.passed}/${r.bench.total}${r.bench.seconds ? ' · ' + r.bench.seconds + ' s each' : ''}`) : null)),
    h('div.sb-score',
      h('div.sb-bar', h('i', { style: { width: pct(r.score) } })),
      h('span', proven ? pct(r.score) : `${r.evidence} result${r.evidence === 1 ? '' : 's'}`)),
  );
}

function benchCard(b) {
  const tasks = data.tasks || [];
  const mark = (res) => (!res ? h('span.sb-cell.wait', '·') : res.skipped ? h('span.sb-cell.skip', { title: res.note }, '–') : res.pass ? h('span.sb-cell.pass', { title: `${res.seconds} s, ${res.steps} steps` }, icon('check')) : h('span.sb-cell.fail', { title: res.note || 'failed' }, icon('x')));
  return h('div.sb-bench',
    h('div.sb-bench-head',
      b.running ? h('span.job-pulse') : icon('check'),
      h('span.grow', b.running ? `Testing ${b.current || ''}` : 'Benchmark finished'),
      h('span.muted', `${b.done}/${b.total}`),
      b.running ? h('button.btn.small.quiet', { type: 'button', onclick: () => api.post('/api/bench/stop').then(() => toast('Stopping after the current task')) }, icon('stop'), 'Stop') : null),
    h('div.jp-bar', h('i', { style: { width: (b.total ? Math.round((b.done / b.total) * 100) : 0) + '%' } })),
    h('div.sb-grid-wrap', h('table.sb-grid',
      h('thead', h('tr', h('th', 'Model'), tasks.map((t) => h('th', t.title)))),
      h('tbody', (b.models || []).map((m) => h('tr', h('td', labelOf(m)), tasks.map((t) => h('td', mark(((b.results || {})[m] || {})[t.id])))))))),
  );
}

function labelOf(model) {
  const s = (data.suggested || []).find((x) => x.model === model);
  return s ? s.label : model.replace(/^@/, '');
}

function pickModels() {
  const options = data.suggested || [];
  if (!options.length) return toast('Add a model provider first (Connections → Model).', 'error');
  const chosen = new Set(options.slice(0, 3).map((o) => o.model));
  const list = h('div.sb-pick', options.map((o) => h('label.sb-pick-row',
    h('input', { type: 'checkbox', checked: chosen.has(o.model), onchange: (e) => (e.target.checked ? chosen.add(o.model) : chosen.delete(o.model)) }),
    h('span', o.label))));
  sheet({
    title: 'Test my free models',
    body: h('div',
      h('p.section-desc', `Each model gets the same ${(data.tasks || []).length} small coding tasks in a folder of its own: fix a bug, follow a spec, change several files, parse text and extend a command-line tool. Hidden tests decide pass or fail. It runs in the background and uses about 5 to 15 requests per task.`),
      list),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h('button.btn.primary', { onclick: async () => {
        if (!chosen.size) return toast('Pick at least one model.', 'error');
        try {
          data.bench = await api.post('/api/bench', { models: [...chosen] });
          close();
          paint();
        } catch (e) {
          toast(e.message, 'error');
        }
      } }, icon('play'), 'Start'),
    ],
  });
}
