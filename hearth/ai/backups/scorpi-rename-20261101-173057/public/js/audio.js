// Microphone capture, speech-to-text and text-to-speech playback.

import { api } from './lib/api.js';
import { toast } from './lib/ui.js';
import { state } from './store.js';

let ctx = null;
export function audioContext() {
  if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

export const micSupported = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);

export function micProblem() {
  if (!window.isSecureContext) {
    return 'The microphone needs a secure connection. Open Scorp through the https:// address (Settings → Devices) or on the host PC.';
  }
  if (!micSupported()) return 'This browser cannot record audio.';
  return '';
}

// phones share one microphone between the recognizer and a recording, so only desktops keep a backup recording
export const mobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(navigator.userAgent));

// Voice mode keeps one microphone stream open between turns: opening it for every sentence cost a few hundred
// milliseconds (the first syllables were lost) and made the browser's recording indicator flicker.
const MIC = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
let held = null; // Promise<{ stream, source, analyser }> while voice mode holds the microphone
export let inputAnalyser = null; // the held microphone, for visuals

export function holdMic() {
  if (!held) {
    held = (async () => {
      const problem = micProblem();
      if (problem) throw new Error(problem);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: MIC });
      const ac = audioContext();
      const source = ac.createMediaStreamSource(stream);
      const analyser = ac.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.55;
      source.connect(analyser);
      inputAnalyser = analyser;
      return { stream, source, analyser };
    })();
    held.catch(() => (held = null));
  }
  return held;
}

export function releaseMic() {
  const h = held;
  held = null;
  inputAnalyser = null;
  if (h) {
    h.then((m) => {
      m.stream.getTracks().forEach((t) => t.stop());
      try {
        m.source.disconnect();
      } catch {
        /* ignore */
      }
    }).catch(() => {});
  }
}

// the held stream when voice mode keeps one, else a new one that the caller stops (own: true)
async function openMic(audio = MIC) {
  if (held) {
    try {
      const m = await held;
      if (m.stream.active) return { stream: m.stream, own: false };
    } catch {
      /* fall through to a stream of its own */
    }
  }
  return { stream: await navigator.mediaDevices.getUserMedia({ audio }), own: true };
}

function pickMime() {
  const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
  return types.find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || '';
}

/**
 * Record from the microphone. Options:
 *  onLevel(0..1)           — live input level for visuals
 *  vad: true               — stop automatically after trailing silence
 *  silenceMs, maxMs        — VAD tuning
 * Returns { stop(), cancel(), done: Promise<Blob|null>, analyser }
 */
export async function record({ onLevel, vad = false, silenceMs = 1300, maxMs = 120000, minSpeechMs = 350 } = {}) {
  const problem = micProblem();
  if (problem) throw new Error(problem);
  const { stream, own } = await openMic();
  const ac = audioContext();
  const source = ac.createMediaStreamSource(stream);
  const analyser = ac.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  const mime = pickMime();
  const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks = [];
  recorder.ondataavailable = (e) => e.data && e.data.size && chunks.push(e.data);

  let cancelled = false;
  let raf = 0;
  let resolveDone;
  const done = new Promise((r) => (resolveDone = r));
  const started = performance.now();
  let lastLoud = started;
  let speechMs = 0;
  let lastFrame = started;
  let noise = 0.012;
  const data = new Float32Array(analyser.fftSize);

  const tick = () => {
    analyser.getFloatTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length);
    const now = performance.now();
    const dt = now - lastFrame;
    lastFrame = now;
    // adaptive noise floor
    if (rms < noise * 1.5) noise = noise * 0.97 + rms * 0.03;
    const loud = rms > Math.max(0.018, noise * 2.6);
    if (loud) {
      lastLoud = now;
      speechMs += dt;
    }
    onLevel && onLevel(Math.min(1, rms * 9));
    if (vad && speechMs > minSpeechMs && now - lastLoud > silenceMs) return stop();
    if (now - started > maxMs) return stop();
    raf = requestAnimationFrame(tick);
  };

  const cleanup = () => {
    cancelAnimationFrame(raf);
    if (own) stream.getTracks().forEach((t) => t.stop());
    try {
      source.disconnect();
    } catch {
      /* ignore */
    }
    onLevel && onLevel(0);
  };

  recorder.onstop = () => {
    cleanup();
    if (cancelled || !chunks.length) return resolveDone(null);
    resolveDone({ blob: new Blob(chunks, { type: recorder.mimeType || mime || 'audio/webm' }), speechMs });
  };

  function stop() {
    if (recorder.state !== 'inactive') recorder.stop();
  }
  function cancel() {
    cancelled = true;
    stop();
  }

  recorder.start(250);
  raf = requestAnimationFrame(tick);
  return { stop, cancel, done, analyser };
}

// ---------- speech recognition ----------
// Order (Settings → Voice): the device's own recognizer first (Android/Google, Edge/Windows online speech, Chrome,
// Safari), then the host (free cloud Whisper or local Whisper). "host" mode reverses that.

const SR = () => window.SpeechRecognition || window.webkitSpeechRecognition;
// errors that mean this device's recognizer will not work at all (vs. "no-speech" / "aborted")
const FATAL = ['not-allowed', 'service-not-allowed', 'network', 'audio-capture', 'language-not-supported', 'bad-grammar'];
let deviceBroken = (() => {
  try {
    return sessionStorage.getItem('scorp.deviceSttBroken') === '1';
  } catch {
    return false;
  }
})();

function markDeviceBroken() {
  deviceBroken = true;
  try {
    sessionStorage.setItem('scorp.deviceSttBroken', '1');
  } catch {
    /* ignore */
  }
}

// Scorp's own window (WebView2) exposes the speech API, but it never hears anything there
const embedded = !!(window.chrome && window.chrome.webview);

// without a secure connection the recognizer exists but is refused the microphone ("not-allowed")
export const deviceSttAvailable = () => !!SR() && !deviceBroken && window.isSecureContext;
export const hostSttAvailable = () => !!(state.status && state.status.voice && state.status.voice.stt) && !micProblem();

/** 'device' | 'host' | '' — which recognizer to use next. */
export function sttRoute() {
  const mode = state.settings.stt_mode || 'auto';
  const device = deviceSttAvailable();
  const host = hostSttAvailable();
  if (mode === 'device') return device ? 'device' : '';
  if (mode === 'host' || embedded) return host ? 'host' : device ? 'device' : '';
  return device ? 'device' : host ? 'host' : '';
}

export function sttUnavailableReason() {
  if (!window.isSecureContext || (!SR() && micProblem())) return micProblem();
  if (deviceBroken) return 'This device\'s speech recognition is not working here, and the host has none. Add a free Groq key (Connections → Model) or install faster-whisper on the host.';
  return 'No speech recognition available. Use Chrome, Edge or Safari, add a free Groq key, or install faster-whisper on the host.';
}

/** Transcribe recorded audio on the host (free cloud Whisper or local Whisper). */
export async function transcribe(blob) {
  const lang = state.settings.stt_language || 'auto';
  if (!(state.status && state.status.voice && state.status.voice.stt)) {
    throw new Error('The host has no speech recognition. Add a free Groq key (Connections → Model) or install faster-whisper.');
  }
  // a sentence takes about a second; never leave the UI waiting on a stuck connection
  const limit = Math.min(180000, 25000 + (blob.size / (1024 * 1024)) * 30000);
  for (let attempt = 0; ; attempt++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), limit);
    try {
      const res = await api.raw('/api/stt?language=' + encodeURIComponent(lang), {
        method: 'POST',
        headers: { 'Content-Type': blob.type || 'audio/webm' },
        body: blob,
        signal: ctl.signal,
      });
      let data = {};
      try {
        data = await res.json();
      } catch {
        /* not JSON: a proxy or a restarting server */
      }
      if (res.ok) return data.text || '';
      if (res.status < 500 || attempt) throw Object.assign(new Error(data.error || 'Transcription failed'), { final: true });
    } catch (err) {
      if (err.final) throw err;
      if (attempt) throw new Error(err.name === 'AbortError' ? 'Speech recognition took too long. Try again.' : 'Could not reach the host for speech recognition.');
    } finally {
      clearTimeout(timer);
    }
    await new Promise((r) => setTimeout(r, 600)); // one retry: the server may have just restarted
  }
}

/** Record alongside the device recognizer (desktops only), so its failures can still be transcribed on the host. */
async function backupRecording() {
  if (mobile || !hostSttAvailable()) return null;
  try {
    return await record({ maxMs: 90000 });
  } catch {
    return null;
  }
}

/**
 * The device's own speech recognizer (Android, Edge, Chrome, Safari).
 * onText(text, final) streams the transcript; onFail(error) fires instead of a final result when the recognizer
 * cannot work on this device (permission, no service, WebView without speech), so callers can fall back to the host.
 * On desktops a backup recording runs alongside; when the recognizer breaks or returns nothing although speech was
 * heard, that recording is transcribed on the host (onRescue() fires first) and delivered as the final text.
 */
export function browserRecognizer(onText, { lang, onFail, onRescue, continuous = false } = {}) {
  const Rec = SR();
  if (!Rec || deviceBroken) return null;
  const rec = new Rec();
  const abortRaw = rec.abort.bind(rec);
  rec.lang = lang && lang !== 'auto' ? lang : navigator.language || 'en-US';
  rec.interimResults = true;
  rec.continuous = continuous; // continuous: keeps listening through pauses until stop()
  rec.maxAlternatives = 1;
  let final = '';
  let failed = false;
  let started = false;
  let closed = false;
  const backup = backupRecording();
  const dropBackup = () => backup.then((b) => b && b.cancel());
  // the recognizer heard nothing or broke, but the backup did hear speech: transcribe that on the host instead
  const rescue = async () => {
    const b = await backup;
    if (!b) return null;
    b.stop();
    const result = await b.done;
    if (!result || result.speechMs < 400) return null;
    if (onRescue) onRescue();
    try {
      return await transcribe(result.blob);
    } catch {
      return null;
    }
  };
  const finish = async () => {
    if (closed) return;
    closed = true;
    if (final.trim()) {
      dropBackup();
      return onText(final, true);
    }
    const text = await rescue();
    onText(text || final, true);
  };
  rec.onstart = () => (started = true);
  rec.onresult = (e) => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) final += e.results[i][0].transcript;
      else interim += e.results[i][0].transcript;
    }
    onText(final + interim, false);
  };
  const fail = async (code) => {
    if (failed) return;
    failed = true;
    markDeviceBroken(); // remembered for this tab only; a new tab or app start tries the device again
    try {
      abortRaw();
    } catch {
      /* ignore */
    }
    if (closed) return;
    closed = true;
    const text = await rescue();
    if (text) return onText(text, true);
    if (onFail) onFail(new Error('Device speech recognition unavailable (' + code + ')'));
    else onText(final, true);
  };
  rec.onerror = (e) => {
    if (FATAL.includes(e.error) && !final) fail(e.error);
  };
  rec.onend = () => {
    if (!failed) finish();
  };
  // abort() means the user cancelled: no rescue
  rec.abort = () => {
    closed = true;
    dropBackup();
    abortRaw();
  };
  const start = rec.start.bind(rec);
  rec.start = () => {
    try {
      start();
    } catch (err) {
      return fail(err.name || 'start');
    }
    // some embedded browsers expose the API but never start listening (generous: a permission prompt may be open)
    setTimeout(() => !started && !failed && fail('did not start'), 9000);
  };
  return rec;
}

// ---------- speech output ----------
// Clips are decoded and scheduled back to back on the audio clock, with the silence each engine pads them with
// trimmed off, so sentences flow into each other instead of stopping and starting. Where Web Audio cannot play
// (a context the browser keeps suspended, a clip it cannot decode) an <audio> element plays the clip instead.

const GAP = 0.1; // seconds between two clips: a breath, not a stop
let speakToken = 0;
let speaking = false;
let quietTimer = 0;
let cursor = 0; // audio-clock time where the next clip may start
const scheduled = new Set(); // { src, gain } clips on the audio clock
let playing = null; // { audio, end } the fallback <audio> element
export let outputAnalyser = null;
const speakListeners = new Set();
export const onSpeaking = (fn) => speakListeners.add(fn);

// "speaking" turns on at once but off only after a short quiet, so between two sentences of one reply voice mode
// does not flip to listening and back
function setSpeaking(on, holdMs = 0) {
  clearTimeout(quietTimer);
  if (on === speaking) return;
  const apply = () => {
    speaking = on;
    speakListeners.forEach((fn) => fn(on));
  };
  if (!on && holdMs) quietTimer = setTimeout(apply, holdMs);
  else apply();
}

function output() {
  const ac = audioContext();
  if (!outputAnalyser || outputAnalyser.context !== ac) {
    outputAnalyser = ac.createAnalyser();
    outputAnalyser.fftSize = 512;
    outputAnalyser.smoothingTimeConstant = 0.6;
    outputAnalyser.connect(ac.destination);
  }
  return outputAnalyser;
}

export function stopSpeaking() {
  speakToken++;
  if (ctx) {
    const t = ctx.currentTime;
    for (const c of scheduled) {
      try {
        // a few milliseconds of fade: a hard cut clicks
        c.gain.gain.cancelScheduledValues(t);
        c.gain.gain.setValueAtTime(c.gain.gain.value, t);
        c.gain.gain.linearRampToValueAtTime(0, t + 0.03);
        c.src.stop(t + 0.035);
      } catch {
        /* already ended */
      }
    }
  }
  scheduled.clear();
  cursor = 0;
  if (playing) {
    const p = playing;
    playing = null;
    try {
      p.audio.pause();
    } catch {
      /* ignore */
    }
    p.end();
  }
  if (window.speechSynthesis) speechSynthesis.cancel();
  setSpeaking(false);
}

export const isSpeaking = () => speaking;

function cleanText(md) {
  return String(md || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#+\s*/gm, '')
    .replace(/[*_~>|#]/g, '')
    .replace(/\[\d+\]/g, '')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Split text into chunks: a short first one so playback starts quickly, then longer ones that sound natural. */
export function chunkSentences(text) {
  const clean = cleanText(text);
  const parts = clean.match(/[^.!?。！？]+[.!?。！？]+["')\]]*\s*|[^.!?。！？]+$/g) || [];
  const out = [];
  let buf = '';
  for (const p of parts) {
    if ((buf + p).length < (out.length ? 260 : 70)) buf += p;
    else {
      if (buf) out.push(buf.trim());
      buf = p;
    }
  }
  if (buf.trim()) out.push(buf.trim());
  return out;
}

/** Whole sentences from the start of text, gathered until they make at least min characters ('' when none). */
function sentencesFrom(text, min) {
  const re = /[\s\S]*?[.!?。！？]["')\]]*(?=\s)/g;
  let end = 0;
  while (end < min && re.exec(text)) end = re.lastIndex;
  return text.slice(0, end);
}

async function fetchAudio(text) {
  const res = await api.raw('/api/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    let msg = 'Speech failed';
    try {
      msg = (await res.json()).error || msg;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  const fallback = res.headers.get('X-TTS-Fallback');
  if (fallback && fallback !== lastFallback) {
    // the chosen engine failed and another one spoke instead: say why once, not for every sentence
    lastFallback = fallback;
    toast('Your chosen voice did not work', { body: fallback + ' — another voice was used instead.', type: 'warn', timeout: 9000 });
  }
  return { bytes: await res.arrayBuffer(), type: res.headers.get('Content-Type') || 'audio/mpeg' };
}
let lastFallback = '';

function browserSpeak(text, token) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    const pref = state.settings.tts_voice || '';
    const lang = (pref.match(/^([a-z]{2}-[A-Z]{2})/) || [])[1];
    const voices = speechSynthesis.getVoices();
    const match = voices.find((v) => lang && v.lang === lang) || voices.find((v) => v.default);
    if (match) u.voice = match;
    u.onend = u.onerror = () => resolve();
    if (token !== speakToken) return resolve();
    speechSynthesis.speak(u);
  });
}

function playUrl(url, token) {
  return new Promise((resolve) => {
    if (token !== speakToken) return resolve();
    const audio = new Audio(url);
    const end = () => {
      URL.revokeObjectURL(url);
      if (playing && playing.audio === audio) playing = null;
      resolve();
    };
    playing = { audio, end };
    audio.onended = audio.onerror = end;
    audio.play().catch(end);
  });
}

// where the sound starts and ends: engines pad every clip with silence, which made a pause around each sentence
function audible(buf) {
  const d = buf.getChannelData(0);
  let a = 0;
  let b = d.length - 1;
  while (a < b && Math.abs(d[a]) < 0.01) a++;
  while (b > a && Math.abs(d[b]) < 0.01) b--;
  const start = Math.max(0, a / buf.sampleRate - 0.025);
  return { start, dur: Math.max(0, Math.min(buf.duration, b / buf.sampleRate + 0.07) - start) };
}

/**
 * Play one fetched clip. On the audio clock it is scheduled right after what is already playing and this returns
 * at once; the returned promise (wrapped, so awaiting this does not wait for it) settles when the clip has ended.
 */
async function play(clip, token) {
  if (token !== speakToken) return { ended: Promise.resolve() };
  if (ctx && ctx.state === 'running') {
    let buf = null;
    try {
      buf = await ctx.decodeAudioData(clip.bytes.slice(0));
    } catch {
      /* not decodable here: the element below may still play it */
    }
    if (token !== speakToken) return { ended: Promise.resolve() };
    if (buf) {
      const { start, dur } = audible(buf);
      if (dur < 0.05) return { ended: Promise.resolve() };
      const at = Math.max(ctx.currentTime + 0.03, cursor + GAP);
      const src = ctx.createBufferSource();
      const gain = ctx.createGain();
      src.buffer = buf;
      gain.gain.setValueAtTime(0, at);
      gain.gain.linearRampToValueAtTime(1, at + 0.012);
      gain.gain.setValueAtTime(1, at + dur - 0.02);
      gain.gain.linearRampToValueAtTime(0, at + dur);
      src.connect(gain);
      gain.connect(output());
      const c = { src, gain };
      scheduled.add(c);
      const ended = new Promise((resolve) => {
        src.onended = () => {
          scheduled.delete(c);
          try {
            gain.disconnect();
          } catch {
            /* ignore */
          }
          resolve();
        };
      });
      src.start(at, start, dur);
      cursor = at + dur;
      return { ended };
    }
  }
  await playUrl(URL.createObjectURL(new Blob([clip.bytes], { type: clip.type })), token);
  return { ended: Promise.resolve() };
}

/**
 * Speak markdown text. Fetches the next chunk while the previous one plays.
 * Returns a promise that resolves when everything was spoken (or stopped).
 */
export async function speak(text, { onError } = {}) {
  stopSpeaking();
  const token = ++speakToken;
  const parts = chunkSentences(text);
  if (!parts.length) return;
  setSpeaking(true);
  const useBrowser = state.settings.tts_engine === 'browser';
  let last = Promise.resolve();
  try {
    if (useBrowser) {
      for (const p of parts) {
        if (token !== speakToken) break;
        await browserSpeak(p, token);
      }
      return;
    }
    let next = fetchAudio(parts[0]);
    for (let i = 0; i < parts.length; i++) {
      if (token !== speakToken) break;
      let clip;
      try {
        clip = await next;
      } catch (err) {
        onError && onError(err);
        // fall back to the device voice for the rest
        await last;
        for (const p of parts.slice(i)) {
          if (token !== speakToken) break;
          await browserSpeak(p, token);
        }
        break;
      }
      if (i + 1 < parts.length) next = fetchAudio(parts[i + 1]);
      last = (await play(clip, token)).ended;
    }
    await last;
  } finally {
    if (token === speakToken) setSpeaking(false);
  }
}

/**
 * Streaming speaker: feed the growing reply; finished sentences are spoken in order while the rest streams.
 * Each sentence is sent off for synthesis the moment it is complete (a few at a time), so the voices are ready
 * before they are needed. say(text) speaks a line of its own (voice mode's short agent updates); interrupt() drops
 * what is queued or playing but keeps the speaker usable for what comes next. onSpeaking() turns false shortly
 * after the queue runs dry.
 */
export function createStreamSpeaker({ onError } = {}) {
  stopSpeaking();
  let token = speakToken;
  const useBrowser = state.settings.tts_engine === 'browser';
  let spoken = 0; // characters of `latest` already queued
  let latest = '';
  let open = true; // more of the reply may still come
  let pending = 0; // clips queued and not yet heard to the end
  let chain = Promise.resolve();
  let last = Promise.resolve(); // the end of the last scheduled clip
  const fetches = []; // synthesis requests in order: at most three run at once

  const heard = (mine) => {
    if (mine === speakToken && !--pending) setSpeaking(false, open ? 900 : 250);
  };

  const enqueue = (sentence) => {
    if (!sentence.trim() || token !== speakToken) return;
    pending++;
    setSpeaking(true);
    const mine = token;
    let audio = null;
    if (!useBrowser) {
      const turn = fetches.length >= 3 ? fetches[fetches.length - 3] : Promise.resolve();
      audio = turn
        .then(() => (mine === speakToken ? fetchAudio(sentence) : null))
        .catch((err) => {
          onError && onError(err);
          return null;
        });
      fetches.push(audio);
      if (fetches.length > 3) fetches.shift();
    }
    chain = chain.then(async () => {
      const clip = audio && mine === speakToken ? await audio : null;
      if (mine !== speakToken) return;
      if (clip) {
        const { ended } = await play(clip, mine);
        last = ended;
        ended.then(() => heard(mine));
      } else {
        await last; // the device voice waits for clips still playing
        await browserSpeak(sentence, mine);
        heard(mine);
      }
    });
  };

  const prepare = (text) => {
    const fences = (text.match(/```/g) || []).length;
    if (fences % 2) text = text.slice(0, text.lastIndexOf('```'));
    return cleanText(text);
  };

  // a very short sentence ("Sure.") waits to go out with the next one; the first goes out as soon as it can
  const flush = () => {
    while (token === speakToken) {
      const rest = latest.slice(spoken);
      const min = spoken ? 40 : 18;
      const chunk = sentencesFrom(rest, min);
      if (!chunk || (chunk.trim().length < min && rest.length - chunk.length < 40)) return;
      enqueue(chunk);
      spoken += chunk.length;
    }
  };

  return {
    push(fullText) {
      latest = prepare(fullText);
      flush();
    },
    say(text) {
      enqueue(cleanText(text));
      return chain.then(() => last);
    },
    async finish(finalText) {
      open = false;
      if (typeof finalText === 'string') latest = prepare(finalText);
      for (;;) {
        const rest = latest.slice(spoken);
        const chunk = sentencesFrom(rest, 40) || rest;
        if (!chunk.trim()) break;
        enqueue(chunk);
        spoken += chunk.length;
      }
      spoken = latest.length;
      if (!pending && speaking && token === speakToken) setSpeaking(false, 250);
      await chain;
      await last;
    },
    interrupt() {
      spoken = latest.length;
      pending = 0;
      fetches.length = 0;
      if (token === speakToken) stopSpeaking();
      token = speakToken;
      chain = Promise.resolve();
      last = Promise.resolve();
    },
    cancel() {
      if (token === speakToken) stopSpeaking();
    },
  };
}

/**
 * Listen for the user starting to talk while Scorp speaks (barge-in). The microphone runs with echo cancellation and a
 * raised threshold, so Scorp's own voice from the speakers rarely counts; headphones make it reliable.
 * Calls onSpeech() once, then stops. Returns { stop() }.
 */
export async function watchForSpeech({ onSpeech, onLevel, holdMs = 280 } = {}) {
  if (micProblem()) return { stop() {} };
  let stream, own;
  try {
    ({ stream, own } = await openMic({ ...MIC, autoGainControl: false }));
  } catch {
    return { stop() {} };
  }
  const ac = audioContext();
  const source = ac.createMediaStreamSource(stream);
  const analyser = ac.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  const data = new Float32Array(analyser.fftSize);
  const out = new Uint8Array(256);
  let noise = 0.015;
  let loudFor = 0;
  let last = performance.now();
  let raf = 0;
  let stopped = false;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    cancelAnimationFrame(raf);
    if (own) stream.getTracks().forEach((t) => t.stop());
    try {
      source.disconnect();
    } catch {
      /* ignore */
    }
    onLevel && onLevel(0);
  };
  const tick = () => {
    if (stopped) return;
    analyser.getFloatTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length);
    // what is playing right now: the louder Scorp is, the louder the user has to be
    let playing = 0;
    if (outputAnalyser) {
      outputAnalyser.getByteTimeDomainData(out);
      let s = 0;
      for (let i = 0; i < out.length; i++) s += ((out[i] - 128) / 128) ** 2;
      playing = Math.sqrt(s / out.length);
    }
    const now = performance.now();
    const dt = now - last;
    last = now;
    if (rms < noise * 1.4) noise = noise * 0.97 + rms * 0.03;
    const loud = rms > Math.max(0.045, noise * 3.6, playing * 0.9);
    loudFor = loud ? loudFor + dt : Math.max(0, loudFor - dt * 0.6);
    onLevel && onLevel(Math.min(1, rms * 9));
    if (loudFor > holdMs) {
      stop();
      return onSpeech && onSpeech();
    }
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return { stop };
}
