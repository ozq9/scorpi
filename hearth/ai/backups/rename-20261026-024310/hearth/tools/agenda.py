"""Reminders and calendar events (fired by the scheduler, exportable as an iCal feed)."""

import datetime as dt
import re
import time

from .. import db
from . import Result, ToolError, tool

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_when(value, now=None):
    """Accept ISO dates, 'in 20 minutes', 'tomorrow 9:00', 'friday 14:30', '18:00'. Returns a local timestamp."""
    if value in (None, ""):
        raise ToolError("A time is required.")
    if isinstance(value, (int, float)):
        return float(value)
    now = now or dt.datetime.now()
    text = str(value).strip().lower()
    m = re.match(r"^in\s+(\d+(?:\.\d+)?)\s*(s|sec|second|m|min|minute|h|hr|hour|d|day|w|week)s?$", text)
    if m:
        amount = float(m.group(1))
        unit = m.group(2)[0]
        delta = {"s": dt.timedelta(seconds=amount), "m": dt.timedelta(minutes=amount), "h": dt.timedelta(hours=amount),
                 "d": dt.timedelta(days=amount), "w": dt.timedelta(weeks=amount)}[unit]
        return (now + delta).timestamp()
    iso = text.upper().replace("Z", "+00:00")
    for candidate in (iso, iso.replace(" ", "T")):
        try:
            parsed = dt.datetime.fromisoformat(candidate)
            if parsed.tzinfo:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed.timestamp()
        except ValueError:
            pass
    day = now.date()
    rest = text
    roll = 0  # days to add if the resulting time already passed
    if rest.startswith("today"):
        rest = rest[5:]
    elif rest.startswith("tomorrow"):
        day = day + dt.timedelta(days=1)
        rest = rest[8:]
    else:
        for i, name in enumerate(WEEKDAYS):
            if rest.startswith(name) or rest.startswith("next " + name):
                ahead = (i - now.weekday()) % 7
                if rest.startswith("next ") and not ahead:
                    ahead = 7
                roll = 7 if not ahead else 0
                day = day + dt.timedelta(days=ahead)
                rest = rest.split(name, 1)[1]
                break
    rest = rest.replace("at", " ").strip()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", rest)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
        stamp = dt.datetime.combine(day, dt.time(hour % 24, minute))
        if stamp < now and text == rest:  # bare time already passed today -> tomorrow
            stamp += dt.timedelta(days=1)
        elif stamp < now and roll:
            stamp += dt.timedelta(days=roll)
        return stamp.timestamp()
    if not rest and day != now.date():
        return dt.datetime.combine(day, dt.time(9, 0)).timestamp()
    raise ToolError("Could not understand the time %r. Use ISO like 2026-05-01T14:30 or 'in 2 hours'." % value)


def fmt(ts):
    return time.strftime("%a %d %b %Y, %H:%M", time.localtime(ts))


@tool(
    "create_reminder",
    "Set a reminder. At the due time the user gets a notification on all devices (and phone push if configured). "
    "Times: ISO local time (e.g. 2026-03-01T09:00), 'in 30 minutes', 'tomorrow 8:00'. Optional repeat.",
    {"text": "string: What to remind about", "when": "string: Due time", "repeat?": "string[none,daily,weekdays,weekly,monthly]: Repeat"},
    group="agenda",
    risk="write",
)
def create_reminder(ctx, text, when, repeat="none"):
    due = parse_when(when)
    rid = db.x("INSERT INTO reminders (text, due, repeat, created) VALUES (?,?,?,?)", (text, due, "" if repeat == "none" else repeat or "", time.time()))
    from .. import events
    events.publish("agenda", {})
    return Result("Reminder #%s set for %s: %s%s" % (rid, fmt(due), text, " (repeats %s)" % repeat if repeat and repeat != "none" else ""),
                  display={"type": "agenda", "items": [{"kind": "reminder", "title": text, "time": due, "repeat": repeat}]})


@tool(
    "create_event",
    "Add an event to the calendar (synced to every device; subscribable as iCal from phones).",
    {"title": "string: Event title", "start": "string: Start time", "end?": "string: End time (default +1h)", "location?": "string: Place",
     "notes?": "string: Details", "remind_minutes?": "integer: Notify this many minutes before (default 10, 0=none)"},
    group="agenda",
    risk="write",
)
def create_event(ctx, title, start, end="", location="", notes="", remind_minutes=10):
    s = parse_when(start)
    e = parse_when(end) if end else s + 3600
    if e < s:
        raise ToolError("End is before start.")
    eid = db.x("INSERT INTO events (title, start, end, location, notes, remind, created) VALUES (?,?,?,?,?,?,?)",
               (title, s, e, location or "", notes or "", int(remind_minutes if remind_minutes is not None else 10), time.time()))
    from .. import events
    events.publish("agenda", {})
    return Result("Event #%s: %s, %s – %s%s" % (eid, title, fmt(s), time.strftime("%H:%M", time.localtime(e)), " @ " + location if location else ""),
                  display={"type": "agenda", "items": [{"kind": "event", "title": title, "time": s, "end": e, "location": location}]})


@tool(
    "list_agenda",
    "List upcoming reminders and calendar events (default next 14 days). Include past to see recent ones.",
    {"days?": "integer: How many days ahead", "include_past?": "boolean: Also show the last 7 days"},
    group="agenda",
)
def list_agenda(ctx, days=14, include_past=False):
    now = time.time()
    start = now - 7 * 86400 if include_past else now - 3600
    end = now + int(days or 14) * 86400
    items = []
    for r in db.q("SELECT * FROM reminders WHERE done=0 AND due BETWEEN ? AND ? ORDER BY due", (start, end)):
        items.append((r["due"], "reminder #%d: %s%s" % (r["id"], r["text"], " (repeats %s)" % r["repeat"] if r["repeat"] else ""), {"kind": "reminder", "title": r["text"], "time": r["due"]}))
    for e in db.q("SELECT * FROM events WHERE end >= ? AND start <= ? ORDER BY start", (start, end)):
        items.append((e["start"], "event #%d: %s%s" % (e["id"], e["title"], " @ " + e["location"] if e["location"] else ""), {"kind": "event", "title": e["title"], "time": e["start"], "end": e["end"], "location": e["location"]}))
    items.sort(key=lambda i: i[0])
    if not items:
        return Result("Nothing scheduled in the next %s days." % days)
    return Result("\n".join("%s — %s" % (fmt(t), label) for t, label, _ in items), display={"type": "agenda", "items": [i[2] for i in items[:20]]})


@tool(
    "delete_agenda_item",
    "Delete (or mark done) a reminder or event by id.",
    {"kind": "string[reminder,event]: Item type", "id": "integer: Item id"},
    group="agenda",
    risk="write",
)
def delete_agenda_item(ctx, kind, id):
    table = "reminders" if kind == "reminder" else "events"
    count = db.x("DELETE FROM %s WHERE id=?" % table, (int(id),))
    from .. import events
    events.publish("agenda", {})
    return Result("Deleted %s #%s." % (kind, id) if count else "No %s #%s." % (kind, id), ok=bool(count))


def next_repeat(due, repeat):
    d = dt.datetime.fromtimestamp(due)
    now = dt.datetime.now()
    while d <= now:
        if repeat == "daily":
            d += dt.timedelta(days=1)
        elif repeat == "weekdays":
            d += dt.timedelta(days=1)
            while d.weekday() >= 5:
                d += dt.timedelta(days=1)
        elif repeat == "weekly":
            d += dt.timedelta(weeks=1)
        elif repeat == "monthly":
            month = d.month % 12 + 1
            year = d.year + (1 if month == 1 else 0)
            day = min(d.day, 28)
            d = d.replace(year=year, month=month, day=day)
        else:
            return None
    return d.timestamp()


def ics_feed():
    def stamp(ts):
        return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ts))

    def esc(text):
        return str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Hearth//Agenda//EN", "X-WR-CALNAME:Hearth", "CALSCALE:GREGORIAN"]
    for e in db.q("SELECT * FROM events WHERE end > ?", (time.time() - 90 * 86400,)):
        lines += ["BEGIN:VEVENT", "UID:hearth-event-%d" % e["id"], "DTSTAMP:%s" % stamp(e["created"] or time.time()),
                  "DTSTART:%s" % stamp(e["start"]), "DTEND:%s" % stamp(e["end"]), "SUMMARY:%s" % esc(e["title"])]
        if e["location"]:
            lines.append("LOCATION:%s" % esc(e["location"]))
        if e["notes"]:
            lines.append("DESCRIPTION:%s" % esc(e["notes"]))
        if e["remind"]:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:%s" % esc(e["title"]), "TRIGGER:-PT%dM" % e["remind"], "END:VALARM"]
        lines.append("END:VEVENT")
    for r in db.q("SELECT * FROM reminders WHERE done=0"):
        lines += ["BEGIN:VEVENT", "UID:hearth-reminder-%d" % r["id"], "DTSTAMP:%s" % stamp(r["created"] or time.time()),
                  "DTSTART:%s" % stamp(r["due"]), "DTEND:%s" % stamp(r["due"] + 900), "SUMMARY:⏰ %s" % esc(r["text"])]
        rule = {"daily": "FREQ=DAILY", "weekdays": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekly": "FREQ=WEEKLY", "monthly": "FREQ=MONTHLY"}.get(r["repeat"])
        if rule:
            lines.append("RRULE:" + rule)
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:%s" % esc(r["text"]), "TRIGGER:PT0M", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
