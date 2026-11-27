"""Regression tests for real production bugs found in the SysV audit report.

Covers:
1. Batch endpoint misclassification & stale live-model facts in `fetch_data.reconcile_model_lifecycle`
   and `health_check.run_quality_gate` (22 `:batch` endpoints previously stuck as `paid` / `is_realtime=None`).
2. Homepage calculator state preservation: editing token volumes (`#calc-in` / `#calc-out`)
   must NOT reset `#task-preset` to `custom` or drop RAG / Real-Time / Context / Tool gates.
3. AI enrichment factual validation in `enrich_llm._valid_enrichment`:
   - Allows legitimate sub-dollar prices like `$0.05` on paid/batch endpoints.
   - Rejects false `$0` / `$0.00` claims on paid/batch/variable endpoints.
   - Validates prices (including directional input/output), context window, capabilities
     (`tools`, `structured_outputs`, `reasoning`), and real-time vs async `:batch` strictly
     against structured model data.
4. Generated dataset & comparison registry consistency checks (`data/models.json`,
   `data/published_comparisons.json`).
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

from scripts import build_site, enrich_llm, fetch_data, health_check


class TestBatchClassificationAndLiveSyncRegression(unittest.TestCase):
    def test_reconcile_syncs_latest_facts_for_all_still_live_models_not_in_selected(self):
        """Reproduces the real bug where 22 live `:batch` models stayed `paid` / `is_realtime=None`."""
        stale_legacy_batch = {
            "id": "openai/gpt-6-luna:batch",
            "slug": "openai-gpt-6-luna-batch",
            "name": "OpenAI: GPT-6 Luna (batch)",
            "provider": "Openai",
            "context_length": 128000,
            "pricing_type": "paid",
            "pricing_label": "Paid",
            "is_free": False,
            "is_realtime": None,
            "prompt_price_per_m": 0.05,
            "completion_price_per_m": 0.25,
            "capabilities": {"tools": False, "structured_outputs": False, "response_format": False, "reasoning": False},
            "status": "active",
            "first_seen": "2026-11-24",
            "last_seen": "2026-11-26",
            "updated_at": "2026-11-24",
            "enrichment": {"source": "deterministic-fallback", "cache_hash": "oldhash", "summary": "Stale paid copy."},
        }
        stale_legacy_paid = {
            "id": "vendor/non-selected-paid",
            "slug": "vendor-non-selected-paid",
            "name": "Vendor Old Price",
            "provider": "Vendor",
            "context_length": 64000,
            "pricing_type": "paid",
            "pricing_label": "Paid",
            "is_free": False,
            "is_realtime": True,
            "prompt_price_per_m": 2.0,
            "completion_price_per_m": 8.0,
            "capabilities": {"tools": False, "structured_outputs": False, "response_format": False, "reasoning": False},
            "status": "active",
            "first_seen": "2026-11-24",
            "last_seen": "2026-11-26",
            "updated_at": "2026-11-24",
        }

        fresh_batch = fetch_data.classify_model(
            {
                "id": "openai/gpt-6-luna:batch",
                "name": "OpenAI: GPT-6 Luna (batch)",
                "pricing": {"prompt": "0.00000005", "completion": "0.00000025"},
                "context_length": 400000,
                "architecture": {"modality": "text->text"},
                "supported_parameters": ["tools", "structured_outputs"],
            },
            today="2026-11-27",
        )
        fresh_paid = fetch_data.classify_model(
            {
                "id": "vendor/non-selected-paid",
                "name": "Vendor New Price",
                "pricing": {"prompt": "0.0000005", "completion": "0.0000015"},
                "context_length": 256000,
                "architecture": {"modality": "text->text"},
                "supported_parameters": ["tools"],
            },
            today="2026-11-27",
        )
        selected_other = fetch_data.classify_model(
            {
                "id": "deepseek/deepseek-v4",
                "name": "DeepSeek V4",
                "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
                "context_length": 128000,
                "supported_parameters": ["tools"],
            },
            today="2026-11-27",
        )

        # Neither stale_legacy_batch nor stale_legacy_paid is in selected_active,
        # but BOTH are still live in OpenRouter (`all_live_models`).
        all_live = {m["id"]: m for m in (selected_other, fresh_batch, fresh_paid)}
        merged = fetch_data.reconcile_model_lifecycle(
            selected_active=[selected_other],
            all_live_ids=set(all_live.keys()),
            existing_models=[stale_legacy_batch, stale_legacy_paid],
            all_live_models=all_live,
            is_full_healthy_fetch=True,
            today="2026-11-27",
        )
        by_id = {m["id"]: m for m in merged}

        repaired_batch = by_id["openai/gpt-6-luna:batch"]
        self.assertEqual(repaired_batch["pricing_type"], "batch")
        self.assertFalse(repaired_batch["is_realtime"])
        self.assertEqual(repaired_batch["context_length"], 400000)
        self.assertTrue(repaired_batch["capabilities"]["tools"])
        self.assertEqual(repaired_batch["first_seen"], "2026-11-24")
        self.assertEqual(repaired_batch["updated_at"], "2026-11-27")
        self.assertEqual(repaired_batch["slug"], "openai-gpt-6-luna-batch")

        repaired_paid = by_id["vendor/non-selected-paid"]
        self.assertAlmostEqual(repaired_paid["prompt_price_per_m"], 0.5)
        self.assertAlmostEqual(repaired_paid["completion_price_per_m"], 1.5)
        self.assertEqual(repaired_paid["context_length"], 256000)
        self.assertTrue(repaired_paid["capabilities"]["tools"])
        self.assertEqual(repaired_paid["updated_at"], "2026-11-27")

    def test_health_check_blocks_batch_endpoint_misclassified_as_paid_or_missing_realtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            dist_dir = root / "dist"
            data_dir.mkdir()
            dist_dir.mkdir()
            bad_models = [
                {
                    "id": "openai/gpt-6-luna:batch",
                    "name": "OpenAI: GPT-6 Luna (batch)",
                    "pricing_type": "paid",
                    "is_free": False,
                    "is_realtime": None,
                    "status": "active",
                }
            ]
            (data_dir / "models.json").write_text(json.dumps(bad_models), encoding="utf-8")
            (data_dir / "repos.json").write_text("[]", encoding="utf-8")

            errs = health_check.run_quality_gate(data_dir=data_dir, dist_dir=dist_dir, repo_root=root)
            joined = "\n".join(errs)
            self.assertIn("misclassified as pricing_type=paid", joined)
            self.assertIn("missing boolean is_realtime", joined)

    def test_comparison_registry_archives_legacy_batch_comparison_pairs(self):
        batch_m = fetch_data.classify_model(
            {"id": "openai/gpt-5.4-mini:batch", "pricing": {"prompt": "0.000000375", "completion": "0.00000225"}, "context_length": 400000},
            today="2026-11-27",
        )
        rt_m = fetch_data.classify_model(
            {"id": "qwen/qwen2.5-vl-72b-instruct", "pricing": {"prompt": "0.0000008", "completion": "0.000001"}, "context_length": 128000},
            today="2026-11-27",
        )
        with tempfile.TemporaryDirectory() as td:
            reg = Path(td) / "published_comparisons.json"
            reg.write_text(
                json.dumps(
                    {
                        "openai-gpt-5-4-mini-batch::qwen-qwen2-5-vl-72b-instruct": {
                            "pair_key": "openai-gpt-5-4-mini-batch::qwen-qwen2-5-vl-72b-instruct",
                            "slug": "openai-gpt-5-4-mini-batch-vs-qwen-qwen2-5-vl-72b-instruct",
                            "model_a_id": batch_m["id"],
                            "model_b_id": rt_m["id"],
                            "cohort": "Low-Cost Real-Time Production APIs",
                            "first_published": "2026-11-24",
                            "status": "active",
                        }
                    }
                ),
                encoding="utf-8",
            )
            resolved = build_site.reconcile_comparison_registry([batch_m, rt_m], max_new_pairs=10, registry_path=reg, today="2026-11-27")
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0][4], "archived")

    def test_same_day_rerun_does_not_double_increment_missing_days(self):
        m = fetch_data.classify_model({"id": "vendor/gone", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}, today="2026-11-25")
        r1 = fetch_data.reconcile_model_lifecycle([], set(), [m], is_full_healthy_fetch=True, today="2026-11-27")
        r2 = fetch_data.reconcile_model_lifecycle([], set(), r1, is_full_healthy_fetch=True, today="2026-11-27")
        self.assertEqual(r1[0]["missing_days"], 1)
        self.assertEqual(r2[0]["missing_days"], 1)
        self.assertEqual(r2[0]["status"], "temporarily_missing")


class TestCalculatorStatePreservationRegression(unittest.TestCase):
    def test_homepage_token_input_listener_does_not_overwrite_task_preset_to_custom(self):
        src = (Path(__file__).resolve().parent.parent / "scripts/build_site.py").read_text(encoding="utf-8")
        self.assertNotIn("presetEl.value = 'custom'", src)
        self.assertNotIn('presetEl.value = "custom"', src)


class TestAIFactualValidationRegression(unittest.TestCase):
    def _sample_paid_model(self, **overrides):
        base = {
            "id": "google/gemma-3-4b-it",
            "name": "Google: Gemma 3 4B",
            "pricing_type": "paid",
            "is_realtime": True,
            "prompt_price_per_m": 0.05,
            "completion_price_per_m": 0.10,
            "cache_read_price_per_m": None,
            "cache_write_price_per_m": None,
            "context_length": 131072,
            "capabilities": {
                "tools": False,
                "structured_outputs": True,
                "response_format": True,
                "reasoning": False,
            },
        }
        base.update(overrides)
        return base

    def test_allows_subdollar_paid_prices_like_005_while_rejecting_zero_price(self):
        m = self._sample_paid_model()
        good_subdollar = {
            "summary": "Google: Gemma 3 4B is a paid real-time model billed at $0.05/1M input and $0.10/1M output tokens with a 128K context window and structured outputs.",
            "best_for": ["Low-cost structured outputs extraction", "High-volume text classification", "Budget interactive prompts"],
            "watch_outs": ["No tool calling or reasoning support", "Paid per-token pricing applies; not free"],
        }
        self.assertTrue(enrich_llm._valid_enrichment(good_subdollar, pricing_type="paid", model=m))
        # Also works without passing model dict (legacy call signature)
        self.assertTrue(enrich_llm._valid_enrichment(good_subdollar, pricing_type="paid"))

        bad_zero = {
            "summary": "Google: Gemma 3 4B is available at $0/1M input and $0.00/1M output tokens.",
            "best_for": ["Prototyping"],
            "watch_outs": ["Rate limits apply"],
        }
        self.assertFalse(enrich_llm._valid_enrichment(bad_zero, pricing_type="paid", model=m))
        self.assertFalse(enrich_llm._valid_enrichment(bad_zero, pricing_type="paid"))

    def test_rejects_hallucinated_or_swapped_prices_against_structured_data(self):
        m = self._sample_paid_model(prompt_price_per_m=0.05, completion_price_per_m=0.25)

        wrong_price = {
            "summary": "Billed at $1.50/1M input and $0.25/1M output tokens with a 128K context window.",
            "best_for": ["Text generation"],
            "watch_outs": ["Paid per-token pricing"],
        }
        self.assertFalse(enrich_llm._valid_enrichment(wrong_price, pricing_type="paid", model=m))

        swapped_price = {
            "summary": "Billed at $0.25/1M input and $0.05/1M output tokens with a 128K context window.",
            "best_for": ["Text generation"],
            "watch_outs": ["Paid per-token pricing"],
        }
        self.assertFalse(enrich_llm._valid_enrichment(swapped_price, pricing_type="paid", model=m))

        unit_price_hallucination = {
            "summary": "Priced at 0.90 per 1M input and 2.50 per 1M output tokens.",
            "best_for": ["Text generation"],
            "watch_outs": ["Paid per-token pricing"],
        }
        self.assertFalse(enrich_llm._valid_enrichment(unit_price_hallucination, pricing_type="paid", model=m))

    def test_validates_context_window_against_structured_data(self):
        m = self._sample_paid_model(context_length=32768)

        bad_ctx = {
            "summary": "Paid real-time model at $0.05/1M input and $0.10/1M output with a 128K context window.",
            "best_for": ["Long documents"],
            "watch_outs": ["No tool calling"],
        }
        self.assertFalse(enrich_llm._valid_enrichment(bad_ctx, pricing_type="paid", model=m))

        good_ctx = {
            "summary": "Paid real-time model at $0.05/1M input and $0.10/1M output with a 32K context window.",
            "best_for": ["Short-to-medium structured outputs"],
            "watch_outs": ["No tool calling"],
        }
        self.assertTrue(enrich_llm._valid_enrichment(good_ctx, pricing_type="paid", model=m))

        # 1M shorthand for 1,048,576 binary million is within 8% tolerance
        m_1m = self._sample_paid_model(context_length=1048576)
        good_1m = {
            "summary": "Paid model at $0.05/1M input and $0.10/1M output with a 1M-token context window.",
            "best_for": ["Long-context text analysis"],
            "watch_outs": ["No tool calling"],
        }
        self.assertTrue(enrich_llm._valid_enrichment(good_1m, pricing_type="paid", model=m_1m))

    def test_validates_capabilities_and_batch_realtime_against_structured_data(self):
        m_no_tools = self._sample_paid_model(
            capabilities={"tools": False, "structured_outputs": False, "response_format": False, "reasoning": False}
        )

        # False positive claims MUST fail
        self.assertFalse(
            enrich_llm._valid_enrichment(
                {
                    "summary": "Supports tool calling at $0.05/1M input and $0.10/1M output.",
                    "best_for": ["Agent workflows"],
                    "watch_outs": ["Paid pricing"],
                },
                model=m_no_tools,
            )
        )
        self.assertFalse(
            enrich_llm._valid_enrichment(
                {
                    "summary": "Paid endpoint supporting structured outputs at $0.05/1M input.",
                    "best_for": ["JSON pipelines"],
                    "watch_outs": ["Paid pricing"],
                },
                model=m_no_tools,
            )
        )
        self.assertFalse(
            enrich_llm._valid_enrichment(
                {
                    "summary": "Paid reasoning model with reasoning support at $0.05/1M input.",
                    "best_for": ["Math"],
                    "watch_outs": ["Paid pricing"],
                },
                model=m_no_tools,
            )
        )

        # Explicit negations MUST pass
        self.assertTrue(
            enrich_llm._valid_enrichment(
                {
                    "summary": "Paid real-time text model at $0.05/1M input and $0.10/1M output with a 128K context window.",
                    "best_for": ["Simple text summarization"],
                    "watch_outs": ["No tool calling, structured outputs, or reasoning support"],
                },
                model=m_no_tools,
            )
        )

        # False denial of an existing capability MUST fail
        m_with_tools = self._sample_paid_model(
            capabilities={"tools": True, "structured_outputs": True, "response_format": True, "reasoning": True}
        )
        self.assertFalse(
            enrich_llm._valid_enrichment(
                {
                    "summary": "Paid endpoint at $0.05/1M input with a 128K context window.",
                    "best_for": ["Text generation"],
                    "watch_outs": ["No tool calling support"],
                },
                model=m_with_tools,
            )
        )

        # Async batch endpoint claiming real-time interactive use MUST fail, while negation passes
        m_batch = self._sample_paid_model(
            id="openai/gpt-6-luna:batch",
            pricing_type="batch",
            is_realtime=False,
            prompt_price_per_m=0.05,
            completion_price_per_m=0.25,
        )
        self.assertFalse(
            enrich_llm._valid_enrichment(
                {
                    "summary": "Real-time interactive endpoint billed at $0.05/1M input and $0.25/1M output.",
                    "best_for": ["Live interactive chat"],
                    "watch_outs": ["Paid pricing"],
                },
                model=m_batch,
            )
        )
        self.assertTrue(
            enrich_llm._valid_enrichment(
                {
                    "summary": "Asynchronous batch-only endpoint billed at $0.05/1M input and $0.25/1M output.",
                    "best_for": ["Overnight bulk translation"],
                    "watch_outs": ["Not suitable for real-time interactive chat or live agents"],
                },
                model=m_batch,
            )
        )


class TestProductionDataConsistencyRegression(unittest.TestCase):
    def test_committed_models_json_has_zero_misclassified_batch_and_valid_enrichments(self):
        data_dir = Path(__file__).resolve().parent.parent / "data"
        models = json.loads((data_dir / "models.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(models), 100)
        for m in models:
            mid = str(m.get("id", "")).lower()
            name = str(m.get("name", "")).lower()
            if ":batch" in mid or "(batch)" in name:
                self.assertEqual(m.get("pricing_type"), "batch", f"Batch model misclassified: {m['id']}")
                self.assertIs(m.get("is_realtime"), False, f"Batch model must have is_realtime=False: {m['id']}")
            if m.get("status", "active") == "active":
                self.assertIsInstance(m.get("is_realtime"), bool, f"Active model missing boolean is_realtime: {m['id']}")
            enr = m.get("enrichment")
            if enr:
                self.assertTrue(
                    enrich_llm._valid_enrichment(enr, pricing_type=m.get("pricing_type"), model=m),
                    f"Stored enrichment failed factual validation for {m['id']}: {enr}",
                )

        comps = json.loads((data_dir / "published_comparisons.json").read_text(encoding="utf-8"))
        by_id = {m["id"]: m for m in models}
        for pkey, rec in comps.items():
            if rec.get("status") == "active":
                ma = by_id.get(rec["model_a_id"])
                mb = by_id.get(rec["model_b_id"])
                self.assertIsNotNone(ma, f"Missing model_a in active comparison {pkey}")
                self.assertIsNotNone(mb, f"Missing model_b in active comparison {pkey}")
                self.assertNotEqual(ma.get("pricing_type"), "batch", f"Active comparison contains batch model: {pkey}")
                self.assertNotEqual(mb.get("pricing_type"), "batch", f"Active comparison contains batch model: {pkey}")
                self.assertTrue(ma.get("is_realtime"), f"Active comparison contains non-realtime model: {pkey}")
                self.assertTrue(mb.get("is_realtime"), f"Active comparison contains non-realtime model: {pkey}")


if __name__ == "__main__":
    unittest.main()
