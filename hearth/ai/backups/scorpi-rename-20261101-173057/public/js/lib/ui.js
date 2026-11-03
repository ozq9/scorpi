// Toasts, popovers, sheets and the lightbox.

import { $, h, icon, isMobile } from './dom.js';
import { createMascot } from './mascot.js';
import { sfx } from './sfx.js';

export function toast(title, opts = {}) {
  const { body = '', type = 'info', action = null, timeout = 4200 } = typeof opts === 'string' ? { type: opts } : opts;
  if (type === 'error') sfx('error');
  const el = h(
    'div.toast.' + type,
    h('i.t-icon'),
    h('div', h('div.t-title', title), body ? h('div.t-body', body) : null),
    action ? h('button.t-act', { onclick: () => { action.run(); dismiss(); } }, action.label) : null,
  );
  const dismiss = () => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 340);
  };
  el.addEventListener('click', (e) => {
    if (!e.target.closest('.t-act')) dismiss();
  });
  $('#toasts').appendChild(el);
  if (timeout) setTimeout(dismiss, timeout);
  return dismiss;
}

let openPop = null;

export function closePop() {
  if (openPop) {
    openPop.remove();
    openPop = null;
  }
}

/** Show a popover menu next to anchor. items: [{label, sub, icon, onClick, checked, danger}] | 'sep' | {title} */
export function popover(anchor, items, { align = 'start', className = '', above = null } = {}) {
  closePop();
  const pop = h('div.pop' + (className ? '.' + className : ''), { role: 'menu' });
  for (const item of items) {
    if (!item) continue;
    if (item === 'sep') pop.appendChild(h('div.pop-sep'));
    else if (item.title) pop.appendChild(h('div.pop-title', item.title));
    else if (item.node) pop.appendChild(item.node);
    else
      pop.appendChild(
        h(
          'button.pop-item' + (item.danger ? '.danger' : '') + (item.checked ? '.sel' : ''),
          {
            type: 'button',
            onclick: (e) => {
              e.stopPropagation();
              if (!item.keepOpen) closePop();
              item.onClick && item.onClick(e);
            },
          },
          item.icon ? icon(item.icon) : null,
          h('span.pi-main', item.label, item.sub ? h('span.pi-sub', item.sub) : null),
          item.checked ? icon('check', 'pi-check') : null,
        ),
      );
  }
  $('#layer').appendChild(pop);
  sfx('open');
  const r = anchor.getBoundingClientRect();
  const pw = pop.offsetWidth;
  const ph = pop.offsetHeight;
  const spaceBelow = innerHeight - r.bottom;
  const goUp = above !== null ? above : spaceBelow < ph + 16 && r.top > spaceBelow;
  let left = align === 'end' ? r.right - pw : r.left;
  left = Math.max(10, Math.min(left, innerWidth - pw - 10));
  const top = goUp ? Math.max(10, r.top - ph - 8) : Math.min(innerHeight - ph - 10, r.bottom + 8);
  pop.style.left = left + 'px';
  pop.style.top = top + 'px';
  pop.style.setProperty('--origin', (goUp ? 'bottom ' : 'top ') + (align === 'end' ? 'right' : 'left'));
  openPop = pop;
  return pop;
}

document.addEventListener('pointerdown', (e) => {
  if (openPop && !openPop.contains(e.target) && !e.target.closest('[data-pop-anchor]')) closePop();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (openPop) closePop();
    else closeSheet();
  }
});

let sheetStack = [];

/** Open a modal sheet. content: Node or (close) => Node. */
export function sheet({ title, body, foot, wide = false, onClose } = {}) {
  const close = () => {
    backdrop.classList.add('closing');
    backdrop.style.transition = 'opacity .2s';
    backdrop.style.opacity = '0';
    setTimeout(() => backdrop.remove(), 200);
    sheetStack = sheetStack.filter((s) => s !== close);
    onClose && onClose();
  };
  const bodyNode = typeof body === 'function' ? body(close) : body;
  const footNode = typeof foot === 'function' ? foot(close) : foot;
  const panel = h(
    'div.sheet' + (wide ? '.wide' : ''),
    { role: 'dialog', 'aria-modal': 'true' },
    h('div.sheet-head', h('h2', title || ''), h('button.icon-btn', { type: 'button', title: 'Close', onclick: close }, icon('x'))),
    h('div.sheet-body', bodyNode),
    footNode ? h('div.sheet-foot', footNode) : null,
  );
  const backdrop = h('div.sheet-backdrop', { onclick: (e) => e.target === backdrop && close() }, panel);
  $('#layer').appendChild(backdrop);
  sheetStack.push(close);
  const first = panel.querySelector('input, textarea, select');
  if (first && !isMobile()) setTimeout(() => first.focus(), 60);
  return close;
}

export function closeSheet() {
  const last = sheetStack[sheetStack.length - 1];
  if (last) last();
}

export function confirmSheet(title, message, { confirm = 'Confirm', danger = false } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const close = sheet({
      title,
      body: h('p.muted', { style: { margin: '4px 0 6px' } }, message),
      foot: (closeIt) => [
        h('button.btn.quiet', { onclick: () => { done = true; resolve(false); closeIt(); } }, 'Cancel'),
        h('button.btn' + (danger ? '.danger' : '.primary'), { onclick: () => { done = true; resolve(true); closeIt(); } }, confirm),
      ],
      onClose: () => !done && resolve(false),
    });
    return close;
  });
}

export function promptSheet(title, { label = '', value = '', placeholder = '', confirm = 'Save', multiline = false } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const input = multiline
      ? h('textarea.input', { rows: 4, placeholder }, value)
      : h('input.input', { value, placeholder });
    sheet({
      title,
      body: h('div.field', label ? h('label', label) : null, input),
      foot: (close) => [
        h('button.btn.quiet', { onclick: () => close() }, 'Cancel'),
        h('button.btn.primary', { onclick: () => { done = true; resolve(input.value); close(); } }, confirm),
      ],
      onClose: () => !done && resolve(null),
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !multiline) {
        done = true;
        resolve(input.value);
        closeSheet();
      }
    });
  });
}

export function lightbox(src) {
  import('./viewer.js').then((v) => v.openViewer({ url: src, kind: 'image' }));
}

export function field(label, control, hint) {
  return h('div.field', h('label', label), control, hint ? h('div.hint', hint) : null);
}

export function switchEl(checked, onChange) {
  const input = h('input', { type: 'checkbox', checked: !!checked, onchange: (e) => onChange(e.target.checked) });
  return h('label.switch', input, h('span'));
}

export function seg(options, value, onChange, cls = '') {
  const wrap = h('div.seg' + (cls ? '.' + cls : ''));
  for (const [v, label] of options) {
    const b = h('button', { type: 'button', dataset: { v }, class: v === value ? 'on' : '' }, label);
    b.addEventListener('click', () => {
      wrap.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
      onChange(v);
    });
    wrap.appendChild(b);
  }
  return wrap;
}

export function selectEl(options, value, onChange) {
  const sel = h('select.select', { onchange: (e) => onChange(e.target.value) });
  for (const opt of options) {
    const [v, label] = Array.isArray(opt) ? opt : [opt, opt];
    sel.appendChild(h('option', { value: v }, label));
  }
  sel.value = value ?? '';
  return sel;
}

export function emptyState(title, text, extra) {
  const ember = createMascot({ size: 72, sleepAfter: 20000 });
  return h(
    'div.empty',
    h('div.e-mark', { onclick: () => ember.flash('happy', 1200).hop() }, ember.el),
    h('div.e-title', title),
    h('div', text),
    extra || null,
  );
}
