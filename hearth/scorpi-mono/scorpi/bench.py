"""Test my free models: five small, real coding tasks (fix a bug, follow a spec, rename across files, parse text,
extend a command-line tool) given to each model in a folder of its own. Hidden tests decide pass or fail, so the
result is about working code, not how good the answer sounds. Results go to the scoreboard (scorpi/scores.py) and
steer the automatic coding model. Free model lineups change every few weeks: run it again when yours change.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback

from . import events, llm, scores
from .config import DATA_DIR, IS_WINDOWS

ROOT = os.path.join(DATA_DIR, "bench")
TASK_SECONDS = 240
MAX_STEPS = 14

TASKS = [
    {
        "id": "bugfix",
        "title": "Fix a bug",
        "prompt": "stats.py has a bug: median() is wrong for lists with an even number of values. Fix it. An empty list should "
                  "raise ValueError.",
        "files": {"stats.py": '''def median(values):
    """The middle value of a list of numbers (the mean of the two middle ones for an even count)."""
    ordered = sorted(values)
    return ordered[len(ordered) // 2]
'''},
        "test": '''import unittest
from stats import median


class T(unittest.TestCase):
    def test_odd(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_even(self):
        self.assertEqual(median([4, 1, 3, 2]), 2.5)

    def test_floats(self):
        self.assertEqual(median([0.5, -1.0]), -0.25)

    def test_empty(self):
        with self.assertRaises(ValueError):
            median([])
''',
        "solution": {"stats.py": '''def median(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("median of an empty list")
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
'''},
    },
    {
        "id": "spec",
        "title": "Follow a spec",
        "prompt": "Create text.py with a function slugify(text) that turns a title into a URL slug: lower case; accented letters "
                  "become plain ones (é becomes e); every run of characters that are not a-z or 0-9 becomes a single hyphen; no "
                  "hyphen at the start or end. Example: slugify('  Héllo, World! 2024 ') returns 'hello-world-2024'.",
        "files": {},
        "test": '''import unittest
from text import slugify


class T(unittest.TestCase):
    def test_cases(self):
        for given, want in [("  Héllo, World! 2024 ", "hello-world-2024"), ("Crème brûlée", "creme-brulee"), ("a--b__c", "a-b-c"),
                            ("!!!", ""), ("Already-a-slug", "already-a-slug"), ("Ünïcödé text", "unicode-text")]:
            self.assertEqual(slugify(given), want, given)
''',
        "solution": {"text.py": '''import re
import unicodedata


def slugify(text):
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
'''},
    },
    {
        "id": "rename",
        "title": "Change several files",
        "prompt": "Rename get_total to compute_total everywhere in this project (util.py and app.py), and make compute_total "
                  "ignore negative prices. app.run must keep working.",
        "files": {"util.py": '''def get_total(prices, tax=0.0):
    return round(sum(prices) * (1 + tax), 2)
''', "app.py": '''from util import get_total


def run(cart):
    return "Total: %.2f" % get_total([item["price"] * item.get("qty", 1) for item in cart], tax=0.2)
'''},
        "test": '''import unittest

import app
import util


class T(unittest.TestCase):
    def test_renamed(self):
        self.assertFalse(hasattr(util, "get_total"))
        self.assertEqual(util.compute_total([10, 5]), 15)

    def test_negative_prices_ignored(self):
        self.assertEqual(util.compute_total([10, -5]), 10)

    def test_tax(self):
        self.assertEqual(util.compute_total([10], tax=0.2), 12.0)

    def test_app(self):
        self.assertEqual(app.run([{"price": 2.5, "qty": 2}, {"price": -1}]), "Total: 6.00")
''',
        "solution": {"util.py": '''def compute_total(prices, tax=0.0):
    return round(sum(p for p in prices if p >= 0) * (1 + tax), 2)
''', "app.py": '''from util import compute_total


def run(cart):
    return "Total: %.2f" % compute_total([item["price"] * item.get("qty", 1) for item in cart], tax=0.2)
'''},
    },
    {
        "id": "parse",
        "title": "Parse text",
        "prompt": "Create logs.py with count_levels(text). Each log line looks like '2026-01-05 12:00:01 [LEVEL] message'. Return a "
                  "dict mapping each level, in upper case, to the number of lines with it. Skip blank and malformed lines. Levels "
                  "can be written in any case ([warn] counts as WARN); only the first [LEVEL] after the time counts.",
        "files": {},
        "test": '''import unittest
from logs import count_levels

TEXT = """2026-01-05 12:00:01 [INFO] start
2026-01-05 12:00:02 [warn] disk almost full

garbage line [ERROR]
2026-01-05 12:00:03 [ERROR] failed [INFO] retry
2026-01-05 12:00:04 [Info] done
"""


class T(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(count_levels(TEXT), {"INFO": 2, "WARN": 1, "ERROR": 1})

    def test_empty(self):
        self.assertEqual(count_levels(""), {})
''',
        "solution": {"logs.py": '''import re

LINE = re.compile(r"^\\d{4}-\\d\\d-\\d\\d \\d\\d:\\d\\d:\\d\\d \\[([A-Za-z]+)\\]")


def count_levels(text):
    counts = {}
    for line in text.splitlines():
        m = LINE.match(line.strip())
        if m:
            level = m.group(1).upper()
            counts[level] = counts.get(level, 0) + 1
    return counts
'''},
    },
    {
        "id": "cli",
        "title": "Extend a tool",
        "prompt": "Add two options to greet.py: --shout prints the greeting in upper case, and --times N (default 1) prints it N "
                  "times, one per line. The normal output must stay exactly the same.",
        "files": {"greet.py": '''import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(description="Say hello.")
    parser.add_argument("name")
    args = parser.parse_args(argv)
    print("Hello, %s!" % args.name)


if __name__ == "__main__":
    main()
'''},
        "test": '''import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def greet(*args):
    out = subprocess.run([sys.executable, os.path.join(HERE, "greet.py"), *args], capture_output=True, text=True, timeout=30)
    return out.stdout.replace("\\r\\n", "\\n")


class T(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(greet("Ana"), "Hello, Ana!\\n")

    def test_shout(self):
        self.assertEqual(greet("Ana", "--shout"), "HELLO, ANA!\\n")

    def test_times(self):
        self.assertEqual(greet("Bo", "--times", "2"), "Hello, Bo!\\nHello, Bo!\\n")

    def test_both(self):
        self.assertEqual(greet("--shout", "--times", "3", "Cy"), "HELLO, CY!\\n" * 3)
''',
        "solution": {"greet.py": '''import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(description="Say hello.")
    parser.add_argument("name")
    parser.add_argument("--shout", action="store_true")
    parser.add_argument("--times", type=int, default=1)
    args = parser.parse_args(argv)
    text = "Hello, %s!" % args.name
    for _ in range(args.times):
        print(text.upper() if args.shout else text)


if __name__ == "__main__":
    main()
'''},
    },
]
HIDDEN = "test_hidden_check.py"

_lock = threading.Lock()
STATE = {"running": False, "models": [], "done": 0, "total": 0, "current": "", "results": {}, "started": 0, "finished": 0, "stop": False}


class BenchRun:
    """What an Agent needs from a run, for a benchmark task nobody watches: approvals are declined, not asked."""
    unattended = True

    def __init__(self, model):
        self.id = "bench"
        self.conv_id = ""
        self.origin = "bench"
        self.inbox = []
        self.cancelled = False
        self.model = model

    def emit(self, event):
        pass


def python():
    exe = sys.executable or "python"
    if IS_WINDOWS and exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    return exe


def prepare(folder, task):
    os.makedirs(folder, exist_ok=True)
    for name, body in task["files"].items():
        with open(os.path.join(folder, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)


def verify(folder, task):
    """Run the hidden tests in the folder: (passed, output)."""
    with open(os.path.join(folder, HIDDEN), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(task["test"])
    for stale in ("__pycache__",):
        shutil.rmtree(os.path.join(folder, stale), ignore_errors=True)
    try:
        out = subprocess.run([python(), "-m", "unittest", HIDDEN[:-3]], cwd=folder, capture_output=True, text=True, timeout=90,
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"))
    except subprocess.TimeoutExpired:
        return False, "The tests took longer than 90 s."
    return out.returncode == 0, (out.stdout + out.stderr)[-3000:]


def run_task(model, task, folder):
    """One model, one task: {"pass", "seconds", "steps", "note"}; "skipped" when the model was out of requests."""
    from .agent import Agent
    prepare(folder, task)
    run = BenchRun(model)
    timer = threading.Timer(TASK_SECONDS, lambda: setattr(run, "cancelled", True))
    timer.daemon = True
    started = time.time()
    agent = Agent(run, skill="code", groups=["code", "files"], pin_model=model, max_steps=MAX_STEPS, auto_approve=True,
                  persona={"name": "Coder", "instructions": "", "workdir": folder, "project": ""}, workdir=folder)
    note = ""
    timer.start()
    try:
        with llm.pinned():
            agent.run_turn([{"role": "user", "content": task["prompt"]}], task["prompt"])
    except llm.LLMError as exc:
        timer.cancel()
        if exc.status in (402, 429) or exc.resume_at or re.search(r"rate.?limit|quota|too many", str(exc), re.I):
            return {"skipped": True, "note": "out of requests: " + str(exc)[:160], "seconds": round(time.time() - started, 1)}
        note = str(exc)[:200]
    except Exception as exc:  # noqa: BLE001 - one task failing must not stop the benchmark
        traceback.print_exc()
        note = "%s: %s" % (type(exc).__name__, exc)
    finally:
        timer.cancel()
    passed, output = verify(folder, task)
    steps = sum(1 for p in agent.parts if p.get("type") == "tool")
    if run.cancelled and not passed:
        note = note or "ran out of time (%d s)" % TASK_SECONDS
    return {"pass": passed, "seconds": round(time.time() - started, 1), "steps": steps, "note": note or ("" if passed else output[-400:])}


def status():
    with _lock:
        return {k: v for k, v in STATE.items() if k != "stop"}


def _publish():
    events.publish("bench", status())


def start(models):
    models = list(dict.fromkeys(m for m in models or [] if m))[:6]
    if not models:
        raise ValueError("Pick at least one model.")
    with _lock:
        if STATE["running"]:
            raise ValueError("A benchmark is already running.")
        STATE.update(running=True, models=models, done=0, total=len(models) * len(TASKS), current="", started=time.time(),
                     finished=0, stop=False, results={m: {} for m in models})
    threading.Thread(target=_work, args=(models,), daemon=True, name="bench").start()
    _publish()
    return status()


def stop():
    with _lock:
        STATE["stop"] = True


def _work(models):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        for model in models:
            passed = total = 0
            seconds = []
            for task in TASKS:
                with _lock:
                    if STATE["stop"]:
                        break
                    STATE["current"] = "%s · %s" % (llm.label(model), task["title"])
                _publish()
                folder = os.path.join(ROOT, stamp, re.sub(r"[^A-Za-z0-9.-]+", "_", model).strip("_"), task["id"])
                result = run_task(model, task, folder)
                shutil.rmtree(folder, ignore_errors=True)
                if not result.get("skipped"):
                    total += 1
                    passed += 1 if result["pass"] else 0
                    seconds.append(result["seconds"])
                    scores.record(model, "bench_pass" if result["pass"] else "bench_fail")
                with _lock:
                    STATE["results"][model][task["id"]] = dict(result, title=task["title"])
                    STATE["done"] += 1
                _publish()
            if total:
                scores.save_bench(model, {"passed": passed, "total": total, "at": time.time(),
                                          "seconds": round(sum(seconds) / len(seconds), 1) if seconds else 0})
    finally:
        shutil.rmtree(os.path.join(ROOT, stamp), ignore_errors=True)
        with _lock:
            STATE.update(running=False, current="", finished=time.time())
        _publish()


def suggest():
    """Models worth testing: the coding pick of every provider set up, the coding model first."""
    picks = []
    chosen = llm.coding_model()
    if chosen:
        picks.append(chosen)
    picks.extend(llm.code_candidates())
    return list(dict.fromkeys(picks))
