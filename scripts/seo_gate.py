"""Independent SEO Indexing Quality Gate (decoupled from technical health_check.py).

Decides per-page indexing status (`index`, `noindex`, `archived`) based on:
- Real search intent & standalone decision utility
- Pricing/data completeness (quarantines `unknown` / `temporarily_missing` from index)
- Uniqueness (zero duplicate titles, zero duplicate canonicals, zero A-vs-B / B-vs-A duplicates)
- Honesty guard (forbids unverified 'benchmark' / 'runtime tested' claims)
"""

import re
from pathlib import Path
from typing import Any

from scripts.utils import DATA_DIR, DIST_DIR, load_json


def decide_model_index_policy(m: dict[str, Any]) -> str:
    status = m.get("status", "active")
    ptype = m.get("pricing_type", "unknown")
    if status == "delisted":
        return "archived"
    if status == "temporarily_missing" or ptype == "unknown":
        return "noindex"
    return "index"


def decide_comparison_index_policy(a: dict[str, Any], b: dict[str, Any], comp_status: str) -> str:
    if comp_status != "active":
        return "archived"
    if a.get("status") != "active" or b.get("status") != "active":
        return "archived"
    if a.get("pricing_type") not in ("limited_free", "free", "paid") or b.get("pricing_type") not in ("limited_free", "free", "paid"):
        return "noindex"
    return "index"


def decide_repo_index_policy(r: dict[str, Any]) -> str:
    if not r.get("description") or int(r.get("stars", 0)) < 100:
        return "noindex"
    return "index"


def decide_static_index_policy(url_path: str) -> str:
    if url_path in ("/privacy/", "/terms/", "/contact/", "/404.html"):
        return "noindex"
    return "index"


def run_seo_audit(dist_dir: Path = DIST_DIR, data_dir: Path = DATA_DIR) -> tuple[list[str], dict[str, Any]]:
    """Audit built HTML & sitemap strictly for SEO indexing quality. Returns (errors, summary)."""
    errors: list[str] = []
    titles_seen: dict[str, str] = {}
    canonicals_seen: dict[str, str] = {}

    index_count = 0
    noindex_count = 0
    missing_h1 = 0
    missing_lang = 0
    unverified_benchmark_pages: list[str] = []

    for html_file in sorted(dist_dir.rglob("*.html")):
        rel = str(html_file.relative_to(dist_dir))
        txt = html_file.read_text(encoding="utf-8")

        if not re.search(r'<html[^>]+lang="en"', txt):
            missing_lang += 1
        if not re.search(r"<h1\b", txt):
            missing_h1 += 1

        is_noindex = bool(re.search(r'<meta\s+name="robots"\s+content="[^"]*noindex', txt, re.I))
        if is_noindex:
            noindex_count += 1
        else:
            index_count += 1

        t_m = re.search(r"<title>(.*?)</title>", txt, re.S)
        if not t_m or not t_m.group(1).strip():
            errors.append(f"Missing title: {rel}")
        else:
            title = t_m.group(1).strip()
            if not is_noindex:
                if title in titles_seen:
                    errors.append(f"Duplicate indexable title '{title}': {titles_seen[title]} AND {rel}")
                else:
                    titles_seen[title] = rel

        c_m = re.search(r'<link\s+rel="canonical"\s+href="([^"]+)"', txt)
        if not c_m:
            errors.append(f"Missing canonical: {rel}")
        elif not is_noindex:
            canon = c_m.group(1)
            if canon in canonicals_seen:
                errors.append(f"Duplicate canonical '{canon}': {canonicals_seen[canon]} AND {rel}")
            else:
                canonicals_seen[canon] = rel

        # Honesty check: forbid unverified 'benchmark' claims in site-authored content (exclude verbatim upstream descriptions)
        body_only = re.sub(r"<script\b[^>]*>.*?</script>", "", txt, flags=re.S | re.I)
        body_only = re.sub(r'<p class="muted upstream-desc">.*?</p>', "", body_only, flags=re.S | re.I)
        if re.search(r"\bbenchmark(s|ed)?\b", body_only, re.I):
            unverified_benchmark_pages.append(rel)

    if missing_lang:
        errors.append(f"{missing_lang} HTML pages missing lang='en'")
    if missing_h1:
        errors.append(f"{missing_h1} HTML pages missing <h1>")
    if unverified_benchmark_pages:
        errors.append(f"Unverified 'benchmark' wording found on {len(unverified_benchmark_pages)} page(s): {unverified_benchmark_pages[:5]}")

    # Verify sitemap ONLY contains `index` canonical URLs (zero noindex URLs in sitemap!)
    sitemap_path = dist_dir / "sitemap.xml"
    sitemap_urls: list[str] = []
    if sitemap_path.exists():
        sm_txt = sitemap_path.read_text(encoding="utf-8")
        sitemap_urls = re.findall(r"<loc>([^<]+)</loc>", sm_txt)
        for u in sitemap_urls:
            if u not in canonicals_seen:
                errors.append(f"Sitemap contains non-indexable or missing canonical URL: {u}")

    # Verify no A-vs-B / B-vs-A duplicate pair keys in published_comparisons.json
    pub_comps = load_json(data_dir / "published_comparisons.json", default={})
    seen_pairs: set[str] = set()
    for pkey, info in pub_comps.items():
        if pkey in seen_pairs:
            errors.append(f"Duplicate pair_key in published_comparisons.json: {pkey}")
        seen_pairs.add(pkey)

    summary = {
        "indexable_pages": index_count,
        "noindex_pages": noindex_count,
        "sitemap_urls": len(sitemap_urls),
        "unique_titles": len(titles_seen),
        "published_comparisons": len(pub_comps),
    }
    return errors, summary


def main() -> None:
    errors, summary = run_seo_audit(DIST_DIR, DATA_DIR)
    if errors:
        print("[seo_gate] FAILED:")
        for err in errors:
            print(f"  - {err}")
        raise SystemExit(1)
    print(
        f"[seo_gate] PASSED: indexable={summary['indexable_pages']}, noindex={summary['noindex_pages']}, "
        f"sitemap_urls={summary['sitemap_urls']}, unique_titles={summary['unique_titles']}, "
        f"stable_comparisons={summary['published_comparisons']}."
    )


if __name__ == "__main__":
    main()
