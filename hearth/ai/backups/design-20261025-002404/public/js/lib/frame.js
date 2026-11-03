// Frameless desktop window (desktop.py): Hearth's own title bar, window buttons and edge resizing.
// Only active inside the pywebview window; browsers and phones never see the title bar.

import { $ } from './dom.js';

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
      $('#tbMax').title = on ? 'Restore' : 'Maximize';
    };
    const toggleMax = () => api.toggle_maximize().then(setMax);
    root.classList.add('framed');
    setMax(info.maximized);

    $('#tbMin').addEventListener('click', () => api.minimize());
    $('#tbMax').addEventListener('click', toggleMax);
    $('#tbClose').addEventListener('click', () => api.close());

    // Drag starts once the mouse moves with the button held, so plain clicks and double-clicks stay clicks.
    const drag = $('#tbDrag');
    let down = null;
    drag.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
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
