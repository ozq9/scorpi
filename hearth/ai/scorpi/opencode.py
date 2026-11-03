"""Coding engine for the Coder: jobs run in the open-source OpenCode agent (opencode.ai) instead of Scorpi's own loop, so
coding gets OpenCode's full toolset (precise edits, patches, code search, LSP, shell...) and native OpenCode Go support.
Scorpi runs a private OpenCode server and turns its live event stream into ordinary Scorpi run events: thinking and text
stream, tool steps show as they happen, permissions become Scorpi approval cards and the agent's questions become
question cards. The user only ever sees Scorpi."""

import atexit
import base64
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request

from . import db, events, llm, notify
from .config import DATA_DIR, IS_WINDOWS, SETTINGS

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PID_FILE = os.path.join(DATA_DIR, "engine.pid")

ROLE = """You are Scorpi's Coder agent, working in the background on a job the user handed over through Scorpi's chat assistant.
The user follows your progress from time to time. Prefer sensible assumptions over questions (note them in your report); ask with the question tool only when a choice really matters.
1. If the folder already has files, read them first and continue from there. Otherwise pick a simple, robust stack (websites: plain HTML, CSS and JavaScript unless a framework is asked for).
2. Write complete, working files. No placeholders, TODOs or "rest stays the same".
3. Test everything: run the code, linters or tests (e.g. node --check app.js, python -m py_compile main.py, npm test), read the errors, fix them and run again until it works. For websites check that every linked file exists.
4. Add a short README.md: what it is and how to open or run it.
5. End with a concise report: the features, the files with full paths, exactly how to open or run it and ideas for next steps.
Work fast: when a job has parts that don't depend on each other (separate pages, components, modules, tests, research), plan them, then start several subagents with the task tool IN THE SAME STEP so they run in parallel, each owning its own files and getting all the context it needs. Then integrate, run and test the whole thing yourself. Small jobs you just do yourself."""

# Scorpi provider id -> OpenCode provider id (both gateways are OpenCode's own)
PROVIDERS = {"opencode-go": "opencode-go", "opencode-zen": "opencode"}

# OpenCode permission action -> the Scorpi tool whose policy and approval card it uses
PERMISSION_TOOLS = {"shell": "run_shell", "bash": "run_shell", "edit": "edit_file", "write": "write_file", "patch": "edit_file",
                    "read": "read_file", "webfetch": "fetch_page", "websearch": "web_search", "external_directory": "manage_path"}

QUESTIONS = {}  # form id -> Turn waiting for the user's answer

_models = {"at": 0, "list": []}


def binary():
    """The real opencode executable. npm installs a .cmd shim on Windows, which cannot be started without cmd.exe."""
    path = shutil.which("opencode")
    if path and path.lower().endswith(".cmd"):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                m = re.search(r'"%dp0%\\([^"]+\.exe)"', fh.read(), re.I)
            exe = os.path.join(os.path.dirname(path), m.group(1)) if m else ""
            if os.path.isfile(exe):
                return exe
        except OSError:
            pass
    return path


def use_for(agent):
    """Whether a job of this agent runs on the coding engine: the Coder (Code skill), when it is installed and not turned off."""
    return agent.get("skill") == "code" and SETTINGS["coder_backend"] not in ("scorpi", "scorp", "hearth") and bool(binary())


def models(refresh=False):
    if refresh or time.time() - _models["at"] > 600:
        try:
            out = SERVER.request("GET", "/api/model")
            items = out.get("data") if isinstance(out, dict) else out
            _models["list"] = ["%s/%s" % (m.get("providerID"), m.get("id")) for m in items or [] if isinstance(m, dict)]
        except Exception:
            _models["list"] = []
        _models["at"] = time.time()
    return _models["list"]


def model_for(scorpi_model):
    """{"providerID", "id"} for a Scorpi model ref (e.g. @opencode-go/glm-5.3), or None for the engine's default model."""
    if not llm.is_cloud(scorpi_model or ""):
        return None
    pid, mid = llm.split_cloud(scorpi_model)
    ocid = PROVIDERS.get(pid, pid)
    if pid in PROVIDERS or "%s/%s" % (ocid, mid) in models():
        return {"providerID": ocid, "id": mid}
    return None


def _project_config(workdir):
    """OpenCode 2 ignores config passed through the environment, but reads <project>/.opencode/opencode.json:
    put Scorpi's coding MCP servers (Blender, Context7, GitHub...) there before a session starts in that folder.
    Anything else in the file is kept."""
    from .mcp import MANAGER
    servers = MANAGER.for_engine()
    folder = os.path.join(workdir, ".opencode")
    path = os.path.join(folder, "opencode.json")
    mine = os.path.join(folder, "scorpi-mcp.json")  # which entries are Scorpi's (OpenCode rejects unknown keys in its config)
    for old in ("scorp-mcp.json", "hearth-mcp.json"):  # the same list under the app's old names
        old = os.path.join(folder, old)
        if os.path.exists(old) and not os.path.exists(mine):
            os.replace(old, mine)

    def load(p, default):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    data = load(path, None)
    if not isinstance(data, dict):
        if not servers:
            return
        data = {"$schema": "https://opencode.ai/config.json"}
    current = data["mcp"] if isinstance(data.get("mcp"), dict) and "servers" not in data["mcp"] else {}
    ours = set(load(mine, []))
    # servers Scorpi added before but that are now off or gone are dropped; the user's own entries stay
    merged = {k: v for k, v in current.items() if k not in ours}
    merged.update(servers)
    if merged == current and ours == set(servers):
        return
    data["mcp"] = merged
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    with open(mine, "w", encoding="utf-8") as fh:
        json.dump(sorted(servers), fh)


def _config():
    """Keys pasted into Scorpi reach the engine too, so a plan works even if OpenCode itself was never logged in.
    MCP servers set up in Scorpi for coding (Blender, Context7, GitHub...) are handed over as well."""
    from .mcp import MANAGER
    providers = {}
    for pid, ocid in PROVIDERS.items():
        key = SETTINGS["key_" + pid]
        if key:
            providers[ocid] = {"options": {"apiKey": key}}
    config = {"provider": providers} if providers else {}
    servers = MANAGER.for_engine()
    if servers:
        config["mcp"] = servers
    return json.dumps(config) if config else ""


def _kill_leftover():
    """An engine left running by a Scorpi that was killed hard (atexit never ran)."""
    try:
        with open(PID_FILE, encoding="utf-8") as fh:
            pid = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return
    if pid and IS_WINDOWS:
        listed = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"], capture_output=True, creationflags=NO_WINDOW)
        if b"opencode" in listed.stdout.lower():
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=NO_WINDOW)
    elif pid:
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                if b"opencode" in fh.read():
                    os.kill(pid, 9)
        except OSError:
            pass
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def _kill(proc):
    if not proc or proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        proc.kill()


# ---------- the private server ----------

class Server:
    """One `opencode serve` on a random localhost port with a random password, plus one reader of its event stream."""

    def __init__(self):
        self.proc = None
        self.url = ""
        self.password = ""
        self.config = None
        self.handlers = {}  # session id -> fn(event)
        self.children = {}  # subagent (child) session id -> {"root": session with a handler, "title"}
        self.lock = threading.Lock()

    def alive(self):
        return bool(self.proc and self.proc.poll() is None and self.url)

    def ensure(self):
        with self.lock:
            config = _config()
            if self.alive() and (config == self.config or self.handlers):
                return
            self.stop()
            self._start(config)

    def _start(self, config):
        exe = binary()
        if not exe:
            raise RuntimeError("The coding engine is not installed.")
        _kill_leftover()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.password = secrets.token_urlsafe(24)
        env = dict(os.environ, OPENCODE_SERVER_PASSWORD=self.password)
        if config:
            env["OPENCODE_CONFIG_CONTENT"] = config
        self.proc = subprocess.Popen([exe, "serve", "--hostname", "127.0.0.1", "--port", str(port)], cwd=DATA_DIR, env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        self.config = config
        try:
            with open(PID_FILE, "w", encoding="utf-8") as fh:
                fh.write(str(self.proc.pid))
        except OSError:
            pass
        ready = threading.Event()
        threading.Thread(target=self._drain, args=(self.proc, ready), daemon=True, name="engine-log").start()
        if not ready.wait(60) or self.proc.poll() is not None:
            self.stop()
            raise RuntimeError("The coding engine did not start.")
        self.url = "http://127.0.0.1:%d" % port
        threading.Thread(target=self._read_events, args=(self.proc,), daemon=True, name="engine-events").start()

    def _drain(self, proc, ready):
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            m = re.match(r"server password (\S+)", line)
            if m:
                self.password = m.group(1)  # an older build that ignores the variable prints its own
            if "listening on" in line:
                ready.set()
        ready.set()

    def stop(self):
        _kill(self.proc)
        self.proc, self.url = None, ""

    def _auth(self):
        return "Basic " + base64.b64encode(("opencode:" + self.password).encode()).decode()

    def request(self, method, path, body=None, timeout=60):
        if not self.alive():
            self.ensure()
        req = urllib.request.Request(self.url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": self._auth(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError("The coding engine refused %s %s (%s): %s" % (method, path.split("?")[0], exc.code, detail))
        return json.loads(raw) if raw.strip() else {}

    def _read_events(self, proc):
        while proc.poll() is None:
            try:
                req = urllib.request.Request(self.url + "/api/event", headers={"Authorization": self._auth(), "Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=3600) as resp:
                    for raw in resp:
                        if raw.startswith(b"data:"):
                            try:
                                self._dispatch(json.loads(raw[5:]))
                            except ValueError:
                                pass
            except Exception:
                if proc.poll() is None:
                    time.sleep(1)
        for fn in list(self.handlers.values()):
            fn({"type": "engine.lost", "data": {}})

    def _dispatch(self, ev):
        data = ev.get("data") or {}
        sid = data.get("sessionID") or (data.get("form") or {}).get("sessionID")
        fn = self.handlers.get(sid)
        if not fn and sid and self.handlers:
            # a subagent (task tool) works in a child session: its events go to the job that started it
            child = self._child(sid)
            fn = self.handlers.get(child["root"]) if child else None
            if fn:
                ev = dict(ev, _child=sid, _title=child["title"])
        if fn:
            try:
                fn(ev)
            except Exception:
                traceback.print_exc()

    def _child(self, sid):
        """{"root", "title"} when sid is a subagent session under a session Scorpi follows (looked up once), else None."""
        if sid in self.children:
            return self.children[sid]
        found, current, title = None, sid, ""
        for _ in range(4):  # subagents of subagents too
            try:
                out = self.request("GET", "/api/session/%s" % current, timeout=10)
            except Exception:
                return None  # not cached: asked again with its next event
            info = out.get("data") if isinstance(out.get("data"), dict) else out
            title = title or info.get("title") or ""
            current = info.get("parentID") or ""
            if not current:
                break
            if current in self.handlers:
                found = {"root": current, "title": title}
                break
        self.children[sid] = found
        return found


SERVER = Server()
atexit.register(SERVER.stop)


# ---------- a turn: one prompt in a session, shown as a Scorpi run ----------

def _texts(content):
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def _file_card(path):
    try:
        return {"type": "file", "path": path, "name": os.path.basename(path), "size": os.path.getsize(path), "caption": ""}
    except OSError:
        return None


def _plan(todos):
    status = {"in_progress": "active", "completed": "done", "cancelled": "failed"}
    return [{"title": t.get("content") or t.get("title") or "", "status": status.get(t.get("status"), "pending")}
            for t in todos or [] if isinstance(t, dict)]


def _tool(name, args, workdir):
    """Engine tool call -> (Scorpi tool name, args, files) so steps read like Scorpi's own."""
    path = args.get("filePath") or args.get("path") or ""
    full = os.path.normpath(os.path.join(workdir, path)) if path and not os.path.isabs(path) else path
    if name == "write":
        return "write_file", {"path": full}, [full]
    if name in ("edit", "multiedit"):
        return "edit_file", {"path": full}, [full]
    if name in ("patch", "apply_patch"):
        return "edit_file", {"path": full or "several files"}, [full] if full else []
    if name == "read":
        return "read_file", {"path": full}, []
    if name in ("list", "ls"):
        return "list_dir", {"path": full or workdir}, []
    if name == "grep":
        return "search_files", {"contains": args.get("pattern") or "", "pattern": args.get("include") or ""}, []
    if name == "glob":
        return "search_files", {"pattern": args.get("pattern") or ""}, []
    if name in ("bash", "shell"):
        return "run_shell", {"command": args.get("command") or ""}, []
    if name == "webfetch":
        return "fetch_page", {"url": args.get("url") or ""}, []
    if name == "websearch":
        return "web_search", {"query": args.get("query") or ""}, []
    if name in ("todowrite", "todo"):
        return "update_plan", {"steps": _plan(args.get("todos"))}, []
    if name == "task":
        return "delegate", {"agent": args.get("subagent_type") or "helper", "task": args.get("description") or args.get("prompt") or ""}, []
    if name == "question":
        return "ask_user", {"question": "; ".join(q.get("question", "") for q in args.get("questions") or [] if isinstance(q, dict))}, []
    if name in ("lsp", "diagnostics"):
        return "check_code", {"path": full}, []
    return name, args, []


class Turn(events.Run):
    """A Scorpi run backed by an engine session: same events, same follow/cancel, plus send() for messages while it works."""

    def __init__(self, conv_id, title, agent, workdir, prompt, session, on_session):
        super().__init__(conv_id, title=title, origin="agent")
        self.agent = agent
        self.workdir = workdir
        self.prompt = prompt
        self.session = session
        self.on_session = on_session
        self.model_ref = agent.get("model") or SETTINGS["code_model"] or SETTINGS["model"] or ""
        self.user_mid = db.add_message(conv_id, "user", prompt, {"attachments": [], "skill": "code"})
        self.assistant_mid = db.new_id()
        self.parts, self.files, self.texts = [], [], []
        self.tools = {}  # engine call id -> part
        self.tool_names = {}  # engine call id -> engine tool name (announced before the call's input)
        self.error = ""
        self.cost = 0.0
        self.question = None
        self.question_session = ""  # the session asking: this one, or a subagent's
        self.subs = {}  # task tool call id -> helper subagent shown in the app
        self.child_subs = {}  # subagent session id -> task tool call id
        self.ended = threading.Event()
        self._state_lock = threading.Lock()

    # ----- lifecycle -----

    def begin(self):
        threading.Thread(target=self._work, daemon=True, name="coder-" + self.id).start()
        return self

    def _work(self):
        self.emit({"type": "start", "run_id": self.id, "conv_id": self.conv_id, "message_id": self.assistant_mid,
                   "user_message_id": self.user_mid, "model": self.model_ref, "skill": "code"})
        try:
            if not SERVER.alive():
                self.emit({"type": "status", "text": "Getting ready"})
            SERVER.ensure()
            try:
                _project_config(self.workdir)
            except Exception:
                traceback.print_exc()  # the job still works, just without MCP tools
            model = model_for(self.model_ref)
            if self.model_ref and not model and llm.is_cloud(self.model_ref):
                self.emit({"type": "notice", "text": "%s isn't available in the coding engine, so it uses its default model. Pick an "
                                                     "OpenCode Go/Zen model, or switch the coding engine to Basic in Connections → Model."
                                                     % llm._label(self.model_ref)})
            text = self.prompt
            if self.session and not self._session_exists(self.session):
                self.session = ""
            if self.session and model:
                # a job that continues keeps its session; the model may have changed since (set_agent_model)
                SERVER.request("POST", "/api/session/%s/model" % self.session, {"model": model})
            if not self.session:
                body = {"location": {"directory": self.workdir}, "permissions": self._permissions()}
                if model:
                    body["model"] = model
                self.session = SERVER.request("POST", "/api/session", body)["data"]["id"]
                if self.on_session:
                    self.on_session(self.session)
                extra = (self.agent.get("instructions") or "").strip()
                from .agents import CODER_ROLE
                text = "%s\n%s\nWork inside this folder: %s\n\n%s" % (
                    ROLE, ("\nThe user's instructions for you:\n" + extra) if extra and extra != CODER_ROLE else "", self.workdir, self.prompt)
            SERVER.handlers[self.session] = self._on_event
            if self.cancelled:
                return self._end()
            SERVER.request("POST", "/api/session/%s/prompt" % self.session, {"text": text, "delivery": "steer"})
            while not self.ended.wait(2):
                if self.cancelled and not self.ended.wait(10):
                    break  # the engine did not confirm the stop; finish anyway
                if not SERVER.alive():
                    self.error = self.error or "The coding engine stopped unexpectedly."
                    break
        except Exception as exc:
            traceback.print_exc()
            self.error = str(exc)
        self._end()

    def _session_exists(self, sid):
        try:
            SERVER.request("GET", "/api/session/%s" % sid)
            return True
        except RuntimeError:
            return False

    def _permissions(self):
        """Scorpi's tool permissions, carried over: Allow / Ask / Off for commands and file changes."""
        if self.agent.get("auto_approve"):
            return [{"action": "*", "resource": "*", "effect": "allow"}]
        from .tools import TOOLS, load_all, policy
        if not TOOLS:
            load_all()
        effect = {"allow": "allow", "ask": "ask", "off": "deny"}
        rules = [{"action": "shell", "resource": "*", "effect": effect[policy("run_shell")]}]
        for action, tool in (("edit", "edit_file"), ("write", "write_file")):
            rules.append({"action": action, "resource": "*", "effect": effect[policy(tool)]})
        return rules

    def _end(self):
        with self._state_lock:
            if self.done:
                return
            SERVER.handlers.pop(self.session, None)
            if self.question:
                QUESTIONS.pop(self.question["id"], None)
                self.emit({"type": "question_done", "id": self.question["id"], "answer": ""})
                self.question = None
            for part in self.tools.values():
                if part["status"] == "running":
                    part.pop("started", None)
                    part.pop("files", None)
                    part.update(status="error", ok=False, result=part.get("result") or "Stopped.")
            data = {"parts": self.parts, "model": self.model_ref, "skill": "code", "files": self.files, "error": self.error,
                    "stopped": self.cancelled}
            db.x("INSERT INTO messages (id, conv_id, role, content, data, created) VALUES (?,?,?,?,?,?)",
                 (self.assistant_mid, self.conv_id, "assistant", "\n\n".join(self.texts), json.dumps(data), time.time()))
            db.touch_conversation(self.conv_id)
            self.result = (self.texts[-1] if self.texts else "") or self.error
            if self.error:
                self.emit({"type": "error", "error": self.error})
            self.emit({"type": "done", "message_id": self.assistant_mid, "content": self.texts[-1] if self.texts else "",
                       "stopped": self.cancelled})
            self.finish()
        events.publish("conversation", {"id": self.conv_id})

    def cancel(self):
        super().cancel()
        if self.question:
            QUESTIONS.pop(self.question["id"], None)
        if self.session:
            threading.Thread(target=self._interrupt, daemon=True).start()
        else:
            self.ended.set()

    def _interrupt(self):
        try:
            SERVER.request("POST", "/api/session/%s/interrupt" % self.session, timeout=20)
        except Exception:
            self.ended.set()

    def switch_model(self, ref):
        """The user picked another model while the job works: the session continues with it from its next step."""
        model = model_for(ref)
        if not model:
            self.emit({"type": "notice", "text": "%s isn't available in the coding engine; the job keeps %s." % (
                llm._label(ref), llm._label(self.model_ref) if self.model_ref else "its model")})
            return False
        self.model_ref = ref
        if self.session:
            SERVER.request("POST", "/api/session/%s/model" % self.session, {"model": model})
        self.emit({"type": "notice", "text": "Now working with %s." % llm._label(ref)})
        return True

    def send(self, text):
        """A message from the user while the agent works: answers its open question, or steers the current work."""
        if self.question:
            answer({f["key"]: text for f in self.question["fields"]} or {"q0": text}, self.question["id"])
            return "answered"
        self.emit({"type": "notice", "text": "You: " + text})
        self.parts.append({"type": "text", "text": "> " + text.replace("\n", "\n> ")})
        SERVER.request("POST", "/api/session/%s/prompt" % self.session, {"text": text, "delivery": "steer"})
        return "delivered"

    # ----- engine events -> Scorpi events -----

    def _on_event(self, ev):
        kind, d = ev.get("type", ""), ev.get("data") or {}
        if ev.get("_child"):
            return self._on_child(ev["_child"], ev.get("_title") or "", kind, d)
        if kind == "session.reasoning.delta":
            self.emit({"type": "thinking", "content": d.get("delta", "")})
        elif kind == "session.reasoning.ended":
            if (d.get("text") or "").strip():
                self.parts.append({"type": "thinking", "text": d["text"]})
        elif kind == "session.text.delta":
            self.emit({"type": "token", "content": d.get("delta", "")})
        elif kind == "session.text.ended":
            text = (d.get("text") or "").strip()
            if text:
                self.texts.append(text)
                self.parts.append({"type": "text", "text": text})
        elif kind == "session.tool.input.started":
            self.tool_names[d.get("id")] = d.get("name") or ""
        elif kind == "session.tool.called":
            self._tool_start(d)
        elif kind in ("session.tool.success", "session.tool.failed"):
            self._tool_end(d, kind == "session.tool.success")
        elif kind == "session.step.ended":
            self.cost += float(d.get("cost") or 0)
        elif kind == "session.step.failed":
            err = d.get("error") or {}
            if err.get("type") != "aborted":
                self.error = err.get("message") or "The model failed."
        elif kind == "permission.asked":
            threading.Thread(target=self._approve, args=(d,), daemon=True).start()
        elif kind == "form.created":
            self._ask(d.get("form") or {})
        elif kind == "session.execution.failed":
            err = d.get("error") or {}
            self.error = self.error or (err.get("message") if isinstance(err, dict) else str(err)) or "The job failed."
            self.ended.set()
        elif kind in ("session.execution.succeeded", "session.execution.interrupted"):
            self.ended.set()
        elif kind == "engine.lost":
            self.error = self.error or "The coding engine stopped unexpectedly."
            self.ended.set()

    def _tool_start(self, d):
        call = d.get("id") or os.urandom(4).hex()
        if call in self.tools:
            return
        name, args, files = _tool(d.get("name") or self.tool_names.get(call) or "tool", d.get("input") or {}, self.workdir)
        part = {"type": "tool", "id": call, "name": name, "args": args, "status": "running", "agent": "", "started": time.time(),
                "files": files}
        self.tools[call] = part
        self.parts.append(part)
        self.emit({"type": "tool_start", "id": call, "name": name, "args": args, "agent": ""})
        if name == "delegate":
            raw = d.get("input") or {}
            label = " ".join(str(raw.get("description") or args.get("agent") or "Helper").split())[:48]
            self.subs[call] = {"name": label, "kind": args.get("agent") or "", "task": str(raw.get("prompt") or args.get("task") or "")[:600],
                               "session": "", "tools": 0}
            self.emit({"type": "subagent", "id": call, "name": label, "task": self.subs[call]["task"], "status": "running",
                       "activity": "Starting", "tools": 0, "model": self.model_ref})

    def _tool_end(self, d, ok):
        call = d.get("id")
        part = self.tools.get(call)
        if not part or part["status"] != "running":
            return
        meta = d.get("metadata") or {}
        if ok:
            result = _texts(d.get("content"))
            ok = meta.get("exit") in (None, 0)
        else:
            err = d.get("error") or {}
            result = (err.get("message") if isinstance(err, dict) else str(err)) or "Failed."
            if isinstance(err, dict) and err.get("type") == "permission.rejected":
                result = "The user declined this action."
        display = None
        files = part.pop("files", [])
        if part["name"] in ("write_file", "edit_file") and ok and files:
            display = _file_card(files[0])
            self.files.extend(f for f in files if f not in self.files)
        elif part["name"] == "run_shell":
            display = {"type": "code", "language": "shell", "code": part["args"].get("command", "")[:12000], "stdout": result[-6000:],
                       "stderr": "", "exit": meta.get("exit", 0 if ok else 1), "images": [], "files": []}
        elif part["name"] == "update_plan":
            display = {"type": "plan", "steps": part["args"].get("steps") or []}
        elapsed = round(time.time() - part.pop("started", time.time()), 2)
        part.update(status="done" if ok else "error", ok=ok, result=result[:2000], display=display, elapsed=elapsed)
        self.emit({"type": "tool_end", "id": call, "name": part["name"], "ok": ok, "result": result[:2000], "display": display,
                   "elapsed": elapsed, "agent": ""})
        sub = self.subs.get(call)
        if sub:
            first = next((ln.strip() for ln in result.splitlines() if ln.strip()), "")
            status = "stopped" if self.cancelled else "done" if ok else "failed"
            self.emit({"type": "subagent", "id": call, "status": status, "tools": sub["tools"], "result": first[:300],
                       "activity": {"done": "Finished", "failed": "Failed", "stopped": "Stopped"}[status]})

    # ----- subagents (the engine's task tool) working in child sessions -----

    def _sub_for(self, sid, title):
        """The task call a child session belongs to: matched by its title (the task's description), else the oldest
        helper that has no session yet."""
        call = self.child_subs.get(sid)
        if call:
            return call
        free = [c for c, s in self.subs.items() if not s["session"] and self.tools.get(c, {}).get("status") == "running"]
        norm = lambda t: " ".join(str(t or "").lower().split())  # noqa: E731
        match = next((c for c in free if title and (norm(title) == norm(self.subs[c]["name"]) or norm(self.subs[c]["name"]) in norm(title))), None)
        call = match or (free[0] if free else None)
        if call:
            self.subs[call]["session"] = sid
            self.child_subs[sid] = call
        return call

    def _on_child(self, sid, title, kind, d):
        if kind == "permission.asked":
            # a helper's command or edit needs the same approval as the lead agent's
            return threading.Thread(target=self._approve, args=(d, sid), daemon=True).start()
        if kind == "form.created":
            return self._ask(d.get("form") or {}, sid)
        call = self._sub_for(sid, title)
        if not call:
            return None
        sub = self.subs[call]
        if kind == "session.tool.called":
            from .agents import activity
            name, args, files = _tool(d.get("name") or "tool", d.get("input") or {}, self.workdir)
            sub["tools"] += 1
            for f in files:
                if f not in self.files:
                    self.files.append(f)
            self.emit({"type": "subagent", "id": call, "activity": activity(name, args), "tools": sub["tools"]})
        elif kind == "session.reasoning.delta" and sub.get("thinking") is not True:
            sub["thinking"] = True
            self.emit({"type": "subagent", "id": call, "activity": "Thinking"})
        elif kind == "session.tool.success" or kind == "session.tool.failed":
            sub["thinking"] = False
        elif kind == "session.execution.failed":
            err = d.get("error") or {}
            self.emit({"type": "subagent", "id": call, "status": "failed",
                       "activity": "Failed: " + str((err.get("message") if isinstance(err, dict) else err) or "")[:140]})
        return None

    def _approve(self, d, session=""):
        """An engine permission request becomes a Scorpi approval card (Allow once / Always / Deny).
        session: the subagent session that asks, when it is not the job's own."""
        from .agent import _describe
        tool = PERMISSION_TOOLS.get(d.get("action"), d.get("action") or "action")
        resources = [str(r) for r in d.get("resources") or []]
        args = {"command": "\n".join(resources)} if tool == "run_shell" else {"path": ", ".join(resources)}
        decision = events.request_approval(self, tool, args, _describe(tool, args))
        if decision == "always":
            pol = dict(SETTINGS["tool_policy"] or {})
            pol[tool] = "allow"
            SETTINGS.update({"tool_policy": pol})
        reply = {"allow": "once", "always": "always"}.get(decision, "reject")
        body = {"decision": reply}
        if reply == "reject":
            body["message"] = "The user declined this action. Do not retry it; continue without it or ask what they prefer."
        try:
            SERVER.request("POST", "/api/session/%s/permission/%s/reply" % (session or self.session, d.get("id")), body)
        except Exception:
            traceback.print_exc()

    def _ask(self, form, session=""):
        fields = [{"key": f.get("key"), "title": f.get("title") or "", "description": f.get("description") or "",
                   "options": [{"value": o.get("value"), "label": o.get("label") or o.get("value"), "description": o.get("description") or ""}
                               for o in f.get("options") or []],
                   "custom": bool(f.get("custom", True))} for f in form.get("fields") or []]
        self.question = {"id": form.get("id"), "title": form.get("title") or "A question for you", "fields": fields}
        self.question_session = session or self.session
        QUESTIONS[form.get("id")] = self
        self.emit({"type": "question", **self.question})
        first = fields[0] if fields else {}
        notify.send("%s has a question" % (self.agent.get("name") or "Coder"), first.get("description") or first.get("title") or "Open Scorpi to answer.",
                    level="urgent", link="/c/" + self.conv_id)


def answer(answers, form_id):
    """The user's answer to an agent question ({field key: value})."""
    turn = QUESTIONS.pop(form_id, None)
    if not turn:
        raise ValueError("That question is no longer open.")
    clean = {str(k): (v if isinstance(v, (str, list, bool, int, float)) else str(v)) for k, v in (answers or {}).items()}
    SERVER.request("POST", "/api/session/%s/form/%s/reply" % (turn.question_session or turn.session, form_id), {"answer": clean})
    turn.question = None
    shown = "; ".join(", ".join(v) if isinstance(v, list) else str(v) for v in clean.values())
    turn.emit({"type": "question_done", "id": form_id, "answer": shown})
    turn.parts.append({"type": "text", "text": "> " + shown})
    return True


def start(conv_id, prompt, agent, workdir, session="", on_session=None, title=""):
    """Like agent.start_turn, but the turn runs on the coding engine. Returns the Run; on_session(id) receives the session."""
    turn = Turn(conv_id, title, agent, workdir, prompt, session, on_session)
    events.register_run(turn)
    return turn.begin()
