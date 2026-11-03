"""Looks: styles (design tokens + CSS) and plugins (mods: CSS, JavaScript with the scorp API, an addition to Scorp's
instructions, and settings the user can change) for Scorp's own interface. The chat assistant makes them from a vibe
("a clean black and white style with small font"); every device applies the active style and the enabled plugins live.
Stored in data/looks.json; what plugins keep with scorp.shared is in data/plugin_data.json."""

import json
import os
import re
import threading
import time

from . import events
from .config import DATA_DIR

PATH = os.path.join(DATA_DIR, "looks.json")
_lock = threading.Lock()

# the design tokens a style can set (anything else must go through its CSS)
TOKENS = {
    "--bg": "page colour behind the glass (default #000)",
    "--ink": "main text colour", "--ink-2": "secondary text", "--muted": "quiet text", "--faint": "faintest text",
    "--accent": "accent colour (send button, switches, progress, highlights)", "--accent-ink": "text on the accent colour",
    "--line": "borders", "--line-2": "subtle dividers",
    "--glass": "fill of content plates (cards, panels, canvas blocks), e.g. rgba(255,255,255,.045)",
    "--glass-2": "hover fill of plates", "--glass-edge": "border of glass surfaces",
    "--glass-blur": "backdrop filter of glass, e.g. blur(22px) saturate(165%) or none",
    "--composer-bg": "the prompt box fill, e.g. rgba(20,20,24,.58)", "--menu-bg": "menus, taskbar panel and toasts fill",
    "--sheet-bg": "dialogs fill", "--slip-bg": "the user's message bubble fill",
    "--font-ui": "UI and reading font stack", "--font-display": "headline font stack", "--font-mono": "code font stack",
    "--fs": "base font size, e.g. 14px (default 15px)", "--chat-w": "width of the chat column, e.g. 680px (default 760px)",
    "--round": "corner roundness factor: 0 square, .5 crisp, 1 default, 1.4 very round",
    "--ok": "success colour", "--warn": "warning colour", "--danger": "error colour", "--info": "info colour",
    "--sc-hi": "Scorpi's body highlight colour", "--sc-mid": "Scorpi's body colour", "--sc-lo": "Scorpi's body shade",
    "--sc-sting": "Scorpi's stinger colour", "--sc-cheek": "Scorpi's blush colour",
    "--sc-cloud-hi": "the top of Scorpi's cloud", "--sc-cloud-lo": "the underside of Scorpi's cloud",
}

SELECTORS = (
    "body; .backdrop (the light / video behind everything); .taskbar (desktop bar at the bottom) with .tk-tab tabs; "
    ".composer (prompt box); .c-chip (its chips); .send (send button); .msg.user .slip (user bubble); .msg.assistant .md "
    "(answer text); .card; .panel; .tile; .sheet (dialogs); .pop and .slash (menus); .welcome-title (the greeting); "
    ".canvas-pane and .cv-block (the visual canvas); .page-title; .btn, .chip, .switch, .seg (controls); .mascot (Scorpi)"
)

GUIDE = ("Design tokens a style can set (CSS custom properties): " + "; ".join("%s = %s" % kv for kv in TOKENS.items())
         + ". Surfaces, hover fills and borders are derived from --ink, so recolouring --ink and --bg recolours everything. "
         "Selectors for extra CSS: " + SELECTORS + ". The app is dark glass by default; a light style must set --bg, the "
         "ink colours, light glass fills (--glass, --composer-bg, --menu-bg, --sheet-bg, --slip-bg) and --accent-ink. "
         "Fonts from Google Fonts can be loaded with fonts.")

PLUGIN_API = (
    "The JavaScript is the body of an async function called with `scorp` (await works); everything added through it is "
    "removed when the plugin is turned off, and errors in its callbacks land in the plugin's console in the Studio. "
    "SURFACES: scorp.pane({title, icon, width, render(body, pane)}) a glass pane docked beside the chat, opened from a tray "
    "button (pane.open/close/toggle/setTitle); scorp.panel({title, icon, width, render(body, panel)}) a floating glass panel "
    "from the tray; scorp.band({render(el, band)}) a strip just above the prompt box (band.show/hide); scorp.status({icon, "
    "text, title, onClick}) a small live item in the prompt box bar (status.set(text)); scorp.tray({icon, title, text, "
    "onClick}) a taskbar button; scorp.messageAction({icon, label, run(text, msgEl)}) a button under every answer; "
    "scorp.command({name, description, icon, run(args)}) a /name command; scorp.key('ctrl+shift+k', fn) a shortcut; "
    "scorp.toast(text, {body, type}); scorp.say(text) Scorpi speaks in a bubble; scorp.sound('ding'|'pop'|'chime'|'tick'|'error'|url). "
    "HOOKS (middleware, like mods): scorp.hook(event, async (e, next) => next(e)): 'send' e={text} before a message is "
    "sent (next({...e, text}) rewrites it, return null to stop it); 'answer' e={text, el, conversation, live} when an answer "
    "is drawn (add things to e.el); 'tool' e={phase:'start'|'end', name, args, ok, result} while Scorp uses tools; "
    "'route' e={view, id}. scorp.on(event, fn) listens to server events ('agent_job', 'conversation', 'notification', "
    "'run_done', 'music'). "
    "STATE: scorp.signal(value) -> {get, set, update(fn), subscribe(fn)}; scorp.mount(el, () => nodes, ...signals) redraws "
    "el whenever a signal changes; scorp.options is the plugin's settings (the user edits them in the Studio, declared in "
    "`options`), scorp.onOptions(fn) runs when they change; scorp.shared.get/set(key, value) and scorp.shared.watch(key, fn) "
    "keep data on the server, synced live across devices; scorp.storage.get/set keep data on this device only. "
    "AI: await scorp.llm(prompt, {system, json}) asks the model directly (no chat message; json:true returns parsed JSON); "
    "scorp.ask(text) sends a message in the chat. "
    "HELPERS: scorp.css(text); scorp.el(tag, props, ...children) builds elements ('div.class#id', props like "
    "onclick/style/dataset); scorp.icon(name) (only these exist: chat voice mic speaker library calendar routine memory "
    "bell plus check x copy retry pin trash download upload folder file image search more edit globe code chart map "
    "compass book doc spark sun moon play pause next prev shield cpu link clock bolt wand eye lock monitor phone terminal "
    "table hand agent cube refresh home palette puzzle sliders user keyboard radio star canvas); scorp.ui.confirm(title, "
    "text), scorp.ui.prompt(title, {label, value}), scorp.ui.switch(on, fn), scorp.ui.seg([[value, label]], value, fn); "
    "scorp.every(ms, fn); scorp.after(ms, fn); scorp.navigate(path); scorp.api.get/post(path, body) call Scorp's API; "
    "scorp.state() returns {view, conversation, project, online, model}; scorp.log(...) writes to the plugin's console. "
    "Classes that look native: btn, btn primary, btn quiet, small, chip, badge, input, muted, row, card, panel. "
    "The body may return a cleanup function."
)

OPTION_TYPES = ("toggle", "text", "textarea", "number", "range", "select", "color")


def _load():
    try:
        with open(PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data.setdefault("active", "")
    data.setdefault("styles", {})
    data.setdefault("plugins", {})
    return data


def _save(data):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, PATH)
    events.publish("looks", public(data))


def public(data=None):
    data = data or _load()
    return {"active": data["active"], "styles": sorted(data["styles"].values(), key=lambda s: -s.get("updated", 0)),
            "plugins": sorted(({**p, "settings": option_values(p)} for p in data["plugins"].values()), key=lambda p: -p.get("updated", 0))}


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40] or "look"


def _clean_css(css):
    css = str(css or "")
    if len(css) > 30000:
        raise ValueError("Keep the CSS under 30,000 characters.")
    return re.sub(r"</\s*style", "", css, flags=re.I)


def _clean_vars(raw):
    out = {}
    for k, v in (raw or {}).items():
        key = "--" + str(k).strip().lstrip("-")
        if not re.match(r"^--[a-zA-Z0-9_-]{1,40}$", key):
            raise ValueError("Bad token name %r." % k)
        value = str(v).strip().rstrip(";")
        if not value or re.search(r"[;{}<>]", value) or len(value) > 300:
            raise ValueError("Bad value for %s: %r." % (key, v))
        out[key] = value
    return out


def _clean_fonts(fonts):
    out = []
    for f in fonts or []:
        name = " ".join(str(f).split())
        if not re.match(r"^[A-Za-z0-9 ]{2,40}$", name):
            raise ValueError("Font %r: give a Google Fonts family name like 'Space Grotesk'." % f)
        out.append(name)
    return out[:4]


def find(kind, ident):
    data = _load()
    items = data["styles" if kind == "style" else "plugins"]
    ident = str(ident or "").strip()
    if ident in items:
        return items[ident]
    for item in items.values():
        if item["name"].lower() == ident.lower() or item["id"] == _slug(ident):
            return item
    return None


def save_style(name, vars=None, css="", fonts=None, description="", activate=True, base="", sid=None, replace=False):
    """Create or update a style (by name, or by sid to rename it). base: an existing style to start from (its tokens and
    CSS are kept unless replaced). replace: the given tokens are the whole set (the Studio editor), not a patch."""
    with _lock:
        data = _load()
        if sid and sid not in data["styles"]:
            sid = None
        sid = sid or _slug(name)
        start = dict(data["styles"].get(sid) or (find("style", base) if base else None) or {})
        tokens = {} if replace else dict(start.get("vars") or {})
        tokens.update(_clean_vars(vars))
        now = time.time()
        style = {"id": sid, "name": " ".join(str(name).split())[:60], "description": str(description or start.get("description") or "")[:300],
                 "vars": tokens, "css": _clean_css(css) if (css or replace) else start.get("css", ""),
                 "fonts": _clean_fonts(fonts) if (fonts or replace) else start.get("fonts", []),
                 "created": (data["styles"].get(sid) or {}).get("created", now), "updated": now}
        data["styles"][sid] = style
        if activate:
            data["active"] = sid
        _save(data)
        return style


def _clean_options(raw):
    """A plugin's settings, drawn as a form in the Studio: [{key, label, type, default, help, choices, min, max, step}]."""
    out = []
    for o in raw or []:
        if not isinstance(o, dict):
            raise ValueError("Each option is an object like {\"key\": \"city\", \"type\": \"text\"}.")
        key = str(o.get("key") or "").strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,30}$", key):
            raise ValueError("Option key %r: letters, digits and _ only." % key)
        kind = str(o.get("type") or "text")
        if kind not in OPTION_TYPES:
            raise ValueError("Option %s: type must be one of %s." % (key, ", ".join(OPTION_TYPES)))
        opt = {"key": key, "type": kind, "label": str(o.get("label") or key)[:60]}
        if o.get("help"):
            opt["help"] = str(o["help"])[:200]
        if kind == "select":
            choices = [c if isinstance(c, list) else [c.get("value"), c.get("label") or c.get("value")] if isinstance(c, dict) else [c, c]
                       for c in (o.get("choices") or [])]
            if not choices:
                raise ValueError("Option %s needs choices." % key)
            opt["choices"] = [[str(v)[:60], str(l)[:60]] for v, l in (c[:2] for c in choices)][:30]
        for k in ("min", "max", "step"):
            if isinstance(o.get(k), (int, float)):
                opt[k] = o[k]
        default = o.get("default")
        opt["default"] = _option_value(opt, default if default is not None else {"toggle": False, "number": 0, "range": opt.get("min", 0), "color": "#ffffff"}.get(kind, ""))
        out.append(opt)
    if len({o["key"] for o in out}) != len(out):
        raise ValueError("Option keys must be different.")
    return out[:24]


def _option_value(opt, value):
    kind = opt["type"]
    if kind == "toggle":
        return bool(value)
    if kind in ("number", "range"):
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = float(opt.get("min", 0))
        v = max(opt.get("min", v), min(opt.get("max", v), v))
        return int(v) if v.is_integer() else v
    if kind == "select":
        keys = [c[0] for c in opt["choices"]]
        return str(value) if str(value) in keys else keys[0]
    if kind == "color":
        v = str(value or "").strip()
        return v if re.match(r"^#[0-9a-fA-F]{3,8}$", v) else "#ffffff"
    return str(value if value is not None else "")[:4000]


def option_values(plugin):
    """The plugin's settings: what the user set, else the defaults."""
    stored = plugin.get("values") or {}
    return {o["key"]: _option_value(o, stored[o["key"]]) if o["key"] in stored else o["default"] for o in plugin.get("options") or []}


def save_plugin(name, css="", js="", description="", enabled=True, pid=None, prompt=None, options=None, icon=None):
    """prompt/options/icon left as None keep what the plugin had."""
    if not css and not js and not prompt:
        raise ValueError("A plugin needs CSS, JavaScript or a prompt.")
    if len(js or "") > 60000:
        raise ValueError("Keep the JavaScript under 60,000 characters.")
    if prompt is not None and len(prompt) > 4000:
        raise ValueError("Keep the prompt under 4,000 characters.")
    if isinstance(options, str):
        try:
            options = json.loads(options or "[]")
        except ValueError:
            raise ValueError("The options are not valid JSON.")
    with _lock:
        data = _load()
        if pid and pid not in data["plugins"]:
            pid = None
        pid = pid or _slug(name)
        old = data["plugins"].get(pid) or {}
        now = time.time()
        plugin = {"id": pid, "name": " ".join(str(name).split())[:60], "description": str(description or "")[:300],
                  "icon": re.sub(r"[^a-z0-9-]", "", str(icon if icon is not None else old.get("icon") or "puzzle"))[:24] or "puzzle",
                  "css": _clean_css(css), "js": str(js or ""),
                  "prompt": str(prompt if prompt is not None else old.get("prompt") or "").strip(),
                  "options": _clean_options(options) if options is not None else old.get("options") or [],
                  "values": old.get("values") or {}, "enabled": bool(enabled),
                  "created": old.get("created", now), "updated": now}
        data["plugins"][pid] = plugin
        _save(data)
        _errors.pop(pid, None)  # a new version starts with a clean slate
        return plugin


def set_values(plugin_id, values):
    """Change a plugin's settings. The plugin keeps running; it hears about it through scorp.onOptions."""
    with _lock:
        data = _load()
        plugin = data["plugins"].get(plugin_id)
        if not plugin:
            raise ValueError("No plugin %r." % plugin_id)
        opts = {o["key"]: o for o in plugin.get("options") or []}
        stored = dict(plugin.get("values") or {})
        for k, v in (values or {}).items():
            if k in opts:
                stored[k] = _option_value(opts[k], v)
        plugin["values"] = stored
        _save(data)
        return option_values(plugin)


def prompt_sections():
    """What enabled plugins add to Scorp's instructions ({{key}} is filled from the plugin's settings)."""
    out = []
    for p in _load()["plugins"].values():
        if p.get("enabled") and p.get("prompt"):
            vals = option_values(p)
            text = re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(vals.get(m.group(1), m.group(0))), p["prompt"])
            out.append("PLUGIN — %s\n%s" % (p["name"], text))
    return out


# ---------- errors devices report, so Scorp can fix the plugins it wrote ----------

_errors = {}  # plugin id -> recent [{time, text}]


def report_error(plugin_id, text):
    if plugin_id in _load()["plugins"]:
        recent = _errors.setdefault(plugin_id, [])
        recent.append({"time": time.time(), "text": str(text)[:600]})
        del recent[:-8]


def errors(plugin_id):
    return _errors.get(plugin_id, [])


# ---------- data plugins keep on the server (scorp.shared), synced to every device ----------

DATA_PATH = os.path.join(DATA_DIR, "plugin_data.json")
_data_lock = threading.Lock()


def _load_data():
    try:
        with open(DATA_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def plugin_data(plugin_id=None):
    data = _load_data()
    return data.get(plugin_id, {}) if plugin_id else data


def set_data(plugin_id, key, value):
    key = str(key or "")
    if not re.match(r"^[\w.:-]{1,80}$", key):
        raise ValueError("Bad key %r." % key)
    with _lock:
        if plugin_id not in _load()["plugins"]:
            raise ValueError("No plugin %r." % plugin_id)
    with _data_lock:
        data = _load_data()
        mine = data.setdefault(plugin_id, {})
        if value is None:
            mine.pop(key, None)
        else:
            mine[key] = value
        if len(json.dumps(mine)) > 512 * 1024:
            raise ValueError("This plugin keeps more than 512 KB; store less.")
        if not mine:
            data.pop(plugin_id, None)
        tmp = DATA_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, DATA_PATH)
    events.publish("plugin_data", {"id": plugin_id, "key": key, "value": value})


def _drop_data(plugin_id):
    with _data_lock:
        data = _load_data()
        if data.pop(plugin_id, None) is not None:
            with open(DATA_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh)


def set_active(style_id):
    with _lock:
        data = _load()
        if style_id and style_id not in data["styles"]:
            raise ValueError("No style %r." % style_id)
        data["active"] = style_id or ""
        _save(data)


def set_plugin(plugin_id, enabled):
    with _lock:
        data = _load()
        if plugin_id not in data["plugins"]:
            raise ValueError("No plugin %r." % plugin_id)
        data["plugins"][plugin_id]["enabled"] = bool(enabled)
        _save(data)


def delete(kind, ident):
    with _lock:
        data = _load()
        items = data["styles" if kind == "style" else "plugins"]
        if ident not in items:
            raise ValueError("No %s %r." % (kind, ident))
        items.pop(ident)
        if kind == "style" and data["active"] == ident:
            data["active"] = ""
        _save(data)
    if kind == "plugin":
        _drop_data(ident)


def export(kind, ident):
    """A shareable file for one style or plugin."""
    item = find(kind, ident)
    if not item:
        raise ValueError("No %s %r." % (kind, ident))
    keep = ("name", "description", "vars", "css", "fonts") if kind == "style" else ("name", "description", "icon", "css", "js", "prompt", "options")
    return {"scorp": "look", "version": 2, "kind": kind, **{k: item.get(k) for k in keep if item.get(k) not in (None, "", [], {})}}


def import_item(payload):
    """Add a style or plugin from a file made by export(). Imported plugins with code start turned off."""
    if not isinstance(payload, dict) or payload.get("kind") not in ("style", "plugin"):
        raise ValueError("That is not a Scorp style or plugin file.")
    name = str(payload.get("name") or "Imported")[:60]
    if find(payload["kind"], name):
        name = name[:48] + " (imported)"
    if payload["kind"] == "style":
        return "style", save_style(name, payload.get("vars") or {}, payload.get("css") or "", payload.get("fonts") or [],
                                   payload.get("description") or "", activate=False, replace=True)
    return "plugin", save_plugin(name, payload.get("css") or "", payload.get("js") or "", payload.get("description") or "",
                                 enabled=not payload.get("js") and not payload.get("prompt"), prompt=payload.get("prompt") or "",
                                 options=payload.get("options") or [], icon=payload.get("icon") or "puzzle")
