"""Record real pipeline publish status in technical ops ledger (commercial revenue/traffic stay strictly in private/)."""

import argparse

from scripts.utils import DATA_DIR, load_json, save_json, utc_now_iso, utc_today

PRIVATE_COMMERCIAL_FIELDS = (
    "visits",
    "gsc_impressions",
    "gsc_clicks",
    "indexed_pages",
    "affiliate_clicks",
    "affiliate_conversions",
    "estimated_revenue_usd",
    "confirmed_payable_usd",
    "bank_settled_usd",
    "net_profit_usd",
    "revenue_status",
)


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
            "llm_unknown_cost_calls": 0,
            "llm_tokens_in": 0,
            "llm_tokens_out": 0,
            "llm_cost_usd": 0.0,
            "llm_unknown_cost_reserve_usd": 0.0,
            "commercial_data_location": "private/commercial_ledger.json (excluded from public repo)",
        }
        daily.append(entry)
    else:
        entry["publish_success"] = bool(success)

    entry["last_deploy_utc"] = utc_now_iso()
    if git_commit:
        entry["last_deploy_commit"] = git_commit

    # Strip private commercial/revenue fields from public technical ops ledger
    for row in daily:
        for priv_k in PRIVATE_COMMERCIAL_FIELDS:
            row.pop(priv_k, None)
        row.setdefault("llm_failed_calls", 0)
        row.setdefault("llm_unknown_cost_calls", 0)
        row.setdefault("llm_unknown_cost_reserve_usd", 0.0)
        row["commercial_data_location"] = "private/commercial_ledger.json (excluded from public repo)"

    ops["daily"] = sorted(daily, key=lambda x: x["date"], reverse=True)[:365]
    save_json(path, ops)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", choices=["success", "failure"], required=True)
    parser.add_argument("--commit", default="")
    args = parser.parse_args()
    entry = record_publish_status(success=(args.status == "success"), git_commit=args.commit)
    print(f"[record_deploy_status] date={entry['date']} publish_success={entry['publish_success']} commit={entry.get('last_deploy_commit')}")


if __name__ == "__main__":
    main()
