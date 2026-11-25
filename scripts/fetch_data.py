"""V2.2 Data Pipeline for SysV (sysv.cn).

V2.2 P0 Hardening:
- Strict Evidence Typing on every record:
  `Source Verified` (official API metadata), `Repository Verified` (confirmed root files),
  `Runtime Tested` (reserved for real runtime runs — never claimed without execution), `Inferred`.
- Stops inferring Docker support from 'self-hosted' description keywords; inspects actual
  repository root files (`Dockerfile`, `docker-compose.yml`, `compose.yml`, etc.) via GitHub API.
- Stops guessing RAM/VPS specs from programming language; honestly reports `Not Verified (Check Repo Docs)`.
- Preserves full 8-state pricing taxonomy, capability flags, 3-day delisting lifecycle,
  append-only intraday price history, and daily ops ledger.
"""

import os
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from scripts.utils import DATA_DIR, http_get_json, load_config, load_json, save_json, slugify, utc_today

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
HISTORY_DIR = DATA_DIR / "history"

ROUTER_KEYWORDS = ("auto", "router", "fusion", "pareto", "bodybuilder", "switchyard", "switchpoint")
MEDIA_KEYWORDS = ("lyria", "dall-e", "imagen", "flux", "stable-diffusion", "midjourney", "whisper", "tts", "music")
CODING_KEYWORDS = ("coder", "codestral", "code", "devstral", "starcoder", "deepseek-v3", "claude", "sonnet")
REASONING_KEYWORDS = ("r1", "reason", "thinking", "o1", "o3", "o4", "qwq", "think")

DOCKER_ROOT_FILENAMES = {
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
}

CURATED_REPO_QUERIES = [
    ("AI Gateway & Proxy", "topic:llm-gateway stars:>300 archived:false"),
    ("AI Gateway & Proxy", "topic:openai-proxy stars:>300 archived:false"),
    ("Local LLM Runtime", "topic:local-llm stars:>1000 archived:false"),
    ("Local LLM Runtime", "topic:ollama stars:>2000 archived:false"),
    ("Workflow Automation", "topic:workflow-automation stars:>2000 archived:false"),
    ("AI Agent Infra", "topic:ai-agents topic:self-hosted stars:>500 archived:false"),
    ("Self-Hosted Infra", "topic:self-hosted topic:docker stars:>8000 archived:false"),
]

PERMISSIVE_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "Unlicense", "0BSD"}
COPYLEFT_OR_SOURCE_AVAIL = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0", "SSPL-1.0", "NOASSERTION"}


def _parse_raw_float(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            val = float(s)
            if val != val or val in (float("inf"), float("-inf")):
                return None
            return val
        except ValueError:
            return None
    return None


def _per_m(val: float | None) -> float | None:
    if val is None or val < 0:
        return None
    return round(val * 1_000_000, 6)


def classify_model(item: dict[str, Any], today: str | None = None) -> dict[str, Any]:
    """Classify an OpenRouter endpoint into strict 8-state pricing & Source Verified capabilities."""
    today = today or utc_today()
    model_id = str(item.get("id", "")).strip()
    name = str(item.get("name") or model_id).strip()
    lower_id = model_id.lower()
    lower_name = name.lower()
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}

    raw_prompt = _parse_raw_float(pricing.get("prompt"))
    raw_completion = _parse_raw_float(pricing.get("completion"))
    raw_request = _parse_raw_float(pricing.get("request"))
    raw_image = _parse_raw_float(pricing.get("image"))
    raw_cache_read = _parse_raw_float(pricing.get("input_cache_read"))
    raw_cache_write = _parse_raw_float(pricing.get("input_cache_write"))
    has_tiered_overrides = bool(pricing.get("overrides"))

    arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    modality = str(arch.get("modality") or "text->text").strip()
    output_mod = modality.split("->")[-1].lower() if "->" in modality else "text"
    context_len = int(item.get("context_length") or 0)
    provider = model_id.split("/", 1)[0].replace("-", " ").title() if "/" in model_id else "Unknown"
    desc = " ".join(str(item.get("description") or "").split())[:380]

    params = set(str(p).lower() for p in (item.get("supported_parameters") or []))
    supports_tools = "tools" in params
    supports_structured = "structured_outputs" in params
    supports_response_format = "response_format" in params
    supports_reasoning = ("reasoning" in params) or ("include_reasoning" in params)

    is_batch_endpoint = lower_id.endswith(":batch") or ":batch" in lower_id
    is_free_suffix = lower_id.endswith(":free")
    is_router_name = (
        lower_id.startswith("openrouter/")
        or any(k in lower_id for k in ROUTER_KEYWORDS)
        or "router" in lower_name
    )
    is_non_text_output = ("audio" in output_mod) or ("image" in output_mod) or ("video" in output_mod)
    is_media_name = any(k in lower_id for k in MEDIA_KEYWORDS)

    if lower_id == "openrouter/free":
        pricing_type = "free_router"
        pricing_label = "Free Router (Rate-Limited)"
        rate_limit_note = "Routes across active $0 endpoints; subject to OpenRouter 20 RPM and 50 RPD (or 1,000 RPD with >=$10 lifetime credits)."
    elif (raw_prompt is not None and raw_prompt < 0) or (raw_completion is not None and raw_completion < 0):
        pricing_type = "variable"
        pricing_label = "Variable (Routed)"
        rate_limit_note = "Dynamic routing (raw price -1): billed at whichever downstream paid model handles the request."
    elif is_router_name and (raw_prompt == 0.0 and raw_completion == 0.0):
        pricing_type = "variable"
        pricing_label = "Variable (Routed)"
        rate_limit_note = "Dynamic routing endpoint; verify downstream billing."
    elif is_non_text_output or is_media_name or (raw_image is not None and raw_image > 0 and not is_batch_endpoint and (raw_prompt or 0) == 0) or (raw_request is not None and raw_request > 0 and (raw_prompt or 0) == 0):
        pricing_type = "media"
        pricing_label = "Media / Per-Request"
        rate_limit_note = "Non-text output or per-request media billing; standard text token calculators do not apply."
    elif raw_prompt is None or raw_completion is None:
        pricing_type = "unknown"
        pricing_label = "Unknown"
        rate_limit_note = "Upstream pricing metadata missing or malformed."
    elif is_batch_endpoint:
        pricing_type = "batch"
        pricing_label = "Batch (Async Only)"
        rate_limit_note = "Asynchronous batch endpoint (typically up to 24h turnaround, ~50% off sync rates); NOT for real-time agents."
    elif raw_prompt == 0.0 and raw_completion == 0.0:
        if is_free_suffix:
            pricing_type = "limited_free"
            pricing_label = "Limited Free Tier ($0)"
            rate_limit_note = "OpenRouter :free tier is capped at 20 RPM and 50 RPD without credits (1,000 RPD after >=$10 lifetime credit purchase); NOT unlimited."
        else:
            pricing_type = "free"
            pricing_label = "Free ($0)"
            rate_limit_note = "$0 listed token rate; still subject to upstream concurrency and fair-use caps."
    elif raw_prompt > 0.005 or raw_completion > 0.02:
        pricing_type = "unknown"
        pricing_label = "Unknown (Anomalous)"
        rate_limit_note = "Upstream price exceeded sanity ceiling and was quarantined as unknown."
    else:
        pricing_type = "paid"
        pricing_label = "Paid (Tiered)" if has_tiered_overrides else "Paid"
        rate_limit_note = (
            "Includes tiered long-context price surcharges above threshold prompt length."
            if has_tiered_overrides
            else "Standard real-time per-token API pricing (plus OpenRouter 5.5% card / 5% crypto top-up fee unless BYOK)."
        )

    billable_token_types = ("limited_free", "free", "free_router", "paid", "batch")
    prompt_m = _per_m(raw_prompt) if pricing_type in billable_token_types else None
    completion_m = _per_m(raw_completion) if pricing_type in billable_token_types else None
    cache_read_m = _per_m(raw_cache_read) if raw_cache_read is not None and raw_cache_read >= 0 else None
    cache_write_m = _per_m(raw_cache_write) if raw_cache_write is not None and raw_cache_write >= 0 else None

    is_realtime = pricing_type not in ("batch", "media", "unknown")

    if pricing_type in ("variable", "free_router"):
        task_category = "router"
    elif pricing_type == "batch":
        task_category = "batch"
    elif pricing_type == "media":
        task_category = "media"
    elif pricing_type == "unknown":
        task_category = "unknown"
    elif any(k in lower_id or k in lower_name for k in CODING_KEYWORDS):
        task_category = "coding"
    elif supports_reasoning or any(k in lower_id or k in lower_name for k in REASONING_KEYWORDS):
        task_category = "reasoning"
    elif pricing_type in ("limited_free", "free"):
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
        "is_free": pricing_type in ("limited_free", "free"),
        "is_realtime": is_realtime,
        "has_tiered_overrides": has_tiered_overrides,
        "rate_limit_note": rate_limit_note,
        "prompt_price_per_m": prompt_m,
        "completion_price_per_m": completion_m,
        "cache_read_price_per_m": cache_read_m,
        "cache_write_price_per_m": cache_write_m,
        "capabilities": {
            "tools": supports_tools,
            "structured_outputs": supports_structured,
            "response_format": supports_response_format,
            "reasoning": supports_reasoning,
        },
        "evidence": {
            "pricing": "Source Verified",
            "capabilities": "Source Verified",
            "source_url": OPENROUTER_MODELS_URL,
            "verified_at": today,
        },
        "raw_pricing": {
            "prompt": pricing.get("prompt"),
            "completion": pricing.get("completion"),
            "request": pricing.get("request"),
            "image": pricing.get("image"),
            "input_cache_read": pricing.get("input_cache_read"),
            "has_overrides": has_tiered_overrides,
            "unit": "USD per token (displayed per 1M tokens)",
            "source": "OpenRouter Public Models API",
        },
        "modality": modality,
        "task_category": task_category,
        "description": desc or f"{provider} model endpoint tracked via OpenRouter registry.",
        "status": "active",
        "missing_days": 0,
        "first_seen": today,
        "last_seen": today,
        "delisted_at": None,
        "updated_at": today,
    }


def save_daily_history_and_changelog(
    all_classified: list[dict[str, Any]],
    history_dir=HISTORY_DIR,
    changelog_path=DATA_DIR / "price_changes.json",
    today: str | None = None,
) -> dict[str, Any]:
    """Archive full daily snapshot & append-only intraday changelog."""
    today = today or utc_today()
    history_dir.mkdir(parents=True, exist_ok=True)
    today_file = history_dir / f"{today}.json"

    curr_models_map = {
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
    }

    if today_file.exists():
        baseline_snapshot = load_json(today_file, default=None)
    else:
        prev_files = sorted(p for p in history_dir.glob("*.json") if p.stem < today)
        baseline_snapshot = load_json(prev_files[-1], default=None) if prev_files else None

    snapshot = {"date": today, "total_endpoints": len(all_classified), "models": curr_models_map}
    save_json(today_file, snapshot)

    changelog = load_json(changelog_path, default={"events": [], "snapshot_days": 0})
    existing_events: list[dict[str, Any]] = list(changelog.get("events", []))
    seen_keys = {(ev.get("date"), ev.get("model_id"), ev.get("type"), ev.get("detail")) for ev in existing_events}

    def add_event(ev: dict[str, Any]) -> None:
        key = (ev["date"], ev["model_id"], ev["type"], ev["detail"])
        if key not in seen_keys:
            seen_keys.add(key)
            existing_events.append(ev)

    if baseline_snapshot and isinstance(baseline_snapshot.get("models"), dict):
        prev_models = baseline_snapshot["models"]
        prev_date = baseline_snapshot.get("date", "previous")

        for mid, curr in curr_models_map.items():
            if mid not in prev_models:
                if curr["pricing_type"] in ("limited_free", "free", "paid", "batch"):
                    add_event(
                        {
                            "date": today,
                            "compared_to": prev_date,
                            "type": "new_free" if curr["pricing_type"] in ("limited_free", "free") else "new_model",
                            "model_id": mid,
                            "name": curr["name"],
                            "slug": curr["slug"],
                            "detail": f"Added ({curr['pricing_type']}) — In: {curr['prompt_price_per_m']} / Out: {curr['completion_price_per_m']} per 1M",
                        }
                    )
                continue

            old = prev_models[mid]
            if old["pricing_type"] != curr["pricing_type"]:
                add_event(
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
            elif curr["pricing_type"] in ("paid", "batch") and old["pricing_type"] == curr["pricing_type"]:
                old_in, new_in = old.get("prompt_price_per_m"), curr.get("prompt_price_per_m")
                old_out, new_out = old.get("completion_price_per_m"), curr.get("completion_price_per_m")
                if old_in is not None and new_in is not None and (old_in != new_in or old_out != new_out):
                    direction = "price_drop" if (new_in + (new_out or 0)) < (old_in + (old_out or 0)) else "price_hike"
                    add_event(
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

        if len(curr_models_map) >= 250:
            for mid, old in prev_models.items():
                if mid not in curr_models_map and old.get("pricing_type") in ("limited_free", "free"):
                    add_event(
                        {
                            "date": today,
                            "compared_to": prev_date,
                            "type": "free_removed",
                            "model_id": mid,
                            "name": old["name"],
                            "slug": old["slug"],
                            "detail": "Absent from OpenRouter active free registry",
                        }
                    )

    all_snapshot_files = sorted(history_dir.glob("*.json"))
    changelog_out = {
        "updated_at": today,
        "snapshot_days": len(all_snapshot_files),
        "first_snapshot_date": all_snapshot_files[0].stem if all_snapshot_files else today,
        "latest_total_endpoints": len(all_classified),
        "events": sorted(existing_events, key=lambda x: x["date"], reverse=True)[:500],
    }
    save_json(changelog_path, changelog_out)
    return changelog_out


def reconcile_model_lifecycle(
    selected_active: list[dict[str, Any]],
    all_live_ids: set[str],
    existing_models: list[dict[str, Any]],
    *,
    is_full_healthy_fetch: bool,
    today: str | None = None,
) -> list[dict[str, Any]]:
    """Merge live models with existing records while preserving permanent SEO URLs."""
    today = today or utc_today()
    existing_map = {m["id"]: m for m in existing_models if "id" in m}
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for m in selected_active:
        prev = existing_map.get(m["id"])
        if prev:
            m["first_seen"] = prev.get("first_seen") or today
            if "enrichment" in prev:
                m["enrichment"] = prev["enrichment"]
        m["status"] = "active"
        m["missing_days"] = 0
        m["last_seen"] = today
        m["delisted_at"] = None
        merged.append(m)
        seen_ids.add(m["id"])

    for prev_id, prev in existing_map.items():
        if prev_id in seen_ids:
            continue
        if prev_id in all_live_ids:
            prev["status"] = "active"
            prev["missing_days"] = 0
            prev["last_seen"] = today
            merged.append(prev)
            continue

        if is_full_healthy_fetch:
            prev_last = prev.get("last_seen", today)
            miss = int(prev.get("missing_days", 0))
            if prev_last != today:
                miss += 1
            elif miss == 0:
                miss = 1
            prev["missing_days"] = miss
            if miss >= 3:
                prev["status"] = "delisted"
                prev["delisted_at"] = prev.get("delisted_at") or today
            else:
                prev["status"] = "temporarily_missing"
        merged.append(prev)

    return merged


def fetch_openrouter_models(max_models: int, min_floor: int, existing_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = http_get_json(OPENROUTER_MODELS_URL, timeout=25)
    raw_items = payload.get("data", [])
    if len(raw_items) < min_floor:
        raise RuntimeError(f"OpenRouter returned too few models ({len(raw_items)} < {min_floor})")

    today = utc_today()
    all_classified = [classify_model(item, today=today) for item in raw_items if item.get("id") and "/" in str(item.get("id"))]
    save_daily_history_and_changelog(all_classified, today=today)

    type_counts = Counter(m["pricing_type"] for m in all_classified)
    limited_free = [m for m in all_classified if m["pricing_type"] in ("limited_free", "free")]
    free_routers = [m for m in all_classified if m["pricing_type"] == "free_router"]
    paid_models = [m for m in all_classified if m["pricing_type"] == "paid" and m["context_length"] >= 32000]
    batch_models = [m for m in all_classified if m["pricing_type"] == "batch"]
    var_routers = [m for m in all_classified if m["pricing_type"] == "variable"]

    limited_free.sort(key=lambda m: (-m["context_length"], m["name"]))
    major_providers = ("openai", "anthropic", "google", "deepseek", "qwen", "meta", "mistralai", "x-ai")
    paid_models.sort(
        key=lambda m: (
            0 if m["provider"].lower() in major_providers else 1,
            (m["prompt_price_per_m"] or 999) + (m["completion_price_per_m"] or 999),
            -m["context_length"],
        )
    )
    batch_models.sort(key=lambda m: ((m["prompt_price_per_m"] or 999) + (m["completion_price_per_m"] or 999), m["name"]))

    selected: list[dict[str, Any]] = []
    selected.extend(limited_free)
    selected.extend(free_routers)
    selected.extend(paid_models[: max(90, max_models - len(selected) - 12)])
    selected.extend(batch_models[:8])
    selected.extend(var_routers[:4])
    selected = selected[:max_models]

    audit_info = {
        "updated_at": today,
        "total_raw_endpoints": len(all_classified),
        "raw_breakdown_by_pricing_type": dict(type_counts),
        "displayed_active_cap": max_models,
        "selected_for_display": len(selected),
        "selection_rationale": (
            "All active OpenRouter endpoints are archived daily in data/history/. "
            "Published model detail pages are curated to ~140 active endpoints (all verified :free models, "
            "major/affordable real-time paid models with >=32K context, top async :batch endpoints, and flagship routers) "
            "to respect search engine crawl budget and prevent duplicate legacy bloat."
        ),
    }
    save_json(DATA_DIR / "selection_audit.json", audit_info)

    all_live_ids = {m["id"] for m in all_classified}
    return reconcile_model_lifecycle(
        selected,
        all_live_ids,
        existing_models,
        is_full_healthy_fetch=(len(all_classified) >= 250),
        today=today,
    )


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


def _classify_license(spdx: str) -> tuple[str, str]:
    if spdx in PERMISSIVE_LICENSES:
        return "permissive", f"Permissive ({spdx} — attribution/notice required; Source Verified via GitHub API)"
    if spdx in COPYLEFT_OR_SOURCE_AVAIL:
        return "restricted", f"Copyleft / Source-Available ({spdx} — review hosted/SaaS obligations; Source Verified)"
    return "unknown", f"Verify LICENSE file directly ({spdx})"


def verify_repo_docker_files(full_name: str, headers: dict[str, str], root_files: list[str] | None = None) -> dict[str, Any]:
    """Verify actual Dockerfile / Compose files in repo root. Never infers Docker from 'self-hosted'."""
    names: list[str] = []
    verified_repo = False
    if root_files is not None:
        names = [str(n) for n in root_files]
        verified_repo = True
    else:
        try:
            url = f"https://api.github.com/repos/{full_name}/contents"
            items = http_get_json(url, headers=headers, timeout=12, retries=0)
            if isinstance(items, list):
                names = [str(it.get("name", "")) for it in items if isinstance(it, dict)]
                verified_repo = True
        except Exception:
            verified_repo = False

    matched = sorted([n for n in names if n.lower() in DOCKER_ROOT_FILENAMES])
    if verified_repo and matched:
        return {
            "has_docker": True,
            "docker_files": matched,
            "docker_evidence": "Repository Verified",
            "docker_status": f"Verified ({', '.join(matched[:2])})",
        }
    if verified_repo and not matched:
        return {
            "has_docker": False,
            "docker_files": [],
            "docker_evidence": "Repository Verified",
            "docker_status": "No Root Dockerfile / Compose Found",
        }
    return {
        "has_docker": False,
        "docker_files": [],
        "docker_evidence": "Not Verified",
        "docker_status": "Not Verified (Check Repo Docs)",
    }


def fetch_github_repos(max_repos: int, prev_repos: list[dict[str, Any]], verify_docker: bool = True) -> list[dict[str, Any]]:
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    gh_token = os.getenv("GITHUB_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    prev_map = {r["full_name"]: r for r in prev_repos if "full_name" in r}
    today = utc_today()
    seen: dict[str, dict[str, Any]] = {}
    per_cat = max(8, max_repos // len(CURATED_REPO_QUERIES) + 2)
    docker_checks_done = 0
    docker_rate_limited = False

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
            if days_since_push > 365:
                continue

            lic_obj = repo.get("license") or {}
            spdx = str(lic_obj.get("spdx_id") or "Unknown")
            lic_tier, lic_note = _classify_license(spdx)

            prev = prev_map.get(full_name)
            prev_stars = int(prev.get("stars", 0)) if prev else None
            star_delta = (stars - prev_stars) if prev_stars is not None else 0

            # Reuse Repository Verified Docker check unless missing or push date changed.
            # When unauthenticated (60 req/hr GitHub cap), limit /contents checks to avoid rate-limit storms.
            can_check_docker = verify_docker and (bool(gh_token) or docker_checks_done < 8) and not docker_rate_limited
            if prev and prev.get("docker_evidence") == "Repository Verified" and prev.get("pushed_at") == pushed_at[:10]:
                docker_info = {
                    "has_docker": bool(prev.get("has_docker")),
                    "docker_files": list(prev.get("docker_files") or []),
                    "docker_evidence": "Repository Verified",
                    "docker_status": str(prev.get("docker_status") or "Repository Verified"),
                }
            elif can_check_docker and len(seen) < max_repos:
                docker_info = verify_repo_docker_files(full_name, headers)
                docker_checks_done += 1
                if docker_info["docker_evidence"] == "Not Verified":
                    docker_rate_limited = True
            else:
                docker_info = {
                    "has_docker": False,
                    "docker_files": [],
                    "docker_evidence": "Not Verified",
                    "docker_status": "Not Verified (Check Repo Docs)",
                }

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
                "license_tier": lic_tier,
                "license_note": lic_note,
                "has_docker": docker_info["has_docker"],
                "docker_files": docker_info["docker_files"],
                "docker_evidence": docker_info["docker_evidence"],
                "docker_status": docker_info["docker_status"],
                "hardware_requirements": "Not Verified (Check Repo Docs)",
                "hardware_evidence": "Not Verified",
                "pushed_at": pushed_at[:10] if pushed_at else today,
                "maintenance_status": maint_label,
                "topics": topics,
                "evidence": {
                    "metadata": "Source Verified",
                    "docker": docker_info["docker_evidence"],
                    "hardware": "Not Verified",
                    "source_url": str(repo.get("html_url") or f"https://github.com/{full_name}"),
                    "verified_at": today,
                },
                "updated_at": today,
            }

    repos = sorted(seen.values(), key=lambda r: -r["stars"])
    return repos[:max_repos]


def merge_repo_enrichment(incoming: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_map = {item["full_name"]: item for item in existing if "full_name" in item}
    merged: list[dict[str, Any]] = []
    for item in incoming:
        prev = existing_map.get(item["full_name"])
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

    fetch_models_ok = False
    try:
        merged_models = fetch_openrouter_models(
            pipe["max_models_tracked"], pipe.get("min_models_health_floor", 40), existing_models
        )
        save_json(models_path, merged_models)
        fetch_models_ok = True
        lf = sum(1 for m in merged_models if m["pricing_type"] == "limited_free" and m["status"] == "active")
        bt = sum(1 for m in merged_models if m["pricing_type"] == "batch" and m["status"] == "active")
        vr = sum(1 for m in merged_models if m["pricing_type"] == "variable" and m["status"] == "active")
        dl = sum(1 for m in merged_models if m["status"] == "delisted")
        print(f"[fetch_data] Updated {len(merged_models)} models (limited_free={lf}, batch={bt}, variable={vr}, delisted={dl}).")
    except Exception as err:
        print(f"[fetch_data] OpenRouter fetch skipped ({err}); preserving {len(existing_models)} last-known-good models.")
        if not models_path.exists():
            save_json(models_path, existing_models)

    fetch_repos_ok = False
    try:
        fresh_repos = fetch_github_repos(
            pipe["max_repos_tracked"], existing_repos, verify_docker=bool(pipe.get("verify_repo_docker_files", True))
        )
        if len(fresh_repos) >= 10:
            merged_repos = merge_repo_enrichment(fresh_repos, existing_repos)
            save_json(repos_path, merged_repos)
            fetch_repos_ok = True
            dv = sum(1 for r in merged_repos if r.get("docker_evidence") == "Repository Verified" and r.get("has_docker"))
            print(f"[fetch_data] Updated {len(merged_repos)} curated infra repos (Repository Verified Docker={dv}).")
        else:
            print(f"[fetch_data] Too few repos ({len(fresh_repos)}); preserving last-known-good repos.")
    except Exception as err:
        print(f"[fetch_data] GitHub fetch skipped ({err}); preserving {len(existing_repos)} last-known-good repos.")
        if not repos_path.exists():
            save_json(repos_path, existing_repos)

    ops_path = DATA_DIR / "ops_metrics.json"
    ops = load_json(ops_path, default={"daily": []})
    today = utc_today()
    entry = next((d for d in ops["daily"] if d.get("date") == today), None)
    if entry is None:
        entry = {
            "date": today,
            "fetch_success": fetch_models_ok,
            "fetch_repos_success": fetch_repos_ok,
            "publish_success": None,
            "llm_calls": 0,
            "llm_failed_calls": 0,
            "llm_unknown_cost_calls": 0,
            "llm_tokens_in": 0,
            "llm_tokens_out": 0,
            "llm_cost_usd": 0.0,
            "llm_unknown_cost_reserve_usd": 0.0,
            "commercial_data_location": "private/commercial_ledger.json (excluded from public repo)",
        }
        ops["daily"].append(entry)
    else:
        entry["fetch_success"] = fetch_models_ok
        entry["fetch_repos_success"] = fetch_repos_ok
    ops["daily"] = sorted(ops["daily"], key=lambda x: x["date"], reverse=True)[:365]
    save_json(ops_path, ops)


if __name__ == "__main__":
    main()
