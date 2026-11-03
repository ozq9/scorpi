// Library: browse the host's files (workspace first) and manage the knowledge base.

import { api, fileUrl, upload } from '../lib/api.js';
import { clear, extOf, fmtDate, fmtSize, h, icon } from '../lib/dom.js';
import { confirmSheet, emptyState, promptSheet, toast } from '../lib/ui.js';
import { openViewer, previewBody } from '../lib/viewer.js';
import { bus } from '../store.js';
import { navigate } from '../router.js';

const IMG = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'];
const AUDIO = ['mp3', 'wav', 'ogg', 'm4a', 'flac'];
const VIDEO = ['mp4', 'webm', 'mov'];
const DOCS = ['pdf', 'docx', 'xlsx', 'pptx'];

let root;
let tab = 'files';
let cwd = '';
let listing = null;
let selected = null;

export function renderLibrary(el, sub) {
  root = el;
  if (sub === 'knowledge') tab = 'knowledge';
  clear(root);
  const tabs = h(
    'div.tabs',
    ['files', 'knowledge'].map((t) =>
      h('button' + (t === tab ? '.on' : ''), { onclick: () => { tab = t; renderLibrary(el); } }, t === 'files' ? 'Files' : 'Knowledge'),
    ),
  );
  const body = h('div');
  root.appendChild(
    h(
      'div.page',
      h(
        'div.page-inner',
        h(
          'div.page-head',
          h('div.ph-text', h('div.page-kicker', 'Library'), h('h1.page-title', tab === 'files' ? 'Files on this PC' : h('span', 'Your ', h('em', 'knowledge'))),
            h('p.page-sub', tab === 'files' ? 'Everything Scorp creates lands in the workspace. Browse any folder on the host, preview, and hand files to the assistant.' : 'Documents indexed for retrieval. Scorp searches them and cites passages when you ask about your own material.')),
        ),
        tabs,
        body,
      ),
    ),
  );
  if (tab === 'files') files(body);
  else knowledge(body);
}

// ---------- files ----------

async function files(body) {
  const list = h('div.panel');
  const preview = h('div.preview');
  const crumbs = h('div.crumbs');
  const roots = h('div.roots');
  const uploadBtn = h('button.btn.small', { onclick: () => pick() }, icon('upload'), 'Upload here');
  const input = h('input', { type: 'file', multiple: true, hidden: true, onchange: (e) => uploadFiles([...e.target.files]) });
  const pick = () => input.click();
  body.appendChild(
    h(
      'div.lib-layout',
      h('div', roots, h('div.row', { style: { marginBottom: '10px' } }, h('div.grow', crumbs), uploadBtn, h('button.icon-btn', { title: 'Refresh', onclick: () => load(cwd) }, icon('retry')), input), list),
      preview,
    ),
  );

  async function uploadFiles(fileList) {
    for (const f of fileList) {
      try {
        await upload('/api/files/upload?dir=' + encodeURIComponent(cwd), f);
      } catch (err) {
        toast('Upload failed', { type: 'error', body: err.message });
      }
    }
    toast(`Uploaded ${fileList.length} file(s)`);
    load(cwd);
  }

  async function load(path) {
    try {
      listing = await api.get('/api/files' + (path ? '?path=' + encodeURIComponent(path) : ''));
    } catch (err) {
      toast('Cannot open folder', { type: 'error', body: err.message });
      return;
    }
    cwd = listing.path;
    renderRoots();
    renderCrumbs();
    renderList();
  }

  function renderRoots() {
    clear(roots);
    listing.roots.forEach((r) => roots.appendChild(h('button' + (cwd === r.path ? '.on' : ''), { onclick: () => load(r.path) }, r.label)));
  }

  function renderCrumbs() {
    clear(crumbs);
    const sep = cwd.includes('\\') ? '\\' : '/';
    const parts = cwd.split(/[\\/]/).filter(Boolean);
    let acc = cwd.startsWith('/') ? '/' : '';
    if (cwd.startsWith('/')) crumbs.appendChild(h('button', { onclick: () => load('/') }, '/'));
    parts.forEach((p, i) => {
      acc += (i === 0 && /:$/.test(p) ? p + sep : p + (i < parts.length - 1 ? sep : ''));
      const target = acc;
      if (i > 0 || cwd.startsWith('/')) crumbs.appendChild(h('span.sep', '›'));
      crumbs.appendChild(h('button', { onclick: () => load(target) }, p));
    });
  }

  function renderList() {
    clear(list);
    if (listing.parent) {
      list.appendChild(h('div.f-row', { onclick: () => load(listing.parent) }, h('span.f-ic', icon('left')), h('span.f-n.muted', 'Up'), h('span'), h('span')));
    }
    if (!listing.entries.length) list.appendChild(emptyState('Empty folder', 'Nothing here yet.'));
    listing.entries.forEach((e) => {
      const row = h(
        'div.f-row' + (selected && selected.path === e.path ? '.sel' : ''),
        {
          onclick: () => (e.dir ? load(e.path) : select(e, row)),
          ondblclick: () => !e.dir && openViewer({ path: e.path, size: e.size }),
        },
        h('span.f-ic' + (e.dir ? '.dir' : ''), icon(e.dir ? 'folder' : IMG.includes(extOf(e.name)) ? 'image' : DOCS.includes(extOf(e.name)) ? 'doc' : 'file')),
        h('span.f-n', e.name),
        h('span.f-s', e.dir ? '' : fmtSize(e.size)),
        h('span.f-d', fmtDate(e.mtime, { day: 'numeric', month: 'short', year: '2-digit' })),
      );
      list.appendChild(row);
    });
  }

  async function select(entry, row) {
    selected = entry;
    list.querySelectorAll('.f-row.sel').forEach((r) => r.classList.remove('sel'));
    row.classList.add('sel');
    clear(preview);
    const ext = extOf(entry.name);
    const url = fileUrl(entry.path);
    let media;
    if (IMG.includes(ext)) media = h('img', { src: url, onclick: () => openViewer({ path: entry.path, kind: 'image' }) });
    else if (ext === 'pdf') media = h('iframe', { src: url, title: entry.name });
    else if (AUDIO.includes(ext)) media = h('audio', { controls: true, src: url, style: { width: '90%', margin: '40px 0' } });
    else if (VIDEO.includes(ext)) media = h('video', { controls: true, src: url, style: { width: '100%' } });
    else media = h('div.pv-rich', previewBody({ path: entry.path, size: entry.size }));
    const inWorkspace = listing.workspace && entry.path.startsWith(listing.workspace);
    preview.appendChild(
      h(
        'div.panel',
        h('div.pv-media', media),
        h(
          'div.pv-meta',
          h('div.pv-name', entry.name),
          h('div.pv-sub', `${fmtSize(entry.size)} · ${fmtDate(entry.mtime, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}`),
          h(
            'div.row',
            h('button.btn.primary.small', { onclick: () => ask(entry) }, icon('spark'), 'Ask Scorp'),
            h('button.btn.small', { onclick: () => openViewer({ path: entry.path, size: entry.size }) }, icon('eye'), 'Full screen'),
            h('a.btn.small', { href: fileUrl(entry.path, true), download: entry.name }, icon('download'), 'Download'),
            h('button.btn.small', { onclick: () => api.post('/api/file/open', { path: entry.path }).then(() => toast('Opened on the host PC')) }, icon('external'), 'Open on PC'),
            h('button.btn.small', { onclick: () => api.post('/api/knowledge', { path: entry.path }).then(() => toast('Indexing…', { body: 'You will be notified when it is searchable.' })) }, icon('book'), 'Add to knowledge'),
            inWorkspace
              ? h('button.btn.quiet.small', {
                  onclick: async () => {
                    if (!(await confirmSheet('Delete file?', entry.path, { confirm: 'Delete', danger: true }))) return;
                    await api.del('/api/file?path=' + encodeURIComponent(entry.path));
                    selected = null;
                    clear(preview);
                    load(cwd);
                  },
                }, icon('trash'))
              : null,
          ),
        ),
      ),
    );
  }

  await load(cwd);
}

function ask(entry) {
  const ext = extOf(entry.name);
  const isData = ['csv', 'xlsx', 'tsv', 'json'].includes(ext);
  navigate('/');
  setTimeout(
    () =>
      bus.emit('compose:prefill', {
        text: isData ? `Analyze ${entry.path} and summarize the key findings.` : `Summarize ${entry.path} and pull out the key points.`,
        skill: isData ? 'data' : DOCS.includes(ext) ? 'document' : '',
      }),
    60,
  );
}

// ---------- knowledge ----------

async function knowledge(body) {
  const list = h('div.panel');
  const results = h('div');
  const search = h('input.input', { placeholder: 'Try a search across your documents…' });
  const fileInput = h('input', { type: 'file', multiple: true, hidden: true, onchange: (e) => addUploads([...e.target.files]) });
  const note = h('p.section-desc');
  body.append(
    h(
      'div.section',
      h('div.row', { style: { marginBottom: '14px' } },
        h('button.btn.primary', { onclick: () => fileInput.click() }, icon('upload'), 'Add documents'),
        h('button.btn', { onclick: addPath }, icon('folder'), 'Add a folder or path'),
        fileInput),
      note,
      list,
    ),
    h('div.section', h('h3.section-title', 'Try it', h('span.rule')), h('form.row', { onsubmit: (e) => { e.preventDefault(); runSearch(); } }, h('div.grow', search), h('button.btn', { type: 'submit' }, icon('search'), 'Search')), h('div', { style: { marginTop: '14px' } }, results)),
  );

  async function addUploads(files) {
    for (const f of files) {
      try {
        const meta = await upload('/api/upload', f);
        await api.post('/api/knowledge', { upload_id: meta.id, title: f.name });
      } catch (err) {
        toast('Could not add ' + f.name, { type: 'error', body: err.message });
      }
    }
    toast('Indexing ' + files.length + ' document(s)', { body: 'A notification arrives when done.' });
  }

  async function addPath() {
    const path = await promptSheet('Add to knowledge', { label: 'File or folder path on the host', placeholder: 'C:\\Users\\you\\Documents\\Notes', confirm: 'Index' });
    if (!path) return;
    try {
      await api.post('/api/knowledge', { path });
      toast('Indexing started', { body: path });
    } catch (err) {
      toast('Could not index', { type: 'error', body: err.message });
    }
  }

  async function runSearch() {
    clear(results);
    if (!search.value.trim()) return;
    const data = await api.post('/api/knowledge/search', { query: search.value });
    if (!data.results.length) return results.appendChild(h('p.muted', 'No matching passages.'));
    data.results.forEach((r) =>
      results.appendChild(h('div.panel.panel-pad', { style: { marginBottom: '10px' } }, h('div.row', h('b', r.title), h('span.badge', '§' + r.chunk), h('span.badge', 'score ' + r.score)), h('p', { style: { margin: '8px 0 0', fontSize: '13.5px', color: 'var(--ink-2)', whiteSpace: 'pre-wrap' } }, r.text.slice(0, 600)))),
    );
  }

  async function load() {
    const data = await api.get('/api/knowledge');
    note.textContent = `Semantic search uses the “${data.embed_model}” embedding model in Ollama (ollama pull ${data.embed_model}). Without it, Scorp falls back to keyword ranking.`;
    clear(list);
    if (!data.documents.length) {
      list.appendChild(emptyState('An empty shelf', 'Add PDFs, Word files, notes or whole folders.'));
      return;
    }
    data.documents.forEach((d) =>
      list.appendChild(
        h(
          'div.list-row',
          h('span.file-glyph', extOf(d.path).slice(0, 4) || 'doc'),
          h('div.lr-main', h('div.lr-title', d.title), h('div.lr-sub', d.path)),
          h('span.badge' + (d.embedded ? '.ok' : ''), d.embedded ? 'semantic' : 'keyword'),
          h('span.badge', d.chunks + ' chunks'),
          h('div.lr-actions', h('button.icon-btn', { title: 'Remove', onclick: async () => { await api.del('/api/knowledge/' + d.id); load(); } }, icon('trash'))),
        ),
      ),
    );
  }
  reloadKnowledge = load;
  load();
}

let reloadKnowledge = null;
bus.on('server:knowledge', () => tab === 'knowledge' && root && root.isConnected && reloadKnowledge && reloadKnowledge());
