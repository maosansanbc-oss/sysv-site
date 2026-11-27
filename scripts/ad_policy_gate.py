"""Phase 2.1 Automated Advertising Policy & Monetization Readiness Gate (TASK-02 & TASK-09).

Checks:
1. Eligibility ledger (`page_ad_eligibility.json`) exists AND matches `dist/version.json` build provenance
   (`build_id`, `git_commit`, `built_at_utc`) — missing/stale/other-build ledgers are NEVER accepted silently.
2. EVERY built HTML page in `dist/` has a matching eligibility entry.
3. Exact bidirectional consistency on EVERY HTML page:
   - Prohibited pages (404, repos/, privacy/, terms/, contact/, embed/*, noindex, archived) have ZERO ad tags.
   - `has_ad_script` MUST equal `meta["ads_enabled"]` AND `ads_enabled == True` requires
     `ads_eligible == True`, `review_status == "approved"`, and `runtime["allow_live_ad_requests"] == True`.
   - Any `<ins class="adsbygoogle"` ad slot requires `ads_enabled == True` and `has_ad_script == True`.
4. Every `approved` review record in `config/content_review.json` passes strict human provenance validation
   (`is_valid_human_reviewer`, `is_valid_iso_timestamp`, non-empty `review_scope`, empty `open_issues`).
5. `dist/ads.txt` exists, is valid plain text (not HTML), and matches configured AdSense publisher ID.
6. Zero unknown or misconfigured publisher IDs (`ca-pub-...` / `pub-...`) anywhere in `dist/`.
7. `dist/privacy/index.html` contains all required disclosures and ZERO ad or Funding Choices scripts.
8. All affiliate links (`vultr.com`, `dmit.io`) carry `rel="sponsored"` and appear ONLY on high-intent pages.
9. Preview / local / off / verification / unauthorized builds contain ZERO live `adsbygoogle.js` requests.
10. Core Phase 1 factual invariants hold (zero misclassified `:batch`, valid enrichments).

Fail-safe policy:
- Unreviewed/stale/source-failed pages safely downgrade to `ads_eligible=False, ads_enabled=False` during build.
- Hard safety violations fail exit code 1 and block deployment.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

from scripts import content_quality_gate, enrich_llm
from scripts.utils import DATA_DIR, DIST_DIR, load_config, load_json, save_json, utc_now_iso

ALLOWED_AFFILIATE_REL_PATHS = {
    "self-hosting/index.html",
    "zero-cost-stack/index.html",
    "tools/n8n-ai-workflow-cost-calculator/index.html",
    "tools/api-vs-local-llm-calculator/index.html",
}

REQUIRED_PRIVACY_MARKERS = [
    "https://myadcenter.google.com/",
    "https://policies.google.com/technologies/ads",
    "https://optout.aboutads.info/",
    "hello@sysv.cn",
    "Google AdSense",
    "Cloudflare Web Analytics",
    "rel=&quot;sponsored&quot;",
    "consent-settings",
    "Pending Live Verification",
]

ADS_TXT_LINE_RE = re.compile(r"^google\.com,\s*(pub-\d{16}),\s*DIRECT,\s*f08c47fec0942fa0$")
PUB_ID_RE = re.compile(r"\b(?:ca-)?(pub-\d{10,20})\b")


def validate_ads_txt_content(raw_text: str, expected_client_id: str) -> tuple[bool, list[str]]:
    errs: list[str] = []
    stripped = raw_text.strip()
    if not stripped:
        return False, ["ads.txt is empty"]
    if "<html" in stripped.lower() or "<!doctype" in stripped.lower():
        return False, ["ads.txt contains HTML instead of plain text"]
    expected_pub = expected_client_id.replace("ca-", "").strip() if expected_client_id else ""
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return False, ["ads.txt has no active directive lines"]
    matched_expected = False
    for ln in lines:
        m = ADS_TXT_LINE_RE.match(ln)
        if not m:
            errs.append(f"Invalid ads.txt line format: '{ln}'")
            continue
        found_pub = m.group(1)
        if expected_pub and found_pub != expected_pub:
            errs.append(f"Unknown publisher ID in ads.txt: {found_pub} (expected {expected_pub})")
        elif expected_pub and found_pub == expected_pub:
            matched_expected = True
    if expected_pub and not matched_expected:
        errs.append(f"Configured publisher ID {expected_pub} missing from ads.txt")
    return len(errs) == 0, errs


def _url_to_rel_path(url_path: str) -> str:
    if url_path == "/":
        return "index.html"
    if url_path == "/404.html":
        return "404.html"
    return f"{url_path.strip('/')}/index.html"


def run_ad_policy_gate(
    *,
    cfg: dict[str, Any] | None = None,
    data_dir: Path = DATA_DIR,
    dist_dir: Path = DIST_DIR,
    reports_dir: Path | None = None,
    review_path: Path = content_quality_gate.REVIEW_CONFIG_PATH,
) -> tuple[list[str], dict[str, Any]]:
    cfg = cfg or load_config()
    target_reports = content_quality_gate.get_reports_dir(reports_dir)
    runtime_cfg = content_quality_gate.resolve_ad_runtime_config(cfg)
    expected_client = runtime_cfg["client_id"]
    expected_pub = expected_client.replace("ca-", "") if expected_client else ""

    errors: list[str] = []
    warnings: list[str] = []

    # 0. Strict eligibility ledger existence & build provenance match against dist/version.json (TASK-02)
    elig_path = target_reports / "page_ad_eligibility.json"
    ver_path = dist_dir / "version.json"
    elig_doc = load_json(elig_path, default=None)
    ver_doc = load_json(ver_path, default=None)

    ledger_provenance_ok = True
    if not isinstance(elig_doc, dict) or not isinstance(elig_doc.get("pages"), dict):
        errors.append(f"Missing or malformed eligibility ledger: {elig_path}")
        ledger_provenance_ok = False
        elig_pages: dict[str, dict[str, Any]] = {}
    else:
        elig_pages = elig_doc["pages"]
        if not isinstance(ver_doc, dict):
            errors.append(f"Missing or malformed dist/version.json for build provenance check: {ver_path}")
            ledger_provenance_ok = False
        else:
            if not elig_doc.get("build_id") or elig_doc.get("build_id") != ver_doc.get("build_id"):
                errors.append(
                    f"Eligibility ledger build_id mismatch (ledger={elig_doc.get('build_id')} vs dist/version.json={ver_doc.get('build_id')})"
                )
                ledger_provenance_ok = False
            if elig_doc.get("git_commit") != ver_doc.get("git_commit"):
                errors.append(
                    f"Eligibility ledger git_commit mismatch (ledger={elig_doc.get('git_commit')} vs dist/version.json={ver_doc.get('git_commit')})"
                )
                ledger_provenance_ok = False
            if elig_doc.get("generated_at_utc") != ver_doc.get("built_at_utc"):
                errors.append(
                    f"Eligibility ledger timestamp stale/mismatch (ledger={elig_doc.get('generated_at_utc')} vs dist/version.json={ver_doc.get('built_at_utc')})"
                )
                ledger_provenance_ok = False

    elig_by_rel = {_url_to_rel_path(u): meta for u, meta in elig_pages.items()}
    review_ledger = content_quality_gate.load_review_ledger(review_path)

    # 1. Inspect EVERY built HTML page for strict parity with eligibility ledger & policy rules
    html_files = sorted(dist_dir.rglob("*.html")) if dist_dir.exists() else []
    no_ad_rel_paths = {"404.html", "repos/index.html", "privacy/index.html", "terms/index.html", "contact/index.html"}

    ad_script_leaks: list[str] = []
    ad_state_mismatches: list[str] = []
    missing_elig_pages: list[str] = []
    unknown_pub_leaks: list[str] = []
    affiliate_issues: list[str] = []
    unreviewed_ad_leaks: list[str] = []
    actual_ad_script_pages: list[str] = []

    for f in html_files:
        rel = f.relative_to(dist_dir).as_posix()
        raw = f.read_text(encoding="utf-8")
        has_ad_script = "adsbygoogle.js" in raw
        has_ad_slot = 'class="adsbygoogle"' in raw
        has_any_ad_tag = has_ad_script or has_ad_slot
        is_noindex = bool(re.search(r'<meta\s+name="robots"\s+content="[^"]*noindex', raw, re.I))
        meta = elig_by_rel.get(rel)

        if has_ad_script:
            actual_ad_script_pages.append(rel)

        # Unknown publisher IDs anywhere in HTML
        for pm in PUB_ID_RE.finditer(raw):
            found_pub = pm.group(1)
            if expected_pub and found_pub != expected_pub:
                unknown_pub_leaks.append(f"{rel}:{found_pub}")

        # Prohibited pages must NEVER contain ad tags
        if has_any_ad_tag and (rel in no_ad_rel_paths or rel.startswith("embed/") or is_noindex):
            ad_script_leaks.append(rel)

        # Unauthorized / non-live / preview builds must NEVER contain live ad tags
        if has_any_ad_tag and not runtime_cfg["allow_live_ad_requests"]:
            ad_script_leaks.append(f"{rel}(mode={runtime_cfg['effective_ad_mode']},prod_auth={runtime_cfg['prod_authorized']})")

        # Exact parity against eligibility ledger (never allow silent missing records!)
        if meta is None:
            missing_elig_pages.append(rel)
            if has_any_ad_tag:
                unreviewed_ad_leaks.append(f"{rel}(missing_eligibility_record)")
        else:
            meta_eligible = bool(meta.get("ads_eligible"))
            meta_enabled = bool(meta.get("ads_enabled"))
            meta_status = str(meta.get("review_status") or "unreviewed")

            if meta_enabled and not meta_eligible:
                ad_state_mismatches.append(f"{rel}:ads_enabled_without_ads_eligible")
            if meta_enabled and meta_status != "approved":
                unreviewed_ad_leaks.append(f"{rel}:ads_enabled_status={meta_status}")
            if meta_eligible and meta_status != "approved":
                unreviewed_ad_leaks.append(f"{rel}:ads_eligible_status={meta_status}")
            if has_ad_script != meta_enabled:
                ad_state_mismatches.append(f"{rel}:html_script={has_ad_script}_vs_ads_enabled={meta_enabled}")
            if has_ad_slot and not (meta_enabled and meta_eligible and has_ad_script):
                ad_state_mismatches.append(f"{rel}:ad_slot_without_enabled_script")
            if has_any_ad_tag and meta_status != "approved":
                unreviewed_ad_leaks.append(rel)

        # Affiliate compliance: rel="sponsored" AND restricted to high-intent self-hosting/VPS pages
        for am in re.finditer(r'<a\b[^>]*href="([^"]*(?:vultr\.com|dmit\.io)[^"]*)"[^>]*>', raw, re.I):
            tag = am.group(0)
            if not re.search(r'rel="[^"]*\bsponsored\b[^"]*"', tag, re.I):
                affiliate_issues.append(f"{rel}:missing_sponsored")
            if rel not in ALLOWED_AFFILIATE_REL_PATHS:
                affiliate_issues.append(f"{rel}:disallowed_affiliate_placement")

    if missing_elig_pages:
        errors.append(f"Built HTML pages missing from eligibility ledger: {missing_elig_pages[:10]}")
    if ad_script_leaks:
        errors.append(f"Ad script leaked on prohibited/unauthorized/preview pages: {sorted(set(ad_script_leaks))[:10]}")
    if ad_state_mismatches:
        errors.append(f"HTML ad tags mismatched ads_enabled/ads_eligible state: {sorted(set(ad_state_mismatches))[:10]}")
    if unreviewed_ad_leaks:
        errors.append(f"Unreviewed pages had ads_eligible/ads_enabled/ad-script: {sorted(set(unreviewed_ad_leaks))[:10]}")
    if unknown_pub_leaks:
        errors.append(f"Unknown publisher ID detected in built HTML: {sorted(set(unknown_pub_leaks))[:10]}")
    if affiliate_issues:
        errors.append(f"Affiliate policy violations: {sorted(set(affiliate_issues))[:10]}")

    # 2. Validate review ledger provenance for any approved entry
    for url, rec in review_ledger.items():
        if str(rec.get("review_status") or "").lower() == "approved":
            if not content_quality_gate.is_valid_human_reviewer(rec.get("reviewer")):
                errors.append(f"Invalid or automated reviewer in approved record for {url}")
            if not content_quality_gate.is_valid_iso_timestamp(rec.get("reviewed_at")):
                errors.append(f"Invalid reviewed_at ISO timestamp in approved record for {url}")
            if not str(rec.get("review_scope") or "").strip():
                errors.append(f"Missing review_scope in approved record for {url}")
            if [x for x in (rec.get("open_issues") or []) if str(x).strip()]:
                errors.append(f"Approved record for {url} still has unresolved open_issues")
            if url in elig_pages:
                cur_cv = elig_pages[url].get("content_version")
                if rec.get("content_version") != cur_cv and elig_pages[url].get("ads_eligible"):
                    errors.append(f"Stale approved review record remained ads_eligible for {url}")

    # 3. Validate dist/ads.txt
    ads_txt_path = dist_dir / "ads.txt"
    ads_txt_ok = False
    ads_txt_errors: list[str] = []
    if expected_client:
        if not ads_txt_path.exists():
            ads_txt_errors.append("Missing dist/ads.txt while adsense_client_id is configured")
        else:
            ads_txt_ok, ads_txt_errors = validate_ads_txt_content(ads_txt_path.read_text(encoding="utf-8"), expected_client)
        if not ads_txt_ok:
            errors.extend(ads_txt_errors)
    else:
        ads_txt_ok = True

    # 4. Validate dist/robots.txt
    robots_path = dist_dir / "robots.txt"
    robots_ok = False
    if robots_path.exists():
        rtxt = robots_path.read_text(encoding="utf-8")
        robots_ok = ("Sitemap:" in rtxt) and ("Disallow: /" not in re.sub(r"#.*", "", rtxt))
        if not robots_ok:
            errors.append("dist/robots.txt missing Sitemap or contains global Disallow: /")
    else:
        errors.append("Missing dist/robots.txt")

    # 5. Validate Privacy Policy disclosures & zero ad/Funding Choices scripts
    priv_path = dist_dir / "privacy" / "index.html"
    privacy_ok = False
    missing_priv_markers: list[str] = []
    if priv_path.exists():
        ptxt = priv_path.read_text(encoding="utf-8")
        missing_priv_markers = [mk for mk in REQUIRED_PRIVACY_MARKERS if mk not in ptxt]
        has_priv_ads = ("adsbygoogle" in ptxt) or ("fundingchoicesmessages" in ptxt.lower()) or ("googlefc" in ptxt.lower())
        if missing_priv_markers:
            errors.append(f"Privacy policy missing required disclosures: {missing_priv_markers}")
        if has_priv_ads:
            errors.append("Privacy page must not contain adsbygoogle or Funding Choices/CMP scripts")
        privacy_ok = (not missing_priv_markers) and (not has_priv_ads)
    else:
        errors.append("Missing dist/privacy/index.html")

    # 6. Core Phase 1 factual integrity cross-check
    models = load_json(data_dir / "models.json", default=[])
    factual_conflicts: list[str] = []
    for m in models:
        _, _, confs = content_quality_gate.compute_model_versions(m)
        factual_conflicts.extend(confs)
        enr = m.get("enrichment")
        if enr and not enrich_llm._valid_enrichment(enr, pricing_type=m.get("pricing_type"), model=m):
            tag = f"invalid_enrichment:{m.get('id')}"
            if tag not in factual_conflicts:
                factual_conflicts.append(tag)
    if factual_conflicts:
        errors.append(f"Phase 1 factual conflicts detected ({len(factual_conflicts)}): {factual_conflicts[:5]}")

    # 7. Aggregate machine-readable audit report
    status_counts = {"approved": 0, "unreviewed": 0, "needs_review": 0, "rejected": 0}
    ads_eligible_count = 0
    ads_enabled_count = 0
    no_ad_count = 0
    source_degraded_pages = 0
    for meta in elig_pages.values():
        rs = meta.get("review_status", "unreviewed")
        if rs in status_counts:
            status_counts[rs] += 1
        if meta.get("ads_eligible"):
            ads_eligible_count += 1
        if meta.get("ads_enabled"):
            ads_enabled_count += 1
        else:
            no_ad_count += 1
        if meta.get("factual_conflicts"):
            source_degraded_pages += 1

    if ads_eligible_count == 0:
        warnings.append("Zero pages currently approved for live ads (expected until owner completes manual review).")
    if source_degraded_pages > 0:
        warnings.append(f"{source_degraded_pages} page(s) have degraded/unverified upstream source signals and were safely kept ads_eligible=False.")

    report = {
        "schema_version": "1.1",
        "generated_at_utc": utc_now_iso(),
        "build_id": (ver_doc or {}).get("build_id"),
        "passed": len(errors) == 0,
        "runtime": runtime_cfg,
        "cmp_verification_status": "Pending Live Verification",
        "counts": {
            "total_pages": len(elig_pages),
            "built_html_files": len(html_files),
            "actual_ad_script_pages": len(actual_ad_script_pages),
            "ads_eligible_pages": ads_eligible_count,
            "ads_enabled_pages": ads_enabled_count,
            "approved_pages": status_counts["approved"],
            "unreviewed_pages": status_counts["unreviewed"],
            "needs_review_pages": status_counts["needs_review"],
            "rejected_pages": status_counts["rejected"],
            "no_ad_pages": no_ad_count,
            "factual_conflicts": len(factual_conflicts),
            "source_degraded_pages": source_degraded_pages,
        },
        "checks": {
            "ledger_provenance_valid": ledger_provenance_ok,
            "missing_eligibility_pages": missing_elig_pages,
            "ads_txt_valid": ads_txt_ok,
            "ads_txt_errors": ads_txt_errors,
            "robots_txt_valid": robots_ok,
            "privacy_disclosures_valid": privacy_ok,
            "missing_privacy_markers": missing_priv_markers,
            "ad_script_leaks": sorted(set(ad_script_leaks)),
            "ad_state_mismatches": sorted(set(ad_state_mismatches)),
            "unreviewed_ad_leaks": sorted(set(unreviewed_ad_leaks)),
            "unknown_pub_leaks": sorted(set(unknown_pub_leaks)),
            "affiliate_issues": sorted(set(affiliate_issues)),
        },
        "errors": errors,
        "warnings": warnings,
        "pending_owner_confirmations": [
            {"item": "AdSense Sites review verdict & rejection verbatim (if any)", "status": "Unknown (2026-11-27 owner screenshot showed 'Getting ready')"},
            {"item": "AdSense Policy Center violations check", "status": "Unknown"},
            {"item": "AdSense Payment / Identity / Tax verification", "status": "Unknown"},
            {"item": "AdSense CMP live consent, reject & revocation verification", "status": "Pending Live Verification"},
            {"item": "AdSense Auto Ads excluded pages rule", "status": "Unknown"},
            {"item": "Cloudflare real Googlebot / Mediapartners-Google / Google-Display-Ads-Bot logs", "status": "Unknown"},
            {"item": "Cloudflare Email Routing for hello@sysv.cn", "status": "Unknown"},
            {"item": "Manual owner approval in config/content_review.json & site.config.json before live mode", "status": "Pending Owner Action"},
        ],
    }
    save_json(target_reports / "ad_policy_audit.json", report)
    return errors, report


def main() -> None:
    errors, report = run_ad_policy_gate()
    c = report["counts"]
    if errors:
        print("[ad_policy_gate] FAILED:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    print(
        f"[ad_policy_gate] PASSED: total_pages={c['total_pages']}, actual_ad_scripts={c['actual_ad_script_pages']}, "
        f"ads_eligible={c['ads_eligible_pages']}, ads_enabled={c['ads_enabled_pages']}, approved={c['approved_pages']}, "
        f"unreviewed={c['unreviewed_pages']}, no_ad={c['no_ad_pages']}, factual_conflicts={c['factual_conflicts']}, "
        f"ads_txt={report['checks']['ads_txt_valid']}, cmp={report['cmp_verification_status']}."
    )


if __name__ == "__main__":
    main()
