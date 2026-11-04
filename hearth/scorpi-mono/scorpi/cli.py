"""scorpi-code: Scorpi's coding agent in a terminal, working in the folder it is started in.

    scorpi-code                         talk to the coder in this folder
    scorpi-code "fix the failing test"  start with a task, then keep talking
    scorpi-code -p "update the README"  run one task without questions and print the answer (scripts, CI)

It uses the same models, keys and settings as the Scorpi app (no server needs to run), and the folder shows up in the
app's Code area with the terminal's conversation, its checkpoints and every change."""

import argparse
import os
import queue
import subprocess
import sys
import threading

from . import events

HELP = """Commands
  /init /review /test /fix /explain /security   project commands (anything after them goes along)
  /plan            your next message: see a plan and approve it before anything changes
  /ask /auto /full what the agent may do without asking (now: {autonomy})
  /model [name]    show or switch the model (loose names work: "glm 5", "groq llama")
  /undo            put the files back as they were before the last run
  /cost            tokens and cost of this session
  /clear           start a new conversation
  /exit            quit (Ctrl+D)
Also
  @path            include a file in your message, e.g. "explain @src/app.py"
  !command         run a shell command yourself; its output goes along with your next message
  Ctrl+C           stop the agent (at the prompt: quit)
  end a line with \\ to write several lines"""


class Colors:
    def __init__(self, on):
        self.on = on

    def __call__(self, code, text):
        return "\x1b[%sm%s\x1b[0m" % (code, text) if self.on else text

    def dim(self, t):
        return self("2", t)

    def bold(self, t):
        return self("1", t)

    def red(self, t):
        return self("31", t)

    def green(self, t):
        return self("32", t)

    def yellow(self, t):
        return self("33", t)

    def blue(self, t):
        return self("34", t)

    def cyan(self, t):
        return self("36", t)


def _color_supported(stream):
    if os.environ.get("NO_COLOR") or not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.name == "nt":
        os.system("")  # turns on ANSI escape handling in the Windows console
    return True


class UI:
    """Prints a run's events: streamed text, one line per tool step, diffs, notices. Approvals go to the main thread."""

    def __init__(self, headless=False, verbose=False):
        self.headless = headless
        self.verbose = verbose
        self.out = sys.stderr if headless else sys.stdout  # with -p only the answer goes to stdout
        self.c = Colors(_color_supported(self.out))
        self.approvals = queue.Queue()
        self.text_open = False
        self.thinking = False

    def write(self, text):
        self.out.write(text)
        self.out.flush()

    def line(self, text=""):
        if self.text_open:
            self.write("\n")
            self.text_open = False
        self.write(text + "\n")

    def on_event(self, ev):
        kind = ev.get("type")
        if kind == "token":
            if self.headless:
                return
            if not self.text_open:
                self.write("\n" if self.thinking else "")
                self.text_open = True
            self.thinking = False
            self.write(ev.get("content", ""))
        elif kind == "thinking":
            if self.verbose:
                self.write(self.c.dim(ev.get("content", "")))
            elif not self.thinking:
                self.thinking = True
                self.line(self.c.dim("✻ Thinking…"))
        elif kind == "tool_start":
            self.thinking = False
            from .agents import activity
            who = (ev.get("agent") + ": ") if ev.get("agent") else ""
            self.line(self.c.cyan("● ") + who + activity(ev.get("name", ""), ev.get("args") or {}))
        elif kind == "tool_end":
            self.tool_end(ev)
        elif kind in ("notice", "status"):
            self.line(self.c.yellow("  ! ") + self.c.dim(ev.get("text", "")))
        elif kind == "subagent" and ev.get("status") in ("running", "done", "failed"):
            self.line(self.c.dim("  ↳ %s: %s" % (ev.get("name", "helper"), ev.get("activity") or ev.get("status"))))
        elif kind == "approval":
            self.approvals.put(ev)
        elif kind == "error":
            self.line(self.c.red("✗ " + str(ev.get("error", ""))))

    def tool_end(self, ev):
        display = ev.get("display") or {}
        ok = ev.get("ok", True)
        first = next((ln for ln in str(ev.get("result") or "").splitlines() if ln.strip()), "")
        if display.get("type") == "diff":
            for f in display.get("files") or []:
                stat = (self.c.green("+%d" % f["add"]) if f.get("add") else "") + (" " + self.c.red("-%d" % f["del"]) if f.get("del") else "")
                self.line("  ⎿ %s %s" % (f.get("path"), stat))
                shown = 0
                for hunk in f.get("hunks") or []:
                    for kind, old, new, text in hunk.get("lines") or []:
                        if shown >= 24:
                            break
                        number = new if new is not None else old
                        prefix = "%5s " % (number if number is not None else "")
                        if kind == "+":
                            self.line(self.c.dim(prefix) + self.c.green("+ " + text))
                        elif kind == "-":
                            self.line(self.c.dim(prefix) + self.c.red("- " + text))
                        else:
                            self.line(self.c.dim(prefix + "  " + text))
                        shown += 1
                if shown >= 24:
                    self.line(self.c.dim("      …"))
            checks = display.get("checks") or ""
            if checks:
                self.line("    " + (self.c.red(checks) if checks.startswith("PROBLEMS") else self.c.dim(checks)))
            return
        if display.get("type") == "code":
            out = (display.get("stdout") or "").strip() or (display.get("stderr") or "").strip()
            tail = out.splitlines()[-6:]
            status = "exit %s" % display.get("exit")
            self.line("  ⎿ " + (self.c.green(status) if display.get("exit") == 0 else self.c.red(status)))
            for ln in tail:
                self.line(self.c.dim("    " + ln[:200]))
            return
        text = first[:180] + ("…" if len(first) > 180 else "")
        self.line("  ⎿ " + (self.c.dim(text) if ok else self.c.red(text)))

    # ---------- questions on the terminal ----------

    def ask(self, prompt):
        if self.text_open:
            self.write("\n")
            self.text_open = False
        try:
            return input(prompt)
        except EOFError:
            return ""

    def approval(self, ev):
        """(decision, note) for an approval card."""
        summary = ev.get("summary") or {}
        if ev.get("tool") == "submit_plan":
            self.line("")
            self.line(self.c.bold(self.c.blue("Plan: " + (summary.get("title") or "review it"))))
            for ln in str(summary.get("plan") or "").splitlines():
                self.line("  " + ln)
            self.line("")
            while True:
                answer = self.ask(self.c.bold("Approve and build it? ") + "[y]es / [c]hange / [n]o: ").strip().lower()
                if answer in ("y", "yes", ""):
                    return "allow", ""
                if answer in ("c", "change"):
                    note = self.ask("What should change? ").strip()
                    if note:
                        return "deny", note
                if answer in ("n", "no"):
                    return "deny", ""
        args = ev.get("args") or {}
        what = args.get("command") or args.get("code") or args.get("path") or ""
        self.line(self.c.yellow("? ") + self.c.bold(summary.get("title") or ev.get("tool", "")) + (" — " + summary["why"] if summary.get("why") else ""))
        if what:
            for ln in str(what).splitlines()[:12]:
                self.line(self.c.dim("    " + ln))
        while True:
            answer = self.ask("  Allow? [y]es / [a]lways for %s / [n]o: " % ev.get("tool", "this")).strip().lower()
            if answer in ("y", "yes"):
                return "allow", ""
            if answer in ("a", "always"):
                return "always", ""
            if answer in ("n", "no", ""):
                return "deny", ""


class Session:
    def __init__(self, folder, model="", autonomy="", headless=False, verbose=False, resume=False):
        from . import agents, db
        from .config import SETTINGS
        self.folder = os.path.realpath(folder)
        self.ui = UI(headless=headless, verbose=verbose)
        self.headless = headless
        self.autonomy = autonomy or SETTINGS["autonomy"] or "auto"
        self.plan_next = False
        self.pending = []  # output of !commands, sent along with the next message
        projects = os.path.realpath(agents.PROJECTS)
        if os.path.dirname(self.folder) == projects:
            self.project = os.path.basename(self.folder)
        else:
            self.project = agents.link_folder(self.folder)  # the folder appears in the app's Code area too
        db.set_user(db.default_user())
        self.model = model or self._default_model()
        self.conv_id = ""
        if resume:
            row = db.one("SELECT id FROM conversations WHERE origin='cli' AND title=? ORDER BY updated DESC LIMIT 1", (self._title(),))
            self.conv_id = row["id"] if row else ""

    def _title(self):
        return "Terminal · %s" % (os.path.basename(self.folder.rstrip("\\/")) or self.folder)

    @staticmethod
    def _default_model():
        from . import llm
        return llm.coding_model() or llm.default_model()

    def conversation(self):
        from . import db
        if not self.conv_id or not db.get_conversation(self.conv_id):
            self.conv_id = db.create_conversation(title=self._title(), skill="code", origin="cli")["id"]
        return self.conv_id

    def persona(self):
        from .agents import CODER_ROLE, project_rules
        instructions = CODER_ROLE
        house = project_rules(self.folder)
        if house:
            instructions += "\n\nThis project's own instructions (%s), follow them:\n%s" % house
        return {"name": "Coder", "instructions": instructions, "workdir": self.folder, "project": self.project, "interactive": True}

    # ---------- one turn ----------

    def turn(self, text, display=""):
        """Run the agent on one message; follow up with the build when a plan gets approved. Returns (answer, ok)."""
        from . import db, gitops
        from .agent import Agent, history_for_model
        from .config import SETTINGS
        conv = self.conversation()
        content = self._expand(text)
        db.add_message(conv, "user", display or text, {"attachments": [], "skill": "code", "model_content": content})
        run = events.register_run(TermRun(conv, self.ui))
        if SETTINGS.get("checkpoints", True):
            try:
                gitops.snapshot(self.project, self.folder, "Before: terminal — %s" % (display or text).splitlines()[0][:120])
            except Exception:  # noqa: BLE001 - no checkpoint (git missing, huge folder) never blocks the work
                pass
        agent = Agent(run, model=self.model, skill="code", groups=["code", "files", "web", "docs"], persona=self.persona(),
                      max_steps=80, plan_mode=self.plan_next, autonomy=self.autonomy,
                      workdir=self.folder, project=self.project)
        self.plan_next = False
        result = {"text": "", "error": ""}

        def work():
            try:
                result["text"] = agent.run_turn(history_for_model(conv), content)
            except Exception as exc:  # noqa: BLE001 - shown to the user, the session goes on
                result["error"] = str(exc)
            finally:
                run.finish()

        worker = threading.Thread(target=work, daemon=True, name="cli-turn")
        worker.start()
        self._wait(worker, run)
        answer = result["text"]
        self.ui.line("")
        if result["error"]:
            self.ui.line(self.ui.c.red("✗ " + result["error"]))
        data = {"parts": agent.parts, "model": agent.model, "skill": "code", "files": agent.files, "error": result["error"], "stopped": run.cancelled}
        db.add_message(conv, "assistant", answer, data)
        db.touch_conversation(conv)
        if agent.approved_plan and not run.cancelled:
            return self.turn("Build the plan the user approved, step by step, and keep update_plan current:\n\n" + agent.approved_plan,
                             display="(building the approved plan)")
        return answer, not result["error"] and not run.cancelled

    def _wait(self, worker, run):
        while worker.is_alive():
            try:
                try:
                    ev = self.ui.approvals.get(timeout=0.15)
                except queue.Empty:
                    continue
                if self.headless:
                    decision, note = "deny", ""
                    self.ui.line(self.ui.c.yellow("! Declined %s (no questions with -p; use --full to allow more)" % ev.get("tool")))
                else:
                    decision, note = self.ui.approval(ev)
                events.resolve_approval(ev["id"], decision, note)
            except KeyboardInterrupt:
                if run.cancelled:
                    raise
                self.ui.line(self.ui.c.yellow("Stopping… (Ctrl+C again to quit)"))
                run.cancel()
        worker.join()

    def _expand(self, text):
        """@path mentions become the file's content; output of !commands goes along."""
        from .coding import expand_mentions
        parts = [expand_mentions(text, self.folder)]
        if self.pending:
            parts.append("Output of commands the user ran:\n" + "\n\n".join(self.pending))
            self.pending = []
        return "\n\n".join(parts)

    # ---------- commands ----------

    def shell(self, command):
        """!command: run it in the folder, show it, and keep it for the next message."""
        shell = ["powershell", "-NoProfile", "-Command", command] if os.name == "nt" else ["bash", "-lc", command]
        try:
            proc = subprocess.run(shell, cwd=self.folder, capture_output=True, timeout=600)
            out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            code = proc.returncode
        except subprocess.TimeoutExpired:
            out, code = "(stopped after 10 minutes)", "timeout"
        self.ui.write(out if out.endswith("\n") or not out else out + "\n")
        self.ui.line(self.ui.c.dim("exit %s" % code))
        self.pending.append("$ %s\n%s(exit %s)" % (command, out[-8000:], code))

    def undo(self):
        from . import gitops
        base = next((c for c in gitops.checkpoints(self.project, self.folder) if c["subject"].startswith("Before")), None)
        if not base:
            return self.ui.line("Nothing to undo yet.")
        files = gitops.checkpoint_diff(self.project, self.folder, base["sha"], "now")
        if not files:
            return self.ui.line("The last run did not change any files.")
        answer = self.ui.ask("Put %d file(s) back as they were before the last run? [y/N] " % len(files)).strip().lower()
        if answer in ("y", "yes"):
            gitops.restore(self.project, self.folder, base["sha"])
            self.ui.line(self.ui.c.green("Undone.") + " The files before the undo are saved as a checkpoint.")

    def cost(self):
        from . import db
        row = db.one("SELECT COUNT(*) AS calls, SUM(input) AS inp, SUM(output) AS outp, SUM(cached) AS cached, SUM(cost) AS cost "
                     "FROM usage WHERE conv_id=?", (self.conv_id,)) if self.conv_id else None
        if not row or not row["calls"]:
            return self.ui.line("No model calls yet.")
        self.ui.line("%d calls · %s in · %s out%s · $%.4f" % (row["calls"], _k(row["inp"]), _k(row["outp"]),
                                                            (" · %s cached" % _k(row["cached"])) if row["cached"] else "", row["cost"] or 0))

    def set_model(self, name):
        from . import llm
        if not name:
            return self.ui.line("Model: %s" % llm.label(self.model))
        try:
            self.model = llm.find_model(name)
            self.ui.line("Model: %s" % llm.label(self.model))
        except ValueError as exc:
            self.ui.line(self.ui.c.red(str(exc)))

    def command(self, line):
        """A /command typed at the prompt. Returns False to quit."""
        from . import coding
        name, _, rest = line[1:].partition(" ")
        name = name.lower()
        if name in ("exit", "quit", "q"):
            return False
        if name == "help":
            self.ui.line(HELP.format(autonomy=self.autonomy))
        elif name == "plan":
            self.plan_next = True
            if rest.strip():
                self.turn(rest.strip(), display="/plan " + rest.strip())
            else:
                self.ui.line("Plan first: describe what to build; you will see a plan to approve before anything changes.")
        elif name in ("ask", "auto", "full"):
            self.autonomy = name
            self.ui.line("Autonomy: %s" % {"ask": "asks before every command", "auto": "builds, tests and edits the project itself",
                                           "full": "everything except destructive commands"}[name])
        elif name == "model":
            self.set_model(rest.strip())
        elif name == "undo":
            self.undo()
        elif name == "cost":
            self.cost()
        elif name == "clear":
            self.conv_id = ""
            self.ui.line("New conversation.")
        elif name in coding.COMMANDS:
            self.turn(coding.command_prompt(name, rest), display=line)
        else:
            self.ui.line("Unknown command /%s. Type /help." % name)
        return True

    def banner(self):
        from . import VERSION, llm
        c = self.ui.c
        self.ui.line(c.bold("Scorpi Code") + c.dim(" %s · %s" % (VERSION, self.folder)))
        self.ui.line(c.dim("model %s · autonomy %s · /help for commands" % (llm.label(self.model) or "none", self.autonomy)))
        if not self.model:
            self.ui.line(c.red("No model yet: add a free key in the Scorpi app (Connections → Model) or start Ollama."))

    def repl(self):
        try:
            import readline  # noqa: F401 - arrow keys and history on Linux/macOS
            from .config import DATA_DIR
            hist = os.path.join(DATA_DIR, "cli_history")
            try:
                readline.read_history_file(hist)
            except OSError:
                pass
            import atexit
            atexit.register(lambda: _save_history(readline, hist))
        except ImportError:
            pass
        while True:
            try:
                line = self._read()
            except (EOFError, KeyboardInterrupt):
                self.ui.line("")
                return
            if not line.strip():
                continue
            if line.startswith("!"):
                self.shell(line[1:].strip())
                continue
            try:
                if line.startswith("/"):
                    if not self.command(line.strip()):
                        return
                    continue
                self.turn(line)
            except KeyboardInterrupt:
                self.ui.line("")
                return

    def _read(self):
        prompt = self.ui.c.bold("plan › " if self.plan_next else "› ")
        lines = [input("\n" + prompt)]
        while lines[-1].endswith("\\"):
            lines[-1] = lines[-1][:-1]
            lines.append(input(self.ui.c.dim("… ")))
        return "\n".join(lines)


class TermRun(events.Run):
    """A run whose events are printed as they happen (approvals go to the main thread, which asks on the terminal)."""

    def __init__(self, conv_id, ui):
        super().__init__(conv_id, title="Terminal", origin="cli")
        self.ui = ui

    def emit(self, event):
        super().emit(event)
        try:
            self.ui.on_event(event)
        except Exception:  # noqa: BLE001 - printing never breaks the agent
            pass


def _k(n):
    n = int(n or 0)
    return "%.1fk" % (n / 1000) if n >= 1000 else str(n)


def _save_history(readline, path):
    try:
        readline.set_history_length(2000)
        readline.write_history_file(path)
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scorpi-code", description="Scorpi's coding agent in this folder (same models and keys as the app).")
    parser.add_argument("prompt", nargs="*", help="a task to start with")
    parser.add_argument("-p", "--print", action="store_true", help="run the task without questions, print the answer and exit")
    parser.add_argument("-m", "--model", default="", help="model to use (loose names work, e.g. 'glm 5' or 'groq llama')")
    parser.add_argument("--plan", action="store_true", help="show a plan to approve before changing anything")
    parser.add_argument("--ask", dest="autonomy", action="store_const", const="ask", help="ask before every command")
    parser.add_argument("--auto", dest="autonomy", action="store_const", const="auto", help="build, test and edit the project without asking")
    parser.add_argument("--full", dest="autonomy", action="store_const", const="full", help="everything except destructive commands")
    parser.add_argument("-c", "--continue", dest="resume", action="store_true", help="continue the last terminal conversation in this folder")
    parser.add_argument("-C", "--cwd", default="", help="work in this folder instead of the current one")
    parser.add_argument("-v", "--verbose", action="store_true", help="show the model's thinking")
    args = parser.parse_args(argv)

    from . import llm
    from .tools import load_all
    load_all()
    folder = os.path.abspath(args.cwd or os.getcwd())
    if not os.path.isdir(folder):
        parser.error("no folder %s" % folder)
    model = ""
    if args.model:
        try:
            model = llm.find_model(args.model)
        except ValueError as exc:
            parser.error(str(exc))
    session = Session(folder, model=model, autonomy=args.autonomy or "", headless=args.print, verbose=args.verbose, resume=args.resume)
    session.plan_next = args.plan
    task = " ".join(args.prompt).strip()
    if args.print and not task and not sys.stdin.isatty():
        task = sys.stdin.read().strip()  # echo "fix the bug" | scorpi-code -p
    if args.print:
        if not task:
            parser.error("-p needs a task")
        if not session.model:
            print("No model available. Add a free key in the Scorpi app (Connections → Model) or start Ollama.", file=sys.stderr)
            return 2
        answer, ok = session.turn(task)
        print(answer)
        return 0 if ok else 1
    session.banner()
    if task:
        if task.startswith("/"):
            if not session.command(task):
                return 0
        else:
            session.turn(task)
    session.repl()
    return 0


if __name__ == "__main__":
    sys.exit(main())
