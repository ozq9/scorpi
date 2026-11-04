# Scorpi

A calm, local-first AI workspace that runs on your own Windows PC and is reachable from any device on your
network or Tailscale. It opens as its own app window on the PC and installs as an app on phones.

Everything can be free: models run locally in [Ollama](https://ollama.com) **or** on free cloud tiers (Groq, Google
Gemini, OpenRouter, Ollama Cloud, Cloudflare Workers AI, Vercel AI Gateway, Mistral, Cerebras, NVIDIA, Hugging Face,
Z.ai, Cohere, SambaNova, SiliconFlow, and Pollinations, OVHcloud and LLM7 with no signup at all) or on the free signup
credit of Nebius, Together, Fireworks, DeepInfra, Novita, Hyperbolic and Scaleway,
voices use free Microsoft neural voices, speech recognition uses each device's own recognizer (or Whisper), and
maps, weather, search and images use free public services. Coding plans such as OpenCode Go and your own API keys
(OpenAI, Anthropic, DeepSeek...) work too.

## Quick start (Windows 10 and 11)

1. Download **Scorpi-Setup.exe** from [scorpi.dev](https://scorpi.dev) and run it. It installs Scorpi for your user
   (no admin) into `%LOCALAPPDATA%\Programs\Scorpi`: a private Python (python.org's 3.12, nothing is added to the
   system), every package from `requirements.txt`, Start menu and desktop shortcuts, and an entry in
   *Settings → Apps* to remove it. Running it again updates Scorpi and keeps your chats and settings.
   `Scorpi-Setup.exe --quiet [--dir <folder>] [--no-desktop] [--startup]` installs without a window.
2. Open **Scorpi**. Then pick your models, either or both:
   - **Free cloud, no GPU needed:** open *Connect → Model*, click *Get a free key* on any provider (Groq and Google
     Gemini are good first picks; Gemini sees images) and paste the key. Pollinations even works without a key.
   - **Local and offline:** install **Ollama** from ollama.com, then in a terminal:
   ```
   ollama pull qwen3:8b            # smart, uses tools
   ollama pull nomic-embed-text    # knowledge search
   ollama pull qwen2.5vl:7b        # optional: sees images and the screen (computer use)
   ```

**`Scorpi.exe`** is the one program: it starts the server in the background and opens the app window (a splash shows
while Scorpi wakes up), or brings the open window forward. Closing the window keeps Scorpi in the tray, so it reopens
instantly; quit from the tray menu. Tick *Start with Windows* there (or in the installer) to have it waiting in the tray
after sign-in.

The server control lives in the same exe: tray menu → *Server control…* (or `Scorpi.exe --control`) starts, stops and
restarts the server and the app window and shows the addresses, PIN and log. Without any window:
`Scorpi.exe --start | --stop | --restart`. Rebuild it with `server-control\build.bat`.

From the source instead of the installer: install Python 3.10+, run `setup.bat`, then open `Scorpi.exe`.
`start-server.bat` runs the server in a console, showing the addresses and the pairing PIN. On Linux/macOS use
`./start.sh`.

**Releasing:** `scripts\make_release.ps1 -Version x.y` builds `Scorpi.exe`, packs the app (never `data\`) into the
installer (`..\installer\Setup.cs`) and writes `..\site\downloads\Scorpi-Setup.exe` plus `..\site\version.json`; then
`npx wrangler pages deploy` from the folder above `ai` publishes the website.

## Free cloud models

Each provider's live model list is read, so models that rotate on and off free tiers appear and disappear
automatically; the best chat and vision model of each is marked ★. Models from different providers can be mixed:
e.g. a Groq chat model with a Gemini vision model. When a free model is rate-limited, removed or offline (or Ollama
is not running), Scorpi switches to the next available one and says so (*Switch models automatically*).
Keys are stored only in `ai/data/settings.json` on the host; messages go to the provider you chose.

- **Three kinds of services** in *Connect → Model*: *Coding plans & API keys*, *Free cloud models* (lasting free tiers)
  and *Free credits* (credit when you sign up; when it runs out, Scorpi moves on to the next model).
- **No signup at all:** Pollinations, OVHcloud AI Endpoints and LLM7 answer without a key (slowly); *Models without a
  key* turns them on or off. A free key from each lifts the limits.
- **Cloudflare Workers AI** needs your Account ID next to the API token (its URLs contain it).
- **Coding model → Automatic (best coder on offer)** picks each service's strongest coding model (Qwen3 Coder, Kimi K2,
  Devstral, GLM...) in the order you added the services, marked ★ *best code*.
- **Retired services** (GitHub Models, retired by GitHub on 30 July 2026): a chat still set to one switches to an
  available model and says why; the stored key is removed.

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
- **Any API model in the full coding engine**: models from every provider with a key (NVIDIA, OpenRouter, Groq,
  Gemini, DeepSeek...) run in OpenCode too. Scorpi hands its keys and the chosen models to the engine through
  `data/engine.json`, which OpenCode reloads by itself, so a new model works without restarting a running job.
- Gateways such as OpenCode serve some models through the OpenAI API style and others (MiniMax, Qwen, Claude) through
  the Anthropic `/messages` style; Scorpi speaks both and picks the right one per model, learning it if a model
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
- Phones need **https** for the microphone. Scorpi serves HTTPS on port **8443** with a self-signed certificate
  (accept the warning once), or, better, run `tailscale serve --bg 8000` on the PC and open
  `https://<pc-name>.<tailnet>.ts.net` for a trusted certificate.
- Install as an app: iPhone → Share → *Add to Home Screen*; Android/desktop Chrome/Edge → *Install app*.
- Windows asks to allow Python through the firewall on first start — allow it for private networks
  (setup can add the rule for you).

## Make it look like you

- **The Studio** (taskbar → brush icon, or `/studio`): styles and plugins, made by hand or by describing them.
  - *Styles* edit the design tokens (colours, glass, fonts, size, corners, chat width, Scorpi's colours) with a live
    preview, plus optional CSS. Start from Obsidian, Porcelain, Paper, Neon, Mono or Midnight.
  - *Plugins* are mods. Each can have JavaScript (the `scorpi` API, `await` works), CSS, a **prompt** added to Scorpi's own
    instructions while it is on (`{{key}}` fills in a setting), and **settings** the Studio draws as a form.
    - Surfaces: a pane docked beside the chat (`scorpi.pane`), a band above the prompt box (`scorpi.band`), a live chip in
      its bar (`scorpi.status`), buttons under every answer (`scorpi.messageAction`), `/commands`, tray buttons, floating
      panels, shortcuts (`scorpi.key`), toasts, sounds, Scorpi speaking (`scorpi.say`).
    - Hooks, as middleware: `scorpi.hook('send', (e, next) => next({...e, text}))` rewrites or stops a message;
      `'answer'` decorates answers; `'tool'` sees Scorpi's tool calls; `'route'` page changes.
    - State: `scorpi.signal` + `scorpi.mount` redraw on change; `scorpi.options` / `onOptions`; `scorpi.shared` keeps data on
      the server, live on every device (`ai/data/plugin_data.json`); `scorpi.storage` stays on one device.
    - AI: `await scorpi.llm(prompt, {json})` asks the model directly; `scorpi.ask` sends a chat message.
    - Each plugin has a console in its editor (`console.log` and errors with line numbers). Errors also reach the
      server, so Scorpi can read and fix its own plugins (`manage_looks` → `plugin_code`).
    - Examples in the Studio, one click to add: Scratchpad, Focus timer, Translate, Text expander, Persona, Tool ticker…
    Everything a plugin adds is removed when it is turned off.
  - Describe it instead: *"warm paper look with serif headings"*, *"a habit tracker pane synced to my devices"*.
  - Export any style or plugin as a `.json` file and import it on another Scorpi (imported plugins with code or a prompt
    start off).
  - *Gallery*: the community's styles, plugins, skills and agents, kept on the website (`functions/api/gallery.js`, D1).
    Search, filter by kind, open one to read exactly what it contains, install it. Plugins with code start off, agents
    start paused and never auto-approve. *Share yours* (or *Share to the gallery* in a style's or plugin's menu) sends one
    item without models, keys or approvals; the site returns a key that stays in `settings.json` (`gallery_mine`) so
    only this Scorpi can take it down. Three reports hide an item until the site's owner looks at it (`/admin.html`).
    The website's gallery page links *Install in Scorpi* to `localhost:8000/studio?gallery=<id>`. `gallery_url`
    points the app at another site (e.g. `wrangler pages dev` on port 8788 for testing).
  Styles and plugins live in `ai/data/looks.json` and apply on every device at once.
- **Fine-tune per device** in *Settings → Appearance*: accent, font, text size, corners, chat width, glass, motion.

The design system lives in `public/css/`: `tokens.css` (every colour, size, radius, material and motion value — the
names styles may set are listed in `scorpi/looks.py`), then `base`, `components`, `shell`, `chat`, `pages`, `canvas`,
`studio`, `scorpi` and `companion`.

## Usage and the recap

Every model answer is counted on the PC (`scorpi/usage.py`, table `usage`): the model, the tokens the provider reported
(OpenAI-style endpoints are asked for a usage chunk, Anthropic-style and Ollama report their own; endpoints that report
nothing are estimated from the text and marked `~`), where it came from (chat, agents, code projects, routines,
titles & helpers) and the cost. Local models and free tiers cost nothing, coding plans count tokens but no money, and
pay-as-you-go keys are priced from a built-in table of estimates or the real price you set per model.

- **Usage page** (sidebar → *Usage*, `/usage`): tokens and spend for 7 / 30 / 90 days or all time, the year of activity
  as a heatmap, the hours and weekdays you work, sessions, streaks, favorite model, busiest day, longest chat; the
  *Models* tab has tokens per day, every model with its calls, tokens, price and share, and where the tokens go.
  Activity comes from your chats, so it reaches back before token counting started.
- **The meter** next to the model chip shows the open conversation's tokens (and cost, if any); click it for the page.
- **Recap**: your period as a short story, a screen at a time (what kind of user you are by the hours you keep, your
  go-to model, streaks, what was on your mind, the tokens), ending on a card with the pixel scorpion to save or copy.

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

## Scorpi Radio

The radio icon in the taskbar (phones: *More → Scorpi Radio*) plays free internet radio from
[radio-browser.info](https://www.radio-browser.info): tap a mood (Focus, Chill, Lo-fi, Jazz…), search for a station or
genre, star favourites. Or just say it in chat: *"play some jazz"*, *"next station"*, *"pause the music"*.

- **One place for all music:** when Spotify, YouTube or any other player is on, the taskbar shows its song, and play,
  pause, next and previous control it (Windows media sessions). Starting the radio pauses the other player.
- **Covers and colours:** covers are looked up by song name (iTunes Search, free). With *Colours follow the music* on,
  the accent takes a colour from the cover while something plays, adjusted so it stays readable on light and dark styles.
- **Scorpi grooves along** while music plays; with the radio it picks up the tempo from the sound.
- **One speaker:** the radio plays on one device at a time — the one where you pressed play or asked for it (the PC for
  push-to-talk and routines). Every device shows what is on and can control it; *Play here* moves it.
- Stations play through Scorpi (`/api/music/stream`), so phones on https can play http stations. Song titles inside the
  stream become "now playing". Windows' own media controls and phone lock screens show and control the radio too.
- Plugins can react with `scorpi.on('music', fn)`.

## Discord

*Settings → Discord* shows "Playing Scorpi" on your profile with the project or conversation, how long, and the model.
Create a Discord application named *Scorpi* at discord.com/developers/applications, paste its Application ID and turn it
on. The Discord app must run on the same PC as the Scorpi server. Its artwork is in `public/icons/discord/`: `app-icon.png`
as the App Icon, and `scorpi.png`, `code.png`, `chat.png` under Rich Presence → Art Assets (keep those names).
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

**The project panel** (next to a project's chat; drag its edge to resize, ⤢ widens it, Alt+1…6 switch tabs):
- *Agents*: the job with its plan (`[x]` `[>]` `[ ]` `[!]`), every helper agent working in parallel (open one for its task,
  live step and result), approvals, and earlier runs.
- *Preview*: a small browser with an address bar, back/forward, desktop/tablet/phone widths and the page's console. Errors
  (also failed images and scripts) are counted on the tab; *Fix errors* hands them to the agent. Type `localhost:5173` to
  look at a dev server.
- *Files*: the tree with a filter and a viewer.
- *Changes*: what the agent's last run changed, as a diff, with *Undo run*; uncommitted git changes with a commit box
  whose message the model can write; and *Checkpoints*: Scorpi saves the files before and after every agent run (in
  `data/checkpoints/`, apart from the project's own git), and any checkpoint can be looked at and restored.
- *Terminal*: commands run in the project folder with live output, stop, input, one-click commands from the project
  (npm scripts, pytest, git…). A failed command goes to the agent with *Fix*; a local address in the output opens in Preview.
- *GitHub*: branch switcher, pull and push, the branch's pull request and its CI checks, *Create pull request* (title and
  description written from the changes), open issues with *Fix* (the coder works on `fix/issue-N` and commits), open PRs
  and recent commits. Projects not on GitHub yet can be published as a new repository.
- `⋯` opens the project in VS Code, a terminal window or Explorer.

**GitHub** (*Connections → GitHub & git*): sign in through the GitHub CLI (`gh`) in the browser, or paste a token with the
`repo` scope. Then *Code → Clone from GitHub* lists your repositories (public ones clone without an account too), and
*Open a folder on this PC* works on any existing folder in place. Git logins use the token as a header for that one
command; it is never written into a repository. The agents get `github` (issues, pull requests, CI) and
`github_pull_request`. A project's `AGENTS.md` (or `CLAUDE.md`) is followed by the coder.

**Write / Voice** (toggle in the composer, Ctrl+Shift+Space): in Voice you talk to the open conversation or project
hands-free. Talk over Scorpi at any time: your voice stops the speech, and while it works what you say reaches it right
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
  scorpi/            backend: agent loop, LLM providers, MCP client, scheduler, voice, RAG, HTTP API
  scorpi/tools/      tool modules (files, docs, code, web, data, media, computer, system, maps, agenda, records)
  public/            the web app (no build step): index.html, css/app.css, js/
```
