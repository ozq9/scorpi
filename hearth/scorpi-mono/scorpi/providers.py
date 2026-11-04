"""Catalog of cloud model providers: coding plans / paid API keys, and free tiers.

Model lists rotate often, so nothing here hard-codes a model as the only choice: each provider's live /models list
is read and the `prefer` patterns pick the best match from what is actually offered.
Models from these providers are referenced as "@<provider>/<model id>" so they never clash with Ollama names.

Fields beyond the obvious ones:
  protocol        "openai" (default, /chat/completions) or "anthropic" (/messages) for every model
  messages_url    base URL of an Anthropic-style /messages endpoint for the models matching `messages_models`
  variants        alternative URL sets tried in order when the main one cannot list models (endpoints move)
  skip            models to hide (e.g. ones served only through APIs Scorpi does not speak)
  static          model ids offered when the provider has no /models listing
  prefer_code     patterns for the provider's best coding model (the "Automatic (best coder)" coding model)
  account         label of an account id the URLs need ("{account}" in url / models_url), e.g. Cloudflare's
  category        "coding" (plans and paid keys), "free" (lasting free tier) or "credits" (free credits at signup)
  resets          when daily limits reset: "utc" (default) or "pacific" (midnight US Pacific time, Google)
  limit_scope     "provider" when a daily limit counts for the whole account rather than per model (OpenRouter's free requests)
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
        "prefer_code": [r"qwen3-coder", r"kimi-k2", r"gpt-oss-120b", r"qwen3", r"llama-3\.3-70b"],
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
        "prefer_code": [r"gemini-[\d.]+-flash$", r"gemini-flash-latest", r"flash"],
        "prefer_vision": [r"gemini-[\d.]+-flash$", r"gemini-flash-latest", r"flash"],
        "only": r"flash|gemma",
        "embed": "gemini-embedding-001",
        "resets": "pacific",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "about": "Dozens of free models through one key (models ending in :free). 50 free requests a day.",
        "url": "https://openrouter.ai/api/v1",
        "signup": "https://openrouter.ai/keys",
        "free_only": True,
        "limit_scope": "provider",
        "prefer": [r"^openrouter/free$", r"deepseek.*:free", r"qwen3.*:free", r"llama-4-maverick:free", r"gpt-oss.*:free", r":free"],
        "prefer_code": [r"qwen3-coder.*:free", r"kimi-k2.*:free", r"deepseek-(chat|v3|r1).*:free", r"gpt-oss-120b:free", r"glm.*:free", r"^openrouter/free$", r":free"],
        "prefer_vision": [r"^openrouter/free$", r"(vl|vision|gemma-3|llama-4|mistral-small).*:free", r":free"],
    },
    {
        "id": "ollama-cloud",
        "name": "Ollama Cloud",
        "about": "Big open models (gpt-oss, Qwen3 Coder, Kimi, DeepSeek...) on Ollama's servers. Free account, no card; usage limits reset regularly.",
        "url": "https://ollama.com/v1",
        "signup": "https://ollama.com/settings/keys",
        "prefer": [r"gpt-oss:120b", r"kimi-k2", r"deepseek-v3", r"qwen3-coder", r"glm-4", r"gpt-oss"],
        "prefer_code": [r"qwen3-coder", r"kimi-k2", r"glm-4", r"deepseek-v3", r"gpt-oss:120b", r"gpt-oss"],
        "prefer_vision": [r"qwen3-vl", r"gemma3", r"vl"],
    },
    {
        "id": "cloudflare",
        "name": "Cloudflare Workers AI",
        "about": "Llama, Qwen Coder, gpt-oss and more at the edge. Free every day (10,000 neurons). Needs your account ID and an API token with Workers AI access.",
        "url": "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
        "models_url": "https://api.cloudflare.com/client/v4/accounts/{account}/ai/models/search?task=Text%20Generation&per_page=200",
        "account": "Account ID",
        "signup": "https://dash.cloudflare.com/profile/api-tokens",
        "static": ["@cf/openai/gpt-oss-120b", "@cf/qwen/qwen2.5-coder-32b-instruct", "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                   "@cf/meta/llama-4-scout-17b-16e-instruct"],
        "prefer": [r"gpt-oss-120b", r"llama-4-scout", r"llama-3\.3-70b", r"qwen", r"llama"],
        "prefer_code": [r"qwen.*coder", r"gpt-oss-120b", r"llama-3\.3-70b"],
        "prefer_vision": [r"llama-4-scout", r"vision", r"llava"],
    },
    {
        "id": "ovh",
        "name": "OVHcloud AI Endpoints",
        "about": "European open models (Qwen3 Coder, gpt-oss, Llama, Mistral). Works without signup at 2 requests a minute; a free key lifts it to 400.",
        "url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "anon_url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "anon_models_url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/models",
        "signup": "https://endpoints.ai.cloud.ovh.net",
        "keyless": True,
        "prefer": [r"gpt-oss-120b", r"Qwen3-32B", r"Meta-Llama-3_3-70B", r"Mistral-Small", r"Qwen"],
        "prefer_code": [r"Qwen3-Coder", r"gpt-oss-120b", r"Qwen3-32B", r"Meta-Llama-3_3-70B"],
        "prefer_vision": [r"Qwen2\.5-VL", r"VL", r"Mistral-Small", r"llava"],
    },
    {
        "id": "llm7",
        "name": "LLM7",
        "about": "Small free gateway that works without signup (about 30 requests a minute); a free token from its site raises the limit.",
        "url": "https://api.llm7.io/v1",
        "anon_url": "https://api.llm7.io/v1",
        "anon_models_url": "https://api.llm7.io/v1/models",
        "signup": "https://token.llm7.io",
        "keyless": True,
        "prefer": [r"gpt-oss", r"deepseek", r"qwen", r"mistral", r"gpt"],
        "prefer_code": [r"qwen.*coder", r"codestral", r"deepseek", r"gpt-oss", r"qwen"],
        "prefer_vision": [r"gpt-4o", r"gpt-4\.1", r"vision", r"vl"],
    },
    {
        "id": "vercel",
        "name": "Vercel AI Gateway",
        "about": "Hundreds of models (Claude, GPT, Gemini, Qwen, Kimi...) through one key, with $5 of free credit every 30 days.",
        "url": "https://ai-gateway.vercel.sh/v1",
        "signup": "https://vercel.com/d?to=%2F%5Bteam%5D%2F%7E%2Fai%2Fapi-keys",
        "prefer": [r"qwen3-coder", r"kimi-k2", r"deepseek-v3", r"gpt-oss-120b", r"glm-4", r"gemini-[\d.]+-flash$"],
        "prefer_code": [r"qwen3-coder", r"kimi-k2", r"glm-4\.[5-9]", r"deepseek-v3", r"gpt-oss-120b"],
        "prefer_vision": [r"gemini-[\d.]+-flash$", r"gpt-4\.1-mini", r"llama-4", r"vl"],
    },
    {
        "id": "siliconflow",
        "name": "SiliconFlow",
        "about": "A set of smaller Qwen, GLM and DeepSeek models are free forever (registration asks for a phone number).",
        "url": "https://api.siliconflow.com/v1",
        "signup": "https://cloud.siliconflow.com/account/ak",
        "prefer": [r"Qwen3-8B$", r"GLM-4-9B-0414", r"GLM-4\.1V-9B", r"Qwen2\.5-7B-Instruct$", r"DeepSeek-R1-0528-Qwen3-8B"],
        "prefer_code": [r"Qwen2\.5-Coder-7B", r"Qwen3-8B$", r"GLM-4-9B-0414", r"Qwen2\.5-7B-Instruct$"],
        "prefer_vision": [r"GLM-4\.1V-9B", r"Qwen2\.5-VL-7B", r"VL"],
    },
    {
        "id": "mistral",
        "name": "Mistral",
        "about": "Free experiment tier. Mistral Small/Medium see images. Also free Voxtral speech recognition.",
        "url": "https://api.mistral.ai/v1",
        "signup": "https://console.mistral.ai/api-keys",
        "prefer": [r"mistral-medium-latest", r"mistral-large-latest", r"mistral-small-latest", r"mistral-medium", r"mistral-large"],
        "prefer_code": [r"devstral-medium", r"devstral", r"codestral-latest", r"codestral", r"mistral-medium-latest"],
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
        "prefer_code": [r"qwen-3-coder", r"gpt-oss-120b", r"qwen-3-235b", r"llama"],
        "prefer_vision": [],
    },
    {
        "id": "nvidia",
        "name": "NVIDIA NIM",
        "about": "NVIDIA's hosted models. Your key may only have access to some of the listed models; the newest Nemotron models are the ones usually enabled.",
        "url": "https://integrate.api.nvidia.com/v1",
        "signup": "https://build.nvidia.com",
        # NVIDIA's /models list is the whole catalog, not what a key may call: most old ids answer 404
        # ("Function ... not found for account"). The Nemotron 3.x models are the ones a current key can use.
        "prefer": [r"nemotron-3\.5-lightning", r"nemotron-3\.5-bolt", r"nemotron-3-ultra", r"nemotron-3-super", r"nemotron-3\.5-super-vl", r"deepseek", r"instruct"],
        "prefer_code": [r"qwen3-coder", r"nemotron-3-ultra", r"nemotron-3-super", r"deepseek", r"kimi-k2"],
        "prefer_vision": [r"nemotron-3\.5-super-vl", r"nemotron-3.*vl", r"llama-3\.2-90b-vision", r"vision", r"-vl"],
    },
    {
        "id": "huggingface",
        "name": "Hugging Face",
        "about": "Open models from many inference providers through one token. Free monthly credits.",
        "url": "https://router.huggingface.co/v1",
        "signup": "https://huggingface.co/settings/tokens",
        "prefer": [r"Qwen3.*Instruct", r"Llama-3\.3-70B", r"gpt-oss-120b", r"DeepSeek-V3", r"Instruct"],
        "prefer_code": [r"Qwen3-Coder", r"Kimi-K2", r"DeepSeek-V3", r"gpt-oss-120b", r"Qwen3.*Instruct"],
        "prefer_vision": [r"Qwen.*VL", r"Llama-4", r"gemma-3", r"vision"],
    },
    {
        "id": "zai",
        "name": "Z.ai (GLM)",
        "about": "GLM Flash models are free, including a vision model.",
        "url": "https://api.z.ai/api/paas/v4",
        "signup": "https://z.ai/manage-apikey/apikey-list",
        "prefer": [r"glm-[\d.]+-flash$", r"flash"],
        "prefer_code": [r"glm-[\d.]+-flash$", r"flash"],
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
        "prefer_code": [r"Qwen3-Coder", r"DeepSeek-V3", r"Llama-4-Maverick", r"Qwen3"],
        "prefer_vision": [r"Llama-4-Maverick", r"Llama-4-Scout", r"vision"],
    },
]
for _p in FREE:
    _p["category"] = "free"

# Gateways that give free credit when you sign up (no lasting free tier). Useful to try strong models at no cost;
# Scorpi switches to the next model when the credit runs out.
CREDITS = [
    {
        "id": "nebius",
        "name": "Nebius Token Factory",
        "about": "DeepSeek, Qwen3 Coder, Kimi, GLM and Llama with free credit at signup.",
        "url": "https://api.tokenfactory.nebius.com/v1",
        "signup": "https://tokenfactory.nebius.com",
        "prefer": [r"DeepSeek-V3", r"Qwen3-235B", r"Kimi-K2", r"gpt-oss-120b", r"Llama-3\.3-70B"],
        "prefer_code": [r"Qwen3-Coder-480B", r"Qwen3-Coder", r"Kimi-K2", r"GLM-4\.[5-9]", r"DeepSeek-V3"],
        "prefer_vision": [r"Qwen2\.5-VL", r"gemma-3", r"VL"],
    },
    {
        "id": "together",
        "name": "Together AI",
        "about": "Large catalog of open models (Qwen3 Coder, DeepSeek, Kimi, Llama). Free credit for new accounts.",
        "url": "https://api.together.xyz/v1",
        "signup": "https://api.together.ai/settings/api-keys",
        "prefer": [r"DeepSeek-V3", r"Qwen3-235B", r"Kimi-K2", r"gpt-oss-120b", r"Llama-3\.3-70B"],
        "prefer_code": [r"Qwen3-Coder-480B", r"Qwen3-Coder", r"Kimi-K2", r"DeepSeek-V3"],
        "prefer_vision": [r"Llama-4-Maverick", r"Qwen2\.5-VL", r"vision"],
    },
    {
        "id": "fireworks",
        "name": "Fireworks AI",
        "about": "Fast hosted open models (Qwen3 Coder, Kimi, DeepSeek, GLM) with free starter credit.",
        "url": "https://api.fireworks.ai/inference/v1",
        "signup": "https://fireworks.ai/account/api-keys",
        "prefer": [r"deepseek-v3", r"kimi-k2", r"qwen3-235b", r"gpt-oss-120b", r"llama-v3p3-70b"],
        "prefer_code": [r"qwen3-coder-480b", r"qwen3-coder", r"kimi-k2", r"glm-4p[5-9]", r"deepseek-v3"],
        "prefer_vision": [r"qwen2p5-vl", r"llama4-maverick", r"vision"],
    },
    {
        "id": "deepinfra",
        "name": "DeepInfra",
        "about": "Cheap hosted open models with a small free allowance for new accounts.",
        "url": "https://api.deepinfra.com/v1/openai",
        "signup": "https://deepinfra.com/dash/api_keys",
        "prefer": [r"DeepSeek-V3", r"Qwen3-235B", r"Kimi-K2", r"gpt-oss-120b", r"Llama-3\.3-70B"],
        "prefer_code": [r"Qwen3-Coder-480B", r"Qwen3-Coder", r"Kimi-K2", r"GLM-4\.[5-9]", r"DeepSeek-V3"],
        "prefer_vision": [r"Llama-4-Maverick", r"Qwen2\.5-VL", r"vision"],
    },
    {
        "id": "novita",
        "name": "Novita AI",
        "about": "Open models (DeepSeek, Qwen, GLM, Kimi) with free credit at signup.",
        "url": "https://api.novita.ai/openai/v1",
        "signup": "https://novita.ai/settings/key-management",
        "prefer": [r"deepseek-v3", r"qwen3-235b", r"kimi-k2", r"gpt-oss-120b", r"llama-3\.3-70b"],
        "prefer_code": [r"qwen3-coder", r"kimi-k2", r"glm-4\.[5-9]", r"deepseek-v3"],
        "prefer_vision": [r"qwen2\.5-vl", r"llama-4", r"vl"],
    },
    {
        "id": "hyperbolic",
        "name": "Hyperbolic",
        "about": "Open models at low prices, with free credit for new accounts.",
        "url": "https://api.hyperbolic.xyz/v1",
        "signup": "https://app.hyperbolic.ai/settings",
        "prefer": [r"DeepSeek-V3", r"Qwen3-235B", r"Kimi-K2", r"gpt-oss-120b", r"Llama-3\.3-70B"],
        "prefer_code": [r"Qwen3-Coder", r"Kimi-K2", r"DeepSeek-V3", r"Qwen2\.5-Coder"],
        "prefer_vision": [r"Qwen2\.5-VL", r"VL"],
    },
    {
        "id": "scaleway",
        "name": "Scaleway Generative APIs",
        "about": "European hosting of Qwen3 Coder, gpt-oss, Llama and Mistral. The first million tokens are free.",
        "url": "https://api.scaleway.ai/v1",
        "signup": "https://console.scaleway.com/iam/api-keys",
        "prefer": [r"gpt-oss-120b", r"qwen3-235b", r"llama-3\.3-70b", r"mistral-small"],
        "prefer_code": [r"qwen3-coder", r"devstral", r"gpt-oss-120b", r"qwen3-235b"],
        "prefer_vision": [r"pixtral", r"mistral-small-3", r"gemma-3"],
    },
]
for _p in CREDITS:
    _p["category"] = "credits"

PROVIDERS = CODING + FREE + CREDITS
BY_ID = {p["id"]: p for p in PROVIDERS}

# Services that shut down. A model still set to one of them switches to an available model with this explanation.
RETIRED = {
    "github": {"name": "GitHub Models", "why": "GitHub Models was retired by GitHub on 30 July 2026"},
}


def key_setting(pid):
    return "key_" + pid
