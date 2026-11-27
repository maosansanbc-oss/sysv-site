"""Traceable Content Review, Production Authorization & Content Fingerprinting Engine (Phase 2.1).

Separates five independent dimensions for every generated page:
- indexable: whether search engines are allowed to index the page
- ads_eligible: whether the page qualifies for monetization under strict human-review & quality rules
- ads_enabled: whether ad serving scripts/slots are actually active under verified production authorization
- review_status: unreviewed | approved | needs_review | rejected
- verification_enabled: whether official site-ownership verification (ads.txt / meta) is active

Hard invariants:
1. Automated scripts MUST NEVER write `review_status = "approved"`.
2. Passing automated factual checks does NOT equal human review approval.
3. Enabling live ad serving requires ALL 7 conditions simultaneously:
   - Explicit production build environment (`DEPLOY_ENV=production` AND `SYSV_PROD_BUILD=1`)
   - `site.config.json` `monetization.ad_mode == "live"` (`SYSV_AD_MODE` env var can ONLY downgrade, never escalate)
   - `monetization.owner_live_ads_approved is True`
   - Valid ISO-8601 `monetization.owner_approved_live_at`
   - Valid manual `monetization.adsense_ready_confirmation` (clearly marked as manual owner check)
   - Page passes human review (`review_status == "approved"` matching current `content_version`) & factual checks
   - NOT PR, NOT local, NOT preview, NOT test, NOT mock_ads
4. Content version fingerprints hash actual normalized editorial prose, calculation formulas,
   policy rules, disclosures, capability gates, and recommendation/comparison conclusions (never
   relying solely on hardcoded strings). Routine price updates that do not alter conclusions or
   task eligibility only change `fact_version`.
5. Source fetch failures (including `FETCH_ERROR_CACHED`) or expired verification dates (> `max_freshness_days`)
   NEVER count as continuous source verification for ad eligibility.
"""

import hashlib
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from scripts import enrich_llm
from scripts.utils import DATA_DIR, ROOT_DIR, load_config, load_json, save_json, utc_today

REVIEW_CONFIG_PATH = ROOT_DIR / "config" / "content_review.json"
DEFAULT_REPORTS_DIR = ROOT_DIR / "reports"

VALID_AD_MODES = {"off", "verification", "live"}
VALID_REVIEW_STATUSES = {"unreviewed", "approved", "needs_review", "rejected"}
DISALLOWED_REVIEWERS = {"auto", "bot", "system", "ci", "script", "codex", "ai", "llm", "agent", "github-actions"}

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
    "legal": ["editorial_policy_judgment"],
    "alias": ["deterministic_tool_logic"],
    "error": ["deterministic_tool_logic"],
    "embed": ["deterministic_tool_logic"],
}

_ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?\b")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def get_reports_dir(override: Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    env_dir = os.getenv("SYSV_REPORTS_DIR", "").strip()
    if env_dir:
        p = Path(env_dir)
        return p if p.is_absolute() else (ROOT_DIR / p)
    return DEFAULT_REPORTS_DIR


def _short_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def is_valid_iso_timestamp(ts: Any) -> bool:
    """Validate strict ISO-8601 timestamp with timezone (e.g. 2026-11-27T08:00:00Z)."""
    if not isinstance(ts, str):
        return False
    s = ts.strip()
    if len(s) < 19 or "T" not in s:
        return False
    if not (s.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", s)):
        return False
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.tzinfo is not None and 2024 <= dt.year <= 2100
    except ValueError:
        return False


def is_valid_human_reviewer(reviewer: Any) -> bool:
    if not isinstance(reviewer, str):
        return False
    r = reviewer.strip()
    return bool(r) and r.lower() not in DISALLOWED_REVIEWERS


def normalize_editorial_text(html_or_text: str) -> str:
    """Extract deterministic editorial text while stripping volatile dates and script payloads."""
    if not html_or_text:
        return ""
    cleaned = _SCRIPT_STYLE_RE.sub(" ", html_or_text)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = _ISO_DATE_RE.sub("<DATE>", cleaned)
    return " ".join(cleaned.split())


def compute_content_fingerprint(
    *,
    editorial_html: str = "",
    formula_sources: list[str] | None = None,
    policy_payload: Any = None,
    capability_payload: Any = None,
    decision_payload: Any = None,
) -> str:
    """Compute a stable substantive content_version fingerprint (TASK-04)."""
    normalized_formulas = [" ".join(str(s or "").split()) for s in (formula_sources or [])]
    return _short_hash(
        {
            "editorial": normalize_editorial_text(editorial_html),
            "formulas": normalized_formulas,
            "policies": policy_payload,
            "capabilities": capability_payload,
            "decisions": decision_payload,
        }
    )


def load_review_ledger(path: Path = REVIEW_CONFIG_PATH) -> dict[str, dict[str, Any]]:
    raw = load_json(path, default={"records": {}})
    records = raw.get("records") if isinstance(raw, dict) else {}
    return records if isinstance(records, dict) else {}


def resolve_ad_runtime_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve active ad_mode & enforce strict 7-point production authorization gate (TASK-03)."""
    mon = cfg.get("monetization") or {}
    client_id = str(mon.get("adsense_client_id") or "").strip()

    # Configured mode comes from site.config.json; SYSV_AD_MODE env var may ONLY downgrade (never escalate to live!)
    cfg_mode = str(mon.get("ad_mode") or "verification").strip().lower()
    if cfg_mode not in VALID_AD_MODES:
        cfg_mode = "off"
    env_mode = os.getenv("SYSV_AD_MODE", "").strip().lower()
    if env_mode in ("off", "verification"):
        requested_mode = env_mode
    else:
        requested_mode = cfg_mode

    deploy_env = os.getenv("DEPLOY_ENV", "").strip().lower()
    prod_build_flag = os.getenv("SYSV_PROD_BUILD", "").strip().lower() in ("1", "true", "yes")
    gh_event = os.getenv("GITHUB_EVENT_NAME", "").strip().lower()
    mock_ads = os.getenv("SYSV_MOCK_ADS", "").strip().lower() in ("1", "true", "yes")

    is_explicit_prod = (deploy_env == "production" and prod_build_flag)
    preview_mode = (
        os.getenv("SYSV_PREVIEW_MODE", "").strip().lower() in ("1", "true", "yes")
        or deploy_env in ("local", "preview", "staging", "test")
        or gh_event == "pull_request"
        or mock_ads
        or not is_explicit_prod
    )

    # Check ALL owner/production authorization prerequisites
    prod_reasons: list[str] = []
    if env_mode == "live" and cfg_mode != "live":
        prod_reasons.append("env_var_cannot_escalate_to_live")
    if not is_explicit_prod:
        prod_reasons.append("not_explicit_production_build")
    if gh_event == "pull_request":
        prod_reasons.append("pull_request_environment_prohibited")
    if mock_ads:
        prod_reasons.append("mock_ads_preview_active")
    if requested_mode != "live":
        prod_reasons.append(f"ad_mode_is_{requested_mode}")
    if not client_id:
        prod_reasons.append("missing_adsense_client_id")

    owner_approved = mon.get("owner_live_ads_approved") is True
    if not owner_approved:
        prod_reasons.append("missing_owner_live_ads_approved")

    owner_approved_at = mon.get("owner_approved_live_at")
    if not is_valid_iso_timestamp(owner_approved_at):
        prod_reasons.append("invalid_or_missing_owner_approved_live_at")

    ready_conf = mon.get("adsense_ready_confirmation")
    if not isinstance(ready_conf, dict) or ready_conf.get("confirmed") is not True:
        prod_reasons.append("missing_manual_adsense_ready_confirmation")
    else:
        if not is_valid_human_reviewer(ready_conf.get("confirmed_by")):
            prod_reasons.append("invalid_adsense_ready_confirmed_by")
        if not is_valid_iso_timestamp(ready_conf.get("confirmed_at")):
            prod_reasons.append("invalid_adsense_ready_confirmed_at")
        if not str(ready_conf.get("source") or "").strip():
            prod_reasons.append("missing_adsense_ready_source")

    prod_authorized = len(prod_reasons) == 0

    if requested_mode == "live" and not prod_authorized:
        # Downgrade safely: if verification is enabled and not off, stay in verification; otherwise off
        effective_ad_mode = "verification" if bool(mon.get("verification_enabled", True)) and not (os.getenv("SYSV_PREVIEW_MODE", "").strip().lower() in ("1", "true", "yes")) else "off"
    else:
        effective_ad_mode = requested_mode

    verification_enabled = (
        bool(mon.get("verification_enabled", True))
        and bool(client_id)
        and (effective_ad_mode != "off" or bool(mon.get("keep_verification_when_off", True)))
    )
    verification_method = str(mon.get("verification_method") or "ads_txt_and_meta").strip()

    return {
        "configured_ad_mode": cfg_mode,
        "requested_ad_mode": requested_mode,
        "effective_ad_mode": effective_ad_mode,
        "deploy_env": deploy_env or "local",
        "is_explicit_prod": is_explicit_prod,
        "preview_mode": preview_mode,
        "mock_ads": mock_ads,
        "client_id": client_id,
        "verification_enabled": verification_enabled,
        "verification_method": verification_method,
        "prod_authorized": prod_authorized,
        "prod_authorization_reasons": prod_reasons,
        "allow_live_ad_requests": prod_authorized,
    }


def _ctx_gate_bucket(ctx_len: int) -> str:
    if ctx_len >= 100000:
        return "ge_100k"
    if ctx_len >= 64000:
        return "64k_to_100k"
    return "lt_64k"


def compute_model_versions(
    m: dict[str, Any],
    *,
    not_for_items: list[str] | None = None,
    editorial_html: str = "",
) -> tuple[str, str, list[str]]:
    """Return (content_version, fact_version, factual_conflicts) for a model record.

    - fact_version tracks routine numeric price/context syncs.
    - content_version tracks MATERIAL changes that invalidate prior human approval:
      pricing_type, is_realtime, capabilities, context gate bucket (<64K / 64K-100K / >=100K),
      tiered surcharge flag, rate_limit_note, constraints (not_for_items), enrichment text, editorial HTML.
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

    ctx_len = int(m.get("context_length") or 0)
    fact_version = _short_hash(
        {
            "id": m.get("id"),
            "prompt": m.get("prompt_price_per_m"),
            "completion": m.get("completion_price_per_m"),
            "cache_read": m.get("cache_read_price_per_m"),
            "context_length": ctx_len,
        }
    )
    content_version = compute_content_fingerprint(
        editorial_html=editorial_html,
        policy_payload={
            "pricing_type": m.get("pricing_type"),
            "has_tiered_overrides": bool(m.get("has_tiered_overrides")),
            "rate_limit_note": m.get("rate_limit_note"),
            "status": m.get("status"),
        },
        capability_payload={
            "is_realtime": m.get("is_realtime"),
            "capabilities": m.get("capabilities"),
            "context_gate_bucket": _ctx_gate_bucket(ctx_len),
        },
        decision_payload={
            "id": m.get("id"),
            "enrichment_source": enr.get("source"),
            "summary": enr.get("summary"),
            "best_for": enr.get("best_for"),
            "watch_outs": enr.get("watch_outs"),
            "not_for_items": list(not_for_items or []),
        },
    )
    return content_version, fact_version, conflicts


def compute_comparison_versions(
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    cohort_label: str,
    comp_status: str,
    task_cells: dict[str, tuple[str, str]] | None = None,
    editorial_html: str = "",
) -> tuple[str, str, list[str]]:
    """Compute material content_version & routine fact_version for a comparison page.

    Routine price syncs that preserve winner order & task applicability only change fact_version.
    Any price/context/capability change that flips cheaper model, context leader, or task eligibility
    changes content_version and invalidates prior manual approval.
    """
    a_cver, a_fver, a_conf = compute_model_versions(a)
    b_cver, b_fver, b_conf = compute_model_versions(b)

    a_in, a_out = a.get("prompt_price_per_m"), a.get("completion_price_per_m")
    b_in, b_out = b.get("prompt_price_per_m"), b.get("completion_price_per_m")
    if a.get("pricing_type") in ("limited_free", "free") and b.get("pricing_type") in ("limited_free", "free"):
        cost_winner = "both_free"
    elif all(isinstance(v, (int, float)) for v in (a_in, a_out, b_in, b_out)):
        a_ref = float(a_in) * 8.0 + float(a_out) * 2.0
        b_ref = float(b_in) * 8.0 + float(b_out) * 2.0
        if abs(a_ref - b_ref) < 0.01:
            cost_winner = "tie"
        else:
            cost_winner = "a" if a_ref < b_ref else "b"
    else:
        cost_winner = "na"

    ctx_a, ctx_b = int(a.get("context_length") or 0), int(b.get("context_length") or 0)
    ctx_winner = "tie" if ctx_a == ctx_b else ("a" if ctx_a > ctx_b else "b")

    task_applicability: dict[str, tuple[bool, bool]] = {}
    for tname, (cell_a, cell_b) in sorted((task_cells or {}).items()):
        task_applicability[tname] = ("Not Applicable" not in cell_a, "Not Applicable" not in cell_b)

    fact_version = _short_hash({"a_fact": a_fver, "b_fact": b_fver})
    content_version = compute_content_fingerprint(
        editorial_html=editorial_html,
        capability_payload={
            "a_caps": a.get("capabilities"),
            "b_caps": b.get("capabilities"),
            "a_rt": a.get("is_realtime"),
            "b_rt": b.get("is_realtime"),
            "a_pt": a.get("pricing_type"),
            "b_pt": b.get("pricing_type"),
        },
        decision_payload={
            "a_id": a.get("id"),
            "b_id": b.get("id"),
            "cohort": cohort_label,
            "comp_status": comp_status,
            "cost_winner": cost_winner,
            "ctx_winner": ctx_winner,
            "task_applicability": task_applicability,
        },
    )
    return content_version, fact_version, a_conf + b_conf


def compute_free_policy_versions(
    policy_db: dict[str, Any],
    *,
    today: str | None = None,
    editorial_html: str = "",
    formula_sources: list[str] | None = None,
) -> tuple[str, str, list[str]]:
    """Compute material content_version, fact_version, and source-verification conflicts for /free-llm-apis/.

    Strictly rejects fetch errors (including FETCH_ERROR_CACHED) and expired verification dates
    (> max_freshness_days) as valid continuous source verification for ad eligibility (TASK-04).
    """
    today_str = today or utc_today()
    today_d = date.fromisoformat(today_str)
    providers = policy_db.get("providers") or []
    conflicts: list[str] = []
    material_items = []
    fact_items = []

    for p in providers:
        pid = p.get("id") or "unknown"
        st = str(p.get("verification_status") or "UNKNOWN")
        last_ver = str(p.get("last_verified_date") or "").strip()
        max_days = int(p.get("max_freshness_days") or 14)

        # Fetch failures (even cached for calculator UX) cannot be treated as continuously verified sources
        if st not in ("OFFICIAL_DOCS_VERIFIED", "DOC_CHANGED_SIGNAL_STABLE"):
            conflicts.append(f"source_verification_failed:{pid}:{st}")
        elif not last_ver:
            conflicts.append(f"source_missing_verified_date:{pid}")
        else:
            try:
                age = (today_d - date.fromisoformat(last_ver)).days
                if age > max_days:
                    conflicts.append(f"source_verification_expired:{pid}:{age}d>{max_days}d")
            except ValueError:
                conflicts.append(f"source_invalid_verified_date:{pid}:{last_ver}")

        material_items.append(
            {
                "id": pid,
                "tier_classification": p.get("tier_classification"),
                "credit_card_required": p.get("credit_card_required"),
                "credit_card_note": p.get("credit_card_note"),
                "trial_amount_usd": p.get("trial_amount_usd"),
                "trial_expiry_days": p.get("trial_expiry_days"),
                "quota_summary": p.get("quota_summary"),
                "reset_cycle": p.get("reset_cycle"),
                "privacy_policy": p.get("privacy_policy"),
                "free_daily_neurons": p.get("free_daily_neurons"),
                "model_quotas": p.get("model_quotas"),
                "verification_status": st,
            }
        )
        fact_items.append({"id": pid, "last_verified_date": last_ver, "last_checked_utc": p.get("last_checked_utc")})

    content_version = compute_content_fingerprint(
        editorial_html=editorial_html,
        formula_sources=formula_sources,
        policy_payload=material_items,
    )
    return content_version, _short_hash(fact_items), conflicts


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

    reasons: list[str] = []
    if not is_valid_human_reviewer(rec.get("reviewer")):
        reasons.append("missing_or_automated_reviewer")
    if not is_valid_iso_timestamp(rec.get("reviewed_at")):
        reasons.append("invalid_or_missing_reviewed_at")
    if not str(rec.get("review_scope") or "").strip():
        reasons.append("missing_review_scope")
    open_issues = [x for x in (rec.get("open_issues") or []) if str(x).strip()]
    if open_issues:
        reasons.append("unresolved_open_issues")
    rec_version = str(rec.get("content_version") or "").strip()
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

    ads_eligible = (
        not is_prohibited_path
        and not is_prohibited_type
        and indexable
        and page_type in CANDIDATE_AD_PAGE_TYPES
        and not conflicts
        and review_status == "approved"
    )

    ads_enabled = ads_eligible and bool(runtime_cfg.get("allow_live_ad_requests"))

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
        "prod_authorized": bool(runtime_cfg.get("prod_authorized")),
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
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Export prioritized queue of candidate pages awaiting human review (never writes approved)."""
    target = output_path or (get_reports_dir() / "manual_review_queue.json")
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
    save_json(target, payload)
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
        f"[content_quality_gate] ad_mode={rt['effective_ad_mode']} (configured={rt['configured_ad_mode']}, "
        f"prod_authorized={rt['prod_authorized']}, preview={rt['preview_mode']}), "
        f"review_records={len(ledger)}, model_conflicts={model_conflicts}, free_policy_conflicts={len(pol_conflicts)}"
    )


if __name__ == "__main__":
    main()
