"""Usage limits of cloud models, remembered so Scorpi works around them instead of stopping.

A model that answers "rate limited", "quota exceeded" or "out of credits" rests until its limit resets (from the
provider's headers, the wait it names, or the provider's daily reset), and the rate-limit headers of successful answers
say when one is about to run out. Model picks skip resting models, so work moves to another provider at once. When every
model is resting, a job pauses and continues by itself when the first one resets (a chat too, unless the user wrote in
the meantime). The state survives restarts.
"""

import datetime as dt
import json
import os
import re
import threading
import time

from .config import DATA_DIR

STORE = os.path.join(DATA_DIR, "limits.json")
_lock = threading.Lock()
_state = None
MIN_REST = 20
MAX_GUESS = 1800  # a limit that names no reset time: 1, 2, 4... minutes up to half an hour

DAILY = re.compile(r"per[ _-]?day|perday|daily|\brpd\b|free-models-per-day|day limit|requests? (a|per) day|24 ?h", re.I)
CREDITS = re.compile(r"insufficient (balance|credit|funds|quota)|out of credits|credit balance|no credits|billing|payment required|"
                     r"exceeded your current quota|top up", re.I)
LIMITED = re.compile(r"rate.?limit|too many requests|quota|limit (reached|exceeded)|exceeded .*limit|resource.?exhausted|"
                     r"capacity|overloaded|throttl", re.I)
_UNIT = {"ms": 0.001, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60,
         "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}
_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)(?![a-z])", re.I)
_WAIT = re.compile(r"(?:try again|retry|retry after|wait|resets?|available again|again) (?:in|after)?\s*((?:\d+(?:\.\d+)?\s*"
                   r"(?:ms|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)(?![a-z])\s*)+)", re.I)
_DELAY = re.compile(r"retry_?delay\W+(\d+(?:\.\d+)?)s", re.I)


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
        _state = {"rest": data.get("rest") or {}, "seen": data.get("seen") or {}, "resumes": data.get("resumes") or []}
    return _state


def _save():
    os.makedirs(DATA_DIR, exist_ok=True)
    now = time.time()
    state = _load()
    state["rest"] = {k: v for k, v in state["rest"].items() if v.get("until", 0) > now or v.get("at", 0) > now - 3600}
    tmp = STORE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1)
        os.replace(tmp, STORE)
    except OSError:
        pass


def reset_state():
    """Forget everything (tests)."""
    global _state
    with _lock:
        _state = {"rest": {}, "seen": {}, "resumes": []}
        _save()


# ---------- parsing what providers say ----------

def duration(text):
    """Seconds in "1m30.5s", "45s", "120ms", "2h", "7m12.5s" or a plain number of seconds; None if it names no time."""
    text = str(text or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    parts = _PART.findall(text)
    if not parts:
        return None
    return sum(float(n) * _UNIT[u.lower()] for n, u in parts)


def _reset_seconds(value, now):
    """A reset header: seconds to wait, a duration, or an epoch time (seconds or milliseconds)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return duration(value)
    if number > 1e12:
        return max(0.0, number / 1000 - now)
    if number > 1e9:
        return max(0.0, number - now)
    return max(0.0, number)


def _headers(headers):
    try:
        items = headers.items()
    except AttributeError:
        return {}
    return {str(k).lower(): str(v) for k, v in items if str(k).lower().startswith(("x-ratelimit", "ratelimit", "retry-after"))}


def _pacific_offset(ts):
    """Hours from UTC for US Pacific time (Gemini's daily quotas reset at midnight there), with daylight saving time."""
    t = dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(tzinfo=None)

    def sunday(month, n):
        first = dt.datetime(t.year, month, 1)
        return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))

    summer = sunday(3, 2).replace(hour=10) <= t < sunday(11, 1).replace(hour=9)
    return -7 if summer else -8


def next_reset(pid, now=None):
    """When a provider's daily limits reset: midnight US Pacific for Gemini, midnight UTC for the others."""
    from . import providers as P
    now = now or time.time()
    zone = (P.BY_ID.get(pid) or {}).get("resets", "utc")
    offset = _pacific_offset(now) if zone == "pacific" else 0
    local = dt.datetime.fromtimestamp(now, dt.timezone.utc) + dt.timedelta(hours=offset)
    midnight = (local + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - dt.timedelta(hours=offset)).timestamp() + 60


# ---------- remembering ----------

def _key(model):
    return model if model.startswith("@") else ""


def _pid(model):
    return model[1:].partition("/")[0] if model.startswith("@") else ""


def rest(key, until, why, kind="limit"):
    """key: a model ref ("@groq/llama-3.3-70b") or a whole provider ("@openrouter")."""
    now = time.time()
    with _lock:
        state = _load()
        old = state["rest"].get(key) or {}
        strikes = (old.get("strikes", 0) + 1) if old.get("at", 0) > now - 900 else 1
        state["rest"][key] = {"until": max(until, now + MIN_REST), "why": why, "kind": kind, "at": now, "strikes": strikes}
        _save()


def hit(model, exc):
    """A request was refused for a limit: rest the model (or its whole provider) until the limit resets.
    Returns the time it may be used again, or 0 when the error was not about limits."""
    key = _key(model)
    if not key:
        return 0
    msg = str(exc)
    status = getattr(exc, "status", 0) or 0
    credits = status == 402 or bool(CREDITS.search(msg))
    if not (status == 429 or credits or LIMITED.search(msg)):
        return 0
    now = time.time()
    pid = _pid(model)
    headers = getattr(exc, "headers", None) or {}
    waits = []
    if getattr(exc, "retry_after", 0):
        waits.append(float(exc.retry_after))
    for name, value in headers.items():
        if "reset" in name:
            remaining = headers.get(name.replace("reset", "remaining"))
            if remaining is None or _number(remaining) <= 0:
                seconds = _reset_seconds(value, now)
                if seconds is not None:
                    waits.append(seconds)
    found = _WAIT.search(msg) or None
    if found:
        seconds = duration(found.group(1))
        if seconds:
            waits.append(seconds)
    delay = _DELAY.search(msg)
    if delay:
        waits.append(float(delay.group(1)))
    wait = max(waits) if waits else None
    daily = bool(DAILY.search(msg))
    from . import providers as P
    whole = (P.BY_ID.get(pid) or {}).get("limit_scope") == "provider" and daily or credits or "free-models-per-day" in msg
    target = "@" + pid if whole else key
    if credits:
        until, why, kind = now + 12 * 3600, "its free credits are used up", "credits"
    elif daily and (wait is None or wait > 600):
        until, why, kind = next_reset(pid, now), "its daily limit is used up", "daily"
    elif wait is not None:
        until, why, kind = now + wait + 1, "it is at its rate limit", "rate"
    else:
        old = _load()["rest"].get(target) or {}
        strikes = old.get("strikes", 0) if old.get("at", 0) > now - 900 else 0
        until, why, kind = now + min(MAX_GUESS, 60 * 2 ** strikes), "it is at its rate limit", "rate"
    rest(target, until, why, kind)
    return until


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def observe(model, headers):
    """Rate-limit headers of a successful answer: remember what is left, and rest a model whose requests just ran out."""
    key = _key(model)
    if not key:
        return
    found = _headers(headers)
    now = time.time()
    with _lock:
        state = _load()
        changed = bool(state["rest"].pop(key, None)) | bool(state["rest"].pop("@" + _pid(model), None))  # it works again
        left = None
        for name, value in found.items():
            if "remaining" not in name or "token" in name:
                continue
            limit = found.get(name.replace("remaining", "limit"))
            seen = {"remaining": int(_number(value)), "limit": int(_number(limit)) if limit else 0, "at": now,
                    "daily": "day" in name or name.endswith("-requests") and _number(limit or 0) >= 500}
            if left is None or seen["remaining"] < left["remaining"]:
                left = seen
            if _number(value) <= 0:
                reset = found.get(name.replace("remaining", "reset"))
                seconds = _reset_seconds(reset, now) if reset else None
                if seconds and seconds > 2:
                    state["rest"][key] = {"until": now + seconds + 1, "why": "it has no requests left until its limit resets",
                                          "kind": "daily" if seconds > 3600 else "rate", "at": now, "strikes": 1}
                    changed = True
        if left is not None:
            state["seen"][key] = left
            changed = changed or left["remaining"] % 10 == 0  # write now and then, not on every answer
        if changed:
            _save()


def resting(model):
    """Until when a model rests (it or its whole provider), or 0."""
    if not model or not model.startswith("@"):
        return 0
    now = time.time()
    state = _load()
    until = max((state["rest"].get(k) or {}).get("until", 0) for k in (model, "@" + _pid(model)))
    return until if until > now else 0


def provider_resting(pid):
    entry = _load()["rest"].get("@" + pid) or {}
    return entry.get("until", 0) if entry.get("until", 0) > time.time() else 0


def why(model):
    state = _load()
    for k in (model, "@" + _pid(model)):
        entry = state["rest"].get(k)
        if entry and entry.get("until", 0) > time.time():
            return entry.get("why", "")
    return ""


def next_free(now=None):
    """(time, key) of the first resting model or provider to become usable again, or (0, "")."""
    now = now or time.time()
    future = [(v["until"], k) for k, v in _load()["rest"].items() if v.get("until", 0) > now]
    return min(future) if future else (0, "")


def clock(ts):
    """A time as people read it: "14:05", or "Tue 01:00" when it is not today."""
    when = time.localtime(ts)
    if time.strftime("%Y%m%d", when) == time.strftime("%Y%m%d"):
        return time.strftime("%H:%M", when)
    return time.strftime("%a %H:%M", when)


def overview():
    """What the app shows: resting models and providers, and requests left where providers report it."""
    from . import llm
    now = time.time()
    state = _load()
    resting_ = [{"key": k, "label": llm.label(k) if "/" in k else llm.provider_name(k[1:]), "until": v["until"], "at": clock(v["until"]),
                 "why": v.get("why", ""), "kind": v.get("kind", "")}
                for k, v in sorted(state["rest"].items(), key=lambda kv: kv[1].get("until", 0)) if v.get("until", 0) > now]
    left = [{"key": k, "label": llm.label(k), **v} for k, v in state["seen"].items() if v.get("at", 0) > now - 86400]
    return {"resting": resting_, "left": sorted(left, key=lambda x: x["remaining"]), "resumes": [dict(r, clock=clock(r["at"])) for r in state["resumes"]]}


def for_provider(pid):
    """A provider's resting state for its tile: {"until", "at", "why", "models"} or None."""
    now = time.time()
    state = _load()
    whole = state["rest"].get("@" + pid) or {}
    models = {k: v for k, v in state["rest"].items() if k.startswith("@%s/" % pid) and v.get("until", 0) > now}
    if whole.get("until", 0) > now:
        return {"until": whole["until"], "at": clock(whole["until"]), "why": whole.get("why", ""), "all": True, "models": len(models)}
    if models:
        first = min(models.values(), key=lambda v: v["until"])
        return {"until": first["until"], "at": clock(first["until"]), "why": first.get("why", ""), "all": False, "models": len(models)}
    return None


def clear(key=""):
    """Let a model (or everything) be tried again now."""
    with _lock:
        state = _load()
        if key:
            state["rest"].pop(key, None)
        else:
            state["rest"].clear()
        _save()


# ---------- pausing and continuing ----------

def resume_later(kind, ident, at, **info):
    """Continue a job ("job", job id) or a chat ("chat", conversation id) at a time, also after a restart."""
    with _lock:
        state = _load()
        state["resumes"] = [r for r in state["resumes"] if not (r["kind"] == kind and r["id"] == ident)]
        state["resumes"].append(dict(info, kind=kind, id=ident, at=at, created=time.time()))
        _save()


def cancel_resume(kind, ident):
    with _lock:
        state = _load()
        before = len(state["resumes"])
        state["resumes"] = [r for r in state["resumes"] if not (r["kind"] == kind and r["id"] == ident)]
        if len(state["resumes"]) != before:
            _save()
            return True
    return False


def pending(kind, ident):
    return next((r for r in _load()["resumes"] if r["kind"] == kind and r["id"] == ident), None)


def due(now=None):
    """Take the resumes whose time has come."""
    now = now or time.time()
    with _lock:
        state = _load()
        ready = [r for r in state["resumes"] if r["at"] <= now]
        if ready:
            state["resumes"] = [r for r in state["resumes"] if r["at"] > now]
            _save()
    return ready


CONTINUE = ("[Written by Scorpi, not by the user] The models were at their usage limits, so your work paused. The limits "
            "have reset: continue exactly where you stopped and finish the task. Check what is already done before you "
            "repeat anything.")


def tick(now):
    """Called by the scheduler every few seconds: continue what paused for a limit."""
    for r in due(now):
        try:
            if r["kind"] == "job":
                from . import agents
                job = agents.job(r["id"])
                if job and job.status == "waiting":
                    job.resume()
            elif r["kind"] == "chat":
                from . import agent, db, events
                if db.get_conversation(r["id"]) and not events.active_run_for(r["id"]):
                    run = agent.start_turn(r["id"], CONTINUE, skill=r.get("skill") or "", model=r.get("model") or "",
                                           system="Continuing after the usage limits reset")
                    events.publish("resumed", {"conv_id": r["id"], "run_id": run.id, "text": "Continuing after the usage limits reset"})
                    events.publish("conversation", {"id": r["id"]})
        except Exception:
            import traceback
            traceback.print_exc()
