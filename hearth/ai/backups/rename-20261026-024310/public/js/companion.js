// Scorpi's talk card: a small glass card next to the desktop Scorpi (scorpi_desktop.Companion opens companion.html in its
// own window). It listens while the push-to-talk key is held (or after a tap, until you stop talking), asks Scorp about
// the window you are looking at, speaks the answer and shows it. Right-click on Scorpi shows its menu here.
//
// desk = window.pywebview.api (Companion): card(show, height, mode), mood(name, level, flash), glance(wait), state(),
// set(patch), open_main(path), dock(), stay(). Python calls companionEvent({type}) for ptt-down, ptt-up, tap, menu, say, prefs.

import { api, streamJSON, upload } from './lib/api.js';
import { h, icon } from './lib/dom.js';
import { applyAppearance } from './theme.js';
import { loadLooks } from './lib/looks.js';
import { state } from './store.js';
import {
  audioContext, browserRecognizer, createStreamSpeaker, hostSttAvailable, onSpeaking, outputAnalyser, record, speak, stopSpeaking,
  sttRoute, sttUnavailableReason, transcribe,
} from './audio.js';
import { handOff, initWake, pauseWake, resumeWake } from './wake.js';

applyAppearance();

const FRESH_AFTER = 15 * 60 * 1000; // a pause this long starts a new conversation
const STORE = 'hearth.companion';
const WAKE = /^\s*(?:(?:hey|hi|hallo|hello|ok(?:ay)?|yo|na)[\s,]+)?(?:scorpi|scorpie|scorpy|skorpi|skorpie|scorpion|scorp)\b[\s,.!?:;-]*/i;
const YES = /\b(yes|yeah|yep|sure|ok(ay)?|go|do it|allow|please|ja|jo|klar|mach|los|gerne?)\b/i;
const ALWAYS = /\b(always|immer)\b/i;
const PHASE_TEXT = {
  idle: 'Scorpi', listening: 'Listening…', hearing: 'Catching that…', thinking: 'Thinking…', working: 'Working…',
  speaking: 'Speaking', asking: 'Waiting for your OK', done: 'Done', error: 'Hmm',
};
const MOOD = { idle: 'idle', listening: 'listening', hearing: 'thinking', thinking: 'thinking', working: 'working', speaking: 'talking', asking: 'alert', done: 'idle', error: 'idle' };

let desk = null;
let prefs = {};
let phase = 'idle';
let mic = null; // {stop, cancel}
let holding = false; // the push-to-talk key is down
let controller = null;
let runId = '';
let speaker = null;
let approval = null;
let hideTimer = 0;
let hovered = false;
let mode = 'talk';

const load = () => {
  try {
    return JSON.parse(localStorage.getItem(STORE) || '{}');
  } catch {
    return {};
  }
};
const save = (patch) => {
  try {
    localStorage.setItem(STORE, JSON.stringify({ ...load(), ...patch }));
  } catch {
    /* private */
  }
};

// ---------- the card ----------

const els = {};

function build() {
  els.dot = h('span.cc-dot');
  els.status = h('span.cc-status', 'Scorpi');
  els.ctx = h('span.cc-ctx.hidden');
  els.open = h('button.cc-ic', { type: 'button', title: 'Open in Scorp', onclick: () => openConversation() }, icon('external'));
  els.close = h('button.cc-ic', { type: 'button', title: 'Close', onclick: () => { stop(); hide(); } }, icon('x'));
  els.you = h('div.cc-you.hidden');
  els.reply = h('div.cc-reply.hidden');
  els.actions = h('div.cc-actions.hidden');
  els.level = h('div.cc-level', Array.from({ length: 24 }, () => h('i')));
  els.card = h('div.cc', { dataset: { phase: 'idle' } },
    h('header.cc-head', els.dot, els.status, els.ctx, h('span.grow'), els.open, els.close),
    els.level, els.you, els.reply, els.actions);
  els.menu = h('div.cm.hidden');
  document.getElementById('companion').append(els.card, els.menu);
  els.card.addEventListener('mouseenter', () => (hovered = true));
  els.card.addEventListener('mouseleave', () => { hovered = false; if (phase === 'done' || phase === 'idle') hideSoon(); });
  new ResizeObserver(() => report()).observe(document.getElementById('companion'));
}

function report() {
  if (!desk) return;
  const hgt = Math.ceil(document.getElementById('companion').getBoundingClientRect().height);
  if (shown) desk.card(true, hgt, mode);
}

let shown = false;
function show(m = 'talk') {
  clearTimeout(hideTimer);
  // "Speech card" off: only the menu and the moment of listening show the card; answers are voice only
  if (m === 'talk' && prefs.card === false && phase !== 'listening' && phase !== 'asking') return hide();
  if (!shown) loadLooks(); // the style may have changed in the app since the card last showed
  mode = m;
  document.documentElement.dataset.mode = m;
  els.card.classList.toggle('hidden', m === 'menu');
  els.menu.classList.toggle('hidden', m !== 'menu');
  shown = true;
  requestAnimationFrame(() => desk && desk.card(true, Math.ceil(document.getElementById('companion').getBoundingClientRect().height), m));
}

function hide() {
  clearTimeout(hideTimer);
  shown = false;
  if (desk) desk.card(false);
}

function hideSoon(ms = 7000) {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    if (!hovered && (phase === 'done' || phase === 'idle' || phase === 'error')) hide();
  }, ms);
}

function setPhase(next, note = '') {
  phase = next;
  if (prefs.card === false && shown && mode === 'talk' && !['listening', 'asking'].includes(next)) hide();
  els.card.dataset.phase = next;
  els.status.textContent = note || PHASE_TEXT[next] || '';
  if (next === 'idle') resumeWake('companion');
  else pauseWake('companion');
  if (desk) {
    if (next === 'done') desk.mood('happy', 0, 1400).then(() => desk.mood('idle'));
    else if (next === 'error') desk.mood('sad', 0, 2200).then(() => desk.mood('idle'));
    else desk.mood(MOOD[next] || 'idle', 0);
  }
  if (next === 'speaking') lipSync();
}

function setText(el, text) {
  el.textContent = text;
  el.classList.toggle('hidden', !text);
}

function showContext(g) {
  if (!g) return els.ctx.classList.add('hidden');
  let where = g.process ? g.process.replace(/\.exe$/i, '') : 'window';
  if (g.url) {
    try {
      where = new URL(g.url).hostname.replace(/^www\./, '');
    } catch {
      /* not a URL */
    }
  }
  els.ctx.replaceChildren(icon(g.url ? 'globe' : 'monitor'), h('span', where));
  els.ctx.title = g.title || '';
  els.ctx.classList.remove('hidden');
}

// the mouth follows the voice
let lipFrame = 0;
let lastMood = 0;
const lipData = new Uint8Array(256);
function lipSync() {
  cancelAnimationFrame(lipFrame);
  let level = 0;
  const step = (now) => {
    if (phase !== 'speaking') return;
    let target = 0.3 + 0.2 * Math.sin(now / 140) * Math.sin(now / 320);
    if (outputAnalyser) {
      outputAnalyser.getByteTimeDomainData(lipData);
      let sum = 0;
      for (let i = 0; i < lipData.length; i++) sum += ((lipData[i] - 128) / 128) ** 2;
      target = Math.min(1, Math.sqrt(sum / lipData.length) * 5);
    }
    level += (target - level) * 0.3;
    if (desk && now - lastMood > 66) {
      lastMood = now;
      desk.mood('talking', level);
    }
    lipFrame = requestAnimationFrame(step);
  };
  lipFrame = requestAnimationFrame(step);
}

function showLevel(l) {
  els.card.style.setProperty('--level', l.toFixed(2));
  const bars = els.level.children;
  const n = bars.length;
  const t = performance.now() / 120;
  for (let i = 0; i < n; i++) {
    const k = 0.25 + 0.75 * Math.abs(Math.sin(t + i * 0.7)) * Math.min(1, l * 1.6);
    bars[i].style.transform = `scaleY(${Math.max(0.12, k).toFixed(2)})`;
  }
}

// ---------- listening ----------

async function listen({ hold = false } = {}) {
  stopSpeaking();
  if (speaker) speaker.cancel();
  speaker = null;
  approval = approval && phase === 'asking' ? approval : null;
  holding = hold;
  setText(els.you, '');
  if (!approval) setText(els.reply, '');
  els.actions.classList.add('hidden');
  show('talk');
  const route = sttRoute();
  if (!route) return fail(sttUnavailableReason());
  if (route === 'device') {
    let heard = '';
    const rec = browserRecognizer(
      (text, final) => {
        heard = text;
        setText(els.you, text);
        if (!final) return;
        mic = null;
        heardText(heard);
      },
      {
        lang: state.settings && state.settings.stt_language,
        continuous: hold,
        onRescue: () => setPhase('hearing'),
        onFail: (err) => {
          mic = null;
          if (hostSttAvailable()) listenOnHost(hold);
          else fail(err.message || sttUnavailableReason());
        },
      },
    );
    if (rec) {
      mic = { stop: () => rec.stop(), cancel: () => rec.abort() };
      setPhase('listening');
      rec.start();
      return;
    }
  }
  return listenOnHost(hold);
}

async function listenOnHost(hold) {
  try {
    audioContext();
    const session = await record({ vad: !hold, onLevel: showLevel });
    mic = session;
    setPhase('listening');
    if (hold && !holding) session.stop(); // let go before the microphone was ready
    const result = await session.done;
    mic = null;
    showLevel(0);
    if (!result || result.speechMs < 250) return quiet();
    setPhase('hearing');
    const text = await transcribe(result.blob);
    heardText(text);
  } catch (err) {
    mic = null;
    fail(err.message || 'The microphone is not available.');
  }
}

function quiet() {
  setPhase(approval ? 'asking' : 'idle');
  if (!approval) {
    setText(els.you, '');
    setText(els.reply, 'I didn’t catch that. Hold the key and try again.');
    hideSoon(3000);
  }
}

function heardText(text) {
  const clean = String(text || '').replace(WAKE, '').trim();
  if (!clean) return quiet();
  setText(els.you, clean.charAt(0).toUpperCase() + clean.slice(1));
  if (approval) return answerApproval(clean);
  ask(clean);
}

function fail(message) {
  setPhase('error', 'Can’t hear you');
  setText(els.reply, message);
  hideSoon(6000);
}

// ---------- asking Scorp ----------

function conversationId() {
  const s = load();
  return s.conv && Date.now() - (s.at || 0) < FRESH_AFTER ? s.conv : null;
}

function b64File(b64, name) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new File([bytes], name, { type: 'image/jpeg' });
}

async function ask(text) {
  setPhase('thinking');
  setText(els.reply, '');
  let g = null;
  try {
    g = desk ? await desk.glance(true) : null;
  } catch {
    g = null;
  }
  showContext(g);
  const attachments = [];
  if (g && g.shot) {
    try {
      attachments.push(await upload('/api/upload', b64File(g.shot, 'screen.jpg')));
    } catch {
      /* answer without the picture */
    }
  }
  const context = g
    ? { kind: 'window', glance: true, spoken: true, hwnd: g.hwnd, title: g.title, process: g.process, url: g.url, rect: g.rect, pointer: g.pointer }
    : { kind: 'voice', spoken: true };
  let full = '';
  let base = 0;
  let error = '';
  speaker = createStreamSpeaker({ onError: () => {} });
  controller = new AbortController();
  try {
    await streamJSON('/api/chat', {
      body: { conversation_id: conversationId(), text, attachments, context },
      signal: controller.signal,
      onEvent: (ev) => {
        switch (ev.type) {
          case 'run':
            runId = ev.run_id;
            save({ conv: ev.conv_id, at: Date.now() });
            break;
          case 'token':
            full += ev.content || '';
            setText(els.reply, full);
            if (speaker && prefs.voice !== false) speaker.push(full.slice(base));
            break;
          case 'tool_start':
            if (phase !== 'speaking' && phase !== 'listening') setPhase('working', 'Working · ' + String(ev.name || '').replace(/_/g, ' '));
            break;
          case 'approval':
            base = full.length;
            speaker = null;
            askApproval(ev);
            break;
          case 'approval_done':
            if (approval && approval.id === ev.id) approval = null;
            els.actions.classList.add('hidden');
            if (!speaker) speaker = createStreamSpeaker({ onError: () => {} });
            break;
          case 'error':
            error = ev.error || 'Something went wrong.';
            break;
          case 'done':
            if (ev.content) {
              full = ev.content;
              setText(els.reply, full);
            }
            break;
          default:
            break;
        }
      },
    });
  } catch (err) {
    if (err.name !== 'AbortError') error = err.message || 'I lost the connection.';
  }
  controller = null;
  runId = '';
  save({ at: Date.now() });
  const rest = full.slice(base);
  if (speaker && rest.trim() && phase !== 'idle' && phase !== 'listening') {
    setPhase('speaking');
    await speaker.finish(rest);
  } else if (error && phase !== 'idle') {
    setText(els.reply, error);
    await speak('Sorry, that didn’t work.', { onError: () => {} });
  }
  speaker = null;
  if (['speaking', 'thinking', 'working'].includes(phase)) {
    setPhase(error ? 'error' : 'done');
    hideSoon(full.length > 160 ? 12000 : 7000);
  }
}

async function stop() {
  approval = null;
  holding = false;
  if (mic) mic.cancel();
  mic = null;
  if (speaker) speaker.cancel();
  speaker = null;
  stopSpeaking();
  if (runId) {
    try {
      await api.post(`/api/runs/${runId}/cancel`);
    } catch {
      if (controller) controller.abort();
    }
  } else if (controller) controller.abort();
  setPhase('idle');
}

// ---------- approvals: by voice or a click ----------

async function askApproval(ev) {
  approval = ev;
  const sum = ev.summary || {};
  const what = (sum.title || String(ev.tool || 'do that').replace(/_/g, ' ')).replace(/[.?!]+$/, '');
  setPhase('asking');
  els.actions.replaceChildren(
    h('span.cc-q', 'May I ' + what.charAt(0).toLowerCase() + what.slice(1) + '?'),
    h('button.btn.primary.small', { onclick: () => decide('allow') }, 'Allow'),
    h('button.btn.quiet.small', { onclick: () => decide('deny') }, 'Deny'),
  );
  els.actions.classList.remove('hidden');
  await speak(`May I ${what.charAt(0).toLowerCase() + what.slice(1)}? Say yes or no.`, { onError: () => {} });
  if (approval === ev && phase === 'asking') listen();
}

async function decide(decision) {
  const ev = approval;
  if (!ev) return;
  approval = null;
  if (mic) mic.cancel();
  mic = null;
  els.actions.classList.add('hidden');
  setPhase(decision === 'deny' ? 'thinking' : 'working');
  try {
    await api.post('/api/approvals/' + ev.id, { decision });
  } catch {
    /* the run reports what happened */
  }
}

const answerApproval = (text) => decide(ALWAYS.test(text) ? 'always' : YES.test(text) ? 'allow' : 'deny');

function openConversation() {
  const id = load().conv;
  if (desk) desk.open_main(id ? '/c/' + id : '/');
  hide();
}

// ---------- the menu (right-click on Scorpi) ----------

function menu(p, summoned) {
  prefs = p || prefs;
  const item = (ic, label, run, sub = '') => h('button.cm-item', { type: 'button', onclick: () => { run(); } }, icon(ic), h('span.cm-text', label, sub ? h('small', sub) : null));
  const toggle = (ic, label, key, sub = '') => h('button.cm-item', { type: 'button', onclick: async () => { prefs = await desk.set({ [key]: !(prefs[key] !== false) }); menu(prefs, summoned); } },
    icon(ic), h('span.cm-text', label, sub ? h('small', sub) : null), h('span.cm-switch' + (prefs[key] !== false ? '.on' : '')));
  const keyName = String(prefs.hotkey || 'ctrl+win').split('+').map((k) => ({ ctrl: 'Ctrl', win: 'Win', alt: 'Alt', shift: 'Shift', rctrl: 'Right Ctrl', space: 'Space' }[k] || k.toUpperCase())).join(' + ');
  els.menu.replaceChildren(
    h('div.cm-title', 'Scorpi'),
    item('voice', 'Talk to me', () => { hide(); setTimeout(() => listen(), 120); }, 'or hold ' + keyName),
    item('external', 'Open Scorp', () => { desk.open_main('/'); hide(); }),
    h('div.cm-sep'),
    toggle('route', 'Roam freely', 'roam'),
    toggle('chat', 'Speech card', 'card'),
    toggle('keyboard', 'Push-to-talk', 'ptt', keyName),
    h('div.cm-sep'),
    summoned
      ? item('pin', 'Stay on the desktop', () => { desk.stay(); hide(); })
      : item('home', 'Back into the app', () => { desk.dock(); hide(); }, 'onto the prompt box'),
    item('settings', 'Settings', () => { desk.open_main('/settings#scorpi'); hide(); }),
  );
  show('menu');
}

// ---------- events from Python ----------

window.companionEvent = (ev) => {
  if (!ev) return;
  if (ev.type === 'prefs') prefs = ev.prefs || prefs;
  else if (ev.type === 'menu') menu(ev.prefs, ev.summoned);
  else if (ev.type === 'ptt-down') {
    if (controller || phase === 'speaking') stop().then(() => listen({ hold: true }));
    else listen({ hold: true });
  } else if (ev.type === 'ptt-up') {
    holding = false;
    if (mic) mic.stop();
  } else if (ev.type === 'tap') {
    if (phase === 'listening') return mic && mic.stop();
    if (phase === 'speaking' || phase === 'thinking' || phase === 'working') return stop();
    if (phase === 'asking') return listen();
    listen();
  } else if (ev.type === 'say') {
    setPhase('idle');
    setText(els.you, '');
    setText(els.reply, ev.text);
    show('talk');
    hideSoon(Math.min(10000, Math.max(3500, ev.text.length * 80)));
  }
};

// the menu closes when it loses the focus (a click anywhere else)
window.addEventListener('blur', () => mode === 'menu' && shown && hide());
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (mode === 'menu') hide();
    else stop().then(hide);
  }
});

onSpeaking((on) => {
  if (on && controller && phase !== 'listening' && phase !== 'asking') setPhase('speaking');
});

async function start() {
  desk = window.pywebview && window.pywebview.api;
  if (!desk || !desk.card) return;
  build();
  loadLooks();
  // this window has no live event stream: pick up a style changed in the app whenever the card or menu shows again
  addEventListener('focus', () => loadLooks());
  document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && loadLooks());
  try {
    const st = await desk.state();
    prefs = st.prefs || {};
  } catch {
    /* defaults */
  }
  // "Hey Scorpi" wakes the companion; "Hey Scorp" opens the main window in voice mode with what was said after it
  initWake((word, rest) => {
    if (word === 'ember') {
      if (rest) return heardText(rest);
      return listen();
    }
    handOff(rest);
    desk.open_main('/voice');
  });
  try {
    const [status, settings] = await Promise.all([api.get('/api/status'), api.get('/api/settings')]);
    Object.assign(state, { status, settings, online: true });
  } catch {
    /* the server answers once it is up */
  }
}

if (window.pywebview && window.pywebview.api) start();
else window.addEventListener('pywebviewready', start, { once: true });
