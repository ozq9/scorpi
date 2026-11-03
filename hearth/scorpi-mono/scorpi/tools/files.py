"""Filesystem tools. Paths may be absolute (anywhere on the host) or relative to the workspace."""

import fnmatch
import os
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
    "Read a text file (code, txt, md, json, csv, log...). For PDF/Word/Excel use read_document.",
    {"path": "string: File path", "start_line?": "integer: First line (1-based)", "max_lines?": "integer: Lines to read"},
    group="files",
)
def read_file(ctx, path, start_line=1, max_lines=0):
    path = resolve_path(path, must_exist=True)
    if os.path.isdir(path):
        raise ToolError("That is a folder; use list_dir.")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"):
        from .docs import extract_text
        return Result("File: %s\n\n%s" % (path, extract_text(path)[:TEXT_LIMIT]))
    with open(path, "rb") as fh:
        raw = fh.read(4 * 1024 * 1024)
    if b"\x00" in raw[:4096]:
        raise ToolError("Binary file (%s); cannot show as text." % human_size(os.path.getsize(path)))
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    start = max(1, int(start_line or 1))
    count = int(max_lines or 0) or len(lines)
    chunk = "\n".join(lines[start - 1:start - 1 + count])
    note = ""
    if len(chunk) > TEXT_LIMIT:
        chunk = chunk[:TEXT_LIMIT]
        note = "\n... [truncated; use start_line/max_lines to read more]"
    return Result("File: %s (%d lines)\n\n%s%s" % (path, len(lines), chunk, note))


@tool(
    "write_file",
    "Create or overwrite a text file (code, markdown, html, csv, json...). Relative paths go into the workspace. "
    "Set append=true to add to the end.",
    {"path": "string: File path", "content": "string: Full text content", "append?": "boolean: Append instead of overwrite"},
    group="files",
    risk="write",
)
def write_file(ctx, path, content, append=False):
    path = resolve_path(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a" if append else "w", encoding="utf-8", newline="") as fh:
        fh.write(content or "")
    return Result("Wrote %s to %s" % (human_size(os.path.getsize(path)), path), display=file_card(path), files=[path])


@tool(
    "edit_file",
    "Replace an exact snippet of text inside a text file (first occurrence, or all).",
    {"path": "string: File path", "find": "string: Exact text to find", "replace": "string: Replacement", "all?": "boolean: Replace every occurrence"},
    group="files",
    risk="write",
)
def edit_file(ctx, path, find, replace, all=False):
    path = resolve_path(path, must_exist=True)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if find not in text:
        raise ToolError("Snippet not found in %s" % path)
    count = text.count(find) if all else 1
    text = text.replace(find, replace) if all else text.replace(find, replace, 1)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return Result("Replaced %d occurrence(s) in %s" % (count, path), display=file_card(path), files=[path])


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
