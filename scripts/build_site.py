"""V2.2 Static Site Generator for SysV (sysv.cn).

All V2.2 P0 + P1 SEO fixes:
- Search-driven Homepage Title/H1 ('LLM API Pricing & Token Cost Calculator')
- Permanent URL registries (`data/published_comparisons.json`, `data/published_pages.json`)
  with order-independent comparison pair keys (zero A-vs-B / B-vs-A duplicates, zero silent deletions)
- True cross-calendar-day semantic `lastmod` (hashes structured data, excludes daily timestamps)
- Independent SEO indexing policy (`index` / `noindex` / `archived`); Sitemap contains ONLY `index` URLs
- Fixed Breadcrumbs (paid models -> /models/; free models -> /free-llm-apis/) + BreadcrumbList JSON-LD
- Shared JS calc engine with Python parity + explicit 'Not Applicable' in compare pages
- Honest evidence tags (`Source Verified`, `Repository Verified`, `Not Verified`) + zero fake 'benchmark' words
- 4 standalone intent-specific tools under /tools/ + open datasets/CSV/RSS/badges under /data/
"""

import hashlib
import html
import json
import os
import shutil
import subprocess
from collections import defaultdict
from itertools import combinations
from typing import Any

from scripts import seo_gate, tools_builder
from scripts.utils import DATA_DIR, DIST_DIR, ROOT_DIR, load_config, load_json, save_json, utc_now_iso, utc_today

TEMPLATE_VERSION = "v2.2.1"


def e(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)


def get_git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT_DIR, stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip() or "unknown"
    except Exception:
        return "unknown"


def sem_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def model_semantic_fingerprint(m: dict[str, Any]) -> str:
    """Strictly semantic fields ONLY — excludes `updated_at` and `last_seen` so cross-day lastmod stays stable."""
    enr = m.get("enrichment") or {}
    return sem_hash(
        {
            "tpl": TEMPLATE_VERSION,
            "id": m.get("id"),
            "name": m.get("name"),
            "pricing_type": m.get("pricing_type"),
            "prompt": m.get("prompt_price_per_m"),
            "completion": m.get("completion_price_per_m"),
            "cache_read": m.get("cache_read_price_per_m"),
            "has_tiered_overrides": m.get("has_tiered_overrides"),
            "context_length": m.get("context_length"),
            "modality": m.get("modality"),
            "capabilities": m.get("capabilities"),
            "status": m.get("status"),
            "delisted_at": m.get("delisted_at"),
            "description": m.get("description"),
            "summary": enr.get("summary"),
        }
    )


def repo_semantic_fingerprint(r: dict[str, Any]) -> str:
    """Excludes daily `updated_at` and minor daily star jitter (buckets stars by 1K)."""
    enr = r.get("enrichment") or {}
    return sem_hash(
        {
            "tpl": TEMPLATE_VERSION,
            "full_name": r.get("full_name"),
            "category": r.get("category"),
            "license": r.get("license"),
            "license_tier": r.get("license_tier"),
            "has_docker": r.get("has_docker"),
            "docker_status": r.get("docker_status"),
            "docker_evidence": r.get("docker_evidence"),
            "maintenance_status": r.get("maintenance_status"),
            "stars_bucket_k": int(r.get("stars", 0)) // 1000,
            "description": r.get("description"),
            "summary": enr.get("summary"),
        }
    )


def fmt_price(price: float | None, ptype: str = "paid") -> str:
    if ptype == "variable":
        return "Variable (Routed)"
    if ptype == "media":
        return "Media / Per-Request"
    if ptype == "unknown" or price is None:
        return "Unknown"
    if price == 0:
        return "$0 (Rate-Limited)" if ptype in ("limited_free", "free_router") else "$0"
    if price < 0.01:
        return f"${price:.4f}"
    return f"${price:.2f}"


def fmt_ctx(ctx: int) -> str:
    if ctx >= 1_000_000:
        return f"{ctx / 1_000_000:.1f}M"
    if ctx >= 1_000:
        return f"{ctx // 1_000}K"
    return str(ctx)


def pricing_badge(m: dict[str, Any]) -> str:
    ptype = m.get("pricing_type", "unknown")
    label = m.get("pricing_label", ptype.title())
    cls = {
        "limited_free": "badge badge-free",
        "free": "badge badge-free",
        "free_router": "badge badge-free",
        "batch": "badge badge-purple",
        "variable": "badge badge-warn",
        "media": "badge badge-purple",
        "unknown": "badge",
        "paid": "badge",
    }.get(ptype, "badge")
    return f'<span class="{cls}">{e(label)}</span>'


CSS = """
:root {
  --bg: #ffffff;
  --surface: #ffffff;
  --surface-2: #f5f5f7;
  --border: rgba(0, 0, 0, 0.08);
  --border-strong: #d2d2d7;
  --text: #1d1d1f;
  --muted: #6e6e73;
  --accent: #0071e3;
  --accent-hover: #0077ed;
  --accent-2: #137333;
  --warn: #9a5b00;
  --purple: #5e35b1;
  --danger: #c5221f;
}
* { box-sizing: border-box; }
html {
  -webkit-tap-highlight-color: rgba(0, 113, 227, 0.12);
}
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Helvetica Neue", Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.58;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
}
a, button, input, select, label { touch-action: manipulation; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, button:focus-visible, .btn:focus-visible, input:focus-visible, select:focus-visible, [tabindex]:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.skip-link {
  position: absolute;
  left: -9999px;
  top: 8px;
  background: var(--text);
  color: #ffffff;
  padding: 8px 14px;
  border-radius: 980px;
  font-size: 0.85rem;
  font-weight: 500;
  z-index: 100;
}
.skip-link:focus-visible { left: 16px; }
code {
  font-family: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.85em;
  background: var(--surface-2);
  color: var(--text);
  padding: 2px 6px;
  border-radius: 6px;
}
pre {
  font-family: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: var(--text);
}
.wrap { max-width: 1040px; margin: 0 auto; padding: 0 24px; }
header {
  border-bottom: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.84);
  position: sticky; top: 0; z-index: 20;
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
}
.nav { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 13px 0; flex-wrap: wrap; }
.brand { font-weight: 600; font-size: 1.08rem; color: var(--text); letter-spacing: -0.02em; }
.brand span { color: var(--muted); font-weight: 400; }
.brand:hover { text-decoration: none; }
.nav-links { display: flex; gap: 24px; flex-wrap: wrap; font-size: 0.86rem; font-weight: 400; }
.nav-links a { color: #424245; transition: color 0.15s ease; }
.nav-links a:hover { color: var(--text); text-decoration: none; }
main { padding: 40px 0 80px; }
main:focus { outline: none; }
.hero-open {
  padding: 6px 0 28px;
}
.hero {
  padding: 0 0 24px;
  margin-bottom: 24px;
  border-bottom: 1px solid var(--border);
}
h1 {
  margin: 0 0 12px;
  font-size: clamp(1.85rem, 3.4vw, 2.55rem);
  font-weight: 700;
  letter-spacing: -0.035em;
  line-height: 1.1;
  color: var(--text);
  text-wrap: balance;
}
h2 {
  margin: 44px 0 12px;
  font-size: 1.45rem;
  font-weight: 600;
  letter-spacing: -0.022em;
  line-height: 1.22;
  color: var(--text);
  text-wrap: balance;
  scroll-margin-top: 72px;
}
h3 {
  margin: 14px 0 6px;
  font-size: 1.04rem;
  font-weight: 600;
  letter-spacing: -0.012em;
  color: var(--text);
  scroll-margin-top: 72px;
}
[id] { scroll-margin-top: 72px; }
p { max-width: 68ch; }
.lead {
  font-size: 1.06rem;
  line-height: 1.55;
  color: var(--muted);
  margin: 0;
  max-width: 62ch;
}
.muted { color: var(--muted); }
.small { font-size: 0.84rem; }
.metrics-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 28px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  font-size: 0.86rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.metrics-strip strong {
  color: var(--text);
  font-weight: 600;
  margin-right: 4px;
}
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-top: 20px; }
.stat {
  background: var(--surface-2);
  border-radius: 14px;
  padding: 15px 18px;
}
.stat .num {
  font-size: 1.48rem;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.025em;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.stat .lbl { font-size: 0.8rem; color: var(--muted); margin-top: 4px; }
.calc-panel {
  background: var(--surface-2);
  border-radius: 20px;
  padding: 28px;
  margin: 8px 0 28px;
}
.calc-panel h2 { margin-top: 0; }
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 22px;
  margin-bottom: 18px;
  transition: border-color 0.15s ease;
}
.grid .card { margin-bottom: 0; }
.grid .card:hover { border-color: var(--border-strong); }
.callout-warn {
  background: #fff9ee;
  border: 1px solid rgba(154, 91, 0, 0.2);
  border-radius: 14px;
  padding: 14px 18px;
  margin-bottom: 18px;
  color: #5c3700;
}
.callout-danger {
  background: #fff2f2;
  border: 1px solid rgba(197, 34, 31, 0.2);
  border-radius: 14px;
  padding: 14px 18px;
  margin-bottom: 18px;
  color: #7a1412;
}
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
.badge {
  display: inline-block; padding: 2px 9px; border-radius: 980px;
  font-size: 0.74rem; font-weight: 500; letter-spacing: -0.005em;
  background: #f0f0f5; color: #424245;
}
.badge-free { background: #e8f8ee; color: #137333; }
.badge-warn { background: #fef3e0; color: #9a5b00; }
.badge-purple { background: #f3effd; color: #5e35b1; }
.badge-danger { background: #fdecec; color: #c5221f; }
.controls { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
input, select {
  background: #ffffff;
  color: var(--text);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  padding: 9px 12px;
  font-size: 0.92rem;
  font-family: inherit;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
input:focus-visible, select:focus-visible {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.18);
}
input[type="checkbox"] {
  accent-color: var(--accent);
  width: 15px;
  height: 15px;
  vertical-align: middle;
}
.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: var(--surface);
}
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; font-variant-numeric: tabular-nums; }
th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid rgba(0, 0, 0, 0.06); vertical-align: top; }
th {
  background: #fbfbfd;
  color: var(--muted);
  font-weight: 600;
  font-size: 0.79rem;
  letter-spacing: 0.01em;
  white-space: nowrap;
}
.num-col { text-align: right; white-space: nowrap; }
tbody tr { content-visibility: auto; contain-intrinsic-size: 0 54px; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #fafafa; }
.calc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(195px, 1fr)); gap: 14px; }
.calc-result {
  margin-top: 18px;
  padding: 18px 20px;
  border-radius: 14px;
  background: #ffffff;
  border: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}
.tool-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-top: 14px;
  font-size: 0.85rem;
  color: var(--muted);
}
.btn {
  display: inline-block;
  background: var(--accent);
  color: #ffffff;
  font-weight: 500;
  padding: 9px 18px;
  border-radius: 980px;
  font-size: 0.88rem;
  letter-spacing: -0.01em;
  transition: background-color 0.15s ease;
}
.btn:hover { background: var(--accent-hover); color: #ffffff; text-decoration: none; }
.ad-slot {
  margin: 26px 0; text-align: center; min-height: 90px;
  border: 1px dashed var(--border-strong); border-radius: 14px; padding: 10px;
  display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 0.84rem;
  background: var(--surface-2);
}
.breadcrumb { font-size: 0.83rem; color: var(--muted); margin-bottom: 14px; }
.breadcrumb a { color: var(--muted); }
.breadcrumb a:hover { color: var(--text); }
footer {
  border-top: 1px solid var(--border);
  background: var(--surface-2);
  padding: 32px 0 52px;
  color: var(--muted);
  font-size: 0.82rem;
}
.footer-links { display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 10px; }
.footer-links a { color: #424245; }
.footer-links a:hover { color: var(--text); }
.provider-cards-mobile { display: none; }
.provider-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 14px 16px;
  margin-bottom: 12px;
}
.provider-card summary {
  cursor: pointer;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.provider-card summary::-webkit-details-marker { display: none; }
.provider-card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.provider-kv { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); font-size: 0.84rem; }
.provider-kv dt { color: var(--muted); font-size: 0.76rem; }
.provider-kv dd { margin: 2px 0 0; color: var(--text); font-weight: 500; }
.quota-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin-top: 12px; }
.quota-item { background: var(--surface-2); border-radius: 12px; padding: 12px 14px; font-size: 0.85rem; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; scroll-behavior: auto !important; }
}
@media (max-width: 768px) {
  .provider-table-desktop { display: none; }
  .provider-cards-mobile { display: block; }
}
@media (max-width: 480px) {
  .wrap { padding: 0 16px; }
  main { padding: 20px 0 52px; }
  h2 { font-size: 1.22rem; margin-top: 28px; }
  .calc-panel, .card { padding: 16px; border-radius: 16px; }
  .calc-grid { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .calc-grid .span-2 { grid-column: 1 / -1; }
  .calc-result { padding: 14px; margin-top: 12px; }
  .stats { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .stat { padding: 13px 14px; }
  .stat .num { font-size: 1.28rem; }
  .nav-links { gap: 14px; font-size: 0.83rem; }
  .metrics-strip { gap: 8px 18px; font-size: 0.82rem; }
  .controls input { min-width: 0 !important; width: 100%; }
}
"""


def render_ad_slot(cfg: dict[str, Any], slot_key: str) -> str:
    mon = cfg.get("monetization", {})
    client_id = str(mon.get("adsense_client_id") or "").strip()
    slot_id = str(mon.get(slot_key) or "").strip()
    if not client_id or not slot_id:
        return ""
    return (
        '<div class="ad-slot">'
        f'<ins class="adsbygoogle" style="display:block" data-ad-client="{e(client_id)}" '
        f'data-ad-slot="{e(slot_id)}" data-ad-format="auto" data-full-width-responsive="true"></ins>'
        "<script>(adsbygoogle = window.adsbygoogle || []).push({});</script>"
        "</div>"
    )


def render_affiliates(cfg: dict[str, Any]) -> str:
    affs = [a for a in cfg.get("monetization", {}).get("affiliates", []) if a.get("url")]
    if not affs:
        return ""
    cards = []
    for a in affs:
        cards.append(
            f'<div class="card">'
            f'<span class="badge">{e(a.get("badge", "Partner"))}</span>'
            f'<h3>{e(a["name"])}</h3>'
            f'<p class="muted">{e(a["description"])}</p>'
            f'<a class="btn" href="{e(a["url"])}" rel="sponsored noopener noreferrer" target="_blank">{e(a["cta"])} &rarr;</a>'
            f"</div>"
        )
    return '<h2>Infrastructure Partners</h2><p class="muted small">Affiliate disclosure: links below are marked <code>sponsored</code> and may earn a cash commission at no extra cost to you.</p><div class="grid">' + "".join(cards) + "</div>"


def render_layout(
    cfg: dict[str, Any],
    *,
    title: str,
    description: str,
    canonical_path: str,
    body_html: str,
    git_commit: str,
    index_policy: str = "index",
    allow_ads: bool | None = None,
    breadcrumbs: list[tuple[str, str]] | None = None,
    extra_json_ld: dict[str, Any] | None = None,
) -> str:
    base = cfg["base_url"].rstrip("/")
    canonical = base + canonical_path
    client_id = str(cfg.get("monetization", {}).get("adsense_client_id") or "").strip()
    robots_meta = '<meta name="robots" content="noindex, follow" />' if index_policy == "noindex" else ""
    no_ad_paths = {"/404.html", "/repos/", "/privacy/", "/terms/", "/contact/"}
    ads_allowed = (index_policy == "index" and canonical_path not in no_ad_paths) if allow_ads is None else bool(allow_ads)
    adsense_head = (
        f'<meta name="google-adsense-account" content="{e(client_id)}" />\n'
        f'  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={e(client_id)}" '
        f'crossorigin="anonymous"></script>'
        if (client_id and ads_allowed)
        else ""
    )

    ld_blocks: list[str] = []
    if breadcrumbs:
        bc_ld = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": idx + 1, "name": lbl, "item": base + path}
                for idx, (lbl, path) in enumerate(breadcrumbs)
            ],
        }
        ld_blocks.append(f'<script type="application/ld+json">{json.dumps(bc_ld, ensure_ascii=False)}</script>')
    if extra_json_ld:
        ld_blocks.append(f'<script type="application/ld+json">{json.dumps(extra_json_ld, ensure_ascii=False)}</script>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="theme-color" content="#ffffff" />
  <title>{e(title)}</title>
  <meta name="description" content="{e(description)}" />
  {robots_meta}
  <link rel="canonical" href="{e(canonical)}" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="alternate" type="application/rss+xml" title="SysV RSS" href="/feed.xml" />
  <meta property="og:title" content="{e(title)}" />
  <meta property="og:description" content="{e(description)}" />
  <meta property="og:url" content="{e(canonical)}" />
  <meta property="og:type" content="website" />
  <style>{CSS}</style>
  {adsense_head}
  {"".join(ld_blocks)}
</head>
<body>
  <a class="skip-link" href="#main-content">Skip to main content</a>
  <header>
    <div class="wrap nav">
      <a class="brand" href="/" translate="no">SysV<span>.cn</span></a>
      <nav class="nav-links" aria-label="Primary">
        <a href="/">Pricing</a>
        <a href="/tools/">Calculators</a>
        <a href="/free-llm-apis/">Free APIs</a>
        <a href="/compare/">Compare</a>
        <a href="/self-hosting/">Self-Hosting</a>
      </nav>
    </div>
  </header>
  <main id="main-content" class="wrap" tabindex="-1">
    {body_html}
  </main>
  <footer>
    <div class="wrap">
      <div class="footer-links">
        <a href="/models/">All Models</a>
        <a href="/price-changes/">Price History</a>
        <a href="/data/">Open Data &amp; CSV</a>
        <a href="/about/">About &amp; Methodology</a>
        <a href="/privacy/">Privacy Policy</a>
        <a href="/terms/">Terms of Use</a>
        <a href="/contact/">Contact</a>
        <a href="/sitemap.xml">Sitemap</a>
        <a href="/version.json">Build Info</a>
      </div>
      <div>&copy; 2026 <span translate="no">{e(cfg["site_name"])} ({e(cfg["domain"])})</span> &mdash; {e(cfg["tagline"])} &middot; Commit <code translate="no">{e(git_commit)}</code> &middot; Source: OpenRouter &amp; GitHub Public APIs.</div>
    </div>
  </footer>
</body>
</html>
"""


def render_embed_layout(
    cfg: dict[str, Any],
    *,
    title: str,
    canonical_path: str,
    body_html: str,
) -> str:
    """Strictly ad-free, tracker-free embed layout (`noindex, follow`, canonical -> /tools/...)."""
    base = cfg["base_url"].rstrip("/")
    canonical = base + canonical_path
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{e(title)}</title>
  <meta name="robots" content="noindex, follow" />
  <link rel="canonical" href="{e(canonical)}" />
  <style>{CSS}
  body {{ padding: 14px; }}
  </style>
</head>
<body>
  <div style="max-width:860px;margin:0 auto">
    {body_html}
    <div class="muted small" style="text-align:right;margin-top:8px">Data &amp; calculator by <a href="{e(canonical)}" target="_blank" rel="noopener">SysV.cn</a> (ad-free embed)</div>
  </div>
</body>
</html>
"""


def write_page(rel_path: str, content: str) -> None:
    out = DIST_DIR / rel_path.lstrip("/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")


def filter_task_candidates(
    models: list[dict[str, Any]], *, task: str, include_batch: bool = False
) -> list[dict[str, Any]]:
    """Capability-gated filter maintaining exact parity with /assets/calc-engine.js."""
    allowed_types = {"limited_free", "free", "paid"} | ({"batch"} if include_batch else set())
    result = []
    for m in models:
        if m.get("status", "active") != "active":
            continue
        if m.get("pricing_type") not in allowed_types:
            continue
        p_in, p_out = m.get("prompt_price_per_m"), m.get("completion_price_per_m")
        if not isinstance(p_in, (int, float)) or not isinstance(p_out, (int, float)) or p_in < 0 or p_out < 0:
            continue
        caps = m.get("capabilities") or {}
        if task == "coding":
            if not m.get("is_realtime") or not caps.get("tools") or int(m.get("context_length") or 0) < 64000:
                continue
        elif task == "autosite":
            if not m.get("is_realtime") or not caps.get("structured_outputs"):
                continue
        elif task == "n8n":
            if not m.get("is_realtime") or not caps.get("tools"):
                continue
        elif task == "rag":
            if not m.get("is_realtime") or int(m.get("context_length") or 0) < 100000:
                continue
        elif task == "translation":
            if not include_batch and not m.get("is_realtime"):
                continue
        result.append(m)
    return result


def reconcile_comparison_registry(
    models: list[dict[str, Any]],
    max_new_pairs: int,
    registry_path=DATA_DIR / "published_comparisons.json",
    today: str | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any], str, str, str]]:
    """Maintain permanent, order-independent comparison URLs (`published_comparisons.json`).

    Returns list of (model_a, model_b, canonical_slug, cohort_label, status).
    """
    today = today or utc_today()
    registry: dict[str, dict[str, Any]] = load_json(registry_path, default={})
    models_by_id = {m["id"]: m for m in models if "id" in m}

    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cohort_labels = {
        "free_general": "Rate-Limited Free ($0) Head-to-Head",
        "coding": "Real-Time Coding & Tool-Calling Models",
        "reasoning": "Reasoning & Thinking Models",
        "budget_general": "Low-Cost Real-Time Production APIs",
        "flagship_general": "Flagship & Long-Context APIs",
    }

    for m in models:
        if m.get("status", "active") != "active":
            continue
        if m.get("pricing_type") not in ("limited_free", "free", "paid") or not m.get("is_realtime", True):
            continue
        cat = m.get("task_category", "")
        if cat in ("router", "batch", "media", "unknown"):
            continue
        bucket = "flagship_general" if cat == "long_context" else cat
        if bucket in cohort_labels:
            cohorts[bucket].append(m)

    # 1. Reconcile existing permanently published comparisons (NEVER silently drop!)
    active_count = 0
    for pkey, rec in registry.items():
        ma = models_by_id.get(rec["model_a_id"])
        mb = models_by_id.get(rec["model_b_id"])
        valid_realtime_pair = (
            ma is not None
            and mb is not None
            and ma.get("status", "active") == "active"
            and mb.get("status", "active") == "active"
            and ma.get("pricing_type") in ("limited_free", "free", "paid")
            and mb.get("pricing_type") in ("limited_free", "free", "paid")
            and bool(ma.get("is_realtime"))
            and bool(mb.get("is_realtime"))
        )
        if valid_realtime_pair:
            rec["status"] = "active"
            active_count += 1
        else:
            rec["status"] = "archived"

    # 2. Add new order-independent pairs up to max_new_pairs active total
    order = ["free_general", "coding", "reasoning", "budget_general", "flagship_general"]
    per_bucket_cap = max(6, max_new_pairs // len(order) + 2)

    for bucket in order:
        if active_count >= max_new_pairs:
            break
        items = cohorts.get(bucket, [])[:10]
        added_here = 0
        for m1, m2 in combinations(items, 2):
            if m1["provider"] == m2["provider"]:
                continue
            # Order-independent canonical identity key prevents A-vs-B / B-vs-A duplicates!
            first, second = (m1, m2) if m1["slug"] <= m2["slug"] else (m2, m1)
            pkey = f"{first['slug']}::{second['slug']}"
            if pkey in registry:
                continue
            slug = f"{first['slug']}-vs-{second['slug']}"
            registry[pkey] = {
                "pair_key": pkey,
                "slug": slug,
                "model_a_id": first["id"],
                "model_b_id": second["id"],
                "cohort": cohort_labels[bucket],
                "first_published": today,
                "status": "active",
            }
            active_count += 1
            added_here += 1
            if added_here >= per_bucket_cap or active_count >= max_new_pairs:
                break

    save_json(registry_path, registry)

    resolved: list[tuple[dict[str, Any], dict[str, Any], str, str, str]] = []
    for pkey in sorted(registry.keys()):
        rec = registry[pkey]
        ma = models_by_id.get(rec["model_a_id"])
        mb = models_by_id.get(rec["model_b_id"])
        if ma and mb:
            resolved.append((ma, mb, rec["slug"], rec["cohort"], rec["status"]))
    return resolved


def update_published_pages_registry(
    page_records: dict[str, dict[str, str]],
    registry_path=DATA_DIR / "published_pages.json",
    now_iso: str | None = None,
    today: str | None = None,
) -> dict[str, dict[str, Any]]:
    """True cross-calendar-day `lastmod` reconciliation over semantic hashes."""
    now_iso = now_iso or utc_now_iso()
    today = today or utc_today()
    prev: dict[str, dict[str, Any]] = load_json(registry_path, default={})
    updated: dict[str, dict[str, Any]] = {}

    for url_path, rec in sorted(page_records.items()):
        old = prev.get(url_path)
        curr_hash = rec["semantic_hash"]
        if old and old.get("semantic_hash") == curr_hash and old.get("lastmod"):
            lastmod = old["lastmod"]
            first_pub = old.get("first_published", today)
        else:
            lastmod = now_iso
            first_pub = old.get("first_published", today) if old else today

        updated[url_path] = {
            "url": url_path,
            "page_type": rec["page_type"],
            "status": rec.get("status", "active"),
            "index_policy": rec["index_policy"],
            "first_published": first_pub,
            "semantic_hash": curr_hash,
            "lastmod": lastmod,
        }

    # Retain historical record of any previously published URL that is no longer generated (marked 'gone')
    for old_url, old in prev.items():
        if old_url not in updated:
            old_copy = dict(old)
            old_copy["status"] = "gone"
            old_copy["index_policy"] = "noindex"
            updated[old_url] = old_copy

    save_json(registry_path, updated)
    return updated


def render_task_cell(m: dict[str, Any], task: str, m_in: float, m_out: float) -> str:
    """Display exact estimated cost or honest 'Not Applicable' when capability gate fails."""
    caps = m.get("capabilities") or {}
    if task == "coding":
        if not m.get("is_realtime"):
            return '<span class="muted small">Not Applicable (Async Batch only)</span>'
        if not caps.get("tools"):
            return '<span class="muted small">Not Applicable (No tool calling)</span>'
        if int(m.get("context_length") or 0) < 64000:
            return '<span class="muted small">Not Applicable (Context &lt; 64K)</span>'
    elif task == "autosite":
        if not m.get("is_realtime"):
            return '<span class="muted small">Not Applicable (Async Batch only)</span>'
        if not caps.get("structured_outputs"):
            return '<span class="muted small">Not Applicable (No structured JSON)</span>'
    elif task == "translation":
        if m.get("pricing_type") not in ("limited_free", "free", "paid", "batch"):
            return '<span class="muted small">Not Applicable</span>'

    p_in, p_out = m.get("prompt_price_per_m"), m.get("completion_price_per_m")
    if not isinstance(p_in, (int, float)) or not isinstance(p_out, (int, float)):
        return '<span class="muted small">Not Applicable (No fixed token price)</span>'
    return f"<strong>${(p_in * m_in + p_out * m_out):.2f}</strong>"


def main(today_override: str | None = None, now_iso_override: str | None = None) -> None:
    cfg = load_config()
    today = today_override or utc_today()
    now_iso = now_iso_override or utc_now_iso()
    git_commit = get_git_commit()

    models = load_json(DATA_DIR / "models.json", default=load_json(DATA_DIR / "seed_models.json", default=[]))
    repos = load_json(DATA_DIR / "repos.json", default=load_json(DATA_DIR / "seed_repos.json", default=[]))
    changelog = load_json(
        DATA_DIR / "price_changes.json",
        default={"snapshot_days": 1, "first_snapshot_date": today, "latest_total_endpoints": len(models), "events": []},
    )
    selection_audit = load_json(DATA_DIR / "selection_audit.json", default={})

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    page_records: dict[str, dict[str, str]] = {}

    def register_page(
        rel_path: str,
        url_path: str,
        page_type: str,
        index_policy: str,
        semantic_hash: str,
        full_html: str,
        status: str = "active",
    ) -> None:
        write_page(rel_path, full_html)
        page_records[url_path] = {
            "page_type": page_type,
            "index_policy": index_policy,
            "semantic_hash": semantic_hash,
            "status": status,
        }

    active_models = [m for m in models if m.get("status", "active") == "active"]
    calc_models = [
        {
            "name": m["name"],
            "slug": m["slug"],
            "provider": m["provider"],
            "pricing_type": m["pricing_type"],
            "is_realtime": bool(m.get("is_realtime", True)),
            "tools": bool((m.get("capabilities") or {}).get("tools")),
            "structured": bool((m.get("capabilities") or {}).get("structured_outputs")),
            "tiered": bool(m.get("has_tiered_overrides")),
            "in": m["prompt_price_per_m"],
            "out": m["completion_price_per_m"],
            "cache_read": m.get("cache_read_price_per_m"),
            "ctx": m["context_length"],
        }
        for m in active_models
        if m["pricing_type"] in ("limited_free", "free", "paid", "batch")
        and isinstance(m.get("prompt_price_per_m"), (int, float))
        and isinstance(m.get("completion_price_per_m"), (int, float))
    ]

    pairs = reconcile_comparison_registry(models, int(cfg["pipeline"].get("max_compare_pages", 40)), today=today)
    active_pairs = [p for p in pairs if p[4] == "active"]
    models_sem_sig = sem_hash([model_semantic_fingerprint(m) for m in active_models])
    repos_sem_sig = sem_hash([repo_semantic_fingerprint(r) for r in repos])

    # --- 1. HOMEPAGE (Search-driven Title & H1) ---
    free_models = [m for m in active_models if m["pricing_type"] in ("limited_free", "free")]
    paid_models = [m for m in active_models if m["pricing_type"] == "paid"]
    batch_models = [m for m in active_models if m["pricing_type"] == "batch"]

    rows = []
    for m in active_models:
        ptype = m["pricing_type"]
        caps = m.get("capabilities") or {}
        cap_tags = [t for t, ok in [("Tools", caps.get("tools")), ("JSON", caps.get("structured_outputs")), ("Async", not m.get("is_realtime"))] if ok]
        sort_price = (
            (m["prompt_price_per_m"] + m["completion_price_per_m"])
            if isinstance(m.get("prompt_price_per_m"), (int, float)) and isinstance(m.get("completion_price_per_m"), (int, float))
            else 999999.0
        )
        rows.append(
            f'<tr data-ptype="{e(ptype)}" data-price="{sort_price}" data-ctx="{m["context_length"]}" '
            f'data-search="{e((m["name"] + " " + m["provider"] + " " + m["id"] + " " + ptype).lower())}">'
            f'<td><a href="/models/{e(m["slug"])}/" translate="no"><strong>{e(m["name"])}</strong></a><br><span class="muted small" translate="no">{e(m["id"])}</span></td>'
            f'<td>{pricing_badge(m)}</td>'
            f'<td class="muted small">{e(" / ".join(cap_tags) or "Text")}</td>'
            f'<td class="num-col">{e(fmt_ctx(m["context_length"]))}</td>'
            f'<td class="num-col">{e(fmt_price(m["prompt_price_per_m"], ptype))}</td>'
            f'<td class="num-col">{e(fmt_price(m["completion_price_per_m"], ptype))}</td>'
            f"</tr>"
        )

    compare_cards = "".join(
        f'<div class="card"><span class="badge">{e(lbl)}</span>'
        f'<div style="margin-top:8px"><a href="/compare/{e(slug)}/" translate="no"><strong>{e(a["name"])} vs {e(b["name"])}</strong></a></div>'
        f'<p class="muted small" style="margin:6px 0 0">Input: {e(fmt_price(a["prompt_price_per_m"], a["pricing_type"]))} vs {e(fmt_price(b["prompt_price_per_m"], b["pricing_type"]))} &middot; '
        f'Context: {fmt_ctx(a["context_length"])} vs {fmt_ctx(b["context_length"])}</p></div>'
        for a, b, slug, lbl, _ in active_pairs[:6]
    )

    home_body = f"""
    <section class="hero-open">
      <h1>LLM API Pricing &amp; Token Cost Calculator</h1>
      <p class="lead">Compare live AI API pricing per 1M tokens, run capability-gated workload estimates, and separate rate-limited free tiers, async batch endpoints, and variable routers.</p>
      <div class="metrics-strip">
        <span><strong>{len(free_models)}</strong> Rate-Limited Free ($0)</span>
        <span><strong>{len(paid_models)}</strong> Real-Time Paid</span>
        <span><strong>{len(batch_models)}</strong> Async Batch</span>
        <span><strong>{changelog.get("latest_total_endpoints", len(active_models))}</strong> Endpoints in Daily Snapshot</span>
      </div>
    </section>

    <section class="calc-panel" id="task-calculator">
      <h2>AI API Token Cost Calculator</h2>
      <p class="muted small">Computes base input and output token spend in USD per 1M tokens from OpenRouter public pricing metadata. Excludes tiered long-context surcharges, prompt-cache discounts, and dynamic routers (<code translate="no">-1</code>).</p>
      <div class="calc-grid">
        <div>
          <label class="muted small" for="task-preset">Workload &amp; Capability Gate</label><br />
          <select id="task-preset" style="width:100%;margin-top:6px">
            <option value="coding" data-in="18.0" data-out="3.5">AI Coding Agent (Real-Time + Tools + &ge;64K ctx)</option>
            <option value="autosite" data-in="1.2" data-out="0.4">Structured Data Pipeline (Real-Time + JSON)</option>
            <option value="translation" data-in="2.5" data-out="2.5">Bulk Translation (1M words / mo, Optional Batch)</option>
            <option value="rag" data-in="12.0" data-out="1.0">RAG Document QA (Real-Time + &ge;100K ctx)</option>
            <option value="custom" data-in="5.0" data-out="1.0">Custom Token Volume</option>
          </select>
        </div>
        <div>
          <label class="muted small" for="calc-in">Monthly Input Tokens (Millions)</label><br />
          <input id="calc-in" type="number" min="0" step="0.1" value="18.0" autocomplete="off" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="calc-out">Monthly Output Tokens (Millions)</label><br />
          <input id="calc-out" type="number" min="0" step="0.1" value="3.5" autocomplete="off" style="width:100%;margin-top:6px" />
        </div>
      </div>
      <div style="margin-top:12px">
        <label class="muted small" style="cursor:pointer"><input id="include-batch" type="checkbox" /> Include asynchronous <code translate="no">:batch</code> endpoints (bulk non-interactive tasks only)</label>
      </div>
      <div class="calc-result" id="task-ranking" aria-live="polite"></div>
      <div class="tool-strip">
        <span>Specialized calculators:</span>
        <a href="/tools/openrouter-cost-calculator/">OpenRouter Fees</a>
        <a href="/tools/n8n-ai-workflow-cost-calculator/">n8n AI Workflow</a>
        <a href="/tools/llm-translation-cost-calculator/">1,000-Doc Translation</a>
        <a href="/tools/api-vs-local-llm-calculator/">API vs Local GPU</a>
      </div>
    </section>

    {render_ad_slot(cfg, "adsense_slot_top")}

    <section>
      <h2>Live LLM API Pricing Table (USD / 1M Tokens)</h2>
      <p class="muted small">Showing {len(active_models)} curated active endpoints. Browse the <a href="/models/">full models directory</a>, track <a href="/price-changes/">daily price history</a>, or export <a href="/data/">open JSON and CSV</a>.</p>
      <div class="controls">
        <input id="model-search" type="search" aria-label="Search models, providers, or pricing types" autocomplete="off" spellcheck="false" placeholder="Search model, provider, or type&hellip;" style="flex:1;min-width:220px" />
        <select id="tier-filter" aria-label="Filter by pricing type">
          <option value="all">All Pricing Types</option>
          <option value="limited_free">Limited Free Tier ($0)</option>
          <option value="paid">Real-Time Paid Only</option>
          <option value="batch">Async Batch Only</option>
          <option value="variable">Variable Routers Only</option>
        </select>
        <select id="model-sort" aria-label="Sort models">
          <option value="default">Sort: Featured</option>
          <option value="price-asc">Price: Lowest Fixed First</option>
          <option value="ctx-desc">Context: Largest First</option>
        </select>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Model Endpoint</th><th>Pricing Type</th><th>Capabilities</th><th class="num-col">Context</th><th class="num-col">Input / 1M</th><th class="num-col">Output / 1M</th></tr></thead>
          <tbody id="model-tbody">{"".join(rows)}</tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>Same-Cohort LLM Comparisons</h2>
      <div class="grid">{compare_cards}</div>
      <p><a href="/compare/">Browse all {len(active_pairs)} active comparisons &rarr;</a></p>
    </section>

    {render_affiliates(cfg)}

    <script src="/assets/calc-engine.js"></script>
    <script>
      (function() {{
        const MODELS = {json.dumps(calc_models, ensure_ascii=False)};
        const presetEl = document.getElementById('task-preset');
        const inEl = document.getElementById('calc-in');
        const outEl = document.getElementById('calc-out');
        const batchEl = document.getElementById('include-batch');
        const outBox = document.getElementById('task-ranking');

        function renderRanking() {{
          const mIn = Math.max(0, parseFloat(inEl.value || '0') || 0);
          const mOut = Math.max(0, parseFloat(outEl.value || '0') || 0);
          const task = presetEl.value;
          const allowBatch = batchEl.checked && (task === 'translation' || task === 'custom');
          const eligible = window.SysVCalc.filterEligible(MODELS, task, allowBatch);

          const paid = eligible
            .filter(m => m.pricing_type === 'paid' || (allowBatch && m.pricing_type === 'batch'))
            .map(m => ({{ ...m, cost: window.SysVCalc.estimateMonthlyCost(m, mIn, mOut, 1.0) }}))
            .sort((a, b) => a.cost - b.cost)
            .slice(0, 5);
          const frees = eligible.filter(m => m.pricing_type === 'limited_free' || m.pricing_type === 'free').slice(0, 4);

          let html = '<strong>Lowest Estimated Token Cost (' + eligible.length + ' capability-matched models) for ' + mIn.toFixed(1) + 'M in / ' + mOut.toFixed(1) + 'M out:</strong><ol style="margin:8px 0 10px;padding-left:20px">';
          paid.forEach(m => {{
            const tags = [];
            if (m.pricing_type === 'batch') tags.push('Async Batch');
            if (m.tiered) tags.push('Tiered >ctx surcharge excluded');
            if (m.cache_read !== null) tags.push('Cache read $' + m.cache_read + '/1M');
            const extra = tags.length ? ' &middot; ' + tags.join(' &middot; ') : '';
            html += '<li><a href="/models/' + m.slug + '/"><strong>' + m.name + '</strong></a> &mdash; <strong>Est. $' + m.cost.toFixed(2) + '/mo</strong> <span class="muted small">($' + m.in + ' in / $' + m.out + ' out per 1M' + extra + ')</span></li>';
          }});
          html += '</ol>';
          if (frees.length) {{
            html += '<strong>Rate-Limited Free ($0) Alternatives Matching Gate:</strong> ' + frees.map(m => '<a href="/models/' + m.slug + '/">' + m.name + '</a>').join(' &middot; ');
          }} else {{
            html += '<span class="muted small">No rate-limited free endpoints currently satisfy all capability gates for this workload.</span>';
          }}
          outBox.innerHTML = html;
        }}

        presetEl.addEventListener('change', () => {{
          const opt = presetEl.options[presetEl.selectedIndex];
          if (opt.value !== 'custom') {{ inEl.value = opt.dataset.in; outEl.value = opt.dataset.out; }}
          renderRanking();
        }});
        [inEl, outEl].forEach(el => {{
          el.addEventListener('input', renderRanking);
          el.addEventListener('change', renderRanking);
        }});
        batchEl.addEventListener('change', renderRanking);
        renderRanking();

        const searchEl = document.getElementById('model-search');
        const tierEl = document.getElementById('tier-filter');
        const sortEl = document.getElementById('model-sort');
        const tbody = document.getElementById('model-tbody');
        const origRows = Array.from(tbody.querySelectorAll('tr'));
        function filterAndSortRows() {{
          const q = (searchEl.value || '').toLowerCase().trim();
          const tier = tierEl.value;
          const mode = sortEl.value;
          let list = origRows.slice();
          if (mode === 'price-asc') list.sort((a, b) => parseFloat(a.dataset.price) - parseFloat(b.dataset.price));
          else if (mode === 'ctx-desc') list.sort((a, b) => parseInt(b.dataset.ctx, 10) - parseInt(a.dataset.ctx, 10));
          list.forEach(tr => {{
            const ok = (!q || tr.dataset.search.includes(q)) && (tier === 'all' || tr.dataset.ptype === tier);
            tr.style.display = ok ? '' : 'none';
            tbody.appendChild(tr);
          }});
        }}
        searchEl.addEventListener('input', filterAndSortRows);
        tierEl.addEventListener('change', filterAndSortRows);
        sortEl.addEventListener('change', filterAndSortRows);
      }})();
    </script>
    """
    register_page(
        "index.html",
        "/",
        "home",
        "index",
        sem_hash(["home", TEMPLATE_VERSION, models_sem_sig]),
        render_layout(
            cfg,
            title="LLM API Pricing & Token Cost Calculator | SysV",
            description=cfg["description"],
            canonical_path="/",
            body_html=home_body,
            git_commit=git_commit,
            index_policy="index",
            extra_json_ld={"@context": "https://schema.org", "@type": "WebSite", "name": "SysV", "url": cfg["base_url"]},
        ),
    )

    # --- 2. /models/ Hub Page (Fixes Paid Model Breadcrumb Architecture!) ---
    models_hub_rows = "".join(
        f'<tr><td><a href="/models/{e(m["slug"])}/" translate="no"><strong>{e(m["name"])}</strong></a></td>'
        f'<td translate="no">{e(m["provider"])}</td><td>{pricing_badge(m)}</td><td class="num-col">{e(fmt_ctx(m["context_length"]))}</td>'
        f'<td class="num-col">{e(fmt_price(m["prompt_price_per_m"], m["pricing_type"]))}</td>'
        f'<td class="num-col">{e(fmt_price(m["completion_price_per_m"], m["pricing_type"]))}</td></tr>'
        for m in active_models
    )
    models_hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Models</div>
    <h1>All LLM API Models &amp; Pricing Directory ({len(active_models)} Active)</h1>
    <p class="lead">Complete directory of Source Verified LLM endpoints tracked on SysV, spanning real-time paid models, rate-limited free models, async <code translate="no">:batch</code> endpoints, and flagged variable routers.</p>
    <div class="table-wrap" style="margin-top:24px"><table><thead><tr><th>Model</th><th>Provider</th><th>Pricing Type</th><th class="num-col">Context</th><th class="num-col">Input / 1M</th><th class="num-col">Output / 1M</th></tr></thead><tbody>{models_hub_rows}</tbody></table></div>
    """
    register_page(
        "models/index.html",
        "/models/",
        "models_hub",
        "index",
        sem_hash(["models_hub", TEMPLATE_VERSION, models_sem_sig]),
        render_layout(
            cfg,
            title="All LLM API Models & Pricing Directory | SysV",
            description=f"Browse {len(active_models)} Source Verified LLM API models with per-1M-token pricing, context limits, and capability flags.",
            canonical_path="/models/",
            body_html=models_hub_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Models", "/models/")],
        ),
    )

    # --- 3. /free-llm-apis/ Hub Page (Structured Provider Policy DB + RPM/RPD/Neurons Calculator + Mobile Expandable Cards) ---
    routers = [m for m in active_models if m["pricing_type"] in ("free_router", "variable")]
    free_rows = "".join(
        f'<tr data-tools="{"1" if (m.get("capabilities") or {}).get("tools") else "0"}" data-json="{"1" if (m.get("capabilities") or {}).get("structured_outputs") else "0"}">'
        f'<td><a href="/models/{e(m["slug"])}/" translate="no"><strong>{e(m["name"])}</strong></a><br><span class="muted small" translate="no">{e(m["id"])}</span></td>'
        f'<td translate="no">{e(m["provider"])}</td><td class="num-col">{e(fmt_ctx(m["context_length"]))}</td>'
        f'<td class="muted small">Tools: <strong>{"Yes" if (m.get("capabilities") or {}).get("tools") else "No"}</strong> / JSON: <strong>{"Yes" if (m.get("capabilities") or {}).get("structured_outputs") else "No"}</strong></td>'
        f'<td>{pricing_badge(m)}</td></tr>'
        for m in free_models
    )
    router_rows = "".join(
        f'<tr><td><a href="/models/{e(m["slug"])}/" translate="no"><strong>{e(m["name"])}</strong></a></td><td>{pricing_badge(m)}</td><td class="num-col">{e(fmt_ctx(m["context_length"]))}</td><td class="muted small">{e(m.get("rate_limit_note", ""))}</td></tr>'
        for m in routers
    )

    policy_db = load_json(DATA_DIR / "free_provider_policies.json", default={"providers": [], "policy_history": []})
    providers_list = policy_db.get("providers") or []
    policy_history = policy_db.get("policy_history") or []

    def _tier_badge(p: dict[str, Any]) -> str:
        tc = p.get("tier_classification", "unknown")
        lbl = p.get("tier_label", tc)
        if tc == "permanently_free":
            return f'<span class="badge badge-free">{e(lbl)}</span>'
        if tc == "permanently_free_with_credit_unlock":
            return f'<span class="badge badge-purple">{e(lbl)}</span>'
        if tc == "free_trial":
            return f'<span class="badge badge-warn">{e(lbl)}</span>'
        return f'<span class="badge">{e(lbl)}</span>'

    def _verify_badge(p: dict[str, Any]) -> str:
        st = p.get("verification_status", "UNKNOWN")
        dt = p.get("last_verified_date") or "Unknown"
        if st == "OFFICIAL_DOCS_VERIFIED":
            return f'<span class="badge badge-free">Docs Verified ({e(dt)})</span>'
        if st == "DOC_CHANGED_SIGNAL_STABLE":
            return f'<span class="badge badge-free">Verified ({e(dt)}) &middot; Docs Layout Updated</span>'
        if st == "FETCH_ERROR_CACHED":
            return f'<span class="badge">Cached Verified ({e(dt)}) &middot; Retry Pending</span>'
        if st == "POLICY_CHANGED_NEEDS_REVIEW":
            return f'<span class="badge badge-danger">Policy Drift — Paused ({e(dt)})</span>'
        if st in ("FETCH_ERROR_PERSISTENT", "FETCH_ERROR"):
            return f'<span class="badge badge-danger">Fetch Degraded — Check Docs ({e(dt)})</span>'
        if st == "STALE":
            return f'<span class="badge badge-warn">STALE — Paused ({e(dt)})</span>'
        return '<span class="badge badge-danger">UNKNOWN</span>'

    provider_rows = []
    provider_mobile_cards = []
    for p in providers_list:
        card_req = "Yes (Payment method required)" if p.get("credit_card_required") else "No"
        if p.get("credit_card_note"):
            card_detail = f'{card_req} &mdash; {e(p["credit_card_note"])}'
        else:
            card_detail = card_req
        provider_rows.append(
            f'<tr>'
            f'<td><strong translate="no">{e(p["name"])}</strong><br>{_tier_badge(p)}<br><a class="small" href="{e(p["source_url"])}" target="_blank" rel="noopener noreferrer">Official Docs</a></td>'
            f'<td class="small">{card_detail}</td>'
            f'<td class="small">{e(p.get("quota_summary", ""))}<br><span class="muted">Reset: {e(p.get("reset_cycle", "Unknown"))}</span></td>'
            f'<td class="small" translate="no">{e(p.get("api_compatibility", ""))}</td>'
            f'<td class="small">{e(p.get("privacy_policy", ""))}</td>'
            f'<td class="small">{_verify_badge(p)}<br><span class="muted">Runtime: Not Live-Probed</span></td>'
            f'</tr>'
        )
        provider_mobile_cards.append(
            f'<details class="provider-card"{" open" if p.get("id") in ("openrouter", "cerebras") else ""}>'
            f'<summary>'
            f'<div class="provider-card-head"><strong translate="no">{e(p["name"])}</strong>{_tier_badge(p)}</div>'
            f'<div class="small muted">Card required: <strong>{e(card_req)}</strong> &middot; Tap for full limits &amp; policy</div>'
            f'</summary>'
            f'<dl class="provider-kv">'
            f'<div><dt>Credit Card Rule</dt><dd>{card_detail}</dd></div>'
            f'<div><dt>Reset / Expiry</dt><dd>{e(p.get("reset_cycle", "Unknown"))}</dd></div>'
            f'<div style="grid-column:1/-1"><dt>Verified Quota</dt><dd>{e(p.get("quota_summary", ""))}</dd></div>'
            f'<div style="grid-column:1/-1"><dt>API Compatibility</dt><dd translate="no">{e(p.get("api_compatibility", ""))}</dd></div>'
            f'<div style="grid-column:1/-1"><dt>Data &amp; Training Policy</dt><dd>{e(p.get("privacy_policy", ""))}</dd></div>'
            f'<div><dt>Verification</dt><dd>{_verify_badge(p)}</dd></div>'
            f'<div><dt>Official Source</dt><dd><a href="{e(p["source_url"])}" target="_blank" rel="noopener noreferrer">Open Docs</a></dd></div>'
            f'</dl>'
            f'</details>'
        )

    policy_history_rows = "".join(
        f'<tr><td class="num-col">{e(h["date"])}</td><td translate="no"><strong>{e(h["provider_name"])}</strong></td>'
        f'<td><span class="badge {"badge-warn" if h.get("change_type") == "policy_correction" else "badge-free"}">{e(h.get("change_type", "update").replace("_", " ").title())}</span></td>'
        f'<td class="small">{e(h["summary"])} (<a href="{e(h["source_url"])}" target="_blank" rel="noopener noreferrer">Source</a>)</td></tr>'
        for h in policy_history
    )

    free_hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Free LLM APIs</div>
    <h1>Free LLM APIs With Tool Calling &amp; Real Rate Limits</h1>
    <p class="lead">Compare permanently free tiers vs. time-limited trials, credit-card rules, burst RPM/TPM ceilings, and Cloudflare Workers AI Neurons across providers.</p>

    <section class="calc-panel" style="margin:18px 0 24px">
      <h2>Free LLM API Quota &amp; Burst Calculator</h2>
      <div class="calc-grid">
        <div>
          <label class="muted small" for="fq-jobs">Daily Jobs / Runs</label><br />
          <input id="fq-jobs" type="number" min="0" step="5" value="30" autocomplete="off" style="width:100%;margin-top:4px" />
        </div>
        <div>
          <label class="muted small" for="fq-calls">AI Calls / Job</label><br />
          <input id="fq-calls" type="number" min="0" step="1" value="3" autocomplete="off" style="width:100%;margin-top:4px" />
        </div>
        <div>
          <label class="muted small" for="fq-peak-rpm">Peak Calls / Min (RPM)</label><br />
          <input id="fq-peak-rpm" type="number" min="0" step="1" value="5" autocomplete="off" style="width:100%;margin-top:4px" />
        </div>
        <div>
          <label class="muted small" for="fq-retry">Retry Multiplier</label><br />
          <select id="fq-retry" style="width:100%;margin-top:4px">
            <option value="1.0" selected>1.0x (Clean single-shot)</option>
            <option value="1.2">1.2x (Normal retries)</option>
            <option value="1.5">1.5x (Multi-step agent)</option>
          </select>
        </div>
        <div>
          <label class="muted small" for="fq-in-tok">Input Tokens / Call</label><br />
          <input id="fq-in-tok" type="number" min="0" step="200" value="1000" autocomplete="off" style="width:100%;margin-top:4px" />
        </div>
        <div>
          <label class="muted small" for="fq-out-tok">Output Tokens / Call</label><br />
          <input id="fq-out-tok" type="number" min="0" step="100" value="500" autocomplete="off" style="width:100%;margin-top:4px" />
        </div>
        <div class="span-2">
          <label class="muted small" for="fq-or-tier">OpenRouter Account Status</label><br />
          <select id="fq-or-tier" style="width:100%;margin-top:4px">
            <option value="free_50" selected>Standard Free (&lt;$9/$10 credits &mdash; 50 RPD, 20 RPM)</option>
            <option value="paid_1000">Credit-Unlocked (&ge;$10 purchase / $9 net buffer &mdash; 1,000 RPD, 20 RPM)</option>
          </select>
        </div>
      </div>
      <div class="calc-result" id="fq-out" aria-live="polite"></div>
      <p class="muted small" style="margin-top:10px">Evaluates both <strong>daily volume (RPD / TPD / Neurons)</strong> and <strong>peak minute throughput (RPM / TPM)</strong>. Need paid fallback pricing? Open the <a href="/tools/openrouter-cost-calculator/">OpenRouter Cost Calculator</a> or <a href="/tools/n8n-ai-workflow-cost-calculator/">n8n AI Workflow Calculator</a>.</p>
    </section>

    <section>
      <h2>Cross-Provider Free LLM API Comparison (Permanently Free vs. Free Trial)</h2>
      <p class="muted small">Loaded from <a href="/data/free-provider-policies.json"><code translate="no">/data/free-provider-policies.json</code></a> and monitored for official documentation drift. Policies are separated into <strong>Permanently Free</strong>, <strong>Credit-Unlocked</strong>, and <strong>Time-Limited Free Trial</strong>. Runtime availability is <strong>Not Live-Probed</strong>.</p>
      <div class="table-wrap provider-table-desktop">
        <table>
          <thead><tr><th>Provider &amp; Tier Type</th><th>Credit Card Required?</th><th>Verified Quota &amp; Reset</th><th>API Compatibility</th><th>Data &amp; Training Policy</th><th>Verification</th></tr></thead>
          <tbody>{"".join(provider_rows)}</tbody>
        </table>
      </div>
      <div class="provider-cards-mobile">{"".join(provider_mobile_cards)}</div>
    </section>

    <section>
      <h2>Free LLM API Policy Changelog</h2>
      <p class="muted small">Append-only record of verified free-tier rule changes and policy corrections.</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th class="num-col">Date</th><th>Provider</th><th>Type</th><th>Verified Policy Change</th></tr></thead>
          <tbody>{policy_history_rows}</tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>Live OpenRouter <code translate="no">:free</code> Text Endpoints ({len(free_models)} Active)</h2>
      <p class="muted small">Updated daily from the OpenRouter Public API. Dynamic routers (<code translate="no">-1</code>) and audio/media endpoints are separated below.</p>
      <div class="controls">
        <label style="cursor:pointer"><input id="f-tools" type="checkbox" /> Tool Calling Supported Only</label>
        <label style="cursor:pointer"><input id="f-json" type="checkbox" /> Structured JSON Supported Only</label>
      </div>
      <div class="table-wrap">
        <table><thead><tr><th>Verified Free Endpoint</th><th>Provider</th><th class="num-col">Context</th><th>Capabilities (Source Verified)</th><th>Tier</th></tr></thead>
        <tbody id="free-tbody">{free_rows}</tbody></table>
      </div>
    </section>

    <h2>Routers Separated for Transparency</h2>
    <div class="table-wrap"><table><thead><tr><th>Router</th><th>Pricing Type</th><th class="num-col">Context</th><th>Billing &amp; Limits</th></tr></thead><tbody>{router_rows}</tbody></table></div>

    <script src="/assets/calc-engine.js"></script>
    <script>
      (function() {{
        const tEl = document.getElementById('f-tools');
        const jEl = document.getElementById('f-json');
        const rows = Array.from(document.querySelectorAll('#free-tbody tr'));
        function filterTable() {{
          rows.forEach(tr => {{
            const ok = (!tEl.checked || tr.dataset.tools === '1') && (!jEl.checked || tr.dataset.json === '1');
            tr.style.display = ok ? '' : 'none';
          }});
        }}
        tEl.addEventListener('change', filterTable); jEl.addEventListener('change', filterTable);

        const POLICY_DB = {json.dumps(policy_db, ensure_ascii=False)};
        const fqJobs = document.getElementById('fq-jobs');
        const fqCalls = document.getElementById('fq-calls');
        const fqPeak = document.getElementById('fq-peak-rpm');
        const fqRetry = document.getElementById('fq-retry');
        const fqIn = document.getElementById('fq-in-tok');
        const fqOutTok = document.getElementById('fq-out-tok');
        const fqTier = document.getElementById('fq-or-tier');
        const fqOut = document.getElementById('fq-out');
        function calcQuota() {{
          const r = window.SysVCalc.calculateFreeQuota(
            parseFloat(fqJobs.value || '0') || 0,
            parseFloat(fqCalls.value || '0') || 0,
            parseFloat(fqPeak.value || '0') || 0,
            parseFloat(fqRetry.value || '1') || 1,
            parseFloat(fqIn.value || '0') || 0,
            parseFloat(fqOutTok.value || '0') || 0,
            fqTier.value || 'free_50',
            POLICY_DB
          );
          function _advisoryTag(st) {{
            if (st === 'VERIFIED_DOC_DRIFT') return ' <span class="badge">Docs updated (review pending)</span>';
            if (st === 'VERIFIED_CACHED') return ' <span class="badge">Using fresh cache</span>';
            return '';
          }}

          let orHtml;
          if (!r.openRouterUsable) {{
            orHtml = '<span class="badge badge-warn">Policy ' + r.openRouterStatus + '</span> Paused — verify live OpenRouter limits in docs.';
          }} else {{
            const orRpdBadge = r.openRouterRpdCovered
              ? '<span class="badge badge-free">Daily RPD Covered (' + r.requiredCalls.toLocaleString() + '/' + r.openRouterLimit.toLocaleString() + ')</span>'
              : '<span class="badge badge-warn">Short by ' + r.openRouterDeficit.toLocaleString() + ' calls/day (' + r.requiredCalls.toLocaleString() + '/' + r.openRouterLimit.toLocaleString() + ')</span>';
            const orRpmBadge = r.openRouterRpmCovered
              ? '<span class="badge badge-free">Burst OK (' + r.effectivePeakRpm + '/' + r.openRouterRpmCap + ' RPM)</span>'
              : '<span class="badge badge-danger">Exceeds ' + r.openRouterRpmCap + ' RPM (' + r.effectivePeakRpm + ' RPM)</span>';
            orHtml = orRpdBadge + ' ' + orRpmBadge + _advisoryTag(r.openRouterStatus);
          }}

          let groqStatus;
          if (!r.groqUsable) {{
            groqStatus = '<span class="badge badge-warn">Policy ' + r.groqStatus + '</span> Paused — verify live Groq limits in console.';
          }} else {{
            const gl = r.groqLimits || {{}};
            const groqIssues = [];
            if (!r.groqRpdCovered) groqIssues.push('RPD > ' + Number(gl.rpd).toLocaleString());
            if (!r.groqTpdCovered) groqIssues.push('TPD > ' + Number(gl.tpd).toLocaleString());
            if (!r.groqRpmCovered) groqIssues.push('RPM > ' + gl.rpm);
            if (!r.groqTpmCovered) groqIssues.push('Peak TPM > ' + Number(gl.tpm).toLocaleString());
            const mList = (r.groqModels || []).join(', ');
            groqStatus = (r.groqFits
              ? '<span class="badge badge-free">Fits</span> ' + mList + ' (' + gl.rpm + ' RPM, ' + Number(gl.rpd).toLocaleString() + ' RPD, ' + Number(gl.tpm).toLocaleString() + ' TPM, ' + Number(gl.tpd).toLocaleString() + ' TPD)'
              : '<span class="badge badge-warn">Blocked: ' + groqIssues.join(', ') + '</span> on ' + mList) + _advisoryTag(r.groqStatus);
          }}

          let cfStatus;
          if (!r.cfUsable) {{
            cfStatus = '<span class="badge badge-warn">Policy ' + r.cfStatus + '</span> Paused — verify live Workers AI Neuron pricing.';
          }} else {{
            cfStatus = (r.cfCovered
              ? '<span class="badge badge-free">Fits ' + Number(r.cfDailyAllowance).toLocaleString() + ' Neurons/day</span> ' + r.cfNeuronsPerCall.toFixed(2) + ' Neurons/call &rarr; max ~<strong>' + r.cfMaxFreeCalls.toLocaleString() + ' calls/day</strong> (uses ' + Math.round(r.cfDailyNeurons).toLocaleString() + ' / ' + Number(r.cfDailyAllowance).toLocaleString() + ')'
              : '<span class="badge badge-warn">Exceeds ' + Number(r.cfDailyAllowance).toLocaleString() + ' Neurons/day</span> ' + Math.round(r.cfDailyNeurons).toLocaleString() + ' Neurons needed (max ~' + r.cfMaxFreeCalls.toLocaleString() + ' calls/day)') + _advisoryTag(r.cfStatus);
          }}

          let cerHtml;
          if (!r.cerebrasUsable) {{
            cerHtml = '<span class="badge badge-warn">Policy ' + r.cerebrasStatus + '</span> Paused — verify live Cerebras trial terms in docs.';
          }} else {{
            cerHtml = '<span class="badge badge-warn">30-Day Trial Only</span> $' + r.cerebrasTrialUsd + ' credit expires in ' + r.cerebrasTrialDays + ' days; verified payment method required (5 RPM; no permanently free tier).' + _advisoryTag(r.cerebrasStatus);
          }}

          fqOut.innerHTML =
            '<div><strong>Daily Demand:</strong> ' + r.requiredCalls.toLocaleString() + ' calls/day (' + r.requiredTokens.toLocaleString() + ' tokens) &middot; <strong>Peak Burst:</strong> ' + r.effectivePeakRpm + ' RPM (' + r.peakTpm.toLocaleString() + ' TPM)</div>' +
            '<div class="quota-grid">' +
              '<div class="quota-item"><strong>OpenRouter (<code translate="no">:free</code>)</strong><div style="margin-top:6px">' + orHtml + '</div></div>' +
              '<div class="quota-item"><strong>Cloudflare Workers AI</strong> <span class="muted small">(<code translate="no">' + (r.cfModelName || 'llama-3.2-1b') + '</code>)</span><div style="margin-top:6px">' + cfStatus + '</div><div class="muted small" style="margin-top:4px">Larger frontier models require Workers Paid ($5/mo).</div></div>' +
              '<div class="quota-item"><strong>Groq Free (Model-Specific)</strong><div style="margin-top:6px">' + groqStatus + '</div></div>' +
              '<div class="quota-item"><strong>Cerebras Inference</strong><div style="margin-top:6px">' + cerHtml + '</div></div>' +
            '</div>';
        }}
        [fqJobs, fqCalls, fqPeak, fqRetry, fqIn, fqOutTok, fqTier].forEach(el => {{ el.addEventListener('input', calcQuota); el.addEventListener('change', calcQuota); }});
        calcQuota();
      }})();
    </script>
    """
    policy_sem_sig = [
        (p.get("id"), p.get("tier_classification"), p.get("credit_card_required"), p.get("quota_summary"), p.get("verification_status"), p.get("last_verified_date"))
        for p in providers_list
    ]
    register_page(
        "free-llm-apis/index.html",
        "/free-llm-apis/",
        "free_hub",
        "index",
        sem_hash(["free_hub_v2.2.3", TEMPLATE_VERSION, policy_sem_sig, len(policy_history), [model_semantic_fingerprint(m) for m in free_models + routers]]),
        render_layout(
            cfg,
            title="Free LLM APIs With Tool Calling & Real Rate Limits (2026) | SysV",
            description=f"Compare permanently free LLM APIs vs. free trials (OpenRouter, Gemini, Groq, Cloudflare Workers AI Neurons, Cerebras), calculate RPM/RPD/TPM limits, and filter {len(free_models)} verified $0 endpoints.",
            canonical_path="/free-llm-apis/",
            body_html=free_hub_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Free LLM APIs", "/free-llm-apis/")],
        ),
    )

    # --- 4. /models/<slug>/ Detail Pages (Fixed Breadcrumbs + True Cross-Day Semantic Hash!) ---
    for m in models:
        ptype = m["pricing_type"]
        status = m.get("status", "active")
        caps = m.get("capabilities") or {}
        enr = m.get("enrichment") or {}
        pol = seo_gate.decide_model_index_policy(m)

        # Fixed Breadcrumb: Paid -> /models/, Free -> /free-llm-apis/
        if ptype in ("limited_free", "free"):
            parent_lbl, parent_path = "Free LLM APIs", "/free-llm-apis/"
        else:
            parent_lbl, parent_path = "Models", "/models/"

        best_for = "".join(f"<li>{e(x)}</li>" for x in enr.get("best_for", []))
        watch_outs = "".join(f"<li>{e(x)}</li>" for x in enr.get("watch_outs", []))
        same_cat = [other for other in active_models if other["id"] != m["id"] and other["task_category"] == m["task_category"]]
        related = (same_cat if len(same_cat) >= 3 else [other for other in active_models if other["id"] != m["id"]])[:4]
        rel_html = "".join(
            f'<li><a href="/models/{e(r["slug"])}/">{e(r["name"])}</a> ({e(fmt_price(r["prompt_price_per_m"], r["pricing_type"]))} / {e(fmt_ctx(r["context_length"]))} ctx)</li>'
            for r in related
        )

        if isinstance(m.get("prompt_price_per_m"), (int, float)) and isinstance(m.get("completion_price_per_m"), (int, float)):
            est_sample = f"${(m['prompt_price_per_m'] * 8 + m['completion_price_per_m'] * 2):.2f}"
        else:
            est_sample = "Depends on routed model" if ptype == "variable" else "Not Applicable"

        status_banner = ""
        if status == "delisted":
            status_banner = f'<div class="callout-danger"><strong>Archived / Delisted Endpoint:</strong> Absent from OpenRouter for &ge;3 consecutive healthy runs (delisted {e(m.get("delisted_at"))}). Last-known pricing preserved below for reference.</div>'
        elif status == "temporarily_missing":
            status_banner = '<div class="callout-warn"><strong>Temporary Availability Notice:</strong> Absent from the latest upstream fetch; page temporarily set to <code>noindex</code> while monitoring.</div>'
        elif ptype == "variable":
            status_banner = '<div class="callout-warn"><strong>Dynamic Routing Notice:</strong> Variable billing (OpenRouter raw price <code>-1</code>). Downstream paid model rates apply per request.</div>'
        elif ptype == "batch":
            status_banner = '<div class="callout-warn"><strong>Async Batch Endpoint:</strong> Discounted asynchronous batch billing only; not suitable for live interactive agents.</div>'

        extra_meta = []
        if m.get("cache_read_price_per_m") is not None:
            extra_meta.append(f"Cache Read: <strong>${m['cache_read_price_per_m']}/1M</strong>")
        if m.get("has_tiered_overrides"):
            extra_meta.append("<strong>Tiered Long-Context Surcharge Applies</strong>")
        extra_line = (" &middot; " + " &middot; ".join(extra_meta)) if extra_meta else ""

        m_body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="{parent_path}">{parent_lbl}</a> / {e(m["name"])}</div>
        {status_banner}
        <div class="hero">
          {pricing_badge(m)} <span class="badge">Source Verified</span>
          {'<span class="badge badge-danger">Delisted</span>' if status == 'delisted' else ''}
          <h1 style="margin-top:10px">{e(m["name"])} API Pricing &amp; Capabilities</h1>
          <p class="muted upstream-desc">{e(m["description"])}</p>
          <p class="muted small">Endpoint ID: <code>{e(m["id"])}</code> &middot; Source: <a href="https://openrouter.ai/api/v1/models" rel="noopener noreferrer" target="_blank">OpenRouter Public API</a>{extra_line}</p>
          <div class="stats">
            <div class="stat"><div class="num">{e(fmt_price(m["prompt_price_per_m"], ptype))}</div><div class="lbl">Input / 1M Tokens</div></div>
            <div class="stat"><div class="num">{e(fmt_price(m["completion_price_per_m"], ptype))}</div><div class="lbl">Output / 1M Tokens</div></div>
            <div class="stat"><div class="num">{m["context_length"]:,}</div><div class="lbl">Max Context Tokens</div></div>
            <div class="stat"><div class="num">{e(est_sample)}</div><div class="lbl">Est. 8M In + 2M Out</div></div>
          </div>
          <p class="muted small" style="margin-top:14px">
            <strong>Capabilities (Source Verified):</strong>
            Real-Time: <strong>{"Yes" if m.get("is_realtime") else "No"}</strong> &middot;
            Tool Calling: <strong>{"Yes" if caps.get("tools") else "No"}</strong> &middot;
            Structured JSON: <strong>{"Yes" if caps.get("structured_outputs") else "No"}</strong> &middot;
            Reasoning: <strong>{"Yes" if caps.get("reasoning") else "No"}</strong>
          </p>
        </div>
        <div class="card">
          <h2>Pricing Scope &amp; Engineering Notes</h2>
          <p>{e(enr.get("summary", m["description"]))}</p>
          <p class="muted small"><strong>Rate Limit / Billing Policy:</strong> {e(m.get("rate_limit_note", ""))}</p>
          <div class="grid">
            <div><h3>Best Suited For</h3><ul>{best_for}</ul></div>
            <div><h3>Watch-Outs</h3><ul>{watch_outs}</ul></div>
          </div>
        </div>
        {render_ad_slot(cfg, "adsense_slot_in_article")}
        <div class="card">
          <h2>Active Same-Cohort Alternatives</h2>
          <ul>{rel_html}</ul>
        </div>
        """
        rel_url = f"/models/{m['slug']}/"
        register_page(
            f"models/{m['slug']}/index.html",
            rel_url,
            "model_detail",
            pol,
            model_semantic_fingerprint(m),
            render_layout(
                cfg,
                title=f"{m['name']} API Pricing ({m['pricing_label']}) & Limits | SysV",
                description=f"{m['name']} ({m['pricing_label']}): {fmt_price(m['prompt_price_per_m'], ptype)} input, {fmt_ctx(m['context_length'])} context, Tools={'Yes' if caps.get('tools') else 'No'}.",
                canonical_path=rel_url,
                body_html=m_body,
                git_commit=git_commit,
                index_policy=pol,
                breadcrumbs=[("Home", "/"), (parent_lbl, parent_path), (m["name"], rel_url)],
            ),
            status=status,
        )

    # --- 5. /compare/ & /compare/<canonical-slug>/ (Permanent Registry + Not Applicable!) ---
    hub_cards = []
    for a, b, slug, cohort_label, comp_status in pairs:
        rel_url = f"/compare/{slug}/"
        pol = seo_gate.decide_comparison_index_policy(a, b, comp_status)
        a_caps, b_caps = a.get("capabilities") or {}, b.get("capabilities") or {}
        if comp_status == "active":
            hub_cards.append(
                f'<div class="card"><span class="badge">{e(cohort_label)}</span>'
                f'<div style="margin-top:8px"><a href="{e(rel_url)}"><strong>{e(a["name"])} vs {e(b["name"])}</strong></a></div>'
                f'<p class="muted small" style="margin:6px 0 0">Input: {e(fmt_price(a["prompt_price_per_m"], a["pricing_type"]))} vs {e(fmt_price(b["prompt_price_per_m"], b["pricing_type"]))} &middot; Context: {fmt_ctx(a["context_length"])} vs {fmt_ctx(b["context_length"])}</p></div>'
            )

        archive_banner = (
            '<div class="callout-danger"><strong>Archived Comparison:</strong> One or both endpoints are no longer active upstream. Preserved permanently for historical reference.</div>'
            if comp_status != "active"
            else ""
        )
        # Deterministic structured decision summary (0 LLM calls; strictly derived from verified model metadata)
        summary_bullets: list[str] = []
        a_in, a_out = a.get("prompt_price_per_m"), a.get("completion_price_per_m")
        b_in, b_out = b.get("prompt_price_per_m"), b.get("completion_price_per_m")
        if a["pricing_type"] in ("limited_free", "free") and b["pricing_type"] in ("limited_free", "free"):
            summary_bullets.append(
                f"Both {a['name']} and {b['name']} are listed at $0/1M tokens under rate-limited free tiers "
                f"(OpenRouter :free shares a global 20 RPM and 50 RPD cap, or 1,000 RPD after a >=$10 lifetime credit purchase)."
            )
        elif all(isinstance(v, (int, float)) for v in (a_in, a_out, b_in, b_out)):
            a_ref = a_in * 8.0 + a_out * 2.0
            b_ref = b_in * 8.0 + b_out * 2.0
            if abs(a_ref - b_ref) < 0.01:
                summary_bullets.append(
                    f"For an 8M input + 2M output monthly workload, both models cost roughly ${a_ref:.2f}/mo in base upstream tokens "
                    f"(before OpenRouter 5.5% card or 5.0% crypto top-up fees unless using BYOK)."
                )
            else:
                cheaper, pricier, c_cost, p_cost = (a, b, a_ref, b_ref) if a_ref < b_ref else (b, a, b_ref, a_ref)
                diff = p_cost - c_cost
                pct = (diff / p_cost * 100.0) if p_cost > 0 else 0.0
                summary_bullets.append(
                    f"On a reference 8M input + 2M output monthly workload, {cheaper['name']} (${c_cost:.2f}/mo) is "
                    f"${diff:.2f}/mo ({pct:.0f}%) less expensive in base token spend than {pricier['name']} (${p_cost:.2f}/mo)."
                )
        ctx_a, ctx_b = int(a.get("context_length") or 0), int(b.get("context_length") or 0)
        if ctx_a == ctx_b:
            summary_bullets.append(
                f"Context capacity is identical at {ctx_a:,} tokens ({fmt_ctx(ctx_a)}), so long-document retrieval eligibility is tied."
            )
        else:
            larger, smaller, l_ctx, s_ctx = (a, b, ctx_a, ctx_b) if ctx_a > ctx_b else (b, a, ctx_b, ctx_a)
            summary_bullets.append(
                f"{larger['name']} provides a larger context window ({l_ctx:,} vs. {s_ctx:,} tokens, +{l_ctx - s_ctx:,} tokens) "
                f"for long-context RAG or multi-file coding sessions."
            )
        cap_diffs: list[str] = []
        for cap_key, cap_lbl in [("tools", "tool calling"), ("structured_outputs", "structured JSON outputs"), ("reasoning", "explicit reasoning")]:
            va, vb = bool(a_caps.get(cap_key)), bool(b_caps.get(cap_key))
            if va and not vb:
                cap_diffs.append(f"{a['name']} supports {cap_lbl} while {b['name']} does not")
            elif vb and not va:
                cap_diffs.append(f"{b['name']} supports {cap_lbl} while {a['name']} does not")
        if cap_diffs:
            summary_bullets.append("Capability differences (Source Verified): " + "; ".join(cap_diffs) + ".")
        else:
            summary_bullets.append(
                f"Both endpoints expose identical Source Verified API flags: "
                f"Tools={'Yes' if a_caps.get('tools') else 'No'}, "
                f"Structured JSON={'Yes' if a_caps.get('structured_outputs') else 'No'}, "
                f"Reasoning={'Yes' if a_caps.get('reasoning') else 'No'}."
            )
        summary_html = "".join(f"<li>{e(item)}</li>" for item in summary_bullets)

        c_body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="/compare/">Compare</a> / {e(a["name"])} vs {e(b["name"])}</div>
        {archive_banner}
        <span class="badge">{e(cohort_label)}</span>
        <h1 style="margin-top:8px">{e(a["name"])} vs {e(b["name"])}: Cost, Limits &amp; Capabilities</h1>
        <p class="muted small">Source Verified via OpenRouter Public API (USD / 1M tokens). Workloads where a model lacks required capabilities are explicitly marked <strong>Not Applicable</strong>.</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Metric / Task Workload</th><th>{e(a["name"])}</th><th>{e(b["name"])}</th></tr></thead>
            <tbody>
              <tr><td>Provider &amp; Pricing Tier</td><td>{e(a["provider"])} &middot; {pricing_badge(a)}</td><td>{e(b["provider"])} &middot; {pricing_badge(b)}</td></tr>
              <tr><td>Context Window</td><td>{a["context_length"]:,} tokens</td><td>{b["context_length"]:,} tokens</td></tr>
              <tr><td>Tool Calling / Structured JSON</td><td>{"Yes" if a_caps.get("tools") else "No"} / {"Yes" if a_caps.get("structured_outputs") else "No"}</td><td>{"Yes" if b_caps.get("tools") else "No"} / {"Yes" if b_caps.get("structured_outputs") else "No"}</td></tr>
              <tr><td>Input / Output per 1M</td><td>{e(fmt_price(a["prompt_price_per_m"], a["pricing_type"]))} / {e(fmt_price(a["completion_price_per_m"], a["pricing_type"]))}</td><td>{e(fmt_price(b["prompt_price_per_m"], b["pricing_type"]))} / {e(fmt_price(b["completion_price_per_m"], b["pricing_type"]))}</td></tr>
              <tr><td>Est. Bulk Translation (2.5M in / 2.5M out)</td><td>{render_task_cell(a, "translation", 2.5, 2.5)}</td><td>{render_task_cell(b, "translation", 2.5, 2.5)}</td></tr>
              <tr><td>Est. Structured Data Pipeline (8M in / 2M out)</td><td>{render_task_cell(a, "autosite", 8.0, 2.0)}</td><td>{render_task_cell(b, "autosite", 8.0, 2.0)}</td></tr>
              <tr><td>Est. Coding Agent Loop (18M in / 3.5M out)</td><td>{render_task_cell(a, "coding", 18.0, 3.5)}</td><td>{render_task_cell(b, "coding", 18.0, 3.5)}</td></tr>
              <tr><td>Full Endpoint Details</td><td><a href="/models/{e(a['slug'])}/">View {e(a["name"])} &rarr;</a></td><td><a href="/models/{e(b['slug'])}/">View {e(b["name"])} &rarr;</a></td></tr>
            </tbody>
          </table>
        </div>
        <div class="card" style="margin-top:18px">
          <h2>Key Engineering Takeaways</h2>
          <ul>{summary_html}</ul>
          <p class="muted small">Need custom token volumes, retry multipliers, or OpenRouter payment fee modeling? Run your exact workload in the <a href="/tools/openrouter-cost-calculator/">OpenRouter Cost Calculator</a> or the <a href="/">main token calculator</a>.</p>
        </div>
        {render_ad_slot(cfg, "adsense_slot_in_article")}
        """
        register_page(
            f"compare/{slug}/index.html",
            rel_url,
            "comparison",
            pol,
            sem_hash(["comp-v2.2.2", model_semantic_fingerprint(a), model_semantic_fingerprint(b), cohort_label, comp_status]),
            render_layout(
                cfg,
                title=f"{a['name']} vs {b['name']}: Cost, Limits & Capabilities | SysV",
                description=f"Side-by-side Source Verified comparison of {a['name']} vs {b['name']}: token pricing, context windows, tool calling, and task eligibility.",
                canonical_path=rel_url,
                body_html=c_body,
                git_commit=git_commit,
                index_policy=pol,
                breadcrumbs=[("Home", "/"), ("Compare", "/compare/"), (f"{a['name']} vs {b['name']}", rel_url)],
            ),
            status=comp_status,
        )

    compare_hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Compare</div>
    <h1>LLM API Comparisons: Cost &amp; Capabilities Side-by-Side</h1>
    <p class="muted">Permanent, order-independent comparison URLs pairing cross-provider real-time text models within the same capability tier ({len(active_pairs)} active).</p>
    <div class="grid">{"".join(hub_cards)}</div>
    """
    register_page(
        "compare/index.html",
        "/compare/",
        "compare_hub",
        "index",
        sem_hash(["compare_hub", TEMPLATE_VERSION, [p[2] for p in active_pairs]]),
        render_layout(
            cfg,
            title="LLM API Comparisons: Cost & Capabilities Side-by-Side | SysV",
            description="Compare real-time LLM APIs by verified token pricing, tool calling, structured JSON support, and workload eligibility.",
            canonical_path="/compare/",
            body_html=compare_hub_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Compare", "/compare/")],
        ),
    )

    # --- 6. /price-changes/ ---
    events = changelog.get("events", [])
    published_slugs = {m["slug"] for m in models}
    if events:
        pc_rows = []
        for ev in events[:100]:
            s = ev.get("slug", "")
            nc = f'<a href="/models/{e(s)}/"><strong>{e(ev["name"])}</strong></a>' if s in published_slugs else f'<strong>{e(ev["name"])}</strong>'
            pc_rows.append(
                f'<tr><td>{e(ev["date"])}</td><td><span class="badge {"badge-free" if ev["type"] in ("new_free", "price_drop") else "badge-warn"}">{e(ev["type"].replace("_", " ").title())}</span></td>'
                f'<td>{nc}<br><span class="muted small">{e(ev["model_id"])}</span></td><td class="muted small">{e(ev["detail"])}</td></tr>'
            )
        pc_table = '<div class="table-wrap"><table><thead><tr><th>Date</th><th>Change</th><th>Model</th><th>Details</th></tr></thead><tbody>' + "".join(pc_rows) + "</tbody></table></div>"
    else:
        pc_table = f'<div class="card"><h3>Daily Archive Initialized</h3><p class="muted">Tracking {changelog.get("latest_total_endpoints", 0)} endpoints since {e(changelog.get("first_snapshot_date", today))}. Price drops, hikes, and free-tier additions/removals appear automatically as snapshots accumulate.</p></div>'

    pc_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Price History</div>
    <h1>LLM API Price History &amp; Free-Tier Changes</h1>
    <p class="muted">Append-only daily price archive covering {changelog.get("latest_total_endpoints", 0)} OpenRouter endpoints (<a href="/data/price-history.csv">download CSV</a>).</p>
    {pc_table}
    """
    register_page(
        "price-changes/index.html",
        "/price-changes/",
        "price_history",
        "index",
        sem_hash(["price_changes", TEMPLATE_VERSION, events[:100], changelog.get("snapshot_days", 1)]),
        render_layout(
            cfg,
            title="LLM API Price History & Free-Tier Changes | SysV",
            description="Daily append-only archive of LLM API price drops, hikes, new free endpoints, and tier retirements with CSV export.",
            canonical_path="/price-changes/",
            body_html=pc_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Price History", "/price-changes/")],
        ),
    )

    # --- 7. /self-hosting/ (and /repos/<slug>/ preserved permanently!) ---
    repo_cards = []
    categories = sorted({r.get("category", "Self-Hosted Infra") for r in repos})
    for r in repos:
        rel_url = f"/repos/{r['slug']}/"
        pol = seo_gate.decide_repo_index_policy(r)
        enr = r.get("enrichment") or {}
        cat = r.get("category", "Self-Hosted Infra")
        lic = r.get("license", "Unknown")
        lic_tier = r.get("license_tier", "unknown")
        has_docker = bool(r.get("has_docker"))
        docker_ev = r.get("docker_evidence", "Not Verified")
        docker_status = r.get("docker_status", "Not Verified")
        docker_badge = '<span class="badge badge-free">Docker: Repo Verified</span>' if (has_docker and docker_ev == "Repository Verified") else ""

        repo_cards.append(
            f'<div class="card repo-card" data-cat="{e(cat)}" data-docker="{"1" if has_docker else "0"}" data-perm="{"1" if lic_tier == "permissive" else "0"}">'
            f'<span class="badge">{e(cat)}</span> <span class="badge {"badge-free" if lic_tier == "permissive" else "badge-warn"}">{e(lic)}</span> {docker_badge}'
            f'<div style="margin-top:8px"><a href="{e(rel_url)}"><strong>{e(r["full_name"])}</strong></a></div>'
            f'<p class="muted small" style="margin:6px 0">{e(r["description"])}</p>'
            f'<div class="muted small">&#9733; {r["stars"]:,} &middot; {e(docker_status)} &middot; {e(r.get("maintenance_status", "Active"))}</div></div>'
        )

        best_for = "".join(f"<li>{e(x)}</li>" for x in enr.get("best_for", []))
        watch_outs = "".join(f"<li>{e(x)}</li>" for x in enr.get("watch_outs", []))
        r_body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="/self-hosting/">Self-Hosting</a> / {e(r["full_name"])}</div>
        <div class="hero">
          <span class="badge">{e(cat)}</span>
          <span class="badge {'badge-free' if lic_tier == 'permissive' else 'badge-warn'}">License: {e(lic)}</span>
          {docker_badge}
          <h1 style="margin-top:10px">{e(r["full_name"])}: Self-Hosting, Docker &amp; License Profile</h1>
          <p class="muted upstream-desc">{e(r["description"])}</p>
          <p><a class="btn" href="{e(r["html_url"])}" target="_blank" rel="noopener noreferrer">Open on GitHub &rarr;</a></p>
          <div class="stats">
            <div class="stat"><div class="num">{r["stars"]:,}</div><div class="lbl">GitHub Stars (Source Verified)</div></div>
            <div class="stat"><div class="num">{e(docker_status)}</div><div class="lbl">Docker ({e(docker_ev)})</div></div>
            <div class="stat"><div class="num">{e(r.get("maintenance_status", "Active"))}</div><div class="lbl">Maintenance Health</div></div>
            <div class="stat"><div class="num">Check Docs</div><div class="lbl">RAM / GPU (Not Verified)</div></div>
          </div>
          <p class="muted small" style="margin-top:12px"><strong>Evidence &amp; License Notice:</strong> Metadata is <em>Source Verified</em> via GitHub API; Docker presence is checked against actual root files (<em>{e(docker_ev)}</em>); RAM/GPU requirements are <em>Not Verified</em>—consult the repository README before sizing servers. {e(r.get("license_note", ""))}</p>
        </div>
        <div class="card">
          <h2>Self-Hosting Notes</h2>
          <p>{e(enr.get("summary", r["description"]))}</p>
          <div class="grid">
            <div><h3>Use Cases</h3><ul>{best_for}</ul></div>
            <div><h3>Watch-Outs</h3><ul>{watch_outs}</ul></div>
          </div>
        </div>
        """
        register_page(
            f"repos/{r['slug']}/index.html",
            rel_url,
            "repo_detail",
            pol,
            repo_semantic_fingerprint(r),
            render_layout(
                cfg,
                title=f"{r['full_name']} Self-Hosting, Docker & License Profile | SysV",
                description=f"{r['full_name']} ({lic}, {docker_status}, {r.get('maintenance_status')}): {r['description']}",
                canonical_path=rel_url,
                body_html=r_body,
                git_commit=git_commit,
                index_policy=pol,
                breadcrumbs=[("Home", "/"), ("Self-Hosting", "/self-hosting/"), (r["full_name"], rel_url)],
            ),
        )

    cat_opts = "".join(f'<option value="{e(c)}">{e(c)}</option>' for c in categories)
    sh_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Self-Hosting</div>
    <h1>Self-Hosted AI &amp; Automation Infrastructure Directory</h1>
    <p class="muted">Curated open-source AI gateways, local LLM runtimes, and workflow automation repositories ({len(repos)} projects). Docker support is verified against actual repository root files (<code>Dockerfile</code> / <code>compose.yml</code>)—never guessed from descriptions.</p>
    <div class="controls">
      <select id="repo-cat"><option value="all">All Categories</option>{cat_opts}</select>
      <label style="cursor:pointer"><input id="repo-docker" type="checkbox" /> Repository-Verified Docker Only</label>
      <label style="cursor:pointer"><input id="repo-perm" type="checkbox" /> Permissive Licenses Only (MIT/Apache/BSD)</label>
    </div>
    <div class="grid">{"".join(repo_cards)}</div>
    {render_affiliates(cfg)}
    <script>
      (function() {{
        const catEl = document.getElementById('repo-cat');
        const dEl = document.getElementById('repo-docker');
        const pEl = document.getElementById('repo-perm');
        const items = Array.from(document.querySelectorAll('.repo-card'));
        function filter() {{
          items.forEach(el => {{
            const ok = (catEl.value === 'all' || el.dataset.cat === catEl.value) && (!dEl.checked || el.dataset.docker === '1') && (!pEl.checked || el.dataset.perm === '1');
            el.style.display = ok ? '' : 'none';
          }});
        }}
        [catEl, dEl, pEl].forEach(el => el.addEventListener('change', filter));
      }})();
    </script>
    """
    register_page(
        "self-hosting/index.html",
        "/self-hosting/",
        "self_hosting_hub",
        "index",
        sem_hash(["self_hosting", TEMPLATE_VERSION, repos_sem_sig]),
        render_layout(
            cfg,
            title="Self-Hosted AI & Automation Infrastructure Directory | SysV",
            description="Repository-verified Docker and license directory of self-hosted AI gateways, Ollama/local runtimes, and n8n workflow tools.",
            canonical_path="/self-hosting/",
            body_html=sh_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Self-Hosting", "/self-hosting/")],
        ),
    )
    # Keep legacy /repos/ alive with canonical pointing to /self-hosting/ (noindex duplicate protection & zero ads!)
    repos_alias_html = render_layout(
        cfg,
        title="Self-Hosted AI Infrastructure Archive Alias | SysV",
        description="Redirecting to /self-hosting/ directory.",
        canonical_path="/self-hosting/",
        body_html='<div class="hero"><h1>Self-Hosted Infrastructure Directory Moved</h1><p class="muted">This directory now lives at <a href="/self-hosting/">/self-hosting/</a>.</p><p><a class="btn" href="/self-hosting/">Open Self-Hosting Directory &rarr;</a></p></div>',
        git_commit=git_commit,
        index_policy="noindex",
        allow_ads=False,
    )
    register_page("repos/index.html", "/repos/", "alias", "noindex", sem_hash(["repos-alias"]), repos_alias_html)

    # Keep /zero-cost-stack/ permanently alive (indexed guide linking to tools & self-hosting)
    zcs_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Zero-Cost Stack</div>
    <h1>Zero-Cost Infrastructure &amp; AI Automation Stack Guide</h1>
    <p class="muted">How to combine free-tier LLM APIs (no credit card), scheduled compute, static CDN hosting, and self-hosted automation with near-zero marginal cost.</p>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Stack Layer</th><th>Option</th><th>Verified Practical Limit</th></tr></thead>
        <tbody>
          <tr><td><strong>Batch LLM Intelligence</strong></td><td><a href="/free-llm-apis/">Rate-Limited Free APIs ({len(free_models)} tracked)</a> + low-cost fallback</td><td>OpenRouter :free capped at 20 RPM &amp; 50 RPD (1,000 RPD with &ge;$10 credits)</td></tr>
          <tr><td><strong>Scheduled Compute</strong></td><td>GitHub Actions Cron + External Monitor</td><td>Public repos unlimited; pair with external 48h freshness check</td></tr>
          <tr><td><strong>Static Hosting &amp; CDN</strong></td><td>Cloudflare Pages / Workers Static Assets</td><td>Unlimited static requests; 20,000 files/site on free plan</td></tr>
          <tr><td><strong>Always-On Self-Hosting</strong></td><td>Entry VPS ($5–$10/mo) + Docker (<a href="/tools/n8n-ai-workflow-cost-calculator/">compare n8n costs</a>)</td><td>Verify actual repository README for RAM/GPU needs</td></tr>
        </tbody>
      </table>
    </div>
    {render_affiliates(cfg)}
    """
    register_page(
        "zero-cost-stack/index.html",
        "/zero-cost-stack/",
        "guide",
        "index",
        sem_hash(["zcs-v2.2", len(free_models)]),
        render_layout(
            cfg,
            title="Zero-Cost AI & Cloud Infrastructure Stack Guide | SysV",
            description="Practical guide to free LLM APIs without credit card, rate limits, static CDN hosting, and self-hosted automation costs.",
            canonical_path="/zero-cost-stack/",
            body_html=zcs_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Zero-Cost Stack", "/zero-cost-stack/")],
        ),
    )

    # --- 8. Standalone Tool Pages (/tools/*), Ad-Free Embeds (/embed/*) & Linkable Data Assets (/data/*) ---
    tools_builder.build_tools_and_data_assets(
        cfg, models, calc_models, changelog, git_commit, write_page, render_layout, render_embed_layout, register_page, e
    )

    # --- 9. Trust / Compliance Pages ---
    email = cfg["contact_email"]
    domain = cfg["domain"]
    breakdown = selection_audit.get("raw_breakdown_by_pricing_type") or {}
    breakdown_rows = "".join(f"<li><code>{e(k)}</code>: {v} endpoints</li>" for k, v in sorted(breakdown.items()))
    trust_defs = [
        (
            "/about/",
            "About & Data Methodology | SysV",
            f"<h1>About SysV &amp; Verification Methodology</h1>"
            f"<div class='card'>"
            f"<p><strong>SysV ({e(domain)})</strong> is an independent developer reference and cost-estimation tool that tracks live LLM API token pricing, capability flags, cross-provider free-tier limits, daily price history, and self-hosted AI infrastructure.</p>"
            f"<p>SysV is not affiliated with, sponsored by, or endorsed by OpenRouter, OpenAI, Anthropic, Google, Groq, Cloudflare, Cerebras, or GitHub. All trademarks and model names belong to their respective owners.</p>"
            f"<h2>Evidence &amp; Verification Standards</h2><ul>"
            f"<li><strong>Source Verified:</strong> Retrieved daily via automated pipelines directly from the OpenRouter Public Models API and GitHub Public API, then validated by deterministic quality gates before publication.</li>"
            f"<li><strong>Repository Verified:</strong> Self-hosting Docker support is checked directly against actual repository root files (<code>Dockerfile</code>, <code>docker-compose.yml</code>, <code>compose.yml</code>) rather than guessed from descriptions.</li>"
            f"<li><strong>Official Docs Verified (Free-Tier Policies):</strong> Free-tier vs. time-limited trial rules (RPM, RPD, TPM, TPD, credit-card gates, and Cloudflare Workers AI Neurons) are audited against vendor documentation and monitored daily for policy drift.</li>"
            f"<li><strong>Not Verified:</strong> Hardware RAM/GPU requirements and runtime latency are never fabricated or displayed as synthetic scores; users should confirm runtime sizing directly in upstream documentation.</li></ul>"
            f"<h2 id='selection'>Endpoint Selection ({selection_audit.get('selected_for_display', 140)} of {selection_audit.get('total_raw_endpoints', 470)})</h2>"
            f"<p class='muted'>{e(selection_audit.get('selection_rationale', ''))}</p><ul>{breakdown_rows}</ul>"
            f"<h2>Open Data &amp; Ad-Free Embeds</h2>"
            f"<p>Machine-readable JSON snapshots, 30-day CSV price history, and RSS feeds are published openly under <a href='/data/'>/data/</a>. Embeddable calculator widgets under <code>/embed/</code> contain zero advertisements and zero third-party analytics.</p>"
            f"<h2 id='partners'>Advertising &amp; Affiliate Disclosure</h2>"
            f"<p class='muted'>To keep SysV free to use, content pages may display Google AdSense advertisements or clearly labeled <code>sponsored</code> infrastructure affiliate links (such as Vultr and DMIT). Model rankings, calculator outputs, and capability gates are computed strictly from public pricing and API metadata and are never influenced by commercial partnerships.</p>"
            f"<p>Spotted an outdated limit or pricing discrepancy? Reach us via our <a href='/contact/'>Contact page</a> or email <a href='mailto:{e(email)}'>{e(email)}</a>.</p>"
            f"</div>",
        ),
        (
            "/privacy/",
            "Privacy Policy | SysV",
            f"<h1>Privacy Policy</h1><div class='card'>"
            f"<p><strong>Effective Date:</strong> 2026-11-24 &middot; <strong>Last Updated:</strong> 2026-11-27</p>"
            f"<p>SysV (<strong>{e(domain)}</strong>) is a static, read-only informational website and client-side cost calculator. We do not offer user accounts, logins, comment forms, or server-side databases, and all calculator inputs are processed locally inside your web browser without being transmitted to our servers.</p>"
            f"<h2>1. Google AdSense &amp; Third-Party Advertising Cookies</h2>"
            f"<p>Content pages on {e(domain)} use <strong>Google AdSense</strong> to display advertisements. Third-party vendors, including Google, use cookies to serve ads based on a user&rsquo;s prior visits to this website or other websites on the internet. Google&rsquo;s use of advertising cookies enables Google and its partners to serve ads based on your visit to {e(domain)} and/or other sites.</p>"
            f"<ul>"
            f"<li>You may opt out of personalized advertising at any time by visiting <a href='https://adssettings.google.com/' target='_blank' rel='noopener noreferrer'>Google Ads Settings</a>.</li>"
            f"<li>You can also opt out of a third-party vendor&rsquo;s use of cookies for personalized advertising via <a href='https://www.aboutads.info/choices/' target='_blank' rel='noopener noreferrer'>aboutads.info</a>.</li>"
            f"<li>Learn more about how Google uses data when you use partner sites at <a href='https://policies.google.com/technologies/partner-sites' target='_blank' rel='noopener noreferrer'>Google&rsquo;s Privacy &amp; Terms</a>.</li>"
            f"</ul>"
            f"<p>AdSense scripts are intentionally excluded from error pages (<code>404</code>), legal pages, and ad-free calculator embeds under <code>/embed/</code>.</p>"
            f"<h2>2. Consent Management (EEA, UK, Switzerland &amp; US States)</h2>"
            f"<p>For visitors in the European Economic Area (EEA), the United Kingdom, and Switzerland, {e(domain)} uses Google&rsquo;s certified Consent Management Platform (CMP) to request and record your consent choices under GDPR and ePrivacy rules. For visitors in applicable US states (including California under CCPA/CPRA), privacy notice and opt-out controls are presented via Google&rsquo;s US state regulations framework. You can review or withdraw your consent at any time using the privacy link provided at the bottom of eligible pages.</p>"
            f"<h2>3. Privacy-First Analytics &amp; Hosting Logs</h2>"
            f"<p>We use <strong>Cloudflare Web Analytics</strong> to measure aggregate page views and Core Web Vitals performance without setting client-side tracking cookies or collecting personally identifiable user profiles. Our static hosting and CDN providers (GitHub Pages and Cloudflare) may process standard HTTP request metadata (such as IP address and user agent) temporarily to deliver content and prevent abuse.</p>"
            f"<h2>4. Outbound Affiliate &amp; External Links</h2>"
            f"<p>Selected infrastructure recommendations are marked <code>rel=&quot;sponsored&quot;</code> (including Vultr and DMIT). Clicking an external or affiliate link navigates you to a third-party website governed by that provider&rsquo;s own privacy policy and cookie practices.</p>"
            f"<h2>5. Contact &amp; Privacy Requests</h2>"
            f"<p>If you have questions about this Privacy Policy or wish to submit a privacy inquiry regarding {e(domain)}, please email <a href='mailto:{e(email)}'>{e(email)}</a>.</p>"
            f"</div>",
        ),
        (
            "/terms/",
            "Terms of Use | SysV",
            f"<h1>Terms of Use</h1><div class='card'>"
            f"<p><strong>Effective Date:</strong> 2026-11-24 &middot; <strong>Last Updated:</strong> 2026-11-27</p>"
            f"<p>By accessing or using SysV (<strong>{e(domain)}</strong>), you agree to these Terms of Use. If you do not agree, please discontinue use of the site.</p>"
            f"<h2>1. Informational Purpose &amp; Pricing Accuracy</h2>"
            f"<p>All LLM API token prices, fee simulations, free-tier quota summaries, comparison tables, and self-hosting profiles published on {e(domain)} are provided strictly &ldquo;as-is&rdquo; for informational comparison. Upstream AI vendors frequently change token rates, rate limits, trial terms, and tiered context surcharges without notice. Always verify live pricing, billing policies, and open-source <code>LICENSE</code> files directly with the upstream provider before making production or financial commitments.</p>"
            f"<h2>2. Independence &amp; Trademarks</h2>"
            f"<p>SysV operates independently and is not affiliated with OpenRouter, OpenAI, Anthropic, Google, Groq, Cloudflare, Cerebras, GitHub, Vultr, or DMIT. All product names, model identifiers, and trademarks remain the property of their respective holders.</p>"
            f"<h2>3. Open Datasets &amp; Embeddable Calculators</h2>"
            f"<p>Public JSON and CSV files published under <a href='/data/'>/data/</a> are made available under CC-BY-4.0 attribution terms (subject to upstream API data policies). Ad-free widgets under <code>/embed/</code> may be embedded via <code>&lt;iframe&gt;</code> on external documentation or blogs provided you do not misrepresent SysV data or inject deceptive overlays.</p>"
            f"<h2>4. Affiliate &amp; Advertising Disclosure</h2>"
            f"<p>SysV may earn advertising revenue via Google AdSense and referral commissions from clearly labeled <code>sponsored</code> affiliate links at no additional cost to you.</p>"
            f"<h2>5. Limitation of Liability</h2>"
            f"<p>To the maximum extent permitted by applicable law, SysV and its operators shall not be liable for any direct, indirect, incidental, or consequential damages (including unexpected API billing overages or service downtime) arising from reliance on estimates or data displayed on this website.</p>"
            f"<p>Questions regarding these terms may be directed to <a href='mailto:{e(email)}'>{e(email)}</a>.</p>"
            f"</div>",
        ),
        (
            "/contact/",
            "Contact | SysV",
            f"<h1>Contact SysV</h1><div class='card'>"
            f"<p>We welcome factual corrections, free-tier policy updates, licensing notices, and privacy or compliance inquiries regarding <strong>{e(domain)}</strong>.</p>"
            f"<h2>Direct Email</h2>"
            f"<p><strong>Primary Contact:</strong> <a href='mailto:{e(email)}'>{e(email)}</a></p>"
            f"<h2>What to Include in Data &amp; Policy Reports</h2>"
            f"<ul>"
            f"<li><strong>Pricing or Capability Discrepancies:</strong> Include the model endpoint ID (e.g., <code>provider/model-name</code>), the SysV URL, and a link to the official provider pricing or documentation page.</li>"
            f"<li><strong>Free-Tier Policy Changes:</strong> Include the provider name (OpenRouter, Gemini, Groq, Cloudflare Workers AI, Cerebras, etc.), the official rate-limit documentation URL, and the updated quota or credit-card rule.</li>"
            f"<li><strong>Privacy, Advertising, or Licensing Notices:</strong> Clearly state the affected URL on {e(domain)} and the requested action so we can review and respond promptly.</li>"
            f"</ul>"
            f"<p class='muted small'>Note: SysV is an independent pricing radar and does not provide customer support, API keys, or billing refunds for OpenRouter or third-party AI model providers.</p>"
            f"</div>",
        ),
    ]
    for path, title, body in trust_defs:
        pol = seo_gate.decide_static_index_policy(path)
        register_page(
            f"{path.strip('/')}/index.html",
            path,
            "trust",
            pol,
            sem_hash([path, "trust-v2.2.2", TEMPLATE_VERSION, breakdown if path == "/about/" else "static"]),
            render_layout(
                cfg,
                title=title,
                description=title,
                canonical_path=path,
                body_html=body,
                git_commit=git_commit,
                index_policy=pol,
                breadcrumbs=[("Home", "/"), (title.split(" | ")[0], path)],
            ),
        )

    # --- 10. 404 Page (noindex & strictly ad-free per AdSense policy) ---
    not_found_html = render_layout(
        cfg,
        title="404 Page Not Found | SysV",
        description="Requested page not found.",
        canonical_path="/404.html",
        body_html="<div class='hero'><h1>404 — Page Not Found</h1><p class='muted'>The requested URL was not found. Delisted model pages remain archived under <a href='/models/'>/models/</a>.</p><p><a class='btn' href='/'>Back to LLM Pricing &amp; Calculator &rarr;</a></p></div>",
        git_commit=git_commit,
        index_policy="noindex",
        allow_ads=False,
    )
    write_page("404.html", not_found_html)

    # --- 11. Reconcile Permanent Page Registry, Sitemap (INDEX ONLY!), Feeds, Manifest ---
    published_pages = update_published_pages_registry(page_records, now_iso=now_iso, today=today)
    base = cfg["base_url"].rstrip("/")
    sitemap_nodes = [
        f"  <url><loc>{e(base + u)}</loc><lastmod>{meta['lastmod']}</lastmod></url>"
        for u, meta in sorted(published_pages.items())
        if meta.get("status") != "gone" and meta.get("index_policy") == "index"
    ]
    write_page(
        "sitemap.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(sitemap_nodes)
        + "\n</urlset>\n",
    )
    write_page("robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n")

    feed_items = "\n".join(
        "    <item>\n"
        f"      <title>{e(m['name'])} ({e(m['pricing_label'])})</title>\n"
        f"      <link>{e(base + '/models/' + m['slug'] + '/')}</link>\n"
        f"      <guid>{e(base + '/models/' + m['slug'] + '/')}</guid>\n"
        f"      <description>{e(m['description'])}</description>\n"
        "    </item>"
        for m in active_models[:20]
    )
    write_page(
        "feed.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0">\n  <channel>\n'
        f"    <title>SysV LLM API Pricing Feed</title>\n    <link>{e(base)}/</link>\n"
        f"    <description>{e(cfg['description'])}</description>\n{feed_items}\n  </channel>\n</rss>\n",
    )
    write_page(
        "favicon.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="#1d1d1f"/>'
        '<text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,sans-serif" font-weight="600" font-size="27" letter-spacing="-1" fill="#ffffff">SV</text></svg>\n',
    )
    write_page("CNAME", f"{cfg['domain']}\n")
    write_page(
        "_headers",
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "  X-Frame-Options: SAMEORIGIN\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  Cache-Control: public, max-age=3600\n\n"
        "/embed/*\n"
        "  ! X-Frame-Options\n"
        "  Content-Security-Policy: frame-ancestors *\n\n"
        "/version.json\n"
        "  Cache-Control: public, max-age=300\n",
    )

    client_id = str(cfg.get("monetization", {}).get("adsense_client_id") or "").strip()
    if client_id:
        write_page("ads.txt", f"google.com, {client_id.replace('ca-', '')}, DIRECT, f08c47fec0942fa0\n")

    indexnow_key = os.getenv("INDEXNOW_KEY", "").strip()
    if indexnow_key:
        write_page(f"{indexnow_key}.txt", f"{indexnow_key}\n")

    version_manifest = {
        "site": cfg["domain"],
        "version": TEMPLATE_VERSION,
        "deploy_target": os.getenv("DEPLOY_TARGET", "github-pages"),
        "git_commit": git_commit,
        "built_at_utc": now_iso,
        "sitemap_indexable_urls": len(sitemap_nodes),
        "active_models": len(active_models),
        "delisted_models": sum(1 for m in models if m.get("status") == "delisted"),
        "limited_free_models": len(free_models),
        "batch_models": len(batch_models),
        "active_comparisons": len(active_pairs),
        "snapshot_days": changelog.get("snapshot_days", 1),
    }
    write_page("version.json", json.dumps(version_manifest, indent=2) + "\n")
    print(f"[build_site] Built V2.2 site: {len(page_records)} total pages, {len(sitemap_nodes)} indexable in sitemap.xml (commit={git_commit})")


if __name__ == "__main__":
    main()
