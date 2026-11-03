// Ember, Hearth's companion: a little ink drop that blinks, follows the pointer and shows what the agent is doing.

const live = new Set();
let uid = 0;
let pointer = null; // {x, y, t} of the last pointer move
let activity = performance.now();
let gazeFrame = 0;

// which eyes and mouth each mood wears
const MOODS = {
  idle: ['open', 'smile'],
  curious: ['open', 'o'],
  thinking: ['open', 'flat'],
  writing: ['open', 'smile'],
  working: ['open', 'flat'],
  happy: ['happy', 'grin'],
  sad: ['open', 'sad'],
  sleeping: ['shut', 'o'],
  listening: ['open', 'o'],
  talking: ['open', 'o'],
  alert: ['open', 'o'],
  shy: ['happy', 'smile'],
};

// where a mood looks, overriding the pointer (-1..1 on each axis)
const GAZE = {
  thinking: [0.55, -0.85],
  writing: [0.5, 0.75],
  working: [0, 0.4],
  sad: [0, 0.9],
  sleeping: [0, 0.2],
  shy: [0, 0],
};

const rand = (a, b) => a + Math.random() * (b - a);

function markup(id) {
  return `
  <defs>
    <radialGradient id="mg${id}" cx="40%" cy="36%" r="72%">
      <stop offset="0" class="m-stop-hi"/><stop offset=".6" class="m-stop"/><stop offset="1" class="m-stop-lo"/>
    </radialGradient>
  </defs>
  <ellipse class="m-shadow" cx="32" cy="57" rx="13" ry="2.2"/>
  <g class="m-hop"><g class="m-body">
    <path class="m-arm m-arm-l" d="M18.5 41.5c-3.2.4-5.6 2.2-6.6 4.8"/>
    <g class="m-arm-r">
      <path class="m-arm" d="M45.5 41.5c3.2.4 5.6 2.2 6.6 4.8"/>
      <g class="m-quill">
        <path class="m-feather" d="M52 47c1.4-6.8 4.6-12.6 9.6-16.4-.6 6.6-3.8 12.4-9.6 16.4z"/>
        <path class="m-vane" d="M52 47c2.4-4.4 5-8.6 8.4-12.4"/>
        <path class="m-nib" d="M52 47l-1.5 2.6 2.4-1.5z"/>
      </g>
    </g>
    <path class="m-skin" fill="url(#mg${id})" d="M32 9C35.6 16.4 49 24.5 49 38a17 17 0 0 1-34 0C15 24.5 28.4 16.4 32 9z"/>
    <path class="m-shine" d="M21.6 33.5c.6-3.9 2.6-7.4 5.6-10.4"/>
    <g class="m-face">
      <ellipse class="m-cheek" cx="21.2" cy="44" rx="2.7" ry="1.6"/>
      <ellipse class="m-cheek" cx="42.8" cy="44" rx="2.7" ry="1.6"/>
      <g class="m-eyes">
        <g class="m-eye"><ellipse class="m-iris" cx="25.6" cy="38.4" rx="2.6" ry="3.6"/><circle class="m-glint" cx="26.5" cy="37" r=".95"/></g>
        <g class="m-eye"><ellipse class="m-iris" cx="38.4" cy="38.4" rx="2.6" ry="3.6"/><circle class="m-glint" cx="39.3" cy="37" r=".95"/></g>
        <path class="m-lids m-lids-happy" d="M22.8 39.6q2.8-3.6 5.6 0M35.6 39.6q2.8-3.6 5.6 0"/>
        <path class="m-lids m-lids-shut" d="M22.8 38.6q2.8 2.6 5.6 0M35.6 38.6q2.8 2.6 5.6 0"/>
      </g>
      <path class="m-mouth m-smile" d="M29.6 44.2q2.4 2.3 4.8 0"/>
      <path class="m-mouth m-flat" d="M30.4 45h3.2"/>
      <path class="m-mouth m-sad" d="M29.8 46q2.2-1.9 4.4 0"/>
      <path class="m-mouth m-grin" d="M29 44h6q-.5 3.4-3 3.4T29 44z"/>
      <ellipse class="m-mouth m-o" cx="32" cy="45.2" rx="1.5" ry="1.8"/>
    </g>
    <g class="m-paws">
      <path class="m-paw-arm" d="M16.4 45.8c1.8-1.2 3.4-2.8 5-4.8M47.6 45.8c-1.8-1.2-3.4-2.8-5-4.8"/>
      <ellipse cx="25.4" cy="38.6" rx="5" ry="4.2" transform="rotate(-14 25.4 38.6)"/>
      <ellipse cx="38.6" cy="38.6" rx="5" ry="4.2" transform="rotate(14 38.6 38.6)"/>
      <path class="m-finger" d="M22.6 35.8l.5 1.6M25.2 35l.2 1.7M27.8 35.2l-.2 1.6M41.4 35.8l-.5 1.6M38.8 35l-.2 1.7M36.2 35.2l.2 1.6"/>
    </g>
  </g></g>
  <g class="m-fx">
    <g class="m-think"><circle cx="47" cy="17" r="1.4"/><circle cx="51.5" cy="11.5" r="2"/><circle cx="57" cy="5.5" r="2.7"/></g>
    <g class="m-zzz"><path d="M46 17h4l-4 4h4"/><path d="M52 9h3l-3 3h3"/></g>
    <g class="m-sparks">
      <path d="M9 18l.9 2.1 2.1.9-2.1.9L9 24l-.9-2.1L6 21l2.1-.9z"/>
      <path d="M55 24l.7 1.6 1.6.7-1.6.7-.7 1.6-.7-1.6-1.6-.7 1.6-.7z"/>
      <path d="M44 4l.6 1.3 1.3.6-1.3.6-.6 1.3-.6-1.3-1.3-.6 1.3-.6z"/>
    </g>
    <path class="m-drop" d="M48.5 23c1.5 2 2.3 3.4 2.3 4.5a2.3 2.3 0 0 1-4.6 0c0-1.1.8-2.5 2.3-4.5z"/>
    <g class="m-bang"><path d="M47 4.5v5"/><circle cx="47" cy="12.6" r=".6"/></g>
    <g class="m-waves"><path d="M53 34q2.4 4 0 8"/><path d="M56.5 31.5q3.6 6.5 0 13"/><path d="M11 34q-2.4 4 0 8"/><path d="M7.5 31.5q-3.6 6.5 0 13"/></g>
  </g>`;
}

/**
 * createMascot({size, mood, follow, sleepAfter, variant, className})
 * variant: '' (full body) | 'mini' (no effects) | 'face' (eyes and mouth only, drawn over something else)
 */
export function createMascot({ size = 48, mood = 'idle', follow = true, sleepAfter = 0, variant = '', className = '' } = {}) {
  const id = ++uid;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', variant === 'face' ? '14 24 36 36' : '0 0 64 64');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', ['mascot', variant, className].filter(Boolean).join(' '));
  svg.style.setProperty('--size', typeof size === 'number' ? size + 'px' : size);
  svg.innerHTML = markup(id);

  const m = {
    el: svg,
    base: mood,
    mood: '',
    follow,
    sleepAfter,
    dozing: false,
    seen: false,
    gaze: [0, 0],
    wander: [0, 0],
    nextWander: 0,
    nextBlink: performance.now() + rand(1200, 4000),
    focus: null,
    flashTimer: 0,

    /** Set the resting mood. */
    set(next) {
      m.base = next;
      m.dozing = false;
      if (!m.flashTimer) apply(m, next);
      return m;
    },
    /** Show a mood for a moment, then return to the resting one. */
    flash(next, ms = 1600) {
      clearTimeout(m.flashTimer);
      apply(m, next);
      m.flashTimer = setTimeout(() => {
        m.flashTimer = 0;
        apply(m, m.base);
      }, ms);
      return m;
    },
    hop() {
      replay(svg, 'hop');
      return m;
    },
    wave() {
      replay(svg, 'wave');
      return m;
    },
    /** Look at an element (or {x, y}) for a while. */
    lookAt(target, ms = 1400) {
      const r = target.getBoundingClientRect ? target.getBoundingClientRect() : null;
      const pt = r ? { x: r.left + r.width * 0.3, y: r.top + r.height / 2 } : target;
      m.focus = { ...pt, until: performance.now() + ms };
      updateGaze(m, performance.now());
      return m;
    },
    /** Mouth opening while talking, 0..1. */
    talk(level) {
      svg.style.setProperty('--talk', Math.max(0, Math.min(1, level)).toFixed(2));
    },
    destroy() {
      clearTimeout(m.flashTimer);
      live.delete(m);
    },
  };
  svg.addEventListener('animationend', (e) => {
    if (e.animationName === 'm-hop') svg.classList.remove('hop');
    if (e.animationName === 'm-wave') svg.classList.remove('wave');
  });
  apply(m, mood);
  live.add(m);
  ensureTicker();
  return m;
}

function apply(m, mood) {
  if (m.mood === mood) return;
  m.mood = mood;
  const [eyes, mouth] = MOODS[mood] || MOODS.idle;
  m.el.dataset.mood = mood;
  m.el.dataset.eyes = eyes;
  m.el.dataset.mouth = mouth;
  updateGaze(m, performance.now());
}

function replay(el, cls) {
  el.classList.remove(cls);
  // restart the animation even when it is already running
  void el.getBoundingClientRect();
  el.classList.add(cls);
}

function setGaze(m, gx, gy) {
  if (Math.abs(gx - m.gaze[0]) < 0.02 && Math.abs(gy - m.gaze[1]) < 0.02) return;
  m.gaze = [gx, gy];
  m.el.style.setProperty('--gx', gx.toFixed(2));
  m.el.style.setProperty('--gy', gy.toFixed(2));
}

function toward(m, pt) {
  const r = m.el.getBoundingClientRect();
  if (!r.width) return null;
  const dx = pt.x - (r.left + r.width / 2);
  const dy = pt.y - (r.top + r.height / 2);
  const d = Math.hypot(dx, dy) || 1;
  const k = Math.min(1, d / 260) / d;
  return [dx * k, dy * k];
}

function updateGaze(m, now) {
  let g = GAZE[m.mood] || null;
  if (!g && m.focus && now < m.focus.until) g = toward(m, m.focus);
  if (!g && m.follow && pointer && now - pointer.t < 4000) g = toward(m, pointer);
  if (!g) g = m.wander;
  setGaze(m, g[0], g[1]);
}

// ---------- shared ticker: blinking, wandering eyes, dozing off ----------

let ticker = 0;
function ensureTicker() {
  if (ticker) return;
  ticker = setInterval(tick, 200);
}

function tick() {
  if (document.hidden) return;
  const now = performance.now();
  for (const m of live) {
    if (!m.el.isConnected) {
      // forget mascots that were on screen and have been removed
      if (m.seen) live.delete(m);
      continue;
    }
    m.seen = true;
    if (m.sleepAfter && m.base === 'idle' && !m.flashTimer && !m.dozing && now - activity > m.sleepAfter) {
      m.dozing = true;
      apply(m, 'sleeping');
    }
    if (now > m.nextBlink) {
      m.nextBlink = now + rand(2400, 6200);
      if (m.el.dataset.eyes === 'open') {
        blink(m.el);
        if (Math.random() < 0.2) setTimeout(() => blink(m.el), 280);
      }
    }
    if (now > m.nextWander) {
      m.nextWander = now + rand(1600, 4200);
      m.wander = Math.random() < 0.3 ? [0, 0] : [rand(-0.7, 0.7), rand(-0.45, 0.5)];
      // a thinker's eyes dart about
      if (m.mood === 'thinking') setGaze(m, rand(0.2, 0.8), rand(-0.95, -0.55));
    }
    if (m.mood !== 'thinking') updateGaze(m, now);
  }
  if (!live.size) {
    clearInterval(ticker);
    ticker = 0;
  }
}

function blink(el) {
  el.classList.add('blink');
  setTimeout(() => el.classList.remove('blink'), 130);
}

function onActivity() {
  activity = performance.now();
  for (const m of live) {
    if (!m.dozing) continue;
    m.dozing = false;
    apply(m, m.base);
    m.hop();
  }
}

addEventListener(
  'pointermove',
  (e) => {
    pointer = { x: e.clientX, y: e.clientY, t: performance.now() };
    if (performance.now() - activity > 1000) onActivity();
    if (gazeFrame) return;
    gazeFrame = requestAnimationFrame(() => {
      gazeFrame = 0;
      const now = performance.now();
      for (const m of live) if (m.el.isConnected && m.mood !== 'thinking') updateGaze(m, now);
    });
  },
  { passive: true },
);
addEventListener('pointerdown', onActivity, { passive: true });
addEventListener('keydown', onActivity, { passive: true });
