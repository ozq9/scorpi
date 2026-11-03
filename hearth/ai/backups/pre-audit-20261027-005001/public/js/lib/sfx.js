// Interface sounds. Kenney's UI audio (CC0, /sounds) covers taps, toggles and menus; the two tones Kenney has no sound
// for (a finished reply, an error) are synthesized. Samples are decoded once, so a sound starts the instant it is asked for.

import { prefs } from '../store.js';

const FILES = {
  tap: ['click4', 0.55],
  select: ['click2', 0.6],
  nav: ['click5', 0.5],
  send: ['mouseclick1', 0.7],
  open: ['rollover2', 0.45],
  close: ['rollover3', 0.35],
  on: ['switch13', 0.6],
  off: ['switch14', 0.55],
};
const GAP = 55; // ms: one gesture makes one sound (a send click would otherwise also play the generic tap)

let ctx = null;
let out = null;
let loading = null;
let last = 0;
const buffers = {};

const enabled = () => prefs.uiSounds === true && (prefs.soundVolume ?? 60) > 0;
const volume = () => Math.max(0, Math.min(1, (prefs.soundVolume ?? 60) / 100));

function context() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    out = ctx.createGain();
    out.connect(ctx.destination);
  }
  if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  return ctx;
}

function load() {
  if (loading || !ctx) return loading;
  loading = Promise.all(
    Object.entries(FILES).map(async ([name, [file]]) => {
      try {
        const res = await fetch(`/sounds/${file}.wav`);
        buffers[name] = await ctx.decodeAudioData(await res.arrayBuffer());
      } catch {
        /* that sound stays silent */
      }
    }),
  );
  return loading;
}

// soft bell-like notes: a sine with a quiet octave above it and a fast, round decay
const TONES = {
  done: { notes: [[659, 0], [988, 0.11]], len: 0.55, gain: 0.2 },
  error: { notes: [[294, 0], [233, 0.1]], len: 0.28, gain: 0.17, type: 'triangle' },
};

function tone(spec, vol) {
  const now = ctx.currentTime + 0.005;
  for (const [freq, at] of spec.notes) {
    for (const [mult, share] of [[1, 1], [2, 0.18]]) {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = spec.type || 'sine';
      osc.frequency.value = freq * mult;
      const peak = spec.gain * share * vol;
      g.gain.setValueAtTime(0, now + at);
      g.gain.linearRampToValueAtTime(peak, now + at + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, now + at + spec.len);
      osc.connect(g).connect(out);
      osc.start(now + at);
      osc.stop(now + at + spec.len + 0.05);
    }
  }
}

/** Play a sound by name: tap, select, nav, send, open, close, on, off, done, error. */
export function sfx(name) {
  if (!enabled()) return;
  const now = performance.now();
  if (now - last < GAP) return;
  if (!context()) return;
  load();
  const vol = volume();
  if (TONES[name]) {
    last = now;
    return tone(TONES[name], vol);
  }
  const buf = buffers[name];
  if (!buf) return;
  last = now;
  const src = ctx.createBufferSource();
  const g = ctx.createGain();
  src.buffer = buf;
  // a little pitch variation keeps repeated taps from sounding like a machine
  src.playbackRate.value = 0.97 + Math.random() * 0.06;
  g.gain.value = FILES[name][1] * vol;
  src.connect(g).connect(out);
  src.start();
}

// ---------- automatic sounds for buttons, menus and switches ----------

const CLICKABLE = 'button, a[href], [role="button"], [role="tab"], summary, .pop-item';

export function initSfx() {
  // the first touch or key unlocks audio and loads the samples, so the first real sound is not late
  const unlock = () => {
    if (!enabled() || !context()) return;
    load();
  };
  addEventListener('pointerdown', unlock, { once: true, passive: true, capture: true });
  addEventListener('keydown', unlock, { once: true, capture: true });

  document.addEventListener('change', (e) => {
    const el = e.target;
    if (el.matches && el.matches('.switch input[type="checkbox"]')) sfx(el.checked ? 'on' : 'off');
  }, true);

  document.addEventListener('click', (e) => {
    const el = e.target.closest && e.target.closest(CLICKABLE);
    if (!el || el.disabled || el.closest('.switch, [data-nosound]')) return;
    if (el.matches('.pop-item')) return sfx('select');
    if (el.closest('nav, .sidebar, .taskbar, [role="tablist"]')) return sfx('nav');
    sfx('tap');
  });
}
