"""V2 Incremental LLM Enrichment with SHA-256 Content-Hash Cache Invalidation.

Fixes V1 BUG 03:
- Computes deterministic SHA-256 hash over pricing_type, pricing numbers, context_length,
  modality, description, and prompt_version.
- Automatically invalidates and re-enriches whenever pricing tier or specs change
  (e.g., Free -> Paid), preventing stale/contradictory AI copy.
- Strictly separates deterministic factual metrics (computed in Python) from semantic
  analysis (LLM), forbidding the LLM from inventing or misstating prices.
"""

import hashlib
import json
import time
from typing import Any

from scripts.llm_router import generate_json_with_fallback, get_available_providers
from scripts.utils import DATA_DIR, load_config, load_json, save_json, utc_today


def compute_model_hash(m: dict[str, Any], prompt_version: str) -> str:
    payload = {
        "id": m.get("id"),
        "pricing_type": m.get("pricing_type"),
        "prompt_price_per_m": m.get("prompt_price_per_m"),
        "completion_price_per_m": m.get("completion_price_per_m"),
        "context_length": m.get("context_length"),
        "modality": m.get("modality"),
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
        "deployment_tier": r.get("deployment_tier"),
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

    if ptype == "free":
        summary = (
            f"{m['name']} is a verified zero-cost ($0/1M tokens) text endpoint with a {ctx_k}K context window, "
            f"well suited for scheduled batch automation and low-budget prototyping."
        )
        best_for = [
            "Zero-cost scheduled batch jobs and daily content/data extraction",
            "Fallback node inside a multi-provider free-tier LLM router",
            "Early MVP validation before committing to paid token budgets",
        ]
        watch_outs = [
            "Free endpoints enforce strict requests-per-minute (RPM) and daily caps",
            "Upstream providers may retire or re-price free tiers with short notice",
        ]
    elif ptype == "free_router":
        summary = (
            f"{m['name']} automatically routes prompts across currently active zero-cost ($0) models, "
            f"reducing manual maintenance when individual free endpoints hit rate limits."
        )
        best_for = [
            "Hands-off fallback routing when specific free models are throttled",
            "Non-critical background summarization where model identity can vary",
            "Zero-budget testing of OpenAI-compatible client pipelines",
        ]
        watch_outs = [
            "Underlying routed model changes dynamically so output style can vary",
            "Subject to shared free-tier concurrency limits",
        ]
    elif ptype == "variable":
        summary = (
            f"{m['name']} is a dynamic routing endpoint with variable billing—there is no fixed token rate; "
            f"you pay the actual rate of whichever downstream model handles each request."
        )
        best_for = [
            "Workloads where automatic quality-vs-cost model selection beats hardcoding one model",
            "Mixed-complexity agent loops combining simple routing with hard coding steps",
            "Simplifying API integration behind a single unified endpoint ID",
        ]
        watch_outs = [
            "NOT a free model: routing has no markup, but downstream paid models are billed normally",
            "Monthly spend can fluctuate unpredictably if hard prompts route to frontier models",
        ]
    elif ptype == "paid" and p_in is not None and p_out is not None:
        est_10m = round(p_in * 8 + p_out * 2, 2)
        summary = (
            f"{m['name']} is a paid API endpoint billed at ${p_in}/1M input tokens and ${p_out}/1M output tokens "
            f"(~${est_10m} for 8M input + 2M output tokens) with a {ctx_k}K context window."
        )
        best_for = [
            "Production workloads needing predictable throughput and paid SLA limits",
            f"Large-document or codebase tasks up to {ctx_k}K tokens" if ctx_k >= 128 else "Structured extraction, classification, and API automation",
            "Cost-controlled pipelines paired with prompt caching and response caching",
        ]
        watch_outs = [
            "Output tokens are typically more expensive than input tokens—cap max_tokens carefully",
            "Always cache repeated extractions by content hash to avoid duplicate API spend",
        ]
    else:
        summary = (
            f"{m['name']} uses specialized or non-standard billing ({m.get('pricing_label', ptype)}); "
            f"verify exact unit pricing on the upstream provider page before production use."
        )
        best_for = [
            "Specialized non-standard or preview workloads matching its declared modality",
            "Experimental testing with strict per-request budget caps",
            "Evaluating emerging capabilities prior to general availability",
        ]
        watch_outs = [
            "Standard per-million-token text calculators do not apply cleanly to this endpoint",
            "Confirm current billing units directly in the OpenRouter model specification",
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
    return (
        "Provide concise engineering decision guidance for this LLM API endpoint strictly from the facts below. "
        "CRITICAL RULES:\n"
        "1. NEVER claim a 'variable' or 'paid' endpoint is free.\n"
        "2. Do NOT invent benchmark scores, rate limits, or unverified pricing.\n"
        "3. Focus on practical task suitability (coding, bulk translation, batch extraction, agent loops) and engineering trade-offs.\n"
        'Return valid JSON only with keys: "summary" (2 concise factual sentences), '
        '"best_for" (array of 3 concise strings), "watch_outs" (array of 2 concise strings).\n\n'
        f"Facts:\n"
        f"- Name: {m['name']} ({m['id']})\n"
        f"- Provider: {m['provider']}\n"
        f"- Pricing Classification: {m['pricing_type']} ({m['pricing_label']})\n"
        f"- Input Price per 1M tokens: {m['prompt_price_per_m']}\n"
        f"- Output Price per 1M tokens: {m['completion_price_per_m']}\n"
        f"- Context Length: {m['context_length']} tokens\n"
        f"- Modality: {m.get('modality', 'text->text')}\n"
        f"- Task Category: {m.get('task_category', 'general')}\n"
        f"- Official Description: {m.get('description', '')}\n"
    )


def _deterministic_repo_enrichment(r: dict[str, Any], cache_hash: str) -> dict[str, Any]:
    lic_note = (
        f"Permissive {r.get('license', 'Unknown')} license suitable for most commercial self-hosting"
        if r.get("commercial_friendly")
        else f"Licensed under {r.get('license', 'Unknown')} — review commercial/copyleft obligations before hosted use"
    )
    return {
        "source": "deterministic-fallback",
        "cache_hash": cache_hash,
        "enriched_at": utc_today(),
        "summary": (
            f"{r['full_name']} is a {r.get('category', 'Self-Hosted Infra')} project written in {r.get('language', 'Multi')} "
            f"with {r.get('stars', 0):,} stars ({r.get('maintenance_status', 'Active')}), targeting {r.get('deployment_tier', 'VPS')} deployments."
        ),
        "best_for": [
            f"Replacing recurring SaaS bills in {r.get('category', 'developer infrastructure')} workflows",
            f"Deploying on {r.get('deployment_tier', 'self-hosted VPS')} setups",
            "Integrating into automated zero-or-low-cost developer pipelines",
        ],
        "watch_outs": [
            lic_note,
            "Pin container tags or release versions before running unattended cron/production traffic",
        ],
    }


def _build_repo_prompt(r: dict[str, Any]) -> str:
    return (
        "Provide concise self-hosting and deployment decision notes for this open-source infrastructure repo strictly from the facts below. "
        'Return valid JSON only with keys: "summary" (2 concise sentences), '
        '"best_for" (array of 3 concise strings), "watch_outs" (array of 2 concise strings).\n\n'
        f"Facts:\n"
        f"- Repo: {r['full_name']} | Category: {r.get('category')}\n"
        f"- Language: {r.get('language')} | Stars: {r.get('stars')} | License: {r.get('license')} (commercial_friendly={r.get('commercial_friendly')})\n"
        f"- Deployment Tier: {r.get('deployment_tier')} | Docker Signal: {r.get('has_docker')} | Maintenance: {r.get('maintenance_status')}\n"
        f"- Description: {r.get('description')}\n"
    )


def _valid_enrichment(data: dict[str, Any] | None, pricing_type: str | None = None) -> bool:
    if not data:
        return False
    if not (
        isinstance(data.get("summary"), str)
        and isinstance(data.get("best_for"), list)
        and isinstance(data.get("watch_outs"), list)
    ):
        return False
    # Guardrail: reject LLM output that falsely calls a variable/paid endpoint free
    if pricing_type in ("variable", "paid"):
        s_low = data["summary"].lower()
        if "$0" in s_low or "completely free" in s_low or "zero-cost" in s_low:
            return False
    return True


def main() -> None:
    cfg = load_config()
    max_calls = int(cfg["pipeline"].get("max_daily_llm_enrich", 10))
    prompt_ver = str(cfg["pipeline"].get("prompt_version", "v2.0"))
    has_live_llm = len(get_available_providers()) > 0

    models_path = DATA_DIR / "models.json"
    repos_path = DATA_DIR / "repos.json"
    models = load_json(models_path, default=[])
    repos = load_json(repos_path, default=[])

    llm_calls_used = 0
    invalidated_count = 0

    for m in models:
        c_hash = compute_model_hash(m, prompt_ver)
        existing = m.get("enrichment") or {}
        hash_matches = existing.get("cache_hash") == c_hash
        already_llm = hash_matches and existing.get("source") not in (None, "deterministic-fallback")
        if already_llm:
            continue
        if existing and not hash_matches:
            invalidated_count += 1

        if has_live_llm and llm_calls_used < max_calls:
            parsed, provider = generate_json_with_fallback(_build_model_prompt(m))
            if _valid_enrichment(parsed, pricing_type=m.get("pricing_type")):
                parsed["source"] = provider
                parsed["cache_hash"] = c_hash
                parsed["enriched_at"] = utc_today()
                m["enrichment"] = parsed
                llm_calls_used += 1
                time.sleep(1.0)
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

        if has_live_llm and llm_calls_used < max_calls:
            parsed, provider = generate_json_with_fallback(_build_repo_prompt(r))
            if _valid_enrichment(parsed):
                parsed["source"] = provider
                parsed["cache_hash"] = c_hash
                parsed["enriched_at"] = utc_today()
                r["enrichment"] = parsed
                llm_calls_used += 1
                time.sleep(1.0)
                continue

        if not hash_matches or not existing:
            r["enrichment"] = _deterministic_repo_enrichment(r, c_hash)

    save_json(models_path, models)
    save_json(repos_path, repos)
    print(
        f"[enrich_llm] Done: live_llm_calls={llm_calls_used}, cache_invalidated={invalidated_count}, "
        f"models={len(models)}, repos={len(repos)}."
    )


if __name__ == "__main__":
    main()
