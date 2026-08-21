"""LLM 统一配置与调用 — 默认 DeepSeek API; 在 settings.json 里配
llm_base_url (如 http://localhost:8001/v1, 本地 vLLM) 和 llm_model 即可切换端点"""
import json, os, urllib.request


def _load_settings() -> dict:
    try:
        with open(os.path.join(os.path.dirname(__file__), "settings.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def llm_config() -> dict:
    """返回规范化的 {base_url(不带 /v1 尾), model, api_key}"""
    s = _load_settings()
    base = (s.get("llm_base_url") or "https://api.deepseek.com").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return {
        "base_url": base,
        "model": s.get("llm_model") or "",
        "api_key": s.get("deepseek_api_key", "") or os.environ.get("DEEPSEEK_API_KEY", "") or "EMPTY",
    }


def chat_request(model_default: str, messages: list, temperature: float = 0.7,
                 max_tokens: int = 2048, timeout: int = 90) -> str:
    """统一 OpenAI 兼容 chat 调用, 返回 assistant 文本 (失败抛异常由调用方兜底)"""
    cfg = llm_config()
    data = json.dumps({
        "model": cfg["model"] or model_default,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(
        f"{cfg['base_url']}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['api_key']}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]
