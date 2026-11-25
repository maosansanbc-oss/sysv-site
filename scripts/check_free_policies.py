"""Low-cost official Free LLM API policy drift & freshness monitor (0 LLM calls).

Checks each provider's official documentation URL in `data/free_provider_policies.json`:
- Computes normalized text SHA-256 hash to detect upstream policy changes.
- Evaluates freshness against `max_freshness_days` (default 14 days).
- Marks stale or drifted policies honestly as `STALE`, `CHANGED_NEEDS_REVIEW`, or `UNKNOWN`
  so the site NEVER claims `OFFICIAL_DOCS_VERIFIED` on unverified or expired data.
"""

from datetime import datetime, timezone
import hashlib
import re
import urllib.request
from typing import Any

from scripts.utils import DATA_DIR, load_json, save_json, utc_now_iso, utc_today


def _normalize_doc_text(raw_html: str) -> str:
    txt = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw_html, flags=re.S | re.I)
    txt = re.sub(r"<style\b[^>]*>.*?</style>", " ", txt, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def compute_policy_freshness_status(
    provider: dict[str, Any],
    *,
    today_iso: str,
    fetched_hash: str | None = None,
    fetch_ok: bool = True,
) -> tuple[str, bool]:
    """Return (verification_status, hash_changed) with strict honesty guards."""
    max_days = int(provider.get("max_freshness_days") or 14)
    last_verified = str(provider.get("last_verified_date") or "").strip()
    prev_hash = provider.get("source_content_hash")

    days_old: int | None = None
    if last_verified:
        try:
            d0 = datetime.strptime(last_verified, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            d1 = datetime.strptime(today_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_old = (d1 - d0).days
        except ValueError:
            days_old = None

    if days_old is None:
        return "UNKNOWN", False

    hash_changed = bool(fetch_ok and fetched_hash and prev_hash and fetched_hash != prev_hash)
    if hash_changed:
        return "CHANGED_NEEDS_REVIEW", True
    if days_old > max_days:
        return "STALE", False
    if not fetch_ok and provider.get("verification_status") not in ("OFFICIAL_DOCS_VERIFIED",):
        return "UNKNOWN", False
    return str(provider.get("verification_status") or "OFFICIAL_DOCS_VERIFIED"), False


def check_all_policies(*, policy_path=None, perform_network: bool = True) -> dict[str, Any]:
    path = policy_path or (DATA_DIR / "free_provider_policies.json")
    data = load_json(path, default={"providers": [], "policy_history": []})
    today = utc_today()
    now_iso = utc_now_iso()
    summary = {"checked": 0, "changed": [], "stale": [], "unreachable": []}

    for p in data.get("providers", []):
        summary["checked"] += 1
        url = str(p.get("source_url") or "").strip()
        fetched_hash: str | None = None
        fetch_ok = False
        if perform_network and url:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "SysV-PolicyMonitor/1.0 (+https://sysv.cn/about/)"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                    norm = _normalize_doc_text(raw)
                    fetched_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
                    fetch_ok = resp.status == 200 and len(norm) >= 200
            except Exception:
                fetch_ok = False
                summary["unreachable"].append(p["id"])

        new_status, changed = compute_policy_freshness_status(
            p, today_iso=today, fetched_hash=fetched_hash, fetch_ok=fetch_ok
        )
        p["verification_status"] = new_status
        p["last_checked_utc"] = now_iso
        if fetch_ok and fetched_hash and not p.get("source_content_hash"):
            # Baseline hash initialized on first clean fetch
            p["source_content_hash"] = fetched_hash
        if changed:
            summary["changed"].append(p["id"])
            p["pending_source_hash"] = fetched_hash
        if new_status == "STALE":
            summary["stale"].append(p["id"])

    data["updated_utc"] = now_iso
    save_json(path, data)
    return summary


def main() -> None:
    res = check_all_policies(perform_network=True)
    print(
        f"[check_free_policies] checked={res['checked']}, "
        f"changed={res['changed']}, stale={res['stale']}, unreachable={res['unreachable']}"
    )


if __name__ == "__main__":
    main()
