"""V2.2.1 Unit Tests: GSC Permanent Auth, URL Inspection States, Weighted Metrics, OpenRouter Fee Math & Ad-Free Embeds."""

import base64
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from scripts import check_free_policies, enrich_llm, gsc_report, record_deploy_status, tools_builder


class TestGSCAuthInspectionAndMath(unittest.TestCase):
    def test_service_account_jwt_rs256_generation(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        jwt_str = gsc_report.build_service_account_jwt({"client_email": "gsc@test.iam.gserviceaccount.com", "private_key": pem})
        parts = jwt_str.split(".")
        self.assertEqual(len(parts), 3)
        hdr = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
        clm = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        self.assertEqual(hdr["alg"], "RS256")
        self.assertEqual(clm["iss"], "gsc@test.iam.gserviceaccount.com")
        self.assertIn("webmasters.readonly", clm["scope"])

    def test_url_inspection_state_mapping(self):
        self.assertEqual(
            gsc_report.map_inspection_result({"inspectionResult": {"indexStatusResult": {"verdict": "PASS", "coverageState": "Submitted and indexed"}}})["state"],
            "INDEXED",
        )
        self.assertEqual(
            gsc_report.map_inspection_result({"inspectionResult": {"indexStatusResult": {"verdict": "NEUTRAL", "coverageState": "Discovered - currently not indexed"}}})["state"],
            "DISCOVERED_NOT_INDEXED",
        )
        self.assertEqual(
            gsc_report.map_inspection_result({"inspectionResult": {"indexStatusResult": {"verdict": "NEUTRAL", "coverageState": "Crawled - currently not indexed"}}})["state"],
            "CRAWLED_NOT_INDEXED",
        )
        self.assertEqual(
            gsc_report.map_inspection_result({"inspectionResult": {"indexStatusResult": {"verdict": "FAIL", "coverageState": "URL is unknown to Google"}}})["state"],
            "NOT_INDEXED",
        )
        self.assertEqual(gsc_report.map_inspection_result({})["state"], "UNKNOWN")

    def test_weighted_position_uninspected_unknown_and_revenue_unknown(self):
        page_rows = [
            {"keys": ["https://sysv.cn/"], "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 5.0},
            {"keys": ["https://sysv.cn/tools/openrouter-cost-calculator/"], "clicks": 0, "impressions": 300, "ctr": 0.0, "position": 15.0},
        ]
        pub = {
            "/": {"index_policy": "index", "status": "active"},
            "/tools/openrouter-cost-calculator/": {"index_policy": "index", "status": "active"},
            "/models/uninspected-1/": {"index_policy": "index", "status": "active"},  # No gsc_inspection -> UNKNOWN!
            "/models/api-err/": {"index_policy": "index", "status": "active", "gsc_inspection": {"state": "API_ERROR"}},
        }
        rep = gsc_report.build_weekly_report(
            site_rows=[],
            page_rows=page_rows,
            query_rows=[],
            published_pages=pub,
            start_date="2026-10-25",
            end_date="2026-11-22",
            auth_mode="service_account_jwt",
        )
        # Impression-weighted position = (5*100 + 15*300) / 400 = 12.5 (NOT simple average 10.0!)
        self.assertEqual(rep["site_totals"]["weighted_avg_position"], 12.5)
        self.assertEqual(rep["site_totals"]["ctr"], 0.025)
        self.assertEqual(rep["diagnostic_buckets"]["uninspected_unknown"], 1)
        self.assertEqual(rep["diagnostic_buckets"]["inspection_api_error"], 1)
        self.assertEqual(rep["diagnostic_buckets"]["clicks_revenue_unknown"], 1)
        self.assertEqual(rep["diagnostic_buckets"]["impressions_zero_clicks"], 1)
        self.assertIn("REVENUE_UNKNOWN", rep["revenue_attribution_policy"])


class TestOpenRouterBillingMath(unittest.TestCase):
    def test_card_min_fee_and_percentage_and_byok_tiers(self):
        # $5 spend -> 5.5% = $0.275 < $0.80 -> $0.80 min applies
        b1 = tools_builder.calculate_openrouter_billing_py(5.0, "card", 0.0)
        self.assertTrue(b1["minFeeTriggered"])
        self.assertEqual(b1["platformFee"], 0.80)
        self.assertEqual(b1["totalCashOutlay"], 5.80)

        # $25 top-up batch -> 5.5% = $1.375 >= $0.80 -> min NOT triggered
        b2 = tools_builder.calculate_openrouter_billing_py(2.0, "card", 25.0)
        self.assertFalse(b2["minFeeTriggered"])
        self.assertAlmostEqual(b2["platformFee"], 1.375)
        self.assertAlmostEqual(b2["totalCashOutlay"], 26.375)

        # Crypto 5% flat
        b3 = tools_builder.calculate_openrouter_billing_py(10.0, "crypto", 0.0)
        self.assertAlmostEqual(b3["platformFee"], 0.50)

        # BYOK within $25k free allowance -> $0 fee; $35k -> 5% of $10k = $500
        b4 = tools_builder.calculate_openrouter_billing_py(20000.0, "byok", 0.0)
        self.assertEqual(b4["platformFee"], 0.0)
        b5 = tools_builder.calculate_openrouter_billing_py(35000.0, "byok", 0.0)
        self.assertEqual(b5["platformFee"], 500.0)
        self.assertEqual(b5["totalCashOutlay"], 35500.0)


class TestEnrichmentBenchmarkGuard(unittest.TestCase):
    def test_llm_enrichment_rejects_unverified_benchmark_word(self):
        bad = {"summary": "Top coding benchmark performer.", "best_for": ["A"], "watch_outs": ["B"]}
        self.assertFalse(enrich_llm._valid_enrichment(bad, pricing_type="paid"))
        good = {"summary": "Low-latency paid coding model.", "best_for": ["A"], "watch_outs": ["B"]}
        self.assertTrue(enrich_llm._valid_enrichment(good, pricing_type="paid"))


class TestFreeQuotaCalculator(unittest.TestCase):
    def test_openrouter_rpd_vs_rpm_groq_cloudflare_neurons_and_cerebras_trial(self):
        # 30 jobs x 3 calls = 90 calls/day, 5 peak RPM, 1000 in / 500 out
        q1 = tools_builder.calculate_free_quota_py(30, 3, 5, 1.0, 1000, 500, "free_50")
        self.assertEqual(q1["requiredCalls"], 90)
        self.assertEqual(q1["requiredTokens"], 135000)
        self.assertFalse(q1["openRouterRpdCovered"])
        self.assertTrue(q1["openRouterRpmCovered"])
        self.assertFalse(q1["openRouterCovered"])
        self.assertEqual(q1["openRouterDeficit"], 40)
        self.assertTrue(q1["groqFits"])
        # Cloudflare Workers AI @cf/meta/llama-3.2-1b-instruct: 1000 in + 500 out = 11.583 Neurons/call -> 863 calls/day
        self.assertAlmostEqual(q1["cfNeuronsPerCall"], 11.583, places=3)
        self.assertEqual(q1["cfMaxFreeCalls"], 863)
        self.assertTrue(q1["cfCovered"])
        self.assertFalse(q1["cerebrasPermanentlyFree"])

        # 40 calls/day concentrated in 1 minute (40 RPM): RPD covered (40<=50), but RPM exceeds 20!
        q_burst = tools_builder.calculate_free_quota_py(1, 40, 40, 1.0, 1000, 500, "free_50")
        self.assertTrue(q_burst["openRouterRpdCovered"])
        self.assertFalse(q_burst["openRouterRpmCovered"])
        self.assertFalse(q_burst["openRouterCovered"])
        self.assertFalse(q_burst["groqRpmCovered"])
        self.assertFalse(q_burst["groqTpmCovered"])

        # Unlocked 1,000 RPD ($9/$10 buffer) covers 90 calls/day at 5 RPM
        q2 = tools_builder.calculate_free_quota_py(30, 3, 5, 1.0, 1000, 500, "paid_1000")
        self.assertTrue(q2["openRouterCovered"])
        self.assertEqual(q2["openRouterDeficit"], 0)

        # Negative/zero inputs never produce negative calls
        q4 = tools_builder.calculate_free_quota_py(-5, 3, -2, 1.0, 1000, 500, "free_50")
        self.assertEqual(q4["requiredCalls"], 0)
        self.assertTrue(q4["openRouterCovered"])


class TestCumulativeDailyBudgetAndPolicies(unittest.TestCase):
    def test_cumulative_daily_budget_across_multiple_runs_and_failed_calls(self):
        from unittest.mock import patch

        def make_models(n):
            return [
                {
                    "id": f"prov/m-{i}",
                    "name": f"M {i}",
                    "pricing_type": "paid",
                    "pricing_label": "Paid",
                    "is_realtime": True,
                    "prompt_price_per_m": 0.5,
                    "completion_price_per_m": 1.0,
                    "context_length": 64000,
                    "modality": "text->text",
                    "capabilities": {"tools": True},
                    "task_category": "general",
                    "rate_limit_note": "",
                    "description": f"desc {i}",
                    "status": "active",
                }
                for i in range(n)
            ]

        ledger = {"calls": 0, "failed": 0, "cost": 0.0}

        def record_cb(*, calls, failed, tokens_in, tokens_out, cost_usd):
            ledger["calls"] += calls
            ledger["failed"] += failed
            ledger["cost"] = round(ledger["cost"] + cost_usd, 6)

        call_idx = {"n": 0}

        def fake_gen(_prompt):
            call_idx["n"] += 1
            # Every 3rd call returns invalid enrichment (e.g. 'benchmark' word) — must STILL consume budget!
            if call_idx["n"] % 3 == 0:
                return (
                    {"summary": "Best benchmark model", "best_for": ["A"], "watch_outs": ["B"]},
                    "deepseek",
                    {"prompt_tokens": 2000, "completion_tokens": 500},
                )
            return (
                {"summary": "Clean model note.", "best_for": ["A"], "watch_outs": ["B"]},
                "deepseek",
                {"prompt_tokens": 2000, "completion_tokens": 500},
            )

        with patch.object(enrich_llm, "get_available_providers", return_value=["deepseek"]), \
             patch.object(enrich_llm, "generate_json_with_fallback", side_effect=fake_gen), \
             patch.object(enrich_llm, "estimate_call_cost_usd", return_value=0.008), \
             patch.object(enrich_llm.time, "sleep", return_value=None):
            # Run 1: already_used=0, max_calls=10, max_cost=0.05 -> stops at 7 calls? 6*0.008=0.048 (<0.05), 7th->0.056 -> stops after 7? Let's test 4 calls first:
            r1 = enrich_llm.run_enrichment(
                make_models(4), [], max_calls=10, max_cost_usd=0.05, prompt_ver="v2.2",
                already_used_calls=ledger["calls"], already_spent_usd=ledger["cost"], on_call_recorded=record_cb
            )
            self.assertEqual(r1["llm_calls"], 4)
            self.assertEqual(r1["llm_failed_calls"], 1)
            self.assertEqual(ledger["calls"], 4)
            self.assertAlmostEqual(ledger["cost"], 0.032)

            # Run 2 (same day, second push/rerun): remaining budget is $0.018 -> at $0.008/call only 3 calls allowed (0.008, 0.016, 0.024>=0.018 stops!)
            r2 = enrich_llm.run_enrichment(
                make_models(10), [], max_calls=10, max_cost_usd=0.05, prompt_ver="v2.2",
                already_used_calls=ledger["calls"], already_spent_usd=ledger["cost"], on_call_recorded=record_cb
            )
            self.assertEqual(r2["llm_calls"], 3)
            self.assertEqual(ledger["calls"], 7)

            # Run 3 (same day): budget already exhausted ($0.056 >= $0.05) -> MUST make 0 LLM calls!
            r3 = enrich_llm.run_enrichment(
                make_models(10), [], max_calls=10, max_cost_usd=0.05, prompt_ver="v2.2",
                already_used_calls=ledger["calls"], already_spent_usd=ledger["cost"], on_call_recorded=record_cb
            )
            self.assertEqual(r3["llm_calls"], 0)
            self.assertEqual(ledger["calls"], 7)

    def test_cerebras_reclassified_and_policy_freshness_honesty(self):
        from pathlib import Path
        import json

        pol = json.loads((Path(__file__).resolve().parent.parent / "data/free_provider_policies.json").read_text(encoding="utf-8"))
        cerebras = next(p for p in pol["providers"] if p["id"] == "cerebras")
        self.assertEqual(cerebras["tier_classification"], "free_trial")
        self.assertTrue(cerebras["credit_card_required"])
        self.assertEqual(cerebras["trial_amount_usd"], 5.0)
        self.assertEqual(cerebras["trial_expiry_days"], 30)

        # Freshness & drift rules
        st_stale, _ = check_free_policies.compute_policy_freshness_status(
            {"last_verified_date": "2026-10-01", "max_freshness_days": 14, "verification_status": "OFFICIAL_DOCS_VERIFIED"},
            today_iso="2026-11-25",
            fetch_ok=False,
        )
        self.assertEqual(st_stale, "STALE")

        st_chg, chg = check_free_policies.compute_policy_freshness_status(
            {"last_verified_date": "2026-11-25", "max_freshness_days": 14, "source_content_hash": "aaa"},
            today_iso="2026-11-25",
            fetched_hash="bbb",
            fetch_ok=True,
        )
        self.assertEqual(st_chg, "CHANGED_NEEDS_REVIEW")
        self.assertTrue(chg)

        st_unk, _ = check_free_policies.compute_policy_freshness_status({}, today_iso="2026-11-25")
        self.assertEqual(st_unk, "UNKNOWN")

    def test_record_deploy_status_distinguishes_revenue_unknown(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ops_metrics.json"
            entry = record_deploy_status.record_publish_status(success=True, git_commit="abc1234", ops_path=p)
            self.assertTrue(entry["publish_success"])
            self.assertEqual(entry["revenue_status"], "REVENUE_UNKNOWN")
            self.assertIsNone(entry["estimated_revenue_usd"])
            self.assertIsNone(entry["bank_settled_usd"])
            self.assertIsNone(entry["net_profit_usd"])


if __name__ == "__main__":
    unittest.main()
