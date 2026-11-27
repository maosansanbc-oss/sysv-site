"""V2.1 Automated Quality Gate & Semantic Anomaly Detector (runs before every publish)."""

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


def run_quality_gate(data_dir: Path = DATA_DIR, dist_dir: Path = DIST_DIR, repo_root: Path | None = None) -> list[str]:
    """Return list of quality gate error strings. Empty list means PASS."""
    errors: list[str] = []
    repo_root = repo_root or data_dir.parent

    models = load_json(data_dir / "models.json", default=[])
    repos = load_json(data_dir / "repos.json", default=[])

    # 1. Data integrity & P0 semantic classification assertions
    if len(models) < 10:
        errors.append(f"Too few models in data/models.json: {len(models)}")
    if len(repos) < 4:
        errors.append(f"Too few repos in data/repos.json: {len(repos)}")

    valid_ptypes = {"limited_free", "free", "free_router", "variable", "batch", "paid", "media", "unknown"}
    for m in models:
        ptype = m.get("pricing_type")
        mid = str(m.get("id", "")).lower()
        if ptype not in valid_ptypes:
            errors.append(f"Unknown pricing_type '{ptype}' on {mid}")
        if m.get("is_free") and ptype not in ("limited_free", "free"):
            errors.append(f"Misclassification: {mid} has is_free=True but pricing_type={ptype}")
        if ptype in ("limited_free", "free") and any(k in mid for k in ("auto", "router", "lyria", "pareto", "fusion", ":batch")):
            errors.append(f"P0 violation: router/media/batch model '{mid}' classified as {ptype}!")
        if ptype == "variable" and (m.get("prompt_price_per_m") == 0 or m.get("completion_price_per_m") == 0):
            errors.append(f"P0 violation: variable router '{mid}' has $0 price!")
        is_batch_id_or_name = (":batch" in mid) or ("(batch)" in str(m.get("name", "")).lower())
        if is_batch_id_or_name and ptype != "batch":
            errors.append(f"P0 violation: batch endpoint '{mid}' misclassified as pricing_type={ptype}!")
        if (ptype == "batch" or is_batch_id_or_name) and m.get("is_realtime") is not False:
            errors.append(f"P0 violation: batch endpoint '{mid}' marked as real-time interactive (is_realtime={m.get('is_realtime')})!")
        if m.get("status", "active") == "active" and m.get("is_realtime") is None:
            errors.append(f"P0 violation: active endpoint '{mid}' missing boolean is_realtime!")

    # 2. Required production files & valid XML/JSON
    for req_file in ["index.html", "sitemap.xml", "robots.txt", "feed.xml", "favicon.svg", "404.html", "CNAME", "version.json"]:
        if not (dist_dir / req_file).exists():
            errors.append(f"Missing required dist file: {req_file}")

    for xml_file in ["sitemap.xml", "feed.xml"]:
        p = dist_dir / xml_file
        if p.exists():
            try:
                ET.parse(p)
            except ET.ParseError as err:
                errors.append(f"Invalid XML in {xml_file}: {err}")

    ver_path = dist_dir / "version.json"
    if ver_path.exists():
        try:
            ver = json.loads(ver_path.read_text(encoding="utf-8"))
            if not ver.get("built_at_utc") or not ver.get("git_commit"):
                errors.append("version.json missing required fields (git_commit / built_at_utc)")
        except Exception as err:
            errors.append(f"Invalid version.json: {err}")

    # 3. Link integrity across ALL generated HTML pages (ignoring JS template strings inside <script>)
    html_files = list(dist_dir.rglob("*.html")) if dist_dir.exists() else []
    if len(html_files) < 20:
        errors.append(f"Suspiciously few HTML pages built: {len(html_files)}")

    broken_links: list[tuple[str, str]] = []
    for f in html_files:
        txt = re.sub(r"<script\b[^>]*>.*?</script>", "", f.read_text(encoding="utf-8"), flags=re.DOTALL | re.IGNORECASE)
        for href in re.findall(r'href="(/[^"#?]*)"', txt):
            target = dist_dir / href.lstrip("/")
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                broken_links.append((str(f.relative_to(dist_dir)), href))
    if broken_links:
        errors.append(f"Found {len(broken_links)} broken internal links: {broken_links[:5]}")

    # 3b. AdSense policy compliance: NEVER load adsbygoogle.js on error, redirect, legal, embed, or noindex pages
    no_ad_rel_paths = {"404.html", "repos/index.html", "privacy/index.html", "terms/index.html", "contact/index.html"}
    ads_policy_violations: list[str] = []
    for f in html_files:
        rel = f.relative_to(dist_dir).as_posix()
        raw_html = f.read_text(encoding="utf-8")
        if "adsbygoogle" not in raw_html:
            continue
        is_noindex = bool(re.search(r'<meta\s+name="robots"\s+content="[^"]*noindex', raw_html, re.I))
        if rel in no_ad_rel_paths or rel.startswith("embed/") or is_noindex:
            ads_policy_violations.append(rel)
    if ads_policy_violations:
        errors.append(f"AdSense policy violation: adsbygoogle loaded on non-content/noindex pages: {ads_policy_violations[:10]}")

    # 4. Zero secret leak check
    if repo_root.exists():
        for p in repo_root.rglob("*"):
            if ".git" in p.parts or "dist" in p.parts or "__pycache__" in p.parts or not p.is_file():
                continue
            content = p.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"sk-[0-9a-f]{32}", content) or re.search(r"ghp_[A-Za-z0-9]{20,}", content):
                errors.append(f"Potential secret leaked in file: {p}")

    return errors


def main() -> None:
    cfg = load_config()
    errors = run_quality_gate(DATA_DIR, DIST_DIR, DATA_DIR.parent)

    if errors:
        msg = "\n".join(f"- {e}" for e in errors)
        print(f"[health_check] FAILED:\n{msg}")
        send_alert(f"[SysV Radar] Quality Gate FAILED ({cfg['domain']})", msg)
        sys.exit(1)

    models = load_json(DATA_DIR / "models.json", default=[])
    repos = load_json(DATA_DIR / "repos.json", default=[])
    html_count = len(list(DIST_DIR.rglob("*.html")))
    lf = sum(1 for m in models if m.get("pricing_type") == "limited_free")
    bt = sum(1 for m in models if m.get("pricing_type") == "batch")
    vr = sum(1 for m in models if m.get("pricing_type") == "variable")
    print(
        f"[health_check] PASSED: html_pages={html_count}, models={len(models)} "
        f"(limited_free={lf}, batch={bt}, variable={vr}), repos={len(repos)}, broken_links=0, leaks=0."
    )


if __name__ == "__main__":
    main()
