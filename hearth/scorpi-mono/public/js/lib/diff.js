// Diffs as the server parses them (scorpi/gitops.py parse_diff): a file list with +/- counts, each file opening to
// its hunks with old and new line numbers.

import { h, icon } from './dom.js';

const KIND = { A: 'added', D: 'deleted', R: 'renamed', M: 'modified', '?': 'new', U: 'conflict' };

/** files: [{path, old_path, status, add, del, binary, truncated, hunks}] -> Node. opts: {open, actions(file) -> Node[]} */
export function diffList(files, { open = files.length <= 6, actions = null, openSet = null } = {}) {
  return h('div.dv', files.map((f) => diffFile(f, { open: openSet ? openSet.has(f.path) : open, actions, openSet })));
}

function diffFile(f, { open, actions, openSet }) {
  const parts = f.path.split('/');
  const name = parts.pop();
  const body = h('div.dv-body');
  let filled = false;
  const fill = () => {
    if (filled) return;
    filled = true;
    body.appendChild(hunksNode(f));
  };
  const box = h(
    'details.dv-file.' + (KIND[f.status] || 'modified'),
    { open, ontoggle: () => { if (box.open) fill(); if (openSet) box.open ? openSet.add(f.path) : openSet.delete(f.path); } },
    h(
      'summary.dv-head',
      icon('right', 'dv-chev'),
      h('span.dv-st', { title: KIND[f.status] || '' }, f.status === '?' ? 'A' : f.status),
      h('span.dv-path', { title: f.status === 'R' ? `${f.old_path} → ${f.path}` : f.path }, parts.length ? h('span.dv-dir', parts.join('/') + '/') : null, h('b', name)),
      h('span.dv-n', f.binary ? h('span.muted', 'binary') : [f.add ? h('span.add', '+' + f.add) : null, f.del ? h('span.del', '−' + f.del) : null]),
      actions ? h('span.dv-acts', { onclick: (e) => e.stopPropagation() }, actions(f)) : null,
    ),
    body,
  );
  if (open) fill();
  return box;
}

function hunksNode(f) {
  if (f.binary) return h('div.dv-note', 'Binary file, not shown.');
  if (!f.hunks.length) return h('div.dv-note', f.status === 'R' ? `Renamed from ${f.old_path}, content unchanged.` : 'No content changes (mode or empty file).');
  const rows = [];
  for (const hk of f.hunks) {
    rows.push(h('div.dv-hunk', hk.header.replace(/^(@@[^@]*@@).*/, '$1'), hk.context ? h('span', ' ' + hk.context) : null));
    for (const [kind, o, n, text] of hk.lines) {
      rows.push(
        h(
          'div.dv-line' + (kind === '+' ? '.add' : kind === '-' ? '.del' : ''),
          h('span.dv-o', o ?? ''),
          h('span.dv-nn', n ?? ''),
          h('span.dv-k', kind === ' ' ? ' ' : kind === '-' ? '−' : '+'),
          h('span.dv-t', text || ' '),
        ),
      );
    }
  }
  if (f.truncated) rows.push(h('div.dv-note', 'Long diff, the rest is not shown.'));
  return h('div.dv-code', rows);
}

/** "+12 −3" */
export function diffStat(add, del) {
  return h('span.dv-n', add ? h('span.add', '+' + add) : null, del ? h('span.del', '−' + del) : null, !add && !del ? h('span.muted', 'no changes') : null);
}
