"""Automated Quality Gate & Anomaly Detector (runs before every publish).

Prevents broken/misclassified data from ever reaching production:
1. Verifies zero variable/router/media/unknown models are mislabeled as free ($0).
2. Verifies zero broken internal links across all built HTML files.
3. Verifies sitemap.xml & feed.xml are valid XML and required trust/SEO files exist.
4. Verifies model/page counts exceed minimum safety floors.
5. Sends optional PushPlus / Webhook alert ONLY on failure.
"""

import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.utils import DATA_DIR, DIST_DIR, load_config, load_json


def send_alert(title: str, content: str) -> None:
    pushplus_token = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if pushplus_token:
        try:
            payload = json.dumps({"token": pushplus_token, "title": title, "content": content, "template": "txt"}).encode()
            req = urllib.request.Request(
                "https://www.pushplus.plus/send",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=12)
        except Exception as err:
            print(f"[health_check] PushPlus alert failed: {err}")

    webhook_url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if webhook_url:
        try:
            payload = json.dumps({"text": f"{title}\n{content}"}).encode()
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=12)
        except Exception as err:
            print(f"[health_check] Webhook alert failed: {err}")


def main() -> None:
    cfg = load_config()
    errors: list[str] = []

    models = load_json(DATA_DIR / "models.json", default=[])
    repos = load_json(DATA_DIR / "repos.json", default=[])

    # 1. Data integrity & P0 anti-misclassification assertions
    if len(models) < 10:
        errors.append(f"Too few models in data/models.json: {len(models)}")
    if len(repos) < 4:
        errors.append(f"Too few repos in data/repos.json: {len(repos)}")

    for m in models:
        ptype = m.get("pricing_type")
        mid = str(m.get("id", "")).lower()
        if m.get("is_free") and ptype != "free":
            errors.append(f"Misclassification: {mid} has is_free=True but pricing_type={ptype}")
        if ptype == "free" and any(k in mid for k in ("auto", "router", "lyria", "pareto", "fusion")):
            errors.append(f"P0 violation: router/media model '{mid}' classified as free!")
        if ptype == "variable" and (m.get("prompt_price_per_m") == 0 or m.get("completion_price_per_m") == 0):
            errors.append(f"P0 violation: variable router '{mid}' has $0 price!")

    # 2. Required production files & valid XML
    for req_file in ["index.html", "sitemap.xml", "robots.txt", "feed.xml", "favicon.svg", "404.html", "CNAME"]:
        if not (DIST_DIR / req_file).exists():
            errors.append(f"Missing required dist file: {req_file}")

    for xml_file in ["sitemap.xml", "feed.xml"]:
        p = DIST_DIR / xml_file
        if p.exists():
            try:
                ET.parse(p)
            except ET.ParseError as err:
                errors.append(f"Invalid XML in {xml_file}: {err}")

    # 3. Link integrity across ALL generated HTML pages
    html_files = list(DIST_DIR.rglob("*.html"))
    if len(html_files) < 20:
        errors.append(f"Suspiciously few HTML pages built: {len(html_files)}")

    broken_links: list[tuple[str, str]] = []
    for f in html_files:
        txt = re.sub(r"<script\b[^>]*>.*?</script>", "", f.read_text(encoding="utf-8"), flags=re.DOTALL | re.IGNORECASE)
        for href in re.findall(r'href="(/[^"#?]*)"', txt):
            target = DIST_DIR / href.lstrip("/")
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                broken_links.append((str(f.relative_to(DIST_DIR)), href))
    if broken_links:
        errors.append(f"Found {len(broken_links)} broken internal links: {broken_links[:5]}")

    # 4. Zero secret leak check
    for p in Path(".").rglob("*"):
        if ".git" in p.parts or "dist" in p.parts or not p.is_file():
            continue
        content = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"sk-[0-9a-f]{32}", content):
            errors.append(f"Potential API key leaked in file: {p}")

    if errors:
        msg = "\n".join(f"- {e}" for e in errors)
        print(f"[health_check] FAILED:\n{msg}")
        send_alert(f"[SysV Radar] Quality Gate FAILED ({cfg['domain']})", msg)
        sys.exit(1)

    free_n = sum(1 for m in models if m.get("pricing_type") == "free")
    var_n = sum(1 for m in models if m.get("pricing_type") == "variable")
    print(
        f"[health_check] PASSED: html_pages={len(html_files)}, models={len(models)} "
        f"(free={free_n}, variable_routers={var_n}), repos={len(repos)}, broken_links=0, leaks=0."
    )


if __name__ == "__main__":
    main()
