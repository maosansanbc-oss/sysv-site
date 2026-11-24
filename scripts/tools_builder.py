"""Shared JS Calculation Engine & 4 Intent-Specific Standalone Calculator Pages + Linkable Data Assets."""

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from scripts.utils import DATA_DIR, utc_today

CALC_ENGINE_JS = """/* SysV Shared LLM Cost Calculation Engine (v2.2)
 * Parity guaranteed with scripts/build_site.py:filter_task_candidates().
 * Strictly enforces null-price guards (never coerces missing/variable prices to 0).
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

  function estimateMonthlyCost(m, inputMillions, outputMillions, feeMultiplier) {
    var mult = typeof feeMultiplier === 'number' ? feeMultiplier : 1.0;
    return (Math.max(0, inputMillions) * m.in + Math.max(0, outputMillions) * m.out) * mult;
  }

  return {
    isValidPrice: isValidPrice,
    filterEligible: filterEligible,
    estimateMonthlyCost: estimateMonthlyCost
  };
})();
"""


def _sem_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def build_tools_and_data_assets(
    cfg: dict[str, Any],
    models: list[dict[str, Any]],
    calc_models: list[dict[str, Any]],
    changelog: dict[str, Any],
    git_commit: str,
    write_page_fn,
    render_layout_fn,
    register_page_fn,
    esc,
) -> list[str]:
    """Generate /assets/calc-engine.js, /tools/* pages, and /data/* linkable assets."""
    write_page_fn("assets/calc-engine.js", CALC_ENGINE_JS)
    urls: list[str] = []
    models_json_literal = json.dumps(calc_models, ensure_ascii=False)
    calc_sig = _sem_hash([(m["slug"], m["in"], m["out"], m["pricing_type"], m["tools"], m["structured"]) for m in calc_models])

    # 1. /tools/ Hub Page
    tools_hub_body = """
    <div class="breadcrumb"><a href="/">Home</a> / Tools</div>
    <h1>AI &amp; LLM Cost Calculators</h1>
    <p class="muted">Focused, decision-oriented calculators powered by daily Source Verified OpenRouter pricing metadata. Each tool applies task-specific capability gates and discloses estimation assumptions transparently.</p>
    <div class="grid">
      <div class="card">
        <span class="badge">OpenRouter</span>
        <h3><a href="/tools/openrouter-cost-calculator/">OpenRouter Pricing &amp; Cost Calculator</a></h3>
        <p class="muted small">Compare real-time vs. asynchronous <code>:batch</code> endpoints, factor in OpenRouter's 5.5% card / 5% crypto top-up fee vs. BYOK, and check free-tier 50 vs. 1,000 RPD limits.</p>
      </div>
      <div class="card">
        <span class="badge">Workflow Automation</span>
        <h3><a href="/tools/n8n-ai-workflow-cost-calculator/">n8n AI Workflow Cost Calculator</a></h3>
        <p class="muted small">Compare n8n Cloud vs. self-hosted VPS hosting plus real-time tool-calling LLM token spend—including retry-loop multiplier stress testing.</p>
      </div>
      <div class="card">
        <span class="badge">Bulk Documents</span>
        <h3><a href="/tools/llm-translation-cost-calculator/">LLM Translation &amp; 1,000-Document Cost Calculator</a></h3>
        <p class="muted small">Convert document count and word length into tokens (1,000 words &approx; 1,333 tokens) and compare sync vs. async <code>:batch</code> processing across models.</p>
      </div>
      <div class="card">
        <span class="badge">Build vs. Buy</span>
        <h3><a href="/tools/api-vs-local-llm-calculator/">API vs. Local LLM Break-Even Calculator</a></h3>
        <p class="muted small">Model GPU hardware purchase, amortization window, power draw (Watts &times; kWh), and maintenance overhead against live managed API token rates.</p>
      </div>
    </div>
    """
    urls.append("/tools/")
    register_page_fn(
        "tools/index.html",
        "/tools/",
        "tool_hub",
        "index",
        _sem_hash(["tools-hub-v2.2", calc_sig]),
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

    # 2. /tools/openrouter-cost-calculator/
    or_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / OpenRouter Cost Calculator</div>
    <h1>OpenRouter Pricing &amp; Cost Calculator</h1>
    <p class="muted">OpenRouter passes upstream token rates through at 0% token markup, but adds a <strong>5.5% credit card top-up fee</strong> (5.0% for crypto; 0% for BYOK up to $25k/mo list inference). Calculate your effective bill across sync and <code>:batch</code> endpoints below.</p>
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
            <option value="1.055">Credit Card Top-Up (+5.5% fee)</option>
            <option value="1.05">Crypto Top-Up (+5.0% fee)</option>
            <option value="1.0">BYOK — Bring Your Own Key (0% fee &le;$25k/mo)</option>
          </select>
        </div>
      </div>
      <div style="margin-top:10px">
        <label class="muted small" style="cursor:pointer"><input id="or-batch" type="checkbox" checked /> Compare asynchronous <code>:batch</code> endpoints alongside real-time models</label>
      </div>
      <div class="calc-result" id="or-out-box"></div>
      <p class="muted small" style="margin-top:10px"><strong>Estimation Scope:</strong> Base input/output token rates (Source Verified via OpenRouter API). Excludes prompt-cache discounts and long-context tiered surcharges unless noted.</p>
    </div>
    <div class="card">
      <h2>OpenRouter Free-Tier Limits Quick Reference</h2>
      <ul>
        <li><strong>Global Free RPM Cap:</strong> 20 requests/minute across <code>:free</code> models.</li>
        <li><strong>Daily RPD Without Paid History:</strong> 50 requests/day.</li>
        <li><strong>Daily RPD After &ge;$10 Lifetime Credits:</strong> 1,000 requests/day (one-time credit threshold, not a recurring subscription).</li>
        <li><strong>Dynamic Routers Warning:</strong> <code>openrouter/auto</code> and <code>openrouter/auto-code</code> have variable downstream billing (raw price <code>-1</code>) and are excluded from $0 estimates.</li>
      </ul>
      <p><a href="/free-llm-apis/">Inspect all verified rate-limited free endpoints &rarr;</a></p>
    </div>
    <script src="/assets/calc-engine.js"></script>
    <script>
      (function() {{
        const MODELS = {models_json_literal};
        const inEl = document.getElementById('or-in');
        const outEl = document.getElementById('or-out');
        const payEl = document.getElementById('or-pay');
        const bEl = document.getElementById('or-batch');
        const box = document.getElementById('or-out-box');
        function run() {{
          const mIn = Math.max(0, parseFloat(inEl.value || '0') || 0);
          const mOut = Math.max(0, parseFloat(outEl.value || '0') || 0);
          const mult = parseFloat(payEl.value || '1.055');
          const list = window.SysVCalc.filterEligible(MODELS, 'custom', bEl.checked)
            .filter(m => m.pricing_type === 'paid' || m.pricing_type === 'batch')
            .map(m => ({{ ...m, rawCost: window.SysVCalc.estimateMonthlyCost(m, mIn, mOut, 1.0), effCost: window.SysVCalc.estimateMonthlyCost(m, mIn, mOut, mult) }}))
            .sort((a, b) => a.effCost - b.effCost)
            .slice(0, 8);
          let html = '<strong>Lowest Effective Monthly Cost (' + mIn.toFixed(1) + 'M in / ' + mOut.toFixed(1) + 'M out):</strong><ol style="margin:8px 0 0;padding-left:20px">';
          list.forEach(m => {{
            const tag = m.pricing_type === 'batch' ? ' <span class="badge badge-purple">Async Batch</span>' : '';
            html += '<li><a href="/models/' + m.slug + '/"><strong>' + m.name + '</strong></a>' + tag + ' &mdash; <strong>$' + m.effCost.toFixed(2) + '/mo</strong> <span class="muted small">(raw tokens $' + m.rawCost.toFixed(2) + ')</span></li>';
          }});
          html += '</ol>';
          box.innerHTML = html;
        }}
        [inEl, outEl, payEl, bEl].forEach(el => el.addEventListener('input', run));
        bEl.addEventListener('change', run);
        run();
      }})();
    </script>
    """
    urls.append("/tools/openrouter-cost-calculator/")
    register_page_fn(
        "tools/openrouter-cost-calculator/index.html",
        "/tools/openrouter-cost-calculator/",
        "tool",
        "index",
        _sem_hash(["or-tool-v2.2", calc_sig]),
        render_layout_fn(
            cfg,
            title="OpenRouter Pricing & Cost Calculator (Incl. 5.5% Fee & Batch) | SysV",
            description="Calculate real OpenRouter API spend including the 5.5% card fee, BYOK 0% tier, async :batch discounts, and verified free-tier limits.",
            canonical_path="/tools/openrouter-cost-calculator/",
            body_html=or_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Tools", "/tools/"), ("OpenRouter Cost Calculator", "/tools/openrouter-cost-calculator/")],
        ),
    )

    # 3. /tools/n8n-ai-workflow-cost-calculator/
    n8n_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / n8n AI Workflow Cost Calculator</div>
    <h1>n8n AI Workflow Cost Calculator: Self-Hosted vs. Cloud</h1>
    <p class="muted">Estimate total monthly cost for running AI Agent nodes inside n8n—combining platform hosting (self-hosted VPS vs. n8n Cloud) with real-time <strong>Tool-Calling</strong> LLM token spend and retry-loop overhead.</p>
    <div class="card">
      <div class="calc-grid">
        <div>
          <label class="muted small" for="n8n-runs">Workflow Executions / Month</label><br />
          <input id="n8n-runs" type="number" min="100" step="500" value="10000" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="n8n-in">Avg. Input Tokens / Execution</label><br />
          <input id="n8n-in" type="number" min="50" step="100" value="1800" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="n8n-out">Avg. Output Tokens / Execution</label><br />
          <input id="n8n-out" type="number" min="20" step="50" value="400" style="width:100%;margin-top:6px" />
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
          <label class="muted small" for="n8n-vps">Self-Hosted VPS Monthly Cost (USD)</label><br />
          <input id="n8n-vps" type="number" min="0" step="1" value="6" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="n8n-cloud">n8n Cloud Plan Cost (USD/mo)</label><br />
          <input id="n8n-cloud" type="number" min="0" step="5" value="24" style="width:100%;margin-top:6px" />
        </div>
      </div>
      <div class="calc-result" id="n8n-out-box"></div>
      <p class="muted small" style="margin-top:10px"><strong>Capability Gate Applied:</strong> Only real-time models with Source Verified <code>tools</code> parameter support are ranked. Excludes async <code>:batch</code> endpoints and variable routers. Enter your actual n8n Cloud tier fee above as plans vary by execution quota.</p>
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
          const mult = parseFloat(els[3].value || '1.35');
          const vps = Math.max(0, parseFloat(els[4].value || '0') || 0);
          const cloud = Math.max(0, parseFloat(els[5].value || '0') || 0);
          const mIn = (runs * tIn * mult) / 1e6;
          const mOut = (runs * tOut * mult) / 1e6;

          const eligible = window.SysVCalc.filterEligible(MODELS, 'n8n', false);
          const paid = eligible.filter(m => m.pricing_type === 'paid')
            .map(m => ({{ ...m, tokenCost: window.SysVCalc.estimateMonthlyCost(m, mIn, mOut, 1.0) }}))
            .sort((a, b) => a.tokenCost - b.tokenCost).slice(0, 5);
          const frees = eligible.filter(m => m.pricing_type === 'limited_free' || m.pricing_type === 'free').slice(0, 4);

          let html = '<strong>Estimated Monthly Volume:</strong> ' + mIn.toFixed(2) + 'M input / ' + mOut.toFixed(2) + 'M output tokens (' + mult + 'x multiplier).';
          html += '<ol style="margin:8px 0 10px;padding-left:20px">';
          paid.forEach(m => {{
            html += '<li><a href="/models/' + m.slug + '/"><strong>' + m.name + '</strong></a> &mdash; Tokens: <strong>$' + m.tokenCost.toFixed(2) + '</strong> | Self-Hosted Total: <strong>$' + (m.tokenCost + vps).toFixed(2) + '/mo</strong> | Cloud Total: <strong>$' + (m.tokenCost + cloud).toFixed(2) + '/mo</strong></li>';
          }});
          html += '</ol>';
          if (frees.length) {{
            html += '<strong>Rate-Limited Free ($0) Tool-Calling Models (for low-volume flows &le;50–1,000 req/day):</strong> ' +
              frees.map(m => '<a href="/models/' + m.slug + '/">' + m.name + '</a>').join(' &middot; ');
          }}
          box.innerHTML = html;
        }}
        els.forEach(el => el.addEventListener('input', run));
        els[3].addEventListener('change', run);
        run();
      }})();
    </script>
    """
    urls.append("/tools/n8n-ai-workflow-cost-calculator/")
    register_page_fn(
        "tools/n8n-ai-workflow-cost-calculator/index.html",
        "/tools/n8n-ai-workflow-cost-calculator/",
        "tool",
        "index",
        _sem_hash(["n8n-tool-v2.2", calc_sig]),
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

    # 4. /tools/llm-translation-cost-calculator/
    trans_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / Bulk Translation &amp; 1,000-Document Cost Calculator</div>
    <h1>LLM Bulk Translation &amp; Document Cost Calculator</h1>
    <p class="muted">Convert real document counts and word lengths into token spend (English rule of thumb: <strong>1,000 words &approx; 1,333 tokens</strong>; CJK/multilingual output ratio adjustable) and compare real-time vs. asynchronous <code>:batch</code> discounts.</p>
    <div class="card">
      <div class="calc-grid">
        <div>
          <label class="muted small" for="tr-docs">Number of Documents</label><br />
          <input id="tr-docs" type="number" min="1" step="100" value="1000" style="width:100%;margin-top:6px" />
        </div>
        <div>
          <label class="muted small" for="tr-words">Avg. Words per Document</label><br />
          <input id="tr-words" type="number" min="50" step="100" value="1500" style="width:100%;margin-top:6px" />
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
      <p class="muted small" style="margin-top:10px"><strong>Estimation Scope:</strong> Adds 200 system-prompt input tokens per document. Excludes prompt-cache discounts and translation quality differences—always run a 20-document sample evaluation before bulk runs.</p>
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
          const docs = Math.max(1, parseFloat(dEl.value || '1000') || 1000);
          const words = Math.max(1, parseFloat(wEl.value || '1500') || 1500);
          const ratio = parseFloat(rEl.value || '1.15');
          const tokPerDoc = words * 1.333;
          const mIn = (docs * (tokPerDoc + 200)) / 1e6;
          const mOut = (docs * tokPerDoc * ratio) / 1e6;

          const paid = window.SysVCalc.filterEligible(MODELS, 'translation', bEl.checked)
            .filter(m => m.pricing_type === 'paid' || m.pricing_type === 'batch')
            .map(m => ({{ ...m, cost: window.SysVCalc.estimateMonthlyCost(m, mIn, mOut, 1.0) }}))
            .sort((a, b) => a.cost - b.cost).slice(0, 6);

          let html = '<strong>Estimated Volume for ' + docs.toLocaleString() + ' docs (' + ((docs * words) / 1e6).toFixed(2) + 'M words):</strong> ' + mIn.toFixed(2) + 'M input / ' + mOut.toFixed(2) + 'M output tokens.';
          html += '<ol style="margin:8px 0 0;padding-left:20px">';
          paid.forEach(m => {{
            const per1k = (m.cost / docs) * 1000;
            const tag = m.pricing_type === 'batch' ? ' <span class="badge badge-purple">Async Batch</span>' : '';
            html += '<li><a href="/models/' + m.slug + '/"><strong>' + m.name + '</strong></a>' + tag + ' &mdash; Total: <strong>$' + m.cost.toFixed(2) + '</strong> <span class="muted small">($' + per1k.toFixed(2) + ' per 1,000 docs)</span></li>';
          }});
          html += '</ol>';
          box.innerHTML = html;
        }}
        [dEl, wEl, rEl, bEl].forEach(el => el.addEventListener('input', run));
        [rEl, bEl].forEach(el => el.addEventListener('change', run));
        run();
      }})();
    </script>
    """
    urls.append("/tools/llm-translation-cost-calculator/")
    register_page_fn(
        "tools/llm-translation-cost-calculator/index.html",
        "/tools/llm-translation-cost-calculator/",
        "tool",
        "index",
        _sem_hash(["trans-tool-v2.2", calc_sig]),
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

    # 5. /tools/api-vs-local-llm-calculator/
    paid_opts = "".join(
        f'<option value="{esc(m["slug"])}" data-in="{m["in"]}" data-out="{m["out"]}">{esc(m["name"])} (${m["in"]} in / ${m["out"]} out)</option>'
        for m in calc_models
        if m["pricing_type"] == "paid"
    )
    local_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / <a href="/tools/">Tools</a> / API vs. Local LLM Break-Even Calculator</div>
    <h1>API vs. Local LLM Break-Even Calculator</h1>
    <p class="muted">Compare buying local GPU hardware (amortization + electricity + engineering upkeep) against paying per-token for managed real-time LLM APIs.</p>
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
      <p class="muted small" style="margin-top:10px"><strong>Caveat:</strong> Assumes 24/7 power at the entered average Watt draw and excludes engineer maintenance hours unless you add them to hardware cost. Low/bursty workloads almost always favor managed APIs; sustained high-volume or strict-privacy workloads favor local GPUs.</p>
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
          const pIn = parseFloat(opt.dataset.in || '0');
          const pOut = parseFloat(opt.dataset.out || '0');
          const mIn = Math.max(0, parseFloat(inEl.value || '0') || 0);
          const mOut = Math.max(0, parseFloat(outEl.value || '0') || 0);
          const hw = Math.max(0, parseFloat(hwEl.value || '0') || 0);
          const amort = Math.max(1, parseFloat(amEl.value || '24') || 24);
          const watts = Math.max(0, parseFloat(wEl.value || '0') || 0);
          const kwh = Math.max(0, parseFloat(kEl.value || '0') || 0);

          const apiMo = mIn * pIn + mOut * pOut;
          const powerMo = (watts / 1000) * 24 * 30 * kwh;
          const localAmortMo = (hw / amort) + powerMo;
          const netMonthlySavings = apiMo - powerMo;
          const payback = netMonthlySavings > 0 ? (hw / netMonthlySavings).toFixed(1) + ' months' : 'Never (electricity alone exceeds API bill)';

          box.innerHTML =
            '<div><strong>Managed API Monthly Spend:</strong> $' + apiMo.toFixed(2) + '/mo</div>' +
            '<div><strong>Local Amortized Monthly Cost:</strong> $' + localAmortMo.toFixed(2) + '/mo <span class="muted small">(depreciation $' + (hw / amort).toFixed(2) + ' + power $' + powerMo.toFixed(2) + ')</span></div>' +
            '<div style="margin-top:6px"><strong>Cash Hardware Payback Period:</strong> ' + payback + '</div>';
        }}
        [mEl, inEl, outEl, hwEl, amEl, wEl, kEl].forEach(el => el.addEventListener('input', run));
        mEl.addEventListener('change', run);
        run();
      }})();
    </script>
    """
    urls.append("/tools/api-vs-local-llm-calculator/")
    register_page_fn(
        "tools/api-vs-local-llm-calculator/index.html",
        "/tools/api-vs-local-llm-calculator/",
        "tool",
        "index",
        _sem_hash(["local-tool-v2.2", calc_sig]),
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

    # 6. Linkable Assets under /data/
    base = cfg["base_url"].rstrip("/")
    active_models = [m for m in models if m.get("status", "active") == "active"]
    free_models = [m for m in active_models if m["pricing_type"] in ("limited_free", "free")]

    # /data/models.json (public standardized JSON export)
    public_json = {
        "schema_version": "2.2",
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

    # /data/price-history.csv (compiled from data/history/*.json)
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

    # /data/free-models.xml (dedicated Free LLM API RSS feed)
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

    # /data/badge-free-count.svg (voluntary open citation badge)
    badge_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="210" height="24" role="img" aria-label="SysV Free LLM APIs">'
        '<rect width="142" height="24" rx="4" fill="#121a2e"/>'
        '<rect x="138" width="72" height="24" rx="4" fill="#16a34a"/>'
        '<text x="70" y="16" fill="#e6edf8" font-family="sans-serif" font-size="11" text-anchor="middle">SysV Free LLM APIs</text>'
        f'<text x="174" y="16" fill="#ffffff" font-family="sans-serif" font-size="11" font-weight="700" text-anchor="middle">{len(free_models)} active</text>'
        "</svg>\n"
    )
    write_page_fn("data/badge-free-count.svg", badge_svg)

    # /data/ Hub Documentation Page
    data_hub_body = f"""
    <div class="breadcrumb"><a href="/">Home</a> / Open Data &amp; Exports</div>
    <h1>Open LLM Pricing Datasets, CSV History &amp; Feeds</h1>
    <p class="muted">Free, stable, machine-readable endpoints updated daily at 02:15 UTC from the OpenRouter Public Models API. Reuse freely with optional attribution to <code>sysv.cn</code>.</p>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Resource</th><th>Format</th><th>Update Cadence</th><th>Contents</th></tr></thead>
        <tbody>
          <tr><td><a href="/data/models.json"><code>/data/models.json</code></a></td><td>JSON (v2.2)</td><td>Daily 02:15 UTC</td><td>{len(active_models)} active models with 8-state pricing &amp; verified capability flags</td></tr>
          <tr><td><a href="/data/price-history.csv"><code>/data/price-history.csv</code></a></td><td>CSV</td><td>Daily 02:15 UTC</td><td>Up to 30 days of archived daily pricing snapshots ({changelog.get('snapshot_days', 1)} day(s) archived so far)</td></tr>
          <tr><td><a href="/data/free-models.xml"><code>/data/free-models.xml</code></a></td><td>RSS 2.0</td><td>Daily 02:15 UTC</td><td>Verified rate-limited $0 free text LLM endpoints ({len(free_models)} active)</td></tr>
          <tr><td><a href="/data/badge-free-count.svg"><code>/data/badge-free-count.svg</code></a></td><td>SVG Badge</td><td>Daily 02:15 UTC</td><td>Voluntary GitHub README status badge <img src="/data/badge-free-count.svg" alt="SysV Free LLM APIs badge" style="vertical-align:middle;margin-left:6px" /></td></tr>
        </tbody>
      </table>
    </div>
    <div class="card" style="margin-top:20px">
      <h2>Voluntary Markdown Citation Snippet (GitHub README / Docs)</h2>
      <pre style="overflow-x:auto;background:#0b1020;padding:12px;border-radius:8px;border:1px solid #263456;font-size:0.84rem">![SysV Free LLM APIs](https://sysv.cn/data/badge-free-count.svg)
Live LLM API pricing &amp; daily CSV archive: https://sysv.cn/data/</pre>
      <h3>Embeddable Cost Calculator Snippet</h3>
      <pre style="overflow-x:auto;background:#0b1020;padding:12px;border-radius:8px;border:1px solid #263456;font-size:0.84rem">&lt;iframe src="https://sysv.cn/tools/openrouter-cost-calculator/" width="100%" height="620" style="border:1px solid #263456;border-radius:12px" loading="lazy"&gt;&lt;/iframe&gt;</pre>
    </div>
    """
    urls.append("/data/")
    register_page_fn(
        "data/index.html",
        "/data/",
        "linkable_asset",
        "index",
        _sem_hash(["data-hub-v2.2", len(active_models), len(free_models), changelog.get("snapshot_days", 1)]),
        render_layout_fn(
            cfg,
            title="Open LLM Pricing JSON, CSV Price History & RSS Feeds | SysV",
            description="Download free daily LLM API pricing JSON, historical CSV price archive, free-model RSS feed, and README badges.",
            canonical_path="/data/",
            body_html=data_hub_body,
            git_commit=git_commit,
            index_policy="index",
            breadcrumbs=[("Home", "/"), ("Open Data", "/data/")],
        ),
    )

    return urls
