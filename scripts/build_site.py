"""V2.1 Static Site Generator for SysV Radar (sysv.cn).

V2.1 Audit Fixes:
- 8-state pricing badges (explicitly separates Limited Free Tier, Async Batch, Variable Routers, Paid, Media, Unknown)
- Capability-gated 'Estimated Token Cost Finder' (checks is_realtime, tools, structured_outputs, context_length; never claims 'Best Model')
- Permanent SEO URL lifecycle: delisted models keep their URL with historical last-known pricing & active alternatives
- True Sitemap `lastmod`: computed from semantic body content hash (`data/page_state.json`), NOT bulk-refreshed daily
- Emits `/version.json` with exact git_commit & built_at_utc for external black-box monitoring
- Documents exact 473 -> 140 selection audit transparently on /about/
- Honest 3-tier open-source license badges (never equates permissive with zero commercial obligations)
"""

import hashlib
import html
import json
import shutil
import subprocess
from collections import defaultdict
from itertools import combinations
from typing import Any

from scripts.utils import DATA_DIR, DIST_DIR, ROOT_DIR, load_config, load_json, save_json, utc_now_iso, utc_today


def e(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)


def get_git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT_DIR, stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip() or "unknown"
    except Exception:
        return "unknown"


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
  --danger: #f87171;
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
h1 { margin: 0 0 10px; font-size: 1.82rem; line-height: 1.25; }
h2 { margin: 28px 0 12px; font-size: 1.32rem; }
h3 { margin: 16px 0 8px; font-size: 1.06rem; }
.muted { color: var(--muted); }
.small { font-size: 0.84rem; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-top: 18px; }
.stat { background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 13px 15px; }
.stat .num { font-size: 1.38rem; font-weight: 700; color: #fff; }
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
.callout-danger {
  background: rgba(248, 113, 113, 0.12);
  border: 1px solid rgba(248, 113, 113, 0.45);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 18px;
}
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 16px; }
.badge {
  display: inline-block; padding: 2px 9px; border-radius: 999px;
  font-size: 0.75rem; font-weight: 600; background: #1e2d4f; color: var(--accent);
}
.badge-free { background: rgba(34, 197, 94, 0.16); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.35); }
.badge-warn { background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.38); }
.badge-purple { background: rgba(192, 132, 252, 0.16); color: #d8b4fe; border: 1px solid rgba(192, 132, 252, 0.38); }
.badge-danger { background: rgba(248, 113, 113, 0.16); color: #fca5a5; border: 1px solid rgba(248, 113, 113, 0.4); }
.controls { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
input, select {
  background: var(--bg); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; padding: 9px 12px; font-size: 0.93rem;
}
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
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
  font-size: 0.86rem;
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
    return '<h2>Recommended Infrastructure Partners</h2><p class="muted small">Affiliate disclosure: links below may earn a cash commission at no extra cost to you.</p><div class="grid">' + "".join(cards) + "</div>"


def render_layout(
    cfg: dict[str, Any],
    *,
    title: str,
    description: str,
    canonical_path: str,
    body_html: str,
    git_commit: str,
    json_ld: dict[str, Any] | None = None,
) -> str:
    base = cfg["base_url"].rstrip("/")
    canonical = base + canonical_path
    client_id = str(cfg.get("monetization", {}).get("adsense_client_id") or "").strip()
    adsense_head = (
        f'<meta name="google-adsense-account" content="{e(client_id)}" />\n'
        f'  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={e(client_id)}" '
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
        <a href="/">Cost Estimator</a>
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
        <a href="/version.json">Build Info</a>
      </div>
      <div>&copy; {today[:4]} {e(cfg["site_name"])} ({e(cfg["domain"])}) &middot; Build <code>{e(git_commit)}</code> &middot; Verified {today} UTC &middot; Pricing unit: USD / 1M tokens (OpenRouter Public API).</div>
    </div>
  </footer>
</body>
</html>
"""


def write_page(rel_path: str, content: str) -> None:
    out = DIST_DIR / rel_path.lstrip("/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")


def record_page_and_write(
    rel_path: str,
    url_path: str,
    body_semantic: str,
    full_html: str,
    page_hashes: dict[str, str],
) -> None:
    write_page(rel_path, full_html)
    page_hashes[url_path] = hashlib.sha256(body_semantic.encode("utf-8")).hexdigest()[:16]


def select_comparison_pairs(
    models: list[dict[str, Any]], max_pairs: int
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Strictly same-cohort, cross-provider, active real-time text LLM pairs."""
    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cohort_labels = {
        "free_general": "Limited Free Tier ($0) Head-to-Head",
        "coding": "Real-Time Coding & Tool-Calling Models",
        "reasoning": "Reasoning & Thinking Models",
        "budget_general": "Low-Cost Real-Time Production APIs",
        "flagship_general": "Flagship & Long-Context APIs",
    }

    for m in models:
        if m.get("status") != "active":
            continue
        if m.get("pricing_type") not in ("limited_free", "free", "paid"):
            continue
        if not m.get("is_realtime", True):
            continue
        cat = m.get("task_category", "")
        if cat in ("router", "batch", "media", "unknown"):
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


def filter_task_candidates(
    models: list[dict[str, Any]], *, task: str, include_batch: bool = False
) -> list[dict[str, Any]]:
    """Capability-gated filter shared by tests and calculator."""
    allowed_types = {"limited_free", "free", "paid"} | ({"batch"} if include_batch else set())
    result = []
    for m in models:
        if m.get("status", "active") != "active":
            continue
        if m.get("pricing_type") not in allowed_types:
            continue
        if m.get("prompt_price_per_m") is None or m.get("completion_price_per_m") is None:
            continue
        caps = m.get("capabilities") or {}
        if task == "coding":
            if not m.get("is_realtime") or not caps.get("tools") or int(m.get("context_length") or 0) < 64000:
                continue
        elif task == "autosite":
            if not m.get("is_realtime") or not caps.get("structured_outputs"):
                continue
        elif task == "rag":
            if not m.get("is_realtime") or int(m.get("context_length") or 0) < 100000:
                continue
        elif task == "translation":
            if not include_batch and not m.get("is_realtime"):
                continue
        result.append(m)
    return result


def build_home_page(
    cfg: dict[str, Any],
    models: list[dict[str, Any]],
    repos: list[dict[str, Any]],
    pairs: list[tuple[dict[str, Any], dict[str, Any], str]],
    changelog: dict[str, Any],
    git_commit: str,
    page_hashes: dict[str, str],
) -> None:
    active_models = [m for m in models if m.get("status", "active") == "active"]
    free_models = [m for m in active_models if m["pricing_type"] in ("limited_free", "free")]
    paid_models = [m for m in active_models if m["pricing_type"] == "paid"]
    batch_models = [m for m in active_models if m["pricing_type"] == "batch"]

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
            "in": m["prompt_price_per_m"] or 0.0,
            "out": m["completion_price_per_m"] or 0.0,
            "cache_read": m.get("cache_read_price_per_m"),
            "ctx": m["context_length"],
        }
        for m in active_models
        if m["pricing_type"] in ("limited_free", "free", "paid", "batch") and m["prompt_price_per_m"] is not None
    ]

    rows = []
    for m in active_models:
        ptype = m["pricing_type"]
        caps = m.get("capabilities") or {}
        cap_tags = []
        if caps.get("tools"):
            cap_tags.append("Tools")
        if caps.get("structured_outputs"):
            cap_tags.append("JSON")
        if not m.get("is_realtime"):
            cap_tags.append("Async")
        sort_price = (
            (m["prompt_price_per_m"] + m["completion_price_per_m"])
            if ptype in ("limited_free", "free", "free_router", "paid", "batch") and m["prompt_price_per_m"] is not None
            else 999999.0
        )
        rows.append(
            f'<tr data-ptype="{e(ptype)}" data-realtime="{"1" if m.get("is_realtime") else "0"}" '
            f'data-price="{sort_price}" data-ctx="{m["context_length"]}" '
            f'data-search="{e((m["name"] + " " + m["provider"] + " " + m["id"] + " " + ptype).lower())}">'
            f'<td><a href="/models/{e(m["slug"])}/"><strong>{e(m["name"])}</strong></a>'
            f'<br><span class="muted small">{e(m["id"])}</span></td>'
            f'<td>{pricing_badge(m)}</td>'
            f'<td class="muted small">{e(" · ".join(cap_tags) or "Text")}</td>'
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
        f'<span class="badge {"badge-free" if r.get("license_tier") == "permissive" else "badge-warn"}">{e(r.get("license", "Unknown"))}</span>'
        f'<div style="margin-top:8px"><a href="/repos/{e(r["slug"])}/"><strong>{e(r["full_name"])}</strong></a></div>'
        f'<p class="muted small" style="margin:6px 0">{e(r["description"])}</p>'
        f'<div class="muted small">&#9733; {r["stars"]:,} &middot; {e(r.get("deployment_tier", "VPS"))} &middot; {e(r.get("maintenance_status", "Active"))}</div></div>'
        for r in repos[:4]
    )

    body = f"""
    <section class="hero">
      <span class="badge badge-free">Verified Daily</span>
      <span class="badge">{changelog.get("latest_total_endpoints", len(active_models))} Registry Endpoints Archived</span>
      <h1 style="margin-top:10px">Zero-Cost Infrastructure &amp; LLM Estimated Token Cost Radar</h1>
      <p class="muted">Capability-gated token cost estimates for real workloads—strictly separating rate-limited free tiers, async batch endpoints, and variable-priced routers.</p>
      <div class="stats">
        <div class="stat"><div class="num">{len(free_models)}</div><div class="lbl">Rate-Limited Free ($0) Models</div></div>
        <div class="stat"><div class="num">{len(paid_models)}</div><div class="lbl">Real-Time Paid Models</div></div>
        <div class="stat"><div class="num">{len(batch_models)}</div><div class="lbl">Async Batch Tracked</div></div>
        <div class="stat"><div class="num">{changelog.get("snapshot_days", 1)}d</div><div class="lbl">Price History Archive</div></div>
      </div>
    </section>

    <section class="card" id="task-calculator">
      <h2 style="margin-top:0">Capability-Gated Estimated Token Cost Finder</h2>
      <p class="muted small"><strong>Important:</strong> Estimates raw token spend only (USD / 1M tokens, OpenRouter Public API). Actual total cost depends on turn count, retries, output verbosity, and prompt-cache hit rate. Dynamic routers (<code>-1</code>) are always excluded.</p>
      <div class="calc-grid">
        <div>
          <label class="muted small" for="task-preset">Workload &amp; Capability Gate</label><br />
          <select id="task-preset" style="width:100%;margin-top:6px">
            <option value="coding" data-in="18.0" data-out="3.5">AI Coding Agent (Requires: Real-Time + Tools + &ge;64K ctx)</option>
            <option value="autosite" data-in="1.2" data-out="0.4">Automated Data Pipeline (Requires: Real-Time + Structured JSON)</option>
            <option value="translation" data-in="2.5" data-out="2.5">Bulk Translation (1M words / mo &middot; Optional Batch)</option>
            <option value="rag" data-in="12.0" data-out="1.0">RAG Document QA (Requires: Real-Time + &ge;100K ctx)</option>
            <option value="custom" data-in="5.0" data-out="1.0">Custom Real-Time Volume</option>
          </select>
        </div>
        <div>
          <label class="muted small" for="calc-in">Monthly Input Tokens (Millions)</label><br />
          <input id="calc-in" type="number" min="0" step="0.1" value="18.0" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="calc-out">Monthly Output Tokens (Millions)</label><br />
          <input id="calc-out" type="number" min="0" step="0.1" value="3.5" style="width:100%;margin-top:6px" />
        </div>
      </div>
      <div style="margin-top:10px">
        <label class="muted small" style="cursor:pointer"><input id="include-batch" type="checkbox" /> Include asynchronous <code>:batch</code> endpoints (only valid for non-interactive bulk jobs)</label>
      </div>
      <div class="calc-result" id="task-ranking"></div>
    </section>

    {render_ad_slot(cfg, "adsense_slot_top")}

    <section>
      <h2>Verified LLM Pricing Directory (USD / 1M Tokens)</h2>
      <p class="muted small">Displaying {len(active_models)} curated active endpoints out of {changelog.get("latest_total_endpoints", len(active_models))} archived daily (<a href="/about/#selection">see selection methodology</a>).</p>
      <div class="controls">
        <input id="model-search" type="search" placeholder="Search model, provider, or type..." style="flex:1;min-width:220px" />
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
          <thead>
            <tr>
              <th>Model Endpoint</th>
              <th>Pricing Type</th>
              <th>Verified Capabilities</th>
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
      <div class="grid">{compare_links}</div>
      <p><a href="/compare/">Browse all {len(pairs)} same-cohort comparisons &rarr;</a></p>
    </section>

    <section>
      <h2>Curated Self-Hosted AI &amp; Automation Infrastructure</h2>
      <div class="grid">{repo_cards}</div>
      <p><a href="/repos/">Explore all {len(repos)} deployment-scored repositories &rarr;</a> &middot; <a href="/zero-cost-stack/">Zero-Cost Stack Blueprint &rarr;</a></p>
    </section>

    {render_affiliates(cfg)}

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

          const eligible = MODELS.filter(m => {{
            if (!allowBatch && !m.is_realtime) return false;
            if (task === 'coding') return m.is_realtime && m.tools && m.ctx >= 64000;
            if (task === 'autosite') return m.is_realtime && m.structured;
            if (task === 'rag') return m.is_realtime && m.ctx >= 100000;
            return true;
          }});

          const paid = eligible
            .filter(m => m.pricing_type === 'paid' || (allowBatch && m.pricing_type === 'batch'))
            .map(m => ({{ ...m, cost: mIn * m.in + mOut * m.out }}))
            .sort((a, b) => a.cost - b.cost)
            .slice(0, 5);

          const frees = eligible.filter(m => m.pricing_type === 'limited_free' || m.pricing_type === 'free').slice(0, 4);

          let html = '<strong>Lowest Estimated Token Cost (' + eligible.length + ' capability-matched endpoints) for ' + mIn.toFixed(1) + 'M in / ' + mOut.toFixed(1) + 'M out:</strong><ol style="margin:8px 0 10px;padding-left:20px">';
          paid.forEach(m => {{
            const tags = [];
            if (m.pricing_type === 'batch') tags.push('Async Batch');
            if (m.tiered) tags.push('Tiered >ctx surcharge');
            if (m.cache_read !== null) tags.push('Cache read $' + m.cache_read + '/1M');
            const extra = tags.length ? ' &middot; ' + tags.join(' &middot; ') : '';
            html += '<li><a href="/models/' + m.slug + '/"><strong>' + m.name + '</strong></a> &mdash; <strong>Est. $' + m.cost.toFixed(2) + '/mo</strong> <span class="muted small">($' + m.in + ' in / $' + m.out + ' out per 1M' + extra + ')</span></li>';
          }});
          html += '</ol>';
          if (frees.length) {{
            html += '<strong>Rate-Limited Free ($0) Alternatives Matching This Gate (strict RPM/RPD caps apply):</strong> ';
            html += frees.map(m => '<a href="/models/' + m.slug + '/">' + m.name + '</a>').join(' &middot; ');
          }} else {{
            html += '<span class="muted small">No rate-limited free endpoints currently satisfy all capability gates for this workload.</span>';
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
    full = render_layout(
        cfg,
        title=f"{cfg['site_name']} — {cfg['tagline']}",
        description=cfg["description"],
        canonical_path="/",
        body_html=body,
        git_commit=git_commit,
        json_ld=json_ld,
    )
    record_page_and_write("index.html", "/", body, full, page_hashes)


def build_free_hub(
    cfg: dict[str, Any], models: list[dict[str, Any]], git_commit: str, page_hashes: dict[str, str]
) -> None:
    active = [m for m in models if m.get("status", "active") == "active"]
    free_models = [m for m in active if m["pricing_type"] in ("limited_free", "free")]
    routers = [m for m in active if m["pricing_type"] in ("free_router", "variable")]

    rows = "".join(
        f'<tr><td><a href="/models/{e(m["slug"])}/"><strong>{e(m["name"])}</strong></a><br><span class="muted small">{e(m["id"])}</span></td>'
        f'<td>{e(m["provider"])}</td>'
        f'<td>{e(fmt_ctx(m["context_length"]))}</td>'
        f'<td class="muted small">{"Tools" if (m.get("capabilities") or {}).get("tools") else "Text"}{" · JSON" if (m.get("capabilities") or {}).get("structured_outputs") else ""}</td>'
        f'<td>{pricing_badge(m)}</td></tr>'
        for m in free_models
    )

    router_rows = "".join(
        f'<tr><td><a href="/models/{e(m["slug"])}/"><strong>{e(m["name"])}</strong></a></td>'
        f'<td>{pricing_badge(m)}</td>'
        f'<td>{e(fmt_ctx(m["context_length"]))}</td>'
        f'<td class="muted small">{e(m.get("rate_limit_note", ""))}</td></tr>'
        for m in routers
    )

    body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Free LLM APIs</div>
    <h1>Rate-Limited Free LLM APIs ({len(free_models)} Verified Text Endpoints)</h1>
    <p class="muted">Zero token cost does <strong>not</strong> mean unlimited. OpenRouter <code>:free</code> tiers typically enforce ~20 requests/minute and 50 requests/day without purchased credits. Dynamic routers (<code>-1</code>) and audio/media endpoints are strictly excluded.</p>
    {render_ad_slot(cfg, "adsense_slot_top")}
    <div class="table-wrap">
      <table>
        <thead><tr><th>Model Endpoint</th><th>Provider</th><th>Context</th><th>Capabilities</th><th>Pricing Tier</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <h2>Routers Separated for Transparency</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Router Endpoint</th><th>Pricing Type</th><th>Context</th><th>Billing &amp; Limits</th></tr></thead>
        <tbody>{router_rows}</tbody>
      </table>
    </div>
    """
    full = render_layout(
        cfg,
        title=f"Verified Rate-Limited Free LLM APIs ({utc_today()[:4]}) — {cfg['site_name']}",
        description=f"Daily verified directory of {len(free_models)} rate-limited $0 LLM text APIs, cleanly separated from variable-priced routers.",
        canonical_path="/free-llm-apis/",
        body_html=body,
        git_commit=git_commit,
    )
    record_page_and_write("free-llm-apis/index.html", "/free-llm-apis/", body, full, page_hashes)


def build_price_changes_page(
    cfg: dict[str, Any],
    changelog: dict[str, Any],
    published_slugs: set[str],
    git_commit: str,
    page_hashes: dict[str, str],
) -> None:
    events = changelog.get("events", [])
    if events:
        row_items = []
        for ev in events[:100]:
            slug = ev.get("slug", "")
            name_cell = (
                f'<a href="/models/{e(slug)}/"><strong>{e(ev["name"])}</strong></a>'
                if slug in published_slugs
                else f'<strong>{e(ev["name"])}</strong>'
            )
            row_items.append(
                f'<tr><td>{e(ev["date"])}</td>'
                f'<td><span class="badge {"badge-free" if ev["type"] in ("new_free", "price_drop") else "badge-warn"}">{e(ev["type"].replace("_", " ").title())}</span></td>'
                f'<td>{name_cell}<br><span class="muted small">{e(ev["model_id"])}</span></td>'
                f'<td class="muted small">{e(ev["detail"])}</td></tr>'
            )
        rows = "".join(row_items)
        table_html = (
            '<div class="table-wrap"><table><thead><tr><th>Date</th><th>Change Type</th><th>Model</th><th>Details</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
        )
    else:
        table_html = (
            '<div class="card"><h3>Baseline Price Archive Active</h3>'
            f'<p class="muted">Daily archiving began on <strong>{e(changelog.get("first_snapshot_date", utc_today()))}</strong> '
            f'covering all <strong>{changelog.get("latest_total_endpoints", 0)}</strong> OpenRouter endpoints. '
            "Intraday runs append and deduplicate events without overwriting prior changes.</p></div>"
        )

    body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Price History</div>
    <h1>LLM API Price History &amp; Free-Tier Changelog</h1>
    <p class="muted">Append-only daily archive monitoring {changelog.get("latest_total_endpoints", 0)} endpoints since {e(changelog.get("first_snapshot_date", utc_today()))} ({changelog.get("snapshot_days", 1)} snapshot day(s)).</p>
    {table_html}
    """
    full = render_layout(
        cfg,
        title=f"LLM API Price History & Free-Tier Changelog — {cfg['site_name']}",
        description="Automated append-only changelog of LLM API price drops, hikes, new free endpoints, and tier removals.",
        canonical_path="/price-changes/",
        body_html=body,
        git_commit=git_commit,
    )
    record_page_and_write("price-changes/index.html", "/price-changes/", body, full, page_hashes)


def build_zero_cost_stack_page(
    cfg: dict[str, Any], models: list[dict[str, Any]], repos: list[dict[str, Any]], git_commit: str, page_hashes: dict[str, str]
) -> None:
    free_count = sum(1 for m in models if m.get("status", "active") == "active" and m["pricing_type"] in ("limited_free", "free"))
    docker_repos = [r for r in repos if r.get("has_docker")][:6]
    docker_list = "".join(
        f'<li><a href="/repos/{e(r["slug"])}/"><strong>{e(r["full_name"])}</strong></a> ({e(r.get("category"))}, {e(r.get("license"))}) &mdash; {e(r["description"])}</li>'
        for r in docker_repos
    )
    body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Zero-Cost Stack</div>
    <h1>Zero-Cost Infrastructure &amp; AI Automation Stack Blueprint</h1>
    <p class="muted">Practical decision guide comparing free-tier LLM APIs (no credit card required), static CDN hosting, scheduled automation, and entry-level VPS tiers.</p>

    <h2>Free LLM API Providers Without a Credit Card (2026 Verified Overview)</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Provider</th><th>Credit Card Required?</th><th>Rate Limit &amp; Practical Caveat</th><th>Best Fit</th></tr></thead>
        <tbody>
          <tr><td><strong>Google AI Studio (Gemini API)</strong></td><td>No</td><td>Free RPM/RPD caps; free-tier prompts may be used to improve Google products</td><td>Scheduled non-sensitive batch summarization</td></tr>
          <tr><td><strong>GroqCloud</strong></td><td>No</td><td>Model-specific daily token caps; ultra-fast LPU latency</td><td>Fast structured JSON extraction &amp; fallback</td></tr>
          <tr><td><strong>OpenRouter (<code>:free</code>)</strong></td><td>No</td><td>~20 RPM &amp; ~50 RPD without purchased credits ({free_count} models tracked)</td><td>Unified OpenAI-compatible multi-model testing</td></tr>
          <tr><td><strong>Cloudflare Workers AI</strong></td><td>No</td><td>Daily free Neurons allowance</td><td>Lightweight edge classification &amp; embeddings</td></tr>
        </tbody>
      </table>
    </div>

    <h2>Zero-Marginal-Cost Hosting &amp; Self-Hosting Stack</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Layer</th><th>Recommended Option</th><th>Practical Limit</th></tr></thead>
        <tbody>
          <tr><td><strong>Static Hosting &amp; CDN</strong></td><td>Cloudflare Pages / Workers Static Assets</td><td>Unlimited bandwidth; 20,000 files/site on free plan; commercial-friendly</td></tr>
          <tr><td><strong>Scheduled Compute</strong></td><td>GitHub Actions Cron + Independent External Monitor</td><td>6h max job; pair with external 48h freshness check to detect silent stops</td></tr>
          <tr><td><strong>24/7 Self-Hosted VPS</strong></td><td>2GB–4GB RAM Entry VPS ($3–$6/mo) + Docker Compose</td><td>Ideal for n8n, API gateways, SQLite, and scrapers without GPU</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card" style="margin-top:20px">
      <h2>Docker-Ready Self-Hosted Building Blocks</h2>
      <ul>{docker_list}</ul>
      <p><a href="/repos/">Explore all self-hosted infrastructure profiles &rarr;</a></p>
    </div>
    """
    full = render_layout(
        cfg,
        title=f"Zero-Cost AI & Cloud Infrastructure Stack Blueprint — {cfg['site_name']}",
        description="Verified 2026 guide to free LLM APIs without credit card, rate limits, static CDN hosting, and low-RAM self-hosted VPS stacks.",
        canonical_path="/zero-cost-stack/",
        body_html=body,
        git_commit=git_commit,
    )
    record_page_and_write("zero-cost-stack/index.html", "/zero-cost-stack/", body, full, page_hashes)


def _deterministic_model_faqs(m: dict[str, Any]) -> list[dict[str, str]]:
    ptype = m["pricing_type"]
    p_in = m.get("prompt_price_per_m")
    p_out = m.get("completion_price_per_m")
    caps = m.get("capabilities") or {}
    if m.get("status") == "delisted":
        price_ans = f"This endpoint was marked delisted on {m.get('delisted_at')}. Last observed pricing was {fmt_price(p_in, ptype)} input / {fmt_price(p_out, ptype)} output per 1M tokens."
    elif ptype == "limited_free":
        price_ans = f"Yes, {m['name']} has $0/1M token pricing as of {m['updated_at']}, but it is strictly rate-limited ({m.get('rate_limit_note')})."
    elif ptype == "free":
        price_ans = f"Yes, {m['name']} is listed at $0/1M tokens as of {m['updated_at']}, subject to upstream fair-use concurrency limits."
    elif ptype == "free_router":
        price_ans = f"Yes, {m['name']} routes across currently active $0 rate-limited free endpoints."
    elif ptype == "batch":
        price_ans = f"No. {m['name']} is a paid asynchronous batch endpoint (${p_in}/1M input, ${p_out}/1M output) and is NOT suitable for real-time interactive calls."
    elif ptype == "variable":
        price_ans = f"No. {m['name']} is a dynamic variable-priced router (OpenRouter raw price = -1). You pay the exact rate of whichever downstream paid model handles your prompt."
    elif ptype == "paid" and p_in is not None and p_out is not None:
        price_ans = f"No. {m['name']} is billed at ${p_in}/1M input tokens and ${p_out}/1M output tokens (USD, verified {m['updated_at']})."
    else:
        price_ans = f"{m['name']} uses specialized or unknown billing ({m.get('pricing_label', ptype)}). Verify live rates on OpenRouter."

    return [
        {"q": f"Is {m['name']} free and unlimited via API?", "a": price_ans},
        {
            "q": f"Does {m['name']} support Tool Calling and Structured JSON Outputs?",
            "a": (
                f"According to OpenRouter supported_parameters metadata: "
                f"Tools={'Yes' if caps.get('tools') else 'No'}, "
                f"Structured Outputs={'Yes' if caps.get('structured_outputs') else 'No'}, "
                f"Real-Time Interactive={'Yes' if m.get('is_realtime') else 'No (Async Batch/Non-Realtime)'}, "
                f"Context={m['context_length']:,} tokens."
            ),
        },
    ]


def build_model_pages(
    cfg: dict[str, Any], models: list[dict[str, Any]], git_commit: str, page_hashes: dict[str, str]
) -> list[str]:
    urls: list[str] = []
    active_models = [m for m in models if m.get("status", "active") == "active"]

    for m in models:
        ptype = m["pricing_type"]
        status = m.get("status", "active")
        caps = m.get("capabilities") or {}
        enr = m.get("enrichment") or {}
        best_for = "".join(f"<li>{e(x)}</li>" for x in enr.get("best_for", []))
        watch_outs = "".join(f"<li>{e(x)}</li>" for x in enr.get("watch_outs", []))
        faqs = _deterministic_model_faqs(m)
        faq_html = "".join(
            f'<div class="faq-item"><h3>{e(f["q"])}</h3><p class="muted">{e(f["a"])}</p></div>' for f in faqs
        )

        # Recommend strictly ACTIVE alternatives from same cohort
        same_cat = [other for other in active_models if other["id"] != m["id"] and other["task_category"] == m["task_category"]]
        related = (same_cat if len(same_cat) >= 3 else [other for other in active_models if other["id"] != m["id"]])[:4]
        rel_html = "".join(
            f'<li><a href="/models/{e(r["slug"])}/">{e(r["name"])}</a> ({e(fmt_price(r["prompt_price_per_m"], r["pricing_type"]))} / {e(fmt_ctx(r["context_length"]))} ctx)</li>'
            for r in related
        )

        if ptype in ("limited_free", "free", "free_router", "paid", "batch") and m["prompt_price_per_m"] is not None:
            est_sample = f"${(m['prompt_price_per_m'] * 8 + (m['completion_price_per_m'] or 0) * 2):.2f}"
        else:
            est_sample = "Depends on routed model" if ptype == "variable" else "N/A"

        status_banner = ""
        if status == "delisted":
            status_banner = (
                f'<div class="callout-danger"><strong>Delisted Model Archive:</strong> This endpoint has been absent from OpenRouter for &ge;3 consecutive healthy runs '
                f'(delisted {e(m.get("delisted_at"))}). Last-known pricing is preserved below for historical reference—see active alternatives at the bottom.</div>'
            )
        elif status == "temporarily_missing":
            status_banner = (
                '<div class="callout-warn"><strong>Temporary Availability Warning:</strong> This endpoint was absent from the latest upstream snapshot '
                f'(missing {m.get("missing_days", 1)} day(s)) and is being monitored before marking delisted.</div>'
            )
        elif ptype == "variable":
            status_banner = (
                '<div class="callout-warn"><strong>Dynamic Routing Notice:</strong> This endpoint has no fixed token rate (OpenRouter raw price = -1). '
                'You are billed the underlying downstream model rate for each request.</div>'
            )
        elif ptype == "batch":
            status_banner = (
                '<div class="callout-warn"><strong>Async Batch Endpoint:</strong> Discounted batch pricing applies only to asynchronous batch jobs, not live interactive chat or real-time agent loops.</div>'
            )

        extra_meta = []
        if m.get("cache_read_price_per_m") is not None:
            extra_meta.append(f"Cache Read: <strong>${m['cache_read_price_per_m']}/1M</strong>")
        if m.get("has_tiered_overrides"):
            extra_meta.append("<strong>Tiered Long-Context Surcharge Applies</strong>")
        extra_line = (" &middot; " + " &middot; ".join(extra_meta)) if extra_meta else ""

        body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="/free-llm-apis/">Models</a> / {e(m["name"])}</div>
        {status_banner}
        <div class="hero">
          {pricing_badge(m)}
          <span class="badge">{e(m["task_category"].replace("_", " ").title())}</span>
          {'<span class="badge badge-danger">Delisted</span>' if status == 'delisted' else ''}
          <h1 style="margin-top:10px">{e(m["name"])} — Pricing, Capabilities &amp; Limits</h1>
          <p class="muted">{e(m["description"])}</p>
          <p class="muted small">Endpoint ID: <code>{e(m["id"])}</code> &middot; Source: OpenRouter Public API &middot; Last Seen: {e(m.get("last_seen", m["updated_at"]))}{extra_line}</p>
          <div class="stats">
            <div class="stat"><div class="num">{e(fmt_price(m["prompt_price_per_m"], ptype))}</div><div class="lbl">Input / 1M Tokens</div></div>
            <div class="stat"><div class="num">{e(fmt_price(m["completion_price_per_m"], ptype))}</div><div class="lbl">Output / 1M Tokens</div></div>
            <div class="stat"><div class="num">{m["context_length"]:,}</div><div class="lbl">Max Context Tokens</div></div>
            <div class="stat"><div class="num">{e(est_sample)}</div><div class="lbl">Est. 8M In + 2M Out</div></div>
          </div>
          <p class="muted small" style="margin-top:14px">
            <strong>Verified Capabilities:</strong>
            Real-Time: <strong>{"Yes" if m.get("is_realtime") else "No"}</strong> &middot;
            Tool Calling: <strong>{"Yes" if caps.get("tools") else "No"}</strong> &middot;
            Structured JSON: <strong>{"Yes" if caps.get("structured_outputs") else "No"}</strong> &middot;
            Reasoning: <strong>{"Yes" if caps.get("reasoning") else "No"}</strong>
          </p>
        </div>

        <div class="card">
          <h2>Engineering Decision Notes</h2>
          <p>{e(enr.get("summary", m["description"]))}</p>
          <p class="muted small"><strong>Rate Limit / Billing Policy:</strong> {e(m.get("rate_limit_note", ""))}</p>
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
          <h2>Active Same-Cohort Alternatives</h2>
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
        full = render_layout(
            cfg,
            title=f"{m['name']} API Pricing ({m['pricing_label']}) & Capabilities — {cfg['site_name']}",
            description=f"{m['name']} ({m['pricing_label']}): {fmt_price(m['prompt_price_per_m'], ptype)} input, {fmt_ctx(m['context_length'])} context, Tools={'Yes' if caps.get('tools') else 'No'}.",
            canonical_path=rel_url,
            body_html=body,
            git_commit=git_commit,
            json_ld=json_ld,
        )
        record_page_and_write(f"models/{m['slug']}/index.html", rel_url, body, full, page_hashes)
    return urls


def build_compare_pages(
    cfg: dict[str, Any],
    pairs: list[tuple[dict[str, Any], dict[str, Any], str]],
    git_commit: str,
    page_hashes: dict[str, str],
) -> list[str]:
    urls: list[str] = ["/compare/"]
    cards = []
    for a, b, cohort_label in pairs:
        slug = f"{a['slug']}-vs-{b['slug']}"
        rel_url = f"/compare/{slug}/"
        urls.append(rel_url)

        a_in, a_out = a["prompt_price_per_m"] or 0.0, a["completion_price_per_m"] or 0.0
        b_in, b_out = b["prompt_price_per_m"] or 0.0, b["completion_price_per_m"] or 0.0
        a_caps, b_caps = a.get("capabilities") or {}, b.get("capabilities") or {}

        a_trans, b_trans = a_in * 2.5 + a_out * 2.5, b_in * 2.5 + b_out * 2.5
        a_pipe, b_pipe = a_in * 8.0 + a_out * 2.0, b_in * 8.0 + b_out * 2.0
        a_agent, b_agent = a_in * 18.0 + a_out * 3.5, b_in * 18.0 + b_out * 3.5

        if a_pipe < b_pipe:
            verdict = f"On raw estimated token cost (8M in / 2M out), {a['name']} totals ${a_pipe:.2f} vs ${b_pipe:.2f} for {b['name']} (saving ${b_pipe - a_pipe:.2f})."
        elif b_pipe < a_pipe:
            verdict = f"On raw estimated token cost (8M in / 2M out), {b['name']} totals ${b_pipe:.2f} vs ${a_pipe:.2f} for {a['name']} (saving ${a_pipe - b_pipe:.2f})."
        else:
            larger = a["name"] if a["context_length"] >= b["context_length"] else b["name"]
            verdict = f"Both endpoints share identical listed token cost (${a_pipe:.2f} for 8M in / 2M out). {larger} offers the larger context window ({fmt_ctx(max(a['context_length'], b['context_length']))})."

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
        <h1 style="margin-top:8px">{e(a["name"])} vs {e(b["name"])}: Estimated Token Cost &amp; Capabilities</h1>
        <p class="muted small">OpenRouter Public API (USD / 1M tokens). Raw token estimates exclude retry/verbosity variance.</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Metric / Capability</th><th>{e(a["name"])}</th><th>{e(b["name"])}</th></tr></thead>
            <tbody>
              <tr><td>Provider &amp; Tier</td><td>{e(a["provider"])} &middot; {pricing_badge(a)}</td><td>{e(b["provider"])} &middot; {pricing_badge(b)}</td></tr>
              <tr><td>Context Window</td><td>{a["context_length"]:,} tokens</td><td>{b["context_length"]:,} tokens</td></tr>
              <tr><td>Tool Calling / Structured JSON</td><td>{"Yes" if a_caps.get("tools") else "No"} / {"Yes" if a_caps.get("structured_outputs") else "No"}</td><td>{"Yes" if b_caps.get("tools") else "No"} / {"Yes" if b_caps.get("structured_outputs") else "No"}</td></tr>
              <tr><td>Input / Output per 1M</td><td>{e(fmt_price(a_in, a["pricing_type"]))} / {e(fmt_price(a_out, a["pricing_type"]))}</td><td>{e(fmt_price(b_in, b["pricing_type"]))} / {e(fmt_price(b_out, b["pricing_type"]))}</td></tr>
              <tr><td>Est. Bulk Translation (2.5M in / 2.5M out)</td><td><strong>${a_trans:.2f}</strong></td><td><strong>${b_trans:.2f}</strong></td></tr>
              <tr><td>Est. Auto-Site Pipeline (8M in / 2M out)</td><td><strong>${a_pipe:.2f}</strong></td><td><strong>${b_pipe:.2f}</strong></td></tr>
              <tr><td>Est. Coding Agent Loop (18M in / 3.5M out)</td><td><strong>${a_agent:.2f}</strong></td><td><strong>${b_agent:.2f}</strong></td></tr>
              <tr><td>Full Specs</td><td><a href="/models/{e(a['slug'])}/">View {e(a["name"])} &rarr;</a></td><td><a href="/models/{e(b['slug'])}/">View {e(b["name"])} &rarr;</a></td></tr>
            </tbody>
          </table>
        </div>
        <div class="card" style="margin-top:20px">
          <h2>Estimated Token Cost Summary</h2>
          <p>{e(verdict)}</p>
        </div>
        {render_ad_slot(cfg, "adsense_slot_in_article")}
        """
        full = render_layout(
            cfg,
            title=f"{a['name']} vs {b['name']} — Estimated Token Cost & Capabilities | {cfg['site_name']}",
            description=verdict,
            canonical_path=rel_url,
            body_html=body,
            git_commit=git_commit,
        )
        record_page_and_write(f"compare/{slug}/index.html", rel_url, body, full, page_hashes)

    hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Comparisons</div>
    <h1>Same-Cohort Real-Time LLM Comparisons ({len(pairs)} Matchups)</h1>
    <p class="muted">Strictly cross-provider, active real-time text LLM matchups—routers, async batch endpoints, and audio/media models are excluded.</p>
    <div class="grid">{"".join(cards)}</div>
    """
    hub_full = render_layout(
        cfg,
        title=f"Same-Cohort LLM API Comparisons — {cfg['site_name']}",
        description="Estimated workload token cost and capability comparisons between comparable real-time LLM APIs.",
        canonical_path="/compare/",
        body_html=hub_body,
        git_commit=git_commit,
    )
    record_page_and_write("compare/index.html", "/compare/", hub_body, hub_full, page_hashes)
    return urls


def build_repo_pages(
    cfg: dict[str, Any], repos: list[dict[str, Any]], git_commit: str, page_hashes: dict[str, str]
) -> list[str]:
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
        lic_tier = r.get("license_tier", "unknown")
        docker_badge = '<span class="badge badge-free">Docker Signal</span>' if r.get("has_docker") else ""
        delta_txt = f" (+{r['star_delta']} today)" if r.get("star_delta", 0) > 0 else ""

        cards.append(
            f'<div class="card repo-card" data-cat="{e(cat)}" data-docker="{"1" if r.get("has_docker") else "0"}" data-perm="{"1" if lic_tier == "permissive" else "0"}">'
            f'<span class="badge">{e(cat)}</span> '
            f'<span class="badge {"badge-free" if lic_tier == "permissive" else "badge-warn"}">{e(lic)}</span> {docker_badge}'
            f'<div style="margin-top:8px"><a href="{e(rel_url)}"><strong>{e(r["full_name"])}</strong></a></div>'
            f'<p class="muted small" style="margin:6px 0">{e(r["description"])}</p>'
            f'<div class="muted small">&#9733; {r["stars"]:,}{e(delta_txt)} &middot; {e(r.get("deployment_tier", "VPS"))} &middot; {e(r.get("maintenance_status", "Active"))}</div></div>'
        )

        body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="/repos/">Self-Hosted Infra</a> / {e(r["full_name"])}</div>
        <div class="hero">
          <span class="badge">{e(cat)}</span>
          <span class="badge {'badge-free' if lic_tier == 'permissive' else 'badge-warn'}">License: {e(lic)}</span>
          {docker_badge}
          <h1 style="margin-top:10px">{e(r["full_name"])} — Self-Hosting &amp; License Profile</h1>
          <p class="muted">{e(r["description"])}</p>
          <p><a class="btn" href="{e(r["html_url"])}" target="_blank" rel="noopener noreferrer">Open on GitHub &rarr;</a></p>
          <div class="stats">
            <div class="stat"><div class="num">{r["stars"]:,}</div><div class="lbl">GitHub Stars{e(delta_txt)}</div></div>
            <div class="stat"><div class="num">{e(r.get("deployment_tier", "VPS"))}</div><div class="lbl">Deployment Tier</div></div>
            <div class="stat"><div class="num">{e(r.get("maintenance_status", "Active"))}</div><div class="lbl">Maintenance Health</div></div>
            <div class="stat"><div class="num">{e(lic_tier.title())}</div><div class="lbl">License Category</div></div>
          </div>
          <p class="muted small" style="margin-top:12px"><strong>License Note:</strong> {e(r.get("license_note", "Review LICENSE file in repository."))}</p>
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
        full = render_layout(
            cfg,
            title=f"{r['full_name']} Self-Hosting Profile ({lic}, {cat}) — {cfg['site_name']}",
            description=f"{r['full_name']} ({lic}, {r.get('deployment_tier')}, {r.get('maintenance_status')}): {r['description']}",
            canonical_path=rel_url,
            body_html=body,
            git_commit=git_commit,
        )
        record_page_and_write(f"repos/{r['slug']}/index.html", rel_url, body, full, page_hashes)

    cat_opts = "".join(f'<option value="{e(c)}">{e(c)}</option>' for c in categories)
    hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Self-Hosted Infra</div>
    <h1>Curated Self-Hosted AI &amp; Automation Infrastructure ({len(repos)} Projects)</h1>
    <p class="muted">Filtered to AI gateways, local LLM runtimes, workflow automation, and self-hosted infra. Note: even permissive licenses (MIT/Apache) carry attribution obligations—always review the repository LICENSE file.</p>
    <div class="controls">
      <select id="repo-cat"><option value="all">All Categories</option>{cat_opts}</select>
      <label style="cursor:pointer"><input id="repo-docker" type="checkbox" /> Docker Signal Only</label>
      <label style="cursor:pointer"><input id="repo-perm" type="checkbox" /> Permissive Licenses Only (MIT/Apache/BSD)</label>
    </div>
    <div class="grid" id="repo-grid">{"".join(cards)}</div>
    <script>
      (function() {{
        const catEl = document.getElementById('repo-cat');
        const dEl = document.getElementById('repo-docker');
        const pEl = document.getElementById('repo-perm');
        const items = Array.from(document.querySelectorAll('.repo-card'));
        function filter() {{
          const cat = catEl.value;
          const needD = dEl.checked;
          const needP = pEl.checked;
          items.forEach(el => {{
            const ok = (cat === 'all' || el.dataset.cat === cat) && (!needD || el.dataset.docker === '1') && (!needP || el.dataset.perm === '1');
            el.style.display = ok ? '' : 'none';
          }});
        }}
        [catEl, dEl, pEl].forEach(el => el.addEventListener('change', filter));
      }})();
    </script>
    """
    hub_full = render_layout(
        cfg,
        title=f"Curated Self-Hosted AI & Automation Infra Directory — {cfg['site_name']}",
        description="Deployment-scored directory of self-hosted AI gateways, local LLM runtimes, and workflow automation engines.",
        canonical_path="/repos/",
        body_html=hub_body,
        git_commit=git_commit,
    )
    record_page_and_write("repos/index.html", "/repos/", hub_body, hub_full, page_hashes)
    return urls


def build_static_trust_pages(
    cfg: dict[str, Any], selection_audit: dict[str, Any], git_commit: str, page_hashes: dict[str, str]
) -> list[str]:
    email = cfg["contact_email"]
    name = cfg["site_name"]
    domain = cfg["domain"]
    breakdown = selection_audit.get("raw_breakdown_by_pricing_type") or {}
    breakdown_rows = "".join(f"<li><code>{e(k)}</code>: {v} endpoints</li>" for k, v in sorted(breakdown.items()))

    pages = [
        (
            "/about/",
            f"About & Methodology — {name}",
            f"<h1>About {e(name)} &amp; Data Methodology</h1>"
            f"<div class='card'><p>{e(name)} ({e(domain)}) is an automated Zero-Cost Infrastructure &amp; AI Automation Radar.</p>"
            f"<h2>8-State Pricing Classification Rules</h2>"
            f"<ul>"
            f"<li><strong>Limited Free Tier (<code>limited_free</code>):</strong> Verified <code>$0/$0</code> <code>:free</code> text endpoints with explicit rate-limit disclaimers.</li>"
            f"<li><strong>Async Batch (<code>batch</code>):</strong> Discounted <code>:batch</code> endpoints excluded from real-time agent recommendations.</li>"
            f"<li><strong>Variable (<code>variable</code>):</strong> Dynamic routers (OpenRouter <code>-1</code>) never labeled free or included in $0 calculations.</li>"
            f"<li><strong>Delisted Lifecycle:</strong> Endpoints absent for &ge;3 consecutive healthy runs keep their URL permanently with historical last-known pricing and active alternatives.</li>"
            f"</ul>"
            f"<h2 id='selection'>Why {selection_audit.get('selected_for_display', 140)} Active Pages Out of {selection_audit.get('total_raw_endpoints', 473)} Raw Endpoints?</h2>"
            f"<p class='muted'>{e(selection_audit.get('selection_rationale', ''))}</p>"
            f"<ul>{breakdown_rows}</ul>"
            f"<h2 id='partners'>Advertising &amp; Affiliate Disclosure</h2>"
            f"<p class='muted'>Pages may display automated ads or clearly disclosed cash-commission affiliate links. Rankings are computed programmatically from public APIs and are never paid placements.</p></div>",
        ),
        (
            "/privacy/",
            f"Privacy Policy — {name}",
            f"<h1>Privacy Policy</h1>"
            f"<div class='card'><p>Effective Date: 2026-11-24</p>"
            f"<p>{e(name)} ({e(domain)}) is a static informational site. We do not require accounts, do not collect personal profiles, and do not sell personal data.</p>"
            f"<h2>Cookies &amp; Third-Party Advertising</h2>"
            f"<p>Third-party vendors, including Google, may use cookies to serve ads based on prior visits. Users may opt out via Google Ads Settings.</p>"
            f"<h2>Infrastructure Logs</h2>"
            f"<p>CDN and static hosting layers may process standard request metadata (IP address, user-agent) for security and delivery.</p>"
            f"<h2>Contact</h2><p>Privacy inquiries: <a href='mailto:{e(email)}'>{e(email)}</a>.</p></div>",
        ),
        (
            "/terms/",
            f"Terms of Use — {name}",
            f"<h1>Terms of Use</h1>"
            f"<div class='card'><p>All token cost estimates, pricing snapshots, and license summaries on {e(domain)} are provided &ldquo;as-is&rdquo; for informational comparison only. Always verify live pricing, rate limits, and LICENSE files directly with upstream providers.</p></div>",
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
        full = render_layout(cfg, title=title, description=title, canonical_path=path, body_html=body, git_commit=git_commit)
        record_page_and_write(f"{path.strip('/')}/index.html", path, body, full, page_hashes)
    return urls


def update_page_state_and_build_seo(
    cfg: dict[str, Any],
    all_urls: list[str],
    page_hashes: dict[str, str],
    models: list[dict[str, Any]],
    repos: list[dict[str, Any]],
    changelog: dict[str, Any],
    git_commit: str,
) -> None:
    base = cfg["base_url"].rstrip("/")
    now = utc_now_iso()

    # True semantic lastmod tracking: only bump lastmod when page body hash actually changed!
    state_path = DATA_DIR / "page_state.json"
    prev_state: dict[str, dict[str, str]] = load_json(state_path, default={})
    new_state: dict[str, dict[str, str]] = {}

    url_nodes = []
    for u in sorted(set(all_urls)):
        curr_hash = page_hashes.get(u, "")
        old = prev_state.get(u)
        if old and old.get("hash") == curr_hash and old.get("lastmod"):
            lastmod = old["lastmod"]
        else:
            lastmod = now
        new_state[u] = {"hash": curr_hash, "lastmod": lastmod}
        url_nodes.append(f"  <url><loc>{e(base + u)}</loc><lastmod>{lastmod}</lastmod></url>")

    save_json(state_path, new_state)

    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(url_nodes)
        + "\n</urlset>\n"
    )
    write_page("sitemap.xml", sitemap)
    write_page("robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n")

    feed_items = []
    for m in [x for x in models if x.get("status", "active") == "active"][:20]:
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
        "<p class='muted'>The requested URL was not found. Note that delisted model pages remain archived under /models/.</p>"
        "<p><a class='btn' href='/'>Back to Cost Estimator &rarr;</a></p></div>"
    )
    write_page(
        "404.html",
        render_layout(
            cfg,
            title=f"404 Not Found — {cfg['site_name']}",
            description="Page not found.",
            canonical_path="/404.html",
            body_html=not_found_body,
            git_commit=git_commit,
        ),
    )

    # Machine-readable build & data provenance manifest for external black-box monitors
    version_manifest = {
        "site": cfg["domain"],
        "git_commit": git_commit,
        "built_at_utc": now,
        "total_sitemap_urls": len(set(all_urls)),
        "active_models": sum(1 for m in models if m.get("status", "active") == "active"),
        "delisted_models": sum(1 for m in models if m.get("status") == "delisted"),
        "limited_free_models": sum(1 for m in models if m.get("status", "active") == "active" and m.get("pricing_type") == "limited_free"),
        "batch_models": sum(1 for m in models if m.get("status", "active") == "active" and m.get("pricing_type") == "batch"),
        "snapshot_days": changelog.get("snapshot_days", 1),
    }
    write_page("version.json", json.dumps(version_manifest, indent=2) + "\n")

    headers_txt = (
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "  X-Frame-Options: SAMEORIGIN\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  Cache-Control: public, max-age=3600\n"
        "\n"
        "/version.json\n"
        "  Cache-Control: public, max-age=300\n"
    )
    write_page("CNAME", f"{cfg['domain']}\n")
    write_page("_headers", headers_txt)

    client_id = str(cfg.get("monetization", {}).get("adsense_client_id") or "").strip()
    if client_id:
        pub_id = client_id.replace("ca-", "")
        write_page("ads.txt", f"google.com, {pub_id}, DIRECT, f08c47fec0942fa0\n")


def main() -> None:
    cfg = load_config()
    git_commit = get_git_commit()
    models = load_json(DATA_DIR / "models.json", default=load_json(DATA_DIR / "seed_models.json", default=[]))
    repos = load_json(DATA_DIR / "repos.json", default=load_json(DATA_DIR / "seed_repos.json", default=[]))
    changelog = load_json(
        DATA_DIR / "price_changes.json",
        default={"snapshot_days": 1, "first_snapshot_date": utc_today(), "latest_total_endpoints": len(models), "events": []},
    )
    selection_audit = load_json(DATA_DIR / "selection_audit.json", default={})

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    pairs = select_comparison_pairs(models, int(cfg["pipeline"].get("max_compare_pages", 40)))
    page_hashes: dict[str, str] = {}

    published_slugs = {m["slug"] for m in models}
    all_urls = ["/", "/free-llm-apis/", "/price-changes/", "/zero-cost-stack/"]
    build_home_page(cfg, models, repos, pairs, changelog, git_commit, page_hashes)
    build_free_hub(cfg, models, git_commit, page_hashes)
    build_price_changes_page(cfg, changelog, published_slugs, git_commit, page_hashes)
    build_zero_cost_stack_page(cfg, models, repos, git_commit, page_hashes)
    all_urls.extend(build_model_pages(cfg, models, git_commit, page_hashes))
    all_urls.extend(build_compare_pages(cfg, pairs, git_commit, page_hashes))
    all_urls.extend(build_repo_pages(cfg, repos, git_commit, page_hashes))
    all_urls.extend(build_static_trust_pages(cfg, selection_audit, git_commit, page_hashes))
    update_page_state_and_build_seo(cfg, all_urls, page_hashes, models, repos, changelog, git_commit)

    print(
        f"[build_site] Built {len(set(all_urls))} V2.1 pages (commit={git_commit}, "
        f"same-cohort comparisons={len(pairs)}) into {DIST_DIR}"
    )


if __name__ == "__main__":
    main()
