"""The community gallery: styles, plugins, skills and agents people share on the Scorpi website.

The website keeps them (functions/api/gallery.js); this module talks to it for the app, so the browser never leaves
the PC's origin. What is installed from the gallery starts safe: plugins with code or a prompt start off (as any
imported plugin), agents start paused and never with automatic approval, skills are just instructions.
Sharing sends one item's file and a name; the site answers with a delete key that stays on this PC (gallery_mine).
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import VERSION, agent, agents, looks, updates
from .config import SETTINGS
from .tools import GROUPS

KINDS = ("style", "plugin", "skill", "agent")


def base():
    url = (SETTINGS["gallery_url"] or updates.site()).strip()
    return url.rstrip("/") + "/api/gallery"


def _call(method="GET", query=None, body=None, headers=None, timeout=20):
    url = base() + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": "Scorpi/%s (gallery)" % VERSION, "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode("utf-8")).get("error")
        except Exception:  # noqa: BLE001
            message = ""
        raise ValueError(message or "The gallery answered with HTTP %d." % exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError("The gallery is not reachable (%s)." % getattr(exc, "reason", exc))


def browse(kind="", q="", sort="popular", offset=0):
    query = {"sort": sort if sort in ("popular", "new") else "popular", "offset": max(0, int(offset or 0))}
    if kind in KINDS:
        query["kind"] = kind
    if q:
        query["q"] = str(q)[:80]
    return _call(query=query)


def item(gid):
    return _call(query={"id": gid})


# ---------- sharing ----------

def package(kind, ident):
    """The shareable file for one of this Scorpi's items (nothing personal: no models, keys or approvals)."""
    if kind in ("style", "plugin"):
        return looks.export(kind, ident)
    if kind == "skill":
        skill = next((s for s in SETTINGS["skills"] or [] if s.get("id") == ident), None)
        if not skill:
            raise ValueError("No skill %r of your own." % ident)
        return {"scorpi": "skill", "version": 1, "kind": "skill", **{k: skill.get(k) for k in ("name", "description", "instructions", "groups", "icon")}}
    if kind == "agent":
        row = agents.get(ident)
        if not row:
            raise ValueError("No agent %r." % ident)
        a = agents.public(row)
        keep = ("name", "icon", "description", "instructions", "groups", "skill", "max_steps", "schedule", "action", "task")
        return {"scorpi": "agent", "version": 1, "kind": "agent", **{k: a.get(k) for k in keep if a.get(k) not in (None, "", [])}}
    raise ValueError("The gallery takes styles, plugins, skills and agents.")


def shareable():
    """Everything of the user's own that can go to the gallery, for the share sheet."""
    data = looks.public()
    out = [{"kind": "style", "id": s["id"], "name": s["name"], "description": s.get("description", "")} for s in data["styles"]]
    out += [{"kind": "plugin", "id": p["id"], "name": p["name"], "description": p.get("description", "")} for p in data["plugins"]]
    out += [{"kind": "skill", "id": s["id"], "name": s["name"], "description": s.get("description", "")} for s in SETTINGS["skills"] or []]
    out += [{"kind": "agent", "id": a["id"], "name": a["name"], "description": a.get("description", "")}
            for a in agents.all_agents() if a.get("kind") != "builtin"]
    return out


def publish(kind, ident, name="", description="", author=""):
    payload = package(kind, ident)
    name = " ".join(str(name or payload.get("name") or "").split())[:60]
    if len(name) < 3:
        raise ValueError("Give it a name of at least three letters.")
    answer = _call("POST", body={"kind": kind, "name": name, "description": str(description or payload.get("description") or "")[:400],
                                 "author": " ".join(str(author or "").split())[:40], "payload": payload, "version": VERSION})
    mine = [m for m in SETTINGS["gallery_mine"] or [] if m.get("id") != answer.get("id")]
    mine.insert(0, {"id": answer["id"], "kind": kind, "name": name, "local": ident, "key": answer.get("delete_key", ""), "at": time.time()})
    SETTINGS.update({"gallery_mine": mine[:200]})
    return {"id": answer["id"], "name": name, "kind": kind}


def mine():
    return [{k: m.get(k) for k in ("id", "kind", "name", "local", "at")} for m in SETTINGS["gallery_mine"] or []]


def unpublish(gid):
    entry = next((m for m in SETTINGS["gallery_mine"] or [] if m.get("id") == gid), None)
    if not entry:
        raise ValueError("Only what you shared from this Scorpi can be taken down here.")
    try:
        _call("DELETE", query={"id": gid}, headers={"X-Delete-Key": entry.get("key", "")})
    except ValueError as exc:
        if "not found" not in str(exc).lower():
            raise
    SETTINGS.update({"gallery_mine": [m for m in SETTINGS["gallery_mine"] or [] if m.get("id") != gid]})


def report(gid, reason=""):
    return _call("POST", query={"id": gid, "action": "report"}, body={"reason": str(reason or "")[:300]})


# ---------- installing ----------

def _free_name(name, taken):
    name = str(name or "Shared")[:60]
    if name.casefold() not in taken:
        return name
    return name[:48] + " (gallery)"


def install(gid):
    """Add a gallery item to this Scorpi. Returns (kind, the new item)."""
    entry = item(gid)
    kind, payload = entry.get("kind"), entry.get("payload") or {}
    if kind not in KINDS or not isinstance(payload, dict):
        raise ValueError("That gallery item cannot be installed.")
    payload = {**payload, "kind": kind, "name": entry.get("name") or payload.get("name")}
    if kind in ("style", "plugin"):
        kind, new = looks.import_item(payload)
    elif kind == "skill":
        skills = list(SETTINGS["skills"] or [])
        instructions = str(payload.get("instructions") or "").strip()
        if not instructions:
            raise ValueError("That skill has no instructions.")
        name = _free_name(payload.get("name"), {s["name"].casefold() for s in skills} | {s["name"].casefold() for s in agent.SKILLS.values()})
        sid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "skill"
        taken = {s["id"] for s in skills} | set(agent.SKILLS)
        while sid in taken:
            sid += "-2"
        new = {"id": sid, "name": name[:40], "description": str(payload.get("description") or "")[:200], "instructions": instructions[:20000],
               "groups": [g for g in payload.get("groups") or [] if g in GROUPS], "icon": str(payload.get("icon") or "spark")[:24]}
        SETTINGS.update({"skills": skills + [new]})
    else:
        sched = payload.get("schedule") if isinstance(payload.get("schedule"), dict) else None
        if sched and sched.get("type") == "once":
            sched = None  # a one-off date from someone else's calendar means nothing here
        name = _free_name(payload.get("name"), {a["name"].casefold() for a in agents.all_agents()})
        new = agents.save({"name": name, "icon": payload.get("icon") or "agent", "description": payload.get("description") or "",
                           "instructions": payload.get("instructions") or "", "groups": payload.get("groups") or [],
                           "skill": payload.get("skill") or "workflow", "max_steps": payload.get("max_steps") or 40,
                           "schedule": sched, "action": payload.get("action") or "run", "task": payload.get("task") or "",
                           "auto_approve": 0, "enabled": 0})  # paused until the user has read it and switches it on
    try:
        _call("POST", query={"id": gid, "action": "install"}, body={}, timeout=8)
    except ValueError:
        pass  # the count on the site is not worth failing the install for
    return kind, new
