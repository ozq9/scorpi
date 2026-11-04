"""Language model providers: Ollama (native), any OpenAI-compatible endpoint, and free cloud providers.

Cloud models are named "@<provider>/<model id>" (see providers.py) and can be mixed freely with local ones,
e.g. a local chat model with a cloud vision model.
"""

import contextlib
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import providers as P
from . import usage
from .config import SETTINGS

_caps_cache = {}
_cloud_cache = {}  # provider id -> {"at", "sig", "models", "error"}
_cloud_lock = threading.Lock()
_slow = {}  # provider id -> time it may be tried again (a model that missed its first-token deadline)
_slow_models = {}  # model ref -> time it may be tried again (404/410: not enabled for this account; 408: too slow)
_model_noticed = set()  # models whose automatic replacement was already explained in this session
_no_usage_option = set()  # endpoints that refused stream_options (they get no usage report, so tokens are estimated)
SLOW_COOLDOWN = 6 * 3600  # a provider that missed the first-token deadline is not retried for a working day
CLOUD_TTL = 900

# Model ids that are not chat models (speech, embeddings, image generation, safety classifiers...).
NON_CHAT = re.compile(
    r"embed|whisper|tts|guard|moderat|rerank|transcri|speech|audio|imagen|veo|dall-e|\baqa\b|playai|orpheus|safety|reward|"
    r"\bocr\b|-image|image-gen|native-audio|\blive\b|realtime|search-preview|computer-use|robotics|\bclip\b|\bbge\b|nv-embed|"
    r"parakeet|canary|voxtral|lyria|kosmos|deplot|paligemma|retriever|nemoretriever|-reward|-classifier",
    re.I,
)
VISION_HINT = re.compile(
    r"vision|-vl\b|-vl-|\bvl\b|gemini|gemma-?3|gpt-4o|gpt-4\.1|gpt-5|llama-4|pixtral|mistral-small|mistral-medium|glm-[\d.]+v|"
    r"kimi-vl|kimi-k2\.[5-9]|kimi-k3|kimi-for-coding|llava|multimodal|omni|maverick|scout|command-a-vision|claude|^openai(-large)?$|"
    r"qwen.*vl|minicpm-v",
    re.I,
)
RETRYABLE = (401, 402, 403, 404, 408, 409, 410, 413, 429, 500, 502, 503, 504, 529)


class LLMError(Exception):
    def __init__(self, message, status=0, retry_after=0, unreachable=False):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.unreachable = unreachable

    @property
    def retryable(self):
        msg = str(self).lower()
        return self.unreachable or self.status in RETRYABLE or any(k in msg for k in ("rate limit", "quota", "overloaded", "capacity"))


# ---------- cloud providers ----------

def is_cloud(model):
    return bool(model) and model.startswith("@")


def split_cloud(model):
    pid, _, mid = model[1:].partition("/")
    return pid, mid


def cloud_ref(pid, mid):
    return "@%s/%s" % (pid, mid)


def _cloud_key(pid):
    return SETTINGS[P.key_setting(pid)] or ""


def _cloud_account(pid):
    return str((SETTINGS["cloud_accounts"] or {}).get(pid) or "").strip()


def cloud_enabled(pid):
    prov = P.BY_ID.get(pid)
    if not prov:
        return False
    if prov.get("account") and not _cloud_account(pid):
        return False  # its URLs need the account id (Cloudflare)
    return bool(_cloud_key(pid)) or bool(prov.get("keyless") and SETTINGS["cloud_anon"])


def cloud_order():
    """Enabled providers, keyed ones first (catalog order), keyless anonymous access last."""
    keyed = [p["id"] for p in P.PROVIDERS if _cloud_key(p["id"])]
    anon = [p["id"] for p in P.PROVIDERS if p["id"] not in keyed and cloud_enabled(p["id"])]
    return keyed + anon


_active_variant = {}  # provider id -> index into _variants() that last listed models successfully
_learned_protocol = {}  # "@pid/model" -> protocol that worked after the default one was refused


def _variants(pid):
    """URL sets to try for a provider: the user's override, else the catalog URL followed by its alternatives."""
    prov = P.BY_ID[pid]
    override = (SETTINGS["cloud_urls"] or {}).get(pid, "").rstrip("/")
    if override:
        return [{"url": override, "messages_url": override}]
    if prov.get("keyless") and not _cloud_key(pid):
        return [{"url": prov["anon_url"], "models_url": prov["anon_models_url"]}]
    first = {k: prov[k] for k in ("url", "messages_url", "models_url") if prov.get(k)}
    options = [first] + list(prov.get("variants") or [])
    account = _cloud_account(pid)
    if prov.get("account"):
        options = [{k: v.replace("{account}", urllib.parse.quote(account, safe="")) for k, v in o.items()} for o in options]
    return options


def _variant(pid):
    options = _variants(pid)
    return options[min(_active_variant.get(pid, 0), len(options) - 1)]


_session = threading.local()


@contextlib.contextmanager
def session(session_id):
    """Requests made inside belong to one conversation (gateways such as OpenCode route and cache by it)."""
    previous = getattr(_session, "id", "")
    _session.id = str(session_id or "")
    try:
        yield
    finally:
        _session.id = previous


def _session_id(messages=None):
    sid = getattr(_session, "id", "")
    if sid:
        return sid
    # no conversation (titles, summaries...): the first user message keeps the id stable across a task's calls
    first = next((m.get("content") for m in messages or [] if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
    return hashlib.sha256(first.encode("utf-8")).hexdigest()[:32] if first else uuid.uuid4().hex


def _cloud_headers(pid, protocol="openai", messages=None):
    headers = {"Content-Type": "application/json", "User-Agent": "Scorpi/1.0"}
    if pid.startswith("opencode"):
        # OpenCode Go asks every client for a stable per-conversation id and its own user agent
        headers["x-opencode-session"] = _session_id(messages)
    key = _cloud_key(pid)
    if key:
        headers["Authorization"] = "Bearer " + key
    if protocol == "anthropic" or P.BY_ID[pid].get("protocol") == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
    if pid == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/scorpi-local"
        headers["X-Title"] = "Scorpi"
    return headers


def _protocol(pid, mid):
    prov = P.BY_ID[pid]
    learned = _learned_protocol.get(cloud_ref(pid, mid))
    if learned:
        return learned
    if prov.get("protocol") == "anthropic":
        return "anthropic"
    if prov.get("messages_models") and _variant(pid).get("messages_url") and re.search(prov["messages_models"], mid, re.I):
        return "anthropic"
    return "openai"


def _endpoint(model, protocol=None, messages=None):
    """How to reach a model: {'protocol', 'base', 'headers', 'model'}, or None when it runs in Ollama."""
    if is_cloud(model):
        pid, mid = split_cloud(model)
        prov = P.BY_ID.get(pid)
        if not prov and pid in P.RETIRED:
            # 410 Gone: chat_stream switches to another available model and says why
            raise LLMError("%s." % P.RETIRED[pid]["why"], status=410)
        if not prov:
            raise LLMError("Unknown model provider %r." % pid)
        if not cloud_enabled(pid):
            raise LLMError("%s needs an API key. Add one in Connections → Model." % prov["name"], status=401)
        protocol = protocol or _protocol(pid, mid)
        variant = _variant(pid)
        base = variant.get("messages_url") if protocol == "anthropic" and prov.get("protocol") != "anthropic" else variant["url"]
        return {"protocol": protocol, "base": (base or variant["url"]).rstrip("/"), "headers": _cloud_headers(pid, protocol, messages), "model": mid}
    if SETTINGS["provider"] == "openai":
        return {"protocol": "openai", "base": _openai(), "headers": _openai_headers(), "model": model}
    return None


def _entry_context(entry):
    """The model's context window in tokens, when its listing says (OpenRouter, Groq, Mistral, Together...)."""
    if not isinstance(entry, dict):
        return 0
    for key in ("context_length", "context_window", "max_context_length", "max_model_len", "contextWindow", "max_input_tokens"):
        value = entry.get(key) or ((entry.get("top_provider") or {}).get(key) if isinstance(entry.get("top_provider"), dict) else None)
        try:
            if value and int(value) >= 1024:
                return int(value)
        except (TypeError, ValueError):
            continue
    for prop in entry.get("properties") or []:  # Cloudflare
        if isinstance(prop, dict) and prop.get("property_id") == "context_window":
            try:
                return int(prop.get("value"))
            except (TypeError, ValueError):
                pass
    return 0


def _entry_caps(entry, mid):
    tools = vision = None
    caps = set()
    if isinstance(entry, dict):
        arch = entry.get("architecture") if isinstance(entry.get("architecture"), dict) else {}
        mods = entry.get("input_modalities") or arch.get("input_modalities") or entry.get("supported_input_modalities")
        if isinstance(mods, dict):
            mods = mods.get("input")
        if isinstance(mods, list) and mods:
            vision = "image" in [str(m).lower() for m in mods]
        params = entry.get("supported_parameters")
        if isinstance(params, list) and params:
            tools = "tools" in params
            if "reasoning" in params:
                caps.add("thinking")
        listed = entry.get("capabilities")
        if isinstance(listed, dict):
            if "function_calling" in listed:
                tools = bool(listed["function_calling"])
            if "vision" in listed:
                vision = bool(listed["vision"])
        elif isinstance(listed, list) and listed:
            low = [str(x).lower() for x in listed]
            tools = any("tool" in x or "function" in x for x in low)
            if "vision" in low:
                vision = True
        props = entry.get("properties")
        if isinstance(props, list) and props and isinstance(entry.get("task"), dict):
            # Cloudflare's catalog: [{"property_id": "function_calling", "value": "true"}, ...]
            flags = {str(x.get("property_id")): str(x.get("value")).lower() for x in props if isinstance(x, dict)}
            tools = flags.get("function_calling") == "true"
            if flags.get("vision") == "true":
                vision = True
        if isinstance(entry.get("tools"), bool):
            tools = entry["tools"]
        if isinstance(entry.get("vision"), bool):
            vision = entry["vision"]
        if entry.get("reasoning") is True:
            caps.add("thinking")
    if vision is None:
        vision = bool(VISION_HINT.search(mid))
    if tools is None:
        tools = True
    if tools:
        caps.add("tools")
    if vision:
        caps.add("vision")
    return caps


def _fetch_cloud(pid):
    prov = P.BY_ID[pid]
    entries, error = None, None
    for index, variant in enumerate(_variants(pid)):
        url = variant.get("models_url") or variant["url"].rstrip("/") + "/models"
        try:
            data = http_json(url, headers=_cloud_headers(pid), timeout=8)
        except urllib.error.HTTPError as exc:
            error = _http_error(exc)
            if exc.code in (401, 403):
                break  # the key is wrong; another URL will not help
            continue
        except Exception as exc:
            error = LLMError(str(getattr(exc, "reason", exc)), unreachable=True)
            continue
        _active_variant[pid] = index
        entries = (data.get("data") or data.get("models") or data.get("result") or []) if isinstance(data, dict) else data
        break
    extra = [m.strip() for m in re.split(r"[,\s]+", (SETTINGS["cloud_extra_models"] or {}).get(pid, "")) if m.strip()]
    if entries is None:
        entries = list(prov.get("static") or [])
        if not entries and not extra:
            raise error or LLMError("No models listed.")
    only = re.compile(prov["only"], re.I) if prov.get("only") else None
    skip = re.compile(prov["skip"], re.I) if prov.get("skip") else None
    out, seen = [], set()
    for entry in list(entries or []) + extra:
        if isinstance(entry, str):
            mid = entry
        elif isinstance(entry.get("task"), dict):
            mid = entry.get("name") or ""  # Cloudflare: "id" is an internal uuid, "name" is the model ("@cf/...")
        else:
            mid = entry.get("id") or entry.get("name") or ""
        mid = str(mid)
        if mid.startswith("models/"):
            mid = mid[7:]
        if not mid or mid in seen:
            continue
        if entry not in extra:  # ids the user typed are always offered
            if NON_CHAT.search(mid) or (only and not only.search(mid)) or (skip and skip.search(mid)):
                continue
            if isinstance(entry, dict) and (entry.get("type") or "chat") not in ("chat", "chat-completion", "llm", "language", "model"):
                continue
            if prov.get("free_only") and isinstance(entry, dict):
                price = entry.get("pricing") or {}
                free = mid.endswith(":free") or mid == "openrouter/free" or (str(price.get("prompt")) in ("0", "0.0") and str(price.get("completion")) in ("0", "0.0"))
                if not free:
                    continue
        seen.add(mid)
        item = {"id": mid, "caps": sorted(_entry_caps(entry, mid))}
        window = _entry_context(entry)
        if window:
            item["ctx"] = window
        out.append(item)
    return out


def cloud_models(pid, refresh=False):
    """Live model list of one provider (cached), with the recommended chat and vision picks flagged first."""
    if not cloud_enabled(pid):
        return [], ""
    sig = _cloud_key(pid)[-6:]
    entry = _cloud_cache.get(pid)
    if not refresh and entry and entry["sig"] == sig and time.time() - entry["at"] < (CLOUD_TTL if not entry["error"] else 60):
        return entry["models"], entry["error"]
    error = ""
    try:
        models = _fetch_cloud(pid)
    except LLMError as exc:
        models, error = [], str(exc)
    except Exception as exc:
        models, error = [], getattr(exc, "reason", None) and str(exc.reason) or str(exc)
    prov = P.BY_ID[pid]
    picks = {}
    for kind, need, pats in (("chat", {"tools"}, prov["prefer"]), ("vision", {"vision"}, prov.get("prefer_vision") or []),
                             ("code", {"tools"}, prov.get("prefer_code") or [])):
        pick = _prefer([m for m in models if need <= set(m["caps"])], pats)
        if pick:
            picks.setdefault(pick["id"], []).append(kind)
    for m in models:
        m["recommended"] = picks.get(m["id"], [])
    models.sort(key=lambda m: (not m["recommended"], m["id"].lower()))
    with _cloud_lock:
        _cloud_cache[pid] = {"at": time.time(), "sig": sig, "models": models, "error": error}
    return models, error


def _prefer(models, patterns):
    for pat in patterns:
        rx = re.compile(pat, re.I)
        for m in models:
            if rx.search(m["id"]):
                return m
    return models[0] if models else None


def cloud_overview(refresh=False):
    """Every catalog provider with its state and live models (fetched in parallel)."""
    enabled = [p["id"] for p in P.PROVIDERS if cloud_enabled(p["id"])]
    results = {}
    if enabled:
        with ThreadPoolExecutor(max_workers=min(8, len(enabled))) as pool:
            for pid, res in zip(enabled, pool.map(lambda x: cloud_models(x, refresh), enabled)):
                results[pid] = res
    out = []
    for prov in P.PROVIDERS:
        models, error = results.get(prov["id"], ([], ""))
        out.append({
            "id": prov["id"], "name": prov["name"], "about": prov["about"], "signup": prov["signup"],
            "keyless": bool(prov.get("keyless")), "has_key": bool(_cloud_key(prov["id"])), "enabled": prov["id"] in enabled,
            "stt": bool(prov.get("stt")), "embed": prov.get("embed", ""), "error": error, "category": prov.get("category", "free"),
            "url": _variant(prov["id"])["url"] if prov["id"] in enabled else prov.get("url", ""), "default_url": prov.get("url", ""),
            "account": prov.get("account", ""), "account_set": bool(_cloud_account(prov["id"])),
            "extra": (SETTINGS["cloud_extra_models"] or {}).get(prov["id"], ""),
            "models": [dict(m, name=cloud_ref(prov["id"], m["id"])) for m in models],
        })
    return out


def pick_model(need=(), exclude_providers=(), exclude=()):
    """Best available cloud model with the needed capabilities ('tools', 'vision'), in provider order."""
    need = set(need)
    now = time.time()
    for pid in cloud_order():
        if pid in exclude_providers or _slow.get(pid, 0) > now:
            continue
        models, _ = cloud_models(pid)
        prov = P.BY_ID[pid]
        eligible = [m for m in models if need <= set(m["caps"]) and cloud_ref(pid, m["id"]) not in exclude]
        pats = prov.get("prefer_vision") if "vision" in need else prov["prefer"]
        pick = _prefer(eligible, pats or [])
        if pick:
            return cloud_ref(pid, pick["id"])
    return ""


def pick_code_model(exclude=(), exclude_providers=()):
    """The strongest coding model on offer: each provider's coding pick, in the order the user set providers up.
    Used when the coding model is "auto" (paid plans come first, then free tiers, then free credits)."""
    now = time.time()
    for pid in cloud_order():
        if _slow.get(pid, 0) > now or pid in exclude_providers:
            continue
        prov = P.BY_ID[pid]
        models, _ = cloud_models(pid)
        eligible = [m for m in models if "tools" in m["caps"] and cloud_ref(pid, m["id"]) not in exclude
                    and _slow_models.get(cloud_ref(pid, m["id"]), 0) <= now]
        pick = _prefer(eligible, prov.get("prefer_code") or prov["prefer"])
        if pick:
            return cloud_ref(pid, pick["id"])
    return ""


def coding_model():
    """The model for coding work: the "Coding model" setting, the best coder on offer for "auto", or "" (the chat model)."""
    chosen = SETTINGS["code_model"] or ""
    if chosen == "auto":
        return pick_code_model() or ""
    return chosen


def pick_same_provider(model, need=(), exclude=()):
    """Another model from the same provider. NVIDIA (and some gateways) list models their account cannot call; a 404
    on one model should not send the whole conversation to a different provider when a working sibling exists."""
    pid, _ = split_cloud(model) if is_cloud(model) else ("", "")
    prov = P.BY_ID.get(pid)
    if not prov:
        return ""
    models, _ = cloud_models(pid)
    need = set(need)
    exclude = set(exclude)
    eligible = [m for m in models if need <= set(m["caps"]) and cloud_ref(pid, m["id"]) not in exclude]
    pats = prov.get("prefer_vision") if "vision" in need else prov["prefer"]
    pick = _prefer(eligible, pats or [])
    return cloud_ref(pid, pick["id"]) if pick else ""


def vision_model(need_tools=False):
    """The model used for images when the chat model is text-only: the setting, else any vision model available.
    need_tools: only a model that can also call tools, so it can take over a turn (computer use) instead of
    just describing pictures."""
    need = {"vision", "tools"} if need_tools else {"vision"}
    chosen = SETTINGS["vision_model"]
    if chosen and (not need_tools or need <= capabilities(chosen)):
        return chosen
    try:
        for m in _local_models():
            name = m.get("name") or ""
            if "embed" not in name and need <= capabilities(name):
                return name
    except Exception:
        pass
    return pick_model(need)


_learned_window = {}  # model ref -> tokens a request may hold, learned from "request too large" answers


def context_budget(model):
    """How many tokens of conversation to send to a model: most of its context window, a limit learned from the
    provider, or a safe default (free tiers often cap tokens per request or per minute well below the window)."""
    learned = _learned_window.get(model)
    if not is_cloud(model or ""):
        window = int(SETTINGS["num_ctx"] or 16384) if SETTINGS["provider"] == "ollama" else 32768
        return min(learned or window, int(window * 0.8))
    window = 0
    pid, mid = split_cloud(model)
    if pid in P.BY_ID:
        for m in (_cloud_cache.get(pid) or {}).get("models") or []:
            if m["id"] == mid:
                window = m.get("ctx") or 0
                break
    budget = int(window * 0.7) if window else 48000
    return max(2000, min(budget, learned or budget, 120000))


def learn_limit(model, exc):
    """Remember a smaller budget after a provider refused a request as too large. True when there is a smaller one."""
    if not is_context_error(exc):
        return False
    text = str(exc)
    limit = 0
    for pattern in (r"limit\s*[:=]?\s*(\d{3,7})", r"maximum context length is (\d{3,7})", r"context (?:length|window) (?:of|is) (\d{3,7})",
                    r"max(?:imum)?[_ ]tokens?[^\d]{0,20}(\d{3,7})"):
        m = re.search(pattern, text, re.I)
        if m:
            limit = int(m.group(1))
            break
    current = context_budget(model)
    new = int(limit * 0.75) if limit else int(current * 0.5)
    new = max(1500, min(new, int(current * 0.75)))
    _learned_window[model] = new
    return True


def is_context_error(exc):
    """A request refused because it holds too many tokens (context window or tokens-per-minute limit)."""
    text = str(exc).lower()
    status = getattr(exc, "status", 0)
    if status == 413 or "request too large" in text:
        return True
    if status == 429:
        return False  # a rate limit: waiting or another model helps, a smaller request does not
    return any(k in text for k in ("context length", "context_length", "context window", "maximum context", "too many tokens",
                                   "prompt is too long", "input is too long", "reduce the length", "string too long",
                                   "exceeds the model's", "exceeds the context"))


def describe_images(images, prompt, model=None):
    """Let a vision model look at images for a text-only model. Returns (description, model used)."""
    model = model or vision_model()
    if not model:
        return "", ""
    text = ""
    messages = [{"role": "user", "content": prompt, "images": images}]
    for event in chat_stream(model, messages, options={"temperature": 0.2, "num_ctx": 8192}):
        if event["type"] == "switch" and event["model"]:
            model = event["model"]
        elif event["type"] == "end":
            text = event["content"]
    return text.strip(), model


def _ollama():
    return (SETTINGS["ollama_url"] or "http://127.0.0.1:11434").rstrip("/")


def _openai():
    return (SETTINGS["openai_url"] or "").rstrip("/")


def _openai_headers():
    headers = {"Content-Type": "application/json"}
    if SETTINGS["openai_key"]:
        headers["Authorization"] = "Bearer " + SETTINGS["openai_key"]
    return headers


def http_json(url, payload=None, method=None, headers=None, timeout=60):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method=method or ("POST" if data is not None else "GET"),
        headers=headers or {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8") or "{}")


def _http_error(exc):
    status = getattr(exc, "code", 0) or 0
    try:
        retry_after = float(exc.headers.get("Retry-After") or 0)
    except (TypeError, ValueError, AttributeError):
        retry_after = 0
    try:
        detail = exc.read().decode("utf-8", "replace")
        parsed = json.loads(detail)
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                err = err.get("message")
            detail = err or parsed.get("detail") or parsed.get("message") or parsed.get("title") or detail
    except Exception:
        detail = str(exc)
    return LLMError(str(detail)[:600], status=status, retry_after=retry_after)


# ---------- models ----------

def _local_models(timeout=None):
    if SETTINGS["provider"] == "openai":
        if not _openai():
            return []
        data = http_json(_openai() + "/models", headers=_openai_headers(), timeout=timeout or 15)
        return [{"name": m.get("id"), "size": 0, "details": {}} for m in data.get("data", [])]
    data = http_json(_ollama() + "/api/tags", timeout=timeout or 8)
    return data.get("models", [])


def list_models():
    """Local models plus the models of every enabled cloud provider. Raises only if nothing at all is reachable."""
    local, error = [], None
    try:
        local = _local_models()
    except Exception as exc:
        error = exc
    cloud = []
    for prov in cloud_overview():
        for m in prov["models"]:
            cloud.append({"name": m["name"], "size": 0, "details": {}, "cloud": True, "provider": prov["id"],
                          "provider_name": prov["name"], "id": m["id"], "caps": m["caps"], "recommended": m["recommended"]})
    if error is not None and not cloud:
        raise error
    return local + cloud


_FILLER = {"model", "the", "use", "using", "with", "on", "via", "from", "a", "an", "please", "llm"}


def find_model(query):
    """A model ref for a loose name as people say it ("deepseek flash 4.1", "glm 5", "groq llama 70b").
    Every word has to appear in the model's name (provider included); ties go to the provider used first.
    Raises ValueError naming close matches when nothing fits."""
    query = str(query or "").strip()
    if not query:
        raise ValueError("Name a model.")
    try:
        names = [m["name"] for m in list_models() if m.get("name") and not NON_CHAT.search(m["name"])]
    except Exception:
        names = []
    if query in names or (is_cloud(query) and "/" in query):
        return query
    words = [w for w in re.split(r"[^a-z0-9.]+", query.lower()) if w and w not in _FILLER]
    order = {pid: i for i, pid in enumerate(cloud_order())}

    def rank(name):
        flat = name.lower()
        squashed = re.sub(r"[^a-z0-9.]", "", flat)
        if not all(w in flat or w in squashed for w in words):
            return None
        pid = split_cloud(name)[0] if is_cloud(name) else ""
        mid = split_cloud(name)[1] if is_cloud(name) else name
        # fewer extra words (e.g. "-thinking", "-preview") wins, then the provider the user set up first
        return (len(re.findall(r"[a-z]+|\d+(?:\.\d+)?", mid.lower())) - len(words), order.get(pid, -1 if not pid else 99), len(name))

    hits = sorted((r, n) for n, r in ((n, rank(n)) for n in names) if r is not None)
    if hits:
        return hits[0][1]
    import difflib
    close = difflib.get_close_matches(query.lower(), [n.lower() for n in names], n=4, cutoff=0.3)
    lookup = {n.lower(): n for n in names}
    raise ValueError("No model matches %r.%s" % (query, (" Closest: " + ", ".join(lookup[c] for c in close) + ".") if close else
                                                  " Add its provider in Connections → Model first."))


def capabilities(model):
    """Return a set like {'tools', 'vision', 'thinking'} (best effort)."""
    if not model:
        return set()
    if model in _caps_cache:
        return _caps_cache[model]
    if is_cloud(model):
        pid, mid = split_cloud(model)
        for m in cloud_models(pid)[0] if pid in P.BY_ID else []:
            if m["id"] == mid:
                return set(m["caps"])
        return _entry_caps(None, mid)
    caps = {"tools"}
    if SETTINGS["provider"] == "ollama":
        try:
            info = http_json(_ollama() + "/api/show", {"model": model}, timeout=10)
            listed = info.get("capabilities")
            if listed:
                caps = set(listed)
            else:
                family = json.dumps(info.get("details", {})).lower() + model.lower()
                if any(k in family for k in ("llava", "vision", "vl", "gemma3", "moondream", "minicpm-v")):
                    caps.add("vision")
        except Exception:
            pass
    else:
        lowered = model.lower()
        if any(k in lowered for k in ("vision", "gpt-4o", "gpt-4.1", "gemini", "vl", "claude", "llama-4")):
            caps.add("vision")
    _caps_cache[model] = caps
    return caps


def default_model():
    model = SETTINGS["model"]
    if model:
        return model
    try:
        models = _local_models()
    except Exception:
        models = []
    # prefer a chat model over embedding-only models
    chat = [m for m in models if "embed" not in (m.get("name") or "")]
    if chat:
        return chat[0]["name"]
    return pick_model({"tools"}) or pick_model() or ""


def pull_model(name):
    req = urllib.request.Request(
        _ollama() + "/api/pull", data=json.dumps({"model": name, "stream": True}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    return urllib.request.urlopen(req, timeout=3600)


def delete_model(name):
    req = urllib.request.Request(
        _ollama() + "/api/delete", data=json.dumps({"model": name}).encode(),
        headers={"Content-Type": "application/json"}, method="DELETE",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


# ---------- chat ----------

def chat_stream(model, messages, tools=None, options=None, cancelled=lambda: False, compactable=False):
    """Yield events: {'type': 'thinking'|'token', 'content'} and finally
    {'type': 'end', 'content', 'thinking', 'tool_calls': [{'id','name','arguments'}]}.
    When a model is rate-limited or unreachable before it answers, another available model takes over and
    {'type': 'switch', 'model', 'text'} is yielded first.
    compactable: the caller can shorten the conversation, so a "too many tokens" refusal is raised to it (after
    learning the limit) instead of moving to another model."""
    options = options or {}
    need = {"tools"} if tools else set()
    if any(m.get("images") for m in messages):
        need.add("vision")
    tried, waited = [], False
    same_tried = False
    if SETTINGS["cloud_fallback"] and is_cloud(model):
        now = time.time()
        if _slow_models.get(model, 0) > now:
            alt = pick_same_provider(model, need, [model]) or pick_model(need, {split_cloud(model)[0]}, [model])
            if alt and alt != model:
                if model not in _model_noticed:
                    _model_noticed.add(model)
                    yield {"type": "switch", "model": alt, "text": "%s is not working well; using %s." % (_label(model), _label(alt))}
                model = alt
        elif _slow.get(split_cloud(model)[0], 0) > now:
            alt = pick_model(need, {split_cloud(model)[0]}, ())
            if alt:
                if model not in _model_noticed:
                    _model_noticed.add(model)
                    yield {"type": "switch", "model": alt, "text": "%s has been slow; using %s." % (_label(model), _label(alt))}
                model = alt
    while True:
        started = False
        began = time.time()
        try:
            for event in _route_stream(model, messages, tools, options, cancelled):
                started = True
                if event["type"] == "end":
                    reply = (event.get("content") or "") + (event.get("thinking") or "") + json.dumps([c.get("arguments") for c in event.get("tool_calls") or []])
                    usage.record(model, event.get("usage"), messages, reply, began)
                yield event
            return
        except LLMError as exc:
            if not started and is_context_error(exc):
                learn_limit(model, exc)
                if compactable:
                    raise
            if is_cloud(model) and exc.status in (404, 410, 408):
                _slow_models[model] = time.time() + SLOW_COOLDOWN
            # a 404/410 usually means "this model is not enabled for this account" (NVIDIA lists models the key
            # cannot call). Try a working sibling from the same provider before leaving it.
            if (not started and not same_tried and is_cloud(model) and SETTINGS["cloud_fallback"]
                    and exc.status in (404, 410, 408)):
                alt = pick_same_provider(model, need, [*tried, model])
                if alt and alt != model:
                    same_tried = True
                    tried.append(model)
                    why = "is too slow" if exc.status == 408 else "is not available to this account" if exc.status == 404 else "has been retired"
                    yield {"type": "switch", "model": alt, "text": "%s %s; trying %s." % (_label(model), why, _label(alt))}
                    model = alt
                    continue
            if exc.status == 408 and is_cloud(model):
                _slow[split_cloud(model)[0]] = time.time() + SLOW_COOLDOWN
            if started or not exc.retryable or not SETTINGS["cloud_fallback"] or cancelled():
                raise
            tried.append(model)
            alt = pick_model(need, {split_cloud(m)[0] for m in tried if is_cloud(m)}, tried)
            if not alt and exc.retry_after and exc.retry_after <= 40 and not waited:
                waited = True
                yield {"type": "switch", "model": model, "text": "%s is busy; waiting %d s." % (_label(model), exc.retry_after)}
                deadline = time.time() + exc.retry_after
                while time.time() < deadline and not cancelled():
                    time.sleep(0.25)
                tried.pop()
                continue
            if not alt:
                raise
            yield {"type": "switch", "model": alt, "text": "%s is unavailable (%s). Switched to %s." % (_label(model), _short(exc), _label(alt))}
            model = alt


def _route_stream(model, messages, tools, options, cancelled):
    endpoint = _endpoint(model, messages=messages)
    if endpoint is None:
        yield from _ollama_stream(model, messages, tools, options, cancelled)
        return
    stream = _anthropic_stream if endpoint["protocol"] == "anthropic" else _openai_stream
    try:
        yield from stream(endpoint, model, messages, tools, options, cancelled)
    except LLMError as exc:
        # Gateways like OpenCode serve some models only through the other API style: try it once and remember.
        other = "openai" if endpoint["protocol"] == "anthropic" else "anthropic"
        pid = split_cloud(model)[0] if is_cloud(model) else ""
        refused = exc.status in (404, 405, 415) or (exc.status in (400, 422) and re.search(r"endpoint|not supported|messages|chat/completions|unsupported model", str(exc), re.I))
        if not pid or not refused or (other == "anthropic" and not _variant(pid).get("messages_url") and P.BY_ID[pid].get("protocol") != "anthropic"):
            raise
        alt = _endpoint(model, other, messages)
        yield from (_anthropic_stream if other == "anthropic" else _openai_stream)(alt, model, messages, tools, options, cancelled)
        _learned_protocol[model] = other


def _label(model):
    if is_cloud(model):
        pid, mid = split_cloud(model)
        name = (P.BY_ID.get(pid) or P.RETIRED.get(pid) or {}).get("name") or pid
        return "%s · %s" % (name, mid)
    return model


def label(model):
    """How a model is shown to people: "Groq · llama-3.3-70b", or the local name."""
    return _label(model)


def _short(exc):
    if exc.status == 429 or "rate" in str(exc).lower():
        return "rate limit"
    if exc.status == 408:
        return "no answer in time"
    if exc.status == 410:
        return "model retired"
    if exc.status == 404:
        return "model not available to this account" if "account" in str(exc).lower() else "model not available"
    if exc.unreachable:
        return "offline"
    return ("HTTP %d" % exc.status) if exc.status else str(exc)[:80]


def _read_chunk(resp, model):
    """One read from a streaming response; a stalled provider becomes a retryable error instead of a fifteen-minute hang."""
    try:
        return resp.read1(4096) if hasattr(resp, "read1") else resp.read(1024)
    except TimeoutError:
        raise LLMError("%s stopped answering (the stream timed out)." % _label(model), status=408, unreachable=True)


def _ollama_stream(model, messages, tools, options, cancelled):
    payload = {"model": model, "messages": [_to_ollama(m) for m in messages], "stream": True, "options": options}
    if tools:
        payload["tools"] = tools
    req = urllib.request.Request(
        _ollama() + "/api/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc)
    except TimeoutError:
        raise LLMError("Ollama took longer than 300 s to answer (is the model loading?).", status=408, unreachable=True)
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise LLMError("Ollama took longer than 300 s to answer (is the model loading?).", status=408, unreachable=True)
        raise LLMError("Ollama is not reachable at %s (%s). Start Ollama, or add a free cloud model in Connections → Model." % (_ollama(), exc.reason), unreachable=True)

    content, thinking, calls = "", "", []
    used = None
    buffer = b""
    with resp:
        while True:
            if cancelled():
                break
            chunk = _read_chunk(resp, model)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("error"):
                    raise LLMError(event["error"])
                if event.get("done") and (event.get("prompt_eval_count") or event.get("eval_count")):
                    used = {"input": event.get("prompt_eval_count") or 0, "output": event.get("eval_count") or 0}
                msg = event.get("message") or {}
                if msg.get("thinking"):
                    thinking += msg["thinking"]
                    yield {"type": "thinking", "content": msg["thinking"]}
                if msg.get("content"):
                    content += msg["content"]
                    yield {"type": "token", "content": msg["content"]}
                for call in msg.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args or "{}")
                        except ValueError:
                            args = {"_raw": args}
                    calls.append({"id": call.get("id") or "call_%d" % len(calls), "name": fn.get("name", ""), "arguments": args})
    yield {"type": "end", "content": content, "thinking": thinking, "tool_calls": calls, "usage": used}


def _to_ollama(m):
    out = {"role": m["role"], "content": m.get("content") or ""}
    if m.get("images"):
        out["images"] = m["images"]
    if m.get("tool_calls"):
        out["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in m["tool_calls"]]
    if m["role"] == "tool" and m.get("name"):
        out["tool_name"] = m["name"]
    return out


def _to_openai(m):
    role = m["role"]
    if role == "tool":
        return {"role": "tool", "tool_call_id": m.get("tool_call_id") or "call_0", "content": m.get("content") or ""}
    out = {"role": role}
    if m.get("images") and role == "user":
        parts = [{"type": "text", "text": m.get("content") or ""}]
        for img in m["images"]:
            parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + img}})
        out["content"] = parts
    else:
        out["content"] = m.get("content") or ""
    if m.get("tool_calls"):
        out["tool_calls"] = [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
            for c in m["tool_calls"]
        ]
    return out


def _openai_stream(endpoint, model, messages, tools, options, cancelled):
    base, headers, model_id = endpoint["base"], endpoint["headers"], endpoint["model"]
    if not base:
        raise LLMError("No OpenAI-compatible endpoint configured. Set one in Settings → Model.")
    payload = {
        "model": model_id,
        "messages": [_to_openai(m) for m in messages],
        "stream": True,
        "temperature": options.get("temperature", 0.6),
    }
    if tools:
        payload["tools"] = tools
    url = base.rstrip("/") + "/chat/completions"
    if url not in _no_usage_option:
        payload["stream_options"] = {"include_usage": True}  # one last chunk with the token counts
    resp = _open_stream(url, payload, headers, model)

    content, thinking = "", ""
    used = None
    partial = {}
    buffer = b""
    with resp:
        while True:
            if cancelled():
                break
            chunk = _read_chunk(resp, model)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if event.get("error"):
                    err = event["error"]
                    code = err.get("code") if isinstance(err, dict) else 0
                    raise LLMError(err.get("message") if isinstance(err, dict) else str(err), status=code if isinstance(code, int) else 0)
                report = event.get("usage") or (event.get("x_groq") or {}).get("usage")
                if isinstance(report, dict) and (report.get("prompt_tokens") or report.get("completion_tokens")):
                    cached = int(((report.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
                    used = {"input": max(0, int(report.get("prompt_tokens") or 0) - cached), "output": int(report.get("completion_tokens") or 0),
                            "cached": cached}
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        thinking += reasoning
                        yield {"type": "thinking", "content": reasoning}
                    if delta.get("content"):
                        content += delta["content"]
                        yield {"type": "token", "content": delta["content"]}
                    for tc in delta.get("tool_calls") or []:
                        slot = partial.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]
    calls = []
    for index in sorted(partial):
        slot = partial[index]
        try:
            args = json.loads(slot["args"] or "{}")
        except ValueError:
            args = {"_raw": slot["args"]}
        calls.append({"id": slot["id"] or "call_%d" % index, "name": slot["name"], "arguments": args})
    yield {"type": "end", "content": content, "thinking": thinking, "tool_calls": calls, "usage": used}


def _open_stream(url, payload, headers, model):
    # A provider that never answers must not hold the chat for the socket's full 15 minutes: give a cloud model
    # 30 s to produce its first byte and switch to the next one (local models get longer to load).
    wait = 30 if is_cloud(model) else 240
    for attempt in range(3):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            return urllib.request.urlopen(req, timeout=wait)
        except urllib.error.HTTPError as exc:
            err = _http_error(exc)
            # some reasoning models only accept their default temperature
            if attempt < 2 and exc.code == 400 and "temperature" in payload and "temperature" in str(err).lower():
                payload = {k: v for k, v in payload.items() if k != "temperature"}
                continue
            # an endpoint that does not know stream_options (the usage report) answers without it
            if attempt < 2 and exc.code in (400, 422) and "stream_options" in payload:
                _no_usage_option.add(url)
                payload = {k: v for k, v in payload.items() if k != "stream_options"}
                continue
            raise err
        except TimeoutError:
            raise LLMError("%s took longer than %d s to answer." % (_label(model), wait), status=408, unreachable=True)
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise LLMError("%s took longer than %d s to answer." % (_label(model), wait), status=408, unreachable=True)
            raise LLMError("%s is not reachable: %s" % (_label(model), exc.reason), unreachable=True)


def _image_mime(data):
    return {"/9j/": "image/jpeg", "iVBO": "image/png", "R0lG": "image/gif", "UklG": "image/webp"}.get(data[:4], "image/png")


def _to_anthropic(messages):
    """OpenAI-style history -> (system, messages) for an Anthropic /messages request (roles must alternate)."""
    system, out = [], []

    def push(role, blocks):
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m["role"]
        if role == "system":
            system.append(m.get("content") or "")
            continue
        if role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id") or "call_0", "content": m.get("content") or "(no output)"}])
            continue
        blocks = []
        if role == "user":
            for img in m.get("images") or []:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": _image_mime(img), "data": img}})
        if (m.get("content") or "").strip():
            blocks.append({"type": "text", "text": m["content"]})
        for call in m.get("tool_calls") or []:
            args = call["arguments"] if isinstance(call["arguments"], dict) else {}
            blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": args})
        push("assistant" if role == "assistant" else "user", blocks or [{"type": "text", "text": "…"}])
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "…"}]})
    return "\n\n".join(system), out


def _anthropic_stream(endpoint, model, messages, tools, options, cancelled):
    system, msgs = _to_anthropic(messages)
    payload = {"model": endpoint["model"], "messages": msgs, "max_tokens": 8192, "stream": True,
               "temperature": min(1.0, float(options.get("temperature", 0.6)))}
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                             "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}} for t in tools]
    resp = _open_stream(endpoint["base"] + "/messages", payload, endpoint["headers"], model)
    content, thinking = "", ""
    used = {"input": 0, "output": 0, "cached": 0}
    blocks = {}
    buffer = b""
    with resp:
        while True:
            if cancelled():
                break
            chunk = _read_chunk(resp, model)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except ValueError:
                    continue
                kind = event.get("type")
                if kind == "error":
                    err = event.get("error") or {}
                    raise LLMError(err.get("message") or str(err), status=529 if err.get("type") == "overloaded_error" else 0)
                if kind == "message_start":
                    report = (event.get("message") or {}).get("usage") or {}
                    used["input"] = int(report.get("input_tokens") or 0) + int(report.get("cache_creation_input_tokens") or 0)
                    used["cached"] = int(report.get("cache_read_input_tokens") or 0)
                    used["output"] = int(report.get("output_tokens") or 0)
                elif kind == "message_delta" and (event.get("usage") or {}).get("output_tokens"):
                    used["output"] = int(event["usage"]["output_tokens"])
                if kind == "content_block_start":
                    block = event.get("content_block") or {}
                    blocks[event.get("index", 0)] = {"type": block.get("type"), "id": block.get("id"), "name": block.get("name"), "json": ""}
                    if block.get("type") == "text" and block.get("text"):
                        content += block["text"]
                        yield {"type": "token", "content": block["text"]}
                elif kind == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        content += delta["text"]
                        yield {"type": "token", "content": delta["text"]}
                    elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                        thinking += delta["thinking"]
                        yield {"type": "thinking", "content": delta["thinking"]}
                    elif delta.get("type") == "input_json_delta":
                        blocks.setdefault(event.get("index", 0), {"type": "tool_use", "id": "", "name": "", "json": ""})["json"] += delta.get("partial_json", "")
    calls = []
    for index in sorted(blocks):
        block = blocks[index]
        if block["type"] != "tool_use":
            continue
        try:
            args = json.loads(block["json"] or "{}")
        except ValueError:
            args = {"_raw": block["json"]}
        calls.append({"id": block["id"] or "call_%d" % index, "name": block["name"] or "", "arguments": args})
    yield {"type": "end", "content": content, "thinking": thinking, "tool_calls": calls, "usage": used if used["input"] or used["output"] else None}


def complete(prompt, system="", model=None, max_chars=4000):
    """Small non-streaming helper (titles, summaries)."""
    model = model or default_model()
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    text = ""
    for event in chat_stream(model, messages, options={"temperature": 0.3, "num_ctx": 4096}):
        if event["type"] == "end":
            text = event["content"]
    return text[:max_chars].strip()


# ---------- embeddings ----------

def embed(texts):
    model = SETTINGS["embed_model"]
    if not model or not texts:
        return None
    try:
        endpoint = _endpoint(model, "openai")
        if endpoint is not None:
            data = http_json(endpoint["base"] + "/embeddings", {"model": endpoint["model"], "input": texts}, headers=endpoint["headers"], timeout=120)
            return [item["embedding"] for item in data.get("data", [])]
        data = http_json(_ollama() + "/api/embed", {"model": model, "input": texts}, timeout=300)
        return data.get("embeddings")
    except Exception:
        return None


def status():
    started = time.time()
    error = ""
    try:
        models = _local_models(timeout=4)
        online = True
    except Exception as exc:
        models = []
        online = False
        error = str(exc)
        # a cloud model the user chose is a working provider even when the local runtime is down; no need to
        # enumerate every cloud provider here (that made /api/status wait on slow ones)
        chosen = SETTINGS["model"]
        if is_cloud(chosen) and cloud_enabled(split_cloud(chosen)[0]):
            online, error = True, ""
    return {"online": online, "models": models, "error": error, "latency": round(time.time() - started, 3)}
