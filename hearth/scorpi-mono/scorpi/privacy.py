"""Privacy guard: what each provider says it does with what you send, private projects that only use providers which
don't train on it (or local models), and secrets kept out of every request to a cloud model.

Data use is as each provider states it in its terms (checked November 2026); free tiers often differ from paid ones.
Secrets (API keys, tokens, private keys, passwords in .env files) are swapped for placeholders like
__SECRET_1a2b3c4d__ before a request leaves the PC, and swapped back in the arguments of tool calls, so the agent can
still write a config file or call an API without the cloud model ever seeing the value.
"""

import hashlib
import json
import os
import re
import threading

from .config import DATA_DIR, SETTINGS

CHECKED = "November 2026"

# provider id -> (kind, what it says). kind: "no-train" (says it does not train on API data), "no-retain" (keeps no
# inputs or outputs), "may-train" (free tier may use prompts to improve models), "mixed" (depends on the model)
DATA = {
    "groq": ("no-train", "Says it does not train on API data and keeps no inference data by default."),
    "cloudflare": ("no-train", "Says customer content is not used to train models or improve services."),
    "ovh": ("no-train", "Says data is never used to train models and is not stored (EU, France)."),
    "ollama-cloud": ("no-train", "Says cloud prompts are processed transiently and never used for training."),
    "cerebras": ("no-retain", "Says inputs and outputs are not retained (training is not mentioned)."),
    "deepinfra": ("no-train", "Says inputs stay in memory only and it does not train on customer data."),
    "fireworks": ("no-train", "Says inference keeps no data by default and never trains on it."),
    "openai": ("no-train", "API data is not used for training by default."),
    "anthropic": ("no-train", "API data is not used for training by default."),
    "gemini": ("may-train", "Free tier: prompts may be used to improve Google products and read by reviewers (not in the EEA, "
                            "UK or Switzerland, or once billing is on)."),
    "mistral": ("may-train", "Free Experiment plan: data may be used for training unless you opt out in your Mistral account."),
    "nvidia": ("may-train", "Trial terms allow NVIDIA to use content to improve its products."),
    "deepseek": ("may-train", "Its terms allow using inputs to improve its services."),
    "openrouter": ("mixed", "Depends on the model's host: some free models' hosts may train. Turn those off in OpenRouter's "
                            "privacy settings."),
}
SAFE = ("no-train", "no-retain")

_local = threading.local()
_vault = {}  # placeholder -> secret (in memory only)
_vault_lock = threading.Lock()
PROJECTS = os.path.join(DATA_DIR, "project_privacy.json")


# ---------- providers ----------

def data_use(pid):
    kind, note = DATA.get(pid, ("unknown", "Its terms don't say clearly; treat it as not private."))
    trusted = pid in (SETTINGS.get("privacy_trusted") or [])
    return {"kind": kind, "note": note, "trusted": trusted, "private_ok": kind in SAFE or trusted, "checked": CHECKED}


def allowed(pid):
    """May a private project's work go to this provider?"""
    return data_use(pid)["private_ok"]


# ---------- private work ----------

def active():
    """Is the current work private: the global private mode, or a private project's agent in this thread."""
    return bool(SETTINGS.get("private_mode")) or bool(getattr(_local, "on", False))


class scope:
    """with privacy.scope(True): requests in this thread only go to providers that keep data private."""

    def __init__(self, on):
        self.on = bool(on)

    def __enter__(self):
        self.prev = getattr(_local, "on", False)
        _local.on = self.prev or self.on
        return self

    def __exit__(self, *exc):
        _local.on = self.prev


def _projects():
    try:
        with open(PROJECTS, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def project_private(slug):
    return bool(slug) and bool(_projects().get(slug))


def set_project_private(slug, on):
    data = _projects()
    if on:
        data[slug] = True
    else:
        data.pop(slug, None)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROJECTS, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


# ---------- secrets ----------

PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----")),
    ("API key", re.compile(r"\b(?:sk-(?:proj-|ant-|or-v1-)?[A-Za-z0-9_-]{24,}|gsk_[A-Za-z0-9]{24,}|AIza[0-9A-Za-z_-]{35}|"
                           r"hf_[A-Za-z0-9]{30,}|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9_-]{30,}|"
                           r"csk-[A-Za-z0-9]{30,}|xai-[A-Za-z0-9]{30,})\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),  # JWT
]
# NAME=value lines in .env files and shell exports: the value of anything called a secret, token, password or key
ASSIGNMENT = re.compile(r"(?im)^(\s*(?:export\s+|set\s+)?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|"
                        r"CLIENT_?SECRET)[A-Z0-9_]*\s*[=:]\s*[\"']?)([^\s\"'#]{8,})")
PLACEHOLDER = re.compile(r"__SECRET_[0-9a-f]{8}__")


def _token(value):
    token = "__SECRET_%s__" % hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]
    with _vault_lock:
        _vault[token] = value
    return token


def _known():
    """Keys Scorpi itself holds: always kept out, whatever they look like."""
    from .config import SECRET_KEYS
    out = []
    for key in SECRET_KEYS:
        value = SETTINGS.get(key)
        if isinstance(value, str) and len(value) >= 12:
            out.append(value)
    return out


def redact(text):
    """(text with secrets replaced by placeholders, number replaced)."""
    if not text or not isinstance(text, str):
        return text, 0
    count = 0
    for value in _known():
        if value in text:
            text = text.replace(value, _token(value))
            count += 1

    def sub(m):
        nonlocal count
        if PLACEHOLDER.fullmatch(m.group(0)):
            return m.group(0)
        count += 1
        return _token(m.group(0))

    for _, rx in PATTERNS:
        text = rx.sub(sub, text)

    def assign(m):
        nonlocal count
        value = m.group(2)
        if PLACEHOLDER.search(value) or value.lower() in ("true", "false", "none", "null", "changeme") or value.startswith(("${", "$(", "process.env", "os.environ")):
            return m.group(0)
        count += 1
        return m.group(1) + _token(value)

    text = ASSIGNMENT.sub(assign, text)
    return text, count


def outgoing(messages):
    """A copy of a request's messages with secrets replaced (for cloud models). Returns (messages, count)."""
    if not SETTINGS.get("redact_secrets", True):
        return messages, 0
    total = 0
    out = []
    for m in messages:
        m = dict(m)
        if isinstance(m.get("content"), str):
            m["content"], n = redact(m["content"])
            total += n
        if m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                raw = json.dumps(c.get("arguments"), ensure_ascii=False)
                clean, n = redact(raw)
                total += n
                calls.append(dict(c, arguments=json.loads(clean)) if n else c)
            m["tool_calls"] = calls
        out.append(m)
    return out, total


def restore(value):
    """Put the real secrets back into a tool call's arguments (placeholders the model copied)."""
    if isinstance(value, str):
        if "__SECRET_" not in value:
            return value
        with _vault_lock:
            return PLACEHOLDER.sub(lambda m: _vault.get(m.group(0), m.group(0)), value)
    if isinstance(value, dict):
        return {k: restore(v) for k, v in value.items()}
    if isinstance(value, list):
        return [restore(v) for v in value]
    return value
