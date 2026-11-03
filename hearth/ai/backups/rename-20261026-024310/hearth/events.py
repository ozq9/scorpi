"""In-process event bus, background runs and approvals."""

import queue
import threading
import time
import uuid

# ---------- bus (pushed to every connected client over SSE) ----------

_subscribers = set()
_sub_lock = threading.Lock()


def subscribe():
    q = queue.Queue(maxsize=500)
    with _sub_lock:
        _subscribers.add(q)
    return q


def unsubscribe(q):
    with _sub_lock:
        _subscribers.discard(q)


def client_count():
    """How many app windows / devices are connected right now."""
    with _sub_lock:
        return len(_subscribers)


def publish(kind, data=None):
    event = {"type": kind, "data": data or {}, "time": time.time()}
    with _sub_lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


# ---------- runs ----------

class Run:
    """A single agent execution whose events can be replayed and followed by any client."""

    def __init__(self, conv_id, title="", origin="chat"):
        self.id = uuid.uuid4().hex[:12]
        self.conv_id = conv_id
        self.title = title
        self.origin = origin
        self.events = []
        self.cond = threading.Condition()
        self.done = False
        self.cancelled = False
        self.started = time.time()
        self.result = ""
        self.inbox = []  # messages for the agent while it works; read between steps

    def emit(self, event):
        with self.cond:
            event.setdefault("seq", len(self.events))
            self.events.append(event)
            self.cond.notify_all()

    def finish(self):
        with self.cond:
            self.done = True
            self.cond.notify_all()
        publish("run_done", {"run_id": self.id, "conv_id": self.conv_id, "origin": self.origin})

    def cancel(self):
        self.cancelled = True
        for approval in list(APPROVALS.values()):
            if approval["run_id"] == self.id:
                resolve_approval(approval["id"], "deny")
        with self.cond:
            self.cond.notify_all()

    def follow(self, start=0, keepalive=15.0):
        index = start
        while True:
            with self.cond:
                while index >= len(self.events) and not self.done:
                    if not self.cond.wait(timeout=keepalive):
                        break
                batch = self.events[index:]
                finished = self.done
            if batch:
                for event in batch:
                    yield event
                index += len(batch)
            elif finished:
                return
            else:
                yield {"type": "ping"}

    def summary(self):
        return {
            "id": self.id,
            "conv_id": self.conv_id,
            "title": self.title,
            "origin": self.origin,
            "done": self.done,
            "started": self.started,
            "events": len(self.events),
        }


RUNS = {}
_runs_lock = threading.Lock()


def register_run(run):
    with _runs_lock:
        RUNS[run.id] = run
        # forget finished runs older than an hour
        cutoff = time.time() - 3600
        for rid in [r.id for r in RUNS.values() if r.done and r.started < cutoff]:
            RUNS.pop(rid, None)
    return run


def active_run_for(conv_id):
    with _runs_lock:
        for run in RUNS.values():
            if run.conv_id == conv_id and not run.done:
                return run
    return None


# ---------- approvals ----------

APPROVALS = {}


def request_approval(run, tool, args, summary, timeout=600):
    approval = {
        "id": uuid.uuid4().hex[:12],
        "run_id": run.id if run else "",
        "conv_id": run.conv_id if run else "",
        "tool": tool,
        "args": args,
        "summary": summary,
        "created": time.time(),
        "event": threading.Event(),
        "decision": None,
    }
    APPROVALS[approval["id"]] = approval
    public = {k: v for k, v in approval.items() if k not in ("event",)}
    if run:
        run.emit({"type": "approval", **public})
    publish("approval", public)
    deadline = time.time() + timeout
    while not approval["event"].wait(timeout=1.0):
        if (run and run.cancelled) or time.time() > deadline:
            approval["decision"] = approval["decision"] or "deny"
            break
    APPROVALS.pop(approval["id"], None)
    decision = approval["decision"] or "deny"
    if run:
        run.emit({"type": "approval_done", "id": approval["id"], "decision": decision})
    publish("approval_done", {"id": approval["id"], "decision": decision})
    return decision


def resolve_approval(approval_id, decision):
    approval = APPROVALS.get(approval_id)
    if not approval:
        return False
    approval["decision"] = decision
    approval["event"].set()
    return True


def pending_approvals():
    return [{k: v for k, v in a.items() if k != "event"} for a in APPROVALS.values()]
