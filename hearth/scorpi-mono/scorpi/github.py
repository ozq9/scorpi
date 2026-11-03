"""GitHub: the account Scorpi works with (a token pasted in Connections, or taken over from the GitHub CLI's login),
the REST calls the Code area needs (repositories, pull requests, issues, checks) and the login for git clone/push."""

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from . import VERSION
from .config import IS_WINDOWS, SETTINGS

API = "https://api.github.com"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TOKEN_URL = "https://github.com/settings/tokens/new?scopes=repo,workflow,read:org,read:user&description=Scorpi"
_me = {"token": "", "at": 0.0, "user": None}


class GitHubError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def token():
    return str(SETTINGS.get("github_token") or "").strip()


def cli():
    return shutil.which("gh")


def cli_token():
    """The token the GitHub CLI (gh) is signed in with, or ''."""
    exe = cli()
    if not exe:
        return ""
    try:
        proc = subprocess.run([exe, "auth", "token"], capture_output=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else ""


def cli_login():
    """Open the GitHub CLI's browser sign-in in a console window on this PC."""
    exe = cli()
    if not exe:
        raise GitHubError("The GitHub CLI is not installed. Get it from https://cli.github.com or paste a token instead.")
    args = [exe, "auth", "login", "--web", "--git-protocol", "https", "--scopes", "repo,workflow,read:org"]
    if IS_WINDOWS:
        subprocess.Popen(["cmd", "/c", "start", "GitHub sign-in", "cmd", "/k"] + args, creationflags=NO_WINDOW)
    else:
        subprocess.Popen(args)


def request(method, path, body=None, tok=None, timeout=30, raw=False):
    tok = tok or token()
    if not tok:
        raise GitHubError("GitHub is not connected. Connect it in Connections → GitHub.", 401)
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Scorpi/" + VERSION, **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text, headers = resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}")
        except ValueError:
            detail = {}
        msg = detail.get("message") or exc.reason
        errs = "; ".join(e.get("message") or e.get("code") or "" for e in detail.get("errors") or [] if isinstance(e, dict))
        if exc.code == 401:
            msg = "GitHub refused the token (it may have expired). Connect GitHub again."
        elif exc.code == 403 and "rate limit" in str(msg).lower():
            msg = "GitHub's rate limit is reached. Try again in a few minutes."
        elif exc.code == 404:
            msg = "Not found on GitHub (or the token can't see it: it needs the repo scope for private repositories)."
        raise GitHubError("%s%s" % (msg, (": " + errs) if errs else ""), exc.code)
    except urllib.error.URLError as exc:
        raise GitHubError("Could not reach GitHub: %s" % exc.reason, 502)
    payload = json.loads(text) if text else {}
    return (payload, headers) if raw else payload


def me(refresh=False):
    """The signed-in account ({login, name, avatar, url, id, scopes}) or None."""
    tok = token()
    if not tok:
        return None
    if not refresh and _me["token"] == tok and time.time() - _me["at"] < 600 and _me["user"]:
        return _me["user"]
    user, headers = request("GET", "/user", raw=True)
    _me.update(token=tok, at=time.time(), user={
        "login": user.get("login"), "name": user.get("name") or user.get("login"), "avatar": user.get("avatar_url"),
        "url": user.get("html_url"), "id": user.get("id"), "scopes": headers.get("X-OAuth-Scopes") or ""})
    return _me["user"]


def status():
    out = {"connected": bool(token()), "cli": bool(cli()), "token_url": TOKEN_URL, "user": None, "error": ""}
    if out["connected"]:
        try:
            out["user"] = me()
        except GitHubError as exc:
            out["error"] = str(exc)
    return out


def connect(tok):
    tok = str(tok or "").strip()
    if not tok:
        raise GitHubError("Paste a token, or sign in with the GitHub CLI first.")
    request("GET", "/user", tok=tok)  # raises when GitHub refuses it
    SETTINGS.update({"github_token": tok})
    return me(refresh=True)


def disconnect():
    SETTINGS.update({"github_token": ""})
    _me.update(token="", at=0, user=None)


def author():
    """(name, email) for commits made from Scorpi when git has no identity: the account's no-reply address."""
    try:
        user = me()
    except GitHubError:
        user = None
    if not user:
        return None
    return user["name"] or user["login"], "%s+%s@users.noreply.github.com" % (user["id"], user["login"])


def parse_remote(url):
    """'owner/name' of a github.com remote, or ''."""
    m = re.match(r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", str(url or "").strip())
    return "%s/%s" % (m.group(1), m.group(2)) if m else ""


def _repo(r):
    return {"full_name": r["full_name"], "name": r["name"], "owner": r["owner"]["login"], "private": r.get("private"),
            "description": r.get("description") or "", "language": r.get("language") or "", "updated": r.get("pushed_at") or r.get("updated_at"),
            "default_branch": r.get("default_branch") or "main", "stars": r.get("stargazers_count") or 0, "url": r.get("html_url"),
            "clone_url": r.get("clone_url"), "fork": r.get("fork"), "archived": r.get("archived")}


def repos(query=""):
    rows = request("GET", "/user/repos?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member")
    out = [_repo(r) for r in rows]
    q = str(query or "").strip().lower()
    if q:
        out = [r for r in out if q in r["full_name"].lower() or q in r["description"].lower()]
        if not out and re.match(r"^[\w.-]+/[\w.-]+$", q):
            try:
                out = [_repo(request("GET", "/repos/" + q))]  # any public repository by owner/name
            except GitHubError:
                pass
    return out


def repo(full):
    return _repo(request("GET", "/repos/" + full))


def _user(u):
    return {"login": (u or {}).get("login") or "", "avatar": (u or {}).get("avatar_url") or ""}


def pulls(full, state="open"):
    rows = request("GET", "/repos/%s/pulls?state=%s&per_page=30&sort=updated&direction=desc" % (full, state))
    return [{"number": p["number"], "title": p["title"], "user": _user(p.get("user")), "draft": p.get("draft"), "state": p["state"],
             "head": p["head"]["ref"], "head_repo": ((p["head"].get("repo") or {}).get("full_name") or "").lower(),
             "base": p["base"]["ref"], "url": p["html_url"], "updated": p.get("updated_at"),
             "merged": bool(p.get("merged_at"))} for p in rows]


def issues(full, state="open"):
    rows = request("GET", "/repos/%s/issues?state=%s&per_page=40&sort=updated" % (full, state))
    return [{"number": i["number"], "title": i["title"], "user": _user(i.get("user")), "url": i["html_url"], "comments": i.get("comments") or 0,
             "labels": [lb.get("name") for lb in i.get("labels") or [] if isinstance(lb, dict)], "updated": i.get("updated_at"),
             "state": i["state"]} for i in rows if "pull_request" not in i]


def issue(full, number):
    i = request("GET", "/repos/%s/issues/%d" % (full, int(number)))
    comments = request("GET", "/repos/%s/issues/%d/comments?per_page=30" % (full, int(number))) if i.get("comments") else []
    return {"number": i["number"], "title": i["title"], "body": i.get("body") or "", "url": i["html_url"], "user": _user(i.get("user")),
            "labels": [lb.get("name") for lb in i.get("labels") or [] if isinstance(lb, dict)],
            "comments": [{"user": _user(c.get("user")), "body": c.get("body") or ""} for c in comments]}


def pull_for(full, branch):
    """The open pull request whose head is this branch, or None."""
    owner = full.split("/")[0]
    rows = request("GET", "/repos/%s/pulls?state=open&head=%s" % (full, urllib.parse.quote("%s:%s" % (owner, branch))))
    return pulls_one(rows[0]) if rows else None


def pulls_one(p):
    return {"number": p["number"], "title": p["title"], "url": p["html_url"], "draft": p.get("draft"), "head": p["head"]["ref"],
            "base": p["base"]["ref"], "state": p["state"]}


def create_pull(full, head, base, title, body="", draft=False):
    p = request("POST", "/repos/%s/pulls" % full, {"title": title, "head": head, "base": base, "body": body, "draft": bool(draft)})
    return pulls_one(p)


def create_repo(name, private=True, description=""):
    return _repo(request("POST", "/user/repos", {"name": name, "private": bool(private), "description": description[:300], "auto_init": False}))


def comment(full, number, body):
    c = request("POST", "/repos/%s/issues/%d/comments" % (full, int(number)), {"body": body})
    return c.get("html_url")


def checks(full, ref):
    """CI for a commit: {state: success|failure|pending|none, runs: [{name, status, conclusion, url}]}."""
    runs = request("GET", "/repos/%s/commits/%s/check-runs?per_page=50" % (full, urllib.parse.quote(ref, safe=""))).get("check_runs") or []
    combined = request("GET", "/repos/%s/commits/%s/status" % (full, urllib.parse.quote(ref, safe="")))
    out = [{"name": r["name"], "status": r["status"], "conclusion": r.get("conclusion") or "", "url": r.get("html_url")} for r in runs]
    out += [{"name": s["context"], "status": "completed" if s["state"] != "pending" else "in_progress",
             "conclusion": {"error": "failure"}.get(s["state"], s["state"]), "url": s.get("target_url")} for s in combined.get("statuses") or []]
    if not out:
        state = "none"
    elif any(r["conclusion"] in ("failure", "timed_out", "cancelled", "action_required") for r in out):
        state = "failure"
    elif any(r["status"] != "completed" for r in out):
        state = "pending"
    else:
        state = "success"
    return {"state": state, "runs": out}
