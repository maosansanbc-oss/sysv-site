"""Record real pipeline publish status & enforce honest REVENUE_UNKNOWN distinction in data/ops_metrics.json.

Usage:
  python3 -m scripts.record_deploy_status --status success --commit 2180975
  python3 -m scripts.record_deploy_status --status failure --commit 2180975
"""

import argparse

from scripts.utils import DATA_DIR, load_json, save_json, utc_now_iso, utc_today


def record_publish_status(*, success: bool, git_commit: str = "", ops_path=None) -> dict:
    path = ops_path or (DATA_DIR / "ops_metrics.json")
    ops = load_json(path, default={"daily": []})
    today = utc_today()
    daily = ops.setdefault("daily", [])
    entry = next((d for d in daily if d.get("date") == today), None)
    if entry is None:
        entry = {
            "date": today,
            "fetch_success": None,
            "fetch_repos_success": None,
            "publish_success": bool(success),
            "llm_calls": 0,
            "llm_failed_calls": 0,
            "llm_tokens_in": 0,
            "llm_tokens_out": 0,
            "llm_cost_usd": 0.0,
            "visits": None,
            "gsc_impressions": None,
            "gsc_clicks": None,
            "indexed_pages": None,
            "affiliate_clicks": None,
            "affiliate_conversions": None,
            "revenue_status": "REVENUE_UNKNOWN",
            "estimated_revenue_usd": None,
            "confirmed_payable_usd": None,
            "bank_settled_usd": None,
            "net_profit_usd": None,
            "manual_interventions": 0,
            "manual_minutes": 0,
        }
        daily.append(entry)
    else:
        entry["publish_success"] = bool(success)
        if entry.get("revenue_status") is None:
            entry["revenue_status"] = "REVENUE_UNKNOWN"
        if entry["revenue_status"] == "REVENUE_UNKNOWN":
            entry["estimated_revenue_usd"] = None
            entry["confirmed_payable_usd"] = None
            entry["bank_settled_usd"] = None
            entry["net_profit_usd"] = None

    entry["last_deploy_utc"] = utc_now_iso()
    if git_commit:
        entry["last_deploy_commit"] = git_commit

    # Normalize historical entries that lacked explicit revenue_status (distinguish unmeasured vs verified $0)
    for row in daily:
        if "revenue_status" not in row:
            row["revenue_status"] = "REVENUE_UNKNOWN"
            row["estimated_revenue_usd"] = None
            row["confirmed_payable_usd"] = None
            row["bank_settled_usd"] = None
            row["net_profit_usd"] = None

    ops["daily"] = sorted(daily, key=lambda x: x["date"], reverse=True)[:365]
    save_json(path, ops)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", choices=["success", "failure"], required=True)
    parser.add_argument("--commit", default="")
    args = parser.parse_args()
    entry = record_publish_status(success=(args.status == "success"), git_commit=args.commit)
    print(f"[record_deploy_status] date={entry['date']} publish_success={entry['publish_success']} revenue_status={entry['revenue_status']}")


if __name__ == "__main__":
    main()
