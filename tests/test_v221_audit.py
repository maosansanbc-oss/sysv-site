"""V2.2.1 Unit Tests: GSC Permanent Auth, URL Inspection States, Weighted Metrics, OpenRouter Fee Math & Ad-Free Embeds."""

import base64
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from scripts import enrich_llm, gsc_report, tools_builder


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


if __name__ == "__main__":
    unittest.main()
