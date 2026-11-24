"""Independent External Dead-Man's-Switch Monitor for https://sysv.cn.

Designed to run OUTSIDE the production GitHub Actions workflow (e.g., on a separate
free Cloudflare Worker Cron, a separate monitoring repo, or a local/VPS cron) so that
silent GitHub Actions stoppage or site outage triggers an immediate PushPlus alert.

Environment variables:
- MONITOR_BASE_URL   (default: https://sysv.cn)
- MAX_STALE_HOURS    (default: 48)
- MIN_SITEMAP_URLS   (default: 50)
- PUSHPLUS_TOKEN     (optional; silent when healthy, alerts ONLY on anomaly)
- ALERT_WEBHOOK_URL  (optional fallback webhook)
"""

import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def fetch_text(url: str, timeout: int = 15) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "SysV-External-Monitor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="ignore")


def send_alert(title: str, body: str) -> None:
    pp = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if pp:
        payload = json.dumps({"token": pp, "title": title, "content": body, "template": "txt"}).encode()
        req = urllib.request.Request("https://www.pushplus.plus/send", data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=12)
    wh = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if wh:
        payload = json.dumps({"text": f"{title}\n{body}"}).encode()
        req = urllib.request.Request(wh, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=12)


def main() -> None:
    base = os.getenv("MONITOR_BASE_URL", "https://sysv.cn").rstrip("/")
    max_stale_hours = float(os.getenv("MAX_STALE_HOURS", "48"))
    min_urls = int(os.getenv("MIN_SITEMAP_URLS", "50"))
    issues: list[str] = []

    # 1. Homepage availability
    try:
        code, html = fetch_text(f"{base}/")
        if code != 200 or "<title>" not in html.lower():
            issues.append(f"Homepage unhealthy: HTTP {code}")
    except Exception as err:
        issues.append(f"Homepage unreachable: {err}")

    # 2. Data freshness & version provenance check (/version.json)
    try:
        code, raw = fetch_text(f"{base}/version.json")
        if code != 200:
            issues.append(f"/version.json returned HTTP {code}")
        else:
            ver = json.loads(raw)
            built_str = ver.get("built_at_utc", "")
            built_dt = datetime.fromisoformat(built_str.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - built_dt).total_seconds() / 3600.0
            if age_hours > max_stale_hours:
                issues.append(f"Data STALE: last build was {age_hours:.1f}h ago (threshold={max_stale_hours}h, commit={ver.get('git_commit')})")
    except Exception as err:
        issues.append(f"/version.json check failed: {err}")

    # 3. Sitemap XML validity & URL count floor
    try:
        code, xml_raw = fetch_text(f"{base}/sitemap.xml")
        if code != 200:
            issues.append(f"/sitemap.xml returned HTTP {code}")
        else:
            root = ET.fromstring(xml_raw)
            urls = [el for el in root.iter() if el.tag.endswith("loc")]
            if len(urls) < min_urls:
                issues.append(f"/sitemap.xml URL count dropped to {len(urls)} (< {min_urls})")
    except Exception as err:
        issues.append(f"/sitemap.xml check failed: {err}")

    if issues:
        report = "\n".join(f"- {i}" for i in issues)
        print(f"[external_monitor] ALERT:\n{report}")
        send_alert(f"[SysV External Monitor] Anomaly on {base}", report)
        sys.exit(1)

    print(f"[external_monitor] OK: {base} live, fresh (<{max_stale_hours}h), sitemap valid.")


if __name__ == "__main__":
    main()
