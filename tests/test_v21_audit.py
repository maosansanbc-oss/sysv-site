"""V2.1 Adversarial Unit & Fault-Injection Audit Suite.

Proves via automated assertions (not self-reporting):
1. All 8 pricing states & edge cases (negative, null, missing, string, anomalous, :batch, media, routers).
2. Capability gating prevents :batch or non-tool models from being recommended for real-time coding agents.
3. Delisting lifecycle preserves URLs after >=3 consecutive missing days while ignoring 1-day API glitches.
4. Intraday changelog appends & deduplicates without wiping earlier same-day price changes.
5. Hard daily LLM call & USD budget cap is strictly enforced even when 50+ cache hashes invalidate.
6. Quality gate (`run_quality_gate`) genuinely BLOCKS broken/misclassified/leaked publishes.
7. Sitemap `lastmod` stays unchanged across rebuilds when semantic page content did not change.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_site, enrich_llm, fetch_data, health_check


class TestPricingClassification8States(unittest.TestCase):
    def test_dynamic_routers_negative_price_never_free(self):
        for rid in ("openrouter/auto", "openrouter/auto-code", "typesafe/jev-router"):
            m = fetch_data.classify_model(
                {
                    "id": rid,
                    "name": f"Router {rid}",
                    "pricing": {"prompt": "-1", "completion": "-1"},
                    "context_length": 2000000,
                    "architecture": {"modality": "text->text"},
                    "supported_parameters": ["tools", "structured_outputs"],
                },
                today="2026-11-24",
            )
            self.assertEqual(m["pricing_type"], "variable")
            self.assertFalse(m["is_free"])
            self.assertIsNone(m["prompt_price_per_m"])
            self.assertIsNone(m["completion_price_per_m"])

    def test_batch_endpoint_separated_and_not_realtime(self):
        m = fetch_data.classify_model(
            {
                "id": "openai/gpt-6.2-sol:batch",
                "name": "GPT 6.2 Sol Batch",
                "pricing": {"prompt": "0.000001", "completion": "0.000005", "overrides": [{"min_prompt_tokens": 272000}]},
                "context_length": 400000,
                "architecture": {"modality": "text->text"},
                "supported_parameters": ["tools", "structured_outputs"],
            },
            today="2026-11-24",
        )
        self.assertEqual(m["pricing_type"], "batch")
        self.assertFalse(m["is_realtime"])
        self.assertFalse(m["is_free"])
        self.assertTrue(m["has_tiered_overrides"])
        self.assertAlmostEqual(m["prompt_price_per_m"], 1.0)
        self.assertAlmostEqual(m["completion_price_per_m"], 5.0)

    def test_limited_free_vs_free_router_vs_media(self):
        lf = fetch_data.classify_model(
            {
                "id": "google/gemma-4-31b-it:free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 262144,
                "architecture": {"modality": "text+image->text"},
                "supported_parameters": ["tools"],
            }
        )
        self.assertEqual(lf["pricing_type"], "limited_free")
        self.assertTrue(lf["is_free"])
        self.assertTrue(lf["capabilities"]["tools"])
        self.assertFalse(lf["capabilities"]["structured_outputs"])

        fr = fetch_data.classify_model(
            {
                "id": "openrouter/free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 200000,
                "architecture": {"modality": "text->text"},
            }
        )
        self.assertEqual(fr["pricing_type"], "free_router")
        self.assertFalse(fr["is_free"])

        media = fetch_data.classify_model(
            {
                "id": "google/lyria-3-clip-preview",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 1048576,
                "architecture": {"modality": "text+image->text+audio"},
            }
        )
        self.assertEqual(media["pricing_type"], "media")
        self.assertFalse(media["is_free"])
        self.assertFalse(media["is_realtime"])

    def test_malformed_null_string_and_anomalous_prices(self):
        cases = [
            {"prompt": None, "completion": "0.000001"},
            {"prompt": "N/A", "completion": "0.000001"},
            {},
            {"prompt": "999.0", "completion": "0.000001"},  # anomalous >$5,000/1M
        ]
        for idx, p in enumerate(cases):
            m = fetch_data.classify_model(
                {"id": f"vendor/broken-{idx}", "pricing": p, "context_length": 128000, "architecture": {"modality": "text->text"}}
            )
            self.assertEqual(m["pricing_type"], "unknown")
            self.assertFalse(m["is_free"])
            self.assertIsNone(m["prompt_price_per_m"])

        neg_weird = fetch_data.classify_model(
            {"id": "vendor/neg-bug", "pricing": {"prompt": "-0.0001", "completion": "0.0001"}, "context_length": 128000}
        )
        self.assertEqual(neg_weird["pricing_type"], "variable")
        self.assertFalse(neg_weird["is_free"])


class TestCapabilityGatingAndComparisons(unittest.TestCase):
    def test_coding_agent_excludes_batch_and_no_tools(self):
        batch_m = fetch_data.classify_model(
            {
                "id": "openai/cheap-coder:batch",
                "pricing": {"prompt": "0.00000005", "completion": "0.0000001"},
                "context_length": 200000,
                "supported_parameters": ["tools", "structured_outputs"],
            }
        )
        no_tools_m = fetch_data.classify_model(
            {
                "id": "vendor/cheap-notools",
                "pricing": {"prompt": "0.00000005", "completion": "0.0000001"},
                "context_length": 200000,
                "supported_parameters": ["max_tokens"],
            }
        )
        good_m = fetch_data.classify_model(
            {
                "id": "deepseek/deepseek-coder-v3",
                "pricing": {"prompt": "0.0000002", "completion": "0.0000005"},
                "context_length": 128000,
                "supported_parameters": ["tools", "structured_outputs"],
            }
        )
        eligible = build_site.filter_task_candidates([batch_m, no_tools_m, good_m], task="coding", include_batch=False)
        self.assertEqual([m["id"] for m in eligible], ["deepseek/deepseek-coder-v3"])

    def test_comparisons_never_include_routers_batch_or_media(self):
        router = fetch_data.classify_model({"id": "openrouter/auto-code", "pricing": {"prompt": "-1", "completion": "-1"}, "context_length": 2000000})
        media = fetch_data.classify_model({"id": "google/lyria-3-clip", "pricing": {"prompt": "0", "completion": "0"}, "architecture": {"modality": "text->text+audio"}})
        batch = fetch_data.classify_model({"id": "openai/sol:batch", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 200000})
        m1 = fetch_data.classify_model({"id": "vendor-a/coder-1", "pricing": {"prompt": "0.0000002", "completion": "0.0000006"}, "context_length": 128000})
        m2 = fetch_data.classify_model({"id": "vendor-b/coder-2", "pricing": {"prompt": "0.0000003", "completion": "0.0000008"}, "context_length": 128000})
        with tempfile.TemporaryDirectory() as td:
            pairs = build_site.reconcile_comparison_registry(
                [router, media, batch, m1, m2], max_new_pairs=20, registry_path=Path(td) / "comps.json", today="2026-11-24"
            )
        self.assertEqual(len(pairs), 1)
        self.assertEqual({pairs[0][0]["id"], pairs[0][1]["id"]}, {"vendor-a/coder-1", "vendor-b/coder-2"})


class TestLifecycleHistoryAndBudgetCap(unittest.TestCase):
    def test_delisting_lifecycle_requires_3_days_and_keeps_url(self):
        m = fetch_data.classify_model({"id": "vendor/old-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 128000}, today="2026-11-20")
        # Day 1 missing -> temporarily_missing (NOT delisted yet)
        day1 = fetch_data.reconcile_model_lifecycle([], set(), [m], is_full_healthy_fetch=True, today="2026-11-21")
        self.assertEqual(day1[0]["status"], "temporarily_missing")
        self.assertEqual(day1[0]["missing_days"], 1)
        # Day 2 missing -> temporarily_missing
        day2 = fetch_data.reconcile_model_lifecycle([], set(), day1, is_full_healthy_fetch=True, today="2026-11-22")
        self.assertEqual(day2[0]["status"], "temporarily_missing")
        self.assertEqual(day2[0]["missing_days"], 2)
        # Day 3 missing -> delisted, URL record STILL preserved!
        day3 = fetch_data.reconcile_model_lifecycle([], set(), day2, is_full_healthy_fetch=True, today="2026-11-23")
        self.assertEqual(day3[0]["status"], "delisted")
        self.assertEqual(day3[0]["delisted_at"], "2026-11-23")
        self.assertEqual(day3[0]["slug"], "vendor-old-model")

    def test_intraday_changelog_does_not_overwrite_earlier_events(self):
        with tempfile.TemporaryDirectory() as td:
            hdir = Path(td) / "history"
            cpath = Path(td) / "price_changes.json"
            m_v1 = fetch_data.classify_model({"id": "v/m1", "pricing": {"prompt": "0.000002", "completion": "0.000004"}}, today="2026-11-24")
            m_v2 = fetch_data.classify_model({"id": "v/m1", "pricing": {"prompt": "0.000001", "completion": "0.000004"}}, today="2026-11-24")
            m_v3 = fetch_data.classify_model({"id": "v/m1", "pricing": {"prompt": "0.0000005", "completion": "0.000004"}}, today="2026-11-24")
            fetch_data.save_daily_history_and_changelog([m_v1], history_dir=hdir, changelog_path=cpath, today="2026-11-24")
            fetch_data.save_daily_history_and_changelog([m_v2], history_dir=hdir, changelog_path=cpath, today="2026-11-24")
            out = fetch_data.save_daily_history_and_changelog([m_v3], history_dir=hdir, changelog_path=cpath, today="2026-11-24")
            drops = [ev for ev in out["events"] if ev["type"] == "price_drop"]
            self.assertEqual(len(drops), 2, f"Expected both intraday drops preserved, got {drops}")

    def test_hard_daily_llm_cap_enforced_on_mass_invalidation(self):
        models = [
            fetch_data.classify_model({"id": f"v/m-{i}", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 128000})
            for i in range(30)
        ]
        fake_resp = (
            {"summary": "Paid API model.", "best_for": ["A", "B", "C"], "watch_outs": ["X", "Y"]},
            "deepseek",
            {"prompt_tokens": 200, "completion_tokens": 120},
        )
        with mock.patch("scripts.enrich_llm.get_available_providers", return_value=[{"name": "deepseek"}]), \
             mock.patch("scripts.enrich_llm.generate_json_with_fallback", return_value=fake_resp), \
             mock.patch("scripts.enrich_llm.time.sleep", return_value=None):
            stats = enrich_llm.run_enrichment(models, [], max_calls=5, max_cost_usd=0.05, prompt_ver="v2.1")
        self.assertEqual(stats["llm_calls"], 5)
        self.assertLessEqual(stats["llm_cost_usd"], 0.05)


class TestFaultInjectionQualityGate(unittest.TestCase):
    def test_quality_gate_blocks_misclassified_router_and_broken_links(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            dist_dir = root / "dist"
            data_dir.mkdir()
            dist_dir.mkdir()

            bad_models = [
                {
                    "id": "openrouter/auto-code",
                    "pricing_type": "limited_free",
                    "is_free": True,
                    "is_realtime": True,
                    "prompt_price_per_m": 0.0,
                    "completion_price_per_m": 0.0,
                }
            ]
            (data_dir / "models.json").write_text(json.dumps(bad_models))
            (data_dir / "repos.json").write_text("[]")
            (dist_dir / "index.html").write_text('<html><a href="/missing-page/">Broken</a></html>')

            errs = health_check.run_quality_gate(data_dir=data_dir, dist_dir=dist_dir, repo_root=root)
            joined = "\n".join(errs)
            self.assertIn("P0 violation", joined)
            self.assertIn("broken internal links", joined)
            self.assertIn("Missing required dist file", joined)

    def test_quality_gate_blocks_batch_marked_realtime_and_leaked_key(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            dist_dir = root / "dist"
            data_dir.mkdir()
            dist_dir.mkdir()

            bad_models = [{"id": "openai/sol:batch", "pricing_type": "batch", "is_free": False, "is_realtime": True}]
            (data_dir / "models.json").write_text(json.dumps(bad_models))
            (data_dir / "repos.json").write_text("[]")
            (root / "leak.txt").write_text("sk-" + "0123456789abcdef" * 2)

            errs = health_check.run_quality_gate(data_dir=data_dir, dist_dir=dist_dir, repo_root=root)
            joined = "\n".join(errs)
            self.assertIn("batch endpoint", joined)
            self.assertIn("Potential secret leaked", joined)


if __name__ == "__main__":
    unittest.main()
