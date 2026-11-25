"""V2.2.1 Google Search Console Automated Operations Collector & Weekly Report.

Fixes V2.2 defects:
1. Permanent auto-refreshing authentication (NO short-lived static access token):
   - Option A (Recommended): Service Account RS256 JWT (`GSC_SERVICE_ACCOUNT_JSON`)
   - Option B: OAuth2 Refresh Token (`GSC_CLIENT_ID` + `GSC_CLIENT_SECRET` + `GSC_REFRESH_TOKEN`)
2. Real URL Inspection API (`urlInspection/index:inspect`) distinguishing:
   `UNKNOWN` (not yet inspected), `API_ERROR`, `INDEXED`, `DISCOVERED_NOT_INDEXED`,
   `CRAWLED_NOT_INDEXED`, `NOT_INDEXED`. Uninspected pages are NEVER labeled "Not Discovered".
3. Exact Search Analytics separation across 3 dimensions (`site`, `page`, `query`),
   exact impression-weighted average position & CTR, explicit date window, and
   `REVENUE_UNKNOWN` when per-page revenue attribution is unavailable (never substitutes
   site-wide revenue for single-page revenue).
"""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from scripts.utils import DATA_DIR, ROOT_DIR, http_post_json, load_json, save_json, utc_now_iso, utc_today

# Commercial/search-query telemetry MUST stay strictly inside private/ (gitignored, never pushed to public repo)
PRIVATE_GSC_DIR = ROOT_DIR / "private" / "gsc"
GSC_DIR = PRIVATE_GSC_DIR / "history"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_service_account_jwt(sa_info: dict[str, Any], scope: str = GSC_SCOPE) -> str:
    """Build RS256-signed JWT assertion for Google Service Account OAuth2 exchange."""
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claim = {
        "iss": sa_info["client_email"],
        "scope": scope,
        "aud": sa_info.get("token_uri") or OAUTH_TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}.{_b64url(json.dumps(claim, separators=(',', ':')).encode())}"
    private_key = serialization.load_pem_private_key(sa_info["private_key"].encode("utf-8"), password=None)
    sig = private_key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(sig)}"


def _post_form(url: str, fields: dict[str, str], timeout: int = 20) -> dict[str, Any]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def obtain_access_token() -> tuple[str | None, str]:
    """Obtain fresh bearer token via Service Account JWT or OAuth2 Refresh Token."""
    sa_raw = os.getenv("GSC_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_raw:
        sa_info = json.loads(sa_raw)
        assertion = build_service_account_jwt(sa_info)
        resp = _post_form(
            sa_info.get("token_uri") or OAUTH_TOKEN_URL,
            {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        )
        return resp["access_token"], "service_account_jwt"

    cid = os.getenv("GSC_CLIENT_ID", "").strip()
    csec = os.getenv("GSC_CLIENT_SECRET", "").strip()
    rtok = os.getenv("GSC_REFRESH_TOKEN", "").strip()
    if cid and csec and rtok:
        resp = _post_form(
            OAUTH_TOKEN_URL,
            {"client_id": cid, "client_secret": csec, "refresh_token": rtok, "grant_type": "refresh_token"},
        )
        return resp["access_token"], "oauth2_refresh_token"

    return None, "unconfigured"


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


def query_search_analytics(
    site_url: str, access_token: str, *, start_date: str, end_date: str, dimensions: list[str], row_limit: int = 5000
) -> list[dict[str, Any]]:
    encoded_site = urllib.parse.quote(site_url, safe="")
    url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"
    payload: dict[str, Any] = {"startDate": start_date, "endDate": end_date, "rowLimit": row_limit}
    if dimensions:
        payload["dimensions"] = dimensions
    resp = http_post_json(url, payload, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    return list(resp.get("rows") or [])


def map_inspection_result(resp: dict[str, Any]) -> dict[str, str]:
    """Normalize Google URL Inspection API response into explicit audit states."""
    idx = (resp.get("inspectionResult") or {}).get("indexStatusResult") or {}
    verdict = str(idx.get("verdict") or "").upper()
    coverage = str(idx.get("coverageState") or "")
    cov_low = coverage.lower()

    if verdict == "PASS":
        state = "INDEXED"
    elif "discovered" in cov_low and "not indexed" in cov_low:
        state = "DISCOVERED_NOT_INDEXED"
    elif "crawled" in cov_low and "not indexed" in cov_low:
        state = "CRAWLED_NOT_INDEXED"
    elif verdict in ("FAIL", "NEUTRAL", "PARTIAL") or coverage:
        state = "NOT_INDEXED"
    else:
        state = "UNKNOWN"

    return {
        "state": state,
        "verdict": verdict or "UNKNOWN",
        "coverage_state": coverage or "UNKNOWN",
        "last_crawl_time": str(idx.get("lastCrawlTime") or ""),
        "inspected_at": utc_now_iso(),
    }


def inspect_single_url(site_url: str, page_url: str, access_token: str) -> dict[str, str]:
    try:
        resp = http_post_json(
            "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
            {"inspectionUrl": page_url, "siteUrl": site_url},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=25,
            retries=1,
        )
        return map_inspection_result(resp)
    except Exception as err:
        return {
            "state": "API_ERROR",
            "verdict": "API_ERROR",
            "coverage_state": str(err)[:180],
            "last_crawl_time": "",
            "inspected_at": utc_now_iso(),
        }


def compute_weighted_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute exact total clicks, impressions, CTR, and impression-weighted average position."""
    total_clicks = sum(int(r.get("clicks", 0)) for r in rows)
    total_impr = sum(int(r.get("impressions", 0)) for r in rows)
    weighted_pos = (
        round(sum(float(r.get("position", 0.0)) * int(r.get("impressions", 0)) for r in rows) / total_impr, 2)
        if total_impr > 0
        else None
    )
    ctr = round(total_clicks / total_impr, 4) if total_impr > 0 else 0.0
    return {
        "clicks": total_clicks,
        "impressions": total_impr,
        "ctr": ctr,
        "weighted_avg_position": weighted_pos,
    }


def build_weekly_report(
    *,
    site_rows: list[dict[str, Any]],
    page_rows: list[dict[str, Any]],
    query_rows: list[dict[str, Any]],
    published_pages: dict[str, Any],
    start_date: str,
    end_date: str,
    auth_mode: str,
) -> dict[str, Any]:
    # Site-level exact totals (from site dimension if returned, else aggregated from page rows)
    if site_rows:
        sr = site_rows[0]
        site_totals = {
            "clicks": int(sr.get("clicks", 0)),
            "impressions": int(sr.get("impressions", 0)),
            "ctr": round(float(sr.get("ctr", 0.0)), 4),
            "weighted_avg_position": round(float(sr.get("position", 0.0)), 2) if int(sr.get("impressions", 0)) > 0 else None,
        }
    else:
        site_totals = compute_weighted_metrics(page_rows)

    # Page-level map (explicit REVENUE_UNKNOWN — never substitutes site-wide revenue!)
    by_page: dict[str, dict[str, Any]] = {}
    by_type_rows: dict[str, list[dict[str, Any]]] = {}
    for r in page_rows:
        raw_url = (r.get("keys") or [""])[0]
        path = urllib.parse.urlparse(raw_url).path or "/"
        clicks = int(r.get("clicks", 0))
        impr = int(r.get("impressions", 0))
        pos = round(float(r.get("position", 0.0)), 2) if impr > 0 else None
        by_page[path] = {
            "clicks": clicks,
            "impressions": impr,
            "ctr": round(clicks / impr, 4) if impr > 0 else 0.0,
            "avg_position": pos,
            "revenue_status": "REVENUE_UNKNOWN",
        }
        pt = classify_page_type(raw_url)
        by_type_rows.setdefault(pt, []).append(r)

    by_page_type = {pt: compute_weighted_metrics(rows) for pt, rows in sorted(by_type_rows.items())}

    top_queries = [
        {
            "query": (r.get("keys") or [""])[0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(float(r.get("ctr", 0.0)), 4),
            "avg_position": round(float(r.get("position", 0.0)), 2),
        }
        for r in sorted(query_rows, key=lambda x: (-int(x.get("clicks", 0)), -int(x.get("impressions", 0))))[:50]
    ]

    # Strict inspection & funnel buckets (uninspected -> UNKNOWN, never 'not_discovered')
    buckets = {
        "uninspected_unknown": 0,
        "inspection_api_error": 0,
        "not_indexed": 0,
        "discovered_not_indexed": 0,
        "crawled_not_indexed": 0,
        "indexed_zero_impressions": 0,
        "impressions_zero_clicks": 0,
        "clicks_revenue_unknown": 0,
    }

    for url_path, meta in published_pages.items():
        if meta.get("status") == "gone" or meta.get("index_policy") != "index":
            continue
        pstat = by_page.get(url_path)
        insp = (meta.get("gsc_inspection") or {}).get("state", "UNKNOWN")

        if pstat and pstat["clicks"] > 0:
            buckets["clicks_revenue_unknown"] += 1
        elif pstat and pstat["impressions"] > 0:
            buckets["impressions_zero_clicks"] += 1
        elif insp == "INDEXED":
            buckets["indexed_zero_impressions"] += 1
        elif insp == "DISCOVERED_NOT_INDEXED":
            buckets["discovered_not_indexed"] += 1
        elif insp == "CRAWLED_NOT_INDEXED":
            buckets["crawled_not_indexed"] += 1
        elif insp == "NOT_INDEXED":
            buckets["not_indexed"] += 1
        elif insp == "API_ERROR":
            buckets["inspection_api_error"] += 1
        else:
            buckets["uninspected_unknown"] += 1

    return {
        "generated_at": utc_now_iso(),
        "auth_mode": auth_mode,
        "date_range": {"start_date": start_date, "end_date": end_date},
        "site_totals": site_totals,
        "by_page_type": by_page_type,
        "top_queries": top_queries,
        "page_metrics_count": len(by_page),
        "diagnostic_buckets": buckets,
        "revenue_attribution_policy": "REVENUE_UNKNOWN unless page-level conversion tracking is verified.",
    }


def main() -> None:
    site_url = os.getenv("GSC_SITE_URL", "sc-domain:sysv.cn").strip()
    base_url = os.getenv("MONITOR_BASE_URL", "https://sysv.cn").rstrip("/")
    inspect_limit = int(os.getenv("GSC_INSPECT_LIMIT", "25"))
    end_date = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

    pub_path = DATA_DIR / "published_pages.json"
    published = load_json(pub_path, default={})
    PRIVATE_GSC_DIR.mkdir(parents=True, exist_ok=True)
    private_insp_path = PRIVATE_GSC_DIR / "url_inspections.json"
    inspections = load_json(private_insp_path, default={})

    # Merge private inspection records into in-memory published view (never written back into public repo!)
    published_view: dict[str, Any] = {}
    for u, meta in published.items():
        merged = dict(meta)
        if u in inspections:
            merged["gsc_inspection"] = inspections[u]
        published_view[u] = merged

    token, auth_mode = obtain_access_token()
    site_rows: list[dict[str, Any]] = []
    page_rows: list[dict[str, Any]] = []
    query_rows: list[dict[str, Any]] = []

    if token:
        site_rows = query_search_analytics(site_url, token, start_date=start_date, end_date=end_date, dimensions=[], row_limit=1)
        page_rows = query_search_analytics(site_url, token, start_date=start_date, end_date=end_date, dimensions=["page"])
        query_rows = query_search_analytics(site_url, token, start_date=start_date, end_date=end_date, dimensions=["query"])

        # Inspect rotating batch of uninspected / oldest-inspected indexable URLs via real URL Inspection API
        candidates = [
            (u, meta)
            for u, meta in published_view.items()
            if meta.get("status") != "gone" and meta.get("index_policy") == "index"
        ]
        candidates.sort(key=lambda item: (item[1].get("gsc_inspection") or {}).get("inspected_at", ""))
        for u, meta in candidates[:inspect_limit]:
            insp_res = inspect_single_url(site_url, base_url + u, token)
            inspections[u] = insp_res
            meta["gsc_inspection"] = insp_res
            time.sleep(0.25)

        save_json(private_insp_path, inspections)
        GSC_DIR.mkdir(parents=True, exist_ok=True)
        save_json(
            GSC_DIR / f"{utc_today()}.json",
            {"date_range": {"start_date": start_date, "end_date": end_date}, "site": site_rows, "pages": page_rows, "queries": query_rows},
        )
    else:
        print("[gsc_report] Neither GSC_SERVICE_ACCOUNT_JSON nor OAuth2 refresh credentials configured; marking uninspected URLs as UNKNOWN.")

    report = build_weekly_report(
        site_rows=site_rows,
        page_rows=page_rows,
        query_rows=query_rows,
        published_pages=published_view,
        start_date=start_date,
        end_date=end_date,
        auth_mode=auth_mode,
    )
    out_path = PRIVATE_GSC_DIR / "gsc_weekly_report.json"
    save_json(out_path, report)
    print(f"[gsc_report] Saved PRIVATE report to {out_path} (auth={auth_mode}): totals={report['site_totals']}, buckets={report['diagnostic_buckets']}")


if __name__ == "__main__":
    main()
