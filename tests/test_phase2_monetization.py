"""Phase 2 Monetization Readiness, Ad Eligibility, Content Review & Compliance Test Suite.

Covers TASK-01 through TASK-09:
- Independent 5-field eligibility model (indexable, ads_eligible, ads_enabled, review_status, verification_enabled)
- Three runtime modes (off / verification / live) + preview/local auto-disable
- Traceable manual review ledger (rejects bot/auto approvals, invalidates on material changes while tolerating routine price syncs)
- Privacy policy required disclosures & zero ad/consent-gated scripts on Privacy/Terms/Contact/404/Embed
- ads.txt format & publisher ID validation + unknown publisher ID blocking
- Affiliate placement & rel="sponsored" enforcement (high-intent pages only; excluded from homepage & embeds)
- Mock preview ad placeholder isolation (zero external Google ad requests in preview/mock mode)
- Machine-readable audit report & manual review queue generation
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import ad_policy_gate, build_site, content_quality_gate, fetch_data


class TestAdEligibilityAndModes(unittest.TestCase):
    def _cfg(self, mode="verification", verify=True):
        return {
            "site_name": "SysV",
            "domain": "sysv.cn",
            "base_url": "https://sysv.cn",
            "tagline": "Radar",
            "monetization": {
                "ad_mode": mode,
                "verification_enabled": verify,
                "verification_method": "ads_txt_and_meta",
                "keep_verification_when_off": True,
                "adsense_client_id": "ca-pub-7981084270242721",
                "adsense_slot_top": "1234567890",
            },
        }

    def test_three_ad_modes_and_preview_blocks_live_ads(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            rt_off = content_quality_gate.resolve_ad_runtime_config(self._cfg("off"))
            self.assertEqual(rt_off["effective_ad_mode"], "off")
            self.assertFalse(rt_off["allow_live_ad_requests"])
            self.assertTrue(rt_off["verification_enabled"])

            rt_ver = content_quality_gate.resolve_ad_runtime_config(self._cfg("verification"))
            self.assertEqual(rt_ver["effective_ad_mode"], "verification")
            self.assertFalse(rt_ver["allow_live_ad_requests"])
            self.assertTrue(rt_ver["verification_enabled"])

            rt_live = content_quality_gate.resolve_ad_runtime_config(self._cfg("live"))
            self.assertEqual(rt_live["effective_ad_mode"], "live")
            self.assertTrue(rt_live["allow_live_ad_requests"])

        # Preview / PR environment MUST force live mode off
        with mock.patch.dict(os.environ, {"SYSV_PREVIEW_MODE": "1"}, clear=True):
            rt_prev = content_quality_gate.resolve_ad_runtime_config(self._cfg("live"))
            self.assertTrue(rt_prev["preview_mode"])
            self.assertEqual(rt_prev["effective_ad_mode"], "off")
            self.assertFalse(rt_prev["allow_live_ad_requests"])

    def test_new_or_unreviewed_pages_default_to_no_ads_without_affecting_indexable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            rt_live = content_quality_gate.resolve_ad_runtime_config(self._cfg("live"))
        meta = content_quality_gate.evaluate_page_ad_eligibility(
            url_path="/models/new-auto-page/",
            page_type="model_detail",
            index_policy="index",
            status="active",
            content_version="cv1",
            fact_version="fv1",
            review_ledger={},
            runtime_cfg=rt_live,
            has_auto_generated_copy=True,
        )
        self.assertTrue(meta["indexable"])
        self.assertEqual(meta["review_status"], "unreviewed")
        self.assertFalse(meta["ads_eligible"])
        self.assertFalse(meta["ads_enabled"])
        self.assertIn("unreviewed_auto_generated_content", meta["block_reasons"])

    def test_prohibited_pages_never_eligible_even_if_reviewed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            rt_live = content_quality_gate.resolve_ad_runtime_config(self._cfg("live"))
        fake_approved = {
            "/404.html": {"review_status": "approved", "reviewer": "owner", "reviewed_at": "2026-11-27T00:00:00Z", "review_scope": "test", "content_version": "cv1"},
            "/privacy/": {"review_status": "approved", "reviewer": "owner", "reviewed_at": "2026-11-27T00:00:00Z", "review_scope": "test", "content_version": "cv1"},
            "/embed/openrouter-cost-calculator/": {"review_status": "approved", "reviewer": "owner", "reviewed_at": "2026-11-27T00:00:00Z", "review_scope": "test", "content_version": "cv1"},
        }
        for url, ptype, pol in [("/404.html", "error", "noindex"), ("/privacy/", "legal", "noindex"), ("/embed/openrouter-cost-calculator/", "embed", "noindex")]:
            meta = content_quality_gate.evaluate_page_ad_eligibility(
                url_path=url, page_type=ptype, index_policy=pol, status="active", content_version="cv1", fact_version="fv1", review_ledger=fake_approved, runtime_cfg=rt_live
            )
            self.assertFalse(meta["ads_eligible"])
            self.assertFalse(meta["ads_enabled"])
            self.assertFalse(meta["verification_enabled"])


class TestTraceableContentReviewInvalidation(unittest.TestCase):
    def test_bot_reviewer_rejected_and_material_change_invalidates_approval(self):
        # Automated/bot reviewer MUST NOT pass approval validation
        st_bot, reasons_bot = content_quality_gate.validate_manual_approval(
            {"review_status": "approved", "reviewer": "bot", "reviewed_at": "2026-11-27T00:00:00Z", "review_scope": "core", "content_version": "abc"},
            current_content_version="abc",
        )
        self.assertEqual(st_bot, "needs_review")
        self.assertIn("missing_or_automated_reviewer", reasons_bot)

        # Valid human review with matching content_version passes
        st_ok, reasons_ok = content_quality_gate.validate_manual_approval(
            {"review_status": "approved", "reviewer": "site-owner", "reviewed_at": "2026-11-27T00:00:00Z", "review_scope": "core", "content_version": "abc", "open_issues": []},
            current_content_version="abc",
        )
        self.assertEqual(st_ok, "approved")
        self.assertEqual(reasons_ok, [])

        # Material content change invalidates old approval to needs_review
        st_changed, reasons_changed = content_quality_gate.validate_manual_approval(
            {"review_status": "approved", "reviewer": "site-owner", "reviewed_at": "2026-11-27T00:00:00Z", "review_scope": "core", "content_version": "abc", "open_issues": []},
            current_content_version="xyz",
        )
        self.assertEqual(st_changed, "needs_review")
        self.assertIn("material_content_changed_since_review", reasons_changed)

    def test_routine_price_update_preserves_content_version_while_capability_change_invalidates(self):
        m1 = fetch_data.classify_model(
            {"id": "vendor/m", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 128000, "supported_parameters": ["tools"]},
            today="2026-11-26",
        )
        m2_price_only = fetch_data.classify_model(
            {"id": "vendor/m", "pricing": {"prompt": "0.0000008", "completion": "0.0000018"}, "context_length": 128000, "supported_parameters": ["tools"]},
            today="2026-11-27",
        )
        m3_cap_change = fetch_data.classify_model(
            {"id": "vendor/m", "pricing": {"prompt": "0.0000008", "completion": "0.0000018"}, "context_length": 128000, "supported_parameters": []},
            today="2026-11-27",
        )
        c1, f1, _ = content_quality_gate.compute_model_versions(m1)
        c2, f2, _ = content_quality_gate.compute_model_versions(m2_price_only)
        c3, _, _ = content_quality_gate.compute_model_versions(m3_cap_change)
        # Routine price sync changes fact_version only (no forced daily manual re-review)
        self.assertEqual(c1, c2)
        self.assertNotEqual(f1, f2)
        # Major capability change alters content_version -> invalidates prior human approval!
        self.assertNotEqual(c2, c3)

    def test_committed_review_ledger_has_zero_fabricated_approvals(self):
        ledger = content_quality_gate.load_review_ledger()
        for url, rec in ledger.items():
            self.assertNotEqual(
                str(rec.get("review_status") or "").lower(),
                "approved",
                f"Committed Phase 2 ledger must not fabricate manual approval for {url}",
            )


class TestAdPolicyGateAndMockPreview(unittest.TestCase):
    def test_ads_txt_validator_catches_html_wrong_pub_and_accepts_valid(self):
        ok, errs = ad_policy_gate.validate_ads_txt_content(
            "google.com, pub-7981084270242721, DIRECT, f08c47fec0942fa0\n",
            "ca-pub-7981084270242721",
        )
        self.assertTrue(ok, errs)

        ok_html, _ = ad_policy_gate.validate_ads_txt_content("<!DOCTYPE html><html>404</html>", "ca-pub-7981084270242721")
        self.assertFalse(ok_html)

        ok_wrong, errs_wrong = ad_policy_gate.validate_ads_txt_content(
            "google.com, pub-1111111111111111, DIRECT, f08c47fec0942fa0\n",
            "ca-pub-7981084270242721",
        )
        self.assertFalse(ok_wrong)
        self.assertIn("Unknown publisher ID", "\n".join(errs_wrong))

    def test_ad_policy_gate_catches_leaks_unknown_pub_missing_sponsored_and_bad_privacy(self):
        cfg = {
            "site_name": "SysV",
            "domain": "sysv.cn",
            "base_url": "https://sysv.cn",
            "monetization": {"ad_mode": "verification", "verification_enabled": True, "adsense_client_id": "ca-pub-7981084270242721"},
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            dist_dir = root / "dist"
            reports_dir = root / "reports"
            data_dir.mkdir()
            (dist_dir / "privacy").mkdir(parents=True)
            reports_dir.mkdir()
            (data_dir / "models.json").write_text("[]", encoding="utf-8")
            (dist_dir / "ads.txt").write_text("<html>not ads.txt</html>", encoding="utf-8")
            (dist_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
            (dist_dir / "index.html").write_text(
                '<html><head><script src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9999999999999999"></script></head>'
                '<body><a href="https://www.vultr.com/?ref=9938124">Vultr</a></body></html>',
                encoding="utf-8",
            )
            (dist_dir / "privacy" / "index.html").write_text("<html><body>Short privacy</body></html>", encoding="utf-8")
            (reports_dir / "page_ad_eligibility.json").write_text(
                json.dumps({"pages": {"/": {"review_status": "unreviewed", "ads_eligible": False, "ads_enabled": False}}}),
                encoding="utf-8",
            )
            review_p = root / "content_review.json"
            review_p.write_text(json.dumps({"records": {}}), encoding="utf-8")

            errs, report = ad_policy_gate.run_ad_policy_gate(
                cfg=cfg, data_dir=data_dir, dist_dir=dist_dir, reports_dir=reports_dir, review_path=review_p
            )
            joined = "\n".join(errs)
            self.assertFalse(report["passed"])
            self.assertIn("Ad script leaked", joined)
            self.assertIn("Unknown publisher ID", joined)
            self.assertIn("Affiliate policy violations", joined)
            self.assertIn("ads.txt contains HTML", joined)
            self.assertIn("Privacy policy missing required disclosures", joined)
            self.assertTrue((reports_dir / "ad_policy_audit.json").exists())

    def test_mock_ad_placeholder_has_zero_google_network_tags(self):
        cfg = {"monetization": {"adsense_client_id": "ca-pub-7981084270242721", "adsense_slot_top": "111"}}
        mock_html = build_site.render_ad_slot(cfg, "adsense_slot_top", ads_enabled=False, mock_ads=True)
        self.assertIn("data-mock-ad=\"true\"", mock_html)
        self.assertNotIn("adsbygoogle", mock_html)
        self.assertNotIn("googlesyndication", mock_html)

        off_html = build_site.render_ad_slot(cfg, "adsense_slot_top", ads_enabled=False, mock_ads=False)
        self.assertEqual(off_html, "")


if __name__ == "__main__":
    unittest.main()
