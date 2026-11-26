"""Low-cost official Free LLM API policy drift, freshness & tiered degradation monitor (0 LLM calls).

Tiered status model (protects unattended availability without hiding real policy drift):
- OFFICIAL_DOCS_VERIFIED     (calculator usable): reachable, within freshness window, no drift
- DOC_CHANGED_SIGNAL_STABLE  (calculator usable, labeled review pending): page HTML/text drifted but core policy signals unchanged & still fresh
- FETCH_ERROR_CACHED         (calculator usable, labeled cached): transient fetch failure (consecutive < 3) while last_verified_date still fresh
- FETCH_ERROR_PERSISTENT     (calculator degraded + ALERT): >= 3 consecutive fetch failures
- POLICY_CHANGED_NEEDS_REVIEW(calculator paused + ALERT): core policy signal tokens changed (dollar/RPM/RPD/TPM/Neurons/trial terms)
- STALE / UNKNOWN            (calculator paused + ALERT): expired (> max_freshness_days) or missing last_verified_date
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

PERSISTENT_FETCH_ERROR_THRESHOLD = 3
USABLE_POLICY_STATUSES = frozenset(
    {"OFFICIAL_DOCS_VERIFIED", "DOC_CHANGED_SIGNAL_STABLE", "FETCH_ERROR_CACHED"}
)

POLICY_SIGNAL_PATTERN = re.compile(
    r"(\$\d[\d,\.]*|\b\d[\d,]*\s*(?:rpm|rpd|tpm|tpd|neurons?|days?|requests?\s+per\s+(?:minute|day)|tokens?\s+per\s+(?:minute|day))\b|\bfree\s+trial\b|\bpayment\s+method\b|\bcredit\s+card\b|\bimprove\s+google\s+products\b)",
    re.I,
)


def _normalize_doc_text(raw_html: str) -> str:
    txt = re.sub(r"<(script|style|noscript|svg|nav|footer)\b[^>]*>.*?</\1>", " ", raw_html, flags=re.S | re.I)
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
    consecutive_fetch_errors: int | None = None,
) -> tuple[str, str]:
    """Return (verification_status, reason) using tiered degradation."""
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

    if days_old > max_days:
        return "STALE", f"expired_{days_old}d_gt_{max_days}d"

    if not fetch_ok:
        consec = (
            int(consecutive_fetch_errors)
            if consecutive_fetch_errors is not None
            else int(provider.get("consecutive_fetch_errors") or 0) + 1
        )
        if consec >= PERSISTENT_FETCH_ERROR_THRESHOLD:
            return "FETCH_ERROR_PERSISTENT", f"consecutive_fetch_errors_{consec}"
        return "FETCH_ERROR_CACHED", f"transient_fetch_error_{consec}_using_fresh_cache"

    signal_changed = bool(fetched_signal_hash and prev_sig_hash and fetched_signal_hash != prev_sig_hash)
    doc_changed = bool(fetched_doc_hash and prev_doc_hash and fetched_doc_hash != prev_doc_hash)

    if signal_changed:
        return "POLICY_CHANGED_NEEDS_REVIEW", "policy_signal_tokens_changed"
    if doc_changed:
        return "DOC_CHANGED_SIGNAL_STABLE", "doc_html_changed_core_signals_unchanged"

    prev_status = str(provider.get("verification_status") or "OFFICIAL_DOCS_VERIFIED")
    if prev_status == "POLICY_CHANGED_NEEDS_REVIEW":
        return prev_status, "awaiting_manual_policy_reverification"
    return "OFFICIAL_DOCS_VERIFIED", "ok"


def has_alert_channel_configured() -> bool:
    return bool(os.getenv("PUSHPLUS_TOKEN", "").strip() or os.getenv("ALERT_WEBHOOK_URL", "").strip())


def send_policy_alert(message: str) -> bool:
    sent = False
    pp = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if pp:
        try:
            payload = json.dumps({"token": pp, "title": "[SysV] Free Policy Alert", "content": message, "template": "txt"}).encode()
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
        "verified": [],
        "doc_changed_signal_stable": [],
        "fetch_error_cached": [],
        "policy_changed": [],
        "fetch_error_persistent": [],
        "stale": [],
        "unknown": [],
        "alert_channel_configured": has_alert_channel_configured(),
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
                req = urllib.request.Request(url, headers={"User-Agent": "SysV-PolicyMonitor/1.2 (+https://sysv.cn/about/)"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                    norm = _normalize_doc_text(raw)
                    fetched_doc_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
                    fetched_sig_hash = extract_policy_signal_hash(norm)
                    fetch_ok = resp.status == 200 and len(norm) >= 200
            except Exception as err:
                fetch_ok = False
                p["last_fetch_error"] = str(err)[:160]

        if fetch_ok:
            consec_errors = 0
        else:
            consec_errors = int(p.get("consecutive_fetch_errors") or 0) + 1
        p["consecutive_fetch_errors"] = consec_errors

        new_status, reason = compute_policy_freshness_status(
            p,
            today_iso=today,
            fetched_doc_hash=fetched_doc_hash,
            fetched_signal_hash=fetched_sig_hash,
            fetch_ok=fetch_ok,
            consecutive_fetch_errors=consec_errors,
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
            if new_status in ("POLICY_CHANGED_NEEDS_REVIEW", "DOC_CHANGED_SIGNAL_STABLE"):
                p["pending_source_hash"] = fetched_doc_hash
                p["pending_signal_hash"] = fetched_sig_hash
                p["drift_detected_utc"] = now_iso

        if new_status == "OFFICIAL_DOCS_VERIFIED":
            summary["verified"].append(p["id"])
        elif new_status == "DOC_CHANGED_SIGNAL_STABLE":
            summary["doc_changed_signal_stable"].append(p["id"])
        elif new_status == "FETCH_ERROR_CACHED":
            summary["fetch_error_cached"].append(p["id"])
        elif new_status == "POLICY_CHANGED_NEEDS_REVIEW":
            summary["policy_changed"].append(p["id"])
        elif new_status == "FETCH_ERROR_PERSISTENT":
            summary["fetch_error_persistent"].append(p["id"])
        elif new_status == "STALE":
            summary["stale"].append(p["id"])
        else:
            summary["unknown"].append(p["id"])

    data["updated_utc"] = now_iso
    save_json(path, data)

    # Alert ONLY on high-severity degradation (real policy change, persistent fetch failure, stale, unknown)
    critical = summary["policy_changed"] + summary["fetch_error_persistent"] + summary["stale"] + summary["unknown"]
    if notify and critical:
        msg = (
            f"SysV Free LLM Policy Monitor CRITICAL ({today}): "
            f"policy_changed={summary['policy_changed']}, fetch_error_persistent={summary['fetch_error_persistent']}, "
            f"stale={summary['stale']}, unknown={summary['unknown']}. "
            f"Review data/free_provider_policies.json and run --confirm-reverified <id>."
        )
        summary["alert_sent"] = send_policy_alert(msg)

    return summary


def confirm_reverified(provider_id: str, *, policy_path=None) -> dict[str, Any]:
    """Re-baseline hashes and mark provider OFFICIAL_DOCS_VERIFIED after manual review."""
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
    target["consecutive_fetch_errors"] = 0
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
        f"[check_free_policies] checked={res['checked']}, verified={res['verified']}, "
        f"doc_changed_signal_stable={res['doc_changed_signal_stable']}, "
        f"fetch_error_cached={res['fetch_error_cached']}, "
        f"policy_changed={res['policy_changed']}, fetch_error_persistent={res['fetch_error_persistent']}, "
        f"stale={res['stale']}, alert_channel_configured={res['alert_channel_configured']}, alert_sent={res['alert_sent']}"
    )


if __name__ == "__main__":
    main()
