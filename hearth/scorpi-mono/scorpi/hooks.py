"""Hooks: commands of the user's own that run at fixed moments of an agent's work, every time (unlike instructions,
which a model may forget). Set in Connections → Tools & permissions → Hooks (setting "hooks"):

  after_edit      after the agent writes or edits files (e.g. a formatter: "npx prettier --write {files}")
  before_command  before it runs a shell command; a failing "block" hook stops the command (e.g. a policy script)
  after_turn      when it is about to finish in a project; a failing "block" hook sends it back to work with the
                  hook's output (e.g. "npm test": it keeps going until the tests pass)

Each hook: {"event", "command", "match" (a file glob like *.py for after_edit, a regex for before_command, empty =
always), "block" (a failure counts), "enabled", "timeout" (seconds)}. Commands run in the project folder with
{file}, {files}, {project} and {command} filled in (quoted), and the same values in SCORPI_FILE, SCORPI_FILES,
SCORPI_PROJECT and SCORPI_COMMAND. Hooks never come from a repository, only from the user's own settings."""

import fnmatch
import os
import re
import shlex
import subprocess
import uuid

from .config import IS_WINDOWS, SETTINGS

EVENTS = ("after_edit", "before_command", "after_turn")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
OUTPUT_LIMIT = 4000


def clean(hooks):
    """The hooks setting, validated: a list of well-formed hooks."""
    out = []
    for h in hooks if isinstance(hooks, list) else []:
        if not isinstance(h, dict):
            continue
        event = str(h.get("event") or "")
        command = str(h.get("command") or "").strip()
        if event not in EVENTS or not command:
            continue
        try:
            timeout = max(5, min(int(h.get("timeout") or 120), 1800))
        except (TypeError, ValueError):
            timeout = 120
        out.append({"id": str(h.get("id") or uuid.uuid4().hex[:8])[:16], "event": event, "command": command[:2000],
                    "match": str(h.get("match") or "").strip()[:300], "block": bool(h.get("block")),
                    "enabled": h.get("enabled", True) is not False, "timeout": timeout, "name": str(h.get("name") or "").strip()[:60]})
    return out[:50]


def _quote(value):
    if IS_WINDOWS:
        return '"%s"' % str(value).replace('"', '`"')  # PowerShell
    return shlex.quote(str(value))


def _matches(hook, files=(), command=""):
    pattern = hook["match"]
    if not pattern:
        return True
    if hook["event"] == "after_edit":
        globs = [g.strip() for g in re.split(r"[,;\s]+", pattern) if g.strip()]
        return any(fnmatch.fnmatch(os.path.basename(f).lower(), g.lower()) or fnmatch.fnmatch(f.replace("\\", "/").lower(), g.lower())
                   for f in files for g in globs)
    if hook["event"] == "before_command":
        try:
            return bool(re.search(pattern, command or ""))
        except re.error:
            return pattern in (command or "")
    return True


def _run(hook, workdir, files=(), command=""):
    files = list(files)
    text = (hook["command"].replace("{files}", " ".join(_quote(f) for f in files))
            .replace("{file}", _quote(files[0]) if files else "")
            .replace("{project}", _quote(workdir))
            .replace("{command}", _quote(command)))
    env = dict(os.environ, SCORPI_EVENT=hook["event"], SCORPI_FILE=files[0] if files else "", SCORPI_FILES=os.pathsep.join(files),
               SCORPI_PROJECT=workdir, SCORPI_COMMAND=command or "", PYTHONIOENCODING="utf-8", NO_COLOR="1")
    shell = (["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", text] if IS_WINDOWS
             else ["bash", "-lc", text])
    try:
        proc = subprocess.run(shell, cwd=workdir or None, capture_output=True, timeout=hook["timeout"], env=env, creationflags=NO_WINDOW)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
        code = proc.returncode
    except subprocess.TimeoutExpired:
        out, code = "stopped after %d s" % hook["timeout"], "timeout"
    except OSError as exc:
        out, code = str(exc), "error"
    return {"hook": hook, "code": code, "ok": code == 0, "output": out[-OUTPUT_LIMIT:]}


def run(event, workdir, files=(), command=""):
    """Run the enabled hooks of an event that match. Returns their results (empty when there are none)."""
    results = []
    for hook in clean(SETTINGS["hooks"] or []):
        if hook["enabled"] and hook["event"] == event and _matches(hook, files, command):
            results.append(_run(hook, workdir, files, command))
    return results


def label(result):
    hook = result["hook"]
    return hook["name"] or hook["command"][:60]


def summary(results):
    """Lines for a tool result or a notice."""
    lines = []
    for r in results:
        state = "ok" if r["ok"] else "failed (exit %s)" % r["code"]
        lines.append("Hook %s %s%s" % (label(r), state, (":\n" + r["output"]) if r["output"] and not r["ok"] else ""))
    return "\n".join(lines)


def blocking_failures(results):
    return [r for r in results if r["hook"]["block"] and not r["ok"]]
