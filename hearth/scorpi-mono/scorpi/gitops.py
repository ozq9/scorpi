"""Git for coding projects: status, diffs, commits, branches and push/pull (signed in with the GitHub account from
github.py), checkpoints the agent's work can be rewound to (a shadow repository per project under data/checkpoints,
apart from the project's own git), and a small terminal that runs commands in a project folder."""

import base64
import ctypes
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from . import events
from .config import DATA_DIR, IS_WINDOWS

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
CHECKPOINTS = os.path.join(DATA_DIR, "checkpoints")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
# what never belongs in a checkpoint or a first commit (dependencies and build output come back with one command)
IGNORE = ["node_modules/", ".venv/", "venv/", "__pycache__/", "*.pyc", "dist/", "build/", ".next/", ".cache/", ".parcel-cache/",
          ".DS_Store", "Thumbs.db", "*.log"]
MAX_FILES = 20000  # bigger folders get no checkpoints (copying them on every agent run would be slow)
_locks = {}
_locks_guard = threading.Lock()


class GitError(Exception):
    pass


def binary():
    return shutil.which("git")


def _lock(key):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def run(args, cwd, timeout=60, env=None, auth=None, check=True, stdin=None):
    """git <args> in cwd -> stdout. auth: a GitHub token for https remotes (sent as a header, never stored in the repo)."""
    exe = binary()
    if not exe:
        raise GitError("Git is not installed on this PC. Get it from https://git-scm.com/download/win and restart Scorpi.")
    cmd = [exe, "-c", "core.quotepath=off", "-c", "color.ui=false"]
    if auth:
        basic = base64.b64encode(("x-access-token:%s" % auth).encode()).decode()
        cmd += ["-c", "credential.helper=", "-c", "http.https://github.com/.extraheader=AUTHORIZATION: basic " + basic]
    full_env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never" if auth else os.environ.get("GCM_INTERACTIVE", "auto"))
    full_env.update(env or {})
    try:
        proc = subprocess.run(cmd + list(args), cwd=cwd, capture_output=True, timeout=timeout, env=full_env, input=stdin,
                              creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise GitError("git %s took longer than %ss and was stopped." % (args[0] if args else "", timeout))
    out = proc.stdout.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip() or out.strip()
        if auth:
            err = err.replace(auth, "***")
        raise GitError(_friendly(err) or "git %s failed." % (args[0] if args else ""))
    return out


def succeeds(args, cwd):
    """Whether git exits with 0 (for --quiet and -e checks, whose answer is the exit code)."""
    exe = binary()
    if not exe:
        return False
    proc = subprocess.run([exe, "-c", "core.quotepath=off"] + list(args), cwd=cwd, capture_output=True, timeout=120, creationflags=NO_WINDOW)
    return proc.returncode == 0


def _friendly(err):
    lines = [ln for ln in err.splitlines() if ln.strip() and not ln.startswith(("hint:", "warning: LF", "warning: CRLF"))]
    text = "\n".join(lines[-6:])
    if "Authentication failed" in text or "could not read Username" in text or "terminal prompts disabled" in text:
        return "GitHub refused the login. Connect GitHub in Connections → GitHub (the token needs the repo scope)."
    if "non-fast-forward" in text or "fetch first" in text:
        return "The remote has commits you don't have yet. Pull first, then push again."
    return text


def is_repo(path):
    return os.path.exists(os.path.join(path, ".git"))


def _has_head(path, git_args=()):
    return bool(run(list(git_args) + ["rev-parse", "--verify", "-q", "HEAD"], path, check=False).strip())


# ---------- the project's own repository ----------

def remote_url(path):
    return run(["remote", "get-url", "origin"], path, check=False).strip()


def status(path):
    """Branch, upstream, ahead/behind, remote and changed files of a project's repository."""
    if not binary():
        return {"repo": False, "git": False}
    if not is_repo(path):
        return {"repo": False, "git": True}
    raw = run(["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"], path, timeout=30)
    info = {"repo": True, "git": True, "branch": "", "oid": "", "upstream": "", "ahead": 0, "behind": 0, "files": []}
    parts = raw.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry:
            continue
        if entry.startswith("# branch.head "):
            info["branch"] = entry[14:] if entry[14:] != "(detached)" else ""
        elif entry.startswith("# branch.oid "):
            info["oid"] = "" if entry[13:] == "(initial)" else entry[13:]
        elif entry.startswith("# branch.upstream "):
            info["upstream"] = entry[18:]
        elif entry.startswith("# branch.ab "):
            m = re.match(r"\+(\d+) -(\d+)", entry[12:])
            if m:
                info["ahead"], info["behind"] = int(m.group(1)), int(m.group(2))
        elif entry[0] in "12u":
            fields = entry.split(" ", {"1": 8, "2": 9, "u": 10}[entry[0]])
            xy, path_ = fields[1], fields[-1]
            orig = ""
            if entry[0] == "2":
                orig = parts[i] if i < len(parts) else ""
                i += 1
            kind = "U" if entry[0] == "u" else "R" if entry[0] == "2" else next((c for c in (xy[1], xy[0]) if c != "."), "M")
            info["files"].append({"path": path_, "orig": orig, "kind": kind, "staged": xy[0] not in ".?", "xy": xy})
        elif entry[0] == "?":
            info["files"].append({"path": entry[2:], "orig": "", "kind": "?", "staged": False, "xy": "??"})
    info["files"] = info["files"][:2000]
    info["remote"] = remote_url(path)
    info["has_commits"] = bool(info["oid"])
    return info


def log(path, limit=30, git_args=()):
    if not _has_head(path, git_args):
        return []
    raw = run(list(git_args) + ["log", "-n", str(limit), "--pretty=format:%x1e%H%x1f%h%x1f%an%x1f%at%x1f%s", "--shortstat"], path)
    out = []
    for rec in raw.split("\x1e"):
        if not rec.strip():
            continue
        head, _, rest = rec.partition("\n")
        f = head.split("\x1f")
        if len(f) < 5:
            continue
        stat = rest.strip()
        nums = {k: int(v) for v, k in re.findall(r"(\d+) (file|insertion|deletion)", stat)}
        out.append({"sha": f[0], "short": f[1], "author": f[2], "time": int(f[3] or 0), "subject": f[4],
                    "files": nums.get("file", 0), "add": nums.get("insertion", 0), "del": nums.get("deletion", 0)})
    return out


def branches(path):
    if not is_repo(path):
        return []
    raw = run(["branch", "--format=%(refname:short)%09%(HEAD)%09%(committerdate:unix)"], path, check=False)
    out = []
    for line in raw.splitlines():
        name, current, when = (line.split("\t") + ["", "", ""])[:3]
        if name:
            out.append({"name": name, "current": current == "*", "time": int(when or 0)})
    out.sort(key=lambda b: (not b["current"], -b["time"]))
    return out


def init(path):
    if not is_repo(path):
        run(["init", "-b", "main"], path)
    gi = os.path.join(path, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as fh:
            fh.write("\n".join(IGNORE) + "\n.env\n")


def _identity(path, author=None):
    """-c user.name/email for a commit when the PC's git has no identity set (the GitHub account's otherwise)."""
    if run(["config", "user.email"], path, check=False).strip():
        return []
    name, email = author or ("Scorpi", "scorpi@localhost")
    return ["-c", "user.name=" + name, "-c", "user.email=" + email]


def commit(path, message, files=None, author=None):
    message = str(message or "").strip()
    if not message:
        raise GitError("Write a commit message.")
    if files:
        run(["add", "-A", "--"] + list(files), path, timeout=120)
    else:
        run(["add", "-A"], path, timeout=120)
    if _has_head(path) and not run(["diff", "--cached", "--name-only"], path).strip():
        raise GitError("Nothing to commit.")
    run(_identity(path, author) + ["commit", "-q", "-F", "-"], path, timeout=120, stdin=message.encode("utf-8"))
    return run(["rev-parse", "--short", "HEAD"], path).strip()


def push(path, auth=None):
    branch = run(["rev-parse", "--abbrev-ref", "HEAD"], path).strip()
    if branch == "HEAD":
        raise GitError("Not on a branch. Create or switch to a branch first.")
    if not remote_url(path):
        raise GitError("This repository has no remote yet. Publish it to GitHub first.")
    run(["push", "-u", "origin", branch], path, timeout=600, auth=auth)
    return branch


def pull(path, auth=None):
    return run(["pull", "--ff-only"], path, timeout=600, auth=auth).strip() or "Up to date."


def fetch(path, auth=None):
    run(["fetch", "--prune", "origin"], path, timeout=300, auth=auth)


def switch(path, name, create=False):
    name = str(name or "").strip()
    if not re.match(r"^[\w./-]+$", name) or ".." in name or name.startswith(("-", "/")) or name.endswith((".", "/", ".lock")):
        raise GitError("That is not a valid branch name (letters, digits, - _ . / only).")
    run(["switch"] + (["-c"] if create else []) + [name], path, timeout=60)
    return name


def discard(path, file):
    """Throw away the uncommitted changes of one file (an untracked file is deleted)."""
    rel = str(file or "").replace("\\", "/").lstrip("/")
    full = os.path.realpath(os.path.join(path, rel))
    if not rel or not full.startswith(os.path.realpath(path) + os.sep):
        raise GitError("Not a file in this project.")
    if _has_head(path) and succeeds(["cat-file", "-e", "HEAD:" + rel], path):
        run(["restore", "--source=HEAD", "--staged", "--worktree", "--", rel], path)
    elif os.path.isfile(full):
        run(["rm", "-q", "--cached", "--ignore-unmatch", "--", rel], path, check=False)
        os.remove(full)


def clone(url, dest, auth=None):
    if os.path.exists(dest) and os.listdir(dest):
        raise GitError("The folder %s is not empty." % dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    run(["clone", "--quiet", url, dest], os.path.dirname(dest), timeout=1800, auth=auth)


def add_remote(path, url):
    if remote_url(path):
        run(["remote", "set-url", "origin", url], path)
    else:
        run(["remote", "add", "origin", url], path)


def default_branch(path):
    ref = run(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], path, check=False).strip()
    return ref.split("/", 1)[1] if "/" in ref else ""


# ---------- diffs ----------

def _snapshot_tree(path, git_args=(), env=None):
    """The work tree as a tree object (new files included, .gitignore respected) without touching the real index."""
    run(list(git_args) + ["add", "-A"], path, timeout=180, env=env)
    return run(list(git_args) + ["write-tree"], path, env=env).strip()


def working_diff(path):
    """Uncommitted changes of the project's repository: HEAD against the files as they are now, new files included."""
    git_dir = run(["rev-parse", "--absolute-git-dir"], path).strip()
    fd, tmp = tempfile.mkstemp(prefix="scorpi-index-")
    os.close(fd)
    try:
        index = os.path.join(git_dir, "index")
        if os.path.isfile(index):
            shutil.copyfile(index, tmp)  # a copy keeps git's stat cache: only changed files are read again
        else:
            os.remove(tmp)
        tree = _snapshot_tree(path, env={"GIT_INDEX_FILE": tmp})
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    base = "HEAD" if _has_head(path) else EMPTY_TREE
    return parse_diff(run(["diff", "--no-ext-diff", "-M", base, tree], path, timeout=120))


def range_diff(path, a, b, git_args=()):
    return parse_diff(run(list(git_args) + ["diff", "--no-ext-diff", "-M", a, b], path, timeout=120))


def parse_diff(text, max_lines=1500, max_files=300):
    """Unified diff -> [{path, old_path, status, add, del, binary, truncated, hunks: [{header, lines: [[kind, old, new, text]]}]}]."""
    files, cur, hunk = [], None, None
    old = new = 0
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]  # CRLF files: the line ending is not part of what changed
        if line.startswith("diff --git "):
            hunk = None
            cur = {"path": "", "old_path": "", "status": "M", "add": 0, "del": 0, "binary": False, "truncated": False, "hunks": [], "_n": 0}
            files.append(cur)
            m = re.match(r'diff --git "?a/(.*?)"? "?b/(.*?)"?$', line)
            if m:
                cur["old_path"], cur["path"] = m.group(1), m.group(2)
            continue
        if cur is None:
            continue
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", line)
            if not m:
                continue
            old, new = int(m.group(1)), int(m.group(2))
            hunk = {"header": line, "context": m.group(3).strip(), "lines": []}
            cur["hunks"].append(hunk)
            continue
        if hunk is None:
            if line.startswith("new file"):
                cur["status"] = "A"
            elif line.startswith("deleted file"):
                cur["status"] = "D"
            elif line.startswith("rename from "):
                cur["status"], cur["old_path"] = "R", line[12:]
            elif line.startswith("rename to "):
                cur["path"] = line[10:]
            elif line.startswith("Binary files"):
                cur["binary"] = True
            elif line.startswith("+++ ") and line[4:] != "/dev/null":
                cur["path"] = line[4:].strip('"')[2:] if line[4:].strip('"').startswith("b/") else cur["path"]
            continue
        kind = line[:1]
        if not kind or kind not in "+- ":
            continue  # "\ No newline at end of file"
        if kind == "+":
            cur["add"] += 1
            row = ["+", None, new, line[1:]]
            new += 1
        elif kind == "-":
            cur["del"] += 1
            row = ["-", old, None, line[1:]]
            old += 1
        else:
            row = [" ", old, new, line[1:]]
            old += 1
            new += 1
        cur["_n"] += 1
        if cur["_n"] > max_lines:
            cur["truncated"] = True
            continue
        row[3] = row[3][:1000]
        hunk["lines"].append(row)
    for f in files:
        f.pop("_n", None)
        if f["status"] == "M" and f["old_path"] and f["old_path"] != f["path"]:
            f["status"] = "R"
    files.sort(key=lambda f: f["path"].lower())
    return files[:max_files]


def diff_text(files, limit=24000):
    """A compact unified diff from parse_diff() output, for the model (commit messages, PR descriptions)."""
    out = []
    for f in files:
        out.append("--- %s (%s, +%d -%d)" % (f["path"], f["status"], f["add"], f["del"]))
        for hk in f["hunks"]:
            out.append(hk["header"])
            out.extend(k + t for k, _o, _n, t in hk["lines"] if k != " ")
        if sum(len(x) for x in out) > limit:
            out.append("… (more changes)")
            break
    return "\n".join(out)[:limit]


# ---------- checkpoints: a shadow repository per project, so any agent run can be looked at and undone ----------

def _shadow(slug):
    return os.path.join(CHECKPOINTS, slug + ".git")


def _shadow_args(slug, path):
    return ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.longpaths=true",
            "--git-dir=" + _shadow(slug), "--work-tree=" + path]


def _shadow_ready(slug, path):
    gd = _shadow(slug)
    if not os.path.isdir(os.path.join(gd, "objects")):
        os.makedirs(gd, exist_ok=True)
        run(["init", "--bare", "-q", gd], CHECKPOINTS)
        run(["--git-dir=" + gd, "config", "core.bare", "false"], CHECKPOINTS)
        run(["--git-dir=" + gd, "config", "user.name", "Scorpi"], CHECKPOINTS)
        run(["--git-dir=" + gd, "config", "user.email", "scorpi@localhost"], CHECKPOINTS)
    exclude = os.path.join(gd, "info", "exclude")
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    if not os.path.isfile(exclude) or "node_modules" not in open(exclude, encoding="utf-8", errors="replace").read():
        with open(exclude, "w", encoding="utf-8") as fh:
            fh.write("\n".join(IGNORE + [".git/", ".env"]) + "\n")
    return _shadow_args(slug, path)


def _too_big(path):
    count = 0
    skip = {d.rstrip("/") for d in IGNORE if d.endswith("/")} | {".git"}
    for _base, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d not in skip]
        count += len(names)
        if count > MAX_FILES:
            return True
    return False


def snapshot(slug, path, label):
    """Save the project's files as a checkpoint. Returns its id (the previous one when nothing changed), or ''."""
    if not binary() or not os.path.isdir(path):
        return ""
    with _lock("ckpt:" + slug):
        try:
            if _too_big(path):
                return ""
            args = _shadow_ready(slug, path)
            run(args + ["add", "-A"], path, timeout=300)
            if _has_head(path, args) and succeeds(args + ["diff", "--cached", "--quiet", "HEAD"], path):
                return run(args + ["rev-parse", "HEAD"], path).strip()
            run(args + ["commit", "-q", "--no-verify", "--allow-empty", "-F", "-"], path, timeout=120,
                stdin=(str(label or "Checkpoint")[:300]).encode("utf-8"))
            return run(args + ["rev-parse", "HEAD"], path).strip()
        except GitError:
            return ""


def checkpoints(slug, path, limit=60):
    if not os.path.isdir(os.path.join(_shadow(slug), "objects")):
        return []
    return log(path, limit, _shadow_args(slug, path))


def checkpoint_diff(slug, path, sha, against=""):
    """What a checkpoint changed (against its parent), or everything since it (against='now')."""
    args = _shadow_ready(slug, path)
    if against == "now":
        with _lock("ckpt:" + slug):
            tree = _snapshot_tree(path, args)
        return range_diff(path, sha, tree, args)
    parent = run(args + ["rev-parse", "--verify", "-q", sha + "^"], path, check=False).strip() or EMPTY_TREE
    return range_diff(path, parent, sha, args)


def restore(slug, path, sha):
    """Put the project's files back as they were at a checkpoint. The current state is saved first, so this can be undone."""
    if not re.match(r"^[0-9a-f]{7,40}$", sha or ""):
        raise GitError("Unknown checkpoint.")
    args = _shadow_ready(slug, path)
    run(args + ["cat-file", "-e", sha + "^{commit}"], path)
    before = snapshot(slug, path, "Before restoring %s" % sha[:7])
    with _lock("ckpt:" + slug):
        run(args + ["read-tree", "-u", "--reset", sha], path, timeout=300)
    snapshot(slug, path, "Restored %s" % sha[:7])
    return before


# ---------- terminal: commands run in a project folder, their output streamed to the app ----------

TERMS = {}
OEM = "cp%d" % ctypes.windll.kernel32.GetOEMCP() if IS_WINDOWS else "utf-8"
ANSI =re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]")


class Term:
    def __init__(self, slug, cwd, command):
        self.id = uuid.uuid4().hex[:10]
        self.slug = slug
        self.command = command
        self.output = ""
        self.status = "running"
        self.code = None
        self.started = time.time()
        self.ended = 0.0
        self._pending = ""
        self._carry = b""
        self._timer = None
        self._mx = threading.Lock()
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", NO_COLOR="1", FORCE_COLOR="0", CI="1")
        if IS_WINDOWS:
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                   "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; " + command]
        else:
            cmd = ["bash", "-lc", command]
        self.proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                     creationflags=NO_WINDOW | NEW_GROUP, start_new_session=not IS_WINDOWS)
        threading.Thread(target=self._pump, daemon=True, name="term-" + self.id).start()

    def _decode(self, chunk):
        """UTF-8, or the console's OEM code page that older Windows programs (ping, ipconfig…) still print in."""
        data, self._carry = self._carry + chunk, b""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.reason == "unexpected end of data" and len(data) - exc.start < 4:
                self._carry = data[exc.start:]  # a character cut in two: the rest comes with the next chunk
                return data[:exc.start].decode("utf-8", "replace")
            return data.decode(OEM, "replace")

    def _pump(self):
        stream = self.proc.stdout
        while True:
            chunk = stream.read1(8192) if hasattr(stream, "read1") else stream.read(1)
            if not chunk:
                break
            self._append(self._decode(chunk))
        if self._carry:
            self._append(self._carry.decode(OEM, "replace"))
        self.code = self.proc.wait()
        self.status = "stopped" if self.status == "stopping" else "done" if self.code == 0 else "failed"
        self.ended = time.time()
        self._flush()

    def _append(self, text):
        text = ANSI.sub("", text).replace("\r\n", "\n")
        if not text:
            return
        with self._mx:
            self.output = (self.output + text)[-300000:]
            self._pending += text
            if not self._timer:
                self._timer = threading.Timer(0.15, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def _flush(self):
        with self._mx:
            chunk, self._pending, self._timer = self._pending, "", None
        events.publish("term", dict(self.public(output=False), chunk=chunk))

    def write(self, text):
        if self.status != "running" or not self.proc.stdin:
            raise GitError("That command has finished.")
        try:
            self.proc.stdin.write((str(text) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError:
            raise GitError("The command no longer reads input.")
        self._append("› %s\n" % text)

    def stop(self):
        if self.status != "running":
            return False
        self.status = "stopping"
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True, creationflags=NO_WINDOW, timeout=20)
            else:
                os.killpg(self.proc.pid, signal.SIGTERM)
        except Exception:  # noqa: BLE001 - it may have ended on its own
            pass
        return True

    def public(self, output=True):
        out = {"id": self.id, "slug": self.slug, "command": self.command, "status": "running" if self.status == "stopping" else self.status,
               "code": self.code, "started": self.started, "ended": self.ended}
        if output:
            out["output"] = self.output[-120000:]
        return out


def term_start(slug, cwd, command):
    command = str(command or "").strip()
    if not command:
        raise GitError("Type a command.")
    mine = sorted((t for t in TERMS.values() if t.slug == slug), key=lambda t: t.started)
    for old in [t for t in mine if t.status != "running"][:max(0, len(mine) - 11)]:
        TERMS.pop(old.id, None)  # keep the last dozen per project
    term = Term(slug, cwd, command)
    TERMS[term.id] = term
    events.publish("term", dict(term.public(output=False), chunk=""))
    return term


def terms(slug):
    return [t.public() for t in sorted(TERMS.values(), key=lambda t: t.started) if t.slug == slug]
