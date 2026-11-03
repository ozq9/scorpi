// Connections: model provider, programs on the PC, tools & permissions, MCP servers and skills.

import { api, streamJSON } from '../lib/api.js';
import { clear, fmtSize, h, icon } from '../lib/dom.js';
import { confirmSheet, emptyState, field, seg, selectEl, sheet, switchEl, toast } from '../lib/ui.js';
import { bus, state } from '../store.js';

let root;
let tab = 'model';
const TABS = [
  ['model', 'Model'],
  ['programs', 'Programs & services'],
  ['tools', 'Tools & permissions'],
  ['github', 'GitHub & git'],
  ['mcp', 'MCP servers'],
  ['skills', 'Skills'],
];

export function renderConnections(el, hash) {
  root = el;
  if (hash && TABS.some(([k]) => k === hash)) tab = hash;
  clear(root);
  const body = h('div');
  root.appendChild(
    h(
      'div.page',
      h(
        'div.page-inner',
        h('div.page-head', h('div.ph-text', h('div.page-kicker', 'Connections'), h('h1.page-title', 'How Scorpi ', h('em', 'reaches out')), h('p.page-sub', 'Choose local or cloud models, connect programs running on this PC, decide which actions need your approval, and plug in MCP servers.'))),
        h('div.tabs', TABS.map(([k, label]) => h('button' + (k === tab ? '.on' : ''), { onclick: () => { tab = k; history.replaceState({}, '', '/connections#' + k); renderConnections(el); } }, label))),
        body,
      ),
    ),
  );
  ({ model, programs, tools, github, mcp, skills })[tab](body);
}

async function saveSettings(patch, msg = 'Saved') {
  try {
    state.settings = await api.post('/api/settings', patch);
    bus.emit('settings', state.settings);
    if (msg) toast(msg, { timeout: 1600 });
  } catch (err) {
    toast(err.message, 'error');
  }
}

// ---------- model ----------

const cloudLabel = (m) => `${m.provider_name} · ${m.id}`;

async function model(body) {
  const s = state.settings;
  const status = h('div.panel.panel-pad', { style: { marginBottom: '18px' } });
  const list = h('div.panel');
  const cloudBox = h('div.cards-grid');
  const codingBox = h('div.cards-grid');
  const pullName = h('input.input', { placeholder: 'e.g. qwen3:8b, llama3.1:8b, qwen2.5vl:7b, nomic-embed-text' });
  const progress = h('div.muted', { style: { fontSize: '12.5px', fontFamily: 'var(--font-mono)', marginTop: '8px' } });
  const ollamaUrl = h('input.input', { value: s.ollama_url || '' });
  const openaiUrl = h('input.input', { value: s.openai_url || '', placeholder: 'http://127.0.0.1:1234/v1 (LM Studio) or https://api.groq.com/openai/v1' });
  const openaiKey = h('input.input', { type: 'password', value: s.openai_key || '', placeholder: 'API key (if needed)' });
  const providerBox = h('div');

  const renderProvider = () => {
    clear(providerBox);
    if (state.settings.provider === 'openai') {
      providerBox.append(field('Endpoint', openaiUrl, 'Any OpenAI-compatible server, e.g. LM Studio or llama.cpp. For free online models use the cloud section below instead.'), field('API key', openaiKey));
    } else {
      providerBox.append(field('Ollama URL', ollamaUrl, 'Optional. Ollama runs models on this PC for free and offline. Without it, the free cloud models below are used.'));
    }
  };

  const toggleRow = (title, desc, on, key) =>
    h('div.row', { style: { marginBottom: '10px' } }, h('div.grow', h('div', { style: { fontWeight: 500 } }, title), h('div.muted', { style: { fontSize: '13px' } }, desc)), switchEl(on, (v) => saveSettings({ [key]: v }).then(() => key === 'cloud_anon' && refreshAll())));

  body.append(
    h(
      'div.section',
      h('h3.section-title', 'Coding plans & API keys', h('span.rule'), h('button.btn.quiet.small', { onclick: () => refreshAll(true) }, icon('retry'), 'Refresh')),
      h('p.section-desc', 'Use a coding subscription such as OpenCode Go, or your own API key. These are preferred over free tiers. Pick one as the coding model below to run the Code skill and coder sub-agents on it.'),
      h(
        'div.panel.panel-pad',
        { style: { marginBottom: '14px' } },
        toggleRow('Switch models automatically', 'When a model is rate-limited, out of quota, removed or offline (or Ollama is not running), continue with the next available one.', s.cloud_fallback, 'cloud_fallback'),
        toggleRow('Pollinations without a key', 'Last-resort model that needs no signup at all. Slow: about one request every 30 seconds.', s.cloud_anon, 'cloud_anon'),
      ),
      codingBox,
    ),
    h(
      'div.section',
      h('h3.section-title', 'Free cloud models', h('span.rule')),
      h('p.section-desc', 'Run Scorpi fully without local models. Each service below gives a free API key: paste it and its models (including vision models) appear in every model picker. Keys stay on this PC. Messages go to the service you pick.'),
      cloudBox,
    ),
    h(
      'div.section',
      h('h3.section-title', 'Local provider', h('span.rule')),
      h(
        'div.panel.panel-pad',
        h('div.row', { style: { marginBottom: '14px' } }, seg([['ollama', 'Ollama (local)'], ['openai', 'OpenAI-compatible']], s.provider, async (v) => { await saveSettings({ provider: v }, ''); renderProvider(); loadModels(); })),
        providerBox,
        h('div.row', h('button.btn.primary.small', { onclick: async () => { await saveSettings({ ollama_url: ollamaUrl.value, openai_url: openaiUrl.value, openai_key: openaiKey.value }); loadModels(); } }, 'Save & reconnect')),
      ),
    ),
    h('div.section', h('h3.section-title', 'Models', h('span.rule')), status, list),
    h(
      'div.section',
      h('h3.section-title', 'Pull a local model', h('span.rule')),
      h('p.section-desc', 'Good free picks: qwen3:8b (smart, tools), llama3.1:8b (tools), qwen2.5vl:7b (sees images & screens), gemma3:12b (vision), nomic-embed-text (knowledge search).'),
      h('form.row', { onsubmit: (e) => { e.preventDefault(); pull(); } }, h('div.grow', pullName), h('button.btn.primary', { type: 'submit' }, icon('download'), 'Pull')),
      progress,
    ),
  );
  renderProvider();

  function refreshAll(force) {
    loadCloud(force).then(() => loadModels());
  }

  async function loadCloud(force) {
    for (const box of [codingBox, cloudBox]) {
      clear(box);
      box.appendChild(h('div.muted', 'Checking providers…'));
    }
    let data;
    try {
      data = await api.get('/api/cloud' + (force ? '?refresh=1' : ''));
    } catch (err) {
      clear(codingBox);
      clear(cloudBox);
      cloudBox.appendChild(h('div.muted', err.message));
      return;
    }
    clear(codingBox);
    clear(cloudBox);
    data.providers.forEach((p, i) => (p.category === 'coding' ? codingBox : cloudBox).appendChild(providerTile(p, i)));
  }

  function providerTile(p, i) {
    const keyName = 'key_' + p.id;
    const coding = p.category === 'coding';
    const key = h('input.input', { type: 'password', value: state.settings[keyName] || '', placeholder: p.keyless ? 'Optional free key' : coding ? 'Paste API key' : 'Paste free API key', autocomplete: 'off' });
    const url = h('input.input', { value: (state.settings.cloud_urls || {})[p.id] || '', placeholder: p.default_url });
    const extra = h('input.input', { value: p.extra || '', placeholder: 'e.g. glm-5.2, kimi-k2.6' });
    const saveAdvanced = async () => {
      await saveSettings({
        cloud_urls: { ...(state.settings.cloud_urls || {}), [p.id]: url.value.trim() },
        cloud_extra_models: { ...(state.settings.cloud_extra_models || {}), [p.id]: extra.value.trim() },
      });
      refreshAll(true);
    };
    const badge = p.error
      ? h('span.badge.err', 'error')
      : p.enabled
        ? h('span.badge.ok', p.models.length + ' model' + (p.models.length === 1 ? '' : 's'))
        : h('span.badge', p.keyless ? 'off' : 'add key');
    const picks = p.models.filter((m) => m.recommended.length);
    const saveKey = async (value) => {
      await saveSettings({ [keyName]: value }, value ? p.name + ' connected' : p.name + ' key removed');
      refreshAll();
    };
    return h(
      'div.tile',
      { style: { animationDelay: i * 30 + 'ms' } },
      h('div.t-kicker', icon(coding ? 'code' : 'globe'), p.keyless ? 'no signup needed' : coding ? 'plan / API key' : 'free key'),
      h('div.t-name', p.name),
      h('div.row', { style: { gap: '6px', flexWrap: 'wrap', margin: '2px 0 8px' } }, badge, p.stt ? h('span.badge', 'speech to text') : null),
      h('div.t-desc', p.about),
      p.error ? h('div.hint', { style: { color: 'var(--danger)', fontSize: '12px', marginTop: '8px', wordBreak: 'break-word' } }, p.error) : null,
      picks.length
        ? h('div', { style: { marginTop: '12px', display: 'grid', gap: '8px' } }, picks.map((m) => h('div', { style: { fontSize: '12.5px', minWidth: 0 } }, h('div.mono', { style: { wordBreak: 'break-all' } }, m.id), h('div.row', { style: { gap: '4px', marginTop: '3px' } }, m.recommended.map((r) => h('span.badge' + (r === 'vision' ? '' : '.ok'), r === 'chat' ? 'best chat' : 'best vision'))))))
        : null,
      p.models.length > picks.length
        ? h(
            'details',
            { style: { marginTop: '10px', fontSize: '12.5px' } },
            h('summary', { style: { cursor: 'pointer', color: 'var(--muted)' } }, `All ${p.models.length} models`),
            h(
              'div',
              { style: { maxHeight: '220px', overflow: 'auto', marginTop: '6px', display: 'grid', gap: '4px' } },
              p.models.map((m) => h('div.mono', { style: { wordBreak: 'break-all' } }, m.id, m.caps.includes('vision') ? h('span.badge', { style: { marginLeft: '6px' } }, 'vision') : null)),
            ),
          )
        : null,
      h('form.row', { style: { marginTop: '12px' }, onsubmit: (e) => { e.preventDefault(); saveKey(key.value.trim()); } }, h('div.grow', key), h('button.btn.small.primary', { type: 'submit' }, 'Save')),
      h(
        'div.t-foot',
        h('a.btn.small.quiet', { href: p.signup, target: '_blank', rel: 'noopener noreferrer' }, icon('external'), coding ? 'Get a key' : 'Get a free key'),
        p.has_key ? h('button.btn.small.quiet', { type: 'button', onclick: () => saveKey('') }, icon('trash'), 'Remove key') : null,
      ),
      p.keyless && !p.has_key
        ? null
        : h(
            'details',
            { style: { marginTop: '10px', fontSize: '13px' }, open: !!((state.settings.cloud_urls || {})[p.id] || p.extra) },
            h('summary', { style: { cursor: 'pointer', color: 'var(--muted)' } }, 'Endpoint & model IDs'),
            h(
              'form',
              { style: { marginTop: '10px' }, onsubmit: (e) => { e.preventDefault(); saveAdvanced(); } },
              field('Base URL', url, 'Leave empty for the default. Change it if the service moved its API.'),
              field('Extra model IDs', extra, 'Comma-separated. Use when a model is missing from the list.'),
              h('button.btn.small', { type: 'submit' }, 'Save'),
            ),
          ),
    );
  }

  async function loadModels() {
    clear(status);
    clear(list);
    let data;
    try {
      data = await api.get('/api/models');
    } catch (err) {
      status.appendChild(h('div', err.message));
      return;
    }
    const all = data.models || [];
    const models = all.filter((m) => !m.cloud);
    const cloud = all.filter((m) => m.cloud);
    if (data.error || !all.length) {
      status.appendChild(h('div.row', { style: { marginBottom: '12px' } }, h('span.badge.err', 'offline'), h('span', data.error ? 'No model reachable: ' + data.error : 'No models yet. Add a free cloud key above or pull a local model below.')));
    }
    const names = models.map((m) => m.name);
    const chat = names.filter((n) => !/embed/i.test(n));
    const cloudChat = cloud.map((m) => [m.name, cloudLabel(m) + (m.recommended.includes('chat') ? ' ★' : '')]);
    const cloudVision = cloud.filter((m) => m.caps.includes('vision')).map((m) => [m.name, cloudLabel(m) + (m.recommended.includes('vision') ? ' ★' : '')]);
    const embedCloud = [];
    try {
      const providers = (await api.get('/api/cloud')).providers;
      providers.filter((p) => p.has_key && p.embed).forEach((p) => embedCloud.push(['@' + p.id + '/' + p.embed, p.name + ' · ' + p.embed]));
    } catch {
      /* ignore */
    }
    const withCurrent = (opts, value) => (value && !opts.some(([v]) => v === value) ? [...opts, [value, value + ' (unavailable)']] : opts);
    const embedOpts = withCurrent([...names.map((n) => [n, n]), ...embedCloud], s.embed_model);
    status.appendChild(
      h(
        'div.grid-2',
        field('Chat model', selectEl(withCurrent([['', 'Automatic (best available)'], ...chat.map((n) => [n, n]), ...cloudChat], s.model), s.model, (v) => saveSettings({ model: v })), '★ = recommended for that service.'),
        field('Coding model', selectEl(withCurrent([['', 'Same as the chat model'], ...chat.map((n) => [n, n]), ...cloudChat], s.code_model), s.code_model, (v) => saveSettings({ code_model: v })), 'Used by the Code skill and coder sub-agents. Any API model works, e.g. OpenCode Go, NVIDIA or OpenRouter.'),
        field('Coding engine', selectEl([['auto', 'Full (recommended)'], ['scorpi', 'Basic']], s.coder_backend || 'auto', (v) => saveSettings({ coder_backend: v })), s.code_engine ? 'The Coder edits precisely, searches and checks code, and runs on your coding plan or any API key added here.' : 'Basic tools for now. For the full engine install the free OpenCode CLI: npm i -g @opencode/cli'),
        field('Vision model', selectEl(withCurrent([['', 'Automatic (any vision model)'], ...chat.map((n) => [n, n]), ...cloudVision], s.vision_model), s.vision_model, (v) => saveSettings({ vision_model: v })), 'Looks at images and screenshots when the chat model is text-only.'),
        field('Embedding model', selectEl(embedOpts.length ? embedOpts : [[s.embed_model, s.embed_model]], s.embed_model, (v) => saveSettings({ embed_model: v })), 'For knowledge search (RAG). Re-add documents after changing it.'),
        field('Context length', selectEl([['4096', '4k'], ['8192', '8k'], ['16384', '16k (default)'], ['32768', '32k'], ['65536', '64k']], String(s.num_ctx), (v) => saveSettings({ num_ctx: +v })), 'Local models only. Larger helps long tasks; uses more memory.'),
      ),
    );
    if (!models.length) list.appendChild(h('div.list-row', h('span.f-ic', icon('cpu')), h('div.lr-main', h('div.lr-title', 'No local models'), h('div.lr-sub', cloud.length ? `${cloud.length} cloud models available above.` : 'Optional: install Ollama to run models offline.'))));
    models.forEach((m) => {
      const caps = h('span.row', { style: { gap: '4px' } });
      api.get('/api/models/caps?model=' + encodeURIComponent(m.name)).then((c) => c.caps.filter((x) => ['tools', 'vision', 'thinking', 'embedding'].includes(x)).forEach((x) => caps.appendChild(h('span.badge' + (x === 'tools' ? '.ok' : ''), x)))).catch(() => {});
      list.appendChild(
        h(
          'div.list-row',
          h('span.f-ic', icon('cpu')),
          h('div.lr-main', h('div.lr-title', m.name), h('div.lr-sub', [m.details && m.details.parameter_size, m.details && m.details.quantization_level, m.size ? fmtSize(m.size) : ''].filter(Boolean).join(' · '))),
          caps,
          state.settings.provider === 'ollama'
            ? h('div.lr-actions', h('button.icon-btn', { title: 'Delete model', onclick: async () => { if (await confirmSheet('Delete ' + m.name + '?', 'The model files are removed from Ollama.', { confirm: 'Delete', danger: true })) { await api.post('/api/models/delete', { name: m.name }); loadModels(); } } }, icon('trash')))
            : null,
        ),
      );
    });
  }

  async function pull() {
    const name = pullName.value.trim();
    if (!name) return;
    progress.textContent = 'Starting…';
    try {
      await streamJSON('/api/models/pull', {
        body: { name },
        onEvent: (ev) => {
          if (ev.error) throw new Error(ev.error);
          const pct = ev.total ? ` ${Math.round((ev.completed / ev.total) * 100)}%` : '';
          progress.textContent = (ev.status || '') + pct;
        },
      });
      progress.textContent = 'Done — ' + name + ' is ready.';
      toast('Model ready', { body: name });
      loadModels();
    } catch (err) {
      progress.textContent = 'Failed: ' + err.message;
    }
  }

  refreshAll();
}

// ---------- programs ----------

async function programs(body) {
  const s = state.settings;
  const found = h('div.cards-grid');
  const haUrl = h('input.input', { value: s.ha_url || '', placeholder: 'http://homeassistant.local:8123' });
  const haToken = h('input.input', { type: 'password', value: s.ha_token || '', placeholder: 'Long-lived access token' });
  const sdUrl = h('input.input', { value: s.sd_url || '' });
  body.append(
    h('div.section', h('h3.section-title', 'Detected on this PC', h('span.rule'), h('button.btn.quiet.small', { onclick: () => scan() }, icon('retry'), 'Rescan')), h('p.section-desc', 'Scorpi has full access to the host, so anything running here — local AI servers, smart-home hubs, media servers, databases, dev servers — can be reached with the http and system tools. Ask: “what’s playing on Jellyfin?” or “turn off the living-room lights”.'), found),
    h(
      'div.section',
      h('h3.section-title', 'Integrations', h('span.rule')),
      h(
        'div.panel.panel-pad',
        h('div.grid-2', field('Home Assistant URL', haUrl), field('Home Assistant token', haToken, 'Profile → Security → Long-lived access tokens.')),
        field('Stable Diffusion (AUTOMATIC1111/Forge) URL', sdUrl, 'When it runs with --api, image generation stays fully local. Otherwise the free Pollinations service is used.'),
        h('div.row', h('button.btn.primary.small', { onclick: () => saveSettings({ ha_url: haUrl.value, ha_token: haToken.value, sd_url: sdUrl.value }) }, 'Save')),
      ),
    ),
    h(
      'div.section',
      h('h3.section-title', 'What Scorpi can operate', h('span.rule')),
      h(
        'div.cards-grid',
        [
          ['monitor', 'Screen, mouse & keyboard', 'Sees the screen with numbered controls and clicks, types and scrolls like a person. A vision model takes over automatically when the screen needs looking at.'],
          ['globe', 'A real browser', 'Drives Edge/Chrome with your logins via Playwright: navigate, fill forms, read pages.'],
          ['terminal', 'Shell & code', 'PowerShell, Python and Node.js — anything scriptable on Windows.'],
          ['cpu', 'Programs & processes', 'Start installed apps, close them, check CPU/RAM, control media playback.'],
          ['folder', 'Every drive', 'Read, write, move and search files anywhere you allow in Permissions.'],
          ['link', 'Any API', 'Call REST APIs on your network or the internet, plus MCP servers.'],
        ].map(([ic, name, desc]) => h('div.tile', h('div.t-kicker', icon(ic)), h('div.t-name', name), h('div.t-desc', desc))),
      ),
    ),
  );
  async function scan() {
    clear(found);
    found.appendChild(h('div.muted', 'Scanning local ports…'));
    const data = await api.get('/api/services');
    clear(found);
    if (!data.services.length) found.appendChild(h('div.muted', 'No known services found. Ollama, LM Studio, ComfyUI, Home Assistant, Jellyfin, Plex, databases and dev servers are recognized automatically.'));
    data.services.forEach((svc, i) =>
      found.appendChild(h('div.tile', { style: { animationDelay: i * 40 + 'ms' } }, h('div.t-kicker', h('span.badge.ok', 'running'), svc.kind), h('div.t-name', svc.name), h('div.t-desc.mono', '127.0.0.1:' + svc.port))),
    );
  }
  scan();
}

// ---------- tools & permissions ----------

async function tools(body) {
  const data = await api.get('/api/tools');
  const s = state.settings;
  const enabled = new Set(data.enabled_groups);
  const pathsAllowed = h('textarea.input', { rows: 3, placeholder: 'Empty = the whole PC. One folder per line.' }, (s.allowed_paths || []).join('\n'));
  const pathsBlocked = h('textarea.input', { rows: 3, placeholder: 'e.g. C:\\Users\\you\\Documents\\Private' }, (s.blocked_paths || []).join('\n'));
  body.append(
    h(
      'div.section',
      h('h3.section-title', 'Approvals', h('span.rule')),
      h('p.section-desc', 'Allow runs immediately. Ask pauses the agent and shows an approval card on every device. Off hides the tool. Reading is allowed by default; running code, controlling the PC, deleting and sending data ask first.'),
      h('div.panel.panel-pad', h('div.row', h('div.grow', h('div', { style: { fontWeight: 500 } }, 'Tool selection'), h('div.muted', { style: { fontSize: '13px' } }, 'Automatic sends only the tool groups a request needs (better for small models); the agent can load more itself.')), seg([['auto', 'Automatic'], ['all', 'All enabled']], data.mode, (v) => saveSettings({ tool_mode: v })))),
    ),
  );
  const groupsBox = h('div');
  body.appendChild(groupsBox);
  for (const g of data.groups) {
    const items = data.tools.filter((t) => t.group === g.id);
    if (!items.length) continue;
    const box = h('div.panel.tool-group');
    const head = h(
      'div.tool-group-head',
      icon('right', 'chev'),
      h('div.grow', { style: { flex: 1 } }, h('div.tg-name', g.name, ' ', h('span.badge', items.length)), h('div.tg-desc', g.description)),
      g.id === 'core' ? h('span.badge', 'always on') : h('span', { onclick: (e) => e.stopPropagation() }, switchEl(enabled.has(g.id), (on) => { on ? enabled.add(g.id) : enabled.delete(g.id); saveSettings({ tool_groups: [...enabled] }, on ? g.name + ' enabled' : g.name + ' disabled'); })),
    );
    head.addEventListener('click', () => box.classList.toggle('open'));
    const tb = h('div.tg-body', items.map(toolRow));
    box.append(head, tb);
    groupsBox.appendChild(box);
  }
  const mcpTools = data.tools.filter((t) => t.group.startsWith('mcp:'));
  if (mcpTools.length) {
    const box = h('div.panel.tool-group', h('div.tool-group-head', { onclick: () => box.classList.toggle('open') }, icon('right', 'chev'), h('div', { style: { flex: 1 } }, h('div.tg-name', 'MCP tools ', h('span.badge', mcpTools.length)), h('div.tg-desc', 'Provided by connected MCP servers.'))), h('div.tg-body', mcpTools.map(toolRow)));
    groupsBox.appendChild(box);
  }
  body.appendChild(
    h(
      'div.section',
      { style: { marginTop: '28px' } },
      h('h3.section-title', 'Data access', h('span.rule')),
      h(
        'div.panel.panel-pad',
        h('div.grid-2', field('Allowed folders', pathsAllowed), field('Blocked folders', pathsBlocked)),
        state.status && state.status.local
          ? h('button.btn.primary.small', { onclick: () => saveSettings({ allowed_paths: lines(pathsAllowed.value), blocked_paths: lines(pathsBlocked.value) }) }, 'Save folder rules')
          : h('div.muted', { style: { fontSize: '13px' } }, 'Folder rules can only be changed on the host PC itself.'),
      ),
    ),
  );
}

const lines = (v) => v.split('\n').map((x) => x.trim()).filter(Boolean);

function toolRow(t) {
  const risk = { read: ['reads', ''], write: ['changes data', '.warn'], danger: ['acts on the PC', '.err'] }[t.risk];
  return h(
    'div.tool-row' + (t.available ? '' : '.unavailable'),
    h('div.tr-main', h('div.row', { style: { gap: '8px' } }, h('span.tr-name', t.name), h('span.badge' + risk[1], risk[0])), h('div.tr-desc', t.available ? t.description : 'Unavailable — install with: ' + t.hint)),
    seg([['allow', 'Allow'], ['ask', 'Ask'], ['off', 'Off']], t.policy, (v) => api.post('/api/tools/policy', { name: t.name, policy: v }).then(() => toast(`${t.name}: ${v}`, { timeout: 1400 })), 'danger-on'),
  );
}

// ---------- MCP ----------

const SCOPES = [['both', 'Both'], ['chat', 'Chat'], ['code', 'Code']];
const SCOPE_HINT = { both: 'chat and coding', chat: 'chat only', code: 'coding only' };
const CATEGORY_ICON = { '3D & design': 'cube', Coding: 'code', Data: 'table', Web: 'globe', Research: 'compass', Files: 'folder', Thinking: 'spark' };

// ---------- GitHub & git ----------

async function github(body) {
  const box = h('div', h('div.panel.panel-pad.muted', 'Loading…'));
  body.append(box);
  let gh;
  try {
    gh = await api.get('/api/github');
  } catch (err) {
    box.replaceChildren(h('div.panel', emptyState('GitHub unavailable', err.message)));
    return;
  }
  const again = () => {
    clear(body);
    github(body);
  };
  const busy = async (btn, fn) => {
    btn.disabled = true;
    try {
      return await fn();
    } catch (err) {
      toast(err.message, 'error');
      return null;
    } finally {
      btn.disabled = false;
    }
  };
  let account;
  if (gh.connected && gh.user) {
    account = h('div.panel.panel-pad', h('div.gh-account',
      gh.user.avatar ? h('img', { src: gh.user.avatar, alt: '' }) : null,
      h('div.grow', h('b', gh.user.name), h('span', '@' + gh.user.login + (gh.user.scopes ? ' · ' + gh.user.scopes : ''))),
      h('a.btn.quiet.small', { href: gh.user.url, target: '_blank', rel: 'noopener' }, icon('external'), 'Profile'),
      h('button.btn.danger.small', { onclick: async (e) => (await busy(e.currentTarget, () => api.del('/api/github'))) && (toast('GitHub disconnected'), again()) }, 'Disconnect')));
  } else {
    const token = h('input.input', { type: 'password', placeholder: 'ghp_… or github_pat_…', autocomplete: 'off' });
    const connectToken = async (e) => {
      if (!token.value.trim()) return token.focus();
      if (await busy(e.currentTarget, () => api.post('/api/github/connect', { token: token.value.trim() }))) {
        toast('GitHub connected');
        again();
      }
    };
    token.addEventListener('keydown', (e) => e.key === 'Enter' && connectToken({ currentTarget: e.target }));
    account = h('div.cards-grid',
      h('div.panel.panel-pad',
        h('div', { style: { fontWeight: 500, marginBottom: '6px' } }, 'Sign in with the browser'),
        h('p.muted', { style: { fontSize: '13px', margin: '0 0 12px' } }, gh.cli
          ? 'Uses the GitHub CLI on this PC: a window opens, you confirm in the browser, then Scorpi takes over that login.'
          : 'Needs the GitHub CLI (free, from cli.github.com). Install it, then come back here.'),
        gh.cli
          ? h('div.row',
              h('button.btn.small', { onclick: (e) => busy(e.currentTarget, () => api.post('/api/github/login')).then((r) => r && toast('A sign-in window opened', { body: 'Finish in the browser, then click "Use that login".', timeout: 9000 })) }, icon('external'), 'Sign in'),
              h('button.btn.small.primary', { onclick: async (e) => (await busy(e.currentTarget, () => api.post('/api/github/connect', { cli: true }))) && (toast('GitHub connected'), again()) }, 'Use that login'))
          : h('a.btn.small', { href: 'https://cli.github.com', target: '_blank', rel: 'noopener' }, icon('external'), 'Get the GitHub CLI')),
      h('div.panel.panel-pad',
        h('div', { style: { fontWeight: 500, marginBottom: '6px' } }, 'Or paste a token'),
        h('p.muted', { style: { fontSize: '13px', margin: '0 0 12px' } }, 'A personal access token with the repo scope (workflow too, to change GitHub Actions). ',
          h('a', { href: gh.token_url, target: '_blank', rel: 'noopener' }, 'Create one'), '. It stays on this PC.'),
        h('div.row', h('div.grow', token), h('button.btn.small.primary', { onclick: connectToken }, 'Connect'))));
  }
  box.replaceChildren(
    h('div.section',
      h('h3.section-title', 'GitHub account', h('span.rule')),
      h('p.section-desc', 'Clone your repositories as projects, publish projects, push and pull, open pull requests with a description written from the changes, see CI checks, and hand issues to the coder. The agents can read issues and open pull requests too.'),
      gh.error ? h('div.panel.panel-pad', { style: { color: 'var(--danger)', marginBottom: '12px' } }, gh.error) : null,
      account),
    h('div.section',
      h('h3.section-title', 'Checkpoints', h('span.rule')),
      h('div.panel.panel-pad', h('div.row',
        h('div.grow', h('div', { style: { fontWeight: 500 } }, 'Save the files before and after every agent run'),
          h('div.muted', { style: { fontSize: '13px' } }, 'In a project\'s Changes tab you see exactly what each run changed and can put everything back with one click. Kept apart from your own git, in Scorpi\'s data folder.')),
        switchEl(state.settings.checkpoints !== false, (v) => saveSettings({ checkpoints: v }))))),
  );
}

async function mcp(body) {
  const data = await api.get('/api/mcp');
  let servers = data.servers.map((s) => s.config);
  const list = h('div');
  const added = (p) => servers.some((s) => s.name === p.name);
  const presetTile = (p) =>
    h(
      'div.tile.mcp-tile',
      h('div.t-kicker', icon(CATEGORY_ICON[p.category] || 'plug'), p.command === 'uvx' ? 'needs uv' : p.command === 'npx' ? 'needs Node.js' : p.command, h('span.badge.k-scope', { title: 'Offered in ' + SCOPE_HINT[p.scope || 'both'] }, SCOPES.find(([k]) => k === (p.scope || 'both'))[1])),
      h('div.t-name', p.title),
      h('div.t-desc', p.about),
      p.setup ? h('div.t-setup', icon('shield'), p.setup) : null,
      h('div.t-foot', added(p) ? h('span.badge.ok', 'added') : h('button.btn.small', { onclick: () => addPreset(p) }, icon('plus'), 'Add'), h('span.mono.muted.t-cmd', [p.command, ...(p.args || [])].join(' '))),
    );
  const categories = [...new Set(data.presets.map((p) => p.category || 'Other'))];
  const presets = h('div');
  const renderPresets = () => {
    clear(presets);
    for (const c of categories) {
      presets.append(h('h4.mcp-cat', icon(CATEGORY_ICON[c] || 'plug'), c), h('div.cards-grid', data.presets.filter((p) => (p.category || 'Other') === c).map(presetTile)));
    }
  };
  body.append(
    h('p.section-desc', 'Model Context Protocol servers add tools from other programs, like Blender, GitHub or a browser. Choose for each one whether it is offered in chat, in coding projects, or both. Stdio servers start on the host (Node.js gives you npx, uv gives uvx); HTTP servers are reached by URL.'),
    h('div.row', { style: { marginBottom: '16px' } }, h('button.btn.primary', { onclick: () => editServer() }, icon('plus'), 'Add server')),
    list,
    h('div.section', { style: { marginTop: '28px' } }, h('h3.section-title', 'Servers to add', h('span.rule')), presets),
  );

  const statusOf = (name) => data.servers.find((s) => s.name === name.replace(/[^a-zA-Z0-9_]/g, '_')) || {};
  const render = () => {
    clear(list);
    renderPresets();
    if (!servers.length) return list.appendChild(h('div.panel', emptyState('No MCP servers yet', 'Add one of the servers below or your own.')));
    const panel = h('div.panel');
    servers.forEach((cfg, i) => {
      const st = statusOf(cfg.name);
      const badge = { connected: '.ok', error: '.err', connecting: '.warn' }[st.status] || '';
      panel.appendChild(
        h(
          'div.list-row',
          h('span.badge' + badge, st.status || 'idle'),
          h('div.lr-main', h('div.lr-title', cfg.name, st.tools && st.tools.length ? h('span.muted', ` · ${st.tools.length} tools`) : null), h('div.lr-sub.mono', st.error || cfg.url || [cfg.command, ...(cfg.args || [])].join(' '))),
          h('div.mcp-scope', { title: 'Where its tools are offered' }, seg(SCOPES, cfg.scope || 'both', (v) => { servers[i] = { ...cfg, scope: v }; persist(); toast(`${cfg.name}: ${SCOPE_HINT[v]}`, { timeout: 1600 }); })),
          switchEl(cfg.enabled !== false, (on) => { servers[i] = { ...cfg, enabled: on }; persist(); }),
          h('div.lr-actions', h('button.icon-btn', { title: 'Edit', onclick: () => editServer(i) }, icon('edit')), h('button.icon-btn', { title: 'Remove', onclick: () => { servers.splice(i, 1); persist(); } }, icon('trash'))),
        ),
      );
    });
    list.appendChild(panel);
  };

  async function persist() {
    const res = await api.post('/api/mcp', { servers });
    data.servers = res.servers;
    servers = res.servers.map((s) => s.config);
    render();
    setTimeout(async () => {
      const again = await api.get('/api/mcp');
      data.servers = again.servers;
      render();
    }, 4000);
  }

  function addPreset(p) {
    if (added(p)) return toast('Already added');
    const cfg = { name: p.name, command: p.command, args: p.args, env: { ...(p.env || {}) }, scope: p.scope || 'both', enabled: true };
    // servers that need a key (GitHub...) open the editor so it can be pasted first
    if (Object.keys(cfg.env).length) return editServer(undefined, cfg, p.setup);
    servers.push(cfg);
    persist();
    toast('Connecting ' + p.title, { body: p.setup || 'First start may take a minute while packages download.', timeout: p.setup ? 12000 : 5000 });
  }

  function editServer(i, draft, note) {
    const cfg = draft || (i === undefined ? { name: '', command: '', args: [], env: {}, url: '', scope: 'both' } : servers[i]);
    const name = h('input.input', { value: cfg.name, placeholder: 'my-server' });
    const command = h('input.input', { value: cfg.command || '', placeholder: 'npx' });
    const args = h('input.input', { value: (cfg.args || []).join(' '), placeholder: '-y @scope/server --flag' });
    const url = h('input.input', { value: cfg.url || '', placeholder: 'http://127.0.0.1:3001/mcp' });
    const env = h('textarea.input', { rows: 3, placeholder: 'KEY=value' }, Object.entries(cfg.env || {}).map(([k, v]) => `${k}=${v}`).join('\n'));
    let scope = cfg.scope || 'both';
    sheet({
      title: i === undefined ? 'Add MCP server' : 'Edit MCP server',
      body: h(
        'div',
        note ? h('p.section-desc', note) : null,
        field('Name', name),
        h('div.grid-2', field('Command (stdio)', command), field('Arguments', args)),
        field('…or URL (HTTP transport)', url),
        field('Environment', env, Object.values(cfg.env || {}).some((v) => !v) ? 'Fill in the value after = (e.g. your token).' : null),
        field('Use in', seg(SCOPES, scope, (v) => (scope = v)), 'Chat conversations, coding projects, or both.'),
      ),
      foot: (close) => [
        h('button.btn.quiet', { onclick: close }, 'Cancel'),
        h('button.btn.primary', {
          onclick: () => {
            if (!name.value.trim() || (!command.value.trim() && !url.value.trim())) return toast('Name and a command or URL are required', 'warn');
            const next = {
              name: name.value.trim(),
              command: command.value.trim(),
              args: args.value.match(/(?:[^\s"]+|"[^"]*")+/g)?.map((a) => a.replace(/^"|"$/g, '')) || [],
              url: url.value.trim(),
              env: Object.fromEntries(lines(env.value).map((l) => [l.split('=')[0], l.split('=').slice(1).join('=')])),
              scope,
              enabled: true,
            };
            if (Object.values(next.env).some((v) => !v.trim())) return toast('Fill in every environment value first', 'warn');
            if (i === undefined) servers.push(next);
            else servers[i] = next;
            close();
            persist();
          },
        }, 'Save'),
      ],
    });
  }
  render();
}

// ---------- skills ----------

async function skills(body) {
  const data = await api.get('/api/skills');
  const groups = (await api.get('/api/tools')).groups.filter((g) => g.id !== 'core');
  const custom = data.skills.filter((s) => s.custom);
  body.append(
    h('p.section-desc', 'Skills define the procedure; tools perform the operations. Pick a skill in the composer (or type /name) to apply it to a message.'),
    h('div.row', { style: { marginBottom: '16px' } }, h('button.btn.primary', { onclick: () => edit() }, icon('plus'), 'New skill')),
    h(
      'div.cards-grid',
      data.skills.map((s) =>
        h(
          'div.tile',
          h('div.t-kicker', icon(s.icon || 'spark'), s.custom ? 'custom' : 'built-in', h('span.mono', { style: { marginLeft: 'auto' } }, '/' + s.id)),
          h('div.t-name', s.name),
          h('div.t-desc', s.description),
          h('div.t-foot', (s.groups || []).map((g) => h('span.badge', g)), s.custom ? h('button.icon-btn', { title: 'Edit', onclick: () => edit(s) }, icon('edit')) : null, s.custom ? h('button.icon-btn', { title: 'Delete', onclick: () => saveAll(custom.filter((x) => x.id !== s.id)) }, icon('trash')) : null),
        ),
      ),
    ),
  );

  async function saveAll(list) {
    const res = await api.post('/api/skills', { skills: list });
    state.skills = res.skills;
    bus.emit('skills');
    renderConnections(root);
  }

  function edit(s = {}) {
    const name = h('input.input', { value: s.name || '', placeholder: 'Meeting prep' });
    const desc = h('input.input', { value: s.description || '', placeholder: 'One line shown in menus' });
    const instr = h('textarea.input', { rows: 8, placeholder: 'Procedure in numbered steps: what to check first, which tools to use, how to format the result…' }, s.instructions || '');
    const chosen = new Set(s.groups || []);
    const groupBox = h('div.roots', groups.map((g) => {
      const b = h('button' + (chosen.has(g.id) ? '.on' : ''), { type: 'button', onclick: () => { chosen.has(g.id) ? chosen.delete(g.id) : chosen.add(g.id); b.classList.toggle('on'); } }, g.name);
      return b;
    }));
    sheet({
      title: s.id ? 'Edit skill' : 'New skill',
      body: h('div', field('Name', name), field('Description', desc), field('Instructions', instr), h('div.field', h('label', 'Tool groups to load'), groupBox)),
      foot: (close) => [
        h('button.btn.quiet', { onclick: close }, 'Cancel'),
        h('button.btn.primary', {
          onclick: async () => {
            if (!name.value.trim() || !instr.value.trim()) return toast('Name and instructions are required', 'warn');
            const next = { id: s.id, name: name.value.trim(), description: desc.value.trim(), instructions: instr.value.trim(), groups: [...chosen] };
            const list = s.id ? custom.map((x) => (x.id === s.id ? next : x)) : [...custom, next];
            close();
            await saveAll(list);
            toast('Skill saved');
          },
        }, 'Save'),
      ],
    });
  }
}
