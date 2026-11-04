"""How well each model actually works for this user, from what it did here: jobs finished or failed, syntax checks
after its edits, test runs, tool calls it got wrong, work the user undid, parallel attempts it won or lost, and the
built-in benchmark (scorpi/bench.py). Automatic model picks use it: a free model that keeps failing drops below one
that delivers, whatever its catalog order. Counted on this PC only.
"""

import json
import os
import threading
import time

from .config import DATA_DIR

STORE = os.path.join(DATA_DIR, "scores.json")
_lock = threading.Lock()
_data = None
MIN_EVIDENCE = 4  # results needed before a model's score changes its place in automatic picks

# outcome -> (signal, good?) ; each signal is a rate of good outcomes, smoothed so a few results don't swing it
OUTCOMES = {
    "job_done": ("jobs", True), "job_failed": ("jobs", False),
    "check_ok": ("checks", True), "check_bad": ("checks", False),
    "test_pass": ("tests", True), "test_fail": ("tests", False),
    "tool_ok": ("tools", True), "tool_error": ("tools", False),
    "kept": ("kept", True), "undone": ("kept", False),
    "attempt_won": ("attempts", True), "attempt_lost": ("attempts", False),
    "bench_pass": ("bench", True), "bench_fail": ("bench", False),
}
WEIGHTS = {"jobs": 2.0, "checks": 1.0, "tests": 2.0, "tools": 1.0, "kept": 2.0, "attempts": 3.0, "bench": 3.0}
LABELS = {"jobs": "Jobs finished", "checks": "Edits that pass checks", "tests": "Test runs passing", "tools": "Tool calls right",
          "kept": "Work kept", "attempts": "Parallel attempts won", "bench": "Benchmark tasks"}


def _load():
    global _data
    if _data is None:
        try:
            with open(STORE, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        _data = data if isinstance(data, dict) else {}
        _data.setdefault("models", {})
        _data.setdefault("bench", {})
    return _data


def _save():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STORE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_load(), fh, indent=1)
        os.replace(tmp, STORE)
    except OSError:
        pass


def reset_state():
    global _data
    with _lock:
        _data = {"models": {}, "bench": {}}
        _save()


def record(model, outcome, count=1, save=True):
    """Count one outcome ("test_pass", "undone"...) for a model ref."""
    if not model or outcome not in OUTCOMES:
        return
    with _lock:
        entry = _load()["models"].setdefault(model, {})
        entry[outcome] = entry.get(outcome, 0) + count
        entry["updated"] = time.time()
        if save:
            _save()


def tool_result(model, name, ok, text=""):
    """What a tool call says about the model that made it."""
    if not model:
        return
    text = text or ""
    if name in ("edit_file", "write_file", "apply_patch"):
        record(model, "check_bad" if "PROBLEMS FOUND" in text else "check_ok" if ok else "tool_error", save=False)
    elif name == "run_tests":
        record(model, "test_pass" if ok else "test_fail", save=False)
    elif not ok and (text.startswith("Error: bad arguments") or text.startswith("Unknown tool") or "could not be read as JSON" in text):
        record(model, "tool_error", save=False)
    else:
        record(model, "tool_ok", save=False)
    with _lock:
        entry = _load()["models"].get(model) or {}
        if sum(v for k, v in entry.items() if k != "updated") % 5 == 0:
            _save()


def _signals(entry):
    out = {}
    for outcome, (signal, good) in OUTCOMES.items():
        n = entry.get(outcome, 0)
        if n:
            s = out.setdefault(signal, [0, 0])
            s[0 if good else 1] += n
    return out


def score(model):
    """(score 0..1, evidence): the weighted, smoothed share of good outcomes; 0.5 means unknown."""
    entry = _load()["models"].get(model) or {}
    total_w, acc, evidence = 0.0, 0.0, 0
    for signal, (good, bad) in _signals(entry).items():
        n = good + bad
        evidence += n
        rate = (good + 1) / (n + 2)  # Laplace: one good and one bad result assumed before any data
        weight = WEIGHTS[signal] * min(1.0, n / 5)  # a signal counts fully from five results on
        acc += rate * weight
        total_w += weight
    return (acc / total_w if total_w else 0.5), evidence


def rank(models):
    """Models ordered by how well they work here; models without enough results keep their place among the unknown
    (0.55: a proven model wins over an untried one, an untried one over a proven failure). Stable."""
    def key(item):
        i, m = item
        s, n = score(m)
        return (-(s if n >= MIN_EVIDENCE else 0.55), i)
    return [m for _, m in sorted(enumerate(models), key=key)]


def board():
    """Every model with results, best first: score, evidence and each signal's rate."""
    from . import llm
    rows = []
    for model, entry in _load()["models"].items():
        s, n = score(model)
        if not n:
            continue
        signals = {k: {"good": g, "bad": b, "rate": round(g / (g + b), 3), "label": LABELS[k]} for k, (g, b) in _signals(entry).items()}
        rows.append({"model": model, "label": llm.label(model), "score": round(s, 3), "evidence": n, "signals": signals,
                     "updated": entry.get("updated", 0), "bench": _load()["bench"].get(model)})
    rows.sort(key=lambda r: (-(r["score"] if r["evidence"] >= MIN_EVIDENCE else 0.0), -r["evidence"]))
    return rows


def save_bench(model, result):
    with _lock:
        _load()["bench"][model] = result
        _save()


def forget(model=""):
    with _lock:
        data = _load()
        if model:
            data["models"].pop(model, None)
            data["bench"].pop(model, None)
        else:
            data["models"].clear()
            data["bench"].clear()
        _save()
