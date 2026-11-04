"""Filesystem tools. Paths may be absolute (anywhere on the host) or relative to the workspace."""

import fnmatch
import os
import re
import shutil
import time

from ..config import WORKSPACE
from . import Result, ToolError, module_available, resolve_path, tool

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "$Recycle.Bin", "System Volume Information"}
TEXT_LIMIT = 60_000


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024.0
    return "%d B" % n


MEDIA = {
    "image": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif"),
    "video": (".mp4", ".webm", ".mov", ".m4v", ".ogv"),
    "audio": (".mp3", ".wav", ".ogg", ".oga", ".m4a", ".flac", ".aac", ".opus"),
}


def file_card(path, caption=""):
    """The chat card for a file: pictures, videos and sounds show themselves; everything else opens in the viewer."""
    ext = os.path.splitext(path)[1].lower()
    for kind, exts in MEDIA.items():
        if ext in exts:
            return {"type": kind, "path": path, "caption": caption}
    return {"type": "file", "path": path, "name": os.path.basename(path), "size": os.path.getsize(path), "caption": caption}


@tool(
    "list_dir",
    "List files and folders in a directory. Use '~' for the user's home, '~/Desktop', 'C:\\\\' etc. Relative paths are inside the workspace.",
    {"path?": "string: Folder path (default: workspace)"},
    group="files",
)
def list_dir(ctx, path=""):
    path = resolve_path(path or WORKSPACE)
    if not os.path.isdir(path):
        raise ToolError("Not a folder: %s" % path)
    rows = []
    try:
        entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    except PermissionError:
        raise ToolError("Permission denied: %s" % path)
    for entry in entries[:400]:
        try:
            if entry.is_dir(follow_symlinks=False):
                rows.append("[dir]  %s/" % entry.name)
            else:
                st = entry.stat(follow_symlinks=False)
                rows.append("[file] %s  (%s, %s)" % (entry.name, human_size(st.st_size), time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))))
        except OSError:
            rows.append("[?]    %s" % entry.name)
    more = "\n... %d more" % (len(entries) - 400) if len(entries) > 400 else ""
    return Result("Contents of %s:\n%s%s" % (path, "\n".join(rows) or "(empty)", more))


@tool(
    "read_file",
    "Read a text file (code, txt, md, json, csv, log...). Lines come numbered (number, tab, text) so you can refer to "
    "them; the numbers are not part of the file. Read big files in parts with start_line/max_lines. For PDF/Word/Excel "
    "use read_document.",
    {"path": "string: File path", "start_line?": "integer: First line (1-based)", "max_lines?": "integer: Lines to read",
     "numbers?": "boolean: Number the lines (default true)"},
    group="files",
)
def read_file(ctx, path, start_line=1, max_lines=0, numbers=True):
    path = resolve_path(path, must_exist=True)
    if os.path.isdir(path):
        raise ToolError("That is a folder; use list_dir, glob or repo_map.")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"):
        from .docs import extract_text
        return Result("File: %s\n\n%s" % (path, extract_text(path)[:TEXT_LIMIT]))
    with open(path, "rb") as fh:
        raw = fh.read(8 * 1024 * 1024)
    if b"\x00" in raw[:4096]:
        raise ToolError("Binary file (%s); cannot show as text." % human_size(os.path.getsize(path)))
    from .dev import _seen
    _seen(ctx, path)
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    start = max(1, int(start_line or 1))
    count = int(max_lines or 0) or len(lines)
    chosen = lines[start - 1:start - 1 + count]
    if numbers is not False:
        chunk = "\n".join("%5d\t%s" % (start + i, line) for i, line in enumerate(chosen))
    else:
        chunk = "\n".join(chosen)
    note = ""
    if len(chunk) > TEXT_LIMIT:
        cut = chunk[:TEXT_LIMIT]
        shown = cut.count("\n") + 1
        chunk = cut
        note = "\n... [truncated after line %d of %d; read on with start_line=%d]" % (start + shown - 1, len(lines), start + shown - 1)
    elif start - 1 + count < len(lines):
        note = "\n... [%d more lines; read on with start_line=%d]" % (len(lines) - (start - 1 + count), start + count)
    return Result("File: %s (%d lines)\n\n%s%s" % (path, len(lines), chunk, note))


def _after_write(ctx, path, before, after, verb):
    """Result of a write or edit: what changed (a diff card), and the syntax checks of the new content."""
    from .. import codecheck
    from .dev import _seen, diff_display, diff_summary
    from . import project_dir
    _seen(ctx, path, wrote=True)
    checks = codecheck.note([path])
    summary = "%s %s (%s)" % (verb, path, human_size(os.path.getsize(path)))
    if before is not None and after is not None:
        diff = diff_summary(before, after, 24)
        if diff:
            summary += "\n" + diff
    if checks:
        summary += "\n\n" + checks
    root = project_dir() or os.path.dirname(path)
    display = diff_display([(path, before, after)], root, checks) if before != after else file_card(path)
    return Result(summary, display=display, files=[path], ok=not checks.startswith("PROBLEMS"))


@tool(
    "write_file",
    "Create or overwrite a text file (code, markdown, html, csv, json...) with its complete content. Relative paths go "
    "into the project folder (or the workspace). Set append=true to add to the end. For changes to an existing file "
    "prefer edit_file. Code is checked for syntax errors after writing.",
    {"path": "string: File path", "content": "string: Full text content", "append?": "boolean: Append instead of overwrite"},
    group="files",
    risk="write",
)
def write_file(ctx, path, content, append=False):
    path = resolve_path(path)
    from .dev import stale
    if os.path.isfile(path) and not append and stale(ctx, path):
        raise ToolError("%s changed on disk since you last read it (the user or another helper edited it). Read it again "
                        "before overwriting, so their changes are not lost." % path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    before = None
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                before = fh.read()
        except OSError:
            before = None
    with open(path, "a" if append else "w", encoding="utf-8", newline="") as fh:
        fh.write(content or "")
    with open(path, encoding="utf-8", errors="replace") as fh:
        after = fh.read()
    return _after_write(ctx, path, before, after, "Appended to" if append else "Wrote" if before is None else "Overwrote")


LINE_PREFIX = re.compile(r"^\s*\d+\t")


def _trim_blank(find_lines, new_lines):
    """Drop the blank lines a model puts around a quoted block, and as many from the replacement."""
    core, new = find_lines[:], new_lines[:]
    while core and not core[0].strip():
        core.pop(0)
        if new and not new[0].strip():
            new.pop(0)
    while core and not core[-1].strip():
        core.pop()
        if new and not new[-1].strip():
            new.pop()
    return core, new


def _apply_one(text, find, replace, all_=False):
    """Replace find in text. Exact first; then tolerant of line-number prefixes copied from read_file, trailing spaces
    and indentation (the replacement is shifted to the real indentation). Returns (new_text, count, how)."""
    from .dev import closest, find_block, _reindent
    if find == "":
        raise ToolError("find is empty. To create or replace a whole file use write_file.")
    hits = text.count(find)
    if hits > 1 and not all_:
        lines = [text[:m.start()].count("\n") + 1 for m in re.finditer(re.escape(find), text)][:8]
        raise ToolError("The text to replace occurs %d times (lines %s). Include more surrounding lines so it is unique, "
                        "or set all=true to change every occurrence." % (hits, ", ".join(map(str, lines))))
    if hits:
        return (text.replace(find, replace) if all_ else text.replace(find, replace, 1)), (hits if all_ else 1), "exact"
    find_lines = find.split("\n")
    if all(LINE_PREFIX.match(x) or not x.strip() for x in find_lines) and any(LINE_PREFIX.match(x) for x in find_lines):
        stripped = "\n".join(LINE_PREFIX.sub("", x, count=1) for x in find_lines)
        repl = "\n".join(LINE_PREFIX.sub("", x, count=1) for x in replace.split("\n"))
        if stripped in text:
            return _apply_one(text, stripped, repl, all_)
    lines = text.split("\n")
    # drop the empty first/last line a model adds when it quotes a block
    core, new_lines = _trim_blank(find_lines, replace.split("\n"))
    if core:
        at, how = find_block(lines, core)
        if at >= 0:
            if how == "indentation":
                new_lines = _reindent(new_lines, core, lines[at:at + len(core)])
            lines[at:at + len(core)] = new_lines
            return "\n".join(lines), 1, how
    line, near = closest(lines, core or find_lines)
    raise ToolError("The text to replace was not found in the file.%s Copy the exact current text (read_file shows it; leave out "
                    "the line numbers) or use write_file for a full rewrite."
                    % (("\nThe most similar text, at line %d:\n%s\n" % (line, near)) if near else ""))


@tool(
    "edit_file",
    "Change part of a text file: replace an exact snippet (find) with new text (replace). The snippet must be unique "
    "in the file (include a few surrounding lines), or set all=true. Several changes to the same file go in one call "
    "with edits=[{find, replace}, ...], applied in order, all or nothing. Small differences in indentation or "
    "trailing spaces are tolerated. Code is checked for syntax errors after the change.",
    {"path": "string: File path", "find?": "string: Exact text to find (without read_file's line numbers)",
     "replace?": "string: Replacement text", "all?": "boolean: Replace every occurrence",
     "edits?": {"type": "array", "description": "Several changes in order: [{find, replace, all?}]",
                "items": {"type": "object", "properties": {"find": {"type": "string"}, "replace": {"type": "string"}, "all": {"type": "boolean"}},
                          "required": ["find", "replace"]}}},
    group="files",
    risk="write",
)
def edit_file(ctx, path, find=None, replace=None, all=False, edits=None):
    path = resolve_path(path, must_exist=True)
    if os.path.isdir(path):
        raise ToolError("That is a folder.")
    from .dev import _read_text, _write_text, stale
    if stale(ctx, path):
        raise ToolError("%s changed on disk since you last read it (the user or another helper edited it). Read it again, "
                        "then make your change." % path)
    changes = []
    if find is not None:
        changes.append({"find": find, "replace": replace if replace is not None else "", "all": all})
    for e in edits or []:
        if isinstance(e, dict) and e.get("find") is not None:
            changes.append({"find": str(e["find"]), "replace": str(e.get("replace") or ""), "all": bool(e.get("all"))})
    if not changes:
        raise ToolError("Give find and replace, or edits=[{find, replace}].")
    before, eol = _read_text(path)
    text, total, fuzzy = before, 0, []
    for number, change in enumerate(changes, 1):
        try:
            text, count, how = _apply_one(text, change["find"].replace("\r\n", "\n"), change["replace"].replace("\r\n", "\n"), change["all"])
        except ToolError as exc:
            prefix = ("Edit %d of %d: " % (number, len(changes))) if len(changes) > 1 else ""
            raise ToolError(prefix + str(exc) + (" No change was written." if len(changes) > 1 else ""))
        total += count
        if how != "exact":
            fuzzy.append(how)
    if text == before:
        return Result("No change: the replacement equals the current text in %s." % path, ok=True)
    _write_text(path, text, eol)
    verb = "Edited (%d change%s%s)" % (total, "" if total == 1 else "s", (", matched ignoring " + " and ".join(sorted(set(fuzzy)))) if fuzzy else "")
    return _after_write(ctx, path, before, text, verb)


@tool(
    "search_files",
    "Find files by name (glob like *.pdf) and optionally by text contained in them, recursively under a folder.",
    {"path?": "string: Folder to search (default home)", "pattern?": "string: Glob for file names, e.g. *.docx", "contains?": "string: Text the file must contain", "max_results?": "integer: Limit (default 50)"},
    group="files",
)
def search_files(ctx, path="~", pattern="*", contains="", max_results=50):
    base = resolve_path(path or "~")
    limit = min(int(max_results or 50), 300)
    needle = (contains or "").lower()
    results = []
    started = time.time()
    base_depth = base.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(base):
        if time.time() - started > 25:
            results.append("... stopped after 25 seconds")
            break
        if root.count(os.sep) - base_depth >= 8:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and d != "AppData"]
        for name in files:
            if not fnmatch.fnmatch(name.lower(), (pattern or "*").lower()):
                continue
            full = os.path.join(root, name)
            if needle:
                try:
                    if os.path.getsize(full) > 5 * 1024 * 1024:
                        continue
                    with open(full, "rb") as fh:
                        if needle not in fh.read().decode("utf-8", "ignore").lower():
                            continue
                except OSError:
                    continue
            results.append(full)
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    return Result("Matches under %s:\n%s" % (base, "\n".join(results) or "(none)"))


@tool("file_info", "Get size, type and timestamps of a file or folder.", {"path": "string: Path"}, group="files")
def file_info(ctx, path):
    path = resolve_path(path, must_exist=True)
    st = os.stat(path)
    kind = "folder" if os.path.isdir(path) else "file"
    extra = ""
    if kind == "folder":
        try:
            extra = "\nitems: %d" % len(os.listdir(path))
        except OSError:
            pass
    return Result(
        "%s: %s\nsize: %s\nmodified: %s\ncreated: %s%s" % (
            kind, path, human_size(st.st_size),
            time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_ctime)), extra,
        )
    )


@tool(
    "manage_path",
    "Copy, move/rename, create a folder, or delete a file/folder. Delete sends to the Recycle Bin when possible.",
    {"action": "string[copy,move,mkdir,delete]: Operation", "path": "string: Source path", "destination?": "string: Target path for copy/move"},
    group="files",
    risk="danger",
)
def manage_path(ctx, action, path, destination=""):
    src = resolve_path(path)
    if action == "mkdir":
        os.makedirs(src, exist_ok=True)
        return Result("Created folder %s" % src)
    if not os.path.exists(src):
        raise ToolError("Not found: %s" % src)
    if action == "delete":
        if module_available("send2trash"):
            from send2trash import send2trash
            send2trash(src)
            return Result("Moved to Recycle Bin: %s" % src)
        if os.path.isdir(src):
            shutil.rmtree(src)
        else:
            os.remove(src)
        return Result("Deleted %s" % src)
    dst = resolve_path(destination)
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    if action == "copy":
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return Result("Copied to %s" % dst, files=[dst])
    if action == "move":
        shutil.move(src, dst)
        return Result("Moved to %s" % dst, files=[dst])
    raise ToolError("Unknown action %s" % action)


@tool(
    "open_path",
    "Open a file or folder on the host PC with its default program (e.g. show a PDF, open Explorer).",
    {"path": "string: File or folder"},
    group="files",
    risk="write",
)
def open_path(ctx, path):
    path = resolve_path(path, must_exist=True)
    from .system import open_with_default
    open_with_default(path)
    return Result("Opened %s on the host." % path)
