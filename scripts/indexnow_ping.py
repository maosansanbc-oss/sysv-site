"""Selective IndexNow notifier for Bing & participating search engines.

Only submits indexable canonical URLs whose semantic `lastmod` actually changed today.
Requires env var `INDEXNOW_KEY` (and places `<INDEXNOW_KEY>.txt` in dist/ during build).
"""

import os
from scripts.utils import DATA_DIR, http_post_json, load_config, load_json, utc_today


def main() -> None:
    key = os.getenv("INDEXNOW_KEY", "").strip()
    if not key:
        print("[indexnow_ping] INDEXNOW_KEY not set; skipping IndexNow notification.")
        return

    cfg = load_config()
    host = cfg["domain"]
    base = cfg["base_url"].rstrip("/")
    today = utc_today()

    published = load_json(DATA_DIR / "published_pages.json", default={})
    changed_urls = [
        base + u
        for u, meta in published.items()
        if meta.get("index_policy") == "index" and str(meta.get("lastmod", "")).startswith(today)
    ][:100]

    if not changed_urls:
        print("[indexnow_ping] No indexable URLs changed today; nothing to submit.")
        return

    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{base}/{key}.txt",
        "urlList": changed_urls,
    }
    http_post_json("https://api.indexnow.org/indexnow", payload, timeout=20)
    print(f"[indexnow_ping] Submitted {len(changed_urls)} changed URL(s) to IndexNow.")


if __name__ == "__main__":
    main()
