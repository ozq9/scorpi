// Settings: appearance, you, voice, devices & security, agent behaviour.

import { api } from '../lib/api.js';
import { clear, copyText, h, icon } from '../lib/dom.js';
import { field, seg, selectEl, switchEl, toast } from '../lib/ui.js';
import { applyAppearance, inkTransition } from '../theme.js';
import { bus, prefs, setPref, state } from '../store.js';
import { speak, audioContext } from '../audio.js';
import { refreshWake, wakeEnabled } from '../wake.js';

let root;

const ACCENTS = [
  ['moss', '#3D6B58'],
  ['clay', '#A35D3D'],
  ['tide', '#365F7E'],
  ['plum', '#6A4B6C'],
  ['ochre', '#8A6A22'],
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

export async function renderSettings(el) {
  root = el;
  clear(root);
  const s = state.settings;
  const st = state.status || {};
  const vs = st.voice || {};
  const inner = h('div.page-inner', h('div.page-head', h('div.ph-text', h('div.page-kicker', 'Settings'), h('h1.page-title', 'Make it ', h('em', 'yours')))));
  root.appendChild(h('div.page', inner));

  // appearance
  const swatches = h('div.swatches', ACCENTS.map(([k, c]) => h('button' + (prefs.accent === k ? '.on' : ''), { title: k, style: { background: c }, onclick: (e) => { setPref('accent', k); inkTransition(applyAppearance); e.currentTarget.parentElement.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === e.currentTarget)); } })));
  inner.appendChild(
    block(
      'Appearance',
      'Stored on this device.',
      line('Theme', 'Paper by day, ink by night — or follow the system.', seg([['auto', 'Auto'], ['light', 'Paper'], ['dark', 'Night']], prefs.theme, (v) => { setPref('theme', v); inkTransition(applyAppearance); })),
      line('Accent', null, swatches),
      line('Text size', null, seg([['small', 'Small'], ['normal', 'Normal'], ['large', 'Large']], prefs.textSize, (v) => { setPref('textSize', v); applyAppearance(); })),
      line('Motion', 'Reduce animations to a minimum.', seg([['full', 'Full'], ['reduced', 'Reduced']], prefs.motion, (v) => { setPref('motion', v); applyAppearance(); })),
      line('Enter sends', 'Otherwise use Ctrl+Enter. (On phones the return key adds a line.)', switchEl(prefs.sendOnEnter, (v) => setPref('sendOnEnter', v))),
    ),
  );

  // you
  const name = h('input.input', { value: s.user_name || '', placeholder: 'What should Hearth call you?' });
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
      line('Read replies aloud', 'Speak every answer as it streams in (this device).', switchEl(prefs.autoSpeak, (v) => setPref('autoSpeak', v))),
      line('Hold to talk', 'In Voice mode, hold Space (or press and hold the voice bar), speak, and let go to send, like a voice message. Off: Hearth listens all the time (this device).', switchEl(prefs.voiceHold !== false, (v) => setPref('voiceHold', v))),
      line('Wake word', 'Say “Hey Hearth” to start talking, or “Hey Ember” to call Ember, while Hearth is open on this device. Only short bursts of speech are checked, on the host (local Whisper when installed).', switchEl(wakeEnabled(), (v) => {
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
      'Open Hearth from any device on your network or tailnet. Phones need the https address for the microphone; the first visit shows a certificate warning (self-signed) — or use `tailscale serve 8000` for a trusted certificate.',
      addrs,
      h('div', { style: { marginTop: '10px' } }, pinBox),
      h('div', { style: { marginTop: '16px' } },
        h('div', { style: { fontWeight: 500, fontSize: '14px' } }, 'Install as an app'),
        h('p.muted', { style: { fontSize: '13px', margin: '4px 0 0' } }, 'Desktop: run “Hearth.bat” for a native window, or use your browser’s “Install app”. iPhone: Share → Add to Home Screen. Android: menu → Install app.')),
    ),
  );
  inner.appendChild(
    block(
      'Notifications',
      'Alerts show in the app and as Windows notifications on the host. For phone push even when Hearth is closed, install the free ntfy app and subscribe to your topic.',
      h('div.row', h('div.grow', field('ntfy topic', ntfy, 'Anyone who knows the topic can read it — make it long and random.')), h('button.btn.small', { onclick: () => save({ ntfy_topic: ntfy.value }) }, 'Save')),
      line('Desktop notifications on the host', null, switchEl(s.desktop_notifications, (v) => save({ desktop_notifications: v }))),
      line('Browser notifications on this device', 'Shows alerts while Hearth is in the background.', h('button.btn.small', { onclick: async () => { const r = await Notification.requestPermission(); toast('Permission: ' + r); } }, 'Enable')),
      h('div.row', { style: { marginTop: '12px' } }, h('button.btn.small', { onclick: () => api.post('/api/notifications/test').then((r) => toast('Sent via ' + r.channels.join(', '))) }, icon('bell'), 'Send a test')),
    ),
  );

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
    h('div.section', h('div.muted', { style: { fontSize: '12.5px', textAlign: 'center' } }, `Hearth ${st.version || ''} · ${st.host || ''} · workspace ${st.workspace || ''}`)),
  );
}
