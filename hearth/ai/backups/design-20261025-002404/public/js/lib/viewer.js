// Showing things: one viewer previews anything full screen (pictures, video, sound, PDFs, web pages, documents,
// tables, code) and rich embeds bring link previews, players, diagrams and math into rendered Markdown.

import { api, fileUrl } from './api.js';
import { baseName, extOf, fmtSize, h, icon, loadScript, loadStyle } from './dom.js';
import { highlight, renderMarkdown } from './md.js';
import { toast } from './ui.js';

const EXT = {
  image: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'avif'],
  video: ['mp4', 'webm', 'mov', 'm4v', 'ogv'],
  audio: ['mp3', 'wav', 'ogg', 'oga', 'm4a', 'flac', 'aac', 'opus'],
  pdf: ['pdf'],
  html: ['html', 'htm', 'xhtml'],
  markdown: ['md', 'markdown', 'mdx'],
  table: ['csv', 'tsv'],
  json: ['json', 'geojson', 'ipynb'],
  doc: ['docx', 'xlsx', 'xlsm', 'pptx'],
};

export const LANGS = { js: 'javascript', mjs: 'javascript', ts: 'typescript', py: 'python', html: 'xml', htm: 'xml', svg: 'xml', xml: 'xml', css: 'css', json: 'json', md: 'markdown', sh: 'bash', ps1: 'powershell', bat: 'dos', yml: 'yaml', yaml: 'yaml', rs: 'rust', go: 'go', java: 'java', c: 'c', h: 'c', cpp: 'cpp', cs: 'csharp', php: 'php', rb: 'ruby', sql: 'sql', toml: 'ini', ini: 'ini', tsx: 'typescript', jsx: 'javascript', vue: 'xml', kt: 'kotlin', swift: 'swift', lua: 'lua', gd: 'python', r: 'r', dart: 'dart', scss: 'scss', log: 'plaintext' };

// sandbox for pages Hearth shows: their scripts run, but in an opaque origin that can't reach the app or its API
const PAGE_SANDBOX = 'allow-scripts allow-forms allow-modals allow-popups allow-downloads';

export function kindOf(name, mime = '') {
  const ext = extOf(String(name || '').split(/[?#]/)[0]);
  for (const [kind, list] of Object.entries(EXT)) if (list.includes(ext)) return kind;
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('video/')) return 'video';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime.includes('pdf')) return 'pdf';
  if (mime.includes('html')) return 'html';
  return 'text';
}

const nameOf = (it) => it.name || baseName(it.path || '') || (it.url ? decodeURIComponent(it.url.split(/[?#]/)[0].split('/').pop() || '') || it.url : 'Preview');
const srcOf = (it) => it.url || (it.path ? fileUrl(it.path) : '');

// ---------- the full-screen viewer ----------

let current = null;

/** Open one item or a gallery: {path?, url?, upload?, name?, kind?, mime?, html?, caption?} (kind 'web' for websites). */
export function openViewer(items, index = 0) {
  items = (Array.isArray(items) ? items : [items]).filter(Boolean);
  if (!items.length) return;
  closeViewer();
  let i = Math.max(0, Math.min(index, items.length - 1));
  const title = h('span.vw-name');
  const sub = h('span.vw-sub');
  const acts = h('div.vw-acts');
  const body = h('div.vw-body');
  const count = h('span.vw-count');
  const nav = items.length > 1
    ? [h('button.vw-nav.prev', { type: 'button', title: 'Previous (←)', onclick: () => go(-1) }, icon('left')), h('button.vw-nav.next', { type: 'button', title: 'Next (→)', onclick: () => go(1) }, icon('right'))]
    : [];
  const stage = h('div.vw-stage', body, nav);
  const root = h(
    'div.viewer',
    { role: 'dialog', 'aria-modal': 'true' },
    h('div.vw-bar', h('div.vw-title', title, sub), count, acts, h('button.icon-btn', { type: 'button', title: 'Close (Esc)', onclick: closeViewer }, icon('x'))),
    stage,
  );
  stage.addEventListener('click', (e) => {
    if (e.target === stage || e.target === body || e.target.classList.contains('vw-media')) closeViewer();
  });
  const show = () => {
    const it = items[i];
    const kind = it.kind || kindOf(it.name || it.path || it.url, it.mime);
    title.textContent = it.caption || nameOf(it);
    sub.textContent = it.kind === 'web' ? (it.url || '').replace(/^https?:\/\/(www\.)?/, '') : it.path || '';
    count.textContent = items.length > 1 ? `${i + 1} / ${items.length}` : '';
    acts.replaceChildren(...actionsFor(it, kind));
    body.className = 'vw-body k-' + kind;
    body.replaceChildren(previewBody(it, { full: true }));
  };
  const go = (d) => {
    i = (i + d + items.length) % items.length;
    show();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      closeViewer();
    } else if (items.length > 1 && !e.target.closest('input, textarea, video, audio, [contenteditable]')) {
      if (e.key === 'ArrowLeft') go(-1);
      if (e.key === 'ArrowRight') go(1);
    }
  };
  document.addEventListener('keydown', onKey, true);
  current = { root, onKey };
  document.body.appendChild(root);
  show();
}

export function closeViewer() {
  if (!current) return;
  document.removeEventListener('keydown', current.onKey, true);
  current.root.remove();
  current = null;
}

function actionsFor(it, kind) {
  const out = [];
  if (kind === 'web') {
    out.push(h('a.icon-btn', { href: it.url, target: '_blank', rel: 'noopener noreferrer', title: 'Open in the browser' }, icon('external')));
    return out;
  }
  if (it.path) {
    const tab = h('a.icon-btn', { href: fileUrl(it.path), target: '_blank', rel: 'noopener', title: 'Open in a new tab' }, icon('external'));
    if (kind === 'html') api.get('/api/file/info?path=' + encodeURIComponent(it.path)).then((info) => info.view && (tab.href = info.view)).catch(() => {});
    out.push(
      tab,
      h('a.icon-btn', { href: fileUrl(it.path, true), download: baseName(it.path), title: 'Download' }, icon('download')),
      h('button.icon-btn', { type: 'button', title: 'Open on the PC', onclick: () => api.post('/api/file/open', { path: it.path }).then(() => toast('Opened on the host PC')).catch((e) => toast(e.message, 'error')) }, icon('monitor')),
    );
  } else if (it.url) {
    out.push(
      h('a.icon-btn', { href: it.url, target: '_blank', rel: 'noopener', title: 'Open in a new tab' }, icon('external')),
      h('a.icon-btn', { href: it.url, download: nameOf(it), title: 'Download' }, icon('download')),
    );
  }
  return out;
}

// ---------- previews of one item (also used inline by the project pane) ----------

export function previewBody(it, { full = false } = {}) {
  const kind = it.kind || kindOf(it.name || it.path || it.url, it.mime);
  const src = srcOf(it);
  switch (kind) {
    case 'image': {
      const img = h('img.vw-img', { src, alt: it.caption || nameOf(it) });
      img.addEventListener('click', (e) => {
        e.stopPropagation();
        img.classList.toggle('zoom');
      });
      return h('div.vw-media', img);
    }
    case 'video':
      return h('div.vw-media', h('video', { src, controls: true, autoplay: full, playsInline: true, preload: 'metadata' }));
    case 'audio':
      return h('div.vw-media.vw-audio', h('div.vw-glyph', icon('speaker')), h('div.vw-audio-name', nameOf(it)), h('audio', { src, controls: true, autoplay: full }));
    case 'pdf':
      return h('div.vw-pdf', h('iframe.vw-frame', { src, title: nameOf(it) }), h('a.vw-fallback', { href: src, target: '_blank', rel: 'noopener' }, icon('external'), 'Open the PDF in a new tab'));
    case 'html':
      return htmlPreview(it);
    case 'web':
      return webPreview(it, full);
    default:
      return textPreview(it, kind);
  }
}

function htmlPreview(it) {
  const frame = h('iframe.vw-frame', { sandbox: PAGE_SANDBOX, title: nameOf(it) });
  const src = h('div.vw-doc');
  let loaded = false;
  const tabs = h('div.vw-tabs');
  const box = h('div.vw-page', tabs, frame);
  const pick = (which) => {
    tabs.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.v === which));
    if (which === 'page') src.replaceWith(frame);
    else {
      frame.replaceWith(src);
      if (!loaded) {
        loaded = true;
        src.replaceChildren(textPreview(it, 'text', LANGS.html));
      }
    }
  };
  tabs.append(
    h('button.on', { type: 'button', dataset: { v: 'page' }, onclick: () => pick('page') }, icon('eye'), 'Page'),
    h('button', { type: 'button', dataset: { v: 'source' }, onclick: () => pick('source') }, icon('code'), 'Source'),
  );
  if (it.html !== undefined) frame.srcdoc = it.html;
  else if (it.path) {
    api.get('/api/file/info?path=' + encodeURIComponent(it.path))
      .then((info) => (frame.src = info.view || fileUrl(it.path)))
      .catch(() => (frame.src = fileUrl(it.path)));
  } else frame.src = srcOf(it);
  return box;
}

function webPreview(it, full) {
  const player = embedUrl(it.url);
  if (player) return h('div.vw-media', h('iframe.vw-embed', { src: player, allow: 'autoplay; encrypted-media; picture-in-picture; fullscreen; clipboard-write', allowFullscreen: true, title: 'Player' }));
  const box = h('div.vw-web', waiting());
  unfurl(it.url).then((info) => {
    if (['image', 'video', 'audio', 'pdf'].includes(info.kind)) return box.replaceChildren(previewBody({ url: it.url, kind: info.kind }, { full }));
    if (info.frameable && new URL(it.url).origin !== location.origin) {
      return box.replaceChildren(
        h('iframe.vw-frame', { src: it.url, sandbox: 'allow-scripts allow-same-origin allow-forms allow-popups allow-modals', referrerpolicy: 'no-referrer', title: info.title || it.url }),
      );
    }
    box.replaceChildren(h('div.vw-web-card', linkCardBody(info, it.url, true), h('p.muted', 'This site does not allow being shown inside other apps.'), h('a.btn.primary', { href: it.url, target: '_blank', rel: 'noopener noreferrer' }, icon('external'), 'Open the website')));
  });
  return box;
}

const waiting = () => h('div.waiting', h('i'), h('i'), h('i'));

async function loadText(it) {
  if (it.text !== undefined) return { text: it.text, kind: 'text' };
  if (it.html !== undefined) return { text: it.html, kind: 'text' };
  if (it.path) return api.get('/api/file/text?path=' + encodeURIComponent(it.path));
  if (it.upload) return api.get('/api/file/text?upload=' + encodeURIComponent(it.upload));
  const res = await api.raw(it.url);
  if (!res.ok) throw new Error(res.statusText || 'Could not load');
  const buf = new Uint8Array(await res.arrayBuffer());
  if (buf.subarray(0, 4096).includes(0)) return { text: '', kind: 'binary' };
  return { text: new TextDecoder().decode(buf.subarray(0, 2 * 1024 * 1024)), kind: 'text' };
}

function textPreview(it, kind, lang = '') {
  const box = h('div.vw-doc', waiting());
  const name = nameOf(it);
  const ext = extOf(name);
  loadText(it)
    .then(({ text, kind: got }) => {
      if (got === 'binary') return box.replaceChildren(binaryNode(it));
      if (kind === 'markdown') {
        const md = h('div.md.vw-md', { html: renderMarkdown(text) });
        if (it.path) {
          // pictures and links next to the document are relative to its folder, not the workspace
          const dir = it.path.replace(/[\\/][^\\/]*$/, '');
          const sep = it.path.includes('\\') ? '\\' : '/';
          md.querySelectorAll('[data-path]').forEach((el) => {
            const p = el.dataset.path;
            if (/^(?:[A-Za-z]:|[\\/~]|file:)/.test(p)) return;
            el.dataset.path = dir + sep + p.replace(/^\.[\\/]/, '').replace(/[\\/]/g, sep);
            el.setAttribute(el.tagName === 'A' ? 'href' : 'src', fileUrl(el.dataset.path));
          });
        }
        box.replaceChildren(md);
        enhanceMarkdown(md);
        return;
      }
      if (kind === 'table') return box.replaceChildren(tableNode(text, ext === 'tsv' ? '\t' : null));
      if (got === 'document' || kind === 'doc') return box.replaceChildren(h('div.vw-text', text || '(no text found)'));
      if (kind === 'json') {
        try {
          text = JSON.stringify(JSON.parse(text), null, 2);
        } catch {
          /* show as is */
        }
      }
      const shown = text.length > 300000 ? text.slice(0, 300000) + '\n…' : text;
      const code = h('code' + ((lang || LANGS[ext] || (kind === 'json' ? 'json' : '')) ? '.language-' + (lang || LANGS[ext] || 'json') : ''), shown);
      const md = h('div.md.vw-code', h('pre', code));
      box.replaceChildren(md);
      highlight(md);
    })
    .catch((e) => box.replaceChildren(h('div.vw-empty', icon('file'), h('b', 'Could not open this file'), h('span', e.message))));
  return box;
}

function binaryNode(it) {
  const name = nameOf(it);
  return h(
    'div.vw-empty',
    h('div.file-glyph.big', extOf(name).slice(0, 4) || 'file'),
    h('b', name),
    h('span', (it.size ? fmtSize(it.size) + ' · ' : '') + 'No preview for this kind of file.'),
    h('a.btn', { href: it.path ? fileUrl(it.path, true) : srcOf(it), download: name }, icon('download'), 'Download'),
  );
}

function parseDelimited(text, sep) {
  const first = text.split('\n', 1)[0];
  sep = sep || [',', ';', '\t', '|'].sort((a, b) => first.split(b).length - first.split(a).length)[0];
  const rows = [];
  let row = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length && rows.length < 2001; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"' && !cell) quoted = true;
    else if (c === sep) {
      row.push(cell);
      cell = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
    } else cell += c;
  }
  if (cell || row.length) rows.push([...row, cell]);
  return rows;
}

function tableNode(text, sep) {
  const rows = parseDelimited(text, sep);
  if (!rows.length) return h('div.vw-empty', h('b', 'Empty table'));
  const [head, ...body] = rows;
  return h(
    'div.vw-table',
    h('table', h('thead', h('tr', head.map((c) => h('th', c)))), h('tbody', body.slice(0, 2000).map((r) => h('tr', r.map((c) => h('td', { title: c }, c)))))),
    body.length > 2000 ? h('div.muted', { style: { padding: '10px' } }, 'Showing the first 2000 rows.') : null,
  );
}

// ---------- links: previews and players ----------

/** A player URL for YouTube, Vimeo, Spotify and Loom links, or ''. */
export function embedUrl(url) {
  let m;
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^(www|m|music)\./, '');
    if (host === 'youtu.be' && (m = u.pathname.match(/^\/([\w-]{6,})/))) return ytEmbed(m[1], u);
    if (host === 'youtube.com' || host === 'youtube-nocookie.com') {
      if (u.searchParams.get('v')) return ytEmbed(u.searchParams.get('v'), u);
      if ((m = u.pathname.match(/^\/(?:shorts|embed|live)\/([\w-]{6,})/))) return ytEmbed(m[1], u);
      if (u.searchParams.get('list')) return 'https://www.youtube-nocookie.com/embed/videoseries?list=' + encodeURIComponent(u.searchParams.get('list'));
    }
    if (host === 'vimeo.com' && (m = u.pathname.match(/^\/(\d+)/))) return 'https://player.vimeo.com/video/' + m[1];
    if (host === 'open.spotify.com' && (m = u.pathname.match(/^\/(?:intl-\w+\/)?(track|album|playlist|episode|show|artist)\/(\w+)/))) return `https://open.spotify.com/embed/${m[1]}/${m[2]}`;
    if (host === 'loom.com' && (m = u.pathname.match(/^\/share\/(\w+)/))) return 'https://www.loom.com/embed/' + m[1];
  } catch {
    /* not a URL */
  }
  return '';
}

function ytEmbed(id, u) {
  const t = parseInt(u.searchParams.get('t') || u.searchParams.get('start') || '0', 10);
  return 'https://www.youtube-nocookie.com/embed/' + encodeURIComponent(id) + (t ? '?start=' + t : '');
}

const unfurls = new Map();
export function unfurl(url) {
  if (!unfurls.has(url)) {
    unfurls.set(url, api.get('/api/unfurl?url=' + encodeURIComponent(url)).catch(() => ({ url, title: '', kind: 'page', site: url.replace(/^https?:\/\/(www\.)?/, '').split('/')[0] })));
  }
  return unfurls.get(url);
}

function linkCardBody(info, url, big = false) {
  const site = info.site || url.replace(/^https?:\/\/(www\.)?/, '').split('/')[0];
  const fav = info.icon ? h('img.lc-icon', { src: info.icon, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer', onerror: (e) => e.target.remove() }) : icon('globe');
  return h(
    'div.lc' + (big ? '.big' : ''),
    h('div.lc-text', h('div.lc-site', fav, h('span', site)), h('div.lc-title', info.title || url), info.description ? h('div.lc-desc', info.description) : null),
    // the picture sits behind its box so a tall image never stretches the card
    info.image ? h('div.lc-img', h('img', { src: info.image, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer', onerror: (e) => e.target.parentNode.remove() })) : null,
  );
}

/** What a link turns into when it stands on its own: a player, the picture or video itself, or a preview card. */
export function linkEmbed(url, { caption = '', title = '' } = {}) {
  const player = embedUrl(url);
  if (player) {
    return h(
      'div.embed-player',
      h('iframe', { src: player, allow: 'autoplay; encrypted-media; picture-in-picture; fullscreen; clipboard-write', allowFullscreen: true, loading: 'lazy', title: caption || 'Player' }),
      caption ? h('div.embed-cap', caption) : null,
    );
  }
  const kind = kindOf(url);
  if (kind === 'image' || kind === 'video' || kind === 'audio') return mediaEmbed({ url, caption });
  const card = h('a.link-card', { href: url, target: '_blank', rel: 'noopener noreferrer' }, linkCardBody({ title }, url));
  unfurl(url).then((info) => {
    if (['image', 'video', 'audio'].includes(info.kind)) return card.replaceWith(mediaEmbed({ url, caption, kind: info.kind }));
    card.replaceChildren(linkCardBody({ ...info, title: info.title || title }, url));
    if (info.kind === 'pdf' || info.frameable) {
      card.classList.add('previewable');
      card.title = 'Preview · ctrl-click opens the site';
      card.addEventListener('click', (e) => {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
        e.preventDefault();
        openViewer(info.kind === 'pdf' ? { url, kind: 'pdf' } : { url, kind: 'web', caption: info.title });
      });
    }
  });
  if (!caption) return card;
  return h('div.link-wrap', card, h('div.embed-cap', caption));
}

/** A picture, video or sound in the flow of a message. */
export function mediaEmbed(it) {
  const kind = it.kind || kindOf(it.name || it.path || it.url, it.mime);
  const src = srcOf(it);
  const cap = it.caption ? h('div.embed-cap', it.caption) : null;
  if (kind === 'video') return h('div.embed-media', h('video', { src, controls: true, preload: 'metadata', playsInline: true }), cap);
  if (kind === 'audio') return h('div.embed-media.audio', h('audio', { src, controls: true, preload: 'none' }), cap);
  if (kind === 'image') return h('div.embed-media', h('img', { src, alt: it.caption || '', loading: 'lazy', dataset: it.path ? { path: it.path } : {} }), cap);
  return fileChip(it);
}

/** A compact card for any file; clicking previews it. */
export function fileChip(it) {
  const name = nameOf(it);
  const kind = it.kind || kindOf(name, it.mime);
  return h(
    'button.file-chip',
    { type: 'button', title: 'Preview ' + name, onclick: () => openViewer(it) },
    h('span.file-glyph', extOf(name).slice(0, 4) || 'file'),
    h('span.fc-meta', h('span.fc-name', it.caption || name), h('span.fc-sub', [kind === 'text' ? 'file' : kind, it.size ? fmtSize(it.size) : '', it.path || ''].filter(Boolean).join(' · '))),
    icon('eye', 'fc-eye'),
  );
}

// ---------- diagrams and math ----------

const isDark = () => document.documentElement.classList.contains('is-dark') || document.documentElement.dataset.theme === 'dark';

export function renderMermaid(box, code) {
  box.replaceChildren(waiting());
  import('https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.esm.min.mjs')
    .then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, theme: isDark() ? 'dark' : 'neutral', securityLevel: 'strict', fontFamily: 'Geist, system-ui, sans-serif' });
      const { svg } = await mermaid.render('mmd' + Math.random().toString(36).slice(2), code);
      box.innerHTML = svg;
    })
    .catch((err) => box.replaceChildren(h('pre', { style: { margin: 0, fontSize: '12px' } }, code + '\n\n(' + (err.message || 'could not render') + ')')));
}

const KATEX = 'https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/';
let katexReady = null;

function renderMath(root) {
  if (!/\$\$|\\\(|\\\[/.test(root.textContent)) return;
  if (!katexReady) {
    loadStyle(KATEX + 'katex.min.css');
    katexReady = loadScript(KATEX + 'katex.min.js').then(() => loadScript(KATEX + 'contrib/auto-render.min.js'));
  }
  katexReady
    .then(() =>
      window.renderMathInElement(root, {
        delimiters: [
          { left: '$$', right: '$$', display: true },
          { left: '\\[', right: '\\]', display: true },
          { left: '\\(', right: '\\)', display: false },
        ],
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
        throwOnError: false,
      }),
    )
    .catch(() => {});
}

// ---------- rendered Markdown comes alive ----------

/** Turn a settled .md block's placeholders into players, link previews, file cards, diagrams and math. */
export function enhanceMarkdown(root) {
  root.querySelectorAll('.embed:not([data-wired])').forEach((el) => {
    el.dataset.wired = '';
    if (el.dataset.url) el.replaceChildren(linkEmbed(el.dataset.url));
    else if (el.dataset.path) el.replaceChildren(mediaEmbed({ path: el.dataset.path }));
  });
  root.querySelectorAll('pre code.language-mermaid:not([data-wired])').forEach((code) => {
    code.dataset.wired = '';
    const pre = code.closest('pre');
    const box = h('div.md-diagram');
    const toggle = h('button.act', { type: 'button', title: 'Show the diagram source', onclick: () => pre.classList.toggle('src-open') }, icon('code'));
    (pre.querySelector('.code-acts') || pre.querySelector('.code-head')).prepend(toggle);
    pre.classList.add('diagram');
    pre.insertBefore(box, pre.querySelector('code'));
    renderMermaid(box, code.textContent);
  });
  root.querySelectorAll('.preview-code:not([data-wired])').forEach((b) => {
    b.dataset.wired = '';
    b.addEventListener('click', () => {
      const code = b.closest('pre').querySelector('code');
      const src = code.textContent;
      const svg = /language-(svg|xml)/.test(code.className);
      openViewer({
        kind: 'html',
        name: svg ? 'Graphic preview' : 'Page preview',
        html: svg ? `<!doctype html><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#fff">${src}</body>` : src,
      });
    });
  });
  renderMath(root);
}

// one listener for every picture and file link in rendered text: they open in the viewer
document.addEventListener('click', (e) => {
  const t = e.target instanceof Element ? e.target : null;
  if (!t || e.defaultPrevented) return;
  const img = t.closest('.md img, .embed-media img');
  if (img && !img.closest('a, .viewer .vw-media, .lc, .md-diagram')) {
    const scope = img.closest('.msg') || img.closest('.md') || document.body;
    const imgs = [...scope.querySelectorAll('.md img, .embed-media img')].filter((x) => !x.closest('a, .lc, .md-diagram'));
    const items = imgs.map((x) => (x.dataset.path ? { path: x.dataset.path, kind: 'image', caption: x.alt } : { url: x.currentSrc || x.src, kind: 'image', caption: x.alt }));
    openViewer(items, imgs.indexOf(img));
    return;
  }
  const link = t.closest('a.file-link, code.path-link');
  if (link && !(e.ctrlKey || e.metaKey || e.shiftKey)) {
    e.preventDefault();
    openViewer({ path: link.dataset.path });
  }
});
