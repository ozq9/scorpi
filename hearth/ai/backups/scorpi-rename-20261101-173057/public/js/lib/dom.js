// Small DOM helpers shared by every view.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** h('div.card#id', {onclick, attrs}, ...children) */
export function h(tag, props, ...children) {
  const m = tag.match(/^([a-z0-9-]+)?((?:[.#][\w-]+)*)$/i);
  const el = document.createElement((m && m[1]) || 'div');
  if (m && m[2]) {
    for (const part of m[2].match(/[.#][\w-]+/g)) {
      if (part[0] === '.') el.classList.add(part.slice(1));
      else el.id = part.slice(1);
    }
  }
  if (props && (typeof props !== 'object' || props instanceof Node || Array.isArray(props))) {
    children.unshift(props);
    props = null;
  }
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'class') el.className += (el.className ? ' ' : '') + value;
    else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
    else if (key === 'html') el.innerHTML = value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key in el && typeof value !== 'string') el[key] = value;
    else el.setAttribute(key, value === true ? '' : value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function icon(name, cls = '') {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  if (cls) svg.setAttribute('class', cls);
  const use = document.createElementNS(ns, 'use');
  use.setAttribute('href', '#i-' + name);
  svg.appendChild(use);
  return svg;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1073741824).toFixed(2) + ' GB';
}

export function fmtTime(ts, opts = {}) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', ...opts });
}

export function fmtDate(ts, opts = { day: 'numeric', month: 'short' }) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleDateString([], opts);
}

export function fmtRelative(ts) {
  if (!ts) return '';
  const diff = Date.now() / 1000 - ts;
  const ahead = diff < 0;
  const s = Math.abs(diff);
  let out;
  if (s < 50) out = 'now';
  else if (s < 3600) out = Math.round(s / 60) + ' min';
  else if (s < 86400) out = Math.round(s / 3600) + ' h';
  else if (s < 86400 * 6) out = Math.round(s / 86400) + ' d';
  else return fmtDate(ts);
  if (out === 'now') return out;
  return ahead ? 'in ' + out : out + ' ago';
}

export function dayKey(ts) {
  const d = new Date(ts * 1000);
  return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
}

export function dayLabel(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const tomorrow = new Date(Date.now() + 86400000);
  const yesterday = new Date(Date.now() - 86400000);
  if (d.toDateString() === today.toDateString()) return 'Today';
  if (d.toDateString() === tomorrow.toDateString()) return 'Tomorrow';
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'long' });
}

export function debounce(fn, ms = 200) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const ta = h('textarea', { style: { position: 'fixed', opacity: '0' } }, text);
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand('copy');
  } finally {
    ta.remove();
  }
  return Promise.resolve();
}

export function extOf(name) {
  const m = String(name || '').match(/\.([a-z0-9]+)$/i);
  return m ? m[1].toLowerCase() : '';
}

export function baseName(path) {
  return String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path;
}

export const isMobile = () => matchMedia('(max-width: 900px)').matches;

export function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve();
    const s = document.createElement('script');
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error('Could not load ' + src));
    document.head.appendChild(s);
  });
}

export function loadStyle(href) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  document.head.appendChild(h('link', { rel: 'stylesheet', href }));
}

export function wordsIn(el, text) {
  // Split a sentence into staggered words for the reveal animation. *word* becomes accent italic.
  clear(el);
  text.split(/(\s+)/).forEach((part, i) => {
    if (/^\s+$/.test(part)) return el.appendChild(document.createTextNode(' '));
    const em = part.match(/^\*(.+)\*([.,!?]*)$/);
    const span = h('span.w', { style: { animationDelay: 80 + i * 55 + 'ms' } });
    if (em) {
      span.appendChild(h('em', em[1]));
      if (em[2]) span.appendChild(document.createTextNode(em[2]));
    } else span.textContent = part;
    el.appendChild(span);
  });
}
