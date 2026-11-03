"""Scorpi Radio: internet radio played through Scorpi, plus what Windows itself is playing (Spotify, YouTube, any player).

Radio stations come from radio-browser.info (free, no key). The app plays them through /api/music/stream, so phones on
https can play http streams, the page can listen to the beat (same origin), and the song titles inside the stream (ICY
metadata) become "now playing". What other programs play is read from Windows' media sessions with pywinrt, covers
included (a PowerShell helper stands in without it, minus the covers), which also take play/pause/next/previous.
Radio songs (and players without a cover) get theirs looked up by song name (iTunes Search, free).

One device plays the radio at a time (the "speaker"); every device shows it and can control it.
"""

import asyncio
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from . import events
from .config import DATA_DIR, IS_WINDOWS

UA = "Scorpi/1.0 (+https://github.com/scorpi)"
STATE_PATH = os.path.join(DATA_DIR, "music.json")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PLAYABLE = {"MP3", "AAC", "AAC+", "OGG", "OPUS"}

# moods offered as one-tap chips (and understood by the play_music tool): mood -> radio-browser tags, tried in order
MOODS = {
    "focus": ["focus", "study", "ambient"],
    "chill": ["chillout", "chill", "lounge"],
    "lofi": ["lofi", "lo-fi"],
    "jazz": ["jazz", "smooth jazz"],
    "classical": ["classical", "piano"],
    "electronic": ["electronic", "house", "techno"],
    "rock": ["rock", "classic rock"],
    "hiphop": ["hip hop", "hiphop", "rap"],
    "pop": ["pop", "top 40"],
    "news": ["news", "talk"],
}

_lock = threading.RLock()
_state = {
    # the radio, shared by every device: what is on air, whether it plays, and which device plays it
    "radio": {"station": None, "playing": False, "speaker": "", "title": "", "artist": "", "art": "", "mood": ""},
    # what Windows says is playing in other programs
    "system": None,
    "favorites": [],
    "recent": [],
}
_queue = {"stations": [], "index": 0}
_conv_device = {}  # conversation id -> the device that sent its latest message (where "play music" should sound)


# ---------- state ----------

def _load():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    _state["favorites"] = [s for s in saved.get("favorites", []) if isinstance(s, dict) and s.get("url")][:60]
    _state["recent"] = [s for s in saved.get("recent", []) if isinstance(s, dict) and s.get("url")][:12]
    last = saved.get("station")
    if isinstance(last, dict) and last.get("url"):
        _state["radio"]["station"] = last


def _save():
    data = {"favorites": _state["favorites"], "recent": _state["recent"], "station": _state["radio"]["station"]}
    tmp = STATE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


def snapshot():
    with _lock:
        out = json.loads(json.dumps(_state))
    song = out["system"]
    if song and song["status"] == "Playing":
        now = time.time()
        song["position"] = round(song["position"] + now - song["at"], 1)
        song["at"] = now
    out["moods"] = list(MOODS)
    out["windows"] = IS_WINDOWS
    return out


def _publish():
    events.publish("music", snapshot())


def note_device(conv_id, device):
    """The chat endpoint tells us which device a message came from, so "play some jazz" plays there."""
    if conv_id and device:
        _conv_device[conv_id] = str(device)[:64]


def device_for(conv_id):
    return _conv_device.get(conv_id, "")


# ---------- stations (radio-browser.info) ----------

def _api(path, params):
    url = "https://all.api.radio-browser.info/json/" + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read(4 * 1024 * 1024))


def _station(raw):
    return {
        "id": raw.get("stationuuid", ""),
        "name": (raw.get("name") or "").strip()[:80] or "Radio",
        "url": raw.get("url_resolved") or raw.get("url") or "",
        "favicon": raw.get("favicon") or "",
        "tags": [t for t in (raw.get("tags") or "").split(",") if t][:6],
        "country": raw.get("countrycode") or "",
        "codec": raw.get("codec") or "",
        "bitrate": raw.get("bitrate") or 0,
        "homepage": raw.get("homepage") or "",
    }


def search(query="", tag="", limit=24):
    """Stations by name or tag, most listened first, only ones a browser can play."""
    params = {"limit": max(1, min(int(limit or 24), 60)) * 2, "order": "clickcount", "reverse": "true", "hidebroken": "true"}
    if query:
        params["name"] = query
    if tag:
        params["tag"] = tag
    seen, out = set(), []
    for raw in _api("stations/search", params):
        s = _station(raw)
        if raw.get("hls") or s["codec"].upper() not in PLAYABLE or not s["url"].startswith(("http://", "https://")):
            continue
        key = s["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out[: params["limit"] // 2]


def stations_for(mood_or_query):
    """A mood ("focus", "lofi"…), a genre or a station name -> stations."""
    text = (mood_or_query or "").strip().lower()
    key = text.replace("-", "").replace(" ", "")
    for tag in MOODS.get(key, []):
        found = search(tag=tag)
        if found:
            return found, key
    if text:
        found = search(tag=text) or search(query=text)
        return found, ""
    return search(), ""


def _remember(station):
    _state["recent"] = [station] + [s for s in _state["recent"] if s.get("url") != station.get("url")]
    _state["recent"] = _state["recent"][:12]


def set_favorite(station, on):
    with _lock:
        rest = [s for s in _state["favorites"] if s.get("url") != station.get("url")]
        _state["favorites"] = ([station] + rest if on else rest)[:60]
        _save()
    _publish()


# ---------- the radio ----------

def play(station=None, speaker="", mood="", queue=None):
    """Put a station on air (or resume the last one) on the speaker device."""
    with _lock:
        radio = _state["radio"]
        if queue is not None:
            _queue["stations"], _queue["index"] = queue, 0
        if station:
            if not radio["station"] or radio["station"].get("url") != station.get("url"):
                radio.update({"title": "", "artist": "", "art": ""})
            radio["station"] = station
            _remember(station)
        if not radio["station"]:
            return False
        radio["playing"] = True
        if speaker:
            radio["speaker"] = speaker
        if mood or station:
            radio["mood"] = mood
        _save()
        other = _state["system"]
    if other and other["status"] == "Playing":
        system_control("pause")  # one thing at a time: the radio takes over from Spotify & co.
    _publish()
    return True


def pause():
    with _lock:
        _state["radio"]["playing"] = False
    _publish()


def skip(step=1):
    """Next (or previous) station: in the mood list that was picked, else in the favourites, else recent ones."""
    with _lock:
        cur = (_state["radio"]["station"] or {}).get("url")
        pool = _queue["stations"] or _state["favorites"] or _state["recent"]
        if not pool:
            return False
        i = next((n for n, s in enumerate(pool) if s.get("url") == cur), -1)
        nxt = pool[(i + step) % len(pool)]
    return play(nxt)


def set_title(stream_url, text):
    """A song title from inside the stream ("Artist - Title")."""
    with _lock:
        radio = _state["radio"]
        if not radio["station"] or radio["station"].get("url") != stream_url:
            return
        artist, _, title = text.partition(" - ")
        if not title:
            artist, title = "", text
        if (artist, title) == (radio["artist"], radio["title"]):
            return
        radio.update({"artist": artist.strip(), "title": title.strip(), "art": ""})
    _publish()
    _find_art_later("radio", artist, title)


# ---------- album covers ----------

_art_cache = {}


def _clean(text):
    text = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", text or "")  # (Remastered 2011), [Official Video]
    text = re.sub(r"\s+(feat\.?|ft\.?|featuring)\s.*$", "", text, flags=re.I)
    text = re.sub(r"\s*[-–]\s*(slowed|sped up|reverb|remix|live|edit).*$", "", text, flags=re.I)
    return text.strip()


def find_art(artist, title, album=""):
    """A cover picture URL for a song (or else its album), or ''."""
    title = _clean(title)
    if not title:
        return ""
    key = (artist.lower(), title.lower())
    if key in _art_cache:
        return _art_cache[key]
    url = ""
    for term, entity in (((_clean(artist) + " " + title).strip(), "song"), ((_clean(artist) + " " + _clean(album)).strip(), "album")):
        if entity == "album" and not _clean(album):
            break
        try:
            req = urllib.request.Request("https://itunes.apple.com/search?" + urllib.parse.urlencode({"term": term, "entity": entity, "limit": 1}),
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=8) as resp:
                hits = json.loads(resp.read()).get("results") or []
        except Exception:
            hits = []
        if hits and hits[0].get("artworkUrl100"):
            url = hits[0]["artworkUrl100"].replace("100x100bb", "600x600bb")
            break
    if len(_art_cache) > 300:
        _art_cache.clear()
    _art_cache[key] = url
    return url


def _find_art_later(source, artist, title, album=""):
    def work():
        art = find_art(artist, title, album)
        with _lock:
            target = _state["radio"] if source == "radio" else _state["system"]
            if not target or (target.get("artist"), target.get("title")) != (artist.strip(), title.strip()) or target.get("art") == art:
                return
            target["art"] = art
        _publish()

    threading.Thread(target=work, daemon=True).start()


_img_cache = {}


def fetch_image(url):
    """(bytes, content type) of a cover or station logo, kept for a while (the app reads its colours)."""
    if url in _img_cache:
        return _img_cache[url]
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8) as resp:
        ctype = resp.headers.get("Content-Type") or "image/jpeg"
        body = resp.read(3 * 1024 * 1024)
    if not ctype.startswith("image/"):
        raise ValueError("not an image")
    if len(_img_cache) > 40:
        _img_cache.pop(next(iter(_img_cache)))
    _img_cache[url] = (body, ctype)
    return body, ctype


# ---------- the stream relay ----------

def open_stream(url):
    """Connect to a station; returns (response, icy metaint)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Icy-MetaData": "1", "Accept": "*/*"})
    resp = urllib.request.urlopen(req, timeout=15)
    try:
        metaint = int(resp.headers.get("icy-metaint") or 0)
    except ValueError:
        metaint = 0
    return resp, metaint


def _read(resp, n):
    out = b""
    while len(out) < n:
        chunk = resp.read(n - len(out))
        if not chunk:
            break
        out += chunk
    return out


def relay(resp, metaint, write, stream_url):
    """Copy the audio to the listener, taking the ICY song titles out on the way. Returns when either side stops."""
    try:
        while True:
            if not metaint:
                chunk = resp.read(16384)
                if not chunk:
                    return
                write(chunk)
                continue
            audio = _read(resp, metaint)
            if not audio:
                return
            write(audio)
            size = _read(resp, 1)
            if not size:
                return
            meta = _read(resp, size[0] * 16) if size[0] else b""
            match = re.search(rb"StreamTitle='(.*?)';", meta)
            if match:
                text = match.group(1).decode("utf-8", "replace").strip()
                if text:
                    set_title(stream_url, text)
    finally:
        resp.close()


# ---------- what Windows is playing ----------

# Runs in a hidden PowerShell: prints the current media session as one JSON line a second, and takes commands
# (play_pause, play, pause, next, previous) one per line on stdin. Ends when Scorpi closes its stdin.
WATCHER = r"""
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(4000) | Out-Null; $task.Result }
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput())
$pending = $reader.ReadLineAsync()
while ($true) {
  if ($pending.IsCompleted) {
    $cmd = $pending.Result
    if ($null -eq $cmd) { break }
    $s = $mgr.GetCurrentSession()
    if ($s) {
      switch ($cmd.Trim()) {
        'play_pause' { $null = $s.TryTogglePlayPauseAsync() }
        'play' { $null = $s.TryPlayAsync() }
        'pause' { $null = $s.TryPauseAsync() }
        'next' { $null = $s.TrySkipNextAsync() }
        'previous' { $null = $s.TrySkipPreviousAsync() }
      }
    }
    $pending = $reader.ReadLineAsync()
    Start-Sleep -Milliseconds 250
  }
  $s = $mgr.GetCurrentSession()
  if ($s) {
    $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
    $i = $s.GetPlaybackInfo()
    $t = $s.GetTimelineProperties()
    $o = [ordered]@{ app = $s.SourceAppUserModelId; title = $p.Title; artist = $p.Artist; album = $p.AlbumTitle; status = "$($i.PlaybackStatus)";
      position = [math]::Round($t.Position.TotalSeconds, 1); duration = [math]::Round($t.EndTime.TotalSeconds, 1);
      updated = $t.LastUpdatedTime.ToUnixTimeMilliseconds(); next = $i.Controls.IsNextEnabled; previous = $i.Controls.IsPreviousEnabled }
  } else { $o = @{ app = '' } }
  [Console]::Out.WriteLine(($o | ConvertTo-Json -Compress))
  [Console]::Out.Flush()
  Start-Sleep -Milliseconds 900
}
"""

_watcher = {"proc": None, "winrt": None, "lock": threading.Lock()}

# programs that are Scorpi's own window: their session is the radio itself
_OWN_APPS = ("msedge", "chrome", "webview", "pythonw", "python", "scorpi")


def _app_name(app_id):
    base = re.split(r"[\\/!]", app_id or "")[-1]
    base = re.sub(r"\.exe$", "", base, flags=re.I)
    known = {"spotify": "Spotify", "msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox", "vlc": "VLC",
             "microsoft.zunemusic": "Media Player", "applemusic": "Apple Music", "tidal": "TIDAL", "deezer": "Deezer"}
    low = base.lower()
    for key, name in known.items():
        if key in low:
            return name
    return base[:1].upper() + base[1:] if base else ""


def _on_session(raw):
    with _lock:
        before = _state["system"]
        if not raw.get("app") or not (raw.get("title") or "").strip():
            if before is None:
                return
            _state["system"] = None
            changed = True
        else:
            radio = _state["radio"]
            # Scorpi's own window shows up as a media session with the radio's song: that is not "another program"
            own = radio["station"] and (raw.get("title") or "") in (radio["title"], radio["station"].get("name"))
            if own and any(a in (raw.get("app") or "").lower() for a in _OWN_APPS):
                return
            now = time.time()
            playing = raw.get("status") == "Playing"
            pos = float(raw.get("position") or 0)
            if playing and raw.get("updated"):
                pos += max(0.0, now - raw["updated"] / 1000.0)  # where the song is now, not when Windows last said
            info = {"app": _app_name(raw.get("app")), "title": raw.get("title", "").strip(), "artist": (raw.get("artist") or "").strip(),
                    "album": raw.get("album") or "", "status": raw.get("status") or "", "position": round(pos, 1),
                    "duration": float(raw.get("duration") or 0), "at": now, "next": bool(raw.get("next")),
                    "previous": bool(raw.get("previous")), "art": ""}
            same_song = before and (before["title"], before["artist"]) == (info["title"], info["artist"])
            # the player's own cover (read from Windows) wins over one looked up by name
            info["art"] = raw.get("art") or (before.get("art", "") if same_song else "")
            if same_song:
                expected = before["position"] + (now - before["at"] if before["status"] == "Playing" else 0)
                changed = (before["status"] != info["status"] or abs(expected - info["position"]) > 3 or before["app"] != info["app"]
                           or before.get("art") != info["art"])
            else:
                changed = True
            _state["system"] = info if changed or not before else {**before}
        song = _state["system"]
    if changed:
        _publish()
        # no cover from the player (or no WinRT): look it up by name
        if song and not song.get("art") and not raw.get("has_thumbnail") and (not before or (before["title"], before["artist"]) != (song["title"], song["artist"])):
            _find_art_later("system", song["artist"], song["title"], song.get("album", ""))


# ---------- what Windows is playing, read natively (pywinrt): real covers, no helper process ----------

_STATUS = {0: "Closed", 1: "Opened", 2: "Changing", 3: "Stopped", 4: "Playing", 5: "Paused"}
_covers = {}  # "windows:<id>" -> (bytes, content type): the players' own covers, the last few songs
_commands = queue.Queue()


def winrt_available():
    try:
        import winrt.windows.media.control  # noqa: F401
        import winrt.windows.storage.streams  # noqa: F401
        return True
    except Exception:
        return False


async def _read_cover(props):
    """The cover a player hands Windows, kept under a short id; '' if it has none."""
    from winrt.windows.storage.streams import Buffer, InputStreamOptions
    if not props.thumbnail:
        return ""
    stream = await props.thumbnail.open_read_async()
    size = int(stream.size)
    if not size or size > 8 * 1024 * 1024:
        return ""
    data = bytes(await stream.read_async(Buffer(size), size, InputStreamOptions.READ_AHEAD))
    key = "windows:" + hashlib.sha1(data).hexdigest()[:16]
    if key not in _covers:
        if len(_covers) >= 8:
            _covers.pop(next(iter(_covers)))
        _covers[key] = (data, stream.content_type or "image/jpeg")
    return key


async def _watch_winrt():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    mgr = await Manager.request_async()
    song_key, cover, cover_checks, tick = None, "", 0, 0
    while True:
        session = mgr.get_current_session()
        while not _commands.empty():
            action = _commands.get_nowait()
            if session:
                call = {"play_pause": session.try_toggle_play_pause_async, "play": session.try_play_async, "pause": session.try_pause_async,
                        "next": session.try_skip_next_async, "previous": session.try_skip_previous_async}[action]
                try:
                    await call()
                except Exception:
                    pass
            tick = 0  # report the result right away
        if tick % 4 == 0:
            raw = {"app": ""}
            if session:
                try:
                    props = await session.try_get_media_properties_async()
                    info = session.get_playback_info()
                    tl = session.get_timeline_properties()
                    key = (session.source_app_user_model_id, props.title, props.artist, props.album_title)
                    if key != song_key:
                        song_key, cover, cover_checks = key, "", 3
                    # a player often names the new song a moment before it hands over the new cover: look a few times
                    if cover_checks:
                        cover_checks -= 1
                        cover = await _read_cover(props) or cover
                    raw = {"app": session.source_app_user_model_id, "title": props.title, "artist": props.artist, "album": props.album_title,
                           "status": _STATUS.get(int(info.playback_status), ""), "position": tl.position.total_seconds(),
                           "duration": tl.end_time.total_seconds(), "updated": tl.last_updated_time.timestamp() * 1000,
                           "next": info.controls.is_next_enabled, "previous": info.controls.is_previous_enabled,
                           "art": cover, "has_thumbnail": bool(props.thumbnail)}
                except Exception:
                    raw = None
            if raw is not None:
                _on_session(raw)
        tick += 1
        await asyncio.sleep(0.25)


def _run_winrt():
    try:
        asyncio.run(_watch_winrt())
    except Exception:
        pass
    _watcher["winrt"] = None  # ended: the next request starts it again (or the PowerShell helper)


def windows_cover(key):
    return _covers.get(key)


def _read_watcher(proc):
    for line in proc.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            _on_session(json.loads(line))
        except Exception:
            continue
    with _watcher["lock"]:
        if _watcher["proc"] is proc:
            _watcher["proc"] = None


def ensure_watcher():
    """Start watching Windows' media sessions once (Windows only): natively with pywinrt, else a PowerShell helper."""
    if not IS_WINDOWS:
        return
    with _watcher["lock"]:
        if _watcher.get("winrt") and _watcher["winrt"].is_alive():
            return
        if winrt_available():
            _watcher["winrt"] = threading.Thread(target=_run_winrt, name="scorpi-media", daemon=True)
            _watcher["winrt"].start()
            return
        proc = _watcher["proc"]
        if proc and proc.poll() is None:
            return
        try:
            proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", WATCHER],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        except OSError:
            return
        _watcher["proc"] = proc
    threading.Thread(target=_read_watcher, args=(proc,), daemon=True).start()


def system_control(action):
    """play_pause | play | pause | next | previous for whatever Windows is playing."""
    if action not in ("play_pause", "play", "pause", "next", "previous"):
        raise ValueError("unknown action")
    ensure_watcher()
    if _watcher.get("winrt") and _watcher["winrt"].is_alive():
        _commands.put(action)
        return True
    proc = _watcher["proc"]
    if not proc or proc.poll() is not None:
        return False
    try:
        proc.stdin.write(action + "\n")
        proc.stdin.flush()
        return True
    except OSError:
        return False


_load()
