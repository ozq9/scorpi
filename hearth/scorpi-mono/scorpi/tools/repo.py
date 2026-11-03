"""GitHub for the agents: read a repository's issues, pull requests and CI, and open pull requests, with the account
connected in Connections → GitHub (scorpi/github.py)."""

import json

from .. import github, gitops
from . import Result, ToolError, resolve_path, tool


def _full(repo="", path=""):
    if repo:
        full = github.parse_remote(repo) or repo.strip().strip("/")
        if full.count("/") == 1:
            return full
        raise ToolError("Give the repository as owner/name.")
    if path:
        folder = resolve_path(path, must_exist=True)
        full = github.parse_remote(gitops.remote_url(folder)) if gitops.is_repo(folder) else ""
        if full:
            return full
        raise ToolError("%s is not a GitHub repository (no github.com remote)." % folder)
    raise ToolError("Give repo (owner/name) or path (a project folder with a GitHub remote).")


def _call(fn):
    try:
        return fn()
    except (github.GitHubError, gitops.GitError) as exc:
        raise ToolError(str(exc))


@tool(
    "github",
    "Read from GitHub with the user's connected account. action: whoami | repo | issues | issue (number) | pulls | checks "
    "(CI of the project's current commit, or of ref). Identify the repository by repo (owner/name) or by path (a project "
    "folder whose git remote is on GitHub).",
    {"action": "string[whoami,repo,issues,issue,pulls,checks]: What to read", "repo?": "string: owner/name",
     "path?": "string: Project folder (instead of repo)", "number?": "integer: Issue or pull request number",
     "ref?": "string: Branch or commit for checks", "state?": "string[open,closed,all]: For issues and pulls (default open)"},
    group="code",
)
def github_read(ctx, action, repo="", path="", number=0, ref="", state="open"):
    if action == "whoami":
        user = _call(github.me)
        if not user:
            raise ToolError("GitHub is not connected. The user can connect it in Connections → GitHub.")
        return Result(json.dumps(user))
    full = _full(repo, path)
    if action == "repo":
        data = _call(lambda: github.repo(full))
    elif action == "issues":
        data = _call(lambda: github.issues(full, state or "open"))
    elif action == "issue":
        if not number:
            raise ToolError("Give the issue number.")
        data = _call(lambda: github.issue(full, number))
    elif action == "pulls":
        data = _call(lambda: github.pulls(full, state or "open"))
    elif action == "checks":
        if not ref and path:
            ref = gitops.run(["rev-parse", "HEAD"], resolve_path(path, must_exist=True)).strip()
        if not ref:
            raise ToolError("Give ref (a branch or commit) or path.")
        data = _call(lambda: github.checks(full, ref))
    else:
        raise ToolError("Unknown action %r." % action)
    return Result(json.dumps(data, ensure_ascii=False)[:30000])


@tool(
    "github_pull_request",
    "Open a pull request on GitHub for a project folder: commits uncommitted changes, moves them to a new branch when "
    "they are on the base branch, pushes, and opens the pull request. Only when the user asked for a pull request.",
    {"path": "string: The project folder (a git repository with a GitHub remote)", "title": "string: Pull request title",
     "body?": "string: Description in Markdown", "base?": "string: Branch to merge into (default: the repository's default)",
     "draft?": "boolean: Open as a draft"},
    group="code",
    risk="danger",
)
def github_pull_request(ctx, path, title, body="", base="", draft=False):
    folder = resolve_path(path, must_exist=True)
    full = _full("", folder)
    if not github.token():
        raise ToolError("GitHub is not connected. The user can connect it in Connections → GitHub.")

    def go():
        st = gitops.status(folder)
        target = base or gitops.default_branch(folder) or github.repo(full)["default_branch"]
        branch = st.get("branch") or ""
        if not branch or branch == target:
            name = "scorpi/" + "-".join("".join(c if c.isalnum() else " " for c in title.lower()).split())[:40]
            branch = gitops.switch(folder, name, create=True)
        if st.get("files"):
            gitops.commit(folder, title, author=github.author())
        gitops.push(folder, auth=github.token())
        return github.create_pull(full, branch, target, title, body, draft)

    pr = _call(go)
    return Result("Opened pull request #%s: %s (%s → %s)" % (pr["number"], pr["url"], pr["head"], pr["base"]))
