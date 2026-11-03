"""HTTP(S) server: static app, REST API, NDJSON run streams and SSE push events."""

import base64
import errno
import gzip
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import queue
import re
import shutil
import socket
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import VERSION, agent, agents, db, discord, events, knowledge, llm, looks, music, notify, opencode, scheduler, voice
from .config import DATA_DIR, IS_WINDOWS, PUBLIC_DIR, SETTINGS, UPLOADS, WORKSPACE
from .mcp import MANAGER, PRESETS
from .tools import GROUPS, TOOLS, ToolError, catalog, check_access, module_available
from .tools.agenda import ics_feed, parse_when
from .tools.system import detect_services
from .tools.web import _public_host

MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json", ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".ico": "image/x-icon", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8", ".csv": "text/csv; charset=utf-8",
    ".mmd": "text/plain; charset=utf-8", ".woff2": "font/woff2", ".mp4": "video/mp4", ".webm": "video/webm",
}
_IMPORT = re.compile(r"""(?:import|export)\s[^'"]*?from\s*['"](\.[^'"]+)['"]|import\s*['"](\.[^'"]+)['"]""")


def module_graph(entry="js/main.js"):
    """Every script the app loads at start (found through the static imports), as paths below the public folder."""
    seen, todo = [], [entry]
    while todo:
        rel = todo.pop(0)
        if rel in seen:
            continue
        try:
            with open(os.path.join(PUBLIC_DIR, rel), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        seen.append(rel)
        for m in _IMPORT.finditer(text):
            todo.append(os.path.normpath(os.path.join(os.path.dirname(rel), m.group(1) or m.group(2))).replace("\\", "/"))
    return seen


def app_page():
    with open(os.path.join(PUBLIC_DIR, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    links = "".join('<link rel="modulepreload" href="/%s">\n' % rel for rel in module_graph())
    return html.replace("</head>", links + "</head>", 1).encode("utf-8")


PUBLIC_PATHS = ("/api/auth/", "/api/hooks/", "/api/calendar.ics")
_failed_logins = {}
ROUTES = []


def route(method, pattern):
    def wrap(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return wrap


def session_token(label=b"scorp"):
    return hmac.new(SETTINGS["secret"].encode(), label + b"-session-v1:" + SETTINGS["pin"].encode(), hashlib.sha256).hexdigest()


def user_token(name):
    """A login: the name it was issued for, then a signature over the name and the PIN (a new PIN retires every login)."""
    name = db.clean_name(name)
    mac = hmac.new(SETTINGS["secret"].encode(), b"scorp-user-v1:" + SETTINGS["pin"].encode() + b":" + name.casefold().encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(name.encode()).decode().rstrip("=") + "." + mac


def user_from_token(token):
    """The name a login token was issued for, or None when it is not ours."""
    try:
        enc, mac = (token or "").split(".", 1)
        name = db.clean_name(base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode())
    except (ValueError, UnicodeDecodeError):
        return None
    return name if name and hmac.compare_digest(mac, user_token(name).split(".", 1)[1]) else None


def guess_mime(path):
    ext = os.path.splitext(path)[1].lower()
    return MIME.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    server_version = "Scorp/" + VERSION
    # keep-alive: one connection carries the app's scripts, styles, API calls and previews instead of a new TCP/TLS
    # handshake for each (the app loads ~40 files). It is what makes a phone on Tailscale feel slow.
    protocol_version = "HTTP/1.1"
    timeout = 120

    def log_message(self, fmt, *args):
        pass

    def setup(self):
        if isinstance(self.request, ssl.SSLSocket):
            self.request.settimeout(30)
            self.request.do_handshake()
            self.request.settimeout(None)
        super().setup()

    # ---------- helpers ----------

    def host_name(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            return host[1:].split("]")[0]
        return host.rsplit(":", 1)[0] if host.count(":") == 1 else host

    @property
    def is_local(self):
        host = self.client_address[0]
        loop = host in ("127.0.0.1", "::1") or host.startswith("::ffff:127.")
        # A foreign Host header on a loopback request means DNS rebinding: never trust it as local.
        return loop and not self.headers.get("X-Forwarded-For") and self.host_name() in ("localhost", "127.0.0.1", "::1", "")

    def cross_site(self):
        """True when a browser on another site is trying to send us a state-changing request (CSRF)."""
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            return origin == "null"
        try:
            o_host = urllib.parse.urlparse(origin).netloc.lower()
        except ValueError:
            return True
        return o_host != (self.headers.get("Host") or "").lower()

    def authed(self):
        """Signed in? Also records who: a name from the login, or None for the host PC (and when no PIN is needed), which is the owner."""
        self.who = None
        if not SETTINGS["require_pin"] or self.is_local:
            return True
        # nobody skips the PIN on a network, not even devices of the tailnet: everyone also says who they are
        cookie = self.headers.get("Cookie") or ""
        match = re.search(r"(?:^|;\s*)scorp=([\w.=-]+)", cookie)
        bearer = (self.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        self.who = (match and user_from_token(match.group(1))) or user_from_token(bearer)
        return bool(self.who)

    @property
    def user_key(self):
        return db.user_key(self.who) if getattr(self, "who", None) else db.default_user()

    @property
    def is_owner(self):
        return self.user_key == db.default_user()

    def wants_gzip(self):
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def body_bytes_send(self, body, content_type, status=200, headers=None, compress=True):
        """Write one complete response, compressed when the client asked and it is text."""
        raw = body
        encoded = False
        texty = any(t in content_type for t in ("text/", "json", "javascript", "svg", "xml"))
        if compress and texty and len(raw) > 700 and self.wants_gzip():
            raw = gzip.compress(raw, 5)
            encoded = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if compress and texty:
            self.send_header("Vary", "Accept-Encoding")
        if encoded:
            self.send_header("Content-Encoding", "gzip")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            self.wfile.write(raw)
        except OSError:
            pass

    def send_json(self, payload, status=200, headers=None):
        body = json.dumps(payload, default=str).encode()
        head = {"Cache-Control": "no-store"}
        head.update(headers or {})
        self.body_bytes_send(body, "application/json", status=status, headers=head)

    def error(self, message, status=400):
        self.send_json({"error": message}, status)

    def body_bytes(self, limit=64 * 1024 * 1024):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ToolError("Upload too large.")
        self._body_read = True
        return self.rfile.read(length) if length else b""

    def body_json(self):
        try:
            return json.loads(self.body_bytes().decode("utf-8") or "{}")
        except ValueError:
            return {}

    def stream_start(self, ctype="application/x-ndjson"):
        # a stream has no length: HTTP/1.1 must end it by closing the connection
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()

    def stream_line(self, payload):
        self.wfile.write((json.dumps(payload, default=str) + "\n").encode())
        self.wfile.flush()

    def send_file(self, path, download=False, cache=False, headers=None):
        try:
            stat = os.stat(path)
            size = stat.st_size
        except OSError:
            return self.error("not found", 404)
        etag = '"%x-%x"' % (int(stat.st_mtime), size)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "public, max-age=0, must-revalidate" if cache else "no-cache")
            self.end_headers()
            return
        ranged = self.headers.get("Range")
        start, end = 0, size - 1
        if ranged:
            m = re.match(r"bytes=(\d*)-(\d*)", ranged)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else end
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
        ctype = guess_mime(path)
        body = None
        # a script or stylesheet compresses well and the app loads dozens of them; pictures and media do not
        if not ranged and size <= 3 * 1024 * 1024 and any(t in ctype for t in ("text/", "json", "javascript", "svg", "xml")):
            try:
                with open(path, "rb") as fh:
                    body = fh.read()
            except OSError:
                return self.error("not found", 404)
            self.body_bytes_send(
                body[start:end + 1], ctype, status=206 if ranged else 200,
                headers={"ETag": etag, "Accept-Ranges": "bytes",
                         "Cache-Control": "public, max-age=0, must-revalidate" if cache else "no-cache",
                         **({"Content-Disposition": "attachment; filename*=UTF-8''%s" % urllib.parse.quote(os.path.basename(path))} if download else {}),
                         **(headers or {})},
            )
            return
        self.send_response(206 if ranged else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        if ranged:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Cache-Control", "public, max-age=300" if cache else "no-cache")
        if download:
            name = os.path.basename(path)
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%s" % urllib.parse.quote(name))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except OSError:
                    return
                remaining -= len(chunk)

    # ---------- dispatch ----------

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def do_HEAD(self):
        self.dispatch("GET")

    def dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        self.query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        self._body_read = False
        if path.startswith("/p/") and method == "GET":
            return serve_preview(self, path)
        if path.startswith("/f/") and method == "GET":
            return serve_file_view(self, path)
        if not path.startswith("/api/"):
            return self.static(path)
        if method != "GET" and self.cross_site() and not path.startswith("/api/hooks/"):
            self.close_connection = True
            return self.error("cross-site request blocked", 403)
        authed = self.authed()
        if not path.startswith(PUBLIC_PATHS) and not authed:
            self.close_connection = True
            return self.error("unauthorized", 401)
        db.set_user(self.user_key)  # this thread now works for whoever signed in
        for m, pattern, fn in ROUTES:
            if m != method:
                continue
            match = pattern.match(path)
            if match:
                try:
                    return fn(self, *match.groups())
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                except ToolError as exc:
                    return self.error(str(exc), 400)
                except Exception as exc:
                    traceback.print_exc()
                    try:
                        return self.error("%s: %s" % (type(exc).__name__, exc), 500)
                    except Exception:
                        return
                finally:
                    # HTTP/1.1 reuses the connection: an unread request body would be parsed as the next request
                    self._drain_body()
        self.close_connection = True
        self.error("not found", 404)

    def _drain_body(self):
        if getattr(self, "_body_read", False) or self.command not in ("POST", "PATCH", "DELETE"):
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if 0 < length <= 8 * 1024 * 1024:
            try:
                self.rfile.read(length)
            except OSError:
                self.close_connection = True
        elif length:
            self.close_connection = True  # a big body nobody read: let the connection end instead of desyncing it

    def secure_redirect(self, path):
        """Send phones and other LAN devices on plain HTTP to the HTTPS port: browsers block the microphone otherwise."""
        if isinstance(self.connection, ssl.SSLSocket) or not HTTPS_STATE["running"]:
            return False
        client = self.client_address[0]
        if client in ("127.0.0.1", "::1") or client.startswith("::ffff:127.") or self.headers.get("X-Forwarded-Proto"):
            return False  # this PC, or a proxy (tailscale serve) that already terminates TLS
        host = self.host_name()
        if ":" in host:
            host = "[%s]" % host
        query = urllib.parse.urlparse(self.path).query
        self.send_response(302)
        self.send_header("Location", "https://%s:%d%s%s" % (host, HTTPS_STATE["port"], path, "?" + query if query else ""))
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return True

    def send_app(self):
        """The app page, telling the browser up front which scripts it will need. Without that it finds them one import at a
        time, a round trip per level, which left the page blank for seconds on a slow link (a PC browser through the internet)."""
        body = app_page()
        etag = '"%s"' % hashlib.sha1(body).hexdigest()[:20]
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        self.body_bytes_send(body, "text/html; charset=utf-8", headers={"ETag": etag, "Cache-Control": "no-cache"})

    def static(self, path):
        rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
        if rel == "index.html" or "." not in os.path.basename(rel):
            if self.secure_redirect(path):
                return  # only pages: scripts, icons and the API keep working on HTTP
        full = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        public_root = os.path.normpath(PUBLIC_DIR)
        if not (full == public_root or full.startswith(public_root + os.sep)) or not os.path.isfile(full):
            if "." in os.path.basename(rel):
                return self.error("not found", 404)
            full = os.path.join(PUBLIC_DIR, "index.html")  # client-side routes
        if os.path.normpath(full) == os.path.normpath(os.path.join(PUBLIC_DIR, "index.html")):
            return self.send_app()
        self.send_file(full, cache=False)


# ---------- auth ----------

@route("GET", "/api/auth/status")
def auth_status(h):
    authed = h.authed()
    h.send_json({"required": bool(SETTINGS["require_pin"]) and not h.is_local, "authed": authed, "local": h.is_local, "version": VERSION,
                 "user": db.display_name(h.user_key) if authed else ""})


@route("POST", "/api/auth/login")
def auth_login(h):
    ip = h.client_address[0]
    now = time.time()
    attempts = [t for t in _failed_logins.get(ip, []) if now - t < 600]
    if len(attempts) >= 8:
        return h.error("Too many attempts. Wait 10 minutes.", 429)
    body = h.body_json()
    pin = str(body.get("pin", "")).strip()
    if not hmac.compare_digest(pin, SETTINGS["pin"]):
        attempts.append(now)
        _failed_logins[ip] = attempts
        return h.error("Wrong PIN.", 403)
    name = db.clean_name(body.get("name"))
    if not name:
        return h.error("Enter your name.", 400)  # the PIN was right: not a failed attempt
    _failed_logins.pop(ip, None)
    name = db.touch_user(name)
    token = user_token(name)
    secure = "; Secure" if isinstance(h.connection, ssl.SSLSocket) else ""
    h.send_json({"ok": True, "token": token, "name": name},
                headers={"Set-Cookie": "scorp=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax%s" % (token, secure)})


@route("POST", "/api/auth/logout")
def auth_logout(h):
    h.send_json({"ok": True}, headers={"Set-Cookie": "scorp=; Path=/; Max-Age=0"})


@route("POST", "/api/auth/rotate")
def auth_rotate(h):
    if not h.is_local:
        return h.error("Only from the host PC.", 403)
    import secrets as _s
    SETTINGS.update({"pin": "%06d" % _s.randbelow(1_000_000)})
    h.send_json({"pin": SETTINGS["pin"]})


# ---------- status & settings ----------

def lan_addresses():
    global _LAN_CACHE
    now = time.time()
    with _LAN_LOCK:
        if _LAN_CACHE["at"] and now - _LAN_CACHE["at"] < 60:
            return list(_LAN_CACHE["value"])
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    out = []
    for ip in sorted(ips):
        addr = ipaddress.ip_address(ip)
        if addr.is_loopback:
            continue
        kind = "tailscale" if addr in ipaddress.ip_network("100.64.0.0/10") else "lan"
        out.append({"ip": ip, "kind": kind})
    with _LAN_LOCK:
        _LAN_CACHE.update(at=now, value=out)
    return out


_LAN_CACHE = {"at": 0.0, "value": []}
_LAN_LOCK = threading.Lock()
_LLM_STATUS = {"at": 0.0, "value": None}
_LLM_LOCK = threading.Lock()


def llm_status_cached():
    """The provider probe is an HTTP round trip; several devices polling /api/status share one answer for a few seconds."""
    with _LLM_LOCK:
        if _LLM_STATUS["value"] is not None and time.time() - _LLM_STATUS["at"] < 8:
            return _LLM_STATUS["value"]
    value = llm.status()
    with _LLM_LOCK:
        _LLM_STATUS.update(at=time.time(), value=value)
    return value


@route("GET", "/api/status")
def status(h):
    llm_state = llm_status_cached()
    https = SETTINGS["https_port"] if HTTPS_STATE["running"] else None
    h.send_json({
        "version": VERSION,
        "host": socket.gethostname(),
        "platform": "windows" if IS_WINDOWS else os.name,
        "local": h.is_local,
        "llm": {"provider": SETTINGS["provider"], "online": llm_state["online"], "error": llm_state.get("error", ""),
                "models": [m for m in llm_state["models"] if not m.get("cloud")], "model": SETTINGS["model"] or (llm.default_model() if llm_state["online"] else "")},
        "voice": {"stt": voice.stt_available(), "stt_local": voice.local_stt_available(), "stt_cloud": voice.cloud_stt_provider(),
                  "engines": voice.engines()},
        "modules": {m: module_available(m) for m in ("pypdf", "docx", "openpyxl", "fpdf", "pptx", "pandas", "matplotlib", "faster_whisper",
                                                      "edge_tts", "pyautogui", "pygetwindow", "playwright", "psutil", "ddgs", "cv2", "webview", "cryptography")},
        "network": {"port": SETTINGS["port"], "https_port": https, "addresses": lan_addresses(), "https_error": HTTPS_STATE["error"]},
        "counts": {"knowledge": knowledge.doc_count(), "memories": (db.one("SELECT COUNT(*) n FROM memories WHERE owner=?", (h.user_key,)) or {}).get("n", 0),
                   "unread": (db.one("SELECT COUNT(*) n FROM notifications WHERE read=0") or {}).get("n", 0)},
        "approvals": [a for a in events.pending_approvals() if mine_event(a, h.user_key)],
        "runs": [r.summary() for r in events.active_runs() if not r.done and mine_event({"conv_id": r.conv_id}, h.user_key)],
        "workspace": WORKSPACE,
    })


@route("GET", "/api/settings")
def settings_get(h):
    data = SETTINGS.public()
    data["code_engine"] = bool(opencode.binary())
    if h.is_local:
        data["pin"] = SETTINGS["pin"]
    if not h.is_owner:
        # a guest sees their own name, neither the owner's name nor the owner's standing instructions,
        # and no MCP servers (their env holds tokens for programs on this PC)
        data["user_name"] = db.display_name(h.user_key)
        data["system_prompt"] = ""
        data["mcp_servers"] = []
    h.send_json(data)


@route("POST", "/api/settings")
def settings_set(h):
    patch = h.body_json()
    if not h.is_local:
        for key in ("require_pin", "pin", "allowed_paths", "blocked_paths"):
            patch.pop(key, None)  # security settings only from the host itself
    if not h.is_owner:
        # a guest may change their own appearance and voice, not the host's security, files, models or programs
        for key in ("user_name", "system_prompt", "mcp_servers", "provider", "ollama_url", "openai_url", "openai_key",
                    "tool_policy", "tool_mode", "tool_groups", "allowed_paths", "blocked_paths", "secret", "ha_url", "ha_token",
                    "discord_presence", "discord_client_id", "port", "https_port", "fish_api_key", "fish_model", "fish_voice",
                    "tts_engine", "tts_voice", "desktop_notifications", "ntfy_topic"):
            patch.pop(key, None)
        for key in [k for k in patch if k.startswith("key_") or k == "cloud_anon"]:
            patch.pop(key, None)
    before = (SETTINGS["provider"], SETTINGS["ollama_url"], SETTINGS["openai_url"])
    owner_before = db.default_user()
    if "user_name" in patch:
        wanted = db.user_key(patch.get("user_name"))
        if wanted and wanted != owner_before and db.one("SELECT 1 AS x FROM users WHERE key=? AND key<>?", (wanted, owner_before)):
            return h.error("That name is already used by someone else. Pick another name.", 409)
    SETTINGS.update(patch)
    if "user_name" in patch:
        # the owner is known by their name: renaming must carry their chats and memories along, not orphan them
        owner_after = db.default_user()
        if owner_after != owner_before:
            db.rename_owner(owner_before, owner_after)
    if before != (SETTINGS["provider"], SETTINGS["ollama_url"], SETTINGS["openai_url"]):
        llm._caps_cache.clear()
    if any(k in patch for k in ("provider", "model", "vision_model", "ollama_url", "openai_url")) or any(k.startswith("key_") for k in patch):
        _LLM_STATUS.update(at=0, value=None)  # the next status asks the new provider
        llm._slow.clear()  # a different model or a new key deserves a fresh try
        llm._slow_models.clear()
        llm._model_noticed.clear()
    if any(k.startswith("key_") or k == "cloud_anon" for k in patch):
        llm._cloud_cache.clear()
        llm._slow.clear()  # a new key or provider should be tried at once
        llm._slow_models.clear()
        llm._model_noticed.clear()
    if "mcp_servers" in patch:
        MANAGER.reload()
    if "fish_api_key" in patch:
        voice._voice_cache.pop("fish", None)
    events.publish("settings", {})
    settings_get(h)


# ---------- models ----------

@route("GET", "/api/models")
def models(h):
    try:
        items = llm.list_models()
    except Exception as exc:
        return h.send_json({"models": [], "error": str(exc)})
    h.send_json({"models": items, "current": SETTINGS["model"] or llm.default_model()})


@route("GET", "/api/cloud")
def cloud(h):
    h.send_json({"providers": llm.cloud_overview(refresh=h.query.get("refresh") == "1")})


@route("GET", "/api/models/caps")
def model_caps(h):
    h.send_json({"caps": sorted(llm.capabilities(h.query.get("model", "")))})


@route("POST", "/api/models/pull")
def models_pull(h):
    name = str(h.body_json().get("name", "")).strip()
    if not name:
        return h.error("model name required")
    try:
        upstream = llm.pull_model(name)
    except Exception as exc:
        return h.error("Could not reach Ollama: %s" % exc, 502)
    h.stream_start()
    with upstream:
        for line in upstream:
            h.wfile.write(line)
            h.wfile.flush()
    llm._caps_cache.pop(name, None)


@route("POST", "/api/models/delete")
def models_delete(h):
    llm.delete_model(str(h.body_json().get("name", "")))
    h.send_json({"ok": True})


# ---------- conversations & chat ----------

@route("GET", "/api/conversations")
def conversations(h):
    rows = db.list_conversations()
    active = {r.conv_id for r in events.active_runs() if not r.done}  # only of the chats listed, which are this person's
    for row in rows:
        row["running"] = row["id"] in active
    h.send_json({"conversations": rows})


@route("POST", "/api/conversations")
def conversation_create(h):
    body = h.body_json()
    h.send_json(db.create_conversation(title=body.get("title") or "New conversation", skill=body.get("skill", "")))


@route("GET", "/api/conversations/([a-f0-9]+)")
def conversation_get(h, cid):
    conv = db.get_my_conversation(cid)
    if not conv:
        return h.error("not found", 404)
    run = events.active_run_for(cid)
    msgs = db.get_messages(cid)
    for m in msgs:
        m["data"].pop("model_content", None)
    job = agents.job_for_conversation(cid)  # an agent's own chat: the prompt box shows that agent and its model
    h.send_json({"conversation": conv, "messages": msgs, "run": run.summary() if run else None, "job": job.public() if job else None})


@route("PATCH", "/api/conversations/([a-f0-9]+)")
def conversation_patch(h, cid):
    if not db.get_my_conversation(cid):
        return h.error("not found", 404)
    body = h.body_json()
    fields = {}
    if "title" in body:
        fields["title"] = str(body["title"])[:120]
    if "pinned" in body:
        fields["pinned"] = 1 if body["pinned"] else 0
    if fields:
        db.touch_conversation(cid, **fields)
    events.publish("conversation", {"id": cid})
    h.send_json(db.get_conversation(cid))


@route("DELETE", "/api/conversations/([a-f0-9]+)")
def conversation_delete(h, cid):
    if not db.get_my_conversation(cid):
        return h.error("not found", 404)
    run = events.active_run_for(cid)
    if run:
        run.cancel()
    db.delete_conversation(cid)
    events.publish("conversation", {"id": cid, "deleted": True})
    h.send_json({"ok": True})


@route("POST", "/api/conversations/([a-f0-9]+)/truncate")
def conversation_truncate(h, cid):
    if not db.get_my_conversation(cid):
        return h.error("not found", 404)
    mid = h.body_json().get("message_id")
    if mid:
        db.delete_messages_from(cid, mid)
    h.send_json({"ok": True})


@route("GET", "/api/conversations/([a-f0-9]+)/export")
def conversation_export(h, cid):
    conv = db.get_my_conversation(cid)
    if not conv:
        return h.error("not found", 404)
    lines = ["# %s" % conv["title"], ""]
    for m in db.get_messages(cid):
        who = "You" if m["role"] == "user" else "Scorp"
        lines += ["### %s · %s" % (who, time.strftime("%Y-%m-%d %H:%M", time.localtime(m["created"]))), "", m["content"] or "", ""]
    body = "\n".join(lines).encode()
    h.send_response(200)
    h.send_header("Content-Type", "text/markdown; charset=utf-8")
    h.send_header("Content-Disposition", "attachment; filename*=UTF-8''%s.md" % urllib.parse.quote(re.sub(r"[^\w -]", "", conv["title"])[:60] or "chat"))
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _run_visible(h, run):
    """A run belongs to the conversation it works in; another person must not read or steer it."""
    if not run:
        return False
    conv = db.get_conversation(run.conv_id) if getattr(run, "conv_id", "") else None
    return not conv or db.can_see(conv, h.user_key)


def _job_visible(h, job):
    if not job:
        return False
    conv = db.get_conversation(job.conv_id) if getattr(job, "conv_id", "") else None
    return not conv or db.can_see(conv, h.user_key)


def follow_run(h, run, start=0):
    h.stream_start()
    h.stream_line({"type": "run", "run_id": run.id, "conv_id": run.conv_id})
    for event in run.follow(start):
        h.stream_line(event)


@route("POST", "/api/chat")
def chat(h):
    body = h.body_json()
    text = str(body.get("text") or "").strip()
    attachments = body.get("attachments") or []
    if not text and not attachments:
        return h.error("empty message")
    cid = body.get("conversation_id")
    conv = db.get_my_conversation(cid) if cid else None  # someone else's chat id just starts a new chat
    project = str(body.get("project") or "")
    if project and not conv:
        # the Code area: a message in a project goes to its agent, or starts the coder on it
        job = agents.latest_job(project)
        if job and db.get_conversation(job.conv_id):
            conv = db.get_conversation(job.conv_id)
        else:
            try:
                job = agents.assign("coder", text, title=body.get("title") or project.replace("-", " ").capitalize(), project=project)
            except ValueError as exc:
                return h.error(str(exc))
            return follow_run(h, job.run)
    if not conv:
        conv = db.create_conversation(skill=body.get("skill", ""))
    music.note_device(conv["id"], body.get("device"))  # "play some jazz" plays on the device that asked
    job = agents.job_for_conversation(conv["id"])
    if job and not attachments:
        # an agent job's own conversation: the message goes to the agent, which carries on in the same chat
        busy = events.active_run_for(conv["id"])
        try:
            job.message(text)
        except Exception as exc:
            return h.error(str(exc))
        return follow_run(h, job.run, len(job.run.events) if busy is job.run else 0)
    if events.active_run_for(conv["id"]):
        return h.error("This conversation is still working. Stop it first.", 409)
    context = body.get("context") if isinstance(body.get("context"), dict) else None
    run = agent.start_turn(conv["id"], text, attachments=attachments, skill=body.get("skill") or "",
                           model=body.get("model") or "", groups=body.get("groups") or None, context=context,
                           display=str(body.get("display") or "")[:300], command=str(body.get("command") or "")[:40])
    events.publish("conversation", {"id": conv["id"]})
    follow_run(h, run)


@route("GET", "/api/runs/([a-f0-9]+)/stream")
def run_stream(h, rid):
    run = events.RUNS.get(rid)
    if not _run_visible(h, run):
        return h.error("run not found", 404)
    follow_run(h, run, int(h.query.get("from", 0)))


@route("POST", "/api/runs/([a-f0-9]+)/cancel")
def run_cancel(h, rid):
    run = events.RUNS.get(rid) if _run_visible(h, events.RUNS.get(rid)) else None
    if run:
        run.cancel()
    h.send_json({"ok": bool(run)})


@route("POST", "/api/runs/([a-f0-9]+)/message")
def run_message(h, rid):
    """Something the user says while a run works (typed or spoken): it reaches the agent between steps, without stopping it."""
    run = events.RUNS.get(rid)
    text = str(h.body_json().get("text") or "").strip()
    if not _run_visible(h, run) or run.done:
        return h.error("That reply has already finished.", 409)
    if not text:
        return h.error("empty message")
    job = agents.job_for_conversation(run.conv_id)
    if job and job.run is run:
        return h.send_json({"ok": True, "how": job.message(text)})
    if hasattr(run, "send"):
        return h.send_json({"ok": True, "how": run.send(text)})
    run.inbox.append(text)
    h.send_json({"ok": True, "how": "delivered"})


@route("POST", "/api/questions/([A-Za-z0-9_]+)")
def question_answer(h, qid):
    body = h.body_json()
    try:
        opencode.answer(body.get("answers") or {"q0": str(body.get("text") or "")}, qid)
    except Exception as exc:
        return h.error(str(exc))
    h.send_json({"ok": True})


@route("GET", "/api/approvals")
def approvals(h):
    h.send_json({"approvals": [a for a in events.pending_approvals() if mine_event(a, h.user_key)]})


@route("POST", "/api/approvals/([a-f0-9]+)")
def approval_decide(h, aid):
    decision = h.body_json().get("decision", "deny")
    if decision not in ("allow", "deny", "always"):
        decision = "deny"
    pending = next((a for a in events.approvals_snapshot() if a["id"] == aid), None)
    if pending and not mine_event(pending, h.user_key):
        return h.error("not found", 404)  # what someone else's chat asks to do is theirs to decide
    h.send_json({"ok": events.resolve_approval(aid, decision)})


# ---------- uploads & files ----------

def _safe_name(name):
    name = os.path.basename(name or "file").strip().replace("\x00", "")
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:120] or "file"


def _save_stream(h, dest):
    length = int(h.headers.get("Content-Length") or 0)
    if length > 2 * 1024 * 1024 * 1024:
        raise ToolError("File too large (2 GB max).")
    h._body_read = True
    remaining = length
    with open(dest, "wb") as fh:
        while remaining > 0:
            chunk = h.rfile.read(min(1 << 20, remaining))
            if not chunk:
                break
            fh.write(chunk)
            remaining -= len(chunk)
    return length - remaining


@route("POST", "/api/upload")
def upload(h):
    name = _safe_name(h.query.get("name") or h.headers.get("X-Filename") or "file")
    att_id = db.new_id()
    dest = os.path.join(UPLOADS, "%s-%s" % (att_id, name))
    size = _save_stream(h, dest)
    mime = h.headers.get("Content-Type") or guess_mime(name)
    if mime in ("application/octet-stream", "") or "charset" in mime and mime.startswith("text/plain") and not name.endswith(".txt"):
        mime = guess_mime(name)
    kind = "image" if mime.startswith("image/") else "audio" if mime.startswith("audio/") else "video" if mime.startswith("video/") else "file"
    h.send_json({"id": att_id, "name": name, "mime": mime, "size": size, "kind": kind, "path": dest})


@route("GET", "/api/uploads/([a-f0-9_]+)")
def upload_get(h, att_id):
    path = agent.attachment_path(att_id)
    if not path:
        return h.error("not found", 404)
    active = os.path.splitext(path)[1].lower() in ACTIVE_EXT
    h.send_file(path, cache=True, headers=SANDBOX_CSP if active else None)


def _resolve(path):
    path = os.path.expanduser(path or WORKSPACE)
    if path.lower().startswith("file:///"):
        path = urllib.parse.unquote(path[8 if IS_WINDOWS else 7:])
    path = os.path.abspath(path if os.path.isabs(path) else os.path.join(WORKSPACE, path))  # like the file tools
    check_access(path)
    return path


@route("GET", "/api/files")
def files_list(h):
    path = _resolve(h.query.get("path"))
    if not os.path.isdir(path):
        return h.error("not a folder")
    entries = []
    try:
        for e in os.scandir(path):
            if e.name.startswith(".") and path == WORKSPACE:
                continue
            try:
                st = e.stat()
                entries.append({"name": e.name, "path": e.path, "dir": e.is_dir(), "size": st.st_size, "mtime": st.st_mtime})
            except OSError:
                continue
    except PermissionError:
        return h.error("permission denied", 403)
    entries.sort(key=lambda e: (not e["dir"], -e["mtime"] if path == WORKSPACE else 0, e["name"].lower()))
    parent = os.path.dirname(path.rstrip("\\/"))
    h.send_json({"path": path, "parent": parent if parent and parent != path else "", "entries": entries[:3000],
                 "workspace": WORKSPACE, "home": os.path.expanduser("~"), "roots": _roots()})


def _roots():
    home = os.path.expanduser("~")
    quick = [{"label": "Workspace", "path": WORKSPACE}, {"label": "Home", "path": home}]
    for label in ("Desktop", "Documents", "Downloads", "Pictures"):
        p = os.path.join(home, label)
        if os.path.isdir(p):
            quick.append({"label": label, "path": p})
    if IS_WINDOWS:
        import string
        quick += [{"label": "%s:" % d, "path": "%s:\\" % d} for d in string.ascii_uppercase if os.path.exists("%s:\\" % d)]
    else:
        quick.append({"label": "/", "path": "/"})
    return quick


# pages and graphics can carry scripts: shown on their own they run sandboxed, away from the API and the session
SANDBOX_CSP = {"Content-Security-Policy": "sandbox allow-scripts allow-forms allow-modals allow-popups allow-downloads"}
ACTIVE_EXT = (".html", ".htm", ".xhtml", ".svg", ".xml")


@route("GET", "/api/file")
def file_get(h):
    path = _resolve(h.query.get("path"))
    if not os.path.isfile(path):
        return h.error("not found", 404)
    active = os.path.splitext(path)[1].lower() in ACTIVE_EXT
    h.send_file(path, download=h.query.get("download") == "1", headers=SANDBOX_CSP if active else None)


@route("GET", "/api/file/info")
def file_info(h):
    """What the viewer needs to preview a file; web pages get a link that also serves their images, styles and scripts."""
    path = _resolve(h.query.get("path"))
    if not os.path.isfile(path):
        return h.error("not found", 404)
    st = os.stat(path)
    out = {"path": path, "name": os.path.basename(path), "size": st.st_size, "mtime": st.st_mtime, "mime": guess_mime(path)}
    if os.path.splitext(path)[1].lower() in (".html", ".htm", ".xhtml"):
        folder = os.path.dirname(path)
        key = base64.urlsafe_b64encode(folder.encode()).decode().rstrip("=")
        out["view"] = "/f/%s/%s/%s" % (file_token(folder), key, urllib.parse.quote(os.path.basename(path)))
    h.send_json(out)


def file_token(folder):
    return hmac.new(SETTINGS["secret"].encode(), ("scorp-file-v1:%s:%s" % (SETTINGS["pin"], folder)).encode(), hashlib.sha256).hexdigest()[:32]


def serve_file_view(h, path):
    """/f/<token>/<folder>/<file>: a web page from anywhere on the PC with its neighbouring assets, sandboxed."""
    parts = path.split("/", 4)  # ['', 'f', token, folder, rest]
    if len(parts) < 5:
        return h.error("not found", 404)
    try:
        folder = base64.urlsafe_b64decode(parts[3] + "=" * (-len(parts[3]) % 4)).decode()
    except ValueError:
        return h.error("not found", 404)
    if not hmac.compare_digest(parts[2], file_token(folder)):
        return h.error("not found", 404)
    root = os.path.realpath(folder)
    full = os.path.realpath(os.path.join(root, urllib.parse.unquote(parts[4])))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return h.error("not found", 404)
    try:
        check_access(full)
    except ToolError:
        return h.error("not found", 404)
    h.send_file(full, headers=SANDBOX_CSP)


_UNFURLED = {}


@route("GET", "/api/unfurl")
def unfurl(h):
    """Title, description, picture and icon of a web page, for link previews in the chat."""
    url = (h.query.get("url") or "").strip()
    if not re.match(r"https?://", url):
        return h.error("http(s) URL required")
    hit = _UNFURLED.get(url)
    if not hit or time.time() - hit[0] > 3600:
        from .tools.web import link_preview
        hit = (time.time(), link_preview(url))
        if len(_UNFURLED) > 500:
            _UNFURLED.clear()
        _UNFURLED[url] = hit
    h.send_json(hit[1])


@route("GET", "/api/file/text")
def file_text(h):
    path = agent.attachment_path(h.query["upload"]) if h.query.get("upload") else _resolve(h.query.get("path"))
    if not path or not os.path.isfile(path):
        return h.error("not found", 404)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"):
        from .tools.docs import extract_text
        return h.send_json({"text": extract_text(path, 200000), "kind": "document"})
    with open(path, "rb") as fh:
        raw = fh.read(1024 * 1024)
    if b"\x00" in raw[:4096]:
        return h.send_json({"text": "", "kind": "binary"})
    h.send_json({"text": raw.decode("utf-8", "replace"), "kind": "text"})


@route("POST", "/api/files/upload")
def files_upload(h):
    folder = _resolve(h.query.get("dir") or WORKSPACE)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, _safe_name(h.query.get("name")))
    _save_stream(h, dest)
    events.publish("files", {})
    h.send_json({"ok": True, "path": dest})


@route("DELETE", "/api/file")
def file_delete(h):
    path = _resolve(h.query.get("path"))
    root = os.path.normcase(os.path.realpath(WORKSPACE))
    if not os.path.normcase(os.path.realpath(path)).startswith(root + os.sep):
        return h.error("Only files inside the workspace can be deleted from the app.", 403)
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)
    events.publish("files", {})
    h.send_json({"ok": True})


@route("POST", "/api/file/open")
def file_open(h):
    from .tools.system import open_with_default
    path = _resolve(h.body_json().get("path"))
    open_with_default(path)
    h.send_json({"ok": True})


# ---------- voice ----------

@route("POST", "/api/tts")
def tts(h):
    body = h.body_json()
    try:
        audio, mime, used, note = voice.synthesize_ex(body.get("text", ""), voice=body.get("voice") or None,
                                                      engine=body.get("engine") or None, rate=body.get("rate"))
    except voice.VoiceError as exc:
        return h.error(str(exc), 503)
    h.send_response(200)
    h.send_header("Content-Type", mime)
    h.send_header("X-TTS-Engine", used)
    if note:
        # why the chosen engine did not speak (header values must be latin-1)
        h.send_header("X-TTS-Fallback", note.encode("latin-1", "replace").decode("latin-1")[:400])
    h.send_header("Content-Length", str(len(audio)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(audio)


@route("GET", "/api/tts/voices")
def tts_voices(h):
    engine = h.query.get("engine") or SETTINGS["tts_engine"]
    try:
        h.send_json({"engine": engine, "voices": voice.list_voices(engine)})
    except Exception as exc:
        h.send_json({"engine": engine, "voices": [], "error": str(exc)})


@route("POST", "/api/stt")
def stt(h):
    audio = h.body_bytes(limit=200 * 1024 * 1024)
    ctype = h.headers.get("Content-Type", "")
    kind = ctype.split(";")[0].strip().lower()
    suffix = {"audio/mp4": ".mp4", "audio/x-m4a": ".m4a", "audio/aac": ".aac", "audio/ogg": ".ogg", "audio/opus": ".ogg",
              "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
              "audio/flac": ".flac", "video/mp4": ".mp4", "video/webm": ".webm"}.get(kind, ".webm")
    try:
        text, lang = voice.transcribe_bytes(audio, h.query.get("language") or SETTINGS["stt_language"], suffix)
    except voice.VoiceError as exc:
        return h.error(str(exc), 503)
    h.send_json({"text": text, "language": lang})


@route("POST", "/api/wake")
def wake(h):
    """A short burst of speech from a device listening for "Hey Scorp" / "Hey Ember"."""
    audio = h.body_bytes(limit=4 * 1024 * 1024)
    try:
        h.send_json(voice.wake_check(audio, h.query.get("language") or SETTINGS["stt_language"]))
    except voice.VoiceError as exc:
        return h.error(str(exc), 503)


# ---------- memory ----------

@route("GET", "/api/memory")
def memory_list(h):
    h.send_json({"memories": db.q("SELECT * FROM memories WHERE owner=? ORDER BY created DESC", (h.user_key,))})


@route("POST", "/api/memory")
def memory_add(h):
    body = h.body_json()
    text = str(body.get("text", "")).strip()
    if not text:
        return h.error("text required")
    mid = db.x("INSERT INTO memories (text, kind, created, used, owner) VALUES (?,?,?,?,?)", (text, body.get("kind") or "fact", time.time(), time.time(), h.user_key))
    h.send_json({"id": mid})


@route("PATCH", "/api/memory/(\\d+)")
def memory_patch(h, mid):
    db.x("UPDATE memories SET text=? WHERE id=? AND owner=?", (str(h.body_json().get("text", "")), int(mid), h.user_key))
    h.send_json({"ok": True})


@route("DELETE", "/api/memory/(\\d+)")
def memory_delete(h, mid):
    db.x("DELETE FROM memories WHERE id=? AND owner=?", (int(mid), h.user_key))
    h.send_json({"ok": True})


# ---------- agenda ----------

@route("GET", "/api/agenda")
def agenda(h):
    now = time.time()
    days = int(h.query.get("days", 60))
    start = now - int(h.query.get("past", 1)) * 86400
    h.send_json({
        "reminders": db.q("SELECT * FROM reminders WHERE done=0 ORDER BY due"),
        "events": db.q("SELECT * FROM events WHERE end>=? AND start<=? ORDER BY start", (start, now + days * 86400)),
        "ics": "/api/calendar.ics?token=%s" % session_token(),
    })


@route("POST", "/api/reminders")
def reminder_add(h):
    body = h.body_json()
    due = parse_when(body.get("when"))
    rid = db.x("INSERT INTO reminders (text, due, repeat, created) VALUES (?,?,?,?)", (body.get("text", "Reminder"), due, body.get("repeat") or "", time.time()))
    events.publish("agenda", {})
    h.send_json({"id": rid, "due": due})


@route("POST", "/api/reminders/(\\d+)/done")
def reminder_done(h, rid):
    db.x("UPDATE reminders SET done=1 WHERE id=?", (int(rid),))
    events.publish("agenda", {})
    h.send_json({"ok": True})


@route("DELETE", "/api/reminders/(\\d+)")
def reminder_delete(h, rid):
    db.x("DELETE FROM reminders WHERE id=?", (int(rid),))
    events.publish("agenda", {})
    h.send_json({"ok": True})


@route("POST", "/api/events")
def event_add(h):
    body = h.body_json()
    start = parse_when(body.get("start"))
    end = parse_when(body.get("end")) if body.get("end") else start + 3600
    eid = db.x("INSERT INTO events (title, start, end, location, notes, remind, created) VALUES (?,?,?,?,?,?,?)",
               (body.get("title", "Event"), start, end, body.get("location", ""), body.get("notes", ""), int(body.get("remind", 10)), time.time()))
    events.publish("agenda", {})
    h.send_json({"id": eid})


@route("DELETE", "/api/events/(\\d+)")
def event_delete(h, eid):
    db.x("DELETE FROM events WHERE id=?", (int(eid),))
    events.publish("agenda", {})
    h.send_json({"ok": True})


@route("GET", "/api/calendar.ics")
def calendar_ics(h):
    token = h.query.get("token", "")
    if SETTINGS["require_pin"] and not h.is_local and not hmac.compare_digest(token, session_token()) and not h.authed():
        return h.error("unauthorized", 401)
    body = ics_feed().encode()
    h.send_response(200)
    h.send_header("Content-Type", "text/calendar; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


# ---------- records ----------

@route("GET", "/api/records/collections")
def record_collections(h):
    h.send_json({"collections": db.q("SELECT collection, COUNT(*) AS count, MAX(updated) AS updated FROM records GROUP BY collection ORDER BY collection")})


@route("GET", "/api/records")
def records_list(h):
    sql, params = "SELECT * FROM records WHERE 1=1", []
    if h.query.get("collection"):
        sql += " AND collection=?"
        params.append(h.query["collection"])
    if h.query.get("q"):
        sql += " AND data LIKE ?"
        params.append("%" + h.query["q"] + "%")
    rows = db.q(sql + " ORDER BY updated DESC LIMIT 500", tuple(params))
    for r in rows:
        r["data"] = json.loads(r["data"] or "{}")
    h.send_json({"records": rows})


@route("POST", "/api/records")
def record_add(h):
    body = h.body_json()
    now = time.time()
    rid = db.x("INSERT INTO records (collection, data, created, updated) VALUES (?,?,?,?)",
               (str(body.get("collection", "notes")).lower(), json.dumps(body.get("data") or {}, ensure_ascii=False), now, now))
    h.send_json({"id": rid})


@route("PATCH", "/api/records/(\\d+)")
def record_patch(h, rid):
    db.x("UPDATE records SET data=?, updated=? WHERE id=?", (json.dumps(h.body_json().get("data") or {}, ensure_ascii=False), time.time(), int(rid)))
    h.send_json({"ok": True})


@route("DELETE", "/api/records/(\\d+)")
def record_delete(h, rid):
    db.x("DELETE FROM records WHERE id=?", (int(rid),))
    h.send_json({"ok": True})


# ---------- automations & tasks ----------

def _automation_row(row, h):
    row = dict(row)
    row["schedule"] = json.loads(row["schedule"] or "{}")
    row["describe"] = scheduler.describe(row["schedule"])
    row["hook"] = "/api/hooks/%d?key=%s" % (row["id"], row["key"])
    return row


@route("GET", "/api/automations")
def automations(h):
    h.send_json({"automations": [_automation_row(r, h) for r in db.q("SELECT * FROM automations ORDER BY id DESC")]})


@route("POST", "/api/automations")
def automation_create(h):
    from .agents_tools import save_automation
    body = h.body_json()
    if not body.get("name") or not body.get("prompt"):
        return h.error("name and prompt required")
    if body.get("schedule", {}).get("type") == "once":
        body["schedule"]["at"] = parse_when(body["schedule"].get("at"))
    h.send_json({"id": save_automation(body)})


@route("PATCH", "/api/automations/(\\d+)")
def automation_patch(h, aid):
    from .agents_tools import save_automation
    row = db.one("SELECT * FROM automations WHERE id=?", (int(aid),))
    if not row:
        return h.error("not found", 404)
    current = {"name": row["name"], "prompt": row["prompt"], "schedule": json.loads(row["schedule"]), "skill": row["skill"],
               "notify": row["notify"], "enabled": row["enabled"]}
    current.update(h.body_json())
    save_automation(current, int(aid))
    h.send_json({"ok": True})


@route("DELETE", "/api/automations/(\\d+)")
def automation_delete(h, aid):
    db.x("DELETE FROM automations WHERE id=?", (int(aid),))
    events.publish("automation", {})
    h.send_json({"ok": True})


@route("POST", "/api/automations/(\\d+)/run")
def automation_run(h, aid):
    row = db.one("SELECT * FROM automations WHERE id=?", (int(aid),))
    if not row:
        return h.error("not found", 404)
    task_id, conv_id = scheduler.run_automation(row)
    h.send_json({"task_id": task_id, "conv_id": conv_id})


@route("POST", "/api/hooks/(\\d+)")
def automation_hook(h, aid):
    row = db.one("SELECT * FROM automations WHERE id=?", (int(aid),))
    if not row or not hmac.compare_digest(h.query.get("key", ""), row["key"] or ""):
        return h.error("unauthorized", 401)
    payload = h.body_json()
    task_id, conv_id = scheduler.run_automation(row, payload or None)
    h.send_json({"ok": True, "task_id": task_id})


@route("GET", "/api/tasks")
def tasks(h):
    h.send_json({"tasks": db.q("SELECT * FROM tasks WHERE agent_id='' ORDER BY created DESC LIMIT 100")})


@route("POST", "/api/tasks")
def task_create(h):
    body = h.body_json()
    if not body.get("goal"):
        return h.error("goal required")
    task_id, conv_id, _ = agent.run_detached(body["goal"], body.get("title") or body["goal"][:50], skill=body.get("skill") or "workflow")
    h.send_json({"id": task_id, "conv_id": conv_id})


@route("POST", "/api/tasks/([a-f0-9]+)/cancel")
def task_cancel(h, tid):
    row = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
    conv = db.get_conversation(row["conv_id"]) if row and row["conv_id"] else None
    if conv and not db.can_see(conv, h.user_key):
        return h.error("not found", 404)
    run = events.RUNS.get(row["run_id"]) if row else None
    if run:
        run.cancel()
    h.send_json({"ok": bool(run)})


# ---------- dedicated agents & their jobs ----------

@route("GET", "/api/agents")
def agents_list(h):
    h.send_json({"agents": agents.all_agents(), "jobs": agents.jobs(limit=int(h.query.get("limit", 60)), agent_id=h.query.get("agent") or None),
                 "approvals": [a for a in events.pending_approvals() if mine_event(a, h.user_key)]})


@route("POST", "/api/agents")
def agent_create(h):
    try:
        h.send_json(agents.save(_agent_body(h.body_json())))
    except ValueError as exc:
        h.error(str(exc))


def _agent_body(body):
    sched = body.get("schedule")
    if isinstance(sched, dict):
        try:
            body["schedule"] = agents.build_schedule(sched.get("type"), sched.get("time"), sched.get("day"), sched.get("minutes"), sched.get("at"))
        except (ValueError, TypeError) as exc:
            raise ValueError(str(exc))
    return body


@route("PATCH", "/api/agents/([a-z0-9-]+)")
def agent_patch(h, aid):
    body = h.body_json()
    try:
        if set(body) == {"enabled"}:
            agents.set_enabled(aid, body["enabled"])
            return h.send_json({"ok": True})
        h.send_json(agents.save(_agent_body(body), aid))
    except ValueError as exc:
        h.error(str(exc))


@route("DELETE", "/api/agents/([a-z0-9-]+)")
def agent_delete(h, aid):
    try:
        agents.delete(aid)
    except ValueError as exc:
        return h.error(str(exc))
    h.send_json({"ok": True})


@route("POST", "/api/agents/([a-z0-9-]+)/assign")
def agent_assign(h, aid):
    body = h.body_json()
    try:
        model = llm.find_model(body["model"]) if str(body.get("model") or "").strip() else ""
        job = agents.assign(aid, body.get("task"), title=body.get("title") or "", project=body.get("project") or "", model=model)
    except ValueError as exc:
        return h.error(str(exc))
    h.send_json(job.public())


@route("POST", "/api/agents/([a-z0-9-]+)/run")
def agent_run_now(h, aid):
    row = db.one("SELECT * FROM agents WHERE id=?", (aid,))
    if not row:
        return h.error("not found", 404)
    try:
        job = agents.fire(row)
    except ValueError as exc:
        return h.error(str(exc))
    h.send_json(job.public() if job else {"reminded": True})


@route("GET", "/api/agent-jobs/([a-f0-9]+)")
def agent_job_get(h, jid):
    job = agents.job(jid)
    if not _job_visible(h, job):
        return h.error("not found", 404)
    h.send_json(job.public())


@route("POST", "/api/agent-jobs/([a-f0-9]+)/stop")
def agent_job_stop(h, jid):
    job = agents.job(jid)
    h.send_json({"ok": bool(_job_visible(h, job) and job.stop())})


@route("DELETE", "/api/agent-jobs/([a-f0-9]+|finished)")
def agent_job_delete(h, jid):
    try:
        removed, skipped = agents.delete_jobs(jid)
    except ValueError as exc:
        return h.error(str(exc), 404)
    if jid != "finished" and skipped:
        return h.error("That job is still running. Stop it first.")
    h.send_json({"ok": True, "removed": len(removed), "skipped": len(skipped)})


@route("POST", "/api/agent-jobs/([a-f0-9]+)/model")
def agent_job_model(h, jid):
    job = agents.job(jid)
    if not _job_visible(h, job):
        return h.error("not found", 404)
    raw = str(h.body_json().get("model") or "").strip()
    try:
        ref = llm.find_model(raw) if raw else ""
    except ValueError as exc:
        return h.error(str(exc))
    live = job.set_model(ref)
    h.send_json(dict(job.public(), switched=live))


@route("POST", "/api/agent-jobs/([a-f0-9]+)/message")
def agent_job_message(h, jid):
    job = agents.job(jid)
    if not _job_visible(h, job):
        return h.error("not found", 404)
    try:
        h.send_json({"ok": True, "how": job.message(h.body_json().get("text"))})
    except ValueError as exc:
        h.error(str(exc))


@route("GET", "/api/discord")
def discord_status(h):
    h.send_json(discord.status())


# ---------- looks: styles and plugins for Scorp's own interface ----------

@route("GET", "/api/looks")
def looks_get(h):
    h.send_json(looks.public())


@route("POST", "/api/looks/active")
def looks_active(h):
    try:
        looks.set_active(str(h.body_json().get("id") or ""))
    except ValueError as exc:
        return h.error(str(exc), 404)
    h.send_json(looks.public())


@route("POST", "/api/looks/plugins/([a-z0-9-]+)")
def looks_plugin(h, pid):
    try:
        looks.set_plugin(pid, bool(h.body_json().get("enabled")))
    except ValueError as exc:
        return h.error(str(exc), 404)
    h.send_json(looks.public())


@route("POST", "/api/looks/plugins/([a-z0-9-]+)/values")
def looks_plugin_values(h, pid):
    """A plugin's settings, from the form the Studio draws for its options."""
    try:
        values = looks.set_values(pid, h.body_json().get("values") or {})
    except ValueError as exc:
        return h.error(str(exc), 404)
    h.send_json({"values": values})


@route("GET", "/api/plugins/data")
def plugins_data(h):
    h.send_json(looks.plugin_data())


@route("POST", "/api/plugins/([a-z0-9-]+)/data")
def plugins_data_set(h, pid):
    """scorp.shared.set(key, value): kept on the server and sent to every device (value null removes it)."""
    b = h.body_json()
    try:
        looks.set_data(pid, b.get("key"), b.get("value"))
    except ValueError as exc:
        return h.error(str(exc))
    h.send_json({"ok": True})


@route("POST", "/api/plugins/([a-z0-9-]+)/errors")
def plugins_error(h, pid):
    looks.report_error(pid, h.body_json().get("text") or "")
    h.send_json({"ok": True})


@route("POST", "/api/plugins/([a-z0-9-]+)/llm")
def plugins_llm(h, pid):
    """scorp.llm(prompt, {system, json}): one question to the model, outside any conversation."""
    b = h.body_json()
    plugin = looks.find("plugin", pid)
    if not plugin or not plugin.get("enabled"):
        return h.error("That plugin is not on.", 404)
    prompt = str(b.get("prompt") or "").strip()[:20000]
    if not prompt:
        return h.error("Nothing to ask.")
    system = str(b.get("system") or "")[:4000]
    if b.get("json"):
        system = (system + "\n\n" if system else "") + "Answer with valid JSON only: no Markdown, no code fences, no words around it."
    try:
        text = llm.complete(prompt, system, model=str(b.get("model") or "") or None, max_chars=16000)
    except Exception as exc:  # noqa: BLE001 - the plugin shows it
        return h.error("The model could not answer: %s" % exc, 502)
    if b.get("json"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        try:
            return h.send_json({"text": text, "data": json.loads(raw)})
        except ValueError:
            m = re.search(r"[\[{][\s\S]*[\]}]", raw)
            try:
                return h.send_json({"text": text, "data": json.loads(m.group(0)) if m else None})
            except ValueError:
                return h.send_json({"text": text, "data": None})
    h.send_json({"text": text})


@route("POST", "/api/looks/styles")
def looks_save_style(h):
    """The Studio's editor: the whole style as edited (its tokens replace the old set)."""
    b = h.body_json()
    try:
        style = looks.save_style(str(b.get("name") or "My style"), b.get("vars") or {}, str(b.get("css") or ""), b.get("fonts") or [],
                                 str(b.get("description") or ""), activate=bool(b.get("activate")), sid=b.get("id") or None, replace=True)
    except ValueError as exc:
        return h.error(str(exc))
    h.send_json({"style": style, **looks.public()})


@route("POST", "/api/looks/plugins")
def looks_save_plugin(h):
    b = h.body_json()
    try:
        plugin = looks.save_plugin(str(b.get("name") or "My plugin"), str(b.get("css") or ""), str(b.get("js") or ""),
                                   str(b.get("description") or ""), enabled=b.get("enabled", True) is not False, pid=b.get("id") or None,
                                   prompt=str(b.get("prompt") or ""), options=b.get("options") or [], icon=b.get("icon"))
    except ValueError as exc:
        return h.error(str(exc))
    h.send_json({"plugin": plugin, **looks.public()})


@route("GET", "/api/looks/export/(style|plugin)/([a-z0-9-]+)")
def looks_export(h, kind, ident):
    try:
        h.send_json(looks.export(kind, ident))
    except ValueError as exc:
        h.error(str(exc), 404)


@route("POST", "/api/looks/import")
def looks_import(h):
    try:
        kind, item = looks.import_item(h.body_json())
    except ValueError as exc:
        return h.error(str(exc))
    h.send_json({"kind": kind, "item": item, **looks.public()})


@route("DELETE", "/api/looks/(style|plugin)s/([a-z0-9-]+)")
def looks_delete(h, kind, ident):
    try:
        looks.delete(kind, ident)
    except ValueError as exc:
        return h.error(str(exc), 404)
    h.send_json(looks.public())


# ---------- coding projects ----------

def preview_token(folder):
    # tied to the PIN like sessions are: a new PIN retires every preview link
    return hmac.new(SETTINGS["secret"].encode(), ("scorp-preview-v1:%s:%s" % (SETTINGS["pin"], folder)).encode(), hashlib.sha256).hexdigest()[:32]


def preview_url(folder):
    return "/p/%s/%s/" % (preview_token(folder), folder)


@route("GET", "/api/projects")
def projects_list(h):
    out = agents.projects()
    for p in out:
        p["preview"] = preview_url(p["slug"])
    h.send_json({"projects": out, "root": agents.PROJECTS})


@route("POST", "/api/projects")
def project_create(h):
    body = h.body_json()
    task = str(body.get("task") or "").strip()
    name = str(body.get("name") or "").strip() or agents._first_line(task)[:48]
    if not task:
        return h.error("Describe what to build.")
    folder = agents.slug(name, "project")
    if agents.latest_job(folder) and os.path.isdir(agents.project_path(folder)) and not body.get("continue"):
        folder = "%s-%s" % (folder, time.strftime("%m%d-%H%M"))  # a new project never takes over an old one's folder
    try:
        job = agents.assign(body.get("agent") or "coder", task, title=name, project=folder)
    except ValueError as exc:
        return h.error(str(exc))
    h.send_json({"slug": folder, "job": job.public(), "conv_id": job.conv_id, "run_id": job.run.id})


@route("GET", "/api/projects/([a-z0-9-]+)")
def project_get(h, folder):
    path = agents.project_path(folder)
    if not os.path.isdir(path):
        return h.error("not found", 404)
    jobs = agents.project_jobs(folder)
    latest = jobs[0] if jobs else None
    h.send_json({"slug": folder, "name": (latest or {}).get("title") or folder.replace("-", " ").capitalize(), "path": path,
                 "tree": agents.project_tree(folder), "jobs": jobs, "job": latest, "conv_id": (latest or {}).get("conv_id") or "",
                 "preview": preview_url(folder), "has_index": os.path.isfile(os.path.join(path, "index.html"))})


def serve_preview(h, path):
    """/p/<token>/<project>/<file>: a project's files for the sandboxed live preview (relative links keep working)."""
    parts = urllib.parse.unquote(path).split("/", 4)  # ['', 'p', token, project, rest]
    if len(parts) < 4 or not hmac.compare_digest(parts[2], preview_token(parts[3])):
        return h.error("not found", 404)
    try:
        root = os.path.realpath(agents.project_path(parts[3]))
    except ValueError:
        return h.error("not found", 404)
    rel = parts[4] if len(parts) > 4 else ""
    full = os.path.realpath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        return h.error("not found", 404)
    if os.path.isdir(full):
        full = os.path.join(full, "index.html")
    if not os.path.isfile(full):
        return h.error("not found", 404)
    # generated code runs in its own opaque origin, even when the preview is opened in a tab: it can't reach the API
    h.send_file(full, headers={"Content-Security-Policy": "sandbox allow-scripts allow-forms allow-modals allow-popups allow-downloads"})


# ---------- knowledge ----------

@route("GET", "/api/knowledge")
def knowledge_list(h):
    h.send_json({"documents": db.q("SELECT * FROM kb_docs ORDER BY added DESC"), "embed_model": SETTINGS["embed_model"]})


@route("POST", "/api/knowledge")
def knowledge_add(h):
    body = h.body_json()
    path = body.get("path") or (agent.attachment_path(body["upload_id"]) if body.get("upload_id") else None)
    if not path:
        return h.error("path or upload_id required")
    path = _resolve(path)

    def work():
        try:
            if os.path.isdir(path):
                knowledge.add_folder(path)
            else:
                knowledge.add_document(path, body.get("title", ""))
            notify.send("Knowledge updated", "Indexed %s" % os.path.basename(path.rstrip("\\/")), level="success")
        except Exception as exc:
            notify.send("Indexing failed", str(exc), level="warning")

    threading.Thread(target=work, daemon=True).start()
    h.send_json({"ok": True, "queued": path})


@route("DELETE", "/api/knowledge/(\\d+)")
def knowledge_delete(h, did):
    db.x("DELETE FROM kb_chunks WHERE doc_id=?", (int(did),))
    db.x("DELETE FROM kb_docs WHERE id=?", (int(did),))
    h.send_json({"ok": True})


@route("POST", "/api/knowledge/search")
def knowledge_search(h):
    h.send_json({"results": knowledge.search(str(h.body_json().get("query", "")), 8)})


# ---------- notifications ----------

@route("GET", "/api/notifications")
def notifications(h):
    h.send_json({"notifications": db.q("SELECT * FROM notifications ORDER BY created DESC LIMIT 100")})


@route("POST", "/api/notifications/read")
def notifications_read(h):
    db.x("UPDATE notifications SET read=1 WHERE read=0")
    h.send_json({"ok": True})


@route("POST", "/api/notifications/test")
def notifications_test(h):
    channels = notify.send("Scorp is connected", "Notifications reach this device.", level="success")
    h.send_json({"channels": channels})


# ---------- tools, skills, MCP, services ----------

@route("GET", "/api/tools")
def tools_catalog(h):
    h.send_json({"tools": catalog(), "groups": [{"id": k, "name": v[0], "description": v[1]} for k, v in GROUPS.items()],
                 "enabled_groups": SETTINGS["tool_groups"], "mode": SETTINGS["tool_mode"]})


@route("POST", "/api/tools/policy")
def tools_policy(h):
    body = h.body_json()
    pol = dict(SETTINGS["tool_policy"] or {})
    for name, value in (body.get("policies") or {body.get("name"): body.get("policy")}).items():
        if name in TOOLS and value in ("allow", "ask", "off", "default"):
            if value == "default":
                pol.pop(name, None)
            else:
                pol[name] = value
    SETTINGS.update({"tool_policy": pol})
    h.send_json({"tools": catalog()})


@route("GET", "/api/skills")
def skills(h):
    out = [{"id": k, **{kk: vv for kk, vv in v.items()}} for k, v in agent.all_skills().items()]
    h.send_json({"skills": out})


@route("POST", "/api/skills")
def skills_save(h):
    items = h.body_json().get("skills") or []
    clean = []
    for s in items:
        if s.get("name") and s.get("instructions"):
            sid = s.get("id") or re.sub(r"[^a-z0-9]+", "-", s["name"].lower()).strip("-") or db.new_id()
            if sid in agent.SKILLS:
                sid = "my-" + sid
            clean.append({"id": sid, "name": s["name"][:40], "description": s.get("description", "")[:200], "instructions": s["instructions"],
                          "groups": [g for g in s.get("groups") or [] if g in GROUPS], "icon": s.get("icon") or "spark"})
    SETTINGS.update({"skills": clean})
    skills(h)


@route("GET", "/api/mcp")
def mcp_status(h):
    h.send_json({"servers": MANAGER.status(), "presets": PRESETS})


@route("POST", "/api/mcp")
def mcp_save(h):
    servers = h.body_json().get("servers") or []
    SETTINGS.update({"mcp_servers": servers})
    MANAGER.reload()
    time.sleep(0.3)
    mcp_status(h)


@route("GET", "/api/services")
def services(h):
    h.send_json({"services": detect_services()})


# ---------- Scorp Radio ----------

@route("GET", "/api/music")
def music_state(h):
    music.ensure_watcher()
    h.send_json(music.snapshot())


@route("GET", "/api/music/stations")
def music_stations(h):
    q, tag = h.query.get("q", "").strip(), h.query.get("tag", "").strip()
    try:
        found = music.stations_for(tag)[0] if tag else music.search(query=q)
    except Exception as exc:
        return h.error("The station directory did not answer: %s" % exc, 502)
    h.send_json({"stations": found})


@route("POST", "/api/music/radio")
def music_radio(h):
    body = h.body_json()
    action = body.get("action") or "play"
    device = str(body.get("device") or "")[:64]
    if action == "play":
        station = body.get("station") if isinstance(body.get("station"), dict) else None
        if station and not str(station.get("url", "")).startswith(("http://", "https://")):
            return h.error("bad station")
        mood, queue = "", None
        if body.get("mood"):
            try:
                queue, mood = music.stations_for(str(body["mood"]))
            except Exception as exc:
                return h.error("The station directory did not answer: %s" % exc, 502)
            if not queue:
                return h.error("No stations found for that.", 404)
            station = queue[0]
        if not music.play(station, speaker=device, mood=mood, queue=queue):
            return h.error("Pick a station first.")
    elif action == "pause":
        music.pause()
    elif action in ("next", "previous"):
        music.skip(1 if action == "next" else -1)
    elif action == "here":
        music.play(speaker=device)
    else:
        return h.error("unknown action")
    h.send_json(music.snapshot())


@route("POST", "/api/music/system")
def music_system(h):
    try:
        ok = music.system_control(str(h.body_json().get("action") or ""))
    except ValueError as exc:
        return h.error(str(exc))
    h.send_json({"ok": ok})


@route("POST", "/api/music/favorite")
def music_favorite(h):
    body = h.body_json()
    station = body.get("station")
    if not isinstance(station, dict) or not str(station.get("url", "")).startswith(("http://", "https://")):
        return h.error("bad station")
    keep = {k: station.get(k) for k in ("id", "name", "url", "favicon", "tags", "country", "codec", "bitrate", "homepage")}
    music.set_favorite(keep, bool(body.get("on", True)))
    h.send_json(music.snapshot())


@route("GET", "/api/music/art")
def music_art(h):
    # covers and station logos come through here so the app may read their colours (same origin)
    url = h.query.get("u", "")
    if url.startswith("windows:"):
        # a player's own cover, read from Windows
        found = music.windows_cover(url)
        if not found:
            return h.error("not found", 404)
        body, ctype = found
    elif not url.startswith(("http://", "https://")) or not _public_host(url):
        return h.error("not found", 404)
    else:
        try:
            body, ctype = music.fetch_image(url)
        except Exception:
            return h.error("not found", 404)
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "private, max-age=86400")
    h.end_headers()
    try:
        h.wfile.write(body)
    except OSError:
        pass


@route("GET", "/api/music/stream")
def music_stream(h):
    # the radio plays through Scorp: phones on https can play http stations and the app can hear the beat
    url = h.query.get("url", "")
    if not url.startswith(("http://", "https://")) or not _public_host(url):
        return h.error("not found", 404)
    try:
        resp, metaint = music.open_stream(url)
    except Exception as exc:
        return h.error("The station did not answer: %s" % exc, 502)
    h.close_connection = True
    h.send_response(200)
    h.send_header("Content-Type", resp.headers.get("Content-Type") or "audio/mpeg")
    h.send_header("Cache-Control", "no-store")
    h.send_header("Connection", "close")
    h.end_headers()

    def write(chunk):
        h.wfile.write(chunk)
        h.wfile.flush()

    try:
        music.relay(resp, metaint, write, url)
    except (OSError, ValueError):
        pass


# ---------- push events (SSE) ----------

def mine_event(data, user):
    """Whether an event or approval about a conversation concerns this person (others' chats stay out of their app)."""
    cid = data.get("conv_id") or ""
    conv = db.get_conversation(cid) if cid else None
    return conv is None or db.can_see(conv, user)


@route("GET", "/api/events")
def sse(h):
    user = h.user_key
    h.close_connection = True
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Accel-Buffering", "no")
    h.send_header("Connection", "close")
    h.end_headers()
    q = events.subscribe()
    try:
        h.wfile.write(b"retry: 3000\n\n")
        h.wfile.flush()
        while True:
            try:
                event = q.get(timeout=15)
                data = event.get("data") or {}
                if event.get("type") == "conversation" and data.get("id"):
                    data = {"conv_id": data["id"]}  # that event names the chat by "id"
                if not mine_event(data, user):
                    continue
                h.wfile.write(("data: %s\n\n" % json.dumps(event, default=str)).encode())
            except queue.Empty:
                h.wfile.write(b": ping\n\n")
            h.wfile.flush()
    except OSError:
        pass
    finally:
        events.unsubscribe(q)


# ---------- HTTPS ----------

HTTPS_STATE = {"running": False, "error": "", "port": 0}


def ensure_certificate():
    cert = os.path.join(DATA_DIR, "cert.pem")
    key = os.path.join(DATA_DIR, "key.pem")
    if os.path.exists(cert) and os.path.exists(key) and time.time() - os.path.getmtime(cert) < 300 * 86400:
        return cert, key
    if not module_available("cryptography"):
        raise RuntimeError("pip install cryptography to enable HTTPS (needed for microphone access from phones)")
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host = socket.gethostname()
    names = [x509.DNSName("localhost"), x509.DNSName(host)]
    for addr in ["127.0.0.1"] + [a["ip"] for a in lan_addresses()]:
        names.append(x509.IPAddress(ipaddress.ip_address(addr)))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Scorp on %s" % host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825)).add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(private, hashes.SHA256())
    )
    with open(key, "wb") as fh:
        fh.write(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    with open(cert, "wb") as fh:
        fh.write(certificate.public_bytes(serialization.Encoding.PEM))
    return cert, key


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # A browser opens dozens of connections at once while the app loads (every script and stylesheet, each its own connection
    # through the tailscale proxy). The default queue holds 5: the rest were refused or retried after seconds, so a phone could
    # lose a script and show an error.
    request_queue_size = 256
    allow_reuse_address = not IS_WINDOWS  # on Windows this would let two servers share a port

    def handle_error(self, request, client_address):
        pass


class TLSServer(Server):
    def __init__(self, addr, handler, context):
        super().__init__(addr, handler)
        self.context = context

    def get_request(self):
        sock, addr = super().get_request()
        # The handshake happens lazily on first read, inside the per-request thread.
        return self.context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr


def serve(host="0.0.0.0", port=None, https_port=None, block=True):
    from .tools import load_all
    load_all()
    port = port or SETTINGS["port"]
    https_port = https_port if https_port is not None else SETTINGS["https_port"]
    # bind before starting anything: a second launch must not mark running jobs interrupted, start MCP programs or
    # rewrite files before it discovers that the port is already taken.
    try:
        httpd = Server((host, port), Handler)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or exc.errno == errno.EADDRINUSE:
            print("  Scorp is already running on port %d (this start is a duplicate)." % port, flush=True)
            return None
        raise
    scheduler.start()
    discord.start()
    MANAGER.start_all()
    if voice._stt_order()[:1] == ["local"]:
        voice.warm_up()  # local Whisper answers first: load it now rather than on the first spoken words
    threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    if https_port:
        try:
            cert, key = ensure_certificate()
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            tls = TLSServer((host, https_port), Handler, ctx)
            threading.Thread(target=tls.serve_forever, daemon=True, name="https").start()
            HTTPS_STATE.update(running=True, port=https_port)
        except Exception as exc:
            HTTPS_STATE["error"] = str(exc)
    banner(port, https_port if HTTPS_STATE["running"] else None)
    if block:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            MANAGER.shutdown()
    return httpd


def banner(port, https_port):
    lines = ["", "  Scorp %s is running" % VERSION, "  ---------------------------------------------",
             "  On this PC:      http://localhost:%d" % port]
    for a in lan_addresses():
        label = "Tailscale:" if a["kind"] == "tailscale" else "Local network:"
        lines.append("  %-16s http://%s:%d" % (label, a["ip"], port))
        if https_port:
            lines.append("  %-16s https://%s:%d  (use this on phones for the microphone)" % ("", a["ip"], https_port))
    if HTTPS_STATE["error"]:
        lines.append("  HTTPS disabled: %s" % HTTPS_STATE["error"])
    if SETTINGS["require_pin"]:
        lines.append("  Pairing PIN for other devices:  %s" % SETTINGS["pin"])
    status = llm.status()
    lines.append("  Models: %s (%s)" % (SETTINGS["provider"], "online, %d models" % len(status["models"]) if status["online"] else "offline - start Ollama or add a free cloud key"))
    lines.append("")
    text = "\n".join(lines)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        # a Windows console can be cp1252; a model or host name must not stop the server from starting
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, "replace").decode(enc), flush=True)
