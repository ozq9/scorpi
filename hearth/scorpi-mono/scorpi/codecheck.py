"""Quick checks of source files after an agent writes them (like a linter on save): syntax errors with their line,
broken JSON, unbalanced CSS, and web pages that link to files that do not exist. Each check is fast and works with
the standard library (plus node, php, bash... when they are installed), so it can run after every edit."""

import json
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_BYTES = 2 * 1024 * 1024


def _tool(cmd, path, timeout=15):
    """Run an external checker; returns its message on failure, "" when it passed or is not installed."""
    exe = shutil.which(cmd[0])
    if not exe:
        return ""
    try:
        proc = subprocess.run([exe] + cmd[1:] + [path], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode == 0:
        return ""
    text = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
    return text.replace(path, os.path.basename(path))[:1200]


def _python(path, text):
    try:
        compile(text, path, "exec", dont_inherit=True)
    except SyntaxError as exc:
        where = "line %s" % exc.lineno if exc.lineno else "?"
        line = (exc.text or "").rstrip()
        return ["%s: %s%s" % (where, exc.msg, ("\n    " + line) if line else "")]
    except ValueError as exc:  # e.g. null bytes
        return [str(exc)]
    return []


def _json(path, text):
    if not text.strip():
        return []
    try:
        json.loads(text)
    except ValueError as exc:
        # package.json, tsconfig.json and friends may hold comments (JSONC); only plain .json is strict
        if os.path.basename(path).lower().startswith(("tsconfig", "jsconfig")) or path.lower().endswith((".jsonc", ".json5")):
            return []
        return [str(exc)]
    return []


def _css(path, text):
    stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    stripped = re.sub(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", "\"\"", stripped)
    depth = 0
    for number, line in enumerate(stripped.splitlines(), 1):
        for ch in line:
            depth += (ch == "{") - (ch == "}")
            if depth < 0:
                return ["line %d: '}' without a matching '{'" % number]
    return ["%d '{' never closed" % depth] if depth > 0 else []


class _Links(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("src", "href"):
            value = a.get(key)
            if not value or (tag == "a" and key == "href"):
                continue  # page links may point anywhere (routes, anchors); scripts, styles and images must exist
            if tag == "link" and (a.get("rel") or "").lower() not in ("stylesheet", "icon", "manifest", "preload", "modulepreload", "apple-touch-icon"):
                continue
            self.refs.append((value, self.getpos()[0]))


def _html(path, text):
    parser = _Links()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001 - the parser is lenient; anything it trips on is not worth blocking an edit
        return []
    folder = os.path.dirname(path)
    problems = []
    for ref, line in parser.refs:
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#|\{|\$|data:)", ref, re.I) or "{{" in ref or "<%" in ref:
            continue  # remote, data, templating
        clean = ref.split("?")[0].split("#")[0]
        if not clean:
            continue
        target = os.path.normpath(os.path.join(folder, clean.lstrip("/")) if not clean.startswith("/") else os.path.join(_site_root(folder), clean.lstrip("/")))
        if not os.path.exists(target):
            problems.append("line %d: links to %s, which does not exist" % (line, ref))
    return problems[:12]


def _site_root(folder):
    """For "/style.css" in a page: the nearest folder up with index.html or package.json, else the page's own folder."""
    current = folder
    for _ in range(6):
        if os.path.exists(os.path.join(current, "package.json")) or os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return folder


def _toml(path, text):
    try:
        import tomllib
    except ImportError:
        return []
    try:
        tomllib.loads(text)
    except Exception as exc:  # noqa: BLE001
        return [str(exc)]
    return []


def _yaml(path, text):
    try:
        import yaml
    except ImportError:
        return []
    try:
        list(yaml.safe_load_all(text))
    except Exception as exc:  # noqa: BLE001
        return [str(exc).splitlines()[0] if str(exc) else "invalid YAML"]
    return []


EXTERNAL = {
    (".js", ".mjs", ".cjs"): ["node", "--check"],
    (".php",): ["php", "-l"],
    (".sh", ".bash"): ["bash", "-n"],
    (".rb",): ["ruby", "-c"],
    (".pl", ".pm"): ["perl", "-c"],
    (".lua",): ["luac", "-p"],
}
INTERNAL = {
    (".py", ".pyw"): _python,
    (".json",): _json,
    (".css",): _css,
    (".html", ".htm"): _html,
    (".toml",): _toml,
    (".yml", ".yaml"): _yaml,
}


def supported(path):
    ext = os.path.splitext(path)[1].lower()
    return any(ext in exts for exts in list(INTERNAL) + list(EXTERNAL))


def check(path):
    """Problems found in one file, as short strings (empty when it looks fine or the type is not checked)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > MAX_BYTES:
            return []
        for exts, fn in INTERNAL.items():
            if ext in exts:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    return fn(path, fh.read())
        for exts, cmd in EXTERNAL.items():
            if ext in exts:
                out = _tool(cmd, path)
                if out and ext in (".js", ".mjs", ".cjs") and ("Cannot use import statement" in out or "ERR_REQUIRE_ESM" in out):
                    return []  # module syntax node cannot pre-check in this mode; not a real error
                return [out] if out else []
    except OSError as exc:
        return [str(exc)]
    return []


def note(paths):
    """A line for a tool result: "Checks passed" or the problems, so the agent fixes them right away."""
    paths = [p for p in dict.fromkeys(paths) if supported(p)]
    if not paths:
        return ""
    found = []
    for path in paths[:20]:
        for problem in check(path):
            found.append("%s: %s" % (os.path.basename(path), problem))
    if not found:
        return "Checks passed (%s)." % ", ".join(os.path.basename(p) for p in paths[:6])
    return "PROBLEMS FOUND — fix them now:\n" + "\n".join("- " + f for f in found[:20])
