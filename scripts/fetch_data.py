"""V2 Data Pipeline for SysV Radar (sysv.cn).

Fixes V1 P0/P1 audit findings:
1. Strict 7-state pricing taxonomy (free, free_router, variable, paid, media, unknown)
   — NEVER coerces OpenRouter '-1' dynamic routers or audio/media endpoints into FREE ($0).
2. Daily historical price snapshots (`data/history/YYYY-MM-DD.json`) + automated changelog
   (`data/price_changes.json`) for long-term proprietary data moat.
3. Curated Zero-Cost Infrastructure & AI Automation GitHub repo directory with real
   deployment metadata (License/Commercial status, Maintenance Health, Docker signal, Category).
4. Fail-safe retention: keeps last valid dataset if upstream API returns empty/degraded data.
"""

import os
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from scripts.utils import DATA_DIR, http_get_json, load_config, load_json, save_json, slugify, utc_today

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
HISTORY_DIR = DATA_DIR / "history"

ROUTER_KEYWORDS = ("auto", "router", "fusion", "pareto", "bodybuilder", "switchpoint")
MEDIA_KEYWORDS = ("lyria", "dall-e", "imagen", "flux", "stable-diffusion", "midjourney", "whisper", "tts", "music")
CODING_KEYWORDS = ("coder", "codestral", "code", "devstral", "starcoder", "qwen3-coder", "deepseek-v3", "claude", "sonnet")
REASONING_KEYWORDS = ("r1", "reason", "thinking", "o1", "o3", "o4", "qwq", "think")

# Focused single-topic queries for Zero-Cost Infra & AI Automation (GitHub Search API safe)
CURATED_REPO_QUERIES = [
    ("AI Gateway & Proxy", "topic:llm-gateway stars:>300 archived:false"),
    ("AI Gateway & Proxy", "topic:openai-proxy stars:>300 archived:false"),
    ("Local LLM Runtime", "topic:local-llm stars:>1000 archived:false"),
    ("Local LLM Runtime", "topic:ollama stars:>2000 archived:false"),
    ("Workflow Automation", "topic:workflow-automation stars:>2000 archived:false"),
    ("AI Agent Infra", "topic:ai-agents topic:self-hosted stars:>500 archived:false"),
    ("Self-Hosted Infra", "topic:self-hosted topic:docker stars:>8000 archived:false"),
]

COMMERCIAL_FRIENDLY_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "Unlicense", "0BSD"}


def _parse_raw_float(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _per_m(val: float | None) -> float | None:
    if val is None or val < 0:
        return None
    return round(val * 1_000_000, 6)


def classify_model(item: dict[str, Any]) -> dict[str, Any]:
    """Classify an OpenRouter model with strict 7-state pricing & task cohort rules."""
    model_id = str(item.get("id", "")).strip()
    name = str(item.get("name") or model_id).strip()
    lower_id = model_id.lower()
    lower_name = name.lower()
    pricing = item.get("pricing") or {}

    raw_prompt = _parse_raw_float(pricing.get("prompt"))
    raw_completion = _parse_raw_float(pricing.get("completion"))
    raw_request = _parse_raw_float(pricing.get("request"))
    raw_image = _parse_raw_float(pricing.get("image"))
    raw_cache_read = _parse_raw_float(pricing.get("input_cache_read"))

    arch = item.get("architecture") or {}
    modality = str(arch.get("modality") or "text->text").strip()
    output_mod = modality.split("->")[-1].lower() if "->" in modality else "text"
    context_len = int(item.get("context_length") or 0)
    provider = model_id.split("/", 1)[0].replace("-", " ").title()
    desc = " ".join(str(item.get("description") or "").split())[:380]

    # 1. Strict 7-state pricing taxonomy
    is_router_name = (
        lower_id.startswith("openrouter/")
        or any(k in lower_id for k in ROUTER_KEYWORDS)
        or "router" in lower_name
    )
    is_non_text_output = ("audio" in output_mod) or ("image" in output_mod) or ("video" in output_mod)
    is_media_name = any(k in lower_id for k in MEDIA_KEYWORDS)

    if lower_id == "openrouter/free":
        pricing_type = "free_router"
        pricing_label = "Free Router"
    elif (raw_prompt is not None and raw_prompt < 0) or (raw_completion is not None and raw_completion < 0):
        pricing_type = "variable"
        pricing_label = "Variable (Routed)"
    elif is_router_name and (raw_prompt == 0.0 and raw_completion == 0.0):
        pricing_type = "variable"
        pricing_label = "Variable (Routed)"
    elif is_non_text_output or is_media_name or (raw_image is not None and raw_image > 0) or (raw_request is not None and raw_request > 0 and (raw_prompt or 0) == 0):
        pricing_type = "media"
        pricing_label = "Media / Per-Request"
    elif raw_prompt is None or raw_completion is None:
        pricing_type = "unknown"
        pricing_label = "Unknown"
    elif raw_prompt == 0.0 and raw_completion == 0.0:
        pricing_type = "free"
        pricing_label = "Free ($0)"
    else:
        pricing_type = "paid"
        pricing_label = "Paid"

    prompt_m = _per_m(raw_prompt) if pricing_type in ("free", "free_router", "paid") else None
    completion_m = _per_m(raw_completion) if pricing_type in ("free", "free_router", "paid") else None
    cache_read_m = _per_m(raw_cache_read) if raw_cache_read is not None and raw_cache_read >= 0 else None

    # 2. Task cohort classification (for meaningful same-cohort comparisons)
    if pricing_type == "variable" or pricing_type == "free_router":
        task_category = "router"
    elif pricing_type == "media":
        task_category = "media"
    elif pricing_type == "unknown":
        task_category = "unknown"
    elif any(k in lower_id or k in lower_name for k in CODING_KEYWORDS):
        task_category = "coding"
    elif any(k in lower_id or k in lower_name for k in REASONING_KEYWORDS):
        task_category = "reasoning"
    elif pricing_type == "free":
        task_category = "free_general"
    elif (prompt_m or 0) <= 0.40 and (completion_m or 0) <= 1.60:
        task_category = "budget_general"
    elif context_len >= 256000:
        task_category = "long_context"
    else:
        task_category = "flagship_general"

    return {
        "id": model_id,
        "slug": slugify(model_id),
        "name": name,
        "provider": provider,
        "context_length": context_len,
        "pricing_type": pricing_type,
        "pricing_label": pricing_label,
        "is_free": pricing_type == "free",
        "prompt_price_per_m": prompt_m,
        "completion_price_per_m": completion_m,
        "cache_read_price_per_m": cache_read_m,
        "raw_pricing": {
            "prompt": pricing.get("prompt"),
            "completion": pricing.get("completion"),
            "request": pricing.get("request"),
            "image": pricing.get("image"),
            "unit": "USD per token (displayed per 1M tokens)",
            "source": "OpenRouter Public Models API",
        },
        "modality": modality,
        "task_category": task_category,
        "description": desc or f"{provider} model endpoint tracked via OpenRouter registry.",
        "updated_at": utc_today(),
    }


def save_daily_history_and_changelog(all_classified: list[dict[str, Any]]) -> dict[str, Any]:
    """Archive full daily snapshot of ALL tracked endpoints & compute price/free-tier diffs."""
    today = utc_today()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    today_file = HISTORY_DIR / f"{today}.json"

    snapshot = {
        "date": today,
        "total_endpoints": len(all_classified),
        "models": {
            m["id"]: {
                "name": m["name"],
                "slug": m["slug"],
                "provider": m["provider"],
                "pricing_type": m["pricing_type"],
                "prompt_price_per_m": m["prompt_price_per_m"],
                "completion_price_per_m": m["completion_price_per_m"],
                "context_length": m["context_length"],
            }
            for m in all_classified
        },
    }

    # Find latest previous snapshot strictly before today
    prev_files = sorted(p for p in HISTORY_DIR.glob("*.json") if p.stem < today)
    prev_snapshot = load_json(prev_files[-1], default=None) if prev_files else None

    save_json(today_file, snapshot)

    changelog = load_json(DATA_DIR / "price_changes.json", default={"events": [], "snapshot_days": 0})
    events: list[dict[str, Any]] = [ev for ev in changelog.get("events", []) if ev.get("date") != today]

    if prev_snapshot and isinstance(prev_snapshot.get("models"), dict):
        prev_models = prev_snapshot["models"]
        curr_models = snapshot["models"]
        prev_date = prev_snapshot.get("date", "previous")

        for mid, curr in curr_models.items():
            if mid not in prev_models:
                if curr["pricing_type"] in ("free", "paid"):
                    events.append(
                        {
                            "date": today,
                            "compared_to": prev_date,
                            "type": "new_free" if curr["pricing_type"] == "free" else "new_model",
                            "model_id": mid,
                            "name": curr["name"],
                            "slug": curr["slug"],
                            "detail": f"Added ({curr['pricing_type']}) — In: {curr['prompt_price_per_m']} / Out: {curr['completion_price_per_m']} per 1M",
                        }
                    )
                continue

            old = prev_models[mid]
            if old["pricing_type"] != curr["pricing_type"]:
                events.append(
                    {
                        "date": today,
                        "compared_to": prev_date,
                        "type": "tier_change",
                        "model_id": mid,
                        "name": curr["name"],
                        "slug": curr["slug"],
                        "detail": f"Pricing tier changed: {old['pricing_type']} -> {curr['pricing_type']}",
                    }
                )
            elif curr["pricing_type"] == "paid" and old["pricing_type"] == "paid":
                old_in, new_in = old.get("prompt_price_per_m"), curr.get("prompt_price_per_m")
                old_out, new_out = old.get("completion_price_per_m"), curr.get("completion_price_per_m")
                if old_in is not None and new_in is not None and (old_in != new_in or old_out != new_out):
                    direction = "price_drop" if (new_in + (new_out or 0)) < (old_in + (old_out or 0)) else "price_hike"
                    events.append(
                        {
                            "date": today,
                            "compared_to": prev_date,
                            "type": direction,
                            "model_id": mid,
                            "name": curr["name"],
                            "slug": curr["slug"],
                            "detail": f"Input ${old_in} -> ${new_in}, Output ${old_out} -> ${new_out} per 1M",
                        }
                    )

        for mid, old in prev_models.items():
            if mid not in curr_models and old.get("pricing_type") == "free":
                events.append(
                    {
                        "date": today,
                        "compared_to": prev_date,
                        "type": "free_removed",
                        "model_id": mid,
                        "name": old["name"],
                        "slug": old["slug"],
                        "detail": "Removed from OpenRouter active free registry",
                    }
                )

    all_snapshot_files = sorted(HISTORY_DIR.glob("*.json"))
    changelog_out = {
        "updated_at": today,
        "snapshot_days": len(all_snapshot_files),
        "first_snapshot_date": all_snapshot_files[0].stem if all_snapshot_files else today,
        "latest_total_endpoints": len(all_classified),
        "events": sorted(events, key=lambda x: x["date"], reverse=True)[:300],
    }
    save_json(DATA_DIR / "price_changes.json", changelog_out)
    return changelog_out


def fetch_openrouter_models(max_models: int, min_floor: int) -> list[dict[str, Any]]:
    payload = http_get_json(OPENROUTER_MODELS_URL, timeout=25)
    raw_items = payload.get("data", [])
    if len(raw_items) < min_floor:
        raise RuntimeError(f"OpenRouter returned suspiciously few models ({len(raw_items)} < {min_floor})")

    all_classified = [classify_model(item) for item in raw_items if item.get("id") and "/" in str(item.get("id"))]
    save_daily_history_and_changelog(all_classified)

    # Select balanced high-value set for site pages (exclude 'unknown' noise)
    free_models = [m for m in all_classified if m["pricing_type"] == "free"]
    free_routers = [m for m in all_classified if m["pricing_type"] == "free_router"]
    paid_models = [m for m in all_classified if m["pricing_type"] == "paid" and m["context_length"] >= 32000]
    var_routers = [m for m in all_classified if m["pricing_type"] == "variable"]

    free_models.sort(key=lambda m: (-m["context_length"], m["name"]))
    # Prefer widely searched flagship & budget paid models
    paid_models.sort(
        key=lambda m: (
            0 if m["provider"].lower() in ("openai", "anthropic", "google", "deepseek", "qwen", "meta", "mistralai", "x-ai") else 1,
            (m["prompt_price_per_m"] or 999) + (m["completion_price_per_m"] or 999),
            -m["context_length"],
        )
    )

    selected: list[dict[str, Any]] = []
    selected.extend(free_models[:45])
    selected.extend(free_routers[:3])
    selected.extend(paid_models[: max(60, max_models - len(selected) - 4)])
    selected.extend(var_routers[:4])
    return selected[:max_models]


def _maintenance_status(pushed_at: str) -> tuple[str, int]:
    if not pushed_at:
        return "Unknown", 999
    try:
        dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        days = max(0, (datetime.now(timezone.utc) - dt).days)
        if days <= 30:
            return "Active (<30d)", days
        if days <= 180:
            return "Maintained (<6m)", days
        return f"Stale ({days}d)", days
    except Exception:
        return "Unknown", 999


def _infer_deployment_tier(topics: list[str], desc: str, lang: str) -> tuple[bool, str]:
    blob = (" ".join(topics) + " " + desc).lower()
    has_docker = "docker" in blob or "compose" in blob or "container" in blob or "self-hosted" in blob
    if any(k in blob for k in ("cuda", "vllm", "gpu", "tensorrt", "inference engine", "gguf")):
        tier = "GPU / Local Hardware"
    elif has_docker and lang.lower() in ("go", "rust", "python", "typescript", "javascript"):
        tier = "Low-RAM VPS / Docker Ready"
    elif lang.lower() in ("go", "rust", "c", "c++"):
        tier = "Lightweight Binary / VPS Ready"
    else:
        tier = "Standard VPS / Self-Hosted"
    return has_docker, tier


def fetch_github_repos(max_repos: int, prev_repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    gh_token = os.getenv("GITHUB_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    prev_stars_map = {r["full_name"]: int(r.get("stars", 0)) for r in prev_repos if "full_name" in r}
    today = utc_today()
    seen: dict[str, dict[str, Any]] = {}
    per_cat = max(8, max_repos // len(CURATED_REPO_QUERIES) + 2)

    for category, raw_query in CURATED_REPO_QUERIES:
        query = urllib.parse.quote(raw_query)
        url = f"{GITHUB_SEARCH_URL}?q={query}&sort=stars&order=desc&per_page={per_cat}"
        try:
            payload = http_get_json(url, headers=headers, timeout=25)
        except Exception as err:
            print(f"[fetch_data] Query '{raw_query}' skipped: {err}")
            continue
        for repo in payload.get("items", []):
            full_name = str(repo.get("full_name") or "").strip()
            if not full_name or full_name in seen or repo.get("archived"):
                continue
            desc = " ".join(str(repo.get("description") or "").split())[:320]
            topics = [str(t) for t in (repo.get("topics") or [])][:6]
            lang = str(repo.get("language") or "Multi")
            stars = int(repo.get("stargazers_count") or 0)
            pushed_at = str(repo.get("pushed_at") or "")
            maint_label, days_since_push = _maintenance_status(pushed_at)
            # Exclude abandoned repos (> 365 days without push)
            if days_since_push > 365:
                continue

            lic_obj = repo.get("license") or {}
            spdx = str(lic_obj.get("spdx_id") or "Unknown")
            commercial_friendly = spdx in COMMERCIAL_FRIENDLY_LICENSES
            has_docker, deploy_tier = _infer_deployment_tier(topics, desc, lang)
            prev_stars = prev_stars_map.get(full_name)
            star_delta = (stars - prev_stars) if prev_stars is not None else 0

            seen[full_name] = {
                "full_name": full_name,
                "slug": slugify(full_name),
                "name": str(repo.get("name") or full_name),
                "owner": str((repo.get("owner") or {}).get("login") or ""),
                "html_url": str(repo.get("html_url") or f"https://github.com/{full_name}"),
                "description": desc or "Open-source self-hosted AI & developer infrastructure.",
                "category": category,
                "language": lang,
                "stars": stars,
                "star_delta": star_delta,
                "forks": int(repo.get("forks_count") or 0),
                "open_issues": int(repo.get("open_issues_count") or 0),
                "license": spdx,
                "commercial_friendly": commercial_friendly,
                "has_docker": has_docker,
                "deployment_tier": deploy_tier,
                "pushed_at": pushed_at[:10] if pushed_at else today,
                "maintenance_status": maint_label,
                "topics": topics,
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
        fresh_models = fetch_openrouter_models(pipe["max_models_tracked"], pipe.get("min_models_health_floor", 40))
        merged_models = merge_preserving_enrichment(fresh_models, existing_models, "id")
        save_json(models_path, merged_models)
        free_n = sum(1 for m in merged_models if m["pricing_type"] == "free")
        var_n = sum(1 for m in merged_models if m["pricing_type"] == "variable")
        print(f"[fetch_data] Updated {len(merged_models)} models (verified free={free_n}, variable_routers={var_n}).")
    except Exception as err:
        print(f"[fetch_data] OpenRouter fetch skipped ({err}); preserving {len(existing_models)} last-known-good models.")
        if not models_path.exists():
            save_json(models_path, existing_models)

    try:
        fresh_repos = fetch_github_repos(pipe["max_repos_tracked"], existing_repos)
        if len(fresh_repos) >= 10:
            merged_repos = merge_preserving_enrichment(fresh_repos, existing_repos, "full_name")
            save_json(repos_path, merged_repos)
            print(f"[fetch_data] Updated {len(merged_repos)} curated infra repos from GitHub API.")
        else:
            print(f"[fetch_data] Too few repos ({len(fresh_repos)}); preserving last-known-good repos.")
    except Exception as err:
        print(f"[fetch_data] GitHub fetch skipped ({err}); preserving {len(existing_repos)} last-known-good repos.")
        if not repos_path.exists():
            save_json(repos_path, existing_repos)


if __name__ == "__main__":
    main()
