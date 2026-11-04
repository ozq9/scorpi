"""Visual check: open a web project in a hidden browser, take a screenshot, and collect what went wrong (errors in the
console, scripts that crashed, files that failed to load, a page wider than the screen). The screenshot goes to the
model (a vision model looks at it) and to the chat, next to the page as it was before the agent's run, with a slider to
compare them. Uses Playwright when installed, else Edge, Chrome or Chromium itself.
"""

import base64
import glob as globlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse

from ..config import DATA_DIR, IS_WINDOWS
from . import Result, ToolError, project_dir, tool, work_dir

ROOT = os.path.join(DATA_DIR, "visual")
UI_FILES = re.compile(r"\.(html?|css|scss|sass|less|jsx?|tsx?|mjs|vue|svelte|astro)$", re.I)
ENTRY_PAGES = ("index.html", "public/index.html", "dist/index.html", "src/index.html", "www/index.html", "site/index.html", "docs/index.html")
PREVIEW = None  # set by the server: fn(project slug, relative path) -> http URL of the project preview
_lock = threading.Lock()


# ---------- finding a browser ----------

def browser_binary():
    """Edge, Chrome or Chromium on this PC, for when Playwright is missing (SCORPI_BROWSER picks one)."""
    chosen = os.environ.get("SCORPI_BROWSER", "")
    if chosen and os.path.isfile(chosen):
        return chosen
    if not IS_WINDOWS:
        shell = sorted(globlib.glob("/opt/pw-browsers/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"))
        if shell:
            return shell[-1]  # Playwright's headless Chromium: made for exactly this
    names = ["msedge", "microsoft-edge", "google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    if IS_WINDOWS:
        for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("LOCALAPPDATA", "")):
            candidates += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                           os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    else:
        candidates += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                       "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
        candidates += sorted(globlib.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))
    return next((c for c in candidates if c and os.path.isfile(c)), "")


# ---------- capturing a page ----------

FACTS_JS = """() => ({
  title: document.title || '',
  overflow: document.documentElement.scrollWidth > window.innerWidth + 2,
  text: document.body ? document.body.innerText.trim().length : 0,
  images: document.images.length,
  broken: [...document.images].filter(i => i.complete && i.naturalWidth === 0).map(i => i.currentSrc || i.src).slice(0, 8),
})"""


def _with_playwright(url, out, width, height):
    from playwright.sync_api import sync_playwright
    found = {"console": [], "errors": [], "failed": [], "engine": "playwright"}
    with sync_playwright() as pw:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = pw.chromium.launch(headless=True, **({"channel": channel} if channel else {}))
                break
            except Exception:  # noqa: BLE001 - try the next browser
                continue
        if browser is None:
            raise RuntimeError("no browser for Playwright")
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("console", lambda m: found["console"].append("%s: %s" % (m.type, m.text)) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: found["errors"].append(str(e)))
            page.on("requestfailed", lambda r: found["failed"].append("%s (%s)" % (r.url, r.failure or "failed")))
            page.on("response", lambda r: found["failed"].append("%s (HTTP %d)" % (r.url, r.status)) if r.status >= 400 else None)
            page.goto(url, wait_until="load", timeout=25000)
            page.wait_for_timeout(900)  # scripts that run after load
            found.update(page.evaluate(FACTS_JS))
            page.screenshot(path=out)
        finally:
            browser.close()
    return found


CONSOLE_LINE = re.compile(r'CONSOLE(?:\(\d+\)|:\d+)\]\s*"(.*?)",\s*source:\s*(\S*)\s*\((\d+)\)')


def _with_binary(url, out, width, height):
    exe = browser_binary()
    if not exe:
        raise ToolError("No browser found for the visual check. Install Microsoft Edge or Chrome (or pip install playwright).")
    profile = tempfile.mkdtemp(prefix="scorpi-visual-")
    args = [exe] + ([] if "headless-shell" in exe else ["--headless=new"]) + [
            "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
            "--user-data-dir=" + profile, "--window-size=%d,%d" % (width, height), "--screenshot=" + out,
            "--virtual-time-budget=4000", "--enable-logging=stderr", "--v=0", "--allow-file-access-from-files"]
    if not IS_WINDOWS and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")  # root in a container
    try:
        proc = subprocess.run(args + [url], capture_output=True, timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise ToolError("The browser took longer than 45 s to show the page.")
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    log = proc.stderr.decode("utf-8", "replace")
    found = {"console": [], "errors": [], "failed": [], "engine": os.path.basename(exe)}
    for text, source, line in CONSOLE_LINE.findall(log):
        where = "%s:%s" % (os.path.basename(urllib.parse.urlparse(source).path) or source, line)
        target = found["errors"] if re.search(r"uncaught|error", text, re.I) else found["console"]
        target.append("%s (%s)" % (text, where))
    for m in re.finditer(r"net::(ERR_[A-Z_]+)[^\n]*?(https?://\S+|file://\S+)?", log):
        if m.group(1) not in ("ERR_ABORTED",):
            found["failed"].append("%s %s" % (m.group(2) or "", m.group(1)))
    if not os.path.isfile(out):
        raise ToolError("The browser could not take a screenshot of %s." % url)
    return found


def capture(url, out, width=1280, height=800):
    """Screenshot of a page into out (PNG) and what went wrong on it."""
    try:
        return _with_playwright(url, out, width, height)
    except ImportError:
        pass
    except ToolError:
        raise
    except Exception:  # noqa: BLE001 - Playwright without a usable browser: try the browser itself
        pass
    return _with_binary(url, out, width, height)


# ---------- what to look at ----------

def entry_page(root):
    for rel in ENTRY_PAGES:
        if os.path.isfile(os.path.join(root, rel)):
            return rel
    return ""


def _served_url(slug):
    """The URL a running dev server of this project printed (run_shell background=true)."""
    from .. import gitops
    from .dev import URL_RX
    for term in sorted(gitops.TERMS.values(), key=lambda t: -t.started):
        if term.status == "running" and (not slug or term.slug == slug):
            urls = [u.rstrip(".,;/") + "/" for u in URL_RX.findall(term.output)]
            if urls:
                return urls[-1].replace("0.0.0.0", "127.0.0.1")
    return ""


def resolve_target(url="", path="", slug=""):
    """(url to open, relative file or "", description)."""
    if url:
        return url, "", url
    root = project_dir() or work_dir()
    if path:
        full = path if os.path.isabs(path) else os.path.join(work_dir(), path)
        if not os.path.isfile(full):
            raise ToolError("No such page: %s" % path)
        rel = os.path.relpath(full, root) if root and full.startswith(root) else ""
        return _file_url(full, slug, rel), rel, rel or full
    served = _served_url(slug)
    if served:
        return served, "", served
    rel = entry_page(root) if root else ""
    if not rel:
        raise ToolError("Nothing to look at: no index.html in the project and no dev server running. Start it with "
                        "run_shell(background=true) or pass url or path.")
    return _file_url(os.path.join(root, rel), slug, rel), rel, rel


def _file_url(full, slug, rel):
    if PREVIEW and slug and rel:
        return PREVIEW(slug, rel.replace(os.sep, "/"))  # over http: module scripts and fetch work as on a server
    return "file:///" + full.replace(os.sep, "/").lstrip("/") if IS_WINDOWS else "file://" + full


# ---------- history: the "before" of a run ----------

def _key(slug, target):
    raw = slug or target
    return re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")[:60] or "page"


def _history(folder):
    try:
        with open(os.path.join(folder, "history.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def _remember(folder, entry):
    with _lock:
        items = _history(folder) + [entry]
        keep = items[-12:]
        for old in items[:-12]:
            try:
                os.remove(os.path.join(folder, old["file"]))
            except OSError:
                pass
        with open(os.path.join(folder, "history.json"), "w", encoding="utf-8") as fh:
            json.dump(keep, fh, indent=1)


def _before(folder, page, run_id, slug, rel, root, width, height, checkpoint="", since=0):
    """The page before this run: the last screenshot from an earlier run, else the page rendered from the checkpoint
    taken before the run (a static page only)."""
    earlier = [e for e in _history(folder) if e.get("page", e.get("target")) == page and e.get("run") != run_id and e.get("size") == [width, height]]
    if earlier:
        return earlier[-1]["file"]
    if not (slug and rel and root):
        return ""
    from .. import gitops
    base = {"sha": checkpoint} if checkpoint else None
    if not base:
        try:  # the newest checkpoint from before this run started
            base = next((c for c in gitops.checkpoints(slug, root) if not since or c["time"] <= since + 1), None)
        except Exception:  # noqa: BLE001 - no checkpoints (git missing): no before picture
            base = None
    if not base:
        return ""
    copy = tempfile.mkdtemp(prefix="scorpi-before-")
    try:
        gitops.export_tree(slug, base["sha"], copy)
        page = os.path.join(copy, rel)
        if not os.path.isfile(page):
            return ""
        name = "before-%s.png" % base["sha"][:10]
        if not os.path.isfile(os.path.join(folder, name)):
            capture("file:///" + page.replace(os.sep, "/").lstrip("/") if IS_WINDOWS else "file://" + page, os.path.join(folder, name), width, height)
        return name
    except Exception:  # noqa: BLE001 - the before picture is a nicety
        return ""
    finally:
        shutil.rmtree(copy, ignore_errors=True)


# ---------- the tool ----------

def check(ctx, url="", path="", width=1280, height=800):
    """Look at the page: (Result for the agent, facts)."""
    agent = getattr(ctx, "agent", None)
    slug = getattr(agent, "project", "") or ""
    run = getattr(agent, "run", None)
    run_id = getattr(run, "id", "") or ""
    width, height = max(320, min(int(width or 1280), 2560)), max(320, min(int(height or 800), 2000))
    target, rel, label = resolve_target(url, path, slug)
    folder = os.path.join(ROOT, _key(slug, target))
    os.makedirs(folder, exist_ok=True)
    name = "%s-%dx%d.png" % (time.strftime("%Y%m%d-%H%M%S"), width, height)
    root = project_dir() or work_dir()
    page = rel.replace(os.sep, "/") if rel else target  # the same page, whether opened as a file or over the preview server
    before = _before(folder, page, run_id, slug, rel, root, width, height, getattr(agent, "checkpoint", "") or "",
                     getattr(agent, "started", 0) or 0)
    facts = capture(target, os.path.join(folder, name), width, height)
    _remember(folder, {"file": name, "target": target, "page": page, "run": run_id, "time": time.time(), "size": [width, height]})
    problems = facts["errors"] + [c for c in facts["console"] if c.startswith("error")] + facts["failed"]
    lines = ["Looked at %s (%dx%d, %s)%s." % (label, width, height, facts["engine"], (": " + facts["title"]) if facts.get("title") else "")]
    if facts["errors"]:
        lines.append("Script errors:\n" + "\n".join("- " + e for e in facts["errors"][:10]))
    console = [c for c in facts["console"] if c not in facts["errors"]]
    if console:
        lines.append("Console:\n" + "\n".join("- " + c for c in console[:10]))
    if facts["failed"]:
        lines.append("Failed to load:\n" + "\n".join("- " + f for f in facts["failed"][:10]))
    if facts.get("broken"):
        lines.append("Broken images:\n" + "\n".join("- " + b for b in facts["broken"]))
    if facts.get("overflow"):
        lines.append("The page is wider than the window (sideways scrolling).")
    if facts.get("text") == 0 and not facts.get("images"):
        lines.append("The page looks empty: no text and no images.")
    if not problems and not facts.get("overflow") and not facts.get("broken"):
        lines.append("No errors.")
    lines.append("The screenshot is attached: check that the page looks right.")
    with open(os.path.join(folder, name), "rb") as fh:
        image = base64.b64encode(fh.read()).decode()
    base = "/api/visual/%s/" % os.path.basename(folder)
    display = {"type": "visual", "target": label, "after": base + name, "before": (base + before) if before else "",
               "errors": (facts["errors"] + [c for c in facts["console"] if c.startswith("error")])[:8], "failed": facts["failed"][:6],
               "overflow": bool(facts.get("overflow")), "title": facts.get("title", ""), "size": [width, height]}
    ok = not facts["errors"] and not facts.get("broken")
    facts.update(problems=problems, screenshot=os.path.join(folder, name), before=before)
    return Result("\n".join(lines), ok=ok, display=display, images=[image]), facts


@tool(
    "visual_check",
    "Open the web page you are building in a hidden browser: get a screenshot (you see it) and the problems a person "
    "would hit: script errors, console errors, files that fail to load, broken images, sideways scrolling. Without "
    "arguments it opens the running dev server of the project, else its index.html. Use it after changing how a page "
    "looks or behaves, and fix what it finds. width 390 checks a phone.",
    {"url?": "string: A URL to open (e.g. http://localhost:5173/about)", "path?": "string: An HTML file of the project",
     "width?": "integer: Window width in pixels (default 1280; 390 for a phone)", "height?": "integer: Window height (default 800)"},
    group="code",
)
def visual_check(ctx, url="", path="", width=1280, height=800):
    result, _ = check(ctx, url, path, width, height)
    return result
