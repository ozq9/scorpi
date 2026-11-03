"""The agent loop: model <-> tools, approvals, skills, sub-agents and attachments."""

import base64
import inspect
import json
import mimetypes
import os
import platform
import socket
import threading
import time
import traceback

from . import db, events, llm
from .config import SETTINGS, UPLOADS, WORKSPACE
from .tools import TOOLS, Result, ToolError, policy, schema_for, select_tools

# ---------- skills: procedures layered on top of the tools ----------

SKILLS = {
    "research": {
        "name": "Research",
        "icon": "compass",
        "description": "Search several sources, compare evidence, verify claims and write a cited report.",
        "groups": ["web", "knowledge", "docs"],
        "instructions": """You are in RESEARCH mode. Procedure:
1. Restate the question and list the sub-questions you must answer (use update_plan).
2. Search broadly: at least 3 different web_search queries (different phrasings, and news when recency matters); also check knowledge_search for the user's own documents and wikipedia for background.
3. Read the most promising primary sources with fetch_page (aim for 3-6 independent sources, prefer official, academic and reputable outlets).
4. Compare evidence: note where sources agree, disagree, or are outdated. A claim counts as verified only if two independent sources support it; mark single-source claims as such.
5. Write the report: a short answer first, then sections with findings, a 'Points of disagreement / uncertainty' section, and 'Sources' as a numbered list [n] Title — URL. Cite inline with [n] after each factual claim.
6. If the user wants a file, save the report with create_document (pdf or docx).""",
    },
    "learning": {
        "name": "Tutor",
        "icon": "book",
        "description": "Explain concepts at the learner's level, generate practice and evaluate answers.",
        "groups": ["learning", "web", "code"],
        "instructions": """You are in TUTOR mode. Procedure:
1. Check memory (recall) for the learner's level and goals; if unknown, ask one short calibrating question or infer it from how they write.
2. Explain the concept in small steps: intuition first, then a concrete example or analogy, then the precise definition. Keep each step short and check understanding.
3. Offer practice: use present_quiz to create 3-5 exercises of increasing difficulty (mix multiple choice and open questions). For code or math, you may verify solutions with run_python/calculate.
4. When the learner answers, evaluate each answer: say what is right, correct misconceptions gently, and explain why. Adapt difficulty up or down.
5. Use remember to store the learner's level, strengths and weak spots for next time.""",
    },
    "workflow": {
        "name": "Workflow",
        "icon": "route",
        "description": "Break a complex goal into steps, use the right tools, recover from failures and verify.",
        "groups": [],
        "instructions": """You are in WORKFLOW mode for a multi-step goal. Procedure:
1. Call update_plan with a concrete numbered plan (3-10 steps) before acting.
2. Execute step by step with the most suitable tools; mark each step active/done with update_plan as you go. Load more tool groups with enable_tools if needed.
3. When a tool fails, read the error, diagnose, and try a different approach (other parameters, another tool, or a workaround). Mark the step failed only after reasonable attempts and explain why.
4. Verify results (re-read the file you wrote, re-run the code, check the page) before declaring success.
5. Finish with a short report: what was done, where outputs are (paths), what could not be done and suggested next steps.""",
    },
    "document": {
        "name": "Documents",
        "icon": "doc",
        "description": "Summarize documents, extract key information, compare contracts and prepare reports.",
        "groups": ["docs", "files", "data"],
        "instructions": """You are in DOCUMENT mode. Procedure:
- Always read the actual document(s) with read_document (attached files are already included) before answering; never guess content.
- Summaries: one-paragraph overview, then key points, then notable numbers/dates/obligations.
- Extraction: present extracted fields as a Markdown table (field | value | where found).
- Contract comparison: use compare_documents for exact differences, then a table per clause (parties, term, payment, liability, termination, IP, confidentiality, governing law...) showing A vs B, and a 'Risks & changes to watch' list. Quote exact wording for important clauses.
- Reports: when asked, produce a polished file with create_document (pdf/docx/xlsx) and give its path.""",
    },
    "code": {
        "name": "Code",
        "icon": "code",
        "description": "Write, run and debug code on the PC.",
        "groups": ["code", "files"],
        "instructions": "You are in CODE mode. Write clean, complete code. Run it with run_python/run_javascript/run_shell to verify it works before presenting it. Save multi-file work into the workspace with write_file and report paths.",
    },
    "data": {
        "name": "Analyst",
        "icon": "chart",
        "description": "Analyze datasets, compute statistics and build charts and reports.",
        "groups": ["data", "code", "docs", "files"],
        "instructions": "You are in DATA ANALYST mode. Inspect the data first with analyze_data, compute with pandas via run_python when needed, visualize with make_chart, and state findings with exact numbers. Offer an Excel or PDF report via create_document.",
    },
}

SUBAGENTS = {
    "researcher": {"groups": ["web", "knowledge"], "skill": "research", "about": "searches and verifies information on the web and in the knowledge base"},
    "coder": {"groups": ["code", "files"], "skill": "code", "about": "writes and runs code, debugs, edits files"},
    "analyst": {"groups": ["data", "code", "docs", "files"], "skill": "data", "about": "analyzes data files and builds charts"},
    "writer": {"groups": ["docs", "files"], "skill": "document", "about": "reads documents and writes polished reports/files"},
    "operator": {"groups": ["computer", "system", "files"], "skill": "workflow", "about": "operates the PC: apps, browser, windows"},
}


COMPUTER_GUIDE = """Operating the PC's GUI:
- Look before acting: screenshot returns the screen with numbered boxes on clickable controls and the same controls as a text list. Act on them with element=N; use x,y pixels only for things without a number.
- Be fast: put every step you can predict into one computer_actions call (e.g. click a field, type, press enter) and look once at the end. Look again before acting on UI that the steps change.
- Prefer the most direct route: keyboard shortcuts (ctrl+l, ctrl+s, alt+f4, win then typing an app name), launch_app for programs, windows to focus one, and the browser tool (not the mouse) for websites.
- After each action check the new screenshot: did the expected window, dialog or text appear? If not, try another way instead of repeating the same action.
- If an element list is missing (games, custom-drawn apps), use screenshot pixel coordinates and zoom to read small text."""

SEE_PROMPT = """You are the eyes of an assistant that cannot see images. It is operating a computer and just got the image(s) below with this tool output:
---
%s
---
Describe what is on screen so it can choose its next action: the active app/window and its state; any dialog, popup, menu, error or loading indicator; important visible text (quote it exactly); and where the key controls are. Refer to numbered boxes as [N] when they exist, otherwise give x,y pixel positions in the image. Be concise and factual; give no advice."""


def all_skills():
    custom = {}
    for s in SETTINGS["skills"] or []:
        if s.get("id") and s.get("name"):
            custom[s["id"]] = {"name": s["name"], "icon": s.get("icon") or "spark", "description": s.get("description", ""),
                               "groups": s.get("groups") or [], "instructions": s.get("instructions", ""), "custom": True}
    return {**SKILLS, **custom}


class Ctx:
    def __init__(self, agent):
        self.agent = agent
        self.run = agent.run
        self.conv_id = agent.run.conv_id if agent.run else ""

    def progress(self, text):
        self.agent.emit({"type": "status", "text": text})


class Agent:
    def __init__(self, run, model=None, skill=None, groups=None, depth=0, label="", emit=None,
                 persona=None, max_steps=None, auto_approve=False, pin_model=""):
        self.run = run
        # coding work (the Code skill, coder sub-agents) can run on its own model, e.g. an OpenCode Go plan;
        # a dedicated agent's own model (pin_model) wins over both
        coder = SETTINGS["code_model"] if skill == "code" else ""
        self.model = pin_model or coder or model or llm.default_model()
        self.persona = persona  # {"name", "instructions", "workdir"} for a dedicated agent working in the background
        self.max_steps = max_steps
        self.auto_approve = auto_approve
        self.skill = all_skills().get(skill) if skill else None
        self.skill_id = skill or ""
        self.groups_override = groups
        self.extra_groups = set(self.skill["groups"]) if self.skill else set()
        self.depth = depth
        self.label = label
        self._emit = emit or (run.emit if run else (lambda e: None))
        self.parts = []
        self.files = []
        self.no_tools = False
        self.switched = ""
        self.eyes = ""  # vision model describing images for a text-only model this turn

    def emit(self, event):
        self._emit(event)

    def cancelled(self):
        return bool(self.run and self.run.cancelled)

    def scope(self):
        """'code' for coding work (a project's agent, the Code skill), else 'chat': picks which MCP servers it gets."""
        return "code" if (self.persona and self.persona.get("workdir")) or self.skill_id == "code" else "chat"

    # ---------- prompt ----------

    def system_prompt(self):
        home = os.path.expanduser("~")
        lines = [
            "You are Scorpi, a calm, capable AI assistant that runs on the user's own PC (%s %s, host '%s'). "
            "Your tools really execute on that PC, so you can work with its files, programs, screen, browser and network."
            % (platform.system(), platform.release(), socket.gethostname()),
            "Now: %s. Workspace for files you create: %s. User home: %s." % (time.strftime("%A %d %B %Y, %H:%M (%Z, UTC%z)"), WORKSPACE, home),
        ]
        # whose chat this is: the owner at the PC, or a guest who signed in with a name
        owner = db.owner_of(self.run.conv_id) if self.run and self.run.conv_id else db.default_user()
        host = owner == db.default_user()
        name = SETTINGS["user_name"] if host else db.display_name(owner)
        if name:
            lines.append("The user's name is %s." % name)
        lines.append(
            "How you work:\n"
            "- Use tools for facts about files, the web, the PC, dates, maths and anything current; never invent tool results or file contents.\n"
            "- Chain tools when a task needs several steps. If a tool fails, read the error, adapt and try again or choose another route. Verify before you claim success.\n"
            "- If you need a capability that is not in your tool list, call enable_tools with the right group.\n"
            "- Risky actions are confirmed by the user through the app's approval system; do not ask for permission in text, just call the tool.\n"
            "- When you create or change files, give their full paths. Do not paste long file contents back.\n"
            "- When you used web sources, cite them inline as [n] and list them at the end.\n"
            "- The chat shows rich content, so show rather than describe: ![caption](path or URL) displays pictures, videos "
            "and sounds (local paths work too); a URL alone on its own line becomes a link preview or an embedded player "
            "(YouTube, Vimeo, images, video); file paths in `backticks` open a preview; ```mermaid blocks become diagrams, "
            "```html and ```svg blocks get a live preview; $$...$$ and \\(...\\) render as math. "
            "The show tool puts any file, PDF, document, page or website in front of the user.\n"
            "- Reply in the user's language. Be warm, concise and direct; use Markdown (short headings, lists, tables) when it helps."
        )
        if self.depth == 0 and not self.persona:
            if SETTINGS["response_style"] == "visual":
                lines.append(
                    "RESPONSE STYLE: VISUAL. The user wants to SEE answers, not read them. Beside the chat is a live canvas "
                    "(the canvas tool) that stays for the whole conversation. Put the substance of every answer with real "
                    "content (facts, numbers, steps, options, comparisons, plans, explanations, results, places, pictures) "
                    "on the canvas as visual blocks, and keep it current as the conversation moves on: update blocks by id, "
                    "remove stale ones, clear it when the topic changes. Prefer visual blocks (stats, charts, steps, "
                    "compare, timeline, cards) over text blocks. Your written reply is what the user hears read aloud: one "
                    "to three short, natural spoken sentences with the key takeaway. No lists, headings, tables or "
                    "Markdown in the reply; do not repeat what the canvas shows or narrate what you changed on it (never say "
                    "'the canvas now shows...'): just say the answer. Update the canvas title when the topic shifts. "
                    "Skip the canvas only for small talk "
                    "or a one-line answer. Code and long documents still go in code blocks or files.")
            else:
                lines.append(
                    "RESPONSE STYLE: TEXT. Answer in clear, well-structured text. A live canvas (the canvas tool) is "
                    "available beside the chat: use it when a visual (chart, comparison, timeline, plan) clearly helps, "
                    "and keep it current.")
            lines.append(
                "You can redesign Scorpi itself: when the user describes how the app should look or feel (a vibe, colours, "
                "fonts, sizes, 'cleaner', 'cozier') use style_app; for small interface features use app_plugin; to switch or "
                "undo use manage_looks (load them with enable_tools('looks') if they are not in your list). Make bold, "
                "coherent designs that match the vibe, and keep text readable on phones and desktops.")
            if self.run and self.run.conv_id:
                from .tools import canvas as canvas_tool
                note = canvas_tool.summary(self.run.conv_id)
                if note:
                    lines.append(note + "\nUpdate these blocks by id instead of adding duplicates.")
        if SETTINGS["memory_enabled"] and self.depth == 0:
            mems = db.q("SELECT text FROM memories WHERE owner=? ORDER BY used DESC LIMIT 40", (owner,))
            if mems:
                lines.append("What you remember about the user (use naturally; update with remember/forget):\n" + "\n".join("- " + m["text"] for m in mems))
            lines.append("When the user shares lasting preferences or facts about themselves, save them with remember.")
        from . import knowledge
        count = knowledge.doc_count()
        if count:
            lines.append("The user's knowledge base holds %d document(s); search it with knowledge_search when questions may concern their own material." % count)
        if self.persona:
            p = self.persona
            lines.append(
                "YOU ARE SCORPI'S %s AGENT, working in the background on a job the user handed over (usually through "
                "Scorpi's chat assistant). Nobody watches this conversation live: work autonomously, make sensible "
                "assumptions instead of asking (note them in your report), keep update_plan current so the user can follow "
                "your progress, and verify your work before you finish. New messages from the user may arrive while you "
                "work; follow them. End with a concise report: what you did, where the results are (full paths), how to "
                "open or run them, and anything left open." % p["name"].upper()
                + ("\nWork inside this folder unless told otherwise: %s" % p["workdir"] if p.get("workdir") else "")
                + ("\n\nYour role:\n" + p["instructions"] if p.get("instructions") else ""))
        elif self.depth == 0 and self.run and self.run.origin in ("chat", "agent_update"):
            from . import agents
            roster = agents.roster_note()
            if roster:
                lines.append(roster)
        if self.depth == 0 and not self.persona:
            from . import looks
            lines.extend(looks.prompt_sections())
        if self.skill:
            lines.append("ACTIVE SKILL — %s\n%s" % (self.skill["name"], self.skill["instructions"]))
        if SETTINGS["system_prompt"] and host:
            lines.append("User's standing instructions:\n" + SETTINGS["system_prompt"])
        return "\n\n".join(lines)

    # ---------- main loop ----------

    def run_turn(self, history, user_text=""):
        """history: list of {'role','content','images'?}. Returns final assistant text."""
        caps = llm.capabilities(self.model)
        model = self.model
        has_images = any(m.get("images") for m in history[-2:])
        seer = (llm.vision_model(need_tools=True) or llm.vision_model()) if has_images and "vision" not in caps else ""
        if seer:
            model = seer
            caps = llm.capabilities(model)
            self.emit({"type": "notice", "text": "Using %s to look at the image." % llm._label(model)})
        elif has_images and "vision" not in caps:
            self.emit({"type": "notice", "text": "%s may not support images. Pick a vision model (e.g. a free Gemini key, or qwen2.5vl locally) in Connections → Model." % model})

        messages = [{"role": "system", "content": self.system_prompt()}] + history
        options = {"temperature": float(SETTINGS["temperature"]), "num_ctx": int(SETTINGS["num_ctx"])}
        texts = []
        seen_calls = {}
        max_steps = int(self.max_steps or (SETTINGS["max_steps"] if self.depth == 0 else 12))

        for step in range(max_steps + 1):
            if self.cancelled():
                break
            override = getattr(self.run, "model_override", "") if self.depth == 0 and self.run else ""
            if override and override != model:
                # the user switched this job's model while it works (set_agent_model): carry on with the new one
                self.model = model = override
                caps = llm.capabilities(model)
                self.emit({"type": "notice", "text": "Now working with %s." % llm._label(model)})
            if self.depth == 0 and self.run and self.run.inbox and step < max_steps:
                notes, self.run.inbox[:] = list(self.run.inbox), []
                for note in notes:
                    messages.append({"role": "user", "content": "[New message from the user while you work — take it into account]\n" + note})
                    self.emit({"type": "notice", "text": "Message received: " + note[:200]})
            names, active = ([], set()) if self.no_tools else select_tools(user_text, self.groups_override, self.extra_groups, scope=self.scope())
            if "computer" in active and COMPUTER_GUIDE not in messages[0]["content"]:
                messages[0]["content"] += "\n\n" + COMPUTER_GUIDE
            tools = [schema_for(n) for n in names] if names and step < max_steps else None
            if step == max_steps:
                messages.append({"role": "user", "content": "(Step limit reached. Summarize what you have done and what remains, without calling tools.)"})
            self._trim_images(messages)
            text, thinking, calls = self._stream(model, messages, tools, options)
            if self.switched and self.switched != model:
                model = self.switched  # keep using the model that took over after a rate limit
                caps = llm.capabilities(model)
            if text.strip():
                texts.append(text.strip())
            if not calls:
                if self.depth == 0 and self.run and self.run.inbox and step < max_steps - 1:
                    messages.append({"role": "assistant", "content": text or ""})
                    continue  # the user wrote while this answer streamed: carry on with their message
                break
            messages.append({"role": "assistant", "content": text or "", "tool_calls": calls})
            images = []
            for call in calls:
                if self.cancelled():
                    break
                key = call["name"] + json.dumps(call["arguments"], sort_keys=True, default=str)
                seen_calls[key] = seen_calls.get(key, 0) + 1
                # the screen changes between calls, so repeating a GUI action (another "Next" click) is legitimate
                repeatable = (TOOLS.get(call["name"]) or {}).get("group") == "computer"
                if seen_calls[key] > 2 and not repeatable:
                    result = Result("You already ran this exact call twice. Change approach or answer with what you have.", ok=False)
                else:
                    result = self.execute(call, caps)
                messages.append({"role": "tool", "name": call["name"], "tool_call_id": call["id"], "content": (result.text or "")[:16000]})
                if result.images:
                    images.extend(result.images[:2])
            if images and not self.cancelled():
                model, caps = self._show_images(model, caps, images[-2:], messages)
        return "\n\n".join(texts)

    def _show_images(self, model, caps, images, messages):
        """Give tool images (screenshots, pages, charts) to the model. A text-only model hands the turn to a vision
        model that can use tools; if there is none, a vision model describes the images for it."""
        if "vision" not in caps:
            seer = llm.vision_model(need_tools=True)
            if seer and seer != model:
                self.emit({"type": "notice", "text": "%s can't see images, so %s takes over to look at the screen." % (llm._label(model), llm._label(seer))})
                self.switched = ""
                model, caps = seer, llm.capabilities(seer)
        if "vision" in caps:
            messages.append({"role": "user", "content": "[Image(s) returned by the tool(s) above — look at them to continue.]", "images": images, "_tool_images": True})
            return model, caps
        context = next((m["content"] for m in reversed(messages) if m.get("role") == "tool"), "")
        try:
            description, eyes = llm.describe_images(images, SEE_PROMPT % context[:3000])
        except llm.LLMError:
            description, eyes = "", ""
        if description:
            if not self.eyes:
                self.emit({"type": "notice", "text": "%s describes images for %s." % (llm._label(eyes), llm._label(model))})
            self.eyes = eyes
            messages.append({"role": "user", "content": "[What the image(s) above show, described by a vision model]\n" + description, "_tool_images": True})
        elif not self.eyes:
            self.eyes = "none"
            self.emit({"type": "notice", "text": "No vision model available, so working from the on-screen element lists only. Add one (e.g. a free Gemini key, or qwen2.5vl in Ollama) in Connections → Model to see apps without accessibility info."})
        return model, caps

    def _trim_images(self, messages):
        injected = [m for m in messages if m.get("_tool_images")]
        for m in injected[:-1]:
            m.pop("images", None)
            m["content"] = "[earlier screenshot omitted]"

    def _stream(self, model, messages, tools, options):
        clean = [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]
        conv = self.run.conv_id if self.run else ""
        # sub-agents get their own session (parallel helpers one each)
        sid = ("%s-%s%s" % (conv, self.depth, ("-" + self.label) if self.label else "") if self.depth else conv) if conv else ""
        with llm.session(sid):
            try:
                return self._consume(llm.chat_stream(model, clean, tools, options, self.cancelled))
            except llm.LLMError as exc:
                msg = str(exc).lower()
                if tools and ("does not support tools" in msg or "tool" in msg and "support" in msg):
                    self.no_tools = True
                    self.emit({"type": "notice", "text": "%s can't use tools, so answering without them. Pick a tool-capable model (qwen3, llama3.1, mistral-nemo...) for full abilities." % model})
                    return self._consume(llm.chat_stream(model, clean, None, options, self.cancelled))
                raise

    def _consume(self, stream):
        text, thinking, calls = "", "", []
        for event in stream:
            if event["type"] == "thinking":
                thinking += event["content"]
                self.emit({"type": "thinking", "content": event["content"]})
            elif event["type"] == "token":
                text += event["content"]
                self.emit({"type": "token", "content": event["content"]})
                if self.depth == 0 and self.run and self.run.inbox:
                    # the user said something mid-answer: stop here and turn to them (run_turn picks the message up)
                    close = getattr(stream, "close", None)
                    if close:
                        close()
                    text += " …"
                    self.emit({"type": "token", "content": " …\n\n"})
                    break
            elif event["type"] == "switch":
                self.switched = event["model"]
                self.emit({"type": "notice", "text": event["text"]})
            elif event["type"] == "end":
                calls = event["tool_calls"]
        if thinking:
            self.parts.append({"type": "thinking", "text": thinking})
        if text:
            self.parts.append({"type": "text", "text": text})
        return text, thinking, calls

    # ---------- tools ----------

    def execute(self, call, caps=()):
        name = call["name"]
        args = call["arguments"] if isinstance(call["arguments"], dict) else {}
        call_id = call["id"] + "-" + os.urandom(3).hex()
        entry = TOOLS.get(name)
        part = {"type": "tool", "id": call_id, "name": name, "args": args, "status": "running", "agent": self.label}
        self.parts.append(part)
        self.emit({"type": "tool_start", "id": call_id, "name": name, "args": args, "agent": self.label})
        started = time.time()
        if not entry:
            result = Result("Unknown tool %r. Use enable_tools to load more tools." % name, ok=False)
        else:
            mode = policy(name)
            if mode == "off":
                result = Result("The user has disabled the %s tool." % name, ok=False)
            else:
                allowed = True
                if mode == "ask" and self.run and not self.auto_approve:
                    decision = events.request_approval(self.run, name, args, _describe(name, args))
                    if decision == "always":
                        pol = dict(SETTINGS["tool_policy"] or {})
                        pol[name] = "allow"
                        SETTINGS.update({"tool_policy": pol})
                    allowed = decision in ("allow", "always")
                if not allowed:
                    result = Result("The user declined this action. Do not retry it; continue without it or ask what they prefer.", ok=False)
                else:
                    result = self._invoke(entry, args)
        part.update({"status": "done" if result.ok else "error", "ok": result.ok, "result": (result.text or "")[:2000],
                     "display": result.display, "elapsed": round(time.time() - started, 2)})
        self.files.extend(result.files or [])
        self.emit({"type": "tool_end", "id": call_id, "name": name, "ok": result.ok, "result": (result.text or "")[:2000],
                   "display": result.display, "elapsed": part["elapsed"], "agent": self.label})
        return result

    def _invoke(self, entry, args):
        fn = entry["fn"]
        try:
            sig = inspect.signature(fn)
            accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
            clean = args if accepts_kwargs else {k: v for k, v in args.items() if k in sig.parameters and k != "ctx"}
            result = fn(Ctx(self), **clean)
            if not isinstance(result, Result):
                result = Result(str(result))
            return result
        except ToolError as exc:
            return Result("Error: %s" % exc, ok=False)
        except TypeError as exc:
            return Result("Error: bad arguments for %s: %s" % (entry["name"], exc), ok=False)
        except Exception as exc:
            traceback.print_exc()
            return Result("Error: %s: %s" % (type(exc).__name__, exc), ok=False)


def _describe(name, args):
    short = {k: (v if not isinstance(v, str) or len(v) < 300 else v[:300] + "…") for k, v in (args or {}).items()}
    labels = {
        "run_shell": "Run a command on this PC", "run_python": "Run Python code on this PC", "run_javascript": "Run JavaScript on this PC",
        "manage_path": "Change files on disk", "mouse": "Use the mouse", "keyboard": "Type on the keyboard", "computer_actions": "Use the mouse and keyboard", "scroll": "Scroll the screen",
        "browser": "Control the browser", "launch_app": "Start a program", "kill_process": "Close a program",
        "http_request": "Send data to a web service", "db_delete": "Delete database records", "home_assistant": "Control smart-home devices",
    }
    return {"title": labels.get(name, "Use %s" % name.replace("_", " ")), "args": short}


# ---------- attachments ----------

def attachment_path(att_id):
    for name in os.listdir(UPLOADS):
        if name.startswith(att_id + "-"):
            return os.path.join(UPLOADS, name)
    return None


def prepare_user_message(text, attachments, emit):
    """Turn attachments into model-ready content (extracted text, transcripts, frames)."""
    content = text or ""
    images = []
    notes = []
    for att in attachments or []:
        path = attachment_path(att.get("id", ""))
        if not path:
            continue
        name = att.get("name") or os.path.basename(path)
        mime = att.get("mime") or mimetypes.guess_type(name)[0] or ""
        ext = os.path.splitext(name)[1].lower()
        try:
            if mime.startswith("image/") and ext != ".svg":
                images.append(att["id"])
                notes.append("[image attached: %s, saved at %s]" % (name, path))
            elif mime.startswith("audio/") or ext in (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm"):
                emit({"type": "status", "text": "Transcribing %s" % name})
                from . import voice
                transcript, lang = voice.transcribe_file(path, "auto", timestamps=True)
                notes.append("<attachment name=\"%s\" path=\"%s\" kind=\"audio transcript\" language=\"%s\">\n%s\n</attachment>" % (name, path, lang, transcript[:30000]))
            elif mime.startswith("video/") or ext in (".mp4", ".mov", ".mkv", ".avi"):
                emit({"type": "status", "text": "Watching %s" % name})
                frames = _video_frames(path)
                for i, frame in enumerate(frames):
                    fid = "%s_f%d" % (att["id"], i)
                    with open(os.path.join(UPLOADS, "%s-frame.jpg" % fid), "wb") as fh:
                        fh.write(base64.b64decode(frame))
                    images.append(fid)
                transcript = ""
                try:
                    from . import voice
                    transcript, _ = voice.transcribe_file(path, "auto", timestamps=True)
                except Exception:
                    pass
                notes.append("<attachment name=\"%s\" path=\"%s\" kind=\"video\">%d frames attached as images.%s</attachment>" % (
                    name, path, len(frames), ("\nAudio transcript:\n" + transcript[:20000]) if transcript else ""))
            else:
                emit({"type": "status", "text": "Reading %s" % name})
                from .tools.docs import extract_text
                body = extract_text(path, max_chars=40000)
                notes.append("<attachment name=\"%s\" path=\"%s\">\n%s\n</attachment>" % (name, path, body))
        except Exception as exc:
            notes.append("[attachment %s at %s could not be read: %s]" % (name, path, exc))
    if notes:
        content = (content + "\n\n" + "\n\n".join(notes)).strip()
    return content, images


def _video_frames(path, count=4):
    try:
        import cv2
    except ImportError:
        return []
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frames = []
    for i in range(count):
        if total:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / count))
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        if w > 960:
            frame = cv2.resize(frame, (960, int(h * 960 / w)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            frames.append(base64.b64encode(buf.tobytes()).decode())
    cap.release()
    return frames


def load_image(image_id):
    path = attachment_path(image_id)
    if not path:
        return None
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def history_for_model(conv_id, limit=40):
    rows = db.get_messages(conv_id, limit=limit)
    out = []
    user_rows = [r for r in rows if r["role"] == "user"]
    recent_image_rows = {r["id"] for r in user_rows[-2:]}
    for row in rows:
        data = row["data"] or {}
        if row["role"] == "user":
            msg = {"role": "user", "content": data.get("model_content") or row["content"]}
            if data.get("images") and row["id"] in recent_image_rows:
                imgs = [load_image(i) for i in data["images"]]
                msg["images"] = [i for i in imgs if i]
            out.append(msg)
        elif row["role"] == "assistant":
            content = row["content"] or ""
            tools = [p for p in data.get("parts", []) if p.get("type") == "tool"]
            if tools:
                summary = "; ".join("%s(%s)%s" % (t["name"], ", ".join("%s=%s" % (k, str(v)[:60]) for k, v in (t.get("args") or {}).items())[:160],
                                                   "" if t.get("ok", True) else " failed") for t in tools[:12])
                content = (content + "\n\n[tools used this turn: %s]" % summary).strip()
            if data.get("files"):
                content += "\n[files: %s]" % ", ".join(data["files"][:10])
            if content:
                out.append({"role": "assistant", "content": content})
    return out


# ---------- running a turn in the background ----------

def context_note(context):
    """Describe what Ember (the quick companion) is attached to, so the turn is about that window or panel."""
    if not isinstance(context, dict):
        return ""
    clip = lambda v, n: str(v or "").replace("\x00", "")[:n]  # noqa: E731
    notes = []
    if context.get("spoken"):
        notes.append("[The user is talking to Scorpi, the little scorpion voice companion, and your reply is read aloud. Answer like "
                     "a person speaking: one to three short sentences, no Markdown, lists, tables, links or code. If you did "
                     "something, say briefly what you did. Work that takes longer (building or changing a website or app, "
                     "research) goes to the right agent (e.g. assign_agent to the coder) and you say that it started.]")
    if context.get("kind") == "window" and context.get("glance"):
        r = context.get("rect") or {}
        p = context.get("pointer") or {}
        url = clip(context.get("url"), 600)
        notes.append("[The user held Scorpi's push-to-talk key while looking at the window \"%s\" (%s)%s%s. \"This\", \"here\" and "
                     "\"what I'm looking at\" mean that window%s. A picture of it is attached when available. To act in it, "
                     "focus it first with windows(action='focus', title=...) and take a screenshot.]"
                     % (clip(context.get("title"), 200), clip(context.get("process"), 80) or "unknown program",
                        ", showing %s" % url if url else "", ", %sx%s px" % (r.get("w"), r.get("h")) if r else "",
                        "; the mouse points at %s,%s in it" % (p.get("x"), p.get("y")) if p else ""))
    elif context.get("kind") == "window":
        r = context.get("rect") or {}
        where = " at %s,%s, %sx%s px" % (r.get("x"), r.get("y"), r.get("w"), r.get("h")) if r else ""
        notes.append("[Scorpi is attached to the window \"%s\" (%s)%s. The user's message is about this window: treat it as the "
                     "subject, and when you act, act in it. Before using the mouse or keyboard, focus it with "
                     "windows(action='focus', title=...) and take a screenshot. A snapshot of the window is attached when available.]"
                     % (clip(context.get("title"), 200), clip(context.get("process"), 80) or "unknown program", where))
    elif context.get("kind") == "element":
        notes.append("[The user pointed Scorpi at this part of the Scorpi app (%s). The message is about it:\n<context>\n%s\n</context>]"
                     % (clip(context.get("label"), 120), clip(context.get("text"), 8000)))
    return "\n".join(notes)


def start_turn(conv_id, user_text, attachments=None, skill="", model="", groups=None, origin="chat", title_hint="", context=None,
               agent_kwargs=None, note="", display="", command=""):
    """Store the user message and start a Run in a thread. Returns the Run.
    agent_kwargs configure a dedicated agent; note marks a message Scorpi wrote itself (shown as a small note, not a bubble).
    display: what the chat shows instead of user_text (a plugin's slash command: "/game", while its full prompt goes to the model)."""
    conv = db.get_conversation(conv_id)
    run = events.register_run(events.Run(conv_id, title=title_hint or (conv or {}).get("title", ""), origin=origin))
    base_data = {"attachments": attachments or [], "skill": skill}
    if note:
        base_data["note"] = note
    if display and command:
        base_data["command"] = command
    if context and context.get("kind") in ("window", "element"):
        # only what the chat shows as a chip; the full text goes to the model through model_content
        base_data["context"] = {k: context.get(k) for k in ("kind", "title", "process", "label", "url") if context.get(k)}
    user_mid = db.add_message(conv_id, "user", display or user_text, base_data)
    assistant_mid = db.new_id()

    def worker():
        db.set_user(db.owner_of(conv_id))  # this thread works for the chat's owner
        agent = Agent(run, model=model or None, skill=skill or None, groups=groups, **(agent_kwargs or {}))
        if context and context.get("kind") == "window":
            agent.extra_groups.add("computer")  # operating the attached window needs the screen tools
            if context.get("glance"):
                agent.extra_groups.update({"agents", "web"})  # "tell the coder to rebuild this site", "look this up"
        if origin in ("chat", "agent_update"):
            from . import agents
            if agents.has_jobs(conv_id):
                agent.extra_groups.add("agents")  # "how far is the coder?" needs the agent tools
        run.emit({"type": "start", "run_id": run.id, "conv_id": conv_id, "message_id": assistant_mid, "user_message_id": user_mid,
                  "model": agent.model, "skill": skill})
        error = ""
        text = ""
        try:
            if not agent.model:
                raise llm.LLMError("No model available. Add a free cloud key in Connections → Model (Groq, Gemini, OpenRouter…), or install Ollama and pull a model such as qwen3:8b.")
            model_content, image_ids = prepare_user_message(user_text, attachments, run.emit)
            note = context_note(context)
            if note:
                model_content = note + "\n\n" + model_content
            db.update_message(user_mid, data={**base_data, "model_content": model_content, "images": image_ids})
            history = history_for_model(conv_id)
            text = agent.run_turn(history, user_text)
        except llm.LLMError as exc:
            error = str(exc)
        except Exception as exc:
            traceback.print_exc()
            error = "%s: %s" % (type(exc).__name__, exc)
        if run.cancelled and not text:
            text = ""
        if not db.get_conversation(conv_id):
            # the chat was deleted while this turn was working: do not leave an invisible message behind
            run.finish()
            return
        data = {"parts": agent.parts, "model": agent.model, "skill": skill, "files": agent.files, "error": error, "stopped": run.cancelled}
        db.x("INSERT INTO messages (id, conv_id, role, content, data, created) VALUES (?,?,?,?,?,?)",
             (assistant_mid, conv_id, "assistant", text, json.dumps(data), time.time()))
        db.touch_conversation(conv_id)
        run.result = text or error
        if error:
            run.emit({"type": "error", "error": error})
        run.emit({"type": "done", "message_id": assistant_mid, "content": text, "stopped": run.cancelled})
        run.finish()
        events.publish("conversation", {"id": conv_id})
        if conv and conv.get("title") in ("", "New conversation") and text:
            threading.Thread(target=_auto_title, args=(conv_id, user_text, text), daemon=True).start()

    threading.Thread(target=worker, daemon=True, name="run-" + run.id).start()
    return run


def _auto_title(conv_id, question, answer):
    fallback = " ".join((question or "Conversation").split())[:60]
    # show a useful title at once; the model's nicer one can arrive later without the sidebar saying "New conversation"
    db.touch_conversation(conv_id, title=fallback)
    events.publish("conversation", {"id": conv_id, "title": fallback})
    title = fallback
    try:
        raw = llm.complete(
            "Write a 2-6 word title for this conversation. Reply with the title only, no quotes or punctuation at the end.\n\n"
            "User: %s\nAssistant: %s" % (question[:600], answer[:600]))
        raw = raw.strip().strip('"').strip("'").splitlines()[0] if raw.strip() else ""
        if 2 <= len(raw) <= 70:
            title = raw.rstrip(".")
    except Exception:
        pass
    if title != fallback:
        db.touch_conversation(conv_id, title=title)
        events.publish("conversation", {"id": conv_id, "title": title})


def run_detached(prompt, title, skill="workflow", origin="task", notify_done=True, conv_id=None):
    """Long-running background agent: own conversation, task record and a notification at the end."""
    conv = db.get_conversation(conv_id) if conv_id else None
    if not conv:
        conv = db.create_conversation(title=title, skill=skill, origin=origin)
    run = start_turn(conv["id"], prompt, skill=skill, origin=origin, title_hint=title)
    task_id = run.id
    db.x("INSERT INTO tasks (id, title, status, created, updated, conv_id, run_id, origin) VALUES (?,?,?,?,?,?,?,?)",
         (task_id, title, "running", time.time(), time.time(), conv["id"], run.id, origin))
    events.publish("task", {"id": task_id})

    def watcher():
        for _ in run.follow():
            pass
        failed = any(e.get("type") == "error" for e in run.events)
        status = "stopped" if run.cancelled else "failed" if failed else "done"
        db.x("UPDATE tasks SET status=?, updated=?, result=? WHERE id=?", (status, time.time(), (run.result or "")[:4000], task_id))
        events.publish("task", {"id": task_id, "status": status})
        if notify_done:
            from . import notify
            summary = (run.result or "").strip().split("\n")[0][:220]
            notify.send("%s · %s" % (title, {"done": "finished", "failed": "failed", "stopped": "stopped"}[status]),
                        summary or "Open Scorpi to see the result.", level="success" if status == "done" else "warning", link="/c/" + conv["id"])

    threading.Thread(target=watcher, daemon=True).start()
    return task_id, conv["id"], run


def run_subagent(parent_ctx, kind, task):
    spec = SUBAGENTS.get(kind) or SUBAGENTS["researcher"]
    parent = parent_ctx.agent
    if parent.depth >= 2:
        raise ToolError("Sub-agents cannot delegate further.")

    def relay(event):
        if event["type"] in ("tool_start", "tool_end", "status", "notice"):
            parent.emit(dict(event, agent=kind))

    sub = Agent(parent.run, model=parent.model, skill=spec["skill"], groups=spec["groups"] + ["core"], depth=parent.depth + 1, label=kind, emit=relay)
    text = sub.run_turn([{"role": "user", "content": "You are the %s sub-agent. Complete this task and reply with a self-contained result "
                                                       "(facts, paths, sources) for the main assistant:\n\n%s" % (kind, task)}], task)
    parent.files.extend(sub.files)
    return text, sub.files


MAX_HELPERS = 4


def run_subagents(parent_ctx, tasks):
    """Run several independent sub-tasks at the same time, each in its own helper agent with the parent's model and tools.
    Every helper reports its progress as "subagent" events, so the app can show who does what. Returns
    [(name, status, text, files)] in the order given."""
    from concurrent.futures import ThreadPoolExecutor
    parent = parent_ctx.agent
    if parent.depth >= 1:
        raise ToolError("Helpers cannot start helpers of their own; do the work yourself.")
    clean = []
    for i, t in enumerate(tasks or []):
        if isinstance(t, str):
            t = {"task": t}
        if isinstance(t, dict) and str(t.get("task") or "").strip():
            name = " ".join(str(t.get("name") or "").split())[:40] or "Helper %d" % (i + 1)
            clean.append({"name": name, "task": str(t["task"]).strip()})
    if not clean:
        raise ToolError("Give at least one task.")
    if len(clean) > 8:
        raise ToolError("At most 8 helpers per call; group the work into bigger pieces.")
    groups = sorted(parent.groups_override or parent.extra_groups or {"code", "files"})
    workdir = (parent.persona or {}).get("workdir") or ""
    tag = os.urandom(2).hex()

    def one(index, spec):
        sid = "%s-%d" % (tag, index)
        label = spec["name"]
        state = {"tools": 0}

        def emit(**fields):
            parent.emit(dict({"type": "subagent", "id": sid, "name": label}, **fields))

        def relay(event):
            kind = event.get("type")
            if kind == "tool_start":
                state["tools"] += 1
                from .agents import activity
                emit(activity=activity(event.get("name", ""), event.get("args") or {}), tools=state["tools"])
            if kind in ("tool_start", "tool_end", "status", "notice"):
                parent.emit(dict(event, agent=label))

        emit(status="running", task=spec["task"][:600], activity="Starting", tools=0, model=parent.model)
        sub = Agent(parent.run, model=parent.model, skill=parent.skill_id or None, groups=list(groups) + ["core"], depth=parent.depth + 1,
                    label=label, emit=relay, auto_approve=parent.auto_approve, max_steps=max(12, int(parent.max_steps or 24) // 2))
        sub.model = parent.model  # same model as the job, also for coding work with its own coding model
        prompt = ("You are %s, one of several helper agents working IN PARALLEL on parts of one job. Do only your part; other "
                  "helpers handle the rest at the same time, so do not touch files outside your part.%s\n\nYour part:\n%s\n\n"
                  "When done, reply with a short self-contained report for the lead agent: what you did, files (full paths), "
                  "checks you ran and anything left open." % (label, ("\nProject folder: %s" % workdir) if workdir else "", spec["task"]))
        try:
            text = sub.run_turn([{"role": "user", "content": prompt}], spec["task"])
            status = "stopped" if parent.cancelled() else "done"
        except Exception as exc:
            traceback.print_exc()
            text, status = "Failed: %s" % exc, "failed"
        first = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
        emit(status=status, activity={"done": "Finished", "failed": "Failed", "stopped": "Stopped"}[status],
             result=first[:300], tools=state["tools"])
        return label, status, text, sub.files

    with ThreadPoolExecutor(max_workers=min(MAX_HELPERS, len(clean)), thread_name_prefix="helper") as pool:
        results = list(pool.map(lambda pair: one(*pair), enumerate(clean)))
    for _, _, _, files in results:
        parent.files.extend(f for f in files if f not in parent.files)
    return results
