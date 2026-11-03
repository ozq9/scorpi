// Scorpi, Scorp's companion: a small, round, porcelain-white scorpion drawn as smooth vectors. It blinks, follows the
// pointer with its eyes and shows what the agent is doing (claws snip, the tail sways and curls, little effects pop up).

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

// Shapes are written in their own little coordinate system and placed by mapping every point (no nested
// transforms, so CSS can rotate the parts around absolute pivots).
function place(d, fn) {
  return d.replace(/(-?\d*\.?\d+)[ ,](-?\d*\.?\d+)/g, (_, a, b) => {
    const [x, y] = fn(+a, +b);
    return x.toFixed(2) + ' ' + y.toFixed(2);
  });
}

// a claw in local units: palm at the origin, pointing along +x, the finger hinged on top
const PALM = 'M-5.8 .6C-5.8 -3.4 -2.6 -5.2 .6 -4.4L1.8 -1.5C3.4 -1.7 5.1 -2.6 6.8 -3.7C7.6 -1.2 6.8 2.6 3.8 4.3C.6 6 -5.8 5.1 -5.8 .6Z';
const FINGER = 'M-.6 -2.7C0 -6 3.6 -8 7 -6.9C6.2 -5.3 4.4 -4 2.1 -3.3Z';
const HINGE = [0.2, -3.2];

function claw(side, p) {
  const k = 1.18;
  const a = (-62 * Math.PI) / 180;
  const [cx, cy] = [10.8, 34.2];
  const mirror = (x) => (side === 'l' ? x : 64 - x);
  const fn = (x, y) => [mirror(cx + (x * Math.cos(a) - y * Math.sin(a)) * k), cy + (x * Math.sin(a) + y * Math.cos(a)) * k];
  const [hx, hy] = fn(...HINGE);
  const [sx, sy] = fn(-3.4, -2.6);
  return `
    <g class="m-arm-${side}" style="transform-origin:${mirror(20.5)}px 43px">
      <path class="m-limb" d="M${mirror(20.8)} 44Q${mirror(13.6)} 44.6 ${mirror(10.4)} 39.4"/>
      <path class="m-part" fill="url(#${p}-claw)" d="${place(PALM, fn)}"/>
      <path class="m-part m-finger" fill="url(#${p}-claw)" d="${place(FINGER, fn)}" style="transform-origin:${hx.toFixed(2)}px ${hy.toFixed(2)}px"/>
      <ellipse class="m-shine" cx="${sx.toFixed(2)}" cy="${sy.toFixed(2)}" rx="1.5" ry=".9" transform="rotate(${side === 'l' ? -40 : 40} ${sx.toFixed(2)} ${sy.toFixed(2)})"/>
    </g>`;
}

const CLOUD_BACK = [[17, 56, 6.2], [29, 52.5, 7.6], [41, 53.5, 7.2], [51, 57, 5.6]];
const CLOUD_FRONT = [[10.5, 61.5, 5.2], [20, 60.5, 7], [32, 61.5, 7.8], [44, 60.8, 7], [54.5, 61.8, 5]];
const puffs = (list) => list.map(([x, y, r]) => `<circle cx="${x}" cy="${y}" r="${r}"/>`).join('');

function markup(p, cloud) {
  const back = cloud ? `<g class="m-cloud m-cloud-back" fill="url(#${p}-cloud-back)">${puffs(CLOUD_BACK)}</g>` : '';
  const front = cloud
    ? `<g class="m-cloud m-cloud-front" fill="url(#${p}-cloud)">${puffs(CLOUD_FRONT)}<ellipse cx="32" cy="64" rx="23" ry="4.6"/>
        <g class="m-cloud-shine">${CLOUD_FRONT.slice(1, 4).map(([x, y, r]) => `<ellipse cx="${x - r * 0.25}" cy="${y - r * 0.55}" rx="${r * 0.38}" ry="${r * 0.16}" transform="rotate(-12 ${x - r * 0.25} ${y - r * 0.55})"/>`).join('')}</g></g>`
    : '';
  return `
  <defs>
    <radialGradient id="${p}-body" cx="38%" cy="28%" r="78%">
      <stop offset="0" class="m-c-hi"/><stop offset=".55" class="m-c-mid"/><stop offset="1" class="m-c-lo"/>
    </radialGradient>
    <radialGradient id="${p}-claw" cx="30%" cy="30%" r="90%">
      <stop offset="0" class="m-c-hi"/><stop offset=".6" class="m-c-mid"/><stop offset="1" class="m-c-lo"/>
    </radialGradient>
    <radialGradient id="${p}-tail" gradientUnits="userSpaceOnUse" cx="44" cy="14" r="30">
      <stop offset="0" class="m-c-hi"/><stop offset=".6" class="m-c-mid"/><stop offset="1" class="m-c-lo"/>
    </radialGradient>
    <radialGradient id="${p}-sting" cx="35%" cy="30%" r="80%">
      <stop offset="0" class="m-c-sting-hi"/><stop offset="1" class="m-c-sting"/>
    </radialGradient>
    <radialGradient id="${p}-eye" cx="40%" cy="30%" r="75%">
      <stop offset="0" class="m-c-eye-hi"/><stop offset="1" class="m-c-eye"/>
    </radialGradient>
    <linearGradient id="${p}-cloud" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="m-c-cloud-hi"/><stop offset="1" class="m-c-cloud-lo"/></linearGradient>
    <linearGradient id="${p}-cloud-back" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="m-c-cloud-back-hi"/><stop offset="1" class="m-c-cloud-back-lo"/></linearGradient>
  </defs>
  <g class="m-float">
  ${cloud ? '' : '<ellipse class="m-shadow" cx="32" cy="58.6" rx="15" ry="2.2"/>'}
  ${back}
  <g class="m-hop"><g class="m-body">
    <g class="m-tail">
      <ellipse class="m-part" fill="url(#${p}-tail)" cx="43.8" cy="37" rx="5.6" ry="5.2" transform="rotate(-55 43.8 37)"/>
      <ellipse class="m-part" fill="url(#${p}-tail)" cx="48.6" cy="29.8" rx="5" ry="4.6" transform="rotate(-75 48.6 29.8)"/>
      <ellipse class="m-part" fill="url(#${p}-tail)" cx="49.6" cy="22.3" rx="4.5" ry="4.2" transform="rotate(-100 49.6 22.3)"/>
      <ellipse class="m-part" fill="url(#${p}-tail)" cx="46.6" cy="15.8" rx="4.1" ry="3.8" transform="rotate(-135 46.6 15.8)"/>
      <ellipse class="m-part" fill="url(#${p}-tail)" cx="41.6" cy="12.6" rx="3.7" ry="3.4" transform="rotate(-165 41.6 12.6)"/>
      <path class="m-barb" d="M34.4 14.4C32.8 15.2 31.7 16.8 31.5 19C33.2 17.8 34.8 16.9 36.2 16Z"/>
      <ellipse class="m-sting" fill="url(#${p}-sting)" cx="36.6" cy="13.4" rx="3.3" ry="2.8" transform="rotate(-24 36.6 13.4)"/>
      <ellipse class="m-shine" cx="35.6" cy="12.2" rx="1.1" ry=".6" transform="rotate(-24 35.6 12.2)"/>
    </g>
    <g class="m-legs">
      <path d="M20.8 51.2l-2.9 2.6M24.2 53.8l-1.9 3M28.2 55.2l-.9 2.8M43.2 51.2l2.9 2.6M39.8 53.8l1.9 3M35.8 55.2l.9 2.8"/>
    </g>
    ${claw('l', p)}
    ${claw('r', p)}
    <g class="m-skin">
      <path class="m-part" fill="url(#${p}-body)" d="M32 31.2c9.4 0 16.4 6.1 16.4 13.6 0 7-7.1 11.6-16.4 11.6S15.6 51.8 15.6 44.8C15.6 37.3 22.6 31.2 32 31.2z"/>
      <path class="m-seg" d="M19.6 50.2c3.4 2.4 7.6 3.5 12.4 3.5s9-1.1 12.4-3.5"/>
      <path class="m-seg" d="M22.4 53.4c2.8 1.3 6 1.9 9.6 1.9s6.8-.6 9.6-1.9"/>
      <ellipse class="m-shine" cx="25.2" cy="35.6" rx="4.2" ry="1.9" transform="rotate(-18 25.2 35.6)"/>
    </g>
    <g class="m-face">
      <g class="m-cheek"><ellipse cx="22.6" cy="47.2" rx="2.7" ry="1.5"/><ellipse cx="41.4" cy="47.2" rx="2.7" ry="1.5"/></g>
      <g class="m-eyes">
        <g class="m-eye"><ellipse class="m-iris" fill="url(#${p}-eye)" cx="26.4" cy="42.2" rx="3.05" ry="3.7"/><circle class="m-glint" cx="27.5" cy="40.6" r="1.25"/><circle class="m-glint m-glint-2" cx="25.5" cy="43.9" r=".55"/></g>
        <g class="m-eye"><ellipse class="m-iris" fill="url(#${p}-eye)" cx="37.6" cy="42.2" rx="3.05" ry="3.7"/><circle class="m-glint" cx="38.7" cy="40.6" r="1.25"/><circle class="m-glint m-glint-2" cx="36.7" cy="43.9" r=".55"/></g>
        <g class="m-lids m-lids-happy"><path d="M23.4 43c1-2.2 5-2.2 6 0M34.6 43c1-2.2 5-2.2 6 0"/></g>
        <g class="m-lids m-lids-shut"><path d="M23.6 42.4c1.2 1.6 4.4 1.6 5.6 0M34.8 42.4c1.2 1.6 4.4 1.6 5.6 0"/></g>
      </g>
      <path class="m-mouth m-smile" d="M30.2 47.2c.9 1.1 2.7 1.1 3.6 0"/>
      <path class="m-mouth m-flat" d="M30.4 47.8h3.2"/>
      <path class="m-mouth m-sad" d="M30.2 48.6c.9-1.1 2.7-1.1 3.6 0"/>
      <g class="m-mouth m-grin"><path class="m-mouth-in" d="M29.4 46.7h5.2c0 1.9-1.2 3-2.6 3s-2.6-1.1-2.6-3z"/><path class="m-tongue" d="M30.6 48.9c.7-.6 2.1-.6 2.8 0-.4.5-.9.8-1.4.8s-1-.3-1.4-.8z"/></g>
      <ellipse class="m-mouth m-o" cx="32" cy="47.9" rx="1.25" ry="1.4"/>
    </g>
  </g></g>
  ${front}
  </g>
  <g class="m-fx">
    <g class="m-think"><circle cx="52.5" cy="17" r="1.4"/><circle cx="56.2" cy="11.6" r="2"/><circle cx="60" cy="5" r="2.8"/></g>
    <g class="m-zzz"><path d="M50 18h4l-4 4h4"/><path d="M56 9.5h3l-3 3h3"/></g>
    <g class="m-sparks">
      <path d="M8 16.5l.9 2.3 2.3.9-2.3.9-.9 2.3-.9-2.3-2.3-.9 2.3-.9z"/>
      <path d="M57 27l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>
      <path d="M45 1.6l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>
    </g>
    <path class="m-drop" d="M51.6 23.4c1.2 1.8 2.2 3 2.2 4.2a2.2 2.2 0 01-4.4 0c0-1.2 1-2.4 2.2-4.2z"/>
    <g class="m-bang"><rect x="51.4" y="6" width="3.2" height="8" rx="1.6"/><circle cx="53" cy="17.4" r="1.7"/></g>
    <g class="m-waves"><path d="M58 37c1.6 2 1.6 5 0 7"/><path d="M61 34c2.8 3.6 2.8 9.4 0 13"/><path d="M6 37c-1.6 2-1.6 5 0 7"/><path d="M3 34c-2.8 3.6-2.8 9.4 0 13"/></g>
  </g>`;
}

/**
 * createMascot({size, mood, follow, sleepAfter, variant, className, cloud})
 * variant: '' (full body) | 'mini' (no effects) | 'face' (eyes and mouth only, drawn over something else)
 * cloud: Scorpi floats on a little cloud
 */
export function createMascot({ size = 48, mood = 'idle', follow = true, sleepAfter = 0, variant = '', className = '', cloud = false } = {}) {
  const id = ++uid;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', variant === 'face' ? '18 34 28 20' : cloud ? '-4 -1 72 72' : '0 0 64 64');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', ['mascot', variant, className, cloud ? 'on-cloud' : ''].filter(Boolean).join(' '));
  svg.style.setProperty('--size', typeof size === 'number' ? size + 'px' : size);
  svg.innerHTML = markup('sc' + id, cloud);

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
  ticker = setInterval(tick, 250);
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
  setTimeout(() => el.classList.remove('blink'), 140);
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
