// A compact, safe Markdown renderer (escapes HTML first, then adds formatting).
// Beyond plain Markdown it shows pictures, videos and sounds (also from paths on the PC), makes file paths
// clickable, marks lone links for rich previews and keeps math intact for KaTeX (see viewer.js).

import { fileUrl } from './api.js';
import { escapeHtml } from './dom.js';

const B = '\u0000';
const VIDEO = /\.(mp4|webm|mov|m4v|ogv)$/i;
const AUDIO = /\.(mp3|wav|ogg|oga|m4a|flac|aac|opus)$/i;
const MEDIA = /\.(png|jpe?g|gif|webp|svg|bmp|avif|ico|mp4|webm|mov|m4v|ogv|mp3|wav|ogg|oga|m4a|flac|aac|opus)$/i;
// a path on the host: C:\..., \\server\..., ~/..., /home/... (not /api/), file:///...
const LOCAL = /^(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|\/(?!\/|api\/)|file:\/\/)/;
const PATH_LIKE = /^(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|\/(?!\/)|file:\/\/)[^\n<>|*?]*\.[A-Za-z0-9]{1,6}$/;

const unescapeHtml = (s) => s.replace(/&(amp|lt|gt|quot|#39);/g, (_, e) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[e]));

// where an image/link target points: a URL, or a file on the host (attribute-safe, built from already escaped text)
function target(raw) {
  const t = raw.trim().replace(/^&lt;(.*)&gt;$/, '$1').replace(/\s+&quot;.*&quot;$/, '');
  if (/^(https?:\/\/|\/api\/|data:image\/)/i.test(t)) return { src: t, name: t };
  if (/^[a-z][a-z0-9+.-]+:/i.test(t) && !LOCAL.test(t)) return null; // other schemes (javascript: ...) stay text
  return { src: escapeHtml(fileUrl(unescapeHtml(t))), path: t, name: t };
}

function media(alt, raw) {
  const t = target(raw);
  if (!t) return alt;
  const name = t.name.split(/[?#]/)[0];
  const path = t.path ? ` data-path="${t.path}"` : '';
  if (VIDEO.test(name)) return `<video class="md-media" src="${t.src}" controls preload="metadata" playsinline${path}></video>`;
  if (AUDIO.test(name)) return `<audio class="md-media" src="${t.src}" controls preload="none"${path}></audio>`;
  return `<img alt="${alt}" src="${t.src}" loading="lazy"${path}>`;
}

function inline(text) {
  const codes = [];
  text = text.replace(/`([^`\n]+)`/g, (_, c) => {
    codes.push(c);
    return `${B}C${codes.length - 1}${B}`;
  });
  // math stays exactly as written for KaTeX: no emphasis or links inside it
  const maths = [];
  text = text.replace(/\$\$[\s\S]+?\$\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]/g, (m) => {
    maths.push(m.replace(/\n/g, ' '));
    return `${B}M${maths.length - 1}${B}`;
  });
  text = text
    .replace(/!\[([^\]]*)\]\(([^)\n]+)\)/g, (_, alt, raw) => media(alt, raw))
    .replace(/\[([^\]]+)\]\(((?:[A-Za-z]:[\\/]|\\\\|~[\\/]|\/(?!\/|api\/)|file:\/\/)[^)\n]+)\)/g, (_, label, raw) => {
      const t = target(raw);
      return `<a class="file-link" href="${t.src}" data-path="${t.path}">${label}</a>`;
    })
    .replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/api\/)[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?'"])/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_\n]+)__/g, '<strong>$1</strong>')
    .replace(/(^|[\s(>])\*([^*\n]+)\*(?=[\s).,;:!?<]|$)/g, '$1<em>$2</em>')
    .replace(/(^|[\s(>])_([^_\n]+)_(?=[\s).,;:!?<]|$)/g, '$1<em>$2</em>')
    .replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
    .replace(/\[(\d{1,2})\](?!\()/g, '<a class="cite" data-cite="$1">$1</a>');
  return text
    .replace(new RegExp(`${B}M(\\d+)${B}`, 'g'), (_, i) => maths[+i])
    .replace(new RegExp(`${B}C(\\d+)${B}`, 'g'), (_, i) => {
      const c = codes[+i];
      return PATH_LIKE.test(unescapeHtml(c)) ? `<code class="path-link" data-path="${c}" title="Preview">${c}</code>` : `<code>${c}</code>`;
    });
}

function table(lines) {
  const rows = lines.map((l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
  const sepIndex = rows.findIndex((r) => r.every((c) => /^:?-{1,}:?$/.test(c)));
  let head = [];
  let body = rows;
  let align = [];
  if (sepIndex === 1) {
    head = rows[0];
    align = rows[1].map((c) => (c.endsWith(':') && c.startsWith(':') ? 'center' : c.endsWith(':') ? 'right' : ''));
    body = rows.slice(2);
  }
  const cell = (tag, c, i) => `<${tag}${align[i] ? ` style="text-align:${align[i]}"` : ''}>${inline(c)}</${tag}>`;
  return (
    '<table>' +
    (head.length ? `<thead><tr>${head.map((c, i) => cell('th', c, i)).join('')}</tr></thead>` : '') +
    `<tbody>${body.map((r) => `<tr>${r.map((c, i) => cell('td', c, i)).join('')}</tr>`).join('')}</tbody></table>`
  );
}

export function renderMarkdown(source) {
  const blocks = [];
  let text = escapeHtml(source || '').replace(/\r\n?/g, '\n');

  // fenced code (an unterminated fence while streaming still renders as code)
  text = text.replace(/(^|\n)```([\w+#.-]*)[^\n]*\n([\s\S]*?)(?:\n```[ \t]*(?=\n|$)|$)/g, (_, pre, lang, code) => {
    blocks.push({ lang: lang.toLowerCase(), code });
    return `${pre}${B}B${blocks.length - 1}${B}`;
  });

  const lines = text.split('\n');
  let out = '';
  let para = [];
  let list = null; // {type, items}
  const flushPara = () => {
    // a link or a media file alone in its paragraph becomes a rich embed (player, preview card, picture)
    const only = para.length === 1 ? para[0].replace(/^&lt;(.+)&gt;$/, '$1').replace(/^`(.+)`$/, '$1') : '';
    if (/^https?:\/\/\S+$/.test(only)) out += `<div class="embed" data-url="${only}">${inline(only)}</div>`;
    else if (LOCAL.test(only) && MEDIA.test(only)) out += `<p>${media('', only)}</p>`;
    else if (para.length) out += `<p>${inline(para.join('\n')).replace(/\n/g, '<br>')}</p>`;
    para = [];
  };
  const flushList = () => {
    if (!list) return;
    out += `<${list.type}${list.start > 1 ? ` start="${list.start}"` : ''}>` + list.items.map((i) => i).join('') + `</${list.type}>`;
    list = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = raw.trim();
    if (!line) {
      flushPara();
      flushList();
      continue;
    }
    if (line.startsWith('$$') && (line.match(/\$\$/g) || []).length === 1) {
      // display math over several lines
      flushPara();
      flushList();
      const math = [line];
      while (++i < lines.length) {
        math.push(lines[i]);
        if (lines[i].includes('$$')) break;
      }
      out += `<div class="math-block">${math.join('\n')}</div>`;
      continue;
    }
    if (new RegExp(`^${B}B\\d+${B}$`).test(line)) {
      flushPara();
      flushList();
      out += line;
      continue;
    }
    let m;
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      flushPara();
      flushList();
      const level = Math.min(m[1].length, 4);
      out += `<h${level}>${inline(m[2].replace(/\s#+$/, ''))}</h${level}>`;
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
      flushPara();
      flushList();
      out += '<hr>';
      continue;
    }
    if (line.startsWith('|') && i + 1 < lines.length && /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$/.test(lines[i + 1].trim())) {
      flushPara();
      flushList();
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(lines[i++]);
      i--;
      out += table(rows);
      continue;
    }
    if (line.startsWith('&gt;')) {
      flushPara();
      flushList();
      const quote = [];
      while (i < lines.length && lines[i].trim().startsWith('&gt;')) quote.push(lines[i++].trim().replace(/^&gt;\s?/, ''));
      i--;
      out += `<blockquote>${inline(quote.join(' '))}</blockquote>`;
      continue;
    }
    if ((m = raw.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/))) {
      flushPara();
      const ordered = /\d/.test(m[2]);
      const type = ordered ? 'ol' : 'ul';
      if (!list || list.type !== type) {
        flushList();
        list = { type, items: [], start: ordered ? parseInt(m[2], 10) : 1 };
      }
      let body = m[3];
      const task = body.match(/^\[( |x|X)\]\s+(.*)$/);
      if (task) {
        list.items.push(`<li class="task"><input type="checkbox" disabled${task[1] !== ' ' ? ' checked' : ''}>${inline(task[2])}</li>`);
      } else {
        // indented continuation lines belong to the item
        while (i + 1 < lines.length && /^\s{2,}\S/.test(lines[i + 1]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i + 1])) {
          body += ' ' + lines[++i].trim();
        }
        const nested = m[1].length >= 2;
        list.items.push(`<li${nested ? ' style="margin-left:1.2em"' : ''}>${inline(body)}</li>`);
      }
      continue;
    }
    flushList();
    para.push(line);
  }
  flushPara();
  flushList();

  return out.replace(new RegExp(`${B}B(\\d+)${B}`, 'g'), (_, i) => {
    const b = blocks[+i];
    const live = ['html', 'htm', 'xhtml', 'svg'].includes(b.lang) || (b.lang === 'xml' && /^\s*&lt;svg/.test(b.code));
    return (
      `<pre><div class="code-head"><span>${b.lang || 'text'}</span><span class="code-acts">` +
      (live ? `<button class="act preview-code" type="button" title="Preview"><svg><use href="#i-eye"/></svg></button>` : '') +
      `<button class="act copy-code" type="button" title="Copy code"><svg><use href="#i-copy"/></svg></button></span></div>` +
      `<code class="${b.lang ? 'language-' + b.lang : ''}">${b.code}</code></pre>`
    );
  });
}

let hljsPromise = null;
export function highlight(root) {
  const blocks = root.querySelectorAll('pre code[class*="language-"]:not(.hljs)');
  if (!blocks.length) return;
  if (!hljsPromise) {
    hljsPromise = new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js';
      s.onload = () => resolve(window.hljs);
      s.onerror = () => resolve(null);
      document.head.appendChild(s);
    });
  }
  hljsPromise.then((hljs) => {
    if (!hljs) return;
    blocks.forEach((el) => {
      const lang = (el.className.match(/language-([\w+#-]+)/) || [])[1];
      if (lang && !hljs.getLanguage(lang)) return;
      try {
        hljs.highlightElement(el);
      } catch {
        /* ignore */
      }
    });
  });
}
