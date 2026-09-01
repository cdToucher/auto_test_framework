"""OpenAI 兼容 chat 接口客户端（httpx）。

配置来源（优先级：CLI 参数 > 环境变量 > 默认值）：
  ATK_LLM_BASE_URL  默认智谱开放平台 OpenAI 兼容端点
  ATK_LLM_API_KEY   必填（任何 OpenAI 兼容服务均可：智谱/DeepSeek/OpenAI/本地 vLLM）
  ATK_LLM_MODEL     默认 glm-4.6
API Key 永远只从环境变量读取，不落配置文件。
"""
import os

import httpx

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4.6"


class LlmError(RuntimeError):
    pass


def resolve_config(
    base_url: str | None = None, api_key: str | None = None, model: str | None = None
) -> dict:
    base_url = base_url or os.getenv("ATK_LLM_BASE_URL", DEFAULT_BASE_URL)
    api_key = api_key or os.getenv("ATK_LLM_API_KEY")
    model = model or os.getenv("ATK_LLM_MODEL", DEFAULT_MODEL)
    if not api_key:
        raise LlmError(
            "缺少 LLM API Key：请设置环境变量 ATK_LLM_API_KEY（或用 --api-key 传入）"
        )
    return {"base_url": base_url, "api_key": api_key, "model": model}


def chat(
    cfg: dict,
    messages: list[dict],
    temperature: float = 0.3,
    timeout: float = 180.0,
) -> str:
    """调用 /chat/completions，返回助手回复文本。"""
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise LlmError(f"LLM 请求失败：{e}") from e
    if resp.status_code != 200:
        raise LlmError(f"LLM 返回 HTTP {resp.status_code}：{resp.text[:300]}")
    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        raise LlmError(f"LLM 响应结构异常：{e}") from e
