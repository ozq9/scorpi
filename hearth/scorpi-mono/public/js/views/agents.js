// Agents: the dedicated team (coder, researcher...) and custom agents, their live jobs, and controls to steer or stop them.

import { api } from '../lib/api.js';
import { $$, clear, fmtRelative, h, icon } from '../lib/dom.js';
import { renderMarkdown } from '../lib/md.js';
import { confirmSheet, emptyState, field, seg, selectEl, sheet, switchEl, toast } from '../lib/ui.js';
import { bus } from '../store.js';
import { navigate } from '../router.js';

let root;
const WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
const GROUPS = [
  ['files', 'Files'], ['docs', 'Documents'], ['code', 'Code'], ['web', 'Web'], ['data', 'Data'], ['media', 'Media'],
  ['computer', 'Computer use'], ['system', 'System'], ['maps', 'Maps'], ['agenda', 'Agenda'], ['records', 'Records'], ['knowledge', 'Knowledge'],
];
const STATUS_BADGE = { running: '.warn', waiting: '.warn', done: '.ok', failed: '.err', stopped: '', interrupted: '' };
const EXAMPLES = [
  'Build me a landing page for my bakery, with opening hours and a contact form',
  'Create an agent that reminds me every day at 13:00 to take a break',
  'Create an agent that checks the news on AI every weekday at 8 and sends me 3 headlines',
];

const jobs = new Map(); // live job state by id, fed by the server's agent_job events
const askingJobs = new Set(); // a card is waiting for the job details
const missingJobs = new Set(); // the server no longer has that job (an old chat keeps its card)

// ---------- job card (used here and in chat) ----------

const duration = (from, to) => {
  const s = Math.max(0, Math.round((to || Date.now() / 1000) - from));
  if (s < 60) return s + ' s';
  if (s < 3600) return Math.round(s / 60) + ' min';
  return (s / 3600).toFixed(1).replace('.0', '') + ' h';
};

export const shortModel = (ref) => String(ref || '').split('/').pop();

// Helper agents a job runs in parallel: one row each with what it is doing right now.
export function helpersBlock(job, { compact = false, limit = compact ? 3 : 8 } = {}) {
  const subs = job.subs || [];
  if (!subs.length) return null;
  const order = { running: 0, failed: 1, done: 2, stopped: 3 };
  const rows = [...subs].sort((a, b) => (order[a.status] ?? 4) - (order[b.status] ?? 4));
  const shown = rows.slice(0, limit);
  const working = subs.filter((s) => s.status === 'running').length;
  const done = subs.filter((s) => s.status === 'done').length;
  return h(
    'div.job-helpers' + (compact ? '.compact' : ''),
    h('div.jh-head', icon('agent'), h('span', working ? `${working} helper${working === 1 ? '' : 's'} working in parallel` : `${subs.length} helper${subs.length === 1 ? '' : 's'}`), h('span.jp-text', `${done}/${subs.length} done`)),
    shown.map((s) =>
      h(
        'div.jh-row.' + (s.status || 'running'),
        { title: s.task || '' },
        s.status === 'running' ? h('span.job-pulse') : h('span.jh-mark', icon(s.status === 'done' ? 'check' : s.status === 'failed' ? 'x' : 'clock')),
        h('span.jh-name', s.name || 'Helper'),
        h('span.jh-now', s.status === 'running' ? s.activity || 'Working' : s.result || s.activity || ''),
        h('span.jh-meta', (s.tools ? s.tools + ' · ' : '') + (s.started ? duration(s.started, s.ended) : '')),
      ),
    ),
    rows.length > shown.length ? h('div.jh-more', `+${rows.length - shown.length} more`) : null,
  );
}

export function jobCard(job, { compact = false } = {}) {
  const running = job.status === 'running';
  const pct = job.steps_total ? Math.round((job.steps_done / job.steps_total) * 100) : 0;
  const card = h(
    'div.card.job' + (running ? '.running' : '') + (compact ? '.compact' : ''),
    { dataset: { job: job.id, compact: compact ? '1' : '' } },
    h(
      'div.job-head',
      h('span.job-avatar', icon(job.agent_icon || 'agent')),
      h('div.job-names', h('div.job-title', job.title || 'Job'), h('div.job-agent', job.agent_name || 'Agent', job.started ? ' · ' + (running ? 'for ' + duration(job.started) : fmtRelative(job.updated || job.started)) : '')),
      job.model ? h('button.badge.job-model', { title: 'Model: ' + job.model + ' (click to change)', onclick: () => modelSheet(job) }, shortModel(job.model)) : null,
      h('span.badge' + (STATUS_BADGE[job.status] ?? ''), job.status === 'running' ? (job.approval || job.question ? 'needs you' : 'working') : job.status === 'waiting' ? 'paused' : job.status || 'queued'),
    ),
    h('div.job-now', running ? h('span.job-pulse') : icon(job.status === 'done' ? 'check' : job.status === 'failed' ? 'x' : 'clock'), h('span', job.activity || '')),
    running && job.says ? h('div.job-says', '…' + job.says.replace(/\s+/g, ' ').slice(-180)) : null,
    job.steps_total
      ? h('div.job-progress', h('div.jp-bar', h('i', { style: { width: pct + '%' } })), h('span.jp-text', `${job.steps_done}/${job.steps_total} steps · ${job.tools} actions`))
      : job.tools ? h('div.job-progress', h('span.jp-text', `${job.tools} actions`)) : null,
    job.plan && job.plan.length && !compact
      ? h('ol.job-plan', job.plan.map((s) => h('li.' + s.status, h('span.jp-mark', s.status === 'done' ? icon('check') : s.status === 'failed' ? icon('x') : null), s.title)))
      : null,
    job.plan && job.plan.length && compact && running
      ? (() => {
          const now = job.plan.find((s) => s.status === 'active') || job.plan.find((s) => s.status === 'pending');
          return now ? h('div.job-step', h('span.muted', 'Step: '), now.title) : null;
        })()
      : null,
    running || !compact ? helpersBlock(job, { compact }) : null,
    job.approval && job.approval.plan
      ? planBlock(job)
      : job.approval
      ? h(
          'div.job-approval',
          { dataset: { approval: job.approval.id } }, // tells the app not to toast an approval that is already on screen
          icon('shield'),
          h('div.ja-text', h('b', job.agent_name + ' wants to: '), job.approval.title, approvalDetail(job.approval)),
          h('button.btn.small.accent', { onclick: () => decide(job.approval.id, 'allow') }, 'Allow'),
          h('button.btn.small.quiet', { onclick: () => decide(job.approval.id, 'deny') }, 'Deny'),
        )
      : null,
    job.question ? questionBlock(job) : null,
    !running && job.result && !compact ? h('div.job-result', job.result.split('\n').filter((l) => l.trim()).slice(0, 6).join('\n')) : null,
    !running && job.error && job.status !== 'waiting' ? h('div.job-result.err', job.error) : null,
    h(
      'div.job-foot',
      job.conv_id ? h('a.btn.small', { href: '/c/' + job.conv_id, 'data-link': '' }, icon('eye'), 'Watch work') : null,
      h('button.btn.small.quiet', { onclick: () => messageSheet(job) }, icon('chat'), running ? 'Message' : 'Continue'),
      job.status === 'waiting' ? h('button.btn.small.accent', { title: 'Try again now instead of waiting for the limits to reset', onclick: () => resumeJob(job) }, icon('play'), 'Continue now') : null,
      running || job.status === 'waiting' ? h('button.btn.small.danger', { onclick: () => stopJob(job) }, icon('stop'), 'Stop') : null,
      job.workdir && !compact ? h('button.btn.small.quiet', { title: job.workdir, onclick: () => api.post('/api/file/open', { path: job.workdir }).then(() => toast('Opened the project folder on the PC')).catch((e) => toast(e.message, 'error')) }, icon('folder'), 'Folder') : null,
    ),
  );
  return card;
}

// The plan an agent proposes before building (plan mode): read it, approve, or say what to change.
function planBlock(job) {
  const a = job.approval;
  const body = h('div.jp-plan.md');
  body.innerHTML = renderMarkdown(a.plan);
  return h(
    'div.job-approval.plan',
    { dataset: { approval: a.id } },
    icon('route'),
    h('div.ja-text', h('b', job.agent_name + ' proposes a plan: '), a.title && a.title !== 'Review the plan' ? a.title : 'review it before anything changes'),
    h('details.ja-plan', h('summary', 'Read the plan'), body),
    h('button.btn.small.accent', { onclick: () => decide(a.id, 'allow') }, icon('check'), 'Approve & build'),
    h('button.btn.small.quiet', { onclick: () => planChanges(job) }, 'Changes…'),
  );
}

function planChanges(job) {
  const text = h('textarea.input', { rows: 4, placeholder: 'e.g. Skip step 3, and use SQLite instead of JSON files.' });
  sheet({
    title: 'Change ' + job.agent_name + "'s plan",
    body: h('div', h('p.section-desc', job.agent_name + ' revises the plan with your notes and shows it again. Nothing has changed yet.'), field('What should change', text)),
    foot: (close) => [
      h('button.btn.quiet', { onclick: async () => { close(); await decide(job.approval.id, 'deny', ''); } }, 'Reject the plan'),
      h('button.btn.primary', { onclick: async () => { if (!text.value.trim()) return text.focus(); close(); await decide(job.approval.id, 'deny', text.value.trim()); } }, 'Request changes'),
    ],
  });
}

function questionBlock(job) {
  const q = job.question;
  const f = (q.fields || [])[0] || { options: [] };
  return h(
    'div.job-approval',
    { dataset: { question: q.id } },
    icon('chat'),
    h('div.ja-text', h('b', job.agent_name + ' asks: '), f.description || f.title || q.title),
    ...(q.fields || []).length === 1 ? f.options.slice(0, 4).map((o) => h('button.btn.small.accent', { title: o.description || '', onclick: () => answer(q.id, { [f.key]: o.value }) }, o.label)) : [],
    h('button.btn.small.quiet', { onclick: () => messageSheet(job) }, (q.fields || []).length === 1 && f.options.length ? 'Other…' : 'Answer'),
  );
}

async function answer(id, answers) {
  try {
    await api.post('/api/questions/' + id, { answers });
  } catch (e) {
    toast(e.message, 'error');
  }
}

function approvalDetail(a) {
  const args = a.args || {};
  const text = args.command || args.code || args.path || args.url || '';
  return [text ? h('code.ja-arg', String(text).slice(0, 160)) : null, a.why ? h('span.ja-why', a.why) : null];
}

async function decide(id, decision, note = '') {
  try {
    await api.post('/api/approvals/' + id, { decision, note });
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function resumeJob(job) {
  await api.post(`/api/agent-jobs/${job.id}/resume`).then(() => toast(job.agent_name + ' carries on')).catch((e) => toast(e.message, 'error'));
}

async function stopJob(job) {
  await api.post(`/api/agent-jobs/${job.id}/stop`).catch((e) => toast(e.message, 'error'));
  toast('Stopping ' + job.agent_name);
}

function messageSheet(job) {
  const running = job.status === 'running';
  const asks = running && job.question ? ((job.question.fields || [])[0] || {}).description || job.question.title : '';
  const text = h('textarea.input', { rows: 4, placeholder: asks ? 'Your answer' : running ? 'e.g. Use blue as the main colour, and add a gallery.' : 'e.g. Now add a contact page.' });
  sheet({
    title: (asks ? 'Answer ' : running ? 'Message ' : 'Continue with ') + job.agent_name,
    body: h('div', h('p.section-desc', asks ? `${job.agent_name} asks: ${asks}` : running ? `${job.agent_name} reads this right away, while it works.` : `${job.agent_name} picks “${job.title}” up again with your message.`), field(asks ? 'Answer' : 'Message', text)),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h('button.btn.primary', {
        onclick: async () => {
          if (!text.value.trim()) return text.focus();
          try {
            const res = await api.post(`/api/agent-jobs/${job.id}/message`, { text: text.value.trim() });
            close();
            toast(res.how === 'answered' ? 'Answer sent' : res.how === 'delivered' ? 'Message delivered' : job.agent_name + ' is working again');
          } catch (e) {
            toast(e.message, 'error');
          }
        },
      }, 'Send'),
    ],
  });
  setTimeout(() => text.focus(), 80);
}

let modelList = null; // [[value, label]] of chat-capable models, loaded once per page
const loadModels = () =>
  (modelList ||= api.get('/api/models').then((res) => (res.models || []).filter((m) => !/embed/i.test(m.name)).map((m) => [m.name, (m.provider_name ? m.provider_name + ' · ' : '') + (m.id || m.name)])).catch(() => {
    modelList = null;
    return [];
  }));

function modelField(current, onPick, emptyLabel = 'Default model') {
  const box = h('div', selectEl([['', emptyLabel]], '', () => {}));
  loadModels().then((list) => {
    const opts = [['', emptyLabel], ...list];
    if (current && !opts.some((o) => o[0] === current)) opts.push([current, current]);
    clear(box);
    box.appendChild(selectEl(opts, current || '', onPick));
  });
  return box;
}

// Switch a job to another model (a running job from its next step). Scorpi does the same when asked in chat.
function modelSheet(job) {
  let pick = job.model_pinned ? job.model : '';
  sheet({
    title: 'Model for ' + (job.title || 'this job'),
    body: h('div', h('p.section-desc', `Now: ${job.model || 'default'}. ${job.status === 'running' ? job.agent_name + ' switches from its next step.' : 'Used when the job continues.'} You can also just say it in chat, e.g. “tell the coder to use deepseek flash 4.1”.`), field('Model', modelField(pick, (v) => (pick = v), 'Agent’s default'))),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h('button.btn.primary', {
        onclick: async () => {
          try {
            const res = await api.post(`/api/agent-jobs/${job.id}/model`, { model: pick });
            close();
            onJob(res);
            toast((res.switched ? 'Switched to ' : 'Will continue with ') + (shortModel(res.model) || 'the default model'));
          } catch (e) {
            toast(e.message, 'error');
          }
        },
      }, 'Use this model'),
    ],
  });
}

// Cards in chat start from the tool's small display and fill in from the live job.
export function jobPlaceholder(d) {
  const known = jobs.get(d.id);
  const card = jobCard(known || { id: d.id, title: d.title, agent_name: d.name, agent_icon: d.icon, status: 'running', activity: known === undefined && missingJobs.has(d.id) ? 'No longer available' : 'Starting…', conv_id: d.conv_id }, { compact: true });
  if (!known && !missingJobs.has(d.id) && !askingJobs.has(d.id)) {
    askingJobs.add(d.id);
    api.get('/api/agent-jobs/' + d.id)
      .then((job) => onJob(job))
      .catch((err) => {
        if (err && err.status === 404) {
          missingJobs.add(d.id);
          $$(`[data-job="${d.id}"]`).forEach((el) => el.replaceWith(
            h('div.card.chip-card.job-gone', { dataset: { job: d.id } }, icon('agent'), h('span', h('span.muted', 'Agent job · '), 'details no longer stored')),
          ));
        }
      })
      .finally(() => askingJobs.delete(d.id));
  }
  return card;
}

export function agentCard(d) {
  return h(
    'div.card.chip-card',
    icon(d.icon || 'agent'),
    h('span', { style: { flex: 1 } }, 'New agent ', h('b', d.name), h('span.muted', ' · ' + (d.describe || 'on demand') + (d.next ? ', next ' + fmtRelative(d.next) : ''))),
    h('a.btn.small', { href: '/agents', 'data-link': '' }, 'Agents'),
  );
}

function onJob(job) {
  jobs.set(job.id, job);
  $$(`[data-job="${job.id}"]`).forEach((el) => el.replaceWith(jobCard(job, { compact: el.dataset.compact === '1' })));
  bus.emit('agents:count', runningCount());
  if (root && root.classList.contains('active')) {
    const live = root.querySelector('.agents-live');
    if (live && !live.querySelector(`[data-job="${job.id}"]`) && job.status === 'running') renderAgents(root);
    else if (live && job.status !== 'running') scheduleRefresh();
  }
}

export const runningCount = () => [...jobs.values()].filter((j) => j.status === 'running').length;

let refreshTimer = null;
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => root && root.classList.contains('active') && renderAgents(root), 1500);
}

bus.on('server:agent_job', onJob);
bus.on('server:agents', () => root && root.classList.contains('active') && scheduleRefresh());
bus.on('server:approval_done', () => {
  // a decision taken elsewhere (chat, another device) clears the box here too
  for (const job of jobs.values()) if (job.approval) api.get('/api/agent-jobs/' + job.id).then(onJob).catch(() => {});
});

export async function loadJobCount() {
  try {
    const data = await api.get('/api/agents?limit=20');
    data.jobs.forEach((j) => jobs.set(j.id, j));
    bus.emit('agents:count', runningCount());
  } catch {
    /* offline */
  }
}

// ---------- the Agents view ----------

export async function renderAgents(el) {
  root = el;
  let data;
  try {
    data = await api.get('/api/agents');
  } catch (err) {
    clear(root);
    root.appendChild(h('div.page', h('div.page-inner', emptyState('Agents unavailable', err.message))));
    return;
  }
  data.jobs.forEach((j) => jobs.set(j.id, { ...(jobs.get(j.id) || {}), ...j }));
  const live = data.jobs.filter((j) => j.status === 'running');
  const past = data.jobs.filter((j) => j.status !== 'running');
  const scrollTop = root.querySelector('.page') ? root.querySelector('.page').scrollTop : 0;
  clear(root);
  const page = h(
    'div.page',
    h(
      'div.page-inner',
      h(
        'div.page-head',
        h('div.ph-text', h('div.page-kicker', 'Agents'), h('h1.page-title', 'A team that works ', h('em', 'while you talk')), h('p.page-sub', 'Ask in chat and Scorpi hands bigger jobs to a dedicated agent, follows its progress and reports back. Create your own agents in chat or here: reminders, daily checks, chores.')),
        h('div.row', h('button.btn.primary', { onclick: () => editor() }, icon('plus'), 'New agent')),
      ),
      h(
        'div.section',
        h('h3.section-title', 'Working now', h('span.count', live.length || ''), h('span.rule')),
        h(
          'div.agents-live',
          live.length
            ? live.map((j) => jobCard(j))
            : h(
                'div.panel.agents-idle',
                h('div.ai-text', h('b', 'Everyone is free.'), ' Try asking in chat:'),
                h('div.ai-examples', EXAMPLES.map((ex) => h('button.ai-example', { onclick: () => { navigate('/'); bus.emit('chat:prefill', ex); } }, icon('arrow-right'), ex))),
              ),
        ),
      ),
      h('div.section', h('h3.section-title', 'The team', h('span.count', data.agents.length), h('span.rule')), h('div.cards-grid', data.agents.map((a, i) => agentTile(a, i)))),
      h(
        'div.section',
        h('h3.section-title', 'Recent jobs', h('span.count', past.length || ''), h('span.rule'), past.length ? h('button.btn.quiet.small', { onclick: clearFinished }, icon('trash'), 'Clear finished') : null),
        past.length ? h('div.panel', past.slice(0, 30).map(jobRow)) : h('div.panel', emptyState('No jobs yet', 'Finished work shows up here.')),
      ),
    ),
  );
  root.appendChild(page);
  page.scrollTop = scrollTop;
}

function agentTile(a, i) {
  const scheduled = !!a.schedule;
  return h(
    'div.tile.agent-tile' + (a.working ? '.busy' : ''),
    { style: { animationDelay: i * 35 + 'ms' } },
    h(
      'div.t-kicker',
      icon(a.icon || 'agent'),
      a.kind === 'builtin' ? 'Built-in' : a.action === 'remind' ? 'Reminder' : 'Custom',
      scheduled ? h('span.k-switch', { title: a.enabled ? 'Pause schedule' : 'Resume schedule' }, switchEl(a.enabled, (on) => api.patch('/api/agents/' + a.id, { enabled: on ? 1 : 0 }).then(() => toast(a.name + (on ? ' resumed' : ' paused'))))) : null,
    ),
    h('div.t-name', a.name),
    h('div.t-desc', a.action === 'remind' ? '“' + a.task + '”' : a.description || 'No description'),
    h(
      'div.t-foot',
      a.working ? h('span.badge.warn', 'working' + (a.working > 1 ? ' ×' + a.working : '')) : h('span.badge', scheduled && !a.enabled ? 'paused' : 'ready'),
      scheduled ? h('span.badge', a.describe) : null,
      scheduled && a.enabled && a.next_run ? h('span.badge', 'next ' + fmtRelative(a.next_run)) : null,
      a.model ? h('span.badge', a.model.split('/').pop()) : null,
      a.auto_approve ? h('span.badge.warn', { title: 'Runs risky tools without asking' }, 'autonomous') : null,
    ),
    h(
      'div.t-foot',
      a.action !== 'remind' ? h('button.btn.small', { onclick: () => assignSheet(a) }, icon('play'), 'Give a job') : null,
      scheduled ? h('button.btn.small.quiet', { onclick: () => runNow(a) }, icon('bolt'), a.action === 'remind' ? 'Test' : 'Run now') : null,
      h('span.grow', { style: { flex: 1 } }),
      h('button.icon-btn', { title: 'Edit', onclick: () => editor(a) }, icon('edit')),
      a.kind !== 'builtin' ? h('button.icon-btn', { title: 'Delete', onclick: async () => (await confirmSheet('Delete ' + a.name + '?', 'Its schedule stops. Finished work stays in the workspace.', { confirm: 'Delete', danger: true })) && api.del('/api/agents/' + a.id).then(() => renderAgents(root)) }, icon('trash')) : null,
    ),
  );
}

function jobRow(j) {
  return h(
    'div.list-row',
    h('span.badge' + (STATUS_BADGE[j.status] ?? ''), j.status),
    h('div.lr-main', h('div.lr-title', h('b', j.agent_name), ' · ', j.title), h('div.lr-sub', (j.result || j.activity || '').split('\n').find((l) => l.trim()) || '')),
    h('span.lr-when.muted', { style: { fontSize: '12px', whiteSpace: 'nowrap' } }, fmtRelative(j.updated || j.started)),
    h(
      'div.lr-actions',
      j.conv_id ? h('button.btn.quiet.small', { onclick: () => messageSheet(j) }, 'Continue') : null,
      j.conv_id ? h('a.btn.small', { href: '/c/' + j.conv_id, 'data-link': '' }, 'Open') : null,
      h('button.icon-btn', { title: 'Remove from list', onclick: () => removeJobs(j.id) }, icon('trash')),
    ),
  );
}

async function removeJobs(target) {
  try {
    await api.del('/api/agent-jobs/' + target);
    if (target !== 'finished') jobs.delete(target);
    else for (const [id, j] of jobs) if (j.status !== 'running') jobs.delete(id);
    toast(target === 'finished' ? 'Cleared finished jobs' : 'Job removed');
    renderAgents(root);
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function clearFinished() {
  if (await confirmSheet('Clear all finished jobs?', 'Running jobs stay. Conversations and project files are kept.', { confirm: 'Clear', danger: true })) removeJobs('finished');
}

async function runNow(a) {
  try {
    const res = await api.post(`/api/agents/${a.id}/run`);
    toast(res.reminded ? 'Reminder sent' : a.name + ' is running');
  } catch (e) {
    toast(e.message, 'error');
  }
}

function assignSheet(a) {
  const title = h('input.input', { placeholder: a.id === 'coder' ? 'Bakery website' : 'Short title' });
  const task = h('textarea.input', { rows: 6, placeholder: a.id === 'coder' ? 'Describe what to build: pages, features, style…' : 'What should ' + a.name + ' do?' });
  const project = h('input.input', { placeholder: 'Leave empty to use the title' });
  const isCoder = (a.groups || []).includes('code');
  let model = '';
  sheet({
    title: 'Give ' + a.name + ' a job',
    body: h(
      'div',
      field('Title', title),
      field('Job', task, 'It works in the background; follow it here or in its conversation.'),
      isCoder ? field('Project folder', project, 'Reuse a name to continue an earlier project.') : null,
      field('Model', modelField('', (v) => (model = v), a.model ? 'Agent’s model (' + shortModel(a.model) + ')' : 'Default model'), 'Just for this job.'),
    ),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h('button.btn.primary', {
        onclick: async () => {
          if (!task.value.trim()) return task.focus();
          try {
            const job = await api.post(`/api/agents/${a.id}/assign`, { title: title.value.trim(), task: task.value.trim(), project: project.value.trim(), model });
            close();
            onJob(job);
            toast(a.name + ' is on it', { action: { label: 'Watch', run: () => navigate('/c/' + job.conv_id) } });
            renderAgents(root);
          } catch (e) {
            toast(e.message, 'error');
          }
        },
      }, 'Start'),
    ],
  });
}

async function editor(a) {
  const src = a || { action: 'run', groups: [], enabled: 1, max_steps: 40 };
  const sched = { type: 'none', time: '13:00', day: 'monday', minutes: 60, ...(src.schedule || {}) };
  const name = h('input.input', { value: src.name || '', placeholder: 'Break reminder' });
  const description = h('input.input', { value: src.description || '', placeholder: 'What this agent is for' });
  const task = h('textarea.input', { rows: 3 }, src.task || '');
  const instructions = h('textarea.input', { rows: 5, placeholder: 'Role, rules and style. e.g. “You are a careful bookkeeper. Always…”' }, src.instructions || '');
  const time = h('input.input', { type: 'time', value: sched.time || '13:00' });
  const minutes = h('input.input', { type: 'number', min: 5, value: sched.minutes || 60 });
  const at = h('input.input', { type: 'datetime-local' });
  const maxSteps = h('input.input', { type: 'number', min: 5, max: 200, value: src.max_steps || 40 });
  let action = src.action || 'run';
  let type = sched.type || 'none';
  let day = sched.day || 'monday';
  let model = src.model || '';
  let autoApprove = !!src.auto_approve;
  const groups = new Set(src.groups || []);
  const extra = h('div');
  const taskField = h('div');
  let runOnly = null;
  const renderExtra = () => {
    clear(extra);
    if (['daily', 'weekdays', 'weekly'].includes(type)) extra.appendChild(h('div.grid-2', field('Time', time), type === 'weekly' ? field('Day', selectEl(WEEKDAYS.map((d) => [d, d[0].toUpperCase() + d.slice(1)]), day, (v) => (day = v))) : h('div')));
    if (type === 'interval') extra.appendChild(field('Every (minutes)', minutes));
    if (type === 'once') extra.appendChild(field('At', at));
    clear(taskField);
    if (action === 'remind') {
      task.placeholder = 'Time for a short break: stand up and stretch.';
      taskField.appendChild(field('Reminder text', task, 'Sent as a notification to every device at the scheduled time.'));
    } else if (type !== 'none') {
      task.placeholder = 'What to do on each run, e.g. “Check the news on AI and send me 3 headlines with sources.”';
      taskField.appendChild(field('Task on each run', task, 'Runs with tools, then notifies you with the result.'));
    }
    if (runOnly) runOnly.style.display = action === 'remind' ? 'none' : '';
  };
  const chips = h('div.group-chips', GROUPS.map(([g, label]) => {
    const b = h('button.chip-toggle' + (groups.has(g) ? '.on' : ''), { type: 'button' }, label);
    b.addEventListener('click', () => {
      groups.has(g) ? groups.delete(g) : groups.add(g);
      b.classList.toggle('on', groups.has(g));
    });
    return b;
  }));
  const modelBox = h('div', selectEl([['', 'Default model']], '', () => {}));
  api.get('/api/models').then((res) => {
    const opts = [['', 'Default model']].concat((res.models || []).filter((m) => !/embed/i.test(m.name)).map((m) => [m.name, (m.provider_name ? m.provider_name + ' · ' : '') + m.name]));
    if (model && !opts.some((o) => o[0] === model)) opts.push([model, model]);
    clear(modelBox);
    modelBox.appendChild(selectEl(opts, model, (v) => (model = v)));
  }).catch(() => {});
  runOnly = h(
    'div',
    field('Instructions', instructions),
    field('Tools', chips, 'None selected: picked automatically from the job. Core tools are always on.'),
    h('div.grid-2', field('Model', modelBox, 'e.g. a coding model for the coder.'), field('Max steps per job', maxSteps)),
    h('div.row', switchEl(autoApprove, (v) => (autoApprove = v)), h('span', 'Autonomous: run risky tools (code, shell, mouse) without asking')),
  );
  renderExtra();
  const builtin = a && a.kind === 'builtin';
  sheet({
    title: a ? 'Edit ' + a.name : 'New agent',
    wide: true,
    body: h(
      'div',
      h('div.grid-2', field('Name', name), field('Kind', seg([['run', 'Works on tasks'], ['remind', 'Reminds me']], action, (v) => { action = v; renderExtra(); }))),
      field('Description', description, builtin ? 'Scorpi uses this to decide which agent gets a job.' : null),
      field('When it acts by itself', selectEl([['none', 'Only when given a job'], ['daily', 'Every day'], ['weekdays', 'Weekdays'], ['weekly', 'Weekly'], ['interval', 'Every N minutes'], ['once', 'Once']], type, (v) => { type = v; renderExtra(); })),
      extra,
      taskField,
      runOnly,
    ),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h(
        'button.btn.primary',
        {
          onclick: async () => {
            if (!name.value.trim()) return name.focus();
            const schedule = type === 'none' ? null : { type };
            if (schedule) {
              if (['daily', 'weekdays', 'weekly'].includes(type)) schedule.time = time.value || '13:00';
              if (type === 'weekly') schedule.day = day;
              if (type === 'interval') schedule.minutes = Math.max(5, +minutes.value || 60);
              if (type === 'once') schedule.at = at.value;
            }
            const body = {
              name: name.value.trim(), description: description.value.trim(), action, task: task.value.trim(), instructions: instructions.value.trim(),
              schedule, groups: [...groups], model, auto_approve: autoApprove ? 1 : 0, max_steps: +maxSteps.value || 40,
              icon: src.icon && src.icon !== 'agent' && src.icon !== 'bell' ? src.icon : action === 'remind' ? 'bell' : 'agent',
            };
            try {
              if (a) await api.patch('/api/agents/' + a.id, body);
              else await api.post('/api/agents', body);
              close();
              toast(a ? 'Agent saved' : 'Agent created');
              renderAgents(root);
            } catch (err) {
              toast(err.message, 'error');
            }
          },
        },
        a ? 'Save' : 'Create',
      ),
    ],
  });
}
