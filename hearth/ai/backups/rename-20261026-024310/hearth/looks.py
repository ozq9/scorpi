"""Looks: styles (design tokens + CSS) and small plugins (CSS and/or JavaScript) for Scorp's own interface.
The chat assistant makes them from a vibe ("a clean black and white style with small font"); every device applies the
active style and the enabled plugins live. Stored in data/looks.json."""

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
    "The JavaScript is the body of a function called with `scorp`; everything added through it is removed when the plugin "
    "is turned off. scorp.css(text) adds CSS; scorp.el(tag, props, ...children) builds elements ('div.class#id', props "
    "like onclick/style/dataset); scorp.icon(name) gives an icon (chat, code, spark, clock, bell, globe, bolt, puzzle, "
    "palette, calendar, music is not available: use speaker); scorp.tray({icon, title, text, onClick}) adds a button to the "
    "taskbar tray; scorp.panel({title, icon, width, render(body, panel)}) makes a floating glass panel toggled from the "
    "tray (panel.open/close/toggle); scorp.command({name, description, run(args)}) adds a /name command to the prompt box; "
    "scorp.every(ms, fn) runs a timer; scorp.on(event, fn) listens to server events ('agent_job', 'conversation', "
    "'notification', 'run_done'); scorp.onRoute(fn) runs when the page changes; scorp.ask(text) sends a message to Scorp; "
    "scorp.say(text) makes Scorpi say something in a bubble; scorp.toast(text, {body}); scorp.navigate(path); "
    "scorp.api.get/post(path, body) call Scorp's own API; scorp.storage.get/set(key, value) keep small data on this "
    "device; scorp.state() returns {view, conversation, project, online, model}. The body may return a cleanup function."
)


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
            "plugins": sorted(data["plugins"].values(), key=lambda p: -p.get("updated", 0))}


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


def save_plugin(name, css="", js="", description="", enabled=True, pid=None):
    if not css and not js:
        raise ValueError("A plugin needs CSS or JavaScript.")
    if len(js or "") > 30000:
        raise ValueError("Keep the JavaScript under 30,000 characters.")
    with _lock:
        data = _load()
        if pid and pid not in data["plugins"]:
            pid = None
        pid = pid or _slug(name)
        now = time.time()
        plugin = {"id": pid, "name": " ".join(str(name).split())[:60], "description": str(description or "")[:300],
                  "css": _clean_css(css), "js": str(js or ""), "enabled": bool(enabled),
                  "created": (data["plugins"].get(pid) or {}).get("created", now), "updated": now}
        data["plugins"][pid] = plugin
        _save(data)
        return plugin


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


def export(kind, ident):
    """A shareable file for one style or plugin."""
    item = find(kind, ident)
    if not item:
        raise ValueError("No %s %r." % (kind, ident))
    keep = ("name", "description", "vars", "css", "fonts") if kind == "style" else ("name", "description", "css", "js")
    return {"scorp": "look", "version": 1, "kind": kind, **{k: item.get(k) for k in keep if item.get(k) not in (None, "", [], {})}}


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
                                 enabled=not payload.get("js"))
