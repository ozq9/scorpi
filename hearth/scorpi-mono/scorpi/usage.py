"""Usage: the tokens and cost of every model call, and the activity stats built from them and the chats.

Every answer from a model is one row: which model, who it worked for, where it came from (chat, agents, code,
routines, other), the tokens the provider reported (or an estimate from the text when it reports none) and what it
cost. Local models and free tiers cost nothing; coding plans are a flat fee, so their calls count tokens but no money;
pay-as-you-go keys are priced from the table below (estimates: providers change prices) or a price the user set.
"""

import contextlib
import re
import threading
import time
from collections import Counter

from . import db, events
from . import providers as P
from .config import SETTINGS

db.x("""CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, owner TEXT, model TEXT, provider TEXT, kind TEXT, source TEXT,
    conv_id TEXT DEFAULT '', input INTEGER DEFAULT 0, output INTEGER DEFAULT 0, cached INTEGER DEFAULT 0,
    cost REAL DEFAULT 0, estimated INTEGER DEFAULT 0, ms INTEGER DEFAULT 0
)""")
db.x("CREATE INDEX IF NOT EXISTS idx_usage_owner ON usage(owner, ts)")
db.x("CREATE INDEX IF NOT EXISTS idx_usage_conv ON usage(conv_id)")

PLANS = {"opencode-go", "zai-coding", "kimi-coding"}  # flat monthly fee: tokens count, money does not
FREE = {p["id"] for p in P.FREE}

# US$ per million tokens (input, output) for pay-as-you-go keys, first match wins. Estimates: set the real price of a
# model on the Usage page and it is used instead.
PRICES = [
    (r"claude-(3-)?opus-4-?[01]\b|claude-3-opus", 15, 75),
    (r"claude.*opus", 5, 25),
    (r"claude.*sonnet", 3, 15),
    (r"claude-3(-5)?-haiku", 0.8, 4),
    (r"claude.*haiku", 1, 5),
    (r"gpt-5.*nano", 0.05, 0.4),
    (r"gpt-5.*mini", 0.25, 2),
    (r"gpt-5", 1.25, 10),
    (r"gpt-4\.1-nano", 0.1, 0.4),
    (r"gpt-4\.1-mini", 0.4, 1.6),
    (r"gpt-4\.1", 2, 8),
    (r"gpt-4o-mini", 0.15, 0.6),
    (r"gpt-4o", 2.5, 10),
    (r"\bo[34]-mini", 1.1, 4.4),
    (r"\bo3\b", 2, 8),
    (r"deepseek", 0.28, 0.42),
    (r"kimi|moonshot", 0.6, 2.5),
    (r"minimax", 0.3, 1.2),
    (r"glm", 0.6, 2.2),
    (r"qwen.*max", 1.6, 6.4),
    (r"qwen.*coder", 1, 5),
    (r"qwen", 0.4, 1.2),
]
CACHED_SHARE = 0.1  # cached input is billed at about a tenth of the input price
SOURCES = ("chat", "agents", "code", "routines", "other")
ORIGIN_SOURCE = {"chat": "chat", "agent": "agents", "agent_update": "agents", "task": "agents",
                 "automation": "routines", "schedule": "routines"}

_tag = threading.local()


@contextlib.contextmanager
def tag(origin, conv_id=""):
    """Model calls made inside are counted for this conversation and source (chat, agents, routines...)."""
    previous = getattr(_tag, "value", None)
    _tag.value = (ORIGIN_SOURCE.get(origin, origin or "other"), conv_id or "")
    try:
        yield
    finally:
        _tag.value = previous


def split(model):
    model = str(model or "")
    if model.startswith("@") and "/" in model:
        pid, mid = model[1:].split("/", 1)
        return pid, mid
    return "", model


def kind_of(model):
    """local (Ollama), free (a free tier), plan (a coding subscription), paid (pay as you go) or custom (own endpoint)."""
    pid, mid = split(model)
    if not pid:
        return "custom" if SETTINGS["provider"] == "openai" else "local"
    if pid in FREE or re.search(r"(^|[-:/])free\b", mid, re.I):
        return "free"
    return "plan" if pid in PLANS else "paid"


def label(model):
    pid, mid = split(model)
    return "%s · %s" % (P.BY_ID[pid]["name"] if pid in P.BY_ID else pid, mid) if pid else (mid or "unknown")


def price(model):
    """(input, output) US$ per million tokens, whether the user set it, or None when it costs nothing / is unknown."""
    own = str((SETTINGS["prices"] or {}).get(model) or "")
    if re.match(r"^\d+(\.\d+)?/\d+(\.\d+)?$", own):
        pin, pout = own.split("/")
        return float(pin), float(pout), True
    if kind_of(model) != "paid":
        return None
    mid = split(model)[1].lower()
    for pattern, pin, pout in PRICES:
        if re.search(pattern, mid):
            return pin, pout, False
    return None


def cost_of(model, inp, out, cached=0):
    p = price(model)
    if not p:
        return 0.0
    return (inp * p[0] + cached * p[0] * CACHED_SHARE + out * p[1]) / 1e6


def set_price(model, inp=None, out=None):
    prices = dict(SETTINGS["prices"] or {})
    if inp is None and out is None:
        prices.pop(model, None)
    else:
        num = lambda v: ("%.6f" % max(0.0, float(v or 0))).rstrip("0").rstrip(".")  # noqa: E731
        prices[model] = "%s/%s" % (num(inp), num(out))  # settings keep dict values as text
    SETTINGS.update({"prices": prices})
    # what was counted before is priced again, so the totals stay consistent
    for row in db.q("SELECT id, input, output, cached FROM usage WHERE model=?", (model,)):
        db.x("UPDATE usage SET cost=? WHERE id=?", (cost_of(model, row["input"], row["output"], row["cached"]), row["id"]))


def _chars(messages):
    total = 0
    for m in messages or []:
        total += len(str(m.get("content") or ""))
        for call in m.get("tool_calls") or []:
            total += len(str(call.get("arguments") or ""))
        total += 800 * len(m.get("images") or [])  # an image is worth a few hundred tokens
    return total


def record(model, used=None, messages=None, reply="", started=None, conv_id=None, source=None, owner=None, cost=None):
    """Count one answer. used: {'input', 'output', 'cached'} as the provider reported it; without it the tokens are
    estimated from the text (about four characters a token). cost: what the provider said it cost (OpenCode)."""
    try:
        tagged = getattr(_tag, "value", None) or ("other", "")
        source = source or tagged[0]
        conv_id = conv_id if conv_id is not None else tagged[1]
        if conv_id and source != "code":
            conv = db.get_conversation(conv_id)
            if conv and conv.get("skill") == "code":
                source = "code"
        used = used or {}
        inp, out, cached = int(used.get("input") or 0), int(used.get("output") or 0), int(used.get("cached") or 0)
        estimated = 0
        if not inp and not out and messages is not None:
            inp, out, estimated = _chars(messages) // 4, len(reply or "") // 4, 1
        if not inp and not out and not cost:
            return
        kind = kind_of(model)
        if cost is None or kind != "paid":
            cost = cost_of(model, inp, out, cached) if kind in ("paid", "custom") else 0.0
        owner = owner or (db.owner_of(conv_id) if conv_id else db.current_user())
        db.x("INSERT INTO usage (ts, owner, model, provider, kind, source, conv_id, input, output, cached, cost, estimated, ms) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (time.time(), owner, model, split(model)[0] or ("ollama" if kind == "local" else "custom"), kind,
              source if source in SOURCES else "other", conv_id or "", inp, out, cached, float(cost or 0), estimated,
              int((time.time() - started) * 1000) if started else 0))
        events.publish("usage", {"conv_id": conv_id or "", "tokens": inp + out + cached, "cost": cost})
    except Exception:  # noqa: BLE001 - counting must never break an answer
        import traceback
        traceback.print_exc()


def conversation(conv_id):
    row = db.one("SELECT COUNT(*) AS calls, COALESCE(SUM(input),0) AS input, COALESCE(SUM(output),0) AS output, "
                 "COALESCE(SUM(cached),0) AS cached, COALESCE(SUM(cost),0) AS cost, COALESCE(MAX(estimated),0) AS estimated "
                 "FROM usage WHERE conv_id=?", (conv_id,))
    return row or {"calls": 0, "input": 0, "output": 0, "cached": 0, "cost": 0, "estimated": 0}


# ---------- stats ----------

SESSION_GAP = 30 * 60  # a pause longer than this starts a new session
STOP = set("""a an and the of to in on for with from by at about into over is are was be it this that these those my your our
how what why when where which who can could should would will i you we me us do does did make made new chat conversation
help using use vs or not no yes get got want need please some any just more most all one two up out off as if so than
then there here its it's im i'm let lets let's quick short plan write fix explain summarize compare translate debug
create build find show tell give list add update check draft""".split())


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _streaks(days):
    """(current, longest) runs of consecutive days in a set of 'YYYY-MM-DD' strings. Today without activity yet does
    not break the current streak."""
    if not days:
        return 0, 0
    ordinals = sorted({time.mktime(time.strptime(d, "%Y-%m-%d")) // 86400 for d in days})
    longest = run = 1
    for a, b in zip(ordinals, ordinals[1:]):
        run = run + 1 if b - a == 1 else 1
        longest = max(longest, run)
    today = time.mktime(time.strptime(_day(time.time()), "%Y-%m-%d")) // 86400
    have = set(ordinals)
    current, day = 0, today if today in have else today - 1
    while day in have:
        current += 1
        day -= 1
    return current, longest


def stats(owner, days=0):
    """Everything the Usage page and the recap show, for the last `days` days (0: all time)."""
    now = time.time()
    start = now - days * 86400 if days else 0
    rows = db.q("SELECT ts, model, kind, source, input, output, cached, cost, estimated FROM usage WHERE owner=? AND ts>=?",
                (owner, start))
    first_usage = db.one("SELECT MIN(ts) AS t FROM usage WHERE owner=?", (owner,))["t"]

    # the person's own activity: what they wrote in chats and coding projects (not scheduled agents talking to themselves)
    mine = ("FROM messages m JOIN conversations c ON c.id=m.conv_id WHERE c.owner=? AND m.role='user' "
            "AND (c.origin='chat' OR c.skill='code')")
    prompts = [r["created"] for r in db.q("SELECT m.created " + mine + " AND m.created>=?", (owner, start))]
    year = [r["created"] for r in db.q("SELECT m.created " + mine + " AND m.created>=?", (owner, now - 371 * 86400))]
    every_day = {_day(r["created"]) for r in db.q("SELECT m.created " + mine, (owner,))}
    first_chat = db.one("SELECT MIN(m.created) AS t " + mine, (owner,))["t"]

    # sessions: runs of messages (anyone's, in the person's chats) without a pause longer than SESSION_GAP
    stamps = [r["created"] for r in db.q(
        "SELECT m.created FROM messages m JOIN conversations c ON c.id=m.conv_id WHERE c.owner=? AND m.created>=? "
        "AND (c.origin='chat' OR c.skill='code') ORDER BY m.created", (owner, start))]
    sessions, longest = 0, 0.0
    begin = prev = None
    for t in stamps:
        if prev is None or t - prev > SESSION_GAP:
            if begin is not None:
                longest = max(longest, prev - begin)
            sessions += 1
            begin = t
        prev = t
    if begin is not None:
        longest = max(longest, prev - begin)

    hours = [0] * 24
    grid = [[0] * 24 for _ in range(7)]  # weekday (Monday first) x hour
    per_day = Counter()
    for t in prompts:
        lt = time.localtime(t)
        hours[lt.tm_hour] += 1
        grid[lt.tm_wday][lt.tm_hour] += 1
        per_day[_day(t)] += 1
    heat = Counter(_day(t) for t in year)

    models, sources, daily = {}, Counter(), {}
    totals = {"input": 0, "output": 0, "cached": 0, "cost": 0.0, "calls": 0, "estimated": 0}
    for r in rows:
        tokens = r["input"] + r["output"] + r["cached"]
        m = models.setdefault(r["model"], {"model": r["model"], "label": label(r["model"]), "kind": r["kind"],
                                           "input": 0, "output": 0, "cached": 0, "cost": 0.0, "calls": 0, "estimated": 0})
        for key in ("input", "output", "cached", "cost"):
            m[key] += r[key]
            totals[key] += r[key]
        m["calls"] += 1
        m["estimated"] += r["estimated"]
        totals["calls"] += 1
        totals["estimated"] += r["estimated"]
        sources[r["source"]] += tokens
        d = daily.setdefault(_day(r["ts"]), {"tokens": 0, "cost": 0.0, "models": Counter()})
        d["tokens"] += tokens
        d["cost"] += r["cost"]
        d["models"][r["model"]] += tokens
    for m in models.values():
        p = price(m["model"])
        m["price"] = {"input": p[0], "output": p[1], "own": p[2]} if p else None
        m["tokens"] = m["input"] + m["output"] + m["cached"]
    by_model = sorted(models.values(), key=lambda m: -m["tokens"])

    # before tokens were counted, the answers in the chats still say which model wrote them
    answers = db.q("SELECT json_extract(m.data, '$.model') AS model, COUNT(*) AS n FROM messages m JOIN conversations c "
                   "ON c.id=m.conv_id WHERE c.owner=? AND m.role='assistant' AND m.created>=? GROUP BY 1 ORDER BY 2 DESC LIMIT 8",
                   (owner, start))
    answers = [{"model": a["model"], "label": label(a["model"]), "answers": a["n"]} for a in answers if a["model"]]
    favorite = by_model[0] if by_model else (answers[0] if answers else None)

    current, longest_streak = _streaks(every_day)
    busiest = per_day.most_common(1)
    convs = db.one("SELECT COUNT(*) AS n FROM conversations c WHERE c.owner=? AND c.created>=? AND (c.origin='chat' OR c.skill='code') "
                   "AND EXISTS (SELECT 1 FROM messages m WHERE m.conv_id=c.id)", (owner, start))["n"]
    longest_chat = db.one("SELECT c.id, c.title, COUNT(*) AS n FROM messages m JOIN conversations c ON c.id=m.conv_id "
                          "WHERE c.owner=? AND m.created>=? AND (c.origin='chat' OR c.skill='code') GROUP BY c.id ORDER BY n DESC LIMIT 1",
                          (owner, start))
    words = Counter()
    for r in db.q("SELECT title FROM conversations c WHERE c.owner=? AND c.created>=? AND (c.origin='chat' OR c.skill='code')",
                  (owner, start)):
        for w in re.findall(r"[^\W\d_][\w'+#.-]{2,}", (r["title"] or "").lower()):
            w = w.strip(".-'")
            if len(w) > 2 and w not in STOP:
                words[w] += 1
    return {
        "range": days,
        "now": now,
        "counting_since": first_usage,
        "first_chat": first_chat,
        "totals": {**totals, "tokens": totals["input"] + totals["output"] + totals["cached"]},
        "models": by_model,
        "answers": answers,
        "favorite": favorite,
        "sources": {s: sources.get(s, 0) for s in SOURCES},
        "daily": [{"day": k, "tokens": v["tokens"], "cost": v["cost"], "models": dict(v["models"])} for k, v in sorted(daily.items())],
        "heat": sorted(heat.items()),
        "hours": hours,
        "week": grid,
        "prompts": len(prompts),
        "conversations": convs,
        "sessions": sessions,
        "longest_session": longest,
        "longest_chat": longest_chat,
        "active_days": len(per_day),
        "streak": {"current": current, "longest": longest_streak},
        "busiest_day": {"day": busiest[0][0], "prompts": busiest[0][1]} if busiest else None,
        "words": words.most_common(12),
    }
