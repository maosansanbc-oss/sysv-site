"""Traceable Content Review & Ad Eligibility Engine (Phase 2: TASK-01 & TASK-02).

Separates five independent dimensions for every generated page:
- indexable: whether search engines are allowed to index the page
- ads_eligible: whether the page qualifies for monetization under strict human-review & quality rules
- ads_enabled: whether ad serving scripts/slots are actually active in the current runtime mode
- review_status: unreviewed | approved | needs_review | rejected
- verification_enabled: whether official site-ownership verification (ads.txt / meta) is active

Hard invariants:
1. Automated scripts MUST NEVER write `review_status = "approved"`.
2. Passing automated factual checks does NOT equal human review approval.
3. Unreviewed pages, prohibited pages (404, privacy, terms, contact, embed, alias, noindex, archived),
   and preview/local environments ALWAYS resolve to `ads_enabled = False`.
4. Routine numeric price updates validated by Phase 1 factual gates do not force daily re-review of
   approved pages; material changes (new explanatory copy, policy/tier/capability changes, source
   failures, or factual conflicts) automatically downgrade an approved review to `needs_review` and
   revoke `ads_eligible`.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from scripts import enrich_llm
from scripts.utils import DATA_DIR, ROOT_DIR, load_config, load_json, save_json

REVIEW_CONFIG_PATH = ROOT_DIR / "config" / "content_review.json"
REPORTS_DIR = ROOT_DIR / "reports"

VALID_AD_MODES = {"off", "verification", "live"}
VALID_REVIEW_STATUSES = {"unreviewed", "approved", "needs_review", "rejected"}

# Pages that are ALWAYS prohibited from showing ads regardless of review_status or ad_mode
PROHIBITED_AD_PATHS = {
    "/404.html",
    "/repos/",
    "/privacy/",
    "/terms/",
    "/contact/",
}
PROHIBITED_AD_PAGE_TYPES = {
    "error",
    "alias",
    "embed",
    "empty_search",
    "legal",
}

# Page types allowed to APPLY for manual monetization review (eligibility is NEVER granted by type alone)
CANDIDATE_AD_PAGE_TYPES = {
    "home",
    "tool",
    "free_hub",
    "comparison",
    "model_detail",
    "guide",
    "self_hosting_hub",
}

# Four-layer content provenance taxonomy (TASK-02)
CONTENT_LAYERS = {
    "home": ["deterministic_tool_logic", "structured_source_data", "editorial_policy_judgment"],
    "tool": ["deterministic_tool_logic", "structured_source_data", "editorial_policy_judgment"],
    "tool_hub": ["deterministic_tool_logic"],
    "free_hub": ["deterministic_tool_logic", "structured_source_data", "editorial_policy_judgment"],
    "comparison": ["deterministic_tool_logic", "structured_source_data"],
    "model_detail": ["structured_source_data", "auto_explanatory_text"],
    "repo_detail": ["structured_source_data", "auto_explanatory_text"],
    "self_hosting_hub": ["structured_source_data", "editorial_policy_judgment"],
    "guide": ["structured_source_data", "editorial_policy_judgment"],
    "models_hub": ["structured_source_data"],
    "compare_hub": ["structured_source_data"],
    "price_history": ["structured_source_data"],
    "linkable_asset": ["structured_source_data"],
    "trust": ["editorial_policy_judgment"],
    "alias": ["deterministic_tool_logic"],
    "error": ["deterministic_tool_logic"],
    "embed": ["deterministic_tool_logic"],
}


def _short_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def load_review_ledger(path: Path = REVIEW_CONFIG_PATH) -> dict[str, dict[str, Any]]:
    raw = load_json(path, default={"records": {}})
    records = raw.get("records") if isinstance(raw, dict) else {}
    return records if isinstance(records, dict) else {}


def resolve_ad_runtime_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve active ad_mode, verification_enabled, preview_mode, and mock_ads flag."""
    mon = cfg.get("monetization") or {}
    client_id = str(mon.get("adsense_client_id") or "").strip()

    env_mode = os.getenv("SYSV_AD_MODE", "").strip().lower()
    raw_mode = env_mode or str(mon.get("ad_mode") or "verification").strip().lower()
    ad_mode = raw_mode if raw_mode in VALID_AD_MODES else "off"

    deploy_env = os.getenv("DEPLOY_ENV", "").strip().lower()
    gh_event = os.getenv("GITHUB_EVENT_NAME", "").strip().lower()
    preview_mode = (
        os.getenv("SYSV_PREVIEW_MODE", "").strip().lower() in ("1", "true", "yes")
        or deploy_env in ("local", "preview", "staging", "test")
        or gh_event == "pull_request"
    )
    mock_ads = os.getenv("SYSV_MOCK_ADS", "").strip().lower() in ("1", "true", "yes")

    # Preview / local environments MUST NEVER issue live ad requests
    effective_ad_mode = "off" if (preview_mode and ad_mode == "live") else ad_mode
    verification_enabled = bool(mon.get("verification_enabled", True)) and bool(client_id) and (effective_ad_mode != "off" or bool(mon.get("keep_verification_when_off", True)))
    verification_method = str(mon.get("verification_method") or "ads_txt_and_meta").strip()

    return {
        "configured_ad_mode": ad_mode,
        "effective_ad_mode": effective_ad_mode,
        "preview_mode": preview_mode,
        "mock_ads": mock_ads,
        "client_id": client_id,
        "verification_enabled": verification_enabled,
        "verification_method": verification_method,
        "allow_live_ad_requests": (effective_ad_mode == "live" and not preview_mode and bool(client_id)),
    }


def compute_model_versions(m: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Return (content_version, fact_version, factual_conflicts) for a model record.

    - fact_version tracks routine upstream facts (prices, context, timestamps).
    - content_version tracks MATERIAL changes that must invalidate a previous human approval:
      pricing_type, is_realtime, capabilities, explanatory enrichment text, and status.
    """
    enr = m.get("enrichment") or {}
    conflicts: list[str] = []
    mid = str(m.get("id", "")).lower()
    name = str(m.get("name", "")).lower()

    if (":batch" in mid or "(batch)" in name) and (m.get("pricing_type") != "batch" or m.get("is_realtime") is not False):
        conflicts.append(f"batch_misclassification:{m.get('id')}")
    if m.get("status", "active") == "active" and m.get("is_realtime") is None:
        conflicts.append(f"missing_is_realtime:{m.get('id')}")
    if m.get("pricing_type") == "unknown":
        conflicts.append(f"unknown_pricing_source:{m.get('id')}")
    if enr and not enrich_llm._valid_enrichment(enr, pricing_type=m.get("pricing_type"), model=m):
        conflicts.append(f"enrichment_factual_conflict:{m.get('id')}")

    fact_version = _short_hash(
        {
            "id": m.get("id"),
            "prompt": m.get("prompt_price_per_m"),
            "completion": m.get("completion_price_per_m"),
            "cache_read": m.get("cache_read_price_per_m"),
            "context_length": m.get("context_length"),
            "has_tiered_overrides": m.get("has_tiered_overrides"),
        }
    )
    content_version = _short_hash(
        {
            "id": m.get("id"),
            "pricing_type": m.get("pricing_type"),
            "is_realtime": m.get("is_realtime"),
            "capabilities": m.get("capabilities"),
            "status": m.get("status"),
            "enrichment_source": enr.get("source"),
            "summary": enr.get("summary"),
            "best_for": enr.get("best_for"),
            "watch_outs": enr.get("watch_outs"),
        }
    )
    return content_version, fact_version, conflicts


def compute_free_policy_versions(policy_db: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Compute material content_version, fact_version, and policy conflicts for /free-llm-apis/."""
    providers = policy_db.get("providers") or []
    conflicts: list[str] = []
    material_items = []
    fact_items = []
    for p in providers:
        st = p.get("verification_status", "UNKNOWN")
        if st not in ("OFFICIAL_DOCS_VERIFIED", "DOC_CHANGED_SIGNAL_STABLE", "FETCH_ERROR_CACHED"):
            conflicts.append(f"free_policy_unverified:{p.get('id')}:{st}")
        material_items.append(
            {
                "id": p.get("id"),
                "tier_classification": p.get("tier_classification"),
                "credit_card_required": p.get("credit_card_required"),
                "quota_summary": p.get("quota_summary"),
                "verification_status": st,
            }
        )
        fact_items.append({"id": p.get("id"), "last_verified_date": p.get("last_verified_date"), "model_quotas": p.get("model_quotas")})
    return _short_hash(material_items), _short_hash(fact_items), conflicts


def validate_manual_approval(rec: dict[str, Any] | None, current_content_version: str) -> tuple[str, list[str]]:
    """Validate a review record strictly. Never upgrades unreviewed -> approved.

    Returns (effective_review_status, reasons).
    """
    if not rec or not isinstance(rec, dict):
        return "unreviewed", ["no_manual_review_record"]

    raw_status = str(rec.get("review_status") or "unreviewed").strip().lower()
    if raw_status not in VALID_REVIEW_STATUSES:
        return "unreviewed", [f"invalid_review_status:{raw_status}"]

    if raw_status in ("unreviewed", "needs_review", "rejected"):
        return raw_status, [f"review_status_{raw_status}"]

    # Strict provenance check for `approved`
    reasons: list[str] = []
    reviewer = str(rec.get("reviewer") or "").strip()
    reviewed_at = str(rec.get("reviewed_at") or "").strip()
    review_scope = str(rec.get("review_scope") or "").strip()
    rec_version = str(rec.get("content_version") or "").strip()
    open_issues = [x for x in (rec.get("open_issues") or []) if str(x).strip()]

    if not reviewer or reviewer.lower() in ("auto", "bot", "system", "ci", "script"):
        reasons.append("missing_or_automated_reviewer")
    if not reviewed_at:
        reasons.append("missing_reviewed_at")
    if not review_scope:
        reasons.append("missing_review_scope")
    if open_issues:
        reasons.append("unresolved_open_issues")
    if not rec_version or rec_version != current_content_version:
        reasons.append("material_content_changed_since_review")

    if reasons:
        return "needs_review", reasons
    return "approved", []


def evaluate_page_ad_eligibility(
    *,
    url_path: str,
    page_type: str,
    index_policy: str,
    status: str,
    content_version: str,
    fact_version: str,
    review_ledger: dict[str, dict[str, Any]],
    runtime_cfg: dict[str, Any],
    factual_conflicts: list[str] | None = None,
    has_auto_generated_copy: bool = False,
) -> dict[str, Any]:
    """Compute independent indexable / ads_eligible / ads_enabled / review_status / verification_enabled."""
    conflicts = list(factual_conflicts or [])
    indexable = (index_policy == "index" and status == "active")
    block_reasons: list[str] = []

    is_prohibited_path = (url_path in PROHIBITED_AD_PATHS) or url_path.startswith("/embed/")
    is_prohibited_type = page_type in PROHIBITED_AD_PAGE_TYPES

    if is_prohibited_path or is_prohibited_type:
        block_reasons.append("prohibited_page_category")
    if not indexable:
        block_reasons.append(f"non_indexable_or_inactive:{index_policy}:{status}")
    if page_type not in CANDIDATE_AD_PAGE_TYPES:
        block_reasons.append(f"page_type_not_ad_candidate:{page_type}")
    if conflicts:
        block_reasons.extend(conflicts)

    rec = review_ledger.get(url_path)
    review_status, review_reasons = validate_manual_approval(rec, content_version)
    if conflicts and review_status == "approved":
        review_status = "needs_review"
        review_reasons.append("factual_conflict_invalidated_review")

    if review_status != "approved":
        block_reasons.extend(review_reasons)
    if has_auto_generated_copy and review_status != "approved":
        if "unreviewed_auto_generated_content" not in block_reasons:
            block_reasons.append("unreviewed_auto_generated_content")

    # Ads eligibility requires zero blocks, indexable, candidate type, zero factual conflicts, AND real human approval
    ads_eligible = (
        not is_prohibited_path
        and not is_prohibited_type
        and indexable
        and page_type in CANDIDATE_AD_PAGE_TYPES
        and not conflicts
        and review_status == "approved"
    )

    ads_enabled = ads_eligible and bool(runtime_cfg.get("allow_live_ad_requests"))

    # Verification meta only on clean indexable pages (never on 404/privacy/terms/contact/embed/noindex)
    verification_meta_allowed = (
        bool(runtime_cfg.get("verification_enabled"))
        and indexable
        and not is_prohibited_path
        and not is_prohibited_type
        and runtime_cfg.get("verification_method") in ("meta_only", "ads_txt_and_meta")
    )

    if is_prohibited_path or is_prohibited_type or page_type not in CANDIDATE_AD_PAGE_TYPES or not indexable:
        queue_priority = "excluded"
    elif page_type in ("home", "tool", "free_hub", "guide", "self_hosting_hub"):
        queue_priority = "P0_core"
    elif page_type == "comparison":
        queue_priority = "P1_compare"
    else:
        queue_priority = "P2_detail"

    return {
        "url": url_path,
        "page_type": page_type,
        "status": status,
        "index_policy": index_policy,
        "indexable": indexable,
        "review_status": review_status,
        "ads_eligible": ads_eligible,
        "ads_enabled": ads_enabled,
        "verification_enabled": verification_meta_allowed,
        "ad_mode": runtime_cfg.get("effective_ad_mode"),
        "preview_mode": bool(runtime_cfg.get("preview_mode")),
        "content_version": content_version,
        "fact_version": fact_version,
        "content_layers": CONTENT_LAYERS.get(page_type, ["structured_source_data"]),
        "factual_conflicts": conflicts,
        "block_reasons": sorted(set(block_reasons)),
        "queue_priority": queue_priority,
        "reviewer": (rec or {}).get("reviewer"),
        "reviewed_at": (rec or {}).get("reviewed_at"),
        "review_scope": (rec or {}).get("review_scope"),
        "open_issues": list((rec or {}).get("open_issues") or []),
    }


def write_manual_review_queue(
    eligibility_map: dict[str, dict[str, Any]],
    output_path: Path = REPORTS_DIR / "manual_review_queue.json",
) -> dict[str, Any]:
    """Export prioritized queue of candidate pages awaiting human review (never writes approved)."""
    priority_rank = {"P0_core": 0, "P1_compare": 1, "P2_detail": 2}
    items = []
    for url, meta in sorted(eligibility_map.items()):
        if meta["queue_priority"] == "excluded":
            continue
        if meta["review_status"] in ("unreviewed", "needs_review"):
            items.append(
                {
                    "priority": meta["queue_priority"],
                    "url": url,
                    "page_type": meta["page_type"],
                    "review_status": meta["review_status"],
                    "content_version": meta["content_version"],
                    "fact_version": meta["fact_version"],
                    "content_layers": meta["content_layers"],
                    "factual_conflicts": meta["factual_conflicts"],
                    "reasons": meta["block_reasons"],
                    "open_issues": meta["open_issues"],
                }
            )
    items.sort(key=lambda x: (priority_rank.get(x["priority"], 9), x["url"]))
    payload = {
        "schema_version": "1.0",
        "policy": "Human review required before any page can become ads_eligible=true. Automated pipelines never approve pages.",
        "summary": {
            "total_pending_manual_review": len(items),
            "p0_core_pending": sum(1 for i in items if i["priority"] == "P0_core"),
            "p1_compare_pending": sum(1 for i in items if i["priority"] == "P1_compare"),
            "p2_detail_pending": sum(1 for i in items if i["priority"] == "P2_detail"),
        },
        "items": items,
    }
    save_json(output_path, payload)
    return payload


def main() -> None:
    cfg = load_config()
    rt = resolve_ad_runtime_config(cfg)
    ledger = load_review_ledger()
    models = load_json(DATA_DIR / "models.json", default=[])
    policy_db = load_json(DATA_DIR / "free_provider_policies.json", default={"providers": []})

    model_conflicts = 0
    for m in models:
        _, _, c = compute_model_versions(m)
        model_conflicts += len(c)
    _, _, pol_conflicts = compute_free_policy_versions(policy_db)

    print(
        f"[content_quality_gate] ad_mode={rt['effective_ad_mode']} (configured={rt['configured_ad_mode']}, preview={rt['preview_mode']}), "
        f"review_records={len(ledger)}, model_conflicts={model_conflicts}, free_policy_conflicts={len(pol_conflicts)}"
    )


if __name__ == "__main__":
    main()
