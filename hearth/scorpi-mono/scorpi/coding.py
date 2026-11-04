"""Coding helpers shared by the agents' tools, the app and the terminal: a second model for independent opinions and
code review, the diff a review looks at, and the project commands (/init, /review, /test, /fix...)."""

import os
import re

from . import gitops, llm, usage
from .config import SETTINGS

EXPERT_SYSTEM = """You are a principal software engineer asked for a second opinion by another AI coding agent. You see only what it
sends you. Be concrete and direct: point at the exact cause, give the fix as code when that is clearer, and say what you
are unsure about. No preamble, no flattery. Keep it as short as the problem allows."""

REVIEW_PROMPT = """Review these code changes like a careful senior engineer reviewing a pull request.

Look for, in this order: bugs and wrong behaviour (logic errors, off-by-one, missing awaits, wrong conditions, unhandled
errors and edge cases), security problems (injection, secrets in code, unsafe input handling, path traversal), data
loss or breaking changes, performance traps, then maintainability. Ignore pure style nits unless they hide a bug.

Answer in Markdown. Start with one line: the verdict ("Looks good", "Minor issues" or "Needs changes"). Then one bullet
per finding, most severe first:
- **critical|major|minor** `path:line`: the problem, why it matters, and the fix.
If there is nothing worth fixing, say so after the verdict. Do not repeat the diff.
%s
--- changes ---
%s
--- end ---"""

# Project commands: typed as /name in a project's chat (and in the terminal), sent to the coder as these instructions.
COMMANDS = {
    "init": {
        "description": "Write AGENTS.md: how to build, test and work in this project, for every coding agent",
        "icon": "doc",
        "prompt": "Create or update AGENTS.md at the root of this project: the instructions coding agents (you, Claude Code, Codex, "
                  "Cursor...) read before working here. First explore: repo_map, the README, manifest files (package.json, "
                  "pyproject.toml, go.mod...), the test setup and a few central source files. Then write AGENTS.md, at most about "
                  "120 lines: a short overview; exact commands to install, run, build, test and lint (verify the cheap ones); the "
                  "layout (key folders and files and what lives where); conventions you see in the code (style, naming, patterns, "
                  "error handling); gotchas and things not to do. If AGENTS.md exists, improve it and keep what is still true; if "
                  "only CLAUDE.md exists, start from it. Do not change any other file.",
    },
    "review": {
        "description": "Code review of the current changes by a second model (no files are changed)",
        "icon": "eye",
        "prompt": "Review the current changes of this project: call review_changes, then give the user its findings, most severe "
                  "first, each with file:line and the suggested fix, and add anything you noticed yourself. Do not change any files; "
                  "the user decides what to fix.",
    },
    "test": {
        "description": "Run the tests, fix what fails, run them again until they pass",
        "icon": "check",
        "prompt": "Run the project's tests with run_tests. If something fails, find the cause (read the code, don't just change the "
                  "test), fix it and run the tests again, until they pass. If there are no tests yet, add a small useful set first. "
                  "Report what failed and what you changed.",
    },
    "fix": {
        "description": "Find and fix every error: syntax checks, tests and the build",
        "icon": "wand",
        "prompt": "Find and fix every error in this project: run check_code on the whole project, run_tests, and the build if there "
                  "is one. Fix each problem at its cause and check again until everything passes. Report what you fixed.",
    },
    "explain": {
        "description": "Explain how this codebase works (no files are changed)",
        "icon": "book",
        "prompt": "Explain how this project works for someone new to it: what it does, how it is organised (repo_map, key files), "
                  "the main flow from entry point to output, and where to look to change common things. Read the code to be "
                  "accurate. Do not change any files.",
    },
    "security": {
        "description": "Look for security problems in the project (no files are changed)",
        "icon": "shield",
        "prompt": "Do a security review of this project: look for injection (SQL, shell, HTML), secrets committed in code or config, "
                  "unsafe deserialisation, path traversal, missing authentication or authorisation, insecure defaults and "
                  "vulnerable dependency versions. Use grep and read the relevant code. List each finding with severity, "
                  "file:line and the fix. Do not change any files.",
    },
}


def expert_model(current=""):
    """The model for second opinions and reviews: the "Second-opinion model" setting, else the strongest coding model
    from another provider than the one doing the work (an independent view), else any other coding model."""
    chosen = SETTINGS["expert_model"] or ""
    if chosen and chosen != "auto":
        return chosen
    own = llm.split_cloud(current)[0] if llm.is_cloud(current or "") else ""
    return (llm.pick_code_model(exclude=[current], exclude_providers={own} if own else ())
            or llm.pick_code_model(exclude=[current]) or current or llm.default_model())


def ask(prompt, current="", system=EXPERT_SYSTEM, origin="agents"):
    """One answer from the expert model. Returns (text, model)."""
    model = expert_model(current)
    if not model:
        raise llm.LLMError("No model available for a second opinion. Add a model in Connections → Model.")
    text = ""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    with usage.tag(origin, ""):
        for event in llm.chat_stream(model, messages, options={"temperature": 0.2, "num_ctx": int(SETTINGS["num_ctx"])}):
            if event["type"] == "switch" and event.get("model"):
                model = event["model"]
            elif event["type"] == "end":
                text = event["content"]
    return text.strip(), model


def changes(path, project="", base=""):
    """(description, diff text) of what to review in a project folder: against base (a branch or commit) when given,
    else the uncommitted changes (with new files), else what the agent's last run changed (checkpoints)."""
    if gitops.binary() and gitops.is_repo(path):
        if base:
            text = gitops.run(["diff", "--no-color", "%s...HEAD" % base], path, timeout=60)
            if text.strip():
                return "the branch against %s" % base, text
        text = gitops.run(["diff", "--no-color", "HEAD"], path, timeout=60, check=False) if _has_head(path) else ""
        untracked = gitops.run(["ls-files", "--others", "--exclude-standard"], path, timeout=30).split("\n")
        for rel in [u for u in untracked if u.strip()][:40]:
            full = os.path.join(path, *rel.split("/"))
            try:
                if os.path.getsize(full) > 200_000:
                    continue
                with open(full, encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            text += "\ndiff --git a/%s b/%s\nnew file mode 100644\n--- /dev/null\n+++ b/%s\n@@ -0,0 +1,%d @@\n%s\n" % (
                rel, rel, rel, len(lines), "\n".join("+" + ln for ln in lines))
        if text.strip():
            return "the uncommitted changes", text
    if project:
        base_ckpt = next((c for c in gitops.checkpoints(project, path) if c["subject"].startswith("Before")), None)
        if base_ckpt:
            files = gitops.checkpoint_diff(project, path, base_ckpt["sha"], "now")
            if files:
                return "what the agent's last run changed", gitops.diff_text(files, limit=80000)
    return "", ""


def _has_head(path):
    return gitops.succeeds(["rev-parse", "--verify", "HEAD"], path)


def review(path, project="", base="", current="", focus=""):
    """A code review of a project's changes. Returns {"review", "model", "scope"}; review is "" when nothing changed."""
    scope, diff = changes(path, project, base)
    if not diff.strip():
        return {"review": "", "model": "", "scope": ""}
    if len(diff) > 90000:
        diff = diff[:90000] + "\n… (the rest of the diff is left out)"
    focus = ("\nPay special attention to: %s\n" % focus) if focus else ""
    text, model = ask(REVIEW_PROMPT % (focus, diff), current=current, system="You are a meticulous senior code reviewer.")
    if not text:
        raise llm.LLMError("%s returned an empty review. Try again, or choose another second-opinion model." % llm.label(model))
    return {"review": text, "model": model, "scope": scope}


MENTION = re.compile(r"(?<![\w@])@([\w.\-/\\]*[\w])")


def expand_mentions(text, folder, limit=60000):
    """@path mentions of files in a project folder: the message plus each file's content (once, inside the folder only)."""
    if not folder or "@" not in (text or ""):
        return text
    root = os.path.realpath(folder)
    parts, seen, budget = [text], set(), limit
    for m in MENTION.finditer(text):
        rel = m.group(1).replace("\\", "/")
        full = os.path.realpath(os.path.join(root, *rel.split("/")))
        if rel in seen or not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full) or budget <= 0:
            continue
        seen.add(rel)
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                body = fh.read(budget)
        except OSError:
            continue
        budget -= len(body)
        parts.append('<file path="%s">\n%s\n</file>' % (rel, body))
    return "\n\n".join(parts)


def command_prompt(name, extra=""):
    """The instructions behind /name, with what the user typed after it."""
    cmd = COMMANDS.get(str(name or "").lower().lstrip("/"))
    if not cmd:
        return ""
    extra = str(extra or "").strip()
    return cmd["prompt"] + (("\n\nThe user added: " + extra) if extra else "")
