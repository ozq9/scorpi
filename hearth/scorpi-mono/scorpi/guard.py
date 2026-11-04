"""Notices when an agent goes in circles: the same error coming back after every fix, a file edited back to how it
was, or one failure after another. The agent is told to step back and change approach; if that does not help, its
work moves to a stronger model (scorpi/agent.py) or it stops and reports instead of burning the user's free requests.
"""

import hashlib
import os
import re

WATCHED = {"run_shell", "run_tests", "run_python", "run_javascript", "check_code", "edit_file", "write_file", "apply_patch",
           "start_process", "visual_check"}
EDITS = {"edit_file", "write_file", "apply_patch"}


def signature(text):
    """The gist of an error, comparable across runs: its most telling line with numbers, paths and addresses masked."""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    telling = [ln for ln in lines if re.search(r"error|exception|failed|fail:|cannot|can't|not found|undefined|refused|denied|problems found", ln, re.I)]
    pick = (telling or lines or [""])[-1]
    pick = re.sub(r"0x[0-9a-f]+", "#", pick, flags=re.I)
    pick = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s:'\"]+", "<path>", pick)
    pick = re.sub(r"\d+(\.\d+)?", "#", pick)
    return pick[:200]


def _digest(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return ""


class LoopGuard:
    def __init__(self):
        self.errors = {}  # signature -> times seen
        self.files = {}  # path -> digests after each edit
        self.failing = 0  # failed calls in a row
        self.nudged = set()
        self.loops = 0  # loops noticed this turn

    def observe(self, name, args, ok, text, workdir=""):
        """After a tool call: None, or (level, message) with level "nudge" (change approach) or "stuck"."""
        self.failing = 0 if ok else self.failing + 1
        found = None
        if not ok and name in WATCHED:
            sig = name + ":" + signature(text)
            self.errors[sig] = self.errors.get(sig, 0) + 1
            seen = self.errors[sig]
            if seen == 3 and sig not in self.nudged:
                self.nudged.add(sig)
                found = ("The same error came back %d times:\n  %s\nYour fixes are not reaching the cause." % (seen, signature(text)))
            elif seen >= 5:
                return self._stuck("The same error came back %d times:\n  %s" % (seen, signature(text)))
        elif ok and name in ("run_tests", "check_code"):
            self.errors = {k: v for k, v in self.errors.items() if not k.startswith(name + ":")}
        if ok and name in EDITS:
            for path in self._paths(name, args, workdir):
                digest = _digest(path)
                history = self.files.setdefault(path, [])
                if digest and history and digest != history[-1] and digest in history[:-1]:
                    found = found or ("You changed %s back to how it was a few edits ago: you are going back and forth."
                                      % os.path.basename(path))
                if digest:
                    history.append(digest)
        if not found and self.failing >= 6 and "streak" not in self.nudged:
            self.nudged.add("streak")
            found = "Your last %d tool calls all failed." % self.failing
        if not found:
            return None
        self.loops += 1
        if self.loops >= 3:
            return self._stuck(found)
        return ("nudge", "[Scorpi noticed a loop] %s Stop repeating it. Step back: re-read the code involved and the full error, "
                         "write down a different explanation of the cause, check it with a small experiment (a print, a "
                         "minimal test) and only then change code. ask_expert can give a second opinion." % found)

    def _stuck(self, why):
        return ("stuck", "[Scorpi: you are stuck] %s" % why)

    @staticmethod
    def _paths(name, args, workdir):
        args = args or {}
        if name == "apply_patch":
            rels = re.findall(r"^\*\*\* (?:Add|Update) File: (.+)$|^\+\+\+ b/(.+)$", str(args.get("patch") or ""), re.M)
            rels = [a or b for a, b in rels]
        else:
            rels = [args.get("path")] if args.get("path") else []
        out = []
        for rel in rels:
            rel = str(rel).strip()
            out.append(rel if os.path.isabs(rel) else os.path.join(workdir or os.getcwd(), rel))
        return out
