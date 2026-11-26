"""Multi-provider LLM router with per-request budget enforcement and honest cost accounting.

Design guarantees:
1. Daily call & USD budget enforcement lives inside the router at the ACTUAL provider HTTP request layer.
2. Every single provider attempt (success, HTTP error, JSON parse failure, fallback retry) is counted and
   persisted independently via `DailyBudgetGuard` — retries NEVER bypass daily call or cost caps.
3. Unknown provider costs NEVER default to $0.00: `estimate_call_cost_usd` returns `(None, False)` for
   unpriced/variable providers, and `DailyBudgetGuard` deducts a conservative safety reserve
   (`UNKNOWN_COST_RESERVE_USD`) while recording `unknown_cost_calls`.
4. When remaining daily call or USD budget is exhausted, the router makes zero network requests and
   returns `("budget-exhausted")` so callers immediately fall back to deterministic templates.
"""

from dataclasses import dataclass, field
import json
import os
import re
from typing import Any, Callable

from scripts.utils import http_post_json, utc_now_iso

# Conservative per-attempt ESTIMATED reservation when a provider's billing rate is unknown/unverified.
# NOTE: This is an estimated budget guard reservation, NOT a hard provider-side spending cap.
UNKNOWN_COST_RESERVE_USD = 0.005
DEFAULT_MAX_OUTPUT_TOKENS = 450


@dataclass
class DailyBudgetGuard:
    max_calls: int
    max_cost_usd: float
    used_calls: int = 0
    failed_calls: int = 0
    unknown_cost_calls: int = 0
    spent_usd: float = 0.0
    reserved_unknown_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    this_run_calls: int = 0
    this_run_failed_calls: int = 0
    this_run_unknown_cost_calls: int = 0
    this_run_tokens_in: int = 0
    this_run_tokens_out: int = 0
    this_run_cost_usd: float = 0.0
    attempt_log: list[dict[str, Any]] = field(default_factory=list)
    on_attempt_recorded: Callable[[dict[str, Any]], None] | None = None

    @property
    def effective_spent_usd(self) -> float:
        return round(self.spent_usd + self.reserved_unknown_usd, 6)

    @property
    def remaining_calls(self) -> int:
        return max(0, int(self.max_calls) - int(self.used_calls))

    @property
    def remaining_budget_usd(self) -> float:
        return max(0.0, round(float(self.max_cost_usd) - self.effective_spent_usd, 6))

    def can_attempt(self, estimated_max_request_cost_usd: float = 0.0) -> bool:
        est = max(0.0, float(estimated_max_request_cost_usd or 0.0))
        return self.remaining_calls > 0 and round(self.effective_spent_usd + est, 6) <= float(self.max_cost_usd)

    def record_attempt(
        self,
        *,
        provider: str,
        model: str,
        status: str,
        usage: dict[str, int] | None,
        cost_usd: float | None,
        cost_known: bool,
        error: str = "",
    ) -> dict[str, Any]:
        u = usage or {}
        in_tok = int(u.get("prompt_tokens", 0) or 0)
        out_tok = int(u.get("completion_tokens", 0) or 0)
        failed = status != "ok"

        self.used_calls += 1
        self.this_run_calls += 1
        self.tokens_in += in_tok
        self.tokens_out += out_tok
        self.this_run_tokens_in += in_tok
        self.this_run_tokens_out += out_tok

        if failed:
            self.failed_calls += 1
            self.this_run_failed_calls += 1

        if cost_known and cost_usd is not None:
            actual_cost = round(float(cost_usd), 6)
            self.spent_usd = round(self.spent_usd + actual_cost, 6)
            self.this_run_cost_usd = round(self.this_run_cost_usd + actual_cost, 6)
            reserve_applied = 0.0
        else:
            actual_cost = None
            self.unknown_cost_calls += 1
            self.this_run_unknown_cost_calls += 1
            reserve_applied = UNKNOWN_COST_RESERVE_USD
            self.reserved_unknown_usd = round(self.reserved_unknown_usd + reserve_applied, 6)

        rec = {
            "timestamp_utc": utc_now_iso(),
            "provider": provider,
            "model": model,
            "status": status,
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "cost_usd": actual_cost,
            "cost_known": bool(cost_known),
            "unknown_cost_reserve_usd": reserve_applied,
            "error": error[:180] if error else "",
        }
        self.attempt_log.append(rec)
        if self.on_attempt_recorded is not None:
            self.on_attempt_recorded(rec)
        return rec


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


def _call_gemini(prompt: str, api_key: str, model: str, *, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS) -> tuple[str, dict[str, int]]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
            "maxOutputTokens": int(max_output_tokens),
        },
    }
    # retries=0: disable hidden HTTP retries so every attempt is explicitly gated & counted by DailyBudgetGuard
    resp = http_post_json(url, payload, timeout=40, retries=0)
    candidates = resp.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {resp}")
    parts = candidates[0].get("content", {}).get("parts", [])
    meta = resp.get("usageMetadata") or {}
    usage = {
        "prompt_tokens": int(meta.get("promptTokenCount") or 0),
        "completion_tokens": int(meta.get("candidatesTokenCount") or 0),
    }
    return "".join(p.get("text", "") for p in parts), usage


def _call_openai_compat(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    extra_headers: dict[str, str] | None = None,
    *,
    max_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> tuple[str, dict[str, int]]:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    if extra_headers:
        headers.update(extra_headers)
    payload = {
        "model": model,
        "temperature": 0.3,
        "max_tokens": int(max_tokens),
        "messages": [
            {
                "role": "system",
                "content": "You are a concise technical analyst. Always respond with valid JSON only, without markdown wrappers.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    # retries=0: disable hidden HTTP retries so every attempt is explicitly gated & counted by DailyBudgetGuard
    resp = http_post_json(url, payload, headers=headers, timeout=45, retries=0)
    choices = resp.get("choices", [])
    if not choices:
        raise RuntimeError(f"OpenAI-compatible endpoint returned no choices: {resp}")
    u = resp.get("usage") or {}
    usage = {
        "prompt_tokens": int(u.get("prompt_tokens") or 0),
        "completion_tokens": int(u.get("completion_tokens") or 0),
    }
    return choices[0].get("message", {}).get("content", ""), usage


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
                        "is_paid": "1",
                    }
                )
    if os.getenv("GEMINI_API_KEY"):
        providers.append(
            {
                "name": "gemini",
                "key": os.environ["GEMINI_API_KEY"],
                "model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
                "is_paid": "0",
            }
        )
    if os.getenv("GROQ_API_KEY"):
        providers.append(
            {
                "name": "groq",
                "key": os.environ["GROQ_API_KEY"],
                "base_url": "https://api.groq.com/openai/v1",
                "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                "is_paid": "0",
            }
        )
    if os.getenv("OPENROUTER_API_KEY"):
        providers.append(
            {
                "name": "openrouter",
                "key": os.environ["OPENROUTER_API_KEY"],
                "base_url": "https://openrouter.ai/api/v1",
                "model": os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
                "is_paid": "0",
            }
        )
    if os.getenv("SILICONFLOW_API_KEY"):
        providers.append(
            {
                "name": "siliconflow",
                "key": os.environ["SILICONFLOW_API_KEY"],
                "base_url": "https://api.siliconflow.cn/v1",
                "model": os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
                "is_paid": "unknown",
            }
        )
    return providers


def estimate_call_cost_detail(
    provider_name: str, usage: dict[str, int] | None, *, model: str = ""
) -> tuple[float | None, bool]:
    """Return (cost_usd, cost_known). Unknown costs NEVER default to $0.00."""
    u = usage or {}
    in_tok = int(u.get("prompt_tokens", 0) or 0)
    out_tok = int(u.get("completion_tokens", 0) or 0)

    if provider_name.startswith("deepseek"):
        # Conservative upper-bound estimate for deepseek-chat: $0.28/1M in, $0.42/1M out
        if in_tok == 0 and out_tok == 0:
            return None, False
        return round((in_tok * 0.28 + out_tok * 0.42) / 1_000_000, 6), True

    if provider_name == "openrouter" and model.endswith(":free"):
        return 0.0, True

    if provider_name in ("groq", "gemini") and not os.getenv(f"{provider_name.upper()}_PAID_TIER"):
        return 0.0, True

    # Any other provider (e.g., siliconflow, paid openrouter model, custom endpoint): cost is UNKNOWN
    return None, False


def estimate_call_cost_usd(provider_name: str, usage: dict[str, int] | None, *, model: str = "") -> float | None:
    """Backward-compatible wrapper returning float cost when known, or None when unknown (never 0.0 for unknown)."""
    cost, known = estimate_call_cost_detail(provider_name, usage, model=model)
    return cost if known else None


def estimate_max_request_cost_usd(
    provider_name: str, prompt: str, *, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS, model: str = ""
) -> float:
    """Pre-request conservative estimated reservation (input chars/3 + max_output_tokens)."""
    est_in_tokens = max(256, int(len(prompt or "") / 3) + 128)
    est_out_tokens = max(1, int(max_output_tokens))
    cost, known = estimate_call_cost_detail(
        provider_name, {"prompt_tokens": est_in_tokens, "completion_tokens": est_out_tokens}, model=model
    )
    if known and cost is not None:
        return cost
    return UNKNOWN_COST_RESERVE_USD


def generate_json_with_fallback(
    prompt: str,
    *,
    budget_guard: DailyBudgetGuard | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> tuple[dict[str, Any] | None, str, dict[str, int]]:
    """Try configured LLM providers in order with zero hidden HTTP retries & pre-request budget gating."""
    providers = get_available_providers()
    if not providers:
        return None, "deterministic-fallback", {"prompt_tokens": 0, "completion_tokens": 0}

    errors: list[str] = []
    for p in providers:
        name = p["name"]
        model = p.get("model", "")
        pre_est = estimate_max_request_cost_usd(name, prompt, max_output_tokens=max_output_tokens, model=model)
        if budget_guard is not None and not budget_guard.can_attempt(pre_est):
            return None, "budget-exhausted", {"prompt_tokens": 0, "completion_tokens": 0}

        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        try:
            if name == "gemini":
                raw, usage = _call_gemini(prompt, p["key"], model or "gemini-2.0-flash", max_output_tokens=max_output_tokens)
            else:
                extra = {"HTTP-Referer": "https://sysv.cn", "X-Title": "SysV Radar"} if name == "openrouter" else None
                raw, usage = _call_openai_compat(
                    p["base_url"], p["key"], model, prompt, extra_headers=extra, max_tokens=max_output_tokens
                )
        except Exception as err:
            # Request failed or timed out — still consumes an attempt and may have incurred upstream cost!
            cost, known = estimate_call_cost_detail(name, usage, model=model)
            # If request errored with 0 reported usage on a paid/unknown provider, cost is unknown (never $0)
            if p.get("is_paid") != "0":
                cost, known = None, False
            if budget_guard is not None:
                budget_guard.record_attempt(
                    provider=name, model=model, status="http_error", usage=usage, cost_usd=cost, cost_known=known, error=str(err)
                )
            errors.append(f"{name} (http_error): {err}")
            continue

        # HTTP succeeded (upstream tokens were billed!) — now parse JSON
        cost, known = estimate_call_cost_detail(name, usage, model=model)
        try:
            parsed = _extract_json_object(raw)
        except Exception as err:
            if budget_guard is not None:
                budget_guard.record_attempt(
                    provider=name, model=model, status="parse_error", usage=usage, cost_usd=cost, cost_known=known, error=str(err)
                )
            errors.append(f"{name} (parse_error): {err}")
            continue

        if budget_guard is not None:
            budget_guard.record_attempt(
                provider=name, model=model, status="ok", usage=usage, cost_usd=cost, cost_known=known
            )
        return parsed, name, usage

    print("[llm_router] All attempted providers failed or exhausted budget, falling back to deterministic generator:")
    for e in errors:
        print(f"  - {e}")
    return None, "deterministic-fallback", {"prompt_tokens": 0, "completion_tokens": 0}
