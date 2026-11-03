// Code: coding projects. Start one by describing it; each project has its own folder and an agent that builds,
// runs and tests it. Opening a project shows the conversation with its agent next to the files and a live preview.

import { api } from '../lib/api.js';
import { clear, debounce, fmtRelative, h, icon } from '../lib/dom.js';
import { emptyState, field, sheet, toast } from '../lib/ui.js';
import { state } from '../store.js';
import { navigate } from '../router.js';

const STATUS = { running: ['working', '.warn'], done: ['done', '.ok'], failed: ['failed', '.err'], stopped: ['stopped', ''], interrupted: ['paused', ''] };
const IDEAS = [
  ['A landing page for my bakery', 'with opening hours, a menu and a contact form'],
  ['A snake game in the browser', 'with a high score and touch controls'],
  ['A Python script', 'that renames my photos by the date they were taken'],
  ['A Blender scene', 'of a cosy low-poly cabin in the snow (needs the Blender MCP server)'],
];

let root;

export async function renderCode(el) {
  root = el;
  let projects = [];
  let mcp = { servers: [] };
  try {
    [projects, mcp] = await Promise.all([api.get('/api/projects').then((d) => d.projects), api.get('/api/mcp').catch(() => ({ servers: [] }))]);
  } catch (err) {
    clear(root);
    root.appendChild(h('div.page', h('div.page-inner', emptyState('Projects unavailable', err.message))));
    return;
  }
  state.projects = projects;
  const scroll = root.querySelector('.page') ? root.querySelector('.page').scrollTop : 0;
  clear(root);

  const name = h('input.input.cs-name', { placeholder: 'Project name (optional)', maxLength: 60 });
  const task = h('textarea.cs-task', { rows: 3, placeholder: 'Describe what to build…' });
  const start = h('button.send.cs-go', { type: 'submit', title: 'Start building', disabled: true }, icon('arrow-up', 'i-send'));
  task.addEventListener('input', () => {
    start.disabled = !task.value.trim();
    task.style.height = 'auto';
    task.style.height = Math.min(task.scrollHeight, innerHeight * 0.35) + 'px';
  });
  task.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  const form = h(
    'form.composer.code-start',
    { onsubmit: (e) => { e.preventDefault(); create(name.value.trim(), task.value.trim(), start); } },
    task,
    h('div.composer-bar', icon('folder', 'cs-ic'), name, h('span.grow'), start),
  );

  const tools = mcp.servers.filter((s) => s.config.enabled !== false && s.scope !== 'chat');
  const page = h(
    'div.page.code-page',
    h(
      'div.page-inner.narrow',
      h('div.code-hero', h('h1.code-title', 'What are we ', h('em', 'building?')), h('p.page-sub', 'Each project gets its own folder and an agent that writes, runs and tests the code. Follow along, talk to it while it works, or switch to voice.')),
      form,
      h('div.code-import',
        h('button.btn.small.quiet', { type: 'button', onclick: cloneSheet }, icon('branch'), 'Clone from GitHub'),
        h('button.btn.small.quiet', { type: 'button', onclick: folderSheet }, icon('folder'), 'Open a folder on this PC')),
      h('div.idea-chips', IDEAS.map(([t, more]) => h('button.chip', { type: 'button', onclick: () => { task.value = `${t} ${more}`; task.dispatchEvent(new Event('input')); task.focus(); } }, t))),
      h(
        'div.code-tools',
        icon('plug'),
        tools.length
          ? h('span', 'Coding tools: ', tools.map((s, i) => [i ? ', ' : '', h('b', s.config.name)]), ' · ')
          : h('span', 'Add MCP servers like Blender, GitHub or Context7 for coding · '),
        h('a', { href: '/connections#mcp', 'data-link': '' }, tools.length ? 'Manage' : 'Browse servers'),
      ),
      h(
        'div.section',
        h('h3.section-title', 'Projects', h('span.count', projects.length || ''), h('span.rule')),
        projects.length ? h('div.project-grid', projects.map(projectCard)) : h('div.panel', emptyState('No projects yet', 'Describe one above and the coder starts right away.')),
      ),
    ),
  );
  root.appendChild(page);
  page.scrollTop = scroll;
  if (!projects.length) setTimeout(() => task.focus(), 50);
}

function projectCard(p, i) {
  const job = p.job || {};
  const [label, cls] = STATUS[job.status] || ['new', ''];
  return h(
    'a.project-card' + (job.status === 'running' ? '.running' : ''),
    { href: '/code/' + p.slug, 'data-link': '', style: { animationDelay: Math.min(i, 12) * 30 + 'ms' } },
    h('div.pc-top', h('span.pc-icon', icon(p.has_index ? 'globe' : 'code')), h('span.badge' + cls, label)),
    h('div.pc-name', p.name),
    h('div.pc-now', job.status === 'running' ? h('span.job-pulse') : null, h('span', job.activity || (job.result || '').split('\n').find((l) => l.trim()) || 'No agent yet')),
    h('div.pc-foot', h('span', p.files + (p.files === 1 ? ' file' : ' files') + (p.git ? ' · git' : '') + (p.linked ? ' · linked' : '')), h('span', fmtRelative(p.updated))),
  );
}

async function create(name, task, btn) {
  if (!task) return;
  btn.disabled = true;
  try {
    const res = await api.post('/api/projects', { name, task });
    navigate('/code/' + res.slug);
  } catch (err) {
    btn.disabled = false;
    toast('Could not start the project', { type: 'error', body: err.message });
  }
}

/** Clone a GitHub repository as a project: pick one of yours (when GitHub is connected) or type owner/name. */
async function cloneSheet() {
  let gh = { connected: false };
  try {
    gh = await api.get('/api/github');
  } catch {
    /* not the owner, or offline: typing owner/name still works for public repositories */
  }
  let picked = '';
  const q = h('input.input', { placeholder: gh.connected ? 'Search your repositories, or type owner/name' : 'owner/name or https://github.com/owner/name', spellcheck: false });
  const list = h('div.repo-list');
  const task = h('textarea.input', { rows: 2, placeholder: 'Optional: what the coder should do in it (leave empty to just open it)' });
  const pick = (full) => {
    picked = full;
    list.querySelectorAll('.repo-row').forEach((r) => r.classList.toggle('on', r.dataset.full === full));
  };
  const load = debounce(async () => {
    list.replaceChildren(h('div.repo-empty', 'Loading…'));
    try {
      const res = await api.get('/api/github/repos?q=' + encodeURIComponent(q.value.trim()));
      list.replaceChildren(...(res.repos.length
        ? res.repos.map((r) => h('button.repo-row', { type: 'button', dataset: { full: r.full_name }, onclick: () => pick(r.full_name), ondblclick: () => { pick(r.full_name); go(); } },
            h('b', r.full_name, r.private ? h('span.faint', ' · private') : null), h('span', [r.language, r.description].filter(Boolean).join(' · ') || 'No description')))
        : [h('div.repo-empty', 'No repository matches.')]));
    } catch (err) {
      list.replaceChildren(h('div.repo-empty', err.message));
    }
  }, 300);
  q.addEventListener('input', () => {
    picked = '';
    if (gh.connected) load();
  });
  q.addEventListener('keydown', (e) => e.key === 'Enter' && go());
  let button;
  let close;
  const go = async () => {
    const repo = picked || q.value.trim();
    if (!repo) return q.focus();
    button.disabled = true;
    button.textContent = 'Cloning…';
    try {
      const res = await api.post('/api/github/clone', { repo, task: task.value.trim() });
      close();
      navigate('/code/' + res.slug);
    } catch (err) {
      toast('Could not clone it', { type: 'error', body: err.message, timeout: 8000 });
      button.disabled = false;
      button.textContent = 'Clone';
    }
  };
  close = sheet({
    title: 'Clone from GitHub',
    wide: true,
    body: h('div.form-stack',
      h('div', q, gh.connected ? list : h('div.muted.small', { style: { marginTop: '8px' } }, 'Public repositories clone without an account. ', h('a', { href: '/connections#github', 'data-link': '' }, 'Connect GitHub'), ' to pick from your own (private ones too) and to push.')),
      field('Task', task, 'The coder starts on it right after cloning. You can also tell it later in the project\'s chat.')),
    foot: (closeIt) => [h('button.btn.quiet', { onclick: closeIt }, 'Cancel'), (button = h('button.btn.primary', { onclick: go }, 'Clone'))],
  });
  if (gh.connected) load();
}

/** Work on a folder that is already on the PC: it becomes a project in place. */
function folderSheet() {
  const path = h('input.input', { placeholder: 'C:\\Users\\you\\code\\my-app', spellcheck: false });
  const name = h('input.input', { placeholder: 'Name (optional, the folder\'s name otherwise)' });
  const go = async (e, close) => {
    if (!path.value.trim()) return path.focus();
    e.currentTarget.disabled = true;
    try {
      const res = await api.post('/api/projects/link', { path: path.value.trim(), name: name.value.trim() });
      close();
      navigate('/code/' + res.slug);
    } catch (err) {
      e.currentTarget.disabled = false;
      toast('Could not open that folder', { type: 'error', body: err.message });
    }
  };
  sheet({
    title: 'Open a folder',
    body: h('div.form-stack',
      h('p.muted', { style: { margin: 0 } }, 'Work on a project that already lives on this PC. Its files stay where they are: the agent, preview, terminal, git and checkpoints all work in that folder.'),
      field('Folder', path), field('Name', name)),
    foot: (close) => [h('button.btn.quiet', { onclick: close }, 'Cancel'), h('button.btn.primary', { onclick: (e) => go(e, close) }, 'Open')],
  });
}

export function refreshCode() {
  if (root && root.classList.contains('active')) renderCode(root);
}
