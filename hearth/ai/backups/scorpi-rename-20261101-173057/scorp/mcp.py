"""Model Context Protocol client. Connects to MCP servers over stdio or HTTP and exposes their tools to the agent."""

import json
import os
import re
import shutil
import subprocess
import threading
import urllib.request

from . import events
from .config import SETTINGS
from .tools import TOOLS, Result, ToolError, _parse_params

PROTOCOL = "2025-06-18"
CLIENT = {"name": "scorp", "version": "1.0"}

# One-click presets for free, popular MCP servers (need Node.js / uv on the host).
# scope: where the tools are offered — "chat" (conversations), "code" (coding projects) or "both"; the user can change it.
# env: keys the server needs, filled in by the user when adding it.
PRESETS = [
    # Two unrelated projects are both called "blender-mcp", and each server only talks to its own Blender add-on.
    {"name": "blender", "title": "Blender (official, Blender Lab)", "category": "3D & design", "scope": "both", "command": "uvx",
     "args": ["--from", "git+https://projects.blender.org/lab/blender_mcp.git", "blender-mcp"],
     "about": "Blender's own MCP server: inspect and edit scenes with Python, read the API docs and manual, take screenshots and renders.",
     "setup": "Needs the add-on from lab.blender.org/mcp-server enabled in Blender. With Claude Desktop's Blender extension installed, "
              "you can instead set the command to its blender-mcp.exe (in Claude Extensions\\ant.dir.gh.blender.blender-mcp\\.venv\\Scripts)."},
    {"name": "blender-addon", "title": "Blender (community add-on)", "category": "3D & design", "scope": "both", "command": "uvx", "args": ["blender-mcp"],
     "about": "Build and edit 3D scenes, plus Poly Haven assets and AI-generated models, through ahujasid's BlenderMCP add-on.",
     "setup": "Only with that add-on (addon.py from github.com/ahujasid/blender-mcp): in Blender open the sidebar (N) → BlenderMCP → Connect. "
              "It does not work with the official Blender Lab add-on."},
    {"name": "context7", "title": "Context7 docs", "category": "Coding", "scope": "code", "command": "npx", "args": ["-y", "@upstash/context7-mcp"],
     "about": "Up-to-date documentation and examples for thousands of libraries, so code uses current APIs."},
    {"name": "github", "title": "GitHub", "category": "Coding", "scope": "both", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
     "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": ""}, "about": "Repositories, issues, pull requests and code search on GitHub.",
     "setup": "Create a personal access token at github.com/settings/tokens and paste it."},
    {"name": "devtools", "title": "Chrome DevTools", "category": "Coding", "scope": "code", "command": "npx", "args": ["-y", "chrome-devtools-mcp@latest"],
     "about": "Open pages in Chrome, read the console and network, take screenshots and measure performance while debugging web apps."},
    {"name": "desktop-commander", "title": "Desktop Commander", "category": "Coding", "scope": "code", "command": "npx",
     "args": ["-y", "@wonderwhy-er/desktop-commander"], "about": "Long-running terminal sessions, process control and precise file edits."},
    {"name": "git", "title": "Git", "category": "Coding", "scope": "code", "command": "uvx", "args": ["mcp-server-git"],
     "about": "Inspect and operate on git repositories."},
    {"name": "docker", "title": "Docker", "category": "Coding", "scope": "code", "command": "uvx", "args": ["mcp-server-docker"],
     "about": "Run, inspect and manage containers, images and compose projects."},
    {"name": "sqlite", "title": "SQLite", "category": "Data", "scope": "both", "command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "~/scorp-mcp.db"],
     "about": "A SQLite database the agent can create tables in, query and analyse."},
    {"name": "playwright", "title": "Playwright browser", "category": "Web", "scope": "both", "command": "npx", "args": ["-y", "@playwright/mcp@latest"],
     "about": "Microsoft's browser automation server: navigate, click and fill forms in a real browser."},
    {"name": "fetch", "title": "Fetch", "category": "Web", "scope": "both", "command": "uvx", "args": ["mcp-server-fetch"],
     "about": "Fetch URLs and convert pages to markdown."},
    {"name": "duckduckgo", "title": "DuckDuckGo search", "category": "Research", "scope": "chat", "command": "uvx", "args": ["duckduckgo-mcp-server"],
     "about": "Free web search and page content, no key needed."},
    {"name": "wikipedia", "title": "Wikipedia", "category": "Research", "scope": "chat", "command": "uvx", "args": ["wikipedia-mcp"],
     "about": "Search and read Wikipedia articles, summaries and sections."},
    {"name": "arxiv", "title": "arXiv papers", "category": "Research", "scope": "chat", "command": "uvx", "args": ["arxiv-mcp-server"],
     "about": "Search, download and read scientific papers from arXiv."},
    {"name": "youtube", "title": "YouTube transcripts", "category": "Research", "scope": "chat", "command": "npx",
     "args": ["-y", "@kimtaeyoon83/mcp-server-youtube-transcript"], "about": "Get the transcript of a YouTube video to summarise or quote it."},
    {"name": "filesystem", "title": "Filesystem", "category": "Files", "scope": "both", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "~"],
     "about": "Read/write files under your home folder through the reference MCP server."},
    {"name": "memory", "title": "Knowledge graph memory", "category": "Thinking", "scope": "chat", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"],
     "about": "Entity/relation memory graph."},
    {"name": "thinking", "title": "Sequential thinking", "category": "Thinking", "scope": "both", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
     "about": "Structured step-by-step reasoning tool."},
    {"name": "time", "title": "Time", "category": "Thinking", "scope": "chat", "command": "uvx", "args": ["mcp-server-time"],
     "about": "Time and timezone conversions."},
]

SCOPES = ("both", "chat", "code")


def scope_of(cfg):
    return cfg.get("scope") if cfg.get("scope") in SCOPES else "both"


def _safe(name):
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)[:40]


class StdioServer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.pending = {}
        self.next_id = 1
        self.lock = threading.Lock()
        self.stderr = []

    def start(self):
        command = os.path.expandvars(os.path.expanduser(self.cfg["command"]))
        resolved = shutil.which(command) or command
        args = [os.path.expanduser(a) if a.startswith("~") else a for a in self.cfg.get("args") or []]
        env = dict(os.environ, **{k: str(v) for k, v in (self.cfg.get("env") or {}).items()})
        self.proc = subprocess.Popen(
            [resolved] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), shell=False,
        )
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "id" in msg and msg["id"] in self.pending and ("result" in msg or "error" in msg):
                slot = self.pending[msg["id"]]
                slot["msg"] = msg
                slot["event"].set()
        for slot in list(self.pending.values()):
            slot["event"].set()

    def _read_err(self):
        for raw in self.proc.stderr:
            self.stderr.append(raw.decode("utf-8", "replace").rstrip())
            self.stderr = self.stderr[-40:]

    def request(self, method, params=None, timeout=60):
        with self.lock:
            rid = self.next_id
            self.next_id += 1
            slot = {"event": threading.Event(), "msg": None}
            self.pending[rid] = slot
            payload = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                payload["params"] = params
            self.proc.stdin.write((json.dumps(payload) + "\n").encode())
            self.proc.stdin.flush()
        ok = slot["event"].wait(timeout)
        self.pending.pop(rid, None)
        if not ok:
            raise ToolError("MCP server timed out on %s" % method)
        if slot["msg"] is None:
            raise ToolError("MCP server exited. %s" % " ".join(self.stderr[-3:]))
        if "error" in slot["msg"]:
            raise ToolError("MCP error: %s" % slot["msg"]["error"].get("message"))
        return slot["msg"].get("result") or {}

    def notify(self, method, params=None):
        with self.lock:
            payload = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                payload["params"] = params
            self.proc.stdin.write((json.dumps(payload) + "\n").encode())
            self.proc.stdin.flush()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class HttpServer:
    """Streamable HTTP transport (JSON or SSE responses)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.session = None
        self.next_id = 1
        self.stderr = []

    def start(self):
        pass

    def _post(self, payload, timeout=60):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOL}
        headers.update(self.cfg.get("headers") or {})
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.cfg["url"], data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            self.session = resp.headers.get("Mcp-Session-Id") or self.session
            ctype = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", "replace")
        if not body.strip():
            return None
        if "event-stream" in ctype:
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if msg.get("id") == payload.get("id"):
                        return msg
            return None
        return json.loads(body)

    def request(self, method, params=None, timeout=60):
        rid = self.next_id
        self.next_id += 1
        payload = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        msg = self._post(payload, timeout) or {}
        if "error" in msg:
            raise ToolError("MCP error: %s" % msg["error"].get("message"))
        return msg.get("result") or {}

    def notify(self, method, params=None):
        try:
            self._post({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})
        except Exception:
            pass

    def alive(self):
        return True

    def stop(self):
        pass


class Manager:
    def __init__(self):
        self.servers = {}  # name -> {"cfg","client","tools","status","error"}
        self.lock = threading.Lock()

    def configs(self):
        return [c for c in (SETTINGS["mcp_servers"] or []) if c.get("name")]

    def start_all(self):
        for cfg in self.configs():
            if cfg.get("enabled", True):
                threading.Thread(target=self.connect, args=(cfg,), daemon=True).start()

    def connect(self, cfg):
        name = _safe(cfg["name"])
        self.disconnect(name)
        entry = {"cfg": cfg, "client": None, "tools": [], "status": "connecting", "error": ""}
        with self.lock:
            self.servers[name] = entry
        try:
            client = HttpServer(cfg) if cfg.get("url") else StdioServer(cfg)
            if not cfg.get("url") and not (shutil.which(os.path.expanduser(cfg.get("command", ""))) or os.path.exists(os.path.expanduser(cfg.get("command", "")))):
                raise ToolError("Command %r not found on the host. Install it (Node.js gives npx, uv gives uvx)." % cfg.get("command"))
            client.start()
            entry["client"] = client
            client.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT}, timeout=120)
            client.notify("notifications/initialized")
            tools, cursor = [], None
            while True:
                result = client.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=60)
                tools.extend(result.get("tools") or [])
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            entry["tools"] = tools
            entry["status"] = "connected"
            self._register(name, entry)
        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = str(exc) + (" — " + " ".join(entry["client"].stderr[-2:]) if entry.get("client") and entry["client"].stderr else "")
        events.publish("mcp", {"server": name, "status": entry["status"]})

    def _register(self, name, entry):
        for t in entry["tools"]:
            tool_name = ("mcp_%s_%s" % (name, _safe(t["name"])))[:64]
            schema = t.get("inputSchema") or {"type": "object", "properties": {}}
            if "properties" not in schema:
                schema = dict(schema, properties={})
            annotations = t.get("annotations") or {}
            risk = "read" if annotations.get("readOnlyHint") else "danger" if annotations.get("destructiveHint") else "write"

            def runner(ctx, _server=name, _tool=t["name"], **kwargs):
                return self.call(_server, _tool, kwargs)

            TOOLS[tool_name] = {
                "name": tool_name,
                "description": "[%s] %s" % (entry["cfg"]["name"], (t.get("description") or t["name"])[:500]),
                "schema": schema if isinstance(schema, dict) else _parse_params({}),
                "group": "mcp:" + name,
                "scope": scope_of(entry["cfg"]),
                "risk": risk,
                "requires": (),
                "platforms": None,
                "fn": runner,
                "mcp": {"server": name, "tool": t["name"]},
            }

    def call(self, server, tool_name, args):
        entry = self.servers.get(server)
        if not entry or entry["status"] != "connected" or not entry["client"].alive():
            if entry:
                self.connect(entry["cfg"])
                entry = self.servers.get(server)
            if not entry or entry["status"] != "connected":
                raise ToolError("MCP server %s is not connected." % server)
        result = entry["client"].request("tools/call", {"name": tool_name, "arguments": args or {}}, timeout=300)
        texts, images = [], []
        for item in result.get("content") or []:
            kind = item.get("type")
            if kind == "text":
                texts.append(item.get("text", ""))
            elif kind == "image":
                images.append(item.get("data", ""))
            elif kind == "resource":
                res = item.get("resource") or {}
                texts.append(res.get("text") or "[resource %s]" % res.get("uri", ""))
        if result.get("structuredContent") and not texts:
            texts.append(json.dumps(result["structuredContent"], ensure_ascii=False, indent=1))
        return Result("\n".join(texts)[:30000] or "(no output)", ok=not result.get("isError"), images=images[:2])

    def disconnect(self, name):
        with self.lock:
            entry = self.servers.pop(name, None)
        for key in [k for k, v in TOOLS.items() if v.get("group") == "mcp:" + name]:
            TOOLS.pop(key, None)
        if entry and entry.get("client"):
            try:
                entry["client"].stop()
            except Exception:
                pass

    def reload(self):
        configured = {_safe(c["name"]): c for c in self.configs()}
        for name in list(self.servers):
            if name not in configured or not configured[name].get("enabled", True):
                self.disconnect(name)
        for name, cfg in configured.items():
            current = self.servers.get(name)
            if cfg.get("enabled", True) and (not current or current["cfg"] != cfg or current["status"] == "error"):
                threading.Thread(target=self.connect, args=(cfg,), daemon=True).start()

    def status(self):
        out = []
        for cfg in self.configs():
            name = _safe(cfg["name"])
            entry = self.servers.get(name) or {}
            out.append({"config": cfg, "name": name, "scope": scope_of(cfg),
                        "status": entry.get("status", "disabled" if not cfg.get("enabled", True) else "idle"),
                        "error": entry.get("error", ""), "tools": [{"name": t["name"], "description": (t.get("description") or "")[:160]} for t in entry.get("tools", [])]})
        return out

    def shutdown(self):
        for name in list(self.servers):
            self.disconnect(name)

    def for_engine(self):
        """The enabled servers meant for coding, in the coding engine's (OpenCode's) config format."""
        out = {}
        for cfg in self.configs():
            if not cfg.get("enabled", True) or scope_of(cfg) == "chat":
                continue
            if cfg.get("url"):
                out[_safe(cfg["name"])] = {"type": "remote", "url": cfg["url"], "headers": cfg.get("headers") or {}, "enabled": True}
            else:
                args = [os.path.expanduser(a) if a.startswith("~") else a for a in cfg.get("args") or []]
                out[_safe(cfg["name"])] = {"type": "local", "command": [cfg["command"]] + args,
                                           "environment": {k: str(v) for k, v in (cfg.get("env") or {}).items()}, "enabled": True}
        return out


MANAGER = Manager()
