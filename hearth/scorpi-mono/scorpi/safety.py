"""How much a shell command can change, so the agent does not have to ask before every harmless one.

classify(command) -> (level, reason):
  read     only looks (ls, cat, git status, grep, --version...)
  dev      builds, tests, lints, type-checks or runs the project (npm test, pytest, cargo build, tsc, python app.py...)
  edit     changes files (mkdir, cp, mv, sed, tar...)
  risky    anything else: deleting, publishing, network, admin rights, unknown programs... (asks unless autonomy is "full")
  blocked  could wreck the PC (wipe a disk or the home folder, fork bombs...): never run

Autonomy (the "autonomy" setting; an agent allowed to act on its own counts as "full"):
  ask   everything that is not read-only asks first
  auto  read-only, build, test and run commands go ahead; file changes too inside a coding project (Scorpi keeps a
        checkpoint of every run to undo them) when no path points outside it; everything else asks (default)
  full  everything goes ahead, except blocked commands and destructive ones (deleting folders, force pushes, hard
        resets, killing programs...), which still ask
"""

import re

import os

LEVELS = ("read", "dev", "edit", "risky", "blocked")

# first word(s) of read-only commands
READ = {
    "ls", "dir", "pwd", "cat", "type", "head", "tail", "wc", "echo", "printf", "which", "where", "whereis", "file", "stat",
    "tree", "du", "df", "date", "uname", "whoami", "hostname", "id", "env", "printenv", "true", "test", "[", "basename",
    "dirname", "realpath", "readlink", "sort", "uniq", "cut", "tr", "diff", "cmp", "md5sum", "sha256sum", "sha1sum",
    "less", "more", "nl", "column", "jq", "yq", "grep", "egrep", "fgrep", "rg", "ag", "fd", "ps", "free", "uptime", "nproc",
    "lscpu", "lsb_release", "sw_vers", "ver", "systeminfo", "tasklist", "netstat", "ipconfig", "ifconfig", "ping", "nslookup",
    "get-childitem", "gci", "get-content", "gc", "get-item", "gi", "test-path", "get-location", "gl", "select-string", "sls",
    "get-command", "gcm", "get-process", "gps", "measure-object", "measure", "get-filehash", "resolve-path", "get-date",
    "write-output", "write-host", "format-table", "ft", "format-list", "fl", "select-object", "select", "where-object", "?",
    "sort-object", "convertfrom-json", "convertto-json", "out-string", "get-itemproperty", "split-path", "join-path",
}
# tools whose subcommands decide (subcommand -> level)
SUB = {
    "git": {"read": {"status", "diff", "log", "show", "branch", "remote", "rev-parse", "ls-files", "blame", "describe", "tag",
                     "shortlog", "reflog", "config", "grep", "ls-tree", "cat-file", "rev-list", "whatchanged", "stash list",
                     "worktree list", "submodule status", "check-ignore", "merge-base", "name-rev", "for-each-ref", "help", "version"},
            "dev": {"add", "commit", "checkout", "switch", "restore", "stash", "fetch", "pull", "merge", "rebase", "cherry-pick",
                    "init", "mv", "worktree", "submodule", "apply", "am", "revert"}},
    "npm": {"read": {"ls", "list", "outdated", "view", "info", "search", "help", "version", "config list", "why", "explain", "audit"},
            "dev": {"test", "t", "run", "run-script", "start", "build", "ci", "install", "i", "exec", "pack", "init", "create"}},
    "pnpm": {"read": {"ls", "list", "outdated", "why", "help", "audit"},
             "dev": {"test", "t", "run", "start", "build", "install", "i", "exec", "dlx", "create", "add"}},
    "yarn": {"read": {"list", "info", "why", "outdated", "help", "audit"}, "dev": {"test", "run", "start", "build", "install", "add", "create", "dlx"}},
    "bun": {"read": {"pm"}, "dev": {"test", "run", "build", "install", "i", "x", "create", "add"}},
    "pip": {"read": {"list", "show", "freeze", "check", "help", "--version", "index"}, "dev": {"install", "download", "wheel"}},
    "pip3": {"read": {"list", "show", "freeze", "check", "help", "--version"}, "dev": {"install", "download", "wheel"}},
    "uv": {"read": {"pip list", "pip show", "tree", "--version"}, "dev": {"run", "sync", "add", "pip install", "venv", "lock", "build", "init"}},
    "poetry": {"read": {"show", "check", "--version"}, "dev": {"install", "run", "build", "add", "lock"}},
    "cargo": {"read": {"tree", "metadata", "--version", "search"}, "dev": {"build", "check", "test", "run", "clippy", "fmt", "bench", "doc", "add", "fetch", "new", "init"}},
    "go": {"read": {"version", "env", "list", "doc", "help"}, "dev": {"build", "test", "vet", "run", "fmt", "mod", "get", "generate", "install"}},
    "dotnet": {"read": {"--info", "--version", "--list-sdks", "list"}, "dev": {"build", "test", "run", "restore", "format", "new", "add"}},
    "docker": {"read": {"ps", "images", "logs", "inspect", "version", "info", "compose ps", "compose logs"},
               "dev": {"build", "compose build", "compose up", "run", "compose run", "pull", "start", "stop", "compose down"}},
    "gh": {"read": {"pr view", "pr list", "pr checks", "pr diff", "pr status", "issue view", "issue list", "repo view", "run list",
                    "run view", "auth status", "status", "api", "search"}, "dev": {"pr checkout", "repo clone", "run watch"}},
    "deno": {"read": {"info", "--version", "doc"}, "dev": {"test", "run", "task", "check", "lint", "fmt", "compile", "bench"}},
    "composer": {"read": {"show", "outdated", "validate", "--version"}, "dev": {"install", "test", "run-script", "dump-autoload", "require"}},
    "mvn": {"read": {"--version", "-v", "help:effective-pom", "dependency:tree"}, "dev": {"test", "compile", "package", "verify", "install", "-q"}},
    "gradle": {"read": {"--version", "dependencies", "tasks", "projects"}, "dev": {"test", "build", "check", "assemble", "run", "clean"}},
    "make": {"read": {"-n", "--dry-run", "help"}, "dev": {"", "test", "build", "check", "lint", "all", "run", "dev", "fmt", "format"}},
    "flutter": {"read": {"doctor", "--version", "devices"}, "dev": {"test", "build", "run", "pub", "analyze", "create"}},
    "rustup": {"read": {"show", "--version", "toolchain list"}, "dev": {}},
    "conda": {"read": {"list", "info", "env list"}, "dev": {"install", "create", "run"}},
    "brew": {"read": {"list", "info", "search", "--version", "doctor"}, "dev": {}},
    "winget": {"read": {"list", "search", "show", "--version"}, "dev": {}},
}
# standalone build / test / lint / type-check tools (they run the project's own code or check it)
DEV = {
    "pytest", "py.test", "tox", "nox", "jest", "vitest", "mocha", "ava", "tap", "karma", "playwright", "cypress", "eslint",
    "prettier", "tsc", "vue-tsc", "biome", "stylelint", "ruff", "black", "isort", "flake8", "pylint", "mypy", "pyright",
    "bandit", "rubocop", "rspec", "phpunit", "phpstan", "golangci-lint", "gofmt", "rustfmt", "clippy-driver", "swiftlint",
    "ktlint", "shellcheck", "hadolint", "markdownlint", "vite", "next", "nuxt", "astro", "webpack", "rollup", "esbuild",
    "parcel", "turbo", "nx", "tsx", "ts-node", "node", "nodemon", "python", "python3", "py", "ruby", "php", "java", "javac",
    "kotlinc", "swift", "swiftc", "rustc", "gcc", "g++", "clang", "clang++", "cmake", "ninja", "meson", "zig", "dart", "elixir",
    "mix", "lua", "perl", "Rscript", "julia", "npx", "pnpx", "bunx", "http-server", "serve", "live-server", "uvicorn", "gunicorn",
    "flask", "django-admin", "hugo", "jekyll", "eleventy", "storybook", "jupyter", "pre-commit", "sass", "lessc",
    "curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "source", ".",
}
# harmless shell bookkeeping
READ |= {"cd", "set-location", "sl", "pushd", "popd", "sleep", "start-sleep", "export", "set", "cls", "clear", "find"}
# commands that change files
EDIT = {"mkdir", "md", "touch", "cp", "copy", "mv", "move", "copy-item", "cpi", "new-item", "ni", "move-item", "mi", "rename-item",
        "ren", "set-content", "sc", "add-content", "ac", "out-file", "sed", "awk", "tar", "unzip", "zip", "gzip", "gunzip",
        "expand-archive", "compress-archive", "chmod", "ln", "tee", "patch", "xcopy", "robocopy"}
# never auto-run, at any autonomy: these erase things that cannot come back
DESTRUCTIVE = [
    (r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+", "deletes files or folders"),
    (r"\b(remove-item|ri|rmdir|rd|del|erase)\b", "deletes files or folders"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|push\s+.*(--force|-f\b)|branch\s+-D|checkout\s+--\s|stash\s+(drop|clear)|filter-branch)",
     "throws away git history or changes"),
    (r"\bdocker\s+(system\s+prune|volume\s+(rm|prune)|rm\s+-f)", "deletes Docker data"),
    (r"\b(drop\s+(table|database)|truncate\s+table)\b", "deletes database data"),
    (r"\bnpm\s+(publish|unpublish)|\bcargo\s+publish|\btwine\s+upload|\bgh\s+(release\s+delete|repo\s+delete)", "publishes or deletes something online"),
    (r"\b(shutdown|reboot|restart-computer|stop-computer|logoff)\b", "shuts the PC down"),
    (r"\b(kill|pkill|killall|taskkill|stop-process)\b", "ends programs"),
    (r"\bchmod\s+-R\b|\bchown\s+-R\b|\bicacls\b|\btakeown\b", "changes permissions of many files"),
    (r"\breg\s+(add|delete)|\bset-itemproperty\b.*hk(lm|cu)", "changes the Windows registry"),
    (r"(curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod)\b[^|]*\|\s*(ba|z|da)?sh\b|\|\s*iex\b|invoke-expression", "runs a script from the internet"),
    (r"\bsudo\b|\bsu\s|runas\b|start-process\b.*-verb\s+runas", "runs as administrator"),
]
# refused outright
BLOCKED = [
    (r"\brm\s+(-[a-z]*\s+)*(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)[a-z]*\s+(/|/\*|~|~/|\$home|/home|/users|/etc|/usr|/var|/bin|c:\\?)(\s|$)", "would delete the whole disk or home folder"),
    (r"\b(remove-item|rm|del|rmdir|rd)\b.*\s(c:\\?|c:\\windows|c:\\users|\$env:userprofile|\$home|~)\s*(-|$)", "would delete the system drive or home folder"),
    (r"\bmkfs(\.\w+)?\b|\bformat(-volume)?\s+[a-z]:|\bdiskpart\b|\bdd\s+.*of=/dev/(sd|nvme|hd|disk)", "would erase a disk"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "is a fork bomb"),
    (r">\s*/dev/(sd|nvme|hd)", "would overwrite a disk"),
    (r"\bvssadmin\s+delete\s+shadows|\bbcdedit\b|\bcipher\s+/w", "damages system recovery"),
]
SPLIT = re.compile(r"\s*(?:&&|\|\||;|\n|(?<![|])\|(?![|]))\s*")
REDIRECT = re.compile(r"(?<![0-9&])>{1,2}(?!\s*&?\s*(?:/dev/null|\$null|nul)\b)(?!&)")


def _words(segment):
    return [w for w in re.split(r"\s+", segment.strip()) if w]


def _strip_prefixes(words):
    """Skip env assignments and harmless wrappers: "CI=1 npm test", "time pytest", "cd x && ..."."""
    while words and (re.match(r"^\w+=", words[0]) or words[0] in ("time", "nice", "command", "env", "&", "call")):
        words = words[1:]
    return words


def _segment_level(segment):
    words = _strip_prefixes(_words(segment))
    if not words:
        return "read"
    exe = re.sub(r"\.(exe|cmd|bat|ps1)$", "", words[0].split("/")[-1].split("\\")[-1].lower())
    rest = [w.lower() for w in words[1:] if not w.startswith("-") or w in ("--version", "-v", "--info", "--dry-run", "-n")]
    if exe in SUB:
        table = SUB[exe]
        two = " ".join(rest[:2])
        one = rest[0] if rest else ""
        if exe in ("python", "python3", "py") and len(words) > 2 and words[1] == "-m":
            return "dev"
        for level in ("read", "dev"):
            if two in table.get(level, ()) or one in table.get(level, ()):
                return level
        if not rest and "" in table.get("dev", ()):
            return "dev"
        if any(w in ("--version", "-v", "version", "help", "--help") for w in [w.lower() for w in words[1:2]]):
            return "read"
        return "risky"
    if exe == "find":
        return "risky" if re.search(r"-(delete|exec|execdir|ok)\b", segment) else "read"
    if exe == "sed" and not re.search(r"(^|\s)-[a-z]*i", segment):
        return "read"  # sed without -i only prints
    if exe in READ:
        return "read"
    if exe in EDIT:
        return "edit"
    if exe in DEV:
        if exe in ("python", "python3", "py", "node", "ruby", "php", "perl") and len(words) > 1 and words[1] in ("--version", "-V", "-v"):
            return "read"
        if exe in ("curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm"):
            return "dev" if re.search(r"localhost|127\.0\.0\.1|0\.0\.0\.0", segment) else "risky"
        return "dev"
    if words[0].startswith(("./", ".\\")) or exe in ("gradlew", "mvnw"):
        return "dev"  # the project's own scripts
    return "risky"


def classify(command):
    """(level, reason) for a shell command line."""
    text = str(command or "").strip()
    if not text:
        return "read", ""
    low = text.lower()
    for pattern, why in BLOCKED:
        if re.search(pattern, low):
            return "blocked", why
    for pattern, why in DESTRUCTIVE:
        if re.search(pattern, low):
            return "risky", why
    if "`" in text or "$(" in text:
        return "risky", "runs a nested command"
    level = "read"
    for segment in SPLIT.split(text):
        if not segment.strip():
            continue
        seg = _segment_level(segment)
        if seg in ("read", "dev") and REDIRECT.search(segment):
            seg = "edit"  # writes its output to a file
        if LEVELS.index(seg) > LEVELS.index(level):
            level = seg
    return level, ""


def destructive(command):
    low = str(command or "").lower()
    return next((why for pattern, why in DESTRUCTIVE if re.search(pattern, low)), "")


PATHISH = re.compile(r"""(?:^|[\s='"])((?:[a-zA-Z]:[\\/]|/|~|\$home\b|\$env:|%[a-z]+%)[^\s'"]*|[^\s'"]*\.\.[\\/][^\s'"]*)""", re.I)


def outside(command, workdir):
    """True when the command names a path outside the project folder (absolute elsewhere, ~, .., environment paths)."""
    if not workdir:
        return True
    root = os.path.normcase(os.path.realpath(workdir))
    for m in PATHISH.finditer(str(command or "")):
        token = m.group(1).strip("\"'")
        if token.lower().startswith(("/dev/null", "$null")) or re.match(r"^https?:", token, re.I):
            continue
        if token.startswith(("~", "$", "%")) or ".." in token.replace("\\", "/").split("/"):
            return True
        full = os.path.normcase(os.path.realpath(token))
        if full != root and not full.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


def decide(tool, args, autonomy, workdir=""):
    """"allow" (run without asking), "ask" or "block", with a reason, for a tool whose policy is to ask.
    Commands, test runs and code runs get smarter handling; other tools keep their policy (ask)."""
    autonomy = autonomy if autonomy in ("ask", "auto", "full") else "auto"
    if tool == "run_shell":
        command = (args or {}).get("command") or ""
        level, why = classify(command)
        if level == "blocked":
            return "block", why
        if level == "read":
            return "allow", ""
        if autonomy == "full":
            danger = destructive(command)
            return ("ask", danger) if danger else ("allow", "")
        if autonomy == "auto":
            if level == "dev":
                return "allow", ""
            if level == "edit" and workdir and not outside(command, workdir):
                return "allow", ""
            if level == "edit":
                return "ask", "changes files outside the project"
        return "ask", why
    if tool == "run_tests":
        return ("allow", "") if autonomy in ("auto", "full") else ("ask", "")
    if tool in ("run_python", "run_javascript"):
        if autonomy == "full" or (autonomy == "auto" and workdir):
            return "allow", ""
        return "ask", ""
    return "ask", ""
