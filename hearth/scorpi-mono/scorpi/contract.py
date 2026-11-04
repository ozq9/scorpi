"""Tests first: the agent turns the request into failing tests, the user approves them as the contract, and the agent
then builds until exactly those tests pass. Approved test files are locked: an edit is refused, a change made another
way (a shell command) is put back, and a change the agent thinks is needed goes to the user (propose_test_change).
The work only counts as done when the approved tests pass.
"""

import hashlib
import os
import re

TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|specs?|e2e|testing)/|(^|/)test_[^/]*\.py$|(^|/)[^/]*_test\.(py|go|rs|rb|exs?)$|"
                       r"\.(test|spec)\.[cm]?[jt]sx?$|(^|/)[^/]*Tests?\.(java|kt|cs|swift)$|_spec\.rb$|(^|/)conftest\.py$", re.I)
EDIT_TOOLS = {"write_file", "edit_file", "apply_patch", "manage_path"}

TESTS_MODE = """TESTS FIRST — the user wants to agree on tests before anything is built.
1. Look around (repo_map, grep, read_file...) to see how the project is organised and tested. Use its test framework; if it has none, pick the standard one for the language (unittest/pytest for Python, the runner in package.json, go test...).
2. Write tests that pin down the behaviour the user asked for: the main cases, edge cases and error cases, with clear names. Only test files may change now: no implementation yet.
3. Run them with run_tests: they must FAIL, and for the right reason (the feature is missing, not a typo in the test).
4. Call submit_tests with the test files, the exact command that runs them and a short Markdown summary of what they check. The user approves them, asks for changes (revise and submit again) or rejects them."""


def is_test_file(path, root=""):
    rel = os.path.relpath(path, root) if root and os.path.isabs(path) else str(path)
    return bool(TEST_FILE.search(rel.replace("\\", "/")))


def _digest(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def snapshot(files, root):
    """{rel path: content} of the approved test files."""
    out = {}
    for rel in files:
        path = rel if os.path.isabs(rel) else os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                out[os.path.relpath(path, root).replace("\\", "/")] = fh.read()
        except OSError:
            pass
    return out


def targets(name, args, root):
    """Files a tool call would change (absolute paths)."""
    args = args or {}
    rels = []
    if name in ("write_file", "edit_file"):
        rels = [args.get("path")]
    elif name == "manage_path":
        rels = [args.get("path"), args.get("destination") or args.get("dest") or args.get("target")]
    elif name == "apply_patch":
        rels = [a or b for a, b in re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^(?:\+\+\+|---) [ab]/(.+)$", str(args.get("patch") or ""), re.M)]
    return [os.path.normpath(r if os.path.isabs(r) else os.path.join(root, r)) for r in rels if r]


class Contract:
    """The approved tests of one job: what they are, how to run them, and their approved content."""

    def __init__(self, files, command, summary, root, content=None):
        self.root = root
        self.command = command
        self.summary = summary
        self.content = content or snapshot(files, root)
        self.files = sorted(self.content)
        self.rounds = 0  # times the final check sent the agent back to work

    def to_dict(self):
        return {"files": self.files, "command": self.command, "summary": self.summary, "content": self.content}

    @classmethod
    def from_dict(cls, data, root):
        return cls(data.get("files") or [], data.get("command") or "", data.get("summary") or "", root, data.get("content") or {})

    def locked(self, path):
        rel = os.path.relpath(os.path.normpath(path), self.root).replace("\\", "/")
        return rel in self.content

    def tampered(self):
        """Approved test files that no longer match what was approved: put back. Returns their paths."""
        changed = []
        for rel, text in self.content.items():
            path = os.path.join(self.root, rel)
            try:
                with open(path, encoding="utf-8", newline="") as fh:
                    current = fh.read()
            except OSError:
                current = None
            if current is None or _digest(current) != _digest(text):
                os.makedirs(os.path.dirname(path) or self.root, exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
                changed.append(rel)
        return changed

    def amend(self, rel, text):
        """The user agreed to change an approved test."""
        rel = rel.replace("\\", "/")
        self.content[rel] = text
        self.files = sorted(self.content)
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path) or self.root, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)


def review_markdown(summary, content, command):
    """What the user reads before approving: the summary, the command and every test file."""
    parts = [summary.strip(), "", "**Runs with** `%s`" % command if command else ""]
    for rel, text in content.items():
        ext = os.path.splitext(rel)[1].lstrip(".") or "text"
        lang = {"py": "python", "js": "javascript", "ts": "typescript", "rb": "ruby", "rs": "rust"}.get(ext, ext)
        shown = text if len(text) < 12000 else text[:12000] + "\n… (cut)"
        parts += ["", "### %s" % rel, "```%s\n%s\n```" % (lang, shown.rstrip())]
    return "\n".join(parts).strip()
