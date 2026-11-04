// The project pane: a workbench next to a project's conversation with its agent. Agents (the job, its plan and the
// helpers working in parallel), Preview (a small browser with the page's console), Files, Changes (the agent's diff,
// commits and checkpoints to rewind), Terminal and GitHub. Drag its edge to resize; Alt+1…6 switch tabs.

import { api, fileUrl } from '../lib/api.js';
import { $, copyText, debounce, extOf, fmtRelative, fmtSize, h, icon, isMobile } from '../lib/dom.js';
import { highlight } from '../lib/md.js';
import { diffList, diffStat } from '../lib/diff.js';
import { confirmSheet, field, popover, sheet, toast } from '../lib/ui.js';
import { LANGS, kindOf, openViewer, previewBody } from '../lib/viewer.js';
import { navigate } from '../router.js';
import { bus, prefs, setPref, state } from '../store.js';
import { shortModel } from './agents.js';
import { renderChanges, renderGithub } from './project-git.js';
import { renderTerminal, termRunning, unmountTerminal } from './project-term.js';

const TABS = [
  ['agents', 'Agents', 'agent'],
  ['preview', 'Preview', 'globe'],
  ['files', 'Files', 'folder'],
  ['changes', 'Changes', 'diff'],
  ['terminal', 'Terminal', 'terminal'],
  ['github', 'GitHub', 'branch'],
];
const STATUS = { running: ['working', '.warn'], done: ['done', '.ok'], failed: ['failed', '.err'], stopped: ['stopped', ''], interrupted: ['paused', ''] };
const MARK = { running: 'active', active: 'active', done: 'done', failed: 'failed', stopped: 'pending', interrupted: 'pending', pending: 'pending' };
const IMAGES = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp'];
// shown as they look rather than as source (web pages keep their source view: that's what a coder reads)
const VISUAL = ['image', 'video', 'audio', 'pdf', 'markdown', 'table', 'doc'];
const DEVICES = [['desktop', 'monitor', 'Full width', 0], ['tablet', 'tablet', 'Tablet · 768 px', 768], ['phone', 'phone', 'Phone · 390 px', 390]];

let pane = null;
let data = null; // GET /api/projects/:slug
let tab = 'agents';
let bodies = {}; // tab -> its body element, kept while the project is open (the preview and terminal keep running)
let built = new Set(); // tabs filled since the last change
let badges = { changes: 0 };
let stamp = ''; // what the files looked like last time, to reload the preview only when they changed
let selected = '';
let filter = '';
let collapsed = new Set();
const openSubs = new Set();
const pv = { path: '', url: '', device: 'desktop', logs: [], errors: 0, console: false, frame: null, els: null };

const ctx = {
  get data() {
    return data;
  },
  refresh: () => refreshProject(true),
  setTab: (k) => setTab(k),
  badge: (k, n) => {
    badges[k] = n;
    renderTabs();
  },
  ask: (msg) => bus.emit('compose:send', msg),
  openUrl: (u) => {
    pv.url = u;
    pv.path = '';
    setTab('preview');
    loadPreview();
  },
};

const visible = () => !!(pane && data && pane.classList.contains('open'));
const dur = (from, to) => {
  const s = Math.max(0, Math.round((to || Date.now() / 1000) - from));
  return s < 60 ? s + 's' : s < 3600 ? Math.floor(s / 60) + 'm ' + String(s % 60).padStart(2, '0') + 's' : Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
};

export function projectPaneOpen() {
  return visible();
}

/** Show the pane for a project (GET /api/projects/:slug data). */
export function showProjectPane(project) {
  pane = pane || $('#projectPane');
  const same = data && data.slug === project.slug;
  data = project;
  if (!same) {
    selected = '';
    filter = '';
    collapsed = new Set();
    openSubs.clear();
    badges = { changes: 0 };
    Object.assign(pv, { path: '', url: '', logs: [], errors: 0, frame: null, els: null });
    const job = project.job;
    tab = job && job.status === 'running' ? 'agents' : project.has_index ? 'preview' : job ? 'agents' : 'files';
    stamp = treeStamp(project.tree);
    unmountTerminal();
    build();
    setOpen(!isMobile() && prefs.projectPane !== false); // on a phone the pane covers the chat: open it on request
    loadAttempts();
  }
  renderStrip();
  renderTabs();
  showTab();
}

export function hideProjectPane() {
  data = null;
  unmountTerminal();
  if (pane) {
    pane.classList.remove('open');
    pane.replaceChildren();
  }
  bodies = {};
  $('#view-chat').classList.remove('with-pane');
}

export function toggleProjectPane() {
  if (!data) return;
  setOpen(!pane.classList.contains('open'));
  if (!isMobile()) setPref('projectPane', pane.classList.contains('open'));
}

function setOpen(on) {
  pane.classList.toggle('open', on);
  $('#view-chat').classList.toggle('with-pane', on);
  const btn = $('#paneBtn');
  if (btn) btn.classList.toggle('on', on);
  if (on && !isMobile()) setWidth(prefs.paneWide ? 1e5 : prefs.paneW || 0);
}

const treeStamp = (tree) => (tree || []).map((f) => f.path + ':' + (f.mtime || 0)).join('|');

const fetchProject = debounce(async (explicit) => {
  if (!data) return;
  const slug = data.slug;
  let fresh;
  try {
    fresh = await api.get('/api/projects/' + encodeURIComponent(slug));
  } catch {
    return;
  }
  if (!data || data.slug !== slug) return;
  const changed = treeStamp(fresh.tree) !== stamp;
  stamp = treeStamp(fresh.tree);
  data = fresh;
  if (state.project && state.project.slug === slug) Object.assign(state.project, fresh);
  bus.emit('project:updated', fresh);
  renderStrip();
  if (changed || explicit) {
    built.delete('files');
    built.delete('changes');
    if (explicit) built.delete('github');
    if (changed && !pv.url && pv.els) loadPreview();
  }
  built.delete('agents');
  renderTabs();
  if (visible() && !built.has(tab)) showTab();
}, 900);

/** Fetch the project again; explicit (after the user did something) also redraws its git views. */
export function refreshProject(explicit = false) {
  fetchProject(explicit === true);
}

// the agent is working: follow its progress, and reload files / preview as they change
let lastTools = -1;
bus.on('server:agent_job', (job) => {
  if (!data || job.project !== data.slug) return;
  data.job = { ...(data.job || {}), ...job };
  renderStrip();
  renderTabs();
  if (tab === 'agents' && visible()) fill('agents');
  if (job.tools !== lastTools || job.status !== 'running') {
    lastTools = job.tools;
    refreshProject();
  }
});
bus.on('server:checkpoint', (d) => data && d.project === data.slug && refreshProject(true));

// ---------- parallel attempts: the same task by several models, each in a copy; keep the best (scorpi/attempts.py) ----------

let att = { slug: '', list: [] };
const loadAttempts = debounce(async () => {
  if (!data) return;
  const slug = data.slug;
  try {
    const res = await api.get('/api/projects/' + encodeURIComponent(slug) + '/attempts');
    if (!data || data.slug !== slug) return;
    att = { slug, list: res.attempts || [] };
  } catch {
    return;
  }
  built.delete('agents');
  if (tab === 'agents' && visible()) fill('agents');
}, 500);
bus.on('server:attempts', (d) => data && d.project === data.slug && loadAttempts());
const attemptStates = new Map();
bus.on('server:agent_job', (job) => {
  if (!data || job.attempt_of !== data.slug) return;
  const before = attemptStates.get(job.id);
  attemptStates.set(job.id, job.status + '|' + job.activity);
  const rec = att.list[0];
  const a = rec && rec.attempts.find((x) => x.job_id === job.id);
  if (a) Object.assign(a, { status: job.status, activity: job.activity, steps_done: job.steps_done, steps_total: job.steps_total });
  if (!before || before.split('|')[0] !== job.status) loadAttempts(); // finished: fetch its diff
  else if (tab === 'agents' && visible()) fill('agents');
});

function attemptsNode() {
  const rec = att.slug === (data && data.slug) ? att.list[0] : null;
  if (!rec) return null;
  const card = (a) => {
    const running = a.status === 'running';
    const applied = rec.applied === a.n;
    return h('div.att-card' + (applied ? '.applied' : '') + (running ? '.running' : ''),
      h('div.att-head', h('span.att-n', '#' + a.n), h('b.att-model', { title: a.model }, a.model_label || a.model), h('span.grow'),
        applied ? h('span.badge.ok', 'applied') : h('span.badge' + (STATUS[a.status] || ['', ''])[1], (STATUS[a.status] || [a.status])[0])),
      h('div.att-now', running ? h('span.job-pulse') : null, h('span', running ? a.activity || 'Working' : (a.result || a.activity || '').split('\n').find((l) => l.trim()) || '')),
      !running && a.count !== undefined ? h('div.att-stat', a.count ? [`${a.count} file${a.count === 1 ? '' : 's'} `, diffStat(a.add, a.dele)] : h('span.muted', 'no changes')) : null,
      h('div.att-acts',
        !running && a.files && a.files.length ? h('button.btn.small.quiet', { onclick: () => sheet({ title: `Try #${a.n} · ${a.model_label}`, body: diffList(a.files, { open: a.files.length <= 3 }), foot: (close) => [h('button.btn.quiet', { onclick: close }, 'Close')] }) }, icon('diff'), 'Diff') : null,
        a.conv_id ? h('a.btn.small.quiet', { href: '/c/' + a.conv_id, 'data-link': '' }, icon('chat'), 'Chat') : null,
        !running && !applied && a.count ? h('button.btn.small.primary', { onclick: (e) => applyAttempt(e.currentTarget, rec, a) }, icon('check'), 'Use this') : null));
  };
  return h('div.pa.att-wrap', h('div.pc-sec.att',
    h('div.pa-h', 'Attempts', h('span.count', rec.running ? `${rec.running} working` : `${rec.attempts.length} models`)),
    h('div.att-task', rec.task),
    h('div.att-grid', rec.attempts.map(card)),
    h('div.att-foot', h('span.muted.small', rec.applied ? `Try #${rec.applied} is in the project (Changes → Checkpoints can undo it).` : 'Each model works in its own copy; the project changes only when you pick one.'), h('span.grow'),
      h('button.btn.small.quiet', { onclick: () => discardAttempts(rec) }, icon('trash'), rec.running ? 'Stop & discard' : 'Discard'))));
}

async function applyAttempt(btn, rec, a) {
  if (!(await confirmSheet(`Use try #${a.n}?`, `Its ${a.count} changed file${a.count === 1 ? '' : 's'} go into the project. A checkpoint is saved first, so you can undo it in Changes.`, { confirm: 'Use this one' }))) return;
  btn.disabled = true;
  try {
    const out = await api.post('/api/attempts/' + rec.id + '/apply', { n: a.n });
    toast(`Try #${a.n} applied`, { body: `${out.files} file${out.files === 1 ? '' : 's'} changed.` });
    loadAttempts();
    refreshProject(true);
  } catch (err) {
    btn.disabled = false;
    toast(err.message, 'error');
  }
}

async function discardAttempts(rec) {
  if (!(await confirmSheet('Discard these attempts?', 'Their copies are deleted' + (rec.running ? ' and the ones still working stop' : '') + '. The project itself stays as it is.', { confirm: 'Discard', danger: true }))) return;
  try {
    await api.del('/api/attempts/' + rec.id);
    loadAttempts();
  } catch (err) {
    toast(err.message, 'error');
  }
}

/** "Try with several models": the task, and which models; each gets its own copy of the project. */
export async function startAttempts(task = '') {
  if (!data) return toast('Open a project first');
  const slug = data.slug;
  const text = h('textarea.input', { rows: 4, placeholder: 'e.g. Add a search box that filters the recipes as you type.' });
  text.value = task;
  const list = h('div.att-models', h('span.muted.small', 'Finding strong coding models…'));
  const chosen = new Set();
  const extra = h('input.input', { placeholder: 'Another model, e.g. "glm 5" or "groq llama"' });
  const draw = (models) => {
    list.replaceChildren(...models.map((m) => {
      const b = h('button.btn.small' + (chosen.has(m.ref) ? '.on' : ''), { type: 'button', title: m.ref, onclick: () => { chosen.has(m.ref) ? chosen.delete(m.ref) : chosen.add(m.ref); b.classList.toggle('on', chosen.has(m.ref)); } }, m.label);
      return b;
    }));
  };
  api.get('/api/attempts/models').then((r) => { (r.models || []).forEach((m) => chosen.add(m.ref)); draw(r.models || []); }).catch(() => draw([]));
  sheet({
    title: 'Try with several models',
    body: h('div',
      h('p.section-desc', 'The same task is done by each model at the same time, in its own copy of this project. Compare the results in the Agents tab and keep the best; the project only changes when you pick one.'),
      field('Task', text),
      field('Models', list, 'Strong coders from different services are picked for you. Click to leave one out.'),
      field('Add a model (optional)', extra)),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h('button.btn.primary', { onclick: async (e) => {
        if (!text.value.trim()) return text.focus();
        const models = [...chosen];
        if (extra.value.trim()) models.push(extra.value.trim());
        if (models.length < 2) return toast('Pick at least two models', 'warn');
        e.currentTarget.disabled = true;
        try {
          await api.post('/api/projects/' + encodeURIComponent(slug) + '/attempts', { task: text.value.trim(), models });
          close();
          toast('Attempts started', { body: models.length + ' models are working on it. Follow them in the Agents tab.' });
          tab = 'agents';
          loadAttempts();
          renderTabs();
          showTab();
        } catch (err) {
          e.currentTarget.disabled = false;
          toast(err.message, 'error');
        }
      } }, icon('bolt'), 'Start'),
    ],
  });
}
bus.on('server:git', (d) => data && d.project === data.slug && refreshProject(true));
bus.on('project:term-count', (d) => data && d.slug === data.slug && renderTabs());

// ---------- the frame: resize edge, head, tabs ----------

function build() {
  pane.replaceChildren(
    h('div.pp-resize', { title: 'Drag to resize · double-click to widen', onpointerdown: startResize, ondblclick: toggleWide }),
    h(
      'div.pp-head',
      h('div.pp-strip', { id: 'ppStrip' }),
      h('div.pp-tools',
        h('button.icon-btn', { title: 'Open in…', 'data-pop-anchor': '', onclick: moreMenu }, icon('more')),
        h('button.icon-btn', { id: 'ppWide', title: 'Widen the panel', onclick: toggleWide }, icon(prefs.paneWide ? 'minimize' : 'maximize')),
        h('button.icon-btn', { title: 'Hide panel', onclick: toggleProjectPane }, icon('x'))),
    ),
    h('div.pp-tabs', { id: 'ppTabs', role: 'tablist' }),
    h('div.pp-bodies', { id: 'ppBodies' }),
  );
  bodies = {};
  built = new Set();
}

function renderStrip() {
  const el = pane && pane.querySelector('#ppStrip');
  if (!el || !data) return;
  const job = data.job;
  const [label, cls] = STATUS[job ? job.status : ''] || ['new', ''];
  const pct = job && job.steps_total ? Math.round((job.steps_done / job.steps_total) * 100) : 0;
  el.replaceChildren(
    ...[
      h('div.pp-name', h('span.pp-title', { title: data.path }, data.name), h('span.badge' + cls, job && job.status === 'running' && (job.approval || job.question) ? 'needs you' : label),
        job && job.model ? h('span.badge', { title: job.model }, shortModel(job.model)) : null,
        data.linked ? h('span.badge', { title: 'A folder of your own: ' + data.path }, 'linked') : null),
      h('div.pp-now', job && job.status === 'running' ? h('span.job-pulse') : null, h('span', job ? job.activity || '' : 'Tell the agent what to build.')),
      job && job.steps_total ? h('div.job-progress', h('div.jp-bar', h('i', { style: { width: pct + '%' } })), h('span.jp-text', `${job.steps_done}/${job.steps_total}`)) : null,
    ].filter(Boolean),
  );
}

function renderTabs() {
  const el = pane && pane.querySelector('#ppTabs');
  if (!el || !data) return;
  const job = data.job || {};
  const running = job.status === 'running';
  const count = {
    agents: running ? (job.subs_running ? h('span.count.live', String(job.subs_running + 1)) : h('span.job-pulse')) : null,
    preview: pv.errors ? h('span.count.err', String(pv.errors)) : null,
    files: h('span.count', String(data.tree.filter((f) => !f.dir).length)),
    changes: badges.changes ? h('span.count', String(badges.changes)) : null,
    terminal: termRunning(data.slug) ? h('span.count.live', String(termRunning(data.slug))) : null,
    github: null,
  };
  el.replaceChildren(
    ...TABS.map(([k, label, ic], i) =>
      h('button' + (tab === k ? '.on' : ''), { type: 'button', role: 'tab', 'aria-selected': String(tab === k), title: `${label} (Alt+${i + 1})`, onclick: () => setTab(k) }, icon(ic), h('span.pt-label', label), count[k]),
    ),
  );
}

function setTab(k) {
  if (!data) return;
  tab = k;
  if (!pane.classList.contains('open')) setOpen(true);
  renderTabs();
  showTab();
}

function showTab() {
  const wrap = pane && pane.querySelector('#ppBodies');
  if (!wrap || !data) return;
  if (!bodies[tab]) {
    bodies[tab] = h('div.pp-body.pb-' + tab);
    wrap.appendChild(bodies[tab]);
  }
  for (const [k, el] of Object.entries(bodies)) el.hidden = k !== tab;
  if (!built.has(tab)) fill(tab);
}

function fill(k) {
  const el = bodies[k];
  if (!el || !data) return;
  built.add(k);
  if (k === 'agents') {
    const keep = el.scrollTop;
    el.replaceChildren(...[attemptsNode(), agentsNode()].filter(Boolean));
    el.scrollTop = keep;
  } else if (k === 'preview') {
    if (!pv.els) previewMount(el);
  } else if (k === 'files') {
    const keep = el.scrollTop;
    el.replaceChildren(filesNode());
    el.scrollTop = selected ? 0 : keep;
  } else if (k === 'changes') renderChanges(el, ctx);
  else if (k === 'terminal') renderTerminal(el, ctx);
  else if (k === 'github') renderGithub(el, ctx);
}

function setWidth(w) {
  if (!pane || isMobile()) return;
  if (!w) {
    pane.style.width = '';
    return;
  }
  const room = ($('#view-chat').clientWidth || innerWidth) - 380;
  pane.style.width = Math.max(340, Math.min(Math.max(360, room), w)) + 'px';
}

function startResize(e) {
  if (isMobile() || e.button !== 0) return;
  e.preventDefault();
  const right = pane.getBoundingClientRect().right;
  pane.classList.add('resizing');
  document.body.classList.add('col-resizing');
  const move = (ev) => setWidth(Math.round(right - ev.clientX));
  const up = () => {
    removeEventListener('pointermove', move);
    removeEventListener('pointerup', up);
    pane.classList.remove('resizing');
    document.body.classList.remove('col-resizing');
    setPref('paneW', parseInt(pane.style.width, 10) || 0);
    setPref('paneWide', false);
    const b = $('#ppWide');
    if (b) b.replaceChildren(icon('maximize'));
  };
  addEventListener('pointermove', move);
  addEventListener('pointerup', up);
}

function toggleWide() {
  const wide = !prefs.paneWide;
  setPref('paneWide', wide);
  setWidth(wide ? 1e5 : prefs.paneW || 0);
  const b = $('#ppWide');
  if (b) {
    b.replaceChildren(icon(wide ? 'minimize' : 'maximize'));
    b.title = wide ? 'Narrow the panel' : 'Widen the panel';
  }
}

function moreMenu(e) {
  const open = (app, done) => () => api.post(`/api/projects/${data.slug}/open`, { app }).then(() => toast(done, { timeout: 1800 })).catch((err) => toast(err.message, 'error'));
  popover(e.currentTarget, [
    { label: 'Open in VS Code', icon: 'code', onClick: open('vscode', 'Opening VS Code…') },
    { label: 'Open a terminal window', icon: 'terminal', onClick: open('terminal', 'Opening a terminal…') },
    { label: 'Show the folder', icon: 'folder', onClick: open('folder', 'Opened the project folder on the PC') },
    { label: 'Copy the path', icon: 'copy', sub: data.path, onClick: () => copyText(data.path).then(() => toast('Path copied', { timeout: 1400 })) },
    data.linked ? 'sep' : null,
    data.linked ? { label: 'Remove from Scorpi', icon: 'x', sub: 'The folder and its files stay', danger: true, onClick: unlink } : null,
  ], { align: 'end' });
}

async function unlink() {
  if (!(await confirmSheet('Remove this project from Scorpi?', `${data.path} stays on the PC with all its files. Only Scorpi's link to it goes.`, { confirm: 'Remove', danger: true }))) return;
  try {
    await api.del(`/api/projects/${data.slug}/link`);
    navigate('/code');
  } catch (err) {
    toast(err.message, 'error');
  }
}

document.addEventListener('keydown', (e) => {
  if (!visible() || e.ctrlKey || e.metaKey) return;
  if (e.altKey && /^[1-6]$/.test(e.key)) {
    e.preventDefault();
    setTab(TABS[Number(e.key) - 1][0]);
  }
});

// ---------- Agents: the job, its plan, the helpers working in parallel, earlier runs ----------

function agentsNode() {
  const job = data.job;
  if (!job) {
    return h('div.pp-empty', icon('agent'), h('b', 'No agent yet'), h('span', 'Describe what to build or change in the chat. The coder starts in this folder; its plan, its helpers and what they do show up here.'));
  }
  const running = job.status === 'running';
  const plan = job.plan || [];
  const subs = [...(job.subs || [])].sort((a, b) => (a.status === 'running' ? 0 : 1) - (b.status === 'running' ? 0 : 1) || (a.started || 0) - (b.started || 0));
  const pct = job.steps_total ? Math.round((job.steps_done / job.steps_total) * 100) : 0;
  const nodes = [
    h(
      'div.pa-job' + (running ? '.running' : ''),
      h('div.pa-row', h('span.mk.' + (MARK[job.status] || 'pending')), h('b.pa-title', job.title || 'Job'), h('span.grow'),
        running ? h('button.btn.small.quiet', { title: 'Stop the agent', onclick: stopJob }, icon('stop'), 'Stop') : null),
      h('div.pa-now', running ? h('span.job-pulse') : null, h('span', job.activity || '')),
      running && job.says ? h('div.pa-says', '…' + job.says.replace(/\s+/g, ' ').slice(-220)) : null,
      h('div.pa-meta', [job.agent_name || 'Coder', job.model ? shortModel(job.model) : '', job.started ? (running ? dur(job.started) : fmtRelative(job.updated || job.started)) : '',
        job.tools ? job.tools + ' actions' : ''].filter(Boolean).join(' · ')),
      job.steps_total ? h('div.job-progress', h('div.jp-bar', h('i', { style: { width: pct + '%' } })), h('span.jp-text', `${job.steps_done}/${job.steps_total} steps`)) : null,
    ),
  ];
  if (job.approval && job.approval.plan) {
    // plan mode: the plan is in the chat to read and comment on; approve it here too
    nodes.push(h('div.pa-ask.plan', icon('route'), h('div.grow', h('b', 'Plan ready: '), job.approval.title || 'review it in the chat'),
      h('button.btn.small.primary', { onclick: () => decide(job.approval.id, 'allow') }, icon('check'), 'Approve & build'),
      h('button.btn.small.quiet', { title: 'Write what should change in the plan card in the chat', onclick: () => {
        const card = document.querySelector(`.plan-review[data-approval="${job.approval.id}"]`);
        if (card) { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); const note = card.querySelector('.plan-note'); if (note) note.focus(); }
      } }, 'Changes…')));
  } else if (job.approval) {
    nodes.push(h('div.pa-ask', icon('shield'), h('div.grow', h('b', 'Wants to: '), job.approval.title || job.approval.tool,
      job.approval.why ? h('div.muted.small', job.approval.why) : null),
      h('button.btn.small.primary', { onclick: () => decide(job.approval.id, 'allow') }, 'Allow'),
      h('button.btn.small.quiet', { onclick: () => decide(job.approval.id, 'deny') }, 'Deny')));
  }
  if (job.question) nodes.push(h('div.pa-ask', icon('chat'), h('div.grow', h('b', 'Asks: '), job.question.title || 'a question'), h('span.muted.small', 'Answer in the chat')));
  if (plan.length) {
    nodes.push(h('div.pc-sec', h('div.pa-h', 'Plan', h('span.count', `${job.steps_done}/${plan.length}`)),
      h('ol.pa-plan', plan.map((s) => h('li.' + s.status, h('span.mk.' + (MARK[s.status] || 'pending')), h('span', s.title))))));
  }
  if (subs.length) {
    const live = subs.filter((s) => s.status === 'running').length;
    nodes.push(h('div.pc-sec', h('div.pa-h', 'Helpers', h('span.count', live ? `${live} working` : `${subs.filter((s) => s.status === 'done').length}/${subs.length} done`)), h('div.pa-subs', subs.map(subNode))));
  } else if (running) {
    nodes.push(h('div.pc-sec', h('div.pa-h', 'Helpers'), h('div.muted.small', 'For bigger jobs the coder splits the work across helper agents that run at the same time. They show up here live.')));
  }
  const earlier = (data.jobs || []).filter((j) => j.id !== job.id);
  if (earlier.length) {
    nodes.push(h('div.pc-sec', h('div.pa-h', 'Earlier runs', h('span.count', String(earlier.length))),
      h('div.pa-runs', earlier.slice(0, 12).map((j) => h('details.pa-run',
        h('summary', h('span.mk.' + (MARK[j.status] || 'pending')), h('span.pa-run-t', j.title), h('span.pa-run-m', fmtRelative(j.updated || j.started))),
        h('div.pa-run-b', (j.result || j.activity || '').slice(0, 800) || 'No report.'))))));
  }
  return h('div.pa', nodes);
}

function subNode(s) {
  const id = s.id || s.name;
  const running = s.status === 'running';
  const box = h(
    'details.pa-sub.' + (s.status || 'running'),
    { open: openSubs.has(id), ontoggle: () => (box.open ? openSubs.add(id) : openSubs.delete(id)) },
    h('summary',
      h('span.mk.' + (MARK[s.status] || 'active')),
      h('span.ps-name', s.name || 'Helper'),
      s.model ? h('span.badge', { title: s.model }, shortModel(s.model)) : null,
      h('span.ps-now', running ? s.activity || 'Working' : (s.result || s.activity || '').split('\n')[0]),
      h('span.ps-meta', [s.tools ? s.tools + ' acts' : '', s.started ? dur(s.started, s.ended) : ''].filter(Boolean).join(' · '))),
    h('div.ps-body',
      s.task ? h('div.ps-task', h('span.muted', 'Task  '), s.task) : null,
      running ? h('div.ps-live', h('span.job-pulse'), s.activity || 'Working') : null,
      !running && s.result ? h('div.ps-result', s.result.slice(0, 1500)) : null),
  );
  return box;
}

function stopJob(e) {
  const job = data && data.job;
  if (!job) return;
  e.currentTarget.disabled = true;
  api.post(`/api/agent-jobs/${job.id}/stop`).catch((err) => toast(err.message, 'error'));
}

function decide(id, decision) {
  api.post('/api/approvals/' + id, { decision }).catch((err) => toast(err.message, 'error'));
}

// ---------- Preview: a small browser with an address bar, device widths and the page's console ----------

function previewMount(body) {
  const btn = (ic, title, fn) => h('button.icon-btn', { type: 'button', title, onclick: fn }, icon(ic));
  const addr = h('input.pv-addr', { spellcheck: false, autocomplete: 'off', 'aria-label': 'Address', onkeydown: (e) => e.key === 'Enter' && go(addr.value), onfocus: () => addr.select() });
  const devices = h('div.pv-devices', DEVICES.map(([k, ic, label]) => h('button.icon-btn' + (pv.device === k ? '.on' : ''), { type: 'button', title: label, dataset: { d: k }, onclick: () => { pv.device = k; applyDevice(); } }, icon(ic))));
  const consoleBtn = h('button.btn.small.quiet.pv-cbtn', { type: 'button', title: 'The page\'s console: logs, warnings and errors', onclick: () => { pv.console = !pv.console; renderConsole(); } }, icon('terminal'), h('span.pt-label', 'Console'), h('span.count'));
  const ext = h('a.icon-btn', { title: 'Open in a new tab', target: '_blank', rel: 'noopener' }, icon('external'));
  const bar = h('div.pp-subbar.pv-bar', btn('left', 'Back', () => navFrame('back')), btn('right', 'Forward', () => navFrame('forward')), btn('refresh', 'Reload', () => loadPreview()), addr, devices, consoleBtn, ext);
  const stage = h('div.pv-stage', h('div.pv-device'));
  const cons = h('div.pv-console');
  body.replaceChildren(bar, stage, cons);
  pv.els = { addr, devices, consoleBtn, ext, stage, cons };
  loadPreview();
}

function frameSrc() {
  if (pv.url) return pv.url;
  if (!data.has_index && !pv.path) return '';
  return data.preview + pv.path.replace(/^\/+/, '');
}

function loadPreview() {
  if (!pv.els || !data) return;
  pv.logs = [];
  pv.errors = 0;
  renderConsole();
  renderTabs();
  const device = pv.els.stage.firstChild;
  const src = frameSrc();
  if (!src) {
    pv.frame = null;
    device.replaceChildren(h('div.pp-empty', icon('globe'), h('b', 'No preview yet'),
      h('span', 'A web page shows here once the project has an index.html. For an app with a dev server, start it in Terminal and open its address (e.g. localhost:5173) above.')));
    updateAddr();
    return;
  }
  const external = !!pv.url;
  let sameOrigin = false;
  try {
    sameOrigin = new URL(src, location.href).origin === location.origin;
  } catch {
    /* typed address */
  }
  pv.frame = h('iframe.pp-frame', {
    src: external ? src : src + (src.includes('?') ? '&' : '?') + 't=' + Date.now(),
    title: 'Preview of ' + data.name,
    // the project's own pages run isolated from Scorpi; a dev server on another port keeps its own origin
    sandbox: 'allow-scripts allow-forms allow-modals allow-popups allow-downloads' + (external && !sameOrigin ? ' allow-same-origin' : ''),
  });
  device.replaceChildren(pv.frame);
  applyDevice();
  updateAddr();
}

function go(value) {
  const v = String(value || '').trim();
  if (/^(https?:\/\/|localhost\b|127\.0\.0\.1|\[::1\]|[\w.-]+:\d{2,5}\b)/i.test(v)) {
    pv.url = /^https?:\/\//i.test(v) ? v : 'http://' + v;
    pv.path = '';
  } else {
    pv.url = '';
    pv.path = v.replace(/^\/+/, '');
  }
  loadPreview();
}

function navFrame(dir) {
  if (pv.frame && pv.frame.contentWindow) pv.frame.contentWindow.postMessage({ scorpiNav: dir }, '*');
}

function updateAddr() {
  if (!pv.els) return;
  const shown = pv.url || '/' + pv.path;
  if (document.activeElement !== pv.els.addr) pv.els.addr.value = pv.url || (data.has_index || pv.path ? shown : '');
  pv.els.addr.placeholder = data.has_index ? '/index.html' : 'localhost:5173 or a page of the project';
  const src = frameSrc();
  pv.els.ext.href = src || '#';
  pv.els.ext.classList.toggle('hidden', !src);
}

function applyDevice() {
  if (!pv.els) return;
  const [, , , width] = DEVICES.find(([k]) => k === pv.device) || DEVICES[0];
  const dev = pv.els.stage.firstChild;
  dev.style.width = width ? width + 'px' : '';
  pv.els.stage.classList.toggle('framed', !!width);
  pv.els.devices.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.d === pv.device));
}

addEventListener('message', (e) => {
  const d = e.data;
  if (!d || !d.scorpiPreview || !pv.frame || e.source !== pv.frame.contentWindow) return;
  if (d.kind === 'nav') {
    const prefix = new URL(data.preview, location.href).pathname;
    const path = String(d.text || '');
    pv.path = (path.startsWith(prefix) ? path.slice(prefix.length) : path.replace(/^\//, '')).replace(/([?&])t=\d+&?/, '$1').replace(/[?&]$/, '');
    updateAddr();
    return;
  }
  pv.logs.push({ kind: d.kind, text: d.text });
  if (pv.logs.length > 400) pv.logs.shift();
  if (d.kind === 'error') {
    pv.errors += 1;
    renderTabs();
  }
  renderConsole();
});

function renderConsole() {
  if (!pv.els) return;
  const { cons, consoleBtn } = pv.els;
  consoleBtn.querySelector('.count').textContent = pv.errors ? String(pv.errors) : pv.logs.length ? String(pv.logs.length) : '';
  consoleBtn.classList.toggle('err', pv.errors > 0);
  consoleBtn.classList.toggle('on', pv.console);
  cons.classList.toggle('open', pv.console);
  if (!pv.console) return;
  const logs = h('div.pv-logs', pv.logs.length
    ? pv.logs.map((l) => h('div.pv-log.' + l.kind, h('span.pv-lk', l.kind === 'error' ? '[!]' : l.kind === 'warn' ? '[~]' : ' › '), h('span.pv-lt', l.text)))
    : h('div.muted.small', pv.frame ? 'Nothing logged yet. The page\'s console.log, warnings and errors show up here.' : 'No page is open.'));
  cons.replaceChildren(
    h('div.pv-chead', h('b', 'Console'), h('span.muted', pv.errors ? `${pv.errors} error${pv.errors === 1 ? '' : 's'}` : `${pv.logs.length} message${pv.logs.length === 1 ? '' : 's'}`), h('span.grow'),
      pv.errors ? h('button.btn.small.quiet', { title: 'Send the errors to the agent and ask it to fix them', onclick: askFixErrors }, icon('wand'), 'Fix errors') : null,
      h('button.icon-btn', { title: 'Clear', onclick: () => { pv.logs = []; pv.errors = 0; renderConsole(); renderTabs(); } }, icon('trash')),
      h('button.icon-btn', { title: 'Close', onclick: () => { pv.console = false; renderConsole(); } }, icon('x'))),
    logs,
  );
  logs.scrollTop = logs.scrollHeight;
}

function askFixErrors() {
  const errors = [...new Set(pv.logs.filter((l) => l.kind === 'error').map((l) => l.text))].slice(-20);
  ctx.ask({
    text: `The project's page (${pv.url || '/' + (pv.path || 'index.html')}) shows these errors in the browser console:\n\n\`\`\`\n${errors.join('\n').slice(0, 6000)}\n\`\`\`\n\nFind the causes, fix them, and check that the page loads without errors.`,
    display: `Fix ${errors.length} console error${errors.length === 1 ? '' : 's'}`,
  });
}

// ---------- Files ----------

function filesNode() {
  const files = data.tree;
  if (!files.length) return h('div.pp-empty', icon('folder'), h('b', 'Empty so far'), h('span', 'Files the agent writes show up here live.'));
  const open = selected && files.find((f) => f.path === selected);
  if (open) return h('div.pp-files', viewerNode(open));
  const list = h('div.pp-tree');
  const search = h('input.pp-filter', {
    placeholder: `Filter ${files.filter((f) => !f.dir).length} files`, value: filter, spellcheck: false,
    oninput: debounce(() => {
      filter = search.value;
      list.replaceChildren(...treeRows());
    }, 120),
  });
  list.append(...treeRows());
  return h('div.pp-files', h('div.pp-subbar', icon('search', 'pf-ic'), search), list);
}

function treeRows() {
  const q = filter.trim().toLowerCase();
  const rows = [];
  for (const f of data.tree) {
    const parts = f.path.split('/');
    const parent = parts.slice(0, -1);
    if (q) {
      if (f.dir || !f.path.toLowerCase().includes(q)) continue;
    } else if (parent.some((_, i) => collapsed.has(parent.slice(0, i + 1).join('/')))) continue; // inside a collapsed folder
    const name = q ? f.path : parts[parts.length - 1];
    const depth = q ? 0 : parent.length;
    const row = h(
      'button.pp-file' + (f.dir ? '.dir' : '') + (f.path === selected ? '.sel' : ''),
      { type: 'button', style: { paddingLeft: 10 + depth * 14 + 'px' }, title: f.path },
      f.dir ? icon(collapsed.has(f.path) ? 'right' : 'down', 'chev') : null,
      icon(f.dir ? 'folder' : IMAGES.includes(extOf(name)) ? 'image' : 'file'),
      h('span.pf-name', name),
      f.dir ? null : h('span.pf-meta', fmtSize(f.size)),
    );
    row.addEventListener('click', () => {
      if (f.dir) {
        if (collapsed.has(f.path)) collapsed.delete(f.path);
        else collapsed.add(f.path);
      } else selected = selected === f.path ? '' : f.path;
      fill('files');
    });
    rows.push(row);
  }
  if (!rows.length) rows.push(h('div.muted.small.pp-none', 'No file matches.'));
  return rows;
}

function viewerNode(f) {
  if (!f) return null;
  const sep = data.path.includes('\\') ? '\\' : '/';
  const full = data.path.replace(/[\\/]+$/, '') + sep + f.path.replace(/\//g, sep);
  const ext = extOf(f.path);
  const head = h('div.pv-head',
    h('button.icon-btn', { title: 'Back to the files', onclick: () => { selected = ''; fill('files'); } }, icon('left')),
    h('span.mono', f.path), h('span.muted', fmtSize(f.size) + ' · ' + fmtRelative(f.mtime)), h('span.grow'),
    h('button.icon-btn', { title: 'Ask the agent about this file', onclick: () => bus.emit('compose:prefill', { text: `In ${f.path}: ` }) }, icon('chat')),
    h('button.icon-btn', { title: 'Open full screen', onclick: () => openViewer({ path: full, size: f.size }) }, icon('eye')),
    h('a.icon-btn', { title: 'Download', href: fileUrl(full, true) }, icon('download')));
  const box = h('div.pp-viewer', head);
  if (IMAGES.includes(ext)) {
    box.appendChild(h('div.pv-img', h('img', { src: fileUrl(full), alt: f.path, onclick: () => openViewer({ path: full, kind: 'image' }) })));
    return box;
  }
  if (VISUAL.includes(kindOf(f.path))) {
    box.appendChild(h('div.pv-rich', previewBody({ path: full, size: f.size })));
    return box;
  }
  const code = h('code' + (LANGS[ext] ? '.language-' + LANGS[ext] : ''), 'Loading…');
  const md = h('div.md.pv-code', h('pre', code));
  box.appendChild(md);
  api.get('/api/file/text?path=' + encodeURIComponent(full)).then((res) => {
    code.textContent = res.kind === 'binary' ? '(binary file)' : res.text.length > 200000 ? res.text.slice(0, 200000) + '\n…' : res.text;
    highlight(md);
  }).catch((e) => (code.textContent = e.message));
  return box;
}
