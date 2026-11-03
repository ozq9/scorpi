// Settings: every option as a row in a category, a search that finds any of them, and deep links (/settings#voice).
// Rows are declared once (label, description, search words, control); the page lays them out per category or as
// search results across all categories.

import { api } from '../lib/api.js';
import { clear, copyText, h, icon } from '../lib/dom.js';
import { seg, selectEl, switchEl, toast } from '../lib/ui.js';
import { FONTS, TUNING_KEYS, applyAppearance, inkTransition } from '../theme.js';
import { looks } from '../lib/looks.js';
import { localVideoInfo, refreshBackdrop, removeVideoFile, setVideoFile } from '../lib/backdrop.js';
import { bus, prefs, setPref, state } from '../store.js';
import { speak, audioContext } from '../audio.js';
import { refreshWake, wakeEnabled } from '../wake.js';
import { navigate } from '../router.js';
import { createMascot } from '../lib/mascot.js';

let root;
let current = 'general';
let query = '';

const CATS = [
  { id: 'general', label: 'Chat', icon: 'chat', desc: 'How answers look and how the conversation behaves.' },
  { id: 'appearance', label: 'Appearance', icon: 'palette', desc: 'Style, colour, type and the backdrop. Fine-tuning is stored on this device.' },
  { id: 'scorpi', label: 'Scorpi', icon: 'scorpi', desc: 'Your companion: on the prompt box, or free on the desktop with a push-to-talk key.' },
  { id: 'voice', label: 'Voice', icon: 'voice', desc: 'How Scorp sounds and how it hears you.' },
  { id: 'you', label: 'You', icon: 'user', desc: 'What Scorp knows about you and how it should behave.' },
  { id: 'agent', label: 'Agent', icon: 'agent', desc: 'How freely the model works on a request.' },
  { id: 'devices', label: 'Devices', icon: 'phone', desc: 'Open Scorp on other devices, pairing and security.' },
  { id: 'notifications', label: 'Notifications', icon: 'bell', desc: 'Alerts in the app, on Windows and on your phone.' },
  { id: 'integrations', label: 'Integrations', icon: 'plug', desc: 'Discord, models, programs and MCP servers.' },
  { id: 'about', label: 'About', icon: 'spark', desc: 'Version, keyboard shortcuts and resets.' },
];

const ACCENTS = [['white', '#FFFFFF'], ['ice', '#A8DBFF'], ['violet', '#CBB8FF'], ['mint', '#93F2CC'], ['amber', '#FFD08A'], ['rose', '#FFB3C9']];

const LANGS = [
  ['auto', 'Detect automatically'], ['en', 'English'], ['de', 'German'], ['es', 'Spanish'], ['fr', 'French'], ['it', 'Italian'], ['nl', 'Dutch'],
  ['pl', 'Polish'], ['pt', 'Portuguese'], ['tr', 'Turkish'], ['ru', 'Russian'], ['uk', 'Ukrainian'], ['ar', 'Arabic'], ['hi', 'Hindi'],
  ['ja', 'Japanese'], ['ko', 'Korean'], ['zh', 'Chinese'], ['sv', 'Swedish'], ['da', 'Danish'], ['no', 'Norwegian'], ['fi', 'Finnish'], ['cs', 'Czech'],
];

// push-to-talk keys for the desktop companion (Python polls them; see desktop.py Companion)
const HOTKEYS = [
  ['ctrl+win', 'Ctrl + Win'],
  ['rctrl', 'Right Ctrl'],
  ['ctrl+alt', 'Ctrl + Alt'],
  ['xbutton1', 'Mouse back button'],
  ['xbutton2', 'Mouse forward button'],
  ['f9', 'F9'],
  ['ctrl+shift+space', 'Ctrl + Shift + Space'],
];

const desk = () => {
  const a = window.pywebview && window.pywebview.api;
  return a && a.companion ? a : null;
};

async function save(patch, msg = 'Saved') {
  try {
    state.settings = await api.post('/api/settings', patch);
    bus.emit('settings', state.settings);
    if (msg) toast(msg, { timeout: 1400 });
  } catch (err) {
    toast(err.message, 'error');
  }
}

function range(min, max, value, unit, onChange, step = 1) {
  const out = h('span.range-val', value + unit);
  const input = h('input.range', { type: 'range', min, max, step, value, oninput: () => { out.textContent = input.value + unit; onChange(+input.value); } });
  return h('div.set-range', input, out);
}

/** A row: { label, desc, keys, control, stack (control under the text), id } */
const row = (label, desc, control, opts = {}) => ({ label, desc, control, ...opts });
const group = (title, rows, opts = {}) => ({ title, rows: rows.filter(Boolean), ...opts });

// ---------- categories ----------

function generalGroups(s) {
  return [
    group('Answers', [
      row('Answer style', 'Visual: the answer appears on a live canvas beside the chat and Scorp only says the key point. Text: classic written answers.',
        () => seg([['visual', 'Visual'], ['text', 'Text']], s.response_style || 'visual', (v) => save({ response_style: v }, v === 'visual' ? 'Answers are visual now' : 'Answers are text now')), { keys: 'canvas visual text response' }),
      row('Open the canvas automatically', 'When Scorp changes the canvas, bring it into view.', () => switchEl(prefs.canvasAuto !== false, (v) => setPref('canvasAuto', v)), { keys: 'canvas' }),
      row('Read replies aloud', 'Speak every answer on this device.', () => switchEl(prefs.autoSpeak, (v) => setPref('autoSpeak', v)), { keys: 'speak tts voice aloud' }),
    ]),
    group('Writing', [
      row('Enter sends', 'Otherwise use Ctrl+Enter. On phones the return key adds a line.', () => switchEl(prefs.sendOnEnter, (v) => setPref('sendOnEnter', v)), { keys: 'keyboard enter send' }),
      row('Minimal prompt box', 'Model, skill and tool chips appear only while you use the prompt box.', () => switchEl(!!prefs.minimalComposer, (v) => setPref('minimalComposer', v)), { keys: 'composer chips clean' }),
      row('Starter chips', 'Skill buttons and an info line under the greeting.', () => switchEl(prefs.hideStarters === false, (v) => setPref('hideStarters', !v)), { keys: 'welcome greeting skills clean' }),
    ]),
  ];
}

function appearanceGroups() {
  const data = looks();
  const active = data.active || '';
  const styleTiles = h('div.style-strip',
    [null, ...data.styles.slice(0, 7)].map((st) => {
      const v = (st && st.vars) || {};
      const on = (st ? st.id : '') === active;
      return h('button.style-tile' + (on ? '.on' : ''), {
        type: 'button',
        title: st ? st.description || st.name : 'Obsidian (default)',
        onclick: () => api.post('/api/looks/active', { id: st ? st.id : '' }).catch((e) => toast(e.message, 'error')),
      },
      h('span.st-swatch', { style: { background: v['--bg'] || '#000', color: v['--ink'] || '#f4f4f6', fontFamily: v['--font-display'] || v['--font-ui'] || '' } }, 'Aa', h('i', { style: { background: v['--accent'] || '#fff' } })),
      h('span.st-name', st ? st.name : 'Obsidian'));
    }),
    h('button.style-tile.more', { type: 'button', onclick: () => navigate('/studio') }, h('span.st-swatch', icon('studio')), h('span.st-name', 'Open Studio')),
  );
  const swatches = h('div.swatches', ACCENTS.map(([k, c]) => h('button' + ((prefs.accent || 'white') === k ? '.on' : ''), {
    type: 'button', title: k, style: { background: c },
    onclick: (e) => {
      setPref('accent', k);
      inkTransition(applyAppearance);
      e.currentTarget.parentElement.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === e.currentTarget));
    },
  })));
  const videoRows = prefs.bgMode === 'video' ? videoGroupRows() : [];
  return [
    group('Style', [
      row('Active style', 'Styles change the whole look on every device. Make your own in the Studio — by hand or by describing it.', () => styleTiles, { stack: true, keys: 'theme look studio style plugin' }),
    ]),
    group('Fine-tune on this device', [
      row('Accent', 'White by default; a hint of colour if you like.', () => swatches, { keys: 'colour color accent' }),
      row('Font', null, () => selectEl(Object.entries(FONTS).map(([k, [label]]) => [k, label]), prefs.fontFace || 'default', (v) => setPref('fontFace', v === 'default' ? null : v)), { keys: 'typeface font type' }),
      row('Text size', null, () => range(12, 20, prefs.fontPx || 15, 'px', (v) => setPref('fontPx', v === 15 ? null : v)), { keys: 'font size bigger smaller zoom' }),
      row('Corners', null, () => seg([['0.35', 'Sharp'], ['0.7', 'Crisp'], ['1', 'Soft'], ['1.4', 'Round']], String(prefs.round ?? 1), (v) => setPref('round', +v === 1 ? null : +v)), { keys: 'radius rounded square' }),
      row('Chat width', null, () => seg([['narrow', 'Narrow'], ['normal', 'Normal'], ['wide', 'Wide']], prefs.chatWidth || 'normal', (v) => setPref('chatWidth', v === 'normal' ? null : v)), { keys: 'column width' }),
      row('Glass tint', '50% is the style as made. Clear lets the backdrop through; solid is calmer to read.', () => range(0, 100, prefs.glassTint ?? 50, '%', (v) => setPref('glassTint', v)), { keys: 'transparency opacity glass' }),
      row('Glass blur', null, () => range(0, 40, prefs.glassBlur ?? 22, 'px', (v) => setPref('glassBlur', v)), { keys: 'blur frosted glass' }),
      row('Motion', 'Full keeps Scorpi and the light moving even when Windows animations are off. System follows Windows.', () => seg([['full', 'Full'], ['system', 'System'], ['reduced', 'Reduced']], prefs.motion || 'full', (v) => { setPref('motion', v); applyAppearance(); }), { keys: 'animation motion reduce' }),
      row('Reset fine-tuning', 'Back to what the active style says.', () => h('button.btn.small', { onclick: () => { TUNING_KEYS.forEach((k) => setPref(k, null)); paint(); toast('Fine-tuning reset', { timeout: 1500 }); } }, icon('refresh'), 'Reset'), { keys: 'reset default' }),
    ]),
    group('Backdrop', [
      row('Backdrop', 'Black with slow white light that wakes up while Scorp thinks, plain black, or your own looping video.',
        () => seg([['effects', 'Light'], ['black', 'Black'], ['video', 'Video']], prefs.bgMode || 'effects', (v) => { setPref('bgMode', v); paint(); }), { keys: 'background wallpaper video light' }),
      row('Crisp light', 'Draws the light at full screen resolution with dithering (no banding). Turn off on slow PCs.', () => switchEl(prefs.bgHiRes !== false, (v) => setPref('bgHiRes', v)), { keys: 'quality antialiasing sharp resolution smaa fxaa' }),
      ...videoRows,
    ]),
  ];
}

function videoGroupRows() {
  const box = h('div.set-video', h('span.muted', '…'));
  localVideoInfo().then((local) => {
    clear(box);
    const file = h('input', { type: 'file', accept: 'video/*', hidden: true, onchange: async () => {
      const f = file.files[0];
      if (!f) return;
      try {
        await setVideoFile(f);
        toast('Video set', { body: f.name, timeout: 2000 });
      } catch (err) {
        toast('Could not keep the video', { type: 'error', body: err.message });
      }
      paint();
    } });
    box.append(file,
      h('button.btn.small', { onclick: () => file.click() }, icon('upload'), local ? 'Replace' : 'Choose video'),
      local ? h('span.muted.set-note', local.name) : null,
      local ? h('button.btn.quiet.small', { onclick: async () => { await removeVideoFile(); paint(); } }, 'Remove') : null);
  });
  const url = h('input.input', { value: prefs.bgVideoUrl || '', placeholder: 'https://…/loop.mp4' });
  const useUrl = () => { setPref('bgVideoUrl', url.value.trim()); refreshBackdrop(); toast(url.value.trim() ? 'Video URL set' : 'Video URL removed', { timeout: 1500 }); };
  url.addEventListener('keydown', (e) => e.key === 'Enter' && useUrl());
  return [
    row('Video file', 'Kept on this device. It loops silently behind the glass.', () => box, { keys: 'video file' }),
    row('Video URL', null, () => h('div.set-inline', url, h('button.btn.small', { onclick: useUrl }, 'Use')), { stack: true, keys: 'video url link' }),
    row('Video blur', null, () => range(0, 60, prefs.bgBlur ?? 18, 'px', (v) => setPref('bgBlur', v)), { keys: 'video blur' }),
    row('Video brightness', null, () => range(10, 100, prefs.bgBright ?? 55, '%', (v) => setPref('bgBright', v)), { keys: 'video brightness dim' }),
    row('Light over the video', null, () => switchEl(prefs.bgEffectsOverVideo !== false, (v) => setPref('bgEffectsOverVideo', v)), { keys: 'video light effects' }),
  ];
}

function scorpiGroups() {
  const d = desk();
  const companion = d
    ? group('On the desktop', [
        row('Push-to-talk key', 'Hold it anywhere in Windows and talk: “hey Scorpi, what’s this?”, “tell the coder to rebuild this website”. Scorpi sees the window you are looking at. Tap it to talk hands-free.',
          () => companionControl('hotkey'), { keys: 'hotkey shortcut push to talk ptt key hold keyboard' }),
        row('Live on the desktop', 'Scorpi leaves the app window and lives over every program. Right-click it for its menu.', () => companionControl('detached'), { keys: 'desktop overlay free float' }),
        row('Roam freely', 'Walks along the taskbar and the tops of windows, falls when a window moves away, naps when you are away.', () => companionControl('roam'), { keys: 'walk wander roam move' }),
        row('Look at the window', 'Send a picture of the window you are looking at with your question, so “this” means what is on screen.', () => companionControl('peek'), { keys: 'screenshot snapshot context window' }),
        row('Speech card', 'Show what Scorpi hears and answers in a small card next to it. Off: voice only.', () => companionControl('card'), { keys: 'bubble card text caption' }),
        row('Size', null, () => companionControl('size'), { keys: 'size big small scale' }),
      ])
    : group('On the desktop', [
        row('Desktop companion', 'In the Scorp desktop app on Windows, Scorpi can live freely on the desktop and answer a push-to-talk key from any program.', () => h('span.badge', 'Desktop app'), { keys: 'hotkey desktop push to talk' }),
      ]);
  return [
    group(null, [row(null, null, () => scorpiHero(), { stack: true, bare: true })]),
    group('In the app', [
      row('Show Scorpi', 'The little scorpion on the prompt box. Drag it along the edge, pet it, right-click it.', () => switchEl(!prefs.hideEmber, (v) => setPref('hideEmber', !v)), { keys: 'mascot hide show companion' }),
      row('Wake word', 'Say “Hey Scorp” to start talking, or “Hey Scorpi” to call Scorpi, while Scorp is open. Short bursts of speech are checked on the host.', () => switchEl(wakeEnabled(), (v) => {
        setPref('wakeWord', v);
        audioContext();
        refreshWake();
        if (v && !(state.status && state.status.voice && state.status.voice.stt)) toast('The host can’t check wake words yet', { type: 'warn', body: 'Install faster-whisper on the host or add a free Groq key (Connections → Model).', timeout: 8000 });
      }), { keys: 'hey scorp wake word listen' }),
    ]),
    companion,
  ];
}

function scorpiHero() {
  const m = createMascot({ size: 104, sleepAfter: 0, cloud: true });
  const moods = ['happy', 'curious', 'thinking', 'working', 'listening', 'shy'];
  let i = 0;
  const box = h('div.scorpi-hero', { onclick: () => { m.flash(moods[i++ % moods.length], 1600).hop(); } }, m.el,
    h('div', h('div.sh-title', 'Scorpi'), h('div.sh-sub', 'Click to say hi. Hold your push-to-talk key anywhere to ask about what you see.')));
  setTimeout(() => m.wave(), 400);
  return box;
}

// companion settings live in the desktop app (data/ember.json), read and written through pywebview
let companionPrefs = null;
function companionControl(key) {
  const d = desk();
  const wrap = h('div.set-ctl');
  const render = () => {
    clear(wrap);
    const p = companionPrefs || {};
    const set = (patch) => d.companion('set', patch).then((r) => { companionPrefs = r; render(); }).catch((e) => toast(e.message, 'error'));
    if (key === 'hotkey') {
      const known = HOTKEYS.some(([k]) => k === (p.hotkey || 'ctrl+win'));
      const opts = known ? HOTKEYS : [...HOTKEYS, [p.hotkey, keyLabel(p.hotkey)]];
      wrap.append(
        switchEl(p.ptt !== false, (v) => set({ ptt: v })),
        selectEl([...opts, ['__record', 'Record a key…']], p.hotkey || 'ctrl+win', (v) => (v === '__record' ? recordKey((k) => k ? set({ hotkey: k }) : render()) : set({ hotkey: v }))),
      );
    } else if (key === 'size') {
      wrap.append(seg([['small', 'Small'], ['normal', 'Normal'], ['large', 'Large']], p.size || 'normal', (v) => set({ size: v })));
    } else if (key === 'detached') {
      wrap.append(switchEl(!!p.detached, (v) => set({ detached: v })));
    } else {
      wrap.append(switchEl(p[key] !== false, (v) => set({ [key]: v })));
    }
  };
  if (companionPrefs) render();
  else d.companion('get').then((r) => { companionPrefs = r; render(); }).catch(() => wrap.append(h('span.muted', 'Unavailable')));
  return wrap;
}

function keyLabel(k) {
  return String(k || '').split('+').map((p) => ({ ctrl: 'Ctrl', rctrl: 'Right Ctrl', lctrl: 'Left Ctrl', alt: 'Alt', ralt: 'Right Alt', shift: 'Shift', win: 'Win', space: 'Space', capslock: 'Caps Lock' }[p] || p.toUpperCase())).join(' + ');
}

// Press a key combination in the page; modifiers alone count when they are released together.
function recordKey(done) {
  const hint = h('div.key-rec', icon('keyboard'), h('b', 'Press the keys you want to hold…'), h('span.muted', 'Esc cancels'));
  document.body.appendChild(hint);
  const held = new Set();
  let best = [];
  const name = (e) => {
    if (e.key === 'Control') return e.location === 2 ? 'rctrl' : 'ctrl';
    if (e.key === 'Alt' || e.key === 'AltGraph') return e.location === 2 ? 'ralt' : 'alt';
    if (e.key === 'Shift') return 'shift';
    if (e.key === 'Meta' || e.key === 'OS') return 'win';
    if (e.key === ' ') return 'space';
    if (e.key === 'CapsLock') return 'capslock';
    if (/^F\d{1,2}$/.test(e.key)) return e.key.toLowerCase();
    if (/^[a-z0-9]$/i.test(e.key)) return e.key.toLowerCase();
    return '';
  };
  const order = ['ctrl', 'rctrl', 'alt', 'ralt', 'shift', 'win'];
  const finish = (k) => {
    removeEventListener('keydown', down, true);
    removeEventListener('keyup', up, true);
    hint.remove();
    done(k);
  };
  const down = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.key === 'Escape') return finish('');
    const n = name(e);
    if (!n) return;
    held.add(n);
    if (held.size >= best.length) best = [...held].sort((a, b) => (order.indexOf(a) + 1 || 9) - (order.indexOf(b) + 1 || 9));
    hint.querySelector('b').textContent = keyLabel(best.join('+'));
  };
  const up = (e) => {
    e.preventDefault();
    held.delete(name(e));
    if (!held.size && best.length) finish(best.join('+'));
  };
  addEventListener('keydown', down, true);
  addEventListener('keyup', up, true);
}

function voiceGroups(s) {
  const st = state.status || {};
  const vs = st.voice || {};
  const engines = vs.engines || [];
  const voiceBox = h('div.set-ctl');
  const loadVoices = async (engine) => {
    clear(voiceBox);
    if (engine === 'browser') return voiceBox.append(h('span.muted', 'Each device uses its own voice'));
    voiceBox.append(h('span.muted', 'Loading…'));
    try {
      const data = await api.get('/api/tts/voices?engine=' + engine);
      clear(voiceBox);
      if (!data.voices.length) return voiceBox.append(h('span.muted', data.error || 'No voices found'));
      const key = engine === 'fish' ? 'fish_voice' : 'tts_voice';
      const options = data.voices.map((v) => [v.id, `${v.name}${v.gender ? ' · ' + v.gender : ''}`]);
      if (engine === 'fish') {
        if (state.settings[key] && !options.some(([id]) => id === state.settings[key])) options.unshift([state.settings[key], 'Custom voice · ' + state.settings[key].slice(0, 8)]);
        options.unshift(['', 'Fish default voice']);
      }
      voiceBox.append(selectEl(options, state.settings[key], (v) => save({ [key]: v }, 'Voice set')));
    } catch (err) {
      clear(voiceBox);
      voiceBox.append(h('span.muted', err.message));
    }
  };
  loadVoices(s.tts_engine);
  const fishRows = s.tts_engine === 'fish' ? fishGroupRows() : [];
  return [
    group('Speaking', [
      row('Engine', 'Neural voices are free (Microsoft Edge voices, online). The system voice works offline.', () => selectEl(engines.map((e) => [e.id, e.name + (e.available ? '' : ' (no key yet)')]), s.tts_engine, async (v) => { await save({ tts_engine: v }); paint(); }), { keys: 'tts engine speak edge fish' }),
      row('Voice', null, () => voiceBox, { keys: 'tts voice speaker' }),
      row('Speed', null, () => selectEl([['-25', 'Slower'], ['-10', 'Relaxed'], ['0', 'Normal'], ['10', 'Brisk'], ['25', 'Fast']], String(s.tts_rate || 0), (v) => save({ tts_rate: +v })), { keys: 'rate speed fast slow' }),
      row('Try it', null, () => h('button.btn.small', { onclick: () => { audioContext(); speak('Hello. This is how I sound. Calm, clear, and ready when you are.', { onError: (e) => toast('Using the device voice', { body: e.message, type: 'warn' }) }); } }, icon('speaker'), 'Test voice'), { keys: 'test listen' }),
      ...fishRows,
    ]),
    group('Listening', [
      row('Hold to talk', 'In Voice mode, hold Space (or the voice bar), speak and let go to send. Off: Scorp listens all the time.', () => switchEl(prefs.voiceHold !== false, (v) => setPref('voiceHold', v)), { keys: 'push to talk hold space microphone' }),
      row('Recognition', 'Device recognizers are fast on Android, Edge, Chrome and Safari; others fall back to the host.', () => selectEl([['auto', 'This device first, then the host'], ['device', 'This device only'], ['host', 'Host first (Whisper)']], s.stt_mode || 'auto', (v) => save({ stt_mode: v })), { keys: 'stt speech recognition whisper' }),
      row('Host recognition', 'Automatic uses cloud Whisper when a Groq or Mistral key is set, else local faster-whisper.', () => selectEl([
        ['auto', 'Automatic'],
        ['cloud', 'Free cloud Whisper' + (vs.stt_cloud ? ' (' + vs.stt_cloud + ')' : ' (add a Groq or Mistral key)')],
        ['local', 'Local Whisper' + (vs.stt_local ? '' : ' (not installed)')],
      ], s.stt_host || 'auto', (x) => save({ stt_host: x })), { keys: 'whisper cloud local groq' }),
      row('Local Whisper model', 'Downloaded on first use.', () => selectEl([['tiny', 'tiny — fastest'], ['base', 'base — balanced'], ['small', 'small — better'], ['medium', 'medium — accurate'], ['large-v3', 'large-v3 — best (GPU)']], s.stt_model, (x) => save({ stt_model: x })), { keys: 'whisper model' }),
      row('Spoken language', null, () => selectEl(LANGS, s.stt_language, (v) => save({ stt_language: v })), { keys: 'language german english' }),
    ]),
  ];
}

function fishGroupRows() {
  const key = h('input.input', { type: 'password', value: state.settings.fish_api_key || '', placeholder: 'Paste your API key from fish.audio' });
  const saveKey = async () => {
    if (!key.value.trim() || key.value.startsWith('••')) return toast('Paste a Fish Audio API key first', 'warn');
    await save({ fish_api_key: key.value.trim() }, 'Key saved');
    paint();
  };
  key.addEventListener('keydown', (e) => e.key === 'Enter' && saveKey());
  const vid = h('input.input', { value: state.settings.fish_voice || '', placeholder: 'https://fish.audio/m/… or the 32-character id' });
  const saveVoice = async () => {
    const m = vid.value.match(/[0-9a-f]{32}/i);
    if (vid.value.trim() && !m) return toast('That is not a Fish Audio voice id', 'warn');
    await save({ fish_voice: m ? m[0].toLowerCase() : '' }, m ? 'Voice set' : 'Using the default voice');
    paint();
  };
  vid.addEventListener('keydown', (e) => e.key === 'Enter' && saveVoice());
  return [
    row('Fish Audio key', state.settings.fish_api_key_set ? 'A key is saved.' : 'Create one at fish.audio → API keys.', () => h('div.set-inline', key, h('button.btn.small', { onclick: saveKey }, 'Save')), { stack: true, keys: 'fish api key' }),
    row('Fish model', null, () => selectEl([['s2.1-pro', 'S2.1 Pro (best)'], ['s2.1-pro-free', 'S2.1 Pro free tier'], ['s2-pro', 'S2 Pro'], ['s1', 'S1'], ['drama-3', 'Drama 3']], state.settings.fish_model || 's2.1-pro', (v) => save({ fish_model: v })), { keys: 'fish model' }),
    row('Fish voice by id', null, () => h('div.set-inline', vid, h('button.btn.small', { onclick: saveVoice }, 'Use')), { stack: true, keys: 'fish voice id' }),
  ];
}

function youGroups(s) {
  const name = h('input.input', { value: s.user_name || '', placeholder: 'What should Scorp call you?' });
  const sys = h('textarea.input', { rows: 5, placeholder: 'Standing instructions, e.g. “Answer in German. Keep it short. I live in Vienna.”' }, s.system_prompt || '');
  const saveBtn = h('button.btn.primary.small', { onclick: () => save({ user_name: name.value, system_prompt: sys.value }) }, icon('check'), 'Save');
  return [
    group('Profile', [
      row('Name', null, () => name, { keys: 'name call me' }),
      row('Standing instructions', 'Applied to every conversation.', () => h('div.set-stack', sys, h('div.set-actions', saveBtn)), { stack: true, keys: 'system prompt instructions rules language' }),
      row('Long-term memory', 'Remember preferences and facts across conversations.', () => switchEl(s.memory_enabled, (v) => save({ memory_enabled: v })), { keys: 'memory remember' }),
    ]),
  ];
}

function agentGroups(s) {
  return [
    group('Behaviour', [
      row('Creativity', 'Lower is more precise, higher more inventive.', () => selectEl([['0.2', 'Precise'], ['0.4', 'Focused'], ['0.6', 'Balanced'], ['0.8', 'Creative'], ['1.0', 'Playful']], String(s.temperature), (v) => save({ temperature: +v })), { keys: 'temperature creativity' }),
      row('Maximum steps per answer', 'How many tool rounds a single request may take.', () => selectEl([['8', '8'], ['16', '16'], ['24', '24'], ['40', '40'], ['60', '60']], String(s.max_steps), (v) => save({ max_steps: +v })), { keys: 'steps tools rounds limit' }),
      row('Models, tools & permissions', 'Choose models, allow or block tools, connect programs and MCP servers.', () => h('button.btn.small', { onclick: () => navigate('/connections') }, icon('plug'), 'Connections'), { keys: 'model provider permission tools mcp' }),
    ]),
  ];
}

function devicesGroups(s) {
  const st = state.status || {};
  const net = st.network || {};
  const addrs = h('div.set-stack');
  const add = (label, url) => addrs.append(h('div.addr', h('span.badge', label), h('span.grow', url), h('button.icon-btn', { title: 'Copy', onclick: () => copyText(url).then(() => toast('Copied')) }, icon('copy'))));
  add('this PC', `http://localhost:${net.port}`);
  (net.addresses || []).forEach((a) => {
    add(a.kind, `http://${a.ip}:${net.port}`);
    if (net.https_port) add(a.kind + ' · https', `https://${a.ip}:${net.https_port}`);
  });
  const security = st.local
    ? [
        row('Pairing PIN', 'Other devices enter this once.', () => h('span.pin-display', s.pin || '••••••'), { keys: 'pin pairing code' }),
        row('New PIN', 'Signs out every paired device.', () => h('button.btn.small', { onclick: async () => { const r = await api.post('/api/auth/rotate'); s.pin = r.pin; paint(); toast('New PIN: ' + r.pin); } }, icon('retry'), 'Rotate'), { keys: 'pin rotate reset' }),
        row('Require PIN on the network', 'Turn off only on a network you fully trust.', () => switchEl(s.require_pin, (v) => save({ require_pin: v })), { keys: 'pin security' }),
      ]
    : [row('This device', 'Security settings are managed on the host PC.', () => h('button.btn.small', { onclick: async () => { await api.post('/api/auth/logout'); localStorage.removeItem('hearth.token'); location.reload(); } }, icon('lock'), 'Sign out'), { keys: 'sign out logout' })];
  return [
    group('Addresses', [
      row('Open Scorp elsewhere', 'Phones need the https address for the microphone (accept the certificate once), or run tailscale serve 8000 for a trusted one.', () => addrs, { stack: true, keys: 'address url ip network tailscale https phone' }),
      row('Install as an app', 'Desktop: the Scorp shortcut opens the native window. iPhone: Share → Add to Home Screen. Android: menu → Install app.', () => h('span.badge', 'PWA'), { keys: 'install app pwa home screen' }),
    ]),
    group('Security', security),
  ];
}

function notificationsGroups(s) {
  const ntfy = h('input.input', { value: s.ntfy_topic || '', placeholder: 'a-long-random-topic-name' });
  return [
    group('Where alerts show', [
      row('Desktop notifications on the host', null, () => switchEl(s.desktop_notifications, (v) => save({ desktop_notifications: v })), { keys: 'windows toast notification' }),
      row('Browser notifications on this device', 'Alerts while Scorp is in the background.', () => h('button.btn.small', { onclick: async () => { const r = await Notification.requestPermission(); toast('Permission: ' + r); } }, 'Enable'), { keys: 'browser notification permission' }),
      row('Phone push (ntfy)', 'Install the free ntfy app and subscribe to this topic. Anyone who knows it can read it — make it long and random.', () => h('div.set-inline', ntfy, h('button.btn.small', { onclick: () => save({ ntfy_topic: ntfy.value }) }, 'Save')), { stack: true, keys: 'ntfy phone push' }),
      row('Test', null, () => h('button.btn.small', { onclick: () => api.post('/api/notifications/test').then((r) => toast('Sent via ' + r.channels.join(', '))) }, icon('bell'), 'Send a test'), { keys: 'test notification' }),
    ]),
  ];
}

function integrationsGroups(s) {
  const status = h('span.muted.set-note', '…');
  const paintDiscord = async () => {
    try {
      const d = await api.get('/api/discord');
      const a = d.activity;
      status.textContent = !d.enabled ? 'Off.' : !d.has_id ? 'Add your Application ID.' : d.status === 'no_discord' ? 'Waiting for Discord: ' + (d.detail || 'is it running on the PC?') : d.status === 'showing' && a ? `Showing: ${a.details} · ${a.state}` : d.status === 'idle' ? 'Connected. Nothing shown while Scorp is idle.' : 'Connecting…';
    } catch {
      status.textContent = '';
    }
  };
  paintDiscord();
  const id = h('input.input', { value: s.discord_client_id || '', placeholder: 'Application ID, e.g. 1301234567890123456', inputmode: 'numeric' });
  const saveId = () => {
    const v = id.value.replace(/\D/g, '');
    if (id.value.trim() && !v) return toast('The Application ID is a long number', 'error');
    save({ discord_client_id: v }, v ? 'Application ID saved' : 'Application ID removed').then(() => setTimeout(paintDiscord, 5500));
  };
  id.addEventListener('keydown', (e) => e.key === 'Enter' && saveId());
  return [
    group('Discord', [
      row('Show on Discord', () => status, () => switchEl(s.discord_presence, (v) => save({ discord_presence: v }, v ? 'Discord presence on' : 'Discord presence off').then(() => setTimeout(paintDiscord, 5500))), { keys: 'discord rich presence playing' }),
      row('Show project names', 'Off: only “Chatting” or “Coding with an agent”.', () => switchEl(s.discord_show_project !== false, (v) => save({ discord_show_project: v })), { keys: 'discord project privacy' }),
      row('Application ID', 'discord.com/developers/applications → New Application named “Scorp” → copy its Application ID. In Discord, “Share your detected activities” must be on.', () => h('div.set-inline', id, h('button.btn.small', { onclick: saveId }, 'Save')), { stack: true, keys: 'discord application id' }),
    ]),
    group('More connections', [
      row('Models, programs and MCP', 'Cloud and local models, coding plans, tool permissions and MCP servers.', () => h('button.btn.small', { onclick: () => navigate('/connections') }, icon('plug'), 'Open'), { keys: 'mcp models api keys connections' }),
      row('Styles & plugins', 'Design your own look or add small features to Scorp.', () => h('button.btn.small', { onclick: () => navigate('/studio') }, icon('studio'), 'Studio'), { keys: 'plugin mod extension style theme' }),
    ]),
  ];
}

const SHORTCUTS = [
  ['Ctrl K', 'Search chats and pages, or ask Scorp'], ['Ctrl B', 'All chats and pages'], ['Ctrl Shift O', 'New chat'], ['Ctrl Tab', 'Next tab'],
  ['Ctrl W', 'Close tab (desktop app)'], ['Ctrl Shift Space', 'Write / Voice'], ['Ctrl Shift M', 'Dictate'], ['F11', 'Full screen (desktop app)'],
  ['/', 'Skills in the prompt box'],
];

function aboutGroups() {
  const st = state.status || {};
  const keys = h('div.kbd-grid', SHORTCUTS.map(([k, what]) => h('div.kbd-row', h('span', k.split(' ').map((p) => h('kbd', p))), h('span.muted', what))));
  return [
    group('Keyboard', [row('Shortcuts', null, () => keys, { stack: true, keys: 'keyboard shortcuts hotkeys' })]),
    group('Scorp', [
      row('Version', `${st.host || ''} · workspace ${st.workspace || ''}`, () => h('span.badge', st.version || '—'), { keys: 'version about' }),
      row('Reset this device', 'Forget appearance and layout preferences stored in this browser.', () => h('button.btn.danger.small', { onclick: () => { localStorage.removeItem('hearth.prefs'); location.reload(); } }, icon('refresh'), 'Reset'), { keys: 'reset preferences clear' }),
    ]),
  ];
}

const BUILD = {
  general: generalGroups, appearance: appearanceGroups, scorpi: scorpiGroups, voice: voiceGroups, you: youGroups,
  agent: agentGroups, devices: devicesGroups, notifications: notificationsGroups, integrations: integrationsGroups, about: aboutGroups,
};

// ---------- layout ----------

function renderRow(r, highlight) {
  const text = r.label ? h('div.set-text', h('div.set-label', mark(r.label, highlight)), r.desc ? h('div.set-desc', typeof r.desc === 'function' ? r.desc() : mark(r.desc, highlight)) : null) : null;
  return h('div.set-row' + (r.stack ? '.stack' : '') + (r.bare ? '.bare' : ''), text, h('div.set-control', r.control()));
}

function mark(text, q) {
  if (!q || !text) return text;
  const i = text.toLowerCase().indexOf(q);
  if (i < 0) return text;
  return [text.slice(0, i), h('mark', text.slice(i, i + q.length)), text.slice(i + q.length)];
}

function renderGroups(groups, highlight = '') {
  return groups.filter((g) => g.rows.length).map((g) => h('section.set-group', g.title ? h('h3.set-group-title', g.title) : null, h('div.set-card', g.rows.map((r) => renderRow(r, highlight)))));
}

function searchResults(q) {
  const s = state.settings;
  const words = q.split(/\s+/).filter(Boolean);
  const hits = [];
  for (const cat of CATS) {
    const catHit = cat.label.toLowerCase().includes(q);
    const groups = BUILD[cat.id](s)
      .map((g) => ({ ...g, rows: g.rows.filter((r) => !r.bare && (catHit || words.every((w) => [r.label, typeof r.desc === 'string' ? r.desc : '', r.keys, g.title].join(' ').toLowerCase().includes(w)))) }))
      .filter((g) => g.rows.length);
    if (groups.length) hits.push(h('div.set-hit', h('button.set-hit-cat', { type: 'button', onclick: () => go(cat.id) }, icon(cat.icon), cat.label, icon('right')), ...renderGroups(groups, words[0])));
  }
  return hits.length ? hits : [h('div.set-none', mascotEmpty(), h('b', 'Nothing matches “' + q + '”'), h('span.muted', 'Try a word like “voice”, “hotkey”, “font” or “PIN”.'))];
}

function mascotEmpty() {
  const m = createMascot({ size: 72, mood: 'curious' });
  return m.el;
}

function go(id) {
  query = '';
  current = id;
  history.replaceState({}, '', '/settings#' + id);
  paint();
}

function paint() {
  if (!root || !root.isConnected) return;
  const content = root.querySelector('.set-content');
  const rail = root.querySelector('.set-rail');
  const input = root.querySelector('.set-search input');
  if (!content) return;
  const keepScroll = content.scrollTop;
  clear(content);
  content.classList.toggle('searching', !!query);
  rail.querySelectorAll('[data-cat]').forEach((b) => b.classList.toggle('on', !query && b.dataset.cat === current));
  if (input.value.trim().toLowerCase() !== query) input.value = query;
  if (query) {
    content.append(h('div.set-head', h('div.page-kicker', 'Search'), h('h1.set-title', 'Results')), ...searchResults(query));
  } else {
    const cat = CATS.find((c) => c.id === current) || CATS[0];
    content.append(h('div.set-head', h('div.page-kicker', 'Settings'), h('h1.set-title', cat.label), h('p.set-sub', cat.desc)), ...renderGroups(BUILD[cat.id](state.settings)));
    content.scrollTop = keepScroll;
  }
}

export async function renderSettings(el) {
  root = el;
  clear(root);
  const hash = (location.hash || '').slice(1);
  if (hash === 'looks') return navigate('/studio', { replace: true });
  if (CATS.some((c) => c.id === hash)) current = hash;
  query = '';
  const back = h('button.page-back', { type: 'button', title: 'Back to the conversation', onclick: () => navigate(state.lastChatPath || sessionStorage.getItem('hearth.lastChat') || '/') }, icon('left'), 'Back');
  const input = h('input', { type: 'search', placeholder: 'Search settings', autocomplete: 'off', spellcheck: false, 'aria-label': 'Search settings' });
  input.addEventListener('input', () => { query = input.value.trim().toLowerCase(); paint(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && input.value) { e.stopPropagation(); input.value = ''; query = ''; paint(); }
  });
  const rail = h('nav.set-rail', { 'aria-label': 'Settings' },
    back,
    h('label.set-search', icon('search'), input, h('kbd', '/')),
    h('div.set-cats', CATS.map((c) => h('button.set-cat', { type: 'button', dataset: { cat: c.id }, onclick: () => go(c.id) }, icon(c.icon), h('span', c.label)))),
    h('div.set-rail-foot', h('button.set-cat', { type: 'button', onclick: () => navigate('/studio') }, icon('studio'), h('span', 'Studio'), icon('external', 'set-ext'))),
  );
  root.appendChild(h('div.settings', rail, h('div.set-content')));
  paint();
  // "/" or Ctrl+F jumps to the search while Settings is open
  const key = (e) => {
    if (!root.isConnected || state.view !== 'settings') return removeEventListener('keydown', key);
    const typing = /input|textarea|select/i.test(document.activeElement && document.activeElement.tagName);
    if ((e.key === '/' && !typing) || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f')) {
      e.preventDefault();
      input.focus();
      input.select();
    }
  };
  addEventListener('keydown', key);
  const off = bus.on('looks', () => (root.isConnected && current === 'appearance' && !query ? paint() : !root.isConnected && off()));
}
