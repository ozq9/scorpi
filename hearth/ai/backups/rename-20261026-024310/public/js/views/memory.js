// Memory (what Hearth knows about you) and Records (the structured database).

import { api } from '../lib/api.js';
import { clear, fmtRelative, h, icon } from '../lib/dom.js';
import { confirmSheet, emptyState, field, promptSheet, sheet, toast } from '../lib/ui.js';

let root;
let tab = 'memory';
let collection = '';

export function renderMemory(el) {
  root = el;
  clear(root);
  const body = h('div');
  root.appendChild(
    h(
      'div.page',
      h(
        'div.page-inner',
        h(
          'div.page-head',
          h('div.ph-text', h('div.page-kicker', 'Memory'), h('h1.page-title', tab === 'memory' ? h('span', 'What I ', h('em', 'remember')) : h('span', 'Your ', h('em', 'records'))),
            h('p.page-sub', tab === 'memory' ? 'Preferences and facts Scorp carries into every conversation. Edit or remove anything.' : 'A simple database Scorp reads and updates for you — contacts, expenses, reading lists, inventories.')),
        ),
        h('div.tabs', ['memory', 'records'].map((t) => h('button' + (t === tab ? '.on' : ''), { onclick: () => { tab = t; renderMemory(el); } }, t === 'memory' ? 'Memories' : 'Records'))),
        body,
      ),
    ),
  );
  if (tab === 'memory') memories(body);
  else records(body);
}

async function memories(body) {
  const input = h('input.input', { placeholder: 'Add something to remember, e.g. “I prefer metric units”' });
  const list = h('div.panel');
  body.append(
    h('form.row', { style: { marginBottom: '16px' }, onsubmit: async (e) => { e.preventDefault(); if (!input.value.trim()) return; await api.post('/api/memory', { text: input.value.trim(), kind: 'preference' }); input.value = ''; load(); } }, h('div.grow', input), h('button.btn.primary', { type: 'submit' }, icon('plus'), 'Remember')),
    list,
  );
  async function load() {
    const data = await api.get('/api/memory');
    clear(list);
    if (!data.memories.length) return list.appendChild(emptyState('A blank page', 'Tell Scorp about yourself and it will remember what matters.'));
    data.memories.forEach((m) =>
      list.appendChild(
        h(
          'div.list-row',
          h('span.badge', m.kind),
          h('div.lr-main', h('div.lr-title', m.text), h('div.lr-sub', 'saved ' + fmtRelative(m.created))),
          h(
            'div.lr-actions',
            h('button.icon-btn', { title: 'Edit', onclick: async () => { const t = await promptSheet('Edit memory', { value: m.text }); if (t) { await api.patch('/api/memory/' + m.id, { text: t }); load(); } } }, icon('edit')),
            h('button.icon-btn', { title: 'Forget', onclick: async () => { await api.del('/api/memory/' + m.id); load(); } }, icon('trash')),
          ),
        ),
      ),
    );
  }
  load();
}

async function records(body) {
  const side = h('div.roots');
  const table = h('div.panel', { style: { overflow: 'auto' } });
  const search = h('input.input', { placeholder: 'Filter records…', oninput: () => loadRecords() });
  body.append(
    side,
    h('div.row', { style: { marginBottom: '12px' } }, h('div.grow', search), h('button.btn', { onclick: addRecord }, icon('plus'), 'Add record')),
    table,
  );

  async function loadCollections() {
    const data = await api.get('/api/records/collections');
    clear(side);
    side.appendChild(h('button' + (!collection ? '.on' : ''), { onclick: () => { collection = ''; loadCollections(); loadRecords(); } }, 'All'));
    data.collections.forEach((c) => side.appendChild(h('button' + (collection === c.collection ? '.on' : ''), { onclick: () => { collection = c.collection; loadCollections(); loadRecords(); } }, `${c.collection} · ${c.count}`)));
  }

  async function loadRecords() {
    const qs = new URLSearchParams();
    if (collection) qs.set('collection', collection);
    if (search.value.trim()) qs.set('q', search.value.trim());
    const data = await api.get('/api/records?' + qs);
    clear(table);
    if (!data.records.length) {
      table.appendChild(emptyState('No records yet', 'Ask Scorp: “keep track of my expenses” or “save these contacts”.'));
      return;
    }
    const cols = [...new Set(data.records.flatMap((r) => Object.keys(r.data)))].slice(0, 8);
    const t = h(
      'table',
      { style: { width: '100%', borderCollapse: 'collapse', fontSize: '13px' } },
      h('thead', h('tr', ['#', ...(collection ? [] : ['collection']), ...cols, ''].map((c) => h('th', { style: { textAlign: 'left', padding: '10px 14px', color: 'var(--muted)', fontWeight: 500, borderBottom: '1px solid var(--line)' } }, c)))),
      h(
        'tbody',
        data.records.map((r) =>
          h(
            'tr',
            [r.id, ...(collection ? [] : [r.collection]), ...cols.map((c) => (r.data[c] === undefined ? '' : typeof r.data[c] === 'object' ? JSON.stringify(r.data[c]) : String(r.data[c])))].map((v) =>
              h('td', { style: { padding: '9px 14px', borderBottom: '1px solid var(--line-2)', maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: v }, v),
            ),
            h(
              'td',
              { style: { borderBottom: '1px solid var(--line-2)', whiteSpace: 'nowrap' } },
              h('button.icon-btn', { title: 'Edit', onclick: () => editRecord(r) }, icon('edit')),
              h('button.icon-btn', { title: 'Delete', onclick: async () => { if (await confirmSheet('Delete record?', '#' + r.id, { confirm: 'Delete', danger: true })) { await api.del('/api/records/' + r.id); loadRecords(); loadCollections(); } } }, icon('trash')),
            ),
          ),
        ),
      ),
    );
    table.appendChild(t);
  }

  function editRecord(r) {
    const ta = h('textarea.input', { rows: 10, style: { fontFamily: 'var(--font-mono)', fontSize: '12.5px' } }, JSON.stringify(r.data, null, 2));
    sheet({
      title: `Record #${r.id}`,
      body: field('JSON', ta),
      foot: (close) => [
        h('button.btn.quiet', { onclick: close }, 'Cancel'),
        h('button.btn.primary', { onclick: async () => { try { await api.patch('/api/records/' + r.id, { data: JSON.parse(ta.value) }); close(); loadRecords(); } catch (err) { toast('Invalid JSON', { type: 'error', body: err.message }); } } }, 'Save'),
      ],
    });
  }

  function addRecord() {
    const coll = h('input.input', { value: collection || '', placeholder: 'contacts' });
    const ta = h('textarea.input', { rows: 8, style: { fontFamily: 'var(--font-mono)', fontSize: '12.5px' } }, '{\n  "name": "",\n  "note": ""\n}');
    sheet({
      title: 'New record',
      body: h('div', field('Collection', coll), field('Data (JSON)', ta)),
      foot: (close) => [
        h('button.btn.quiet', { onclick: close }, 'Cancel'),
        h('button.btn.primary', { onclick: async () => { try { await api.post('/api/records', { collection: coll.value.trim() || 'notes', data: JSON.parse(ta.value) }); close(); loadCollections(); loadRecords(); } catch (err) { toast('Invalid JSON', { type: 'error', body: err.message }); } } }, 'Add'),
      ],
    });
  }

  loadCollections();
  loadRecords();
}
