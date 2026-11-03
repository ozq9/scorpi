// Quick Ember: a little droplet that floats over everything and that you talk to. No chat window: tap it, speak,
// and it answers out loud. Drag it onto a window (on the desktop) or onto a panel of the app to attach it, so what
// you ask is about that, and work happens in it.
//
// Taps: idle → listen · listening → done talking · thinking → stop · speaking → interrupt and listen.
// Right-click opens the conversation in Hearth.
//
// Two hosts share this widget:
//   page    — inside the Hearth app (browsers, phones): Ember floats over every view.
//   desktop — Ember's own always-on-top overlay window (desktop.py), which can attach to any program's window.
//             `desk` is window.pywebview.api there; Python owns the window's position and size.

import { api, streamJSON, upload } from './api.js';
import { h, icon } from './dom.js';
import { createMascot } from './mascot.js';
import { toast } from './ui.js';
import { bus, state } from '../store.js';
import {
  audioContext, browserRecognizer, createStreamSpeaker, hostSttAvailable, onSpeaking, outputAnalyser, record,
  speak, stopSpeaking, sttRoute, sttUnavailableReason, transcribe,
} from '../audio.js';
import { pauseWake, resumeWake } from '../wake.js';

const STORE = 'hearth.buddy';
// things in the app Ember can be attached to, most specific first
const TARGETS = '[data-ctx], .msg, .approval, .card, .tile, .list-row, .glance-item, .panel, .section, .view.active';
const ORB = 30; // half the orb's size
const FRESH_AFTER = 15 * 60 * 1000; // a pause this long starts a new conversation
const YES = /\b(yes|yeah|yep|sure|ok(ay)?|go|do it|allow|please|ja|jo|klar|okay|mach|los|gerne?)\b/i;
const ALWAYS = /\b(always|immer)\b/i;

function load() {
  try {
    return JSON.parse(localStorage.getItem(STORE) || '{}');
  } catch {
    return {};
  }
}

function save(patch) {
  try {
    localStorage.setItem(STORE, JSON.stringify({ ...load(), ...patch }));
  } catch {
    /* private mode */
  }
}

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function labelFor(el) {
  if (el.dataset.ctxLabel) return el.dataset.ctxLabel;
  if (el.matches('.msg.user')) return 'Your message';
  if (el.matches('.msg')) return 'Scorp’s reply';
  if (el.matches('.approval')) return 'Approval request';
  if (el.matches('.view')) return (el.dataset.view || 'page').replace(/^./, (c) => c.toUpperCase()) + ' page';
  const head = el.querySelector('h1, h2, h3, h4, .lr-title, .t-name, .card-title, .ph-text, strong');
  const text = ((head && head.textContent) || el.textContent || '').replace(/\s+/g, ' ').trim();
  return text.slice(0, 48) || 'Panel';
}

function b64File(b64, name) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new File([bytes], name, { type: 'image/jpeg' });
}

const STATUS = {
  idle: 'Scorpi — tap and talk, drag onto a window to attach, right-click to go back',
  listening: 'Listening… tap when you’re done',
  hearing: 'Catching that…',
  thinking: 'Thinking… tap to stop',
  working: 'Working… tap to stop',
  speaking: 'Speaking… tap to interrupt',
  asking: 'Waiting for your yes or no',
};

export function createBuddy({ mount, desk = null } = {}) {
  const saved = load();
  const ember = createMascot({ size: 52, sleepAfter: desk ? 240000 : 180000 });
  const pin = h('span.bd-pin.hidden', icon('link'));
  const orb = h('button.bd-orb', { type: 'button', 'aria-label': 'Talk to Scorpi' }, h('span.bd-ring'), h('span.bd-glow'), ember.el, pin);
  const hint = h('div.bd-hint');
  const root = h('div.buddy' + (desk ? '.desk' : ''), { dataset: { mode: 'compact', state: 'idle' } }, orb, hint);
  const box = h('div.bd-target.hidden', h('span'));
  mount.append(root, box);

  let mode = 'idle';
  let mic = null; // the running recording / recognizer: {stop, cancel}
  let controller = null;
  let runId = '';
  let speaker = null;
  let approval = null; // a tool approval Ember asked about out loud
  let attached = null; // page: {el, label}; desktop: {hwnd, title, process, rect}
  let peek = saved.peek !== false; // send a snapshot of the attached window with each question
  let x = 0;
  let y = 0;

  // ---------- state & face ----------

  const MOOD = { idle: 'idle', listening: 'listening', hearing: 'thinking', thinking: 'thinking', working: 'working', speaking: 'talking', asking: 'alert' };

  function setState(next, note = '') {
    mode = next;
    root.dataset.state = next;
    // Ember has the microphone while it is busy: no wake words meanwhile
    if (next === 'idle') resumeWake('ember');
    else pauseWake('ember');
    ember.set(MOOD[next] || 'idle');
    const where = attached ? ' · ' + (attached.title || attached.label) : '';
    orb.title = (note || STATUS[next]) + where;
    if (next === 'speaking') lipSync();
  }

  let lipFrame = 0;
  const lipData = new Uint8Array(256);
  function lipSync() {
    cancelAnimationFrame(lipFrame);
    let level = 0;
    const step = (now) => {
      if (mode !== 'speaking') return ember.talk(0);
      let target = 0.3 + 0.2 * Math.sin(now / 140) * Math.sin(now / 320);
      if (outputAnalyser) {
        outputAnalyser.getByteTimeDomainData(lipData);
        let sum = 0;
        for (let i = 0; i < lipData.length; i++) sum += ((lipData[i] - 128) / 128) ** 2;
        target = Math.min(1, Math.sqrt(sum / lipData.length) * 5);
      }
      level += (target - level) * 0.25;
      ember.talk(level);
      root.style.setProperty('--level', level.toFixed(2));
      lipFrame = requestAnimationFrame(step);
    };
    lipFrame = requestAnimationFrame(step);
  }

  function fail(message) {
    setState('idle', message);
    ember.flash('sad', 2600);
    if (!desk) toast('Scorpi can’t hear you', { type: 'warn', body: message, timeout: 7000 });
  }

  // the main chat's Ember moods show here too while Ember itself is idle
  bus.on('agent:mood', ({ mood, flash }) => {
    if (mode !== 'idle') return;
    if (flash) ember.flash(mood, flash);
    else ember.set(mood);
  });
  // the playing voice decides when Ember's mouth moves
  onSpeaking((on) => {
    if (on && controller && mode !== 'listening' && mode !== 'asking') setState('speaking');
  });

  // ---------- listening ----------

  async function listen() {
    stopSpeaking();
    const route = sttRoute();
    if (!route) return fail(sttUnavailableReason());
    if (route === 'device') {
      let heard = '';
      const rec = browserRecognizer(
        (text, final) => {
          heard = text;
          if (!final) return;
          mic = null;
          if (heard.trim()) heardText(heard);
          else setState(approval ? 'asking' : 'idle');
        },
        {
          lang: state.settings && state.settings.stt_language,
          onRescue: () => setState('hearing'),
          onFail: (err) => {
            mic = null;
            if (hostSttAvailable()) listenOnHost();
            else fail(err.message || sttUnavailableReason());
          },
        },
      );
      if (rec) {
        mic = { stop: () => rec.stop(), cancel: () => rec.abort() };
        setState('listening');
        rec.start();
        return;
      }
    }
    return listenOnHost();
  }

  async function listenOnHost() {
    try {
      audioContext();
      const session = await record({ vad: true, onLevel: (l) => root.style.setProperty('--level', l.toFixed(2)) });
      mic = session;
      setState('listening');
      const result = await session.done;
      mic = null;
      root.style.setProperty('--level', '0');
      if (!result) return setState(approval ? 'asking' : 'idle');
      setState('hearing');
      const text = await transcribe(result.blob);
      if (text.trim()) heardText(text);
      else {
        setState(approval ? 'asking' : 'idle');
        ember.flash('curious', 1400);
      }
    } catch (err) {
      mic = null;
      fail(err.message || 'The microphone is not available.');
    }
  }

  function heardText(text) {
    if (approval) return answerApproval(text);
    ask(text);
  }

  // ---------- asking Hearth ----------

  function contextNow() {
    const ctx = { spoken: true };
    if (!attached) return { ...ctx, kind: 'voice' };
    if (attached.el) return { ...ctx, kind: 'element', label: attached.label, text: (attached.el.innerText || '').slice(0, 8000) };
    return { ...ctx, kind: 'window', hwnd: attached.hwnd, title: attached.title, process: attached.process, rect: attached.rect };
  }

  function conversationId() {
    const s = load();
    return s.conv && Date.now() - (s.at || 0) < FRESH_AFTER ? s.conv : null;
  }

  async function ask(text) {
    setState('thinking');
    ember.hop();
    const context = contextNow();
    const attachments = [];
    if (desk && context.kind === 'window' && peek) {
      try {
        const shot = await desk.snapshot();
        if (shot) attachments.push(await upload('/api/upload', b64File(shot, 'window.jpg')));
      } catch {
        /* answer without the snapshot */
      }
    }
    let full = '';
    let base = 0; // how much of the reply was handed to a speaker that an approval question interrupted
    let error = '';
    speaker = createStreamSpeaker({ onError: () => {} });
    controller = new AbortController();
    try {
      await streamJSON('/api/chat', {
        body: { conversation_id: conversationId(), text, attachments, context, model: state.chatModel || '' },
        signal: controller.signal,
        onEvent: (ev) => {
          switch (ev.type) {
            case 'run':
              runId = ev.run_id;
              save({ conv: ev.conv_id, at: Date.now() });
              break;
            case 'token':
              full += ev.content || '';
              if (speaker) speaker.push(full.slice(base));
              break;
            case 'tool_start':
              if (mode !== 'speaking' && mode !== 'listening') setState('working', 'Working · ' + String(ev.name || '').replace(/_/g, ' '));
              break;
            case 'approval':
              base = full.length;
              speaker = null;
              askApproval(ev);
              break;
            case 'approval_done':
              if (approval && approval.id === ev.id) approval = null;
              if (!speaker) speaker = createStreamSpeaker({ onError: () => {} });
              break;
            case 'error':
              error = ev.error || 'Something went wrong.';
              break;
            case 'done':
              if (ev.content) full = ev.content;
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
    if (speaker && rest.trim() && mode !== 'idle') {
      if (mode !== 'listening') setState('speaking');
      await speaker.finish(rest);
    } else if (error && mode !== 'idle') {
      ember.flash('sad', 2400);
      await speak('Sorry, that didn’t work. ' + error.split('.')[0] + '.', { onError: () => {} });
    }
    speaker = null;
    if (mode === 'speaking' || mode === 'thinking' || mode === 'working') setState('idle');
    if (full.trim() && !error) ember.flash('happy', 1400);
  }

  async function stop() {
    approval = null;
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
    setState('idle');
  }

  // ---------- approvals, by voice ----------

  async function askApproval(ev) {
    approval = ev;
    const sum = ev.summary || {};
    const what = (sum.title || String(ev.tool || 'do that').replace(/_/g, ' ')).replace(/[.?!]+$/, '');
    setState('asking');
    ember.flash('alert', 2000);
    await speak(`May I ${what.charAt(0).toLowerCase() + what.slice(1)}? Say yes or no.`, { onError: () => {} });
    if (approval === ev && mode === 'asking') listen();
  }

  async function answerApproval(text) {
    const ev = approval;
    approval = null;
    const decision = ALWAYS.test(text) ? 'always' : YES.test(text) ? 'allow' : 'deny';
    setState(decision === 'deny' ? 'thinking' : 'working');
    try {
      await api.post('/api/approvals/' + ev.id, { decision });
    } catch {
      /* the run reports what happened */
    }
  }

  // ---------- tapping ----------

  function tap() {
    if (mode === 'idle') return listen();
    if (mode === 'listening') return mic && mic.stop();
    if (mode === 'speaking') {
      // interrupt and listen again; the rest of the reply is dropped
      if (speaker) speaker.cancel();
      speaker = null;
      stopSpeaking();
      if (!controller) setState('idle');
      return listen();
    }
    if (mode === 'asking') {
      stopSpeaking();
      return listen();
    }
    if (mode === 'hearing') return;
    stop(); // thinking or working
  }

  function openConversation() {
    const id = load().conv;
    const path = id ? '/c/' + id : '/';
    if (desk) desk.open_main(path);
    else {
      history.pushState({}, '', path);
      dispatchEvent(new PopStateEvent('popstate'));
    }
  }

  // ---------- placement (page host) ----------

  function place(nx, ny) {
    x = clamp(nx, ORB + 6, innerWidth - ORB - 6);
    y = clamp(ny, ORB + 6, innerHeight - ORB - 6);
    root.style.setProperty('--x', x + 'px');
    root.style.setProperty('--y', y + 'px');
  }

  function home() {
    const p = load().pos;
    if (p && typeof p.x === 'number') place(p.x * innerWidth, p.y * innerHeight);
    else place(innerWidth - 40, innerHeight * 0.62);
  }

  function snap(nx, ny) {
    // rest against the nearest side, like a sticky note
    place(nx > innerWidth / 2 ? innerWidth - ORB - 10 : ORB + 10, ny);
    save({ pos: { x: x / innerWidth, y: y / innerHeight } });
  }

  // ---------- attaching ----------

  function pageTarget(px, py) {
    for (const el of document.elementsFromPoint(px, py)) {
      if (root.contains(el) || box.contains(el)) continue;
      const hit = el.closest(TARGETS);
      if (hit && !hit.closest('.buddy, .sidebar, .tabbar, .titlebar')) return hit;
    }
    return null;
  }

  function showBox(el) {
    if (!el) return box.classList.add('hidden');
    const r = el.getBoundingClientRect();
    Object.assign(box.style, { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px' });
    box.firstChild.textContent = 'Attach to · ' + labelFor(el);
    box.classList.remove('hidden');
  }

  let follow = 0;
  function followEl() {
    cancelAnimationFrame(follow);
    const step = () => {
      if (!attached || !attached.el) return;
      if (!attached.el.isConnected || (attached.el.matches('.view') && !attached.el.classList.contains('active'))) return detach();
      const r = attached.el.getBoundingClientRect();
      place(clamp(r.right - 8, ORB + 6, innerWidth - ORB - 6), clamp(r.top + 26, ORB + 6, innerHeight - ORB - 6));
      follow = requestAnimationFrame(step);
    };
    step();
  }

  function attach(target) {
    detach(true);
    attached = target.el ? { el: target.el, label: labelFor(target.el) } : target;
    if (attached.el) {
      attached.el.classList.add('bd-attached');
      followEl();
    }
    pin.classList.remove('hidden');
    root.classList.add('attached');
    ember.flash('happy', 1200).hop();
    setState(mode);
  }

  function detach(quiet = false) {
    if (!attached) return;
    if (attached.el) attached.el.classList.remove('bd-attached');
    cancelAnimationFrame(follow);
    attached = null;
    if (desk && !quiet) desk.detach();
    pin.classList.add('hidden');
    root.classList.remove('attached');
    setState(mode);
  }

  // ---------- dragging ----------

  function pageDrag(e) {
    const start = { x: e.clientX, y: e.clientY };
    let dragging = false;
    let target = null;
    orb.setPointerCapture(e.pointerId);
    const move = (ev) => {
      if (!dragging) {
        if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 6) return;
        dragging = true;
        detach(true);
        root.classList.add('dragging');
        if (mode === 'idle') ember.set('curious');
      }
      place(ev.clientX, ev.clientY);
      target = pageTarget(ev.clientX, ev.clientY);
      showBox(target);
    };
    const end = (ev) => {
      orb.removeEventListener('pointermove', move);
      orb.removeEventListener('pointerup', end);
      orb.removeEventListener('pointercancel', end);
      root.classList.remove('dragging');
      showBox(null);
      if (!dragging) return tap();
      setState(mode);
      if (target && ev.type === 'pointerup') attach({ el: target });
      else snap(ev.clientX, ev.clientY);
    };
    orb.addEventListener('pointermove', move);
    orb.addEventListener('pointerup', end);
    orb.addEventListener('pointercancel', end);
  }

  async function deskDrag() {
    // Python moves the window with the mouse until the button is released; meanwhile name what is underneath
    let polling = true;
    const poll = async () => {
      while (polling) {
        const st = await desk.hover();
        if (!polling) break;
        if (st && st.dragging) {
          root.dataset.mode = 'drag';
          hint.textContent = st.target ? 'Attach to · ' + st.target.title : 'Drop on a window to attach';
          hint.classList.toggle('on', !!st.target);
          if (mode === 'idle' && ember.base !== 'curious') ember.set('curious');
        }
        await new Promise((r) => setTimeout(r, 90));
      }
    };
    poll();
    const res = await desk.drag();
    polling = false;
    root.dataset.mode = 'compact';
    if (!res || !res.moved) return tap();
    setState(mode);
    if (res.target) attach(res.target);
    else detach(true);
  }

  orb.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    if (desk) deskDrag();
    else pageDrag(e);
  });
  orb.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    // out on the desktop, a right-click sends Scorpi back onto the prompt box
    if (desk && desk.dock) desk.dock();
    else openConversation();
  });
  orb.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      tap();
    }
    if (e.key === 'Escape' && mode !== 'idle') stop();
  });

  if (desk) {
    // Python tells the page when the attached window closes
    window.emberEvent = (ev) => {
      if (ev.type === 'detached') {
        detach(true);
        ember.flash('sad', 1200);
      }
      if (ev.type === 'listen' && mode === 'idle') listen();
    };
    desk.attached().then((t) => t && attach(t));
  } else {
    home();
    addEventListener('resize', () => (attached ? followEl() : home()));
    addEventListener('keydown', (e) => {
      // Ctrl+Space calls Ember from anywhere in the app
      if ((e.ctrlKey || e.metaKey) && e.code === 'Space' && !e.shiftKey) {
        e.preventDefault();
        tap();
      }
    });
  }
  // "Hey Ember": listen, or answer what was said with it right away
  function wake(rest = '') {
    ember.hop();
    if (rest) heardText(rest);
    else if (mode === 'idle' || mode === 'speaking') tap();
  }

  setState('idle');
  return { tap, stop, wake, ember, setPeek: (on) => save({ peek: (peek = !!on) }) };
}
