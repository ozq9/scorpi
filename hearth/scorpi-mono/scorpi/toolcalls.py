"""Tool calls from models that are not good at them. Free and small models often send arguments that are not quite
JSON, misname a tool or a parameter, write the call as text instead of making it, or cannot call tools at all. This
repairs what can be repaired, reads calls written as text, and gives models without tool calling a plain-text way to
use tools (the "text tool format": <tool_call>{...}</tool_call> in, <tool_result> back).
"""

import ast
import difflib
import json
import re
import secrets

OPEN, CLOSE = "<tool_call>", "</tool_call>"

# parameter names models use for ours: our name -> what they write instead
ALIASES = {
    "path": ("file_path", "filepath", "file", "filename", "file_name", "target_file", "absolute_path", "relative_path"),
    "content": ("contents", "text", "code", "file_content", "data", "body"),
    "command": ("cmd", "shell_command", "script", "commands"),
    "pattern": ("regex", "query", "search", "search_pattern"),
    "find": ("old_str", "old_string", "old_text", "old", "search_text", "original"),
    "replace": ("new_str", "new_string", "new_text", "new", "replacement", "updated"),
    "url": ("link", "href", "address"),
}
WRAPPERS = ("arguments", "parameters", "args", "input", "params", "kwargs")


# ---------- arguments that are almost JSON ----------

def _strings_closed(text):
    """Close an unterminated string and brackets left open (a reply cut off mid-call)."""
    stack, quote, escape = [], False, False
    for ch in text:
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                quote = False
            continue
        if ch == '"':
            quote = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    return text + ('"' if quote else "") + "".join(reversed(stack))


def _loads(text):
    try:
        return json.loads(text, strict=False)  # strict=False: raw newlines inside strings, as models write code
    except ValueError:
        return None


def repair_json(raw):
    """A dict from arguments as models send them: JSON, JSON in a string, in a code fence, with trailing commas,
    single quotes, Python literals, unquoted keys or cut off. None if nothing sensible comes out."""
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    found = _loads(text)
    if isinstance(found, str):
        found = _loads(found)  # JSON encoded twice
    if isinstance(found, dict):
        return found
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    start = text.find("{")
    if start < 0:
        return None
    end = text.rfind("}")
    body = text[start:end + 1] if end > start else text[start:]
    for attempt in (body, _strings_closed(body)):
        found = _loads(attempt)
        if isinstance(found, dict):
            return found
        try:
            found = ast.literal_eval(attempt)  # Python dict syntax: 'single quotes', True/False/None
            if isinstance(found, dict):
                return found
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            pass
    fixed = body.replace("'", '"') if '"' not in body else body  # 'single quotes' throughout
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)  # trailing commas
    fixed = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', fixed)  # unquoted keys
    fixed = re.sub(r":\s*True\b", ": true", re.sub(r":\s*False\b", ": false", re.sub(r":\s*None\b", ": null", fixed)))
    for attempt in (fixed, _strings_closed(fixed)):
        found = _loads(attempt)
        if isinstance(found, dict):
            return found
    return None


def parse_args(raw):
    """Arguments for a call: the repaired dict, or {"_raw": text} when they cannot be read."""
    found = repair_json(raw)
    return found if isinstance(found, dict) else {"_raw": str(raw or "")}


# ---------- names and parameters ----------

def _snake(name):
    name = re.sub(r"^(functions|function|tools|tool|default_api|api)[.:/]", "", str(name or "").strip())
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def fix_name(name, names):
    """The tool a model meant: exact, differently written (readFile, functions.read_file) or a close typo."""
    if name in names:
        return name
    snake = _snake(name)
    if snake in names:
        return snake
    close = difflib.get_close_matches(snake, list(names), n=1, cutoff=0.82)
    return close[0] if close else name


def fix_call(call, schemas):
    """A call with its tool name and arguments mended against the offered tools ({name: JSON schema})."""
    names = set(schemas)
    name = fix_name(call.get("name") or "", names) if names else call.get("name") or ""
    args = call.get("arguments")
    if not isinstance(args, dict):
        args = parse_args(args)
    props = ((schemas.get(name) or {}).get("properties") or {})
    if props and len(args) == 1:
        (key, inner), = args.items()
        if key in WRAPPERS and key not in props:
            inner = inner if isinstance(inner, dict) else repair_json(inner)
            if isinstance(inner, dict):
                args = inner  # {"arguments": {...}} wrapped once more
    if props and "_raw" not in args:
        args = dict(args)
        for ours, theirs in ALIASES.items():
            if ours in props and ours not in args:
                for alt in theirs:
                    if alt in args and alt not in props:
                        args[ours] = args.pop(alt)
                        break
    return dict(call, name=name, arguments=args)


def unreadable(args):
    return isinstance(args, dict) and set(args) == {"_raw"}


# ---------- calls written as text ----------

def _objects(text):
    """(start, end, dict) for every balanced {...} JSON-ish object in the text."""
    out, i = [], 0
    while True:
        start = text.find("{", i)
        if start < 0:
            return out
        depth, quote, escape = 0, False, False
        for j in range(start, len(text)):
            ch = text[j]
            if quote:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    quote = False
                continue
            if ch == '"':
                quote = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    found = repair_json(text[start:j + 1])
                    if isinstance(found, dict):
                        out.append((start, j + 1, found))
                    i = j + 1
                    break
        else:
            return out


def _as_call(obj, names):
    """{"name", "arguments"} from the shapes models use: {"name", "arguments"|"parameters"}, {"tool", "args"},
    {"function": {"name", "arguments"}}."""
    if isinstance(obj.get("function"), dict):
        obj = obj["function"]
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name") or obj.get("function")
    if not isinstance(name, str):
        return None
    name = fix_name(name, names)
    if name not in names:
        return None
    args = next((obj[k] for k in ("arguments", "parameters", "args", "input", "params") if k in obj), {})
    args = args if isinstance(args, dict) else parse_args(args)
    return {"name": name, "arguments": args}


TAGGED = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
FUNCTION_TAG = re.compile(r"<function=([\w.-]+)>\s*(.*?)\s*(?:</function>|$)", re.S)
MISTRAL = re.compile(r"\[TOOL_CALLS\]\s*(\[.*\])", re.S)
FENCE = re.compile(r"```(?:json|tool_call|tool)?\s*(\{.*?\})\s*```", re.S)


def extract(text, names):
    """Tool calls a model wrote into its answer instead of making them: (calls, the answer without them).
    Only calls of offered tools count, so ordinary JSON in an answer is left alone."""
    names = set(names or ())
    if not text or not names:
        return [], text
    calls, spans = [], []

    def add(obj, span):
        call = _as_call(obj, names) if isinstance(obj, dict) else None
        if call:
            calls.append(dict(call, id="c" + secrets.token_hex(4)))  # nine letters and digits: every provider accepts it
            spans.append(span)

    for m in TAGGED.finditer(text):
        add(repair_json(m.group(1)), m.span())
    for m in FUNCTION_TAG.finditer(text):
        args = parse_args(m.group(2)) if m.group(2).strip() else {}
        add({"name": m.group(1), "arguments": args}, m.span())
    if not calls:
        m = MISTRAL.search(text)
        if m:
            try:
                for obj in json.loads(m.group(1), strict=False):
                    add(obj, m.span())
            except ValueError:
                pass
    if not calls:
        for m in FENCE.finditer(text):
            add(repair_json(m.group(1)), m.span())
    if not calls:
        stripped = text.strip()
        objects = _objects(stripped)
        # a whole answer that is nothing but call objects
        if objects and not re.sub(r"[\s,\[\]]", "", stripped[:objects[0][0]] + "".join(
                stripped[a[1]:b[0]] for a, b in zip(objects, objects[1:])) + stripped[objects[-1][1]:]):
            for start, end, obj in objects:
                add(obj, None)
            if calls:
                return calls, ""
    if not calls:
        return [], text
    rest, last = [], 0
    for span in sorted(s for s in spans if s):
        if span[0] >= last:
            rest.append(text[last:span[0]])
            last = span[1]
    rest.append(text[last:])
    return calls, re.sub(r"\n{3,}", "\n\n", "".join(rest)).strip()


# ---------- the text tool format, for models without tool calling ----------

def _param(name, spec, required):
    kind = spec.get("type") or "any"
    if kind == "array":
        kind = "list"
    return "%s: %s%s" % (name, kind, "" if name in required else "?")


def protocol(tools):
    """System prompt section that teaches the text tool format, with the tools on offer."""
    lines = []
    for t in tools or []:
        fn = t.get("function") or t
        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        required = set(params.get("required") or [])
        sig = ", ".join(_param(k, v, required) for k, v in props.items())
        about = (fn.get("description") or "").strip().split("\n")[0][:220]
        lines.append("- %s(%s): %s" % (fn["name"], sig, about))
    return ("TOOLS. Call a tool by writing exactly this, with valid JSON arguments:\n"
            "<tool_call>{\"name\": \"tool_name\", \"arguments\": {\"param\": \"value\"}}</tool_call>\n"
            "You may write several calls in one message; write nothing after the last one and wait. Each result comes back "
            "as <tool_result name=\"tool_name\">...</tool_result>. Never invent results. When the work is done, answer "
            "normally without a tool call. Parameters marked ? are optional.\nAvailable tools:\n" + "\n".join(lines))


def to_text_history(messages):
    """A conversation with native tool calls rewritten for a model without them: calls become <tool_call> text in
    the assistant's message, results become <tool_result> user messages."""
    out = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = "\n".join("%s%s%s" % (OPEN, json.dumps({"name": c["name"], "arguments": c.get("arguments") or {}}, ensure_ascii=False), CLOSE)
                              for c in m["tool_calls"])
            out.append({"role": "assistant", "content": ((m.get("content") or "").strip() + "\n" + calls).strip()})
        elif role == "tool":
            block = '<tool_result name="%s">\n%s\n</tool_result>' % (m.get("name") or "tool", m.get("content") or "(no output)")
            if out and out[-1]["role"] == "user" and out[-1].get("_results"):
                out[-1]["content"] += "\n" + block
            else:
                out.append({"role": "user", "content": block, "_results": True})
        else:
            out.append({k: v for k, v in m.items() if k not in ("tool_calls", "tool_call_id", "name")})
    for m in out:
        m.pop("_results", None)
    return out


class Hide:
    """Keeps <tool_call>...</tool_call> blocks out of the streamed answer the user sees (they show as tool cards)."""

    MARKERS = (OPEN, "<function=")

    def __init__(self):
        self.held = ""
        self.inside = ""

    def feed(self, token):
        self.held += token
        shown = ""
        while self.held:
            if self.inside:
                end = self.held.find(self.inside)
                if end < 0:
                    keep = len(self.inside) - 1
                    self.held = self.held[-keep:] if keep and len(self.held) > keep else self.held
                    return shown
                self.held = self.held[end + len(self.inside):]
                self.inside = ""
                continue
            starts = [(self.held.find(m), m) for m in self.MARKERS if self.held.find(m) >= 0]
            if starts:
                pos, marker = min(starts)
                shown += self.held[:pos]
                self.held = self.held[pos + len(marker):]
                self.inside = CLOSE if marker == OPEN else "</function>"
                continue
            # hold back a tail that could be the start of a marker
            cut = len(self.held)
            for m in self.MARKERS:
                for k in range(min(len(m) - 1, len(self.held)), 0, -1):
                    if self.held.endswith(m[:k]):
                        cut = min(cut, len(self.held) - k)
                        break
            shown += self.held[:cut]
            self.held = self.held[cut:]
            return shown
        return shown

    def flush(self):
        out = "" if self.inside else self.held
        self.held, self.inside = "", ""
        return out
