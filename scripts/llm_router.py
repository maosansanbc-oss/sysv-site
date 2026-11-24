"""Multi-provider free-tier LLM router with automatic fallback.

Supported environment variables (configure 1 or more in GitHub Secrets):
- DEEPSEEK_API_KEY     (DeepSeek official API, OpenAI-compatible)
- GEMINI_API_KEY       (Google AI Studio free tier)
- GROQ_API_KEY         (Groq free tier, OpenAI-compatible)
- OPENROUTER_API_KEY   (OpenRouter :free models, OpenAI-compatible)
- SILICONFLOW_API_KEY  (SiliconFlow free/low-cost models, OpenAI-compatible)
- CUSTOM_LLM_BASE_URL + CUSTOM_LLM_API_KEY + CUSTOM_LLM_MODEL (any OpenAI-compatible endpoint)
"""

import json
import os
import re
from typing import Any

from scripts.utils import http_post_json


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object")
    return data


def _call_gemini(prompt: str, api_key: str) -> str:
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }
    resp = http_post_json(url, payload, timeout=40)
    candidates = resp.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {resp}")
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def _call_openai_compat(base_url: str, api_key: str, model: str, prompt: str, extra_headers: dict[str, str] | None = None) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    if extra_headers:
        headers.update(extra_headers)
    payload = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {
                "role": "system",
                "content": "You are a concise technical analyst. Always respond with valid JSON only, without markdown wrappers.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    resp = http_post_json(url, payload, headers=headers, timeout=45)
    choices = resp.get("choices", [])
    if not choices:
        raise RuntimeError(f"OpenAI-compatible endpoint returned no choices: {resp}")
    return choices[0].get("message", {}).get("content", "")


def get_available_providers() -> list[dict[str, str]]:
    providers: list[dict[str, str]] = []
    if os.getenv("DEEPSEEK_API_KEY"):
        for idx, k in enumerate(os.environ["DEEPSEEK_API_KEY"].split(",")):
            k = k.strip()
            if k:
                providers.append(
                    {
                        "name": f"deepseek-{idx + 1}" if idx > 0 else "deepseek",
                        "key": k,
                        "base_url": "https://api.deepseek.com/v1",
                        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                    }
                )
    if os.getenv("GEMINI_API_KEY"):
        providers.append({"name": "gemini", "key": os.environ["GEMINI_API_KEY"]})
    if os.getenv("GROQ_API_KEY"):
        providers.append(
            {
                "name": "groq",
                "key": os.environ["GROQ_API_KEY"],
                "base_url": "https://api.groq.com/openai/v1",
                "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            }
        )
    if os.getenv("OPENROUTER_API_KEY"):
        providers.append(
            {
                "name": "openrouter",
                "key": os.environ["OPENROUTER_API_KEY"],
                "base_url": "https://openrouter.ai/api/v1",
                "model": os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
            }
        )
    if os.getenv("SILICONFLOW_API_KEY"):
        providers.append(
            {
                "name": "siliconflow",
                "key": os.environ["SILICONFLOW_API_KEY"],
                "base_url": "https://api.siliconflow.cn/v1",
                "model": os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
            }
        )
    if os.getenv("CUSTOM_LLM_BASE_URL") and os.getenv("CUSTOM_LLM_API_KEY"):
        providers.append(
            {
                "name": "custom",
                "key": os.environ["CUSTOM_LLM_API_KEY"],
                "base_url": os.environ["CUSTOM_LLM_BASE_URL"],
                "model": os.getenv("CUSTOM_LLM_MODEL", "gpt-4o-mini"),
            }
        )
    return providers


def generate_json_with_fallback(prompt: str) -> tuple[dict[str, Any] | None, str]:
    """Try each configured free LLM provider in order. Returns (parsed_json, provider_name)."""
    providers = get_available_providers()
    if not providers:
        return None, "deterministic-fallback"

    errors: list[str] = []
    for p in providers:
        name = p["name"]
        try:
            if name == "gemini":
                raw = _call_gemini(prompt, p["key"])
            else:
                extra = {"HTTP-Referer": "https://sysv.cn", "X-Title": "SysV Radar"} if name == "openrouter" else None
                raw = _call_openai_compat(p["base_url"], p["key"], p["model"], prompt, extra_headers=extra)
            parsed = _extract_json_object(raw)
            return parsed, name
        except Exception as err:
            errors.append(f"{name}: {err}")

    print("[llm_router] All configured providers failed, falling back to deterministic generator:")
    for e in errors:
        print(f"  - {e}")
    return None, "deterministic-fallback"
