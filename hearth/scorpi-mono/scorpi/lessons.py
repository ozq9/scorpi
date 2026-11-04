"""Learning from corrections. When the user rejects a plan or tests and says why, undoes an agent's run and says why,
or corrects the agent ("don't use jQuery", "always run the linter"), Scorpi turns it into one short project rule and
offers to add it to the project's AGENTS.md. Every coding agent reads that file (Scorpi's, and Claude Code, Codex,
Cursor...), so the same mistake is not made twice, and the user can read, edit and share what was learned.
"""

import json
import os
import re
import secrets
import threading
import time

from . import events
from .config import DATA_DIR

STORE = os.path.join(DATA_DIR, "lessons.json")
SECTION = "## Learned from your corrections"
_lock = threading.Lock()

CORRECTION = re.compile(
    r"\b(don'?t|do not|never|always|stop (?:using|doing|adding)|instead of|rather than|not like that|that'?s wrong|wrong|"
    r"prefer|should(?:n'?t| not| always)|must(?:n'?t| not)?|no more|avoid|please use|use \S+ instead|keep using|from now on)\b",
    re.I)
PROMPT = """A user corrected an AI coding agent working on their project. Their words ({source}):
---
{text}
---
If this states a lasting preference or rule for working on this project (a technology to use or avoid, a style, a
process, a constraint), rewrite it as ONE short imperative rule for the project's AGENTS.md, under 25 words, general
enough to apply to future tasks (e.g. "Store data in SQLite, not JSON files." or "Run the linter before finishing.").
If it is only about this one task, a question, or not a correction, answer exactly NONE.
Rules already in the file:
{existing}
If the rule is already covered there, answer NONE. Reply with the rule or NONE only."""


def _load():
    try:
        with open(STORE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as fh:
        json.dump(items[-100:], fh, indent=1)


def pending(slug=""):
    return [i for i in _load() if not slug or i["slug"] == slug]


def rules_path(folder):
    return os.path.join(folder, "AGENTS.md")


def existing_rules(folder):
    try:
        with open(rules_path(folder), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    return [ln[2:].strip() for ln in text.splitlines() if ln.startswith("- ")]


def looks_like_correction(text):
    text = str(text or "").strip()
    return 6 <= len(text) <= 2000 and bool(CORRECTION.search(text))


def _similar(a, b):
    import difflib
    a, b = re.sub(r"\W+", " ", a.lower()).strip(), re.sub(r"\W+", " ", b.lower()).strip()
    return a == b or a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() > 0.82


def write_rule(text, source, folder, model=""):
    """The rule for AGENTS.md, from the model (fast model), or None when it is not a lasting rule."""
    from . import llm
    existing = existing_rules(folder)
    try:
        raw = llm.complete(PROMPT.format(source=source, text=text[:2000], existing="\n".join("- " + r for r in existing[-40:]) or "(none)"),
                           model=model or None, max_chars=400)
    except Exception:  # noqa: BLE001 - no model: the user's own words, when short, are the rule
        raw = text if len(text) < 160 and not text.endswith("?") else "NONE"
    rule = raw.strip().strip('"').strip("`").splitlines()[0].strip() if raw.strip() else "NONE"
    rule = re.sub(r"^[-*•]\s*", "", rule)
    if not rule or rule.upper().startswith("NONE") or len(rule) > 240:
        return None
    if any(_similar(rule, r) for r in existing):
        return None
    return rule


def consider(slug, folder, text, source, conv_id="", wait=False):
    """Maybe offer a rule from a correction (in the background unless wait). Returns the suggestion when wait."""
    if not slug or not folder or not str(text or "").strip():
        return None
    from . import privacy

    def work():
        with privacy.scope(privacy.project_private(slug)):
            rule = write_rule(str(text), source, folder)
        if not rule:
            return None
        with _lock:
            items = _load()
            if any(i["slug"] == slug and _similar(i["rule"], rule) for i in items):
                return None
            item = {"id": secrets.token_hex(4), "slug": slug, "rule": rule, "source": source, "said": str(text)[:400],
                    "conv_id": conv_id, "created": time.time()}
            items.append(item)
            _save(items)
        events.publish("lesson", item)
        return item

    if wait:
        return work()
    threading.Thread(target=work, daemon=True, name="lesson").start()
    return None


def accept(lesson_id, folder, rule=""):
    """Add the rule to the project's AGENTS.md (under its own section). Returns the file's path."""
    with _lock:
        items = _load()
        item = next((i for i in items if i["id"] == lesson_id), None)
        if not item:
            raise ValueError("That suggestion is gone.")
        items.remove(item)
        _save(items)
    rule = " ".join(str(rule or item["rule"]).split())[:240]
    if not rule:
        raise ValueError("Write the rule.")
    path = rules_path(folder)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        text = "# AGENTS.md\n\nInstructions for coding agents working on this project.\n"
    line = "- %s" % rule
    if SECTION in text:
        head, _, tail = text.partition(SECTION)
        body, sep, rest = tail.partition("\n## ")
        body = body.rstrip() + "\n" + line + "\n"
        text = head + SECTION + body + (("\n## " + rest) if sep else "")
    else:
        text = text.rstrip() + "\n\n" + SECTION + "\n" + line + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    events.publish("lesson_done", {"id": lesson_id, "slug": item["slug"], "rule": rule, "added": True})
    return path


def dismiss(lesson_id):
    with _lock:
        items = _load()
        item = next((i for i in items if i["id"] == lesson_id), None)
        if item:
            items.remove(item)
            _save(items)
    if item:
        events.publish("lesson_done", {"id": lesson_id, "slug": item["slug"], "added": False})
