// Conversations: thread list (or the project list in Code mode), live agent turns, composer (Write / Voice).

import { api, streamJSON, upload } from '../lib/api.js';
import { $, baseName, clear, copyText, debounce, extOf, fmtRelative, fmtSize, h, icon, isMobile, wordsIn } from '../lib/dom.js';
import { highlight, renderMarkdown } from '../lib/md.js';
import { enhanceMarkdown, openViewer } from '../lib/viewer.js';
import { closePop, confirmSheet, popover, promptSheet, toast } from '../lib/ui.js';
import { sfx } from '../lib/sfx.js';
import { createScorpi } from '../lib/scorpi.js';
import { decorateAnswer, hasHooks, pluginCommands, runHooks, toolEvent } from '../lib/looks.js';
import { InkWriter } from '../lib/ink.js';
import { bus, prefs, setPref, skillById, state } from '../store.js';
import { browserRecognizer, createStreamSpeaker, hostSttAvailable, record, speak, stopSpeaking, sttRoute, sttUnavailableReason, transcribe, audioContext } from '../audio.js';
import { renderCard } from './cards.js';
import { canvasApply, canvasChip, canvasLoad, canvasReset, initCanvas } from './canvas.js';
import { hideProjectPane, refreshProject, showProjectPane, startAttempts, toggleProjectPane } from './project.js';
import { startVoice, stopVoice, voiceBar, voiceOn, voiceRefresh, voiceStatus } from './voice.js';
import { pauseWake, resumeWake } from '../wake.js';
import { navigate } from '../router.js';
import { deviceId } from '../lib/radio.js';

const TOOL_LABELS = {
  web_search: ['Searched the web', (a) => a.query],
  fetch_page: ['Read a page', (a) => (a.url || '').replace(/^https?:\/\/(www\.)?/, '')],
  wikipedia: ['Looked up Wikipedia', (a) => a.topic],
  http_get: ['Called an API', (a) => a.url],
  http_request: ['Sent a request', (a) => `${a.method} ${a.url}`],
  list_dir: ['Opened a folder', (a) => a.path || 'workspace'],
  read_file: ['Read a file', (a) => baseName(a.path)],
  write_file: ['Wrote a file', (a) => baseName(a.path)],
  edit_file: ['Edited a file', (a) => baseName(a.path)],
  search_files: ['Searched files', (a) => [a.pattern, a.contains].filter(Boolean).join(' · ')],
  file_info: ['Inspected', (a) => baseName(a.path)],
  manage_path: ['Changed files', (a) => `${a.action} ${baseName(a.path)}`],
  open_path: ['Opened on the PC', (a) => baseName(a.path)],
  read_document: ['Read a document', (a) => baseName(a.path)],
  create_document: ['Created a document', (a) => baseName(a.path)],
  edit_document: ['Edited a document', (a) => baseName(a.path)],
  pdf_tools: ['Worked on a PDF', (a) => a.action],
  compare_documents: ['Compared documents', (a) => `${baseName(a.path_a)} ↔ ${baseName(a.path_b)}`],
  run_python: ['Ran Python', (a) => firstLine(a.code)],
  run_javascript: ['Ran JavaScript', (a) => firstLine(a.code)],
  run_shell: ['Ran a command', (a) => a.command],
  calculate: ['Calculated', (a) => a.expression],
  analyze_data: ['Analyzed data', (a) => baseName(a.path)],
  make_chart: ['Drew a chart', (a) => a.title || a.kind],
  generate_image: ['Generated an image', (a) => a.prompt],
  create_diagram: ['Drew a diagram', (a) => a.title],
  create_svg: ['Designed a graphic', (a) => a.title || a.filename],
  text_to_speech: ['Recorded audio', (a) => a.text],
  transcribe_audio: ['Transcribed audio', (a) => baseName(a.path)],
  screenshot: ['Looked at the screen', () => ''],
  mouse: ['Used the mouse', (a) => `${a.action} ${Math.round(a.x)},${Math.round(a.y)}`],
  keyboard: ['Typed', (a) => a.text || a.keys],
  scroll: ['Scrolled', (a) => a.amount],
  windows: ['Managed windows', (a) => `${a.action} ${a.title || ''}`],
  browser: ['Used the browser', (a) => `${a.action} ${a.url || a.target || ''}`],
  list_programs: ['Listed programs', (a) => a.filter],
  launch_app: ['Started a program', (a) => a.name],
  open_url: ['Opened a website', (a) => a.url],
  list_processes: ['Checked running programs', (a) => a.filter],
  kill_process: ['Closed a program', (a) => a.target],
  system_info: ['Checked the system', () => ''],
  clipboard: ['Used the clipboard', (a) => a.action],
  media_control: ['Media keys', (a) => a.action],
  local_services: ['Scanned local services', () => ''],
  home_assistant: ['Smart home', (a) => `${a.action} ${a.entity_id || a.filter || ''}`],
  geocode: ['Found a place', (a) => a.query],
  find_places: ['Searched nearby', (a) => `${a.what} near ${a.near}`],
  route: ['Planned a route', (a) => `${a.origin} → ${a.destination}`],
  weather: ['Checked the weather', (a) => a.place],
  create_reminder: ['Set a reminder', (a) => a.text],
  create_event: ['Added to the calendar', (a) => a.title],
  list_agenda: ['Checked the agenda', () => ''],
  delete_agenda_item: ['Removed from agenda', (a) => `${a.kind} #${a.id}`],
  db_collections: ['Opened the database', () => ''],
  db_insert: ['Saved records', (a) => a.collection],
  db_search: ['Searched records', (a) => [a.collection, a.query].filter(Boolean).join(' · ')],
  db_update: ['Updated a record', (a) => '#' + a.id],
  db_delete: ['Deleted records', (a) => (a.ids || []).join(', ')],
  sql_query: ['Queried a database', (a) => a.sql],
  knowledge_search: ['Searched your library', (a) => a.query],
  knowledge_add: ['Added to library', (a) => baseName(a.path)],
  remember: ['Remembered', (a) => a.text],
  recall: ['Recalled memories', (a) => a.query],
  forget: ['Forgot a memory', (a) => '#' + a.id],
  update_plan: ['Updated the plan', (a) => `${(a.steps || []).length} steps`],
  send_notification: ['Sent a notification', (a) => a.title],
  enable_tools: ['Loaded tools', (a) => (a.groups || []).join(', ')],
  current_time: ['Checked the time', () => ''],
  delegate: ['Delegated', (a) => `${a.agent}: ${a.task}`],
  start_background_task: ['Started a background task', (a) => a.title],
  create_automation: ['Created a routine', (a) => a.name],
  list_automations: ['Listed routines', () => ''],
  assign_agent: ['Handed to an agent', (a) => `${a.agent}: ${a.title}`],
  agent_status: ['Checked on the agents', (a) => a.job_id || ''],
  message_agent: ['Messaged an agent', (a) => a.message],
  stop_agent: ['Stopped an agent', (a) => a.target],
  delete_agent_jobs: ['Removed agent jobs', (a) => a.target],
  create_agent: ['Created an agent', (a) => a.name],
  manage_agent: ['Changed an agent', (a) => `${a.agent} · ${a.action}`],
  present_quiz: ['Prepared practice', (a) => a.title],
  style_app: ['Restyled Scorpi', (a) => a.name],
  app_plugin: ['Added a plugin', (a) => a.name],
  manage_looks: ['Changed the look', (a) => [a.action, a.target].filter(Boolean).join(' · ')],
  set_agent_model: ['Switched a model', (a) => a.model],
  spawn_helpers: ['Started helpers', (a) => (a.helpers || []).map((x) => x.name).filter(Boolean).join(', ')],
  ask_user: ['Asked you', (a) => a.question],
  show: ['Showed you', (a) => (/^https?:/.test(a.target || '') ? a.target.replace(/^https?:\/\/(www\.)?/, '') : baseName(a.target))],
  check_code: ['Checked the code', (a) => baseName(a.path)],
  canvas: ['Updated the canvas', (a) => a.title || (a.blocks || []).map((b) => b && (b.title || b.id)).filter(Boolean).join(', ')],
};

const firstLine = (s) => String(s || '').split('\n').find((l) => l.trim()) || '';

function toolLabel(name, args = {}) {
  if (name.startsWith('mcp_')) {
    const parts = name.split('_');
    return [`${parts[1]} · ${parts.slice(2).join('_')}`, Object.values(args).filter((v) => typeof v === 'string')[0] || ''];
  }
  const entry = TOOL_LABELS[name];
  if (!entry) return [name.replace(/_/g, ' '), ''];
  let arg = '';
  try {
    arg = entry[1](args || {}) ?? '';
  } catch {
    arg = '';
  }
  return [entry[0], String(arg)];
}

// ---------- module state ----------

const els = {};
const composer = {
  attachments: [], // {id?, name, mime, size, kind, path, local, progress, thumb}
  skill: '',
  groups: null,
  editFrom: null,
  io: 'write', // 'write' or 'voice'
  plan: false, // a coding project's next message: the agent proposes a plan to approve before it changes anything
  tests: false, // ... or writes failing tests to approve, then builds until they pass (tests first)
};
let live = null; // active TurnView
let controller = null;
let buddy = null; // Scorpi on the prompt box
let talker = null; // reads the live turn aloud (voice mode, or "read replies aloud")

// Ember mirrors the agent: on the composer perch, and in the rail through the bus.
function mood(name, flashMs = 0) {
  if (!buddy) return;
  if (flashMs) buddy.flash(name, flashMs);
  else buddy.set(name);
  bus.emit('agent:mood', { mood: name, flash: flashMs });
}

// ---------- thread list ----------

let threadsPromise = null;
export async function loadThreads() {
  if (threadsPromise) return threadsPromise; // an event burst asks several times: one request is enough
  threadsPromise = (async () => {
    try {
      const data = await api.get('/api/conversations');
      state.conversations = data.conversations;
    } catch {
      return;
    }
    renderThreads();
  })();
  try {
    return await threadsPromise;
  } finally {
    threadsPromise = null;
  }
}

// Events arrive in bursts (a reply finishing, a title being made, a run registering). Rebuild the list once.
let threadsTimer = 0;
export function scheduleThreads(delay = 220) {
  clearTimeout(threadsTimer);
  threadsTimer = setTimeout(() => {
    threadsTimer = 0;
    loadThreads();
  }, delay);
}

function groupFor(conv) {
  if (conv.pinned) return 'Pinned';
  const d = new Date(conv.updated * 1000);
  const now = new Date();
  const days = (now - d) / 86400000;
  if (d.toDateString() === now.toDateString()) return 'Today';
  if (days < 2) return 'Yesterday';
  if (days < 7) return 'This week';
  if (days < 31) return 'This month';
  return 'Earlier';
}

export async function loadProjects() {
  try {
    state.projects = (await api.get('/api/projects')).projects;
  } catch {
    return;
  }
  if (state.mode === 'code') renderThreads();
  else bus.emit('threads'); // project tabs in the taskbar want their names
}

function renderProjects(list, q) {
  const items = state.projects.filter((p) => !q || p.name.toLowerCase().includes(q) || p.slug.includes(q));
  if (!items.length) {
    list.appendChild(h('div.thread-empty', q ? 'Nothing matches.' : 'Your projects will gather here.'));
    return;
  }
  items.forEach((p, i) => {
    const working = p.job && p.job.status === 'running';
    list.appendChild(
      h(
        'div.thread.project' + (state.project && state.project.slug === p.slug ? '.active' : ''),
        { style: { animationDelay: Math.min(i, 14) * 18 + 'ms' }, onclick: () => navigate('/code/' + p.slug), title: p.path },
        working ? h('span.t-live', { title: 'Working' }) : icon(p.has_index ? 'globe' : 'code', 't-ic'),
        h('span.t-title', p.name),
        h('span.t-when', fmtRelative(p.updated).replace(' ago', '').replace(' ', '')),
      ),
    );
  });
}

export function renderThreads() {
  fillThreads();
  bus.emit('threads'); // the taskbar's tabs follow titles and running state
}

let threadsSig = '';
function fillThreads() {
  const list = els.threadList;
  const q = els.search.value.trim().toLowerCase();
  const source = state.mode === 'code' ? state.projects : state.conversations;
  // identical data (the usual case: an event says "conversation changed" but the list did not) must not rebuild 300 rows
  const sig = state.mode + '|' + q + '|' + state.activeId + '|' + (state.project ? state.project.slug : '') + '|' + JSON.stringify(source);
  if (sig === threadsSig) return;
  threadsSig = sig;
  clear(list);
  if (state.mode === 'code') return renderProjects(list, q);
  const items = state.conversations.filter((c) => !q || (c.title || '').toLowerCase().includes(q));
  if (!items.length) {
    list.appendChild(h('div.thread-empty', q ? 'Nothing matches.' : 'Your conversations will gather here.'));
    return;
  }
  let group = '';
  items.forEach((c, i) => {
    const g = groupFor(c);
    if (g !== group) {
      group = g;
      list.appendChild(h('div.thread-group', g));
    }
    const more = h('button.t-more', { title: 'More', 'data-pop-anchor': '' }, icon('more'));
    const row = h(
      'div.thread' + (c.id === state.activeId ? '.active' : ''),
      { style: { animationDelay: Math.min(i, 14) * 18 + 'ms' }, onclick: (e) => !e.target.closest('.t-more') && navigate('/c/' + c.id) },
      c.running ? h('span.t-live', { title: 'Working' }) : null,
      h('span.t-title', c.title || 'Untitled'),
      c.origin && c.origin !== 'chat' ? h('span.t-badge', c.origin) : null,
      h('span.t-when', fmtRelative(c.updated).replace(' ago', '').replace(' ', '')),
      more,
    );
    more.addEventListener('click', (e) => {
      e.stopPropagation();
      threadMenu(more, c);
    });
    list.appendChild(row);
  });
}

function threadMenu(anchor, c) {
  popover(
    anchor,
    [
      { label: c.pinned ? 'Unpin' : 'Pin to top', icon: 'pin', onClick: () => patchConversation(c.id, { pinned: !c.pinned }) },
      { label: 'Rename', icon: 'edit', onClick: () => renameConversation(c) },
      { label: 'Export as Markdown', icon: 'download', onClick: () => (location.href = `/api/conversations/${c.id}/export`) },
      'sep',
      { label: 'Delete', icon: 'trash', danger: true, onClick: () => deleteConversation(c.id) },
    ],
    { align: 'end' },
  );
}

async function patchConversation(id, body) {
  await api.patch('/api/conversations/' + id, body);
  await loadThreads();
  if (id === state.activeId) updateHead();
}

async function renameConversation(c) {
  const title = await promptSheet('Rename conversation', { value: c.title, confirm: 'Rename' });
  if (title && title.trim()) patchConversation(c.id, { title: title.trim() });
}

async function deleteConversation(id) {
  const ok = await confirmSheet('Delete conversation?', 'This removes the conversation and its messages. Files it created stay in the workspace.', { confirm: 'Delete', danger: true });
  if (!ok) return;
  await api.del('/api/conversations/' + id);
  state.conversations = state.conversations.filter((c) => c.id !== id);
  renderThreads();
  if (id === state.activeId) navigate('/');
}

// ---------- conversation view ----------

function currentConv() {
  return state.conversations.find((c) => c.id === state.activeId) || null;
}

function updateHead() {
  const conv = currentConv();
  const proj = state.project;
  const has = !!state.activeId || !!proj;
  els.view.classList.toggle('has-convo', has);
  els.view.classList.toggle('in-project', !!proj);
  const title = proj ? proj.name : has ? (conv && conv.title) || 'Conversation' : '';
  els.title.textContent = title;
  $('#topbarTitle').textContent = title || 'Scorpi';
  els.projectTag.classList.toggle('hidden', !proj);
  if (proj) {
    const job = proj.job;
    els.projectTag.replaceChildren(icon('code'), job && job.status === 'running' ? 'Agent working' : 'Project');
    els.projectTag.classList.toggle('busy', !!(job && job.status === 'running'));
  }
  $('#convoMore').classList.toggle('hidden', !conv || !!proj);
  els.pin.classList.toggle('on', !!(conv && conv.pinned));
  els.pin.classList.toggle('hidden', !!proj);
  $('#deleteBtn').classList.toggle('hidden', !!proj);
  $('#exportBtn').classList.toggle('hidden', !state.activeId);
  $('#paneBtn').classList.toggle('hidden', !proj);
  const skill = !proj && conv && conv.skill ? skillById(conv.skill) : null;
  els.skillTag.classList.toggle('hidden', !skill);
  if (skill) els.skillTag.replaceChildren(icon(skill.icon || 'spark'), skill.name);
  const job = agentJob();
  els.input.placeholder = proj ? (state.activeId ? `Message the ${proj.name} agent…` + (isMobile() ? '' : '  / commands · @ files') : 'Describe what to build…') : job ? `Message ${job.agent_name}…` : placeholderFor(anySkill(composer.skill));
  els.form.classList.toggle('agent-chat', !!job);
  updateModelChip();
  updateSkillChip();
  document.title = title ? `${title} · Scorpi` : 'Scorpi';
}

// An agent's own conversation (Agents, a coding project): the prompt box shows that agent and the model it runs on,
// and picking a model switches the agent, not the chat.
let watching = null;
const agentJob = () => (watching && watching.conv_id && watching.conv_id === state.activeId ? watching : null);

function leaveProject() {
  if (!state.project) return;
  state.project = null;
  hideProjectPane();
}

export function showWelcome() {
  leaveProject();
  state.activeId = null;
  if (live) detachLive();
  clear(els.messages);
  canvasReset();
  updateHead();
  renderWelcome();
  renderThreads();
  els.input.focus({ preventScroll: true });
}

/** A coding project: the conversation with its agent, next to the project's files and live preview. */
export async function openProject(slug) {
  let data;
  try {
    data = await api.get('/api/projects/' + encodeURIComponent(slug));
  } catch (err) {
    toast('Could not open the project', { type: 'error', body: err.message });
    navigate('/code');
    return;
  }
  const switching = !state.project || state.project.slug !== slug;
  state.project = data;
  canvasReset();
  showProjectPane(data);
  if (data.conv_id) {
    await openConversation(data.conv_id, { keepProject: true });
  } else if (switching || state.activeId) {
    if (live) detachLive();
    state.activeId = null;
    clear(els.messages);
    els.messages.appendChild(h('div.project-intro', icon('code'), h('div', h('b', 'This project has no agent yet.'), ' Describe what to build or change, and the coder starts in this folder.')));
  }
  updateHead();
  renderThreads();
  if (!isMobile()) els.input.focus({ preventScroll: true });
}

bus.on('project:updated', (p) => {
  if (!state.project || state.project.slug !== p.slug) return;
  updateHead();
  const idx = state.projects.findIndex((x) => x.slug === p.slug);
  if (idx >= 0) Object.assign(state.projects[idx], { job: p.job, name: p.name, has_index: p.has_index });
  if (state.mode === 'code') renderThreads();
});

function greeting() {
  const hr = new Date().getHours();
  const name = (state.settings.user_name || '').trim();
  const who = name ? `, *${name}*` : '';
  if (hr < 5) return [`Still up${who}?`, 'The quiet hours are good for deep work.'];
  if (hr < 12) return [`Good morning${who}.`, 'What shall we set in motion today?'];
  if (hr < 17) return [`Good afternoon${who}.`, 'What are we working on?'];
  if (hr < 22) return [`Good evening${who}.`, 'What shall we make tonight?'];
  return [`Late hours${who}.`, 'Let’s keep it calm and focused.'];
}

async function renderWelcome() {
  const [title, sub] = greeting();
  wordsIn(els.welcomeTitle, title);
  // Scorpi says hello from the prompt box
  if (buddy && buddy.base === 'idle') setTimeout(() => buddy.hop().wave(), 700);
  els.welcomeSub.textContent = sub;
  const chips = els.skillIndex;
  clear(chips);
  state.skills.slice(0, 6).forEach((s) =>
    chips.appendChild(h('button.chip', { type: 'button', title: s.description, onclick: () => { setSkill(s.id); els.input.focus(); } }, icon(s.icon || 'spark'), s.name)),
  );
  chips.appendChild(h('button.chip', { type: 'button', title: 'Start a coding project', onclick: () => navigate('/code') }, icon('code'), 'Code a project'));
  renderGlance();
}

async function renderGlance() {
  const box = els.glance;
  clear(box);
  const st = state.status;
  const items = [];
  if (st) {
    const model = st.llm.model ? shortModel(st.llm.model) : 'no model';
    items.push(['cpu', 'Model', st.llm.online ? model : 'Offline — add a model in Connections', () => navigate('/connections')]);
  }
  try {
    const ag = await api.get('/api/agenda?days=7&past=0');
    const now = Date.now() / 1000;
    const next = [...ag.reminders.map((r) => ({ t: r.due, title: r.text })), ...ag.events.map((e) => ({ t: e.start, title: e.title }))]
      .filter((x) => x.t > now)
      .sort((a, b) => a.t - b.t)[0];
    items.push(['calendar', 'Next up', next ? `${next.title} · ${fmtRelative(next.t)}` : 'Nothing scheduled this week', () => navigate('/agenda')]);
  } catch {
    /* offline */
  }
  if (st && st.runs && st.runs.length) items.push(['routine', 'Working', `${st.runs.length} task(s) in progress`, () => navigate('/routines')]);
  else if (st) items.push(['memory', 'Memory', `${st.counts.memories} memories · ${st.counts.knowledge} library docs`, () => navigate('/memory')]);
  for (const [ic, label, value, go] of items) {
    box.appendChild(h('div.glance-item', { onclick: go }, h('div.g-label', icon(ic), label), h('div.g-value', value)));
  }
}

export async function openConversation(id, { keepProject = false } = {}) {
  if (!keepProject) leaveProject();
  if (state.activeId === id && els.messages.children.length && !els.messages.querySelector('.project-intro')) {
    scrollToEnd(true);
    return updateHead();
  }
  if (live) detachLive();
  state.activeId = id;
  updateHead();
  renderThreads();
  clear(els.messages);
  let data;
  try {
    data = await api.get('/api/conversations/' + id);
  } catch (err) {
    toast('Could not open conversation', { type: 'error', body: err.message });
    if (!keepProject) navigate('/');
    return;
  }
  if (state.activeId !== id) return;
  watching = data.job || null;
  if (!state.conversations.find((c) => c.id === id)) state.conversations.unshift(data.conversation);
  else Object.assign(state.conversations.find((c) => c.id === id), data.conversation);
  updateHead();
  const frag = document.createDocumentFragment();
  data.messages.forEach((m, i) => frag.appendChild(m.role === 'user' ? (m.data && m.data.system ? systemNode(m.data.system) : m.data && m.data.note ? noteNode(m.data.note) : userNode(m)) : assistantNode(m, i === data.messages.length - 1)));
  els.messages.appendChild(frag);
  if (!keepProject) canvasLoad(data.messages, id);
  wireCode(els.messages);
  scrollToEnd(true);
  if (data.run) attachRun(data.run.id, id);
  if (!isMobile()) els.input.focus({ preventScroll: true });
}

// A turn Scorpi started itself (an agent reporting back), shown as a quiet line instead of a user bubble.
function noteNode(label) {
  return h('div.msg.note', icon('agent'), h('span', 'Update from ', h('b', label)));
}

// A run an agent started by itself (building an approved plan): a quiet line instead of its long prompt.
function systemNode(text) {
  return h('div.msg.note', icon('route'), h('span', text));
}

// The chat assistant reports on a finished agent job: follow it live when that conversation is on screen.
export function followAgentUpdate(d) {
  if (!d || d.conv_id !== state.activeId || live || state.view !== 'chat') return;
  els.messages.querySelectorAll('.msg.last').forEach((n) => n.classList.remove('last'));
  els.messages.appendChild(noteNode(d.note || 'an agent'));
  attachRun(d.run_id, d.conv_id);
}

// A chat that paused at the usage limits continues by itself: show it live when it is open.
export function followResumed(d) {
  if (!d || d.conv_id !== state.activeId || live || state.view !== 'chat') return;
  els.messages.querySelectorAll('.msg.last').forEach((n) => n.classList.remove('last'));
  els.messages.querySelectorAll('.resume-row').forEach((n) => n.remove());
  els.messages.appendChild(systemNode(d.text || 'Continuing after the usage limits reset'));
  attachRun(d.run_id, d.conv_id);
}

function userNode(m) {
  const atts = (m.data && m.data.attachments) || [];
  // every attachment opens in the viewer; pictures page through each other there
  const item = (a) => ({ url: '/api/uploads/' + a.id, upload: a.id, name: a.name, mime: a.mime, size: a.size });
  const pics = atts.filter((a) => a.kind === 'image');
  const strip = atts.length
    ? h(
        'div.att-strip',
        atts.map((a) =>
          a.kind === 'image'
            ? h('img.att-thumb', { src: '/api/uploads/' + a.id, alt: a.name, loading: 'lazy', onclick: () => openViewer(pics.map(item), pics.indexOf(a)) })
            : a.kind === 'video'
              ? h('video.att-video', { src: '/api/uploads/' + a.id, controls: true, preload: 'metadata', playsInline: true })
              : h('button.att-pill', { type: 'button', title: 'Preview ' + a.name, onclick: () => openViewer(item(a)) }, icon(a.kind === 'audio' ? 'speaker' : 'file'), h('span', a.name)),
        ),
      )
    : null;
  // what Ember was attached to when this was asked
  const ctx = m.data && m.data.context;
  const ctxChip = ctx ? h('span.ctx-chip', icon(ctx.kind === 'window' ? 'monitor' : 'eye'), h('span', ctx.title || ctx.label || 'Attached'), ctx.process ? h('i', ctx.process) : null) : null;
  const node = h(
    'div.msg.user',
    { dataset: { id: m.id } },
    ctxChip,
    strip,
    m.content ? h('div.slip', commandTag(m), commandTag(m) ? m.content.replace(/^\/[\w-]+\s*/, '') : m.content) : null,
    h(
      'div.meta',
      h('button.act', { title: 'Copy', onclick: () => copyText(m.content).then(() => toast('Copied')) }, icon('copy')),
      h('button.act', { title: 'Edit & resend', onclick: () => editFrom(m) }, icon('edit')),
    ),
  );
  return node;
}

/** "/game" at the start of a message sent by a plugin command, drawn as a tag */
function commandTag(m) {
  const cmd = m.data && m.data.command;
  return cmd && (m.content || '').startsWith('/' + cmd) ? h('span.cmd-tag', icon('puzzle'), '/' + cmd) : null;
}

function editFrom(m) {
  composer.editFrom = m.id;
  els.input.value = m.content || '';
  autosize();
  updateSend();
  els.input.focus();
  toast('Editing message', { body: 'Sending will replace it and everything after.', timeout: 3000 });
}

// Every model was at its usage limit: the chat continues by itself when the first one resets (scorpi/limits.py).
function resumeRow(at) {
  const when = new Date(at * 1000);
  const clock = when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const row = h('div.resume-row');
  const act = async (now) => {
    const id = state.activeId;
    if (!id) return;
    try {
      const res = await api.post('/api/limits/resume', { conversation_id: id, now });
      row.replaceChildren(h('span.muted', now ? 'Trying again now…' : 'It will not continue by itself.'));
      if (res.run_id) attachRun(res.run_id, id);
    } catch (e) {
      toast(e.message, 'error');
    }
  };
  row.append(
    icon('clock'),
    h('span', 'Continues by itself at ' + clock + '.'),
    h('button.btn.small.quiet', { onclick: () => act(true) }, icon('play'), 'Try now'),
    h('button.btn.small.quiet', { onclick: () => act(false) }, 'Don’t continue'),
  );
  return row;
}

function assistantNode(m, isLast) {
  const data = m.data || {};
  const view = new TurnView({ stored: true, message: m });
  for (const part of data.parts || []) view.addStoredPart(part);
  if (!(data.parts || []).some((p) => p.type === 'text') && m.content) view.setText(m.content, true);
  if (data.error) view.error(data.error, isLast && data.resume_at > Date.now() / 1000 ? data.resume_at : 0);
  if (data.stopped && !m.content) view.note('Stopped.');
  view.finish(m.content || '', { isLast });
  return view.el;
}

// ---------- live turn rendering ----------

class TurnView {
  constructor({ stored = false, message = null } = {}) {
    this.stored = stored;
    this.message = message;
    this.body = h('div.turn');
    this.actions = h('div.msg-actions');
    this.el = h('div.msg.assistant' + (stored ? '' : '.streaming'), this.body, this.actions);
    this.textEl = null;
    this.textBuf = '';
    this.fullText = '';
    this.trace = null;
    this.steps = {};
    this.raf = 0;
    this.paintTimer = 0;
    this.lastPaint = 0;
    this.statusEl = null;
    this.thinkEl = null;
    this.waiting = null;
    if (!stored) {
      this.waiting = h('div.waiting', h('i'), h('i'), h('i'));
      this.body.appendChild(this.waiting);
    }
  }

  clearWaiting() {
    if (this.waiting) {
      this.waiting.remove();
      this.waiting = null;
    }
    if (this.statusEl) {
      this.statusEl.remove();
      this.statusEl = null;
    }
  }

  thinking(text, stored = false) {
    this.clearWaiting();
    if (!this.thinkEl || stored) {
      this.endText();
      const body = h('div.t-body');
      this.thinkEl = h('details.thinking', h('summary', icon('right'), h('span', stored ? 'Thought process' : 'Thinking…')), body);
      this.thinkEl._body = body;
      // append a text node as the reasoning streams: `textContent +=` rebuilds the whole block on every chunk
      this.thinkEl._text = document.createTextNode('');
      body.appendChild(this.thinkEl._text);
      this.thinkEl._start = performance.now();
      this.body.appendChild(this.thinkEl);
      this.trace = null;
      if (!this.stored) mood('thinking');
    }
    this.thinkEl._text.appendData(text || '');
  }

  closeThinking() {
    if (this.thinkEl && !this.thinkEl._closed) {
      this.thinkEl._closed = true;
      const secs = Math.max(1, Math.round((performance.now() - this.thinkEl._start) / 1000));
      if (!this.stored) this.thinkEl.querySelector('summary span').textContent = `Thought for ${secs}s`;
      this.thinkEl.open = false;
    }
  }

  token(text) {
    this.clearWaiting();
    this.closeThinking();
    if (!this.textEl) {
      this.textEl = h('div.md');
      this.ink = new InkWriter(this.textEl);
      this.textBuf = '';
      this.body.appendChild(this.textEl);
      this.trace = null;
      mood('writing');
    } else if (!this.ink) {
      // a turn that already had a settled text block (e.g. after a tool) gets a fresh writer for the next words
      this.ink = new InkWriter(this.textEl);
    }
    this.textBuf += text;
    this.fullText += text;
    this.schedulePaint();
  }

  // A reply can arrive in hundreds of tiny tokens. Re-rendering the whole Markdown on every one of them is what made
  // long answers (and phones) stutter: coalesce to ~15 frames a second, which still looks live.
  schedulePaint() {
    if (this.raf || this.paintTimer) return;
    // a long answer costs more to re-render: fewer frames, still visibly live
    const interval = this.textBuf.length > 8000 ? 140 : 65;
    const wait = interval - (performance.now() - (this.lastPaint || 0));
    if (wait > 0) {
      this.paintTimer = setTimeout(() => {
        this.paintTimer = 0;
        this.schedulePaint();
      }, wait);
      return;
    }
    this.raf = requestAnimationFrame(() => this.paint());
  }

  paint() {
    this.raf = 0;
    this.lastPaint = performance.now();
    if (!this.textEl) return;
    // a very long answer: skip the per-word ink animation (it wraps every word in a span on every frame)
    if (this.ink && this.textBuf.length > 6000) this.ink.animate = false;
    const stick = nearBottom();
    this.ink.render(renderMarkdown(this.textBuf), { caret: true });
    if (stick) scrollToEnd();
  }

  // close the current block of text: its ink dries, then code gets colours and copy buttons
  endText() {
    if (this.raf) cancelAnimationFrame(this.raf);
    if (this.paintTimer) clearTimeout(this.paintTimer);
    this.raf = 0;
    this.paintTimer = 0;
    if (this.ink) this.ink.seal(renderMarkdown(this.textBuf), wireCode);
    this.ink = null;
    this.textEl = null;
  }

  setText(text) {
    this.endText();
    this.textEl = h('div.md', { html: renderMarkdown(text) });
    this.textBuf = text;
    this.fullText += (this.fullText ? '\n\n' : '') + text;
    this.body.appendChild(this.textEl);
    this.trace = null;
  }

  ensureTrace() {
    if (this.trace) return this.trace;
    const count = h('b', '');
    const steps = h('div');
    const head = h('div.trace-head', icon('right', 'chev'), h('span', 'Working · ', count));
    // visual answers keep the work to one quiet line; written answers show the steps as they happen
    const open = !this.stored && !document.documentElement.classList.contains('style-visual');
    const el = h('div.trace' + (open ? '.open' : ''), head, h('div.trace-steps', steps));
    head.addEventListener('click', () => el.classList.toggle('open'));
    this.endText();
    this.body.appendChild(el);
    this.trace = { el, steps, count, n: 0, cards: h('div.trace-cards') };
    this.body.appendChild(this.trace.cards);
    return this.trace;
  }

  toolStart(ev) {
    this.clearWaiting();
    this.closeThinking();
    const trace = this.ensureTrace();
    if (!this.stored) mood('working');
    const [label, arg] = toolLabel(ev.name, ev.args);
    const detail = h('div.step-detail', h('div.s-args', JSON.stringify(ev.args || {}, null, 1).slice(0, 1500)), h('pre', '…'));
    const time = h('span.s-time', '');
    const step = h(
      'div.step.running',
      h('div.step-line', h('span.s-name', label), ev.agent ? h('span.s-agent', ev.agent) : null, h('span.s-arg', arg), time),
      detail,
    );
    step.querySelector('.step-line').addEventListener('click', () => step.classList.toggle('expanded'));
    trace.steps.appendChild(step);
    trace.n += 1;
    trace.count.textContent = trace.n === 1 ? '1 step' : `${trace.n} steps`;
    this.steps[ev.id] = { step, detail, time, trace };
    if (nearBottom()) scrollToEnd();
  }

  toolEnd(ev) {
    let entry = this.steps[ev.id];
    if (!entry) {
      this.toolStart(ev);
      entry = this.steps[ev.id];
    }
    entry.step.classList.remove('running', 'waiting');
    entry.step.classList.add(ev.ok ? 'done' : 'error');
    entry.detail.querySelector('pre').textContent = ev.result || '';
    if (ev.elapsed !== undefined) entry.time.textContent = ev.elapsed < 1 ? '' : ev.elapsed.toFixed(1) + 's';
    if (ev.display && ev.display.type === 'canvas') {
      // the canvas itself changes beside the chat; here a quiet line says what changed
      if (!this.stored) canvasApply(ev.display, { live: true, conv: state.activeId });
      entry.trace.cards.appendChild(canvasChip(ev.display));
    } else if (ev.display) {
      const card = renderCard(ev.display, { conversation: state.activeId });
      if (card) {
        // a plan card replaces the previous plan card
        if (ev.display.type === 'plan') {
          const old = this.body.querySelector('.card.plan');
          if (old) old.replaceWith(card);
          else entry.trace.cards.appendChild(card);
        } else entry.trace.cards.appendChild(card);
        if (nearBottom()) scrollToEnd();
      }
    }
    const stick = nearBottom();
    this.trace && this.trace.el.classList.toggle('done', true);
    if (stick) scrollToEnd();
  }

  approval(ev) {
    this.clearWaiting();
    const entry = this.steps[[...Object.keys(this.steps)].pop()];
    if (entry) entry.step.classList.add('waiting');
    const card = approvalCard(ev);
    this.endText();
    this.body.appendChild(card);
    this.trace = null;
    mood('alert');
    scrollToEnd();
  }

  question(ev) {
    this.clearWaiting();
    const card = questionCard(ev);
    this.endText();
    this.body.appendChild(card);
    this.trace = null;
    mood('alert');
    scrollToEnd();
  }

  status(text) {
    this.clearWaiting();
    this.statusEl = h('div.status-line', h('span.shimmer', text));
    this.body.appendChild(this.statusEl);
  }

  note(text) {
    this.body.appendChild(h('div.note', icon('spark'), text));
  }

  // something the user said while this turn worked: it sits where it was said, and the answer continues below it
  aside(text) {
    this.clearWaiting();
    this.endText();
    this.trace = null;
    (this.asides ||= []).push(text);
    this.body.appendChild(h('div.aside', h('span.aside-who', 'You, while it worked'), h('div.slip', text)));
    if (nearBottom()) scrollToEnd();
  }

  error(text, resumeAt = 0) {
    sfx('error');
    this.clearWaiting();
    this.body.appendChild(h('div.error-note', text, resumeAt ? resumeRow(resumeAt) : null));
    if (!this.stored) {
      this.failed = true;
      mood('sad', 3800);
    }
  }

  addStoredPart(part) {
    if (part.type === 'thinking') {
      this.thinking(part.text, true);
      this.closeThinking();
    } else if (part.type === 'text') {
      this.setText(part.text);
    } else if (part.type === 'tool') {
      const id = part.id || Math.random().toString(36);
      this.toolStart({ id, name: part.name, args: part.args, agent: part.agent });
      this.toolEnd({ id, ok: part.ok !== false, result: part.result, display: part.display, elapsed: part.elapsed });
    } else if (part.type === 'card') {
      this.card(part.display);
    }
  }

  // A card at the end of the turn, outside the steps (the proof of what was verified).
  card(display) {
    const node = renderCard(display, { conversation: state.activeId });
    if (!node) return;
    this.body.appendChild(node);
    if (nearBottom()) scrollToEnd();
  }

  finish(content, { isLast = false } = {}) {
    this.clearWaiting();
    this.closeThinking();
    this.endText();
    this.el.classList.remove('streaming');
    const text = content || this.fullText;
    if (!this.stored && !this.finished) {
      this.finished = true;
      mood('idle');
      if (text && !this.failed) {
        mood('happy', 1800);
        if (buddy) buddy.hop();
      }
    }
    clear(this.actions);
    if (text) {
      this.actions.append(
        h('button.act', { title: 'Copy', onclick: () => copyText(text).then(() => toast('Copied')) }, icon('copy'), 'Copy'),
        speakButton(text),
      );
    }
    if (isLast) {
      this.actions.append(h('button.act', { title: 'Try again', onclick: () => retryLast() }, icon('retry'), 'Retry'));
      $$last();
      this.el.classList.add('last');
    }
    wireCode(this.el);
    decorateAnswer(this.el, text, { live: !this.stored }); // plugins: buttons under answers, 'answer' hooks
  }
}

// Syntax colours and copy buttons for every settled block of text (wet ink is wired once it dries).
function wireCode(root) {
  const blocks = root.matches('.md') ? [root] : root.querySelectorAll('.md');
  for (const md of blocks) {
    if (md.classList.contains('wet')) continue;
    highlight(md);
    enhanceMarkdown(md);
    md.querySelectorAll('.copy-code:not([data-wired])').forEach((b) => {
      b.dataset.wired = '';
      b.addEventListener('click', () => copyText(b.closest('pre').querySelector('code').textContent).then(() => {
        toast('Code copied');
        b.classList.remove('copied');
        void b.offsetWidth;
        b.classList.add('copied');
      }));
    });
  }
}

// a drop of ink spreads from the send button
function splash() {
  const drop = h('span.splash');
  drop.addEventListener('animationend', () => drop.remove());
  els.send.prepend(drop);
}

function $$last() {
  els.messages.querySelectorAll('.msg.last').forEach((n) => n.classList.remove('last'));
}

function speakButton(text) {
  const btn = h('button.act', { title: 'Read aloud' }, icon('speaker'), 'Listen');
  btn.addEventListener('click', async () => {
    if (btn.classList.contains('on')) {
      stopSpeaking();
      btn.classList.remove('on');
      return;
    }
    document.querySelectorAll('.act.on').forEach((b) => b.classList.remove('on'));
    btn.classList.add('on');
    audioContext();
    await speak(text, { onError: (e) => toast('Using the device voice', { body: e.message, type: 'warn' }) });
    btn.classList.remove('on');
  });
  return btn;
}

// What a review card says: a plan, the tests of tests-first mode, or a change to an approved test.
const REVIEW_WORDS = {
  plan: { icon: 'route', title: 'Review the plan', sub: 'Nothing has changed yet. Approve to start building, or say what to change.', ok: 'Approve & build' },
  tests: { icon: 'check', title: 'Review the tests', sub: 'Only tests were written, and they fail for now. Approve them as the contract: the agent then builds until they pass and cannot change them without asking.', ok: 'Approve tests & build' },
  test_change: { icon: 'edit', title: 'Change an approved test?', sub: 'The agent thinks this approved test is wrong. Allow the change, or keep the test as it is.', ok: 'Allow the change' },
};

// The coder's plan (or tests), to approve, change in words, or reject (plan mode, Antigravity-style review).
function planCard(ev) {
  const sum = ev.summary || {};
  const result = h('div.ap-result');
  const note = h('textarea.input.plan-note', { rows: 2, placeholder: 'What should change? (optional when approving)' });
  const decide = async (decision) => {
    const text = note.value.trim();
    if (decision === 'deny' && !text && !(await confirmSheet('Reject the plan?', 'The agent stops. To get a better plan instead, write what should change and press Request changes.', { confirm: 'Reject', danger: true }))) return;
    card.querySelectorAll('button, textarea').forEach((b) => (b.disabled = true));
    try {
      await api.post('/api/approvals/' + ev.id, { decision, note: text });
    } catch (err) {
      card.querySelectorAll('button, textarea').forEach((b) => (b.disabled = false));
      toast(err.message, 'error');
    }
  };
  const body = h('div.plan-body.md');
  body.innerHTML = renderMarkdown(sum.plan || '');
  const words = REVIEW_WORDS[sum.kind] || REVIEW_WORDS.plan;
  const card = h(
    'div.approval.plan-review',
    { dataset: { approval: ev.id } },
    h('div.ap-head', icon(words.icon), sum.title || words.title),
    h('div.ap-sub', words.sub),
    body,
    h('div.ap-actions.col', note, h('div.ap-actions',
      h('button.btn.primary.small', { onclick: () => decide('allow') }, icon('check'), words.ok),
      h('button.btn.small', { onclick: () => (note.value.trim() ? decide('deny') : (note.focus(), toast('Write what should change first', 'warn'))) }, icon('edit'), 'Request changes'),
      h('button.btn.quiet.small', { onclick: () => decide('deny') }, 'Reject'),
    )),
    result,
  );
  return card;
}

function approvalCard(ev) {
  if (['submit_plan', 'submit_tests', 'propose_test_change'].includes(ev.tool)) return planCard(ev);
  const sum = ev.summary || {};
  const args = sum.args || ev.args || {};
  const pretty = args.command || args.code || JSON.stringify(args, null, 2);
  const result = h('div.ap-result');
  const card = h(
    'div.approval',
    { dataset: { approval: ev.id } },
    h('div.ap-head', icon('shield'), sum.title || 'Allow this action?'),
    h('div.ap-sub', `Tool: ${ev.tool}. ${sum.why ? sum.why + ' ' : ''}Scorpi paused until you decide.`),
    h('pre', pretty),
    h(
      'div.ap-actions',
      h('button.btn.primary.small', { onclick: () => decide('allow') }, icon('check'), 'Allow once'),
      h('button.btn.small', { onclick: () => decide('always') }, 'Always allow ' + ev.tool.replace(/_/g, ' ')),
      h('button.btn.quiet.small', { onclick: () => decide('deny') }, 'Deny'),
    ),
    result,
  );
  async function decide(decision) {
    card.querySelectorAll('button').forEach((b) => (b.disabled = true));
    try {
      await api.post('/api/approvals/' + ev.id, { decision });
    } catch (err) {
      toast(err.message, 'error');
    }
  }
  return card;
}

// A question from a background agent: pick an option or type an answer; it goes straight back to the agent.
export function questionCard(ev) {
  const result = h('div.ap-result');
  const answers = {};
  const fields = ev.fields && ev.fields.length ? ev.fields : [{ key: 'q0', title: '', description: ev.title, options: [], custom: true }];
  const send = async () => {
    if (fields.some((f) => !String(answers[f.key] || '').trim())) return toast('Answer every question first', 'warn');
    card.querySelectorAll('button, input').forEach((b) => (b.disabled = true));
    try {
      await api.post('/api/questions/' + ev.id, { answers });
    } catch (err) {
      card.querySelectorAll('button, input').forEach((b) => (b.disabled = false));
      toast(err.message, 'error');
    }
  };
  const rows = fields.map((f) => {
    const input = f.custom ? h('input.input', { placeholder: f.options.length ? 'Or type your own answer' : 'Your answer', oninput: () => { answers[f.key] = input.value; opts.querySelectorAll('.on').forEach((b) => b.classList.remove('on')); }, onkeydown: (e) => { if (e.key === 'Enter') send(); } }) : null;
    const opts = h('div.ap-actions', f.options.map((o) => h('button.btn.small', {
      title: o.description || '',
      onclick: (e) => {
        answers[f.key] = o.value;
        opts.querySelectorAll('.on').forEach((b) => b.classList.remove('on'));
        e.currentTarget.classList.add('on');
        if (input) input.value = '';
        if (fields.length === 1) send();
      },
    }, o.label)));
    return h('div.q-field', f.title && f.title !== f.description ? h('div.q-title', f.title) : null, h('div.ap-sub', f.description || ''), f.options.length ? opts : null, input);
  });
  const card = h(
    'div.approval.question',
    { dataset: { question: ev.id } },
    h('div.ap-head', icon('chat'), ev.title && ev.title !== 'Questions' ? ev.title : 'A question for you'),
    ...rows,
    fields.length > 1 || fields.some((f) => f.custom) ? h('div.ap-actions', h('button.btn.primary.small', { onclick: send }, icon('check'), 'Send answer')) : null,
    result,
  );
  return card;
}

export function resolveQuestionCard(id, answer) {
  document.querySelectorAll(`[data-question="${id}"]`).forEach((card) => {
    if (card.classList.contains('resolved')) return;
    card.classList.add('resolved');
    card.querySelectorAll('.ap-actions, input').forEach((n) => n.remove());
    card.querySelector('.ap-result').textContent = answer ? 'You answered: ' + answer : 'No longer needed.';
  });
}

export function resolveApprovalCard(id, decision, note = '') {
  document.querySelectorAll(`[data-approval="${id}"]`).forEach((card) => {
    if (card.classList.contains('resolved')) return;
    card.classList.add('resolved');
    const actions = card.querySelector('.ap-actions');
    if (actions) actions.remove();
    const plan = card.classList.contains('plan-review');
    card.querySelectorAll('.plan-note').forEach((n) => n.remove());
    card.querySelector('.ap-result').textContent = plan
      ? (decision === 'deny' ? (note ? 'Changes requested: ' + note : 'Plan rejected.') : 'Plan approved' + (note ? ': ' + note : '') + '. Building it now.')
      : decision === 'deny' ? 'Declined.' : decision === 'always' ? 'Allowed (always).' : 'Allowed.';
  });
}

function handleEvent(view, ev) {
  try {
    applyEvent(view, ev);
    if (talker && talker.view === view) talker.event(ev);
    if (ev.type === 'tool_start' || ev.type === 'tool_end') toolEvent(ev); // plugins' 'tool' hooks
  } catch (err) {
    console.error(err);
  }
}

// ---------- reading replies aloud ----------

const plain = (text) => String(text || '')
  .replace(/```[\s\S]*?(```|$)/g, ' ')
  .replace(/`[^`]*`/g, ' ')
  .replace(/(?:[A-Za-z]:\\|\/)[^\s,;:)]*[\\/][^\s,;:)]*/g, ' ') // file paths are for reading, not for listening
  .replace(/^\s*[#>|-].*$/gm, (l) => (/^\s*-\s/.test(l) ? l : ' '))
  .replace(/\s+/g, ' ')
  .trim();

function firstSentences(text, n = 2, max = 260) {
  const parts = plain(text).match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g) || [];
  let out = '';
  for (const p of parts.slice(0, n)) {
    if ((out + p).length > max && out) break;
    out += p;
  }
  return out.trim();
}

/** A conversation where a background agent works (a coding project, an agent's job) rather than the chat assistant. */
const agentConversation = () => !!state.project || (currentConv() && currentConv().origin === 'agent');

/**
 * Speaks a live turn. In a plain conversation it reads the reply as it streams. When an agent works (Code), it only
 * says what matters: that it started, questions and approvals it needs, problems, the answer to something you asked
 * while it worked, and a short summary at the end, never the code it writes or each step it takes.
 */
function makeTalker(view, { brief = agentConversation() } = {}) {
  if (!voiceOn() && !prefs.autoSpeak) return null;
  const sp = createStreamSpeaker({ onError: (e) => toast('Using the device voice', { body: e.message, type: 'warn' }) });
  let acked = false;
  let reply = null; // collecting the agent's answer to something said while it worked
  const flushReply = () => {
    if (reply && reply.trim()) sp.say(firstSentences(reply, 3, 320));
    reply = null;
  };
  return {
    view,
    brief,
    event(ev) {
      if (!brief) {
        if (ev.type === 'token') sp.push(view.fullText);
        else if (ev.type === 'approval' && voiceOn()) sp.say('I need your OK first. Please check the screen.');
        return;
      }
      if (ev.type === 'token' && reply !== null) reply += ev.content;
      else if (ev.type === 'tool_start') {
        flushReply();
        if (!acked) {
          acked = true;
          sp.say('On it. I’ll tell you when there’s something to hear.');
        }
      } else if (ev.type === 'question') {
        const q = (ev.fields || []).map((f) => [f.description || f.title, (f.options || []).length ? 'Options: ' + f.options.map((o) => o.label).join(', ') + '.' : ''].join(' ')).join(' ');
        sp.say('Question for you. ' + (q || ev.title || ''));
      } else if (ev.type === 'approval') {
        sp.say('I need your OK to ' + ((ev.summary && ev.summary.title) || ev.tool.replace(/_/g, ' ')).replace(/[.?!]$/, '') + '. Check the screen.');
      } else if (ev.type === 'error') {
        sp.say('Something went wrong. ' + firstSentences(ev.error, 1));
      }
    },
    expectReply() {
      reply = '';
    },
    finish(content) {
      if (!brief) return sp.finish(content);
      if (reply !== null && reply.trim()) reply = null; // the summary covers it
      const summary = firstSentences(content, 2);
      if (summary) sp.say('Done. ' + summary);
      return Promise.resolve();
    },
    skip(text) {
      if (!brief) sp.push(text);
      sp.interrupt();
    },
    interrupt() {
      sp.interrupt();
    },
    cancel() {
      sp.cancel();
    },
  };
}

function applyEvent(view, ev) {
  switch (ev.type) {
    case 'start':
      view.runId = ev.run_id;
      break;
    case 'thinking':
      view.thinking(ev.content);
      break;
    case 'token':
      view.token(ev.content);
      break;
    case 'tool_start':
      view.toolStart(ev);
      break;
    case 'tool_end':
      view.toolEnd(ev);
      break;
    case 'card':
      view.card(ev.display);
      break;
    case 'approval':
      view.approval(ev);
      break;
    case 'approval_done':
      resolveApprovalCard(ev.id, ev.decision, ev.note);
      break;
    case 'question':
      view.question(ev);
      break;
    case 'question_done':
      resolveQuestionCard(ev.id, ev.answer);
      break;
    case 'status':
      view.status(ev.text);
      break;
    case 'switch':
      // the provider was busy or too slow: say which model took over instead of silently answering
      view.note(ev.text || ('Using ' + ev.model));
      break;
    case 'notice':
      // the agent confirms a message sent while it worked; it is already shown where it was said
      if (/^Message received: /.test(ev.text) && (view.asides || []).some((a) => ev.text.slice(18).startsWith(a.slice(0, 120)))) break;
      view.note(ev.text);
      break;
    case 'error':
      view.error(ev.error, ev.resume_at || 0);
      break;
    default:
      break;
  }
}

function detachLive() {
  if (controller) controller.abort();
  controller = null;
  live = null;
  setBusy(false);
}

async function attachRun(runId, convId) {
  const view = new TurnView();
  els.messages.appendChild(view.el);
  live = { view, runId, convId };
  setBusy(true);
  mood('thinking');
  controller = new AbortController();
  // joining a turn already under way: only what happens from now on is spoken
  let caughtUp = false;
  const ownTalker = makeTalker(view, { brief: true });
  try {
    await streamJSON(`/api/runs/${runId}/stream?from=0`, {
      method: 'GET',
      signal: controller.signal,
      onEvent: (ev) => {
        if (!caughtUp && ev.type !== 'run') {
          caughtUp = true;
          setTimeout(() => {
            if (live && live.view === view && ownTalker) talker = ownTalker;
          }, 400); // replayed history arrives in one burst first
        }
        if (ev.type === 'done') {
          view.finish(ev.content, { isLast: true });
          if (talker && talker.view === view) talker.finish(ev.content);
          return;
        }
        handleEvent(view, ev);
      },
    });
  } catch {
    /* aborted */
  }
  if (live && live.view === view) {
    live = null;
    setBusy(false);
    scheduleThreads();
    if (state.project) refreshProject();
  }
}

// ---------- sending ----------

/** Say something to the turn that is working right now, without stopping it (typed, or spoken in voice mode). */
async function interject(text) {
  if (!live) return;
  const view = live.view;
  if (!live.runId) {
    toast('One moment', { body: 'It is just starting; say it again in a second.', timeout: 2500 });
    return;
  }
  view.aside(text);
  if (talker && talker.view === view) {
    talker.interrupt();
    talker.expectReply();
  }
  try {
    const res = await api.post(`/api/runs/${live.runId}/message`, { text });
    if (res.how === 'answered') toast('Answer sent', { timeout: 1800 });
  } catch (err) {
    toast('Could not reach it', { type: 'error', body: err.message });
  }
}

const STOP_WORDS = /^(stop|stop it|cancel|never ?mind|halt|abort|enough|quiet|be quiet|stopp|hör auf|abbrechen|ruhe)[.!]*$/i;

function onUtterance(text) {
  if (live) {
    if (STOP_WORDS.test(text.trim())) return stop();
    return interject(text);
  }
  if (STOP_WORDS.test(text.trim())) return; // nothing to stop
  send(text);
}

export async function send(textOverride, opts = {}) {
  const typed = typeof textOverride === 'string' ? textOverride : els.input.value;
  if (live) {
    // while it works, what you write goes to it right away; the stop button stops it
    if (!typed.trim()) return stop();
    if (typeof textOverride !== 'string') {
      els.input.value = '';
      autosize();
      updateSend();
    }
    return interject(typed.trim());
  }
  let text = typed.trim();
  const pending = composer.attachments.filter((a) => !a.failed);
  if (pending.some((a) => !a.id)) {
    toast('Still uploading…', 'warn');
    return;
  }
  if (!text && !pending.length && !pluginCommands.get(composer.skill)) return;
  sfx('send');
  // plugins' 'send' hooks may rewrite the message or stop it (then it stays in the box)
  if (hasHooks('send') && text && !opts.display) {
    if (send.hooking) return; // a slow hook is still deciding about the last Enter
    send.hooking = true;
    let out;
    try {
      out = await runHooks('send', { text, attachments: pending.length, conversation: state.activeId, skill: composer.skill });
    } finally {
      send.hooking = false;
    }
    if (!out || typeof out.text !== 'string') return;
    text = out.text.trim();
    if (!text && !pending.length) return;
  }

  let body = text;
  let skill = composer.skill;
  const slash = text.match(/^\/([\w-]+)\s*([\s\S]*)$/);
  const chosen = !skillById(skill) && pluginCommands.get(skill);
  const cmd = chosen || (slash && !skillById(slash[1].toLowerCase()) && pluginCommands.get(slash[1].toLowerCase()));
  if (cmd && !pending.length && !opts.display) {
    const args = chosen ? text : slash[2].trim();
    els.input.value = '';
    autosize();
    updateSend();
    composer.skill = '';
    updateSkillChip();
    try {
      await cmd.run(args);
    } catch (err) {
      toast('/' + cmd.name + ' failed', { type: 'error', body: err.message });
    }
    return;
  }
  if (opts.command) skill = '';
  if (slash) {
    const s = state.skills.find((x) => x.id === slash[1].toLowerCase() || x.name.toLowerCase() === slash[1].toLowerCase());
    if (s) {
      skill = s.id;
      body = slash[2].trim() || `Let's start a ${s.name.toLowerCase()} session.`;
    }
  }

  if (composer.editFrom && state.activeId) {
    await api.post(`/api/conversations/${state.activeId}/truncate`, { message_id: composer.editFrom });
    const node = els.messages.querySelector(`[data-id="${composer.editFrom}"]`);
    if (node) {
      let n = node;
      while (n) {
        const next = n.nextSibling;
        n.remove();
        n = next;
      }
    }
    composer.editFrom = null;
  }

  const attachments = pending.map(({ id, name, mime, size, kind }) => ({ id, name, mime, size, kind }));
  if (typeof textOverride !== 'string') els.input.value = '';
  composer.attachments = [];
  renderAttachments();
  autosize();
  hideSlash();
  stopSpeaking();

  if (!state.activeId) {
    els.view.classList.add('has-convo');
    clear(els.messages);
  }
  const project = state.project;
  const firstMode = agentConversation() ? (composer.plan ? 'plan' : composer.tests ? 'tests' : '') : '';
  if (composer.plan || composer.tests) {
    composer.plan = composer.tests = false;
    updateModeChip();
  }
  const userMsg = { id: 'local-' + Date.now(), content: opts.display || body, data: { attachments, command: opts.command || '' } };
  els.messages.querySelectorAll('.msg.last').forEach((n) => n.classList.remove('last'));
  const fresh = userNode(userMsg);
  fresh.classList.add('fresh');
  els.messages.appendChild(fresh);
  splash();
  if (buddy) buddy.hop();
  mood('thinking');
  const view = new TurnView();
  els.messages.appendChild(view.el);
  scrollToEnd(true);
  setBusy(true);
  controller = new AbortController();
  live = { view, convId: state.activeId };
  talker = makeTalker(view);
  const mine = talker;
  let finished = false;

  try {
    await streamJSON('/api/chat', {
      body: {
        conversation_id: state.activeId, text: body, display: opts.display || '', command: opts.command || '', attachments, skill, groups: composer.groups, model: state.chatModel || '',
        project: project ? project.slug : '', title: project ? project.name : '', device: deviceId(), mode: firstMode,
      },
      signal: controller.signal,
      onEvent: (ev) => {
        if (ev.type === 'run') {
          live.runId = ev.run_id;
          if (!state.activeId || state.activeId !== ev.conv_id) {
            state.activeId = ev.conv_id;
            // a project keeps its own address; its conversation is the agent's
            if (!project) history.replaceState({}, '', '/c/' + ev.conv_id);
            else els.messages.querySelectorAll('.project-intro').forEach((n) => n.remove());
            loadThreads().then(updateHead);
            if (project) refreshProject();
          }
          return;
        }
        if (ev.type === 'start') {
          userMsg.id = ev.user_message_id;
          const node = els.messages.querySelector('[data-id^="local-"]');
          if (node) node.dataset.id = ev.user_message_id;
        }
        if (ev.type === 'done') {
          finished = true;
          view.finish(ev.content, { isLast: true });
          if (mine && talker === mine) mine.finish(ev.content);
          else sfx('done'); // not on top of Scorpi's own voice
          return;
        }
        handleEvent(view, ev);
      },
    });
  } catch (err) {
    if (err.name !== 'AbortError') view.error(err.message || 'Connection lost.');
  }
  if (!finished) {
    view.finish(view.fullText, { isLast: true });
    if (mine && talker === mine) mine.cancel();
  }
  live = null;
  controller = null;
  setBusy(false);
  if (buddy && ['thinking', 'writing', 'working', 'alert'].includes(buddy.base)) mood('idle');
  composer.skill = '';
  updateSkillChip();
  scheduleThreads();
  if (project && state.project === project) refreshProject();
}

export async function stop() {
  if (!live) return;
  const runId = live.runId;
  // free the composer at once: waiting for the cancel request (or for the server) left it busy for seconds
  if (controller) controller.abort();
  setBusy(false);
  if (runId) api.post(`/api/runs/${runId}/cancel`).catch(() => {});
}

async function retryLast() {
  if (!state.activeId || live) return;
  const users = els.messages.querySelectorAll('.msg.user');
  const last = users[users.length - 1];
  if (!last) return;
  const data = await api.get('/api/conversations/' + state.activeId);
  const msg = data.messages.filter((m) => m.role === 'user' && !(m.data && (m.data.note || m.data.system))).pop();
  if (!msg) return;
  composer.editFrom = msg.id;
  send(msg.content);
}

function setBusy(on) {
  document.body.classList.toggle('busy', on);
  els.app.classList.toggle('busy', on);
  updateSend();
  voiceRefresh();
  if (!on && talker && !voiceOn() && !prefs.autoSpeak) talker = null;
}

// ---------- scrolling ----------

// The chat sticks to the bottom until you scroll up: answers streaming in, pictures and code that render late, a
// conversation just opened and coming back from another page all keep the latest message in view.
let stick = true;

function nearBottom() {
  const s = els.scroller;
  return s.scrollHeight - s.scrollTop - s.clientHeight < 120;
}

function scrollToEnd(force = false) {
  const s = els.scroller;
  if (force) stick = true;
  if (force || stick || nearBottom()) s.scrollTop = s.scrollHeight;
  updateJump();
}

function updateJump() {
  const away = !!state.activeId && !stick && !nearBottom();
  els.jump.classList.toggle('hidden', !away);
  els.jump.classList.toggle('news', away && !!live); // something is still arriving below
}

function initScrolling() {
  const s = els.scroller;
  // only a person scrolling changes whether it sticks (layout jumps, e.g. the chat being hidden and shown, don't)
  let byHand = 0;
  const hand = () => (byHand = Date.now());
  s.addEventListener('wheel', hand, { passive: true });
  s.addEventListener('touchstart', hand, { passive: true });
  s.addEventListener('pointerdown', (e) => e.offsetX > s.clientWidth && hand()); // the scrollbar
  s.addEventListener('keydown', hand);
  s.addEventListener('scroll', () => {
    if (Date.now() - byHand < 400) {
      byHand = Date.now();
      stick = s.scrollHeight - s.scrollTop - s.clientHeight < 24;
    }
    updateJump();
  }, { passive: true });
  // content growing (late renders) or the chat showing again after another page (display:none forgets the position)
  const follow = () => {
    if (stick && s.clientHeight) s.scrollTop = s.scrollHeight;
    updateJump();
  };
  new ResizeObserver(follow).observe(els.messages);
  new ResizeObserver(follow).observe(s);
  els.jump.addEventListener('click', () => {
    stick = true;
    s.scrollTo({ top: s.scrollHeight, behavior: 'smooth' });
    updateJump();
  });
}

// ---------- composer ----------

function autosize() {
  const t = els.input;
  t.style.height = 'auto';
  t.style.height = Math.min(t.scrollHeight, innerHeight * 0.4) + 'px';
}

function updateSend() {
  const typed = !!els.input.value.trim();
  // a plugin command picked as the skill runs even without words
  const has = typed || composer.attachments.length || (!skillById(composer.skill) && pluginCommands.has(composer.skill));
  els.send.disabled = !live && !has;
  // while it works: typing turns the stop button back into send, and the message reaches it without stopping it
  els.send.classList.toggle('interject', !!live && typed);
  els.send.title = live ? (typed ? 'Send while it works (Enter)' : 'Stop (Esc)') : 'Send (Enter)';
}

export function setSkill(id) {
  composer.skill = id || '';
  updateSkillChip();
  updateSend();
}

/** A skill, or a slash command a plugin added (Studio → plugins: scorpi.command), shaped like a skill. */
function anySkill(id) {
  const s = skillById(id);
  if (s || !id) return s;
  const c = pluginCommands.get(id);
  return c ? { id: c.name, name: c.name.charAt(0).toUpperCase() + c.name.slice(1), description: c.description, icon: c.icon || 'puzzle', command: true } : null;
}
const pluginSkills = () => [...pluginCommands.keys()].filter((k) => !skillById(k)).map(anySkill);

const placeholderFor = (s) => (s ? `${s.name}: ${s.description}` : isMobile() ? 'Ask anything…' : 'Ask anything, or type / for a skill');

// ---------- how an agent works: plan first, and what it may do without asking (scorpi/safety.py) ----------

const AUTONOMY = [
  ['ask', 'Ask first', 'shield', 'Asks before every command and code run.'],
  ['auto', 'Auto', 'bolt', 'Builds, tests and edits the project itself; asks for the rest.'],
  ['full', 'Full auto', 'wand', 'Does everything except destructive commands.'],
];

function updateModeChip() {
  if (!els.modeBtn) return;
  const show = agentConversation();
  els.modeBtn.classList.toggle('hidden', !show);
  if (!show) return;
  const level = AUTONOMY.find(([k]) => k === (state.settings && state.settings.autonomy)) || AUTONOMY[1];
  els.modeLabel.textContent = composer.plan ? 'Plan first' : composer.tests ? 'Tests first' : level[1];
  els.modeBtn.classList.toggle('set', composer.plan || composer.tests || level[0] !== 'auto');
  els.modeBtn.querySelector('use').setAttribute('href', '#i-' + (composer.plan ? 'route' : composer.tests ? 'check' : level[2]));
}

async function setAutonomy(value) {
  try {
    state.settings = await api.post('/api/settings', { autonomy: value });
    bus.emit('settings', state.settings);
  } catch (err) {
    toast(err.message, 'error');
  }
  updateModeChip();
}

function modeMenu() {
  if (menuKind === 'mode') return closeComposerMenu();
  const current = (state.settings && state.settings.autonomy) || 'auto';
  composerMenu('mode', '', [
    { name: 'Plan first', desc: 'Next message: approve a plan before anything is built.', icon: 'route', checked: composer.plan,
      onPick: () => { composer.plan = !composer.plan; composer.tests = false; updateModeChip(); } },
    { name: 'Tests first', desc: 'Next message: approve the tests, then it builds until they pass.', icon: 'check', checked: composer.tests,
      onPick: () => { composer.tests = !composer.tests; composer.plan = false; updateModeChip(); } },
    state.project ? { name: 'Try several models', desc: 'Each model does it in a copy; keep the best one.', icon: 'bolt', onPick: () => startAttempts(els.input.value.trim()) } : null,
    'sep',
    ...AUTONOMY.map(([key, name, ic, desc]) => ({ name, desc, icon: ic, checked: current === key, onPick: () => setAutonomy(key) })),
  ]);
}

/** Shift+Tab: Auto → Plan first → Tests first → Full auto → Ask first → Auto. */
function cycleMode() {
  const current = (state.settings && state.settings.autonomy) || 'auto';
  if (composer.plan) {
    composer.plan = false;
    composer.tests = true;
    updateModeChip();
  } else if (composer.tests) {
    composer.tests = false;
    setAutonomy('full');
  } else if (current === 'auto') {
    composer.plan = true;
    updateModeChip();
  } else {
    setAutonomy(current === 'full' ? 'ask' : 'auto');
  }
}

function updateSkillChip() {
  updateModeChip();
  const job = agentJob();
  const s = job ? null : anySkill(composer.skill);
  els.skillBtn.classList.toggle('set', !!s || !!job);
  els.skillLabel.textContent = job ? job.agent_name : s ? s.name : 'Skill';
  els.skillBtn.title = job ? `This chat is ${job.agent_name}'s: what you write goes to the agent` : 'Pick a skill';
  if (!state.project && !job) els.input.placeholder = placeholderFor(s);
}

// ---------- Write / Voice ----------

export function setInputMode(mode) {
  if (mode === composer.io) return;
  if (mode === 'voice') {
    if (dictation) dictation.stop();
    const ok = startVoice({
      onUtterance,
      onBargeIn: () => talker && talker.interrupt(),
      onStop: () => setInputMode('write'),
      busy: () => !!live,
    });
    if (!ok || !voiceOn()) return; // no microphone after all
    pauseWake('voice');
    // a turn already under way is spoken from here on
    if (live && !(talker && talker.view === live.view)) {
      talker = makeTalker(live.view);
      if (talker) talker.skip(live.view.fullText);
    }
  } else {
    stopVoice();
    resumeWake('voice');
    if (talker && !prefs.autoSpeak) {
      talker.cancel();
      talker = null;
    }
  }
  composer.io = mode;
  setPref('inputMode', mode);
  els.form.classList.toggle('voice', mode === 'voice');
  els.ioToggle.querySelectorAll('button').forEach((b) => {
    b.classList.toggle('on', b.dataset.io === mode);
    b.setAttribute('aria-checked', String(b.dataset.io === mode));
  });
  if (mode === 'write' && !isMobile()) els.input.focus();
}

/** "Hey Scorpi": voice on in the conversation that is open, and what was said with it is the first thing said. */
export function wakeVoice(rest = '') {
  const go = () => {
    setInputMode('voice');
    if (rest && voiceOn()) onUtterance(rest);
  };
  if (document.querySelector('.view.active[data-view="chat"]')) return go();
  navigate('/');
  setTimeout(go, 150);
}

// Skill and tool pickers open in the same panel above the composer as the / menu.
let menuClose = null;
let menuKind = '';

function closeComposerMenu() {
  if (!menuClose) return;
  menuClose();
  menuClose = null;
  menuKind = '';
  els.slash.classList.add('hidden');
  placeMenu(null);
}

// The panel spans the prompt box (the / menu, Skill, Tools, Model), or sits above a chip when one is given as anchor.
// It lives in the top layer, not inside the prompt box: glass inside glass can't blur what is behind the page.
let menuOwner = null; // the prompt box the panel belongs to
let menuAnchor = null;
function placeMenu(anchor) {
  const box = els.slash;
  if (!menuOwner) {
    menuOwner = box.parentElement;
    $('#layer').appendChild(box);
    // the window or the phone's keyboard changed size: follow the prompt box
    addEventListener('resize', () => !box.classList.contains('hidden') && placeMenu(menuAnchor));
  }
  menuAnchor = anchor;
  if (box.classList.contains('hidden')) return; // placed when it opens
  const c = menuOwner.getBoundingClientRect();
  const side = innerWidth <= 560 || !anchor;
  box.classList.toggle('anchored', !side);
  box.style.bottom = Math.round(innerHeight - c.top + 10) + 'px';
  box.style.width = side ? Math.round(c.width - 16) + 'px' : '';
  box.style.maxHeight = '';
  // never taller than the room above the prompt box
  const room = Math.floor(c.top - 22);
  if (room < box.offsetHeight) box.style.maxHeight = Math.max(150, room) + 'px';
  const w = box.offsetWidth;
  let left = c.left + 8;
  if (!side) {
    const a = anchor.getBoundingClientRect();
    left = a.left + a.width / 2 > c.left + c.width / 2 ? a.right - w : a.left;
  }
  box.style.left = Math.round(Math.max(8, Math.min(left, innerWidth - w - 8))) + 'px';
}

/** rows: {name, desc, meta, icon, checked, keep, compact, onPick} | 'sep' | {title} */
function composerMenu(kind, title, rows, { anchor = null, search = '' } = {}) {
  closeComposerMenu();
  slashItems = [];
  clear(els.slash);
  els.slash.dataset.kind = kind;
  // no title (Skill, Tools): a plain list over the whole prompt box, exactly like the / menu
  const head = h('div.slash-head', title ? h('div.slash-title', title) : null);
  if (title || search) els.slash.appendChild(head);
  const list = h('div.slash-list');
  for (const r of rows) {
    if (!r) continue;
    if (r === 'sep') {
      list.appendChild(h('div.slash-sep'));
      continue;
    }
    if (r.title) {
      list.appendChild(h('div.slash-group', r.title));
      continue;
    }
    const row = h(
      'div.slash-item' + (r.checked ? '.on' : '') + (r.compact ? '.compact' : ''),
      { title: r.compact ? r.name + (r.meta ? ' · ' + r.meta : '') : '', onmousedown: (e) => { e.preventDefault(); r.onPick(row); if (!r.keep) closeComposerMenu(); } },
      r.compact ? null : icon(r.icon || 'spark'),
      r.compact
        ? h('div.si-text', h('span.si-name', r.name), r.meta ? h('span.si-meta', r.meta) : null)
        : h('div.si-text', h('div.si-name', r.name), r.desc ? h('div.si-desc', r.desc) : null),
      h('span.si-check', icon('check')),
    );
    list.appendChild(row);
  }
  els.slash.appendChild(list);
  let input = null;
  if (search) {
    // long lists (models): type to filter; groups left without matches hide; Enter picks the first match
    input = h('input.slash-search', { type: 'search', placeholder: search, autocomplete: 'off', spellcheck: false });
    head.appendChild(input);
    input.addEventListener('input', () => {
      const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      let group = null;
      let shown = 0;
      for (const el of list.children) {
        if (el.classList.contains('slash-group')) {
          if (group) group.hidden = !shown;
          group = el;
          shown = 0;
        } else if (el.classList.contains('slash-item')) {
          el.hidden = !words.every((w) => (el.textContent + ' ' + (group ? group.textContent : '')).toLowerCase().includes(w));
          if (!el.hidden) shown++;
        } else el.hidden = words.length > 0;
      }
      if (group) group.hidden = !shown;
    });
    input.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const first = [...list.querySelectorAll('.slash-item')].find((el) => !el.hidden);
      if (first) first.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    });
  }
  els.slash.classList.remove('hidden');
  placeMenu(anchor);
  // the prompt box may still be settling (greeting animation, keyboard): measure again once it has
  requestAnimationFrame(() => menuKind === kind && placeMenu(anchor));
  setTimeout(() => menuKind === kind && placeMenu(anchor), 400);
  if (input && !isMobile()) input.focus({ preventScroll: true });
  const sel = list.querySelector('.slash-item.on');
  if (sel && search) sel.scrollIntoView({ block: 'nearest' });
  menuKind = kind;
  const away = (e) => {
    if (!els.slash.contains(e.target) && !e.target.closest('#skillBtn, #toolsBtn, #modelBtn, #modeBtn')) closeComposerMenu();
  };
  const esc = (e) => {
    if (e.key === 'Escape') closeComposerMenu();
  };
  document.addEventListener('pointerdown', away, true);
  document.addEventListener('keydown', esc);
  menuClose = () => {
    document.removeEventListener('pointerdown', away, true);
    document.removeEventListener('keydown', esc);
  };
}

function skillMenu() {
  if (menuKind === 'skill') return closeComposerMenu();
  if (agentJob()) return navigate('/agents');
  composerMenu('skill', '', [
    { name: 'No skill', desc: 'Plain conversation with automatic tools', icon: 'chat', checked: !composer.skill, onPick: () => setSkill('') },
    ...[...state.skills, ...pluginSkills()].map((s) => ({ name: '/' + s.id, desc: s.description, icon: s.icon || 'spark', checked: composer.skill === s.id, onPick: () => setSkill(s.id) })),
    'sep',
    { name: 'Create a skill…', icon: 'plus', onPick: () => navigate('/connections#skills') },
  ]);
}

const GROUP_ICONS = {
  files: 'folder', docs: 'doc', code: 'terminal', web: 'globe', data: 'chart', media: 'image', computer: 'monitor',
  system: 'cpu', maps: 'map', agenda: 'calendar', records: 'table', knowledge: 'book', agents: 'agent', learning: 'spark',
};

async function toolsMenu() {
  if (menuKind === 'tools') return closeComposerMenu();
  let groups = [];
  try {
    groups = (await api.get('/api/tools')).groups.filter((g) => g.id !== 'core');
  } catch {
    return;
  }
  const chosen = new Set(composer.groups || []);
  let autoRow = null;
  composerMenu('tools', '', [
    { name: 'Automatic', desc: 'Picks tool groups from what you ask', icon: 'wand', checked: !composer.groups, onPick: () => { composer.groups = null; updateToolsChip(); } },
    'sep',
    ...groups.map((g) => ({
      name: g.name,
      desc: g.description,
      icon: GROUP_ICONS[g.id] || 'bolt',
      checked: chosen.has(g.id),
      keep: true,
      onPick: (row) => {
        if (chosen.has(g.id)) chosen.delete(g.id);
        else chosen.add(g.id);
        composer.groups = chosen.size ? [...chosen] : null;
        row.classList.toggle('on', chosen.has(g.id));
        autoRow = autoRow || els.slash.querySelector('.slash-item');
        if (autoRow) autoRow.classList.toggle('on', !composer.groups);
        updateToolsChip();
      },
    })),
  ]);
}

function updateToolsChip() {
  els.toolsLabel.textContent = composer.groups ? `${composer.groups.length} group${composer.groups.length > 1 ? 's' : ''}` : 'Auto';
  els.toolsBtn.classList.toggle('set', !!composer.groups);
}

// the model list takes the server a moment (it asks every provider): keep it, so the picker opens at once
const modelCache = { at: 0, list: null, pending: null };
function fetchModels() {
  modelCache.pending ||= api.get('/api/models')
    .then((res) => {
      modelCache.list = res.models || [];
      modelCache.at = Date.now();
      return modelCache.list;
    })
    .catch(() => modelCache.list || [])
    .finally(() => (modelCache.pending = null));
  return modelCache.pending;
}

async function modelMenu() {
  if (menuKind === 'model') return closeComposerMenu();
  if (!modelCache.list) {
    // first time: open at once and fill in when the providers have answered
    composerMenu('model', '', [{ name: 'Loading models…', desc: 'Asking your providers', icon: 'clock', keep: true, onPick: () => {} }]);
    await fetchModels();
    if (menuKind !== 'model') return; // closed meanwhile
  }
  const models = modelCache.list || [];
  if (Date.now() - modelCache.at > 120000) fetchModels(); // stale: refresh behind the open menu for next time
  const job = agentJob();
  const chosen = job ? (job.model_pinned ? job.model : '') : state.chatModel || state.settings.model || '';
  const pick = (name) => async () => {
    if (job) {
      // the agent switches (a running one from its next step); the chat's own model stays as it was
      try {
        const res = await api.post(`/api/agent-jobs/${job.id}/model`, { model: name });
        if (watching && watching.id === res.id) watching = res;
        toast(res.switched ? `${job.agent_name} switched to ${shortModel(res.model || name)}` : `${job.agent_name} will use ${shortModel(res.model || name)}`, { timeout: 1800 });
      } catch (err) {
        toast('Could not switch the model', { type: 'error', body: err.message });
      }
      updateModelChip();
      return;
    }
    state.chatModel = name;
    await api.post('/api/settings', { model: name });
    state.settings.model = name;
    updateModelChip();
  };
  const local = models.filter((m) => !m.cloud && !/embed/i.test(m.name));
  // every cloud model, grouped by service (recommended picks first, as the server sorts them)
  const services = new Map();
  models.filter((m) => m.cloud).forEach((m) => {
    if (!services.has(m.provider_name)) services.set(m.provider_name, []);
    services.get(m.provider_name).push(m);
  });
  const row = (m) => ({
    compact: true,
    name: (m.cloud ? m.id : m.name) + (m.recommended && m.recommended.length ? ' ★' : ''),
    meta: m.cloud
      ? [m.caps.includes('vision') ? 'vision' : '', m.caps.includes('tools') ? '' : 'no tools'].filter(Boolean).join(' · ')
      : [m.details && m.details.parameter_size, m.size ? fmtSize(m.size) : ''].filter(Boolean).join(' · '),
    checked: m.name === chosen,
    onPick: pick(m.name),
  });
  composerMenu('model', '', [
    job
      ? { name: 'Agent default', desc: `What ${job.agent_name} is set to use`, icon: 'agent', checked: !chosen, onPick: pick('') }
      : { name: 'Automatic', desc: 'Best available, switches when rate-limited', icon: 'wand', checked: !chosen, onPick: pick('') },
    ...(local.length ? [{ title: 'On this PC' }, ...local.map(row)] : []),
    ...[...services].flatMap(([name, list]) => [{ title: `${name} · ${list.length}` }, ...list.map(row)]),
    !local.length && !services.size ? { name: 'No models found', desc: 'Add a cloud key or start Ollama', icon: 'x', onPick: () => {} } : null,
    'sep',
    { name: 'Manage models…', icon: 'settings', onPick: () => navigate('/connections') },
  ], { search: models.length > 10 ? `Search ${models.length} models…` : '' });
}

// "@groq/openai/gpt-oss-120b" -> "gpt-oss-120b"
function shortModel(name) {
  return name.startsWith('@') ? name.split('/').pop().replace(/:free$/, '') : name.replace(/:latest$/, '');
}

export function updateModelChip() {
  const st = state.status;
  const job = agentJob();
  const name = job ? job.model || (st && st.llm.model) || 'Default model' : state.chatModel || state.settings.model || (st && st.llm.model) || 'No model';
  els.modelLabel.textContent = shortModel(name);
  els.modelBtn.title = job ? `${job.agent_name} runs on ${name}${job.model_pinned ? '' : ' (its default)'} · click to switch` : 'Model for this chat';
  els.modelBtn.classList.toggle('off', !(st && st.llm.online));
}

// slash menu
let slashIndex = 0;
let slashItems = [];
// Project commands (/init, /review, /test...): the server keeps their instructions; /plan and /undo work here.
let codeCommands = [];
const LOCAL_CODE_COMMANDS = [
  { name: 'plan', description: 'Your next message: see a plan to approve before anything is built', icon: 'route' },
  { name: 'tdd', description: 'Tests first: approve the tests, then it builds until they pass', icon: 'check' },
  { name: 'undo', description: 'Undo the last agent run: the files go back to how they were before it', icon: 'history' },
  { name: 'try', description: 'Try with several models at once, each in a copy; keep the best', icon: 'bolt' },
];
function loadCodeCommands() {
  api.get('/api/code-commands').then((r) => (codeCommands = r.commands || [])).catch(() => {});
}
const projectCommands = () =>
  (agentConversation() ? [...LOCAL_CODE_COMMANDS.filter((c) => !['undo', 'try'].includes(c.name) || state.project), ...codeCommands] : [])
    .map((c) => ({ id: c.name, name: c.name, description: c.description, icon: c.icon, code: true }));

async function undoLastRun() {
  const p = state.project;
  if (!p) return;
  const base = '/api/projects/' + encodeURIComponent(p.slug);
  try {
    const res = await api.get(base + '/diff?scope=run');
    if (!res.base || !res.files.length) return toast('Nothing to undo', { body: 'The last run did not change any files.' });
    if (!(await confirmSheet('Undo the last run?', `${res.files.length} file${res.files.length === 1 ? '' : 's'} go back to how they were before the agent started it. The current files are saved as a checkpoint first.`, { confirm: 'Undo run', danger: true }))) return;
    await api.post(base + '/checkpoints/' + res.base.sha + '/restore');
    toast('Run undone', { body: 'The files are back as before it.' });
    refreshProject();
  } catch (err) {
    toast(err.message, 'error');
  }
}

// @ in a project's prompt box: its files, to hand to the agent with the message (the server adds their contents).
function mentionQuery() {
  if (!state.project || !state.project.tree) return null;
  const pos = els.input.selectionStart ?? els.input.value.length;
  const m = els.input.value.slice(0, pos).match(/(?:^|\s)@([\w./\\-]*)$/);
  return m ? { q: m[1].toLowerCase(), start: pos - m[1].length - 1, end: pos } : null;
}

function updateMention() {
  const mq = mentionQuery();
  if (!mq) return false;
  const ranked = state.project.tree
    .filter((f) => !f.dir)
    .map((f) => {
      const p = f.path.toLowerCase();
      const base = p.split('/').pop();
      const score = !mq.q ? 3 : base.startsWith(mq.q) ? 0 : base.includes(mq.q) ? 1 : p.includes(mq.q) ? 2 : -1;
      return [score, f];
    })
    .filter(([score]) => score >= 0)
    .sort((a, b) => a[0] - b[0] || a[1].path.length - b[1].path.length)
    .slice(0, 40);
  if (!ranked.length) return false;
  slashItems = ranked.map(([, f]) => ({ id: f.path, mention: true, start: mq.start, end: mq.end }));
  slashIndex = Math.min(slashIndex, slashItems.length - 1);
  clear(els.slash);
  els.slash.dataset.kind = 'mention';
  const list = els.slash.appendChild(h('div.slash-list'));
  slashItems.forEach((f, i) => {
    const parts = f.id.split('/');
    const name = parts.pop();
    list.appendChild(h('div.slash-item.compact' + (i === slashIndex ? '.sel' : ''), { onmousedown: (e) => { e.preventDefault(); pickSlash(i); } },
      h('div.si-text', h('span.si-name', name), h('span.si-meta', parts.join('/') || './'))));
  });
  els.slash.classList.remove('hidden');
  placeMenu(null);
  return true;
}

/** The menu over the prompt box that fits what is typed: @ files in a project, else / commands and skills. */
function updateMenus() {
  if (!updateMention()) updateSlash();
}

function updateSlash() {
  const m = els.input.value.match(/^\/([\w-]*)$/);
  if (!m) return hideSlash();
  const q = m[1].toLowerCase();
  // project commands in a coding project, then skills, then commands that plugins added (Studio → plugins: scorpi.command)
  slashItems = [...projectCommands(), ...state.skills, ...pluginSkills()].filter((s) => s.id.includes(q) || (s.name || '').toLowerCase().includes(q));
  if (!slashItems.length) return hideSlash();
  slashIndex = Math.min(slashIndex, slashItems.length - 1);
  clear(els.slash);
  els.slash.dataset.kind = 'slash';
  const list = els.slash.appendChild(h('div.slash-list'));
  slashItems.forEach((s, i) =>
    list.appendChild(
      h(
        'div.slash-item' + (i === slashIndex ? '.sel' : ''),
        { onmousedown: (e) => { e.preventDefault(); pickSlash(i); } },
        icon(s.icon || 'spark'),
        h('div', h('div.si-name', '/' + s.id), h('div.si-desc', s.description)),
      ),
    ),
  );
  els.slash.classList.remove('hidden');
  placeMenu(null); // after it shows: a hidden menu is not measured, and it would keep an old (or no) position
}
function hideSlash() {
  els.slash.classList.add('hidden');
  slashItems = [];
}
function pickSlash(i) {
  const s = slashItems[i];
  if (!s) return;
  if (s.mention) {
    const v = els.input.value;
    const insert = '@' + s.id + ' ';
    els.input.value = v.slice(0, s.start) + insert + v.slice(s.end);
    const caret = s.start + insert.length;
    els.input.setSelectionRange(caret, caret);
    hideSlash();
    autosize();
    updateSend();
    els.input.focus();
    return;
  }
  if (s.code) {
    hideSlash();
    els.input.value = '';
    if (s.id === 'plan') {
      composer.plan = true;
      composer.tests = false;
      updateModeChip();
      toast('Plan first', { body: 'Describe what to build: the agent shows a plan to approve before it changes anything.', timeout: 2600 });
    } else if (s.id === 'tdd') {
      composer.tests = true;
      composer.plan = false;
      updateModeChip();
      toast('Tests first', { body: 'Describe what to build: the agent writes tests for you to approve, then builds until they pass.', timeout: 2600 });
    } else if (s.id === 'undo') {
      undoLastRun();
    } else if (s.id === 'try') {
      startAttempts();
    } else {
      els.input.value = '/' + s.id + ' '; // Enter sends it; anything typed after it goes along
    }
    autosize();
    updateSend();
    els.input.focus();
    return;
  }
  setSkill(s.id);
  els.input.value = '';
  hideSlash();
  updateSend();
}

// attachments
function addFiles(files) {
  for (const file of files) {
    const att = { name: file.name || 'pasted.png', mime: file.type, size: file.size, kind: kindOf(file), local: true, progress: 0 };
    if (att.kind === 'image') att.thumb = URL.createObjectURL(file);
    composer.attachments.push(att);
    upload('/api/upload', file, (p) => {
      att.progress = p;
    })
      .then((meta) => {
        Object.assign(att, meta, { local: false });
        renderAttachments();
        updateSend();
      })
      .catch((err) => {
        att.failed = true;
        toast('Upload failed', { type: 'error', body: err.message });
        composer.attachments = composer.attachments.filter((a) => a !== att);
        renderAttachments();
      });
  }
  renderAttachments();
  updateSend();
}

function kindOf(file) {
  const t = file.type || '';
  if (t.startsWith('image/')) return 'image';
  if (t.startsWith('audio/')) return 'audio';
  if (t.startsWith('video/')) return 'video';
  return 'file';
}

function renderAttachments() {
  const row = els.attachRow;
  clear(row);
  row.classList.toggle('hidden', !composer.attachments.length);
  composer.attachments.forEach((a) => {
    const thumb = a.thumb ? h('img.ac-thumb', { src: a.thumb, alt: '' }) : h('span.ac-thumb', icon(a.kind === 'audio' ? 'speaker' : a.kind === 'video' ? 'play' : 'file'));
    row.appendChild(
      h(
        'div.att-chip' + (a.local ? '.uploading' : ''),
        thumb,
        h('div', h('div.ac-name', a.name), h('div.ac-sub', (extOf(a.name) || a.kind).toUpperCase() + ' · ' + fmtSize(a.size))),
        h('button.ac-x', { type: 'button', title: 'Remove', onclick: () => { composer.attachments = composer.attachments.filter((x) => x !== a); renderAttachments(); updateSend(); } }, icon('x')),
      ),
    );
  });
}

// dictation: the device's recognizer first (Settings → Voice), the host as fallback
let dictation = null;
window.addEventListener('scorpi:hidden', () => dictation && dictation.stop());
async function toggleDictation() {
  if (dictation) {
    dictation.stop();
    return;
  }
  const route = sttRoute();
  if (!route) {
    toast('Dictation unavailable', { type: 'warn', body: sttUnavailableReason(), timeout: 8000 });
    return;
  }
  pauseWake('dictation'); // phones share one microphone
  if (route === 'device') return dictateOnDevice();
  return dictateOnHost();
}

function dictateOnDevice() {
  const base = els.input.value;
  const rec = browserRecognizer((text, final) => {
    els.input.value = (base + ' ' + text).trim();
    autosize();
    updateSend();
    if (final) {
      els.mic.classList.remove('rec', 'working');
      dictation = null;
      mood('idle');
      resumeWake('dictation');
    }
  }, {
    lang: state.settings.stt_language,
    onRescue: () => els.mic.classList.replace('rec', 'working'),
    onFail: (err) => {
      els.mic.classList.remove('rec');
      dictation = null;
      mood('idle');
      if (hostSttAvailable()) {
        toast('Using the host for dictation', { body: err.message, timeout: 3000 });
        dictateOnHost();
      } else {
        resumeWake('dictation');
        toast('Dictation unavailable', { type: 'warn', body: sttUnavailableReason(), timeout: 8000 });
      }
    },
  });
  if (!rec) return dictateOnHost();
  els.mic.classList.add('rec');
  mood('listening');
  dictation = { stop: () => rec.stop() };
  rec.start();
}

async function dictateOnHost() {
  try {
    audioContext();
    const session = await record({
      vad: false,
      onLevel: (l) => els.mic.style.setProperty('--level', l.toFixed(2)),
    });
    dictation = session;
    els.mic.classList.add('rec');
    mood('listening');
    const result = await session.done;
    els.mic.classList.remove('rec');
    dictation = null;
    if (!result) return;
    els.mic.classList.add('working');
    const text = await transcribe(result.blob);
    els.input.value = (els.input.value + ' ' + text).trim();
    autosize();
    updateSend();
    els.input.focus();
  } catch (err) {
    toast('Dictation failed', { type: 'error', body: err.message });
  } finally {
    els.mic.classList.remove('rec', 'working');
    dictation = null;
    if (!live) mood('idle');
    resumeWake('dictation');
  }
}

// ---------- init ----------

bus.on('chat:prefill', (text) => {
  if (!els.input) return;
  els.input.value = text;
  autosize();
  updateSend();
  setTimeout(() => els.input.focus(), 60);
});

export function initChat() {
  setTimeout(fetchModels, 2500); // so the model picker opens at once
  Object.assign(els, {
    app: $('#app'),
    view: $('#view-chat'),
    threadList: $('#threadList'),
    search: $('#threadSearch'),
    messages: $('#messages'),
    scroller: $('#scroller'),
    input: $('#input'),
    send: $('#sendBtn'),
    form: $('#composer'),
    attachRow: $('#attachRow'),
    slash: $('#slashMenu'),
    jump: $('#jumpBtn'),
    skillBtn: $('#skillBtn'),
    skillLabel: $('#skillLabel'),
    toolsBtn: $('#toolsBtn'),
    toolsLabel: $('#toolsLabel'),
    modeBtn: $('#modeBtn'),
    modeLabel: $('#modeLabel'),
    modelBtn: $('#modelBtn'),
    modelLabel: $('#modelLabel'),
    mic: $('#micBtn'),
    title: $('#convoTitle'),
    skillTag: $('#convoSkill'),
    projectTag: $('#convoProject'),
    pin: $('#pinBtn'),
    ioToggle: $('#ioToggle'),
    welcomeTitle: $('#welcomeTitle'),
    welcomeSub: $('#welcomeSub'),
    skillIndex: $('#skillIndex'),
    glance: $('#glance'),
  });
  initCanvas({ view: els.view, pane: $('#canvasPane'), fab: $('#canvasFab'), btn: $('#canvasBtn') });
  // pin, rename, export and delete live in one menu next to the title
  $('#convoMore').addEventListener('click', () => currentConv() && threadMenu($('#convoMore'), currentConv()));

  buddy = createScorpi({ perch: $('#perch'), composer: els.form, input: els.input, onTalk: () => setInputMode('voice') });

  els.search.addEventListener('input', debounce(renderThreads, 120));
  els.form.addEventListener('submit', (e) => {
    e.preventDefault();
    send();
  });
  els.input.addEventListener('input', () => {
    autosize();
    updateSend();
    updateMenus();
    // Ember peeks at what you are typing
    for (const m of [buddy]) {
      if (m && m.base === 'idle') m.lookAt(els.input, 1600).flash('curious', 1600);
    }
  });
  els.input.addEventListener('keydown', (e) => {
    if (!els.slash.classList.contains('hidden')) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        slashIndex = (slashIndex + (e.key === 'ArrowDown' ? 1 : -1) + slashItems.length) % slashItems.length;
        updateMenus();
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        pickSlash(slashIndex);
        return;
      }
      if (e.key === 'Escape') return hideSlash();
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && (prefs.sendOnEnter && !isMobile())) {
      e.preventDefault();
      send();
    }
    if (e.key === 'Tab' && e.shiftKey && agentConversation()) {
      e.preventDefault();
      cycleMode();
    }
    if (e.key === 'Backspace' && !els.input.value && composer.skill) setSkill('');
  });
  els.input.addEventListener('paste', (e) => {
    const files = [...(e.clipboardData && e.clipboardData.files ? e.clipboardData.files : [])];
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  });

  $('#attachBtn').addEventListener('click', () => $('#fileInput').click());
  $('#fileInput').addEventListener('change', (e) => {
    addFiles([...e.target.files]);
    e.target.value = '';
  });
  els.skillBtn.addEventListener('click', skillMenu);
  els.skillBtn.dataset.popAnchor = '';
  els.toolsBtn.addEventListener('click', toolsMenu);
  els.toolsBtn.dataset.popAnchor = '';
  els.modeBtn.addEventListener('click', modeMenu);
  loadCodeCommands();
  els.modeBtn.dataset.popAnchor = '';
  els.modelBtn.addEventListener('click', modelMenu);
  els.modelBtn.dataset.popAnchor = '';
  els.mic.addEventListener('click', toggleDictation);
  els.send.addEventListener('click', (e) => {
    if (live && !els.input.value.trim()) {
      e.preventDefault();
      stop();
    }
  });
  $('#voiceSlot').appendChild(voiceBar());
  $('#voiceStatus').appendChild(voiceStatus());
  els.ioToggle.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-io]');
    if (b) setInputMode(b.dataset.io);
  });
  $('#paneBtn').addEventListener('click', toggleProjectPane);

  // drag & drop
  let dragDepth = 0;
  els.view.addEventListener('dragenter', (e) => {
    if (![...e.dataTransfer.types].includes('Files')) return;
    dragDepth++;
    els.view.classList.add('dragging');
  });
  els.view.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) els.view.classList.remove('dragging');
  });
  els.view.addEventListener('dragover', (e) => e.preventDefault());
  els.view.addEventListener('drop', (e) => {
    e.preventDefault();
    dragDepth = 0;
    els.view.classList.remove('dragging');
    if (e.dataTransfer.files.length) addFiles([...e.dataTransfer.files]);
  });

  initScrolling();

  // header
  els.title.addEventListener('click', () => {
    if (!state.activeId || els.title.isContentEditable) return;
    els.title.contentEditable = 'true';
    els.title.focus();
    document.getSelection().selectAllChildren(els.title);
  });
  const commitTitle = () => {
    if (!els.title.isContentEditable) return;
    els.title.contentEditable = 'false';
    const t = els.title.textContent.trim();
    const conv = currentConv();
    if (t && conv && t !== conv.title) patchConversation(conv.id, { title: t });
    else updateHead();
  };
  els.title.addEventListener('blur', commitTitle);
  els.title.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      els.title.blur();
    }
    if (e.key === 'Escape') {
      els.title.contentEditable = 'false';
      updateHead();
    }
  });
  els.pin.addEventListener('click', () => {
    const c = currentConv();
    if (c) patchConversation(c.id, { pinned: !c.pinned });
  });
  $('#exportBtn').addEventListener('click', () => state.activeId && (location.href = `/api/conversations/${state.activeId}/export`));
  $('#deleteBtn').addEventListener('click', () => state.activeId && deleteConversation(state.activeId));
  const newChat = () => {
    closePop();
    els.app.classList.remove('threads-open');
    if (state.mode === 'code') return navigate('/code');
    navigate('/');
    setTimeout(() => els.input.focus(), 50);
  };
  $('#newChatBtn').addEventListener('click', newChat);
  $('#topNewBtn').addEventListener('click', newChat);

  // the agent of the open chat switched model or started: keep the prompt box in step
  bus.on('server:agent_job', (d) => {
    if (!d || !d.conv_id || d.conv_id !== state.activeId) return;
    if (watching && watching.id !== d.id && watching.started > d.started) return;
    const before = watching;
    watching = d;
    updateModelChip();
    updateSkillChip();
    // the job started a new run by itself (building the approved plan, a message that came in late): follow it live
    if (d.status === 'running' && d.run_id && !live && state.view === 'chat' && before && before.run_id && before.run_id !== d.run_id) {
      els.messages.querySelectorAll('.msg.last').forEach((n) => n.classList.remove('last'));
      if (d.run_note) els.messages.appendChild(systemNode(d.run_note));
      attachRun(d.run_id, d.conv_id);
    }
  });
  bus.on('compose:send', (msg) => (typeof msg === 'string' ? send(msg) : send(msg.text, { display: msg.display, command: msg.command })));
  // the project pane started work in another conversation of the project (e.g. the coder fixing an issue): follow it
  bus.on('project:conversation', (id) => {
    if (state.project && id && id !== state.activeId) openConversation(id, { keepProject: true });
  });
  bus.on('compose:prefill', ({ text, skill }) => {
    if (skill) setSkill(skill);
    els.input.value = text || '';
    autosize();
    updateSend();
    els.input.focus();
  });

  document.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (isMobile()) {
        els.app.classList.add('threads-open');
        els.search.focus();
      } else $('#tkSearch').focus(); // desktop: the taskbar's search

    }
    if (mod && e.shiftKey && e.key.toLowerCase() === 'o') {
      e.preventDefault();
      newChat();
    }
    if (mod && e.shiftKey && e.key.toLowerCase() === 'm') {
      e.preventDefault();
      if (composer.io !== 'voice') toggleDictation();
    }
    if (mod && e.shiftKey && e.code === 'Space' && state.view === 'chat') {
      e.preventDefault();
      setInputMode(composer.io === 'voice' ? 'write' : 'voice');
    }
    if (e.key === 'Escape' && live && (document.activeElement === els.input || composer.io === 'voice')) stop();
  });

  updateSkillChip();
  updateToolsChip();
  updateSend();
}

export function onConversationEvent(data) {
  if (data && data.deleted) {
    state.conversations = state.conversations.filter((c) => c.id !== data.id);
    renderThreads();
    if (data.id === state.activeId && !live) navigate('/');
    return;
  }
  loadThreads().then(() => {
    if (data && data.id === state.activeId) updateHead();
  });
}

export function refreshWelcome() {
  if (!state.activeId) renderWelcome();
  updateModelChip();
}
