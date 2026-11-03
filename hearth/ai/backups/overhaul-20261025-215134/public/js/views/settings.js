// Settings: appearance, you, voice, devices & security, agent behaviour.

import { api } from '../lib/api.js';
import { clear, copyText, h, icon } from '../lib/dom.js';
import { field, seg, selectEl, switchEl, toast } from '../lib/ui.js';
import { FONTS, TUNING_KEYS, applyAppearance, inkTransition, refractionSupported } from '../theme.js';
import { looks } from '../lib/looks.js';
import { localVideoInfo, refreshBackdrop, removeVideoFile, setVideoFile } from '../lib/backdrop.js';
import { bus, prefs, setPref, state } from '../store.js';
import { speak, audioContext } from '../audio.js';
import { refreshWake, wakeEnabled } from '../wake.js';
import { navigate } from '../router.js';

let root;

const ACCENTS = [
  ['white', '#FFFFFF'],
  ['ice', '#A8DBFF'],
  ['violet', '#CBB8FF'],
  ['mint', '#93F2CC'],
  ['amber', '#FFD08A'],
];

const LANGS = [
  ['auto', 'Detect automatically'], ['en', 'English'], ['de', 'German'], ['es', 'Spanish'], ['fr', 'French'], ['it', 'Italian'], ['nl', 'Dutch'],
  ['pl', 'Polish'], ['pt', 'Portuguese'], ['tr', 'Turkish'], ['ru', 'Russian'], ['uk', 'Ukrainian'], ['ar', 'Arabic'], ['hi', 'Hindi'],
  ['ja', 'Japanese'], ['ko', 'Korean'], ['zh', 'Chinese'], ['sv', 'Swedish'], ['da', 'Danish'], ['no', 'Norwegian'], ['fi', 'Finnish'], ['cs', 'Czech'],
];

async function save(patch, msg = 'Saved') {
  try {
    state.settings = await api.post('/api/settings', patch);
    bus.emit('settings', state.settings);
    if (msg) toast(msg, { timeout: 1500 });
  } catch (err) {
    toast(err.message, 'error');
  }
}

function block(title, desc, ...content) {
  return h('div.section', h('h3.section-title', title, h('span.rule')), desc ? h('p.section-desc', desc) : null, h('div.panel.panel-pad', ...content));
}

function line(label, sub, control) {
  return h('div.row', { style: { padding: '10px 0', borderBottom: '1px solid var(--line-2)' } }, h('div.grow', h('div', { style: { fontWeight: 500, fontSize: '14px' } }, label), sub ? h('div.muted', { style: { fontSize: '12.5px' } }, sub) : null), control);
}

function range(min, max, value, unit, onChange) {
  const out = h('span.range-val', value + unit);
  const input = h('input.range', { type: 'range', min, max, value, oninput: () => { out.textContent = input.value + unit; onChange(+input.value); } });
  return h('div.row', { style: { gap: '12px', flexWrap: 'nowrap' } }, input, out);
}

// what sits behind the glass: white light on black, plain black, or a looping video
function backdropBlock() {
  const videoRow = h('div');
  const paintVideo = async () => {
    clear(videoRow);
    if ((prefs.bgMode || 'effects') !== 'video') return;
    const local = await localVideoInfo();
    const file = h('input', { type: 'file', accept: 'video/*', hidden: true, onchange: async () => {
      const f = file.files[0];
      if (!f) return;
      try {
        await setVideoFile(f);
        toast('Video set', { body: f.name, timeout: 2000 });
      } catch (err) {
        toast('Could not keep the video', { type: 'error', body: err.message });
      }
      paintVideo();
    } });
    const url = h('input.input', { value: prefs.bgVideoUrl || '', placeholder: 'or paste a video URL (mp4, webm)' });
    const useUrl = () => { setPref('bgVideoUrl', url.value.trim()); refreshBackdrop(); toast(url.value.trim() ? 'Video URL set' : 'Video URL removed', { timeout: 1500 }); };
    url.addEventListener('keydown', (e) => { if (e.key === 'Enter') useUrl(); });
    videoRow.append(
      line('Video', local ? `${local.name} · kept on this device` : 'Pick a video from this device. It loops silently behind the glass.', h('div.row',
        file,
        h('button.btn.small', { onclick: () => file.click() }, icon('upload'), local ? 'Replace' : 'Choose video'),
        local ? h('button.btn.quiet.small', { onclick: async () => { await removeVideoFile(); paintVideo(); } }, 'Remove') : null)),
      h('div.row', { style: { padding: '10px 0', borderBottom: '1px solid var(--line-2)' } }, h('div.grow', url), h('button.btn.small', { onclick: useUrl }, 'Use URL')),
      line('Blur', 'Soften the video so the glass and text stay readable.', range(0, 60, prefs.bgBlur ?? 18, 'px', (v) => setPref('bgBlur', v))),
      line('Brightness', null, range(10, 100, prefs.bgBright ?? 55, '%', (v) => setPref('bgBright', v))),
      line('Light effects over the video', null, switchEl(prefs.bgEffectsOverVideo !== false, (v) => setPref('bgEffectsOverVideo', v))),
    );
  };
  const node = block(
    'Background',
    'What sits behind the glass. Stored on this device.',
    line('Backdrop', 'Black with slow white light that wakes up while Scorp thinks, plain black, or your own looping video.', seg([['effects', 'Light'], ['black', 'Black'], ['video', 'Video']], prefs.bgMode || 'effects', (v) => { setPref('bgMode', v); paintVideo(); })),
    videoRow,
  );
  paintVideo();
  return node;
}

const VIBES = [
  'Make a clean black and white style with small font',
  'Give Scorp a warm paper look with a serif font',
  'Neon cyberpunk: dark, glowing cyan and magenta',
  'Bigger text, square corners and solid panels',
];

// styles Scorp made from a vibe in chat, plus the plugins it added; applied on every device
function looksBlock() {
  const body = h('div');
  const paint = (data) => {
    const active = data.active || '';
    const tile = (s) => {
      const v = (s && s.vars) || {};
      const colors = s ? [v['--bg'] || '#000', v['--ink'] || '#f5f5f7', v['--accent'] || '#fff'] : ['#000', '#f5f5f7', '#fff'];
      const on = (s ? s.id : '') === active;
      return h(
        'div.look-tile' + (on ? '.on' : ''),
        { role: 'button', tabindex: 0, title: s ? s.description || s.name : 'Black liquid glass', onclick: () => api.post('/api/looks/active', { id: s ? s.id : '' }).catch((e) => toast(e.message, 'error')) },
        h('div.lt-preview', { style: { background: colors[0], color: colors[1], fontFamily: v['--font-ui'] || '' } }, h('span', 'Aa'), h('i', { style: { background: colors[2] } })),
        h('div.lt-name', s ? s.name : 'Default', on ? icon('check') : null),
        s ? h('button.icon-btn.lt-del', { title: 'Delete style', onclick: (e) => { e.stopPropagation(); api.del('/api/looks/styles/' + s.id).catch((err) => toast(err.message, 'error')); } }, icon('trash')) : null,
      );
    };
    clear(body);
    body.append(...[
      h('div.look-grid', tile(null), ...data.styles.map(tile)),
      h('div.look-try', h('span.muted', 'Try in chat:'), ...VIBES.map((v) => h('button.chip', { onclick: () => { navigate('/'); setTimeout(() => bus.emit('chat:prefill', v), 120); } }, v))),
      data.plugins.length
        ? h('div', { style: { marginTop: '14px' } }, ...data.plugins.map((p) => line(p.name, (p.description || '') + (p.js ? ' · runs code' : ''), h('div.row', { style: { gap: '8px' } },
            switchEl(p.enabled, (on) => api.post('/api/looks/plugins/' + p.id, { enabled: on }).catch((e) => toast(e.message, 'error'))),
            h('button.icon-btn', { title: 'Delete plugin', onclick: () => api.del('/api/looks/plugins/' + p.id).catch((e) => toast(e.message, 'error')) }, icon('trash'))))))
        : null,
    ].filter(Boolean));
  };
  paint(looks());
  const off = bus.on('looks', (data) => (body.isConnected ? paint(data) : off()));
  const node = block('Styles & plugins', 'Describe a vibe in chat and Scorp designs it, e.g. “make a clean black and white style with small font” or “add a clock in the corner”. Every device switches at once.', body);
  node.id = 'looks';
  return node;
}

// Discord shows "Playing Scorp" with the project, how long, and the model (Rich Presence through the Discord app on the host)
function discordBlock(s) {
  const status = h('div.muted', { style: { fontSize: '12.5px' } }, '…');
  const id = h('input.input', { value: s.discord_client_id || '', placeholder: 'Application ID, e.g. 1301234567890123456', inputmode: 'numeric' });
  const paint = async () => {
    try {
      const d = await api.get('/api/discord');
      const a = d.activity;
      status.textContent = !d.enabled ? 'Off.'
        : !d.has_id ? 'Add your Application ID below.'
        : d.status === 'no_discord' ? 'Waiting for Discord: ' + (d.detail || 'is the Discord app running on the PC?')
        : d.status === 'showing' && a ? `Showing: ${a.details} · ${a.state}`
        : d.status === 'idle' ? 'Connected. Nothing shown while Scorp is idle.' : 'Connecting…';
    } catch {
      status.textContent = '';
    }
  };
  paint();
  const timer = setInterval(() => (status.isConnected ? paint() : clearInterval(timer)), 6000);
  const saveId = () => {
    const v = id.value.replace(/\D/g, '');
    if (id.value.trim() && !v) return toast('The Application ID is a long number', 'error');
    save({ discord_client_id: v }, v ? 'Application ID saved' : 'Application ID removed').then(() => setTimeout(paint, 5500));
  };
  id.addEventListener('keydown', (e) => e.key === 'Enter' && saveId());
  return block(
    'Discord',
    'Show on your Discord profile that you are using Scorp: the project or conversation, for how long, and the model. Runs through the Discord app on the host PC.',
    line('Show on Discord', status, switchEl(s.discord_presence, (v) => save({ discord_presence: v }, v ? 'Discord presence on' : 'Discord presence off').then(() => setTimeout(paint, 5500)))),
    line('Show project names', 'Off: only “Chatting” or “Coding with an agent”.', switchEl(s.discord_show_project !== false, (v) => save({ discord_show_project: v }))),
    h('div.row', { style: { padding: '10px 0', gap: '8px' } }, h('div.grow', id), h('button.btn.small', { onclick: saveId }, 'Save')),
    h('ol.discord-steps',
      h('li', 'Open ', h('a', { href: 'https://discord.com/developers/applications', target: '_blank', rel: 'noopener' }, 'discord.com/developers/applications'), ' → New Application, name it ', h('b', 'Scorp'), ' (Discord shows “Playing Scorp”).'),
      h('li', 'Copy its ', h('b', 'Application ID'), ' into the field above and turn on “Show on Discord”.'),
      h('li', 'Optional: under Rich Presence → Art Assets upload a picture named ', h('code', 'scorp'), ' (and ', h('code', 'code'), ', ', h('code', 'chat'), ' for the small badge).'),
      h('li', 'In Discord: Settings → Activity Privacy → “Share your detected activities” must be on.'),
    ),
  );
}

export async function renderSettings(el) {
  root = el;
  clear(root);
  const s = state.settings;
  const st = state.status || {};
  const vs = st.voice || {};
  const back = h('button.page-back', { type: 'button', title: 'Back to the conversation', onclick: () => navigate(state.lastChatPath || sessionStorage.getItem('hearth.lastChat') || '/') }, icon('left'), 'Back');
  const inner = h('div.page-inner', back, h('div.page-head', h('div.ph-text', h('div.page-kicker', 'Settings'), h('h1.page-title', 'Make it ', h('em', 'yours')))));
  root.appendChild(h('div.page', inner));

  // answers
  inner.appendChild(
    block(
      'Answers',
      null,
      line('Answer style', 'Visual: Scorp shows the answer on a live canvas beside the chat and only says the key point (read aloud when voice is on). Text: classic written answers.', seg([['visual', 'Visual'], ['text', 'Text']], s.response_style || 'visual', (v) => save({ response_style: v }, v === 'visual' ? 'Answers are visual now' : 'Answers are text now'))),
      line('Open the canvas automatically', 'When Scorp changes the canvas, bring it into view.', switchEl(prefs.canvasAuto !== false, (v) => setPref('canvasAuto', v))),
      line('Read replies aloud', 'Speak every answer (this device). With visual answers this is the only text you need.', switchEl(prefs.autoSpeak, (v) => setPref('autoSpeak', v))),
    ),
  );

  // clean chat
  inner.appendChild(
    block(
      'Clean chat',
      'Keep just the conversation on screen. Stored on this device.',
      line('Hide the starter chips', 'No skill buttons and info line under the greeting.', switchEl(prefs.hideStarters !== false, (v) => setPref('hideStarters', v))),
      line('Minimal composer', 'Model, skill and tool chips appear only while you use the composer.', switchEl(!!prefs.minimalComposer, (v) => setPref('minimalComposer', v))),
      line('Hide Scorpi', 'The little pixel scorpion on the greeting, the composer and the floating buddy.', switchEl(!!prefs.hideEmber, (v) => setPref('hideEmber', v))),
    ),
  );

  inner.appendChild(backdropBlock());

  inner.appendChild(looksBlock());

  // appearance: fine-tuning on this device, over whatever style is active
  const swatches = h('div.swatches', ACCENTS.map(([k, c]) => h('button' + ((prefs.accent || 'white') === k ? '.on' : ''), { title: k, style: { background: c }, onclick: (e) => { setPref('accent', k); inkTransition(applyAppearance); e.currentTarget.parentElement.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === e.currentTarget)); } })));
  const tuned = () => TUNING_KEYS.some((k) => prefs[k] != null && prefs[k] !== '' && !(k === 'round' && prefs[k] === 1));
  const resetBtn = h('button.btn.quiet.small', { onclick: () => { TUNING_KEYS.forEach((k) => setPref(k, null)); renderSettings(root); toast('Fine-tuning reset', { timeout: 1500 }); } }, icon('refresh'), 'Reset');
  const appearance = block(
    'Appearance',
    'Fine-tune on this device. Anything you leave untouched follows the active style.',
    line('Accent', 'White by default; a hint of colour if you like.', swatches),
    line('Font', null, Object.assign(selectEl(Object.entries(FONTS).map(([k, [label]]) => [k, label]), prefs.fontFace || 'default', (v) => setPref('fontFace', v === 'default' ? null : v)), { style: 'width: 200px; max-width: 46vw' })),
    line('Text size', null, range(12, 20, prefs.fontPx || 15, 'px', (v) => setPref('fontPx', v === 15 ? null : v))),
    line('Corners', null, seg([['0.35', 'Sharp'], ['0.7', 'Crisp'], ['1', 'Soft'], ['1.4', 'Round']], String(prefs.round ?? 1), (v) => setPref('round', +v === 1 ? null : +v))),
    line('Chat width', null, seg([['narrow', 'Narrow'], ['normal', 'Normal'], ['wide', 'Wide']], prefs.chatWidth || 'normal', (v) => setPref('chatWidth', v === 'normal' ? null : v))),
    line('Glass tint', 'Clear lets the background through; solid is calmer to read.', range(0, 100, prefs.glassTint ?? 35, '%', (v) => setPref('glassTint', v))),
    line('Glass blur', null, range(0, 40, prefs.glassBlur ?? 26, 'px', (v) => setPref('glassBlur', v))),
    refractionSupported ? line('Liquid refraction', 'The glass bends the light behind it. Turn off on slower PCs.', switchEl(prefs.refraction !== false, (v) => setPref('refraction', v))) : null,
    line('Motion', 'Reduce animations to a minimum.', seg([['full', 'Full'], ['reduced', 'Reduced']], prefs.motion, (v) => { setPref('motion', v); applyAppearance(); })),
    line('Enter sends', 'Otherwise use Ctrl+Enter. (On phones the return key adds a line.)', switchEl(prefs.sendOnEnter, (v) => setPref('sendOnEnter', v))),
    tuned() ? h('div.row', { style: { marginTop: '10px' } }, resetBtn) : null,
  );
  inner.appendChild(appearance);

  // you
  const name = h('input.input', { value: s.user_name || '', placeholder: 'What should Scorp call you?' });
  const sys = h('textarea.input', { rows: 4, placeholder: 'Standing instructions, e.g. “Answer in German. Keep it short. I live in Vienna.”' }, s.system_prompt || '');
  inner.appendChild(
    block(
      'You',
      null,
      field('Name', name),
      field('Standing instructions', sys),
      line('Long-term memory', 'Remember preferences and facts across conversations.', switchEl(s.memory_enabled, (v) => save({ memory_enabled: v }))),
      h('div.row', { style: { marginTop: '12px' } }, h('button.btn.primary.small', { onclick: () => save({ user_name: name.value, system_prompt: sys.value }) }, 'Save')),
    ),
  );

  // voice
  const voiceSel = h('div');
  const engines = (st.voice && st.voice.engines) || [];
  const loadVoices = async (engine) => {
    clear(voiceSel);
    if (engine === 'browser') return voiceSel.appendChild(h('div.muted', { style: { fontSize: '13px' } }, 'Each device uses its own built-in voice.'));
    voiceSel.appendChild(h('div.muted', 'Loading voices…'));
    try {
      const data = await api.get('/api/tts/voices?engine=' + engine);
      clear(voiceSel);
      if (!data.voices.length) return voiceSel.appendChild(h('div.muted', { style: { fontSize: '13px' } }, data.error || 'No voices found for this engine.'));
      const key = engine === 'fish' ? 'fish_voice' : 'tts_voice';
      const current = state.settings[key];
      const options = data.voices.map((v) => [v.id, `${v.name}${v.gender ? ' · ' + v.gender : ''}`]);
      if (engine === 'fish') {
        if (current && !options.some(([id]) => id === current)) options.unshift([current, 'Custom voice · ' + current.slice(0, 8)]);
        options.unshift(['', 'Fish default voice']);
      }
      voiceSel.appendChild(field('Voice', selectEl(options, current, (v) => save({ [key]: v }, 'Voice set').then(() => engine === 'fish' && paintFish('fish')))));
    } catch (err) {
      clear(voiceSel);
      voiceSel.appendChild(h('div.muted', err.message));
    }
  };
  // Fish Audio: key, model and any voice from fish.audio by its id (own voices and popular ones are listed above)
  const fishBox = h('div');
  const paintFish = (engine) => {
    clear(fishBox);
    if (engine !== 'fish') return;
    const fishKey = h('input.input', { type: 'password', value: state.settings.fish_api_key || '', placeholder: 'Paste your API key from fish.audio' });
    const saveKey = async () => {
      if (!fishKey.value.trim() || fishKey.value.startsWith('••')) return toast('Paste a Fish Audio API key first', 'warn');
      await save({ fish_api_key: fishKey.value.trim() }, 'Key saved');
      fishKey.value = state.settings.fish_api_key || '';
      loadVoices('fish');
    };
    fishKey.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveKey(); });
    const voiceId = h('input.input', { value: state.settings.fish_voice || '', placeholder: 'e.g. https://fish.audio/m/… or the 32-character id' });
    const saveVoiceId = async () => {
      const m = voiceId.value.match(/[0-9a-f]{32}/i);
      if (voiceId.value.trim() && !m) return toast('That is not a Fish Audio voice id', 'warn');
      await save({ fish_voice: m ? m[0].toLowerCase() : '' }, m ? 'Voice set' : 'Using the default voice');
      voiceId.value = state.settings.fish_voice || '';
      loadVoices('fish');
    };
    voiceId.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveVoiceId(); });
    fishBox.append(
      h('div.row', h('div.grow', field('Fish Audio API key', fishKey, state.settings.fish_api_key_set ? 'A key is saved.' : 'No key saved yet. Create one at fish.audio → API keys.')), h('button.btn.small', { onclick: saveKey }, 'Save key')),
      h('div.grid-2',
        field('Fish model', selectEl([['s2.1-pro', 'S2.1 Pro (best)'], ['s2.1-pro-free', 'S2.1 Pro free tier'], ['s2-pro', 'S2 Pro'], ['s1', 'S1'], ['drama-3', 'Drama 3']], state.settings.fish_model || 's2.1-pro', (v) => save({ fish_model: v }))),
        h('div.row', h('div.grow', field('Voice id or link', voiceId)), h('button.btn.small', { onclick: saveVoiceId }, 'Use')),
      ),
    );
  };
  inner.appendChild(
    block(
      'Voice',
      'Neural voices are free (Microsoft Edge voices, online). The system voice works offline. Speech recognition uses each device\'s own recognizer first (Android, Edge/Windows, Chrome, Safari), then the host.',
      h('div.grid-2',
        field('Speaking engine', selectEl(engines.map((e) => [e.id, e.name + (e.available ? '' : ' (no key yet)')]), s.tts_engine, async (v) => { await save({ tts_engine: v }); paintFish(v); loadVoices(v); })),
        field('Speed', selectEl([['-25', 'Slower'], ['-10', 'Relaxed'], ['0', 'Normal'], ['10', 'Brisk'], ['25', 'Fast']], String(s.tts_rate || 0), (v) => save({ tts_rate: +v }))),
      ),
      voiceSel,
      fishBox,
      h('div.row', { style: { margin: '4px 0 14px' } }, h('button.btn.small', { onclick: () => { audioContext(); speak('Hello. This is how I sound. Calm, clear, and ready when you are.', { onError: (e) => toast('Using the device voice', { body: e.message, type: 'warn' }) }); } }, icon('speaker'), 'Test voice')),
      line('Hold to talk', 'In Voice mode, hold Space (or press and hold the voice bar), speak, and let go to send, like a voice message. Off: Scorp listens all the time (this device).', switchEl(prefs.voiceHold !== false, (v) => setPref('voiceHold', v))),
      line('Wake word', 'Say “Hey Scorp” to start talking, or “Hey Scorpi” to call Scorpi, while Scorp is open on this device. Only short bursts of speech are checked, on the host (local Whisper when installed).', switchEl(wakeEnabled(), (v) => {
        setPref('wakeWord', v);
        audioContext();
        refreshWake();
        if (v && !vs.stt) toast('The host can’t check wake words yet', { type: 'warn', body: 'Install faster-whisper on the host or add a free Groq key (Connections → Model).', timeout: 8000 });
      })),
      h('div.grid-2', { style: { marginTop: '14px' } },
        field('Recognition', selectEl([['auto', 'This device first, then the host'], ['device', 'This device only'], ['host', 'Host first (Whisper)']], s.stt_mode || 'auto', (v) => save({ stt_mode: v })), 'Device recognizers are fast and accurate on Android, Edge (Windows online speech), Chrome and Safari. Others fall back to the host.'),
        field('Host recognition', selectEl([
          ['auto', 'Automatic'],
          ['cloud', 'Free cloud Whisper' + (vs.stt_cloud ? ' (' + vs.stt_cloud + ')' : ' (add a Groq or Mistral key)')],
          ['local', 'Local Whisper' + (vs.stt_local ? '' : ' (not installed)')],
        ], s.stt_host || 'auto', (x) => save({ stt_host: x })), 'Automatic uses cloud Whisper when a Groq/Mistral key is set, else local faster-whisper.'),
        field('Local Whisper model', selectEl([['tiny', 'tiny — fastest'], ['base', 'base — balanced'], ['small', 'small — better'], ['medium', 'medium — accurate'], ['large-v3', 'large-v3 — best (GPU)']], s.stt_model, (x) => save({ stt_model: x })), 'Downloaded on first use.'),
      ),
      field('Spoken language', selectEl(LANGS, s.stt_language, (v) => save({ stt_language: v }))),
    ),
  );
  paintFish(s.tts_engine);
  loadVoices(s.tts_engine);

  // devices & security
  const net = st.network || {};
  const addrs = h('div');
  const add = (label, url) => addrs.appendChild(h('div.addr', h('span.badge', label), h('span.grow', url), h('button.icon-btn', { title: 'Copy', onclick: () => copyText(url).then(() => toast('Copied')) }, icon('copy'))));
  add('this PC', `http://localhost:${net.port}`);
  (net.addresses || []).forEach((a) => {
    add(a.kind, `http://${a.ip}:${net.port}`);
    if (net.https_port) add(a.kind + ' · https', `https://${a.ip}:${net.https_port}`);
  });
  const pinBox = h('div');
  if (st.local) {
    pinBox.append(
      line('Pairing PIN', 'Other devices enter this once.', h('span.pin-display', s.pin || '••••••')),
      line('New PIN', 'Signs out every paired device.', h('button.btn.small', { onclick: async () => { const r = await api.post('/api/auth/rotate'); s.pin = r.pin; renderSettings(root); toast('New PIN: ' + r.pin); } }, icon('retry'), 'Rotate')),
      line('Require PIN on the network', 'Turn off only on a network you fully trust.', switchEl(s.require_pin, (v) => save({ require_pin: v }))),
    );
  } else {
    pinBox.append(h('div.muted', { style: { fontSize: '13px', padding: '10px 0' } }, 'Security settings are managed on the host PC.'), h('button.btn.small', { onclick: async () => { await api.post('/api/auth/logout'); localStorage.removeItem('hearth.token'); location.reload(); } }, icon('lock'), 'Sign out this device'));
  }
  const ntfy = h('input.input', { value: s.ntfy_topic || '', placeholder: 'a-long-random-topic-name' });
  inner.appendChild(
    block(
      'Devices',
      'Open Scorp from any device on your network or tailnet. Phones need the https address for the microphone; the first visit shows a certificate warning (self-signed) — or use `tailscale serve 8000` for a trusted certificate.',
      addrs,
      h('div', { style: { marginTop: '10px' } }, pinBox),
      h('div', { style: { marginTop: '16px' } },
        h('div', { style: { fontWeight: 500, fontSize: '14px' } }, 'Install as an app'),
        h('p.muted', { style: { fontSize: '13px', margin: '4px 0 0' } }, 'Desktop: run “Hearth.bat” (or the Scorp shortcut) for a native window, or use your browser’s “Install app”. iPhone: Share → Add to Home Screen. Android: menu → Install app.')),
    ),
  );
  inner.appendChild(
    block(
      'Notifications',
      'Alerts show in the app and as Windows notifications on the host. For phone push even when Scorp is closed, install the free ntfy app and subscribe to your topic.',
      h('div.row', h('div.grow', field('ntfy topic', ntfy, 'Anyone who knows the topic can read it — make it long and random.')), h('button.btn.small', { onclick: () => save({ ntfy_topic: ntfy.value }) }, 'Save')),
      line('Desktop notifications on the host', null, switchEl(s.desktop_notifications, (v) => save({ desktop_notifications: v }))),
      line('Browser notifications on this device', 'Shows alerts while Scorp is in the background.', h('button.btn.small', { onclick: async () => { const r = await Notification.requestPermission(); toast('Permission: ' + r); } }, 'Enable')),
      h('div.row', { style: { marginTop: '12px' } }, h('button.btn.small', { onclick: () => api.post('/api/notifications/test').then((r) => toast('Sent via ' + r.channels.join(', '))) }, icon('bell'), 'Send a test')),
    ),
  );

  inner.appendChild(discordBlock(s));

  // agent
  inner.appendChild(
    block(
      'Agent',
      null,
      line('Creativity', 'Lower is more precise, higher more inventive.', selectEl([['0.2', 'Precise'], ['0.4', 'Focused'], ['0.6', 'Balanced'], ['0.8', 'Creative'], ['1.0', 'Playful']], String(s.temperature), (v) => save({ temperature: +v }))),
      line('Maximum steps per answer', 'How many tool rounds a single request may take.', selectEl([['8', '8'], ['16', '16'], ['24', '24'], ['40', '40'], ['60', '60']], String(s.max_steps), (v) => save({ max_steps: +v }))),
    ),
  );

  inner.appendChild(
    h('div.section', h('div.muted', { style: { fontSize: '12.5px', textAlign: 'center' } }, `Scorp ${st.version || ''} · ${st.host || ''} · workspace ${st.workspace || ''}`)),
  );
  if (location.hash === '#looks') setTimeout(() => { const n = root.querySelector('#looks'); if (n) n.scrollIntoView({ block: 'start' }); }, 300);
}
