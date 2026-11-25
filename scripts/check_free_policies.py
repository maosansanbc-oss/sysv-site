"""Low-cost official Free LLM API policy drift, freshness & alert monitor (0 LLM calls).

Explicitly distinguishes 5 states:
- OFFICIAL_DOCS_VERIFIED: reachable, within freshness window, no document or policy-signal drift
- DOC_CHANGED_NEEDS_REVIEW: official doc page hash changed, but core policy-signal numbers unchanged
- POLICY_CHANGED_NEEDS_REVIEW: key policy signals (dollar amounts, RPM/RPD/TPM/TPD, neurons, trial/credit terms) drifted
- FETCH_ERROR: official docs URL failed HTTP fetch/timeout
- STALE: `last_verified_date` exceeded `max_freshness_days` (default 14 days)
- UNKNOWN: missing `last_verified_date`

Supports:
- Anomaly-only notification via PushPlus / ALERT_WEBHOOK_URL whenever any provider enters a non-verified state.
- `--confirm-reverified <provider_id>` CLI workflow to re-baseline hashes & reset `OFFICIAL_DOCS_VERIFIED` after review.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import urllib.request
from typing import Any

from scripts.utils import DATA_DIR, load_json, save_json, utc_now_iso, utc_today

POLICY_SIGNAL_PATTERN = re.compile(
    r"(\$\d[\d,\.]*|\b\d[\d,]*\s*(?:rpm|rpd|tpm|tpd|neurons?|days?|requests?\s+per\s+(?:minute|day)|tokens?\s+per\s+(?:minute|day))\b|\bfree\s+trial\b|\bpayment\s+method\b|\bcredit\s+card\b|\bimprove\s+google\s+products\b)",
    re.I,
)


def _normalize_doc_text(raw_html: str) -> str:
    txt = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw_html, flags=re.S | re.I)
    txt = re.sub(r"<style\b[^>]*>.*?</style>", " ", txt, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def extract_policy_signal_hash(normalized_text: str) -> str:
    """Hash only quota/pricing/trial/billing signal tokens to separate real policy drift from cosmetic doc edits."""
    tokens = sorted({m.group(0).lower().strip() for m in POLICY_SIGNAL_PATTERN.finditer(normalized_text)})
    return hashlib.sha256("\n".join(tokens).encode("utf-8")).hexdigest()[:16]


def compute_policy_freshness_status(
    provider: dict[str, Any],
    *,
    today_iso: str,
    fetched_doc_hash: str | None = None,
    fetched_signal_hash: str | None = None,
    fetch_ok: bool = True,
) -> tuple[str, str]:
    """Return (verification_status, reason) with strict distinction between doc drift, policy drift, fetch failure & expiry."""
    max_days = int(provider.get("max_freshness_days") or 14)
    last_verified = str(provider.get("last_verified_date") or "").strip()
    prev_doc_hash = provider.get("source_content_hash")
    prev_sig_hash = provider.get("policy_signal_hash")

    days_old: int | None = None
    if last_verified:
        try:
            d0 = datetime.strptime(last_verified, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            d1 = datetime.strptime(today_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_old = (d1 - d0).days
        except ValueError:
            days_old = None

    if days_old is None:
        return "UNKNOWN", "missing_last_verified_date"

    if not fetch_ok:
        if days_old > max_days:
            return "STALE", f"fetch_failed_and_expired_{days_old}d"
        return "FETCH_ERROR", "upstream_doc_unreachable"

    signal_changed = bool(fetched_signal_hash and prev_sig_hash and fetched_signal_hash != prev_sig_hash)
    doc_changed = bool(fetched_doc_hash and prev_doc_hash and fetched_doc_hash != prev_doc_hash)

    if signal_changed:
        return "POLICY_CHANGED_NEEDS_REVIEW", "policy_signal_tokens_changed"
    if doc_changed:
        return "DOC_CHANGED_NEEDS_REVIEW", "doc_text_hash_changed_signals_stable"
    if days_old > max_days:
        return "STALE", f"expired_{days_old}d_gt_{max_days}d"

    prev_status = str(provider.get("verification_status") or "OFFICIAL_DOCS_VERIFIED")
    if prev_status in ("POLICY_CHANGED_NEEDS_REVIEW", "DOC_CHANGED_NEEDS_REVIEW"):
        return prev_status, "awaiting_manual_reverification"
    return "OFFICIAL_DOCS_VERIFIED", "ok"


def send_policy_alert(message: str) -> bool:
    sent = False
    pp = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if pp:
        try:
            payload = json.dumps({"token": pp, "title": "[SysV] Free Policy Drift Alert", "content": message, "template": "txt"}).encode()
            req = urllib.request.Request("https://www.pushplus.plus/send", data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=12)
            sent = True
        except Exception as err:
            print(f"[check_free_policies] PushPlus alert failed: {err}")
    wh = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if wh:
        try:
            payload = json.dumps({"text": message}).encode()
            req = urllib.request.Request(wh, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=12)
            sent = True
        except Exception as err:
            print(f"[check_free_policies] Webhook alert failed: {err}")
    return sent


def check_all_policies(*, policy_path=None, perform_network: bool = True, notify: bool = True) -> dict[str, Any]:
    path = policy_path or (DATA_DIR / "free_provider_policies.json")
    data = load_json(path, default={"providers": [], "policy_history": []})
    today = utc_today()
    now_iso = utc_now_iso()
    summary: dict[str, Any] = {
        "checked": 0,
        "policy_changed": [],
        "doc_changed": [],
        "stale": [],
        "fetch_error": [],
        "unknown": [],
        "alert_sent": False,
    }

    for p in data.get("providers", []):
        summary["checked"] += 1
        url = str(p.get("source_url") or "").strip()
        fetched_doc_hash: str | None = None
        fetched_sig_hash: str | None = None
        fetch_ok = not perform_network

        if perform_network and url:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "SysV-PolicyMonitor/1.1 (+https://sysv.cn/about/)"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                    norm = _normalize_doc_text(raw)
                    fetched_doc_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
                    fetched_sig_hash = extract_policy_signal_hash(norm)
                    fetch_ok = resp.status == 200 and len(norm) >= 200
            except Exception as err:
                fetch_ok = False
                p["last_fetch_error"] = str(err)[:160]

        new_status, reason = compute_policy_freshness_status(
            p,
            today_iso=today,
            fetched_doc_hash=fetched_doc_hash,
            fetched_signal_hash=fetched_sig_hash,
            fetch_ok=fetch_ok,
        )
        p["verification_status"] = new_status
        p["verification_reason"] = reason
        p["last_checked_utc"] = now_iso

        if fetch_ok:
            p.pop("last_fetch_error", None)
            if fetched_doc_hash and not p.get("source_content_hash"):
                p["source_content_hash"] = fetched_doc_hash
            if fetched_sig_hash and not p.get("policy_signal_hash"):
                p["policy_signal_hash"] = fetched_sig_hash
            if new_status in ("POLICY_CHANGED_NEEDS_REVIEW", "DOC_CHANGED_NEEDS_REVIEW"):
                p["pending_source_hash"] = fetched_doc_hash
                p["pending_signal_hash"] = fetched_sig_hash
                p["drift_detected_utc"] = now_iso

        if new_status == "POLICY_CHANGED_NEEDS_REVIEW":
            summary["policy_changed"].append(p["id"])
        elif new_status == "DOC_CHANGED_NEEDS_REVIEW":
            summary["doc_changed"].append(p["id"])
        elif new_status == "STALE":
            summary["stale"].append(p["id"])
        elif new_status == "FETCH_ERROR":
            summary["fetch_error"].append(p["id"])
        elif new_status == "UNKNOWN":
            summary["unknown"].append(p["id"])

    data["updated_utc"] = now_iso
    save_json(path, data)

    anomalies = (
        summary["policy_changed"]
        + summary["doc_changed"]
        + summary["stale"]
        + summary["fetch_error"]
        + summary["unknown"]
    )
    if notify and anomalies:
        msg = (
            f"SysV Free LLM Policy Monitor detected anomalies ({today}): "
            f"policy_changed={summary['policy_changed']}, doc_changed={summary['doc_changed']}, "
            f"stale={summary['stale']}, fetch_error={summary['fetch_error']}, unknown={summary['unknown']}. "
            f"Review data/free_provider_policies.json and run --confirm-reverified <id> after verifying."
        )
        summary["alert_sent"] = send_policy_alert(msg)

    return summary


def confirm_reverified(provider_id: str, *, policy_path=None) -> dict[str, Any]:
    """Re-baseline hashes and mark provider OFFICIAL_DOCS_VERIFIED after human/manual doc re-verification."""
    path = policy_path or (DATA_DIR / "free_provider_policies.json")
    data = load_json(path, default={"providers": [], "policy_history": []})
    target = next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)
    if target is None:
        raise ValueError(f"Unknown provider_id: {provider_id}")
    if target.get("pending_source_hash"):
        target["source_content_hash"] = target.pop("pending_source_hash")
    if target.get("pending_signal_hash"):
        target["policy_signal_hash"] = target.pop("pending_signal_hash")
    target.pop("drift_detected_utc", None)
    target.pop("last_fetch_error", None)
    target["last_verified_date"] = utc_today()
    target["last_checked_utc"] = utc_now_iso()
    target["verification_status"] = "OFFICIAL_DOCS_VERIFIED"
    target["verification_reason"] = "confirmed_reverified"
    data["updated_utc"] = utc_now_iso()
    save_json(path, data)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-reverified", default="", help="Provider ID confirmed after manual doc review")
    args = parser.parse_args()
    if args.confirm_reverified:
        updated = confirm_reverified(args.confirm_reverified)
        print(f"[check_free_policies] Re-verified {updated['id']} on {updated['last_verified_date']}")
        return
    res = check_all_policies(perform_network=True, notify=True)
    print(
        f"[check_free_policies] checked={res['checked']}, policy_changed={res['policy_changed']}, "
        f"doc_changed={res['doc_changed']}, stale={res['stale']}, fetch_error={res['fetch_error']}, "
        f"alert_sent={res['alert_sent']}"
    )


if __name__ == "__main__":
    main()
