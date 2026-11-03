// Voice mode for the composer (the Write / Voice toggle): hands-free turns in the open conversation or project.
// You can talk over Hearth at any time: while it speaks, your voice stops the speech; while it works, what you say
// reaches it right away (a question, a change of plan, or "stop").
// Hold to talk (the default): like a voice message, hold Space (or press and hold the voice bar), speak, let go and
// it is sent. The microphone is only open while held. Hands-free (Settings → Voice) listens all the time instead.
// While voice is on, what you say shows where the text box was, beside a waveform of whoever is talking, and a
// small state chip sits in the toolbar. The composer keeps its size, so nothing jumps when you switch.

import { h } from '../lib/dom.js';
import { toast } from '../lib/ui.js';
import { bus, prefs, state } from '../store.js';
import {
  audioContext, browserRecognizer, holdMic, inputAnalyser, mobile, onSpeaking, outputAnalyser, record, releaseMic,
  stopSpeaking, sttRoute, sttUnavailableReason, transcribe, watchForSpeech,
} from '../audio.js';

// [chip label, hint shown where the text box was while nothing is heard]
const STATES = {
  off: ['Voice off', ''],
  listening: ['Listening', 'Go ahead, I’m listening…'],
  working: ['Working', 'Working on it · talk any time to add something'],
  understanding: ['Transcribing', 'Getting that down…'],
  speaking: ['Speaking', 'Talk any time to interrupt'],
  ready: ['Hold to talk', mobile ? 'Press and hold here, speak, then let go' : 'Hold Space (or press here), speak, then let go'],
  recording: ['Recording', 'Talking… let go to send'],
};
// what the hint says instead while holding to talk
const HOLD_HINTS = {
  working: mobile ? 'Working on it · press and hold to add something' : 'Working on it · hold Space to add something',
  speaking: mobile ? 'Press and hold to interrupt and talk' : 'Hold Space to interrupt and talk',
};

const holdMode = () => prefs.voiceHold !== false;

let on = false;
let phase = 'off'; // off | listening | understanding | speaking
let rec = null; // the recognizer or recording that is listening now
let watcher = null; // barge-in detection while Hearth speaks
let speaking = false;
let hooks = {}; // { onUtterance(text), onBargeIn(), onStop(), busy() }
let els = null;
let raf = 0;
let generation = 0; // bumps on every start/stop, so late callbacks of an old listen are ignored
let transcribing = 0; // utterances being transcribed on the host while listening goes on
let inOrder = Promise.resolve(); // transcripts reach the conversation in the order they were said
let settleTimer = 0;
let pressed = false; // the talk key or the voice bar is held down
let talk = null; // what records while held: { stop(), cancel() }

export const voiceOn = () => on;

/** What replaces the text box while voice is on: what you say, and a waveform of who is talking. */
export function voiceBar() {
  if (els) return els.bar;
  els = {};
  els.said = h('span.vb-said');
  els.hint = h('span.vb-hint');
  els.heard = h('div.vb-heard', els.said, els.hint);
  els.wave = h('canvas.vb-wave', { 'aria-hidden': 'true', onclick: () => !holdMode() && interrupt() });
  els.bar = h('div.voice-bar', els.heard, els.wave);
  els.label = h('span.vb-label');
  els.chip = h('button.vb-chip', { type: 'button', onclick: () => !holdMode() && interrupt() }, h('span.vb-dot'), els.label);
  // press and hold the bar or the chip to talk (mouse, finger or pen)
  for (const el of [els.bar, els.chip]) {
    el.addEventListener('pointerdown', (e) => {
      if (!on || !holdMode() || e.button > 0) return;
      e.preventDefault();
      try {
        el.setPointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      pressTalk();
    });
    el.addEventListener('pointerup', releaseTalk);
    el.addEventListener('pointercancel', releaseTalk);
    el.addEventListener('contextmenu', (e) => on && holdMode() && e.preventDefault());
  }
  render();
  return els.bar;
}

/** The state chip shown in the composer's toolbar while voice is on. */
export function voiceStatus() {
  voiceBar();
  return els.chip;
}

// what the person sees: "listening" reads as working or transcribing when that is what is going on
function shown() {
  if (phase === 'ready') return transcribing ? 'understanding' : hooks.busy && hooks.busy() ? 'working' : 'ready';
  if (phase !== 'listening') return phase;
  if (transcribing) return 'understanding';
  return hooks.busy && hooks.busy() ? 'working' : 'listening';
}

function render() {
  if (!els) return;
  const s = shown();
  const hold = on && holdMode();
  els.bar.dataset.phase = els.chip.dataset.phase = s;
  els.bar.classList.toggle('hold', hold);
  els.chip.classList.toggle('hold', hold);
  els.label.textContent = STATES[s][0];
  els.hint.textContent = (hold && HOLD_HINTS[s]) || STATES[s][1];
  els.chip.title = hold ? STATES.ready[1] : s === 'speaking' ? 'Interrupt' : STATES[s][1];
  els.wave.title = !hold && s === 'speaking' ? 'Interrupt' : '';
}

function setPhase(p) {
  phase = p;
  render();
}

/** Show what was heard; faint while the recognizer is still guessing. Empty text lets the hint show again. */
export function showHeard(text, faint = false) {
  if (!els) return;
  clearTimeout(settleTimer);
  if (text) {
    els.said.textContent = text;
    els.said.classList.toggle('faint', faint);
  }
  els.heard.classList.toggle('has', !!text);
}

// what was said stays a moment after it went out, then gives way to the hint
function settle() {
  clearTimeout(settleTimer);
  settleTimer = setTimeout(() => els && els.heard.classList.remove('has'), 1800);
}

/** Turn voice on. Returns false (and says why) when this device has no way to hear. */
export function startVoice(handlers) {
  hooks = handlers || {};
  if (!sttRoute()) {
    toast('Voice unavailable', { type: 'warn', body: sttUnavailableReason(), timeout: 8000 });
    return false;
  }
  audioContext();
  on = true;
  showHeard('');
  loop();
  begin();
  return true;
}

// start in the chosen way of talking (again, when it is switched in Settings while voice is on)
function begin() {
  generation++;
  transcribing = 0;
  pressed = false;
  cancelTalk();
  cancelListening();
  stopWatch();
  if (holdMode()) {
    releaseMic(); // nothing listens until a key or the bar is held
    return setPhase(speaking ? 'speaking' : 'ready');
  }
  // desktops keep the microphone open for the whole session (phones share it with their own recognizer)
  if (!mobile) holdMic().catch(() => {});
  if (speaking) {
    setPhase('speaking');
    bargeWatch();
  } else listen();
}

bus.on('prefs', ({ key }) => key === 'voiceHold' && on && begin());

export function stopVoice() {
  on = false;
  generation++;
  transcribing = 0;
  pressed = false;
  cancelTalk();
  cancelListening();
  stopWatch();
  stopSpeaking();
  releaseMic();
  cancelAnimationFrame(raf);
  raf = 0;
  setPhase('off');
  showHeard('');
}

// voice can't go on (no microphone, no recognizer): stop and let the composer go back to Write
function giveUp() {
  stopVoice();
  if (hooks.onStop) hooks.onStop();
}

/** The run's state changed (started / finished): refresh the label. */
export function voiceRefresh() {
  if (on) render();
}

function interrupt() {
  if (!on || !speaking) return;
  hooks.onBargeIn && hooks.onBargeIn();
  stopSpeaking();
}

// the desktop window went to the tray: never keep the microphone open out of sight
window.addEventListener('hearth:hidden', () => on && stopVoice());

onSpeaking((now) => {
  speaking = now;
  if (!on) return;
  if (holdMode()) {
    if (phase === 'ready' && now) setPhase('speaking');
    else if (phase === 'speaking' && !now) setPhase('ready');
    return;
  }
  if (now) {
    cancelListening();
    setPhase('speaking');
    bargeWatch();
  } else {
    stopWatch();
    if (phase === 'speaking') listen();
  }
});

function cancelListening() {
  if (rec) {
    try {
      rec.cancel();
    } catch {
      /* ignore */
    }
  }
  rec = null;
}

async function bargeWatch() {
  stopWatch();
  const gen = generation;
  const w = await watchForSpeech({
    onSpeech: () => {
      if (gen !== generation || !on || !speaking) return;
      watcher = null;
      hooks.onBargeIn && hooks.onBargeIn();
      stopSpeaking();
      showHeard('');
      listen();
    },
  });
  if (gen !== generation || !speaking || !on) return w.stop();
  watcher = w;
}

function stopWatch() {
  if (watcher) watcher.stop();
  watcher = null;
}

function listen() {
  if (!on || speaking || rec || holdMode()) return;
  const gen = generation;
  setPhase('listening');
  const route = sttRoute();
  if (route === 'device') {
    const sr = browserRecognizer(
      (text, final) => {
        if (gen !== generation) return;
        if (text) showHeard(text, !final);
        if (final) {
          rec = null;
          if (phase === 'understanding') setPhase('listening');
          deliver(text);
          // the recognizer has ended: start the next one right away, so nothing said next is lost
          setTimeout(() => gen === generation && on && !speaking && listen(), 60);
        }
      },
      {
        lang: state.settings.stt_language,
        onRescue: () => gen === generation && setPhase('understanding'),
        onFail: () => {
          rec = null;
          if (gen === generation && on) listen(); // sttRoute() now picks the host, or explains why it can't
        },
      },
    );
    if (sr) {
      rec = { cancel: () => sr.abort(), device: true };
      sr.start();
      return;
    }
  }
  if (route === 'device' || route === 'host') return listenHost(gen);
  giveUp();
  toast('Voice unavailable', { type: 'warn', body: sttUnavailableReason(), timeout: 8000 });
}

async function listenHost(gen) {
  let session;
  try {
    session = await record({ vad: true, silenceMs: 950 });
  } catch (err) {
    giveUp();
    toast('Microphone unavailable', { type: 'error', body: err.message, timeout: 8000 });
    return;
  }
  if (gen !== generation || speaking) return session.cancel();
  rec = session;
  const result = await session.done;
  if (rec === session) rec = null;
  if (gen !== generation || !on) return;
  if (result && result.speechMs >= 250) {
    // transcribe while already listening again: what is said meanwhile is not lost
    transcribing++;
    render();
    const job = transcribe(result.blob).then(
      (t) => t.trim(),
      (err) => {
        toast('Could not understand that', { type: 'error', body: err.message });
        return '';
      },
    );
    inOrder = inOrder.then(async () => {
      const text = await job;
      if (gen !== generation) return;
      transcribing--;
      render();
      if (text) showHeard(text);
      deliver(text);
    });
  }
  if (!speaking) listen();
}

// ---------- hold to talk ----------

window.addEventListener('keydown', (e) => {
  if (!on || !holdMode() || e.code !== 'Space' || e.ctrlKey || e.altKey || e.metaKey || e.shiftKey) return;
  if (e.target instanceof Element && e.target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
  e.preventDefault();
  if (!e.repeat) pressTalk();
});
window.addEventListener('keyup', (e) => {
  if (e.code !== 'Space' || !pressed) return;
  e.preventDefault();
  releaseTalk();
});
// a key let go in another window never arrives here
window.addEventListener('blur', () => releaseTalk());

/** Held down: stop Hearth if it is talking, and record until let go. */
export function pressTalk() {
  if (!on || !holdMode() || pressed) return;
  pressed = true;
  if (speaking) {
    hooks.onBargeIn && hooks.onBargeIn();
    stopSpeaking();
  }
  if (talk) return setPhase('recording'); // the last take is still finishing: keep it, it is sent first
  setPhase('recording');
  showHeard('');
  if (!mobile) holdMic().catch(() => {}); // the waveform follows the voice, and recording starts without a gap
  capture(generation);
}

/** Let go: what was said is sent. */
export function releaseTalk() {
  if (!pressed) return;
  pressed = false;
  if (!talk) return;
  setPhase('understanding');
  talk.stop();
}

function cancelTalk() {
  if (talk) {
    try {
      talk.cancel();
    } catch {
      /* ignore */
    }
  }
  talk = null;
}

// one take is over: wait for the next press (the microphone closes)
function afterTalk() {
  if (pressed || !on || !holdMode()) return;
  releaseMic();
  setPhase(speaking ? 'speaking' : 'ready');
}

function capture(gen) {
  const route = sttRoute();
  if (route === 'device') {
    const sr = browserRecognizer(
      (text, final) => {
        if (gen !== generation) return;
        if (text) showHeard(text, !final);
        if (!final) return;
        if (talk && talk.sr === sr) talk = null;
        deliver(text);
        if (!text) showHeard('');
        afterTalk();
      },
      {
        lang: state.settings.stt_language,
        continuous: true,
        onRescue: () => gen === generation && setPhase('understanding'),
        onFail: () => {
          if (gen !== generation || !(talk && talk.sr === sr)) return;
          talk = null;
          if (pressed) capture(gen); // sttRoute() now picks the host for the rest of this take
          else afterTalk();
        },
      },
    );
    if (sr) {
      talk = { sr, stop: () => sr.stop(), cancel: () => sr.abort() };
      sr.start();
      return;
    }
  }
  if (route === 'device' || route === 'host') return captureHost(gen);
  giveUp();
  toast('Voice unavailable', { type: 'warn', body: sttUnavailableReason(), timeout: 8000 });
}

async function captureHost(gen) {
  let session;
  try {
    session = await record({ maxMs: 300000 });
  } catch (err) {
    pressed = false;
    afterTalk();
    toast('Microphone unavailable', { type: 'error', body: err.message, timeout: 8000 });
    return;
  }
  if (gen !== generation) return session.cancel();
  const take = { stop: () => session.stop(), cancel: () => session.cancel() };
  talk = take;
  if (!pressed) session.stop(); // let go while the microphone was still opening
  const result = await session.done;
  if (talk === take) talk = null;
  if (gen !== generation || !on) return;
  if (!result || result.speechMs < 200) {
    showHeard('');
    return afterTalk();
  }
  transcribing++;
  afterTalk();
  render();
  const job = transcribe(result.blob).then(
    (t) => t.trim(),
    (err) => {
      toast('Could not understand that', { type: 'error', body: err.message });
      return '';
    },
  );
  inOrder = inOrder.then(async () => {
    const text = await job;
    if (gen !== generation) return;
    transcribing--;
    render();
    if (text) showHeard(text);
    deliver(text);
  });
}

function deliver(text) {
  text = String(text || '').trim();
  if (!on || !text) return;
  settle();
  hooks.onUtterance && hooks.onUtterance(text);
}

// ---------- the waveform ----------
// Bars mirrored from the middle: the low end of the voice band in the centre, its top at the edges. They follow
// the microphone while listening and Hearth's voice while it speaks, with a fast rise and a slow fall so they glide.

const BARS = 23;
const bars = new Float32Array(BARS);
let freq = null;
let color = '';
let colorFor = '';

function loop() {
  if (raf) return;
  const frame = (now) => {
    raf = requestAnimationFrame(frame);
    if (els) draw(now);
  };
  raf = requestAnimationFrame(frame);
}

function draw(now) {
  const c = els.wave;
  const w = c.clientWidth;
  const ht = c.clientHeight;
  if (!w || !ht) return;
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(ht * dpr)) {
    c.width = Math.round(w * dpr);
    c.height = Math.round(ht * dpr);
  }
  const s = shown();
  if (colorFor !== s) {
    colorFor = s;
    color = getComputedStyle(c).color;
  }
  const src = phase === 'speaking' ? outputAnalyser : inputAnalyser;
  let live = false;
  if (src) {
    if (!freq || freq.length !== src.frequencyBinCount) freq = new Uint8Array(src.frequencyBinCount);
    src.getByteFrequencyData(freq);
    live = true;
  }
  const hz = src ? src.context.sampleRate / 2 / freq.length : 1;
  const mid = (BARS - 1) / 2;
  let sum = 0;
  for (let i = 0; i < BARS; i++) {
    const k = Math.abs(i - mid) / mid;
    let t = 0;
    if (live) {
      const bin = Math.min(freq.length - 2, Math.round((110 * Math.pow(4200 / 110, k)) / hz));
      const v = (freq[bin] + freq[bin + 1]) / 510;
      t = Math.max(0, (v - 0.32) / 0.68) ** 1.25 * (1 - k * 0.35);
    }
    // a little life when nobody talks: breathing while listening, a travelling ripple while working
    if (s === 'listening' || s === 'recording') t = Math.max(t, 0.05 + 0.035 * Math.sin(now / 520 - k * 2.4));
    else if (s === 'ready') t = Math.max(t, 0.04);
    else if (s === 'working' || s === 'understanding') t = Math.max(t, 0.06 + 0.3 * Math.max(0, Math.cos(i / BARS * 6.283 - now / 230)) ** 6);
    else if (s === 'speaking' && !live) t = Math.max(t, 0.2 + 0.18 * Math.sin(now / 150 + i));
    bars[i] += (t - bars[i]) * (t > bars[i] ? 0.5 : 0.13);
    sum += bars[i];
  }
  els.chip.style.setProperty('--level', Math.min(1, (sum / BARS) * 3).toFixed(3));
  const g = c.getContext('2d');
  const W = c.width;
  const H = c.height;
  const step = W / BARS;
  const bw = Math.max(2, Math.min(step * 0.5, 3.5 * dpr));
  g.clearRect(0, 0, W, H);
  g.fillStyle = color;
  for (let i = 0; i < BARS; i++) {
    const bh = Math.max(bw, Math.min(1, bars[i]) * H);
    const x = i * step + (step - bw) / 2;
    const y = (H - bh) / 2;
    g.beginPath();
    if (g.roundRect) g.roundRect(x, y, bw, bh, bw / 2);
    else g.rect(x, y, bw, bh);
    g.fill();
  }
}
