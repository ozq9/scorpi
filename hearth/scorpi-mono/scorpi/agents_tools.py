"""Agent-level tools: sub-agents, background tasks, automations and interactive quizzes."""

import json
import secrets
import time

from . import agents, db, events, llm, scheduler
from .agent import SUBAGENTS, run_detached, run_subagent, run_subagents
from .tools import Result, ToolError, tool
from .tools.agenda import parse_when


@tool(
    "delegate",
    "Hand a self-contained sub-task to a specialised sub-agent and get its result back. Agents: "
    + "; ".join("%s (%s)" % (k, v["about"]) for k, v in SUBAGENTS.items())
    + ". Waits for the answer, so use it for quick sub-tasks inside your own turn; for bigger jobs that should run in the "
    "background (e.g. a coding project) use assign_agent.",
    {"agent": "string[%s]: Which specialist" % ",".join(SUBAGENTS), "task": "string: Precise instructions incl. all context the sub-agent needs"},
    group="agents",
)
def delegate(ctx, agent, task):
    text, files = run_subagent(ctx, agent, task)
    return Result("Result from %s sub-agent:\n%s%s" % (agent, text or "(no answer)", ("\nFiles: " + ", ".join(files)) if files else ""))


@tool(
    "spawn_helpers",
    "Speed up a bigger job: split it into independent parts and let helper agents work on them AT THE SAME TIME (up to 4 "
    "run at once), each with your tools and model. Use it when parts don't depend on each other, e.g. separate pages, "
    "modules, tests or research questions; give each helper its own files so they never edit the same file. Waits until "
    "all are done and returns every helper's report; then integrate, run and test the whole thing yourself. The app "
    "shows each helper's live progress.",
    {
        "helpers": {
            "type": "array",
            "description": "One entry per helper",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short role, e.g. 'Styles', 'API', 'Tests'"},
                    "task": {"type": "string", "description": "Complete instructions incl. the exact files this helper owns and all context it needs"},
                },
                "required": ["task"],
            },
        },
    },
    group="code",
)
def spawn_helpers(ctx, helpers):
    results = run_subagents(ctx, helpers)
    blocks = ["### %s (%s)\n%s" % (name, status, (text or "(no report)")[:5000]) for name, status, text, _ in results]
    files = sorted({f for *_, fs in results for f in fs})
    ok = sum(1 for _, status, _, _ in results if status == "done")
    return Result("%d of %d helpers finished.\n\n%s%s" % (ok, len(results), "\n\n".join(blocks),
                                                         ("\n\nFiles: " + ", ".join(files[:40])) if files else ""), files=files)


@tool(
    "start_background_task",
    "Start a long-running task that continues in the background (even if the user closes the app) and notifies them when "
    "finished. Use for big research, batch file processing, monitoring, etc.",
    {"title": "string: Short task name", "goal": "string: Full instructions for the background agent", "skill?": "string[workflow,research,document,data,code]: Procedure"},
    group="agents",
    risk="write",
)
def start_background_task(ctx, title, goal, skill="workflow"):
    task_id, conv_id, _ = run_detached(goal, title, skill=skill or "workflow", origin="task")
    return Result("Background task '%s' started (id %s). The user will be notified when it finishes; results appear under Tasks." % (title, task_id),
                  display={"type": "task", "id": task_id, "title": title, "conv_id": conv_id})


@tool(
    "create_automation",
    "Create a recurring automation: a prompt that runs on a schedule and notifies the user with the result (e.g. a daily news "
    "digest at 07:30, a check every 60 minutes, a weekly report). Each run has full tool access.",
    {
        "name": "string: Automation name",
        "prompt": "string: What the agent should do each run",
        "schedule": "string[interval,daily,weekdays,weekly,once]: Schedule type",
        "time?": "string: HH:MM for daily/weekdays/weekly",
        "day?": "string[monday,tuesday,wednesday,thursday,friday,saturday,sunday]: For weekly",
        "minutes?": "integer: For interval (minimum 5)",
        "at?": "string: For once — when to run",
        "skill?": "string[workflow,research,document,data,code]: Procedure",
    },
    group="agents",
    risk="write",
)
def create_automation(ctx, name, prompt, schedule, time="", day="", minutes=60, at="", skill="workflow"):
    sched = {"type": schedule}
    if schedule in ("daily", "weekdays", "weekly"):
        sched["time"] = time or "08:00"
    if schedule == "weekly":
        sched["day"] = day or "monday"
    if schedule == "interval":
        sched["minutes"] = max(5, int(minutes or 60))
    if schedule == "once":
        sched["at"] = parse_when(at)
    nxt = scheduler.next_run(sched)
    if nxt is None:
        raise ToolError("That schedule never runs (is the time in the past?).")
    aid = save_automation({"name": name, "prompt": prompt, "schedule": sched, "skill": skill})
    return Result("Automation #%s '%s' created: %s. Next run %s." % (aid, name, scheduler.describe(sched), _fmt(nxt)),
                  display={"type": "automation", "id": aid, "name": name, "schedule": scheduler.describe(sched), "next": nxt})


@tool("list_automations", "List automations with their schedules and last results.", {}, group="agents")
def list_automations(ctx):
    rows = db.q("SELECT * FROM automations ORDER BY id")
    if not rows:
        return Result("No automations yet.")
    lines = []
    for r in rows:
        sched = json.loads(r["schedule"] or "{}")
        lines.append("#%d %s — %s, %s, next %s, last %s" % (r["id"], r["name"], scheduler.describe(sched), "on" if r["enabled"] else "paused",
                                                         _fmt(r["next_run"]) if r["next_run"] else "-", r["last_status"] or "never"))
    return Result("\n".join(lines))


@tool(
    "present_quiz",
    "Show the learner an interactive quiz card. The app checks multiple-choice answers instantly and sends the learner's "
    "answers back to you for evaluation and feedback.",
    {
        "title": "string: Quiz title",
        "questions": {
            "type": "array",
            "description": "Questions",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}, "description": "Choices for multiple choice; omit for open questions"},
                    "answer": {"type": "integer", "description": "Index of the correct option (0-based)"},
                    "explanation": {"type": "string"},
                },
                "required": ["question"],
            },
        },
    },
    group="learning",
)
def present_quiz(ctx, title, questions):
    clean = []
    for q in questions or []:
        if isinstance(q, str):
            q = {"question": q}
        clean.append({"question": str(q.get("question", "")), "options": [str(o) for o in (q.get("options") or [])],
                      "answer": q.get("answer"), "explanation": str(q.get("explanation") or "")})
    if not clean:
        raise ToolError("Give at least one question.")
    return Result("Quiz '%s' with %d question(s) is now shown to the learner. Wait for their answers." % (title, len(clean)),
                  display={"type": "quiz", "title": title, "questions": clean})


# ---------- dedicated agents (see agents.py) ----------

def _agent_names():
    return ", ".join("%s (%s)" % (a["id"], a["name"]) for a in agents.all_agents()) or "none"


@tool(
    "assign_agent",
    "Hand a job to one of the user's dedicated agents. It works in the background in its own conversation (the coder gets "
    "its own project folder), the app shows its live progress, and you are told when it is done so you can report back. "
    "Use it for coding projects (websites, apps, programs: agent 'coder'), long research, reports and any job a custom "
    "agent was made for. Give the agent everything it needs: goal, requirements, style, file names, where results go.",
    {
        "agent": "string: Agent id or name, e.g. coder, researcher, writer, analyst, operator or a custom agent",
        "task": "string: Complete instructions for the job, including all details the user gave",
        "title": "string: Short job title, e.g. 'Bakery website'",
        "project?": "string: Project/folder name for coding jobs; reuse the same name to continue an earlier project",
        "model?": "string: Only when the user names a model for this job, as they said it (e.g. 'deepseek flash 4.1', 'glm 5'); omit otherwise",
        "plan_first?": "boolean: The user wants to see and approve a plan before the agent changes anything",
    },
    group="agents",
    risk="write",
)
def assign_agent(ctx, agent, task, title, project="", model="", plan_first=False):
    try:
        ref = llm.find_model(model) if str(model or "").strip() else ""
        job = agents.assign(agent, task, title=title, parent_conv=ctx.conv_id, project=project, model=ref, plan=bool(plan_first))
    except ValueError as exc:
        raise ToolError(str(exc))
    where = (" Project folder: %s." % job.workdir) if job.workdir else ""
    where += (" Model: %s." % llm._label(ref)) if ref else ""
    where += " It first proposes a plan for the user to approve." if plan_first else ""
    return Result("%s is now working on '%s' in the background (job %s).%s Tell the user briefly that it's on it; you will get its "
                  "report when it finishes. Use agent_status to check progress." % (job.agent["name"], job.title, job.id, where),
                  display={"type": "agent_job", "id": job.id, "agent": job.agent["id"], "name": job.agent["name"],
                           "icon": job.agent.get("icon") or "agent", "title": job.title, "conv_id": job.conv_id})


@tool(
    "agent_status",
    "See what the dedicated agents are doing: running and recent jobs with their current step and plan, or one job in detail "
    "(including its latest output).",
    {"job_id?": "string: A job id for details; omit for an overview"},
    group="agents",
)
def agent_status(ctx, job_id=""):
    if job_id:
        job = agents.job(job_id)
        if not job:
            raise ToolError("No job %s." % job_id)
        p = job.public()
        lines = ["Job %s · %s · %s — status %s" % (p["id"], p["agent_name"], p["title"], p["status"]),
                 "Now: %s" % p["activity"], "Tool calls so far: %d" % p["tools"]]
        if p.get("model"):
            lines.append("Model: %s" % llm._label(p["model"]))
        if p.get("subs"):
            lines.append("Helpers working in parallel (%d running):\n%s" % (p["subs_running"], "\n".join(
                "  [%s] %s — %s" % (s.get("status"), s.get("name"), s.get("result") or s.get("activity") or "") for s in p["subs"])))
        if p["plan"]:
            lines.append("Plan (%d/%d done):\n%s" % (p["steps_done"], p["steps_total"],
                                                   "\n".join("  [%s] %s" % (s["status"], s["title"]) for s in p["plan"])))
        if p["approval"]:
            lines.append("Waiting for the user's approval to: %s" % p["approval"]["title"])
        if p.get("question"):
            lines.append("Waiting for an answer: %s (answer with message_agent once the user has decided)" % agents.question_text(p["question"]))
        if p["workdir"]:
            lines.append("Project folder: %s" % p["workdir"])
        if p["says"]:
            lines.append("Latest output: …%s" % p["says"])
        if p["result"]:
            lines.append("Report:\n%s" % p["result"])
        return Result("\n".join(lines))
    rows = agents.jobs(limit=12)
    if not rows:
        return Result("No agent jobs yet. Agents: %s." % _agent_names())
    lines = []
    for p in rows:
        plan = " (plan %d/%d)" % (p["steps_done"], p["steps_total"]) if p["steps_total"] else ""
        lines.append("job %s · %s · %s — %s: %s%s" % (p["id"], p["agent_name"], p["title"], p["status"], p["activity"], plan))
    return Result("\n".join(lines))


@tool(
    "message_agent",
    "Pass a message from the user to an agent's job: new wishes, corrections or answers. A running agent reads it before its "
    "next step (or at once, if it is waiting for an answer to its question); a finished job continues with it (e.g. 'add a contact page').",
    {"job_id": "string: Job id", "message": "string: What to tell the agent",
     "model?": "string: Only when the user wants this job to continue on another model (loose name is fine)"},
    group="agents",
    risk="write",
)
def message_agent(ctx, job_id, message, model=""):
    job = agents.job(job_id)
    if not job:
        raise ToolError("No job %s." % job_id)
    if not job.parent_conv:
        job.parent_conv = ctx.conv_id
    if str(model or "").strip():
        try:
            job.set_model(llm.find_model(model))
        except ValueError as exc:
            raise ToolError(str(exc))
    how = job.message(message)
    said = {"delivered": "got the message and takes it into account", "answered": "got the answer to its question and carries on"}
    return Result("%s %s." % (job.agent["name"], said.get(how, "continues the job with this message")),
                  display={"type": "agent_job", "id": job.id, "agent": job.agent["id"], "name": job.agent["name"],
                           "icon": job.agent.get("icon") or "agent", "title": job.title, "conv_id": job.conv_id})


@tool(
    "set_agent_model",
    "Change the model an agent works with. With job_id: that job switches now (a running job from its next step; a finished "
    "one when it continues). With agent: the agent uses the model for all future jobs (e.g. 'the coder should use deepseek "
    "flash 4.1 from now on'). The model can be named loosely, as the user said it; 'default' goes back to the coding / chat model.",
    {"model": "string: Model as the user named it, e.g. 'deepseek flash 4.1', or a model id, or 'default'",
     "job_id?": "string: A job id, to switch only that job",
     "agent?": "string: Agent id or name, to change its model for future jobs (default: coder)"},
    group="agents",
    risk="write",
)
def set_agent_model(ctx, model, job_id="", agent=""):
    reset = str(model or "").strip().lower() in ("", "default", "none", "auto", "reset")
    try:
        ref = "" if reset else llm.find_model(model)
    except ValueError as exc:
        raise ToolError(str(exc))
    label = llm._label(ref) if ref else "the default model"
    if job_id:
        job = agents.job(job_id)
        if not job:
            raise ToolError("No job %s." % job_id)
        live = job.set_model(ref)
        return Result("%s · %s %s %s." % (job.agent["name"], job.title, "now works with" if live else "will continue with", label),
                      display={"type": "agent_job", "id": job.id, "agent": job.agent["id"], "name": job.agent["name"],
                               "icon": job.agent.get("icon") or "agent", "title": job.title, "conv_id": job.conv_id})
    row = agents.get(agent or "coder")
    if not row:
        raise ToolError("No agent %r. Agents: %s." % (agent, _agent_names()))
    try:
        agents.save({"model": ref}, row["id"])
    except ValueError as exc:
        raise ToolError(str(exc))
    # its running jobs that have no model of their own follow along
    running = [j for j in agents.running_jobs() if j.agent["id"] == row["id"] and not j.model]
    for j in running:
        j.agent = dict(j.agent, model=ref)
        if ref:
            j.set_model(ref)
    return Result("%s uses %s from now on.%s" % (row["name"], label, (" %d running job(s) switched too." % len(running)) if running and ref else ""))


@tool(
    "stop_agent",
    "Stop an agent's running job (by job id), or every running job of one agent (by agent id or name).",
    {"target": "string: Job id, or agent id/name"},
    group="agents",
    risk="write",
)
def stop_agent(ctx, target):
    job = agents.job(target)
    stopped = []
    if job:
        if job.stop():
            stopped.append(job)
    else:
        row = agents.get(target)
        if not row:
            raise ToolError("No job or agent %r." % target)
        stopped = [j for j in agents.running_jobs() if j.agent["id"] == row["id"] and j.stop()]
    if not stopped:
        return Result("Nothing was running.")
    return Result("Stopped: %s." % ", ".join("%s · %s" % (j.agent["name"], j.title) for j in stopped))


@tool(
    "delete_agent_jobs",
    "Remove jobs from the Agents window's job list: one finished job (by job id) or every job that is not running (target "
    "'finished'). Running jobs are never removed; stop them first with stop_agent. The job's conversation and project files stay.",
    {"target": "string: Job id, or 'finished' for all finished, failed and stopped jobs"},
    group="agents",
    risk="write",
)
def delete_agent_jobs(ctx, target):
    try:
        removed, skipped = agents.delete_jobs(str(target).strip())
    except ValueError as exc:
        raise ToolError(str(exc))
    if not removed:
        raise ToolError("That job is still running; stop it with stop_agent first." if skipped else "Nothing to remove.")
    return Result("Removed %d job(s) from the list.%s" % (len(removed), (" %d running job(s) were kept." % len(skipped)) if skipped else ""))


@tool(
    "create_agent",
    "Create a new dedicated agent for the user. It can work on demand (assign_agent), or on a schedule: action 'remind' "
    "simply notifies the user with the task text at the scheduled time (e.g. 'every day at 13:00 remind me to drink water'); "
    "action 'run' does the task with tools each time and notifies the user with the result (e.g. 'every morning at 7 summarise "
    "the news'). It appears in the app's Agents window, where the user can pause, edit or stop it.",
    {
        "name": "string: Short name, e.g. 'Water reminder'",
        "description": "string: One sentence: what this agent does",
        "action?": "string[run,remind]: remind = only notify with the task text; run = do the task with tools (default)",
        "task?": "string: For scheduled agents: the reminder text, or what to do on each run",
        "instructions?": "string: Role and standing instructions for how the agent works",
        "schedule?": "string[none,daily,weekdays,weekly,interval,once]: When it acts by itself (none = only when assigned)",
        "time?": "string: HH:MM (24h) for daily/weekdays/weekly",
        "day?": "string[monday,tuesday,wednesday,thursday,friday,saturday,sunday]: For weekly",
        "minutes?": "integer: For interval (minimum 5)",
        "at?": "string: For once — when, e.g. 'tomorrow 13:00'",
        "tools?": "array<string>: Tool groups it may use (files, docs, code, web, data, media, computer, system, maps, agenda, records, knowledge); omit to choose automatically",
        "auto_approve?": "boolean: Let it run risky tools without asking each time (only if the user explicitly wants that)",
    },
    group="agents",
    risk="write",
)
def create_agent(ctx, name, description, action="run", task="", instructions="", schedule="none", time="", day="", minutes=60, at="",
                 tools=None, auto_approve=False):
    try:
        sched = agents.build_schedule(schedule, time, day, minutes, at)
        if action == "remind" and not task:
            task = description
        row = agents.save({"name": name, "description": description, "action": action, "task": task, "instructions": instructions,
                           "schedule": sched, "groups": list(tools or []), "auto_approve": bool(auto_approve),
                           "icon": "bell" if action == "remind" else "agent"})
    except ValueError as exc:
        raise ToolError(str(exc))
    when = ("%s, next %s" % (row["describe"], _fmt(row["next_run"]))) if row["schedule"] else "on demand (assign it jobs)"
    return Result("Agent '%s' (id %s) created: %s. It shows in the Agents window." % (row["name"], row["id"], when),
                  display={"type": "agent", "id": row["id"], "name": row["name"], "icon": row["icon"], "describe": row["describe"],
                           "action": row["action"], "next": row["next_run"]})


@tool(
    "manage_agent",
    "Change an existing agent: pause or resume its schedule, run it right now, delete it, or update its fields.",
    {
        "agent": "string: Agent id or name",
        "action": "string[pause,resume,run_now,delete,update]: What to do",
        "description?": "string: New description (update)",
        "task?": "string: New reminder text / task (update)",
        "instructions?": "string: New instructions (update)",
        "schedule?": "string[none,daily,weekdays,weekly,interval,once]: New schedule (update)",
        "time?": "string: HH:MM (update)",
        "day?": "string: Weekday (update)",
        "minutes?": "integer: Interval minutes (update)",
        "at?": "string: When, for once (update)",
    },
    group="agents",
    risk="write",
)
def manage_agent(ctx, agent, action, description=None, task=None, instructions=None, schedule=None, time="", day="", minutes=60, at=""):
    row = agents.get(agent)
    if not row:
        raise ToolError("No agent %r. Agents: %s." % (agent, _agent_names()))
    try:
        if action in ("pause", "resume"):
            agents.set_enabled(row["id"], action == "resume")
            return Result("%s %s." % (row["name"], "paused" if action == "pause" else "resumed"))
        if action == "delete":
            agents.delete(row["id"])
            return Result("Deleted the agent %s." % row["name"])
        if action == "run_now":
            job = agents.fire(row)
            if not job:
                return Result("%s just sent its reminder." % row["name"])
            return Result("%s is running now (job %s)." % (row["name"], job.id),
                          display={"type": "agent_job", "id": job.id, "agent": row["id"], "name": row["name"], "icon": row["icon"],
                                   "title": job.title, "conv_id": job.conv_id})
        patch = {k: v for k, v in (("description", description), ("task", task), ("instructions", instructions)) if v is not None}
        if schedule is not None:
            patch["schedule"] = agents.build_schedule(schedule, time, day, minutes, at)
        updated = agents.save(patch, row["id"])
    except ValueError as exc:
        raise ToolError(str(exc))
    return Result("Updated %s: %s." % (updated["name"], updated["describe"]))


def save_automation(data, aid=None):
    sched = data.get("schedule") or {"type": "daily", "time": "08:00"}
    nxt = scheduler.next_run(sched)
    if aid:
        db.x("UPDATE automations SET name=?, prompt=?, schedule=?, skill=?, notify=?, enabled=?, next_run=? WHERE id=?",
             (data["name"], data["prompt"], json.dumps(sched), data.get("skill") or "workflow", int(data.get("notify", 1)),
              int(data.get("enabled", 1)), nxt, aid))
    else:
        aid = db.x("INSERT INTO automations (name, prompt, schedule, skill, notify, key, next_run, created) VALUES (?,?,?,?,?,?,?,?)",
                   (data["name"], data["prompt"], json.dumps(sched), data.get("skill") or "workflow", int(data.get("notify", 1)),
                    secrets.token_urlsafe(18), nxt, time.time()))
    events.publish("automation", {"id": aid})
    return aid


def _fmt(ts):
    return time.strftime("%a %d %b %H:%M", time.localtime(ts)) if ts else "-"
