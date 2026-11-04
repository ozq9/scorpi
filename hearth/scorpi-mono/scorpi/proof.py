"""Proof of work: at the end of a coding run, what was actually verified and what was not.

Only evidence from after the last change counts: tests that passed before the final edit prove nothing about the
final code. The card lists the checks that ran (tests, approved tests, syntax checks, the page in a browser, a review by
a second model, the user's hooks), whether they passed, and says plainly what nobody checked, so "done!" is never just
the model's word.
"""

import os
import re
import threading

from .contract import EDIT_TOOLS, is_test_file, targets

DOCS = re.compile(r"\.(md|markdown|txt|rst|adoc)$|(^|/)(LICENSE|CHANGELOG|AUTHORS)[^/]*$", re.I)
WEB = re.compile(r"\.(html?|css|scss|sass|less|jsx?|tsx?|mjs|vue|svelte|astro)$", re.I)
# how test runners say how many tests ran, most telling first
COUNTS = [re.compile(p, re.M) for p in (r"\d+ passed[^\n=]*", r"\d+ failed[^\n=]*", r"Ran \d+ tests?", r"Tests?:\s+\d+[^\n]*",
                                         r"\d+ tests?,? \d+ (?:failures?|passed)[^\n]*", r"FAILED \([^)]*\)")]


def _first(text, n=160):
    line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
    return line[:n]


def _counts(text):
    for rx in COUNTS:
        found = rx.findall(str(text or ""))
        if found:
            return found[-1].strip(" ,;")[:120]
    return ""


def _command(args, text):
    """The test command: as given, else as the run_tests output names it ("pytest -q: PASSED in 2s")."""
    given = str((args or {}).get("command") or "").strip()
    if given:
        return given
    m = re.match(r"\s*(.+?): (?:PASSED|FAILED|passed|failed)", str(text or ""))
    return m.group(1) if m else _first(text, 80)


class Ledger:
    """What happened in one run, in order."""

    def __init__(self, workdir=""):
        self.workdir = workdir
        self.seq = 0
        self.last_edit = -1
        self.last_web_edit = -1
        self.changed = {}  # rel path -> seq of its last change
        self.checks = {}  # rel path -> (seq, ok) of the syntax check after its last change
        self.tests = []  # (seq, ok, command, counts)
        self.visual = []  # (seq, ok, display)
        self.reviews = []  # seq
        self.hooks = []  # (seq, ok, label)
        self.contract = None  # (ok, files)
        self.commands = [0, 0]  # ok, failed
        self.lock = threading.Lock()  # helpers working in parallel share the job's ledger

    def _rel(self, path):
        try:
            return os.path.relpath(path, self.workdir).replace("\\", "/") if self.workdir else path
        except ValueError:
            return path

    def tool(self, name, args, ok, text, display=None):
        with self.lock:
            self._tool(name, args, ok, text, display)

    def _tool(self, name, args, ok, text, display):
        self.seq += 1
        if name in EDIT_TOOLS and ok:
            for path in targets(name, args or {}, self.workdir or "."):
                rel = self._rel(path)
                self.changed[rel] = self.seq
                if not DOCS.search(rel):
                    self.last_edit = self.seq
                if WEB.search(rel):
                    self.last_web_edit = self.seq
                if "PROBLEMS FOUND" in (text or ""):
                    self.checks[rel] = (self.seq, False)
                elif "Checks passed" in (text or ""):
                    self.checks[rel] = (self.seq, True)
        elif name in EDIT_TOOLS and "PROBLEMS FOUND" in (text or ""):
            for path in targets(name, args or {}, self.workdir or "."):
                self.checks[self._rel(path)] = (self.seq, False)
        elif name == "run_tests":
            self.tests.append((self.seq, ok, _command(args, text), _counts(text)))
        elif name == "check_code":
            for rel in list(self.checks):
                if ok:
                    self.checks[rel] = (self.seq, True)
        elif name == "visual_check":
            self.visual.append((self.seq, ok, display or {}))
        elif name == "review_changes" and ok:
            self.reviews.append(self.seq)
        elif name == "run_shell":
            self.commands[0 if ok else 1] += 1

    def hook(self, ok, label):
        self.seq += 1
        self.hooks.append((self.seq, ok, label))

    def approved_tests(self, ok, files):
        self.seq += 1
        self.contract = (ok, list(files or []))

    # ---------- the card ----------

    def card(self, has_tests=None):
        """The proof card ({"type": "proof", ...}), or None when the run changed no files."""
        if not self.changed:
            return None
        items, unverified = [], []
        code_changed = self.last_edit >= 0
        after = [t for t in self.tests if t[0] > self.last_edit]
        if self.contract:
            ok, files = self.contract
            items.append({"kind": "contract", "ok": ok, "text": ("The approved tests pass" if ok else "The approved tests still fail") +
                          " (%s)" % ", ".join(files[:4])})
        if after:
            seq, ok, command, counts = after[-1]
            items.append({"kind": "tests", "ok": ok, "text": ("Tests pass" if ok else "Tests fail") + (": " + counts if counts else "") + " · " + command[:80]})
        elif code_changed:
            if self.tests:
                unverified.append("The tests ran before the last change, not after it")
            elif has_tests is False:
                unverified.append("This project has no tests")
            elif not self.contract:
                unverified.append("No tests ran after the last change")
        bad = sorted(rel for rel, (seq, ok) in self.checks.items() if not ok)
        good = [rel for rel, (seq, ok) in self.checks.items() if ok]
        if bad:
            items.append({"kind": "checks", "ok": False, "text": "Syntax problems left in " + ", ".join(bad[:4])})
        elif good:
            items.append({"kind": "checks", "ok": True, "text": "Syntax checks pass (%d file%s)" % (len(good), "" if len(good) == 1 else "s")})
        if self.last_web_edit >= 0:
            seen = [v for v in self.visual if v[0] > self.last_web_edit]
            if seen:
                seq, ok, display = seen[-1]
                problems = len(display.get("errors") or []) + len(display.get("failed") or [])
                items.append({"kind": "page", "ok": ok, "text": "The page opens without errors" if ok else "The page has %d problem%s" % (problems, "" if problems == 1 else "s"),
                              "image": display.get("after", "")})
            else:
                unverified.append("The page was not opened in a browser after the last change")
        if self.reviews and self.reviews[-1] > self.last_edit:
            items.append({"kind": "review", "ok": True, "text": "Reviewed by a second model"})
        for seq, ok, label in self.hooks[-3:]:
            items.append({"kind": "hook", "ok": ok, "text": "Your check %s %s" % (label, "passed" if ok else "failed")})
        failing = any(not i["ok"] for i in items)
        proven = any(i["ok"] and i["kind"] in ("tests", "contract") for i in items)
        verdict = "failing" if failing else "verified" if proven and not unverified else "partial" if items else "unverified"
        files = sorted(self.changed, key=lambda r: self.changed[r])
        return {"type": "proof", "verdict": verdict, "items": items, "unverified": unverified, "files": files[-30:], "count": len(files),
                "tests_changed": [f for f in files if is_test_file(f)][:10]}


HEADLINE = {"verified": "Verified", "partial": "Partly verified", "unverified": "Not verified", "failing": "Checks fail"}


def summary(card):
    """One line for reports and notifications."""
    if not card:
        return ""
    good = [i["text"] for i in card["items"] if i["ok"]]
    bad = [i["text"] for i in card["items"] if not i["ok"]]
    parts = [HEADLINE[card["verdict"]] + ":"]
    if bad:
        parts.append("; ".join(bad) + ".")
    if good:
        parts.append("; ".join(good) + ".")
    if card["unverified"]:
        parts.append("Not checked: " + "; ".join(card["unverified"]).lower() + ".")
    return " ".join(parts)
