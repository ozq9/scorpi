"""Code execution on the host: Python, JavaScript (Node) and the system shell."""

import base64
import os
import shutil
import subprocess
import sys
import time
import uuid

from ..config import IS_WINDOWS, WORKSPACE
from . import Result, ToolError, resolve_path, tool

OUTPUT_LIMIT = 20_000
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _snapshot(folder):
    out = {}
    for root, dirs, files in os.walk(folder):
        if root.count(os.sep) - folder.count(os.sep) >= 2:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            full = os.path.join(root, name)
            try:
                out[full] = os.path.getmtime(full)
            except OSError:
                pass
    return out


def _execute(cmd, cwd, timeout, stdin=None, env=None, track=True):
    before = _snapshot(cwd) if track else {}
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, input=stdin, capture_output=True, timeout=timeout, env=env,
            creationflags=NO_WINDOW,
        )
        code = proc.returncode
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        code = "timeout"
        out = (exc.stdout or b"").decode("utf-8", "replace")
        err = (exc.stderr or b"").decode("utf-8", "replace") + "\n[stopped after %ss]" % timeout
    after = _snapshot(cwd) if track else {}
    created = [p for p, m in after.items() if before.get(p) != m]
    elapsed = time.time() - started
    return code, out, err, created, elapsed


def _report(language, code_text, code, out, err, created, elapsed):
    parts = ["exit code: %s (%.1fs)" % (code, elapsed)]
    if out.strip():
        parts.append("stdout:\n" + (out[:OUTPUT_LIMIT] + ("\n... [truncated]" if len(out) > OUTPUT_LIMIT else "")))
    if err.strip():
        parts.append("stderr:\n" + err[-8000:])
    if created:
        parts.append("files written:\n" + "\n".join(created[:30]))
    images = []
    shown = []
    for path in created:
        if path.lower().endswith((".png", ".jpg", ".jpeg")) and os.path.getsize(path) < 6 * 1024 * 1024:
            shown.append(path)
            if len(images) < 2:
                with open(path, "rb") as fh:
                    images.append(base64.b64encode(fh.read()).decode())
    display = {
        "type": "code",
        "language": language,
        "code": code_text[:12000],
        "stdout": out[-6000:],
        "stderr": err[-3000:],
        "exit": code,
        "images": [p for p in created if p.lower().endswith(IMAGE_EXT)][:6],
        "files": [p for p in created if not p.lower().endswith(IMAGE_EXT)][:10],
    }
    return Result("\n\n".join(parts), ok=(code == 0), display=display, images=images, files=created)


def _cwd(cwd):
    if cwd:
        path = resolve_path(cwd)
        os.makedirs(path, exist_ok=True)
        return path
    return WORKSPACE


@tool(
    "run_python",
    "Run Python 3 code on the host and return stdout/stderr. Great for calculations, data processing, file conversion "
    "and automation. Has access to installed packages (pandas, matplotlib, openpyxl...). Save plots with "
    "plt.savefig('name.png') — new image files are shown to the user. Working directory: the workspace.",
    {"code": "string: Python source", "timeout?": "integer: Seconds (default 120)", "cwd?": "string: Working directory"},
    group="code",
    risk="danger",
)
def run_python(ctx, code, timeout=120, cwd=""):
    folder = _cwd(cwd)
    script_dir = os.path.join(WORKSPACE, ".runs")
    os.makedirs(script_dir, exist_ok=True)
    script = os.path.join(script_dir, "run_%s.py" % uuid.uuid4().hex[:8])
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(code)
    env = dict(os.environ, MPLBACKEND="Agg", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    python = sys.executable
    if python.lower().endswith("pythonw.exe"):
        python = python[:-5] + ".exe"
    result = _execute([python, script], folder, min(int(timeout or 120), 1800), env=env)
    try:
        os.remove(script)
    except OSError:
        pass
    return _report("python", code, *result)


@tool(
    "run_javascript",
    "Run JavaScript with Node.js on the host and return the output (console.log).",
    {"code": "string: JavaScript source", "timeout?": "integer: Seconds (default 60)"},
    group="code",
    risk="danger",
)
def run_javascript(ctx, code, timeout=60):
    node = shutil.which("node")
    if not node:
        raise ToolError("Node.js is not installed on the host. Install it from https://nodejs.org or use run_python.")
    script_dir = os.path.join(WORKSPACE, ".runs")
    os.makedirs(script_dir, exist_ok=True)
    script = os.path.join(script_dir, "run_%s.mjs" % uuid.uuid4().hex[:8])
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(code)
    result = _execute([node, script], WORKSPACE, min(int(timeout or 60), 900))
    try:
        os.remove(script)
    except OSError:
        pass
    return _report("javascript", code, *result)


@tool(
    "run_shell",
    "Run a shell command on the host (PowerShell on Windows, bash elsewhere). Use for installing software, git, "
    "system configuration, controlling programs with their CLIs, etc.",
    {"command": "string: Command line", "timeout?": "integer: Seconds (default 120)", "cwd?": "string: Working directory"},
    group="code",
    risk="danger",
)
def run_shell(ctx, command, timeout=120, cwd=""):
    folder = _cwd(cwd) if cwd else os.path.expanduser("~")
    if IS_WINDOWS:
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
               "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]
    else:
        cmd = ["bash", "-lc", command]
    code, out, err, created, elapsed = _execute(cmd, folder, min(int(timeout or 120), 1800), track=False)
    return _report("powershell" if IS_WINDOWS else "bash", command, code, out, err, [], elapsed)
