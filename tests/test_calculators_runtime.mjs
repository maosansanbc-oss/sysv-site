/**
 * V2.2.1 Real JavaScript Engine + Headless Chrome (375x812 mobile) Calculator Runtime Verification.
 * Runs in CI (ubuntu-latest has Node.js + google-chrome preinstalled).
 */

import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const DIST = path.resolve("dist");

// 1. Execute actual /assets/calc-engine.js inside Node VM and assert boundary math
const engineCode = fs.readFileSync(path.join(DIST, "assets/calc-engine.js"), "utf8");
const sandbox = { window: {} };
vm.runInNewContext(engineCode, sandbox);
const calc = sandbox.window.SysVCalc;
assert.ok(calc, "SysVCalc must attach to window");

// Boundary tests: OpenRouter 5.5% with $0.80 min, crypto 5%, BYOK $25k allowance + 5% overage, $0 & negative
const bSmall = calc.calculateOpenRouterBilling(5.0, "card", 0);
assert.equal(bSmall.minFeeTriggered, true);
assert.equal(bSmall.platformFee, 0.8);
assert.equal(bSmall.totalCashOutlay, 5.8);

const bLarge = calc.calculateOpenRouterBilling(100.0, "card", 0);
assert.equal(bLarge.minFeeTriggered, false);
assert.ok(Math.abs(bLarge.platformFee - 5.5) < 1e-6);
assert.ok(Math.abs(bLarge.totalCashOutlay - 105.5) < 1e-6);

const bBatch = calc.calculateOpenRouterBilling(2.0, "card", 25.0);
assert.equal(bBatch.topupAmount, 25.0);
assert.equal(bBatch.minFeeTriggered, false);
assert.ok(Math.abs(bBatch.platformFee - 1.375) < 1e-6);

const bZero = calc.calculateOpenRouterBilling(-10, "card", 0);
assert.equal(bZero.totalCashOutlay, 0);

const bByokFree = calc.calculateOpenRouterBilling(12000, "byok", 0);
assert.equal(bByokFree.platformFee, 0);

const bByokOver = calc.calculateOpenRouterBilling(35000, "byok", 0);
assert.equal(bByokOver.platformFee, 500);
assert.equal(bByokOver.totalCashOutlay, 35500);

// Null/NaN price must NEVER coerce to 0
assert.equal(calc.isValidPrice({ in: null, out: 1 }), false);
assert.equal(calc.isValidPrice({ in: NaN, out: 1 }), false);
assert.equal(calc.isValidPrice({ in: -1, out: 1 }), false);
assert.equal(calc.isValidPrice({ in: 0, out: 0 }), true);

// Free LLM API Quota & Burst Calculator boundary checks (reads directly from free-provider-policies.json!)
const policyDb = JSON.parse(fs.readFileSync(path.join(DIST, "data/free-provider-policies.json"), "utf8"));
const verifiedPolicy = {
  providers: policyDb.providers.map((p) => ({ ...p, verification_status: "OFFICIAL_DOCS_VERIFIED" })),
};
const fqShort = calc.calculateFreeQuota(30, 3, 5, 1.0, 1000, 500, "free_50", verifiedPolicy);
assert.equal(fqShort.requiredCalls, 90);
assert.equal(fqShort.openRouterStatus, "VERIFIED");
assert.equal(fqShort.openRouterRpdCovered, false);
assert.equal(fqShort.openRouterRpmCovered, true);
assert.equal(fqShort.openRouterDeficit, 40);
assert.equal(fqShort.groqFits, true);
assert.equal(fqShort.cfMaxFreeCalls, 863);
assert.equal(fqShort.cfCovered, true);
assert.equal(fqShort.cerebrasPermanentlyFree, false);

const fqBurst = calc.calculateFreeQuota(1, 40, 40, 1.0, 1000, 500, "free_50", verifiedPolicy);
assert.equal(fqBurst.openRouterRpdCovered, true);
assert.equal(fqBurst.openRouterRpmCovered, false);
assert.equal(fqBurst.openRouterCovered, false);
assert.equal(fqBurst.groqRpmCovered, false);

const fqPaid = calc.calculateFreeQuota(30, 3, 5, 1.0, 1000, 500, "paid_1000", verifiedPolicy);
assert.equal(fqPaid.openRouterCovered, true);
assert.equal(fqPaid.openRouterDeficit, 0);

// DOC_CHANGED_SIGNAL_STABLE & FETCH_ERROR_CACHED remain usable (no false outage); STALE/POLICY_CHANGED pause verdicts
const docDriftPolicy = {
  providers: policyDb.providers.map((p) => ({ ...p, verification_status: "DOC_CHANGED_SIGNAL_STABLE" })),
};
const fqDrift = calc.calculateFreeQuota(30, 3, 5, 1.0, 1000, 500, "free_50", docDriftPolicy);
assert.equal(fqDrift.openRouterUsable, true);
assert.equal(fqDrift.openRouterDeficit, 40);
assert.equal(fqDrift.cfMaxFreeCalls, 863);

const stalePolicy = {
  providers: policyDb.providers.map((p) => ({ ...p, verification_status: "STALE" })),
};
const fqStale = calc.calculateFreeQuota(10, 1, 2, 1.0, 1000, 500, "free_50", stalePolicy);
assert.equal(fqStale.openRouterUsable, false);
assert.equal(fqStale.openRouterStatus, "STALE");
assert.equal(fqStale.openRouterCovered, null);
assert.equal(fqStale.groqFits, null);
assert.equal(fqStale.cfCovered, null);

console.log("[runtime_js] calc-engine.js boundary assertions PASSED");

// 1b. Execute ACTUAL generated homepage calculator script from dist/index.html to verify
//     editing token inputs (#calc-in / #calc-out) preserves selected workload gate (RAG / Coding / Autosite)
//     and never silently resets #task-preset to 'custom' or leaks async :batch / short-ctx models.
const indexHtml = fs.readFileSync(path.join(DIST, "index.html"), "utf8");
const inlineScripts = [...indexHtml.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
const homeCalcScript = inlineScripts.find((s) => s.includes("document.getElementById('task-preset')"));
assert.ok(homeCalcScript, "Homepage calculator inline script must exist in dist/index.html");

function makeMockEl(initial = {}) {
  const listeners = {};
  return {
    value: initial.value ?? "",
    checked: Boolean(initial.checked),
    innerHTML: "",
    style: {},
    dataset: initial.dataset || {},
    options: initial.options || [],
    selectedIndex: initial.selectedIndex ?? 0,
    addEventListener(type, fn) {
      (listeners[type] ||= []).push(fn);
    },
    dispatch(type) {
      for (const fn of listeners[type] || []) fn({ target: this });
    },
    querySelectorAll() {
      return [];
    },
    appendChild() {},
  };
}

const presetOpts = [
  { value: "coding", dataset: { in: "18.0", out: "3.5" } },
  { value: "autosite", dataset: { in: "1.2", out: "0.4" } },
  { value: "translation", dataset: { in: "2.5", out: "2.5" } },
  { value: "rag", dataset: { in: "12.0", out: "1.0" } },
  { value: "custom", dataset: { in: "5.0", out: "1.0" } },
];
const mockEls = {
  "task-preset": makeMockEl({ value: "coding", options: presetOpts, selectedIndex: 0 }),
  "calc-in": makeMockEl({ value: "18.0" }),
  "calc-out": makeMockEl({ value: "3.5" }),
  "include-batch": makeMockEl({ checked: false }),
  "task-ranking": makeMockEl(),
  "model-search": makeMockEl({ value: "" }),
  "tier-filter": makeMockEl({ value: "all" }),
  "model-sort": makeMockEl({ value: "default" }),
  "model-tbody": makeMockEl(),
};
const domSandbox = {
  window: { SysVCalc: calc },
  document: { getElementById: (id) => mockEls[id] || makeMockEl() },
  Array,
  Math,
  Number,
  parseFloat,
  parseInt,
};
vm.runInNewContext(homeCalcScript, domSandbox);

// Select RAG (Real-Time + >=100K ctx), check include-batch, then edit token counts!
mockEls["task-preset"].value = "rag";
mockEls["task-preset"].selectedIndex = 3;
mockEls["task-preset"].dispatch("change");
assert.equal(mockEls["calc-in"].value, "12.0");
assert.equal(mockEls["calc-out"].value, "1.0");
const ragCountBefore = mockEls["task-ranking"].innerHTML.match(/\((\d+) capability-matched models\)/)?.[1];
assert.ok(ragCountBefore, "RAG ranking must report capability-matched count");

mockEls["include-batch"].checked = true;
mockEls["include-batch"].dispatch("change");
mockEls["calc-in"].value = "25.0";
mockEls["calc-in"].dispatch("input");
mockEls["calc-out"].value = "4.0";
mockEls["calc-out"].dispatch("input");

assert.equal(mockEls["task-preset"].value, "rag", "Editing token inputs must NOT reset #task-preset to 'custom'!");
assert.ok(mockEls["task-ranking"].innerHTML.includes("25.0M in / 4.0M out"), "Updated token volume must render");
const ragCountAfter = mockEls["task-ranking"].innerHTML.match(/\((\d+) capability-matched models\)/)?.[1];
assert.equal(ragCountAfter, ragCountBefore, "RAG capability-matched model count must stay identical after editing tokens");
assert.equal(mockEls["task-ranking"].innerHTML.includes("Async Batch"), false, "RAG real-time gate must NEVER leak Async Batch endpoints");

// Repeat for Coding Agent (Real-Time + Tools + >=64K ctx)
mockEls["task-preset"].value = "coding";
mockEls["task-preset"].selectedIndex = 0;
mockEls["task-preset"].dispatch("change");
const codingCountBefore = mockEls["task-ranking"].innerHTML.match(/\((\d+) capability-matched models\)/)?.[1];
mockEls["calc-in"].value = "40.0";
mockEls["calc-in"].dispatch("input");
assert.equal(mockEls["task-preset"].value, "coding", "Editing tokens must preserve Coding Agent preset");
assert.equal(
  mockEls["task-ranking"].innerHTML.match(/\((\d+) capability-matched models\)/)?.[1],
  codingCountBefore,
  "Coding Agent capability gate must stay intact after editing tokens"
);

// Verify generated /data/models.json has zero misclassified :batch models
const publicModels = JSON.parse(fs.readFileSync(path.join(DIST, "data/models.json"), "utf8")).models || [];
const badBatch = publicModels.filter(
  (m) => (m.id.toLowerCase().includes(":batch") || m.name.toLowerCase().includes("(batch)")) && (m.pricing_type !== "batch" || m.is_realtime !== false)
);
assert.equal(badBatch.length, 0, `Misclassified batch models in dist/data/models.json: ${badBatch.map((m) => m.id).join(", ")}`);
console.log("[runtime_js] homepage calculator state preservation & dist/data/models.json consistency PASSED");

// 2. Verify all 4 ad-free /embed/ pages + prohibited non-content pages strictly exclude AdSense scripts
const embedPaths = [
  "embed/openrouter-cost-calculator/index.html",
  "embed/n8n-ai-workflow-cost-calculator/index.html",
  "embed/llm-translation-cost-calculator/index.html",
  "embed/api-vs-local-llm-calculator/index.html",
];
for (const ep of embedPaths) {
  const html = fs.readFileSync(path.join(DIST, ep), "utf8");
  assert.equal(html.includes("adsbygoogle"), false, `AdSense script leaked into ${ep}`);
  assert.equal(html.includes("google-adsense-account"), false, `AdSense meta leaked into ${ep}`);
  assert.equal(html.includes('content="noindex, follow"'), true, `Missing noindex on ${ep}`);
}
for (const noAdPath of ["404.html", "repos/index.html", "privacy/index.html", "terms/index.html", "contact/index.html"]) {
  const html = fs.readFileSync(path.join(DIST, noAdPath), "utf8");
  assert.equal(html.includes("adsbygoogle"), false, `AdSense script leaked into prohibited page ${noAdPath}`);
  assert.equal(html.includes("google-adsense-account"), false, `AdSense meta leaked into prohibited page ${noAdPath}`);
}
console.log("[runtime_js] 4 ad-free /embed/ pages & 5 prohibited non-content pages verified clean");

// 3. Real Headless Chrome (375x812 mobile viewport) DOM execution check on all 4 calculators + 1 embed
let chromeBin = null;
for (const cand of ["google-chrome", "chromium-browser", "chromium"]) {
  try {
    execFileSync("which", [cand], { stdio: "ignore" });
    chromeBin = cand;
    break;
  } catch {}
}

if (!chromeBin) {
  console.log("[runtime_js] Headless Chrome not installed in this environment; skipped DOM dump.");
  process.exit(0);
}

const server = spawn("python3", ["-m", "http.server", "18765", "--bind", "127.0.0.1", "--directory", DIST], {
  stdio: "ignore",
});
await new Promise((r) => setTimeout(r, 1200));

try {
  const isUsable = (p) => ["OFFICIAL_DOCS_VERIFIED", "DOC_CHANGED_SIGNAL_STABLE", "FETCH_ERROR_CACHED"].includes(p?.verification_status);
  const orPol = policyDb.providers.find((p) => p.id === "openrouter");
  const cfPol = policyDb.providers.find((p) => p.id === "cloudflare_workers_ai");
  const cerPol = policyDb.providers.find((p) => p.id === "cerebras");
  const targets = [
    ["/", "Lowest Estimated Token Cost"],
    ["/free-llm-apis/", "Daily Demand:"],
    ["/free-llm-apis/", isUsable(orPol) ? "Short by 40 calls/day" : "Policy "],
    ["/free-llm-apis/", isUsable(cfPol) ? "863 calls/day" : "Policy "],
    ["/free-llm-apis/", isUsable(cerPol) ? "30-Day Trial Only" : "Policy "],
    ["/tools/openrouter-cost-calculator/", "Total Cash Outlay"],
    ["/tools/n8n-ai-workflow-cost-calculator/", "Self-Hosted Total:"],
    ["/tools/llm-translation-cost-calculator/", "per 1,000 docs"],
    ["/tools/api-vs-local-llm-calculator/", "Cash Hardware Payback Period:"],
    ["/embed/openrouter-cost-calculator/", "Total Cash Outlay"],
  ];
  for (const [route, expectedText] of targets) {
    const dom = execFileSync(
      chromeBin,
      [
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--window-size=375,812",
        "--virtual-time-budget=2500",
        "--dump-dom",
        `http://127.0.0.1:18765${route}`,
      ],
      { encoding: "utf8", maxBuffer: 10 * 1024 * 1024 }
    );
    assert.ok(dom.includes(expectedText), `Headless Chrome (375x812) failed to render '${expectedText}' on ${route}`);
    assert.ok(!dom.includes("NaN"), `Rendered NaN found in DOM on ${route}`);
    console.log(`[chrome_375px] OK ${route} -> rendered '${expectedText}' (no NaN)`);
  }
} finally {
  server.kill("SIGTERM");
}
