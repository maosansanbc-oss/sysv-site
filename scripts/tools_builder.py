"""V2.2.1 Shared JS Calculation Engine, 4 Standalone Calculators, Ad-Free Embed Mode & Linkable Data Assets.

V2.2.1 Fixes:
- OpenRouter Cost Calculator: exact 5.5% card fee with $0.80 minimum, 5.0% crypto fee,
  BYOK free allowance ($25,000/mo list inference) vs 5% overage, and explicit 3-line breakdown
  (Inference Cost / Payment or Platform Fee / Total Cash Outlay) + Top-Up batch selector.
- Ad-Free Embed Mode: generates `/embed/<tool>/` with ZERO AdSense scripts, ZERO analytics,
  `noindex, follow`, canonical pointing to `/tools/<tool>/`, and updates `/data/` iframe snippet.
"""

import csv
import hashlib
import io
import json
from typing import Any

from scripts import content_quality_gate
from scripts.utils import DATA_DIR

CALC_ENGINE_JS = """/* SysV Shared LLM Cost Calculation Engine (v2.2.1)
 * Parity guaranteed with scripts/build_site.py & tests/test_v221_audit.py.
 */
window.SysVCalc = (function () {
  function isValidPrice(m) {
    return typeof m.in === 'number' && typeof m.out === 'number' && !isNaN(m.in) && !isNaN(m.out) && m.in >= 0 && m.out >= 0;
  }

  function filterEligible(models, task, includeBatch) {
    return models.filter(function (m) {
      if (!isValidPrice(m)) return false;
      var allowed = (m.pricing_type === 'limited_free' || m.pricing_type === 'free' || m.pricing_type === 'paid') ||
        (includeBatch && m.pricing_type === 'batch');
      if (!allowed) return false;
      if (task === 'coding') return !!(m.is_realtime && m.tools && m.ctx >= 64000);
      if (task === 'autosite') return !!(m.is_realtime && m.structured);
      if (task === 'n8n') return !!(m.is_realtime && m.tools);
      if (task === 'rag') return !!(m.is_realtime && m.ctx >= 100000);
      if (task === 'translation') return !!(includeBatch || m.is_realtime);
      return !!(includeBatch || m.is_realtime);
    });
  }

  function estimateMonthlyCost(m, inputMillions, outputMillions) {
    var inM = Math.max(0, Number(inputMillions) || 0);
    var outM = Math.max(0, Number(outputMillions) || 0);
    return inM * m.in + outM * m.out;
  }

  /* OpenRouter fee model (v2.2.1):
   * - card: 5.5% with $0.80 minimum per top-up transaction (0 when topup === 0)
   * - crypto: 5.0% flat
   * - byok: 0% up to $25,000/mo list-price inference; 5.0% on inference volume above $25,000/mo
   * Returns { inferenceCost, topupAmount, platformFee, totalCashOutlay, minFeeTriggered, note }
   */
  function calculateOpenRouterBilling(inferenceCost, billingMethod, topupBatchUsd) {
    var inf = Math.max(0, Number(inferenceCost) || 0);
    var batch = Math.max(0, Number(topupBatchUsd) || 0);
    if (billingMethod === 'byok') {
      var freeCap = 25000.0;
      var overage = Math.max(0, inf - freeCap);
      var fee = overage * 0.05;
      return {
        inferenceCost: inf,
        topupAmount: 0,
        platformFee: fee,
        totalCashOutlay: inf + fee,
        minFeeTriggered: false,
        note: overage > 0 ? 'BYOK 5% fee applied to volume above $25,000/mo' : 'BYOK 0% platform fee (within $25,000/mo allowance; upstream provider bills directly)'
      };
    }
    var chargeBase = batch > 0 ? Math.max(inf, batch) : inf;
    if (chargeBase <= 0) {
      return { inferenceCost: 0, topupAmount: 0, platformFee: 0, totalCashOutlay: 0, minFeeTriggered: false, note: '$0 spend' };
    }
    if (billingMethod === 'crypto') {
      var cFee = chargeBase * 0.05;
      return {
        inferenceCost: inf,
        topupAmount: chargeBase,
        platformFee: cFee,
        totalCashOutlay: chargeBase + cFee,
        minFeeTriggered: false,
        note: '5.0% crypto top-up fee'
      };
    }
    var pctFee = chargeBase * 0.055;
    var minApplied = pctFee < 0.80;
    var cardFee = minApplied ? 0.80 : pctFee;
    return {
      inferenceCost: inf,
      topupAmount: chargeBase,
      platformFee: cardFee,
      totalCashOutlay: chargeBase + cardFee,
      minFeeTriggered: minApplied,
      note: minApplied ? '$0.80 minimum card fee triggered (top up >=$14.55 to reach 5.5%)' : '5.5% credit card top-up fee'
    };
  }

  /* Free LLM API daily & burst quota estimator (v2.2.3 — Single Source of Truth from policyDb):
   * Reads ALL limits, Neuron coefficients, trial flags & verification_status strictly from policyDb.
   * When verification_status !== 'OFFICIAL_DOCS_VERIFIED' (e.g. STALE, UNKNOWN, FETCH_ERROR,
   * DOC_CHANGED_NEEDS_REVIEW, POLICY_CHANGED_NEEDS_REVIEW), outputs UNVERIFIED (covered = null)
   * instead of a deterministic coverage verdict.
   */
  function _findProvider(policyDb, id) {
    var list = (policyDb && policyDb.providers) ? policyDb.providers : [];
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].id === id) return list[i];
    }
    return null;
  }

  function _isUsablePolicy(p) {
    if (!p) return false;
    var st = p.verification_status;
    return st === 'OFFICIAL_DOCS_VERIFIED' || st === 'DOC_CHANGED_SIGNAL_STABLE' || st === 'FETCH_ERROR_CACHED';
  }

  function _policyDisplayStatus(p) {
    if (!p) return 'UNKNOWN';
    var st = p.verification_status || 'UNKNOWN';
    if (st === 'OFFICIAL_DOCS_VERIFIED') return 'VERIFIED';
    if (st === 'DOC_CHANGED_SIGNAL_STABLE') return 'VERIFIED_DOC_DRIFT';
    if (st === 'FETCH_ERROR_CACHED') return 'VERIFIED_CACHED';
    return st;
  }

  function calculateFreeQuota(dailyJobs, callsPerJob, peakRpm, retryMult, inputTokensPerCall, outputTokensPerCall, openRouterTier, policyDb) {
    var jobs = Math.max(0, Number(dailyJobs) || 0);
    var perJob = Math.max(0, Number(callsPerJob) || 0);
    var burstRpm = Math.max(0, Number(peakRpm) || 0);
    var mult = Math.max(1.0, Number(retryMult) || 1.0);
    var inTok = Math.max(0, Number(inputTokensPerCall) || 0);
    var outTok = Math.max(0, Number(outputTokensPerCall) || 0);
    var tokPerCall = inTok + outTok;

    var requiredCalls = Math.ceil(jobs * perJob * mult);
    var requiredTokens = Math.round(requiredCalls * tokPerCall);
    var effectivePeakRpm = Math.ceil(burstRpm * mult);
    var peakTpm = Math.round(effectivePeakRpm * tokPerCall);

    // 1. OpenRouter (from policyDb)
    var pOr = _findProvider(policyDb, 'openrouter');
    var orUsable = _isUsablePolicy(pOr);
    var orQuota = (pOr && pOr.model_quotas && pOr.model_quotas[0]) || {};
    var orLimit = openRouterTier === 'paid_1000' ? Number(orQuota.rpd_unlocked) : Number(orQuota.rpd);
    var orRpmCap = Number(orQuota.rpm);
    var orRpdCovered = null, orRpmCovered = null, orCovered = null, orDeficit = null;
    if (orUsable && !isNaN(orLimit) && !isNaN(orRpmCap)) {
      orRpdCovered = requiredCalls <= orLimit;
      orRpmCovered = effectivePeakRpm <= orRpmCap;
      orCovered = orRpdCovered && orRpmCovered;
      orDeficit = Math.max(0, requiredCalls - orLimit);
    }

    // 2. Groq (model-specific quotas from policyDb)
    var pGroq = _findProvider(policyDb, 'groq');
    var groqUsable = _isUsablePolicy(pGroq);
    var groqModels = (pGroq && pGroq.model_quotas) ? pGroq.model_quotas : [];
    var groqRef = groqModels[0] || {};
    var groqModelNames = groqModels.map(function (m) { return m.model; });
    var groqRpdCovered = null, groqTpdCovered = null, groqRpmCovered = null, groqTpmCovered = null, groqFits = null;
    if (groqUsable && groqRef.rpd != null && groqRef.tpd != null && groqRef.rpm != null && groqRef.tpm != null) {
      groqRpdCovered = requiredCalls <= Number(groqRef.rpd);
      groqTpdCovered = requiredTokens <= Number(groqRef.tpd);
      groqRpmCovered = effectivePeakRpm <= Number(groqRef.rpm);
      groqTpmCovered = peakTpm <= Number(groqRef.tpm) && tokPerCall <= Number(groqRef.tpm);
      groqFits = groqRpdCovered && groqTpdCovered && groqRpmCovered && groqTpmCovered;
    }

    // 3. Cloudflare Workers AI (official Neuron coefficients from policyDb)
    var pCf = _findProvider(policyDb, 'cloudflare_workers_ai');
    var cfUsable = _isUsablePolicy(pCf);
    var cfModel = (pCf && pCf.model_quotas && pCf.model_quotas[0]) || {};
    var cfDailyAllowance = pCf ? Number(pCf.free_daily_neurons) : NaN;
    var cfInRate = Number(cfModel.neurons_per_m_input);
    var cfOutRate = Number(cfModel.neurons_per_m_output);
    var cfNeuronsPerCall = null, cfDailyNeurons = null, cfMaxFreeCalls = null, cfCovered = null;
    if (cfUsable && !isNaN(cfDailyAllowance) && !isNaN(cfInRate) && !isNaN(cfOutRate)) {
      cfNeuronsPerCall = (inTok / 1e6) * cfInRate + (outTok / 1e6) * cfOutRate;
      cfDailyNeurons = requiredCalls * cfNeuronsPerCall;
      cfMaxFreeCalls = cfNeuronsPerCall > 0 ? Math.floor(cfDailyAllowance / cfNeuronsPerCall) : 0;
      cfCovered = cfDailyNeurons <= cfDailyAllowance;
    }

    // 4. Cerebras (trial classification from policyDb)
    var pCer = _findProvider(policyDb, 'cerebras');
    var cerUsable = _isUsablePolicy(pCer);
    var cerPermanentlyFree = cerUsable ? (pCer.tier_classification === 'permanently_free') : null;

    return {
      requiredCalls: requiredCalls,
      requiredTokens: requiredTokens,
      effectivePeakRpm: effectivePeakRpm,
      peakTpm: peakTpm,
      openRouterUsable: orUsable,
      openRouterStatus: _policyDisplayStatus(pOr),
      openRouterLimit: isNaN(orLimit) ? null : orLimit,
      openRouterRpmCap: isNaN(orRpmCap) ? null : orRpmCap,
      openRouterRpdCovered: orRpdCovered,
      openRouterRpmCovered: orRpmCovered,
      openRouterCovered: orCovered,
      openRouterDeficit: orDeficit,
      groqUsable: groqUsable,
      groqStatus: _policyDisplayStatus(pGroq),
      groqModels: groqModelNames,
      groqLimits: groqRef,
      groqRpdCovered: groqRpdCovered,
      groqTpdCovered: groqTpdCovered,
      groqRpmCovered: groqRpmCovered,
      groqTpmCovered: groqTpmCovered,
      groqFits: groqFits,
      cfUsable: cfUsable,
      cfStatus: _policyDisplayStatus(pCf),
      cfModelName: cfModel.model || '',
      cfDailyAllowance: isNaN(cfDailyAllowance) ? null : cfDailyAllowance,
      cfNeuronsPerCall: cfNeuronsPerCall,
      cfDailyNeurons: cfDailyNeurons,
      cfMaxFreeCalls: cfMaxFreeCalls,
      cfCovered: cfCovered,
      cerebrasUsable: cerUsable,
      cerebrasStatus: _policyDisplayStatus(pCer),
      cerebrasTierLabel: pCer ? (pCer.tier_label || '') : 'UNKNOWN',
      cerebrasTrialUsd: pCer ? pCer.trial_amount_usd : null,
      cerebrasTrialDays: pCer ? pCer.trial_expiry_days : null,
      cerebrasPermanentlyFree: cerPermanentlyFree
    };
  }

  return {
    isValidPrice: isValidPrice,
    filterEligible: filterEligible,
    estimateMonthlyCost: estimateMonthlyCost,
    calculateOpenRouterBilling: calculateOpenRouterBilling,
    calculateFreeQuota: calculateFreeQuota
  };
})();
"""


def _sem_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def calculate_openrouter_billing_py(inference_cost: float, billing_method: str, topup_batch_usd: float = 0.0) -> dict[str, Any]:
    """Python parity mirror of window.SysVCalc.calculateOpenRouterBilling for unit tests."""
    inf = max(0.0, float(inference_cost or 0.0))
    batch = max(0.0, float(topup_batch_usd or 0.0))
    if billing_method == "byok":
        overage = max(0.0, inf - 25000.0)
        fee = round(overage * 0.05, 6)
        return {
            "inferenceCost": round(inf, 6),
            "topupAmount": 0.0,
            "platformFee": fee,
            "totalCashOutlay": round(inf + fee, 6),
            "minFeeTriggered": False,
        }
    charge_base = max(inf, batch) if batch > 0 else inf
    if charge_base <= 0:
        return {"inferenceCost": 0.0, "topupAmount": 0.0, "platformFee": 0.0, "totalCashOutlay": 0.0, "minFeeTriggered": False}
    if billing_method == "crypto":
        fee = round(charge_base * 0.05, 6)
        return {
            "inferenceCost": round(inf, 6),
            "topupAmount": round(charge_base, 6),
            "platformFee": fee,
            "totalCashOutlay": round(charge_base + fee, 6),
            "minFeeTriggered": False,
        }
    pct_fee = charge_base * 0.055
    min_applied = pct_fee < 0.80
    fee = 0.80 if min_applied else round(pct_fee, 6)
    return {
        "inferenceCost": round(inf, 6),
        "topupAmount": round(charge_base, 6),
        "platformFee": fee,
        "totalCashOutlay": round(charge_base + fee, 6),
        "minFeeTriggered": min_applied,
    }


def calculate_free_quota_py(
    daily_jobs: float,
    calls_per_job: float,
    peak_rpm: float = 5.0,
    retry_mult: float = 1.0,
    input_tokens_per_call: float = 1000.0,
    output_tokens_per_call: float = 500.0,
    openrouter_tier: str = "free_50",
    policy_db: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Python parity mirror of window.SysVCalc.calculateFreeQuota reading directly from policy_db."""
    import math

    if policy_db is None:
        pfile = DATA_DIR / "free_provider_policies.json"
        policy_db = json.loads(pfile.read_text(encoding="utf-8")) if pfile.exists() else {"providers": []}

    def _find(pid: str) -> dict[str, Any] | None:
        return next((p for p in (policy_db.get("providers") or []) if p.get("id") == pid), None)

    def _usable(p: dict[str, Any] | None) -> bool:
        return bool(p and p.get("verification_status") in ("OFFICIAL_DOCS_VERIFIED", "DOC_CHANGED_SIGNAL_STABLE", "FETCH_ERROR_CACHED"))

    def _status_lbl(p: dict[str, Any] | None) -> str:
        if not p:
            return "UNKNOWN"
        st = str(p.get("verification_status") or "UNKNOWN")
        if st == "OFFICIAL_DOCS_VERIFIED":
            return "VERIFIED"
        if st == "DOC_CHANGED_SIGNAL_STABLE":
            return "VERIFIED_DOC_DRIFT"
        if st == "FETCH_ERROR_CACHED":
            return "VERIFIED_CACHED"
        return st

    jobs = max(0.0, float(daily_jobs or 0.0))
    per_job = max(0.0, float(calls_per_job or 0.0))
    burst_rpm = max(0.0, float(peak_rpm or 0.0))
    mult = max(1.0, float(retry_mult or 1.0))
    in_tok = max(0.0, float(input_tokens_per_call or 0.0))
    out_tok = max(0.0, float(output_tokens_per_call or 0.0))
    tok = in_tok + out_tok

    req_calls = int(math.ceil(jobs * per_job * mult))
    req_tokens = int(round(req_calls * tok))
    eff_peak_rpm = int(math.ceil(burst_rpm * mult))
    peak_tpm = int(round(eff_peak_rpm * tok))

    p_or = _find("openrouter")
    or_ok = _usable(p_or)
    or_q = (p_or.get("model_quotas") or [{}])[0] if p_or else {}
    or_limit = or_q.get("rpd_unlocked") if openrouter_tier == "paid_1000" else or_q.get("rpd")
    or_rpm_cap = or_q.get("rpm")
    if or_ok and or_limit is not None and or_rpm_cap is not None:
        or_rpd_cov = req_calls <= int(or_limit)
        or_rpm_cov = eff_peak_rpm <= int(or_rpm_cap)
        or_cov = or_rpd_cov and or_rpm_cov
        or_def = max(0, req_calls - int(or_limit))
    else:
        or_rpd_cov = or_rpm_cov = or_cov = or_def = None

    p_groq = _find("groq")
    groq_ok = _usable(p_groq)
    groq_q = (p_groq.get("model_quotas") or [{}])[0] if p_groq else {}
    if groq_ok and all(groq_q.get(k) is not None for k in ("rpd", "tpd", "rpm", "tpm")):
        groq_rpd_cov = req_calls <= int(groq_q["rpd"])
        groq_tpd_cov = req_tokens <= int(groq_q["tpd"])
        groq_rpm_cov = eff_peak_rpm <= int(groq_q["rpm"])
        groq_tpm_cov = peak_tpm <= int(groq_q["tpm"]) and tok <= int(groq_q["tpm"])
        groq_fits = groq_rpd_cov and groq_tpd_cov and groq_rpm_cov and groq_tpm_cov
    else:
        groq_rpd_cov = groq_tpd_cov = groq_rpm_cov = groq_tpm_cov = groq_fits = None

    p_cf = _find("cloudflare_workers_ai")
    cf_ok = _usable(p_cf)
    cf_q = (p_cf.get("model_quotas") or [{}])[0] if p_cf else {}
    cf_allow = p_cf.get("free_daily_neurons") if p_cf else None
    if cf_ok and cf_allow is not None and cf_q.get("neurons_per_m_input") is not None and cf_q.get("neurons_per_m_output") is not None:
        cf_n_call = (in_tok / 1e6) * float(cf_q["neurons_per_m_input"]) + (out_tok / 1e6) * float(cf_q["neurons_per_m_output"])
        cf_daily_n = req_calls * cf_n_call
        cf_max_calls = int(math.floor(float(cf_allow) / cf_n_call)) if cf_n_call > 0 else 0
        cf_cov = cf_daily_n <= float(cf_allow)
    else:
        cf_n_call = cf_daily_n = cf_max_calls = cf_cov = None

    p_cer = _find("cerebras")
    cer_ok = _usable(p_cer)
    cer_perm = (p_cer.get("tier_classification") == "permanently_free") if cer_ok else None

    return {
        "requiredCalls": req_calls,
        "requiredTokens": req_tokens,
        "effectivePeakRpm": eff_peak_rpm,
        "peakTpm": peak_tpm,
        "openRouterUsable": or_ok,
        "openRouterStatus": _status_lbl(p_or),
        "openRouterLimit": or_limit,
        "openRouterRpdCovered": or_rpd_cov,
        "openRouterRpmCovered": or_rpm_cov,
        "openRouterCovered": or_cov,
        "openRouterDeficit": or_def,
        "groqUsable": groq_ok,
        "groqStatus": _status_lbl(p_groq),
        "groqRpdCovered": groq_rpd_cov,
        "groqTpdCovered": groq_tpd_cov,
        "groqRpmCovered": groq_rpm_cov,
        "groqTpmCovered": groq_tpm_cov,
        "groqFits": groq_fits,
        "cfUsable": cf_ok,
        "cfStatus": _status_lbl(p_cf),
        "cfNeuronsPerCall": round(cf_n_call, 4) if cf_n_call is not None else None,
        "cfDailyNeurons": round(cf_daily_n, 4) if cf_daily_n is not None else None,
        "cfMaxFreeCalls": cf_max_calls,
        "cfCovered": cf_cov,
        "cerebrasUsable": cer_ok,
        "cerebrasStatus": _status_lbl(p_cer),
        "cerebrasPermanentlyFree": cer_perm,
    }


def _render_tool_affiliates(cfg: dict[str, Any], esc) -> str:
    affs = [a for a in cfg.get("monetization", {}).get("affiliates", []) if a.get("url")]
    if not affs:
        return ""
    cards = []
    for a in affs:
        cards.append(
            '<div class="card">'
            f'<span class="badge">{esc(a.get("badge", "Partner"))}</span>'
            f'<h3>{esc(a["name"])}</h3>'
            f'<p class="muted small">{esc(a["description"])}</p>'
            f'<a class="btn" href="{esc(a["url"])}" rel="sponsored noopener noreferrer" target="_blank">{esc(a["cta"])} &rarr;</a>'
            "</div>"
        )
    return (
        '<h2>Self-Hosting Infrastructure Partners</h2>'
        '<p class="muted small">Affiliate disclosure: links below are marked <code>sponsored</code> and may earn a cash commission at no extra cost to you.</p>'
        '<div class="grid">' + "".join(cards) + "</div>"
    )


def _render_embed_and_cite_card(base: str, tool_slug: str, tool_name: str, esc) -> str:
    embed_url = f"{base}/embed/{tool_slug}/"
    tool_url = f"{base}/tools/{tool_slug}/"
    iframe_code = (
        f'<iframe src="{embed_url}" width="100%" height="580" '
        f'style="border:1px solid #d2d2d7;border-radius:16px" loading="lazy"></iframe>'
    )
    md_cite = f"[{tool_name} (Daily-Verified LLM Pricing)]({tool_url})"
    return f"""
    <div class="card">
      <h2>Embed This Calculator (Ad-Free) or Cite Live Data</h2>
      <p class="muted small">Use the dedicated ad-free <code>/embed/</code> endpoint on technical blogs, GitHub wikis, or internal engineering docs (zero ads, zero third-party analytics). Open datasets available at <a href="/data/">/data/</a>.</p>
      <p class="small"><strong>Ad-Free Iframe Embed:</strong></p>
      <pre style="overflow-x:auto;background:#f5f5f7;padding:12px;border-radius:12px;border:1px solid rgba(0,0,0,0.08);font-size:0.82rem">{esc(iframe_code)}</pre>
      <p class="small"><strong>Markdown Citation:</strong></p>
      <pre style="overflow-x:auto;background:#f5f5f7;padding:12px;border-radius:12px;border:1px solid rgba(0,0,0,0.08);font-size:0.82rem">{esc(md_cite)}</pre>
    </div>
    """


def _render_related_tools_card(current_slug: str, esc) -> str:
    all_links = [
        ("home", "/", "LLM API Pricing Calculator", "Compare live per-1M token rates across capability-gated workloads"),
        ("free-llm-apis", "/free-llm-apis/", "Free LLM API List & Quota Calculator", "Verify permanently free tiers, RPM/RPD caps, and Cloudflare Neurons"),
        ("openrouter-cost-calculator", "/tools/openrouter-cost-calculator/", "OpenRouter Cost Calculator", "Separate token inference, 5.5% card fee ($0.80 min), crypto, and BYOK"),
        ("n8n-ai-workflow-cost-calculator", "/tools/n8n-ai-workflow-cost-calculator/", "n8n AI Workflow Cost Calculator", "Compare self-hosted VPS vs. n8n Cloud plus tool-calling LLM spend"),
        ("llm-translation-cost-calculator", "/tools/llm-translation-cost-calculator/", "LLM Translation Cost Calculator", "Estimate bulk 1,000-document & 1M-word translation with :batch savings"),
        ("api-vs-local-llm-calculator", "/tools/api-vs-local-llm-calculator/", "API vs Local LLM Cost Calculator", "Model GPU hardware amortization and power draw vs. managed APIs"),
        ("price-changes", "/price-changes/", "Daily LLM Price History", "Inspect append-only daily price drops and CSV exports"),
    ]
    items = "".join(
        f'<li><a href="{href}"><strong>{esc(lbl)}</strong></a> &mdash; {esc(desc)}</li>'
        for slug, href, lbl, desc in all_links
        if slug != current_slug
    )
    return f"""
    <div class="card">
      <h2>Related LLM Cost Calculators &amp; Data</h2>
      <ul>{items}</ul>
    </div>
    """


def _build_tool_json_ld(base: str, canonical_path: str, name: str, description: str, faqs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "@context": "https://schema.org",
            "@type": "WebApplication",
            "name": name,
            "url": base + canonical_path,
            "applicationCategory": "DeveloperApplication",
            "operatingSystem": "All",
            "isAccessibleForFree": True,
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            "description": description,
        },
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {"@type": "Answer", "text": a},
                }
                for q, a in faqs
            ],
        },
    ]


def build_tools_and_data_assets(
    cfg: dict[str, Any],
    models: list[dict[str, Any]],
    calc_models: list[dict[str, Any]],
    changelog: dict[str, Any],
    git_commit: str,
    write_page_fn,
    render_layout_fn,
    render_embed_layout_fn,
    register_page_fn,
    esc,
    *,
    eval_page_fn=None,
    render_ad_slot_fn=None,
    runtime_cfg: dict[str, Any] | None = None,
    today: str = "",
) -> list[str]:
    """Generate /assets/calc-engine.js, /tools/* pages, ad-free /embed/* pages, and /data/* assets."""
    write_page_fn("assets/calc-engine.js", CALC_ENGINE_JS)
    urls: list[str] = []
    models_json_literal = json.dumps(calc_models, ensure_ascii=False)
    calc_sig = _sem_hash([(m["slug"], m["in"], m["out"], m["pricing_type"], m["tools"], m["structured"]) for m in calc_models])
    rt = runtime_cfg or {"mock_ads": False}
    date_lbl = today or changelog.get("updated_at") or "latest snapshot"
    base = cfg["base_url"].rstrip("/")

    def _eval(url_path: str, page_type: str, index_policy: str, cver: str) -> dict[str, Any]:
        if eval_page_fn is not None:
            return eval_page_fn(url_path, page_type, index_policy, content_version=cver, fact_version=calc_sig)
        return {"ads_enabled": False, "verification_enabled": False, "ads_eligible": False}

    def _ad_slot(slot_key: str, elig: dict[str, Any]) -> str:
        if render_ad_slot_fn is not None:
            return render_ad_slot_fn(
                cfg,
                slot_key,
                ads_enabled=bool(elig.get("ads_enabled")),
                mock_ads=bool(rt.get("mock_ads")),
                runtime_cfg=runtime_cfg,
            )
        return ""

    def _register_embed(rel_path: str, url_path: str, html_str: str) -> None:
        write_page_fn(rel_path, html_str)
        if eval_page_fn is not None:
            eval_page_fn(
                url_path,
                "embed",
                "noindex",
                content_version=content_quality_gate.compute_content_fingerprint(editorial_html=url_path, formula_sources=[CALC_ENGINE_JS]),
                fact_version=calc_sig,
            )

    def _tool_cver(editorial_html: str, formula_extras: list[str] | None = None, policies: Any = None) -> str:
        return content_quality_gate.compute_content_fingerprint(
            editorial_html=editorial_html,
            formula_sources=[CALC_ENGINE_JS] + list(formula_extras or []),
            policy_payload=policies,
        )

    # 1. /tools/ Hub
    tools_hub_body = """
    <div class="breadcrumb"><a href="/">Home</a> / Tools</div>
    <h1>AI &amp; LLM Cost Calculators</h1>
    <p class="muted">Decision-oriented calculators powered by daily Source Verified OpenRouter pricing metadata, explicit capability gates, and transparent fee disclosures.</p>
    <div class="grid">
      <div class="card">
        <span class="badge">OpenRouter</span>
        <h3><a href="/tools/openrouter-cost-calculator/">OpenRouter Pricing &amp; Cost Calculator</a></h3>
        <p class="muted small">Separates upstream inference cost, 5.5% credit card fee ($0.80 min), 5% crypto fee, BYOK allowance ($25k/mo free), and async <code>:batch</code> discounts.</p>
      </div>
      <div class="card">
        <span class="badge">Workflow Automation</span>
        <h3><a href="/tools/n8n-ai-workflow-cost-calculator/">n8n AI Workflow Cost Calculator</a></h3>
        <p class="muted small">Compare n8n Cloud vs. self-hosted VPS hosting plus real-time tool-calling LLM token spend and retry-loop overhead.</p>
      </div>
      <div class="card">
        <span class="badge">Bulk Documents</span>
        <h3><a href="/tools/llm-translation-cost-calculator/">LLM Translation &amp; 1,000-Document Cost Calculator</a></h3>
        <p class="muted small">Convert document count and word length into tokens (1,000 words &approx; 1,333 tokens) and compare sync vs. async <code>:batch</code> models.</p>
      </div>
      <div class="card">
        <span class="badge">Build vs. Buy</span>
        <h3><a href="/tools/api-vs-local-llm-calculator/">API vs. Local LLM Break-Even Calculator</a></h3>
        <p class="muted small">Model GPU hardware purchase, amortization window, power draw (Watts &times; kWh), and upkeep against live managed API token rates.</p>
      </div>
    </div>
    """
    tools_hub_elig = _eval("/tools/", "tool_hub", "index", _tool_cver(tools_hub_body))
    urls.append("/tools/")
    register_page_fn(
        "tools/index.html",
        "/tools/",
        "tool_hub",
        "index",
        _sem_hash(["tools-hub-v2.2.1", calc_sig]),
        render_layout_fn(
            cfg,
            title="AI & LLM Cost Calculators | SysV",
            description="Task-specific calculators for OpenRouter fees, n8n AI workflows, bulk 1,000-document translation, and API vs local GPU break-even.",
            canonical_path="/tools/",
            body_html=tools_hub_body,
            git_commit=git_commit,
            index_policy="index",
            ads_enabled=tools_hub_elig["ads_enabled"],
            verification_enabled=tools_hub_elig["verification_enabled"],
            runtime_cfg=runtime_cfg,
            breadcrumbs=[("Home", "/"), ("Tools", "/tools/")],
        ),
    )

    # 2. OpenRouter Cost Calculator (with exact 5.5% / $0.80 min / BYOK $25k allowance / 3-line breakdown!)
    or_widget = f"""
    <div class="card">
      <div class="calc-grid">
        <div>
          <label class="muted small" for="or-in">Monthly Input Tokens (Millions)</label><br />
          <input id="or-in" type="number" min="0" step="0.5" value="10.0" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="or-out">Monthly Output Tokens (Millions)</label><br />
          <input id="or-out" type="number" min="0" step="0.5" value="2.0" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="or-pay">Payment / Billing Method</label><br />
          <select id="or-pay" style="width:100%;margin-top:6px">
            <option value="card">Credit Card Top-Up (5.5%, $0.80 min)</option>
            <option value="crypto">Crypto Top-Up (5.0% flat)</option>
            <option value="byok">BYOK (0% up to $25k/mo; 5% overage)</option>
          </select>
        </div>
        <div>
          <label class="muted small" for="or-topup">Top-Up Batch Size (Card / Crypto)</label><br />
          <select id="or-topup" style="width:100%;margin-top:6px">
            <option value="0" selected>Exact Monthly Inference Spend</option>
            <option value="10">$10 Single Top-Up (unlocks 1,000 free RPD)</option>
            <option value="25">$25 Single Top-Up (avoids $0.80 min)</option>
            <option value="50">$50 Single Top-Up</option>
          </select>
        </div>
      </div>
      <div style="margin-top:10px">
        <label class="muted small" style="cursor:pointer"><input id="or-batch" type="checkbox" checked /> Compare asynchronous <code>:batch</code> endpoints alongside real-time models</label>
      </div>
      <div class="calc-result" id="or-out-box" aria-live="polite"></div>
      <p class="muted small" style="margin-top:10px"><strong>Fee &amp; Scope Disclosure:</strong> Separates (1) upstream token inference cost, (2) OpenRouter payment/platform fee (card 5.5% with $0.80 min; crypto 5%; BYOK 0% up to $25,000/mo list inference on eligible plans, 5% overage), and (3) total cash outlay. Excludes prompt-cache discounts &amp; long-context tiered surcharges unless noted.</p>
    </div>
    <script src="/assets/calc-engine.js"></script>
    <script>
      (function() {{
        const MODELS = {models_json_literal};
        const inEl = document.getElementById('or-in');
        const outEl = document.getElementById('or-out');
        const payEl = document.getElementById('or-pay');
        const topEl = document.getElementById('or-topup');
        const bEl = document.getElementById('or-batch');
        const box = document.getElementById('or-out-box');
        function run() {{
          const mIn = Math.max(0, parseFloat(inEl.value || '0') || 0);
          const mOut = Math.max(0, parseFloat(outEl.value || '0') || 0);
          const method = payEl.value || 'card';
          const topup = parseFloat(topEl.value || '0') || 0;
          const list = window.SysVCalc.filterEligible(MODELS, 'custom', bEl.checked)
            .filter(m => m.pricing_type === 'paid' || m.pricing_type === 'batch')
            .map(m => {{
              const inf = window.SysVCalc.estimateMonthlyCost(m, mIn, mOut);
              const bill = window.SysVCalc.calculateOpenRouterBilling(inf, method, topup);
              return {{ ...m, bill }};
            }})
            .sort((a, b) => a.bill.inferenceCost - b.bill.inferenceCost)
            .slice(0, 6);
          let html = '<strong>Breakdown for ' + mIn.toFixed(1) + 'M in / ' + mOut.toFixed(1) + 'M out:</strong><ol style="margin:8px 0 0;padding-left:20px">';
          list.forEach(m => {{
            const b = m.bill;
            const tag = m.pricing_type === 'batch' ? ' <span class="badge badge-purple">Async Batch</span>' : '';
            const minBadge = b.minFeeTriggered ? ' <span class="badge badge-warn">$0.80 min fee</span>' : '';
            const topStr = b.topupAmount > 0 ? ' | Top-Up: $' + b.topupAmount.toFixed(2) : '';
            html += '<li style="margin-bottom:6px"><a href="/models/' + m.slug + '/" target="_top"><strong>' + m.name + '</strong></a>' + tag + minBadge +
              '<br><span class="small">Inference: <strong>$' + b.inferenceCost.toFixed(2) + '</strong>' + topStr +
              ' | Platform Fee: <strong>$' + b.platformFee.toFixed(2) + '</strong> | Total Cash Outlay: <strong>$' + b.totalCashOutlay.toFixed(2) + '</strong></span></li>';
          }});
          html += '</ol>';
          if (list.length) html += '<div class="muted small" style="margin-top:8px"><em>Rule applied:</em> ' + list[0].bill.note + '</div>';
          box.innerHTML = html;
        }}
        [inEl, outEl, payEl, topEl, bEl].forEach(el => {{ el.addEventListener('input', run); el.addEventListener('change', run); }});
        run();
      }})();
    </script>
    """
    or_notes_html = f"""
    <div class="card">
      <h2>OpenRouter Fee Thresholds: When Card, Crypto, or BYOK Wins</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Billing Method</th><th>Fee Rule</th><th>Example on $10 Top-Up</th><th>Example on $100 Spend</th><th>Best Fit</th></tr></thead>
          <tbody>
            <tr><td><strong>Credit Card (&lt;$14.55 top-up)</strong></td><td>$0.80 minimum fee</td><td>$0.80 (8.0% effective)</td><td>&mdash;</td><td>Avoid small top-ups; bundle &ge;$25 to eliminate minimum-fee penalty</td></tr>
            <tr><td><strong>Credit Card (&ge;$14.55 top-up)</strong></td><td>5.5% flat</td><td>&mdash;</td><td>$5.50 (5.5%)</td><td>Fastest setup for small to medium teams</td></tr>
            <tr><td><strong>Crypto Top-Up</strong></td><td>5.0% flat</td><td>$0.50 (5.0%)</td><td>$5.00 (5.0%)</td><td>Saves 0.5% vs card; avoids the $0.80 card minimum on small loads</td></tr>
            <tr><td><strong>BYOK (Bring Your Own Key)</strong></td><td>0% up to $25k/mo list inference; 5% overage</td><td>$0 platform fee</td><td>$0 platform fee</td><td>Best for teams with existing OpenAI/Anthropic/Google billing accounts</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <h2>Formula, Worked Example &amp; Data Scope</h2>
      <ul>
        <li><strong>Input vs. Output Tokens:</strong> Prompts, system instructions, and retrieved context count as <em>input tokens</em>; model completions and reasoning traces count as <em>output tokens</em> (typically 3&times;&ndash;5&times; pricier per 1M tokens).</li>
        <li><strong>Billing Formula:</strong> <code>Inference = (Input_M &times; Input_Rate) + (Output_M &times; Output_Rate)</code>; Card Top-Up Fee = <code>max($0.80, Charge_Base &times; 5.5%)</code>; Crypto Fee = <code>Charge_Base &times; 5.0%</code>; BYOK = <code>$0</code> up to <code>$25,000/mo</code> list inference, then <code>5%</code> on overage.</li>
        <li><strong>Worked Example:</strong> At <code>10M input &times; $0.20 + 2M output &times; $0.80 = $3.60</code> inference spend on a card with exact spend, <code>5.5%</code> is <code>$0.198</code>, which triggers the <code>$0.80</code> minimum fee &rarr; <code>$4.40</code> total cash outlay. Topping up <code>$25.00</code> at once drops the effective card fee to <code>5.5%</code> (<code>$1.38</code>).</li>
        <li><strong>Free vs. Paid vs. Batch:</strong> <code>:free</code> models bill <code>$0</code> under strict RPM/RPD caps; paid models bill real-time per token; <code>:batch</code> endpoints run asynchronously (not for interactive chat). Unknown or dynamic router prices (<code>-1</code> / <code>null</code>) are excluded rather than coerced to <code>$0</code>.</li>
        <li><strong>Source &amp; Limits:</strong> Token rates come from the <a href="https://openrouter.ai/api/v1/models" target="_blank" rel="noopener noreferrer">OpenRouter Public Models API</a> ({esc(date_lbl)}). Excludes prompt-cache discounts and long-context tiered surcharges.</li>
      </ul>
    </div>
    <div class="card">
      <h2>OpenRouter Free-Tier Limits Quick Reference</h2>
      <ul>
        <li><strong>Global Free RPM Cap:</strong> 20 requests/minute across <code>:free</code> models.</li>
        <li><strong>Daily RPD Without Paid History:</strong> 50 requests/day.</li>
        <li><strong>Daily RPD After &ge;$10 Lifetime Credits:</strong> 1,000 requests/day (one-time credit purchase threshold).</li>
        <li><strong>Dynamic Routers Warning:</strong> <code>openrouter/auto</code> and <code>openrouter/auto-code</code> have variable downstream billing (raw price <code>-1</code>) and are excluded from $0 estimates.</li>
      </ul>
      <p><a href="/free-llm-apis/">Inspect all verified rate-limited free endpoints &rarr;</a></p>
    </div>
    """
    or_elig = _eval(
        "/tools/openrouter-cost-calculator/",
        "tool",
        "index",
        _tool_cver(or_notes_html, policies={"card_pct": 0.055, "card_min": 0.80, "crypto_pct": 0.05, "byok_free_cap": 25000}),
    )
    or_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / OpenRouter Cost Calculator</div>
    <h1>OpenRouter Cost Calculator: Token Pricing, 5.5% Fee &amp; BYOK</h1>
    <p class="muted">Compare daily-verified upstream token inference costs against OpenRouter's exact billing rules (<strong>5.5% card fee with $0.80 minimum</strong>, <strong>5.0% crypto</strong>, or <strong>BYOK 0% up to $25k/mo</strong>). Last pricing sync: <strong>{esc(date_lbl)}</strong>.</p>
    {or_widget}
    {_ad_slot("adsense_slot_in_article", or_elig)}
    {or_notes_html}
    {_render_embed_and_cite_card(base, "openrouter-cost-calculator", "SysV OpenRouter Cost Calculator", esc)}
    {_render_related_tools_card("openrouter-cost-calculator", esc)}
    """
    or_json_ld = _build_tool_json_ld(
        base,
        "/tools/openrouter-cost-calculator/",
        "SysV OpenRouter Cost Calculator",
        "Calculate real OpenRouter API spend separating upstream inference cost, 5.5% credit card fee ($0.80 minimum), 5.0% crypto fee, BYOK $25k/mo allowance, and async :batch discounts.",
        [
            (
                "How much is the OpenRouter credit card fee?",
                "OpenRouter charges a 5.5% credit card top-up fee with a $0.80 minimum per transaction. Topping up $14.55 or more avoids the minimum-fee surcharge.",
            ),
            (
                "Does OpenRouter BYOK charge a platform fee?",
                "OpenRouter BYOK charges 0% platform fee up to $25,000/month in list-price inference volume on eligible plans, and 5% on monthly volume above $25,000, while the upstream provider bills your API key directly.",
            ),
            (
                "How do I unlock 1,000 free requests per day on OpenRouter?",
                "Purchasing at least $10 in lifetime credits raises the :free model cap from 50 requests/day to 1,000 requests/day while keeping the global 20 RPM burst limit.",
            ),
        ],
    )
    urls.append("/tools/openrouter-cost-calculator/")
    register_page_fn(
        "tools/openrouter-cost-calculator/index.html",
        "/tools/openrouter-cost-calculator/",
        "tool",
        "index",
        _sem_hash(["or-tool-v2.4", calc_sig]),
        render_layout_fn(
            cfg,
            title="OpenRouter Cost Calculator — Exact 5.5% Fee, $0.80 Min & BYOK (2026) | SysV",
            description="Calculate real OpenRouter API spend separating upstream inference cost, 5.5% card fee ($0.80 min), 5% crypto fee, BYOK $25k allowance, and async :batch discounts.",
            canonical_path="/tools/openrouter-cost-calculator/",
            body_html=or_body,
            git_commit=git_commit,
            index_policy="index",
            ads_enabled=or_elig["ads_enabled"],
            verification_enabled=or_elig["verification_enabled"],
            runtime_cfg=runtime_cfg,
            breadcrumbs=[("Home", "/"), ("Tools", "/tools/"), ("OpenRouter Cost Calculator", "/tools/openrouter-cost-calculator/")],
            extra_json_ld=or_json_ld,
        ),
    )
    # Ad-free embed version!
    _register_embed(
        "embed/openrouter-cost-calculator/index.html",
        "/embed/openrouter-cost-calculator/",
        render_embed_layout_fn(
            cfg,
            title="OpenRouter Pricing & Cost Calculator (Embed) | SysV",
            canonical_path="/tools/openrouter-cost-calculator/",
            body_html=f"<h1 style='font-size:1.25rem;margin-top:0'>OpenRouter Cost Calculator</h1>{or_widget}",
        ),
    )

    # 3. n8n AI Workflow Cost Calculator
    n8n_widget = f"""
    <div class="card">
      <div class="calc-grid">
        <div>
          <label class="muted small" for="n8n-runs">Workflow Executions / Month</label><br />
          <input id="n8n-runs" type="number" min="0" step="500" value="10000" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="n8n-in">Avg. Input Tokens / Execution</label><br />
          <input id="n8n-in" type="number" min="0" step="100" value="1800" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="n8n-out">Avg. Output Tokens / Execution</label><br />
          <input id="n8n-out" type="number" min="0" step="50" value="400" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="n8n-retry">Retry / Multi-Turn Multiplier</label><br />
          <select id="n8n-retry" style="width:100%;margin-top:6px">
            <option value="1.0">1.0x (Single-shot node)</option>
            <option value="1.35" selected>1.35x (Normal retries + 1 tool step)</option>
            <option value="2.2">2.2x (Multi-step agent loop)</option>
          </select>
        </div>
        <div>
          <label class="muted small" for="n8n-vps">Self-Hosted VPS Cost (USD/mo)</label><br />
          <input id="n8n-vps" type="number" min="0" step="1" value="6" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="n8n-cloud">n8n Cloud Plan Cost (USD/mo)</label><br />
          <input id="n8n-cloud" type="number" min="0" step="5" value="24" style="width:100%;margin-top:6px" />
        </div>
      </div>
      <div class="calc-result" id="n8n-out-box" aria-live="polite"></div>
      <p class="muted small" style="margin-top:10px"><strong>Capability Gate Applied:</strong> Ranks only real-time endpoints with Source Verified <code>tools</code> support; excludes async <code>:batch</code> endpoints and variable routers.</p>
    </div>
    <script src="/assets/calc-engine.js"></script>
    <script>
      (function() {{
        const MODELS = {models_json_literal};
        const els = ['n8n-runs', 'n8n-in', 'n8n-out', 'n8n-retry', 'n8n-vps', 'n8n-cloud'].map(id => document.getElementById(id));
        const box = document.getElementById('n8n-out-box');
        function run() {{
          const runs = Math.max(0, parseFloat(els[0].value || '0') || 0);
          const tIn = Math.max(0, parseFloat(els[1].value || '0') || 0);
          const tOut = Math.max(0, parseFloat(els[2].value || '0') || 0);
          const mult = Math.max(1.0, parseFloat(els[3].value || '1.35') || 1.35);
          const vps = Math.max(0, parseFloat(els[4].value || '0') || 0);
          const cloud = Math.max(0, parseFloat(els[5].value || '0') || 0);
          const mIn = (runs * tIn * mult) / 1e6;
          const mOut = (runs * tOut * mult) / 1e6;

          const eligible = window.SysVCalc.filterEligible(MODELS, 'n8n', false);
          const paid = eligible.filter(m => m.pricing_type === 'paid')
            .map(m => ({{ ...m, tokenCost: window.SysVCalc.estimateMonthlyCost(m, mIn, mOut) }}))
            .sort((a, b) => a.tokenCost - b.tokenCost).slice(0, 5);
          const frees = eligible.filter(m => m.pricing_type === 'limited_free' || m.pricing_type === 'free').slice(0, 4);

          let html = '<strong>Estimated Monthly Volume:</strong> ' + mIn.toFixed(2) + 'M input / ' + mOut.toFixed(2) + 'M output tokens (' + mult + 'x multiplier).';
          html += '<ol style="margin:8px 0 10px;padding-left:20px">';
          paid.forEach(m => {{
            html += '<li><a href="/models/' + m.slug + '/" target="_top"><strong>' + m.name + '</strong></a> &mdash; Tokens: <strong>$' + m.tokenCost.toFixed(2) + '</strong> | Self-Hosted Total: <strong>$' + (m.tokenCost + vps).toFixed(2) + '/mo</strong> | Cloud Total: <strong>$' + (m.tokenCost + cloud).toFixed(2) + '/mo</strong></li>';
          }});
          html += '</ol>';
          if (frees.length) {{
            html += '<strong>Rate-Limited Free ($0) Tool-Calling Models:</strong> ' +
              frees.map(m => '<a href="/models/' + m.slug + '/" target="_top">' + m.name + '</a>').join(' &middot; ');
          }}
          box.innerHTML = html;
        }}
        els.forEach(el => {{ el.addEventListener('input', run); el.addEventListener('change', run); }});
        run();
      }})();
    </script>
    """
    n8n_notes_html = f"""
    <div class="card">
      <h2>Common n8n AI Workflow Cost Profiles</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Workflow Pattern</th><th>Typical Tokens / Run</th><th>Retry Multiplier</th><th>Main Cost Driver</th></tr></thead>
          <tbody>
            <tr><td><strong>Webhook Triage / JSON Classifier</strong></td><td>800 in / 150 out</td><td>1.0x&ndash;1.15x</td><td>Hosting plan (often &gt;80% of total spend vs. tokens)</td></tr>
            <tr><td><strong>Support Draft + CRM Tool Call</strong></td><td>1,800 in / 400 out</td><td>1.35x</td><td>Balanced hosting + tool-calling tokens</td></tr>
            <tr><td><strong>Multi-Step Autonomous AI Agent</strong></td><td>4,500 in / 1,200 out</td><td>2.2x+</td><td>Token compounding across tool-call loops (prefer low-cost tool-verified models)</td></tr>
          </tbody>
        </table>
      </div>
      <p class="muted small" style="margin-top:8px">Planning to self-host n8n? Inspect our <a href="/self-hosting/">Self-Hosted AI &amp; Automation Directory</a> and <a href="/zero-cost-stack/">Zero-Cost Automation Stack</a>.</p>
    </div>
    <div class="card">
      <h2>Formula, Worked Example &amp; Data Scope</h2>
      <ul>
        <li><strong>Formula:</strong> <code>Input_M = (Executions &times; Input_Tokens &times; Retry_Mult) / 1,000,000</code>; <code>Output_M = (Executions &times; Output_Tokens &times; Retry_Mult) / 1,000,000</code>; <code>Total = Token_Cost + Hosting_Cost</code>.</li>
        <li><strong>Worked Example:</strong> <code>10,000</code> monthly runs &times; <code>1,800</code> input / <code>400</code> output tokens &times; <code>1.35x</code> retry factor = <code>24.30M</code> input and <code>5.40M</code> output tokens. At <code>$0.10/1M</code> in and <code>$0.40/1M</code> out, token cost is <code>$4.59/mo</code> &rarr; <code>$10.59/mo</code> on a <code>$6</code> VPS vs. <code>$28.59/mo</code> on a <code>$24</code> cloud plan.</li>
        <li><strong>Real-Time &amp; Pricing Filters:</strong> Excludes async <code>:batch</code> queues (unsuitable for interactive webhook workflows), non-tool models, and unknown/variable (<code>-1</code>) endpoints. Source: <a href="https://openrouter.ai/api/v1/models" target="_blank" rel="noopener noreferrer">OpenRouter Public Models API</a> ({esc(date_lbl)}).</li>
      </ul>
    </div>
    """
    n8n_elig = _eval(
        "/tools/n8n-ai-workflow-cost-calculator/",
        "tool",
        "index",
        _tool_cver(n8n_notes_html, policies={"affiliates": [a.get("url") for a in cfg.get("monetization", {}).get("affiliates", []) if a.get("url")]}),
    )
    n8n_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / n8n AI Workflow Cost Calculator</div>
    <h1>n8n AI Workflow Cost Calculator: Cloud vs. Self-Hosted + Tokens</h1>
    <p class="muted">Compare self-hosted VPS hosting vs. n8n Cloud alongside live real-time tool-calling LLM token spend and multi-step retry multipliers. Last pricing sync: <strong>{esc(date_lbl)}</strong>.</p>
    {n8n_widget}
    {_ad_slot("adsense_slot_in_article", n8n_elig)}
    {n8n_notes_html}
    {_render_tool_affiliates(cfg, esc)}
    {_render_embed_and_cite_card(base, "n8n-ai-workflow-cost-calculator", "SysV n8n AI Workflow Cost Calculator", esc)}
    {_render_related_tools_card("n8n-ai-workflow-cost-calculator", esc)}
    """
    n8n_json_ld = _build_tool_json_ld(
        base,
        "/tools/n8n-ai-workflow-cost-calculator/",
        "SysV n8n AI Workflow Cost Calculator",
        "Forecast monthly n8n AI workflow costs comparing self-hosted VPS vs n8n Cloud alongside live tool-calling LLM token rates and multi-turn retry loops.",
        [
            (
                "Why do n8n AI agent workflows cost more than single-prompt estimates?",
                "Each tool call or validation retry re-sends prior conversation and tool-schema tokens, multiplying input token spend by 1.35x to 2.2x+ per execution.",
            ),
            (
                "Why does this calculator filter for tool-calling models only?",
                "n8n AI Agent nodes rely on structured function/tool calling. Filtering by Source Verified tools=True and real-time endpoints prevents recommending async :batch or chat-only models that fail in agent loops.",
            ),
        ],
    )
    urls.append("/tools/n8n-ai-workflow-cost-calculator/")
    register_page_fn(
        "tools/n8n-ai-workflow-cost-calculator/index.html",
        "/tools/n8n-ai-workflow-cost-calculator/",
        "tool",
        "index",
        _sem_hash(["n8n-tool-v2.4", calc_sig]),
        render_layout_fn(
            cfg,
            title="n8n AI Workflow Cost Calculator — Self-Hosted VPS vs Cloud + LLM Tokens | SysV",
            description="Forecast monthly n8n AI workflow costs comparing self-hosted VPS vs n8n Cloud alongside live tool-calling LLM token rates and retry multipliers.",
            canonical_path="/tools/n8n-ai-workflow-cost-calculator/",
            body_html=n8n_body,
            git_commit=git_commit,
            index_policy="index",
            ads_enabled=n8n_elig["ads_enabled"],
            verification_enabled=n8n_elig["verification_enabled"],
            runtime_cfg=runtime_cfg,
            breadcrumbs=[("Home", "/"), ("Tools", "/tools/"), ("n8n AI Workflow Cost Calculator", "/tools/n8n-ai-workflow-cost-calculator/")],
            extra_json_ld=n8n_json_ld,
        ),
    )
    _register_embed(
        "embed/n8n-ai-workflow-cost-calculator/index.html",
        "/embed/n8n-ai-workflow-cost-calculator/",
        render_embed_layout_fn(
            cfg,
            title="n8n AI Workflow Cost Calculator (Embed) | SysV",
            canonical_path="/tools/n8n-ai-workflow-cost-calculator/",
            body_html=f"<h1 style='font-size:1.25rem;margin-top:0'>n8n AI Workflow Cost Calculator</h1>{n8n_widget}",
        ),
    )

    # 4. Bulk Translation & 1,000-Document Calculator
    trans_widget = f"""
    <div class="card">
      <div class="calc-grid">
        <div>
          <label class="muted small" for="tr-docs">Number of Documents</label><br />
          <input id="tr-docs" type="number" min="0" step="100" value="1000" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="tr-words">Avg. Words per Document</label><br />
          <input id="tr-words" type="number" min="0" step="100" value="1500" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="tr-ratio">Output / Input Token Ratio</label><br />
          <select id="tr-ratio" style="width:100%;margin-top:6px">
            <option value="1.15" selected>1.15x (Standard Translation)</option>
            <option value="0.25">0.25x (Document Summarization / Extraction)</option>
            <option value="1.5">1.50x (English &rarr; Dense Multilingual / CJK)</option>
          </select>
        </div>
      </div>
      <div style="margin-top:10px">
        <label class="muted small" style="cursor:pointer"><input id="tr-batch" type="checkbox" checked /> Include asynchronous <code>:batch</code> endpoints (best fit for non-urgent bulk translation)</label>
      </div>
      <div class="calc-result" id="tr-out-box" aria-live="polite"></div>
      <p class="muted small" style="margin-top:10px"><strong>Estimation Scope:</strong> Uses 1,000 words &approx; 1,333 tokens plus 200 system-prompt tokens per document. Excludes prompt-cache discounts and translation quality differences.</p>
    </div>
    <script src="/assets/calc-engine.js"></script>
    <script>
      (function() {{
        const MODELS = {models_json_literal};
        const dEl = document.getElementById('tr-docs');
        const wEl = document.getElementById('tr-words');
        const rEl = document.getElementById('tr-ratio');
        const bEl = document.getElementById('tr-batch');
        const box = document.getElementById('tr-out-box');
        function run() {{
          const docs = Math.max(0, parseFloat(dEl.value || '0') || 0);
          const words = Math.max(0, parseFloat(wEl.value || '0') || 0);
          const ratio = Math.max(0, parseFloat(rEl.value || '1.15') || 1.15);
          if (docs <= 0 || words <= 0) {{
            box.innerHTML = '<strong>Enter positive document count and average word length to estimate token cost.</strong>';
            return;
          }}
          const tokPerDoc = words * 1.333;
          const mIn = (docs * (tokPerDoc + 200)) / 1e6;
          const mOut = (docs * tokPerDoc * ratio) / 1e6;

          const paid = window.SysVCalc.filterEligible(MODELS, 'translation', bEl.checked)
            .filter(m => m.pricing_type === 'paid' || m.pricing_type === 'batch')
            .map(m => ({{ ...m, cost: window.SysVCalc.estimateMonthlyCost(m, mIn, mOut) }}))
            .sort((a, b) => a.cost - b.cost).slice(0, 6);

          let html = '<strong>Estimated Volume for ' + docs.toLocaleString() + ' docs (' + ((docs * words) / 1e6).toFixed(2) + 'M words):</strong> ' + mIn.toFixed(2) + 'M input / ' + mOut.toFixed(2) + 'M output tokens.';
          html += '<ol style="margin:8px 0 0;padding-left:20px">';
          paid.forEach(m => {{
            const per1k = (m.cost / docs) * 1000;
            const tag = m.pricing_type === 'batch' ? ' <span class="badge badge-purple">Async Batch</span>' : '';
            html += '<li><a href="/models/' + m.slug + '/" target="_top"><strong>' + m.name + '</strong></a>' + tag + ' &mdash; Total: <strong>$' + m.cost.toFixed(2) + '</strong> <span class="muted small">($' + per1k.toFixed(2) + ' per 1,000 docs)</span></li>';
          }});
          html += '</ol>';
          box.innerHTML = html;
        }}
        [dEl, wEl, rEl, bEl].forEach(el => {{ el.addEventListener('input', run); el.addEventListener('change', run); }});
        run();
      }})();
    </script>
    """
    tr_notes_html = f"""
    <div class="card">
      <h2>Language Expansion Ratios &amp; Sync vs. Async <code>:batch</code> Decision Guide</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Translation Workload</th><th>Typical Output / Input Ratio</th><th>Recommended Mode</th><th>Cost Control Tip</th></tr></thead>
          <tbody>
            <tr><td><strong>English &harr; European Languages</strong></td><td>~1.15x</td><td>Async <code>:batch</code> for backfills; Real-Time for live UI</td><td>Keep glossary/system prompt concise (added per chunk)</td></tr>
            <tr><td><strong>English &rarr; CJK / Dense Multilingual</strong></td><td>~1.4x&ndash;1.6x</td><td>Async <code>:batch</code> (~50% lower output rate)</td><td>Output tokens dominate cost; verify output $/1M first</td></tr>
            <tr><td><strong>Bulk Summarization / Structured Extraction</strong></td><td>~0.25x</td><td>Real-Time or Async <code>:batch</code></td><td>Input tokens dominate; choose lowest input $/1M model</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <h2>Formula, Worked Example &amp; Data Scope</h2>
      <ul>
        <li><strong>Word-to-Token Formula:</strong> <code>Input_Tokens = Docs &times; (Words &times; 1.333 + 200)</code> (adds 200 system-prompt tokens per document); <code>Output_Tokens = Docs &times; Words &times; 1.333 &times; Ratio</code>.</li>
        <li><strong>Worked Example:</strong> <code>1,000</code> documents &times; <code>1,500</code> words at <code>1.15x</code> ratio &approx; <code>2.20M</code> input tokens and <code>2.30M</code> output tokens. At <code>$0.05/1M</code> input and <code>$0.25/1M</code> output on an async <code>:batch</code> model, total spend is <code>$0.69</code> (<code>$0.69</code> per 1,000 docs).</li>
        <li><strong>Sync vs. Async Batch vs. Free:</strong> Toggle <code>:batch</code> off for immediate responses; keep it checked for non-urgent overnight backfills (~50% lower rates). Unknown/variable (<code>-1</code>) prices are excluded. Source: <a href="https://openrouter.ai/api/v1/models" target="_blank" rel="noopener noreferrer">OpenRouter Public Models API</a> ({esc(date_lbl)}).</li>
      </ul>
    </div>
    """
    tr_elig = _eval(
        "/tools/llm-translation-cost-calculator/",
        "tool",
        "index",
        _tool_cver(tr_notes_html, policies={"words_to_tokens": 1.333, "sys_prompt_tokens": 200}),
    )
    trans_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / LLM Translation Cost Calculator</div>
    <h1>LLM Translation Cost Calculator: Bulk Documents &amp; 1M Words</h1>
    <p class="muted">Convert document counts and word lengths into exact input/output LLM token spend and compare real-time vs. asynchronous <code>:batch</code> discounts. Last pricing sync: <strong>{esc(date_lbl)}</strong>.</p>
    {trans_widget}
    {_ad_slot("adsense_slot_in_article", tr_elig)}
    {tr_notes_html}
    {_render_embed_and_cite_card(base, "llm-translation-cost-calculator", "SysV LLM Translation Cost Calculator", esc)}
    {_render_related_tools_card("llm-translation-cost-calculator", esc)}
    """
    tr_json_ld = _build_tool_json_ld(
        base,
        "/tools/llm-translation-cost-calculator/",
        "SysV LLM Translation Cost Calculator",
        "Calculate LLM translation API cost per 1,000 documents and per 1M words across daily-verified real-time and async :batch models.",
        [
            (
                "How many LLM tokens is 1,000 words for translation?",
                "English prose averages roughly 1,333 tokens per 1,000 words, plus system-prompt overhead (modeled at 200 tokens per document). Target languages like CJK or morphologically rich scripts often require 1.15x to 1.5x output tokens.",
            ),
            (
                "When should I use async :batch LLM APIs for document translation?",
                "Use async :batch endpoints for non-urgent localization backfills, knowledge-base migrations, or overnight jobs where 24-hour completion windows are acceptable and ~50% lower token pricing matters.",
            ),
        ],
    )
    urls.append("/tools/llm-translation-cost-calculator/")
    register_page_fn(
        "tools/llm-translation-cost-calculator/index.html",
        "/tools/llm-translation-cost-calculator/",
        "tool",
        "index",
        _sem_hash(["trans-tool-v2.4", calc_sig]),
        render_layout_fn(
            cfg,
            title="LLM Translation Cost Calculator — Per 1,000 Docs, 1M Words & Batch API | SysV",
            description="Calculate LLM translation API cost per 1,000 documents and per 1M words across real-time and async :batch models with daily-verified pricing.",
            canonical_path="/tools/llm-translation-cost-calculator/",
            body_html=trans_body,
            git_commit=git_commit,
            index_policy="index",
            ads_enabled=tr_elig["ads_enabled"],
            verification_enabled=tr_elig["verification_enabled"],
            runtime_cfg=runtime_cfg,
            breadcrumbs=[("Home", "/"), ("Tools", "/tools/"), ("LLM Translation Cost Calculator", "/tools/llm-translation-cost-calculator/")],
            extra_json_ld=tr_json_ld,
        ),
    )
    _register_embed(
        "embed/llm-translation-cost-calculator/index.html",
        "/embed/llm-translation-cost-calculator/",
        render_embed_layout_fn(
            cfg,
            title="LLM Bulk Translation Cost Calculator (Embed) | SysV",
            canonical_path="/tools/llm-translation-cost-calculator/",
            body_html=f"<h1 style='font-size:1.25rem;margin-top:0'>LLM Translation Cost Calculator</h1>{trans_widget}",
        ),
    )

    # 5. API vs Local LLM Break-Even Calculator
    paid_opts = "".join(
        f'<option value="{esc(m["slug"])}" data-in="{m["in"]}" data-out="{m["out"]}">{esc(m["name"])} (${m["in"]} in / ${m["out"]} out)</option>'
        for m in calc_models
        if m["pricing_type"] == "paid"
    )
    local_widget = f"""
    <div class="card">
      <div class="calc-grid">
        <div>
          <label class="muted small" for="loc-model">Managed API Reference Model</label><br />
          <select id="loc-model" style="width:100%;margin-top:6px">{paid_opts}</select>
        </div>
        <div>
          <label class="muted small" for="loc-in">Monthly Input Tokens (Millions)</label><br />
          <input id="loc-in" type="number" min="0" step="5" value="100" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="loc-out">Monthly Output Tokens (Millions)</label><br />
          <input id="loc-out" type="number" min="0" step="2" value="25" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="loc-hw">Local Hardware Cost (USD)</label><br />
          <input id="loc-hw" type="number" min="0" step="100" value="2500" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="loc-amort">Hardware Amortization (Months)</label><br />
          <input id="loc-amort" type="number" min="1" step="6" value="24" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="loc-watts">Avg. System Power (Watts) / kWh Price ($)</label><br />
          <div style="display:flex;gap:8px;margin-top:6px">
            <input id="loc-watts" type="number" min="0" step="50" value="350" style="width:55%" />
            <input id="loc-kwh" type="number" min="0" step="0.02" value="0.18" style="width:45%" />
          </div>
        </div>
      </div>
      <div class="calc-result" id="loc-out-box" aria-live="polite"></div>
      <p class="muted small" style="margin-top:10px"><strong>Caveat:</strong> Assumes 24/7 power at the entered average Watt draw and excludes engineer maintenance hours unless added to hardware cost.</p>
    </div>
    <script>
      (function() {{
        const mEl = document.getElementById('loc-model');
        const inEl = document.getElementById('loc-in');
        const outEl = document.getElementById('loc-out');
        const hwEl = document.getElementById('loc-hw');
        const amEl = document.getElementById('loc-amort');
        const wEl = document.getElementById('loc-watts');
        const kEl = document.getElementById('loc-kwh');
        const box = document.getElementById('loc-out-box');
        function run() {{
          const opt = mEl.options[mEl.selectedIndex];
          const pIn = opt ? parseFloat(opt.dataset.in || '0') : 0;
          const pOut = opt ? parseFloat(opt.dataset.out || '0') : 0;
          const mIn = Math.max(0, parseFloat(inEl.value || '0') || 0);
          const mOut = Math.max(0, parseFloat(outEl.value || '0') || 0);
          const hw = Math.max(0, parseFloat(hwEl.value || '0') || 0);
          const amort = Math.max(1, parseFloat(amEl.value || '24') || 24);
          const watts = Math.max(0, parseFloat(wEl.value || '0') || 0);
          const kwh = Math.max(0, parseFloat(kEl.value || '0') || 0);

          const apiMo = mIn * pIn + mOut * pOut;
          const powerMo = (watts / 1000) * 24 * 30 * kwh;
          const localAmortMo = (hw / amort) + powerMo;
          const netSavings = apiMo - powerMo;
          const payback = hw <= 0 ? '0.0 months ($0 hardware)' : (netSavings > 0 ? (hw / netSavings).toFixed(1) + ' months' : 'Never (electricity alone exceeds API bill)');

          box.innerHTML =
            '<div><strong>Managed API Monthly Spend:</strong> $' + apiMo.toFixed(2) + '/mo</div>' +
            '<div><strong>Local Amortized Monthly Cost:</strong> $' + localAmortMo.toFixed(2) + '/mo <span class="muted small">(depreciation $' + (hw / amort).toFixed(2) + ' + power $' + powerMo.toFixed(2) + ')</span></div>' +
            '<div style="margin-top:6px"><strong>Cash Hardware Payback Period:</strong> ' + payback + '</div>';
        }}
        [mEl, inEl, outEl, hwEl, amEl, wEl, kEl].forEach(el => {{ el.addEventListener('input', run); el.addEventListener('change', run); }});
        run();
      }})();
    </script>
    """
    loc_notes_html = f"""
    <div class="card">
      <h2>Typical Local LLM Hardware Reference Profiles</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Local Setup</th><th>Est. Hardware Cost</th><th>Avg. Power Draw (24/7)</th><th>Monthly Power (@ $0.18/kWh)</th><th>When Local Beats API</th></tr></thead>
          <tbody>
            <tr><td><strong>Single 24GB GPU Workstation</strong></td><td>$2,500</td><td>350W</td><td>$45.36/mo</td><td>Steady &gt;$150/mo API spend or strict on-prem privacy requirements</td></tr>
            <tr><td><strong>Dual-GPU 48GB Inference Server</strong></td><td>$6,000</td><td>700W</td><td>$90.72/mo</td><td>High continuous team token volume (&gt;$400/mo API spend)</td></tr>
            <tr><td><strong>Low-Volume Dev / Burst Traffic</strong></td><td>$0 CapEx (API)</td><td>0W idle</td><td>$0/mo</td><td>Managed per-token APIs or <a href="/free-llm-apis/">free tiers</a> usually win when GPU utilization is low</td></tr>
          </tbody>
        </table>
      </div>
      <p class="muted small" style="margin-top:8px">Evaluating open-source local runtimes? Browse <a href="/self-hosting/">Self-Hosted AI Stacks &amp; GitHub Repos</a> (Ollama, vLLM, LiteLLM, Open WebUI).</p>
    </div>
    <div class="card">
      <h2>Formula, Worked Example &amp; Data Scope</h2>
      <ul>
        <li><strong>Formula:</strong> <code>API_Monthly = (Input_M &times; Input_Rate) + (Output_M &times; Output_Rate)</code>; <code>Power_Monthly = (Watts / 1000) &times; 24 &times; 30 &times; $/kWh</code>; <code>Payback_Months = Hardware_Cost / (API_Monthly - Power_Monthly)</code>.</li>
        <li><strong>Worked Example:</strong> At <code>100M</code> input + <code>25M</code> output tokens/mo on an API priced at <code>$0.50/1M</code> in and <code>$2.00/1M</code> out, API spend is <code>$100.00/mo</code>. A <code>$2,500</code> rig drawing <code>350W</code> at <code>$0.18/kWh</code> uses <code>$45.36/mo</code> in power &rarr; net savings <code>$54.64/mo</code> &rarr; <code>45.8 months</code> cash payback.</li>
        <li><strong>Scope &amp; Limits:</strong> Reference API options include real-time paid endpoints with verified token prices from <a href="https://openrouter.ai/api/v1/models" target="_blank" rel="noopener noreferrer">OpenRouter Public Models API</a> ({esc(date_lbl)}); excludes async <code>:batch</code>, free-tier caps, unknown prices, and local quantization quality tradeoffs.</li>
      </ul>
    </div>
    """
    loc_elig = _eval(
        "/tools/api-vs-local-llm-calculator/",
        "tool",
        "index",
        _tool_cver(loc_notes_html, policies={"affiliates": [a.get("url") for a in cfg.get("monetization", {}).get("affiliates", []) if a.get("url")]}),
    )
    local_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / API vs. Local LLM Cost Calculator</div>
    <h1>API vs. Local LLM Cost Calculator: GPU Hardware Break-Even</h1>
    <p class="muted">Compare buying local GPU hardware (amortization + 24/7 electricity) against paying per-token for managed real-time LLM APIs. Last pricing sync: <strong>{esc(date_lbl)}</strong>.</p>
    {local_widget}
    {_ad_slot("adsense_slot_in_article", loc_elig)}
    {loc_notes_html}
    {_render_tool_affiliates(cfg, esc)}
    {_render_embed_and_cite_card(base, "api-vs-local-llm-calculator", "SysV API vs Local LLM Cost Calculator", esc)}
    {_render_related_tools_card("api-vs-local-llm-calculator", esc)}
    """
    loc_json_ld = _build_tool_json_ld(
        base,
        "/tools/api-vs-local-llm-calculator/",
        "SysV API vs Local LLM Cost Calculator",
        "Compare managed LLM API token spending against local GPU hardware amortization and electricity costs to calculate exact break-even months.",
        [
            (
                "How do you calculate API vs local LLM break-even payback months?",
                "Subtract monthly local electricity cost from monthly managed API spend to find net monthly cash savings, then divide upfront hardware cost by those net monthly savings. If electricity exceeds API spend, hardware never pays back on cost alone.",
            ),
            (
                "When is a managed LLM API cheaper than self-hosting a local GPU?",
                "When monthly token usage is bursty or low, electricity plus 24-month hardware depreciation exceeds per-token API spend—especially with sub-$0.50/1M paid models.",
            ),
        ],
    )
    urls.append("/tools/api-vs-local-llm-calculator/")
    register_page_fn(
        "tools/api-vs-local-llm-calculator/index.html",
        "/tools/api-vs-local-llm-calculator/",
        "tool",
        "index",
        _sem_hash(["local-tool-v2.4", calc_sig]),
        render_layout_fn(
            cfg,
            title="API vs Local LLM Cost Calculator — GPU Break-Even, Power & Payback | SysV",
            description="Compare managed LLM API token spending against local GPU hardware amortization and electricity costs to calculate exact break-even months.",
            canonical_path="/tools/api-vs-local-llm-calculator/",
            body_html=local_body,
            git_commit=git_commit,
            index_policy="index",
            ads_enabled=loc_elig["ads_enabled"],
            verification_enabled=loc_elig["verification_enabled"],
            runtime_cfg=runtime_cfg,
            breadcrumbs=[("Home", "/"), ("Tools", "/tools/"), ("API vs Local LLM Calculator", "/tools/api-vs-local-llm-calculator/")],
            extra_json_ld=loc_json_ld,
        ),
    )
    _register_embed(
        "embed/api-vs-local-llm-calculator/index.html",
        "/embed/api-vs-local-llm-calculator/",
        render_embed_layout_fn(
            cfg,
            title="API vs Local LLM Break-Even Calculator (Embed) | SysV",
            canonical_path="/tools/api-vs-local-llm-calculator/",
            body_html=f"<h1 style='font-size:1.25rem;margin-top:0'>API vs Local LLM Break-Even</h1>{local_widget}",
        ),
    )

    # 6. Linkable Assets under /data/ (iframe snippets point to AD-FREE /embed/!)
    active_models = [m for m in models if m.get("status", "active") == "active"]
    free_models = [m for m in active_models if m["pricing_type"] in ("limited_free", "free")]

    public_json = {
        "schema_version": "2.2.1",
        "source": "https://openrouter.ai/api/v1/models",
        "generated_by": base,
        "license": "CC-BY-4.0 (attribution appreciated, not required by technical lock)",
        "update_schedule": "Daily at 02:15 UTC",
        "count": len(active_models),
        "models": [
            {
                "id": m["id"],
                "name": m["name"],
                "provider": m["provider"],
                "pricing_type": m["pricing_type"],
                "prompt_usd_per_1m": m["prompt_price_per_m"],
                "completion_usd_per_1m": m["completion_price_per_m"],
                "cache_read_usd_per_1m": m.get("cache_read_price_per_m"),
                "context_length": m["context_length"],
                "is_realtime": m.get("is_realtime"),
                "capabilities": m.get("capabilities"),
                "url": f"{base}/models/{m['slug']}/",
            }
            for m in active_models
        ],
    }
    write_page_fn("data/models.json", json.dumps(public_json, indent=2) + "\n")

    policies_src = DATA_DIR / "free_provider_policies.json"
    if policies_src.exists():
        write_page_fn("data/free-provider-policies.json", policies_src.read_text(encoding="utf-8"))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "model_id", "provider", "pricing_type", "prompt_usd_per_1m", "completion_usd_per_1m", "context_length"])
    history_dir = DATA_DIR / "history"
    if history_dir.exists():
        for hfile in sorted(history_dir.glob("*.json"))[-30:]:
            snap = json.loads(hfile.read_text(encoding="utf-8"))
            sdate = snap.get("date", hfile.stem)
            for mid, info in sorted((snap.get("models") or {}).items()):
                writer.writerow(
                    [
                        sdate,
                        mid,
                        info.get("provider", ""),
                        info.get("pricing_type", ""),
                        info.get("prompt_price_per_m", ""),
                        info.get("completion_price_per_m", ""),
                        info.get("context_length", ""),
                    ]
                )
    write_page_fn("data/price-history.csv", buf.getvalue())

    free_items = "\n".join(
        "    <item>\n"
        f"      <title>{esc(m['name'])} — {esc(m['pricing_label'])}</title>\n"
        f"      <link>{esc(base + '/models/' + m['slug'] + '/')}</link>\n"
        f"      <guid>{esc(base + '/models/' + m['slug'] + '/')}</guid>\n"
        f"      <description>{esc(m.get('rate_limit_note', ''))}</description>\n"
        "    </item>"
        for m in free_models
    )
    write_page_fn(
        "data/free-models.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0">\n  <channel>\n'
        f"    <title>SysV Verified Free LLM APIs Feed</title>\n    <link>{esc(base)}/free-llm-apis/</link>\n"
        f"    <description>Daily verified list of rate-limited $0 text LLM APIs.</description>\n{free_items}\n  </channel>\n</rss>\n",
    )

    badge_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="210" height="24" role="img" aria-label="SysV Free LLM APIs">'
        '<rect width="142" height="24" rx="6" fill="#1d1d1f"/>'
        '<rect x="138" width="72" height="24" rx="6" fill="#137333"/>'
        '<text x="70" y="16" fill="#f5f5f7" font-family="-apple-system,BlinkMacSystemFont,sans-serif" font-size="11" text-anchor="middle">SysV Free LLM APIs</text>'
        f'<text x="174" y="16" fill="#ffffff" font-family="-apple-system,BlinkMacSystemFont,sans-serif" font-size="11" font-weight="600" text-anchor="middle">{len(free_models)} active</text>'
        "</svg>\n"
    )
    write_page_fn("data/badge-free-count.svg", badge_svg)

    data_hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Open Data &amp; Exports</div>
    <h1>Open LLM Pricing Datasets, CSV History &amp; Ad-Free Embeds</h1>
    <p class="muted">Free, stable, machine-readable endpoints updated daily at 02:15 UTC from the OpenRouter Public Models API. Embeddable calculator endpoints under <code>/embed/</code> contain <strong>zero ads and zero third-party analytics</strong>.</p>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Resource</th><th>Format</th><th>Update Cadence</th><th>Contents</th></tr></thead>
        <tbody>
          <tr><td><a href="/data/models.json"><code>/data/models.json</code></a></td><td>JSON (v2.2.1)</td><td>Daily 02:15 UTC</td><td>{len(active_models)} active models with 8-state pricing &amp; verified capability flags</td></tr>
          <tr><td><a href="/data/free-provider-policies.json"><code>/data/free-provider-policies.json</code></a></td><td>JSON (v1.0)</td><td>Daily / On Change</td><td>Structured cross-provider free-tier policies, credit-card rules, Neuron pricing &amp; policy changelog</td></tr>
          <tr><td><a href="/data/price-history.csv"><code>/data/price-history.csv</code></a></td><td>CSV</td><td>Daily 02:15 UTC</td><td>Up to 30 days of archived daily pricing snapshots ({changelog.get('snapshot_days', 1)} day(s) archived)</td></tr>
          <tr><td><a href="/data/free-models.xml"><code>/data/free-models.xml</code></a></td><td>RSS 2.0</td><td>Daily 02:15 UTC</td><td>Verified rate-limited $0 free text LLM endpoints ({len(free_models)} active)</td></tr>
          <tr><td><a href="/data/badge-free-count.svg"><code>/data/badge-free-count.svg</code></a></td><td>SVG Badge</td><td>Daily 02:15 UTC</td><td>Voluntary GitHub README status badge <img src="/data/badge-free-count.svg" width="210" height="24" alt="SysV Free LLM APIs badge" style="vertical-align:middle;margin-left:6px" /></td></tr>
        </tbody>
      </table>
    </div>
    <div class="card" style="margin-top:20px">
      <h2>Voluntary Markdown Citation Snippet (GitHub README / Docs)</h2>
      <pre style="overflow-x:auto;background:#f5f5f7;padding:14px;border-radius:12px;border:1px solid rgba(0,0,0,0.08);font-size:0.84rem">![SysV Free LLM APIs](https://sysv.cn/data/badge-free-count.svg)
Live LLM API pricing &amp; daily CSV archive: https://sysv.cn/data/</pre>
      <h3>Ad-Free Embeddable Cost Calculators (4 Widgets)</h3>
      <p class="muted small">Dedicated <code>/embed/</code> endpoints strip all ads and third-party trackers to prevent invalid ad impressions on host pages.</p>
      <p class="small"><strong>1. OpenRouter Cost Calculator</strong> (<a href="/tools/openrouter-cost-calculator/">Full page</a>)</p>
      <pre style="overflow-x:auto;background:#f5f5f7;padding:12px;border-radius:12px;border:1px solid rgba(0,0,0,0.08);font-size:0.82rem">&lt;iframe src="https://sysv.cn/embed/openrouter-cost-calculator/" width="100%" height="580" style="border:1px solid #d2d2d7;border-radius:16px" loading="lazy"&gt;&lt;/iframe&gt;</pre>
      <p class="small"><strong>2. n8n AI Workflow Cost Calculator</strong> (<a href="/tools/n8n-ai-workflow-cost-calculator/">Full page</a>)</p>
      <pre style="overflow-x:auto;background:#f5f5f7;padding:12px;border-radius:12px;border:1px solid rgba(0,0,0,0.08);font-size:0.82rem">&lt;iframe src="https://sysv.cn/embed/n8n-ai-workflow-cost-calculator/" width="100%" height="580" style="border:1px solid #d2d2d7;border-radius:16px" loading="lazy"&gt;&lt;/iframe&gt;</pre>
      <p class="small"><strong>3. LLM Translation Cost Calculator</strong> (<a href="/tools/llm-translation-cost-calculator/">Full page</a>)</p>
      <pre style="overflow-x:auto;background:#f5f5f7;padding:12px;border-radius:12px;border:1px solid rgba(0,0,0,0.08);font-size:0.82rem">&lt;iframe src="https://sysv.cn/embed/llm-translation-cost-calculator/" width="100%" height="580" style="border:1px solid #d2d2d7;border-radius:16px" loading="lazy"&gt;&lt;/iframe&gt;</pre>
      <p class="small"><strong>4. API vs Local LLM Break-Even Calculator</strong> (<a href="/tools/api-vs-local-llm-calculator/">Full page</a>)</p>
      <pre style="overflow-x:auto;background:#f5f5f7;padding:12px;border-radius:12px;border:1px solid rgba(0,0,0,0.08);font-size:0.82rem">&lt;iframe src="https://sysv.cn/embed/api-vs-local-llm-calculator/" width="100%" height="580" style="border:1px solid #d2d2d7;border-radius:16px" loading="lazy"&gt;&lt;/iframe&gt;</pre>
    </div>
    """
    data_hub_elig = _eval("/data/", "linkable_asset", "index", _tool_cver(data_hub_body))
    urls.append("/data/")
    register_page_fn(
        "data/index.html",
        "/data/",
        "linkable_asset",
        "index",
        _sem_hash(["data-hub-v2.2.1", len(active_models), len(free_models), changelog.get("snapshot_days", 1)]),
        render_layout_fn(
            cfg,
            title="Open LLM Pricing JSON, CSV Price History & Ad-Free Embeds | SysV",
            description="Download free daily LLM API pricing JSON, historical CSV price archive, free-model RSS feed, and ad-free calculator embeds.",
            canonical_path="/data/",
            body_html=data_hub_body,
            git_commit=git_commit,
            index_policy="index",
            ads_enabled=data_hub_elig["ads_enabled"],
            verification_enabled=data_hub_elig["verification_enabled"],
            runtime_cfg=runtime_cfg,
            breadcrumbs=[("Home", "/"), ("Open Data", "/data/")],
        ),
    )

    return urls
