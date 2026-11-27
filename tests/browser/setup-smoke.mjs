import assert from "node:assert/strict";
import path from "node:path";
import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(path.join(process.env.YONDER_BROWSER_DEPS, "package.json"));
const { chromium } = require("playwright");
const axePath = require.resolve("axe-core/axe.min.js");
const base = process.env.YONDER_BROWSER_URL || "http://127.0.0.1:3000";
const artifact = process.env.YONDER_BROWSER_ARTIFACTS || "/tmp/yonder-browser-artifacts";
await mkdir(artifact, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  // Real running local stack. Default mock routes must never unlock real inference.
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(base + "/setup");
  await page.getByText("Gateway is accepting requests.", { exact: true }).waitFor();
  await page.getByText("Mock routes are demonstrations only.", { exact: false }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Send test prompt" }).isDisabled(), true);
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), "Skip to content");
  await page.keyboard.press("Enter");
  assert.equal(await page.evaluate(() => document.activeElement?.id), "yonder-main");
  assert.equal(await page.getByRole("link", { name: "Get started" }).getAttribute("aria-current"), "page");
  await page.addScriptTag({ path: axePath });
  const results = await page.evaluate(async () => {
    return window.axe.run(document.querySelector(".setup-workspace"), {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    });
  });
  assert.deepEqual(results.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })), []);
  await page.screenshot({ path: path.join(artifact, "setup-desktop-local.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.getByText("Gateway is accepting requests.", { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => {
    const element = document.querySelector(".setup-workspace");
    return element.scrollWidth <= element.clientWidth + 1;
  }), true);
  await page.screenshot({ path: path.join(artifact, "setup-mobile-local.png"), fullPage: true });
  await page.close();

  // Explicit browser fixtures exercise success/error UI, not real model quality.
  const fixture = await browser.newPage();
  let calls = 0;
  let failInference = true;
  const headers = { "access-control-allow-origin": "*", "access-control-allow-methods": "GET,POST,OPTIONS", "access-control-allow-headers": "content-type" };
  await fixture.route("**/v1/onboarding/status", route => route.fulfill({
    json: { security_mode: "development", gateway_ready: true, aliases: [
      { alias: "fixture-real", kind: "model", status: "route_ready", experimental: false },
      { alias: "fixture-mock", kind: "mock", status: "route_ready", experimental: true },
    ] }, headers,
  }));
  await fixture.route("**/v1/chat/completions", async route => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    calls++;
    assert.equal(route.request().postDataJSON().model, "fixture-real");
    return route.fulfill({ status: failInference ? 503 : 200, headers,
      json: failInference ? { error: { code: "PROVIDER_FAILURE", message: "Fixture runtime unavailable", request_id: "fixture" } }
        : { choices: [{ message: { content: "Fixture response, not a real inference result." } }] },
    });
  });
  await fixture.goto(base + "/setup");
  const submit = fixture.getByRole("button", { name: "Send test prompt" });
  await submit.waitFor();
  await fixture.locator("#setup-alias").selectOption("fixture-real");
  assert.equal(await fixture.locator("#setup-alias option").count(), 1);
  await submit.click();
  await fixture.getByRole("alert").getByText("Fixture runtime unavailable").waitFor();
  assert.equal(await fixture.getByRole("link", { name: "Open this model in Chat" }).count(), 0);
  failInference = false;
  await submit.click();
  await fixture.getByText("Fixture response, not a real inference result.").waitFor();
  assert.equal(await fixture.getByRole("link", { name: "Open this model in Chat" }).getAttribute("href"), "/?model=fixture-real");
  assert.equal(calls, 2);
  await fixture.close();
  console.log("Real local setup, keyboard, WCAG automated checks and fixture inference UI passed.");
} finally {
  await browser.close();
}
