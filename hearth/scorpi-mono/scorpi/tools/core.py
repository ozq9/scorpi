"""Always-available tools: memory, planning, notifications, calculation, tool loading."""

import ast
import math
import operator
import os
import re
import statistics
import time

from .. import db, notify
from . import GROUPS, Result, ToolError, tool


@tool(
    "remember",
    "Save a durable fact about the user (preference, name, goal, learner level, project detail) to long-term memory. "
    "Use when the user shares something worth remembering in future conversations.",
    {"text": "string: The fact, written in third person, e.g. 'Prefers metric units.'", "kind?": "string[preference,fact,goal,person,learning]: Category"},
    risk="write",
)
def remember(ctx, text, kind="fact"):
    text = (text or "").strip()
    if not text:
        raise ToolError("Nothing to remember.")
    owner = db.owner_of(ctx.conv_id)  # memory belongs to the person whose chat this is
    existing = db.one("SELECT id FROM memories WHERE lower(text)=lower(?) AND owner=?", (text, owner))
    if existing:
        return Result("Already remembered: %s" % text)
    mid = db.x("INSERT INTO memories (text, kind, created, used, owner) VALUES (?,?,?,?,?)", (text, kind or "fact", time.time(), time.time(), owner))
    return Result("Remembered (#%s): %s" % (mid, text), display={"type": "memory", "text": text})


@tool(
    "show",
    "Show the user something right in the chat: a picture, video, sound, PDF, document, spreadsheet, web page you "
    "built, or a website. Files appear as previews they can open full screen; URLs as link previews or embedded "
    "players (YouTube, images, videos). Use it whenever seeing the thing helps more than describing it.",
    {"target": "string: Absolute file path (relative = workspace) or http(s) URL", "caption?": "string: Short caption"},
)
def show(ctx, target, caption=""):
    target = (target or "").strip().strip('"')
    if re.match(r"https?://", target):
        return Result("Shown to the user: %s" % target, display={"type": "link", "url": target, "caption": caption})
    from . import resolve_path
    from .files import file_card
    path = resolve_path(target, must_exist=True)
    if os.path.isdir(path):
        raise ToolError("That is a folder; show a file inside it.")
    return Result("Shown to the user: %s" % path, display=file_card(path, caption), files=[path])


@tool(
    "recall",
    "Search long-term memory for facts about the user.",
    {"query?": "string: Words to look for; empty lists recent memories"},
)
def recall(ctx, query=""):
    rows = db.q("SELECT * FROM memories WHERE owner=? ORDER BY used DESC LIMIT 200", (db.owner_of(ctx.conv_id),))
    words = [w for w in (query or "").lower().split() if len(w) > 2]
    if words:
        rows = [r for r in rows if any(w in r["text"].lower() for w in words)]
    if not rows:
        return Result("No matching memories.")
    for row in rows[:30]:
        db.x("UPDATE memories SET used=? WHERE id=?", (time.time(), row["id"]))
    return Result("\n".join("#%s [%s] %s" % (r["id"], r["kind"], r["text"]) for r in rows[:30]))


@tool("forget", "Delete a memory by its id (from recall).", {"id": "integer: Memory id"}, risk="write")
def forget(ctx, id):
    count = db.x("DELETE FROM memories WHERE id=? AND owner=?", (int(id), db.owner_of(ctx.conv_id)))
    return Result("Forgot memory #%s." % id if count else "No memory #%s." % id, ok=bool(count))


@tool(
    "update_plan",
    "Show or update a step-by-step plan for a multi-step goal. Call it first with all steps, then again as steps progress.",
    {
        "steps": {
            "type": "array",
            "description": "Ordered steps",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "active", "done", "failed"]},
                },
                "required": ["title"],
            },
        },
        "note?": "string: Short status note",
    },
)
def update_plan(ctx, steps, note=""):
    clean = []
    for step in steps or []:
        if isinstance(step, str):
            step = {"title": step}
        clean.append({"title": str(step.get("title", "")).strip(), "status": step.get("status") or "pending"})
    done = sum(1 for s in clean if s["status"] == "done")
    return Result(
        "Plan updated: %d/%d steps done." % (done, len(clean)),
        display={"type": "plan", "steps": clean, "note": note},
    )


def plan_steps(plan):
    """Numbered or bulleted top-level lines of a Markdown plan -> [{title, status}] for the job's checklist."""
    steps = []
    for line in str(plan or "").splitlines():
        m = re.match(r"^(?:\d+[.)]|[-*+])\s+(?:\[[ x]\]\s*)?(.+)$", line)
        if m:
            title = re.sub(r"`|\*\*|__|(?<!\w)[*_]|[*_](?!\w)", "", m.group(1)).strip()
            if title:
                steps.append({"title": title[:160], "status": "pending"})
    return steps[:30]


@tool(
    "submit_plan",
    "PLAN MODE ONLY: hand your plan to the user for review. They approve it (then the work starts), ask for changes "
    "(you get their words: revise and submit again) or reject it.",
    {"plan": "string: The plan in Markdown: goal, findings, numbered steps (files to add or change and how), how you will test it, open questions",
     "title?": "string: A short title for the plan"},
)
def submit_plan(ctx, plan, title=""):
    from .. import events
    agent = ctx.agent
    if not getattr(agent, "plan_mode", False):
        raise ToolError("You are not in plan mode: go ahead with the work.")
    plan = str(plan or "").strip()
    if len(plan) < 20:
        raise ToolError("Write the whole plan in plan (Markdown with numbered steps).")
    title = str(title or "").strip()[:120] or "Review the plan"
    steps = plan_steps(plan)
    if agent.run:
        decision, note = events.request_approval(agent.run, "submit_plan", {"title": title},
                                                 {"title": title, "plan": plan, "kind": "plan"}, timeout=24 * 3600, with_note=True)
    else:
        decision, note = "allow", ""
    if decision in ("allow", "always"):
        agent.plan_mode = False
        agent.approved_plan = plan
        agent.stop_after_tools = True
        agent.emit({"type": "plan_approved", "plan": plan, "note": note, "title": title})
        extra = (" with this note: %s" % note) if note else ""
        return Result("The user approved the plan%s. Stop here with one short sentence; the building starts right after." % extra,
                      display={"type": "plan", "steps": steps, "note": "Approved" + (": " + note if note else "")})
    if note:
        return Result("The user wants changes before approving:\n%s\nRevise the plan (look at more code if needed) and call "
                      "submit_plan again." % note, ok=False)
    agent.stop_after_tools = True
    return Result("The user rejected the plan. Stop, and say in one or two sentences what you would need to plan it differently.", ok=False)


@tool(
    "submit_tests",
    "TESTS-FIRST MODE ONLY: hand the failing tests you wrote to the user for review. They approve them as the contract "
    "(then you build until they pass), ask for changes (you get their words) or reject them.",
    {"files": "array: The test files you wrote or changed (paths)",
     "command": "string: The exact command that runs these tests (e.g. python -m pytest tests/test_cart.py)",
     "summary": "string: Markdown: what the tests check (cases, edge cases, errors) and why they fail now"},
)
def submit_tests(ctx, files, command, summary):
    from .. import contract as C
    from .. import events
    agent = ctx.agent
    if not getattr(agent, "tests_mode", False):
        raise ToolError("You are not in tests-first mode: go ahead with the work.")
    root = agent.workdir or os.getcwd()
    files = [str(f) for f in (files if isinstance(files, list) else [files]) if str(f).strip()]
    if not files:
        raise ToolError("Name the test files in files.")
    content = C.snapshot(files, root)
    missing = [f for f in files if not any(os.path.normpath(os.path.join(root, f)) == os.path.normpath(os.path.join(root, r)) for r in content)]
    if missing:
        raise ToolError("These files do not exist: %s. Write them first." % ", ".join(missing))
    not_tests = [r for r in content if not C.is_test_file(r)]
    if not_tests:
        raise ToolError("Only test files belong in the contract: %s." % ", ".join(not_tests))
    command = str(command or "").strip()
    if not command:
        raise ToolError("Give the command that runs these tests.")
    title = "Review the tests"
    review = C.review_markdown(str(summary or ""), content, command)
    if agent.run:
        decision, note = events.request_approval(agent.run, "submit_tests", {"title": title},
                                                 {"title": title, "plan": review, "kind": "tests"}, timeout=24 * 3600, with_note=True)
    else:
        decision, note = "allow", ""
    if decision in ("allow", "always"):
        agent.tests_mode = False
        agent.approved_tests = C.Contract(list(content), command, str(summary or ""), root, content).to_dict()
        agent.stop_after_tools = True
        agent.emit({"type": "tests_approved", "contract": agent.approved_tests, "note": note})
        return Result("The user approved the tests%s. Stop here with one short sentence; building starts right after."
                      % ((" with this note: %s" % note) if note else ""), display={"type": "plan", "steps": [{"title": "Tests approved: " + ", ".join(sorted(content)), "status": "done"}]})
    if note:
        return Result("The user wants changes to the tests before approving:\n%s\nRevise them, run them (they must fail for the "
                      "right reason) and call submit_tests again." % note, ok=False)
    agent.stop_after_tools = True
    return Result("The user rejected the tests. Stop, and say in one or two sentences what you would need to write them differently.", ok=False)


@tool(
    "propose_test_change",
    "Ask the user to change one of the approved tests (the contract). Only when the test itself is wrong, not to make "
    "failing code pass. The user sees your reason and the new content and decides.",
    {"path": "string: The approved test file", "content": "string: Its complete new content", "why": "string: Why the approved test is wrong"},
)
def propose_test_change(ctx, path, content, why):
    import difflib
    from .. import events
    agent = ctx.agent
    deal = getattr(agent, "contract", None)
    if not deal:
        raise ToolError("There are no approved tests to change.")
    root = deal.root
    full = os.path.normpath(path if os.path.isabs(path) else os.path.join(root, path))
    if not deal.locked(full):
        raise ToolError("%s is not one of the approved tests; you may change it normally." % path)
    rel = os.path.relpath(full, root).replace("\\", "/")
    diff = "\n".join(difflib.unified_diff(deal.content[rel].splitlines(), str(content).splitlines(), "a/" + rel, "b/" + rel, lineterm=""))
    review = "**Why:** %s\n\n```diff\n%s\n```" % (str(why or "").strip(), diff[:12000])
    decision, note = (events.request_approval(agent.run, "propose_test_change", {"path": rel},
                                              {"title": "Change an approved test?", "plan": review, "kind": "test_change"},
                                              timeout=24 * 3600, with_note=True) if agent.run else ("deny", ""))
    if decision in ("allow", "always"):
        deal.amend(rel, str(content))
        return Result("The user agreed: %s is changed and is part of the contract now.%s" % (rel, (" Their note: " + note) if note else ""))
    return Result("The user kept the approved test as it is%s. Make the code satisfy it." % ((": " + note) if note else ""), ok=False)


@tool(
    "send_notification",
    "Send an alert to the user: shows in the app on every device, as a desktop notification on the host, "
    "and as a phone push via ntfy if configured.",
    {"title": "string: Short title", "message": "string: Body text", "level?": "string[info,success,warning,urgent]: Importance"},
    risk="write",
)
def send_notification(ctx, title, message, level="info"):
    channels = notify.send(title, message, level=level)
    return Result("Notification sent via %s." % ", ".join(channels))


_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_NAMES = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
_NAMES.update({
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum, "len": len,
    "mean": statistics.mean, "median": statistics.median, "stdev": statistics.stdev,
})


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, complex)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 10000:
            raise ToolError("Exponent too large.")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e) for e in node.elts]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _NAMES:
        return _NAMES[node.func.id](*[_eval(a) for a in node.args])
    raise ToolError("Unsupported expression element: %s" % type(node).__name__)


@tool(
    "calculate",
    "Evaluate a math expression exactly (supports + - * / // % **, sqrt, sin, log, pi, e, mean, median, round...). "
    "Use it instead of mental arithmetic.",
    {"expression": "string: e.g. 'sqrt(2) * 15 / 4' or 'mean([3, 5, 9])'"},
)
def calculate(ctx, expression):
    expr = str(expression).replace("^", "**").replace("×", "*").replace("÷", "/")
    try:
        value = _eval(ast.parse(expr, mode="eval"))
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError("Could not evaluate: %s" % exc)
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return Result("%s = %s" % (expression, value))


@tool(
    "enable_tools",
    "Load additional tool groups for this conversation when you need capabilities you don't currently have. "
    "Groups: " + "; ".join("%s (%s)" % (k, v[1]) for k, v in GROUPS.items() if k != "core"),
    {"groups": "array<string>: Group ids to load"},
)
def enable_tools(ctx, groups):
    valid = [g for g in (groups or []) if g in GROUPS]
    if not valid:
        raise ToolError("Unknown groups. Valid: %s" % ", ".join(GROUPS))
    ctx.agent.extra_groups.update(valid)
    return Result("Loaded tool groups: %s. Their tools are available from your next step." % ", ".join(valid))


@tool("current_time", "Get the current local date, time and timezone of the host PC.", {})
def current_time(ctx):
    return Result(time.strftime("%A, %d %B %Y, %H:%M:%S %Z (UTC%z)"))
