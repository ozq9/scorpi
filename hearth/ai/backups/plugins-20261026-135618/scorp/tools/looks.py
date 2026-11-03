"""Tools to change Scorp's own look from chat: styles from a vibe, small interface plugins, and switching between them."""

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
    "Add a plugin (a mod) to Scorp's own interface: CSS and/or JavaScript that runs in the app page on every device (e.g. "
    "'a clock in the taskbar', 'a pomodoro timer panel', 'a /dice command', 'hide the greeting', 'a word counter on the "
    "prompt box'). " + looks.PLUGIN_API + " Keep it small and self-contained, never send data to other sites. Same name "
    "updates the plugin.",
    {
        "name": "string: Short plugin name",
        "description": "string: One line: what it does",
        "css?": "string: CSS rules",
        "js?": "string: JavaScript (function body, see above)",
    },
    group="looks",
    risk="write",
)
def app_plugin(ctx, name, description, css="", js=""):
    try:
        plugin = looks.save_plugin(name, css, js, description)
    except ValueError as exc:
        raise ToolError(str(exc))
    return Result("Plugin '%s' is on, on every device. The user can turn it off in Settings → Styles & plugins." % plugin["name"],
                  display={"type": "look", "kind": "plugin", "id": plugin["id"], "name": plugin["name"]})


@tool(
    "manage_looks",
    "List Scorp's interface styles and plugins, switch to a style, go back to the default look, or turn plugins on/off and delete them.",
    {
        "action": "string[list,use_style,default_look,delete_style,plugin_on,plugin_off,delete_plugin]: What to do",
        "target?": "string: Style or plugin name/id",
    },
    group="looks",
)
def manage_looks(ctx, action, target=""):
    data = looks.public()
    if action == "list":
        lines = ["Active style: %s" % (next((s["name"] for s in data["styles"] if s["id"] == data["active"]), "default (black glass)"))]
        lines += ["style %s (%s): %s" % (s["id"], s["name"], s["description"]) for s in data["styles"]]
        lines += ["plugin %s (%s, %s): %s" % (p["id"], p["name"], "on" if p["enabled"] else "off", p["description"]) for p in data["plugins"]]
        return Result("\n".join(lines))
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
