"""V2.2 SEO, Cross-Day lastmod, Permanent Registry & Evidence Honesty Test Suite."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_site, fetch_data, seo_gate


class TestCrossDayLastmodStability(unittest.TestCase):
    def test_identical_data_across_calendar_days_preserves_lastmod(self):
        with tempfile.TemporaryDirectory() as td:
            reg = Path(td) / "published_pages.json"
            m_day1 = fetch_data.classify_model(
                {"id": "vendor/m1", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 128000, "supported_parameters": ["tools"]},
                today="2026-11-24",
            )
            m_day2 = fetch_data.classify_model(
                {"id": "vendor/m1", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 128000, "supported_parameters": ["tools"]},
                today="2026-11-25",
            )
            m_day3_changed = fetch_data.classify_model(
                {"id": "vendor/m1", "pricing": {"prompt": "0.0000005", "completion": "0.000002"}, "context_length": 128000, "supported_parameters": ["tools"]},
                today="2026-11-26",
            )

            h1 = build_site.model_semantic_fingerprint(m_day1)
            h2 = build_site.model_semantic_fingerprint(m_day2)
            h3 = build_site.model_semantic_fingerprint(m_day3_changed)
            self.assertEqual(h1, h2, "Semantic hash must NOT change when only date/last_seen changed!")
            self.assertNotEqual(h2, h3, "Semantic hash MUST change when actual price changed!")

            p1 = build_site.update_published_pages_registry(
                {"/models/vendor-m1/": {"page_type": "model_detail", "index_policy": "index", "semantic_hash": h1}},
                registry_path=reg,
                now_iso="2026-11-24T02:15:00Z",
                today="2026-11-24",
            )
            p2 = build_site.update_published_pages_registry(
                {"/models/vendor-m1/": {"page_type": "model_detail", "index_policy": "index", "semantic_hash": h2}},
                registry_path=reg,
                now_iso="2026-11-25T02:15:00Z",
                today="2026-11-25",
            )
            p3 = build_site.update_published_pages_registry(
                {"/models/vendor-m1/": {"page_type": "model_detail", "index_policy": "index", "semantic_hash": h3}},
                registry_path=reg,
                now_iso="2026-11-26T02:15:00Z",
                today="2026-11-26",
            )
            self.assertEqual(p1["/models/vendor-m1/"]["lastmod"], "2026-11-24T02:15:00Z")
            self.assertEqual(p2["/models/vendor-m1/"]["lastmod"], "2026-11-24T02:15:00Z")
            self.assertEqual(p3["/models/vendor-m1/"]["lastmod"], "2026-11-26T02:15:00Z")


class TestPermanentComparisonRegistry(unittest.TestCase):
    def test_order_independent_pair_key_and_archive_on_delist(self):
        with tempfile.TemporaryDirectory() as td:
            reg_path = Path(td) / "published_comparisons.json"
            ma = fetch_data.classify_model({"id": "zeta/coder-1", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 128000})
            mb = fetch_data.classify_model({"id": "alpha/coder-2", "pricing": {"prompt": "0.000002", "completion": "0.000004"}, "context_length": 128000})

            # Pass [ma, mb] then reversed [mb, ma] — must produce identical canonical alpha-vs-zeta URL!
            res1 = build_site.reconcile_comparison_registry([ma, mb], max_new_pairs=10, registry_path=reg_path, today="2026-11-24")
            res2 = build_site.reconcile_comparison_registry([mb, ma], max_new_pairs=10, registry_path=reg_path, today="2026-11-25")
            self.assertEqual(len(res1), 1)
            self.assertEqual(len(res2), 1)
            self.assertEqual(res1[0][2], "alpha-coder-2-vs-zeta-coder-1")
            self.assertEqual(res2[0][2], "alpha-coder-2-vs-zeta-coder-1")

            # Delist ma -> comparison URL must remain preserved with status='archived'
            ma["status"] = "delisted"
            res3 = build_site.reconcile_comparison_registry([ma, mb], max_new_pairs=10, registry_path=reg_path, today="2026-11-28")
            self.assertEqual(len(res3), 1)
            self.assertEqual(res3[0][4], "archived")


class TestEvidenceHonestyAndNotApplicable(unittest.TestCase):
    def test_docker_not_inferred_from_self_hosted_keyword(self):
        # Empty root file list -> has_docker MUST be False even if repo is "self-hosted"
        res_no = fetch_data.verify_repo_docker_files("org/self-hosted-app", {}, root_files=["README.md", "main.go"])
        self.assertFalse(res_no["has_docker"])
        self.assertEqual(res_no["docker_evidence"], "Repository Verified")

        res_yes = fetch_data.verify_repo_docker_files("org/app", {}, root_files=["README.md", "Dockerfile", "compose.yml"])
        self.assertTrue(res_yes["has_docker"])
        self.assertEqual(res_yes["docker_files"], ["Dockerfile", "compose.yml"])

    def test_compare_task_cell_shows_not_applicable_without_tools(self):
        no_tools = fetch_data.classify_model(
            {"id": "v/no-tools", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 128000, "supported_parameters": ["max_tokens"]}
        )
        cell = build_site.render_task_cell(no_tools, "coding", 18.0, 3.5)
        self.assertIn("Not Applicable", cell)
        self.assertNotIn("$", cell)

        missing_price = fetch_data.classify_model({"id": "v/missing", "pricing": {}, "context_length": 128000, "supported_parameters": ["tools"]})
        cell2 = build_site.render_task_cell(missing_price, "coding", 18.0, 3.5)
        self.assertIn("Not Applicable", cell2)


class TestSEOGateEnforcement(unittest.TestCase):
    def test_seo_gate_catches_duplicate_titles_noindex_in_sitemap_and_benchmark_word(self):
        with tempfile.TemporaryDirectory() as td:
            dist = Path(td) / "dist"
            data = Path(td) / "data"
            dist.mkdir()
            data.mkdir()
            (dist / "a.html").write_text('<html lang="en"><head><title>Dup Title</title><link rel="canonical" href="https://sysv.cn/a/"></head><body><h1>A</h1><p>Best benchmark</p></body></html>')
            (dist / "b.html").write_text('<html lang="en"><head><title>Dup Title</title><link rel="canonical" href="https://sysv.cn/b/"></head><body><h1>B</h1></body></html>')
            (dist / "priv.html").write_text('<html lang="en"><head><meta name="robots" content="noindex, follow"><title>Privacy</title><link rel="canonical" href="https://sysv.cn/privacy/"></head><body><h1>Privacy</h1></body></html>')
            (dist / "sitemap.xml").write_text('<urlset><url><loc>https://sysv.cn/privacy/</loc></url></urlset>')

            errs, _ = seo_gate.run_seo_audit(dist, data)
            joined = "\n".join(errs)
            self.assertIn("Duplicate indexable title", joined)
            self.assertIn("Unverified 'benchmark'", joined)
            self.assertIn("non-indexable", joined)


if __name__ == "__main__":
    unittest.main()
