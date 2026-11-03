// The project pane's Terminal: commands run in the project folder on the PC (PowerShell on Windows) with their output
// streaming in; long-running ones (a dev server) keep going until stopped. A failed command goes to the agent with one
// click, and a local address in the output opens in the pane's browser.

import { api } from '../lib/api.js';
import { h, icon } from '../lib/dom.js';
import { toast } from '../lib/ui.js';
import { bus } from '../store.js';

const per = new Map(); // slug -> {terms: Map, nodes: Map, loaded, cwd, history, hi, urls: Set, quick}
const LOCAL_URL = /https?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):\d{2,5}[^\s'")\]]*/g;
let mounted = null; // {el, ctx, slug, out}

const S = (slug) => {
  if (!per.has(slug)) per.set(slug, { terms: new Map(), nodes: new Map(), loaded: false, cwd: '', history: [], hi: -1, urls: new Set(), quick: null });
  return per.get(slug);
};
const base = (slug) => '/api/projects/' + encodeURIComponent(slug);

/** How many commands run in a project right now (the tab shows it). */
export const termRunning = (slug) => [...S(slug).terms.values()].filter((t) => t.status === 'running').length;

bus.on('server:term', (t) => {
  const s = S(t.slug);
  const known = s.terms.get(t.id);
  const term = known || { ...t, output: '' };
  Object.assign(term, { status: t.status, code: t.code, ended: t.ended, command: t.command });
  if (t.chunk) term.output = (term.output + t.chunk).slice(-300000);
  s.terms.set(t.id, term);
  scanUrls(s, t.chunk);
  if (mounted && mounted.slug === t.slug && mounted.el.isConnected) {
    if (!known || !s.nodes.has(t.id)) renderOut();
    else patchBlock(term, t.chunk);
    renderUrls();
  }
  bus.emit('project:term-count', { slug: t.slug, running: termRunning(t.slug) });
});

function scanUrls(s, text) {
  for (const m of (text || '').match(LOCAL_URL) || []) s.urls.add(m.replace('0.0.0.0', 'localhost').replace(/[.,;:]+$/, ''));
}

export async function renderTerminal(el, ctx) {
  const slug = ctx.data.slug;
  const s = S(slug);
  if (!s.loaded) {
    el.replaceChildren(h('div.pp-loading', h('span.job-pulse'), 'Loading…'));
    try {
      const res = await api.get(base(slug) + '/terminal');
      s.cwd = res.cwd;
      for (const t of res.terms) {
        s.terms.set(t.id, t);
        if (t.status === 'running') scanUrls(s, t.output);
      }
      s.loaded = true;
    } catch (err) {
      el.replaceChildren(h('div.pp-empty', icon('terminal'), h('b', 'No terminal here'), h('span', err.message)));
      return;
    }
  }
  const out = h('div.tm-out');
  const input = h('input.tm-input', { placeholder: 'Run a command in the project folder…', spellcheck: false, autocomplete: 'off', 'aria-label': 'Command' });
  const form = h('form.tm-in', { onsubmit: (e) => { e.preventDefault(); run(ctx, input.value); input.value = ''; s.hi = -1; } },
    h('span.tm-prompt', 'PS ›'), input, h('button.icon-btn', { type: 'submit', title: 'Run (Enter)' }, icon('arrow-right')));
  input.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
    e.preventDefault();
    if (!s.history.length) return;
    s.hi = e.key === 'ArrowUp' ? Math.min(s.history.length - 1, s.hi + 1) : Math.max(-1, s.hi - 1);
    input.value = s.hi < 0 ? '' : s.history[s.history.length - 1 - s.hi];
  });
  const tools = h('div.pp-subbar.tm-bar',
    h('span.tm-cwd.mono', { title: s.cwd }, s.cwd),
    h('span.grow'),
    h('button.icon-btn', { title: 'Clear finished commands', onclick: () => clearFinished(slug) }, icon('trash')),
    h('button.icon-btn', { title: 'Open a terminal window in this folder on the PC', onclick: () => api.post(base(slug) + '/open', { app: 'terminal' }).catch((e) => toast(e.message, 'error')) }, icon('external')));
  const chips = h('div.tm-chips');
  const urls = h('div.tm-urls');
  el.replaceChildren(tools, out, urls, chips, form);
  mounted = { el, ctx, slug, out, chips, urls, input };
  renderOut();
  renderUrls();
  quickCommands(ctx).then((list) => {
    if (mounted && mounted.slug === slug) chips.replaceChildren(...list.map((c) => h('button.chip.small', { type: 'button', title: 'Run ' + c, onclick: () => run(ctx, c) }, c)));
  });
  setTimeout(() => input.focus({ preventScroll: true }), 30);
}

async function run(ctx, command) {
  command = String(command || '').trim();
  if (!command) return;
  const s = S(ctx.data.slug);
  if (s.history[s.history.length - 1] !== command) s.history.push(command);
  try {
    const t = await api.post(base(ctx.data.slug) + '/terminal', { command });
    if (!s.terms.has(t.id)) s.terms.set(t.id, t);
    if (mounted && mounted.slug === ctx.data.slug) renderOut();
  } catch (err) {
    toast(err.message, 'error');
  }
}

function renderOut() {
  const { out, slug } = mounted;
  const s = S(slug);
  s.nodes.clear();
  const list = [...s.terms.values()].sort((a, b) => a.started - b.started);
  if (!list.length) {
    out.replaceChildren(h('div.tm-hello', h('b', 'Terminal'), h('span', 'Commands run in this project\'s folder on the PC. Start a dev server, run the tests, install packages. Output streams in; a local address opens in Preview.')));
    return;
  }
  out.replaceChildren(...list.map(blockNode));
  out.scrollTop = out.scrollHeight;
}

function blockNode(t) {
  const s = S(mounted.slug);
  const pre = h('pre.tm-pre', t.output || '');
  const head = h('div.tm-cmd');
  const block = h('div.tm-block', head, pre);
  s.nodes.set(t.id, { block, pre, head });
  fillHead(t, block, head);
  return block;
}

function fillHead(t, block, head) {
  const running = t.status === 'running';
  block.className = 'tm-block ' + t.status;
  const send = running
    ? h('input.tm-stdin', { placeholder: 'input…', title: 'Send a line to this command (Enter)', onkeydown: (e) => {
        if (e.key !== 'Enter') return;
        api.post(`/api/terminal/${t.id}/input`, { text: e.target.value }).catch((err) => toast(err.message, 'error'));
        e.target.value = '';
      } })
    : null;
  head.replaceChildren(...[
    h('span.mk.' + (running ? 'active' : t.status === 'done' ? 'done' : t.status === 'failed' ? 'failed' : 'pending')),
    h('span.tm-c', t.command),
    h('span.grow'),
    send,
    h('span.tm-meta', running ? 'running' : t.status === 'stopped' ? 'stopped' : `exit ${t.code}` + (t.ended && t.started ? ` · ${Math.max(0, t.ended - t.started).toFixed(1)}s` : '')),
    t.status === 'failed' ? h('button.btn.small.quiet', { title: 'Send the output to the agent and ask it to fix the problem', onclick: () => askFix(t) }, icon('wand'), 'Fix') : null,
    running ? h('button.icon-btn', { title: 'Stop', onclick: () => api.post(`/api/terminal/${t.id}/stop`).catch((e) => toast(e.message, 'error')) }, icon('stop')) : null,
    !running ? h('button.icon-btn', { title: 'Run again', onclick: () => run(mounted.ctx, t.command) }, icon('retry')) : null,
  ].filter(Boolean));
}

function patchBlock(t, chunk) {
  const s = S(mounted.slug);
  const node = s.nodes.get(t.id);
  if (!node) return;
  const out = mounted.out;
  const atEnd = out.scrollHeight - out.scrollTop - out.clientHeight < 40;
  if (chunk) {
    node.pre.appendChild(document.createTextNode(chunk));
    if (node.pre.textContent.length > 320000) node.pre.textContent = t.output;
  }
  if (!node.block.classList.contains(t.status)) {
    const typing = node.head.querySelector('.tm-stdin') === document.activeElement;
    if (!typing || t.status !== 'running') fillHead(t, node.block, node.head);
  }
  if (atEnd) out.scrollTop = out.scrollHeight;
}

function renderUrls() {
  const s = S(mounted.slug);
  mounted.urls.replaceChildren(...[...s.urls].slice(-3).map((u) => h('button.chip.small.tm-url', { type: 'button', title: 'Open in the pane\'s browser', onclick: () => mounted.ctx.openUrl(u) }, icon('globe'), u.replace(/^https?:\/\//, ''), h('span.muted', ' → Preview'))));
}

function askFix(t) {
  const tail = (t.output || '').trim().split('\n').slice(-60).join('\n').slice(-5000);
  mounted.ctx.ask({
    text: `I ran \`${t.command}\` in the project folder and it failed (exit code ${t.code}). The output:\n\n\`\`\`\n${tail}\n\`\`\`\n\nFind the cause, fix it, and run the command again to check.`,
    display: `Fix: \`${t.command}\` failed`,
  });
}

async function clearFinished(slug) {
  const s = S(slug);
  const done = [...s.terms.values()].filter((t) => t.status !== 'running');
  await Promise.all(done.map((t) => api.del('/api/terminal/' + t.id).catch(() => null)));
  done.forEach((t) => s.terms.delete(t.id));
  s.urls.clear();
  if (mounted && mounted.slug === slug) {
    renderOut();
    renderUrls();
  }
}

/** One-click commands that fit the project: its npm scripts, Python entry points, tests, git. */
async function quickCommands(ctx) {
  const s = S(ctx.data.slug);
  if (s.quick) return s.quick;
  const files = new Set(ctx.data.tree.filter((f) => !f.dir).map((f) => f.path));
  const out = [];
  if (files.has('package.json')) {
    try {
      const sep = ctx.data.path.includes('\\') ? '\\' : '/';
      const res = await api.get('/api/file/text?path=' + encodeURIComponent(ctx.data.path.replace(/[\\/]+$/, '') + sep + 'package.json'));
      const scripts = Object.keys(JSON.parse(res.text).scripts || {});
      out.push('npm install');
      for (const k of ['dev', 'start', 'test', 'build', 'lint']) if (scripts.includes(k)) out.push(k === 'start' || k === 'test' ? `npm ${k}` : `npm run ${k}`);
    } catch {
      out.push('npm install');
    }
  }
  if (files.has('requirements.txt')) out.push('pip install -r requirements.txt');
  for (const f of ['main.py', 'app.py', 'manage.py']) if (files.has(f)) out.push(f === 'manage.py' ? 'python manage.py runserver' : `python ${f}`);
  if (files.has('pyproject.toml') || [...files].some((f) => /(^|\/)test_.*\.py$/.test(f))) out.push('python -m pytest -q');
  if (files.has('Cargo.toml')) out.push('cargo run', 'cargo test');
  if (files.has('go.mod')) out.push('go run .', 'go test ./...');
  if (files.has('index.html') && !files.has('package.json')) out.push('python -m http.server 8080');
  if (ctx.data.git) out.push('git status', 'git log --oneline -10');
  s.quick = out.slice(0, 8);
  return s.quick;
}

export function unmountTerminal() {
  mounted = null;
}

