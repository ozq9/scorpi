"""Catalog of cloud model providers: coding plans / paid API keys, and free tiers.

Model lists rotate often, so nothing here hard-codes a model as the only choice: each provider's live /models list
is read and the `prefer` patterns pick the best match from what is actually offered.
Models from these providers are referenced as "@<provider>/<model id>" so they never clash with Ollama names.

Fields beyond the obvious ones:
  protocol        "openai" (default, /chat/completions) or "anthropic" (/messages) for every model
  messages_url    base URL of an Anthropic-style /messages endpoint for the models matching `messages_models`
  variants        alternative URL sets tried in order when the main one cannot list models (endpoints move)
  skip            models to hide (e.g. ones served only through APIs Hearth does not speak)
  static          model ids offered when the provider has no /models listing
"""

# Coding plans and pay-as-you-go keys. Listed first: when you pay for a plan it is preferred over free tiers.
CODING = [
    {
        "id": "opencode-go",
        "name": "OpenCode Go",
        "about": "Low-cost monthly plan with the best open coding models (GLM, Kimi, DeepSeek, MiniMax, Qwen...).",
        "url": "https://opencode.ai/zen/go/v1",
        "messages_url": "https://opencode.ai/zen/go/v1",
        "variants": [{"url": "https://opencode.ai/inference/go/openai/v1", "messages_url": "https://opencode.ai/inference/go/anthropic/v1",
                      "models_url": "https://opencode.ai/inference/go/v1/models"}],
        "messages_models": r"minimax|qwen|claude",
        "skip": r"(^|/)gpt-|codex|gemini",
        "signup": "https://opencode.ai/auth",
        "prefer": [r"glm-5", r"kimi-k", r"deepseek-v4-pro", r"deepseek", r"qwen", r"minimax", r"glm"],
        "prefer_vision": [r"kimi-k(2\.[5-9]|3)", r"qwen.*vl", r"glm-[\d.]+v"],
    },
    {
        "id": "opencode-zen",
        "name": "OpenCode Zen",
        "about": "Pay-as-you-go gateway of models tested for coding agents (Claude, GLM, Kimi, Qwen Coder), plus some free ones.",
        "url": "https://opencode.ai/zen/v1",
        "messages_url": "https://opencode.ai/zen/v1",
        "messages_models": r"claude",
        "skip": r"(^|/)gpt-|codex|gemini",
        "signup": "https://opencode.ai/auth",
        "prefer": [r"claude-sonnet", r"glm-5", r"kimi-k", r"qwen3-coder", r"deepseek", r"free"],
        "prefer_vision": [r"claude-sonnet", r"claude", r"kimi-k(2\.[5-9]|3)"],
    },
    {
        "id": "zai-coding",
        "name": "Z.ai GLM Coding Plan",
        "about": "GLM coding subscription. Uses the dedicated coding endpoint so the plan's quota applies.",
        "url": "https://api.z.ai/api/coding/paas/v4",
        "signup": "https://z.ai/manage-apikey/apikey-list",
        "prefer": [r"glm-5(\.\d)?$", r"glm-4\.\d$", r"glm"],
        "prefer_vision": [r"glm-[\d.]+v"],
    },
    {
        "id": "kimi-coding",
        "name": "Kimi Code",
        "about": "Moonshot's Kimi coding membership (model kimi-for-coding).",
        "url": "https://api.kimi.com/coding/v1",
        "messages_url": "https://api.kimi.com/coding/v1",
        "signup": "https://www.kimi.com/code",
        "static": ["kimi-for-coding"],
        "prefer": [r"kimi-for-coding", r"kimi"],
        "prefer_vision": [r"kimi-for-coding"],
    },
    {
        "id": "minimax",
        "name": "MiniMax",
        "about": "MiniMax API or Coding Plan key. Strong agentic coding models.",
        "url": "https://api.minimax.io/v1",
        "messages_url": "https://api.minimax.io/anthropic/v1",
        "signup": "https://platform.minimax.io/user-center/basic-information/interface-key",
        "prefer": [r"minimax-m\d", r"minimax"],
        "prefer_vision": [r"vl|vision"],
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "about": "Very cheap pay-as-you-go API from DeepSeek.",
        "url": "https://api.deepseek.com/v1",
        "signup": "https://platform.deepseek.com/api_keys",
        "prefer": [r"deepseek-chat", r"deepseek-v", r"deepseek-reasoner"],
        "prefer_vision": [],
    },
    {
        "id": "moonshot",
        "name": "Moonshot (Kimi API)",
        "about": "Pay-as-you-go Kimi models from the Moonshot platform.",
        "url": "https://api.moonshot.ai/v1",
        "signup": "https://platform.moonshot.ai/console/api-keys",
        "prefer": [r"kimi-k\d.*(?<!preview)$", r"kimi-k", r"kimi"],
        "prefer_vision": [r"kimi-k(2\.[5-9]|3)", r"vision"],
    },
    {
        "id": "qwen",
        "name": "Alibaba Qwen",
        "about": "Qwen3 Coder, Qwen Max and Qwen VL from Alibaba Cloud Model Studio (international).",
        "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "signup": "https://modelstudio.console.alibabacloud.com/?tab=playground#/api-key",
        "prefer": [r"qwen3-coder-plus", r"qwen3-max", r"qwen-max", r"qwen-plus"],
        "prefer_vision": [r"qwen3-vl-plus", r"qwen-vl-max", r"vl"],
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "about": "GPT models with your OpenAI API key.",
        "url": "https://api.openai.com/v1",
        "skip": r"codex|-pro\b|^o1|realtime|instruct|davinci|babbage|chatgpt|-search|deep-research|gpt-3",
        "signup": "https://platform.openai.com/api-keys",
        "prefer": [r"^gpt-5(\.\d)?$", r"^gpt-5", r"^gpt-4\.1$", r"^gpt-4o$"],
        "prefer_vision": [r"^gpt-5(\.\d)?$", r"^gpt-4\.1$", r"^gpt-4o$"],
        "embed": "text-embedding-3-small",
        "stt": "whisper-1",
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "about": "Claude models with your Anthropic API key.",
        "url": "https://api.anthropic.com/v1",
        "protocol": "anthropic",
        "signup": "https://console.anthropic.com/settings/keys",
        "prefer": [r"claude-sonnet", r"claude-opus", r"claude"],
        "prefer_vision": [r"claude-sonnet", r"claude"],
    },
]
for _p in CODING:
    _p["category"] = "coding"

FREE = [
    {
        "id": "pollinations",
        "name": "Pollinations",
        "about": "Works with no signup at all (slow: about one request every 30 s). A free key from enter.pollinations.ai lifts the limit.",
        "url": "https://gen.pollinations.ai/v1",
        "anon_url": "https://text.pollinations.ai/openai",
        "anon_models_url": "https://text.pollinations.ai/models",
        "signup": "https://enter.pollinations.ai",
        "keyless": True,
        "prefer": [r"^openai$", r"openai", r"gpt", r"mistral", r"deepseek", r"qwen"],
        "prefer_vision": [r"^openai$", r"openai", r"gpt"],
    },
    {
        "id": "groq",
        "name": "Groq",
        "about": "Very fast. Free key, no card. Also offers free Whisper speech recognition.",
        "url": "https://api.groq.com/openai/v1",
        "signup": "https://console.groq.com/keys",
        "prefer": [r"gpt-oss-120b", r"kimi-k2", r"qwen3", r"llama-4-maverick", r"llama-3\.3-70b", r"gpt-oss"],
        "prefer_vision": [r"llama-4-maverick", r"llama-4-scout", r"vision"],
        "stt": "whisper-large-v3-turbo",
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "about": "Strong vision and long context. Free key from Google AI Studio (Flash models are free).",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "signup": "https://aistudio.google.com/apikey",
        "prefer": [r"gemini-[\d.]+-flash$", r"gemini-flash-latest", r"gemini-.*flash(?!.*(lite|image|tts|live|audio))", r"flash"],
        "prefer_vision": [r"gemini-[\d.]+-flash$", r"gemini-flash-latest", r"flash"],
        "only": r"flash|gemma",
        "embed": "gemini-embedding-001",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "about": "Dozens of free models through one key (models ending in :free). 50 free requests a day.",
        "url": "https://openrouter.ai/api/v1",
        "signup": "https://openrouter.ai/keys",
        "free_only": True,
        "prefer": [r"^openrouter/free$", r"deepseek.*:free", r"qwen3.*:free", r"llama-4-maverick:free", r"gpt-oss.*:free", r":free"],
        "prefer_vision": [r"^openrouter/free$", r"(vl|vision|gemma-3|llama-4|mistral-small).*:free", r":free"],
    },
    {
        "id": "github",
        "name": "GitHub Models",
        "about": "GPT-4.1, GPT-4o and more, free with a GitHub token (scope: models:read). Daily limits apply.",
        "url": "https://models.github.ai/inference",
        "models_url": "https://models.github.ai/catalog/models",
        "signup": "https://github.com/settings/personal-access-tokens/new",
        "prefer": [r"openai/gpt-4\.1$", r"openai/gpt-4o$", r"openai/gpt-5", r"gpt-4\.1", r"gpt-4o"],
        "prefer_vision": [r"openai/gpt-4\.1$", r"openai/gpt-4o$", r"gpt-4\.1", r"gpt-4o"],
    },
    {
        "id": "mistral",
        "name": "Mistral",
        "about": "Free experiment tier. Mistral Small/Medium see images. Also free Voxtral speech recognition.",
        "url": "https://api.mistral.ai/v1",
        "signup": "https://console.mistral.ai/api-keys",
        "prefer": [r"mistral-medium-latest", r"mistral-large-latest", r"mistral-small-latest", r"mistral-medium", r"mistral-large"],
        "prefer_vision": [r"mistral-medium-latest", r"mistral-small-latest", r"pixtral", r"mistral-small"],
        "embed": "mistral-embed",
        "stt": "voxtral-mini-latest",
    },
    {
        "id": "cerebras",
        "name": "Cerebras",
        "about": "Extremely fast open models. Free key (newer accounts get free credits).",
        "url": "https://api.cerebras.ai/v1",
        "signup": "https://cloud.cerebras.ai",
        "prefer": [r"gpt-oss-120b", r"qwen-3-235b", r"llama-3\.3-70b", r"qwen", r"llama"],
        "prefer_vision": [],
    },
    {
        "id": "nvidia",
        "name": "NVIDIA NIM",
        "about": "Hundreds of open models (Llama, Qwen, DeepSeek, vision models) with free developer credits.",
        "url": "https://integrate.api.nvidia.com/v1",
        "signup": "https://build.nvidia.com",
        "prefer": [r"llama-3\.3-70b-instruct", r"qwen3", r"deepseek", r"llama-4-maverick", r"instruct"],
        "prefer_vision": [r"llama-4-maverick", r"llama-4-scout", r"llama-3\.2-90b-vision", r"vision", r"-vl"],
    },
    {
        "id": "huggingface",
        "name": "Hugging Face",
        "about": "Open models from many inference providers through one token. Free monthly credits.",
        "url": "https://router.huggingface.co/v1",
        "signup": "https://huggingface.co/settings/tokens",
        "prefer": [r"Qwen3.*Instruct", r"Llama-3\.3-70B", r"gpt-oss-120b", r"DeepSeek-V3", r"Instruct"],
        "prefer_vision": [r"Qwen.*VL", r"Llama-4", r"gemma-3", r"vision"],
    },
    {
        "id": "zai",
        "name": "Z.ai (GLM)",
        "about": "GLM Flash models are free, including a vision model.",
        "url": "https://api.z.ai/api/paas/v4",
        "signup": "https://z.ai/manage-apikey/apikey-list",
        "prefer": [r"glm-[\d.]+-flash$", r"flash"],
        "prefer_vision": [r"glm-[\d.]+v-flash", r"v-flash", r"glm-[\d.]+v"],
        "only": r"flash",
    },
    {
        "id": "cohere",
        "name": "Cohere",
        "about": "Command models including Command A Vision. Free trial key for personal use.",
        "url": "https://api.cohere.ai/compatibility/v1",
        "signup": "https://dashboard.cohere.com/api-keys",
        "prefer": [r"command-a(?!.*vision)", r"command-r-plus", r"command"],
        "prefer_vision": [r"command-a-vision", r"vision"],
        "embed": "embed-v4.0",
    },
    {
        "id": "sambanova",
        "name": "SambaNova",
        "about": "Fast Llama, DeepSeek and Qwen models with a free tier.",
        "url": "https://api.sambanova.ai/v1",
        "signup": "https://cloud.sambanova.ai/apis",
        "prefer": [r"DeepSeek-V3", r"Llama-4-Maverick", r"Meta-Llama-3\.3-70B", r"Qwen3"],
        "prefer_vision": [r"Llama-4-Maverick", r"Llama-4-Scout", r"vision"],
    },
]

PROVIDERS = CODING + FREE
BY_ID = {p["id"]: p for p in PROVIDERS}


def key_setting(pid):
    return "key_" + pid
