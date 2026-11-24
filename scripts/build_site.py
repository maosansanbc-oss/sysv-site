"""V2 Static Site Generator for SysV Radar (sysv.cn).

Zero-Cost Infrastructure & AI Automation Radar:
- Strict 7-state pricing display (never labels dynamic routers or media endpoints as FREE $0)
- Task-Oriented LLM Cost Calculator (Bulk Translation, Auto-Site Pipeline, Coding Agent, RAG, Custom)
- Same-cohort, cross-provider head-to-head model comparisons (zero nonsense pairs)
- /free-llm-apis/ verified free directory (separating Free Models vs Free Router)
- /price-changes/ proprietary daily price-history & free-tier changelog
- /zero-cost-stack/ decision blueprint for zero-cost AI & cloud infrastructure
- /repos/ curated self-hosted infra directory with License, Docker, Maintenance & Deployment Tier
- Full AdSense compliance pages, sitemap.xml, robots.txt, feed.xml, CNAME, _headers
"""

import html
import json
import shutil
from collections import defaultdict
from itertools import combinations
from typing import Any

from scripts.utils import DATA_DIR, DIST_DIR, load_config, load_json, utc_now_iso, utc_today


def e(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)


def fmt_price(price: float | None, ptype: str = "paid") -> str:
    if ptype == "variable":
        return "Variable (Routed)"
    if ptype == "media":
        return "Per-Request / Media"
    if ptype == "unknown" or price is None:
        return "Unknown"
    if price == 0:
        return "$0 (Free)"
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
        "free": "badge badge-free",
        "free_router": "badge badge-free",
        "variable": "badge badge-warn",
        "media": "badge badge-purple",
        "unknown": "badge",
        "paid": "badge",
    }.get(ptype, "badge")
    return f'<span class="{cls}">{e(label)}</span>'


CSS = """
:root {
  --bg: #0b1020;
  --surface: #121a2e;
  --surface-2: #19243f;
  --border: #263456;
  --text: #e6edf8;
  --muted: #98a8c7;
  --accent: #38bdf8;
  --accent-2: #22c55e;
  --warn: #f59e0b;
  --purple: #c084fc;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap { max-width: 1120px; margin: 0 auto; padding: 0 20px; }
header {
  border-bottom: 1px solid var(--border);
  background: rgba(11, 16, 32, 0.94);
  position: sticky; top: 0; z-index: 20; backdrop-filter: blur(8px);
}
.nav { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 0; flex-wrap: wrap; }
.brand { font-weight: 800; font-size: 1.15rem; color: #fff; letter-spacing: 0.3px; }
.brand span { color: var(--accent); }
.nav-links { display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.93rem; }
.nav-links a { color: var(--muted); }
.nav-links a:hover { color: #fff; text-decoration: none; }
main { padding: 28px 0 60px; }
.hero {
  background: linear-gradient(135deg, #131e38 0%, #0e162a 100%);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 26px;
  margin-bottom: 24px;
}
h1 { margin: 0 0 10px; font-size: 1.85rem; line-height: 1.25; }
h2 { margin: 28px 0 12px; font-size: 1.32rem; }
h3 { margin: 16px 0 8px; font-size: 1.06rem; }
.muted { color: var(--muted); }
.small { font-size: 0.84rem; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-top: 18px; }
.stat { background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 13px 15px; }
.stat .num { font-size: 1.4rem; font-weight: 700; color: #fff; }
.stat .lbl { font-size: 0.82rem; color: var(--muted); }
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 18px;
}
.callout-warn {
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.4);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 18px;
}
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 16px; }
.badge {
  display: inline-block; padding: 2px 9px; border-radius: 999px;
  font-size: 0.76rem; font-weight: 600; background: #1e2d4f; color: var(--accent);
}
.badge-free { background: rgba(34, 197, 94, 0.16); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.35); }
.badge-warn { background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.38); }
.badge-purple { background: rgba(192, 132, 252, 0.16); color: #d8b4fe; border: 1px solid rgba(192, 132, 252, 0.38); }
.controls { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
input, select {
  background: var(--bg); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; padding: 9px 12px; font-size: 0.93rem;
}
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
table { width: 100%; border-collapse: collapse; font-size: 0.91rem; }
th, td { padding: 11px 13px; text-align: left; border-bottom: 1px solid var(--border); vertical-align: top; }
th { background: var(--surface-2); color: var(--muted); font-weight: 600; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(56, 189, 248, 0.04); }
.calc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(195px, 1fr)); gap: 12px; }
.calc-result {
  margin-top: 16px; padding: 14px 16px; border-radius: 10px;
  background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.28);
}
.btn {
  display: inline-block; background: var(--accent); color: #071221;
  font-weight: 700; padding: 8px 14px; border-radius: 8px; font-size: 0.88rem;
}
.btn:hover { opacity: 0.92; text-decoration: none; }
.ad-slot {
  margin: 22px 0; text-align: center; min-height: 90px;
  border: 1px dashed var(--border); border-radius: 10px; padding: 10px;
  display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 0.85rem;
}
.breadcrumb { font-size: 0.86rem; color: var(--muted); margin-bottom: 14px; }
.faq-item { border-top: 1px solid var(--border); padding: 12px 0; }
.faq-item:first-child { border-top: none; }
footer {
  border-top: 1px solid var(--border);
  padding: 26px 0 40px;
  color: var(--muted);
  font-size: 0.87rem;
}
.footer-links { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 10px; }
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
    return '<h2>Recommended Infrastructure Partners</h2><p class="muted small">Affiliate disclosure: links below may earn a commission at no extra cost to you.</p><div class="grid">' + "".join(cards) + "</div>"


def render_layout(
    cfg: dict[str, Any],
    *,
    title: str,
    description: str,
    canonical_path: str,
    body_html: str,
    json_ld: dict[str, Any] | None = None,
) -> str:
    base = cfg["base_url"].rstrip("/")
    canonical = base + canonical_path
    client_id = str(cfg.get("monetization", {}).get("adsense_client_id") or "").strip()
    adsense_head = (
        f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={e(client_id)}" '
        f'crossorigin="anonymous"></script>'
        if client_id
        else ""
    )
    ld_html = (
        f'<script type="application/ld+json">{json.dumps(json_ld, ensure_ascii=False)}</script>'
        if json_ld
        else ""
    )
    today = utc_today()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{e(title)}</title>
  <meta name="description" content="{e(description)}" />
  <link rel="canonical" href="{e(canonical)}" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="alternate" type="application/rss+xml" title="{e(cfg['site_name'])} RSS" href="/feed.xml" />
  <meta property="og:title" content="{e(title)}" />
  <meta property="og:description" content="{e(description)}" />
  <meta property="og:url" content="{e(canonical)}" />
  <meta property="og:type" content="website" />
  <style>{CSS}</style>
  {adsense_head}
  {ld_html}
</head>
<body>
  <header>
    <div class="wrap nav">
      <a class="brand" href="/">SysV<span>.cn</span> Radar</a>
      <nav class="nav-links">
        <a href="/">Task Calculator</a>
        <a href="/free-llm-apis/">Free APIs</a>
        <a href="/compare/">Comparisons</a>
        <a href="/price-changes/">Price History</a>
        <a href="/zero-cost-stack/">Zero-Cost Stack</a>
        <a href="/repos/">Self-Hosted Infra</a>
      </nav>
    </div>
  </header>
  <main class="wrap">
    {body_html}
  </main>
  <footer>
    <div class="wrap">
      <div class="footer-links">
        <a href="/about/">About &amp; Methodology</a>
        <a href="/privacy/">Privacy Policy</a>
        <a href="/terms/">Terms of Use</a>
        <a href="/contact/">Contact</a>
        <a href="/feed.xml">RSS Feed</a>
        <a href="/sitemap.xml">Sitemap</a>
      </div>
      <div>&copy; {today[:4]} {e(cfg["site_name"])} ({e(cfg["domain"])}). {e(cfg["tagline"])} &middot; Built {today} UTC &middot; Pricing unit: USD / 1M tokens via OpenRouter Public API.</div>
    </div>
  </footer>
</body>
</html>
"""


def write_page(rel_path: str, content: str) -> None:
    out = DIST_DIR / rel_path.lstrip("/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")


def select_comparison_pairs(
    models: list[dict[str, Any]], max_pairs: int
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Build strictly meaningful same-cohort, cross-provider text LLM pairs.

    Bans routers, variable-priced endpoints, audio/image media endpoints, and unknown pricing.
    """
    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cohort_labels = {
        "free_general": "Free Tier ($0) Head-to-Head",
        "coding": "Coding & Agent Models",
        "reasoning": "Reasoning & Thinking Models",
        "budget_general": "Low-Cost Production APIs",
        "flagship_general": "Flagship & Long-Context APIs",
    }

    for m in models:
        if m.get("pricing_type") not in ("free", "paid"):
            continue
        cat = m.get("task_category", "")
        if cat in ("router", "media", "unknown"):
            continue
        bucket = "flagship_general" if cat == "long_context" else cat
        if bucket in cohort_labels:
            cohorts[bucket].append(m)

    pairs: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    seen_slugs: set[str] = set()
    order = ["free_general", "coding", "reasoning", "budget_general", "flagship_general"]
    per_bucket_cap = max(6, max_pairs // len(order) + 2)

    for bucket in order:
        items = cohorts.get(bucket, [])[:10]
        added_here = 0
        for a, b in combinations(items, 2):
            # Prefer cross-provider comparisons so matchups are genuinely useful
            if a["provider"] == b["provider"]:
                continue
            slug = f"{a['slug']}-vs-{b['slug']}"
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            pairs.append((a, b, cohort_labels[bucket]))
            added_here += 1
            if added_here >= per_bucket_cap or len(pairs) >= max_pairs:
                break
        if len(pairs) >= max_pairs:
            break

    return pairs[:max_pairs]


def build_home_page(
    cfg: dict[str, Any],
    models: list[dict[str, Any]],
    repos: list[dict[str, Any]],
    pairs: list[tuple[dict[str, Any], dict[str, Any], str]],
    changelog: dict[str, Any],
) -> None:
    free_models = [m for m in models if m["pricing_type"] == "free"]
    paid_models = [m for m in models if m["pricing_type"] == "paid"]
    var_routers = [m for m in models if m["pricing_type"] == "variable"]

    # Feed strictly verified fixed-price models (free + paid) into JS Task Calculator
    calc_models = [
        {
            "name": m["name"],
            "slug": m["slug"],
            "provider": m["provider"],
            "pricing_type": m["pricing_type"],
            "task_category": m["task_category"],
            "in": m["prompt_price_per_m"] or 0.0,
            "out": m["completion_price_per_m"] or 0.0,
            "ctx": m["context_length"],
        }
        for m in models
        if m["pricing_type"] in ("free", "paid") and m["prompt_price_per_m"] is not None
    ]

    rows = []
    for m in models:
        ptype = m["pricing_type"]
        sort_price = (
            (m["prompt_price_per_m"] + m["completion_price_per_m"])
            if ptype in ("free", "free_router", "paid") and m["prompt_price_per_m"] is not None
            else 999999.0
        )
        rows.append(
            f'<tr data-ptype="{e(ptype)}" data-cat="{e(m["task_category"])}" '
            f'data-price="{sort_price}" data-ctx="{m["context_length"]}" '
            f'data-search="{e((m["name"] + " " + m["provider"] + " " + m["id"] + " " + m["task_category"]).lower())}">'
            f'<td><a href="/models/{e(m["slug"])}/"><strong>{e(m["name"])}</strong></a>'
            f'<br><span class="muted small">{e(m["id"])}</span></td>'
            f'<td>{pricing_badge(m)}</td>'
            f'<td><span class="muted small">{e(m["task_category"].replace("_", " ").title())}</span></td>'
            f'<td>{e(fmt_ctx(m["context_length"]))}</td>'
            f'<td>{e(fmt_price(m["prompt_price_per_m"], ptype))}</td>'
            f'<td>{e(fmt_price(m["completion_price_per_m"], ptype))}</td>'
            f"</tr>"
        )

    compare_links = "".join(
        f'<div class="card"><span class="badge">{e(label)}</span>'
        f'<div style="margin-top:8px"><a href="/compare/{e(a["slug"])}-vs-{e(b["slug"])}/"><strong>{e(a["name"])} vs {e(b["name"])}</strong></a></div>'
        f'<p class="muted small" style="margin:6px 0 0">Input: {e(fmt_price(a["prompt_price_per_m"], a["pricing_type"]))} vs {e(fmt_price(b["prompt_price_per_m"], b["pricing_type"]))} &middot; '
        f'Context: {fmt_ctx(a["context_length"])} vs {fmt_ctx(b["context_length"])}</p></div>'
        for a, b, label in pairs[:6]
    )

    repo_cards = "".join(
        f'<div class="card"><span class="badge">{e(r.get("category", "Infra"))}</span> '
        f'<span class="badge {"badge-free" if r.get("commercial_friendly") else "badge-warn"}">{e(r.get("license", "Unknown"))}</span>'
        f'<div style="margin-top:8px"><a href="/repos/{e(r["slug"])}/"><strong>{e(r["full_name"])}</strong></a></div>'
        f'<p class="muted small" style="margin:6px 0">{e(r["description"])}</p>'
        f'<div class="muted small">&#9733; {r["stars"]:,} &middot; {e(r.get("deployment_tier", "VPS"))} &middot; {e(r.get("maintenance_status", "Active"))}</div></div>'
        for r in repos[:4]
    )

    body = f"""
    <section class="hero">
      <span class="badge badge-free">Daily Snapshot &middot; {utc_today()}</span>
      <span class="badge">{changelog.get("latest_total_endpoints", len(models))} Endpoints Monitored</span>
      <h1 style="margin-top:10px">Zero-Cost Infrastructure &amp; LLM Task Cost Radar</h1>
      <p class="muted">Find the lowest-cost way to run real AI workloads—bulk translation, automated sites, coding agents, and self-hosted infra—without falling for misleading &ldquo;$0&rdquo; dynamic routers.</p>
      <div class="stats">
        <div class="stat"><div class="num">{len(free_models)}</div><div class="lbl">Verified Free ($0) Text Models</div></div>
        <div class="stat"><div class="num">{len(paid_models)}</div><div class="lbl">Fixed-Rate Paid Models</div></div>
        <div class="stat"><div class="num">{len(var_routers)}</div><div class="lbl">Variable Routers (Flagged)</div></div>
        <div class="stat"><div class="num">{changelog.get("snapshot_days", 1)}d</div><div class="lbl">Historical Price Archive</div></div>
      </div>
    </section>

    <section class="card" id="task-calculator">
      <h2 style="margin-top:0">Task-Oriented Lowest-Cost LLM Finder</h2>
      <p class="muted small">Pick a real workload below to rank the cheapest verified paid APIs (excluding variable routers) plus verified $0 free alternatives. Unit: USD per 1M tokens (OpenRouter Public API).</p>
      <div class="calc-grid">
        <div>
          <label class="muted small" for="task-preset">Real Workload Preset</label><br />
          <select id="task-preset" style="width:100%;margin-top:6px">
            <option value="translation" data-in="2.5" data-out="2.5" data-cat="all">Bulk Translation (1M words / mo)</option>
            <option value="autosite" data-in="1.2" data-out="0.4" data-cat="all">Daily Auto-Site Pipeline (300 items / mo)</option>
            <option value="coding" data-in="18.0" data-out="3.5" data-cat="coding">AI Coding Agent (200 turns / day)</option>
            <option value="rag" data-in="12.0" data-out="1.0" data-cat="all">RAG Document QA (100 docs / day)</option>
            <option value="custom" data-in="5.0" data-out="1.0" data-cat="all">Custom Token Volume</option>
          </select>
        </div>
        <div>
          <label class="muted small" for="calc-in">Monthly Input Tokens (Millions)</label><br />
          <input id="calc-in" type="number" min="0" step="0.1" value="2.5" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="calc-out">Monthly Output Tokens (Millions)</label><br />
          <input id="calc-out" type="number" min="0" step="0.1" value="2.5" style="width:100%;margin-top:6px" />
        </div>
      </div>
      <div class="calc-result" id="task-ranking"></div>
    </section>

    {render_ad_slot(cfg, "adsense_slot_top")}

    <section>
      <h2>Verified LLM Pricing Directory (USD / 1M Tokens)</h2>
      <p class="muted small">Dynamic routers (e.g. OpenRouter Auto / Auto Code) are explicitly labeled <strong>Variable (Routed)</strong>—they are never misclassified as $0 free models.</p>
      <div class="controls">
        <input id="model-search" type="search" placeholder="Search model, provider, or category..." style="flex:1;min-width:220px" />
        <select id="tier-filter" aria-label="Filter by pricing type">
          <option value="all">All Pricing Types</option>
          <option value="free">Verified Free ($0) Only</option>
          <option value="paid">Fixed Paid Only</option>
          <option value="variable">Variable Routers Only</option>
        </select>
        <select id="model-sort" aria-label="Sort models">
          <option value="default">Sort: Featured</option>
          <option value="price-asc">Price: Cheapest Fixed First</option>
          <option value="ctx-desc">Context: Largest First</option>
        </select>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Model Endpoint</th>
              <th>Pricing Type</th>
              <th>Cohort</th>
              <th>Context</th>
              <th>Input / 1M</th>
              <th>Output / 1M</th>
            </tr>
          </thead>
          <tbody id="model-tbody">
            {"".join(rows)}
          </tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>Same-Cohort Head-to-Head Comparisons</h2>
      <p class="muted small">Only models with genuine task substitution (same capability cohort &amp; text modality) are compared.</p>
      <div class="grid">{compare_links}</div>
      <p><a href="/compare/">Browse all {len(pairs)} verified comparisons &rarr;</a></p>
    </section>

    <section>
      <h2>Curated Self-Hosted AI &amp; Automation Infrastructure</h2>
      <div class="grid">{repo_cards}</div>
      <p><a href="/repos/">Explore all {len(repos)} deployment-scored repositories &rarr;</a> &middot; <a href="/zero-cost-stack/">Read the Zero-Cost Stack Blueprint &rarr;</a></p>
    </section>

    {render_affiliates(cfg)}

    <script>
      (function() {{
        const MODELS = {json.dumps(calc_models, ensure_ascii=False)};
        const presetEl = document.getElementById('task-preset');
        const inEl = document.getElementById('calc-in');
        const outEl = document.getElementById('calc-out');
        const outBox = document.getElementById('task-ranking');

        function renderRanking() {{
          const mIn = Math.max(0, parseFloat(inEl.value || '0'));
          const mOut = Math.max(0, parseFloat(outEl.value || '0'));
          const cat = presetEl.options[presetEl.selectedIndex].dataset.cat || 'all';

          let pool = MODELS.filter(m => cat === 'all' || m.task_category === cat);
          if (pool.length < 5) pool = MODELS;

          const paid = pool
            .filter(m => m.pricing_type === 'paid')
            .map(m => ({{ ...m, cost: mIn * m.in + mOut * m.out }}))
            .sort((a, b) => a.cost - b.cost)
            .slice(0, 5);

          const frees = pool.filter(m => m.pricing_type === 'free').slice(0, 3);

          let html = '<strong>Cheapest Verified Paid APIs for ' + mIn.toFixed(1) + 'M in / ' + mOut.toFixed(1) + 'M out:</strong><ol style="margin:8px 0 10px;padding-left:20px">';
          paid.forEach(m => {{
            html += '<li><a href="/models/' + m.slug + '/"><strong>' + m.name + '</strong></a> &mdash; <strong>$' + m.cost.toFixed(2) + '/mo</strong> <span class="muted small">($' + m.in + ' in / $' + m.out + ' out per 1M)</span></li>';
          }});
          html += '</ol>';
          if (frees.length) {{
            html += '<strong>Zero-Cost ($0) Verified Free Alternatives (subject to RPM caps):</strong> ';
            html += frees.map(m => '<a href="/models/' + m.slug + '/">' + m.name + '</a>').join(' &middot; ');
          }}
          outBox.innerHTML = html;
        }}

        presetEl.addEventListener('change', () => {{
          const opt = presetEl.options[presetEl.selectedIndex];
          if (opt.value !== 'custom') {{
            inEl.value = opt.dataset.in;
            outEl.value = opt.dataset.out;
          }}
          renderRanking();
        }});
        [inEl, outEl].forEach(el => el.addEventListener('input', () => {{
          presetEl.value = 'custom';
          renderRanking();
        }}));
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
          if (mode === 'price-asc') {{
            list.sort((a, b) => parseFloat(a.dataset.price) - parseFloat(b.dataset.price));
          }} else if (mode === 'ctx-desc') {{
            list.sort((a, b) => parseInt(b.dataset.ctx, 10) - parseInt(a.dataset.ctx, 10));
          }}
          list.forEach(tr => {{
            const matchQ = !q || tr.dataset.search.includes(q);
            const matchT = tier === 'all' || tr.dataset.ptype === tier;
            tr.style.display = (matchQ && matchT) ? '' : 'none';
            tbody.appendChild(tr);
          }});
        }}
        searchEl.addEventListener('input', filterAndSortRows);
        tierEl.addEventListener('change', filterAndSortRows);
        sortEl.addEventListener('change', filterAndSortRows);
      }})();
    </script>
    """

    json_ld = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": cfg["site_name"],
        "url": cfg["base_url"],
        "description": cfg["description"],
    }
    write_page(
        "index.html",
        render_layout(
            cfg,
            title=f"{cfg['site_name']} — {cfg['tagline']}",
            description=cfg["description"],
            canonical_path="/",
            body_html=body,
            json_ld=json_ld,
        ),
    )


def build_free_hub(cfg: dict[str, Any], models: list[dict[str, Any]]) -> None:
    free_models = [m for m in models if m["pricing_type"] == "free"]
    free_routers = [m for m in models if m["pricing_type"] == "free_router"]
    var_routers = [m for m in models if m["pricing_type"] == "variable"]

    rows = "".join(
        f'<tr><td><a href="/models/{e(m["slug"])}/"><strong>{e(m["name"])}</strong></a><br><span class="muted small">{e(m["id"])}</span></td>'
        f'<td>{e(m["provider"])}</td>'
        f'<td>{e(fmt_ctx(m["context_length"]))}</td>'
        f'<td>{e(m.get("modality", "text->text"))}</td>'
        f'<td><span class="badge badge-free">Verified $0 / 1M</span></td></tr>'
        for m in free_models
    )

    router_rows = "".join(
        f'<tr><td><a href="/models/{e(m["slug"])}/"><strong>{e(m["name"])}</strong></a></td>'
        f'<td>{pricing_badge(m)}</td>'
        f'<td>{e(fmt_ctx(m["context_length"]))}</td>'
        f'<td class="muted small">{"Routes strictly across active $0 free endpoints." if m["pricing_type"] == "free_router" else "Dynamic billing: you pay the routed downstream model rate (NOT free)."}</td></tr>'
        for m in (free_routers + var_routers)
    )

    body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Free LLM APIs</div>
    <h1>Verified Free LLM APIs ({len(free_models)} Text Models)</h1>
    <p class="muted">Updated {utc_today()} from OpenRouter Public Models API. Only endpoints with verified <code>prompt = 0</code>, <code>completion = 0</code>, and text output are listed below.</p>
    <div class="callout-warn">
      <strong>Audit Note — Routers Excluded from Free List:</strong> Dynamic routers like <code>openrouter/auto</code> and <code>openrouter/auto-code</code> return <code>-1</code> (variable billing) in OpenRouter metadata. Routing carries no extra markup, but downstream paid models are billed normally—they are separated in the table below.
    </div>
    {render_ad_slot(cfg, "adsense_slot_top")}
    <div class="table-wrap">
      <table>
        <thead><tr><th>Verified Free Model</th><th>Provider</th><th>Context</th><th>Modality</th><th>Status</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>

    <h2>Free-Only vs. Variable Routers (Separated for Clarity)</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Router Endpoint</th><th>Pricing Type</th><th>Context</th><th>Billing Behavior</th></tr></thead>
        <tbody>{router_rows}</tbody>
      </table>
    </div>
    """
    write_page(
        "free-llm-apis/index.html",
        render_layout(
            cfg,
            title=f"Verified Free LLM APIs Without Misleading Routers ({utc_today()[:4]}) — {cfg['site_name']}",
            description=f"Daily verified directory of {len(free_models)} true $0 free LLM text APIs, separated cleanly from variable-priced routers.",
            canonical_path="/free-llm-apis/",
            body_html=body,
        ),
    )


def build_price_changes_page(cfg: dict[str, Any], changelog: dict[str, Any]) -> None:
    events = changelog.get("events", [])
    if events:
        rows = "".join(
            f'<tr><td>{e(ev["date"])}</td>'
            f'<td><span class="badge {"badge-free" if ev["type"] in ("new_free", "price_drop") else "badge-warn"}">{e(ev["type"].replace("_", " ").title())}</span></td>'
            f'<td><a href="/models/{e(ev["slug"])}/"><strong>{e(ev["name"])}</strong></a><br><span class="muted small">{e(ev["model_id"])}</span></td>'
            f'<td class="muted small">{e(ev["detail"])}</td></tr>'
            for ev in events[:100]
        )
        table_html = (
            '<div class="table-wrap"><table><thead><tr><th>Date</th><th>Change Type</th><th>Model</th><th>Details</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
        )
    else:
        table_html = (
            '<div class="card"><h3>Baseline Snapshot Recorded</h3>'
            f'<p class="muted">Historical tracking began on <strong>{e(changelog.get("first_snapshot_date", utc_today()))}</strong> '
            f'covering <strong>{changelog.get("latest_total_endpoints", 0)}</strong> live endpoints. '
            "Each daily automated run compares today's registry against previous snapshots and logs every price drop, price hike, newly launched free model, and free-tier removal here automatically.</p></div>"
        )

    body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Price History</div>
    <h1>LLM API Price History &amp; Free-Tier Changelog</h1>
    <p class="muted">Proprietary daily price archive tracking {changelog.get("latest_total_endpoints", 0)} endpoints since {e(changelog.get("first_snapshot_date", utc_today()))} ({changelog.get("snapshot_days", 1)} daily snapshot(s) archived).</p>
    {table_html}
    """
    write_page(
        "price-changes/index.html",
        render_layout(
            cfg,
            title=f"LLM API Price History & Free-Tier Changelog — {cfg['site_name']}",
            description="Daily automated changelog of LLM API price drops, hikes, new free endpoints, and tier removals.",
            canonical_path="/price-changes/",
            body_html=body,
        ),
    )


def build_zero_cost_stack_page(cfg: dict[str, Any], models: list[dict[str, Any]], repos: list[dict[str, Any]]) -> None:
    free_count = sum(1 for m in models if m["pricing_type"] == "free")
    docker_repos = [r for r in repos if r.get("has_docker")][:6]
    docker_list = "".join(
        f'<li><a href="/repos/{e(r["slug"])}/"><strong>{e(r["full_name"])}</strong></a> ({e(r.get("category"))}, {e(r.get("license"))}) &mdash; {e(r["description"])}</li>'
        for r in docker_repos
    )
    body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Zero-Cost Stack</div>
    <h1>Zero-Cost Infrastructure &amp; AI Automation Stack Blueprint</h1>
    <p class="muted">A practical architecture guide for running automated AI pipelines, static web tools, and self-hosted workflows with near-zero marginal cost.</p>

    <div class="table-wrap">
      <table>
        <thead><tr><th>Layer</th><th>Zero-Cost / Low-Cost Option</th><th>Practical Limit</th><th>Best Use Case</th></tr></thead>
        <tbody>
          <tr><td><strong>Batch LLM Intelligence</strong></td><td><a href="/free-llm-apis/">Verified Free APIs ({free_count} tracked)</a> + Low-cost DeepSeek / Flash fallback</td><td>Strict RPM caps on free tiers; use daily batch + SHA-256 hash cache</td><td>Scheduled summarization, structured extraction, translation</td></tr>
          <tr><td><strong>Scheduled Compute (Cron)</strong></td><td>GitHub Actions (public repo unlimited / private 2,000 min/mo)</td><td>6h max job duration; cron paused after 60d repo inactivity</td><td>Daily data fetch, snapshot diffing, static site builds</td></tr>
          <tr><td><strong>Static Hosting &amp; CDN</strong></td><td>Cloudflare Pages / Workers Static Assets</td><td>Unlimited bandwidth; 20,000 files/site on free tier</td><td>Zero-marginal-cost programmatic tools &amp; documentation</td></tr>
          <tr><td><strong>Always-On Self-Hosted VPS</strong></td><td>Entry 2GB–4GB RAM VPS ($3–$6/mo) running Docker Compose</td><td>No GPU; suitable for gateways, n8n, scrapers, SQLite</td><td>24/7 webhooks, workflow engines, API proxies</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card" style="margin-top:22px">
      <h2>Top Docker-Ready Self-Hosted Building Blocks</h2>
      <ul>{docker_list}</ul>
      <p><a href="/repos/">Browse full self-hosted infrastructure directory &rarr;</a></p>
    </div>
    """
    write_page(
        "zero-cost-stack/index.html",
        render_layout(
            cfg,
            title=f"Zero-Cost AI & Cloud Infrastructure Stack Blueprint — {cfg['site_name']}",
            description="Practical blueprint comparing free LLM APIs, scheduled compute, static CDN hosting, and low-RAM self-hosted VPS stacks.",
            canonical_path="/zero-cost-stack/",
            body_html=body,
        ),
    )


def _deterministic_model_faqs(m: dict[str, Any]) -> list[dict[str, str]]:
    ptype = m["pricing_type"]
    p_in = m.get("prompt_price_per_m")
    p_out = m.get("completion_price_per_m")
    if ptype == "free":
        price_ans = f"Yes. As of {m['updated_at']}, {m['name']} is verified at $0 per 1M input and output tokens on OpenRouter, subject to upstream rate limits."
    elif ptype == "free_router":
        price_ans = f"Yes. {m['name']} routes strictly across currently active $0 free-tier models without token charges."
    elif ptype == "variable":
        price_ans = f"No. {m['name']} is a variable-priced dynamic router (raw OpenRouter price = -1). There is no extra routing markup, but you pay the exact token rate of whichever downstream paid model handles your prompt."
    elif ptype == "paid" and p_in is not None and p_out is not None:
        price_ans = f"No. {m['name']} is a paid endpoint billed at ${p_in} per 1M input tokens and ${p_out} per 1M output tokens (USD, verified {m['updated_at']})."
    else:
        price_ans = f"{m['name']} uses specialized or non-standard billing ({m.get('pricing_label', ptype)}). Verify current unit rates directly on OpenRouter."

    return [
        {"q": f"Is {m['name']} actually free to use via API?", "a": price_ans},
        {
            "q": f"What context length and modality does {m['name']} support?",
            "a": f"{m['name']} supports up to {m['context_length']:,} tokens ({fmt_ctx(m['context_length'])}) with modality {m.get('modality', 'text->text')}.",
        },
    ]


def build_model_pages(cfg: dict[str, Any], models: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for idx, m in enumerate(models):
        ptype = m["pricing_type"]
        enr = m.get("enrichment") or {}
        best_for = "".join(f"<li>{e(x)}</li>" for x in enr.get("best_for", []))
        watch_outs = "".join(f"<li>{e(x)}</li>" for x in enr.get("watch_outs", []))
        faqs = _deterministic_model_faqs(m)
        faq_html = "".join(
            f'<div class="faq-item"><h3>{e(f["q"])}</h3><p class="muted">{e(f["a"])}</p></div>' for f in faqs
        )

        # Related models strictly from same task_category when possible
        same_cat = [other for j, other in enumerate(models) if j != idx and other["task_category"] == m["task_category"]]
        related = (same_cat if len(same_cat) >= 3 else [other for j, other in enumerate(models) if j != idx])[:4]
        rel_html = "".join(
            f'<li><a href="/models/{e(r["slug"])}/">{e(r["name"])}</a> ({e(fmt_price(r["prompt_price_per_m"], r["pricing_type"]))} / {e(fmt_ctx(r["context_length"]))} ctx)</li>'
            for r in related
        )

        if ptype in ("free", "free_router", "paid") and m["prompt_price_per_m"] is not None:
            est_sample = f"${(m['prompt_price_per_m'] * 8 + (m['completion_price_per_m'] or 0) * 2):.2f}"
        else:
            est_sample = "Depends on routed model" if ptype == "variable" else "N/A"

        var_warning = (
            '<div class="callout-warn"><strong>Dynamic Routing Notice:</strong> This endpoint does not have a fixed token price. '
            'You are billed the underlying downstream model rate for each call. Do not assume $0 cost.</div>'
            if ptype == "variable"
            else ""
        )

        cache_line = (
            f" &middot; Cache Read: <strong>${m['cache_read_price_per_m']}/1M</strong>"
            if m.get("cache_read_price_per_m") is not None
            else ""
        )

        body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="/free-llm-apis/">Models</a> / {e(m["name"])}</div>
        <div class="hero">
          {pricing_badge(m)} <span class="badge">{e(m["task_category"].replace("_", " ").title())}</span>
          <h1 style="margin-top:10px">{e(m["name"])} — Pricing, Specs &amp; Decision Guide</h1>
          <p class="muted">{e(m["description"])}</p>
          <p class="muted small">Endpoint ID: <code>{e(m["id"])}</code> &middot; Source: OpenRouter Public API &middot; Verified {e(m["updated_at"])}{cache_line}</p>
          <div class="stats">
            <div class="stat"><div class="num">{e(fmt_price(m["prompt_price_per_m"], ptype))}</div><div class="lbl">Input / 1M Tokens</div></div>
            <div class="stat"><div class="num">{e(fmt_price(m["completion_price_per_m"], ptype))}</div><div class="lbl">Output / 1M Tokens</div></div>
            <div class="stat"><div class="num">{m["context_length"]:,}</div><div class="lbl">Max Context Tokens</div></div>
            <div class="stat"><div class="num">{e(est_sample)}</div><div class="lbl">Est. 8M In + 2M Out</div></div>
          </div>
        </div>
        {var_warning}
        <div class="card">
          <h2>Engineering Decision Notes</h2>
          <p>{e(enr.get("summary", m["description"]))}</p>
          <div class="grid">
            <div><h3>Best Suited For</h3><ul>{best_for}</ul></div>
            <div><h3>Watch-Outs</h3><ul>{watch_outs}</ul></div>
          </div>
        </div>
        {render_ad_slot(cfg, "adsense_slot_in_article")}
        <div class="card">
          <h2>Frequently Asked Questions</h2>
          {faq_html}
        </div>
        <div class="card">
          <h2>Similar Models in Same Cohort</h2>
          <ul>{rel_html}</ul>
        </div>
        """

        json_ld = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
                for f in faqs
            ],
        }
        rel_url = f"/models/{m['slug']}/"
        urls.append(rel_url)
        write_page(
            f"models/{m['slug']}/index.html",
            render_layout(
                cfg,
                title=f"{m['name']} API Pricing ({m['pricing_label']}) & Specs — {cfg['site_name']}",
                description=f"Verified {m['updated_at']}: {m['name']} ({m['pricing_label']}), {fmt_price(m['prompt_price_per_m'], ptype)} input, {fmt_ctx(m['context_length'])} context.",
                canonical_path=rel_url,
                body_html=body,
                json_ld=json_ld,
            ),
        )
    return urls


def build_compare_pages(
    cfg: dict[str, Any], pairs: list[tuple[dict[str, Any], dict[str, Any], str]]
) -> list[str]:
    urls: list[str] = ["/compare/"]
    cards = []
    for a, b, cohort_label in pairs:
        slug = f"{a['slug']}-vs-{b['slug']}"
        rel_url = f"/compare/{slug}/"
        urls.append(rel_url)

        a_in, a_out = a["prompt_price_per_m"] or 0.0, a["completion_price_per_m"] or 0.0
        b_in, b_out = b["prompt_price_per_m"] or 0.0, b["completion_price_per_m"] or 0.0

        # 3 concrete task workload calculations
        a_trans, b_trans = a_in * 2.5 + a_out * 2.5, b_in * 2.5 + b_out * 2.5
        a_pipe, b_pipe = a_in * 8.0 + a_out * 2.0, b_in * 8.0 + b_out * 2.0
        a_agent, b_agent = a_in * 18.0 + a_out * 3.5, b_in * 18.0 + b_out * 3.5

        if a_pipe < b_pipe:
            verdict = f"For a standard 8M input / 2M output automation workload, {a['name']} costs ${a_pipe:.2f} vs ${b_pipe:.2f} for {b['name']} (saving ${b_pipe - a_pipe:.2f})."
        elif b_pipe < a_pipe:
            verdict = f"For a standard 8M input / 2M output automation workload, {b['name']} costs ${b_pipe:.2f} vs ${a_pipe:.2f} for {a['name']} (saving ${a_pipe - b_pipe:.2f})."
        else:
            larger = a["name"] if a["context_length"] >= b["context_length"] else b["name"]
            verdict = f"Both endpoints have identical token pricing (${a_pipe:.2f} for 8M in / 2M out). On raw context capacity, {larger} offers the larger window ({fmt_ctx(max(a['context_length'], b['context_length']))})."

        cards.append(
            f'<div class="card"><span class="badge">{e(cohort_label)}</span>'
            f'<div style="margin-top:8px"><a href="{e(rel_url)}"><strong>{e(a["name"])} vs {e(b["name"])}</strong></a></div>'
            f'<p class="muted small" style="margin:6px 0 0">'
            f'Input: {e(fmt_price(a_in, a["pricing_type"]))} vs {e(fmt_price(b_in, b["pricing_type"]))} &middot; '
            f'Context: {e(fmt_ctx(a["context_length"]))} vs {e(fmt_ctx(b["context_length"]))}</p></div>'
        )

        body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="/compare/">Comparisons</a> / {e(a["name"])} vs {e(b["name"])}</div>
        <span class="badge">{e(cohort_label)}</span>
        <h1 style="margin-top:8px">{e(a["name"])} vs {e(b["name"])}: Real-Task Cost Comparison</h1>
        <p class="muted small">Verified {utc_today()} via OpenRouter Public Models API (USD per 1M tokens).</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Metric / Task Workload</th><th>{e(a["name"])}</th><th>{e(b["name"])}</th></tr></thead>
            <tbody>
              <tr><td>Provider &amp; Tier</td><td>{e(a["provider"])} &middot; {pricing_badge(a)}</td><td>{e(b["provider"])} &middot; {pricing_badge(b)}</td></tr>
              <tr><td>Context Window</td><td>{a["context_length"]:,} tokens</td><td>{b["context_length"]:,} tokens</td></tr>
              <tr><td>Input / Output per 1M</td><td>{e(fmt_price(a_in, a["pricing_type"]))} / {e(fmt_price(a_out, a["pricing_type"]))}</td><td>{e(fmt_price(b_in, b["pricing_type"]))} / {e(fmt_price(b_out, b["pricing_type"]))}</td></tr>
              <tr><td>Task 1: Bulk Translation (2.5M in / 2.5M out)</td><td><strong>${a_trans:.2f}</strong></td><td><strong>${b_trans:.2f}</strong></td></tr>
              <tr><td>Task 2: Auto-Site Pipeline (8M in / 2M out)</td><td><strong>${a_pipe:.2f}</strong></td><td><strong>${b_pipe:.2f}</strong></td></tr>
              <tr><td>Task 3: Coding Agent Loop (18M in / 3.5M out)</td><td><strong>${a_agent:.2f}</strong></td><td><strong>${b_agent:.2f}</strong></td></tr>
              <tr><td>Modality</td><td>{e(a.get("modality", "text->text"))}</td><td>{e(b.get("modality", "text->text"))}</td></tr>
              <tr><td>Full Specs</td><td><a href="/models/{e(a['slug'])}/">View {e(a["name"])} &rarr;</a></td><td><a href="/models/{e(b['slug'])}/">View {e(b["name"])} &rarr;</a></td></tr>
            </tbody>
          </table>
        </div>
        <div class="card" style="margin-top:20px">
          <h2>Cost &amp; Decision Verdict</h2>
          <p>{e(verdict)}</p>
        </div>
        {render_ad_slot(cfg, "adsense_slot_in_article")}
        """
        write_page(
            f"compare/{slug}/index.html",
            render_layout(
                cfg,
                title=f"{a['name']} vs {b['name']} — Real Workload Cost & Specs ({utc_today()[:4]}) | {cfg['site_name']}",
                description=verdict,
                canonical_path=rel_url,
                body_html=body,
            ),
        )

    hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Comparisons</div>
    <h1>Verified Same-Cohort LLM Comparisons ({len(pairs)} Matchups)</h1>
    <p class="muted">Every comparison strictly pairs cross-provider text models within the same capability tier—no dynamic routers or cross-modality mismatches.</p>
    <div class="grid">{"".join(cards)}</div>
    """
    write_page(
        "compare/index.html",
        render_layout(
            cfg,
            title=f"Same-Cohort LLM API Comparisons — {cfg['site_name']}",
            description="Head-to-head real workload cost comparisons between comparable LLM API endpoints.",
            canonical_path="/compare/",
            body_html=hub_body,
        ),
    )
    return urls


def build_repo_pages(cfg: dict[str, Any], repos: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = ["/repos/"]
    categories = sorted({r.get("category", "Self-Hosted Infra") for r in repos})
    cards = []

    for r in repos:
        rel_url = f"/repos/{r['slug']}/"
        urls.append(rel_url)
        enr = r.get("enrichment") or {}
        best_for = "".join(f"<li>{e(x)}</li>" for x in enr.get("best_for", []))
        watch_outs = "".join(f"<li>{e(x)}</li>" for x in enr.get("watch_outs", []))
        cat = r.get("category", "Self-Hosted Infra")
        lic = r.get("license", "Unknown")
        comm = bool(r.get("commercial_friendly"))
        docker_badge = '<span class="badge badge-free">Docker Ready</span>' if r.get("has_docker") else ""
        delta_txt = f" (+{r['star_delta']} today)" if r.get("star_delta", 0) > 0 else ""

        cards.append(
            f'<div class="card repo-card" data-cat="{e(cat)}" data-docker="{"1" if r.get("has_docker") else "0"}" data-comm="{"1" if comm else "0"}">'
            f'<span class="badge">{e(cat)}</span> '
            f'<span class="badge {"badge-free" if comm else "badge-warn"}">{e(lic)}</span> {docker_badge}'
            f'<div style="margin-top:8px"><a href="{e(rel_url)}"><strong>{e(r["full_name"])}</strong></a></div>'
            f'<p class="muted small" style="margin:6px 0">{e(r["description"])}</p>'
            f'<div class="muted small">&#9733; {r["stars"]:,}{e(delta_txt)} &middot; {e(r.get("deployment_tier", "VPS"))} &middot; {e(r.get("maintenance_status", "Active"))}</div></div>'
        )

        body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="/repos/">Self-Hosted Infra</a> / {e(r["full_name"])}</div>
        <div class="hero">
          <span class="badge">{e(cat)}</span>
          <span class="badge {'badge-free' if comm else 'badge-warn'}">License: {e(lic)}</span>
          {docker_badge}
          <h1 style="margin-top:10px">{e(r["full_name"])} — Self-Hosting &amp; Deployment Profile</h1>
          <p class="muted">{e(r["description"])}</p>
          <p><a class="btn" href="{e(r["html_url"])}" target="_blank" rel="noopener noreferrer">View Repository on GitHub &rarr;</a></p>
          <div class="stats">
            <div class="stat"><div class="num">{r["stars"]:,}</div><div class="lbl">GitHub Stars{e(delta_txt)}</div></div>
            <div class="stat"><div class="num">{e(r.get("deployment_tier", "VPS"))}</div><div class="lbl">Deployment Tier</div></div>
            <div class="stat"><div class="num">{e(r.get("maintenance_status", "Active"))}</div><div class="lbl">Maintenance Health</div></div>
            <div class="stat"><div class="num">{"Yes" if comm else "Check"}</div><div class="lbl">Permissive Commercial License</div></div>
          </div>
        </div>
        <div class="card">
          <h2>Self-Hosting Decision Notes</h2>
          <p>{e(enr.get("summary", r["description"]))}</p>
          <div class="grid">
            <div><h3>Best Suited For</h3><ul>{best_for}</ul></div>
            <div><h3>Deployment &amp; License Watch-Outs</h3><ul>{watch_outs}</ul></div>
          </div>
        </div>
        {render_ad_slot(cfg, "adsense_slot_in_article")}
        """
        write_page(
            f"repos/{r['slug']}/index.html",
            render_layout(
                cfg,
                title=f"{r['full_name']} Self-Hosting Profile ({lic}, {cat}) — {cfg['site_name']}",
                description=f"{r['full_name']} ({lic}, {r.get('deployment_tier')}, {r.get('maintenance_status')}): {r['description']}",
                canonical_path=rel_url,
                body_html=body,
            ),
        )

    cat_opts = "".join(f'<option value="{e(c)}">{e(c)}</option>' for c in categories)
    hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Self-Hosted Infra</div>
    <h1>Curated Self-Hosted AI &amp; Automation Infrastructure ({len(repos)} Projects)</h1>
    <p class="muted">Filtered exclusively for AI gateways, local LLM runtimes, workflow automation, and self-hosted infra—scored by License, Docker support, Deployment Tier, and Maintenance Health.</p>
    <div class="controls">
      <select id="repo-cat"><option value="all">All Categories</option>{cat_opts}</select>
      <label style="cursor:pointer"><input id="repo-docker" type="checkbox" /> Docker Ready Only</label>
      <label style="cursor:pointer"><input id="repo-comm" type="checkbox" /> Permissive Commercial License Only</label>
    </div>
    <div class="grid" id="repo-grid">{"".join(cards)}</div>
    <script>
      (function() {{
        const catEl = document.getElementById('repo-cat');
        const dEl = document.getElementById('repo-docker');
        const cEl = document.getElementById('repo-comm');
        const items = Array.from(document.querySelectorAll('.repo-card'));
        function filter() {{
          const cat = catEl.value;
          const needD = dEl.checked;
          const needC = cEl.checked;
          items.forEach(el => {{
            const ok = (cat === 'all' || el.dataset.cat === cat) && (!needD || el.dataset.docker === '1') && (!needC || el.dataset.comm === '1');
            el.style.display = ok ? '' : 'none';
          }});
        }}
        [catEl, dEl, cEl].forEach(el => el.addEventListener('change', filter));
      }})();
    </script>
    """
    write_page(
        "repos/index.html",
        render_layout(
            cfg,
            title=f"Curated Self-Hosted AI & Automation Infra Directory — {cfg['site_name']}",
            description="Deployment-scored directory of self-hosted AI gateways, local LLM runtimes, and workflow automation engines.",
            canonical_path="/repos/",
            body_html=hub_body,
        ),
    )
    return urls


def build_static_trust_pages(cfg: dict[str, Any]) -> list[str]:
    email = cfg["contact_email"]
    name = cfg["site_name"]
    domain = cfg["domain"]
    pages = [
        (
            "/about/",
            f"About & Methodology — {name}",
            f"<h1>About {e(name)} &amp; Pricing Methodology</h1>"
            f"<div class='card'><p>{e(name)} ({e(domain)}) is an automated Zero-Cost Infrastructure &amp; AI Automation Radar.</p>"
            f"<h2>Data Sources &amp; Classification Rules</h2>"
            f"<ul>"
            f"<li><strong>Pricing Registry:</strong> Pulled daily from the OpenRouter Public Models API. All per-token rates are displayed in USD per 1M tokens.</li>"
            f"<li><strong>Strict Free-Tier Verification:</strong> Only endpoints with verified <code>0/0</code> prompt &amp; completion pricing and text output are labeled Free. Dynamic routers (<code>-1</code>) are explicitly flagged as <em>Variable (Routed)</em> and excluded from free calculators.</li>"
            f"<li><strong>Historical Archive:</strong> Daily price snapshots are archived to track price drops, hikes, and free-tier retirements transparently.</li>"
            f"</ul>"
            f"<h2 id='partners'>Advertising &amp; Affiliate Disclosure</h2>"
            f"<p class='muted'>To keep this resource free, pages may include automated display advertisements (e.g., Google AdSense) or clearly labeled affiliate partner links. Pricing data and rankings are computed programmatically from public APIs and are never paid placements.</p></div>",
        ),
        (
            "/privacy/",
            f"Privacy Policy — {name}",
            f"<h1>Privacy Policy</h1>"
            f"<div class='card'><p>Effective Date: {utc_today()}</p>"
            f"<p>{e(name)} ({e(domain)}) is a static informational site. We do not require accounts, do not collect personal profiles, and do not sell personal data.</p>"
            f"<h2>Cookies &amp; Third-Party Advertising</h2>"
            f"<p>Third-party vendors, including Google, may use cookies to serve ads based on prior visits to this website or other sites. Users may opt out of personalized advertising via Google Ads Settings.</p>"
            f"<h2>CDN &amp; Infrastructure Logs</h2>"
            f"<p>Traffic is delivered via Cloudflare CDN and GitHub Pages, which may process standard request metadata (IP address, user-agent) for security and delivery.</p>"
            f"<h2>Contact</h2><p>Privacy inquiries: <a href='mailto:{e(email)}'>{e(email)}</a>.</p></div>",
        ),
        (
            "/terms/",
            f"Terms of Use — {name}",
            f"<h1>Terms of Use</h1>"
            f"<div class='card'><p>All pricing snapshots, workload estimates, and self-hosting notes on {e(domain)} are provided &ldquo;as-is&rdquo; for informational comparison only. Upstream providers may modify pricing, rate limits, or free-tier availability without notice. Always verify live billing terms on the provider console before production deployment.</p></div>",
        ),
        (
            "/contact/",
            f"Contact — {name}",
            f"<h1>Contact {e(name)}</h1>"
            f"<div class='card'><p>For pricing corrections, data source suggestions, or compliance inquiries regarding {e(domain)}, email <a href='mailto:{e(email)}'>{e(email)}</a>.</p></div>",
        ),
    ]
    urls: list[str] = []
    for path, title, body in pages:
        urls.append(path)
        write_page(
            f"{path.strip('/')}/index.html",
            render_layout(cfg, title=title, description=title, canonical_path=path, body_html=body),
        )
    return urls


def build_seo_files(
    cfg: dict[str, Any], all_urls: list[str], models: list[dict[str, Any]], repos: list[dict[str, Any]]
) -> None:
    base = cfg["base_url"].rstrip("/")
    now = utc_now_iso()
    url_nodes = "\n".join(
        f"  <url><loc>{e(base + u)}</loc><lastmod>{now}</lastmod></url>" for u in sorted(set(all_urls))
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{url_nodes}\n"
        "</urlset>\n"
    )
    write_page("sitemap.xml", sitemap)

    robots = f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n"
    write_page("robots.txt", robots)

    feed_items = []
    for m in models[:20]:
        feed_items.append(
            "    <item>\n"
            f"      <title>{e(m['name'])} ({e(m['pricing_label'])})</title>\n"
            f"      <link>{e(base + '/models/' + m['slug'] + '/')}</link>\n"
            f"      <guid>{e(base + '/models/' + m['slug'] + '/')}</guid>\n"
            f"      <description>{e(m['description'])}</description>\n"
            "    </item>"
        )
    for r in repos[:10]:
        feed_items.append(
            "    <item>\n"
            f"      <title>{e(r['full_name'])} — {e(r.get('category', 'Infra'))} ({r['stars']:,} stars)</title>\n"
            f"      <link>{e(base + '/repos/' + r['slug'] + '/')}</link>\n"
            f"      <guid>{e(base + '/repos/' + r['slug'] + '/')}</guid>\n"
            f"      <description>{e(r['description'])}</description>\n"
            "    </item>"
        )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        f"    <title>{e(cfg['site_name'])}</title>\n"
        f"    <link>{e(base)}/</link>\n"
        f"    <description>{e(cfg['description'])}</description>\n"
        + "\n".join(feed_items)
        + "\n  </channel>\n</rss>\n"
    )
    write_page("feed.xml", rss)

    favicon = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="14" fill="#0b1020"/>'
        '<text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" '
        'font-family="sans-serif" font-weight="800" font-size="28" fill="#38bdf8">SV</text>'
        "</svg>\n"
    )
    write_page("favicon.svg", favicon)

    not_found_body = (
        "<div class='hero'><h1>404 — Page Not Found</h1>"
        "<p class='muted'>The model or resource you requested may have been renamed or delisted upstream.</p>"
        "<p><a class='btn' href='/'>Back to Task Cost Radar &rarr;</a></p></div>"
    )
    write_page(
        "404.html",
        render_layout(
            cfg,
            title=f"404 Not Found — {cfg['site_name']}",
            description="Page not found.",
            canonical_path="/404.html",
            body_html=not_found_body,
        ),
    )

    headers_txt = (
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "  X-Frame-Options: SAMEORIGIN\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  Cache-Control: public, max-age=3600\n"
    )
    write_page("CNAME", f"{cfg['domain']}\n")
    write_page("_headers", headers_txt)

    client_id = str(cfg.get("monetization", {}).get("adsense_client_id") or "").strip()
    if client_id:
        pub_id = client_id.replace("ca-", "")
        write_page("ads.txt", f"google.com, {pub_id}, DIRECT, f08c47fec0942fa0\n")


def main() -> None:
    cfg = load_config()
    models = load_json(DATA_DIR / "models.json", default=load_json(DATA_DIR / "seed_models.json", default=[]))
    repos = load_json(DATA_DIR / "repos.json", default=load_json(DATA_DIR / "seed_repos.json", default=[]))
    changelog = load_json(
        DATA_DIR / "price_changes.json",
        default={"snapshot_days": 1, "first_snapshot_date": utc_today(), "latest_total_endpoints": len(models), "events": []},
    )

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    pairs = select_comparison_pairs(models, int(cfg["pipeline"].get("max_compare_pages", 40)))

    all_urls = ["/", "/free-llm-apis/", "/price-changes/", "/zero-cost-stack/"]
    build_home_page(cfg, models, repos, pairs, changelog)
    build_free_hub(cfg, models)
    build_price_changes_page(cfg, changelog)
    build_zero_cost_stack_page(cfg, models, repos)
    all_urls.extend(build_model_pages(cfg, models))
    all_urls.extend(build_compare_pages(cfg, pairs))
    all_urls.extend(build_repo_pages(cfg, repos))
    all_urls.extend(build_static_trust_pages(cfg))
    build_seo_files(cfg, all_urls, models, repos)

    print(f"[build_site] Built {len(set(all_urls))} V2 pages into {DIST_DIR} (same-cohort comparisons={len(pairs)})")


if __name__ == "__main__":
    main()
