"""Tools to change Scorp's own look from chat: styles from a vibe, small interface plugins, and switching between them."""

import json

from .. import looks
from . import Result, ToolError, tool


@tool(
    "style_app",
    "Restyle Scorp's own interface from a vibe the user describes (e.g. 'a clean black and white style with small font', "
    "'cozy warm paper look', 'neon cyberpunk', 'bigger text and square corners'). Saves it as a named style and applies it "
    "live on every device; the user can switch styles in Settings or by asking. Prefer design tokens; add CSS only for what "
    "tokens can't do. To tweak the current style, use the same name (or base) and give only what changes. " + looks.GUIDE,
    {
        "name": "string: Short style name, e.g. 'Mono paper'",
        "vars": {"type": "object", "description": "Design tokens -> CSS values, e.g. {\"--fs\": \"13px\", \"--accent\": \"#fff\"}",
                 "additionalProperties": {"type": "string"}},
        "css?": "string: Extra CSS rules for things tokens can't express",
        "fonts?": "array<string>: Google Fonts families to load, e.g. ['Space Grotesk'] (then use them in --font-ui)",
        "description?": "string: One line about the style",
        "base?": "string: An existing style to start from (name or id)",
    },
    group="looks",
)
def style_app(ctx, name, vars=None, css="", fonts=None, description="", base=""):
    try:
        style = looks.save_style(name, vars or {}, css, fonts, description, base=base)
    except ValueError as exc:
        raise ToolError(str(exc))
    return Result("Style '%s' is applied on every device (%d tokens%s). The user can switch back in Settings → Styles, or ask you."
                  % (style["name"], len(style["vars"]), ", custom CSS" if style["css"] else ""),
                  display={"type": "look", "kind": "style", "id": style["id"], "name": style["name"], "vars": style["vars"]})


@tool(
    "app_plugin",
    "Add a plugin (a mod) to Scorp itself: JavaScript and CSS that run in the app on every device, an addition to your own "
    "instructions while it is on, and settings the user can change (e.g. 'a notes pane beside the chat', 'a pomodoro timer', "
    "'a /dice command', 'translate any answer with one click', 'a live word counter on the prompt box', 'always answer like "
    "a pirate', 'a habit tracker synced across my devices'). Make it feel native and polished: use the app's classes and "
    "design tokens (var(--ink), var(--muted), var(--accent), var(--glass), var(--line), var(--r-4), var(--font-display)), "
    "real surfaces (pane, band, status, messageAction) instead of floating boxes, and options for anything a user might "
    "want to tweak. " + looks.PLUGIN_API + " Keep it self-contained; never send data to other sites. Same name updates "
    "the plugin.",
    {
        "name": "string: Short plugin name",
        "description": "string: One line: what it does",
        "icon?": "string: Icon name for the plugin (spark, clock, bolt, chat, globe, palette, calendar, speaker, puzzle…)",
        "css?": "string: CSS rules",
        "js?": "string: JavaScript (async function body, see above)",
        "prompt?": "string: Text added to your own instructions while the plugin is on (behaviour, a persona, rules). "
                   "{{key}} is replaced with the value of the option with that key",
        "options?": {"type": "array", "description": "Settings the user can change in the Studio; the code reads them as "
                     "scorp.options.key. Each: {key, label, type: toggle|text|textarea|number|range|select|color, default, "
                     "help?, choices? (select: [[value, label]]), min?, max?, step?}", "items": {"type": "object"}},
    },
    group="looks",
    risk="write",
)
def app_plugin(ctx, name, description, css="", js="", prompt=None, options=None, icon=None):
    old = looks.find("plugin", name)
    try:
        plugin = looks.save_plugin(name, css, js, description, pid=old["id"] if old else None, prompt=prompt, options=options, icon=icon)
    except ValueError as exc:
        raise ToolError(str(exc))
    bits = [b for b in ("code" if plugin["js"] else "", "CSS" if plugin["css"] else "", "instructions" if plugin["prompt"] else "",
                        "%d setting(s)" % len(plugin["options"]) if plugin["options"] else "") if b]
    return Result("Plugin '%s' (%s) is on, on every device. If it fails, its errors show in Studio → Plugins → the plugin's "
                  "console. The user can change its settings or turn it off in the Studio." % (plugin["name"], ", ".join(bits)),
                  display={"type": "look", "kind": "plugin", "id": plugin["id"], "name": plugin["name"]})


@tool(
    "manage_looks",
    "List Scorp's interface styles and plugins, switch to a style, go back to the default look, or turn plugins on/off and delete them.",
    {
        "action": "string[list,use_style,default_look,delete_style,plugin_on,plugin_off,delete_plugin,plugin_code]: What to do "
                  "(plugin_code: a plugin's full code, settings and the errors devices reported, to fix or extend it)",
        "target?": "string: Style or plugin name/id",
    },
    group="looks",
)
def manage_looks(ctx, action, target=""):
    data = looks.public()
    if action == "list":
        lines = ["Active style: %s" % (next((s["name"] for s in data["styles"] if s["id"] == data["active"]), "default (black glass)"))]
        lines += ["style %s (%s): %s" % (s["id"], s["name"], s["description"]) for s in data["styles"]]
        lines += ["plugin %s (%s, %s%s): %s" % (p["id"], p["name"], "on" if p["enabled"] else "off",
                                                ", %d recent error(s)" % len(looks.errors(p["id"])) if looks.errors(p["id"]) else "",
                                                p["description"]) for p in data["plugins"]]
        return Result("\n".join(lines))
    if action == "plugin_code":
        item = looks.find("plugin", target)
        if not item:
            raise ToolError("No plugin %r." % target)
        errs = looks.errors(item["id"])
        return Result("\n\n".join(x for x in (
            "Plugin %s (%s), %s." % (item["name"], item["id"], "on" if item["enabled"] else "off"),
            "Options: " + json.dumps(item.get("options") or []) + "\nCurrent values: " + json.dumps(looks.option_values(item)) if item.get("options") else "",
            "Prompt:\n" + item["prompt"] if item.get("prompt") else "",
            "JavaScript:\n```js\n" + item["js"] + "\n```" if item.get("js") else "",
            "CSS:\n```css\n" + item["css"] + "\n```" if item.get("css") else "",
            "Errors reported by devices:\n" + "\n".join("- " + e["text"] for e in errs) if errs else "No errors reported.",
        ) if x))
    try:
        if action == "default_look":
            looks.set_active("")
            return Result("Back to the default black glass look.")
        kind = "style" if action in ("use_style", "delete_style") else "plugin"
        item = looks.find(kind, target)
        if not item:
            raise ToolError("No %s %r." % (kind, target))
        if action == "use_style":
            looks.set_active(item["id"])
        elif action in ("plugin_on", "plugin_off"):
            looks.set_plugin(item["id"], action == "plugin_on")
        else:
            looks.delete(kind, item["id"])
    except ValueError as exc:
        raise ToolError(str(exc))
    return Result("Done: %s %s." % (action.replace("_", " "), item["name"]))
