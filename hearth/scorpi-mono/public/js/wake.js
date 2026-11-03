// Wake word: "Hey Scorpi" calls Scorpi, in the desktop app its companion, elsewhere a voice conversation (Settings → Voice, per device).
// A small detector here picks out short bursts of speech from the microphone; only those go to the host, which
// transcribes them (local Whisper first) and looks for the wake word at the start. Whatever else is using the
// microphone (voice mode, Ember, dictation) pauses this, and Scorpi's own voice is never checked.

import { api } from './lib/api.js';
import { audioContext, micProblem, onSpeaking } from './audio.js';
import { state } from './store.js';

const PREFS_KEY = 'scorpi.prefs';
const BLOCK = 2048; // samples per analysis block (~43 ms at 48 kHz)
const PRE = 8; // blocks kept from before speech starts, so "Hey" is not clipped
const QUIET = 12; // blocks of quiet that end a burst (~0.5 s)
const MAX_SECONDS = 8; // longest burst checked: a wake word and a question
const RATE = 16000; // what is sent to the host

let handler = null;
const paused = new Set(); // reasons the microphone is someone else's right now
let mic = null; // { stream, source, node, sink }
let starting = false;
let speaking = false;
let checking = false;
let queued = null;

// read fresh each time: Ember's overlay window shares these settings with the main window
export function wakeEnabled() {
  try {
    return !!JSON.parse(localStorage.getItem(PREFS_KEY) || '{}').wakeWord;
  } catch {
    return false;
  }
}

/** Start listening for wake words (when the setting is on). onWake('ember', rest). */
export function initWake(onWake) {
  handler = onWake;
  sync();
}

/** The setting changed (or another window changed it). */
export const refreshWake = () => sync();

export function pauseWake(reason) {
  paused.add(reason);
  sync();
}

export function resumeWake(reason) {
  if (paused.delete(reason)) sync();
}

const wanted = () => !!handler && wakeEnabled() && !paused.size;

function sync() {
  if (wanted()) start();
  else stop();
}

window.addEventListener('storage', (e) => e.key === PREFS_KEY && sync());
// the desktop window went to the tray: it stops listening like everything else
window.addEventListener('scorpi:hidden', () => pauseWake('hidden'));
window.addEventListener('focus', () => resumeWake('hidden'));
// a browser only starts audio after a click or key press
const unlock = () => {
  if (mic) audioContext();
};
window.addEventListener('pointerdown', unlock, true);
window.addEventListener('keydown', unlock, true);

onSpeaking((on) => {
  speaking = on;
  if (on) reset();
});

async function start() {
  if (mic || starting || micProblem()) return;
  starting = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (!wanted()) return stream.getTracks().forEach((t) => t.stop());
    const ac = audioContext();
    const source = ac.createMediaStreamSource(stream);
    const node = ac.createScriptProcessor(BLOCK, 1, 1);
    const sink = ac.createGain(); // the processor only runs while connected to the output; silently
    sink.gain.value = 0;
    source.connect(node);
    node.connect(sink);
    sink.connect(ac.destination);
    node.onaudioprocess = (e) => block(e.inputBuffer.getChannelData(0), ac.sampleRate);
    mic = { stream, source, node, sink };
    // the microphone went away (unplugged, taken by the system): try again in a moment
    stream.getTracks().forEach((t) =>
      t.addEventListener('ended', () => {
        if (!mic || mic.stream !== stream) return;
        stop();
        setTimeout(sync, 3000);
      }),
    );
  } catch {
    /* no microphone (or it was refused): wake words stay quiet */
  } finally {
    starting = false;
  }
}

function stop() {
  reset();
  if (!mic) return;
  const m = mic;
  mic = null;
  m.node.onaudioprocess = null;
  m.stream.getTracks().forEach((t) => t.stop());
  for (const n of [m.source, m.node, m.sink]) {
    try {
      n.disconnect();
    } catch {
      /* ignore */
    }
  }
}

// ---------- finding bursts of speech ----------

let noise = 0.01;
let loudRun = 0;
let quietRun = 0;
let speech = 0;
let pre = [];
let burst = null;

function reset() {
  burst = null;
  pre = [];
  loudRun = 0;
}

function block(input, rate) {
  if (!mic) return;
  let sum = 0;
  for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
  const rms = Math.sqrt(sum / input.length);
  if (!burst && rms < noise * 2) noise = noise * 0.95 + rms * 0.05; // follow the room's own noise
  const loud = rms > Math.max(0.012, noise * 3);
  const pcm = downsample(input, rate);
  if (speaking) return;
  if (!burst) {
    pre.push(pcm);
    if (pre.length > PRE) pre.shift();
    loudRun = loud ? loudRun + 1 : 0;
    if (loudRun >= 2) {
      burst = pre;
      pre = [];
      quietRun = 0;
      speech = loudRun;
    }
    return;
  }
  burst.push(pcm);
  if (loud) {
    quietRun = 0;
    speech++;
  } else quietRun++;
  const full = burst.length * BLOCK >= MAX_SECONDS * rate;
  if (quietRun < QUIET && !full) return;
  const chunks = burst;
  burst = null;
  loudRun = 0;
  // "Hey Ember" is about half a second of voice; a cough or a click is not worth a transcription
  if (speech * BLOCK >= 0.3 * rate) check(chunks, full);
}

function downsample(input, rate) {
  const ratio = rate / RATE;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const a = Math.floor(i * ratio);
    const b = Math.max(a + 1, Math.floor((i + 1) * ratio));
    let s = 0;
    for (let j = a; j < b; j++) s += input[j];
    out[i] = s / (b - a);
  }
  return out;
}

function wav(chunks) {
  const n = chunks.reduce((a, c) => a + c.length, 0);
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const text = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  text(0, 'RIFF');
  v.setUint32(4, 36 + n * 2, true);
  text(8, 'WAVEfmt ');
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, RATE, true);
  v.setUint32(28, RATE * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  text(36, 'data');
  v.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) {
    for (let i = 0; i < c.length; i++, o += 2) v.setInt16(o, Math.max(-1, Math.min(1, c[i])) * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}

// ---------- asking the host ----------

async function check(chunks, cut) {
  if (checking) {
    queued = [chunks, cut]; // only the latest waits: older bursts are stale
    return;
  }
  checking = true;
  try {
    const lang = (state.settings && state.settings.stt_language) || 'auto';
    const res = await api.raw('/api/wake?language=' + encodeURIComponent(lang), {
      method: 'POST',
      headers: { 'Content-Type': 'audio/wav' },
      body: wav(chunks),
    });
    const data = res.ok ? await res.json() : {};
    if (data.wake && wanted() && !speaking) {
      chime();
      // a burst cut off at the limit ends mid-sentence: just wake, and let the listener hear the question
      handler(data.wake, cut ? '' : data.rest || '');
    }
  } catch {
    /* the host is busy or restarting: the next burst tries again */
  } finally {
    checking = false;
    if (queued) {
      const q = queued;
      queued = null;
      if (wanted()) check(...q);
    }
  }
}

// a soft two-note "I'm here"
function chime() {
  try {
    const ac = audioContext();
    const t = ac.currentTime;
    [660, 880].forEach((f, i) => {
      const o = ac.createOscillator();
      const g = ac.createGain();
      o.type = 'sine';
      o.frequency.value = f;
      g.gain.setValueAtTime(0, t + i * 0.09);
      g.gain.linearRampToValueAtTime(0.07, t + i * 0.09 + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.09 + 0.22);
      o.connect(g);
      g.connect(ac.destination);
      o.start(t + i * 0.09);
      o.stop(t + i * 0.09 + 0.25);
    });
  } catch {
    /* no audio: the listener's own change shows it */
  }
}

/** "Hey Scorpi, …" heard by Ember's overlay: the main window picks up what was said with it. */
export function handOff(text) {
  try {
    localStorage.setItem('scorpi.wakeSay', JSON.stringify({ text, at: Date.now() }));
  } catch {
    /* private mode */
  }
}

export function takeHandOff() {
  try {
    const v = JSON.parse(localStorage.getItem('scorpi.wakeSay') || 'null');
    localStorage.removeItem('scorpi.wakeSay');
    return v && Date.now() - v.at < 15000 ? v.text : '';
  } catch {
    return '';
  }
}
