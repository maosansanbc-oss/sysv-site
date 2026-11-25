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

  /* Free LLM API daily quota estimator (v2.2.2):
   * Evaluates daily automation call/token demand against verified free-tier limits.
   */
  function calculateFreeQuota(dailyJobs, callsPerJob, retryMult, avgTokensPerCall, openRouterTier) {
    var jobs = Math.max(0, Number(dailyJobs) || 0);
    var perJob = Math.max(0, Number(callsPerJob) || 0);
    var mult = Math.max(1.0, Number(retryMult) || 1.0);
    var tokPerCall = Math.max(0, Number(avgTokensPerCall) || 0);
    var requiredCalls = Math.ceil(jobs * perJob * mult);
    var requiredTokens = Math.round(requiredCalls * tokPerCall);
    var orLimit = openRouterTier === 'paid_1000' ? 1000 : 50;
    var orDeficit = Math.max(0, requiredCalls - orLimit);
    var groqRpd = 1000;
    var groqTpd = 200000;
    var groqFits = requiredCalls <= groqRpd && requiredTokens <= groqTpd && tokPerCall <= 8000;
    var cerebrasTpd = 1000000;
    var cerebrasFits = requiredTokens <= cerebrasTpd;
    return {
      requiredCalls: requiredCalls,
      requiredTokens: requiredTokens,
      openRouterLimit: orLimit,
      openRouterCovered: requiredCalls <= orLimit,
      openRouterDeficit: orDeficit,
      groqFits: groqFits,
      groqSinglePromptOverTpm: tokPerCall > 8000,
      cerebrasFits: cerebrasFits
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
    retry_mult: float = 1.0,
    avg_tokens_per_call: float = 1500.0,
    openrouter_tier: str = "free_50",
) -> dict[str, Any]:
    """Python parity mirror of window.SysVCalc.calculateFreeQuota for unit tests."""
    import math

    jobs = max(0.0, float(daily_jobs or 0.0))
    per_job = max(0.0, float(calls_per_job or 0.0))
    mult = max(1.0, float(retry_mult or 1.0))
    tok = max(0.0, float(avg_tokens_per_call or 0.0))
    req_calls = int(math.ceil(jobs * per_job * mult))
    req_tokens = int(round(req_calls * tok))
    or_limit = 1000 if openrouter_tier == "paid_1000" else 50
    return {
        "requiredCalls": req_calls,
        "requiredTokens": req_tokens,
        "openRouterLimit": or_limit,
        "openRouterCovered": req_calls <= or_limit,
        "openRouterDeficit": max(0, req_calls - or_limit),
        "groqFits": req_calls <= 1000 and req_tokens <= 200000 and tok <= 8000,
        "groqSinglePromptOverTpm": tok > 8000,
        "cerebrasFits": req_tokens <= 1000000,
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
) -> list[str]:
    """Generate /assets/calc-engine.js, /tools/* pages, ad-free /embed/* pages, and /data/* assets."""
    write_page_fn("assets/calc-engine.js", CALC_ENGINE_JS)
    urls: list[str] = []
    models_json_literal = json.dumps(calc_models, ensure_ascii=False)
    calc_sig = _sem_hash([(m["slug"], m["in"], m["out"], m["pricing_type"], m["tools"], m["structured"]) for m in calc_models])

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
      <div class="calc-result" id="or-out-box"></div>
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
    or_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / OpenRouter Cost Calculator</div>
    <h1>OpenRouter Pricing &amp; Cost Calculator</h1>
    <p class="muted">Compare upstream token inference costs against OpenRouter's exact top-up fee rules (<strong>5.5% card fee with $0.80 minimum</strong>, <strong>5.0% crypto</strong>, or <strong>BYOK 0% up to $25k/mo</strong>).</p>
    {or_widget}
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
    urls.append("/tools/openrouter-cost-calculator/")
    register_page_fn(
        "tools/openrouter-cost-calculator/index.html",
        "/tools/openrouter-cost-calculator/",
        "tool",
        "index",
        _sem_hash(["or-tool-v2.2.1", calc_sig]),
        render_layout_fn(
            cfg,
            title="OpenRouter Pricing & Cost Calculator (5.5% Fee, $0.80 Min & BYOK) | SysV",
            description="Calculate real OpenRouter API spend separating upstream inference cost, 5.5% card fee ($0.80 min), BYOK $25k allowance, and async :batch discounts.",
            canonical_path="/tools/openrouter-cost-calculator/",
            body_html=or_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Tools", "/tools/"), ("OpenRouter Cost Calculator", "/tools/openrouter-cost-calculator/")],
        ),
    )
    # Ad-free embed version!
    write_page_fn(
        "embed/openrouter-cost-calculator/index.html",
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
      <div class="calc-result" id="n8n-out-box"></div>
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
    n8n_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / n8n AI Workflow Cost Calculator</div>
    <h1>n8n AI Workflow Cost Calculator: Self-Hosted vs. Cloud</h1>
    <p class="muted">Compare self-hosted VPS hosting vs. n8n Cloud alongside live real-time tool-calling LLM token spend and retry multipliers.</p>
    {n8n_widget}
    {_render_tool_affiliates(cfg, esc)}
    """
    urls.append("/tools/n8n-ai-workflow-cost-calculator/")
    register_page_fn(
        "tools/n8n-ai-workflow-cost-calculator/index.html",
        "/tools/n8n-ai-workflow-cost-calculator/",
        "tool",
        "index",
        _sem_hash(["n8n-tool-v2.2.1", calc_sig]),
        render_layout_fn(
            cfg,
            title="n8n AI Workflow Cost Calculator: Self-Hosted vs Cloud | SysV",
            description="Forecast monthly n8n AI workflow costs comparing self-hosted VPS vs n8n Cloud alongside live tool-calling LLM token rates and retry loops.",
            canonical_path="/tools/n8n-ai-workflow-cost-calculator/",
            body_html=n8n_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Tools", "/tools/"), ("n8n AI Workflow Cost Calculator", "/tools/n8n-ai-workflow-cost-calculator/")],
        ),
    )
    write_page_fn(
        "embed/n8n-ai-workflow-cost-calculator/index.html",
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
      <div class="calc-result" id="tr-out-box"></div>
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
    trans_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / Bulk Translation &amp; 1,000-Document Cost Calculator</div>
    <h1>LLM Bulk Translation &amp; Document Cost Calculator</h1>
    <p class="muted">Convert document counts and word lengths into token spend and compare real-time vs. asynchronous <code>:batch</code> discounts.</p>
    {trans_widget}
    """
    urls.append("/tools/llm-translation-cost-calculator/")
    register_page_fn(
        "tools/llm-translation-cost-calculator/index.html",
        "/tools/llm-translation-cost-calculator/",
        "tool",
        "index",
        _sem_hash(["trans-tool-v2.2.1", calc_sig]),
        render_layout_fn(
            cfg,
            title="LLM Bulk Translation & 1,000-Document Cost Calculator | SysV",
            description="Calculate LLM API cost per 1,000 documents and per 1M words across real-time and async :batch models.",
            canonical_path="/tools/llm-translation-cost-calculator/",
            body_html=trans_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Tools", "/tools/"), ("LLM Translation Cost Calculator", "/tools/llm-translation-cost-calculator/")],
        ),
    )
    write_page_fn(
        "embed/llm-translation-cost-calculator/index.html",
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
      <div class="calc-result" id="loc-out-box"></div>
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
    local_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / API vs. Local LLM Break-Even Calculator</div>
    <h1>API vs. Local LLM Break-Even Calculator</h1>
    <p class="muted">Compare buying local GPU hardware (amortization + electricity) against paying per-token for managed real-time LLM APIs.</p>
    {local_widget}
    {_render_tool_affiliates(cfg, esc)}
    """
    urls.append("/tools/api-vs-local-llm-calculator/")
    register_page_fn(
        "tools/api-vs-local-llm-calculator/index.html",
        "/tools/api-vs-local-llm-calculator/",
        "tool",
        "index",
        _sem_hash(["local-tool-v2.2.1", calc_sig]),
        render_layout_fn(
            cfg,
            title="API vs Local LLM Break-Even Calculator (GPU vs Per-Token) | SysV",
            description="Compare managed LLM API token spending against local GPU hardware amortization and electricity costs to calculate exact break-even months.",
            canonical_path="/tools/api-vs-local-llm-calculator/",
            body_html=local_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Tools", "/tools/"), ("API vs Local LLM Calculator", "/tools/api-vs-local-llm-calculator/")],
        ),
    )
    write_page_fn(
        "embed/api-vs-local-llm-calculator/index.html",
        render_embed_layout_fn(
            cfg,
            title="API vs Local LLM Break-Even Calculator (Embed) | SysV",
            canonical_path="/tools/api-vs-local-llm-calculator/",
            body_html=f"<h1 style='font-size:1.25rem;margin-top:0'>API vs Local LLM Break-Even</h1>{local_widget}",
        ),
    )

    # 6. Linkable Assets under /data/ (iframe snippet now points to AD-FREE /embed/!)
    base = cfg["base_url"].rstrip("/")
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
          <tr><td><a href="/data/price-history.csv"><code>/data/price-history.csv</code></a></td><td>CSV</td><td>Daily 02:15 UTC</td><td>Up to 30 days of archived daily pricing snapshots ({changelog.get('snapshot_days', 1)} day(s) archived)</td></tr>
          <tr><td><a href="/data/free-models.xml"><code>/data/free-models.xml</code></a></td><td>RSS 2.0</td><td>Daily 02:15 UTC</td><td>Verified rate-limited $0 free text LLM endpoints ({len(free_models)} active)</td></tr>
          <tr><td><a href="/data/badge-free-count.svg"><code>/data/badge-free-count.svg</code></a></td><td>SVG Badge</td><td>Daily 02:15 UTC</td><td>Voluntary GitHub README status badge <img src="/data/badge-free-count.svg" alt="SysV Free LLM APIs badge" style="vertical-align:middle;margin-left:6px" /></td></tr>
        </tbody>
      </table>
    </div>
    <div class="card" style="margin-top:20px">
      <h2>Voluntary Markdown Citation Snippet (GitHub README / Docs)</h2>
      <pre style="overflow-x:auto;background:#f5f5f7;padding:14px;border-radius:12px;border:1px solid rgba(0,0,0,0.08);font-size:0.84rem">![SysV Free LLM APIs](https://sysv.cn/data/badge-free-count.svg)
Live LLM API pricing &amp; daily CSV archive: https://sysv.cn/data/</pre>
      <h3>Ad-Free Embeddable Cost Calculator Snippet</h3>
      <p class="muted small">Dedicated <code>/embed/</code> endpoints strip all ads and third-party trackers to prevent invalid ad impressions on host pages.</p>
      <pre style="overflow-x:auto;background:#f5f5f7;padding:14px;border-radius:12px;border:1px solid rgba(0,0,0,0.08);font-size:0.84rem">&lt;iframe src="https://sysv.cn/embed/openrouter-cost-calculator/" width="100%" height="580" style="border:1px solid #d2d2d7;border-radius:16px" loading="lazy"&gt;&lt;/iframe&gt;</pre>
    </div>
    """
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
            breadcrumbs=[("Home", "/"), ("Open Data", "/data/")],
        ),
    )

    return urls
