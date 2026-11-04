"""Keeping a long agent turn inside the model's budget (context compaction).

A coding job can run for dozens of steps, each adding tool output; free models often accept far fewer tokens than
that. Before each model call the conversation is measured and, when it is too big, shortened in steps that lose as
little as possible:

1. trim old tool output and the long arguments of old tool calls (file contents already written) to a short head;
2. trim older chat messages, then leave out all but the last few of them;
3. summarize the earlier steps of the current task with the model into one message;
4. as a last resort, cut every long message.

The system prompt, the message that started the task and the most recent steps are always kept whole, and a tool
result is never separated from the call it answers."""

import json

CHARS_PER_TOKEN = 3.5
IMAGE_TOKENS = 1500
KEEP_RECENT = 8

SUMMARY_PROMPT = """Below is the earlier part of a task you are working on; it no longer fits in your memory. Write a summary for yourself to
continue from: the goal; what was done so far (files created or changed, with full paths, and what they hold); commands
run and their results; problems hit and how they were solved; decisions made; the current state; and what remains to do.
Be specific (names, paths, numbers) and concise: at most 350 words. Plain text, no preamble.

--- earlier steps ---
%s
--- end ---"""


def tokens(messages, tools=None):
    """A quick estimate of the tokens a request holds."""
    chars = 0
    for m in messages:
        chars += len(m.get("content") or "") + 12
        for call in m.get("tool_calls") or []:
            chars += len(json.dumps(call.get("arguments"), default=str)) + len(call.get("name") or "") + 20
        chars += len(m.get("images") or []) * IMAGE_TOKENS * CHARS_PER_TOKEN
    if tools:
        chars += len(json.dumps(tools))
    return int(chars / CHARS_PER_TOKEN)


def _head(text, keep, what="output"):
    text = text or ""
    if len(text) <= keep + 80:
        return text
    return text[:keep].rstrip() + "\n… [older %s trimmed: %d more characters; run the tool again if you need it]" % (what, len(text) - keep)


def _shrink_args(call, keep=300):
    args = call.get("arguments")
    if not isinstance(args, dict):
        return False
    changed = False
    for key, value in list(args.items()):
        if isinstance(value, str) and len(value) > keep + 80:
            args[key] = value[:keep] + "… [%d more characters, already sent]" % (len(value) - keep)
            changed = True
        elif isinstance(value, list) and len(json.dumps(value, default=str)) > keep * 3:
            args[key] = "[%d items, already sent]" % len(value)
            changed = True
    return changed


def _safe_start(messages, index, floor):
    """Move a cut point back so the kept part never begins with tool results whose call was cut away."""
    index = max(floor, min(index, len(messages)))
    while index > floor and index < len(messages) and messages[index].get("role") == "tool":
        index -= 1
    return index


def transcript(messages, limit):
    """The messages as plain text for the summarizer."""
    lines = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            lines.append("[result of %s] %s" % (m.get("name") or "tool", _head(m.get("content"), 1200)))
            continue
        text = (m.get("content") or "").strip()
        if text:
            lines.append("[%s] %s" % (role, _head(text, 1500, "text")))
        for call in m.get("tool_calls") or []:
            args = json.dumps(call.get("arguments"), default=str, ensure_ascii=False)
            lines.append("[%s called %s] %s" % (role, call.get("name"), args[:600] + ("…" if len(args) > 600 else "")))
    text = "\n".join(lines)
    if len(text) > limit:
        cut = len(text) - limit
        text = "[… the first %d characters are left out]\n" % cut + text[cut:]
    return text


class Compactor:
    """Shortens one turn's messages in place. task: index of the message that started the turn."""

    def __init__(self, summarize=None):
        self.summarize = summarize  # fn(prompt) -> text, or None to never call the model
        self.summaries = 0

    def fit(self, messages, budget, task, tools=None, keep_recent=KEEP_RECENT, force=False):
        """Returns (note for the user or "", new index of the task message)."""
        reserve = tokens([], tools) + 1200  # tool definitions and room for the answer
        limit = max(800, budget - reserve)
        if not force and tokens(messages) <= limit:
            return "", task
        before = tokens(messages)
        steps = []
        recent = _safe_start(messages, len(messages) - keep_recent, task + 1)

        # 1. old tool output and old tool-call arguments
        trimmed = 0
        for m in messages[1:recent]:
            if m.get("role") == "tool" and len(m.get("content") or "") > 700:
                m["content"] = _head(m["content"], 500)
                trimmed += 1
            for call in m.get("tool_calls") or []:
                trimmed += _shrink_args(call)
        if trimmed:
            steps.append("trimmed %d old tool outputs" % trimmed)
        if tokens(messages) <= limit:
            return self._note(steps, before, messages), task

        # 2. the conversation before this task: shorten, then keep only its last few messages
        for m in messages[1:task]:
            if len(m.get("content") or "") > 1200:
                m["content"] = _head(m["content"], 800, "message")
        if tokens(messages) > limit and task > 3:
            keep_from = _safe_start(messages, task - 2, 1)
            dropped = keep_from - 1
            if dropped > 1:
                messages[1:keep_from] = [{"role": "user", "content": "[%d earlier messages of this conversation are left out to save space]" % dropped}]
                task -= dropped - 1
                recent -= dropped - 1
                steps.append("left out %d older messages" % dropped)
        if tokens(messages) <= limit:
            return self._note(steps, before, messages), task

        # 3. summarize the earlier steps of this task
        recent = _safe_start(messages, recent, task + 1)
        middle = messages[task + 1:recent]
        if len(middle) >= 2:
            summary = ""
            if self.summarize:
                try:
                    summary = (self.summarize(SUMMARY_PROMPT % transcript(middle, max(6000, int(limit * CHARS_PER_TOKEN * 0.6)))) or "").strip()
                except Exception:  # noqa: BLE001 - a failed summary falls back to the list of steps below
                    summary = ""
            if summary:
                self.summaries += 1
            else:
                summary = "Steps taken (details left out):\n" + "\n".join(
                    "- %s(%s)" % (c.get("name"), ", ".join("%s=%s" % (k, str(v)[:50]) for k, v in (c.get("arguments") or {}).items())[:160])
                    for m in middle for c in m.get("tool_calls") or [])
            messages[task + 1:recent] = [{"role": "user", "content": "[Summary of the earlier steps of this task, written to save space]\n" + summary[:6000]}]
            steps.append("summarized %d earlier steps" % len(middle))
        if tokens(messages) <= limit:
            return self._note(steps, before, messages), task

        # 4. last resort: cut every long message except the newest one
        share = max(300, int(limit * CHARS_PER_TOKEN / max(1, len(messages))))
        for m in messages[1:-1]:
            if len(m.get("content") or "") > share:
                m["content"] = _head(m["content"], share, "text")
            for call in m.get("tool_calls") or []:
                _shrink_args(call, 120)
        if len(messages[-1].get("content") or "") > limit * CHARS_PER_TOKEN * 0.6:
            messages[-1]["content"] = _head(messages[-1]["content"], int(limit * CHARS_PER_TOKEN * 0.6), "text")
        steps.append("shortened long messages")
        return self._note(steps, before, messages), task

    @staticmethod
    def _note(steps, before, messages):
        after = tokens(messages)
        return "Made room in the model's memory (%s): ~%s → ~%s tokens." % (", ".join(steps) or "trimmed", _k(before), _k(after))


def _k(n):
    return "%.1fk" % (n / 1000) if n >= 1000 else str(n)
