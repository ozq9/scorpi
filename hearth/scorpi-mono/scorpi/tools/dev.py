"""Developer tools for coding agents: code search (grep, glob), a map of the codebase, patches, tests, syntax checks,
background processes (dev servers) and read-only git. Paths are relative to the agent's project folder."""

import ast
import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import time

from .. import codecheck, gitops
from ..config import IS_WINDOWS
from . import Result, ToolError, project_dir, resolve_path, tool, work_dir

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", ".next", ".nuxt", ".cache", ".idea",
             ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache", "target", "vendor", ".gradle", "bin", "obj", "coverage",
             ".parcel-cache", ".svelte-kit", ".turbo", ".tox", "site-packages", "$Recycle.Bin"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_FILE = 2 * 1024 * 1024


def _rel(path, base):
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel.replace("\\", "/")


def _walk(base, max_files=20000):
    """Files under base (skipping dependencies, build output and hidden folders), as absolute paths."""
    count = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            yield os.path.join(root, name)
            count += 1
            if count >= max_files:
                return


def _is_binary(path):
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return True


def _glob_match(rel, pattern):
    """A path relative to the search folder against a glob: "*.py" matches the name anywhere, "src/**/*.ts" the path."""
    pattern = pattern.replace("\\", "/")
    if "/" not in pattern:
        return fnmatch.fnmatch(os.path.basename(rel).lower(), pattern.lower())
    if fnmatch.fnmatch(rel.lower(), pattern.lower()):
        return True
    # "src/**/*.ts" should also match "src/a.ts" (** may be empty)
    return "**/" in pattern and fnmatch.fnmatch(rel.lower(), pattern.replace("**/", "").lower())


def _globs(glob):
    """"*.{ts,tsx}" or "*.ts, *.tsx" -> ["*.ts", "*.tsx"]."""
    parts, current, depth = [], "", 0
    for ch in str(glob or "").strip():
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        if depth == 0 and (ch in ",;" or ch.isspace()):
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    out = []
    for part in (p for p in parts if p):
        m = re.match(r"^(.*?)\{([^}]*)\}(.*)$", part)
        if m:
            out.extend(_globs(m.group(1) + alt + m.group(3)) for alt in m.group(2).split(","))
        else:
            out.append([part])
    return [g for group in out for g in group]


# ---------- search ----------

@tool(
    "grep",
    "Search file CONTENTS with a regular expression across a project (fast, like ripgrep; skips node_modules, build output "
    "and binary files). Use it to find where something is defined or used before changing it. Returns path:line: text.",
    {"pattern": "string: Regular expression, e.g. 'def load_' or 'useState\\(' (escape regex characters for literal text)",
     "path?": "string: Folder or file to search (default: the project folder)",
     "glob?": "string: Only files matching, e.g. '*.py', '*.{ts,tsx}' or 'src/**/*.js'",
     "ignore_case?": "boolean: Case-insensitive",
     "context?": "integer: Lines of context before and after each match (0-5)",
     "output?": "string[content,files,count]: Matching lines (default), only file names, or match counts per file",
     "max_results?": "integer: Limit (default 100)"},
    group="files",
)
def grep(ctx, pattern, path="", glob="", ignore_case=False, context=0, output="content", max_results=100):
    base = resolve_path(path or work_dir(), must_exist=True)
    limit = max(1, min(int(max_results or 100), 1000))
    context = max(0, min(int(context or 0), 5))
    try:
        rx = re.compile(pattern, re.I if ignore_case else 0)
    except re.error as exc:
        raise ToolError("Invalid regular expression (%s). Escape characters like ( ) [ ] . * + ? for literal text." % exc)
    root = base if os.path.isdir(base) else os.path.dirname(base)
    files = [base] if os.path.isfile(base) else [f for f in _walk(base) if not glob or any(_glob_match(_rel(f, base), g) for g in _globs(glob))]
    lines_out, counts, total, started = [], {}, 0, time.time()
    for full in files:
        if time.time() - started > 25:
            lines_out.append("… stopped after 25 seconds; narrow the search with path or glob")
            break
        try:
            if os.path.getsize(full) > MAX_FILE or _is_binary(full):
                continue
            with open(full, encoding="utf-8", errors="replace") as fh:
                text = fh.read().splitlines()
        except OSError:
            continue
        hits = [i for i, line in enumerate(text) if rx.search(line)]
        if not hits:
            continue
        rel = _rel(full, root)
        counts[rel] = len(hits)
        total += len(hits)
        if output == "content":
            shown = set()
            for i in hits:
                for j in range(max(0, i - context), min(len(text), i + context + 1)):
                    if j in shown:
                        continue
                    shown.add(j)
                    sep = ":" if j == i else "-"
                    lines_out.append("%s%s%d%s %s" % (rel, sep, j + 1, sep, text[j][:300]))
                if len(lines_out) >= limit:
                    break
        if (output == "content" and len(lines_out) >= limit) or (output != "content" and len(counts) >= limit):
            lines_out.append("… more matches not shown (raise max_results or narrow the search)")
            break
    if not counts:
        return Result("No matches for /%s/ in %s%s." % (pattern, base, (" (" + glob + ")") if glob else ""))
    head = "%d match%s in %d file%s under %s" % (total, "" if total == 1 else "es", len(counts), "" if len(counts) == 1 else "s", root)
    if output == "files":
        body = "\n".join(counts)
    elif output == "count":
        body = "\n".join("%s: %d" % kv for kv in counts.items())
    else:
        body = "\n".join(lines_out)
    return Result(head + "\n" + body)


@tool(
    "glob",
    "Find files by NAME pattern in a project, newest first. '*.css' matches anywhere; 'src/**/*.tsx' matches paths. "
    "Skips node_modules, build output and hidden folders.",
    {"pattern": "string: Glob, e.g. '*.py', '**/test_*.py', 'src/**/*.{ts,tsx}'", "path?": "string: Folder (default: the project folder)",
     "max_results?": "integer: Limit (default 200)"},
    group="files",
)
def glob(ctx, pattern, path="", max_results=200):
    base = resolve_path(path or work_dir(), must_exist=True)
    if not os.path.isdir(base):
        raise ToolError("Not a folder: %s" % base)
    pats = _globs(pattern) or ["*"]
    found = [f for f in _walk(base) if any(_glob_match(_rel(f, base), p) for p in pats)]
    found.sort(key=lambda f: -os.path.getmtime(f) if os.path.exists(f) else 0)
    limit = max(1, min(int(max_results or 200), 2000))
    more = "\n… %d more" % (len(found) - limit) if len(found) > limit else ""
    if not found:
        return Result("No files match %s under %s." % (pattern, base))
    return Result("%d file(s) under %s:\n%s%s" % (len(found), base, "\n".join(_rel(f, base) for f in found[:limit]), more))


# ---------- a map of the codebase ----------

JS_PATTERNS = [
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)"), "function {0}({1})"),
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"), "class {0}"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\(([^)]*)\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>"), "{0}({1})"),
    (re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(interface|type|enum)\s+([A-Za-z_$][\w$]*)"), "{0} {1}"),
    (re.compile(r"^\s{2,}(?:public\s+|private\s+|protected\s+|static\s+|async\s+|get\s+|set\s+|override\s+)*([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*(?::\s*[^{]+)?\{\s*$"), "  .{0}({1})"),
]
JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "else", "with", "constructor"}
OTHER_PATTERNS = {
    ".go": [(re.compile(r"^func\s+(\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(([^)]*)\)"), "func {0}{1}({2})"),
            (re.compile(r"^type\s+([A-Za-z_]\w*)\s+(struct|interface)"), "type {0} {1}")],
    ".rs": [(re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"), "fn {0}"),
            (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(struct|enum|trait|mod)\s+([A-Za-z_]\w*)"), "{0} {1}"),
            (re.compile(r"^\s*impl(?:<[^>]*>)?\s+([^{]+?)\s*\{"), "impl {0}")],
    ".rb": [(re.compile(r"^\s*(class|module)\s+([A-Z][\w:]*)"), "{0} {1}"), (re.compile(r"^\s*def\s+([\w.?!]+)"), "  def {0}")],
    ".php": [(re.compile(r"^\s*(?:abstract\s+|final\s+)?(class|interface|trait)\s+(\w+)"), "{0} {1}"),
             (re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+(\w+)\s*\(([^)]*)\)"), "function {0}({1})")],
    ".java": [(re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+|static\s+)*(class|interface|enum|record)\s+(\w+)"), "{0} {1}"),
              (re.compile(r"^\s+(?:public|private|protected)\s+(?:static\s+|final\s+|synchronized\s+|abstract\s+)*[\w<>\[\],\s]+\s+(\w+)\s*\(([^)]*)\)"), "  {0}({1})")],
    ".kt": [(re.compile(r"^\s*(?:data\s+|sealed\s+|open\s+|abstract\s+)?(class|interface|object)\s+(\w+)"), "{0} {1}"),
            (re.compile(r"^\s*(?:override\s+|private\s+|suspend\s+)*fun\s+(\w+)\s*\(([^)]*)\)"), "fun {0}({1})")],
    ".cs": [(re.compile(r"^\s*(?:public\s+|internal\s+|private\s+)?(?:static\s+|abstract\s+|sealed\s+|partial\s+)*(class|interface|enum|struct|record)\s+(\w+)"), "{0} {1}"),
            (re.compile(r"^\s+(?:public|private|protected|internal)\s+(?:static\s+|async\s+|override\s+|virtual\s+)*[\w<>\[\],?\s]+\s+(\w+)\s*\(([^)]*)\)"), "  {0}({1})")],
    ".swift": [(re.compile(r"^\s*(?:public\s+|private\s+|open\s+|final\s+)*(class|struct|enum|protocol|extension)\s+(\w+)"), "{0} {1}"),
               (re.compile(r"^\s*(?:public\s+|private\s+|static\s+|override\s+)*func\s+(\w+)\s*\(([^)]*)\)"), "  func {0}({1})")],
    ".c": [(re.compile(r"^(?!\s)(?:static\s+|inline\s+|extern\s+)*[\w\*\s]+?\b(\w+)\s*\(([^;{]*)\)\s*\{?\s*$"), "{0}({1})"),
           (re.compile(r"^\s*(?:typedef\s+)?(struct|enum|union)\s+(\w+)"), "{0} {1}")],
    ".lua": [(re.compile(r"^\s*(?:local\s+)?function\s+([\w.:]+)\s*\(([^)]*)\)"), "function {0}({1})")],
    ".dart": [(re.compile(r"^\s*(?:abstract\s+)?(class|mixin|enum)\s+(\w+)"), "{0} {1}")],
}
for _ext in (".cpp", ".cc", ".h", ".hpp", ".cxx"):
    OTHER_PATTERNS[_ext] = OTHER_PATTERNS[".c"] + [(re.compile(r"^\s*(?:template\s*<[^>]*>\s*)?(class|struct|namespace)\s+(\w+)"), "{0} {1}")]
OTHER_PATTERNS[".kts"] = OTHER_PATTERNS[".kt"]
JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}
CODE_EXT = JS_EXT | set(OTHER_PATTERNS) | {".py", ".pyw", ".md"}
MAP_ALSO = {".html", ".css", ".scss", ".json", ".toml", ".yaml", ".yml", ".sql", ".sh", ".ps1", ".gradle", ".xml"}


def _short_args(args, n=60):
    args = " ".join(str(args or "").split())
    return args if len(args) <= n else args[:n - 1] + "…"


def _python_symbols(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return ["(syntax error: run check_code)"]
    out = []

    def sig(fn):
        names = [a.arg for a in fn.args.posonlyargs + fn.args.args if a.arg not in ("self", "cls")]
        if fn.args.vararg:
            names.append("*" + fn.args.vararg.arg)
        names += [a.arg for a in fn.args.kwonlyargs]
        if fn.args.kwarg:
            names.append("**" + fn.args.kwarg.arg)
        return _short_args(", ".join(names))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append("def %s(%s)" % (node.name, sig(node)))
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (not n.name.startswith("_") or n.name == "__init__")]
            out.append("class %s%s" % (node.name, (": " + ", ".join(methods[:14]) + (" …" if len(methods) > 14 else "")) if methods else ""))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id.isupper():
            out.append(node.targets[0].id)
    return out


def _regex_symbols(text, patterns, js=False):
    out = []
    for line in text.splitlines()[:6000]:
        for rx, fmt in patterns:
            m = rx.match(line)
            if not m:
                continue
            groups = [_short_args(g) if g else "" for g in m.groups()]
            if js and groups and groups[0] in JS_KEYWORDS:
                break
            out.append(fmt.format(*groups).replace("  ", " ", 1) if not fmt.startswith("  ") else fmt.format(*groups))
            break
    return out


def file_symbols(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in CODE_EXT:
        return []
    try:
        if os.path.getsize(path) > MAX_FILE:
            return ["(large file)"]
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    if ext in (".py", ".pyw"):
        return _python_symbols(text)
    if ext == ".md":
        return [("  " * (len(m.group(1)) - 1)) + m.group(2).strip() for m in re.finditer(r"^(#{1,2})\s+(.+)$", text, re.M)][:12]
    if ext in JS_EXT:
        return _regex_symbols(text, JS_PATTERNS, js=True)
    return _regex_symbols(text, OTHER_PATTERNS[ext])


def project_files(base):
    """The project's files: git's list when it is a repository (respects .gitignore), else a walk."""
    if gitops.binary() and gitops.is_repo(base):
        try:
            out = gitops.run(["ls-files", "--cached", "--others", "--exclude-standard"], base, timeout=20)
            files = [os.path.join(base, *line.split("/")) for line in out.splitlines() if line.strip()]
            return [f for f in files if os.path.isfile(f) and not any(part in SKIP_DIRS for part in f.split(os.sep))]
        except gitops.GitError:
            pass
    return list(_walk(base))


def repo_map_text(base, max_chars=12000, focus=""):
    files = project_files(base)
    focus_rx = re.compile(re.escape(focus), re.I) if focus else None
    code = [f for f in files if os.path.splitext(f)[1].lower() in CODE_EXT | MAP_ALSO]
    other = len(files) - len(code)
    # files the focus names, then source files, then the rest; shallow paths first
    code.sort(key=lambda f: (not (focus_rx and focus_rx.search(_rel(f, base))), os.path.splitext(f)[1].lower() not in CODE_EXT,
                             _rel(f, base).count("/"), _rel(f, base).lower()))
    lines, size, skipped = [], 0, 0
    for f in code:
        rel = _rel(f, base)
        syms = file_symbols(f)[:30]
        block = rel + "".join("\n  " + s for s in syms)
        if size + len(block) > max_chars:
            skipped += 1
            continue
        lines.append(block)
        size += len(block) + 1
    footer = []
    if skipped:
        footer.append("… %d more source files not shown (raise max_chars, or use focus / glob)" % skipped)
    if other:
        footer.append("(%d other files: images, data, assets)" % other)
    return "\n".join(lines + footer)


@tool(
    "repo_map",
    "A compact map of a codebase: every source file with its classes, functions and signatures (and headings of docs). "
    "Call it first in an unfamiliar project to see how it is organised, then read only the files you need.",
    {"path?": "string: Project folder (default: the project folder)", "focus?": "string: Put files whose path contains this first, e.g. 'auth'",
     "max_chars?": "integer: Size budget (default 12000)"},
    group="files",
)
def repo_map(ctx, path="", focus="", max_chars=12000):
    base = resolve_path(path or work_dir(), must_exist=True)
    if not os.path.isdir(base):
        raise ToolError("Not a folder: %s" % base)
    text = repo_map_text(base, max(2000, min(int(max_chars or 12000), 60000)), focus)
    return Result("Map of %s:\n%s" % (base, text or "(no source files yet)"))


# ---------- patches ----------

class _Op:
    def __init__(self, kind, path, move_to=""):
        self.kind = kind  # add | update | delete
        self.path = path
        self.move_to = move_to
        self.hunks = []  # [(old_lines, new_lines, hint_line)]
        self.content = []  # for add
        self.raw = []  # for add in the Codex format: (had a "+", text)

    def added(self):
        """An added file's lines: the "+" lines (as the format says), or every line if the "+" were left out."""
        if not self.raw:
            return self.content
        if any(plus for plus, _ in self.raw):
            return [text for plus, text in self.raw if plus]
        lines = [text for _, text in self.raw]
        while lines and not lines[-1].strip():
            lines.pop()
        return lines


def _parse_codex(text):
    ops, cur, hunk = [], None, None
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if line.startswith("*** End Patch"):
            cur = None
            continue
        if line.startswith("*** Begin Patch") or line.startswith("*** End of File"):
            continue
        m = re.match(r"^\*\*\* (Add|Update|Delete) File:\s*(.+)$", line)
        if m:
            cur = _Op(m.group(1).lower(), m.group(2).strip())
            ops.append(cur)
            hunk = None
            continue
        m = re.match(r"^\*\*\* Move to:\s*(.+)$", line)
        if m and cur:
            cur.move_to = m.group(1).strip()
            continue
        if cur is None:
            continue
        if cur.kind == "add":
            cur.raw.append((line.startswith("+"), line[1:] if line.startswith("+") else line))
            continue
        if line.startswith("@@"):
            hunk = ([], [], 0)
            cur.hunks.append(hunk)
            continue
        if hunk is None:
            hunk = ([], [], 0)
            cur.hunks.append(hunk)
        if line.startswith("-"):
            hunk[0].append(line[1:])
        elif line.startswith("+"):
            hunk[1].append(line[1:])
        else:
            body = line[1:] if line.startswith(" ") else line
            hunk[0].append(body)
            hunk[1].append(body)
    return ops


def _parse_unified(text):
    ops, cur, hunk, old_path = [], None, None, ""
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if line.startswith("diff --git") or line.startswith("index ") or line.startswith("new file mode") or line.startswith("deleted file mode"):
            continue
        if line.startswith("--- "):
            old_path = line[4:].split("\t")[0].strip()
            continue
        if line.startswith("+++ "):
            new_path = line[4:].split("\t")[0].strip()
            strip = lambda p: p[2:] if p[:2] in ("a/", "b/") else p  # noqa: E731
            if new_path == "/dev/null":
                cur = _Op("delete", strip(old_path))
            elif old_path == "/dev/null":
                cur = _Op("add", strip(new_path))
            else:
                cur = _Op("update", strip(old_path), move_to=strip(new_path) if strip(new_path) != strip(old_path) else "")
            ops.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        m = re.match(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", line)
        if m:
            hunk = ([], [], int(m.group(1)))
            cur.hunks.append(hunk)
            continue
        if hunk is None or line.startswith("\\"):
            continue
        if cur.kind == "add":
            if line.startswith("+"):
                cur.content.append(line[1:])
            continue
        if line.startswith("-"):
            hunk[0].append(line[1:])
        elif line.startswith("+"):
            hunk[1].append(line[1:])
        elif line.startswith(" ") or line == "":
            hunk[0].append(line[1:])
            hunk[1].append(line[1:])
    return ops


def find_block(lines, block, hint=0, start=0):
    """Index where block (a list of lines) occurs in lines: exact, then ignoring trailing spaces, then ignoring
    indentation. Several exact matches: the one nearest to the hint line. Returns (index, how) or (-1, "")."""
    if not block:
        return (min(max(hint - 1, 0), len(lines)) if hint else len(lines)), "exact"
    n = len(block)
    for how, norm in (("exact", lambda s: s), ("trailing spaces", str.rstrip), ("indentation", str.strip)):
        want = [norm(b) for b in block]
        hits = [i for i in range(start, len(lines) - n + 1) if [norm(x) for x in lines[i:i + n]] == want]
        if not hits and start:
            hits = [i for i in range(0, len(lines) - n + 1) if [norm(x) for x in lines[i:i + n]] == want]
        if hits:
            return min(hits, key=lambda i: abs(i + 1 - hint)) if hint else hits[0], how
    return -1, ""


def closest(lines, block):
    """(line number, text) of the region most like block, to show the model what is really there."""
    if not block or not lines:
        return 0, ""
    n = len(block)
    target = "\n".join(s.strip() for s in block)
    best, where = 0.0, 0
    step = 1 if len(lines) < 4000 else 3
    for i in range(0, max(1, len(lines) - n + 1), step):
        ratio = difflib.SequenceMatcher(None, target, "\n".join(s.strip() for s in lines[i:i + n]), autojunk=False).quick_ratio()
        if ratio > best:
            best, where = ratio, i
    if best < 0.5:
        return 0, ""
    return where + 1, "\n".join("%5d\t%s" % (where + 1 + k, s) for k, s in enumerate(lines[where:where + n + 2]))


def _reindent(new_lines, old_block, actual):
    """When a block matched only ignoring indentation, shift the replacement by the same amount."""
    def indent(s):
        return s[:len(s) - len(s.lstrip())]
    first_old = next((s for s in old_block if s.strip()), "")
    first_real = next((s for s in actual if s.strip()), "")
    have, want = indent(first_old), indent(first_real)
    if have == want:
        return new_lines
    out = []
    for s in new_lines:
        if not s.strip():
            out.append(s)
        elif s.startswith(have):
            out.append(want + s[len(have):])
        else:
            out.append(s)
    return out


def _read_text(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", "replace")
    eol = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), eol


def _write_text(path, text, eol="\n"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace("\n", eol) if eol != "\n" else text)


def diff_display(changes, root, checks=""):
    """A chat card with the changes as a diff. changes: [(path, before, after)] (None = file absent)."""
    chunks = []
    for path, before, after in changes:
        rel = _rel(path, root) if root else os.path.basename(path)
        a = [] if before is None else before.splitlines()
        b = [] if after is None else after.splitlines()
        head = "diff --git a/%s b/%s\n%s" % (rel, rel, "new file mode 100644\n" if before is None else "deleted file mode 100644\n" if after is None else "")
        body = "\n".join(difflib.unified_diff(a, b, "a/" + rel, "b/" + rel, n=2, lineterm=""))
        chunks.append(head + body)
    files = gitops.parse_diff("\n".join(chunks), max_lines=400)
    return {"type": "diff", "files": files, "checks": checks}


def diff_summary(before, after, limit=40):
    """A few lines of unified diff for the model, so it sees exactly what changed."""
    lines = list(difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(), n=1, lineterm=""))[2:]
    if len(lines) > limit:
        lines = lines[:limit] + ["… (%d more diff lines)" % (len(lines) - limit)]
    return "\n".join(lines)


def apply_patch_text(patch):
    """Apply a patch (Codex '*** Begin Patch' format or a unified diff) atomically. Returns [(path, before, after)]."""
    text = str(patch or "").replace("\r\n", "\n")
    ops = _parse_codex(text) if "*** Begin Patch" in text or re.search(r"^\*\*\* (Add|Update|Delete) File:", text, re.M) else _parse_unified(text)
    if not ops:
        raise ToolError("No file changes found in the patch. Use '*** Begin Patch' / '*** Update File: path' / '@@' / -old / +new "
                        "lines / '*** End Patch', or a unified diff with ---/+++ headers and @@ hunks.")
    planned = []
    for op in ops:
        path = resolve_path(op.path)
        if op.kind == "add":
            if os.path.exists(path):
                raise ToolError("%s already exists; use '*** Update File' to change it." % op.path)
            content = op.added()
            planned.append((path, None, "\n".join(content) + ("\n" if content else "")))
            continue
        if not os.path.isfile(path):
            raise ToolError("%s does not exist (to create it use '*** Add File')." % op.path)
        before, eol = _read_text(path)
        if op.kind == "delete":
            planned.append((path, before, None))
            continue
        lines = before.split("\n")
        cursor = 0
        for number, (old, new, hint) in enumerate(op.hunks, 1):
            # trailing empty context lines are often added by accident
            while old and new and old[-1] == "" and new[-1] == "":
                old, new = old[:-1], new[:-1]
            if old == new:
                continue
            at, how = find_block(lines, old, hint, cursor)
            if at < 0:
                line, near = closest(lines, old)
                raise ToolError("Hunk %d of %s does not match the file.%s Read the file again and resend the patch with lines that exist."
                                % (number, op.path, ("\nClosest text, at line %d:\n%s" % (line, near)) if near else ""))
            actual = lines[at:at + len(old)]
            repl = _reindent(new, old, actual) if how == "indentation" else new
            lines[at:at + len(old)] = repl
            cursor = at + len(repl)
        after = "\n".join(lines)
        target = resolve_path(op.move_to) if op.move_to else path
        if op.move_to:
            planned.append((path, before, None))
            planned.append((target, None, after))
        else:
            planned.append((path, before, after))
    # nothing is written until every hunk of every file applies
    for path, before, after in planned:
        if after is None:
            os.remove(path)
        else:
            eol = "\n"
            if before is not None:
                try:
                    eol = _read_text(path)[1]
                except OSError:
                    pass
            _write_text(path, after, eol)
    return planned


@tool(
    "apply_patch",
    "Change several files (or many places in one file) at once with a patch. Format:\n"
    "*** Begin Patch\n*** Update File: path/to/file.py\n@@\n context line\n-old line\n+new line\n*** Add File: path/new.txt\n"
    "+first line\n*** Delete File: path/old.txt\n*** End Patch\n"
    "Every '-' and ' ' line must match the file (a few unchanged lines around each change let it find the place). "
    "Unified diffs (---/+++/@@) work too. All or nothing: if one change does not fit, no file is written.",
    {"patch": "string: The patch text"},
    group="files",
    risk="write",
)
def apply_patch(ctx, patch):
    planned = apply_patch_text(patch)
    root = project_dir() or os.path.commonpath([p for p, _, _ in planned]) if planned else ""
    if root and os.path.isfile(root):
        root = os.path.dirname(root)
    touched = [p for p, _, after in planned if after is not None]
    hooked, hook_failed = after_edit_hooks(touched)
    for p in touched:
        _seen(ctx, p, wrote=True)
    checks = codecheck.note(touched)
    lines = []
    for path, before, after in planned:
        kind = "created" if before is None else "deleted" if after is None else "updated"
        lines.append("%s %s" % (kind, path))
        if before is not None and after is not None:
            lines.append(diff_summary(before, after, 30))
    text = "Applied the patch:\n" + "\n".join(lines) + ("\n\n" + checks if checks else "") + ("\n\n" + hooked if hooked else "")
    return Result(text, display=diff_display(planned, root, checks), files=touched, ok=not checks.startswith("PROBLEMS") and not hook_failed)


def after_edit_hooks(paths):
    """The user's after_edit hooks (formatters, linters) for files the agent just wrote: (note, a blocking hook failed)."""
    from .. import hooks
    if not paths:
        return "", False
    results = hooks.run("after_edit", project_dir() or os.path.dirname(paths[0]), files=paths)
    if not results:
        return "", False
    failed = hooks.blocking_failures(results)
    note = hooks.summary(results)
    if failed:
        note += "\nA required hook failed: fix the problem it reports."
    return note, bool(failed)


def drop_bytecode(path):
    """Python reuses __pycache__ when a file keeps its size and second of modification: a quick one-character edit
    would run the old code. Forget the cached bytecode of a module the agent just changed."""
    if not path.lower().endswith(".py"):
        return
    cache = os.path.join(os.path.dirname(path), "__pycache__")
    stem = os.path.splitext(os.path.basename(path))[0] + "."
    try:
        for name in os.listdir(cache):
            if name.startswith(stem) and name.endswith(".pyc"):
                os.remove(os.path.join(cache, name))
    except OSError:
        pass


def _seen(ctx, path, wrote=False):
    """Remember the file's state as the agent last saw it (to notice changes made by someone else since)."""
    if wrote:
        drop_bytecode(path)
    agent = getattr(ctx, "agent", None)
    if agent is None:
        return
    try:
        getattr(agent, "seen", {})[os.path.normcase(path)] = os.path.getmtime(path)
    except OSError:
        pass


def stale(ctx, path):
    """True when the file changed on disk after the agent last read or wrote it."""
    agent = getattr(ctx, "agent", None)
    seen = getattr(agent, "seen", None) if agent else None
    if not seen:
        return False
    key = os.path.normcase(path)
    if key not in seen:
        return False
    try:
        return abs(os.path.getmtime(path) - seen[key]) > 0.001
    except OSError:
        return False


# ---------- checks and tests ----------

@tool(
    "check_code",
    "Check source files for syntax errors without running them (Python, JavaScript, JSON, CSS, HTML links to missing "
    "files, YAML, TOML, PHP, shell...). Give a file or a folder (default: the project).",
    {"path?": "string: File or folder"},
    group="files",
)
def check_code(ctx, path=""):
    target = resolve_path(path or work_dir(), must_exist=True)
    files = [target] if os.path.isfile(target) else [f for f in project_files(target) if codecheck.supported(f)][:400]
    if not files:
        return Result("No files with checks in %s." % target)
    found = []
    for f in files:
        for problem in codecheck.check(f):
            found.append("%s: %s" % (_rel(f, target if os.path.isdir(target) else os.path.dirname(target)), problem))
    if not found:
        return Result("No problems in %d file(s)." % len(files))
    return Result("%d problem(s) in %d checked file(s):\n%s" % (len(found), len(files), "\n".join("- " + p for p in found[:80])), ok=False)


def _package_runner(base):
    if os.path.exists(os.path.join(base, "pnpm-lock.yaml")) and shutil.which("pnpm"):
        return "pnpm"
    if os.path.exists(os.path.join(base, "yarn.lock")) and shutil.which("yarn"):
        return "yarn"
    if (os.path.exists(os.path.join(base, "bun.lockb")) or os.path.exists(os.path.join(base, "bun.lock"))) and shutil.which("bun"):
        return "bun run"
    return "npm"


def detect_test_command(base):
    """The project's own test command, from its files (package.json, pytest, go.mod, Cargo.toml...), or ""."""
    def has(*names):
        return any(os.path.exists(os.path.join(base, n)) for n in names)

    pkg = os.path.join(base, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, encoding="utf-8") as fh:
                scripts = (json.load(fh).get("scripts") or {})
        except (OSError, ValueError):
            scripts = {}
        test = str(scripts.get("test") or "")
        if test and "no test specified" not in test:
            return "%s test" % _package_runner(base)
    python = "python" if IS_WINDOWS else "python3"
    pyproject = ""
    if has("pyproject.toml"):
        try:
            with open(os.path.join(base, "pyproject.toml"), encoding="utf-8") as fh:
                pyproject = fh.read()
        except OSError:
            pass
    tests_dir = any(os.path.isdir(os.path.join(base, d)) for d in ("tests", "test"))
    if has("pytest.ini", "conftest.py", "tox.ini") or "[tool.pytest" in pyproject or (tests_dir and _has_pytest()):
        return "%s -m pytest -q" % python
    if tests_dir and any(f.endswith(".py") for d in ("tests", "test") if os.path.isdir(os.path.join(base, d)) for f in os.listdir(os.path.join(base, d))):
        return "%s -m unittest discover -s %s" % (python, "tests" if os.path.isdir(os.path.join(base, "tests")) else "test")
    if has("go.mod"):
        return "go test ./..."
    if has("Cargo.toml"):
        return "cargo test"
    if has("deno.json", "deno.jsonc"):
        return "deno test"
    if has("pom.xml"):
        return "mvn -q test"
    if has("gradlew", "gradlew.bat"):
        return ".\\gradlew.bat test" if IS_WINDOWS else "./gradlew test"
    if has("build.gradle", "build.gradle.kts"):
        return "gradle test"
    if has("mix.exs"):
        return "mix test"
    if any(f.endswith((".sln", ".csproj")) for f in os.listdir(base)):
        return "dotnet test"
    if has("Gemfile") and tests_dir is False and os.path.isdir(os.path.join(base, "spec")):
        return "bundle exec rspec"
    if has("composer.json") and os.path.exists(os.path.join(base, "vendor", "bin", "phpunit")):
        return "vendor/bin/phpunit"
    if has("Makefile"):
        try:
            with open(os.path.join(base, "Makefile"), encoding="utf-8", errors="replace") as fh:
                if re.search(r"^test:", fh.read(), re.M):
                    return "make test"
        except OSError:
            pass
    return ""


def _has_pytest():
    try:
        import importlib.util
        return importlib.util.find_spec("pytest") is not None
    except Exception:  # noqa: BLE001
        return False


FAIL_LINE = re.compile(r"(FAIL|FAILED|Error|ERROR|failed|AssertionError|✗|✕|panicked|Traceback|expected .* (got|received))")


@tool(
    "run_tests",
    "Run the project's tests (found automatically: npm/pnpm/yarn test, pytest, go test, cargo test, mvn, gradle, dotnet, "
    "deno, make test...) and report what failed. Give command to run something else, e.g. 'pytest tests/test_api.py -x'.",
    {"command?": "string: Test command (default: detected)", "path?": "string: Project folder (default: the project)",
     "timeout?": "integer: Seconds (default 600)"},
    group="code",
    risk="danger",
)
def run_tests(ctx, command="", path="", timeout=600):
    base = resolve_path(path or work_dir(), must_exist=True)
    command = str(command or "").strip() or detect_test_command(base)
    if not command:
        raise ToolError("No test setup found in %s (no test script, pytest, go.mod, Cargo.toml...). Add tests, or give the command." % base)
    from .code import _execute
    shell = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command] if IS_WINDOWS else ["bash", "-lc", command]
    env = dict(os.environ, CI="1", FORCE_COLOR="0", NO_COLOR="1", PYTHONIOENCODING="utf-8")
    code, out, err, _, elapsed = _execute(shell, base, min(int(timeout or 600), 3600), env=env, track=False)
    output = (out + ("\n" + err if err.strip() else "")).strip()
    fails = [ln.strip() for ln in output.splitlines() if FAIL_LINE.search(ln)][:25]
    verdict = "PASSED" if code == 0 else "FAILED (exit %s)" % code
    text = "%s: %s in %.1fs\n" % (command, verdict, elapsed)
    if code != 0 and fails:
        text += "Failure lines:\n" + "\n".join("  " + f[:300] for f in fails) + "\n"
    text += "\nOutput (end):\n" + output[-7000:]
    display = {"type": "code", "language": "tests", "code": command, "stdout": out[-6000:], "stderr": err[-3000:], "exit": code}
    return Result(text, ok=(code == 0), display=display)


# ---------- background processes ----------

URL_RX = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\])(?::\d+)?[^\s'\"<>)]*", re.I)
_read_marks = {}  # term id -> how much output the agent has seen


def _term(ctx, pid):
    term = gitops.TERMS.get(str(pid or "").strip())
    if not term:
        raise ToolError("No background process %r. Running ones: %s" % (pid, ", ".join(t.id for t in gitops.TERMS.values() if t.status == "running") or "none"))
    return term


def start_background(ctx, command, folder):
    project = getattr(getattr(ctx, "agent", None), "project", "") or ""
    try:
        term = gitops.term_start(project, folder, command)
    except gitops.GitError as exc:
        raise ToolError(str(exc))
    deadline = time.time() + 8
    while time.time() < deadline and term.status == "running" and not URL_RX.search(term.output):
        time.sleep(0.25)
    if term.status == "running" and URL_RX.search(term.output):
        time.sleep(0.4)  # let the rest of the banner arrive
    _read_marks[term.id] = len(term.output)
    urls = list(dict.fromkeys(u.rstrip(".,;") for u in URL_RX.findall(term.output)))
    state = "still running" if term.status == "running" else "already ended (%s, exit %s)" % (term.status, term.code)
    text = "Started in the background (process %s, %s) in %s: %s\n" % (term.id, state, folder, command)
    if urls:
        text += "Serving at: %s\n" % ", ".join(urls[:3])
    text += "Output so far:\n" + (term.output[-4000:] or "(none yet)")
    text += "\nRead new output with process_output(id='%s'); stop it with process_stop(id='%s')." % (term.id, term.id)
    display = {"type": "code", "language": "background", "code": command, "stdout": term.output[-3000:], "stderr": "",
               "exit": 0 if term.status == "running" else term.code}
    return Result(text, ok=term.status == "running" or term.code == 0, display=display)


@tool(
    "process_output",
    "New output of a background process started with run_shell(background=true), and whether it still runs. Without id: "
    "the list of background processes.",
    {"id?": "string: Process id", "all?": "boolean: The whole output so far instead of only what is new"},
    group="code",
)
def process_output(ctx, id="", all=False):
    if not id:
        rows = ["%s  %s  %s  (%s)" % (t.id, t.status, t.command[:80], time.strftime("%H:%M:%S", time.localtime(t.started)))
                for t in sorted(gitops.TERMS.values(), key=lambda t: t.started)]
        return Result("Background processes:\n" + ("\n".join(rows) if rows else "(none)"))
    term = _term(ctx, id)
    start = 0 if all else _read_marks.get(term.id, 0)
    if start > len(term.output):
        start = 0  # the buffer was trimmed
    chunk = term.output[start:]
    _read_marks[term.id] = len(term.output)
    status = "running" if term.status == "running" else "%s (exit %s)" % (term.status, term.code)
    return Result("Process %s (%s): %s\n%s" % (term.id, term.command[:120], status, chunk[-12000:] or "(no new output)"))


@tool(
    "process_stop",
    "Stop a background process (a dev server, watcher...) started with run_shell(background=true).",
    {"id": "string: Process id"},
    group="code",
    risk="write",
)
def process_stop(ctx, id):
    term = _term(ctx, id)
    if not term.stop():
        return Result("Process %s had already ended (%s)." % (term.id, term.status))
    for _ in range(40):
        if term.status not in ("running", "stopping"):
            break
        time.sleep(0.1)
    return Result("Stopped process %s (%s)." % (term.id, term.command[:120]))


# ---------- a second model: opinions and reviews ----------

@tool(
    "ask_expert",
    "Ask an independent second model (a senior engineer on another provider) for a second opinion: a bug you cannot find "
    "after two tries, a design decision, a tricky algorithm, or your plan before a big change. It sees only what you send: "
    "put the error, the relevant code and what you tried in context, or list files to include.",
    {"question": "string: What you want to know", "context?": "string: Errors, code, what you tried",
     "files?": "array<string>: Files whose contents to include (max 8)"},
    group="code",
)
def ask_expert(ctx, question, context="", files=None):
    from .. import coding, llm
    parts = [str(question or "").strip()]
    if context:
        parts.append("Context:\n" + str(context)[:30000])
    budget = 60000
    for path in (files or [])[:8]:
        full = resolve_path(path, must_exist=True)
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                text = fh.read(budget)
        except OSError as exc:
            raise ToolError("Cannot read %s: %s" % (full, exc))
        budget -= len(text)
        parts.append("File %s:\n```\n%s\n```" % (_rel(full, project_dir() or os.path.dirname(full)), text))
        if budget <= 0:
            break
    current = getattr(getattr(ctx, "agent", None), "model", "") or ""
    try:
        answer, model = coding.ask("\n\n".join(parts), current=current)
    except llm.LLMError as exc:
        raise ToolError("The second model did not answer: %s" % exc)
    return Result("Second opinion from %s:\n\n%s" % (llm.label(model), answer or "(no answer)"))


@tool(
    "review_changes",
    "Code review of a project's changes by an independent second model: bugs, security problems, missing edge cases. "
    "Reviews the uncommitted changes (or against base, a branch or commit), or what the last agent run changed. Use it "
    "before reporting a bigger job as done, or when the user asks for a review.",
    {"path?": "string: Project folder (default: the project)", "base?": "string: Compare with this branch or commit",
     "focus?": "string: What to look at closely, e.g. 'the auth changes'"},
    group="code",
)
def review_changes(ctx, path="", base="", focus=""):
    from .. import coding, llm
    folder = resolve_path(path or work_dir(), must_exist=True)
    agent = getattr(ctx, "agent", None)
    try:
        out = coding.review(folder, getattr(agent, "project", "") or "", base, getattr(agent, "model", "") or "", focus)
    except (llm.LLMError, gitops.GitError) as exc:
        raise ToolError(str(exc))
    if not out["review"]:
        return Result("Nothing to review: no uncommitted changes and no recent agent run in %s." % folder)
    return Result("Review of %s by %s:\n\n%s" % (out["scope"], llm.label(out["model"]), out["review"]))


# ---------- git (read-only) ----------

@tool(
    "git_read",
    "Read a git repository without changing it: status, diff (uncommitted changes, or against a ref), log, show "
    "(a commit), blame (who changed each line of a file), branches.",
    {"action": "string[status,diff,log,show,blame,branches]: What to read", "path?": "string: Repository folder (default: the project)",
     "ref?": "string: Commit, branch or range (diff: compare with it; show: the commit; log: e.g. main..HEAD)",
     "file?": "string: Limit to one file (diff, log, blame)", "staged?": "boolean: diff: only staged changes",
     "limit?": "integer: log: number of commits (default 20)"},
    group="code",
)
def git_read(ctx, action, path="", ref="", file="", staged=False, limit=20):
    base = resolve_path(path or work_dir(), must_exist=True)
    if not gitops.is_repo(base):
        raise ToolError("%s is not a git repository." % base)
    ref = str(ref or "").strip()
    if ref.startswith("-"):
        raise ToolError("Give a commit, branch or range as ref.")
    files = ["--", file] if file else []
    if action == "status":
        args = ["status", "--short", "--branch"]
    elif action == "diff":
        args = ["diff", "--stat", "--patch"] + (["--cached"] if staged else []) + ([ref] if ref else []) + files
    elif action == "log":
        args = ["log", "--oneline", "--decorate", "-n", str(max(1, min(int(limit or 20), 200)))] + ([ref] if ref else []) + files
    elif action == "show":
        args = ["show", "--stat", "--patch", ref or "HEAD"]
    elif action == "blame":
        if not file:
            raise ToolError("blame needs file.")
        args = ["blame", "--date=short"] + ([ref] if ref else []) + ["--", file]
    elif action == "branches":
        args = ["branch", "-a", "-vv"]
    else:
        raise ToolError("Unknown action %r." % action)
    try:
        out = gitops.run(args, base, timeout=60)
    except gitops.GitError as exc:
        raise ToolError(str(exc))
    if len(out) > 30000:
        out = out[:30000] + "\n… (truncated; narrow it with file or ref)"
    return Result(out.strip() or "(nothing)")
