// The project pane's Changes and GitHub tabs: what the agent changed (its last run, uncommitted work, every
// checkpoint, each one restorable), committing with a written-for-you message, branches, push and pull, pull requests,
// CI checks, and issues the coder can fix.

import { api } from '../lib/api.js';
import { fmtRelative, h, icon } from '../lib/dom.js';
import { diffList, diffStat } from '../lib/diff.js';
import { renderMarkdown } from '../lib/md.js';
import { confirmSheet, popover, promptSheet, sheet, switchEl, toast } from '../lib/ui.js';
import { navigate } from '../router.js';
import { bus } from '../store.js';
import { askWhy } from './lessons.js';

const per = new Map(); // slug -> {scope, open: Set, ckpt: sha shown in the checkpoint list, message}
const st = (slug) => {
  if (!per.has(slug)) per.set(slug, { scope: 'run', open: new Set(), ckpt: '', message: '' });
  return per.get(slug);
};
const base = (slug) => '/api/projects/' + encodeURIComponent(slug);
const loading = (text = 'Loading…') => h('div.pp-loading', h('span.job-pulse'), text);
const failNode = (err, retry) => h('div.pp-empty', icon('x'), h('b', 'That did not work'), h('span', err.message || String(err)), retry ? h('button.btn.small', { onclick: retry }, icon('refresh'), 'Try again') : null);

/** Run an action with its button showing that it works; returns the result or undefined after a toast. */
/** A second model reviews the changes; the findings open in a sheet with "Fix these" for the agent. */
export async function reviewChanges(btn, slug) {
  const out = await act(btn, () => api.post(base(slug) + '/review', {}));
  if (!out) return;
  if (!out.review) return toast('Nothing to review yet', { body: 'No uncommitted changes and no recent agent run.' });
  const body = h('div.md.review-body');
  body.innerHTML = renderMarkdown(out.review);
  sheet({
    title: 'Code review',
    body: h('div', h('p.section-desc', `${out.model_label || out.model} reviewed ${out.scope}.`), body),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Close'),
      h('button.btn.primary', {
        onclick: () => {
          close();
          bus.emit('compose:send', { text: 'Fix these code review findings (skip any you are sure are wrong, and say why):\n\n' + out.review, display: 'Fix the code review findings' });
        },
      }, icon('wand'), 'Fix these'),
    ],
  });
}

async function act(btn, fn, done) {
  const label = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.classList.add('busy');
  }
  try {
    const out = await fn();
    if (done) toast(typeof done === 'function' ? done(out) : done, { timeout: 2600 });
    return out;
  } catch (err) {
    toast(err.message || 'Failed', { type: 'error', timeout: 7000 });
    return undefined;
  } finally {
    if (btn && btn.isConnected) {
      btn.disabled = false;
      btn.classList.remove('busy');
      btn.innerHTML = label;
    }
  }
}

// ---------- Changes ----------

export async function renderChanges(el, ctx) {
  const d = ctx.data;
  const s = st(d.slug);
  if (s.scope === 'work' && !d.git) s.scope = 'run';
  const scopes = [['run', 'Last run', 'agent'], d.git ? ['work', 'Uncommitted', 'diff'] : null, ['history', 'Checkpoints', 'history']].filter(Boolean);
  const body = h('div.pc-body', loading());
  const bar = h(
    'div.pp-subbar',
    h('div.seg', scopes.map(([k, label]) => h('button' + (s.scope === k ? '.on' : ''), { type: 'button', onclick: () => { s.scope = k; renderChanges(el, ctx); } }, label))),
    h('span.grow'),
    h('button.icon-btn', { title: 'Refresh', onclick: () => renderChanges(el, ctx) }, icon('refresh')),
  );
  el.replaceChildren(bar, body);
  try {
    if (s.scope === 'run') await runScope(body, ctx, s);
    else if (s.scope === 'work') await workScope(body, ctx, s);
    else await historyScope(body, ctx, s);
  } catch (err) {
    body.replaceChildren(failNode(err, () => renderChanges(el, ctx)));
  }
}

async function runScope(body, ctx, s) {
  const d = ctx.data;
  const res = await api.get(base(d.slug) + '/diff?scope=run');
  ctx.badge('changes', res.files.length);
  if (!res.base) {
    body.replaceChildren(h('div.pp-empty', icon('history'), h('b', 'No agent run yet'), h('span', 'Before the agent starts, Scorpi saves a checkpoint of the files. What it changes shows up here, and you can undo it.')));
    return;
  }
  const running = d.job && d.job.status === 'running';
  const head = h(
    'div.pc-summary',
    h('div.pc-sum-text', h('b', res.files.length ? `${res.files.length} file${res.files.length === 1 ? '' : 's'} changed` : 'No changes yet'), ' ', diffStat(res.add, res.del),
      h('div.muted', (running ? 'Since the agent started' : 'In the last run') + ' · ' + fmtRelative(res.base.time))),
    res.files.length && !running ? h('button.btn.small.quiet', { title: 'A second model reviews what changed', onclick: (e) => reviewChanges(e.currentTarget, d.slug) }, icon('eye'), 'Review') : null,
    res.files.length
      ? h('button.btn.small.quiet', {
          title: running ? 'Stop the agent first' : 'Put every file back as it was before this run',
          disabled: running,
          onclick: async (e) => {
            if (!(await confirmSheet('Undo the last run?', 'Every file goes back to how it was before the agent started this run. The current files are saved as a checkpoint first, so you can return to them.', { confirm: 'Undo run', danger: true }))) return;
            const out = await act(e.currentTarget, () => api.post(base(d.slug) + '/checkpoints/' + res.base.sha + '/restore'), 'Run undone. The files are back as before it.');
            if (out) ctx.refresh();
            if (out && out.undid_run) askWhy(d.slug, out.run_title);
          },
        }, icon('history'), 'Undo run')
      : null,
  );
  body.replaceChildren(head, res.files.length ? diffList(res.files, { openSet: s.open, open: res.files.length <= 4 }) : h('div.pp-empty.small', h('span', running ? 'The agent has not changed any files yet.' : 'The last run did not change any files.')));
}

async function workScope(body, ctx, s) {
  const d = ctx.data;
  const [git, res] = await Promise.all([api.get(base(d.slug) + '/git'), api.get(base(d.slug) + '/diff?scope=work')]);
  ctx.badge('changes', res.files.length);
  const discard = (f) => [
    h('button.icon-btn', {
      title: f.status === 'A' || f.status === '?' ? 'Delete this new file' : 'Discard the changes to this file',
      onclick: async (e) => {
        e.preventDefault();
        if (!(await confirmSheet('Discard changes?', `${f.path} goes back to the last commit${f.status === 'A' ? ' (it is new, so it is deleted)' : ''}. This can't be undone with git, but checkpoints may still have it.`, { confirm: 'Discard', danger: true }))) return;
        if (await act(null, () => api.post(base(d.slug) + '/git/discard', { path: f.path }))) ctx.refresh();
      },
    }, icon('retry')),
  ];
  const head = h(
    'div.pc-summary',
    h('div.pc-sum-text',
      h('b', res.files.length ? `${res.files.length} uncommitted file${res.files.length === 1 ? '' : 's'}` : 'Everything is committed'), ' ', res.files.length ? diffStat(res.add, res.del) : null,
      h('div.muted', icon('branch', 'inline'), ' ', git.branch || 'detached', git.upstream ? ` · ${git.ahead ? '↑' + git.ahead + ' ' : ''}${git.behind ? '↓' + git.behind : ''}${!git.ahead && !git.behind ? 'in sync' : ''}` : git.github ? '' : ' · not on GitHub')),
    res.files.length ? h('button.btn.small.quiet', { title: 'A second model reviews the uncommitted changes', onclick: (e) => reviewChanges(e.currentTarget, d.slug) }, icon('eye'), 'Review') : null,
  );
  const nodes = [head];
  if (res.files.length) {
    nodes.push(diffList(res.files, { openSet: s.open, open: res.files.length <= 4, actions: discard }));
    nodes.push(commitBox(ctx, s, git));
  } else if (git.log && git.log.length) {
    nodes.push(h('div.pc-sec', h('div.pa-h', 'Recent commits'), commitsList(git.log.slice(0, 8))));
  }
  body.replaceChildren(...nodes);
}

function commitBox(ctx, s, git) {
  const d = ctx.data;
  const msg = h('textarea.input.pc-msg', { rows: 3, placeholder: 'Commit message', value: s.message, oninput: () => (s.message = msg.value) });
  msg.value = s.message;
  const write = h('button.btn.small.quiet', {
    title: 'Let the model write the message from the diff',
    onclick: async (e) => {
      const out = await act(e.currentTarget, () => api.post(base(d.slug) + '/git/message'));
      if (out) {
        msg.value = s.message = out.message;
        msg.focus();
      }
    },
  }, icon('spark'), 'Write it');
  const commit = (push) => async (e) => {
    if (!msg.value.trim()) {
      msg.focus();
      return toast('Write a commit message first (or let the model write it).');
    }
    const out = await act(e.currentTarget, () => api.post(base(d.slug) + '/git/commit', { message: msg.value, push }), (r) => `Committed ${r.sha}${r.pushed ? ' and pushed to ' + r.pushed : ''}`);
    if (out) {
      s.message = '';
      ctx.refresh();
    }
  };
  return h(
    'div.pc-commit',
    msg,
    h('div.pc-commit-bar', write, h('span.grow'),
      h('button.btn.small', { onclick: commit(false) }, icon('check'), 'Commit'),
      git.remote ? h('button.btn.small.primary', { onclick: commit(true) }, icon('upload'), 'Commit & push') : null),
  );
}

async function historyScope(body, ctx, s) {
  const d = ctx.data;
  const res = await api.get(base(d.slug) + '/checkpoints');
  const list = res.checkpoints || [];
  const save = h('button.btn.small.quiet', {
    onclick: async (e) => {
      const label = await promptSheet('Save a checkpoint', { label: 'Name (optional)', placeholder: 'e.g. Before trying the new layout', confirm: 'Save' });
      if (label === null) return;
      if (await act(e.currentTarget, () => api.post(base(d.slug) + '/checkpoints', { label: label || 'Saved by hand' }), 'Checkpoint saved')) renderChanges(body.parentNode, ctx);
    },
  }, icon('plus'), 'Save now');
  const head = h('div.pc-summary', h('div.pc-sum-text', h('b', list.length ? `${list.length} checkpoint${list.length === 1 ? '' : 's'}` : 'No checkpoints yet'),
    h('div.muted', res.on ? 'Saved before and after every agent run.' : 'Automatic checkpoints are off (Connections → GitHub & git).')), save);
  if (!list.length) {
    body.replaceChildren(head, h('div.pp-empty.small', h('span', 'Checkpoints appear when the agent works on this project, or when you save one.')));
    return;
  }
  const rows = list.map((c) => {
    const detail = h('div.ck-detail');
    const row = h(
      'details.ck',
      {
        open: s.ckpt === c.sha,
        ontoggle: async () => {
          if (!row.open) {
            if (s.ckpt === c.sha) s.ckpt = '';
            return;
          }
          s.ckpt = c.sha;
          detail.replaceChildren(loading());
          try {
            const res2 = await api.get(base(d.slug) + '/diff?scope=checkpoint&sha=' + c.sha);
            detail.replaceChildren(
              h('div.ck-acts',
                h('span.muted', res2.files.length ? `${res2.files.length} file${res2.files.length === 1 ? '' : 's'} changed in this step` : 'No file changes in this step'),
                h('span.grow'),
                h('button.btn.small.quiet', {
                  disabled: res.running,
                  title: res.running ? 'Stop the agent first' : 'Put the files back as they were at this checkpoint',
                  onclick: async (e) => {
                    if (!(await confirmSheet('Restore this checkpoint?', `The project's files go back to "${c.subject}" (${fmtRelative(c.time)}). The current files are saved as a checkpoint first.`, { confirm: 'Restore', danger: true }))) return;
                    if (await act(e.currentTarget, () => api.post(base(d.slug) + '/checkpoints/' + c.sha + '/restore'), 'Restored. The previous state is the newest checkpoint.')) ctx.refresh();
                  },
                }, icon('history'), 'Restore')),
              res2.files.length ? diffList(res2.files, { open: res2.files.length <= 2 }) : '',
            );
          } catch (err) {
            detail.replaceChildren(failNode(err));
          }
        },
      },
      h('summary.ck-head', h('span.mk.' + (c.subject.startsWith('Before') ? 'pending' : c.subject.startsWith('After') ? 'done' : 'active')),
        h('span.ck-sub', c.subject), h('span.ck-meta', c.files ? diffStat(c.add, c.del) : null, h('span', fmtRelative(c.time)))),
      detail,
    );
    return row;
  });
  body.replaceChildren(head, h('div.ck-list', rows));
}

function commitsList(log) {
  return h('div.gh-list', log.map((c) => h('div.gh-row', h('span.mono.faint', c.short), h('span.gh-title', c.subject), h('span.gh-meta', fmtRelative(c.time)))));
}

// ---------- GitHub ----------

export async function renderGithub(el, ctx) {
  const d = ctx.data;
  el.replaceChildren(loading());
  let git;
  try {
    git = await api.get(base(d.slug) + '/git');
  } catch (err) {
    el.replaceChildren(failNode(err, () => renderGithub(el, ctx)));
    return;
  }
  const again = () => renderGithub(el, ctx);
  if (!git.git) {
    el.replaceChildren(h('div.pp-empty', icon('branch'), h('b', 'Git is not installed'), h('span', 'Install Git for Windows, then restart Scorpi.'), h('a.btn.small', { href: 'https://git-scm.com/download/win', target: '_blank', rel: 'noopener' }, icon('external'), 'Get Git')));
    return;
  }
  if (!git.repo || !git.github) {
    el.replaceChildren(publishCard(ctx, git, again));
    return;
  }
  const full = git.github;
  const head = h(
    'div.gh-head',
    h('div.gh-repo', icon('branch'), h('a', { href: 'https://github.com/' + full, target: '_blank', rel: 'noopener' }, full)),
    h('div.gh-branchbar',
      h('button.btn.small.quiet.gh-branch', { 'data-pop-anchor': '', title: 'Switch or create a branch', onclick: (e) => branchMenu(e.currentTarget, ctx, git, again) }, icon('branch'), git.branch || 'detached', icon('down')),
      h('span.grow'),
      h('button.btn.small.quiet', { title: 'Get the newest commits from GitHub', onclick: async (e) => (await act(e.currentTarget, () => api.post(base(d.slug) + '/git/pull'), (r) => r.result || 'Pulled')) && ctx.refresh() }, icon('download'), 'Pull', git.behind ? h('span.count', String(git.behind)) : null),
      h('button.btn.small' + (git.ahead || !git.upstream ? '.primary' : '.quiet'), { title: 'Send your commits to GitHub', onclick: async (e) => (await act(e.currentTarget, () => api.post(base(d.slug) + '/git/push'), 'Pushed')) && ctx.refresh() }, icon('upload'), 'Push', git.ahead ? h('span.count', String(git.ahead)) : null)),
  );
  const body = h('div.gh-body', loading('Asking GitHub…'));
  el.replaceChildren(head, body);
  if (!git.connected) {
    body.replaceChildren(connectNote('Connect GitHub to see pull requests, issues and checks here, and to push without a password prompt.'));
    return;
  }
  let gh;
  try {
    gh = await api.get(base(d.slug) + '/github');
  } catch (err) {
    body.replaceChildren(failNode(err, again));
    return;
  }
  const dirty = (git.files || []).length;
  const onDefault = git.branch && git.branch === (git.default_branch || (gh.info && gh.info.default_branch));
  const prBox = gh.pr
    ? h('div.gh-pr', h('div.gh-pr-top', icon('pr'), h('a', { href: gh.pr.url, target: '_blank', rel: 'noopener' }, h('b', `#${gh.pr.number}`), ' ', gh.pr.title), gh.pr.draft ? h('span.badge', 'draft') : null),
        h('div.muted', `${gh.pr.head} → ${gh.pr.base}`), checksNode(gh.checks))
    : h('div.gh-pr.none',
        h('div', h('b', onDefault ? `You're on ${git.branch}` : `Branch ${git.branch || '(detached)'}`), h('div.muted', onDefault ? 'Creating a pull request moves your changes to a new branch first.' : 'No pull request for this branch yet.')),
        h('button.btn.small.primary', { onclick: () => prSheet(ctx, git, gh, again) }, icon('pr'), 'Create pull request'),
        dirty ? h('div.muted.small', `${dirty} uncommitted file${dirty === 1 ? '' : 's'} will be committed with it.`) : null,
        gh.checks && gh.checks.state !== 'none' ? checksNode(gh.checks) : null);
  body.replaceChildren(
    h('div.pc-sec', h('div.pa-h', 'This branch'), prBox),
    h('div.pc-sec', h('div.pa-h', 'Issues', h('span.count', String(gh.issues.length))),
      gh.issues.length ? h('div.gh-list', gh.issues.map((i) => issueRow(ctx, full, i))) : h('div.muted.small', 'No open issues.')),
    h('div.pc-sec', h('div.pa-h', 'Pull requests', h('span.count', String(gh.pulls.length))),
      gh.pulls.length ? h('div.gh-list', gh.pulls.map((p) => h('a.gh-row', { href: p.url, target: '_blank', rel: 'noopener' }, h('span.mono.faint', '#' + p.number), h('span.gh-title', p.title, p.draft ? h('span.badge', 'draft') : null), h('span.gh-meta', `${p.head} → ${p.base}`)))) : h('div.muted.small', 'No open pull requests.')),
    git.log && git.log.length ? h('div.pc-sec', h('div.pa-h', 'Recent commits'), commitsList(git.log.slice(0, 10))) : '',
  );
}

function checksNode(checks) {
  if (!checks || checks.state === 'none') return null;
  const mark = (r) => (r.status !== 'completed' ? 'active' : r.conclusion === 'success' || r.conclusion === 'skipped' || r.conclusion === 'neutral' ? 'done' : 'failed');
  const label = { success: 'Checks passed', failure: 'Checks failed', pending: 'Checks running' }[checks.state];
  return h('details.gh-checks.' + checks.state, { open: checks.state === 'failure' },
    h('summary', h('span.mk.' + (checks.state === 'success' ? 'done' : checks.state === 'failure' ? 'failed' : 'active')), label, h('span.muted', ` · ${checks.runs.length}`)),
    h('div.gh-list', checks.runs.map((r) => h('a.gh-row', { href: r.url || '#', target: '_blank', rel: 'noopener' }, h('span.mk.' + mark(r)), h('span.gh-title', r.name), h('span.gh-meta', r.conclusion || r.status)))));
}

function issueRow(ctx, full, i) {
  return h(
    'div.gh-row.issue',
    h('span.mono.faint', '#' + i.number),
    h('a.gh-title', { href: i.url, target: '_blank', rel: 'noopener' }, i.title, i.labels.slice(0, 3).map((l) => h('span.badge', l))),
    h('button.btn.small.quiet', {
      title: 'Hand this issue to the coder: it makes a branch, fixes it and commits',
      onclick: async (e) => {
        if (!(await confirmSheet(`Fix #${i.number}?`, `The coder reads the issue "${i.title}", works on a branch fix/issue-${i.number}, fixes it, runs the checks and commits. You review the changes and open the pull request.`, { confirm: 'Start fixing' }))) return;
        const out = await act(e.currentTarget, () => api.post(base(ctx.data.slug) + `/github/issues/${i.number}/fix`), `The coder is on #${i.number}`);
        if (out) {
          ctx.refresh();
          ctx.setTab('agents');
          bus.emit('project:conversation', out.conv_id);
        }
      },
    }, icon('wand'), 'Fix'),
  );
}

function branchMenu(anchor, ctx, git, again) {
  const d = ctx.data;
  const items = [{ title: 'Branches' }];
  for (const b of git.branches || []) {
    items.push({ label: b.name, checked: b.current, onClick: async () => !b.current && (await act(null, () => api.post(base(d.slug) + '/git/switch', { name: b.name }), 'On ' + b.name)) && (ctx.refresh(), again()) });
  }
  items.push('sep', {
    label: 'New branch…', icon: 'plus', onClick: async () => {
      const name = await promptSheet('New branch', { label: 'Name', placeholder: 'e.g. feature/dark-mode', confirm: 'Create and switch' });
      if (name && (await act(null, () => api.post(base(d.slug) + '/git/switch', { name: name.trim(), create: true }), 'On ' + name.trim()))) {
        ctx.refresh();
        again();
      }
    },
  });
  popover(anchor, items);
}

function prSheet(ctx, git, gh, again) {
  const d = ctx.data;
  const title = h('input.input', { placeholder: 'What this pull request does' });
  const text = h('textarea.input', { rows: 9, placeholder: 'Description (Markdown)' });
  const baseIn = h('input.input', { value: git.default_branch || (gh.info && gh.info.default_branch) || 'main' });
  let draft = false;
  const write = h('button.btn.small.quiet', {
    onclick: async (e) => {
      const out = await act(e.currentTarget, () => api.post(base(d.slug) + '/github/pr-text', { base: baseIn.value.trim() }));
      if (out) {
        title.value = out.title;
        text.value = out.body;
      }
    },
  }, icon('spark'), 'Write it from the changes');
  sheet({
    title: 'Create pull request',
    wide: true,
    body: h('div.form-stack',
      h('div.field', h('label', 'Title'), title),
      h('div.field', h('label', 'Description'), text),
      h('div.row.gap', h('div.field.grow', h('label', 'Merge into'), baseIn), h('label.row.gap.muted', switchEl(false, (v) => (draft = v)), 'Draft')),
      write),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h('button.btn.primary', {
        onclick: async (e) => {
          if (!title.value.trim()) return title.focus();
          const pr = await act(e.currentTarget, () => api.post(base(d.slug) + '/github/pr', { title: title.value.trim(), body: text.value, base: baseIn.value.trim(), draft }));
          if (pr) {
            close();
            toast(`Pull request #${pr.number} is open`, { body: pr.title, timeout: 9000, action: { label: 'Open', run: () => window.open(pr.url, '_blank', 'noopener') } });
            ctx.refresh();
            again();
          }
        },
      }, icon('pr'), 'Create pull request'),
    ],
  });
}

function connectNote(text) {
  return h('div.gh-connect', icon('branch'), h('div', h('b', 'GitHub is not connected'), h('div.muted', text)), h('button.btn.small.primary', { onclick: () => navigate('/connections#github') }, 'Connect'));
}

function publishCard(ctx, git, again) {
  const d = ctx.data;
  const init = h('button.btn.small', {
    onclick: async (e) => (await act(e.currentTarget, () => api.post(base(d.slug) + '/git/init'), 'Git is set up for this project')) && (ctx.refresh(), again()),
  }, icon('branch'), 'Start a git repository');
  const publish = h('button.btn.small.primary', { disabled: !git.connected, onclick: () => publishSheet(ctx, again) }, icon('upload'), 'Publish to GitHub');
  return h('div.pp-empty',
    icon('branch'),
    h('b', git.repo ? 'Not on GitHub yet' : 'No git repository yet'),
    h('span', git.repo ? 'Publish it as a new repository on your GitHub account, then pull requests, issues and checks show up here.' : 'Track the project with git to commit, branch and publish it. The agent\'s checkpoints work either way.'),
    h('div.row.gap', git.repo ? null : init, publish),
    git.connected ? null : connectNote('Connect your account to publish and push.'),
  );
}

function publishSheet(ctx, again) {
  const d = ctx.data;
  const name = h('input.input', { value: d.slug });
  const desc = h('input.input', { placeholder: 'One line about it (optional)' });
  let priv = true;
  sheet({
    title: 'Publish to GitHub',
    body: h('div.form-stack', h('div.field', h('label', 'Repository name'), name), h('div.field', h('label', 'Description'), desc),
      h('label.row.gap', switchEl(true, (v) => (priv = v)), h('span', 'Private ', h('span.muted', '(only you can see it)')))),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h('button.btn.primary', {
        onclick: async (e) => {
          const out = await act(e.currentTarget, () => api.post(base(d.slug) + '/github/publish', { name: name.value.trim(), private: priv, description: desc.value.trim() }));
          if (out) {
            close();
            toast('Published to GitHub', { body: out.repo.full_name, timeout: 8000, action: { label: 'Open', run: () => window.open(out.repo.url, '_blank', 'noopener') } });
            ctx.refresh();
            again();
          }
        },
      }, icon('upload'), 'Publish'),
    ],
  });
}

export function resetGit(slug) {
  per.delete(slug);
}

