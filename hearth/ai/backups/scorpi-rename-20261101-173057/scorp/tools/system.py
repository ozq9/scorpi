"""Host system: programs, processes, clipboard, media keys, local services, Home Assistant."""

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

from ..config import IS_WINDOWS, SETTINGS
from . import Result, ToolError, module_available, tool

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SERVICES = [
    (11434, "Ollama", "llm", "/api/tags"),
    (1234, "LM Studio", "llm", "/v1/models"),
    (7860, "Stable Diffusion WebUI", "image", "/sdapi/v1/sd-models"),
    (8188, "ComfyUI", "image", "/system_stats"),
    (8123, "Home Assistant", "home", "/api/"),
    (8096, "Jellyfin", "media", "/System/Info/Public"),
    (32400, "Plex", "media", "/identity"),
    (5678, "n8n", "automation", "/healthz"),
    (1880, "Node-RED", "automation", "/"),
    (8384, "Syncthing", "files", "/"),
    (9091, "Transmission", "downloads", "/"),
    (6333, "Qdrant", "database", "/collections"),
    (5432, "PostgreSQL", "database", None),
    (3306, "MySQL / MariaDB", "database", None),
    (6379, "Redis", "database", None),
    (27017, "MongoDB", "database", None),
    (3000, "Web app (port 3000)", "web", "/"),
    (8080, "Web app (port 8080)", "web", "/"),
    (5173, "Vite dev server", "web", "/"),
    (9000, "Web app (port 9000)", "web", "/"),
]


def _port_open(port, host="127.0.0.1", timeout=0.25):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_services():
    found = []
    for port, name, kind, probe in SERVICES:
        if port == SETTINGS["port"] or port == SETTINGS["https_port"]:
            continue
        if _port_open(port):
            found.append({"port": port, "name": name, "kind": kind, "url": "http://127.0.0.1:%d%s" % (port, probe or ""), "probe": probe})
    return found


def open_with_default(target):
    if IS_WINDOWS:
        os.startfile(target)  # noqa: S606 - opening user-requested files is the point
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def set_clipboard(text):
    if module_available("pyperclip"):
        import pyperclip
        pyperclip.copy(text)
        return
    if IS_WINDOWS:
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"], input=text.encode("utf-8"),
                       capture_output=True, creationflags=NO_WINDOW, timeout=10)
        return
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text.encode(), timeout=5)
            return
    raise ToolError("No clipboard tool available (pip install pyperclip).")


def get_clipboard():
    if module_available("pyperclip"):
        import pyperclip
        return pyperclip.paste()
    if IS_WINDOWS:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"], capture_output=True,
                             creationflags=NO_WINDOW, timeout=10)
        return out.stdout.decode("utf-8", "replace")
    raise ToolError("No clipboard tool available (pip install pyperclip).")


def _start_menu_entries():
    roots = []
    if IS_WINDOWS:
        roots = [
            os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
        ]
    else:
        roots = ["/usr/share/applications", os.path.expanduser("~/.local/share/applications")]
    entries = {}
    for root in roots:
        for base, _, files in os.walk(root):
            for name in files:
                if name.lower().endswith((".lnk", ".url", ".desktop", ".appref-ms")):
                    label = os.path.splitext(name)[0]
                    if "uninstall" in label.lower():
                        continue
                    entries.setdefault(label, os.path.join(base, name))
    return entries


@tool(
    "list_programs",
    "List programs installed on the host (Start menu entries), optionally filtered by name.",
    {"filter?": "string: Part of the program name"},
    group="system",
)
def list_programs(ctx, filter=""):
    entries = _start_menu_entries()
    names = sorted(n for n in entries if not filter or filter.lower() in n.lower())
    return Result("Installed programs (%d):\n%s" % (len(names), "\n".join(names[:300]) or "(none)"))


@tool(
    "launch_app",
    "Start a program on the host by name (e.g. 'Spotify', 'notepad', 'Visual Studio Code'), an executable path, "
    "or a URI like 'spotify:' or 'ms-settings:'. Optional arguments.",
    {"name": "string: Program name, path or URI", "args?": "string: Command-line arguments"},
    group="system",
    risk="danger",
)
def launch_app(ctx, name, args=""):
    target = name.strip()
    entries = _start_menu_entries()
    match = next((p for n, p in entries.items() if n.lower() == target.lower()), None) or next(
        (p for n, p in entries.items() if target.lower() in n.lower()), None)
    try:
        if match and not args:
            if match.endswith(".desktop"):
                subprocess.Popen(["gtk-launch", os.path.basename(match)[:-8]])
            else:
                open_with_default(match)
            return Result("Started %s" % os.path.splitext(os.path.basename(match))[0])
        exe = shutil.which(target) or (target if os.path.exists(target) else None)
        if exe:
            subprocess.Popen([exe] + (args.split() if args else []), creationflags=0)
            return Result("Started %s %s" % (exe, args))
        if ":" in target and not os.path.exists(target):
            open_with_default(target)
            return Result("Opened %s" % target)
        if IS_WINDOWS:
            subprocess.Popen(["cmd", "/c", "start", "", target] + (args.split() if args else []), creationflags=NO_WINDOW)
            return Result("Asked Windows to start %s" % target)
    except Exception as exc:
        raise ToolError("Could not start %s: %s" % (target, exc))
    raise ToolError("Program %r not found. Use list_programs to see what is installed." % target)


@tool("open_url", "Open a website in the host's default browser (visible to the person at the PC).", {"url": "string: URL"}, group="system", risk="write")
def open_url(ctx, url):
    webbrowser.open(url if "://" in url else "https://" + url)
    return Result("Opened %s on the host." % url)


@tool(
    "list_processes",
    "List running programs/processes on the host with CPU and memory use, optionally filtered.",
    {"filter?": "string: Name filter", "sort?": "string[memory,cpu,name]: Sort order"},
    group="system",
    requires=("psutil",),
)
def list_processes(ctx, filter="", sort="memory"):
    import psutil
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        try:
            name = p.info["name"] or ""
            if filter and filter.lower() not in name.lower():
                continue
            procs.append((p.info["pid"], name, (p.info["memory_info"].rss if p.info["memory_info"] else 0) / 1048576, p.info["cpu_percent"] or 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key = {"cpu": lambda r: -r[3], "name": lambda r: r[1].lower()}.get(sort, lambda r: -r[2])
    procs.sort(key=key)
    lines = ["%7d  %-38s %8.0f MB  %5.1f%%" % row for row in procs[:60]]
    return Result("PID      Name                                     Memory   CPU\n" + "\n".join(lines))


@tool(
    "kill_process",
    "Close a running program by name or PID (graceful, then forced).",
    {"target": "string: Process name (e.g. 'notepad.exe') or PID"},
    group="system",
    risk="danger",
    requires=("psutil",),
)
def kill_process(ctx, target):
    import psutil
    victims = []
    for p in psutil.process_iter(["pid", "name"]):
        if str(p.info["pid"]) == str(target).strip() or (p.info["name"] or "").lower() in (target.lower(), target.lower() + ".exe"):
            victims.append(p)
    if not victims:
        raise ToolError("No process matches %r." % target)
    for p in victims:
        try:
            p.terminate()
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(victims, timeout=4)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    return Result("Closed %d process(es) matching %s." % (len(victims), target))


@tool("system_info", "Report the host's OS, CPU, RAM, disks, battery, GPU and uptime.", {}, group="system")
def system_info(ctx):
    lines = ["OS: %s %s (%s)" % (platform.system(), platform.release(), platform.version()),
             "Machine: %s, %s" % (platform.node(), platform.machine()),
             "Python: %s" % platform.python_version()]
    if module_available("psutil"):
        import psutil
        vm = psutil.virtual_memory()
        lines.append("CPU: %d cores, %.0f%% busy" % (psutil.cpu_count(), psutil.cpu_percent(interval=0.4)))
        lines.append("RAM: %.1f / %.1f GB used (%.0f%%)" % ((vm.total - vm.available) / 1e9, vm.total / 1e9, vm.percent))
        for part in psutil.disk_partitions(all=False):
            try:
                du = psutil.disk_usage(part.mountpoint)
                lines.append("Disk %s: %.0f / %.0f GB used" % (part.mountpoint, du.used / 1e9, du.total / 1e9))
            except OSError:
                pass
        battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if battery:
            lines.append("Battery: %d%%%s" % (battery.percent, " (charging)" if battery.power_plugged else ""))
        lines.append("Uptime: %.1f hours" % ((time.time() - psutil.boot_time()) / 3600))
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader"],
                                 capture_output=True, timeout=8, creationflags=NO_WINDOW).stdout.decode()
            lines.append("GPU: " + out.strip())
        except Exception:
            pass
    return Result("\n".join(lines))


@tool(
    "clipboard",
    "Read or set the host clipboard text.",
    {"action": "string[get,set]: Read or write", "text?": "string: Text to copy (for set)"},
    group="system",
    risk="write",
)
def clipboard(ctx, action, text=""):
    if action == "set":
        set_clipboard(text)
        return Result("Copied %d characters to the clipboard." % len(text))
    return Result("Clipboard:\n" + (get_clipboard() or "(empty)")[:20000])


@tool(
    "media_control",
    "Control media playback and volume on the host (works with Spotify, YouTube, any player): play_pause, next, previous, "
    "volume_up, volume_down, mute.",
    {"action": "string[play_pause,next,previous,volume_up,volume_down,mute]: Media key", "times?": "integer: Repeat count"},
    group="system",
    risk="write",
    requires=("pyautogui",),
)
def media_control(ctx, action, times=1):
    import pyautogui
    key = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack", "volume_up": "volumeup",
           "volume_down": "volumedown", "mute": "volumemute"}[action]
    for _ in range(max(1, min(int(times or 1), 50))):
        pyautogui.press(key)
    return Result("Pressed %s x%s" % (action, times))


@tool(
    "local_services",
    "Detect programs and servers running on the host that can be connected to (Ollama, LM Studio, Stable Diffusion, ComfyUI, "
    "Home Assistant, Jellyfin, Plex, databases, dev servers...). Use http_get to talk to them.",
    {},
    group="system",
)
def local_services(ctx):
    found = detect_services()
    if not found:
        return Result("No known local services detected.")
    return Result("Detected on this PC:\n" + "\n".join("- %s (%s) at %s" % (s["name"], s["kind"], s["url"]) for s in found))


@tool(
    "home_assistant",
    "Control smart-home devices through Home Assistant (configure URL and token in Settings). Actions: states (list devices, "
    "optional filter), call (domain/service with entity_id, e.g. light/turn_on).",
    {
        "action": "string[states,call]: What to do",
        "filter?": "string: Entity filter for states, e.g. 'light.'",
        "domain?": "string: e.g. light, switch, climate, media_player",
        "service?": "string: e.g. turn_on, turn_off, toggle, set_temperature",
        "entity_id?": "string: Target entity",
        "data?": {"type": "object", "description": "Extra service data, e.g. {\"brightness_pct\": 40}"},
    },
    group="system",
    risk="danger",
)
def home_assistant(ctx, action, filter="", domain="", service="", entity_id="", data=None):
    base = (SETTINGS["ha_url"] or "").rstrip("/")
    token = SETTINGS["ha_token"]
    if not base or not token:
        raise ToolError("Home Assistant is not configured. Add its URL and a long-lived token in Settings → Connections.")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if action == "states":
        req = urllib.request.Request(base + "/api/states", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            states = json.loads(resp.read())
        rows = [s for s in states if not filter or filter.lower() in s["entity_id"].lower()]
        return Result("\n".join("%s = %s (%s)" % (s["entity_id"], s["state"], s.get("attributes", {}).get("friendly_name", "")) for s in rows[:150]))
    body = dict(data or {})
    if entity_id:
        body["entity_id"] = entity_id
    req = urllib.request.Request("%s/api/services/%s/%s" % (base, domain, service), data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()
    return Result("Called %s.%s on %s" % (domain, service, entity_id or "(no entity)"))
