"""V2.2.1 Unit Tests: GSC Permanent Auth, URL Inspection States, Weighted Metrics, OpenRouter Fee Math & Ad-Free Embeds."""

import base64
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from scripts import check_free_policies, enrich_llm, gsc_report, llm_router, record_deploy_status, tools_builder


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

    def test_tiered_policy_degradation_usable_vs_paused(self):
        # DOC_CHANGED_SIGNAL_STABLE and FETCH_ERROR_CACHED MUST keep calculator usable (no over-strict outage!)
        usable_db = {
            "providers": [
                {"id": "openrouter", "verification_status": "DOC_CHANGED_SIGNAL_STABLE", "model_quotas": [{"rpm": 20, "rpd": 50, "rpd_unlocked": 1000}]},
                {"id": "groq", "verification_status": "FETCH_ERROR_CACHED", "model_quotas": [{"model": "GPT-OSS 120B", "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000}]},
                {"id": "cloudflare_workers_ai", "verification_status": "DOC_CHANGED_SIGNAL_STABLE", "free_daily_neurons": 10000, "model_quotas": [{"model": "@cf/meta/llama-3.2-1b-instruct", "neurons_per_m_input": 2457, "neurons_per_m_output": 18252}]},
                {"id": "cerebras", "verification_status": "FETCH_ERROR_CACHED", "tier_classification": "free_trial"},
            ]
        }
        qu = tools_builder.calculate_free_quota_py(30, 3, 5, 1.0, 1000, 500, "free_50", policy_db=usable_db)
        self.assertTrue(qu["openRouterUsable"])
        self.assertEqual(qu["openRouterStatus"], "VERIFIED_DOC_DRIFT")
        self.assertEqual(qu["openRouterDeficit"], 40)
        self.assertTrue(qu["groqUsable"])
        self.assertEqual(qu["groqStatus"], "VERIFIED_CACHED")
        self.assertTrue(qu["groqFits"])
        self.assertEqual(qu["cfMaxFreeCalls"], 863)
        self.assertFalse(qu["cerebrasPermanentlyFree"])

        # STALE, POLICY_CHANGED_NEEDS_REVIEW, FETCH_ERROR_PERSISTENT, UNKNOWN MUST pause deterministic verdicts
        paused_db = {
            "providers": [
                {"id": "openrouter", "verification_status": "STALE", "model_quotas": [{"rpm": 20, "rpd": 50, "rpd_unlocked": 1000}]},
                {"id": "groq", "verification_status": "POLICY_CHANGED_NEEDS_REVIEW", "model_quotas": [{"model": "GPT-OSS 120B", "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000}]},
                {"id": "cloudflare_workers_ai", "verification_status": "UNKNOWN", "free_daily_neurons": 10000, "model_quotas": [{"model": "@cf/meta/llama-3.2-1b-instruct", "neurons_per_m_input": 2457, "neurons_per_m_output": 18252}]},
                {"id": "cerebras", "verification_status": "FETCH_ERROR_PERSISTENT", "tier_classification": "free_trial"},
            ]
        }
        qp = tools_builder.calculate_free_quota_py(10, 1, 2, 1.0, 1000, 500, "free_50", policy_db=paused_db)
        self.assertFalse(qp["openRouterUsable"])
        self.assertIsNone(qp["openRouterCovered"])
        self.assertFalse(qp["groqUsable"])
        self.assertIsNone(qp["groqFits"])
        self.assertFalse(qp["cfUsable"])
        self.assertIsNone(qp["cfCovered"])
        self.assertFalse(qp["cerebrasUsable"])
        self.assertIsNone(qp["cerebrasPermanentlyFree"])


class TestCumulativeDailyBudgetAndPolicies(unittest.TestCase):
    def test_router_disables_hidden_http_retries_sets_max_tokens_and_gates_pre_request(self):
        from unittest.mock import patch

        captured = {}

        def fake_post(url, payload, headers=None, timeout=45, retries=1):
            captured["retries"] = retries
            captured["max_tokens"] = payload.get("max_tokens")
            return {"choices": [{"message": {"content": '{"summary":"Ok","best_for":["A"],"watch_outs":["B"]}'}}], "usage": {"prompt_tokens": 300, "completion_tokens": 100}}

        fake_providers = [
            {"name": "deepseek", "key": "k1", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "is_paid": "1"},
        ]
        guard = llm_router.DailyBudgetGuard(max_calls=5, max_cost_usd=0.05)
        with patch.object(llm_router, "get_available_providers", return_value=fake_providers), \
             patch.object(llm_router, "http_post_json", side_effect=fake_post):
            parsed, prov, _ = llm_router.generate_json_with_fallback("short prompt", budget_guard=guard)
            self.assertEqual(prov, "deepseek")
            self.assertEqual(captured["retries"], 0)
            self.assertEqual(captured["max_tokens"], llm_router.DEFAULT_MAX_OUTPUT_TOKENS)

        # Pre-request reservation guard: if remaining budget ($0.0001) < estimated max request cost, block BEFORE HTTP!
        tight_guard = llm_router.DailyBudgetGuard(max_calls=5, max_cost_usd=0.0001)
        with patch.object(llm_router, "get_available_providers", return_value=fake_providers), \
             patch.object(llm_router, "http_post_json", side_effect=AssertionError("HTTP should never fire when pre-request budget is insufficient")):
            p_blocked, prov_blocked, _ = llm_router.generate_json_with_fallback("prompt", budget_guard=tight_guard)
            self.assertIsNone(p_blocked)
            self.assertEqual(prov_blocked, "budget-exhausted")
            self.assertEqual(tight_guard.used_calls, 0)

    def test_router_layer_budget_retries_and_unknown_cost_never_zero(self):
        from unittest.mock import patch

        # Unknown cost provider must NEVER default to 0.0
        self.assertIsNone(llm_router.estimate_call_cost_usd("siliconflow", {"prompt_tokens": 500, "completion_tokens": 200}))
        self.assertIsNone(llm_router.estimate_call_cost_usd("openrouter", {"prompt_tokens": 500, "completion_tokens": 200}, model="openai/gpt-4o"))
        self.assertEqual(llm_router.estimate_call_cost_usd("openrouter", {"prompt_tokens": 500, "completion_tokens": 200}, model="qwen/qwen3:free"), 0.0)

        # Router retries: provider 1 spends tokens then fails JSON parse; provider 2 succeeds; BOTH attempts counted!
        fake_providers = [
            {"name": "deepseek", "key": "k1", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "is_paid": "1"},
            {"name": "siliconflow", "key": "k2", "base_url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen2.5-7B-Instruct", "is_paid": "unknown"},
        ]
        responses = [
            ("NOT VALID JSON", {"prompt_tokens": 2000, "completion_tokens": 500}),
            ('{"summary": "Ok", "best_for": ["A"], "watch_outs": ["B"]}', {"prompt_tokens": 1000, "completion_tokens": 300}),
        ]
        guard = llm_router.DailyBudgetGuard(max_calls=2, max_cost_usd=0.05)
        with patch.object(llm_router, "get_available_providers", return_value=fake_providers), \
             patch.object(llm_router, "_call_openai_compat", side_effect=responses):
            parsed, prov, _ = llm_router.generate_json_with_fallback("prompt", budget_guard=guard)
            self.assertEqual(prov, "siliconflow")
            self.assertIsNotNone(parsed)
            # Attempt 1 (deepseek parse_error) + Attempt 2 (siliconflow ok, unknown cost) = 2 attempts!
            self.assertEqual(guard.used_calls, 2)
            self.assertEqual(guard.failed_calls, 1)
            self.assertEqual(guard.unknown_cost_calls, 1)
            self.assertGreater(guard.spent_usd, 0.0)
            self.assertEqual(guard.reserved_unknown_usd, llm_router.UNKNOWN_COST_RESERVE_USD)

            # Next call must immediately short-circuit with budget-exhausted (0 network calls!)
            p2, prov2, _ = llm_router.generate_json_with_fallback("prompt2", budget_guard=guard)
            self.assertIsNone(p2)
            self.assertEqual(prov2, "budget-exhausted")
            self.assertEqual(guard.used_calls, 2)

    def test_cerebras_reclassified_and_policy_drift_distinction(self):
        from pathlib import Path
        import tempfile

        pol = json.loads((Path(__file__).resolve().parent.parent / "data/free_provider_policies.json").read_text(encoding="utf-8"))
        cerebras = next(p for p in pol["providers"] if p["id"] == "cerebras")
        self.assertEqual(cerebras["tier_classification"], "free_trial")
        self.assertTrue(cerebras["credit_card_required"])
        self.assertEqual(cerebras["trial_amount_usd"], 5.0)
        self.assertEqual(cerebras["trial_expiry_days"], 30)

        # Tiered states: STALE, FETCH_ERROR_CACHED (<3), FETCH_ERROR_PERSISTENT (>=3), DOC_CHANGED_SIGNAL_STABLE, POLICY_CHANGED_NEEDS_REVIEW, UNKNOWN
        st_stale, _ = check_free_policies.compute_policy_freshness_status(
            {"last_verified_date": "2026-10-01", "max_freshness_days": 14, "verification_status": "OFFICIAL_DOCS_VERIFIED"},
            today_iso="2026-11-26",
            fetch_ok=True,
        )
        self.assertEqual(st_stale, "STALE")

        st_fetch1, _ = check_free_policies.compute_policy_freshness_status(
            {"last_verified_date": "2026-11-25", "max_freshness_days": 14},
            today_iso="2026-11-26",
            fetch_ok=False,
            consecutive_fetch_errors=1,
        )
        self.assertEqual(st_fetch1, "FETCH_ERROR_CACHED")

        st_fetch3, _ = check_free_policies.compute_policy_freshness_status(
            {"last_verified_date": "2026-11-25", "max_freshness_days": 14},
            today_iso="2026-11-26",
            fetch_ok=False,
            consecutive_fetch_errors=3,
        )
        self.assertEqual(st_fetch3, "FETCH_ERROR_PERSISTENT")

        st_doc, _ = check_free_policies.compute_policy_freshness_status(
            {"last_verified_date": "2026-11-25", "max_freshness_days": 14, "source_content_hash": "aaa", "policy_signal_hash": "sig1"},
            today_iso="2026-11-26",
            fetched_doc_hash="bbb",
            fetched_signal_hash="sig1",
            fetch_ok=True,
        )
        self.assertEqual(st_doc, "DOC_CHANGED_SIGNAL_STABLE")

        st_pol, _ = check_free_policies.compute_policy_freshness_status(
            {"last_verified_date": "2026-11-25", "max_freshness_days": 14, "source_content_hash": "aaa", "policy_signal_hash": "sig1"},
            today_iso="2026-11-26",
            fetched_doc_hash="bbb",
            fetched_signal_hash="sig2",
            fetch_ok=True,
        )
        self.assertEqual(st_pol, "POLICY_CHANGED_NEEDS_REVIEW")

        st_unk, _ = check_free_policies.compute_policy_freshness_status({}, today_iso="2026-11-26")
        self.assertEqual(st_unk, "UNKNOWN")

        # confirm_reverified re-baselines hashes & resets consecutive_fetch_errors cleanly
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "pol.json"
            fp.write_text(json.dumps({"providers": [{"id": "groq", "verification_status": "POLICY_CHANGED_NEEDS_REVIEW", "consecutive_fetch_errors": 3, "pending_source_hash": "h2", "pending_signal_hash": "s2"}]}), encoding="utf-8")
            updated = check_free_policies.confirm_reverified("groq", policy_path=fp)
            self.assertEqual(updated["verification_status"], "OFFICIAL_DOCS_VERIFIED")
            self.assertEqual(updated["consecutive_fetch_errors"], 0)
            self.assertEqual(updated["source_content_hash"], "h2")
            self.assertEqual(updated["policy_signal_hash"], "s2")

    def test_commercial_data_strictly_removed_from_public_repo_workflows(self):
        import tempfile
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        wf_dir = repo_root / ".github/workflows"
        # ZERO GSC workflows or GSC artifacts allowed in public repository!
        for wf_file in wf_dir.glob("*.yml"):
            txt = wf_file.read_text(encoding="utf-8")
            self.assertNotIn("gsc_report", txt, f"Public repo workflow must not run gsc_report: {wf_file}")
            self.assertNotIn("gsc-weekly-report", txt, f"Public repo workflow must not upload GSC artifacts: {wf_file}")

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ops_metrics.json"
            p.write_text(json.dumps({"daily": [{"date": "2026-11-26", "estimated_revenue_usd": 12.34, "gsc_clicks": 99}]}), encoding="utf-8")
            entry = record_deploy_status.record_publish_status(success=True, git_commit="abc1234", ops_path=p)
            self.assertTrue(entry["publish_success"])
            self.assertNotIn("estimated_revenue_usd", entry)
            self.assertNotIn("gsc_clicks", entry)
            self.assertIn("private/commercial_ledger.json", entry["commercial_data_location"])


if __name__ == "__main__":
    unittest.main()
