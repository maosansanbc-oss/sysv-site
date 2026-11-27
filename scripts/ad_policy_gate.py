"""Phase 2 Automated Advertising Policy & Monetization Readiness Gate (TASK-09).

Checks:
1. Prohibited pages (404, repos/, privacy/, terms/, contact/, embed/*, noindex, archived) have ZERO ad tags.
2. Unreviewed / needs_review / rejected pages NEVER have ads_eligible=True or ads_enabled=True or adsbygoogle tags.
3. Every `approved` review record matches current content_version and has real human review provenance.
4. dist/ads.txt exists, is valid plain text (not HTML), and matches configured AdSense publisher ID.
5. Zero unknown or misconfigured publisher IDs (`ca-pub-...` / `pub-...`) anywhere in dist/.
6. dist/privacy/index.html contains all required real disclosures and ZERO ad or consent-gated scripts.
7. All affiliate links (vultr.com, dmit.io) carry rel="sponsored" and appear ONLY on high-intent pages.
8. Preview / local / off / verification builds contain ZERO live adsbygoogle.js requests.
9. Core Phase 1 factual invariants hold (zero misclassified :batch, valid enrichments).

Fail-safe policy:
- Missing/stale human review safely downgrades individual pages to ads_eligible=False (does NOT block builds).
- Hard violations (ad tag leaked onto prohibited/unreviewed/preview page, bad ads.txt, unknown publisher ID,
  missing privacy disclosure, affiliate missing sponsored, or Phase 1 factual corruption) fail exit code 1.
- Always emits machine-readable `reports/ad_policy_audit.json`.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

from scripts import content_quality_gate, enrich_llm
from scripts.utils import DATA_DIR, DIST_DIR, ROOT_DIR, load_config, load_json, save_json, utc_now_iso

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
    reports_dir: Path = content_quality_gate.REPORTS_DIR,
    review_path: Path = content_quality_gate.REVIEW_CONFIG_PATH,
) -> tuple[list[str], dict[str, Any]]:
    cfg = cfg or load_config()
    runtime_cfg = content_quality_gate.resolve_ad_runtime_config(cfg)
    expected_client = runtime_cfg["client_id"]
    expected_pub = expected_client.replace("ca-", "") if expected_client else ""

    errors: list[str] = []
    warnings: list[str] = []

    elig_doc = load_json(reports_dir / "page_ad_eligibility.json", default={"pages": {}, "runtime": runtime_cfg})
    elig_pages: dict[str, dict[str, Any]] = elig_doc.get("pages") or {}
    elig_by_rel = {_url_to_rel_path(u): meta for u, meta in elig_pages.items()}
    review_ledger = content_quality_gate.load_review_ledger(review_path)

    # 1. Inspect every built HTML page
    html_files = sorted(dist_dir.rglob("*.html")) if dist_dir.exists() else []
    no_ad_rel_paths = {"404.html", "repos/index.html", "privacy/index.html", "terms/index.html", "contact/index.html"}

    ad_script_leaks: list[str] = []
    unknown_pub_leaks: list[str] = []
    affiliate_issues: list[str] = []
    unreviewed_ad_leaks: list[str] = []

    for f in html_files:
        rel = f.relative_to(dist_dir).as_posix()
        raw = f.read_text(encoding="utf-8")
        has_ad_script = ("adsbygoogle.js" in raw) or ("class=\"adsbygoogle\"" in raw)
        is_noindex = bool(re.search(r'<meta\s+name="robots"\s+content="[^"]*noindex', raw, re.I))
        meta = elig_by_rel.get(rel)

        # Check unknown publisher IDs anywhere in HTML
        for pm in PUB_ID_RE.finditer(raw):
            found_pub = pm.group(1)
            if expected_pub and found_pub != expected_pub:
                unknown_pub_leaks.append(f"{rel}:{found_pub}")

        # Prohibited pages must NEVER contain ad scripts
        if has_ad_script and (rel in no_ad_rel_paths or rel.startswith("embed/") or is_noindex):
            ad_script_leaks.append(rel)

        # Non-live / preview builds must NEVER contain live ad scripts
        if has_ad_script and not runtime_cfg["allow_live_ad_requests"]:
            ad_script_leaks.append(f"{rel}(mode={runtime_cfg['effective_ad_mode']},preview={runtime_cfg['preview_mode']})")

        # Unreviewed / non-approved pages must NEVER contain ad scripts or ads_eligible=True
        if meta is not None:
            if meta.get("review_status") != "approved" and (meta.get("ads_eligible") or meta.get("ads_enabled") or has_ad_script):
                unreviewed_ad_leaks.append(rel)
        elif has_ad_script:
            unreviewed_ad_leaks.append(f"{rel}(missing_eligibility_record)")

        # Affiliate link compliance: must include rel="sponsored" and appear ONLY on high-intent pages
        for am in re.finditer(r'<a\b[^>]*href="([^"]*(?:vultr\.com|dmit\.io)[^"]*)"[^>]*>', raw, re.I):
            tag = am.group(0)
            if not re.search(r'rel="[^"]*\bsponsored\b[^"]*"', tag, re.I):
                affiliate_issues.append(f"{rel}:missing_sponsored")
            if rel not in ALLOWED_AFFILIATE_REL_PATHS:
                affiliate_issues.append(f"{rel}:disallowed_affiliate_placement")

    if ad_script_leaks:
        errors.append(f"Ad script leaked on prohibited/non-live/preview pages: {sorted(set(ad_script_leaks))[:10]}")
    if unreviewed_ad_leaks:
        errors.append(f"Unreviewed pages had ads_eligible/ads_enabled/ad-script: {sorted(set(unreviewed_ad_leaks))[:10]}")
    if unknown_pub_leaks:
        errors.append(f"Unknown publisher ID detected in built HTML: {sorted(set(unknown_pub_leaks))[:10]}")
    if affiliate_issues:
        errors.append(f"Affiliate policy violations: {sorted(set(affiliate_issues))[:10]}")

    # 2. Check review ledger integrity (ensure no unverified or bot-created 'approved' entries exist)
    for url, rec in review_ledger.items():
        if str(rec.get("review_status") or "").lower() == "approved":
            reviewer = str(rec.get("reviewer") or "").strip().lower()
            if not reviewer or reviewer in ("auto", "bot", "system", "ci", "script"):
                errors.append(f"Invalid automated 'approved' review record for {url}")
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

    # 4. Validate dist/robots.txt does not block Googlebot / Mediapartners-Google / Google-Display-Ads-Bot
    robots_path = dist_dir / "robots.txt"
    robots_ok = False
    if robots_path.exists():
        rtxt = robots_path.read_text(encoding="utf-8")
        robots_ok = (
            "Sitemap:" in rtxt
            and "Disallow: /" not in re.sub(r"#.*", "", rtxt)
        )
        if not robots_ok:
            errors.append("dist/robots.txt missing Sitemap or contains global Disallow: /")
    else:
        errors.append("Missing dist/robots.txt")

    # 5. Validate Privacy Policy disclosures & zero ad/consent-gated scripts on Privacy page
    priv_path = dist_dir / "privacy" / "index.html"
    privacy_ok = False
    missing_priv_markers: list[str] = []
    if priv_path.exists():
        ptxt = priv_path.read_text(encoding="utf-8")
        missing_priv_markers = [mk for mk in REQUIRED_PRIVACY_MARKERS if mk not in ptxt]
        has_priv_ads = ("adsbygoogle" in ptxt) or ("fundingchoicesmessages" in ptxt.lower())
        if missing_priv_markers:
            errors.append(f"Privacy policy missing required disclosures: {missing_priv_markers}")
        if has_priv_ads:
            errors.append("Privacy page must not contain adsbygoogle or Funding Choices tags")
        privacy_ok = (not missing_priv_markers) and (not has_priv_ads)
    else:
        errors.append("Missing dist/privacy/index.html")

    # 6. Phase 1 core factual integrity cross-check
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

    if ads_eligible_count == 0:
        warnings.append("Zero pages currently approved for live ads (expected until owner completes manual review).")

    report = {
        "schema_version": "1.0",
        "generated_at_utc": utc_now_iso(),
        "passed": len(errors) == 0,
        "runtime": runtime_cfg,
        "counts": {
            "total_pages": len(elig_pages),
            "built_html_files": len(html_files),
            "ads_eligible_pages": ads_eligible_count,
            "ads_enabled_pages": ads_enabled_count,
            "approved_pages": status_counts["approved"],
            "unreviewed_pages": status_counts["unreviewed"],
            "needs_review_pages": status_counts["needs_review"],
            "rejected_pages": status_counts["rejected"],
            "no_ad_pages": no_ad_count,
            "factual_conflicts": len(factual_conflicts),
        },
        "checks": {
            "ads_txt_valid": ads_txt_ok,
            "ads_txt_errors": ads_txt_errors,
            "robots_txt_valid": robots_ok,
            "privacy_disclosures_valid": privacy_ok,
            "missing_privacy_markers": missing_priv_markers,
            "ad_script_leaks": sorted(set(ad_script_leaks)),
            "unreviewed_ad_leaks": sorted(set(unreviewed_ad_leaks)),
            "unknown_pub_leaks": sorted(set(unknown_pub_leaks)),
            "affiliate_issues": sorted(set(affiliate_issues)),
        },
        "errors": errors,
        "warnings": warnings,
        "pending_owner_confirmations": [
            {"item": "AdSense Sites review verdict & rejection verbatim (if any)", "status": "Unknown (owner screenshot on 2026-11-27 showed 'Getting ready' / '正在准备')"},
            {"item": "AdSense Policy Center violations check", "status": "Unknown (must verify in AdSense Policy Center)"},
            {"item": "AdSense Payment / Identity / Tax verification", "status": "Unknown (must verify in AdSense Payments)"},
            {"item": "AdSense CMP live consent/reject/withdraw end-to-end test after live approval", "status": "Unknown (owner screenshots on 2026-11-27 showed GDPR & US messages published; live runtime pending AdSense approval)"},
            {"item": "AdSense Auto Ads excluded pages rule (/embed/*, /404.html, /privacy/, /terms/, /contact/, /repos/)", "status": "Unknown (must verify in AdSense Ads -> Overview)"},
            {"item": "Cloudflare real Googlebot / Mediapartners-Google / Google-Display-Ads-Bot crawler logs", "status": "Unknown (simulated probes cannot prove real Google crawler traffic; verify in Cloudflare Security Events & GSC Crawl Stats)"},
            {"item": "Cloudflare Email Routing forwarding for hello@sysv.cn", "status": "Unknown (must verify real inbox delivery)"},
            {"item": "Manual review approval in config/content_review.json before switching ad_mode to 'live'", "status": "Pending human review"},
        ],
    }
    save_json(reports_dir / "ad_policy_audit.json", report)
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
        f"[ad_policy_gate] PASSED: total_pages={c['total_pages']}, ads_eligible={c['ads_eligible_pages']}, "
        f"ads_enabled={c['ads_enabled_pages']}, approved={c['approved_pages']}, unreviewed={c['unreviewed_pages']}, "
        f"no_ad={c['no_ad_pages']}, factual_conflicts={c['factual_conflicts']}, ads_txt={report['checks']['ads_txt_valid']}."
    )


if __name__ == "__main__":
    main()
