"""Incrementally enrich models & repos with structured insights using free LLM APIs.

Rules to avoid free-tier quota exhaustion and Google thin-content penalties:
1. Never re-enrich entries that already have valid LLM enrichment.
2. Cap LLM calls per daily run via `pipeline.max_daily_llm_enrich`.
3. Provide rich, data-backed deterministic fallback when no API key is set yet.
"""

import time
from typing import Any

from scripts.llm_router import generate_json_with_fallback, get_available_providers
from scripts.utils import DATA_DIR, load_config, load_json, save_json, utc_today


def _deterministic_model_enrichment(m: dict[str, Any]) -> dict[str, Any]:
    ctx_k = int(m["context_length"] / 1000) if m.get("context_length") else 0
    is_free = bool(m.get("is_free"))
    p_in = float(m.get("prompt_price_per_m", 0.0))
    p_out = float(m.get("completion_price_per_m", 0.0))

    if is_free:
        tier_summary = (
            f"{m['name']} is currently listed at $0 per million tokens, making it practical for "
            f"scheduled cron pipelines, prototyping, and low-budget batch processing."
        )
        best_for = [
            "Zero-budget batch summarization and scheduled automation jobs",
            "Rapid API prototyping before committing to paid token quotas",
            "Fallback endpoint inside multi-provider LLM routers",
        ]
        watch_outs = [
            "Free endpoints typically enforce strict requests-per-minute (RPM) and daily caps",
            "Latency and availability can fluctuate compared to paid SLA tiers",
        ]
    else:
        est_10m = round(p_in * 8 + p_out * 2, 2)
        tier_summary = (
            f"{m['name']} is priced at ${p_in}/1M input tokens and ${p_out}/1M output tokens "
            f"(~${est_10m} for a typical 8M input + 2M output monthly workload) with a {ctx_k}K context window."
        )
        best_for = [
            "Production API workloads requiring predictable rate limits",
            f"Long-context extraction tasks up to {ctx_k}K tokens" if ctx_k >= 100 else "Structured JSON generation and classification",
            "Cost-controlled developer tooling and backend automation",
        ]
        watch_outs = [
            "Always monitor output token ratios since completion tokens cost more than prompt tokens",
            "Cache repeated system prompts and static extractions to prevent runaway monthly spend",
        ]

    return {
        "source": "deterministic-fallback",
        "enriched_at": utc_today(),
        "summary": tier_summary,
        "best_for": best_for,
        "watch_outs": watch_outs,
        "faq": [
            {
                "q": f"Is {m['name']} free to use via API?",
                "a": (
                    f"Yes, {m['name']} is currently tracked with $0/1M input and output pricing, subject to upstream rate limits."
                    if is_free
                    else f"No, {m['name']} is a paid endpoint billed at ${p_in} per 1M input tokens and ${p_out} per 1M output tokens."
                ),
            },
            {
                "q": f"What is the context window of {m['name']}?",
                "a": f"{m['name']} supports up to {m['context_length']:,} tokens ({ctx_k}K) with modality {m.get('modality', 'text->text')}.",
            },
        ],
    }


def _build_model_prompt(m: dict[str, Any]) -> str:
    return (
        "Analyze this LLM API endpoint strictly based on the provided factual metadata. "
        "Do NOT invent benchmark scores or unverified claims. Return a JSON object with keys: "
        '"summary" (2 concise sentences), "best_for" (array of 3 concise strings), '
        '"watch_outs" (array of 2 concise strings), and "faq" (array of 2 objects with "q" and "a").\n\n'
        f"Metadata:\n"
        f"- Name: {m['name']}\n"
        f"- Provider: {m['provider']}\n"
        f"- Context Length: {m['context_length']} tokens\n"
        f"- Input Price: ${m['prompt_price_per_m']} per 1M tokens\n"
        f"- Output Price: ${m['completion_price_per_m']} per 1M tokens\n"
        f"- Free Tier: {m['is_free']}\n"
        f"- Modality: {m.get('modality', 'text->text')}\n"
        f"- Description: {m.get('description', '')}\n"
    )


def _deterministic_repo_enrichment(r: dict[str, Any]) -> dict[str, Any]:
    lang = r.get("language", "Multi")
    stars = int(r.get("stars", 0))
    return {
        "source": "deterministic-fallback",
        "enriched_at": utc_today(),
        "summary": (
            f"{r['full_name']} is an active {lang} open-source project with {stars:,} GitHub stars, "
            f"focused on practical developer infrastructure and self-hosted automation."
        ),
        "best_for": [
            f"Teams standardizing on {lang}-based self-hosted infrastructure",
            "Reducing recurring SaaS bills through open-source deployment",
            "Integrating into automated CI/CD or AI data pipelines",
        ],
        "watch_outs": [
            "Verify the repository license before embedding into commercial hosted products",
            "Pin release versions in production to avoid breaking upgrades",
        ],
    }


def _build_repo_prompt(r: dict[str, Any]) -> str:
    return (
        "Analyze this open-source GitHub repository strictly based on the provided factual metadata. "
        "Do NOT invent features. Return a JSON object with keys: "
        '"summary" (2 concise sentences), "best_for" (array of 3 concise strings), '
        'and "watch_outs" (array of 2 concise strings).\n\n'
        f"Metadata:\n"
        f"- Repository: {r['full_name']}\n"
        f"- Language: {r.get('language', 'Unknown')}\n"
        f"- Stars: {r.get('stars', 0)}\n"
        f"- Topics: {', '.join(r.get('topics', []))}\n"
        f"- Description: {r.get('description', '')}\n"
    )


def _valid_model_enrichment(data: dict[str, Any] | None) -> bool:
    if not data:
        return False
    return (
        isinstance(data.get("summary"), str)
        and isinstance(data.get("best_for"), list)
        and isinstance(data.get("watch_outs"), list)
        and isinstance(data.get("faq"), list)
    )


def _valid_repo_enrichment(data: dict[str, Any] | None) -> bool:
    if not data:
        return False
    return (
        isinstance(data.get("summary"), str)
        and isinstance(data.get("best_for"), list)
        and isinstance(data.get("watch_outs"), list)
    )


def main() -> None:
    cfg = load_config()
    max_calls = int(cfg["pipeline"].get("max_daily_llm_enrich", 10))
    has_live_llm = len(get_available_providers()) > 0

    models_path = DATA_DIR / "models.json"
    repos_path = DATA_DIR / "repos.json"
    models = load_json(models_path, default=[])
    repos = load_json(repos_path, default=[])

    llm_calls_used = 0

    for m in models:
        existing = m.get("enrichment")
        already_llm = existing and existing.get("source") not in (None, "deterministic-fallback")
        if already_llm:
            continue

        if has_live_llm and llm_calls_used < max_calls:
            parsed, provider = generate_json_with_fallback(_build_model_prompt(m))
            if _valid_model_enrichment(parsed):
                parsed["source"] = provider
                parsed["enriched_at"] = utc_today()
                m["enrichment"] = parsed
                llm_calls_used += 1
                time.sleep(1.2)
                continue

        if not existing:
            m["enrichment"] = _deterministic_model_enrichment(m)

    for r in repos:
        existing = r.get("enrichment")
        already_llm = existing and existing.get("source") not in (None, "deterministic-fallback")
        if already_llm:
            continue

        if has_live_llm and llm_calls_used < max_calls:
            parsed, provider = generate_json_with_fallback(_build_repo_prompt(r))
            if _valid_repo_enrichment(parsed):
                parsed["source"] = provider
                parsed["enriched_at"] = utc_today()
                r["enrichment"] = parsed
                llm_calls_used += 1
                time.sleep(1.2)
                continue

        if not existing:
            r["enrichment"] = _deterministic_repo_enrichment(r)

    save_json(models_path, models)
    save_json(repos_path, repos)
    mode = f"{llm_calls_used} live LLM enrichments" if has_live_llm else "deterministic mode (no LLM API key configured yet)"
    print(f"[enrich_llm] Completed: {mode}. Models={len(models)}, Repos={len(repos)}.")


if __name__ == "__main__":
    main()
