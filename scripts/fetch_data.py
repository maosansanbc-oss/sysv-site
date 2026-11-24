"""Fetch real-time public LLM pricing (OpenRouter) and open-source infra repos (GitHub).

Designed for zero-cost daily GitHub Actions execution:
- No paid APIs required.
- Automatically falls back to existing data or seed data if network is unavailable.
"""

import os
import urllib.parse
from typing import Any

from scripts.utils import DATA_DIR, http_get_json, load_config, load_json, save_json, slugify, utc_today

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


def _to_price_per_m(raw_price: Any) -> float:
    try:
        val = float(raw_price)
        if val < 0:
            return 0.0
        return round(val * 1_000_000, 4)
    except (TypeError, ValueError):
        return 0.0


def fetch_openrouter_models(max_models: int) -> list[dict[str, Any]]:
    payload = http_get_json(OPENROUTER_MODELS_URL, timeout=25)
    raw_items = payload.get("data", [])
    today = utc_today()

    parsed: list[dict[str, Any]] = []
    for item in raw_items:
        model_id = str(item.get("id", "")).strip()
        if not model_id or "/" not in model_id:
            continue
        pricing = item.get("pricing") or {}
        prompt_m = _to_price_per_m(pricing.get("prompt"))
        completion_m = _to_price_per_m(pricing.get("completion"))
        is_free = (prompt_m == 0.0 and completion_m == 0.0) or model_id.endswith(":free")
        provider = model_id.split("/", 1)[0].replace("-", " ").title()
        arch = item.get("architecture") or {}
        modality = arch.get("modality") or "text->text"
        context_len = int(item.get("context_length") or 0)
        desc = " ".join(str(item.get("description") or "").split())[:360]

        parsed.append(
            {
                "id": model_id,
                "slug": slugify(model_id),
                "name": str(item.get("name") or model_id).strip(),
                "provider": provider,
                "context_length": context_len,
                "prompt_price_per_m": prompt_m,
                "completion_price_per_m": completion_m,
                "is_free": is_free,
                "has_free_tier_note": is_free,
                "modality": modality,
                "description": desc or f"{provider} language model available via unified API routing.",
                "updated_at": today,
            }
        )

    # Prioritize all free models first, then well-known / high-context affordable models
    free_models = [m for m in parsed if m["is_free"]]
    paid_models = [m for m in parsed if not m["is_free"] and m["context_length"] >= 32000]
    free_models.sort(key=lambda m: (-m["context_length"], m["name"]))
    paid_models.sort(key=lambda m: (m["prompt_price_per_m"] + m["completion_price_per_m"], -m["context_length"]))

    selected = free_models[: max_models // 2] + paid_models[: max_models - min(len(free_models), max_models // 2)]
    return selected[:max_models]


def fetch_github_repos(topics: list[str], max_repos: int) -> list[dict[str, Any]]:
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    gh_token = os.getenv("GITHUB_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    today = utc_today()
    seen: dict[str, dict[str, Any]] = {}
    per_topic = max(5, max_repos // max(1, len(topics)))

    for topic in topics:
        query = urllib.parse.quote(f"topic:{topic} stars:>1000 archived:false")
        url = f"{GITHUB_SEARCH_URL}?q={query}&sort=updated&order=desc&per_page={per_topic}"
        payload = http_get_json(url, headers=headers, timeout=25)
        for repo in payload.get("items", []):
            full_name = str(repo.get("full_name") or "").strip()
            if not full_name or full_name in seen:
                continue
            seen[full_name] = {
                "full_name": full_name,
                "slug": slugify(full_name),
                "name": str(repo.get("name") or full_name),
                "owner": str((repo.get("owner") or {}).get("login") or ""),
                "html_url": str(repo.get("html_url") or f"https://github.com/{full_name}"),
                "description": " ".join(str(repo.get("description") or "").split())[:320]
                or "Open-source developer infrastructure project on GitHub.",
                "language": str(repo.get("language") or "Multi"),
                "stars": int(repo.get("stargazers_count") or 0),
                "forks": int(repo.get("forks_count") or 0),
                "topics": list(repo.get("topics") or [])[:6],
                "updated_at": today,
            }

    repos = sorted(seen.values(), key=lambda r: -r["stars"])
    return repos[:max_repos]


def merge_preserving_enrichment(
    incoming: list[dict[str, Any]], existing: list[dict[str, Any]], key_field: str
) -> list[dict[str, Any]]:
    existing_map = {item[key_field]: item for item in existing if key_field in item}
    merged: list[dict[str, Any]] = []
    for item in incoming:
        prev = existing_map.get(item[key_field])
        if prev and "enrichment" in prev:
            item["enrichment"] = prev["enrichment"]
        merged.append(item)
    return merged


def main() -> None:
    cfg = load_config()
    pipe = cfg["pipeline"]

    models_path = DATA_DIR / "models.json"
    repos_path = DATA_DIR / "repos.json"
    existing_models = load_json(models_path, default=load_json(DATA_DIR / "seed_models.json", default=[]))
    existing_repos = load_json(repos_path, default=load_json(DATA_DIR / "seed_repos.json", default=[]))

    try:
        fresh_models = fetch_openrouter_models(pipe["max_models_tracked"])
        merged_models = merge_preserving_enrichment(fresh_models, existing_models, "id")
        save_json(models_path, merged_models)
        print(f"[fetch_data] Updated {len(merged_models)} models from OpenRouter API.")
    except Exception as err:
        print(f"[fetch_data] OpenRouter fetch skipped ({err}); preserving {len(existing_models)} existing/seed models.")
        if not models_path.exists():
            save_json(models_path, existing_models)

    try:
        fresh_repos = fetch_github_repos(pipe["github_topics"], pipe["max_repos_tracked"])
        merged_repos = merge_preserving_enrichment(fresh_repos, existing_repos, "full_name")
        save_json(repos_path, merged_repos)
        print(f"[fetch_data] Updated {len(merged_repos)} repos from GitHub API.")
    except Exception as err:
        print(f"[fetch_data] GitHub fetch skipped ({err}); preserving {len(existing_repos)} existing/seed repos.")
        if not repos_path.exists():
            save_json(repos_path, existing_repos)


if __name__ == "__main__":
    main()
