# Scorp

A calm, local-first AI workspace that runs on your own Windows PC and is reachable from any device on your
network or Tailscale. It opens as its own app window on the PC and installs as an app on phones.

Everything can be free: models run locally in [Ollama](https://ollama.com) **or** on free cloud tiers (Groq, Google
Gemini, OpenRouter, GitHub Models, Mistral, Cerebras, NVIDIA, Hugging Face, Z.ai, Cohere, SambaNova, Pollinations),
voices use free Microsoft neural voices, speech recognition uses each device's own recognizer (or Whisper), and
maps, weather, search and images use free public services. Coding plans such as OpenCode Go and your own API keys
(OpenAI, Anthropic, DeepSeek...) work too.

## Quick start (Windows 11)

1. Install **Python 3.10+** from python.org (tick *Add python.exe to PATH*).
2. Pick your models — either or both:
   - **Free cloud, no GPU needed:** open *Connect → Model*, click *Get a free key* on any provider (Groq and Google
     Gemini are good first picks; Gemini sees images) and paste the key. Pollinations even works without a key.
   - **Local and offline:** install **Ollama** from ollama.com, then in a terminal:
   ```
   ollama pull qwen3:8b            # smart, uses tools
   ollama pull nomic-embed-text    # knowledge search
   ollama pull qwen2.5vl:7b        # optional: sees images and the screen (computer use)
   ```
3. Double-click **`setup.bat`**. It creates a private Python environment, installs the optional packages one by
   one, and creates *Scorp* shortcuts on the Desktop and in the Start menu. Say yes to starting the server at
   sign-in if you want reminders and routines to fire while no window is open.
4. Open **Scorp** from the shortcut (or `Scorp.bat`). It starts the server in the background and opens an app
   window (Edge/Chrome app mode, or a pywebview window with `Scorp.bat --webview`).
   Closing the window keeps Scorp in the tray, so it reopens instantly from the tray icon or the shortcut; quit from
   the tray menu. Tick *Start with Windows* there to have it waiting in the tray after sign-in.

**Scorp Server** (`Scorp Server.exe`, shortcut on the Desktop and in the Start menu) is a small control window: start,
stop and restart the server and the app window, see the addresses, PIN and log, and choose whether the server starts
with Windows. Closing it leaves everything running; opening it again picks up where things are. Without the window:
`"Scorp Server.exe" --start | --stop | --restart`. Rebuild it with `server-control\build.bat`.

`start-server.bat` runs the server in a console instead, showing the addresses and the pairing PIN.
On Linux/macOS use `./start.sh`.

## Free cloud models

Each provider's live model list is read, so models that rotate on and off free tiers appear and disappear
automatically; the best chat and vision model of each is marked ★. Models from different providers can be mixed:
e.g. a Groq chat model with a Gemini vision model. When a free model is rate-limited, removed or offline (or Ollama
is not running), Scorp switches to the next available one and says so (*Switch models automatically*).
Keys are stored only in `ai/data/settings.json` on the host; messages go to the provider you chose.

Images pick their model automatically too: when a screenshot, web page or picture reaches a text-only chat model,
a vision model that can use tools takes over the turn (the *Vision model* setting first, then local, then cloud).
If none can use tools, a vision model describes the image for the chat model instead.

## Coding plans & API keys

Coding subscriptions and paid keys sit next to the free tiers in *Connect → Model*: **OpenCode Go**, OpenCode Zen,
Z.ai GLM Coding Plan, Kimi Code, MiniMax, DeepSeek, Moonshot, Alibaba Qwen, OpenAI and Anthropic. Paste the key and
the plan's models appear everywhere; when you pay for a plan it is preferred over free tiers.

- **Per-job model from chat**: say *"tell the coder to build this mod and use deepseek flash 4.1"* and that job runs on
  it (loose names are matched against your models). *"Switch the job to glm 5"* changes a running job from its next step;
  *"the coder should use … from now on"* changes the agent's model. The model chip on a job card does the same.
- **Parallel helpers**: the Coder splits bigger jobs into helper agents that work at the same time (OpenCode's subagents,
  or up to 4 helpers on the Basic engine); each helper shows live on the job card and in the project pane.
- **Coding model**: choose one (e.g. an OpenCode Go model) and the *Code* skill and coder sub-agents run on it while
  everyday chat keeps its own model.
- Gateways such as OpenCode serve some models through the OpenAI API style and others (MiniMax, Qwen, Claude) through
  the Anthropic `/messages` style; Scorp speaks both and picks the right one per model, learning it if a model
  moves.
- Each provider has *Endpoint & model IDs*: override the base URL if a service moves its API (OpenCode's newer
  `inference/go` URLs are tried automatically), and add model IDs missing from its list.

## Speech recognition

Devices use their own recognizer first: Google's on Android, Microsoft's online speech in Edge on Windows,
Chrome's on desktops and Safari's on iPhone. If a device has none (or it fails, e.g. in an embedded WebView),
audio goes to the host: free cloud Whisper when a Groq or Mistral key is set, otherwise local faster-whisper.
Change the order in *Settings → Voice*.

## Other devices (LAN or Tailscale)

- Open `http://<pc-ip>:8000` and enter the **pairing PIN** (shown in the server console and in
  Settings → Devices on the PC). Localhost never needs the PIN.
- Phones need **https** for the microphone. Scorp serves HTTPS on port **8443** with a self-signed certificate
  (accept the warning once), or, better, run `tailscale serve --bg 8000` on the PC and open
  `https://<pc-name>.<tailnet>.ts.net` for a trusted certificate.
- Install as an app: iPhone → Share → *Add to Home Screen*; Android/desktop Chrome/Edge → *Install app*.
- Windows asks to allow Python through the firewall on first start — allow it for private networks
  (setup can add the rule for you).

## Make it look like you

- **The Studio** (taskbar → brush icon, or `/studio`): styles and plugins, made by hand or by describing them.
  - *Styles* edit the design tokens (colours, glass, fonts, size, corners, chat width, Scorpi's colours) with a live
    preview, plus optional CSS. Start from Obsidian, Porcelain, Paper, Neon, Mono or Midnight.
  - *Plugins* are mods. Each can have JavaScript (the `scorp` API, `await` works), CSS, a **prompt** added to Scorp's own
    instructions while it is on (`{{key}}` fills in a setting), and **settings** the Studio draws as a form.
    - Surfaces: a pane docked beside the chat (`scorp.pane`), a band above the prompt box (`scorp.band`), a live chip in
      its bar (`scorp.status`), buttons under every answer (`scorp.messageAction`), `/commands`, tray buttons, floating
      panels, shortcuts (`scorp.key`), toasts, sounds, Scorpi speaking (`scorp.say`).
    - Hooks, as middleware: `scorp.hook('send', (e, next) => next({...e, text}))` rewrites or stops a message;
      `'answer'` decorates answers; `'tool'` sees Scorp's tool calls; `'route'` page changes.
    - State: `scorp.signal` + `scorp.mount` redraw on change; `scorp.options` / `onOptions`; `scorp.shared` keeps data on
      the server, live on every device (`ai/data/plugin_data.json`); `scorp.storage` stays on one device.
    - AI: `await scorp.llm(prompt, {json})` asks the model directly; `scorp.ask` sends a chat message.
    - Each plugin has a console in its editor (`console.log` and errors with line numbers). Errors also reach the
      server, so Scorp can read and fix its own plugins (`manage_looks` → `plugin_code`).
    - Examples in the Studio, one click to add: Scratchpad, Focus timer, Translate, Text expander, Persona, Tool ticker…
    Everything a plugin adds is removed when it is turned off.
  - Describe it instead: *"warm paper look with serif headings"*, *"a habit tracker pane synced to my devices"*.
  - Export any style or plugin as a `.json` file and import it on another Scorp (imported plugins with code or a prompt
    start off).
  Styles and plugins live in `ai/data/looks.json` and apply on every device at once.
- **Fine-tune per device** in *Settings → Appearance*: accent, font, text size, corners, chat width, glass, motion.

The design system lives in `public/css/`: `tokens.css` (every colour, size, radius, material and motion value — the
names styles may set are listed in `scorp/looks.py`), then `base`, `components`, `shell`, `chat`, `pages`, `canvas`,
`studio`, `scorpi` and `companion`.

## Scorpi on the desktop

In the desktop app Scorpi can live freely on the Windows desktop (*Settings → Scorpi*, or right-click Scorpi on the
prompt box → *Live on the desktop*). It is a native see-through window drawn with anti-aliased vectors (no box around
it; clicks pass through everywhere but Scorpi): it walks along the taskbar and the tops of windows, rides along when a
window moves, falls when it closes, naps when you are away, and follows the mouse with its eyes. Drag it anywhere;
right-click for its menu.

**Push-to-talk from any program:** hold **Ctrl + Win** (change it in *Settings → Scorpi*) and talk — *"hey Scorpi,
what's this?"*, *"tell the coder to rebuild this website I'm looking at"*. The window you are looking at goes along
with the question: its title and program, the address of a browser page, where the mouse points, and a picture of it.
The answer is spoken and shown in a small card next to Scorpi. Tap the key to talk hands-free. Even while Scorpi lives
in the app, the key works: it hops out for the conversation and goes back afterwards.

## Scorp Radio

The radio icon in the taskbar (phones: *More → Scorp Radio*) plays free internet radio from
[radio-browser.info](https://www.radio-browser.info): tap a mood (Focus, Chill, Lo-fi, Jazz…), search for a station or
genre, star favourites. Or just say it in chat: *"play some jazz"*, *"next station"*, *"pause the music"*.

- **One place for all music:** when Spotify, YouTube or any other player is on, the taskbar shows its song, and play,
  pause, next and previous control it (Windows media sessions). Starting the radio pauses the other player.
- **Covers and colours:** covers are looked up by song name (iTunes Search, free). With *Colours follow the music* on,
  the accent takes a colour from the cover while something plays, adjusted so it stays readable on light and dark styles.
- **Scorpi grooves along** while music plays; with the radio it picks up the tempo from the sound.
- **One speaker:** the radio plays on one device at a time — the one where you pressed play or asked for it (the PC for
  push-to-talk and routines). Every device shows what is on and can control it; *Play here* moves it.
- Stations play through Scorp (`/api/music/stream`), so phones on https can play http stations. Song titles inside the
  stream become "now playing". Windows' own media controls and phone lock screens show and control the radio too.
- Plugins can react with `scorp.on('music', fn)`.

## Discord

*Settings → Discord* shows "Playing Scorp" on your profile with the project or conversation, how long, and the model.
Create a Discord application named *Scorp* at discord.com/developers/applications, paste its Application ID and turn it
on. The Discord app must run on the same PC as the Scorp server. Its artwork is in `public/icons/discord/`: `app-icon.png`
as the App Icon, and `scorp.png`, `code.png`, `chat.png` under Rich Presence → Art Assets (keep those names).
`python scripts/make_icons.py` redraws every icon; `scripts/install-shortcuts.ps1 -NoPrompt` refreshes the shortcuts.

## What it can do

| Area | Tools |
| --- | --- |
| Code execution | `run_python`, `run_javascript` (Node), `run_shell` (PowerShell), exact `calculate` |
| Files | browse/read/write/edit/search/move anywhere on the PC, open with the default program |
| Documents | read PDF/Word/Excel/PowerPoint, create polished PDF/DOCX/XLSX/PPTX/CSV, edit DOCX/XLSX, merge/split/rotate/watermark PDFs, compare contracts |
| Images | generate illustrations, logos, icons (local Stable Diffusion if running, else free Pollinations), SVG graphics, Mermaid diagrams |
| Voice | text-to-speech (free neural voices, offline Windows voice, Fish Audio), device speech recognition with cloud/local Whisper fallback, hands-free voice conversations with barge-in |
| Computer use | screenshots with numbered clickable controls (Windows UI Automation), batched mouse/keyboard actions, scrolling, window control, a real Edge/Chrome browser via Playwright with numbered page elements |
| Data analysis | inspect CSV/Excel/JSON, pandas expressions, clean charts, reports |
| Maps | places nearby, geocoding, routes by car/bike/foot (OpenStreetMap/OSRM), weather (Open-Meteo) |
| Scheduling | reminders (with repeats) and calendar events, notifications, iCal feed for phone calendars |
| Database | a records database (collections of JSON records) plus read-only SQL on any SQLite file |
| Notifications | in-app on every device, Windows toasts on the host, phone push through the free ntfy app |
| APIs | web search (DuckDuckGo), page reading, Wikipedia, GET/POST to any API, Home Assistant, local service discovery |
| Programs | list/launch/close programs, processes, system info, clipboard, media keys |
| MCP | connect any MCP server (stdio or HTTP); one-click presets for free servers |

**Skills** (procedures on top of the tools): Research (multi-source, verified, cited reports), Tutor (adaptive
explanations, interactive quizzes, evaluation), Workflow (plan → act → recover → verify), Documents (summaries,
extraction, contract comparison, reports), Code, Analyst — plus your own custom skills.

**Code** (sidebar → *Chat / Code* switch): an area for coding projects. Describe what to build and the **Coder** starts in
its own folder under `data/workspace/projects/`. A project opens as a conversation with its agent next to a panel with
its progress, a live preview (sandboxed, for web projects with an `index.html`) and its files. Write to the agent while it
works and it picks the message up at once. In normal chat you can still talk about a project ("tell the bakery site
agent to add a gallery"): the chat assistant passes it on.

**Write / Voice** (toggle in the composer, Ctrl+Shift+Space): in Voice you talk to the open conversation or project
hands-free. Talk over Scorp at any time: your voice stops the speech, and while it works what you say reaches it right
away (a question, a change, or "stop"). In a coding project it only says what matters: that it started, questions and
approvals, problems, answers to what you asked, and a short summary at the end. Headphones keep its own voice from
counting as an interruption.

**MCP for chat and code**: presets include Blender, Context7, GitHub, Chrome DevTools, Desktop Commander, Docker, SQLite,
Playwright, DuckDuckGo, Wikipedia, arXiv and YouTube transcripts. Each server is offered in chat, in coding projects, or
both (the coding engine gets the coding ones too).

**Agents** (sidebar → *Agents*): a team that works in the background while you keep chatting. Ask in chat
"build me a website for my bakery" and the chat model hands the job to the **Coder** (its own folder under
`data/workspace/projects/`, own model if set), shows a live card with its plan, current step and approvals, and
reports back in the same chat when it is done. Researcher, Writer, Analyst and Operator work the same way.
Create your own agents just by asking ("create an agent that reminds me every day at 13:00 to take a break", "...that
checks the AI news every weekday at 8"): reminder agents send a notification, task agents run with tools on their
schedule. The Agents window lists who is working, lets you approve, message, stop or continue jobs, pause schedules
and edit each agent (instructions, tools, model, autonomy).

**Agent features**: long-term memory, image/audio/video/document understanding, background tasks that keep
running and notify you, sub-agents (`delegate`), scheduled automations with webhooks, a knowledge base with
retrieval (RAG), and per-tool permissions (Allow / Ask / Off) with approval cards on every device.

## Privacy & safety

- Data lives in `ai/data/` (SQLite database, settings, workspace, uploads). It is not committed to git.
- Reading is allowed by default; running code, controlling the PC, deleting and sending data ask for approval.
  Change any tool in **Connect → Tools & permissions**, and restrict folders there.
- Remote devices need the PIN; cross-site requests and DNS-rebinding attempts are rejected.

## Layout

```
ai/
  server.py          start the server            desktop.py   open the app window
  scorp/            backend: agent loop, LLM providers, MCP client, scheduler, voice, RAG, HTTP API
  scorp/tools/      tool modules (files, docs, code, web, data, media, computer, system, maps, agenda, records)
  public/            the web app (no build step): index.html, css/app.css, js/
```
