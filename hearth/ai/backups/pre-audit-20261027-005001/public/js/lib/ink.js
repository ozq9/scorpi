// Streamed replies settle onto the page like fresh ink: each new word is drawn in with a pen stroke and dries
// from the accent colour to ink. The Markdown is re-rendered on every frame, so the writer remembers when each
// stretch of text arrived and resumes every word's animation where it was (negative animation-delay).

const DRY_MS = 900; // how long a word stays wet
const PEN_MS = 14; // pen speed within one burst of text, per character
const SPREAD_MAX = 650; // a long burst is still written within this time
const BLOCK_MS = 700;
const BLOCKS = 'li, pre, hr, blockquote, tr, img, h1, h2, h3, h4';
const CARET_HOSTS = new Set(['P', 'LI', 'H1', 'H2', 'H3', 'H4', 'BLOCKQUOTE', 'TD', 'TH', 'CODE']);

export const motionOK = () =>
  document.documentElement.dataset.motion !== 'reduced' && !matchMedia('(prefers-reduced-motion: reduce)').matches;

const spreadOf = (b) => Math.min((b.to - b.from) * PEN_MS, SPREAD_MAX);

export class InkWriter {
  constructor(el) {
    this.el = el;
    this.len = 0;
    this.bursts = []; // {from, to, t}: a stretch of rendered text and when it arrived
    this.blocks = new Map(); // "LI3" -> when that block first appeared
    this.animate = motionOK();
    this.sealed = false;
  }

  /** Render html into the element, inking whatever arrived recently. */
  render(html, { caret = false } = {}) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    const frag = tpl.content;
    if (this.animate) this.wet(frag, performance.now());
    if (caret) placeCaret(frag);
    this.el.classList.toggle('wet', this.animate);
    this.el.replaceChildren(frag);
  }

  /** Final render; once the ink is dry the wrappers are removed and done(el) runs. */
  seal(html, done) {
    if (this.sealed) return;
    this.sealed = true;
    this.render(html);
    const now = performance.now();
    let wait = 0;
    if (this.animate) {
      for (const b of this.bursts) wait = Math.max(wait, b.t + spreadOf(b) + DRY_MS - now);
      for (const t of this.blocks.values()) wait = Math.max(wait, t + BLOCK_MS - now);
    }
    setTimeout(() => {
      this.el.querySelectorAll('.ink').forEach((s) => s.replaceWith(...s.childNodes));
      this.el.querySelectorAll('.ink-in').forEach((b) => {
        b.classList.remove('ink-in');
        b.style.removeProperty('--ink-t');
      });
      this.el.normalize();
      this.el.classList.remove('wet');
      if (done) done(this.el);
    }, Math.max(0, wait) + 40);
  }

  wet(frag, now) {
    const walker = document.createTreeWalker(frag, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let total = 0;
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      nodes.push(n);
      total += n.data.length;
    }
    if (total > this.len) this.bursts.push({ from: this.len, to: total, t: now });
    else if (total < this.len) this.bursts.forEach((b) => (b.to = Math.min(b.to, total)));
    this.len = total;
    this.bursts = this.bursts.filter((b) => b.to > b.from && now - b.t < spreadOf(b) + DRY_MS);

    if (this.bursts.length) {
      const wetFrom = this.bursts[0].from;
      let off = 0;
      for (const node of nodes) {
        const end = off + node.data.length;
        if (end > wetFrom) this.wetText(node, off, now);
        off = end;
      }
    }

    const seen = {};
    frag.querySelectorAll(BLOCKS).forEach((el) => {
      const key = el.tagName + (seen[el.tagName] = (seen[el.tagName] || 0) + 1);
      if (!this.blocks.has(key)) this.blocks.set(key, now);
      const age = now - this.blocks.get(key);
      if (age < BLOCK_MS) {
        el.classList.add('ink-in');
        el.style.setProperty('--ink-t', -age + 'ms');
      }
    });
  }

  bornAt(off) {
    for (let i = this.bursts.length - 1; i >= 0; i--) {
      const b = this.bursts[i];
      if (off >= b.from && off < b.to) return b.t + ((off - b.from) / (b.to - b.from)) * spreadOf(b);
    }
    return -Infinity;
  }

  wetText(node, start, now) {
    const out = document.createDocumentFragment();
    let dry = '';
    let off = start;
    for (const part of node.data.split(/(\s+)/)) {
      if (!part) continue;
      const age = /^\s+$/.test(part) ? Infinity : now - this.bornAt(off);
      off += part.length;
      if (age >= DRY_MS) {
        dry += part;
        continue;
      }
      if (dry) out.appendChild(document.createTextNode(dry));
      dry = '';
      const span = document.createElement('span');
      span.className = 'ink';
      span.style.animationDelay = Math.round(-age) + 'ms';
      span.textContent = part;
      out.appendChild(span);
    }
    if (dry) out.appendChild(document.createTextNode(dry));
    node.replaceWith(out);
  }
}

// The nib rides at the end of the last line rather than on a line of its own.
function placeCaret(frag) {
  const caret = document.createElement('span');
  caret.className = 'caret';
  let host = frag.lastElementChild;
  while (host && host.lastElementChild && !CARET_HOSTS.has(host.tagName)) host = host.lastElementChild;
  if (host && host.tagName === 'PRE') host = host.querySelector('code') || host;
  if (host && (CARET_HOSTS.has(host.tagName) || host.tagName === 'PRE')) host.appendChild(caret);
  else frag.appendChild(caret);
}
