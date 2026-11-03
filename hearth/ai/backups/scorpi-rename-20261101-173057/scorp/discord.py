"""Discord Rich Presence: shows "Playing Scorp" on the user's Discord profile with what they work on (the coding project
or the conversation), how long, and the model. Talks to the Discord app on this PC over its local IPC pipe, so it needs
no packages and no token, only the Application ID of a Discord application the user created (its name is what Discord
shows, e.g. "Scorp"). Off unless turned on in Settings."""

import json
import os
import struct
import sys
import threading
import time
import uuid

from . import db, events
from .config import IS_WINDOWS, SETTINGS

IDLE_AFTER = 30 * 60  # no activity for this long: the presence is cleared
STATE = {"status": "off", "detail": "", "activity": None}  # what Settings shows


class IPCError(Exception):
    pass


class Pipe:
    """One connection to the Discord client (Windows named pipe or Unix socket), framed as op + length + JSON."""

    def __init__(self, client_id):
        self.client_id = client_id
        self.fh = None
        self.sock = None

    def connect(self):
        last = None
        for i in range(10):
            try:
                if IS_WINDOWS:
                    self.fh = open(r"\\.\pipe\discord-ipc-%d" % i, "r+b", buffering=0)
                else:
                    import socket
                    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
                    for sub in ("", "app/com.discordapp.Discord/", "snap.discord/"):
                        path = os.path.join(base, sub, "discord-ipc-%d" % i)
                        if os.path.exists(path):
                            self.sock = socket.socket(socket.AF_UNIX)
                            self.sock.connect(path)
                            break
                    if not self.sock:
                        continue
                break
            except OSError as exc:
                last = exc
        if not self.fh and not self.sock:
            raise IPCError("Discord is not running on this PC%s." % (" (%s)" % last if last and not IS_WINDOWS else ""))
        self.send(0, {"v": 1, "client_id": self.client_id})
        op, data = self.recv()
        if data.get("evt") != "READY":
            raise IPCError(data.get("message") or "Discord refused the Application ID.")
        return data

    def _write(self, raw):
        if self.fh:
            self.fh.write(raw)
        else:
            self.sock.sendall(raw)

    def _read(self, n):
        out = b""
        while len(out) < n:
            chunk = self.fh.read(n - len(out)) if self.fh else self.sock.recv(n - len(out))
            if not chunk:
                raise IPCError("Discord closed the connection.")
            out += chunk
        return out

    def send(self, op, payload):
        body = json.dumps(payload).encode("utf-8")
        self._write(struct.pack("<II", op, len(body)) + body)

    def recv(self):
        op, length = struct.unpack("<II", self._read(8))
        data = json.loads(self._read(length).decode("utf-8") or "{}")
        if op == 2:  # close
            raise IPCError(data.get("message") or "Discord closed the connection.")
        return op, data

    def set_activity(self, activity):
        self.send(1, {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": activity}, "nonce": uuid.uuid4().hex})
        op, data = self.recv()
        if data.get("evt") == "ERROR":
            raise IPCError((data.get("data") or {}).get("message") or "Discord refused the activity.")

    def close(self):
        for thing in (self.fh, self.sock):
            try:
                if thing:
                    thing.close()
            except OSError:
                pass
        self.fh = self.sock = None


def _short_model(ref):
    ref = ref or ""
    return ref.split("/")[-1] if ref else "auto"


def current():
    """The activity to show now, or None when Scorp is idle: the running (or latest) coding job first, else the chat."""
    from . import agents
    now = time.time()
    show = SETTINGS["discord_show_project"]
    live = [r for r in events.active_runs() if not r.done and r.origin == "chat"]
    jobs = agents.running_jobs()
    if jobs:
        j = max(jobs, key=lambda x: x.started)
        helpers = sum(1 for s in j.subs.values() if s.get("status") == "running")
        return {"details": ("Building %s" % (j.project.replace("-", " ") if j.project else j.title))[:120] if show else "Coding with an agent",
                "state": ("%s%s" % (_short_model(j.model_ref()), " · %d helpers" % helpers if helpers else ""))[:120],
                "start": int(j.started), "kind": "code"}
    recent = db.one("SELECT title, updated FROM conversations WHERE updated>? AND origin='chat' ORDER BY updated DESC LIMIT 1", (now - IDLE_AFTER,))
    if not live and not recent:
        if not events.client_count():
            return None
        # Scorp is open on some device but quiet: still "Playing Scorp"
        return {"details": "In Scorp", "state": ("Model: %s" % _short_model(SETTINGS["model"]))[:120],
                "start": int(_session_start(now)), "kind": "chat"}
    started = min([r.started for r in live] + [_session_start(now)])
    title = (recent or {}).get("title") or ""
    return {"details": ("Chatting: %s" % title if show and title and title != "New conversation" else "Chatting with Scorp")[:120],
            "state": ("Model: %s" % _short_model(SETTINGS["model"]))[:120], "start": int(started), "kind": "chat"}


_session = {"start": 0.0, "last": 0.0}


def _session_start(now):
    """When this sitting began: restarts after half an hour without activity."""
    if not _session["start"] or now - _session["last"] > IDLE_AFTER:
        _session["start"] = now
    _session["last"] = now
    return _session["start"]


def _activity(a):
    out = {"details": a["details"], "state": a["state"], "timestamps": {"start": a["start"]},
           "assets": {"large_image": "scorp", "large_text": "Scorp · local AI workspace",
                      "small_image": "code" if a["kind"] == "code" else "chat", "small_text": "Coding" if a["kind"] == "code" else "Chatting"},
           "instance": False}
    return out


def _loop():
    pipe, shown, retry_at = None, None, 0.0
    while True:
        time.sleep(5)
        cid = str(SETTINGS["discord_client_id"] or "").strip()
        if not SETTINGS["discord_presence"] or not cid.isdigit():
            if pipe:
                try:
                    pipe.set_activity(None)
                except Exception:
                    pass
                pipe.close()
                pipe, shown = None, None
            STATE.update(status="off" if not SETTINGS["discord_presence"] else "needs_id", detail="", activity=None)
            continue
        try:
            want = current()
        except Exception as exc:
            want = None
            STATE["detail"] = str(exc)
        try:
            if not pipe or pipe.client_id != cid:
                if time.time() < retry_at:
                    continue
                if pipe:
                    pipe.close()
                pipe, shown = Pipe(cid), None
                pipe.connect()
            key = json.dumps(want, sort_keys=True)
            if key != shown:
                pipe.set_activity(_activity(want) if want else None)
                shown = key
            STATE.update(status="showing" if want else "idle", detail="", activity=want)
        except (IPCError, OSError, ValueError, struct.error) as exc:
            if pipe:
                pipe.close()
            pipe, shown, retry_at = None, None, time.time() + 30
            STATE.update(status="no_discord", detail=str(exc), activity=None)


def start():
    threading.Thread(target=_loop, daemon=True, name="discord-presence").start()


def status():
    return dict(STATE, enabled=bool(SETTINGS["discord_presence"]), has_id=str(SETTINGS["discord_client_id"] or "").strip().isdigit(),
                platform=sys.platform)
