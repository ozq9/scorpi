"""Dedicated agents: a small team that works in the background (coder, researcher, writer...) plus agents the user
creates, optionally on a schedule. The chat assistant hands jobs to them, relays their progress and reports back."""

import json
import os
import re
import secrets
import threading
import time
import traceback
import unicodedata

from . import db, events, gitops, limits, llm, notify, opencode, privacy, proof, scheduler, scores
from .config import DATA_DIR, SETTINGS, WORKSPACE

PROJECTS = os.path.join(WORKSPACE, "projects")
LINKS_PATH = os.path.join(DATA_DIR, "project_links.json")  # projects that live in a folder of the user's own: slug -> path

CODER_ROLE = """You are a senior software engineer. For every job:
1. Understand the goal. If the project folder already has files, get the lay of the land first: repo_map shows every file with its classes and functions, grep finds where something is defined or used, glob finds files by name, git_read shows recent changes. Read the files you will change before changing them. Otherwise choose a simple, robust stack (websites: plain HTML, CSS and JavaScript unless a framework is asked for).
2. Call update_plan with concrete steps (files to create, features, checks) and keep it current.
3. Write new files completely with write_file. Change existing files with edit_file (a unique snippet and its replacement; several changes to one file in one call with edits=[...]) or apply_patch for changes across several files. No placeholders, TODOs or "rest stays the same".
4. Every write and edit reports syntax checks. When it says PROBLEMS FOUND, fix them before anything else.
5. Test everything: run_tests runs the project's tests; check_code checks a whole folder (and that web pages link to files that exist); run scripts with run_python or run_shell. Start servers and watchers with run_shell(background=true), read them with process_output and stop them with process_stop. Read the errors, fix them and run again until it works. For web pages, visual_check opens the page in a hidden browser: you see a screenshot plus its script errors, failed files and sideways scrolling (width=390 for a phone); fix what it finds. If the same error survives two fixes, ask_expert gives you a second opinion from another model.
6. Add a short README.md: what it is and how to open or run it. For bigger changes, call review_changes before you report and fix the real findings.
7. Report: the features, the files with full paths, exactly how to open or run it (e.g. the full path of index.html or the local address of a running server) and ideas for next steps."""

BUILTIN = [
    {"id": "coder", "name": "Coder", "icon": "code", "skill": "code", "groups": ["code", "files", "web", "docs"], "max_steps": 80,
     "description": "Builds websites, apps and scripts in its own project folder, then runs and tests them.", "instructions": CODER_ROLE},
    {"id": "researcher", "name": "Researcher", "icon": "compass", "skill": "research", "groups": ["web", "knowledge", "docs", "files"], "max_steps": 40,
     "description": "Researches a topic across many sources, verifies claims and writes a cited report.",
     "instructions": "You research thoroughly and save longer reports as a document in the workspace."},
    {"id": "writer", "name": "Writer", "icon": "doc", "skill": "document", "groups": ["docs", "files", "web"], "max_steps": 30,
     "description": "Reads documents and writes polished texts, reports and files (PDF, Word, Excel).",
     "instructions": "You write clearly and save the result as a file in the workspace."},
    {"id": "analyst", "name": "Analyst", "icon": "chart", "skill": "data", "groups": ["data", "code", "docs", "files"], "max_steps": 40,
     "description": "Analyzes data files, computes statistics and builds charts and reports.", "instructions": ""},
    {"id": "operator", "name": "Operator", "icon": "monitor", "skill": "workflow", "groups": ["computer", "system", "files", "web"], "max_steps": 40,
     "description": "Operates the PC: programs, windows and a real browser.", "instructions": ""},
]

# Earlier default instructions of built-in agents: an install whose agent still has one of them gets the new default
# (instructions the user changed are kept).
PREVIOUS_DEFAULTS = {
    'coder': [
        (
            'You are a senior software engineer. For every job:\n'
            '1. Understand the goal. If the project folder already has files, get the lay of the land first: repo_map shows every file with its classes and functions, grep finds where something is defined or used, glob finds files by name, git_read shows recent changes. Read the files you will change before changing them. Otherwise choose a simple, robust stack (websites: plain HTML, CSS and JavaScript unless a framework is asked for).\n'
            '2. Call update_plan with concrete steps (files to create, features, checks) and keep it current.\n'
            '3. Write new files completely with write_file. Change existing files with edit_file (a unique snippet and its replacement; several changes to one file in one call with edits=[...]) or apply_patch for changes across several files. No placeholders, TODOs or "rest stays the same".\n'
            '4. Every write and edit reports syntax checks. When it says PROBLEMS FOUND, fix them before anything else.\n'
            "5. Test everything: run_tests runs the project's tests; check_code checks a whole folder (and that web pages link to files that exist); run scripts with run_python or run_shell. Start servers and watchers with run_shell(background=true), read them with process_output and stop them with process_stop. Read the errors, fix them and run again until it works. If the same error survives two fixes, ask_expert gives you a second opinion from another model.\n"
            '6. Add a short README.md: what it is and how to open or run it. For bigger changes, call review_changes before you report and fix the real findings.\n'
            '7. Report: the features, the files with full paths, exactly how to open or run it (e.g. the full path of index.html or the local address of a running server) and ideas for next steps.'
        ),
        (
            'You are a senior software engineer. For every job:\n'
            '1. Understand the goal. If the project folder already has files, get the lay of the land first: repo_map shows every file with its classes and functions, grep finds where something is defined or used, glob finds files by name, git_read shows recent changes. Read the files you will change before changing them. Otherwise choose a simple, robust stack (websites: plain HTML, CSS and JavaScript unless a framework is asked for).\n'
            '2. Call update_plan with concrete steps (files to create, features, checks) and keep it current.\n'
            '3. Write new files completely with write_file. Change existing files with edit_file (a unique snippet and its replacement; several changes to one file in one call with edits=[...]) or apply_patch for changes across several files. No placeholders, TODOs or "rest stays the same".\n'
            '4. Every write and edit reports syntax checks. When it says PROBLEMS FOUND, fix them before anything else.\n'
            "5. Test everything: run_tests runs the project's tests; check_code checks a whole folder (and that web pages link to files that exist); run scripts with run_python or run_shell. Start servers and watchers with run_shell(background=true), read them with process_output and stop them with process_stop. Read the errors, fix them and run again until it works.\n"
            '6. Add a short README.md: what it is and how to open or run it.\n'
            '7. Report: the features, the files with full paths, exactly how to open or run it (e.g. the full path of index.html or the local address of a running server) and ideas for next steps.'
        ),
        (
            'You are a senior software engineer. For every job:\n'
            '1. Understand the goal and choose a simple, robust stack (websites: plain HTML, CSS and JavaScript unless a framework is asked for). If the project folder already has files, read them first and continue from there.\n'
            '2. Call update_plan with concrete steps (files to create, features, checks) and keep it current.\n'
            '3. Write complete, working files with write_file, one file per call, and change existing ones with edit_file. No placeholders, TODOs or "rest stays the same".\n'
            '4. Test everything: run scripts with run_python or run_shell (for example node --check app.js, python -m py_compile main.py, npm test). Read the errors, fix them and run again until it works. For websites check that every linked file exists and the scripts have no syntax errors.\n'
            '5. Add a short README.md: what it is and how to open or run it.\n'
            '6. Report: the features, the files with full paths, exactly how to open or run it (e.g. the full path of index.html) and ideas for next steps.'
        ),
    ],
}

FIELDS = ("name", "icon", "description", "instructions", "groups", "skill", "model", "auto_approve", "max_steps",
          "schedule", "action", "task", "enabled")

JOBS = {}
_lock = threading.Lock()


# ---------- agents ----------

def init():
    now = time.time()
    for b in BUILTIN:
        db.x("INSERT OR IGNORE INTO agents (id, name, kind, icon, description, instructions, groups, skill, max_steps, created, updated) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
             (b["id"], b["name"], "builtin", b["icon"], b["description"], b["instructions"], json.dumps(b["groups"]), b["skill"],
              b["max_steps"], now, now))
        row = db.one("SELECT instructions FROM agents WHERE id=?", (b["id"],))
        if row and row["instructions"] != b["instructions"] and row["instructions"] in PREVIOUS_DEFAULTS.get(b["id"], []):
            db.x("UPDATE agents SET instructions=?, updated=? WHERE id=?", (b["instructions"], now, b["id"]))
    # schedules missed while the PC was off run at their next slot instead of all at once
    for a in db.q("SELECT * FROM agents WHERE enabled=1 AND schedule!=''"):
        if not a["next_run"] or a["next_run"] < now - 300:
            db.x("UPDATE agents SET next_run=? WHERE id=?", (scheduler.next_run(_schedule(a)), a["id"]))


def _schedule(row):
    try:
        return json.loads(row.get("schedule") or "{}") or {}
    except ValueError:
        return {}


def slug(text, fallback="agent"):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or fallback


def get(ident):
    """An agent by id or (case-insensitive) name."""
    ident = str(ident or "").strip()
    if not ident:
        return None
    row = db.one("SELECT * FROM agents WHERE id=?", (ident,)) or db.one("SELECT * FROM agents WHERE lower(name)=lower(?)", (ident,))
    return row or db.one("SELECT * FROM agents WHERE id=?", (slug(ident),))


def public(row):
    out = dict(row)
    out["groups"] = json.loads(row.get("groups") or "[]")
    out["schedule"] = _schedule(row) or None
    out["describe"] = scheduler.describe(out["schedule"]) if out["schedule"] else "on demand"
    with _lock:
        out["working"] = sum(1 for j in JOBS.values() if j.agent["id"] == row["id"] and j.status == "running")
    return out


def all_agents():
    rows = db.q("SELECT * FROM agents ORDER BY kind='builtin' DESC, created")
    return [public(r) for r in rows]


def build_schedule(kind, time_="", day="", minutes=60, at=""):
    """Schedule dict from loose fields (as the model or the editor gives them); None for agents that run on demand."""
    kind = str(kind or "").strip().lower()
    if kind in ("", "none", "manual", "on_demand"):
        return None
    if kind not in ("daily", "weekdays", "weekly", "interval", "once"):
        raise ValueError("Unknown schedule %r (use daily, weekdays, weekly, interval or once)." % kind)
    sched = {"type": kind}
    if kind in ("daily", "weekdays", "weekly"):
        sched["time"] = _clock(time_)
    if kind == "weekly":
        sched["day"] = str(day or "monday").lower()
    if kind == "interval":
        sched["minutes"] = max(5, int(minutes or 60))
    if kind == "once":
        from .tools.agenda import parse_when
        sched["at"] = at if isinstance(at, (int, float)) else parse_when(at)
    return sched


def _clock(value):
    m = re.match(r"^\s*(\d{1,2})(?:[:.h](\d{2}))?", str(value or "08:00"))
    if not m:
        return "08:00"
    return "%02d:%02d" % (int(m.group(1)) % 24, int(m.group(2) or 0) % 60)


def save(data, aid=None):
    current = db.one("SELECT * FROM agents WHERE id=?", (aid,)) if aid else None
    if aid and not current:
        raise ValueError("No agent %r." % aid)
    merged = public(current) if current else {"icon": "agent", "description": "", "instructions": "", "groups": [], "skill": "workflow",
                                              "model": "", "auto_approve": 0, "max_steps": 40, "schedule": None, "action": "run",
                                              "task": "", "enabled": 1}
    merged.update({k: v for k, v in (data or {}).items() if k in FIELDS})
    name = str(merged.get("name") or "").strip()
    if not name:
        raise ValueError("An agent needs a name.")
    sched = merged.get("schedule") or None
    if isinstance(sched, dict) and sched.get("type") == "once" and isinstance(sched.get("at"), str):
        sched = build_schedule("once", at=sched["at"])
    action = merged.get("action") if merged.get("action") in ("run", "remind") else "run"
    if action == "remind" and not sched:
        raise ValueError("A reminder agent needs a schedule (e.g. daily at 13:00).")
    if action == "remind" and not str(merged.get("task") or "").strip():
        raise ValueError("Say what the reminder should tell the user (task).")
    nxt = scheduler.next_run(sched) if sched else None
    if sched and nxt is None:
        raise ValueError("That schedule never runs (is the time in the past?).")
    groups = [g for g in (merged.get("groups") or []) if isinstance(g, str)]
    values = (name, merged.get("icon") or "agent", str(merged.get("description") or ""), str(merged.get("instructions") or ""),
              json.dumps(groups), merged.get("skill") or "workflow", str(merged.get("model") or ""), int(bool(merged.get("auto_approve"))),
              max(5, min(200, int(merged.get("max_steps") or 40))), json.dumps(sched) if sched else "", action,
              str(merged.get("task") or ""), int(bool(merged.get("enabled", 1))), nxt if merged.get("enabled", 1) else None, time.time())
    if current:
        db.x("UPDATE agents SET name=?, icon=?, description=?, instructions=?, groups=?, skill=?, model=?, auto_approve=?, max_steps=?, "
             "schedule=?, action=?, task=?, enabled=?, next_run=?, updated=? WHERE id=?", values + (aid,))
    else:
        aid = slug(name)
        while db.one("SELECT id FROM agents WHERE id=?", (aid,)):
            aid = "%s-%s" % (slug(name), secrets.token_hex(2))
        db.x("INSERT INTO agents (name, icon, description, instructions, groups, skill, model, auto_approve, max_steps, schedule, action, "
             "task, enabled, next_run, updated, id, kind, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             values + (aid, "custom", time.time()))
    events.publish("agents", {"id": aid})
    return public(db.one("SELECT * FROM agents WHERE id=?", (aid,)))


def delete(aid):
    row = db.one("SELECT * FROM agents WHERE id=?", (aid,))
    if not row:
        raise ValueError("No agent %r." % aid)
    if row["kind"] == "builtin":
        raise ValueError("%s is one of Scorpi's built-in agents; it can be edited but not deleted." % row["name"])
    for job in running_jobs():
        if job.agent["id"] == aid:
            job.stop()
    db.x("DELETE FROM agents WHERE id=?", (aid,))
    events.publish("agents", {"id": aid, "deleted": True})


def set_enabled(aid, on):
    row = db.one("SELECT * FROM agents WHERE id=?", (aid,))
    if not row:
        raise ValueError("No agent %r." % aid)
    sched = _schedule(row)
    db.x("UPDATE agents SET enabled=?, next_run=? WHERE id=?", (int(bool(on)), scheduler.next_run(sched) if on and sched else None, aid))
    events.publish("agents", {"id": aid})


# ---------- jobs ----------

class Job:
    """One assignment for an agent: its own conversation and run(s), with live progress for the app and the chat."""

    def __init__(self, agent, title, goal, conv_id, parent_conv="", origin="agent", job_id=None, workdir="", project="", model=""):
        self.agent = agent
        self.title = title
        self.goal = goal
        self.conv_id = conv_id
        self.parent_conv = parent_conv
        self.origin = origin
        self.workdir = workdir
        self.project = project  # folder name under projects/ for coding jobs
        self.model = model  # this job's own model (e.g. "build it with deepseek flash"); else the agent's / coding model
        self.id = job_id
        self.oc_session = ""  # OpenCode session the job continues in, when the agent runs on OpenCode
        self.run = None
        self.status = "running"
        self.activity = "Getting started"
        self.says = ""
        self.plan = []
        self.subs = {}  # helper agents working in parallel for this job: id -> {name, task, status, activity, tools...}
        self.tools = 0
        self.approval = None
        self.question = None
        self.error = ""
        self.result = ""
        self.files = []
        self.started = time.time()
        self.updated = self.started
        self.checkpoint = ""  # the checkpoint taken before the current run (scorpi/gitops.py)
        self.planning = False  # this run only plans (read-only) and asks the user to approve the plan
        self.run_note = ""  # what the chat shows for a run the job started by itself
        self.attempt_of = ""  # the project this job is one of several parallel attempts for (scorpi/attempts.py)
        self.approved_plan = ""  # set when the user approves: the job then builds it in a new run
        self.resume_at = 0  # every model was at its limit: the job waits ("waiting") and continues at this time
        self.testing = False  # this run writes tests for the user to approve first (tests first, contract.py)
        self.contract = None  # the approved tests the job builds against: {files, command, summary, content}
        self.pending_contract = None  # approved in this run: the build starts once it ends
        self.proof = None  # what the last run verified (proof.py)
        self.queued = ""  # the queue item this job works on (backlog.py): approvals then go to the phone too
        self._published = 0.0

    def kwargs(self):
        a = self.agent
        instructions = a.get("instructions") or ""
        house = project_rules(self.workdir)
        if house:
            instructions += "\n\nThis project's own instructions (%s), follow them:\n%s" % house
        return {"persona": {"name": a["name"], "instructions": instructions, "workdir": self.workdir, "project": self.project,
                            "private": privacy.project_private(self.project or self.attempt_of), "checkpoint": self.checkpoint},
                "max_steps": int(a.get("max_steps") or 40), "auto_approve": bool(a.get("auto_approve")),
                "pin_model": self.model or a.get("model") or ""}

    def model_ref(self):
        """The model this job runs on, as the user would see it."""
        a = self.agent
        coding = a.get("skill") == "code"
        return self.model or a.get("model") or (llm.coding_model() if coding else "") or SETTINGS["model"] or ""

    def set_model(self, ref):
        """Switch the job to another model: at once for a running job (from its next step), else for its next run."""
        self.model = ref
        if self.id:
            db.x("UPDATE tasks SET model=? WHERE id=?", (ref, self.id))
        live = self.status == "running" and self.run and not self.run.done
        if live and hasattr(self.run, "switch_model"):
            self.run.switch_model(ref)  # the coding engine switches its session's model
        elif live:
            self.run.model_override = ref  # Scorpi's own loop picks it up before its next step
        self.activity = "Switched to %s" % llm._label(ref) if live else self.activity
        self.publish(force=True)
        return live

    def start(self, prompt, plan=False, system="", display="", tests=False):
        """Start a run. plan: look around and propose a plan first; the building starts once the user approves it.
        system: a short line the chat shows instead of the prompt, for runs the job starts by itself.
        display: what the chat shows as the user's message when the prompt is longer (a /command)."""
        from .agent import start_turn
        self.subs = {}  # helpers belong to one run
        self.proof = None
        self.planning = bool(plan)
        self.testing = bool(tests) and not plan
        self.pending_contract = None
        if self.testing:
            self.tests_request = prompt  # what the approved tests are for: the build gets it again
        self.approved_plan = ""
        self.resume_at = 0
        if self.id:
            limits.cancel_resume("job", self.id)
        if self.workdir and self.project and SETTINGS.get("checkpoints", True):
            # the files as they are before the agent touches them: Changes shows the run's diff, and it can be undone
            self.checkpoint = gitops.snapshot(self.project, self.workdir, "Before: %s — %s" % (self.title, _first_line(prompt)[:120]))
        private = privacy.project_private(self.project or self.attempt_of) or SETTINGS.get("private_mode")
        # private work, tests first and building against approved tests stay on Scorpi's own engine
        if self.workdir and opencode.use_for(self.agent) and not plan and not private and not self.testing and not self.contract:
            agent = dict(self.agent, model=self.model) if self.model else self.agent
            run = opencode.start(self.conv_id, prompt, agent, self.workdir, session=self.oc_session, on_session=self._on_session,
                                 title=self.title, system=system, display=display)
        else:
            groups = json.loads(self.agent.get("groups") or "[]") or None
            run = start_turn(self.conv_id, prompt, skill=self.agent.get("skill") or "", groups=groups, origin="agent",
                             title_hint=self.title, agent_kwargs=dict(self.kwargs(), plan_mode=bool(plan), tests_mode=self.testing,
                                                                     contract=self.contract), system=system,
                             display=display, command=display.split()[0][1:] if display.startswith("/") else "")
        self.run = run
        self.run_note = system
        self.status, self.error, self.approval, self.question = "running", "", None, None
        if self.id is None:
            self.id = run.id
            db.x("INSERT INTO tasks (id, title, status, created, updated, conv_id, run_id, origin, agent_id, parent_conv, goal, project, model) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (self.id, self.title, "running", self.started, time.time(), self.conv_id, run.id, self.origin, self.agent["id"],
                  self.parent_conv, self.goal[:8000], self.project, self.model))
            if self.oc_session:
                db.x("UPDATE tasks SET oc_session=? WHERE id=?", (self.oc_session, self.id))
        else:
            db.x("UPDATE tasks SET status='running', run_id=?, updated=? WHERE id=?", (run.id, time.time(), self.id))
        with _lock:
            JOBS[self.id] = self
        db.x("UPDATE agents SET last_run=?, last_status='running' WHERE id=?", (time.time(), self.agent["id"]))
        self.publish(force=True)
        threading.Thread(target=self._follow, args=(run,), daemon=True, name="job-" + self.id).start()
        return self

    def _on_session(self, sid):
        self.oc_session = sid
        if self.id:
            db.x("UPDATE tasks SET oc_session=? WHERE id=?", (sid, self.id))

    def _follow(self, run):
        try:
            for event in run.follow():
                self._on(event)
        except Exception:
            traceback.print_exc()
        self._settle(run)

    def _on(self, ev):
        kind = ev.get("type")
        if kind == "token":
            self.says = (self.says + ev.get("content", ""))[-400:]
            return self.publish()
        if kind == "thinking":
            if self.activity != "Thinking" and not self.approval and not self.question:
                self.activity = "Thinking"  # long reasoning between steps; don't keep showing the last finished step
                self.publish(force=True)
            return None
        if kind == "tool_start":
            self.tools += 1
            self.activity = activity(ev.get("name", ""), ev.get("args") or {}, ev.get("agent"))
            self.says = ""
            if ev.get("name") == "update_plan":
                self.plan = _plan((ev.get("args") or {}).get("steps"))
        elif kind == "tool_end":
            display = ev.get("display") or {}
            if display.get("type") == "plan":
                self.plan = _plan(display.get("steps"))
            if not ev.get("ok", True):
                self.activity = "Hit a problem, retrying: " + _first_line(ev.get("result"))[:140]
        elif kind in ("status", "notice"):
            self.activity = ev.get("text") or self.activity
        elif kind == "subagent":
            sub = self.subs.setdefault(ev.get("id") or str(len(self.subs)), {"started": time.time(), "tools": 0, "activity": "Starting"})
            sub.update({k: ev[k] for k in ("name", "task", "status", "activity", "tools", "result", "model") if ev.get(k) is not None})
            if sub.get("status") in ("done", "failed", "stopped"):
                sub.setdefault("ended", time.time())
            running = sum(1 for x in self.subs.values() if x.get("status") == "running")
            if running and not self.approval and not self.question:
                self.activity = "%d helper%s working in parallel" % (running, "" if running == 1 else "s")
        elif kind == "approval":
            summary = ev.get("summary") or {}
            self.approval = {"id": ev.get("id"), "tool": ev.get("tool"), "title": summary.get("title") or ev.get("tool"),
                             "args": summary.get("args") or {}, "why": summary.get("why") or "", "plan": summary.get("plan") or "",
                             "kind": summary.get("kind") or ""}
            self.activity = {"tests": "Waiting for you to review the tests", "test_change": "Asking you to change an approved test"}.get(
                summary.get("kind"), "Waiting for you to review the plan" if summary.get("plan") else "Waiting for your approval")
            if self.queued:
                # a queued task runs while the user is away: the approval reaches them wherever they are
                notify.send("Approval needed · %s" % self.title, (summary.get("title") or ev.get("tool") or "") + (" — " + summary["why"] if summary.get("why") else ""),
                            level="urgent", link="/c/" + self.conv_id)
        elif kind == "plan_approved":
            self.approved_plan = ev.get("plan") or ""
            if ev.get("note"):
                self.approved_plan += "\n\nThe user's note on approving it: " + ev["note"]
            from .tools.core import plan_steps
            self.plan = _plan(plan_steps(self.approved_plan))
            self.activity = "Plan approved, starting to build"
        elif kind == "card" and (ev.get("display") or {}).get("type") == "proof":
            self.proof = ev["display"]
        elif kind == "tests_approved":
            self.pending_contract = ev.get("contract") or None
            self.note_on_tests = ev.get("note") or ""
            self.activity = "Tests approved, starting to build"
        elif kind == "approval_done":
            self.approval = None
        elif kind == "question":
            self.question = {"id": ev.get("id"), "title": ev.get("title"), "fields": ev.get("fields") or []}
            self.activity = "Waiting for your answer"
        elif kind == "question_done":
            self.question = None
            self.activity = "Got your answer" if ev.get("answer") else self.activity
        elif kind == "error":
            self.error = ev.get("error") or "failed"
            self.resume_at = ev.get("resume_at") or 0
        elif kind == "done":
            msg = db.one("SELECT data FROM messages WHERE id=?", (ev.get("message_id"),))
            if msg:
                try:
                    self.files = list(dict.fromkeys(self.files + (json.loads(msg["data"] or "{}").get("files") or [])))
                except ValueError:
                    pass
        else:
            return
        self.publish(force=True)

    def _settle(self, run):
        if self.approved_plan and not run.cancelled:
            # the plan was approved: build it, on the coding engine this agent normally uses
            plan, notes = self.approved_plan, list(run.inbox)
            run.inbox[:] = []
            return self.start("Build the plan the user approved, step by step, and keep update_plan current:\n\n%s%s"
                              % (plan, ("\n\nThe user also wrote:\n" + "\n\n".join(notes)) if notes else ""),
                              system="Plan approved: building it")
        if self.pending_contract and not run.cancelled:
            # the user approved the tests: build until they pass, without changing them
            self.contract, self.pending_contract = self.pending_contract, None
            note = getattr(self, "note_on_tests", "")
            return self.start("Build what the user asked for so that the approved tests pass (run them with `%s`). Do not change "
                              "the test files; if one is truly wrong, call propose_test_change.\n\nThe request:\n%s%s"
                              % (self.contract["command"], getattr(self, "tests_request", "") or self.goal,
                                 ("\n\nThe user's note on approving the tests: " + note) if note else ""),
                              system="Tests approved: building until they pass")
        if run.inbox and not run.cancelled:
            # the user wrote after the agent's last step: carry on with it
            notes, run.inbox[:] = list(run.inbox), []
            return self.start("\n\n".join(notes))
        if self.error and self.resume_at and not run.cancelled:
            return self._pause()  # (the approved tests stay the contract when it continues)
        self.contract = None
        self.result = (run.result or "").strip()
        self.status = "stopped" if run.cancelled else "failed" if self.error else "done"
        self.activity = {"done": "Finished", "failed": "Failed: " + self.error[:160], "stopped": "Stopped"}[self.status]
        self.approval = None
        self.question = None
        self.says = ""
        for sub in self.subs.values():
            if sub.get("status") == "running":
                sub.update(status="stopped", ended=time.time())
        self.updated = time.time()
        if self.status != "stopped":
            used = self.model_ref()
            scores.record(used, "job_done" if self.status == "done" else "job_failed")
            if self.status == "done" and self.workdir:
                scores.record(used, "kept")  # until the user undoes it
        db.x("UPDATE tasks SET status=?, updated=?, result=? WHERE id=?", (self.status, self.updated, self.result[:8000], self.id))
        db.x("UPDATE agents SET last_status=? WHERE id=?", (self.status, self.agent["id"]))
        self.publish(force=True)
        events.publish("task", {"id": self.id, "status": self.status})
        if self.workdir and self.project and SETTINGS.get("checkpoints", True):
            threading.Thread(target=self._checkpoint_after, daemon=True).start()
        summary = _first_line(self.result)[:220]
        if self.proof and self.status == "done":
            summary = (proof.HEADLINE[self.proof["verdict"]] + " · " + summary)[:240]
        notify.send("%s · %s %s" % (self.title, self.agent["name"], {"done": "finished", "failed": "failed", "stopped": "stopped"}[self.status]),
                    summary or "Open Scorpi to see the result.", level="success" if self.status == "done" else "warning",
                    link="/c/" + (self.parent_conv or self.conv_id))
        if self.parent_conv and self.status != "stopped":
            threading.Thread(target=self._report, daemon=True).start()

    def _pause(self):
        """Every model is at its usage limit: wait, and continue by itself when the first one resets."""
        self.status = "waiting"
        self.activity = "Paused: every model is at its usage limit. Continues at %s" % limits.clock(self.resume_at)
        self.approval = self.question = None
        self.says = ""
        self.updated = time.time()
        limits.resume_later("job", self.id, self.resume_at)
        db.x("UPDATE tasks SET status='waiting', updated=? WHERE id=?", (self.updated, self.id))
        db.x("UPDATE agents SET last_status='waiting' WHERE id=?", (self.agent["id"],))
        self.publish(force=True)
        events.publish("task", {"id": self.id, "status": self.status})
        notify.send("%s · paused until %s" % (self.title, limits.clock(self.resume_at)),
                    "Every model is at its usage limit. %s continues by itself then." % self.agent["name"], level="info",
                    link="/c/" + (self.parent_conv or self.conv_id))

    def resume(self):
        """Continue a job that paused for usage limits (now, or when its time comes)."""
        if self.status != "waiting":
            return False
        self.error = ""
        self.start(limits.CONTINUE, system="Continuing after the usage limits reset")
        return True

    def _checkpoint_after(self):
        sha = gitops.snapshot(self.project, self.workdir, "After: %s (%s)" % (self.title, self.status))
        events.publish("checkpoint", {"project": self.project, "sha": sha})

    def _report(self):
        """Let the chat assistant tell the user, in the conversation the job came from, what the agent achieved."""
        from .agent import start_turn
        deadline = time.time() + 1800
        while events.active_run_for(self.parent_conv) and time.time() < deadline:
            time.sleep(2)  # the user is mid-conversation; report once that turn is over
        if not db.get_conversation(self.parent_conv):
            return
        note = "%s · %s" % (self.agent["name"], self.title)
        if not llm.default_model() or privacy.project_private(self.project):
            # (a private project's report is not handed to the chat model, which may not be private)
            text = "**%s** %s.\n\n%s" % (self.agent["name"], "finished" if self.status == "done" else "failed", self.result[:3000] or self.error)
            db.add_message(self.parent_conv, "assistant", text, {"parts": [], "note_for": note})
            events.publish("conversation", {"id": self.parent_conv})
            return
        prompt = ("[Agent update — written by Scorpi, not by the user] The %s agent %s the job \"%s\" (job id %s).\n"
                  "Its final report:\n---\n%s\n---\n%s"
                  "Tell the user in their language, in two to five sentences, what was done, where the results are (full paths) "
                  "and what they can do next. Don't repeat the whole report and don't call tools unless the user's question needs it."
                  % (self.agent["name"], "finished" if self.status == "done" else "failed at", self.title, self.id,
                     (self.result or self.error)[:6000], ("Files: %s\n" % ", ".join(self.files[:20])) if self.files else "")
                  + ("What Scorpi itself verified (say it plainly, especially what was not checked): %s\n" % proof.summary(self.proof)
                     if self.proof else ""))
        run = start_turn(self.parent_conv, prompt, groups=["agents"], origin="agent_update", note=note)
        events.publish("agent_update", {"conv_id": self.parent_conv, "run_id": run.id, "job_id": self.id, "note": note})

    def message(self, text, plan=False, display="", tests=False):
        """Pass the user's words on: at once to a running agent, else as a new run (plan: propose a plan first).
        display: what the chat shows instead of text (a /command whose instructions are longer)."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("Empty message.")
        if self.status == "running" and self.run and not self.run.done and hasattr(self.run, "send"):
            how = self.run.send(text)  # reaches the agent at once (or answers its question)
            self.activity = "Got your answer" if how == "answered" else "Got your message, reading it"
            self.publish(force=True)
            return how
        if self.status == "running" and self.run and not self.run.done:
            self.run.inbox.append(text)
            self.activity = "Got your message, reading it"
            self.publish(force=True)
            return "delivered"
        self.start(text, plan=plan, display=display, tests=tests)
        return "restarted"

    def stop(self):
        if self.status == "waiting":
            limits.cancel_resume("job", self.id)
            self.status, self.activity, self.resume_at = "stopped", "Stopped", 0
            db.x("UPDATE tasks SET status='stopped', updated=? WHERE id=?", (time.time(), self.id))
            self.publish(force=True)
            events.publish("task", {"id": self.id, "status": self.status})
            return True
        if self.run and not self.run.done:
            self.run.cancel()
            return True
        return False

    def public(self):
        done = sum(1 for s in self.plan if s["status"] == "done")
        subs = [dict(v, id=k) for k, v in self.subs.items()]
        return {"subs": subs, "subs_running": sum(1 for x in subs if x.get("status") == "running"), "model": self.model_ref(),
                "model_pinned": bool(self.model),
                "id": self.id, "agent_id": self.agent["id"], "agent_name": self.agent["name"], "agent_icon": self.agent.get("icon") or "agent",
                "title": self.title, "goal": self.goal[:600], "status": self.status, "activity": self.activity, "says": self.says[-220:],
                "plan": self.plan, "steps_done": done, "steps_total": len(self.plan), "tools": self.tools, "approval": self.approval, "question": self.question,
                "error": self.error, "result": self.result[:1500], "files": self.files[:20], "started": self.started,
                "updated": self.updated, "conv_id": self.conv_id, "parent_conv": self.parent_conv, "origin": self.origin,
                "workdir": self.workdir, "project": self.project, "checkpoint": self.checkpoint, "live": True,
                "planning": self.planning and self.status == "running", "run_id": self.run.id if self.run else "",
                "run_note": self.run_note, "attempt_of": self.attempt_of, "resume_at": self.resume_at if self.status == "waiting" else 0,
                "testing": self.testing and self.status == "running", "contract": (self.contract or {}).get("files") or [],
                "proof": self.proof}

    def publish(self, force=False):
        now = time.time()
        self.updated = now
        if not force and now - self._published < 1.2:
            return
        self._published = now
        events.publish("agent_job", self.public())


def activity(name, args, sub=None):
    a = args or {}
    base = lambda p: os.path.basename(str(p or "").rstrip("\\/")) or str(p or "")  # noqa: E731
    first = lambda s: _first_line(s)[:90]  # noqa: E731
    labels = {
        "write_file": "Writing %s" % base(a.get("path")), "edit_file": "Editing %s" % base(a.get("path")),
        "read_file": "Reading %s" % base(a.get("path")), "list_dir": "Looking into %s" % base(a.get("path")),
        "search_files": "Searching files for %s" % a.get("query", a.get("pattern", "")),
        "run_python": "Running Python: %s" % first(a.get("code")), "run_javascript": "Running JavaScript: %s" % first(a.get("code")),
        "run_shell": "Running: %s" % first(a.get("command")), "web_search": "Searching the web: %s" % a.get("query", ""),
        "fetch_page": "Reading %s" % a.get("url", ""), "update_plan": "Updating the plan", "create_document": "Creating %s" % a.get("title", "a document"),
        "screenshot": "Looking at the screen", "browser": "Using the browser", "make_chart": "Drawing a chart",
        "analyze_data": "Inspecting %s" % base(a.get("path")), "delegate": "Asking the %s sub-agent" % a.get("agent", ""),
        "lsp": "Checking the code", "task": "Starting a helper agent",
        "grep": "Searching the code for %s" % str(a.get("pattern", ""))[:60], "glob": "Finding files %s" % a.get("pattern", ""),
        "repo_map": "Mapping the codebase", "apply_patch": "Applying a patch", "check_code": "Checking the code",
        "run_tests": "Running the tests%s" % (": " + first(a.get("command")) if a.get("command") else ""),
        "process_output": "Reading a background process", "process_stop": "Stopping a background process",
        "git_read": "Reading git %s" % a.get("action", ""), "ask_expert": "Asking a second model", "submit_plan": "Proposing a plan", "submit_tests": "Proposing the tests",
        "propose_test_change": "Asking to change an approved test", "visual_check": "Looking at the page in a browser",
        "spawn_helpers": "Starting %d helpers in parallel" % len(a.get("helpers") or []), "set_agent_model": "Switching the model",
    }
    text = labels.get(name) or name.replace("_", " ").capitalize()
    return ("%s: %s" % (sub, text)) if sub else text


def _plan(steps):
    out = []
    for s in steps or []:
        if isinstance(s, str):
            s = {"title": s}
        if isinstance(s, dict) and s.get("title"):
            out.append({"title": str(s["title"])[:160], "status": s.get("status") if s.get("status") in ("pending", "active", "done", "failed") else "pending"})
    return out


def question_text(q):
    """One line for a question an agent is waiting on: the question and its options."""
    parts = []
    for f in (q or {}).get("fields") or []:
        opts = " / ".join(o.get("label") or o.get("value") or "" for o in f.get("options") or [])
        parts.append((f.get("description") or f.get("title") or "") + (" [options: %s]" % opts if opts else ""))
    return "; ".join(p for p in parts if p) or (q or {}).get("title") or "a question"


def _first_line(text):
    return next((line.strip() for line in str(text or "").splitlines() if line.strip()), "")


def assign(agent, task, title="", parent_conv="", origin="agent", project="", conv_id=None, model="", plan=False, display="", tests=False):
    """Hand a job to an agent; it starts at once in the background (plan: with a plan for the user to approve first).
    Returns the Job."""
    if isinstance(agent, str):
        row = get(agent)
        if not row:
            raise ValueError("No agent called %r. Agents: %s." % (agent, ", ".join(a["name"] for a in all_agents())))
        agent = row
    task = str(task or "").strip()
    if not task:
        raise ValueError("Describe the job.")
    title = str(title or "").strip() or _first_line(task)[:60]
    workdir = folder = ""
    if "code" in json.loads(agent.get("groups") or "[]"):
        folder = slug(project or title, "project")
        workdir = project_path(folder)
        os.makedirs(workdir, exist_ok=True)
    conv = db.get_conversation(conv_id) if conv_id else None
    if not conv:
        conv = db.create_conversation(title="%s · %s" % (agent["name"], title), skill=agent.get("skill") or "", origin="agent")
    prompt = "Job: %s\n\n%s" % (title, task)
    if parent_conv:
        prompt += "\n\n(Handed over by Scorpi's chat assistant on the user's behalf.)"
    return Job(agent, title, task, conv["id"], parent_conv=parent_conv, origin=origin, workdir=workdir, project=folder,
               model=model).start(prompt, plan=plan, display=display, tests=tests)


def job(job_id):
    """A job by id, also one from before a restart (rebuilt from its record so it can be continued)."""
    with _lock:
        found = JOBS.get(job_id)
    if found:
        return found
    row = db.one("SELECT * FROM tasks WHERE id=? AND agent_id!=''", (job_id,))
    agent = get(row["agent_id"]) if row else None
    if not row or not agent:
        return None
    restored = Job(agent, row["title"], row.get("goal") or "", row["conv_id"], parent_conv=row.get("parent_conv") or "",
                   origin=row["origin"], job_id=row["id"], model=row.get("model") or "")
    restored.status = row["status"] if row["status"] != "running" else "interrupted"
    restored.result = row["result"] or ""
    restored.started, restored.updated = row["created"], row["updated"]
    restored.oc_session = row.get("oc_session") or ""
    restored.activity = _row_activity(restored.status, job_id)
    if restored.status == "waiting":
        restored.resume_at = (limits.pending("job", job_id) or {}).get("at", 0)
    if row["origin"] == "attempt":
        from .attempts import attempt_of_job
        restored.attempt_of, restored.workdir = attempt_of_job(job_id)  # its own copy of the project, not a project folder
    elif "code" in json.loads(agent.get("groups") or "[]"):
        restored.project = row.get("project") or slug(row["title"], "project")
        restored.workdir = project_path(restored.project)
    return restored


def _row_activity(status, job_id=""):
    if status == "waiting":
        pending = limits.pending("job", job_id)
        return ("Paused: every model is at its usage limit. Continues at %s" % limits.clock(pending["at"])) if pending else "Paused"
    return {"done": "Finished", "failed": "Failed", "stopped": "Stopped"}.get(status, "Interrupted (Scorpi restarted)")


def job_for_conversation(conv_id):
    """The job working in this conversation (the user can write to it there like in any chat)."""
    row = db.one("SELECT id FROM tasks WHERE conv_id=? AND agent_id!='' ORDER BY created DESC LIMIT 1", (conv_id,)) if conv_id else None
    return job(row["id"]) if row else None


def running_jobs():
    with _lock:
        return [j for j in JOBS.values() if j.status == "running"]


def delete_jobs(target):
    """Remove finished jobs from the list: one job id, or "finished" for every job that is not running.
    Running jobs are left alone (stop them first). The job's conversation and project files stay. Returns (removed, skipped)."""
    if target in ("finished", "all"):
        ids = [r["id"] for r in db.q("SELECT id FROM tasks WHERE agent_id!=''")]
    else:
        if not db.one("SELECT 1 FROM tasks WHERE id=? AND agent_id!=''", (target,)):
            raise ValueError("No job %s." % target)
        ids = [target]
    removed, skipped = [], []
    for jid in ids:
        with _lock:
            live = JOBS.get(jid)
            if live and live.status == "running":
                skipped.append(live)
                continue
            JOBS.pop(jid, None)
        db.x("DELETE FROM tasks WHERE id=?", (jid,))  # a record still marked "running" without a live job is stale (Scorpi restarted)
        removed.append(jid)
    if removed:
        events.publish("agents", {"jobs_removed": removed})
    return removed, skipped


def jobs(limit=60, agent_id=None):
    rows = db.q("SELECT * FROM tasks WHERE agent_id!=''%s ORDER BY created DESC LIMIT ?" % (" AND agent_id=?" if agent_id else ""),
                ((agent_id, limit) if agent_id else (limit,)))
    out = []
    for row in rows:
        with _lock:
            live = JOBS.get(row["id"])
        if live:
            out.append(live.public())
            continue
        agent = get(row["agent_id"]) or {"id": row["agent_id"], "name": row["agent_id"], "icon": "agent"}
        status = row["status"] if row["status"] != "running" else "interrupted"
        waiting = job(row["id"]) if status == "waiting" else None
        if waiting:
            out.append(waiting.public())  # it continues by itself: keep it live
            continue
        out.append({"id": row["id"], "agent_id": agent["id"], "agent_name": agent["name"], "agent_icon": agent.get("icon") or "agent",
                    "title": row["title"], "goal": (row.get("goal") or "")[:600], "status": status,
                    "activity": {"done": "Finished", "failed": "Failed", "stopped": "Stopped"}.get(status, "Interrupted (Scorpi restarted)"),
                    "says": "", "plan": [], "steps_done": 0, "steps_total": 0, "tools": 0, "approval": None, "question": None, "error": "",
                    "result": (row["result"] or "")[:1500], "files": [], "started": row["created"], "updated": row["updated"],
                    "conv_id": row["conv_id"], "parent_conv": row.get("parent_conv") or "", "origin": row["origin"],
                    "project": row.get("project") or "", "model": row.get("model") or agent.get("model") or "", "subs": [],
                    "subs_running": 0, "live": False})
    return out


# ---------- coding projects (one folder each under projects/, worked on by coder jobs) ----------

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".cache", ".idea", ".vscode"}


def _project_of(row):
    """The project folder a job record belongs to ('' for jobs that are not coding work)."""
    if row.get("project"):
        return row["project"]
    if row.get("origin") == "attempt":
        return ""  # a parallel attempt works in a copy, not in a project of its own
    agent = get(row.get("agent_id"))
    if agent and "code" in json.loads(agent.get("groups") or "[]"):
        return slug(row["title"], "project")
    return ""


def project_jobs(folder):
    """Every job of a project, newest first (as jobs() reports them)."""
    rows = db.q("SELECT * FROM tasks WHERE agent_id!='' ORDER BY created DESC LIMIT 400")
    ids = [r["id"] for r in rows if _project_of(r) == folder]
    return [j for j in jobs(limit=400) if j["id"] in ids]


def latest_job(folder):
    """The job to continue when the user writes in a project: its newest one (live or rebuilt from its record)."""
    for row in db.q("SELECT * FROM tasks WHERE agent_id!='' ORDER BY created DESC LIMIT 400"):
        if _project_of(row) == folder:
            return job(row["id"])
    return None


def project_path(folder):
    folder = slug(folder, "")
    if not folder:
        raise ValueError("No project.")
    return links().get(folder) or os.path.join(PROJECTS, folder)


# Instructions repositories keep for coding agents, in the conventions of every popular agent (all are read)
RULE_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "CONVENTIONS.md", ".cursorrules", ".windsurfrules", ".clinerules",
              ".github/copilot-instructions.md", "CLAUDE.local.md")
RULE_DIRS = (".cursor/rules", ".clinerules", ".windsurf/rules", ".roo/rules", ".kilocode/rules", ".github/instructions", ".scorpi/rules")
RULES_LIMIT = 16000


def _rule_file(path):
    """(text, when) of one rules file; when is "" or the files it applies to (Cursor's globs front matter)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read(RULES_LIMIT).strip()
    when = ""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if m:
        meta, text = m.group(1), text[m.end():].strip()
        globs = re.search(r"^(?:globs|applyTo)\s*:\s*(.+)$", meta, re.M)
        always = re.search(r"^alwaysApply\s*:\s*true", meta, re.M | re.I)
        if globs and globs.group(1).strip() not in ("", '""', "''", "**", '"**"') and not always:
            when = globs.group(1).strip().strip('"\'')
    return text, when


def project_rules(workdir):
    """(file names, text) of the instructions a repository keeps for coding agents (AGENTS.md, CLAUDE.md, Cursor,
    Cline, Windsurf, Copilot... rules), or None. Identical files are read once; rules for some files only are marked
    as such; AGENTS.md files in subfolders are listed so the agent reads them before working there."""
    if not workdir or not os.path.isdir(workdir):
        return None
    found, seen, size = [], set(), 0

    def add(rel, path):
        nonlocal size
        try:
            text, when = _rule_file(path)
        except OSError:
            return
        key = " ".join(text.split())
        if not text or key in seen or size >= RULES_LIMIT:
            return
        seen.add(key)
        text = text[:RULES_LIMIT - size]
        size += len(text)
        found.append((rel, text, when))

    for name in RULE_FILES:
        path = os.path.join(workdir, *name.split("/"))
        if os.path.isfile(path):
            add(name, path)
    for folder in RULE_DIRS:
        base = os.path.join(workdir, *folder.split("/"))
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                if name.lower().endswith((".md", ".mdc", ".txt")) and os.path.isfile(os.path.join(base, name)):
                    add(folder + "/" + name, os.path.join(base, name))
    nested = []
    for root, dirs, files in os.walk(workdir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if root.count(os.sep) - workdir.count(os.sep) > 4:
            dirs[:] = []
        if root != workdir:
            for name in ("AGENTS.md", "CLAUDE.md"):
                if name in files:
                    nested.append(os.path.relpath(os.path.join(root, name), workdir).replace("\\", "/"))
        if len(nested) >= 30:
            break
    if not found and not nested:
        return None
    parts = []
    for rel, text, when in found:
        parts.append("From %s%s:\n%s" % (rel, (" (applies to files matching %s)" % when) if when else "", text))
    if nested:
        parts.append("Subfolders have their own instructions; read the one that covers a folder before changing files there: "
                     + ", ".join(nested))
    names = ", ".join(rel for rel, _, _ in found) or "subfolder instructions"
    return names, "\n\n".join(parts)


def links():
    """Projects opened from a folder of the user's own (anywhere on the PC): slug -> path."""
    try:
        with open(LINKS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def link_folder(path, name=""):
    """Open an existing folder as a project (its files stay where they are). Returns the project's slug."""
    path = os.path.abspath(os.path.expanduser(str(path or "").strip().strip('"')))
    if not os.path.isdir(path):
        raise ValueError("No folder at %s." % path)
    data = links()
    for key, value in data.items():
        if os.path.normcase(value) == os.path.normcase(path):
            return key
    base = slug(name or os.path.basename(path.rstrip("\\/")), "project")
    folder, n = base, 2
    while folder in data or os.path.exists(os.path.join(PROJECTS, folder)):
        folder, n = "%s-%d" % (base, n), n + 1
    data[folder] = path
    tmp = LINKS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, LINKS_PATH)
    return folder


def unlink_folder(folder):
    data = links()
    if folder not in data:
        raise ValueError("That project is not a linked folder.")
    data.pop(folder)
    with open(LINKS_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def projects():
    """Project folders with their latest job, newest activity first."""
    os.makedirs(PROJECTS, exist_ok=True)
    latest = {}
    for j in jobs(limit=400):
        folder = j.get("project") or _project_of({"project": "", "agent_id": j["agent_id"], "title": j["title"]})
        if folder and folder not in latest:
            latest[folder] = j
    out = []
    linked = links()
    for name in sorted(set(os.listdir(PROJECTS)) | set(linked)):
        path = project_path(name) if name in linked else os.path.join(PROJECTS, name)
        if not os.path.isdir(path):
            continue
        files, newest = 0, os.path.getmtime(path)
        for base, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            files += len(names)
            if files > 2000:
                break
            for n in names[:200]:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(base, n)))
                except OSError:
                    pass
        j = latest.get(name)
        out.append({"slug": name, "name": (j or {}).get("title") or name.replace("-", " ").capitalize(), "path": path, "files": files,
                    "updated": max(newest, (j or {}).get("updated") or 0), "job": j, "linked": name in linked,
                    "private": privacy.project_private(name),
                    "git": gitops.is_repo(path), "has_index": os.path.isfile(os.path.join(path, "index.html"))})
    out.sort(key=lambda p: -p["updated"])
    out.sort(key=lambda p: (p["job"] or {}).get("status") != "running")  # stable: working projects first, then by activity
    return out


def project_tree(folder, limit=1500):
    """Files of a project as [{path, dir, size, mtime}] with forward-slash paths relative to the project."""
    root = project_path(folder)
    out = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        rel = os.path.relpath(base, root).replace("\\", "/")
        rel = "" if rel == "." else rel + "/"
        for d in dirs:
            out.append({"path": rel + d, "dir": True})
        for n in sorted(names):
            try:
                st = os.stat(os.path.join(base, n))
            except OSError:
                continue
            out.append({"path": rel + n, "dir": False, "size": st.st_size, "mtime": st.st_mtime})
        if len(out) >= limit:
            break
    return sorted(out[:limit], key=lambda f: f["path"].lower())


def has_jobs(conv_id):
    return bool(conv_id and db.one("SELECT 1 FROM tasks WHERE parent_conv=? AND created>?", (conv_id, time.time() - 3 * 86400)))


def roster_note():
    """What the chat assistant needs to know to lead the team."""
    rows = all_agents()
    if not rows:
        return ""
    lines = ["Your team of dedicated agents. They work in the background in their own conversations while you stay with the user:"]
    for a in rows:
        extra = []
        if a["schedule"]:
            extra.append(a["describe"] + ("" if a["enabled"] else ", paused"))
        if a["action"] == "remind":
            extra.append("reminds: " + a["task"][:80])
        lines.append("- %s (id %s): %s%s" % (a["name"], a["id"], a["description"] or "custom agent", (" [" + "; ".join(extra) + "]") if extra else ""))
    lines.append(
        "Hand bigger jobs to them with assign_agent instead of doing them yourself: coding projects (websites, apps, games, programs, "
        "multi-file scripts) always go to the coder; long research to the researcher; reports to the writer. After handing off, tell "
        "the user in one or two sentences who is on it; the app shows the live progress and you will report back when it is done. "
        "When the user names a model for the job (e.g. 'build it with deepseek flash 4.1'), pass it as assign_agent's model; "
        "to switch a running or finished job's model use set_agent_model with its job id, and to change which model an agent "
        "uses from now on, set_agent_model with the agent (loose names work, Scorpi finds the model). Coders split big jobs "
        "into helper agents that work in parallel; the app shows each helper live. "
        "When the user asks how it is going, call agent_status; to pass on new wishes use message_agent; to stop a job use stop_agent; to remove finished jobs from the list use delete_agent_jobs. "
        "When the user wants a new agent (for example one that reminds them every day at 13:00, or does a task automatically on a "
        "schedule), use create_agent; change, pause or delete agents with manage_agent.")
    live = running_jobs()
    if live:
        lines.append("Jobs running right now:\n" + "\n".join(
            "- job %s: %s · %s (model %s) — %s%s%s" % (j.id, j.agent["name"], j.title, llm._label(j.model_ref()), j.activity,
                                            (" (plan %d/%d)" % (sum(1 for s in j.plan if s["status"] == "done"), len(j.plan))) if j.plan else "",
                                            (" — asks the user: " + question_text(j.question)) if j.question else "")
            for j in live))
    recent = [p for p in projects()[:8] if p["job"]]
    if recent:
        lines.append(
            "Coding projects (the user also works on them in Scorpi's Code area). When the user talks about one here, pass "
            "their wishes to its agent with message_agent and that job id: it continues the project, even when the job had finished.\n"
            + "\n".join("- %s (folder %s, job %s, %s)" % (p["name"], p["path"], p["job"]["id"], p["job"]["status"]) for p in recent))
    return "\n".join(lines)


# ---------- schedules ----------

def fire(row):
    """Run a scheduled agent now: a reminder is just a notification; other agents do their task with tools."""
    row = dict(row)
    if row.get("action") == "remind":
        text = row.get("task") or row.get("description") or row["name"]
        notify.send(row["name"], text, level="urgent", link="/agents")
        now = time.time()
        db.x("INSERT INTO tasks (id, title, status, created, updated, conv_id, run_id, origin, agent_id, result) VALUES (?,?,?,?,?,?,?,?,?,?)",
             (db.new_id(), row["name"], "done", now, now, "", "", "schedule", row["id"], "Reminded you: " + text))
        db.x("UPDATE agents SET last_run=?, last_status='done' WHERE id=?", (now, row["id"]))
        events.publish("agents", {"id": row["id"]})
        return None
    j = assign(row, row.get("task") or row.get("description") or row["name"], title=row["name"], origin="schedule",
               conv_id=row.get("conv_id") or None)
    db.x("UPDATE agents SET conv_id=? WHERE id=?", (j.conv_id, row["id"]))
    return j


def tick(now):
    for a in db.q("SELECT * FROM agents WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=?", (now,)):
        db.x("UPDATE agents SET next_run=? WHERE id=?", (scheduler.next_run(_schedule(a), now + 1), a["id"]))
        try:
            fire(a)
        except Exception:
            traceback.print_exc()
