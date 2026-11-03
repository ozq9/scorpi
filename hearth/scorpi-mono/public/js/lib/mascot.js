// Scorpi, the app's mascot: a white pixel scorpion in line art (round head, happy eyes, a curled tail). It blinks, follows the pointer with its eyes and shows what the
// agent is doing (pincers snip, the tail sways and curls, legs scuttle, little effects pop up). The pixels come from scorpi-sprites.js, which
// scripts/make_scorpi.py writes together with the desktop's copy (scorpi/sprites.py), so both look alike.
// Frames step with CSS (scorpi.css); nothing moves smoothly.

import SPRITES from './scorpi-sprites.js';

const live = new Set();
let pointer = null; // {x, y, t} of the last pointer move
let activity = performance.now();
let gazeFrame = 0;

// where a mood looks, overriding the pointer (-1..1 on each axis)
const GAZE = {
  thinking: [0.8, -0.9],
  writing: [0.6, 0.8],
  working: [0, 0.6],
  sad: [0, 0.9],
  sleeping: [0, 0],
  shy: [0, 0],
};

const rand = (a, b) => a + Math.random() * (b - a);
const FX_H = SPRITES.fxH;

// run-length pixels -> one <path> per colour (cached: the same layers are drawn again and again)
const drawn = new WeakMap();
function pixels(runs, dx = 0, dy = 0) {
  const key = dx + ',' + dy;
  let byOffset = drawn.get(runs);
  if (!byOffset) drawn.set(runs, (byOffset = {}));
  if (byOffset[key]) return byOffset[key];
  const d = {};
  for (const [x, y, w, c] of runs) d[c] = (d[c] || '') + `M${x + dx} ${y + dy}h${w}v1h-${w}z`;
  return (byOffset[key] = Object.entries(d).map(([c, path]) => `<path class="px-${c}" d="${path}"/>`).join(''));
}

/** Frames that step in turn (scorpi.css .px-frames): frame i shows during the i-th slice of ms. */
function frames(list, ms, draw) {
  const n = list.length;
  return list
    .map((item, i) => {
      const style = n > 1 ? ` style="animation-name:px-n${n};animation-duration:${ms}ms;animation-delay:${-((n - i) / n) * ms}ms"` : '';
      return `<g${style}>${draw(item, i)}</g>`;
    })
    .join('');
}

function eyes(name) {
  const e = SPRITES.eyes[name];
  return SPRITES.eyeAt.map(([x, y]) => pixels(e.px, x + e.at[0], y + e.at[1])).join('');
}

function mouth(name) {
  const m = SPRITES.mouths[name];
  return pixels(m.px, SPRITES.mouthAt[0] + m.at[0], SPRITES.mouthAt[1] + m.at[1]);
}

// the parts every mood shares: eyes, mouths, legs while walking, the wave
const EYES = Object.keys(SPRITES.eyes).map((k) => `<g class="px-eye px-eye-${k}">${eyes(k)}</g>`).join('');
const MOUTHS = Object.keys(SPRITES.mouths).map((k) => `<g class="px-mouth px-mouth-${k}">${mouth(k)}</g>`).join('')
  + `<g class="px-mouth px-mouth-talk px-frames">${frames(SPRITES.talk, 420, (k) => mouth(k))}</g>`;
const WALK = `<g class="px-walk px-frames">${frames(SPRITES.walk, SPRITES.walkMs, (k) => pixels(SPRITES.legs[k]))}</g>`;
const WAVE = `<g class="px-wave px-frames">${frames(SPRITES.wave, SPRITES.waveMs, (f) => pixels(f.body))}</g>`;

function moodParts(name) {
  const m = SPRITES.moods[name] || SPRITES.moods.idle;
  const legs = Array.isArray(m.legs) ? frames(m.legs, m.ms, (k) => pixels(SPRITES.legs[k])) : pixels(SPRITES.legs[m.legs]);
  const fx = m.fx ? frames(SPRITES.fx[m.fx], Math.max(m.ms, 900) * (m.fx === 'think' ? 1.5 : 1), (f) => pixels(f)) : '';
  return {
    legs: `<g class="px-legset px-frames">${legs}</g>`,
    body: frames(m.frames, m.ms, (f) => pixels(f.body)),
    front: frames(m.frames, m.ms, (f) => pixels(f.front)),
    fx,
    bob: m.bob && m.bob.some(Boolean) ? m.bob.join('') : '',
  };
}

/**
 * createMascot({size, mood, follow, sleepAfter, variant, className})
 * variant: '' (full body) | 'mini' (no effects, cropped to Scorpi)
 * size: CSS pixels; it is rounded so every sprite pixel covers a whole number of screen pixels
 */
export function createMascot({ size = 48, mood = 'idle', follow = true, sleepAfter = 0, variant = '', className = '' } = {}) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', variant === 'mini' ? `0 ${FX_H} ${SPRITES.w} ${SPRITES.h}` : `0 0 ${SPRITES.w} ${FX_H + SPRITES.h}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMax meet');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('shape-rendering', 'crispEdges');
  svg.setAttribute('class', ['mascot', variant, className].filter(Boolean).join(' '));
  svg.style.setProperty('--size', typeof size === 'number' ? crisp(size) + 'px' : size);
  svg.style.setProperty('--aspect', String((variant === 'mini' ? SPRITES.h : FX_H + SPRITES.h) / SPRITES.w));
  svg.innerHTML = `<g class="px-fx px-frames"></g>
    <g transform="translate(0 ${FX_H})"><g class="px-hop">
      <g class="px-legs"><g class="px-legset"></g>${WALK}</g>
      <g class="px-bob">
        <g class="px-body px-frames"></g>${WAVE}
        <g class="px-face"><g class="px-eyes">${EYES}</g><g class="px-mouths">${MOUTHS}</g></g>
        <g class="px-front px-frames"></g>
      </g>
    </g></g>`;
  const part = (sel) => svg.querySelector(sel);
  const slots = { fx: part('.px-fx'), legs: part('.px-legset'), body: part('.px-body'), front: part('.px-front') };

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
    talkTimer: 0,
    slots,

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
    /** Mouth opening while talking, 0..1: picks one of the mouth frames until the voice goes quiet. */
    talk(level) {
      const v = Math.max(0, Math.min(1, level));
      svg.style.setProperty('--talk', v.toFixed(2));
      svg.dataset.talk = v < 0.08 ? 'o1' : v < 0.4 ? 'o2' : 'o3';
      clearTimeout(m.talkTimer);
      m.talkTimer = setTimeout(() => delete svg.dataset.talk, 350);
    },
    destroy() {
      clearTimeout(m.flashTimer);
      clearTimeout(m.talkTimer);
      live.delete(m);
    },
  };
  svg.addEventListener('animationend', (e) => {
    if (e.animationName === 'px-hop') svg.classList.remove('hop');
    if (e.animationName === 'px-wave') svg.classList.remove('wave');
  });
  apply(m, mood);
  live.add(m);
  ensureTicker();
  return m;
}

/** A size whose sprite pixels land on whole screen pixels. */
function crisp(size) {
  const dpr = window.devicePixelRatio || 1;
  return (Math.max(1, Math.round((size / SPRITES.w) * dpr)) / dpr) * SPRITES.w;
}

function apply(m, mood) {
  if (m.mood === mood) return;
  m.mood = mood;
  const spec = SPRITES.moods[mood] || SPRITES.moods.idle;
  const parts = moodParts(mood);
  m.slots.legs.innerHTML = parts.legs;
  m.slots.body.innerHTML = parts.body;
  m.slots.front.innerHTML = parts.front;
  m.slots.fx.innerHTML = parts.fx;
  const ds = m.el.dataset;
  ds.mood = mood;
  ds.eyes = spec.eyes;
  ds.mouth = spec.mouth;
  if (parts.bob) {
    ds.bob = parts.bob;
    m.el.style.setProperty('--bob-ms', spec.ms + 'ms');
  } else delete ds.bob;
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
  const s = m.el.style;
  s.setProperty('--gx', gx.toFixed(2));
  s.setProperty('--gy', gy.toFixed(2));
  // the eyes move a whole pixel or not at all
  s.setProperty('--egx', String(Math.round(gx * 1.2) || 0));
  s.setProperty('--egy', String(Math.round(gy * 1.2) || 0));
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
      if (['open', 'wide'].includes(m.el.dataset.eyes)) {
        blink(m.el);
        if (Math.random() < 0.2) setTimeout(() => blink(m.el), 280);
      }
    }
    if (now > m.nextWander) {
      m.nextWander = now + rand(1600, 4200);
      m.wander = Math.random() < 0.3 ? [0, 0] : [rand(-0.8, 0.8), rand(-0.6, 0.6)];
    }
    updateGaze(m, now);
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
      for (const m of live) if (m.el.isConnected) updateGaze(m, now);
    });
  },
  { passive: true },
);
addEventListener('pointerdown', onActivity, { passive: true });
addEventListener('keydown', onActivity, { passive: true });
