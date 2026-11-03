"""The canvas: a live visual board beside the chat that the assistant builds and keeps changing as a conversation goes on."""

import copy
import json
import re

from .. import db
from . import Result, ToolError, tool

BLOCK_TYPES = {
    "hero", "stat", "stats", "text", "list", "steps", "timeline", "table", "compare", "chart", "progress", "facts",
    "image", "gallery", "code", "quote", "diagram", "links", "cards", "map",
}
MAX_BLOCKS = 24

BLOCK_GUIDE = (
    "Every block: id (short and stable, e.g. 'weather'; reuse it to update the block), type, optional title, "
    "size (s | m | l | full; default m) and tone (accent | good | warn | bad). Types and their fields: "
    "hero {kicker?, title, text?, value?} the headline of the topic; "
    "stat {value, label, unit?, delta?, trend?: [numbers]}; "
    "stats {items: [{value, label, unit?, delta?}]}; "
    "text {text: a few lines of Markdown}; "
    "list {items: [string | {title, sub?, done?}], ordered?}; "
    "steps {items: [{title, detail?, status: done|active|todo}]}; "
    "timeline {items: [{time, title, detail?}]}; "
    "table {columns: [..], rows: [[..]]}; "
    "compare {items: [{name, tag?, points: [..], score?: 0-10, best?: true}]}; "
    "chart {kind: bar|line|area|pie|donut, labels: [..], series: [{name, values: [numbers]}], unit?}; "
    "progress {items: [{label, value, max?}]}; "
    "facts {items: [{label, value}]}; "
    "image {src: URL or local path, caption?}; "
    "gallery {items: [{src, caption?}]}; "
    "code {language, code}; "
    "quote {text, by?}; "
    "diagram {mermaid: Mermaid source}; "
    "links {items: [{title, url, sub?}]}; "
    "cards {items: [{title, sub?, image?, tag?, url?}]}; "
    "map {markers: [{lat, lon, label?}]}."
)


def _empty():
    return {"title": "", "blocks": []}


def stored(conv_id):
    """The canvas as the last finished turn of a conversation left it."""
    if not conv_id:
        return _empty()
    rows = db.q("SELECT data FROM messages WHERE conv_id=? AND role='assistant' ORDER BY created DESC LIMIT 60", (conv_id,))
    for row in rows:
        try:
            parts = (json.loads(row["data"] or "{}") or {}).get("parts") or []
        except ValueError:
            continue
        for part in reversed(parts):
            display = part.get("display") or {}
            if display.get("type") == "canvas":
                return {"title": display.get("title", ""), "blocks": display.get("blocks") or []}
    return _empty()


def current(ctx):
    """This turn's latest canvas (not saved yet), else the stored one."""
    for part in reversed(getattr(ctx.agent, "parts", None) or []):
        display = part.get("display") or {}
        if display.get("type") == "canvas":
            return {"title": display.get("title", ""), "blocks": copy.deepcopy(display.get("blocks") or [])}
    return stored(ctx.conv_id)


def summary(conv_id, limit=1800):
    """A compact description of the canvas for the system prompt, so the model can keep building on it."""
    state = stored(conv_id)
    if not state["blocks"]:
        return ""
    lines = []
    for b in state["blocks"]:
        body = {k: v for k, v in b.items() if k not in ("id", "type", "title", "size", "tone")}
        lines.append("- %s (%s)%s: %s" % (b.get("id"), b.get("type"), ' "%s"' % b["title"] if b.get("title") else "",
                                          json.dumps(body, ensure_ascii=False)[:220]))
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit] + "\n…"
    return "The canvas currently shows%s:\n%s" % (' "%s"' % state["title"] if state["title"] else "", text)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:32]


@tool(
    "canvas",
    "Build and update the live canvas: a visual board beside the chat that stays for the whole conversation. Put the "
    "substance of answers on it as visual blocks (numbers, steps, comparisons, charts, timelines, tables, pictures, "
    "maps) and keep changing it as the conversation moves on: add blocks, update a block by sending its id again "
    "(only the fields you send change), remove what no longer matters, or clear it for a new topic. " + BLOCK_GUIDE,
    {
        "blocks?": {"type": "array", "items": {"type": "object"}, "description": "Blocks to add or update, in display order"},
        "remove?": "array<string>: Ids of blocks to remove",
        "clear?": "boolean: Start a fresh canvas (for a new topic) before adding the blocks",
        "title?": "string: The canvas title: the current topic in a few words",
        "focus?": "string: Id of the block to bring into view",
    },
)
def canvas(ctx, blocks=None, remove=None, clear=False, title=None, focus=""):
    state = _empty() if clear else current(ctx)
    if title is not None:
        state["title"] = str(title).strip()[:80]
    by_id = {b.get("id"): b for b in state["blocks"]}
    removed = [str(r) for r in (remove or []) if str(r) in by_id]
    if removed:
        state["blocks"] = [b for b in state["blocks"] if b.get("id") not in removed]
        by_id = {b.get("id"): b for b in state["blocks"]}
    changed = []
    if isinstance(blocks, dict):
        blocks = [blocks]
    for i, raw in enumerate(blocks or []):
        if not isinstance(raw, dict):
            continue
        block = dict(raw)
        bid = _slug(block.get("id") or block.get("title") or "") or "%s-%d" % (block.get("type") or "block", len(state["blocks"]) + i + 1)
        block["id"] = bid
        if bid in by_id:
            by_id[bid].update(block)
        else:
            if block.get("type") not in BLOCK_TYPES:
                raise ToolError("Unknown block type %r for %r. Use one of: %s." % (block.get("type"), bid, ", ".join(sorted(BLOCK_TYPES))))
            state["blocks"].append(block)
            by_id[bid] = block
        changed.append(bid)
    if len(state["blocks"]) > MAX_BLOCKS:
        raise ToolError("The canvas holds at most %d blocks; remove some or clear it." % MAX_BLOCKS)
    if not (changed or removed or clear or title is not None):
        raise ToolError("Nothing to change: send blocks, remove, clear or title.")
    ids = ", ".join(b["id"] for b in state["blocks"]) or "empty"
    text = "Canvas updated (%d blocks: %s). The user sees it now; do not repeat its contents in your reply." % (len(state["blocks"]), ids)
    return Result(text, display={"type": "canvas", "title": state["title"], "blocks": state["blocks"], "changed": changed,
                                 "removed": removed, "cleared": bool(clear), "focus": _slug(focus) if focus else ""})
