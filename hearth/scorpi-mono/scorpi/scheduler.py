"""Background clock: fires reminders, event alerts and automations."""

import datetime as dt
import json
import threading
import time
import traceback

from . import db, events, notify

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def describe(schedule):
    kind = schedule.get("type")
    if kind == "interval":
        minutes = int(schedule.get("minutes") or 60)
        return "every %d min" % minutes if minutes < 120 else "every %g h" % (minutes / 60)
    if kind == "daily":
        return "daily at %s" % schedule.get("time", "08:00")
    if kind == "weekdays":
        return "weekdays at %s" % schedule.get("time", "08:00")
    if kind == "weekly":
        return "every %s at %s" % (str(schedule.get("day", "monday")).capitalize(), schedule.get("time", "08:00"))
    if kind == "once":
        return "once at %s" % time.strftime("%d %b %H:%M", time.localtime(float(schedule.get("at") or 0)))
    return "manual"


def next_run(schedule, after=None):
    now = dt.datetime.fromtimestamp(after or time.time())
    kind = schedule.get("type")
    if kind == "interval":
        return (now + dt.timedelta(minutes=max(5, int(schedule.get("minutes") or 60)))).timestamp()
    if kind == "once":
        at = float(schedule.get("at") or 0)
        return at if at > now.timestamp() else None
    if kind in ("daily", "weekdays", "weekly"):
        hh, mm = (str(schedule.get("time") or "08:00").split(":") + ["0"])[:2]
        candidate = now.replace(hour=int(hh) % 24, minute=int(mm) % 60, second=0, microsecond=0)
        while candidate <= now:
            candidate += dt.timedelta(days=1)
        if kind == "weekdays":
            while candidate.weekday() >= 5:
                candidate += dt.timedelta(days=1)
        if kind == "weekly":
            target = WEEKDAYS.index(str(schedule.get("day", "monday")).lower()) if str(schedule.get("day", "")).lower() in WEEKDAYS else 0
            while candidate.weekday() != target:
                candidate += dt.timedelta(days=1)
        return candidate.timestamp()
    return None


def run_automation(row, payload=None):
    from .agent import run_detached
    prompt = row["prompt"]
    if payload:
        prompt += "\n\nTrigger payload:\n" + json.dumps(payload, ensure_ascii=False)[:8000]
    title = row["name"]
    task_id, conv_id, run = run_detached(prompt, title, skill=row.get("skill") or "workflow", origin="automation",
                                         notify_done=bool(row.get("notify", 1)), conv_id=row.get("conv_id") or None)
    db.x("UPDATE automations SET last_run=?, last_status=?, conv_id=? WHERE id=?", (time.time(), "running", conv_id, row["id"]))
    events.publish("automation", {"id": row["id"]})

    def settle():
        for _ in run.follow():
            pass
        failed = any(e.get("type") == "error" for e in run.events)
        db.x("UPDATE automations SET last_status=? WHERE id=?", ("failed" if failed else "done", row["id"]))
        events.publish("automation", {"id": row["id"]})

    threading.Thread(target=settle, daemon=True).start()
    return task_id, conv_id


def _tick():
    now = time.time()
    from .tools.agenda import next_repeat
    for r in db.q("SELECT * FROM reminders WHERE done=0 AND due<=?", (now,)):
        notify.send("Reminder", r["text"], level="urgent")
        nxt = next_repeat(r["due"], r["repeat"]) if r["repeat"] else None
        if nxt:
            db.x("UPDATE reminders SET due=? WHERE id=?", (nxt, r["id"]))
        else:
            db.x("UPDATE reminders SET done=1 WHERE id=?", (r["id"],))
        events.publish("agenda", {})
    for e in db.q("SELECT * FROM events WHERE notified=0 AND remind>0 AND start-remind*60<=? AND start>?", (now, now - 3600)):
        when = time.strftime("%H:%M", time.localtime(e["start"]))
        notify.send("Coming up at %s" % when, e["title"] + (" · " + e["location"] if e["location"] else ""), level="info")
        db.x("UPDATE events SET notified=1 WHERE id=?", (e["id"],))
    for a in db.q("SELECT * FROM automations WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=?", (now,)):
        schedule = json.loads(a["schedule"] or "{}")
        db.x("UPDATE automations SET next_run=? WHERE id=?", (next_run(schedule, now + 1), a["id"]))
        try:
            run_automation(a)
        except Exception:
            traceback.print_exc()
    from . import agents
    agents.tick(now)


def _loop():
    while True:
        try:
            _tick()
        except Exception:
            traceback.print_exc()
        time.sleep(10)


def start():
    # Automations missed while the PC was off run at their next slot instead of all at once.
    for a in db.q("SELECT * FROM automations WHERE enabled=1"):
        schedule = json.loads(a["schedule"] or "{}")
        if not a["next_run"] or a["next_run"] < time.time() - 300:
            db.x("UPDATE automations SET next_run=? WHERE id=?", (next_run(schedule), a["id"]))
    db.x("UPDATE tasks SET status='interrupted' WHERE status='running'")
    from . import agents
    agents.init()
    threading.Thread(target=_loop, daemon=True, name="scheduler").start()
