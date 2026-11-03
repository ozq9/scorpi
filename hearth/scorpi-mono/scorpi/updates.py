"""Updates from scorpi.dev: an installed Scorpi keeps itself current.

A while after the server starts, and every few hours after that, Scorpi reads the site's version.json (written by
scripts/make_release.ps1). When it names a newer version, a copy installed by Scorpi-Setup.exe (uninstall.exe sits next
to server.py) downloads the new Scorpi-Setup.exe, checks its size and SHA-256 against version.json, waits until no chat
or agent is in the middle of something, tells every open window, and runs it with --update. The setup stops Scorpi,
replaces the app's files (data\\ stays), skips pip when the packages did not change, and starts Scorpi again.

A copy run from source (a git checkout, the dev folders) never replaces itself: it only says that a new version is out.
Settings → About shows the state, can check now, and turns the automatic install off (settings: auto_update).
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

from . import VERSION, events
from .config import APP_DIR, DATA_DIR, IS_WINDOWS, SETTINGS

SITE = "https://scorpi-atp.pages.dev/"  # change with install.ps1 when scorpi.dev is attached
FIRST_CHECK = 90  # seconds after the server starts
EVERY = 6 * 3600
QUIET_FOR = 120  # an install waits until nothing has run for this long

_lock = threading.Lock()
_wake = threading.Event()
STATE = {"current": VERSION, "latest": "", "checked": 0, "status": "idle", "error": "", "installed": False, "date": ""}


def installed():
    """Set up by Scorpi-Setup.exe (which leaves uninstall.exe in the app folder), so the setup can update it."""
    return IS_WINDOWS and os.path.isfile(os.path.join(APP_DIR, "uninstall.exe"))


def version_tuple(v):
    parts = [int(x) for x in re.findall(r"\d+", str(v or ""))[:4]]
    return tuple(parts + [0] * (4 - len(parts)))


def newer(a, b):
    """a is a later version than b."""
    return version_tuple(a) > version_tuple(b)


def site():
    url = (SETTINGS.get("update_url") or SITE).strip()
    return url if url.endswith("/") else url + "/"


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Scorpi/%s (updater)" % VERSION, "Cache-Control": "no-cache"})
    return urllib.request.urlopen(req, timeout=timeout)


def check():
    """Reads version.json; returns it, and whether it is newer than this copy."""
    with _get(site() + "version.json?t=%d" % int(time.time())) as res:
        info = json.loads(res.read().decode("utf-8"))
    STATE.update(latest=str(info.get("version") or ""), date=str(info.get("date") or ""), checked=time.time(), error="")
    return info, newer(info.get("version"), VERSION)


def download(info):
    """The new setup in %TEMP%, checked against version.json's size and SHA-256 (None if it doesn't match)."""
    url = urllib.parse.urljoin(site(), info["file"])
    folder = os.path.join(tempfile.gettempdir(), "scorpi-update")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "Scorpi-Setup-%s.exe" % re.sub(r"[^\w.]", "", str(info["version"])))
    digest = hashlib.sha256()
    size = 0
    with _get(url, timeout=60) as res, open(path + ".part", "wb") as out:
        while True:
            chunk = res.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    want_size, want_sha = int(info.get("bytes") or 0), str(info.get("sha256") or "").lower()
    if (want_size and size != want_size) or not want_sha or digest.hexdigest() != want_sha:
        os.remove(path + ".part")
        return None
    os.replace(path + ".part", path)
    return path


def _tried(version=None):
    """The version last handed to the setup (data/update.json); with a version, remember that one now."""
    path = os.path.join(DATA_DIR, "update.json")
    if version:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"version": version, "at": time.time()}, fh)
        except OSError:
            pass
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _busy():
    """Something the user would lose: a chat answer or an agent still running."""
    if any(not r.done for r in events.active_runs()):
        return True
    try:
        from . import agents
        return bool(agents.running_jobs())
    except Exception:
        return False


def _launch(path):
    """Starts the setup through cmd's start, so it is nobody's child: the setup stops Scorpi with Scorpi.exe --stop, which
    ends the server with its whole process tree (taskkill /T), and a child of the server would go with it."""
    flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd.exe", "/c", "start", "", path, "--update"], creationflags=flags, close_fds=True)


def run(install=None):
    """One round: check, and (installed, allowed, newer) download, wait for a quiet moment and install."""
    if not _lock.acquire(blocking=False):
        return dict(STATE)
    try:
        STATE.update(installed=installed(), status="checking")
        info, is_newer = check()
        if not is_newer:
            STATE["status"] = "current"
            return dict(STATE)
        allowed = SETTINGS.get("auto_update", True) if install is None else install
        # a version that was installed already and is still not what runs (a broken release) is not tried again for a
        # day without being asked: no update loops
        tried = _tried()
        if install is None and tried.get("version") == info["version"] and time.time() - tried.get("at", 0) < 86400:
            allowed = False
        if not (STATE["installed"] and allowed):
            STATE["status"] = "available"
            events.publish("update", {"phase": "available", "version": info["version"], "installed": STATE["installed"]})
            return dict(STATE)
        STATE["status"] = "downloading"
        path = download(info)
        if not path:
            STATE.update(status="error", error="The download did not match version.json (size or SHA-256). It will try again later.")
            return dict(STATE)
        STATE["status"] = "waiting"
        quiet_since = None
        while True:
            if _busy():
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.time()
            if quiet_since and (install or time.time() - quiet_since >= QUIET_FOR):
                break
            time.sleep(5)
        STATE["status"] = "installing"
        _tried(info["version"])
        events.publish("update", {"phase": "installing", "version": info["version"]})
        time.sleep(4)  # long enough for every open window to say so
        _launch(path)
        return dict(STATE)
    except Exception as exc:  # offline, the site is down, ...: try again next round
        STATE.update(status="error", error=str(exc)[:200])
        return dict(STATE)
    finally:
        _lock.release()


def check_now(install=None):
    """From Settings: a round right away, in the background."""
    threading.Thread(target=run, kwargs={"install": install}, daemon=True, name="update-now").start()
    return dict(STATE, status="checking")


def _loop():
    time.sleep(FIRST_CHECK)
    while True:
        run()
        _wake.wait(EVERY)
        _wake.clear()


def start():
    STATE["installed"] = installed()
    if os.environ.get("SCORPI_NO_UPDATE"):
        return
    threading.Thread(target=_loop, daemon=True, name="updates").start()
