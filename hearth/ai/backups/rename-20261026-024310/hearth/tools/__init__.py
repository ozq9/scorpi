"""Tool registry. Each tool is a plain function registered with a compact schema."""

import importlib.util
import os
import re

from ..config import SETTINGS, WORKSPACE

TOOLS = {}

GROUPS = {
    "core": ("Core", "Memory, plans, notifications, calculations and tool loading."),
    "files": ("Files", "Browse, read, write, move and search files anywhere on the host PC."),
    "docs": ("Documents", "Read, create and edit PDF, Word, Excel and PowerPoint files; compare documents."),
    "code": ("Code", "Run Python, JavaScript and shell (PowerShell) commands on the host."),
    "web": ("Web & APIs", "Search the web, read pages, Wikipedia, and call any HTTP API."),
    "data": ("Data analysis", "Inspect datasets (CSV/Excel/JSON) and draw charts."),
    "media": ("Media", "Generate images, logos, diagrams and audio; transcribe recordings."),
    "computer": ("Computer use", "See the screen, click, type, open apps and drive a real browser."),
    "system": ("System", "Processes, programs, clipboard, system info and local services."),
    "maps": ("Maps", "Find places, geocode addresses, plan routes and check weather."),
    "agenda": ("Agenda", "Reminders and calendar events."),
    "records": ("Records", "A structured database: collections of records plus read-only SQL."),
    "knowledge": ("Knowledge", "Search and grow the personal document library (RAG)."),
    "agents": ("Agents", "Hand jobs to dedicated agents (coder, researcher...), create custom agents, background tasks, automations."),
    "learning": ("Learning", "Interactive quizzes and practice exercises."),
    "looks": ("Look & plugins", "Restyle Scorp itself from a vibe, add small interface plugins, switch styles."),
}

# Keywords that pull a group into context automatically in "auto" mode.
GROUP_HINTS = {
    "files": r"file|folder|director|desktop|download|document|\bpath\b|\.txt|\.md|disk|drive|ordner|datei",
    "docs": r"pdf|docx|word|excel|xlsx|spreadsheet|powerpoint|pptx|contract|vertrag|report|invoice|document|summar|compare|resume|letter",
    "code": r"python|javascript|\bjs\b|script|code|run|execute|calculat|compute|powershell|terminal|command|install|program",
    "web": r"search|google|web|internet|news|latest|look up|website|url|http|api|research|source|cite|verify|wiki|today|current|price",
    "data": r"csv|dataset|data|chart|graph|plot|statistic|analy|table|excel|average|trend",
    "media": r"image|picture|photo|draw|illustrat|logo|icon|diagram|flowchart|render|generate.*(art|visual)|audio|mp3|transcri|voice|sketch|poster",
    "computer": r"click|screen|screenshot|window|mouse|type into|browser|navigate|open .*(app|site|page)|tab|button|log ?in|fill|"
                r"\bgui\b|desktop app|menu|dialog|settings|control panel|notepad|explorer|go to|scroll|drag",
    "system": r"process|running|program|app|cpu|ram|memory usage|clipboard|service|kill|close|launch|start|system|battery|spotify|steam|discord",
    "maps": r"map|route|direction|distance|near|nearby|place|restaurant|cafe|address|travel|drive to|walk to|weather|forecast|where is|location",
    "agenda": r"remind|reminder|calendar|event|meeting|appointment|schedule|tomorrow|tonight|next week|at \d|o'clock|deadline|termin|erinner",
    "records": r"record|database|\bdb\b|table|entry|entries|log|track|inventory|collection|sql|contacts|expenses",
    "knowledge": r"my (docs|documents|notes|files)|knowledge|library|according to|in the docs|handbook|manual",
    "agents": r"background|automat|every (day|morning|week|hour)|recurring|delegate|sub-?agent|long[- ]running|later|monitor|daily|"
              r"agent|coder|coding|programm|website|webseite|homepage|landing ?page|web ?app|\bapp\b|\bgame\b|spiel|project|projekt|"
              r"\bmods?\b|plugin|(switch|change|use|with|wechsel|nimm|benutz)\b.{0,40}\bmodel|modell|"
              r"\bbuild\b|\bbau|erstell|entwickel|develop|hintergrund|jeden (tag|morgen|abend|montag|dienstag|mittwoch|donnerstag|freitag)|"
              r"täglich|stündlich|wöchentlich|um \d{1,2} ?(uhr|:)|erinner|remind|fortschritt|progress|wie weit|was macht|status|stopp",
    "learning": r"learn|teach|quiz|practice|exercise|explain|study|exam|test me|flashcard|lesson|tutor",
    "looks": r"\bstyle|theme|design|\blook|vibe|aesthetic|font|colou?r|dark mode|light mode|\bui\b|interface|layout|"
             r"corner|rounded|minimal|clean|bigger text|smaller text|plugin|widget|customi[sz]|restyle|skin|"
             r"aussehen|farbe|schrift|stil|oberfläche",
}


class Result:
    def __init__(self, text, ok=True, display=None, images=None, files=None):
        self.text = text
        self.ok = ok
        self.display = display  # dict rendered as a card in the client
        self.images = images or []  # base64 PNG/JPEG shown to vision models
        self.files = files or []  # paths produced by the tool


class ToolError(Exception):
    pass


def _parse_params(spec):
    """{'path': 'string: Absolute path', 'limit?': 'integer: Max'} -> JSON schema."""
    props, required = {}, []
    for raw_key, raw in (spec or {}).items():
        key = raw_key.rstrip("?")
        if not raw_key.endswith("?"):
            required.append(key)
        if isinstance(raw, dict):
            props[key] = raw
            continue
        kind, _, desc = raw.partition(":")
        kind = kind.strip()
        entry = {"description": desc.strip()}
        enum = re.match(r"(\w+)\[(.*)\]$", kind)
        if enum:
            kind = enum.group(1)
            entry["enum"] = [v.strip() for v in enum.group(2).split(",")]
        arr = re.match(r"array<(\w+)>$", kind)
        if arr:
            entry["type"] = "array"
            entry["items"] = {"type": arr.group(1)}
        else:
            entry["type"] = kind
            if kind == "array":
                entry["items"] = {"type": "object"}
        props[key] = entry
    return {"type": "object", "properties": props, "required": required}


def tool(name, description, params=None, group="core", risk="read", requires=(), platforms=None):
    """risk: read (safe), write (changes data), danger (runs code or controls the PC)."""

    def wrap(fn):
        TOOLS[name] = {
            "name": name,
            "description": description,
            "schema": _parse_params(params),
            "group": group,
            "risk": risk,
            "requires": tuple(requires),
            "platforms": platforms,
            "fn": fn,
        }
        return fn

    return wrap


_module_cache = {}


def module_available(name):
    if name not in _module_cache:
        try:
            _module_cache[name] = importlib.util.find_spec(name) is not None
        except Exception:
            _module_cache[name] = False
    return _module_cache[name]


def availability(entry):
    missing = [m for m in entry["requires"] if not module_available(m)]
    if missing:
        return False, "pip install " + " ".join(PIP_NAMES.get(m, m) for m in missing)
    return True, ""


PIP_NAMES = {
    "docx": "python-docx", "pptx": "python-pptx", "fpdf": "fpdf2", "faster_whisper": "faster-whisper",
    "pyautogui": "pyautogui", "pygetwindow": "pygetwindow", "playwright": "playwright", "ddgs": "ddgs",
    "edge_tts": "edge-tts", "cv2": "opencv-python", "PIL": "pillow", "psutil": "psutil",
    "pyperclip": "pyperclip", "send2trash": "send2trash", "uiautomation": "uiautomation",
}


def policy(name):
    entry = TOOLS.get(name)
    configured = (SETTINGS["tool_policy"] or {}).get(name)
    if configured in ("allow", "ask", "off"):
        return configured
    if not entry:
        return "ask"
    return {"read": "allow", "write": "allow", "danger": "ask"}[entry["risk"]]


def schema_for(name):
    entry = TOOLS[name]
    return {
        "type": "function",
        "function": {"name": name, "description": entry["description"], "parameters": entry["schema"]},
    }


def catalog():
    out = []
    for name, entry in sorted(TOOLS.items(), key=lambda kv: (kv[1]["group"], kv[0])):
        ok, hint = availability(entry)
        out.append({
            "name": name,
            "description": entry["description"],
            "group": entry["group"],
            "risk": entry["risk"],
            "available": ok,
            "hint": hint,
            "policy": policy(name),
        })
    return out


def resolve_path(path, must_exist=False):
    """Expand a user/model supplied path. Relative paths land in the workspace."""
    if not path:
        raise ToolError("A path is required.")
    path = os.path.expandvars(os.path.expanduser(str(path).strip().strip('"')))
    if not os.path.isabs(path):
        path = os.path.join(WORKSPACE, path)
    path = os.path.abspath(path)
    check_access(path)
    if must_exist and not os.path.exists(path):
        raise ToolError("Not found: %s" % path)
    return path


def _norm(p):
    return os.path.normcase(os.path.realpath(os.path.expanduser(p)))


def check_access(path):
    target = _norm(path)
    for blocked in SETTINGS["blocked_paths"] or []:
        root = _norm(blocked)
        if target == root or target.startswith(root.rstrip(os.sep) + os.sep):
            raise ToolError("Access to %s is blocked in Settings → Permissions." % path)
    allowed = SETTINGS["allowed_paths"] or []
    if allowed:
        roots = [_norm(a) for a in allowed] + [_norm(WORKSPACE)]
        if not any(target == r or target.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
            raise ToolError("%s is outside the allowed folders (Settings → Permissions)." % path)


def load_all():
    from . import agenda, canvas, code, computer, core, data, docs, files, looks, media, places, records, system, web  # noqa: F401
    from .. import knowledge, agents_tools  # noqa: F401


def select_tools(text, groups_override=None, extra=None, model_caps=None, scope=None):
    """Pick the tool schemas sent to the model. scope ("chat" or "code") filters MCP tools by where their server is used."""
    mode = SETTINGS["tool_mode"]
    enabled = set(SETTINGS["tool_groups"] or GROUPS.keys()) | {"looks"}  # restyling Scorp itself is always possible
    if groups_override:
        chosen = set(groups_override)
    elif mode == "all":
        chosen = set(enabled)
    else:
        chosen = {"files", "web"} & enabled
        lowered = (text or "").lower()
        for group, pattern in GROUP_HINTS.items():
            if group in enabled and re.search(pattern, lowered):
                chosen.add(group)
    chosen |= set(extra or [])
    if "computer" in chosen and "system" in enabled:
        chosen.add("system")  # launch_app and friends belong to operating the PC
    chosen.add("core")
    names = []
    for name, entry in TOOLS.items():
        group = entry["group"]
        base = group.split(":")[0]
        if base != "mcp" and group not in chosen:
            continue
        if base == "mcp" and scope and entry.get("scope", "both") not in ("both", scope):
            continue
        if policy(name) == "off" or not availability(entry)[0]:
            continue
        if entry["platforms"] and os.name not in entry["platforms"]:
            continue
        names.append(name)
    return names, chosen
