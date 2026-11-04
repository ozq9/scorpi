"""Fix it when something crashes. Commands in a project's Terminal (a dev server, a watcher, the agent's background
processes) are watched: a stack trace, a compile error or an unexpected exit marks the command as crashed, the app shows
"Fix it" (also as a notification when nobody is looking), and one click hands the error, the command and what changed
recently to the project's agent, which fixes it and runs the command again to confirm.
"""

import re
import threading
import time

from . import events, gitops, notify

SIGNS = re.compile(
    r"Traceback \(most recent call last\)|^\s*(?:[A-Z]\w*)?(?:Error|Exception)(?:\s*\[\w+\])?:\s|Uncaught |Unhandled|npm ERR!|"
    r"panicked at|Exception in thread|^\s*FATAL|Failed to compile|SyntaxError|ModuleNotFoundError|Cannot find module|"
    r"EADDRINUSE|Segmentation fault|error TS\d+|^ERROR in |Internal server error|\[vite\].*error|^\s*error(?:\[E\d+\])?:\s|"
    r"core dumped|Killed$|out of memory",
    re.M | re.I,
)
QUIET = re.compile(r"0 errors?|no errors|errors?: 0|without errors|DeprecationWarning", re.I)
_seen = {}  # term id -> set of error signatures already reported
_lock = threading.Lock()


def error_line(text):
    """The telling line of an error in some output, or ""."""
    best = ""
    for line in str(text or "").splitlines():
        if SIGNS.search(line) and not QUIET.search(line):
            best = line.strip()
            if not best.lower().startswith("traceback"):
                return best[:240]
    return best[:240]


def _norm(line):
    return re.sub(r"\d+", "#", re.sub(r"(?:[A-Za-z]:)?[\\/][^\s:'\"]+", "<p>", line))[:160]


def watch(term, chunk):
    """Called with every batch of a command's output (gitops.Term): spot a crash once per kind of error."""
    if term.status in ("stopping", "stopped"):
        return
    line = error_line(chunk) if chunk else ""
    if not line and term.status == "failed" and term.ended:
        line = error_line(term.output[-6000:]) or "exited with code %s" % term.code
    if not line:
        return
    with _lock:
        seen = _seen.setdefault(term.id, set())
        key = _norm(line)
        if key in seen:
            return
        seen.add(key)
    running = term.status == "running"
    term.crash = {"line": line, "at": time.time(), "running": running}
    events.publish("crash", {"term": term.id, "slug": term.slug, "command": term.command, "line": line, "running": running})
    long_lived = (time.time() - term.started) > 20
    if term.slug and long_lived and not _agent_busy(term.slug):
        # a dev server that broke while nobody watched the Terminal: tell the user wherever they are
        notify.send("%s · %s crashed" % (term.slug, term.command[:60]), line, level="warning", link="/code/%s#fix=%s" % (term.slug, term.id))


def _agent_busy(slug):
    from . import agents
    job = agents.latest_job(slug)
    return bool(job and job.status == "running")


def prompt(term, path, slug):
    """(the request for the agent, what the chat shows)."""
    from . import coding
    tail = "\n".join(term.output.rstrip().split("\n")[-80:])[-6000:]
    state = "is still running but printed this error" if term.status == "running" else "stopped (exit code %s)" % term.code
    what, diff = "", ""
    try:
        what, diff = coding.changes(path, slug)
    except Exception:  # noqa: BLE001 - no git: the error alone has to do
        pass
    recent = ""
    if diff:
        files = re.findall(r"^diff --git a/(\S+)", diff, re.M)
        recent = ("\n\nRecently changed (%s): %s\n```diff\n%s\n```" % (what, ", ".join(files[:20]), diff[:5000]))
    line = (getattr(term, "crash", None) or {}).get("line") or error_line(tail) or "it failed"
    text = ("The command `%s` in the project's Terminal %s:\n```\n%s\n```%s\n\nFind the cause (start from the error and what changed "
            "recently), fix it, then run the command again (in the background if it is a server) and check that the error is "
            "gone." % (term.command, state, tail, recent))
    return text, "Fix it: %s" % line[:120]


def fix(slug, path, term_id):
    """Hand a crash to the project's agent: the running job gets the message, else the coder starts on it. Returns the job."""
    from . import agents
    term = gitops.TERMS.get(term_id)
    if not term or term.slug != slug:
        raise ValueError("That command is gone. Run it again to see the error.")
    text, shown = prompt(term, path, slug)
    job = agents.latest_job(slug)
    if job:
        job.message(text, display=shown)
        return job
    return agents.assign("coder", text, title="Fix: " + term.command[:40], project=slug, display=shown)


gitops.WATCHERS.append(watch)
