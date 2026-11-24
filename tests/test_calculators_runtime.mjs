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

console.log("[runtime_js] calc-engine.js boundary assertions PASSED");

// 2. Verify all 4 ad-free /embed/ pages strictly exclude AdSense & trackers
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
console.log("[runtime_js] 4 ad-free /embed/ pages verified clean");

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
  const targets = [
    ["/", "Lowest Estimated Token Cost"],
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
