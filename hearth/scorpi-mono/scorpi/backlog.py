"""The queue: coding tasks lined up for later. Scorpi works through them one at a time, in the hours the user picks
(overnight by default, or right away), while the free models have requests left; each task runs as a normal job with a
checkpoint before and after it, so any one of them can be undone. Approvals still reach the user, on the phone too, and
in the morning a report says what was done, what was verified and what failed.

Upkeep jobs are ready-made tasks that join the queue on a schedule: update dependencies, a security review, fix failing
tests, tidy up.
"""

import json
import os
import secrets
import threading
import time
import traceback

from . import events, llm, notify
from .config import DATA_DIR, SETTINGS

STORE = os.path.join(DATA_DIR, "backlog.json")
_lock = threading.Lock()
_state = None

UPKEEP = {
    "deps": {"name": "Update dependencies", "icon": "refresh",
             "task": "Upkeep: update this project's dependencies to their latest compatible versions. Update a few at a time and run "
                     "the tests after each step; keep only updates that pass, and leave major versions with breaking changes for the "
                     "user (list them). Report what was updated."},
    "tests": {"name": "Keep the tests green", "icon": "check",
              "task": "Upkeep: run the project's tests. If any fail, find the cause in the code and fix it, then run them again until they "
                      "pass. If there are no tests, add a small useful set for the most important behaviour. Report what you found."},
    "security": {"name": "Security review", "icon": "shield",
                 "task": "Upkeep: look for security problems in this project: secrets in the code, injection (SQL, shell, HTML), unsafe "
                         "file handling, missing checks on user input, vulnerable dependency versions. Fix what is clearly safe to fix, "
                         "run the tests, and report the rest with file:line and a suggested fix."},
    "tidy": {"name": "Tidy up", "icon": "wand",
             "task": "Upkeep: find dead code, unused files and imports, and obvious duplication. Remove or simplify only what is clearly "
                     "safe, run the tests after the changes, and report what you cleaned up."},
    "docs": {"name": "Refresh the README", "icon": "doc",
             "task": "Upkeep: make README.md (and AGENTS.md if present) match the project as it is now: what it does, how to install, run "
                     "and test it. Check the commands you write down. Change no other files."},
}
WINDOWS = ("night", "always", "paused")


# ---------- state ----------

def _load():
    global _state
    if _state is None:
        try:
            with open(STORE, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        _state = {"items": data.get("items") or [], "upkeep": data.get("upkeep") or [], "reports": data.get("reports") or [],
                  "session": data.get("session") or None}
    return _state


def _save():
    os.makedirs(DATA_DIR, exist_ok=True)
    state = _load()
    state["items"] = [i for i in state["items"] if i["status"] in ("queued", "running")] + \
        [i for i in state["items"] if i["status"] not in ("queued", "running")][-80:]
    state["reports"] = state["reports"][-20:]
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, STORE)


def reset_state():
    global _state
    with _lock:
        _state = {"items": [], "upkeep": [], "reports": [], "session": None}
        _save()


def _publish():
    events.publish("backlog", {"running": next((i["id"] for i in _load()["items"] if i["status"] == "running"), "")})


# ---------- the hours it works in ----------

def window():
    value = SETTINGS.get("backlog_window") or "night"
    return value if value in WINDOWS else "night"


def hours():
    raw = SETTINGS.get("backlog_hours") or [22, 7]
    try:
        start, end = int(raw[0]) % 24, int(raw[1]) % 24
    except (TypeError, ValueError, IndexError):
        start, end = 22, 7
    return start, end


def open_now(now=None):
    """May the queue start a task now?"""
    state = _load()
    if state.get("session") and state["session"].get("now"):
        return True  # "Run now" until the queue is empty
    mode = window()
    if mode == "always":
        return True
    if mode == "paused":
        return False
    start, end = hours()
    hour = time.localtime(now or time.time()).tm_hour
    return (start <= hour or hour < end) if start > end else (start <= hour < end)


def models_free():
    """Is there a model with requests left for coding (else starting would only pause at once)?"""
    from . import limits
    model = llm.coding_model() or llm.default_model()
    if not model:
        return False
    if not llm.is_cloud(model) or not limits.resting(model):
        return True
    return bool(llm.pick_code_model() or llm.pick_model({"tools"}))


# ---------- items ----------

def add(slug, task, title="", source="you", upkeep=""):
    from . import agents
    task = str(task or "").strip()
    if not task:
        raise ValueError("Describe the task.")
    if not os.path.isdir(agents.project_path(slug)):
        raise ValueError("No project %s." % slug)
    item = {"id": secrets.token_hex(4), "slug": slug, "task": task[:8000], "title": (str(title or "").strip() or agents._first_line(task))[:80],
            "status": "queued", "source": source, "upkeep": upkeep, "created": time.time(), "started": 0, "ended": 0, "job_id": "",
            "conv_id": "", "result": "", "proof": None, "checkpoint": ""}
    with _lock:
        _load()["items"].append(item)
        _save()
    _publish()
    return item


def remove(item_id):
    with _lock:
        state = _load()
        item = next((i for i in state["items"] if i["id"] == item_id), None)
        if not item:
            raise ValueError("Unknown task.")
        if item["status"] == "running":
            raise ValueError("That task is running. Stop its job first.")
        state["items"].remove(item)
        _save()
    _publish()


def move(item_id, step):
    with _lock:
        items = _load()["items"]
        queued = [i for i in items if i["status"] == "queued"]
        item = next((i for i in queued if i["id"] == item_id), None)
        if not item:
            raise ValueError("Only waiting tasks can move.")
        pos = queued.index(item)
        new = max(0, min(len(queued) - 1, pos + (1 if int(step) > 0 else -1)))
        if new != pos:
            other = queued[new]
            a, b = items.index(item), items.index(other)
            items[a], items[b] = items[b], items[a]
            _save()
    _publish()


def retry(item_id):
    with _lock:
        item = next((i for i in _load()["items"] if i["id"] == item_id), None)
        if not item or item["status"] in ("queued", "running"):
            raise ValueError("Only finished tasks can run again.")
        item.update(status="queued", started=0, ended=0, job_id="", result="", proof=None)
        _save()
    _publish()


def run_now():
    """Work through the queue now, whatever the hours, until it is empty."""
    with _lock:
        state = _load()
        state["session"] = dict(state.get("session") or {}, now=True, started=time.time())
        _save()
    _publish()


# ---------- upkeep ----------

def set_upkeep(slug, kind, every="week", on=True):
    if kind not in UPKEEP:
        raise ValueError("Unknown upkeep job.")
    every = every if every in ("day", "week") else "week"
    with _lock:
        state = _load()
        state["upkeep"] = [u for u in state["upkeep"] if not (u["slug"] == slug and u["kind"] == kind)]
        if on:
            state["upkeep"].append({"slug": slug, "kind": kind, "every": every, "next": _next_slot(every)})
        _save()
    _publish()


def _next_slot(every, now=None):
    """The next start of the queue's hours (so upkeep joins the night's work), a day or a week from now."""
    now = now or time.time()
    start, _ = hours()
    t = time.localtime(now + (86400 if every == "day" else 7 * 86400) - 3600)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, start, 0, 0, 0, 0, -1))


def _due_upkeep(now):
    from . import agents
    due = []
    with _lock:
        for u in _load()["upkeep"]:
            if u["next"] <= now:
                u["next"] = _next_slot(u["every"], now)
                due.append(dict(u))
        if due:
            _save()
    for u in due:
        if not os.path.isdir(agents.project_path(u["slug"])):
            continue
        if any(i["slug"] == u["slug"] and i["upkeep"] == u["kind"] and i["status"] == "queued" for i in _load()["items"]):
            continue  # last time's is still waiting
        spec = UPKEEP[u["kind"]]
        add(u["slug"], spec["task"], title=spec["name"], source="upkeep", upkeep=u["kind"])


# ---------- the worker ----------

def _running():
    return next((i for i in _load()["items"] if i["status"] == "running"), None)


def _settle(item, job):
    """A running task whose job ended: record how it went."""
    from . import proof
    status = job.status if job else "failed"
    if status in ("running", "waiting"):
        return False  # still at it (or paused for usage limits: it continues by itself)
    with _lock:
        item.update(status={"done": "done", "failed": "failed", "stopped": "stopped"}.get(status, "failed"), ended=time.time(),
                    result=((job.result or job.error) if job else "The job is gone.")[:1500],
                    proof=(job.proof if job else None), verdict=proof.HEADLINE[job.proof["verdict"]] if job and job.proof else "")
        _save()
    _publish()
    return True


def _start(item):
    from . import agents
    job = agents.latest_job(item["slug"])
    prompt = "[From your queue, worked on while the user is away: decide sensible details yourself and note them in your report.]\n\n" + item["task"]
    if job and job.status == "running":
        return False  # the user (or another job) works on this project right now
    try:
        if job:
            job.message(prompt, display="Queued: " + item["title"])
        else:
            job = agents.assign("coder", prompt, title=item["title"], project=item["slug"], display="Queued: " + item["title"])
    except Exception as exc:  # noqa: BLE001 - a task that cannot start is marked failed, the queue goes on
        traceback.print_exc()
        with _lock:
            item.update(status="failed", ended=time.time(), result="Could not start: %s" % exc)
            _save()
        return True
    job.queued = item["id"]
    with _lock:
        item.update(status="running", started=time.time(), job_id=job.id, conv_id=job.conv_id, checkpoint=job.checkpoint)
        state = _load()
        if not state.get("session"):
            state["session"] = {"started": time.time(), "now": False}
        _save()
    _publish()
    return True


def _report():
    """The queue ran dry: the report of everything done since it started."""
    with _lock:
        state = _load()
        session = state.get("session") or {}
        state["session"] = None
        done = sorted((i for i in state["items"] if i["ended"] and i["ended"] >= session.get("started", 0) and i["status"] in ("done", "failed", "stopped")),
                      key=lambda i: i["ended"])
        if not done:
            _save()
            return None
        report = {"id": secrets.token_hex(4), "at": time.time(), "started": session.get("started", 0),
                  "items": [{k: i.get(k) for k in ("id", "slug", "title", "status", "verdict", "result", "conv_id", "job_id", "checkpoint")} for i in done]}
        state["reports"].append(report)
        _save()
    ok = sum(1 for i in done if i["status"] == "done")
    verified = sum(1 for i in done if i.get("verdict") == "Verified")
    failed = len(done) - ok
    notify.send("Queue: %d of %d task%s done" % (ok, len(done), "" if len(done) == 1 else "s"),
                "%d verified%s. Open Code to see what changed." % (verified, (", %d failed" % failed) if failed else ""),
                level="success" if not failed else "warning", link="/code#queue")
    _publish()
    return report


def tick(now=None):
    """Called by the scheduler: settle the running task, start the next one when the hours and the limits allow."""
    from . import agents
    now = now or time.time()
    try:
        _due_upkeep(now)
        current = _running()
        if current:
            if not _settle(current, agents.job(current["job_id"]) if current["job_id"] else None):
                return
        queued = [i for i in _load()["items"] if i["status"] == "queued"]
        if not queued:
            if _load().get("session"):
                _report()
            return
        if not open_now(now) or not models_free():
            return
        for item in queued:
            if _start(item):
                return
    except Exception:  # noqa: BLE001 - the queue never stops the scheduler
        traceback.print_exc()


def overview():
    state = _load()
    start, end = hours()
    return {"items": state["items"], "upkeep": state["upkeep"], "reports": state["reports"][-5:][::-1], "window": window(),
            "hours": [start, end], "open": open_now(), "session": state.get("session"),
            "kinds": [{"id": k, "name": v["name"], "icon": v["icon"]} for k, v in UPKEEP.items()]}
