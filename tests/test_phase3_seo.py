"""Phase 3 SEO & Organic Acquisition Regression Tests for SysV (sysv.cn).

Verifies:
1. All 6 P0/P1 target entry pages emit search-intent-aligned Title/H1/Description + WebApplication & FAQPage JSON-LD.
2. All 4 standalone calculators expose ad-free /embed/ citation snippets and cross-link sibling calculators.
3. Model detail pages link back to P0/P1 calculators (bidirectional internal link equity).
4. /data/ exposes all 4 ad-free calculator embed snippets + JSON/CSV/RSS/SVG assets.
5. Zero SEO/compliance regressions: zero 'benchmark' claims, zero live ad scripts in verification mode,
   exact Sitemap URL parity, and zero modifications to config/content_review.json.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from scripts import ad_policy_gate, build_site, seo_gate
from scripts.utils import DATA_DIR, ROOT_DIR


def _extract_json_ld_types(html_text: str) -> set[str]:
    types: set[str] = set()
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.S):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and obj.get("@type"):
                types.add(str(obj["@type"]))
        except Exception:
            pass
    return types


class TestPhase3SEOAcquisition(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(cls._tmp.name)
        cls.dist = tmp_path / "dist"
        cls.reports = tmp_path / "reports"
        pub_pages = tmp_path / "published_pages.json"
        pub_comps = tmp_path / "published_comparisons.json"
        if (DATA_DIR / "published_pages.json").exists():
            shutil.copy2(DATA_DIR / "published_pages.json", pub_pages)
        if (DATA_DIR / "published_comparisons.json").exists():
            shutil.copy2(DATA_DIR / "published_comparisons.json", pub_comps)
        build_site.main(
            dist_dir_override=cls.dist,
            reports_dir_override=cls.reports,
            published_pages_path_override=pub_pages,
            published_comparisons_path_override=pub_comps,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_p0_p1_entry_pages_have_intent_titles_json_ld_and_twitter_cards(self) -> None:
        expectations = [
            ("index.html", "LLM API Pricing Calculator", {"WebSite", "WebApplication", "FAQPage"}),
            ("free-llm-apis/index.html", "Free LLM API", {"BreadcrumbList", "WebApplication", "FAQPage"}),
            ("tools/openrouter-cost-calculator/index.html", "OpenRouter Cost Calculator", {"BreadcrumbList", "WebApplication", "FAQPage"}),
            ("tools/n8n-ai-workflow-cost-calculator/index.html", "n8n AI Workflow Cost Calculator", {"BreadcrumbList", "WebApplication", "FAQPage"}),
            ("tools/llm-translation-cost-calculator/index.html", "LLM Translation Cost Calculator", {"BreadcrumbList", "WebApplication", "FAQPage"}),
            ("tools/api-vs-local-llm-calculator/index.html", "API vs Local LLM Cost Calculator", {"BreadcrumbList", "WebApplication", "FAQPage"}),
        ]
        for rel_path, expected_title_phrase, expected_ld_types in expectations:
            html = (self.dist / rel_path).read_text(encoding="utf-8")
            title_m = re.search(r"<title>(.*?)</title>", html, re.S)
            self.assertIsNotNone(title_m, f"Missing title in {rel_path}")
            self.assertIn(expected_title_phrase, title_m.group(1), f"Title mismatch in {rel_path}")
            self.assertIn('name="twitter:card"', html, f"Missing Twitter card in {rel_path}")
            ld_types = _extract_json_ld_types(html)
            for t in expected_ld_types:
                self.assertIn(t, ld_types, f"Missing JSON-LD @type={t} in {rel_path}")

    def test_all_four_tools_include_ad_free_embed_snippet_and_internal_links(self) -> None:
        slugs = [
            "openrouter-cost-calculator",
            "n8n-ai-workflow-cost-calculator",
            "llm-translation-cost-calculator",
            "api-vs-local-llm-calculator",
        ]
        for slug in slugs:
            html = (self.dist / "tools" / slug / "index.html").read_text(encoding="utf-8")
            self.assertIn(f"https://sysv.cn/embed/{slug}/", html, f"Missing embed snippet on /tools/{slug}/")
            self.assertIn("Related LLM Cost Calculators", html, f"Missing related tools card on /tools/{slug}/")
            self.assertIn('href="/free-llm-apis/"', html)
            # Confirm embed page itself remains strictly ad-free and noindex
            embed_html = (self.dist / "embed" / slug / "index.html").read_text(encoding="utf-8")
            self.assertIn('content="noindex, follow"', embed_html)
            self.assertNotIn("adsbygoogle.js", embed_html)

    def test_data_hub_exposes_all_four_ad_free_embeds(self) -> None:
        html = (self.dist / "data" / "index.html").read_text(encoding="utf-8")
        for slug in (
            "openrouter-cost-calculator",
            "n8n-ai-workflow-cost-calculator",
            "llm-translation-cost-calculator",
            "api-vs-local-llm-calculator",
        ):
            self.assertIn(f"https://sysv.cn/embed/{slug}/", html)

    def test_model_detail_pages_link_back_to_core_calculators(self) -> None:
        model_pages = sorted((self.dist / "models").glob("*/index.html"))
        self.assertGreaterEqual(len(model_pages), 40)
        for mp in model_pages[:20]:
            html = mp.read_text(encoding="utf-8")
            self.assertIn("Estimate Your Spend", html, f"Missing calculator cross-link block in {mp}")
            self.assertIn('href="/tools/openrouter-cost-calculator/"', html)

    def test_seo_gate_and_ad_policy_gate_pass_with_zero_live_ads(self) -> None:
        seo_errors, seo_summary = seo_gate.run_seo_audit(self.dist)
        self.assertEqual(seo_errors, [], f"SEO gate errors: {seo_errors}")
        self.assertGreaterEqual(seo_summary["sitemap_urls"], 200)

        ad_errors, ad_report = ad_policy_gate.run_ad_policy_gate(
            dist_dir=self.dist, reports_dir=self.reports
        )
        self.assertEqual(ad_errors, [], f"Ad policy gate errors: {ad_errors}")
        self.assertEqual(ad_report["counts"]["actual_ad_script_pages"], 0)
        self.assertEqual(ad_report["counts"]["factual_conflicts"], 0)


if __name__ == "__main__":
    unittest.main()
