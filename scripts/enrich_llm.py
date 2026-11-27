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
import re
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


_NEG_PREFIX_RE = re.compile(
    r"\b(?:no|not|without|lacks?|lacking|missing|never|cannot|can't|does\s+not|doesn't|do\s+not|don't|unable\s+to|excluding)\b",
    re.I,
)
_POS_PIVOT_RE = re.compile(r"\b(?:but|however|while|whereas)\s+(?:it\s+)?(?:supports?|offers?|includes?|provides?|enables?)\b", re.I)
_NEG_SUFFIX_RE = re.compile(
    r"^\s*(?:is|are)?\s*(?:not\s+(?:supported|available|enabled|included|suitable)|unavailable|unsupported|disabled)\b",
    re.I,
)
_DOLLAR_RE = re.compile(r"\$(\d+(?:,\d{3})*(?:\.\d+)?)")
_PER_M_PRICE_RE = re.compile(
    r"(?:priced\s+at|pricing\s+of|billed\s+at|input/output\s+pricing\s+of|at)\s+\$?(\d+(?:\.\d+)?)(?:\s*(?:/|and)\s*\$?(\d+(?:\.\d+)?))?\s*(?:per\s*1m|/1m|input|output)",
    re.I,
)
_CTX_BEFORE_RE = re.compile(
    r"(?<![a-z0-9.])(\d+(?:,\d{3})+|\d+(?:\.\d+)?)\s*([km])?\s*(?:-?\s*tokens?\s+)?(?:context(?:\s+window|\s+length)?|ctx\b)",
    re.I,
)
_CTX_AFTER_RE = re.compile(
    r"(?:context(?:\s+window|\s+length)?\s*(?:of|is|:)\s*(?:up\s+to\s+|~)?|up\s+to\s+~?)(\d+(?:,\d{3})+|\d+(?:\.\d+)?)\s*([km])?\s*(?:-?\s*tokens?)\b",
    re.I,
)
_PLATFORM_FEE_KEYWORDS = (
    "credit",
    "lifetime",
    "1,000 rpd",
    "1000 rpd",
    "top-up",
    "top up",
    "unlock",
    "minimum",
    "min fee",
    "card fee",
    "byok",
)


def _is_negated_match(text: str, start: int, end: int) -> bool:
    """Return True when the matched span is negated within its local sentence/clause."""
    sent_start = max(
        text.rfind(".", 0, start),
        text.rfind(";", 0, start),
        text.rfind("\n", 0, start),
        text.rfind("!", 0, start),
        text.rfind("?", 0, start),
    )
    sent_end_candidates = [pos for pos in (text.find(".", end), text.find(";", end), text.find("\n", end)) if pos != -1]
    sent_end = min(sent_end_candidates) if sent_end_candidates else len(text)
    prefix = text[sent_start + 1 : start]
    suffix = text[end:sent_end]

    # If a negation occurred before the match and was not overturned by an affirmative pivot
    neg_matches = list(_NEG_PREFIX_RE.finditer(prefix))
    if neg_matches:
        after_last_neg = prefix[neg_matches[-1].end() :]
        if not _POS_PIVOT_RE.search(after_last_neg):
            return True
    if _NEG_SUFFIX_RE.search(suffix):
        return True
    return False


def _price_matches_structured(val: float, refs: list[float]) -> bool:
    for ref in refs:
        tol = max(0.005, abs(ref) * 0.02)
        if abs(val - ref) <= tol:
            return True
    return False


def _validate_prices_against_model(all_text: str, summary: str, pricing_type: str | None, model: dict[str, Any] | None) -> bool:
    # 1. Free-claim guards on paid/batch/variable endpoints (without falsely blocking $0.05!)
    if pricing_type in ("variable", "paid", "batch"):
        for phrase in ("completely free", "zero-cost", "free tier", "free model"):
            for m in re.finditer(re.escape(phrase), all_text, re.I):
                if not _is_negated_match(all_text, m.start(), m.end()):
                    return False

    # 2. Unlimited-claim guard on rate-limited free endpoints (allows "not unlimited")
    if pricing_type in ("limited_free", "free", "free_router"):
        for m in re.finditer(r"\bunlimited\b", all_text, re.I):
            if not _is_negated_match(all_text, m.start(), m.end()):
                return False

    # Build allowed structured reference prices when model dict is supplied
    allowed_refs: list[float] = []
    est_10m_ref: float | None = None
    if model is not None:
        p_in = model.get("prompt_price_per_m")
        p_out = model.get("completion_price_per_m")
        for candidate in (p_in, p_out, model.get("cache_read_price_per_m"), model.get("cache_write_price_per_m")):
            if isinstance(candidate, (int, float)):
                allowed_refs.append(float(candidate))
        if isinstance(p_in, (int, float)) and isinstance(p_out, (int, float)):
            est_10m_ref = round(float(p_in) * 8.0 + float(p_out) * 2.0, 2)

    # 3. Inspect all explicit dollar amounts ($X or $X.YY)
    for dm in _DOLLAR_RE.finditer(all_text):
        val = float(dm.group(1).replace(",", ""))
        ctx_win = all_text[max(0, dm.start() - 40) : min(len(all_text), dm.end() + 45)].lower()
        if any(k in ctx_win for k in _PLATFORM_FEE_KEYWORDS):
            if val in (0.8, 9.0, 10.0, 25000.0):
                continue

        if pricing_type in ("variable", "paid", "batch") and val == 0.0:
            if not _is_negated_match(all_text, dm.start(), dm.end()):
                return False
            continue

        if model is None:
            continue

        if pricing_type in ("limited_free", "free", "free_router"):
            if val != 0.0 and not _is_negated_match(all_text, dm.start(), dm.end()):
                return False
            continue

        if pricing_type in ("variable", "unknown", "media") and not allowed_refs:
            if not _is_negated_match(all_text, dm.start(), dm.end()):
                return False
            continue

        if allowed_refs:
            refs_for_check = list(allowed_refs)
            if est_10m_ref is not None and ("8m" in ctx_win or "estimated" in ctx_win):
                refs_for_check.append(est_10m_ref)
            if not _price_matches_structured(val, refs_for_check):
                return False

    # 4. Directional input/output price checks when model has distinct numeric prices
    if model is not None:
        p_in = model.get("prompt_price_per_m")
        p_out = model.get("completion_price_per_m")
        if isinstance(p_in, (int, float)):
            in_refs = [float(p_in)]
            for c_ref in (model.get("cache_read_price_per_m"), model.get("cache_write_price_per_m")):
                if isinstance(c_ref, (int, float)):
                    in_refs.append(float(c_ref))
            for pat in (
                r"\$(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:per\s*1m|/1m)?\s*(?:prompt|input)\b",
                r"\b(?:prompt|input)\s*(?:price|pricing|rate)?\s*(?:is|of|at|:)?\s*\$(\d+(?:,\d{3})*(?:\.\d+)?)",
            ):
                for m in re.finditer(pat, all_text, re.I):
                    val = float(m.group(1).replace(",", ""))
                    win = all_text[max(0, m.start() - 25) : min(len(all_text), m.end() + 25)].lower()
                    refs = in_refs if "cache" in win else [float(p_in)]
                    if not _price_matches_structured(val, refs):
                        return False
        if isinstance(p_out, (int, float)):
            for pat in (
                r"\$(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:per\s*1m|/1m)?\s*(?:completion|output)\b",
                r"\b(?:completion|output)\s*(?:price|pricing|rate)?\s*(?:is|of|at|:)?\s*\$(\d+(?:,\d{3})*(?:\.\d+)?)",
            ):
                for m in re.finditer(pat, all_text, re.I):
                    val = float(m.group(1).replace(",", ""))
                    if not _price_matches_structured(val, [float(p_out)]):
                        return False

    # 5. Also check unit-explicit non-dollar per-1M token prices when model is supplied (e.g. "priced at 0.50 per 1M")
    if model is not None and allowed_refs:
        for pm in _PER_M_PRICE_RE.finditer(summary):
            for grp in (pm.group(1), pm.group(2)):
                if grp is None:
                    continue
                val = float(grp)
                if pricing_type in ("variable", "paid", "batch") and val == 0.0:
                    return False
                if not _price_matches_structured(val, allowed_refs):
                    return False

    return True


def _validate_context_against_model(all_text: str, model: dict[str, Any]) -> bool:
    ctx = int(model.get("context_length") or 0)
    if ctx <= 0:
        return True
    for pat in (_CTX_BEFORE_RE, _CTX_AFTER_RE):
        for cm in pat.finditer(all_text):
            raw_num = float(cm.group(1).replace(",", ""))
            unit = (cm.group(2) or "").lower()
            if unit == "k":
                claimed = raw_num * 1_000
            elif unit == "m":
                claimed = raw_num * 1_000_000
            else:
                if raw_num < 1_000:
                    continue
                claimed = raw_num
            if abs(claimed - ctx) / max(ctx, 1) > 0.08:
                return False
    return True


def _validate_capabilities_against_model(all_text: str, model: dict[str, Any]) -> bool:
    caps = model.get("capabilities") or {}
    ptype = model.get("pricing_type")
    is_realtime = model.get("is_realtime")

    if caps.get("tools") is False:
        pat = re.compile(
            r"\b(?:tool[- ]?calling|tool[- ]?use|tool[- ]?using|function[- ]?calling|supports?\s+tools?|with\s+tools?|tools?\s+(?:enabled|support))\b",
            re.I,
        )
        for m in pat.finditer(all_text):
            if not _is_negated_match(all_text, m.start(), m.end()):
                return False

    if caps.get("structured_outputs") is False:
        pat = re.compile(
            r"\b(?:structured[- ]?outputs?|supports?\s+(?:strict\s+)?json\s+schema)\b",
            re.I,
        )
        for m in pat.finditer(all_text):
            if not _is_negated_match(all_text, m.start(), m.end()):
                return False

    if caps.get("reasoning") is False:
        pat = re.compile(
            r"\b(?:supports?\s+reasoning|reasoning\s+(?:support|enabled|mode|model|capability|capabilities)|with\s+reasoning|extended\s+thinking)\b",
            re.I,
        )
        for m in pat.finditer(all_text):
            if not _is_negated_match(all_text, m.start(), m.end()):
                return False

    if caps.get("tools") is True:
        pat = re.compile(
            r"\b(?:no\s+tool[- ]?(?:calling|use)|does\s+not\s+support\s+tools?|tools?\s+(?:are|is)\s+not\s+(?:supported|available))\b",
            re.I,
        )
        if pat.search(all_text):
            return False

    if caps.get("structured_outputs") is True:
        pat = re.compile(
            r"\b(?:no\s+structured[- ]?outputs?|does\s+not\s+support\s+structured[- ]?outputs?|structured[- ]?outputs?\s+(?:are|is)\s+not\s+(?:supported|available))\b",
            re.I,
        )
        if pat.search(all_text):
            return False

    if caps.get("reasoning") is True:
        pat = re.compile(
            r"\b(?:no\s+reasoning\s+(?:support|capability)|does\s+not\s+support\s+reasoning|reasoning\s+(?:is)\s+not\s+(?:supported|available))\b",
            re.I,
        )
        if pat.search(all_text):
            return False

    if ptype == "batch" or is_realtime is False:
        pat = re.compile(
            r"\b(?:real[- ]time|interactive\s+(?:chat|agent|agents|use|mode|application|applications|workflow|workflows|coding))\b",
            re.I,
        )
        for m in pat.finditer(all_text):
            if not _is_negated_match(all_text, m.start(), m.end()):
                return False

    return True


def _valid_enrichment(
    data: dict[str, Any] | None,
    pricing_type: str | None = None,
    model: dict[str, Any] | None = None,
) -> bool:
    if not data:
        return False
    if not (
        isinstance(data.get("summary"), str)
        and data["summary"].strip()
        and isinstance(data.get("best_for"), list)
        and len(data["best_for"]) > 0
        and isinstance(data.get("watch_outs"), list)
        and len(data["watch_outs"]) > 0
    ):
        return False
    effective_ptype = pricing_type or (model.get("pricing_type") if model else None)
    all_text = " ".join(
        [data["summary"]] + [str(x) for x in data["best_for"]] + [str(x) for x in data["watch_outs"]]
    )
    all_low = all_text.lower()
    if any(w in all_low for w in ("benchmark", "runtime tested")):
        return False
    if not _validate_prices_against_model(all_text, data["summary"], effective_ptype, model):
        return False
    if model is not None:
        if not _validate_context_against_model(all_text, model):
            return False
        if not _validate_capabilities_against_model(all_text, model):
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
        existing_valid = _valid_enrichment(existing, pricing_type=m.get("pricing_type"), model=m)
        hash_matches = existing.get("cache_hash") == c_hash and existing_valid
        already_llm = hash_matches and existing.get("source") not in (None, "deterministic-fallback")
        if already_llm:
            continue
        if existing and not hash_matches:
            invalidated_count += 1

        if has_live_llm and guard.can_attempt() and m.get("status") == "active":
            parsed, provider, _usage = generate_json_with_fallback(_build_model_prompt(m), budget_guard=guard)
            if _valid_enrichment(parsed, pricing_type=m.get("pricing_type"), model=m):
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
