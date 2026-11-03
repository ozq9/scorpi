// Scorpi on the prompt box: the one little pixel scorpion in the app. It sits on the composer's top edge and wanders
// along it, keeps to the edge when the composer moves or narrows, and can be clicked, petted, dragged along the edge
// and right-clicked. In the desktop app the right-click menu sends it out onto the desktop (its own overlay window),
// and a right-click on that one brings it back here.

import { h } from './dom.js';
import { createMascot } from './mascot.js';
import { popover, toast } from './ui.js';
import { prefs, setPref } from '../store.js';

const HEART = '<svg viewBox="0 0 7 6" shape-rendering="crispEdges"><path d="M1 0h2v1h1V0h2v1h1v2H6v1H5v1H4v1H3V5H2V4H1V3H0V1h1z"/></svg>';
const SPEED = 80; // px per second when walking
const busyMoods = ['thinking', 'working', 'writing', 'alert'];

const deskApi = () => {
  const api = window.pywebview && window.pywebview.api;
  return api && api.scorpi ? api : null;
};

export function createScorpi({ perch, composer, input, onTalk }) {
  const m = createMascot({ size: 48, sleepAfter: 150000 });
  const body = h('div.sc-body', m.el);
  perch.replaceChildren(body);
  perch.title = 'Scorpi';

  let frac = typeof prefs.scorpiX === 'number' ? prefs.scorpiX : 0.06; // where on the edge, 0 left … 1 right
  let px = 0;
  let walkTimer = 0;
  let wanderTimer = 0;
  let dragging = false;
  let clicks = [];
  let lastWave = 0;

  const range = () => {
    const w = composer.clientWidth || 600;
    return [14, Math.max(14, w - 70)];
  };

  function face(dir) {
    body.classList.toggle('flip', dir < 0);
  }

  /** Put Scorpi at a spot on the edge; walk: scuttle there instead of jumping. */
  function moveTo(target, { walk = false } = {}) {
    const [lo, hi] = range();
    const next = Math.max(lo, Math.min(hi, target));
    const dx = next - px;
    clearTimeout(walkTimer);
    if (walk && Math.abs(dx) > 4) {
      const secs = Math.min(4, Math.max(0.35, Math.abs(dx) / SPEED));
      face(dx);
      perch.style.transition = `left ${secs}s linear`;
      perch.classList.add('walking');
      walkTimer = setTimeout(() => perch.classList.remove('walking'), secs * 1000);
    } else perch.style.transition = 'none';
    px = next;
    perch.style.left = px + 'px';
  }

  const fit = () => {
    const [lo, hi] = range();
    moveTo(lo + frac * (hi - lo));
  };

  function remember() {
    const [lo, hi] = range();
    frac = hi > lo ? (px - lo) / (hi - lo) : 0;
    setPref('scorpiX', Math.round(frac * 1000) / 1000);
  }

  // ---------- life on the edge ----------

  function wander() {
    clearTimeout(wanderTimer);
    wanderTimer = setTimeout(() => {
      const idle = m.base === 'idle' && !dragging && !document.hidden && document.activeElement !== input;
      if (idle && Math.random() < 0.65) {
        const [lo, hi] = range();
        const span = Math.min(260, hi - lo);
        moveTo(px + (Math.random() - 0.5) * 2 * span * 0.6, { walk: true });
        remember();
      } else if (idle && Math.random() < 0.4) face(Math.random() < 0.5 ? -1 : 1);
      wander();
    }, 7000 + Math.random() * 11000);
  }

  function hearts(n = 3) {
    for (let i = 0; i < n; i++) {
      const heart = h('i.sc-heart', { html: HEART, style: `--dx:${(Math.random() - 0.5) * 46}px;--d:${i * 120}ms` });
      heart.addEventListener('animationend', () => heart.remove());
      perch.appendChild(heart);
    }
  }

  function poke() {
    const now = Date.now();
    clicks = clicks.filter((t) => now - t < 1800);
    clicks.push(now);
    if (clicks.length >= 5) {
      // too many pokes: Scorpi gets dizzy
      clicks = [];
      perch.classList.remove('dizzy');
      void perch.offsetWidth;
      perch.classList.add('dizzy');
      m.flash('sad', 1600);
      return;
    }
    if (busyMoods.includes(m.base)) return m.hop();
    m.flash('happy', 1400).hop();
    hearts(clicks.length > 2 ? 4 : 2);
  }

  // petting: stroke the pointer back and forth over Scorpi
  let petDir = 0;
  let petTurns = 0;
  let petLastX = null;
  perch.addEventListener('pointermove', (e) => {
    if (dragging || e.buttons) return;
    if (petLastX !== null) {
      const dir = Math.sign(e.clientX - petLastX);
      if (dir && dir !== petDir) {
        petDir = dir;
        if (++petTurns >= 4) {
          petTurns = 0;
          m.flash('shy', 1800);
          hearts(3);
        }
      }
    }
    petLastX = e.clientX;
  });
  perch.addEventListener('pointerenter', () => {
    petTurns = 0;
    petLastX = null;
    if (Date.now() - lastWave > 5000 && m.base === 'idle') {
      lastWave = Date.now();
      m.wave();
    }
  });

  // drag along the edge
  perch.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const start = e.clientX;
    const from = px;
    let moved = false;
    perch.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const dx = ev.clientX - start;
      if (!moved && Math.abs(dx) < 5) return;
      if (!moved) {
        moved = dragging = true;
        perch.classList.add('lifted');
        m.set('curious');
      }
      face(dx);
      moveTo(from + dx);
    };
    const end = () => {
      perch.removeEventListener('pointermove', move);
      perch.removeEventListener('pointerup', end);
      perch.removeEventListener('pointercancel', end);
      if (!moved) return poke();
      dragging = false;
      perch.classList.remove('lifted');
      m.set('idle');
      m.hop();
      remember();
    };
    perch.addEventListener('pointermove', move);
    perch.addEventListener('pointerup', end);
    perch.addEventListener('pointercancel', end);
  });

  // ---------- right-click menu ----------

  function setAway(away) {
    document.documentElement.classList.toggle('scorpi-away', !!away);
    if (!away) {
      perch.classList.remove('leaving');
      perch.classList.add('arriving');
      setTimeout(() => perch.classList.remove('arriving'), 900);
      m.flash('happy', 1400);
    }
  }

  async function detach() {
    const api = deskApi();
    if (!api) return;
    const r = m.el.getBoundingClientRect();
    perch.classList.add('leaving');
    await new Promise((res) => setTimeout(res, 380));
    try {
      const st = await api.scorpi('detach', window.screenX + r.left + r.width / 2, window.screenY + r.top + r.height / 2);
      setAway(st && st.detached);
    } catch (err) {
      perch.classList.remove('leaving');
      toast('Scorpi could not leave', { type: 'warn', body: err.message });
    }
  }

  function dance() {
    perch.classList.remove('dancing');
    void perch.offsetWidth;
    perch.classList.add('dancing');
    m.flash('happy', 2400);
    hearts(5);
    setTimeout(() => perch.classList.remove('dancing'), 2400);
  }

  perch.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const desk = !!deskApi();
    popover(perch, [
      { title: 'Scorpi' },
      { label: 'Talk to Scorpi', sub: 'Switch the prompt box to voice', icon: 'voice', onClick: () => onTalk && onTalk() },
      { label: 'Dance', icon: 'spark', onClick: dance },
      desk ? { label: 'Live on the desktop', sub: 'Floats over every app; right-click it to come back', icon: 'external', onClick: detach } : null,
      'sep',
      { label: 'Hide Scorpi', sub: 'Bring it back in Settings → Clean chat', icon: 'eye', onClick: () => setPref('hideEmber', true) },
    ], { above: true });
  });

  // the desktop app says when Scorpi went out or came back (tray menu, right-click on the desktop Scorpi)
  window.scorpiEvent = (ev) => setAway(ev && ev.detached);
  const checkDesk = () => {
    const api = deskApi();
    if (api) api.scorpi('state').then((st) => setAway(st && st.detached)).catch(() => {});
  };
  checkDesk();
  window.addEventListener('pywebviewready', checkDesk);

  new ResizeObserver(fit).observe(composer);
  fit();
  wander();
  return m;
}
