// The project pane: next to a project's conversation with its agent, the project's live preview, files and progress.

import { api, fileUrl } from '../lib/api.js';
import { $, clear, debounce, extOf, fmtRelative, fmtSize, h, icon, isMobile } from '../lib/dom.js';
import { highlight } from '../lib/md.js';
import { toast } from '../lib/ui.js';
import { LANGS, kindOf, openViewer, previewBody } from '../lib/viewer.js';
import { bus, prefs, setPref, state } from '../store.js';

const STATUS = { running: ['working', '.warn'], done: ['done', '.ok'], failed: ['failed', '.err'], stopped: ['stopped', ''], interrupted: ['paused', ''] };
const IMAGES = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp'];
// shown as they look rather than as source (web pages keep their source view: that's what a coder reads)
const VISUAL = ['image', 'video', 'audio', 'pdf', 'markdown', 'table', 'doc'];

let pane = null;
let data = null; // GET /api/projects/:slug
let tab = 'preview';
let selected = '';
let collapsed = new Set();
let stamp = ''; // what the files looked like last time, to reload the preview only when they changed

const visible = () => !!(pane && data && pane.classList.contains('open'));

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
    collapsed = new Set();
    tab = project.has_index ? 'preview' : 'files';
    stamp = treeStamp(project.tree);
  }
  if (!same) setOpen(!isMobile() && prefs.projectPane !== false); // on a phone the pane covers the chat: open it on request
  render(!same);
}

export function hideProjectPane() {
  data = null;
  if (pane) {
    pane.classList.remove('open');
    clear(pane);
  }
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
}

const treeStamp = (tree) => (tree || []).map((f) => f.path + ':' + (f.mtime || 0)).join('|');

export const refreshProject = debounce(async () => {
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
  if (fresh.has_index && tab === 'files' && !selected && changed) tab = 'preview';
  render(changed);
}, 1200);

// the agent is working: follow its progress, and reload files / preview as they change
let lastTools = -1;
bus.on('server:agent_job', (job) => {
  if (!data || job.project !== data.slug) return;
  data.job = { ...(data.job || {}), ...job };
  renderStrip();
  if (job.tools !== lastTools || job.status !== 'running') {
    lastTools = job.tools;
    refreshProject();
  }
});

let shownTab = '';

function render(reloadPreview = false) {
  if (!pane || !data) return;
  const oldBody = pane.querySelector('.pp-body');
  const keepScroll = oldBody ? oldBody.scrollTop : 0;
  // an iframe reloads whenever it is moved, so a preview that needs no reload stays where it is
  const keepPreview = oldBody && tab === 'preview' && shownTab === 'preview' && !reloadPreview && oldBody.querySelector('.pp-frame, .pp-empty');
  const tabs = h(
    'div.pp-tabs',
    [['preview', 'Preview', 'eye'], ['files', 'Files', 'folder']].map(([k, label, ic]) =>
      h('button' + (tab === k ? '.on' : ''), { type: 'button', onclick: () => { tab = k; render(); } }, icon(ic), label, k === 'files' ? h('span.count', String(data.tree.filter((f) => !f.dir).length)) : null),
    ),
    h('span.grow'),
    tab === 'preview' && data.has_index ? h('button.icon-btn', { title: 'Reload preview', onclick: () => render(true) }, icon('refresh')) : null,
    tab === 'preview' && data.has_index ? h('a.icon-btn', { title: 'Open the preview in a new tab', href: data.preview, target: '_blank', rel: 'noopener' }, icon('external')) : null,
    h('button.icon-btn', { title: 'Open the project folder on the PC', onclick: openFolder }, icon('folder')),
    h('button.icon-btn', { title: 'Hide panel', onclick: toggleProjectPane }, icon('x')),
  );
  const head = h('div.pp-head', h('div.pp-strip', { id: 'ppStrip' }));
  shownTab = tab;
  if (keepPreview && !(data.has_index && oldBody.querySelector('.pp-empty'))) {
    pane.querySelectorAll(':scope > :not(.pp-body)').forEach((n) => n.remove());
    pane.prepend(head, tabs);
  } else {
    const body = h('div.pp-body', tab === 'preview' ? previewNode() : filesNode());
    clear(pane);
    pane.append(head, tabs, body);
    body.scrollTop = tab === 'files' && selected ? 0 : keepScroll;
  }
  renderStrip();
}

function renderStrip() {
  const el = pane && pane.querySelector('#ppStrip');
  if (!el || !data) return;
  const job = data.job;
  const [label, cls] = STATUS[job ? job.status : ''] || ['new', ''];
  const pct = job && job.steps_total ? Math.round((job.steps_done / job.steps_total) * 100) : 0;
  el.replaceChildren(
    ...[
      h('div.pp-name', h('span.pp-title', data.name), h('span.badge' + cls, label)),
      h('div.pp-now', job && job.status === 'running' ? h('span.job-pulse') : null, h('span', job ? job.activity || '' : 'Tell the agent what to build.')),
      job && job.steps_total ? h('div.job-progress', h('div.jp-bar', h('i', { style: { width: pct + '%' } })), h('span.jp-text', `${job.steps_done}/${job.steps_total}`)) : null,
      h('div.pp-path.mono', { title: data.path }, data.path),
    ].filter(Boolean),
  );
}

function previewNode() {
  if (!data.has_index) {
    return h('div.pp-empty', icon('eye'), h('b', 'No preview yet'), h('span', 'It appears here when the project has an index.html. For other projects, ask the agent how to run it, or look at the files.'));
  }
  return h('iframe.pp-frame', {
    src: data.preview + '?t=' + Date.now(),
    title: 'Preview of ' + data.name,
    // the project's own code runs isolated from Hearth
    sandbox: 'allow-scripts allow-forms allow-modals allow-popups allow-downloads',
  });
}

function filesNode() {
  const files = data.tree;
  if (!files.length) return h('div.pp-empty', icon('folder'), h('b', 'Empty so far'), h('span', 'Files the agent writes show up here live.'));
  const open = selected && files.find((f) => f.path === selected);
  if (open) return h('div.pp-files', viewerNode(open));
  const list = h('div.pp-tree');
  for (const f of files) {
    const parts = f.path.split('/');
    const parent = parts.slice(0, -1);
    // hidden when any parent folder is collapsed
    if (parent.some((_, i) => collapsed.has(parent.slice(0, i + 1).join('/')))) continue;
    const name = parts[parts.length - 1];
    const row = h(
      'button.pp-file' + (f.dir ? '.dir' : '') + (f.path === selected ? '.sel' : ''),
      { type: 'button', style: { paddingLeft: 10 + parent.length * 14 + 'px' }, title: f.path },
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
      render();
    });
    list.appendChild(row);
  }
  return h('div.pp-files', list);
}

function viewerNode(f) {
  if (!f) return null;
  const full = data.path.replace(/[\\/]+$/, '') + (data.path.includes('\\') ? '\\' : '/') + f.path.replace(/\//g, data.path.includes('\\') ? '\\' : '/');
  const ext = extOf(f.path);
  const head = h('div.pv-head',
    h('button.icon-btn', { title: 'Back to the files', onclick: () => { selected = ''; render(); } }, icon('left')),
    h('span.mono', f.path), h('span.muted', fmtSize(f.size) + ' · ' + fmtRelative(f.mtime)), h('span.grow'),
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

function openFolder() {
  api.post('/api/file/open', { path: data.path }).then(() => toast('Opened the project folder on the PC')).catch((e) => toast(e.message, 'error'));
}
