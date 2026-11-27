"""Phase 2.1 Monetization Readiness, Production Authorization & Review Integrity Test Suite.

Covers Phase 2.1 TASK-01 through TASK-06:
- TASK-01: Legitimate human approvals pass CI; invalid/bot/missing-field/bad-timestamp/stale approvals fail;
  committed review ledger is never mutated by builds or tests.
- TASK-02: Strict default-off ad rendering (`ads_enabled=False`), `allow_ads` cannot bypass eligibility or prod auth,
  simulated live build with ONLY `/` approved renders `adsbygoogle.js` on ONLY `index.html` and 0/278 other pages;
  `ad_policy_gate` rejects missing/stale/mismatched eligibility ledgers and any HTML vs `ads_enabled` mismatch.
- TASK-03: 7-point production authorization gate (`DEPLOY_ENV=production`, `SYSV_PROD_BUILD=1`, `ad_mode=live`,
  `owner_live_ads_approved`, valid `owner_approved_live_at`, valid manual `adsense_ready_confirmation`, non-PR/preview);
  `SYSV_AD_MODE=live` env var alone NEVER enables live ads; missing owner auth turns off ads on 100% of pages.
- TASK-04: Content-driven fingerprints react to editorial copy, calculation formulas, policies, winner flips,
  and capability changes, remain stable across routine price syncs and repeated identical builds, and reject
  source fetch failures (`FETCH_ERROR_CACHED`) or expired caches (`> max_freshness_days`) for ad eligibility.
- TASK-05: Strict isolation between read-only `pr-preview.yml` and production `daily-auto.yml`.
- TASK-06: Official Google CMP revocation hook rendered ONLY on live ad-enabled pages; Privacy page stays script-free
  and documents `Pending Live Verification`.
"""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import ad_policy_gate, build_site, content_quality_gate, fetch_data
from scripts.utils import ROOT_DIR, load_config


def _make_authorized_live_cfg(base_cfg: dict) -> dict:
    cfg = copy.deepcopy(base_cfg)
    cfg["monetization"]["ad_mode"] = "live"
    cfg["monetization"]["owner_live_ads_approved"] = True
    cfg["monetization"]["owner_approved_live_at"] = "2026-11-27T08:00:00Z"
    cfg["monetization"]["adsense_ready_confirmation"] = {
        "confirmed": True,
        "confirmed_by": "site-owner",
        "confirmed_at": "2026-11-27T08:00:00Z",
        "source": "manual_owner_dashboard_check",
    }
    return cfg


class TestTask01ManualApprovalIntegrity(unittest.TestCase):
    def test_committed_review_ledger_records_are_valid_and_support_legitimate_approvals(self):
        ledger = content_quality_gate.load_review_ledger()
        self.assertGreaterEqual(len(ledger), 8)
        for url, rec in ledger.items():
            st = str(rec.get("review_status") or "").lower()
            self.assertIn(st, content_quality_gate.VALID_REVIEW_STATUSES, f"Invalid status on {url}")
            # If an owner later legitimately approves a page, this test MUST allow it when all fields are valid!
            if st == "approved":
                self.assertTrue(content_quality_gate.is_valid_human_reviewer(rec.get("reviewer")), f"Invalid reviewer on {url}")
                self.assertTrue(content_quality_gate.is_valid_iso_timestamp(rec.get("reviewed_at")), f"Invalid reviewed_at on {url}")
                self.assertTrue(str(rec.get("review_scope") or "").strip(), f"Missing review_scope on {url}")
                self.assertTrue(str(rec.get("content_version") or "").strip(), f"Missing content_version on {url}")
                self.assertEqual([x for x in (rec.get("open_issues") or []) if str(x).strip()], [], f"Open issues on approved {url}")

    def test_legitimate_human_approval_passes_while_invalid_or_stale_approvals_fail(self):
        valid_rec = {
            "url": "/",
            "review_status": "approved",
            "reviewer": "site-owner",
            "reviewed_at": "2026-11-27T08:30:00Z",
            "review_scope": "core_landing_and_token_calculator",
            "content_version": "abc1234567890def",
            "open_issues": [],
        }
        st_ok, reasons_ok = content_quality_gate.validate_manual_approval(valid_rec, "abc1234567890def")
        self.assertEqual(st_ok, "approved")
        self.assertEqual(reasons_ok, [])

        bad_cases = [
            ("bot_reviewer", {**valid_rec, "reviewer": "bot"}, "missing_or_automated_reviewer"),
            ("empty_reviewer", {**valid_rec, "reviewer": ""}, "missing_or_automated_reviewer"),
            ("bad_timestamp_text", {**valid_rec, "reviewed_at": "yesterday"}, "invalid_or_missing_reviewed_at"),
            ("bad_timestamp_no_tz", {**valid_rec, "reviewed_at": "2026-11-27T08:30:00"}, "invalid_or_missing_reviewed_at"),
            ("bad_calendar_date", {**valid_rec, "reviewed_at": "2026-13-40T00:00:00Z"}, "invalid_or_missing_reviewed_at"),
            ("missing_scope", {**valid_rec, "review_scope": ""}, "missing_review_scope"),
            ("open_issues", {**valid_rec, "open_issues": ["Verify copy"]}, "unresolved_open_issues"),
            ("stale_version", {**valid_rec, "content_version": "old_hash"}, "material_content_changed_since_review"),
        ]
        for name, rec, expected_reason in bad_cases:
            st, reasons = content_quality_gate.validate_manual_approval(rec, "abc1234567890def")
            self.assertEqual(st, "needs_review", f"Case {name} should downgrade to needs_review")
            self.assertIn(expected_reason, reasons, f"Case {name} missing reason {expected_reason}: {reasons}")


class TestTask02And03StrictDefaultOffAndProdAuth(unittest.TestCase):
    def test_production_authorization_blocks_live_ads_when_any_prerequisite_missing(self):
        base_cfg = load_config()
        live_cfg = _make_authorized_live_cfg(base_cfg)

        # 1. Local default (no DEPLOY_ENV=production / SYSV_PROD_BUILD=1) MUST block live ads
        with mock.patch.dict(os.environ, {}, clear=True):
            rt_local = content_quality_gate.resolve_ad_runtime_config(live_cfg)
            self.assertFalse(rt_local["prod_authorized"])
            self.assertFalse(rt_local["allow_live_ad_requests"])
            self.assertIn("not_explicit_production_build", rt_local["prod_authorization_reasons"])

        # 2. SYSV_AD_MODE=live env var alone cannot escalate verification config to live
        with mock.patch.dict(os.environ, {"DEPLOY_ENV": "production", "SYSV_PROD_BUILD": "1", "SYSV_AD_MODE": "live"}, clear=True):
            rt_env_escalate = content_quality_gate.resolve_ad_runtime_config(base_cfg)
            self.assertFalse(rt_env_escalate["allow_live_ad_requests"])
            self.assertIn("env_var_cannot_escalate_to_live", rt_env_escalate["prod_authorization_reasons"])

        # 3. PR environment blocks live ads even with full owner config
        with mock.patch.dict(os.environ, {"DEPLOY_ENV": "production", "SYSV_PROD_BUILD": "1", "GITHUB_EVENT_NAME": "pull_request"}, clear=True):
            rt_pr = content_quality_gate.resolve_ad_runtime_config(live_cfg)
            self.assertFalse(rt_pr["allow_live_ad_requests"])
            self.assertIn("pull_request_environment_prohibited", rt_pr["prod_authorization_reasons"])

        # 4. Missing each owner field blocks live ads even in prod build
        prod_env = {"DEPLOY_ENV": "production", "SYSV_PROD_BUILD": "1"}
        with mock.patch.dict(os.environ, prod_env, clear=True):
            # All 7 satisfied -> authorized!
            rt_ok = content_quality_gate.resolve_ad_runtime_config(live_cfg)
            self.assertTrue(rt_ok["prod_authorized"])
            self.assertTrue(rt_ok["allow_live_ad_requests"])

            c_no_flag = copy.deepcopy(live_cfg)
            c_no_flag["monetization"]["owner_live_ads_approved"] = False
            self.assertFalse(content_quality_gate.resolve_ad_runtime_config(c_no_flag)["allow_live_ad_requests"])

            c_bad_ts = copy.deepcopy(live_cfg)
            c_bad_ts["monetization"]["owner_approved_live_at"] = "not-a-date"
            self.assertFalse(content_quality_gate.resolve_ad_runtime_config(c_bad_ts)["allow_live_ad_requests"])

            c_no_ready = copy.deepcopy(live_cfg)
            c_no_ready["monetization"]["adsense_ready_confirmation"]["confirmed"] = False
            self.assertFalse(content_quality_gate.resolve_ad_runtime_config(c_no_ready)["allow_live_ad_requests"])

            c_bot_ready = copy.deepcopy(live_cfg)
            c_bot_ready["monetization"]["adsense_ready_confirmation"]["confirmed_by"] = "bot"
            self.assertFalse(content_quality_gate.resolve_ad_runtime_config(c_bot_ready)["allow_live_ad_requests"])

    def test_render_layout_defaults_off_and_allow_ads_cannot_bypass(self):
        live_cfg = _make_authorized_live_cfg(load_config())
        prod_env = {"DEPLOY_ENV": "production", "SYSV_PROD_BUILD": "1"}
        with mock.patch.dict(os.environ, prod_env, clear=True):
            # Default ads_enabled=False -> NO adsbygoogle.js even in authorized live prod!
            out_default = build_site.render_layout(
                live_cfg, title="T", description="D", canonical_path="/self-hosting/", body_html="<h1>T</h1>", git_commit="abc", index_policy="index"
            )
            self.assertNotIn("adsbygoogle", out_default)

            # Legacy allow_ads=True MUST NOT bypass ads_enabled=False!
            out_legacy = build_site.render_layout(
                live_cfg, title="T", description="D", canonical_path="/self-hosting/", body_html="<h1>T</h1>", git_commit="abc", index_policy="index", allow_ads=True
            )
            self.assertNotIn("adsbygoogle", out_legacy)

        # Even with ads_enabled=True, non-prod local env MUST NOT emit adsbygoogle.js!
        with mock.patch.dict(os.environ, {}, clear=True):
            out_local = build_site.render_layout(
                live_cfg, title="T", description="D", canonical_path="/", body_html="<h1>Home</h1>", git_commit="abc", index_policy="index", ads_enabled=True
            )
            self.assertNotIn("adsbygoogle", out_local)

    def test_simulated_prod_live_build_only_approved_homepage_has_ads_and_missing_auth_disables_all(self):
        """TASK-02 & TASK-03 E2E Acceptance:
        1. Simulated prod live build with ONLY `/` legitimately human-approved -> ONLY `index.html` has adsbygoogle.js (1/279),
           all other 278 pages (including self-hosting, zero-cost-stack, tools hub, models hub, compare hub, repos, price history, data hub)
           have ZERO adsbygoogle tags, and real config/content_review.json is untouched.
        2. Same approved ledger WITHOUT owner authorization -> 0/279 pages have adsbygoogle.js.
        """
        real_review_path = ROOT_DIR / "config" / "content_review.json"
        review_sha_before = hashlib.sha256(real_review_path.read_bytes()).hexdigest()
        base_cfg = load_config()
        live_cfg = _make_authorized_live_cfg(base_cfg)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dist_probe = root / "dist_probe"
            rep_probe = root / "rep_probe"
            # Step A: dry build to obtain current exact content_version for `/`
            with mock.patch.dict(os.environ, {}, clear=True):
                build_site.main(
                    today_override="2026-11-27",
                    now_iso_override="2026-11-27T08:00:00Z",
                    cfg_override=base_cfg,
                    dist_dir_override=dist_probe,
                    reports_dir_override=rep_probe,
                    published_pages_path_override=root / "pub_pages_probe.json",
                    published_comparisons_path_override=root / "pub_comps_probe.json",
                )
            probe_elig = json.loads((rep_probe / "page_ad_eligibility.json").read_text(encoding="utf-8"))
            home_cv = probe_elig["pages"]["/"]["content_version"]

            # Create isolated temp ledger approving ONLY `/`
            temp_review = root / "temp_content_review.json"
            temp_review.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "records": {
                            "/": {
                                "url": "/",
                                "page_type": "home",
                                "content_version": home_cv,
                                "fact_version": probe_elig["pages"]["/"]["fact_version"],
                                "review_scope": "core_landing_and_token_calculator",
                                "reviewer": "site-owner",
                                "reviewed_at": "2026-11-27T08:00:00Z",
                                "review_status": "approved",
                                "open_issues": [],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            # Step B: Simulated authorized production LIVE build
            dist_live = root / "dist_live"
            rep_live = root / "rep_live"
            with mock.patch.dict(os.environ, {"DEPLOY_ENV": "production", "SYSV_PROD_BUILD": "1"}, clear=True):
                build_site.main(
                    today_override="2026-11-27",
                    now_iso_override="2026-11-27T08:05:00Z",
                    cfg_override=live_cfg,
                    dist_dir_override=dist_live,
                    reports_dir_override=rep_live,
                    review_path_override=temp_review,
                    published_pages_path_override=root / "pub_pages_live.json",
                    published_comparisons_path_override=root / "pub_comps_live.json",
                )
                errs, report = ad_policy_gate.run_ad_policy_gate(
                    cfg=live_cfg, dist_dir=dist_live, reports_dir=rep_live, review_path=temp_review
                )
            self.assertEqual(errs, [], f"Authorized 1-page live build failed ad_policy_gate: {errs}")
            self.assertEqual(report["counts"]["ads_eligible_pages"], 1)
            self.assertEqual(report["counts"]["ads_enabled_pages"], 1)
            self.assertEqual(report["counts"]["actual_ad_script_pages"], 1)

            html_files = sorted(dist_live.rglob("*.html"))
            with_ads = [f.relative_to(dist_live).as_posix() for f in html_files if "adsbygoogle" in f.read_text(encoding="utf-8")]
            self.assertEqual(with_ads, ["index.html"])
            # Explicitly check key previously-ungated hub/detail pages have ZERO ads
            for must_be_clean in (
                "self-hosting/index.html",
                "zero-cost-stack/index.html",
                "tools/index.html",
                "models/index.html",
                "compare/index.html",
                "price-changes/index.html",
                "data/index.html",
                "privacy/index.html",
                "404.html",
            ):
                self.assertNotIn("adsbygoogle", (dist_live / must_be_clean).read_text(encoding="utf-8"))
            # Official Google CMP revocation hook appears ONLY on live ad-enabled homepage (NOT on privacy!)
            self.assertIn("googlefc.showRevocationMessage", (dist_live / "index.html").read_text(encoding="utf-8"))
            self.assertNotIn("googlefc", (dist_live / "privacy/index.html").read_text(encoding="utf-8"))

            # Step C: Same approved ledger WITHOUT owner authorization -> 0/279 pages have ads!
            unauth_cfg = copy.deepcopy(live_cfg)
            unauth_cfg["monetization"]["owner_live_ads_approved"] = False
            dist_unauth = root / "dist_unauth"
            rep_unauth = root / "rep_unauth"
            with mock.patch.dict(os.environ, {"DEPLOY_ENV": "production", "SYSV_PROD_BUILD": "1"}, clear=True):
                build_site.main(
                    today_override="2026-11-27",
                    now_iso_override="2026-11-27T08:10:00Z",
                    cfg_override=unauth_cfg,
                    dist_dir_override=dist_unauth,
                    reports_dir_override=rep_unauth,
                    review_path_override=temp_review,
                    published_pages_path_override=root / "pub_pages_unauth.json",
                    published_comparisons_path_override=root / "pub_comps_unauth.json",
                )
                errs_u, rep_u = ad_policy_gate.run_ad_policy_gate(
                    cfg=unauth_cfg, dist_dir=dist_unauth, reports_dir=rep_unauth, review_path=temp_review
                )
            self.assertEqual(errs_u, [])
            self.assertEqual(rep_u["counts"]["ads_eligible_pages"], 1)
            self.assertEqual(rep_u["counts"]["ads_enabled_pages"], 0)
            self.assertEqual(rep_u["counts"]["actual_ad_script_pages"], 0)

        # Verify real committed config/content_review.json was NEVER mutated
        review_sha_after = hashlib.sha256(real_review_path.read_bytes()).hexdigest()
        self.assertEqual(review_sha_before, review_sha_after)


class TestTask04ContentFingerprintingAndSourceFreshness(unittest.TestCase):
    def test_editorial_formula_and_policy_changes_invalidate_fingerprint_while_identical_is_stable(self):
        fp1 = content_quality_gate.compute_content_fingerprint(
            editorial_html="<p>How SysV calculates costs on 2026-11-27.</p>",
            formula_sources=["function calc(a, b) { return a * 0.055 + b; }"],
            policy_payload={"card_min": 0.80},
        )
        # Same content on a different calendar date -> IDENTICAL fingerprint
        fp1_next_day = content_quality_gate.compute_content_fingerprint(
            editorial_html="<p>How SysV calculates costs on 2026-11-28.</p>",
            formula_sources=["function calc(a, b) { return a * 0.055 + b; }"],
            policy_payload={"card_min": 0.80},
        )
        self.assertEqual(fp1, fp1_next_day)

        # Editing editorial prose -> NEW fingerprint
        fp_edit = content_quality_gate.compute_content_fingerprint(
            editorial_html="<p>Updated editorial explanation on 2026-11-27.</p>",
            formula_sources=["function calc(a, b) { return a * 0.055 + b; }"],
            policy_payload={"card_min": 0.80},
        )
        self.assertNotEqual(fp1, fp_edit)

        # Editing calculation formula -> NEW fingerprint
        fp_formula = content_quality_gate.compute_content_fingerprint(
            editorial_html="<p>How SysV calculates costs on 2026-11-27.</p>",
            formula_sources=["function calc(a, b) { return a * 0.060 + b; }"],
            policy_payload={"card_min": 0.80},
        )
        self.assertNotEqual(fp1, fp_formula)

        # Editing policy rule -> NEW fingerprint
        fp_policy = content_quality_gate.compute_content_fingerprint(
            editorial_html="<p>How SysV calculates costs on 2026-11-27.</p>",
            formula_sources=["function calc(a, b) { return a * 0.055 + b; }"],
            policy_payload={"card_min": 1.00},
        )
        self.assertNotEqual(fp1, fp_policy)

    def test_routine_price_update_stable_but_winner_flip_or_gate_crossing_invalidates(self):
        a1 = fetch_data.classify_model(
            {"id": "va/m1", "pricing": {"prompt": "0.0000002", "completion": "0.0000006"}, "context_length": 128000, "supported_parameters": ["tools"]},
            today="2026-11-26",
        )
        b1 = fetch_data.classify_model(
            {"id": "vb/m2", "pricing": {"prompt": "0.0000010", "completion": "0.0000030"}, "context_length": 128000, "supported_parameters": ["tools"]},
            today="2026-11-26",
        )
        # Routine price tweak: A is still cheaper than B, same capabilities & context gate
        a2_routine = fetch_data.classify_model(
            {"id": "va/m1", "pricing": {"prompt": "0.00000025", "completion": "0.00000065"}, "context_length": 128000, "supported_parameters": ["tools"]},
            today="2026-11-27",
        )
        # Winner flip: A becomes MORE expensive than B!
        a3_flip = fetch_data.classify_model(
            {"id": "va/m1", "pricing": {"prompt": "0.0000050", "completion": "0.0000100"}, "context_length": 128000, "supported_parameters": ["tools"]},
            today="2026-11-27",
        )
        # Context drop below 64K gate -> changes coding task applicability!
        a4_ctx_drop = fetch_data.classify_model(
            {"id": "va/m1", "pricing": {"prompt": "0.00000025", "completion": "0.00000065"}, "context_length": 32000, "supported_parameters": ["tools"]},
            today="2026-11-27",
        )

        cv1, fv1, _ = content_quality_gate.compute_comparison_versions(a1, b1, cohort_label="Coding", comp_status="active", task_cells={"coding": ("$2", "$10")})
        cv2, fv2, _ = content_quality_gate.compute_comparison_versions(a2_routine, b1, cohort_label="Coding", comp_status="active", task_cells={"coding": ("$3", "$10")})
        cv3, _, _ = content_quality_gate.compute_comparison_versions(a3_flip, b1, cohort_label="Coding", comp_status="active", task_cells={"coding": ("$60", "$10")})
        cv4, _, _ = content_quality_gate.compute_comparison_versions(a4_ctx_drop, b1, cohort_label="Coding", comp_status="active", task_cells={"coding": ("Not Applicable", "$10")})

        self.assertEqual(cv1, cv2, "Routine price update preserving winner & applicability must NOT invalidate approval")
        self.assertNotEqual(fv1, fv2, "fact_version MUST track routine price sync")
        self.assertNotEqual(cv2, cv3, "Price flip changing cheaper model MUST invalidate approval")
        self.assertNotEqual(cv2, cv4, "Context drop changing task applicability MUST invalidate approval")

        # Model-level context gate crossing (<64K vs >=100K) also changes model content_version
        mc1, _, _ = content_quality_gate.compute_model_versions(a2_routine)
        mc2, _, _ = content_quality_gate.compute_model_versions(a4_ctx_drop)
        self.assertNotEqual(mc1, mc2)

    def test_fetch_error_cached_and_expired_cache_cannot_count_as_verified_source(self):
        pol_fetch_err = {
            "providers": [
                {"id": "google_gemini", "verification_status": "FETCH_ERROR_CACHED", "last_verified_date": "2026-11-26", "max_freshness_days": 14}
            ]
        }
        _, _, conf_err = content_quality_gate.compute_free_policy_versions(pol_fetch_err, today="2026-11-27")
        self.assertTrue(any("source_verification_failed:google_gemini:FETCH_ERROR_CACHED" in c for c in conf_err))

        pol_expired = {
            "providers": [
                {"id": "groq", "verification_status": "OFFICIAL_DOCS_VERIFIED", "last_verified_date": "2026-10-01", "max_freshness_days": 14}
            ]
        }
        _, _, conf_exp = content_quality_gate.compute_free_policy_versions(pol_expired, today="2026-11-27")
        self.assertTrue(any("source_verification_expired:groq" in c for c in conf_exp))


class TestTask02And05GateProvenanceAndWorkflowIsolation(unittest.TestCase):
    def test_ad_policy_gate_rejects_stale_other_build_eligibility_ledger_and_html_mismatch(self):
        cfg = load_config()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            dist_dir = root / "dist"
            reports_dir = root / "reports"
            data_dir.mkdir()
            (dist_dir / "privacy").mkdir(parents=True)
            reports_dir.mkdir()
            (data_dir / "models.json").write_text("[]", encoding="utf-8")
            (dist_dir / "ads.txt").write_text("google.com, pub-7981084270242721, DIRECT, f08c47fec0942fa0\n", encoding="utf-8")
            (dist_dir / "robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: https://sysv.cn/sitemap.xml\n", encoding="utf-8")
            (dist_dir / "version.json").write_text(
                json.dumps({"build_id": "new_build_123", "git_commit": "abc1234", "built_at_utc": "2026-11-27T08:00:00Z"}),
                encoding="utf-8",
            )
            (dist_dir / "index.html").write_text("<html><body>Home</body></html>", encoding="utf-8")
            (dist_dir / "privacy" / "index.html").write_text(
                "https://myadcenter.google.com/ https://policies.google.com/technologies/ads https://optout.aboutads.info/ "
                "hello@sysv.cn Google AdSense Cloudflare Web Analytics rel=&quot;sponsored&quot; consent-settings Pending Live Verification",
                encoding="utf-8",
            )
            # Stale eligibility ledger from ANOTHER build_id!
            (reports_dir / "page_ad_eligibility.json").write_text(
                json.dumps(
                    {
                        "build_id": "stale_build_999",
                        "git_commit": "old0000",
                        "generated_at_utc": "2026-11-20T00:00:00Z",
                        "pages": {"/": {"review_status": "unreviewed", "ads_eligible": False, "ads_enabled": False}},
                    }
                ),
                encoding="utf-8",
            )
            review_p = root / "content_review.json"
            review_p.write_text(json.dumps({"records": {}}), encoding="utf-8")

            errs, _ = ad_policy_gate.run_ad_policy_gate(
                cfg=cfg, data_dir=data_dir, dist_dir=dist_dir, reports_dir=reports_dir, review_path=review_p
            )
            joined = "\n".join(errs)
            self.assertIn("build_id mismatch", joined)
            self.assertIn("git_commit mismatch", joined)
            self.assertIn("missing from eligibility ledger", joined)

    def test_pr_preview_workflow_strictly_isolated_from_production_and_secrets(self):
        pr_wf = (ROOT_DIR / ".github" / "workflows" / "pr-preview.yml").read_text(encoding="utf-8")
        prod_wf = (ROOT_DIR / ".github" / "workflows" / "daily-auto.yml").read_text(encoding="utf-8")

        # PR workflow MUST be read-only, isolated, zero secrets, zero fetch/enrich/deploy
        self.assertIn("contents: read", pr_wf)
        self.assertNotIn("contents: write", pr_wf)
        self.assertNotIn("pages: write", pr_wf)
        self.assertNotIn("secrets.", pr_wf)
        self.assertNotIn("scripts.fetch_data", pr_wf)
        self.assertNotIn("scripts.enrich_llm", pr_wf)
        self.assertNotIn("deploy-pages", pr_wf)
        self.assertNotIn("wrangler", pr_wf)
        self.assertIn("SYSV_PREVIEW_MODE: \"1\"", pr_wf)
        self.assertIn("SYSV_PROD_BUILD: \"0\"", pr_wf)
        self.assertIn("sha256sum -c", pr_wf)
        self.assertIn("pr-preview-audit-reports", pr_wf)

        # Production workflow MUST NOT trigger on pull_request and MUST set explicit prod env
        self.assertNotIn("pull_request:", prod_wf)
        self.assertIn("DEPLOY_ENV: production", prod_wf)
        self.assertIn("SYSV_PROD_BUILD: \"1\"", prod_wf)

    def test_mock_ad_placeholder_has_zero_google_network_tags(self):
        cfg = {"monetization": {"adsense_client_id": "ca-pub-7981084270242721", "adsense_slot_top": "111"}}
        mock_html = build_site.render_ad_slot(cfg, "adsense_slot_top", ads_enabled=False, mock_ads=True)
        self.assertIn('data-mock-ad="true"', mock_html)
        self.assertNotIn("adsbygoogle", mock_html)
        self.assertNotIn("googlesyndication", mock_html)
        self.assertEqual(build_site.render_ad_slot(cfg, "adsense_slot_top", ads_enabled=False, mock_ads=False), "")


if __name__ == "__main__":
    unittest.main()
