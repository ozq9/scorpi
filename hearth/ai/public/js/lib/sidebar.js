// The sidebar can be resized from its inner edge and moved to any side of the window: drag it by its header and drop
// it near the left, right, top or bottom edge, where it snaps into place. On top or bottom it lays out sideways.
// Phones keep the usual drawer.

import { h } from './dom.js';
import { prefs, setPref } from '../store.js';

const EDGES = ['left', 'right', 'top', 'bottom'];
const W = [200, 480]; // width limits beside the stage
const H = [170, 0.6]; // height limits above or below it (px, share of the window)
const desktop = () => innerWidth > 900;

export function applySidebarLayout() {
  const root = document.documentElement;
  const dock = EDGES.includes(prefs.sbDock) ? prefs.sbDock : 'left';
  root.dataset.sbDock = dock;
  root.style.setProperty('--sb', Math.round(Math.min(W[1], Math.max(W[0], prefs.sbWidth || 264))) + 'px');
  root.style.setProperty('--sbh', Math.round(Math.max(H[0], prefs.sbHeight || 290)) + 'px');
  return dock;
}

export function initSidebarLayout({ sidebar, head, onDock }) {
  applySidebarLayout();
  const dock = () => document.documentElement.dataset.sbDock;
  const vertical = () => dock() === 'left' || dock() === 'right';

  // ---------- resize from the inner edge ----------
  const grip = h('div.sb-resize', { title: 'Drag to resize' });
  sidebar.appendChild(grip);
  grip.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || !desktop()) return;
    e.preventDefault();
    e.stopPropagation();
    grip.setPointerCapture(e.pointerId);
    const r = sidebar.getBoundingClientRect();
    const d = dock();
    sidebar.classList.add('sb-resizing');
    document.body.classList.add(vertical() ? 'resizing-x' : 'resizing-y');
    const move = (ev) => {
      if (d === 'left') setPref('sbWidth', ev.clientX - r.left);
      else if (d === 'right') setPref('sbWidth', r.right - ev.clientX);
      else if (d === 'top') setPref('sbHeight', Math.min(innerHeight * H[1], ev.clientY - r.top));
      else setPref('sbHeight', Math.min(innerHeight * H[1], r.bottom - ev.clientY));
      applySidebarLayout();
    };
    const end = () => {
      grip.removeEventListener('pointermove', move);
      grip.removeEventListener('pointerup', end);
      grip.removeEventListener('pointercancel', end);
      sidebar.classList.remove('sb-resizing');
      document.body.classList.remove('resizing-x', 'resizing-y');
      setPref('sbWidth', parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sb'), 10));
      setPref('sbHeight', parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sbh'), 10));
    };
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', end);
    grip.addEventListener('pointercancel', end);
  });
  // double-click the grip: back to the standard size
  grip.addEventListener('dblclick', () => {
    setPref(vertical() ? 'sbWidth' : 'sbHeight', vertical() ? 264 : 290);
    applySidebarLayout();
  });

  // ---------- move it to another edge ----------
  head.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || !desktop() || e.target.closest('button, input')) return;
    const start = { x: e.clientX, y: e.clientY };
    let zones = null;
    let target = dock();
    head.setPointerCapture(e.pointerId);
    const nearest = (x, y) => {
      const dist = { left: x, right: innerWidth - x, top: y, bottom: innerHeight - y };
      return EDGES.reduce((a, b) => (dist[b] < dist[a] ? b : a));
    };
    const move = (ev) => {
      const dx = ev.clientX - start.x;
      const dy = ev.clientY - start.y;
      if (!zones) {
        if (Math.hypot(dx, dy) < 8) return;
        zones = h('div.sb-zones', EDGES.map((edge) => h('i', { dataset: { edge } })));
        document.body.appendChild(zones);
        sidebar.classList.add('sb-moving');
      }
      sidebar.style.translate = `${dx}px ${dy}px`;
      target = nearest(ev.clientX, ev.clientY);
      zones.querySelectorAll('i').forEach((z) => z.classList.toggle('on', z.dataset.edge === target));
    };
    const end = () => {
      head.removeEventListener('pointermove', move);
      head.removeEventListener('pointerup', end);
      head.removeEventListener('pointercancel', end);
      if (!zones) return;
      // it was a drag, not a click on the brand link
      const swallow = (ce) => {
        ce.preventDefault();
        ce.stopPropagation();
      };
      addEventListener('click', swallow, { capture: true, once: true });
      setTimeout(() => removeEventListener('click', swallow, { capture: true }), 50);
      zones.remove();
      sidebar.classList.remove('sb-moving');
      sidebar.style.translate = '';
      if (target !== dock()) {
        setPref('sbDock', target);
        applySidebarLayout();
        if (onDock) onDock(target);
      }
      sidebar.classList.add('sb-landed');
      setTimeout(() => sidebar.classList.remove('sb-landed'), 600);
    };
    head.addEventListener('pointermove', move);
    head.addEventListener('pointerup', end);
    head.addEventListener('pointercancel', end);
  });
}
