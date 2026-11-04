"""Paths and persistent settings."""

import copy
import json
import os
import platform
import secrets
import threading

from .providers import PROVIDERS, RETIRED, key_setting

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_DIR = os.path.join(APP_DIR, "public")
DATA_DIR = os.path.abspath(os.environ.get("SCORPI_DATA") or os.environ.get("SCORP_DATA") or os.environ.get("HEARTH_DATA") or os.path.join(APP_DIR, "data"))  # the others: the app's old names
WORKSPACE = os.path.join(DATA_DIR, "workspace")
UPLOADS = os.path.join(DATA_DIR, "uploads")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
LEGACY_CONFIG = os.path.join(APP_DIR, "config.json")
IS_WINDOWS = platform.system() == "Windows"

for _path in (DATA_DIR, WORKSPACE, UPLOADS):
    os.makedirs(_path, exist_ok=True)

# Keys whose values never leave the server.
SECRET_KEYS = {"openai_key", "fish_api_key", "secret", "pin", "ha_token", "github_token"} | {key_setting(p["id"]) for p in PROVIDERS} \
    | {key_setting(pid) for pid in RETIRED}

DEFAULTS = {
    # language model
    "provider": "ollama",  # ollama | openai
    "ollama_url": os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
    "openai_url": "",  # any OpenAI-compatible endpoint (LM Studio, Groq, OpenRouter, ...)
    "openai_key": "",
    "model": "",
    "vision_model": "",
    "embed_model": "nomic-embed-text",
    "temperature": 0.6,
    "num_ctx": 16384,
    "max_steps": 24,
    "system_prompt": "",
    "response_style": "visual",  # visual (answers go on the live canvas, the reply is what is said) | text
    # free cloud providers (keys are added below as key_<id>)
    "cloud_anon": True,  # allow keyless Pollinations when nothing else is available
    "cloud_fallback": True,  # switch to another model when one is rate-limited or offline
    "auto_resume": True,  # work that paused because every model was at its limit continues when the first one resets
    "local_fallback": True,  # when every cloud model is out of requests, carry on with a local (Ollama) model
    "learn_models": True,  # automatic coding picks favour the models that work best here (scorpi/scores.py)
    "step_routing": "smart",  # off | smart (side work on a fast model, stronger model when stuck) | thrifty (start fast, escalate)
    "fast_model": "",  # quick model for side work ("" = automatic)
    "private_mode": False,  # everything only goes to providers that don't train on it, or local models (scorpi/privacy.py)
    "privacy_trusted": [],  # providers the user allows for private work anyway (e.g. after opting out of training there)
    "redact_secrets": True,  # API keys, tokens and passwords are replaced by placeholders before reaching a cloud model
    "cloud_urls": {},  # provider id -> base URL override
    "cloud_extra_models": {},  # provider id -> "model-a, model-b" offered in addition to the live list
    "cloud_accounts": {},  # provider id -> account id its URLs need (Cloudflare)
    "code_model": "",  # model for the Code skill and coder sub-agents ("" = the chat model, "auto" = the best coder on offer)
    "expert_model": "",  # second opinions and code reviews ("" = the best coder of another provider than the one working)
    "coder_backend": "auto",  # auto (OpenCode when installed) | scorpi (Scorpi's own tools)
    "checkpoints": True,  # save a project's files before and after every agent run (scorpi/gitops.py), to look at or undo
    "autonomy": "auto",  # ask | auto | full: what agents may do without asking (scorpi/safety.py)
    "hooks": [],  # the user's commands at fixed moments of an agent's work (scorpi/hooks.py)
    "github_token": "",  # the GitHub account the Code area and the coder use (scorpi/github.py)
    "user_name": "",
    # voice
    "tts_engine": "edge",  # edge | system | fish | browser
    "tts_voice": "en-US-AvaMultilingualNeural",
    "tts_rate": 0,
    "fish_api_key": os.environ.get("FISH_API_KEY", ""),
    "fish_voice": "",
    "fish_model": "s1",
    "stt_mode": "auto",  # auto (device recognizer first, then host) | device | host
    "stt_host": "auto",  # auto (cloud Whisper if a Groq/Mistral key is set, else local) | cloud | local
    "stt_model": "base",
    "stt_language": "auto",
    # media
    "image_provider": "auto",  # auto | sd | pollinations
    "sd_url": "http://127.0.0.1:7860",
    # notifications
    "ntfy_topic": "",
    "ntfy_server": "https://ntfy.sh",
    "desktop_notifications": True,
    # security
    "require_pin": True,
    "pin": "",
    "secret": "",
    "allowed_paths": [],
    "blocked_paths": [],
    "tool_policy": {},
    # tools
    "tool_mode": "auto",  # auto | all | manual
    "tool_groups": ["files", "docs", "code", "web", "data", "media", "computer", "system", "maps", "agenda", "records", "knowledge", "agents", "learning"],
    "mcp_servers": [],
    "skills": [],
    "prices": {},  # model ref -> "input/output" US$ per million tokens, set on the Usage page
    # memory
    "memory_enabled": True,
    # integrations
    "ha_url": "",
    "ha_token": "",
    # Discord Rich Presence ("Playing Scorpi": project, time, model)
    "discord_presence": False,
    "discord_client_id": "",  # the Application ID of the user's own Discord application (named e.g. "Scorpi")
    "discord_show_project": True,  # show which project / conversation you are on, not just "Chatting"
    # updates from scorpi.dev (scorpi/updates.py): an installed copy installs a newer version by itself
    "auto_update": True,
    "update_url": "",  # empty: the site in scorpi/updates.py
    "gallery_url": "",  # empty: the same site
    "gallery_mine": [],  # what this Scorpi shared to the gallery, with the keys that take it down again
    # network
    "port": int(os.environ.get("PORT", "8000")),
    "https_port": int(os.environ.get("HTTPS_PORT", "8443")),
}
DEFAULTS.update({key_setting(p["id"]): "" for p in PROVIDERS})


class Settings:
    def __init__(self):
        self._lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULTS)
        self._load()

    def _load(self):
        stored = {}
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
                    stored = json.load(fh)
            except Exception:
                stored = {}
        elif os.path.exists(LEGACY_CONFIG):
            # Carry over the Fish Audio settings of the previous version.
            try:
                with open(LEGACY_CONFIG, "r", encoding="utf-8") as fh:
                    legacy = json.load(fh)
                for key in ("fish_api_key", "fish_voice", "fish_model", "stt_model"):
                    if legacy.get(key):
                        stored[key] = legacy[key]
            except Exception:
                pass
        for key, value in stored.items():
            if value is not None:
                self.data[key] = value
        changed = False
        for pid in RETIRED:  # keys of services that shut down are of no use any more
            if self.data.pop(key_setting(pid), None) is not None:
                changed = True
        if "stt_mode" not in stored and stored.get("stt_engine") == "browser":
            self.data["stt_mode"] = "device"
        self.data.pop("stt_engine", None)
        if not self.data.get("secret"):
            self.data["secret"] = secrets.token_hex(32)
            changed = True
        if not self.data.get("pin"):
            self.data["pin"] = "%06d" % secrets.randbelow(1_000_000)
            changed = True
        if changed or not os.path.exists(SETTINGS_PATH):
            self.save()

    def save(self):
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
        os.replace(tmp, SETTINGS_PATH)

    def get(self, key, default=None):
        value = self.data.get(key, default)
        return default if value is None else value

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def update(self, patch):
        with self._lock:
            for key, value in patch.items():
                if key not in DEFAULTS:
                    continue
                if key in SECRET_KEYS and isinstance(value, str) and value.startswith("••"):
                    continue  # masked value echoed back by the client
                default = DEFAULTS[key]
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int) and not isinstance(default, bool):
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        continue
                elif isinstance(default, float):
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        continue
                elif isinstance(default, str):
                    value = "" if value is None else str(value).strip()
                elif isinstance(default, list):
                    if not isinstance(value, list):
                        continue
                elif isinstance(default, dict):
                    if not isinstance(value, dict):
                        continue
                    value = {str(k): str(v).strip() for k, v in value.items() if str(v).strip()}
                self.data[key] = value
            self.save()

    def public(self):
        out = {}
        for key, value in self.data.items():
            if key in ("secret", "gallery_mine"):  # gallery_mine holds the keys that take shared items down
                continue
            if key in SECRET_KEYS:
                out[key] = ("••••" + value[-4:]) if value and key != "pin" else ""
                out[key + "_set"] = bool(value)
            else:
                out[key] = value
        return out


SETTINGS = Settings()
