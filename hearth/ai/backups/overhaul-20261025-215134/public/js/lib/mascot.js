// Scorpi, Hearth's companion: a little white pixel scorpion that blinks, follows the pointer and shows what the agent
// is doing (its claws snip, its tail sways and curls).

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

// Scorpi on a 16 x 16 grid of 4-unit pixels: # body, d shade, t tail, s stinger, c/a left claw, C/A right claw, l legs
const SPRITE = [
  '................',
  '................',
  '........ttt.....',
  '.......s...t....',
  '............t...',
  '............t...',
  '...........t....',
  'c.c.......t..C.C',
  'ccc..######..CCC',
  '.aaa########AAA.',
  '....########....',
  '....########....',
  '.....dddddd.....',
  '....l.l..l.l....',
];

function pixels(keys, cls) {
  let out = '';
  SPRITE.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) if (keys.includes(row[x])) out += `<rect x="${x * 4}" y="${y * 4}" width="4.06" height="4.06"/>`;
  });
  return `<g class="${cls}">${out}</g>`;
}

const px = (list) => list.map(([x, y, w = 2, hh = 2]) => `<rect x="${x}" y="${y}" width="${w}" height="${hh}"/>`).join('');

function markup() {
  return `
  <rect class="m-shadow" x="18" y="56.5" width="28" height="2.5" rx="1.2"/>
  <g class="m-hop"><g class="m-body" shape-rendering="crispEdges">
    <g class="m-tail">${pixels('t', 's-tail')}${pixels('s', 's-sting')}</g>
    <g class="m-arm-l">${pixels('ca', 's-claw')}</g>
    <g class="m-arm-r">${pixels('CA', 's-claw')}</g>
    <g class="m-legs">${pixels('l', 's-legs')}</g>
    <g class="m-skin">${pixels('#', 's-px')}${pixels('d', 's-shade')}</g>
    <g class="m-face">
      <g class="m-cheek">${px([[18, 44, 4, 2], [42, 44, 4, 2]])}</g>
      <g class="m-eyes">
        <g class="m-eye"><rect class="m-iris" x="24" y="36" width="4" height="7"/><rect class="m-glint" x="24.6" y="36.6" width="1.6" height="1.6"/></g>
        <g class="m-eye"><rect class="m-iris" x="36" y="36" width="4" height="7"/><rect class="m-glint" x="36.6" y="36.6" width="1.6" height="1.6"/></g>
        <g class="m-lids m-lids-happy">${px([[23, 40], [25, 38], [27, 40], [35, 40], [37, 38], [39, 40]])}</g>
        <g class="m-lids m-lids-shut">${px([[23, 40, 6, 2], [35, 40, 6, 2]])}</g>
      </g>
      <g class="m-mouth m-smile">${px([[28, 45], [30, 47, 4, 2], [34, 45]])}</g>
      <g class="m-mouth m-flat">${px([[29, 46, 6, 2]])}</g>
      <g class="m-mouth m-sad">${px([[28, 48], [30, 46, 4, 2], [34, 48]])}</g>
      <g class="m-mouth m-grin">${px([[28, 45, 8, 2], [30, 47, 4, 2]])}</g>
      <g class="m-mouth m-o">${px([[30, 45, 4, 4]])}</g>
    </g>
  </g></g>
  <g class="m-fx">
    <g class="m-think"><rect x="50" y="16" width="3" height="3"/><rect x="54" y="10" width="4" height="4"/><rect x="58" y="3" width="5" height="5"/></g>
    <g class="m-zzz"><path d="M48 17h4l-4 4h4"/><path d="M54 9h3l-3 3h3"/></g>
    <g class="m-sparks">
      <path d="M8 18h2v2h2v2h-2v2h-2v-2h-2v-2h2z"/>
      <path d="M56 26h2v2h2v2h-2v2h-2v-2h-2v-2h2z"/>
      <path d="M44 2h2v2h2v2h-2v2h-2v-2h-2v-2h2z"/>
    </g>
    <path class="m-drop" d="M50 22h2v2h2v4h-2v2h-2v-2h-2v-4h2z"/>
    <g class="m-bang"><rect x="47" y="3" width="3" height="7"/><rect x="47" y="12" width="3" height="3"/></g>
    <g class="m-waves"><path d="M60 34v8"/><path d="M63 31v14"/><path d="M4 34v8"/><path d="M1 31v14"/></g>
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
  svg.setAttribute('shape-rendering', 'crispEdges');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', ['mascot', variant, className].filter(Boolean).join(' '));
  svg.style.setProperty('--size', typeof size === 'number' ? size + 'px' : size);
  svg.innerHTML = markup();

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
