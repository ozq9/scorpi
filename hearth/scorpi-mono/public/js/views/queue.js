// The queue on the Code page: tasks for later that Scorpi works through one by one in the hours you pick (overnight
// by default), upkeep jobs that join it on a schedule, and the report of the last session (scorpi/backlog.py).

import { api } from '../lib/api.js';
import { debounce, fmtRelative, h, icon } from '../lib/dom.js';
import { seg, selectEl, toast } from '../lib/ui.js';
import { bus, state } from '../store.js';

let box = null;
let data = null;
let projects = [];
let upkeepSlug = '';

const MARK = { queued: 'clock', running: 'play', done: 'check', failed: 'x', stopped: 'stop' };
const pad = (n) => String(n).padStart(2, '0') + ':00';

export function queueSection(list) {
  projects = list || [];
  box = h('div.section.queue', { id: 'queue' });
  paint();
  load().then(() => location.hash === '#queue' && box.scrollIntoView({ block: 'start', behavior: 'smooth' }));
  return box;
}

async function load() {
  try {
    data = await api.get('/api/backlog');
  } catch (e) {
    data = { error: e.message };
  }
  paint();
}

const reload = debounce(() => box && box.isConnected && load(), 400);
bus.on('server:backlog', reload);
bus.on('server:agent_job', (job) => data && (data.items || []).some((i) => i.job_id === job.id) && reload());

async function act(promise, done) {
  try {
    data = await promise;
    if (done) toast(done, { timeout: 1800 });
  } catch (e) {
    toast(e.message, 'error');
  }
  paint();
}

function paint() {
  if (!box) return;
  const items = (data && data.items) || [];
  const waiting = items.filter((i) => i.status === 'queued').length;
  box.replaceChildren(
    h('h3.section-title', 'Queue', h('span.count', waiting || ''), h('span.rule')),
    h('p.section-desc', 'Tasks for later. Scorpi works through them one by one while you are away, as long as the free models have requests left; each gets a checkpoint, so any of them can be undone. Approvals still reach you, on your phone too.'),
    ...(!data ? [h('div.panel.panel-pad.muted', 'Loading…')] : data.error ? [h('div.panel.panel-pad.muted', data.error)] : body(items)),
  );
}

function body(items) {
  return [controls(), addForm(), items.length ? h('div.q-list', items.slice().reverse().sort(order).map(row)) : null, upkeep(), report()].filter(Boolean);
}

const RANK = { running: 0, queued: 1 };
const order = (a, b) => (RANK[a.status] ?? 2) - (RANK[b.status] ?? 2) || (RANK[a.status] !== undefined ? 0 : (b.ended || 0) - (a.ended || 0));

function controls() {
  const [start, end] = data.hours;
  const hoursOpts = Array.from({ length: 24 }, (_, i) => [String(i), pad(i)]);
  const save = (patch) => api.post('/api/settings', patch).then((s) => { state.settings = s; load(); }).catch((e) => toast(e.message, 'error'));
  const running = data.items.some((i) => i.status === 'running');
  return h('div.q-controls',
    seg([['night', 'In these hours'], ['always', 'Right away'], ['paused', 'Paused']], data.window, (v) => save({ backlog_window: v }), 'q-window'),
    h('label.q-hours', 'from', selectEl(hoursOpts, String(start), (v) => save({ backlog_hours: [+v, end] })), 'until', selectEl(hoursOpts, String(end), (v) => save({ backlog_hours: [start, +v] }))),
    h('span.grow'),
    h('span.muted.small', running ? 'Working now' : data.open ? 'Open now' : `Starts at ${pad(start)}`),
    h('button.btn.small', { type: 'button', disabled: !data.items.some((i) => i.status === 'queued'), onclick: () => act(api.post('/api/backlog/run-now'), 'Working through the queue now') }, icon('play'), 'Run now'));
}

function addForm() {
  if (!projects.length) return h('div.muted.small', 'Create a project first; then queue tasks for it here.');
  const pick = selectEl(projects.map((p) => [p.slug, p.name]), projects[0].slug, () => {});
  const task = h('input.input.grow', { placeholder: 'A task for later, e.g. "Add a dark mode and test it on a phone"' });
  const add = () => {
    if (!task.value.trim()) return task.focus();
    act(api.post('/api/backlog', { slug: pick.value, task: task.value.trim() }).then(() => api.get('/api/backlog')), 'Queued');
  };
  task.addEventListener('keydown', (e) => e.key === 'Enter' && (e.preventDefault(), add()));
  return h('div.q-add', pick, task, h('button.btn.small.primary', { type: 'button', onclick: add }, icon('plus'), 'Queue'));
}

function row(i) {
  const name = (projects.find((p) => p.slug === i.slug) || {}).name || i.slug;
  const when = i.status === 'running' ? 'working since ' + fmtRelative(i.started) : i.status === 'queued' ? 'waiting' : fmtRelative(i.ended);
  const verdict = i.proof ? h('span.q-verdict.' + i.proof.verdict, i.verdict || i.proof.verdict) : null;
  return h('div.q-row.' + i.status,
    i.status === 'running' ? h('span.job-pulse') : h('span.q-mark', icon(MARK[i.status] || 'clock')),
    h('div.q-main',
      h('div.q-title', i.upkeep ? h('span.badge', 'upkeep') : null, ' ', i.title),
      h('div.q-sub', h('span', name), h('span', ' · ' + when), verdict ? [' · ', verdict] : null),
      i.result && i.status !== 'queued' && i.status !== 'running' ? h('div.q-result', i.result.split('\n').find((l) => l.trim()) || '') : null),
    h('div.q-acts',
      i.status === 'queued' ? [
        h('button.icon-btn', { title: 'Earlier', onclick: () => act(api.post(`/api/backlog/${i.id}/move`, { step: -1 })) }, icon('down', 'flip')),
        h('button.icon-btn', { title: 'Later', onclick: () => act(api.post(`/api/backlog/${i.id}/move`, { step: 1 })) }, icon('down')),
      ] : null,
      i.conv_id ? h('a.icon-btn', { href: '/c/' + i.conv_id, 'data-link': '', title: 'Open the conversation' }, icon('eye')) : null,
      ['done', 'failed', 'stopped'].includes(i.status) ? h('button.icon-btn', { title: 'Run it again', onclick: () => act(api.post(`/api/backlog/${i.id}/retry`)) }, icon('retry')) : null,
      i.status !== 'running' ? h('button.icon-btn', { title: 'Remove', onclick: () => act(api.del(`/api/backlog/${i.id}`)) }, icon('trash')) : null));
}

function upkeep() {
  if (!projects.length) return null;
  upkeepSlug = projects.some((p) => p.slug === upkeepSlug) ? upkeepSlug : projects[0].slug;
  const mine = (data.upkeep || []).filter((u) => u.slug === upkeepSlug);
  const set = (kind, on, every) => act(api.post('/api/backlog/upkeep', { slug: upkeepSlug, kind, on, every }));
  return h('details.q-upkeep', { open: (data.upkeep || []).length > 0 },
    h('summary', icon('refresh'), 'Upkeep jobs', h('span.muted.small', ' · join the queue on a schedule')),
    h('div.q-upkeep-body',
      h('label.q-for', 'For', selectEl(projects.map((p) => [p.slug, p.name]), upkeepSlug, (v) => { upkeepSlug = v; paint(); })),
      h('div.q-kinds', data.kinds.map((k) => {
        const cur = mine.find((u) => u.kind === k.id);
        return h('div.q-kind' + (cur ? '.on' : ''),
          h('label', h('input', { type: 'checkbox', checked: !!cur, onchange: (e) => set(k.id, e.target.checked, cur ? cur.every : 'week') }), icon(k.icon), k.name),
          cur ? selectEl([['day', 'every day'], ['week', 'every week']], cur.every, (v) => set(k.id, true, v)) : null);
      }))));
}

function report() {
  const r = (data.reports || [])[0];
  if (!r) return null;
  const ok = r.items.filter((i) => i.status === 'done').length;
  const verified = r.items.filter((i) => i.verdict === 'Verified').length;
  return h('div.q-report',
    h('div.q-report-head', icon('history'), h('b', `Last session: ${ok} of ${r.items.length} done`), h('span.muted', ` · ${verified} verified · ${fmtRelative(r.at)}`)),
    h('ul', r.items.map((i) => h('li.' + i.status, icon(i.status === 'done' ? 'check' : 'x'),
      i.conv_id ? h('a', { href: '/c/' + i.conv_id, 'data-link': '' }, i.title) : h('span', i.title),
      h('span.muted', ` · ${(projects.find((p) => p.slug === i.slug) || {}).name || i.slug}${i.verdict ? ' · ' + i.verdict : ''}`)))));
}
