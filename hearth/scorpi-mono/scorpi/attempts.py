"""Parallel attempts ("best of N"): the same task done by two or three models at once, each in its own copy of a project,
then compared side by side; the one the user picks is applied to the real project (after a checkpoint, so it can be
undone). Copies leave out version control, dependencies and build output (node_modules, .venv, dist...): an attempt
installs what it needs, as in a fresh clone, so it never changes the project's own environment."""

import difflib
import json
import os
import secrets
import shutil
import threading
import time

from . import agents, db, events, gitops, llm, scores
from . import providers as P
from .config import DATA_DIR

ROOT = os.path.join(DATA_DIR, "attempts")
STORE = os.path.join(ROOT, "attempts.json")
LEAVE_OUT = agents.SKIP_DIRS | {".git", "env", "target", "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".turbo", ".parcel-cache"}
MAX_FILES = 6000
MAX_BYTES = 300 * 1024 * 1024
MAX_ATTEMPTS = 4
_lock = threading.Lock()


class AttemptError(Exception):
    pass


# ---------- the record ----------

def _load():
    try:
        with open(STORE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(records):
    os.makedirs(ROOT, exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(records[-60:], fh, indent=1)
    os.replace(tmp, STORE)


def _update(rid, fn):
    with _lock:
        records = _load()
        for r in records:
            if r["id"] == rid:
                fn(r)
                break
        _save(records)


def get(rid):
    return next((r for r in _load() if r["id"] == rid), None)


def attempt_of_job(job_id):
    """(project slug, copy folder) of an attempt's job, e.g. after a restart."""
    for r in _load():
        for a in r["attempts"]:
            if a["job_id"] == job_id:
                return r["project"], a["dir"]
    return "", ""


# ---------- models ----------

def suggest_models(count=3):
    """Strong coding models from different providers (different models make the comparison worth it)."""
    picks = []
    coding = llm.coding_model()
    if coding:
        picks.append(coding)
    for pid in llm.cloud_order():
        if len(picks) >= count:
            break
        if any(llm.is_cloud(m) and llm.split_cloud(m)[0] == pid for m in picks):
            continue
        models, _ = llm.cloud_models(pid)
        prov = P.BY_ID[pid]
        pick = llm._prefer([m for m in models if "tools" in m["caps"]], prov.get("prefer_code") or prov["prefer"])
        if pick:
            picks.append(llm.cloud_ref(pid, pick["id"]))
    if len(picks) < 2:
        default = llm.default_model()
        if default and default not in picks:
            picks.append(default)
    return list(dict.fromkeys(picks))[:count]


# ---------- copies ----------

def _files(root):
    """Relative paths of the files that count (no version control, dependencies or build output)."""
    out = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in LEAVE_OUT)
        rel = os.path.relpath(base, root)
        for n in names:
            out.append(n if rel == "." else os.path.join(rel, n))
    return out


def _copy(src, dst):
    files = _files(src)
    if len(files) > MAX_FILES:
        raise AttemptError("This project has %d files; parallel attempts work on projects up to %d (without dependencies)." % (len(files), MAX_FILES))
    size = 0
    for rel in files:
        try:
            size += os.path.getsize(os.path.join(src, rel))
        except OSError:
            pass
    if size > MAX_BYTES:
        raise AttemptError("This project is %d MB without dependencies; parallel attempts copy it once per model, up to %d MB."
                           % (size // 1048576, MAX_BYTES // 1048576))
    for rel in files:
        target = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            shutil.copy2(os.path.join(src, rel), target)
        except OSError:
            pass  # a locked or vanished file: the attempt works without it
    os.makedirs(dst, exist_ok=True)


def _read(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read(2 * 1024 * 1024 + 1)
    except OSError:
        return None
    if b"\x00" in raw[:4096] or len(raw) > 2 * 1024 * 1024:
        return b"binary:" + raw[:64]  # compared, not shown
    return raw


def changes(original, copy):
    """What an attempt changed compared with the project, as parse_diff() files (+ the raw list for applying)."""
    a, b = set(_files(original)), set(_files(copy))
    chunks, changed = [], []
    for rel in sorted(a | b):
        old = _read(os.path.join(original, rel)) if rel in a else None
        new = _read(os.path.join(copy, rel)) if rel in b else None
        if old == new:
            continue
        changed.append((rel, "A" if old is None else "D" if new is None else "M"))
        name = rel.replace(os.sep, "/")
        if (old or b"").startswith(b"binary:") or (new or b"").startswith(b"binary:"):
            chunks.append("diff --git a/%s b/%s\nBinary files differ" % (name, name))
            continue
        before = (old or b"").decode("utf-8", "replace").splitlines()
        after = (new or b"").decode("utf-8", "replace").splitlines()
        head = "diff --git a/%s b/%s\n%s" % (name, name, "new file mode 100644\n" if old is None else "deleted file mode 100644\n" if new is None else "")
        chunks.append(head + "\n".join(difflib.unified_diff(before, after, "a/" + name, "b/" + name, n=2, lineterm="")))
    files = gitops.parse_diff("\n".join(chunks), max_lines=800)
    return files, changed


# ---------- start, follow, apply, discard ----------

PROMPT = """Job: %s

%s

(You are one of %d agents doing this same job in parallel, each with another model and its own copy of the project in
your folder. The user will compare the results and keep the best one, so make it complete, working and tested. Work only
inside your folder. Dependencies are not copied: install what you need to run or test it.)"""


def start(slug, task, models=None, title=""):
    task = str(task or "").strip()
    if not task:
        raise AttemptError("Describe the task.")
    path = agents.project_path(slug)
    if not os.path.isdir(path):
        raise AttemptError("No project %s." % slug)
    models = [m for m in (models or []) if m][:MAX_ATTEMPTS] or suggest_models()
    models = list(dict.fromkeys(models))
    if len(models) < 2:
        raise AttemptError("Parallel attempts need at least two models. Add another free provider in Connections → Model.")
    coder = agents.get("coder")
    rid = secrets.token_hex(4)
    title = str(title or "").strip() or agents._first_line(task)[:50]
    record = {"id": rid, "project": slug, "task": task, "title": title, "created": time.time(), "applied": 0, "attempts": []}
    for n, model in enumerate(models, 1):
        folder = os.path.join(ROOT, slug, rid, str(n))
        _copy(path, folder)
        conv = db.create_conversation(title="%s · try %d" % (title, n), skill="code", origin="agent")
        job = agents.Job(coder, "%s · try %d" % (title, n), task, conv["id"], origin="attempt", workdir=folder, model=model)
        job.attempt_of = slug
        job.start(PROMPT % (title, task, len(models)))
        record["attempts"].append({"n": n, "model": model, "job_id": job.id, "dir": folder, "conv_id": conv["id"]})
    with _lock:
        records = _load()
        records.append(record)
        _save(records)
    events.publish("attempts", {"project": slug, "id": rid})
    return public(record)


def public(record, with_diff=False):
    out = dict(record)
    path = agents.project_path(record["project"])
    attempts = []
    for a in record["attempts"]:
        job = agents.job(a["job_id"])
        item = {"n": a["n"], "model": a["model"], "model_label": llm.label(a["model"]), "job_id": a["job_id"], "conv_id": a.get("conv_id", ""),
                "status": job.status if job else "gone", "activity": job.activity if job else "", "result": (job.result if job else "")[:600],
                "steps_done": sum(1 for s in job.plan if s["status"] == "done") if job else 0, "steps_total": len(job.plan) if job else 0,
                "exists": os.path.isdir(a["dir"]), "proof": (job.proof or {}).get("verdict", "") if job else "",
                "proof_items": [i["text"] for i in (job.proof or {}).get("items", [])][:4] if job else []}
        if with_diff and item["exists"] and item["status"] != "running":
            files, _ = changes(path, a["dir"])
            item.update(files=files, add=sum(f["add"] for f in files), dele=sum(f["del"] for f in files), count=len(files))
        attempts.append(item)
    out["attempts"] = attempts
    out["running"] = sum(1 for a in attempts if a["status"] == "running")
    return out


def for_project(slug, with_diff=True):
    """The project's attempts, newest first (the latest with its diffs)."""
    records = [r for r in _load() if r["project"] == slug]
    records.sort(key=lambda r: -r["created"])
    return [public(r, with_diff=with_diff and i == 0) for i, r in enumerate(records[:6])]


def apply(rid, n):
    """Put attempt n's changes into the real project. A checkpoint is taken first, so it can be undone."""
    record = get(rid)
    if not record:
        raise AttemptError("Unknown attempt.")
    attempt = next((a for a in record["attempts"] if a["n"] == int(n)), None)
    if not attempt or not os.path.isdir(attempt["dir"]):
        raise AttemptError("That attempt is gone.")
    job = agents.job(attempt["job_id"])
    if job and job.status == "running":
        raise AttemptError("That attempt is still working. Wait for it or stop it first.")
    slug = record["project"]
    path = agents.project_path(slug)
    live = agents.latest_job(slug)
    if live and live.status == "running":
        raise AttemptError("The project's own agent is working. Stop it first.")
    gitops.snapshot(slug, path, "Before: applying try %d (%s)" % (attempt["n"], llm.label(attempt["model"])))
    _, changed = changes(path, attempt["dir"])
    for rel, kind in changed:
        target = os.path.join(path, rel)
        if kind == "D":
            try:
                os.remove(target)
            except OSError:
                pass
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(attempt["dir"], rel), target)
    gitops.snapshot(slug, path, "After: applied try %d (%s)" % (attempt["n"], llm.label(attempt["model"])))
    _update(rid, lambda r: r.update(applied=attempt["n"]))
    if not record.get("applied"):
        for a in record["attempts"]:
            scores.record(a["model"], "attempt_won" if a["n"] == attempt["n"] else "attempt_lost")
    events.publish("attempts", {"project": slug, "id": rid})
    events.publish("checkpoint", {"project": slug})
    return {"applied": attempt["n"], "files": len(changed)}


def discard(rid):
    """Stop the attempts that still work and delete their copies."""
    record = get(rid)
    if not record:
        raise AttemptError("Unknown attempt.")
    for a in record["attempts"]:
        job = agents.job(a["job_id"])
        if job and job.status == "running":
            job.stop()
    folder = os.path.join(ROOT, record["project"], rid)
    real = os.path.realpath(folder)
    if real.startswith(os.path.realpath(ROOT) + os.sep):  # only ever inside data/attempts
        shutil.rmtree(real, ignore_errors=True)
    with _lock:
        _save([r for r in _load() if r["id"] != rid])
    events.publish("attempts", {"project": record["project"], "id": rid, "deleted": True})
