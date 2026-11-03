// Frameless desktop window (desktop.py): full screen over the Windows taskbar by default. The window buttons sit at
// the right end of Scorpi's own taskbar (or in a small floating pill on narrow windows); in a window, the taskbar's
// empty space drags it and the edges resize it. Only active inside the pywebview window.

import { $, $$ } from './dom.js';

export function initWindowFrame() {
  let started = false;
  const start = async () => {
    const api = window.pywebview && window.pywebview.api;
    if (started || !api || !api.frame) return;
    started = true;
    const info = await api.frame();
    if (!info || !info.framed) return;
    const root = document.documentElement;
    const setMax = (on) => {
      root.classList.toggle('maximized', !!on);
      $$('[data-wc="max"]').forEach((b) => (b.title = on ? 'Leave full screen (F11)' : 'Full screen (F11)'));
    };
    const toggleMax = () => api.toggle_maximize().then(setMax);
    window.frameEvent = (d) => setMax(d && d.maximized);
    root.classList.add('framed');
    setMax(info.maximized);

    $$('[data-wc="min"]').forEach((b) => b.addEventListener('click', () => api.minimize()));
    $$('[data-wc="max"]').forEach((b) => b.addEventListener('click', toggleMax));
    $$('[data-wc="close"]').forEach((b) => b.addEventListener('click', () => api.close()));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'F11') {
        e.preventDefault();
        toggleMax();
      }
    });

    // Drag starts once the mouse moves with the button held, so plain clicks and double-clicks stay clicks.
    // Grips: the pill's handle, and the taskbar wherever it has nothing to click.
    let down = null;
    const grip = (e) => e.target.closest('#tbDrag') || (e.target.closest('#taskbar') && !e.target.closest('button, a, input, label, .tk-tab, .tk-panel'));
    document.addEventListener('mousedown', (e) => {
      if (e.button !== 0 || !grip(e)) return;
      e.preventDefault();
      if (e.detail === 2) {
        down = null;
        toggleMax();
        return;
      }
      down = { x: e.screenX, y: e.screenY };
    });
    window.addEventListener('mousemove', (e) => {
      if (!down) return;
      if (!(e.buttons & 1)) {
        down = null;
        return;
      }
      if (Math.abs(e.screenX - down.x) + Math.abs(e.screenY - down.y) < 4) return;
      down = null;
      api.drag().then(setMax);
    });
    window.addEventListener('mouseup', () => {
      down = null;
    });

    $('#rzEdges').addEventListener('mousedown', (e) => {
      const edge = e.target.dataset && e.target.dataset.edge;
      if (e.button !== 0 || !edge) return;
      e.preventDefault();
      api.resize(edge);
    });
  };
  start();
  window.addEventListener('pywebviewready', start);
}
