"""Pure-Python static site generator for SysV Radar (sysv.cn).

Outputs clean, fast, SEO-optimized HTML to `dist/` with:
- Interactive Token Cost Calculator & filterable LLM Pricing Table
- Free LLM APIs hub page (/free-llm-apis/)
- Programmatic Model detail pages (/models/<slug>/) with auto internal links & JSON-LD
- Programmatic Head-to-Head Comparison pages (/compare/<a-vs-b>/)
- Open-Source Infra directory (/repos/ and /repos/<slug>/)
- AdSense-required trust pages (/about/, /privacy/, /terms/, /contact/)
- sitemap.xml, robots.txt, and conditional ads.txt
"""

import html
import json
import shutil
from itertools import combinations
from pathlib import Path
from typing import Any

from scripts.utils import DATA_DIR, DIST_DIR, load_config, load_json, utc_now_iso, utc_today


def e(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)


def fmt_price(price: float) -> str:
    if price == 0:
        return "FREE ($0)"
    if price < 0.01:
        return f"${price:.4f}"
    return f"${price:.2f}"


def fmt_ctx(ctx: int) -> str:
    if ctx >= 1_000_000:
        return f"{ctx / 1_000_000:.1f}M"
    if ctx >= 1_000:
        return f"{ctx // 1_000}K"
    return str(ctx)


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
.wrap { max-width: 1080px; margin: 0 auto; padding: 0 20px; }
header {
  border-bottom: 1px solid var(--border);
  background: rgba(11, 16, 32, 0.92);
  position: sticky; top: 0; z-index: 20; backdrop-filter: blur(8px);
}
.nav { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 0; flex-wrap: wrap; }
.brand { font-weight: 800; font-size: 1.15rem; color: #fff; letter-spacing: 0.3px; }
.brand span { color: var(--accent); }
.nav-links { display: flex; gap: 18px; flex-wrap: wrap; font-size: 0.95rem; }
.nav-links a { color: var(--muted); }
.nav-links a:hover { color: #fff; text-decoration: none; }
main { padding: 30px 0 60px; }
.hero {
  background: linear-gradient(135deg, #131e38 0%, #0e162a 100%);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 28px;
  margin-bottom: 26px;
}
h1 { margin: 0 0 10px; font-size: 1.9rem; line-height: 1.25; }
h2 { margin: 28px 0 14px; font-size: 1.35rem; }
h3 { margin: 18px 0 10px; font-size: 1.1rem; }
.muted { color: var(--muted); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-top: 20px; }
.stat { background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.stat .num { font-size: 1.45rem; font-weight: 700; color: #fff; }
.stat .lbl { font-size: 0.85rem; color: var(--muted); }
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 18px;
}
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 16px; }
.badge {
  display: inline-block; padding: 2px 9px; border-radius: 999px;
  font-size: 0.78rem; font-weight: 600; background: #1e2d4f; color: var(--accent);
}
.badge-free { background: rgba(34, 197, 94, 0.16); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.35); }
.controls { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
input, select {
  background: var(--bg); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; padding: 9px 12px; font-size: 0.95rem;
}
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
table { width: 100%; border-collapse: collapse; font-size: 0.93rem; }
th, td { padding: 12px 14px; text-align: left; border-bottom: 1px solid var(--border); }
th { background: var(--surface-2); color: var(--muted); font-weight: 600; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(56, 189, 248, 0.04); }
.calc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; }
.calc-result {
  margin-top: 16px; padding: 14px 16px; border-radius: 10px;
  background: rgba(56, 189, 248, 0.09); border: 1px solid rgba(56, 189, 248, 0.3);
  font-size: 1.05rem;
}
.btn {
  display: inline-block; background: var(--accent); color: #071221;
  font-weight: 700; padding: 8px 14px; border-radius: 8px; font-size: 0.9rem;
}
.btn:hover { opacity: 0.92; text-decoration: none; }
.ad-slot {
  margin: 22px 0; text-align: center; min-height: 90px;
  border: 1px dashed var(--border); border-radius: 10px; padding: 10px;
  display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 0.85rem;
}
.breadcrumb { font-size: 0.88rem; color: var(--muted); margin-bottom: 14px; }
.faq-item { border-top: 1px solid var(--border); padding: 12px 0; }
.faq-item:first-child { border-top: none; }
footer {
  border-top: 1px solid var(--border);
  padding: 26px 0 40px;
  color: var(--muted);
  font-size: 0.88rem;
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
    affs = cfg.get("monetization", {}).get("affiliates", [])
    if not affs:
        return ""
    cards = []
    for a in affs:
        cards.append(
            f'<div class="card">'
            f'<span class="badge">{e(a.get("badge", "Partner"))}</span>'
            f'<h3>{e(a["name"])}</h3>'
            f'<p class="muted">{e(a["description"])}</p>'
            f'<a class="btn" href="{e(a["url"])}" rel="sponsored noopener">{e(a["cta"])} &rarr;</a>'
            f"</div>"
        )
    return '<h2>Recommended Infrastructure Resources</h2><div class="grid">' + "".join(cards) + "</div>"


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
        <a href="/">Pricing &amp; Calculator</a>
        <a href="/free-llm-apis/">Free LLM APIs</a>
        <a href="/compare/">Comparisons</a>
        <a href="/repos/">Open-Source Infra</a>
        <a href="/about/">About</a>
      </nav>
    </div>
  </header>
  <main class="wrap">
    {body_html}
  </main>
  <footer>
    <div class="wrap">
      <div class="footer-links">
        <a href="/about/">About</a>
        <a href="/privacy/">Privacy Policy</a>
        <a href="/terms/">Terms of Use</a>
        <a href="/contact/">Contact</a>
        <a href="/feed.xml">RSS Feed</a>
        <a href="/sitemap.xml">Sitemap</a>
      </div>
      <div>&copy; {today[:4]} {e(cfg["site_name"])} ({e(cfg["domain"])}). Automated daily snapshot &middot; Last built {today} UTC.</div>
    </div>
  </footer>
</body>
</html>
"""


def write_page(rel_path: str, content: str) -> None:
    out = DIST_DIR / rel_path.lstrip("/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")


def build_home_page(cfg: dict[str, Any], models: list[dict[str, Any]], repos: list[dict[str, Any]], pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> None:
    free_count = sum(1 for m in models if m["is_free"])
    providers_count = len({m["provider"] for m in models})

    rows = []
    options = []
    for m in models:
        badge = '<span class="badge badge-free">FREE</span>' if m["is_free"] else f'<span class="badge">{e(m["provider"])}</span>'
        rows.append(
            f'<tr data-free="{"1" if m["is_free"] else "0"}" '
            f'data-provider="{e(m["provider"].lower())}" '
            f'data-price="{m["prompt_price_per_m"] + m["completion_price_per_m"]}" '
            f'data-ctx="{m["context_length"]}" '
            f'data-search="{e((m["name"] + " " + m["provider"] + " " + m["id"]).lower())}">'
            f'<td><a href="/models/{e(m["slug"])}/"><strong>{e(m["name"])}</strong></a><br><span class="muted" style="font-size:0.82rem">{e(m["id"])}</span></td>'
            f'<td>{badge}</td>'
            f'<td>{e(fmt_ctx(m["context_length"]))}</td>'
            f'<td>{e(fmt_price(m["prompt_price_per_m"]))}</td>'
            f'<td>{e(fmt_price(m["completion_price_per_m"]))}</td>'
            f"</tr>"
        )
        options.append(
            f'<option value="{e(m["slug"])}" data-in="{m["prompt_price_per_m"]}" data-out="{m["completion_price_per_m"]}">'
            f'{e(m["name"])} ({e(fmt_price(m["prompt_price_per_m"]))} / {e(fmt_price(m["completion_price_per_m"]))})'
            f"</option>"
        )

    compare_links = "".join(
        f'<div class="card"><a href="/compare/{e(a["slug"])}-vs-{e(b["slug"])}/"><strong>{e(a["name"])} vs {e(b["name"])}</strong></a>'
        f'<p class="muted" style="margin:6px 0 0;font-size:0.88rem">Compare context length ({fmt_ctx(a["context_length"])} vs {fmt_ctx(b["context_length"])}) &amp; token pricing.</p></div>'
        for a, b in pairs[:6]
    )

    repo_cards = "".join(
        f'<div class="card"><a href="/repos/{e(r["slug"])}/"><strong>{e(r["full_name"])}</strong></a> '
        f'<span class="badge">{e(r["language"])}</span>'
        f'<p class="muted" style="margin:8px 0 4px;font-size:0.9rem">{e(r["description"])}</p>'
        f'<div class="muted" style="font-size:0.83rem">&#9733; {r["stars"]:,} stars &middot; Updated {e(r["updated_at"])}</div></div>'
        for r in repos[:4]
    )

    body = f"""
    <section class="hero">
      <span class="badge badge-free">Updated Daily &middot; {utc_today()}</span>
      <h1>LLM API Pricing, Free-Tier Radar &amp; Token Cost Calculator</h1>
      <p class="muted">{e(cfg["description"])}</p>
      <div class="stats">
        <div class="stat"><div class="num">{len(models)}</div><div class="lbl">Tracked LLM Endpoints</div></div>
        <div class="stat"><div class="num">{free_count}</div><div class="lbl">Zero-Cost Free Models</div></div>
        <div class="stat"><div class="num">{providers_count}</div><div class="lbl">AI Providers Monitored</div></div>
        <div class="stat"><div class="num">{len(repos)}</div><div class="lbl">Open-Source Infra Repos</div></div>
      </div>
    </section>

    <section class="card" id="calculator">
      <h2 style="margin-top:0">Interactive LLM Token Cost Calculator</h2>
      <p class="muted">Estimate monthly API spend before shipping your automation workflow.</p>
      <div class="calc-grid">
        <div>
          <label class="muted" for="calc-model">Model</label><br />
          <select id="calc-model" style="width:100%;margin-top:6px">{"".join(options)}</select>
        </div>
        <div>
          <label class="muted" for="calc-in">Input Tokens / Day (in Millions)</label><br />
          <input id="calc-in" type="number" min="0" step="0.1" value="1.0" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted" for="calc-out">Output Tokens / Day (in Millions)</label><br />
          <input id="calc-out" type="number" min="0" step="0.1" value="0.25" style="width:100%;margin-top:6px" />
        </div>
      </div>
      <div class="calc-result" id="calc-output">Daily cost: <strong>$0.00</strong> &middot; Monthly (30d): <strong>$0.00</strong></div>
    </section>

    {render_ad_slot(cfg, "adsense_slot_top")}

    <section>
      <h2>Live LLM Pricing &amp; Free-Tier Directory</h2>
      <div class="controls">
        <input id="model-search" type="search" placeholder="Search model or provider (e.g. Gemini, DeepSeek, Llama)..." style="flex:1;min-width:240px" />
        <select id="model-sort" aria-label="Sort models">
          <option value="default">Sort: Featured (Free first)</option>
          <option value="price-asc">Price: Lowest first</option>
          <option value="ctx-desc">Context: Largest first</option>
        </select>
        <label style="cursor:pointer"><input id="only-free" type="checkbox" /> Free models only ($0)</label>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Tier / Provider</th>
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
      <h2>Popular Head-to-Head Model Comparisons</h2>
      <div class="grid">{compare_links}</div>
      <p><a href="/compare/">Browse all {len(pairs)} comparisons &rarr;</a></p>
    </section>

    <section>
      <h2>Trending Self-Hosted &amp; AI Infrastructure Repos</h2>
      <div class="grid">{repo_cards}</div>
      <p><a href="/repos/">Explore all {len(repos)} tracked open-source repositories &rarr;</a></p>
    </section>

    {render_affiliates(cfg)}

    <script>
      (function() {{
        const sel = document.getElementById('calc-model');
        const inEl = document.getElementById('calc-in');
        const outEl = document.getElementById('calc-out');
        const resEl = document.getElementById('calc-output');
        function updateCalc() {{
          const opt = sel.options[sel.selectedIndex];
          const pIn = parseFloat(opt.dataset.in || '0');
          const pOut = parseFloat(opt.dataset.out || '0');
          const mIn = Math.max(0, parseFloat(inEl.value || '0'));
          const mOut = Math.max(0, parseFloat(outEl.value || '0'));
          const daily = mIn * pIn + mOut * pOut;
          const monthly = daily * 30;
          resEl.innerHTML = 'Daily cost: <strong>$' + daily.toFixed(4) + '</strong> &middot; Monthly (30d): <strong>$' + monthly.toFixed(2) + '</strong>';
        }}
        [sel, inEl, outEl].forEach(el => el.addEventListener('input', updateCalc));
        updateCalc();

        const searchEl = document.getElementById('model-search');
        const freeEl = document.getElementById('only-free');
        const sortEl = document.getElementById('model-sort');
        const tbody = document.getElementById('model-tbody');
        const origRows = Array.from(tbody.querySelectorAll('tr'));
        function filterAndSortRows() {{
          const q = (searchEl.value || '').toLowerCase().trim();
          const onlyFree = freeEl.checked;
          const mode = sortEl.value;
          let list = origRows.slice();
          if (mode === 'price-asc') {{
            list.sort((a, b) => parseFloat(a.dataset.price) - parseFloat(b.dataset.price));
          }} else if (mode === 'ctx-desc') {{
            list.sort((a, b) => parseInt(b.dataset.ctx, 10) - parseInt(a.dataset.ctx, 10));
          }}
          list.forEach(tr => {{
            const matchQ = !q || tr.dataset.search.includes(q);
            const matchF = !onlyFree || tr.dataset.free === '1';
            tr.style.display = (matchQ && matchF) ? '' : 'none';
            tbody.appendChild(tr);
          }});
        }}
        searchEl.addEventListener('input', filterAndSortRows);
        freeEl.addEventListener('change', filterAndSortRows);
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
            title=f"{cfg['site_name']} — Free LLM APIs, Token Pricing & Cost Calculator",
            description=cfg["description"],
            canonical_path="/",
            body_html=body,
            json_ld=json_ld,
        ),
    )


def build_free_hub(cfg: dict[str, Any], models: list[dict[str, Any]]) -> None:
    free_models = [m for m in models if m["is_free"]]
    rows = "".join(
        f'<tr><td><a href="/models/{e(m["slug"])}/"><strong>{e(m["name"])}</strong></a></td>'
        f'<td>{e(m["provider"])}</td>'
        f'<td>{e(fmt_ctx(m["context_length"]))}</td>'
        f'<td>{e(m.get("modality", "text->text"))}</td>'
        f'<td><span class="badge badge-free">$0 / 1M</span></td></tr>'
        for m in free_models
    )
    body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Free LLM APIs</div>
    <h1>Free LLM APIs Directory ({len(free_models)} Zero-Cost Models)</h1>
    <p class="muted">Updated automatically on {utc_today()}. Use these free-tier endpoints for batch pipelines, prototyping, and fallback routing without burning monthly API budgets.</p>
    {render_ad_slot(cfg, "adsense_slot_top")}
    <div class="table-wrap">
      <table>
        <thead><tr><th>Model</th><th>Provider</th><th>Context</th><th>Modality</th><th>Price</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <div class="card" style="margin-top:24px">
      <h2>Best Practices for Running Free LLM APIs in Production</h2>
      <ul>
        <li><strong>Use scheduled batch jobs instead of real-time chat</strong>: Free tiers enforce strict requests-per-minute limits. Pre-compute summaries once a day and serve static outputs.</li>
        <li><strong>Configure multi-provider fallback</strong>: Route across at least two providers (e.g., Gemini free tier + Groq + OpenRouter :free) so rate limits never break your cron job.</li>
        <li><strong>Cache every successful enrichment</strong>: Store structured JSON outputs in Git or object storage so you never pay twice for the same source record.</li>
      </ul>
    </div>
    """
    write_page(
        "free-llm-apis/index.html",
        render_layout(
            cfg,
            title=f"Free LLM APIs Directory ({utc_today()[:4]}) — {cfg['site_name']}",
            description=f"Daily updated list of {len(free_models)} free LLM API models with context lengths, modalities, and rate-limit survival tips.",
            canonical_path="/free-llm-apis/",
            body_html=body,
        ),
    )


def build_model_pages(cfg: dict[str, Any], models: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for idx, m in enumerate(models):
        enr = m.get("enrichment") or {}
        best_for = "".join(f"<li>{e(x)}</li>" for x in enr.get("best_for", []))
        watch_outs = "".join(f"<li>{e(x)}</li>" for x in enr.get("watch_outs", []))
        faqs = enr.get("faq", [])
        faq_html = "".join(
            f'<div class="faq-item"><h3>{e(f["q"])}</h3><p class="muted">{e(f["a"])}</p></div>' for f in faqs
        )

        # Automatic internal links to related models
        related = [other for j, other in enumerate(models) if j != idx][:4]
        rel_html = "".join(
            f'<li><a href="/models/{e(r["slug"])}/">{e(r["name"])}</a> ({e(fmt_price(r["prompt_price_per_m"]))} in / {e(fmt_ctx(r["context_length"]))} ctx)</li>'
            for r in related
        )

        cost_10m_in = m["prompt_price_per_m"] * 10
        cost_2m_out = m["completion_price_per_m"] * 2
        total_sample = cost_10m_in + cost_2m_out

        body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="/free-llm-apis/">Models</a> / {e(m["name"])}</div>
        <div class="hero">
          <span class="badge {'badge-free' if m['is_free'] else ''}">{'FREE TIER ($0)' if m['is_free'] else e(m['provider'])}</span>
          <h1 style="margin-top:10px">{e(m["name"])} API Pricing &amp; Specs</h1>
          <p class="muted">{e(m["description"])}</p>
          <div class="stats">
            <div class="stat"><div class="num">{e(fmt_price(m["prompt_price_per_m"]))}</div><div class="lbl">Input / 1M Tokens</div></div>
            <div class="stat"><div class="num">{e(fmt_price(m["completion_price_per_m"]))}</div><div class="lbl">Output / 1M Tokens</div></div>
            <div class="stat"><div class="num">{m["context_length"]:,}</div><div class="lbl">Max Context Tokens</div></div>
            <div class="stat"><div class="num">${total_sample:.2f}</div><div class="lbl">Est. 10M In + 2M Out</div></div>
          </div>
        </div>

        <div class="card">
          <h2>Technical Summary</h2>
          <p>{e(enr.get("summary", m["description"]))}</p>
          <div class="grid">
            <div><h3>Best Suited For</h3><ul>{best_for}</ul></div>
            <div><h3>Engineering Watch-Outs</h3><ul>{watch_outs}</ul></div>
          </div>
        </div>

        {render_ad_slot(cfg, "adsense_slot_in_article")}

        <div class="card">
          <h2>Frequently Asked Questions</h2>
          {faq_html}
        </div>

        <div class="card">
          <h2>Explore Related Models</h2>
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
                title=f"{m['name']} API Pricing, Context Window & Free Tier ({utc_today()[:4]}) — {cfg['site_name']}",
                description=f"Live pricing for {m['name']}: {fmt_price(m['prompt_price_per_m'])} input, {fmt_price(m['completion_price_per_m'])} output, {fmt_ctx(m['context_length'])} context.",
                canonical_path=rel_url,
                body_html=body,
                json_ld=json_ld if faqs else None,
            ),
        )
    return urls


def select_comparison_pairs(models: list[dict[str, Any]], max_pairs: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    top = models[: min(len(models), 12)]
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for a, b in combinations(top, 2):
        pairs.append((a, b))
        if len(pairs) >= max_pairs:
            break
    return pairs


def build_compare_pages(
    cfg: dict[str, Any], pairs: list[tuple[dict[str, Any], dict[str, Any]]]
) -> list[str]:
    urls: list[str] = ["/compare/"]
    cards = []
    for a, b in pairs:
        slug = f"{a['slug']}-vs-{b['slug']}"
        rel_url = f"/compare/{slug}/"
        urls.append(rel_url)
        cards.append(
            f'<div class="card"><a href="{e(rel_url)}"><strong>{e(a["name"])} vs {e(b["name"])}</strong></a>'
            f'<p class="muted" style="margin:6px 0 0;font-size:0.88rem">'
            f'Input: {e(fmt_price(a["prompt_price_per_m"]))} vs {e(fmt_price(b["prompt_price_per_m"]))} &middot; '
            f'Context: {e(fmt_ctx(a["context_length"]))} vs {e(fmt_ctx(b["context_length"]))}</p></div>'
        )

        a_total = a["prompt_price_per_m"] * 8 + a["completion_price_per_m"] * 2
        b_total = b["prompt_price_per_m"] * 8 + b["completion_price_per_m"] * 2
        if a_total < b_total:
            verdict = f"{a['name']} is cheaper for a standard 8M input / 2M output workload (${a_total:.2f} vs ${b_total:.2f})."
        elif b_total < a_total:
            verdict = f"{b['name']} is cheaper for a standard 8M input / 2M output workload (${b_total:.2f} vs ${a_total:.2f})."
        else:
            verdict = f"Both models have identical estimated token cost (${a_total:.2f}) for an 8M input / 2M output workload."

        body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="/compare/">Comparisons</a> / {e(a["name"])} vs {e(b["name"])}</div>
        <h1>{e(a["name"])} vs {e(b["name"])}: Pricing &amp; Context Comparison</h1>
        <p class="muted">Side-by-side factual comparison updated on {utc_today()}.</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Metric</th><th>{e(a["name"])}</th><th>{e(b["name"])}</th></tr></thead>
            <tbody>
              <tr><td>Provider</td><td>{e(a["provider"])}</td><td>{e(b["provider"])}</td></tr>
              <tr><td>Context Window</td><td>{a["context_length"]:,} tokens</td><td>{b["context_length"]:,} tokens</td></tr>
              <tr><td>Input Price / 1M</td><td>{e(fmt_price(a["prompt_price_per_m"]))}</td><td>{e(fmt_price(b["prompt_price_per_m"]))}</td></tr>
              <tr><td>Output Price / 1M</td><td>{e(fmt_price(a["completion_price_per_m"]))}</td><td>{e(fmt_price(b["completion_price_per_m"]))}</td></tr>
              <tr><td>Est. 8M In + 2M Out</td><td>${a_total:.2f}</td><td>${b_total:.2f}</td></tr>
              <tr><td>Modality</td><td>{e(a.get("modality", "text->text"))}</td><td>{e(b.get("modality", "text->text"))}</td></tr>
              <tr><td>Detail Page</td><td><a href="/models/{e(a['slug'])}/">View Specs &rarr;</a></td><td><a href="/models/{e(b['slug'])}/">View Specs &rarr;</a></td></tr>
            </tbody>
          </table>
        </div>
        <div class="card" style="margin-top:20px">
          <h2>Cost &amp; Selection Verdict</h2>
          <p>{e(verdict)}</p>
        </div>
        {render_ad_slot(cfg, "adsense_slot_in_article")}
        """
        write_page(
            f"compare/{slug}/index.html",
            render_layout(
                cfg,
                title=f"{a['name']} vs {b['name']} Pricing & Specs ({utc_today()[:4]}) — {cfg['site_name']}",
                description=verdict,
                canonical_path=rel_url,
                body_html=body,
            ),
        )

    hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Comparisons</div>
    <h1>LLM Model Comparisons ({len(pairs)} Pairs)</h1>
    <p class="muted">Programmatically updated head-to-head pricing and context window benchmarks.</p>
    <div class="grid">{"".join(cards)}</div>
    """
    write_page(
        "compare/index.html",
        render_layout(
            cfg,
            title=f"LLM API Comparisons — {cfg['site_name']}",
            description="Compare LLM API input/output token pricing and context lengths side-by-side.",
            canonical_path="/compare/",
            body_html=hub_body,
        ),
    )
    return urls


def build_repo_pages(cfg: dict[str, Any], repos: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = ["/repos/"]
    cards = []
    for r in repos:
        rel_url = f"/repos/{r['slug']}/"
        urls.append(rel_url)
        enr = r.get("enrichment") or {}
        best_for = "".join(f"<li>{e(x)}</li>" for x in enr.get("best_for", []))
        watch_outs = "".join(f"<li>{e(x)}</li>" for x in enr.get("watch_outs", []))
        topics = " ".join(f'<span class="badge">{e(t)}</span>' for t in r.get("topics", []))

        cards.append(
            f'<div class="card"><a href="{e(rel_url)}"><strong>{e(r["full_name"])}</strong></a> '
            f'<span class="badge">{e(r["language"])}</span>'
            f'<p class="muted" style="margin:8px 0">{e(r["description"])}</p>'
            f'<div class="muted" style="font-size:0.85rem">&#9733; {r["stars"]:,} stars &middot; Forks: {r["forks"]:,}</div></div>'
        )

        body = f"""
        <div class="breadcrumb"><a href="/">Home</a> / <a href="/repos/">Open-Source Infra</a> / {e(r["full_name"])}</div>
        <div class="hero">
          <span class="badge">{e(r["language"])}</span> {topics}
          <h1 style="margin-top:10px">{e(r["full_name"])}</h1>
          <p class="muted">{e(r["description"])}</p>
          <p><a class="btn" href="{e(r["html_url"])}" target="_blank" rel="noopener noreferrer">Open on GitHub &rarr;</a></p>
          <div class="stats">
            <div class="stat"><div class="num">{r["stars"]:,}</div><div class="lbl">GitHub Stars</div></div>
            <div class="stat"><div class="num">{r["forks"]:,}</div><div class="lbl">Forks</div></div>
            <div class="stat"><div class="num">{e(r["language"])}</div><div class="lbl">Primary Language</div></div>
            <div class="stat"><div class="num">{e(r["updated_at"])}</div><div class="lbl">Last Checked</div></div>
          </div>
        </div>
        <div class="card">
          <h2>Infrastructure Analysis</h2>
          <p>{e(enr.get("summary", r["description"]))}</p>
          <div class="grid">
            <div><h3>Best Suited For</h3><ul>{best_for}</ul></div>
            <div><h3>Deployment Watch-Outs</h3><ul>{watch_outs}</ul></div>
          </div>
        </div>
        {render_ad_slot(cfg, "adsense_slot_in_article")}
        """
        write_page(
            f"repos/{r['slug']}/index.html",
            render_layout(
                cfg,
                title=f"{r['full_name']} — Open-Source Infra Profile | {cfg['site_name']}",
                description=r["description"],
                canonical_path=rel_url,
                body_html=body,
            ),
        )

    hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Open-Source Infra</div>
    <h1>Open-Source AI &amp; DevOps Infrastructure Directory</h1>
    <p class="muted">Daily tracked self-hosted tools, gateways, and automation engines ({len(repos)} repositories).</p>
    <div class="grid">{"".join(cards)}</div>
    """
    write_page(
        "repos/index.html",
        render_layout(
            cfg,
            title=f"Open-Source AI & DevOps Directory — {cfg['site_name']}",
            description="Curated, daily updated directory of high-star open-source LLM, self-hosted, and DevOps infrastructure projects.",
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
            f"About {name}",
            f"<h1>About {e(name)}</h1>"
            f"<div class='card'><p>{e(name)} ({e(domain)}) is an independent automated directory tracking public LLM API pricing, "
            f"zero-cost free-tier endpoints, and open-source developer infrastructure.</p>"
            f"<p>Our data pipeline refreshes daily from public API registries and open-source repositories so engineers can "
            f"estimate token costs and evaluate self-hosted tooling without manual spreadsheet upkeep.</p>"
            f"<h2 id='partners'>Monetization &amp; Affiliate Disclosure</h2>"
            f"<p class='muted'>To keep this directory free, some outbound links may be affiliate links and pages may display "
            f"automated advertisements (such as Google AdSense). Pricing comparisons are generated strictly from public metadata.</p></div>",
        ),
        (
            "/privacy/",
            f"Privacy Policy — {name}",
            f"<h1>Privacy Policy</h1>"
            f"<div class='card'><p>Effective Date: {utc_today()}</p>"
            f"<p>{e(name)} ({e(domain)}) is a static informational website. We do not require user accounts, do not collect "
            f"personal profiles, and do not sell personal data.</p>"
            f"<h2>Cookies &amp; Third-Party Advertising</h2>"
            f"<p>Third-party vendors, including Google, may use cookies to serve ads based on a user's prior visits to this "
            f"website or other websites. Users may opt out of personalized advertising by visiting Google Ads Settings.</p>"
            f"<h2>Server Logs</h2>"
            f"<p>Our hosting infrastructure (Cloudflare) may process standard technical request metadata (such as IP address and "
            f"user-agent) strictly for security, DDoS mitigation, and traffic delivery.</p>"
            f"<h2>Contact</h2><p>For privacy inquiries, email <a href='mailto:{e(email)}'>{e(email)}</a>.</p></div>",
        ),
        (
            "/terms/",
            f"Terms of Use — {name}",
            f"<h1>Terms of Use</h1>"
            f"<div class='card'><p>All pricing data, token estimates, and repository summaries on {e(domain)} are provided "
            f"for informational purposes only on an &ldquo;as-is&rdquo; basis. Upstream API providers may change pricing, context windows, "
            f"or free-tier availability at any time without notice.</p>"
            f"<p>Always verify current rates on the official provider billing page before deploying high-volume production traffic.</p></div>",
        ),
        (
            "/contact/",
            f"Contact — {name}",
            f"<h1>Contact {e(name)}</h1>"
            f"<div class='card'><p>For corrections, data source suggestions, or compliance requests regarding {e(domain)}, "
            f"please reach out via email: <a href='mailto:{e(email)}'>{e(email)}</a>.</p></div>",
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

    # RSS 2.0 Feed for automated crawler/aggregator discovery
    feed_items = []
    for m in models[:20]:
        feed_items.append(
            "    <item>\n"
            f"      <title>{e(m['name'])} API Pricing ({e(fmt_price(m['prompt_price_per_m']))})</title>\n"
            f"      <link>{e(base + '/models/' + m['slug'] + '/')}</link>\n"
            f"      <guid>{e(base + '/models/' + m['slug'] + '/')}</guid>\n"
            f"      <description>{e(m['description'])}</description>\n"
            "    </item>"
        )
    for r in repos[:10]:
        feed_items.append(
            "    <item>\n"
            f"      <title>{e(r['full_name'])} ({r['stars']:,} stars)</title>\n"
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

    # Minimal crisp SVG favicon
    favicon = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="14" fill="#0b1020"/>'
        '<text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" '
        'font-family="sans-serif" font-weight="800" font-size="28" fill="#38bdf8">SV</text>'
        "</svg>\n"
    )
    write_page("favicon.svg", favicon)

    # Cloudflare Pages custom 404 page
    not_found_body = (
        "<div class='hero'><h1>404 — Page Not Found</h1>"
        "<p class='muted'>The model or resource you requested may have been renamed or delisted upstream.</p>"
        "<p><a class='btn' href='/'>Back to Pricing &amp; Calculator &rarr;</a></p></div>"
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

    # Cloudflare Pages security & caching headers
    headers_txt = (
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "  X-Frame-Options: SAMEORIGIN\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  Cache-Control: public, max-age=3600\n"
        "\n"
        "/sitemap.xml\n"
        "  Cache-Control: public, max-age=1800\n"
        "\n"
        "/feed.xml\n"
        "  Cache-Control: public, max-age=1800\n"
    )
    write_page("_headers", headers_txt)

    client_id = str(cfg.get("monetization", {}).get("adsense_client_id") or "").strip()
    if client_id:
        pub_id = client_id.replace("ca-", "")
        write_page("ads.txt", f"google.com, {pub_id}, DIRECT, f08c47fec0942fa0\n")


def main() -> None:
    cfg = load_config()
    models = load_json(DATA_DIR / "models.json", default=load_json(DATA_DIR / "seed_models.json", default=[]))
    repos = load_json(DATA_DIR / "repos.json", default=load_json(DATA_DIR / "seed_repos.json", default=[]))

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    pairs = select_comparison_pairs(models, int(cfg["pipeline"].get("max_compare_pages", 40)))

    all_urls = ["/", "/free-llm-apis/"]
    build_home_page(cfg, models, repos, pairs)
    build_free_hub(cfg, models)
    all_urls.extend(build_model_pages(cfg, models))
    all_urls.extend(build_compare_pages(cfg, pairs))
    all_urls.extend(build_repo_pages(cfg, repos))
    all_urls.extend(build_static_trust_pages(cfg))
    build_seo_files(cfg, all_urls, models, repos)

    print(f"[build_site] Built {len(set(all_urls))} pages into {DIST_DIR}")


if __name__ == "__main__":
    main()
