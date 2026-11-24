"""Google Search Console API Automated SEO Operations Collector & Weekly Report.

Strictly complies with Google Search Console API quotas & scopes:
- Uses Search Analytics (`searchanalytics/query`) and optional URL Inspection (`urlInspection/index:inspect`).
- NEVER uses Google's Indexing API (which is restricted to JobPosting & BroadcastEvent).
- Classifies URLs into the 6 operational diagnostic buckets:
  1. Not Discovered (未发现)
  2. Discovered - Not Indexed (已发现未收录)
  3. Crawled - Not Indexed (已抓取未收录)
  4. Indexed - Zero Impressions (已收录无曝光)
  5. Impressions - Zero Clicks (有曝光无点击)
  6. Clicks - Zero Revenue (有点击无收益)

Required env vars (only when connecting your GSC service account / OAuth token):
- GSC_ACCESS_TOKEN  (OAuth2 bearer token with webmasters.readonly scope)
- GSC_SITE_URL      (default: sc-domain:sysv.cn)
"""

import json
import os
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.utils import DATA_DIR, http_post_json, load_json, save_json, utc_today

GSC_DIR = DATA_DIR / "gsc_history"


def classify_page_type(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    if path == "/":
        return "home"
    if path.startswith("/tools/"):
        return "tool"
    if path.startswith("/free-llm-apis"):
        return "free_hub"
    if path.startswith("/models/"):
        return "model_detail"
    if path.startswith("/compare/"):
        return "comparison"
    if path.startswith("/price-changes"):
        return "price_history"
    if path.startswith("/self-hosting") or path.startswith("/repos"):
        return "self_hosting"
    if path.startswith("/data"):
        return "linkable_asset"
    return "other"


def fetch_search_analytics(site_url: str, access_token: str, days: int = 28) -> list[dict[str, Any]]:
    end = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=days + 2)).strftime("%Y-%m-%d")
    encoded_site = urllib.parse.quote(site_url, safe="")
    url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"
    payload = {
        "startDate": start,
        "endDate": end,
        "dimensions": ["query", "page", "date"],
        "rowLimit": 5000,
    }
    resp = http_post_json(url, payload, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    rows = []
    for r in resp.get("rows", []):
        keys = r.get("keys", ["", "", ""])
        rows.append(
            {
                "query": keys[0],
                "page": keys[1],
                "date": keys[2],
                "clicks": int(r.get("clicks", 0)),
                "impressions": int(r.get("impressions", 0)),
                "ctr": round(float(r.get("ctr", 0.0)), 4),
                "position": round(float(r.get("position", 0.0)), 2),
            }
        )
    return rows


def generate_weekly_report(rows: list[dict[str, Any]], published_pages: dict[str, Any], ops_metrics: dict[str, Any]) -> dict[str, Any]:
    by_page: dict[str, dict[str, float]] = defaultdict(lambda: {"clicks": 0, "impressions": 0, "pos_sum": 0.0, "n": 0})
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"clicks": 0, "impressions": 0, "queries": 0})

    for r in rows:
        p = urllib.parse.urlparse(r["page"]).path or "/"
        by_page[p]["clicks"] += r["clicks"]
        by_page[p]["impressions"] += r["impressions"]
        by_page[p]["pos_sum"] += r["position"]
        by_page[p]["n"] += 1

        pt = classify_page_type(r["page"])
        by_type[pt]["clicks"] += r["clicks"]
        by_type[pt]["impressions"] += r["impressions"]
        by_type[pt]["queries"] += 1

    # 6 diagnostic buckets
    buckets = {
        "not_discovered": 0,
        "discovered_not_indexed": 0,
        "crawled_not_indexed": 0,
        "indexed_zero_impressions": 0,
        "impressions_zero_clicks": 0,
        "clicks_zero_revenue": 0,
    }
    latest_rev = sum(float(d.get("confirmed_payable_usd") or 0.0) for d in ops_metrics.get("daily", [])[:28])

    for url_path, meta in published_pages.items():
        if meta.get("index_policy") != "index":
            continue
        stats = by_page.get(url_path)
        insp = meta.get("gsc_inspection_state")
        if stats and stats["clicks"] > 0:
            if latest_rev <= 0:
                buckets["clicks_zero_revenue"] += 1
        elif stats and stats["impressions"] > 0:
            buckets["impressions_zero_clicks"] += 1
        elif insp == "INDEXED":
            buckets["indexed_zero_impressions"] += 1
        elif insp == "CRAWLED_NOT_INDEXED":
            buckets["crawled_not_indexed"] += 1
        elif insp == "DISCOVERED_NOT_INDEXED":
            buckets["discovered_not_indexed"] += 1
        else:
            buckets["not_discovered"] += 1

    return {
        "generated_at": utc_today(),
        "total_rows": len(rows),
        "total_clicks": sum(r["clicks"] for r in rows),
        "total_impressions": sum(r["impressions"] for r in rows),
        "by_page_type": dict(by_type),
        "diagnostic_buckets": buckets,
        "note": "Do NOT modify page titles/structures on tiny sample sizes (<100 impressions over 14+ days).",
    }


def main() -> None:
    token = os.getenv("GSC_ACCESS_TOKEN", "").strip()
    site_url = os.getenv("GSC_SITE_URL", "sc-domain:sysv.cn").strip()
    published = load_json(DATA_DIR / "published_pages.json", default={})
    ops = load_json(DATA_DIR / "ops_metrics.json", default={"daily": []})

    if not token:
        print("[gsc_report] GSC_ACCESS_TOKEN not configured; generating baseline local report without API call.")
        rows: list[dict[str, Any]] = []
    else:
        rows = fetch_search_analytics(site_url, token, days=28)
        GSC_DIR.mkdir(parents=True, exist_ok=True)
        save_json(GSC_DIR / f"{utc_today()}.json", rows)

    report = generate_weekly_report(rows, published, ops)
    save_json(DATA_DIR / "gsc_weekly_report.json", report)
    print(f"[gsc_report] Saved weekly report: clicks={report['total_clicks']}, impressions={report['total_impressions']}, buckets={report['diagnostic_buckets']}")


if __name__ == "__main__":
    main()
