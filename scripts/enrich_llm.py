"""V2.1 Incremental LLM Enrichment with SHA-256 Hash Cache & Hard Daily Cost Cap.

Guarantees:
1. SHA-256 content-hash invalidation prevents stale copy when pricing/tier changes.
2. Hard daily limits: BOTH `max_daily_llm_enrich` (default 10 calls) AND `max_daily_cost_usd`
   (default $0.05/day) are strictly enforced—even if 70+ hashes invalidate at once, live LLM
   calls never exceed the daily cap; remaining entries immediately receive clean deterministic copy.
3. Logs exact token usage and estimated USD cost into `data/ops_metrics.json`.
"""

import hashlib
import json
import time
from typing import Any

from scripts.llm_router import DailyBudgetGuard, generate_json_with_fallback, get_available_providers
from scripts.utils import DATA_DIR, load_config, load_json, save_json, utc_today


def compute_model_hash(m: dict[str, Any], prompt_version: str) -> str:
    payload = {
        "id": m.get("id"),
        "pricing_type": m.get("pricing_type"),
        "prompt_price_per_m": m.get("prompt_price_per_m"),
        "completion_price_per_m": m.get("completion_price_per_m"),
        "context_length": m.get("context_length"),
        "modality": m.get("modality"),
        "capabilities": m.get("capabilities"),
        "task_category": m.get("task_category"),
        "description": m.get("description"),
        "prompt_version": prompt_version,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def compute_repo_hash(r: dict[str, Any], prompt_version: str) -> str:
    payload = {
        "full_name": r.get("full_name"),
        "category": r.get("category"),
        "language": r.get("language"),
        "license": r.get("license"),
        "license_tier": r.get("license_tier"),
        "docker_status": r.get("docker_status"),
        "description": r.get("description"),
        "prompt_version": prompt_version,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _deterministic_model_enrichment(m: dict[str, Any], cache_hash: str) -> dict[str, Any]:
    ctx_k = int(m["context_length"] / 1000) if m.get("context_length") else 0
    ptype = m.get("pricing_type", "unknown")
    p_in = m.get("prompt_price_per_m")
    p_out = m.get("completion_price_per_m")
    caps = m.get("capabilities") or {}

    if ptype in ("limited_free", "free"):
        summary = (
            f"{m['name']} is listed at $0/1M tokens ({m.get('pricing_label')}) with a {ctx_k}K context window. "
            f"{m.get('rate_limit_note', 'Subject to upstream rate limits.')}"
        )
        best_for = [
            "Low-volume scheduled batch jobs and prototyping under free-tier rate limits",
            "Fallback target inside a multi-provider LLM router",
            "Tool calling tasks" if caps.get("tools") else "General text summarization and extraction",
        ]
        watch_outs = [
            "Zero token price does NOT mean unlimited—respect strict RPM/RPD caps",
            "Upstream providers may retire or re-price free tiers with short notice",
        ]
    elif ptype == "batch":
        summary = (
            f"{m['name']} is an asynchronous batch-only endpoint billed at ${p_in}/1M input and ${p_out}/1M output tokens. "
            f"It trades immediate latency for discounted bulk processing and is NOT suitable for live interactive agents."
        )
        best_for = [
            "Overnight bulk translation, document indexing, or offline dataset labeling",
            "Non-urgent scheduled pipelines that tolerate asynchronous turnaround",
            "Cutting token costs on large static backfills",
        ]
        watch_outs = [
            "Asynchronous batch only—do NOT wire into interactive chat or synchronous agent loops",
            "Verify batch job queue SLA and tiered long-prompt surcharges before bulk runs",
        ]
    elif ptype == "free_router":
        summary = (
            f"{m['name']} routes prompts strictly across active $0 rate-limited models to reduce manual model switching."
        )
        best_for = [
            "Hands-off fallback when individual :free endpoints return HTTP 429",
            "Non-critical background summarization where exact model identity can vary",
            "Testing OpenAI-compatible clients at zero token cost",
        ]
        watch_outs = [
            "Output consistency varies because the underlying model changes dynamically",
            "Still subject to shared OpenRouter free-tier RPM/RPD limits",
        ]
    elif ptype == "variable":
        summary = (
            f"{m['name']} is a dynamically routed endpoint with variable billing (OpenRouter raw price = -1). "
            f"Routing adds no markup, but you pay the actual rate of whichever downstream paid model handles each request."
        )
        best_for = [
            "Mixed-complexity workloads where automatic model routing beats hardcoding a single model",
            "Unified endpoint abstraction across multiple model tiers",
            "Evaluating automated quality-vs-cost routing behavior",
        ]
        watch_outs = [
            "NOT a free model: downstream paid models bill normally per token",
            "Spend can spike unexpectedly if complex prompts route to frontier models",
        ]
    elif ptype == "paid" and p_in is not None and p_out is not None:
        est_10m = round(p_in * 8 + p_out * 2, 2)
        summary = (
            f"{m['name']} is a real-time paid endpoint billed at ${p_in}/1M input and ${p_out}/1M output tokens "
            f"(estimated ${est_10m} for 8M input + 2M output tokens) with a {ctx_k}K context window."
        )
        best_for = [
            "Real-time tool calling & agent loops" if caps.get("tools") else "Real-time text generation and analysis",
            "Strict JSON / structured extraction" if caps.get("structured_outputs") else "General API automation",
            f"Long-context inputs up to {ctx_k}K tokens" if ctx_k >= 128 else "Cost-controlled production API workloads",
        ]
        watch_outs = [
            "Actual task cost also depends on turn count, retries, output length, and cache hit rate",
            "Check tiered long-context price surcharges" if m.get("has_tiered_overrides") else "Cache repeated prompts by content hash to prevent duplicate spend",
        ]
    else:
        summary = (
            f"{m['name']} uses specialized or non-standard billing ({m.get('pricing_label', ptype)}); "
            f"verify exact unit pricing directly on OpenRouter before production use."
        )
        best_for = [
            "Specialized workloads matching its declared modality",
            "Controlled experiments with explicit spend caps",
            "Evaluating preview endpoints before general availability",
        ]
        watch_outs = [
            "Standard per-1M-token text calculators do not apply cleanly here",
            "Confirm live billing units on the upstream provider page",
        ]

    return {
        "source": "deterministic-fallback",
        "cache_hash": cache_hash,
        "enriched_at": utc_today(),
        "summary": summary,
        "best_for": best_for,
        "watch_outs": watch_outs,
    }


def _build_model_prompt(m: dict[str, Any]) -> str:
    caps = m.get("capabilities") or {}
    return (
        "Provide concise engineering decision notes for this LLM API endpoint strictly from the facts below.\n"
        "STRICT RULES:\n"
        "1. NEVER claim 'variable', 'paid', or 'batch' endpoints are free.\n"
        "2. NEVER claim ':free' / 'limited_free' endpoints are unlimited.\n"
        "3. Do NOT invent scores, unverified pricing, or use the words 'benchmark', 'benchmarks', 'benchmarked', or 'runtime tested'.\n"
        'Return valid JSON only with keys: "summary" (2 concise factual sentences), '
        '"best_for" (array of 3 concise strings), "watch_outs" (array of 2 concise strings).\n\n'
        f"Facts:\n"
        f"- Name: {m['name']} ({m['id']})\n"
        f"- Pricing Type: {m['pricing_type']} ({m['pricing_label']})\n"
        f"- Real-time Interactive: {m.get('is_realtime')}\n"
        f"- Input/Output per 1M: {m['prompt_price_per_m']} / {m['completion_price_per_m']}\n"
        f"- Context: {m['context_length']} | Modality: {m.get('modality')}\n"
        f"- Capabilities: tools={caps.get('tools')}, structured_outputs={caps.get('structured_outputs')}, reasoning={caps.get('reasoning')}\n"
        f"- Rate-Limit/Tier Note: {m.get('rate_limit_note')}\n"
        f"- Official Description: {m.get('description', '')}\n"
    )


def _deterministic_repo_enrichment(r: dict[str, Any], cache_hash: str) -> dict[str, Any]:
    return {
        "source": "deterministic-fallback",
        "cache_hash": cache_hash,
        "enriched_at": utc_today(),
        "summary": (
            f"{r['full_name']} is a {r.get('category', 'Self-Hosted Infra')} repository in {r.get('language', 'Multi')} "
            f"({r.get('maintenance_status', 'Active')}, License: {r.get('license', 'Unknown')}). "
            f"Docker root check: {r.get('docker_status', 'Not Verified')}."
        ),
        "best_for": [
            f"Evaluating open-source self-hosted tooling in {r.get('category', 'developer infrastructure')}",
            "Deploying with repository Docker files" if r.get("has_docker") else "Reviewing upstream installation docs for runtime requirements",
            "Tracking active maintenance status and license terms before adoption",
        ],
        "watch_outs": [
            r.get("license_note", f"Verify repository LICENSE ({r.get('license', 'Unknown')}) before commercial deployment"),
            "RAM/GPU requirements are not runtime-tested here—verify official repository documentation before provisioning servers",
        ],
    }


def _valid_enrichment(data: dict[str, Any] | None, pricing_type: str | None = None) -> bool:
    if not data:
        return False
    if not (
        isinstance(data.get("summary"), str)
        and isinstance(data.get("best_for"), list)
        and isinstance(data.get("watch_outs"), list)
    ):
        return False
    all_text = " ".join(
        [data["summary"]] + [str(x) for x in data["best_for"]] + [str(x) for x in data["watch_outs"]]
    ).lower()
    if any(w in all_text for w in ("benchmark", "runtime tested")):
        return False
    s_low = data["summary"].lower()
    if pricing_type in ("variable", "paid", "batch"):
        if "$0" in s_low or "completely free" in s_low or "zero-cost" in s_low:
            return False
    if pricing_type in ("limited_free", "free", "free_router") and "unlimited" in s_low:
        return False
    return True


def _ensure_today_ops_entry(ops: dict[str, Any], today: str) -> dict[str, Any]:
    """Technical pipeline & AI cost ledger ONLY (commercial revenue/traffic live strictly in private/)."""
    daily = ops.setdefault("daily", [])
    entry = next((d for d in daily if d.get("date") == today), None)
    if entry is None:
        entry = {
            "date": today,
            "fetch_success": None,
            "fetch_repos_success": None,
            "publish_success": None,
            "llm_calls": 0,
            "llm_failed_calls": 0,
            "llm_unknown_cost_calls": 0,
            "llm_tokens_in": 0,
            "llm_tokens_out": 0,
            "llm_cost_usd": 0.0,
            "llm_unknown_cost_reserve_usd": 0.0,
            "commercial_data_location": "private/commercial_ledger.json (excluded from public repo)",
        }
        daily.append(entry)
    return entry


def run_enrichment(
    models: list[dict[str, Any]],
    repos: list[dict[str, Any]],
    *,
    max_calls: int,
    max_cost_usd: float,
    prompt_ver: str,
    allow_live_llm: bool = True,
    already_used_calls: int = 0,
    already_spent_usd: float = 0.0,
    already_unknown_reserve_usd: float = 0.0,
    on_attempt_recorded=None,
) -> dict[str, Any]:
    """Enforce true cumulative daily call + USD caps down at llm_router's actual provider request layer."""
    has_live_llm = allow_live_llm and len(get_available_providers()) > 0
    guard = DailyBudgetGuard(
        max_calls=int(max_calls),
        max_cost_usd=float(max_cost_usd),
        used_calls=max(0, int(already_used_calls)),
        spent_usd=max(0.0, float(already_spent_usd)),
        reserved_unknown_usd=max(0.0, float(already_unknown_reserve_usd)),
        on_attempt_recorded=on_attempt_recorded,
    )
    invalidated_count = 0

    for m in models:
        c_hash = compute_model_hash(m, prompt_ver)
        existing = m.get("enrichment") or {}
        existing_valid = _valid_enrichment(existing, pricing_type=m.get("pricing_type"))
        hash_matches = existing.get("cache_hash") == c_hash and existing_valid
        already_llm = hash_matches and existing.get("source") not in (None, "deterministic-fallback")
        if already_llm:
            continue
        if existing and not hash_matches:
            invalidated_count += 1

        if has_live_llm and guard.can_attempt() and m.get("status") == "active":
            parsed, provider, _usage = generate_json_with_fallback(_build_model_prompt(m), budget_guard=guard)
            if _valid_enrichment(parsed, pricing_type=m.get("pricing_type")):
                parsed["source"] = provider
                parsed["cache_hash"] = c_hash
                parsed["enriched_at"] = utc_today()
                m["enrichment"] = parsed
                time.sleep(0.8)
                continue

        if not hash_matches or not existing:
            m["enrichment"] = _deterministic_model_enrichment(m, c_hash)

    for r in repos:
        c_hash = compute_repo_hash(r, prompt_ver)
        existing = r.get("enrichment") or {}
        hash_matches = existing.get("cache_hash") == c_hash
        already_llm = hash_matches and existing.get("source") not in (None, "deterministic-fallback")
        if already_llm:
            continue
        if existing and not hash_matches:
            invalidated_count += 1
        if not hash_matches or not existing:
            r["enrichment"] = _deterministic_repo_enrichment(r, c_hash)

    return {
        "llm_calls": guard.this_run_calls,
        "llm_failed_calls": guard.this_run_failed_calls,
        "llm_unknown_cost_calls": guard.this_run_unknown_cost_calls,
        "llm_tokens_in": guard.this_run_tokens_in,
        "llm_tokens_out": guard.this_run_tokens_out,
        "llm_cost_usd": guard.this_run_cost_usd,
        "llm_unknown_cost_reserve_usd": round(guard.reserved_unknown_usd - max(0.0, float(already_unknown_reserve_usd)), 6),
        "attempts": guard.attempt_log,
        "cache_invalidated": invalidated_count,
    }


def main() -> None:
    cfg = load_config()
    pipe = cfg["pipeline"]
    max_calls = int(pipe.get("max_daily_llm_enrich", 10))
    max_cost_usd = float(pipe.get("max_daily_cost_usd", 0.05))
    prompt_ver = str(pipe.get("prompt_version", "v2.1"))

    models_path = DATA_DIR / "models.json"
    repos_path = DATA_DIR / "repos.json"
    ops_path = DATA_DIR / "ops_metrics.json"
    models = load_json(models_path, default=[])
    repos = load_json(repos_path, default=[])

    ops = load_json(ops_path, default={"daily": []})
    today = utc_today()
    entry = _ensure_today_ops_entry(ops, today)
    already_calls = int(entry.get("llm_calls") or 0)
    already_cost = float(entry.get("llm_cost_usd") or 0.0)
    already_reserve = float(entry.get("llm_unknown_cost_reserve_usd") or 0.0)

    def _persist_attempt(rec: dict[str, Any]) -> None:
        entry["llm_calls"] = int(entry.get("llm_calls") or 0) + 1
        if rec.get("status") != "ok":
            entry["llm_failed_calls"] = int(entry.get("llm_failed_calls") or 0) + 1
        if not rec.get("cost_known"):
            entry["llm_unknown_cost_calls"] = int(entry.get("llm_unknown_cost_calls") or 0) + 1
            entry["llm_unknown_cost_reserve_usd"] = round(
                float(entry.get("llm_unknown_cost_reserve_usd") or 0.0) + float(rec.get("unknown_cost_reserve_usd") or 0.0), 6
            )
        else:
            entry["llm_cost_usd"] = round(float(entry.get("llm_cost_usd") or 0.0) + float(rec.get("cost_usd") or 0.0), 6)
        entry["llm_tokens_in"] = int(entry.get("llm_tokens_in") or 0) + int(rec.get("prompt_tokens") or 0)
        entry["llm_tokens_out"] = int(entry.get("llm_tokens_out") or 0) + int(rec.get("completion_tokens") or 0)
        ops["daily"] = sorted(ops["daily"], key=lambda x: x["date"], reverse=True)[:365]
        save_json(ops_path, ops)

    stats = run_enrichment(
        models,
        repos,
        max_calls=max_calls,
        max_cost_usd=max_cost_usd,
        prompt_ver=prompt_ver,
        already_used_calls=already_calls,
        already_spent_usd=already_cost,
        already_unknown_reserve_usd=already_reserve,
        on_attempt_recorded=_persist_attempt,
    )

    save_json(models_path, models)
    save_json(repos_path, repos)
    ops["daily"] = sorted(ops["daily"], key=lambda x: x["date"], reverse=True)[:365]
    save_json(ops_path, ops)

    print(
        f"[enrich_llm] Done: this_run_attempts={stats['llm_calls']} (failed={stats['llm_failed_calls']}, unknown_cost={stats['llm_unknown_cost_calls']}), "
        f"today_total_calls={entry['llm_calls']}/{max_calls}, "
        f"today_known_cost=${entry['llm_cost_usd']:.6f} + unknown_reserve=${entry.get('llm_unknown_cost_reserve_usd', 0.0):.6f} (cap=${max_cost_usd:.4f}), "
        f"cache_invalidated={stats['cache_invalidated']}, models={len(models)}, repos={len(repos)}."
    )


if __name__ == "__main__":
    main()
