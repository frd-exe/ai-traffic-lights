import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";

// Runs against the mock backend (see playwright.config.ts). The synthetic demo city needs no tiles,
// so every external request is blocked to prove the demo works offline.
const screenshots = "../handoff/screens";
test.beforeEach(async ({ page }) => {
  await page.route(/https?:\/\/(?!127\.0\.0\.1|localhost)/, route => route.abort());
});

async function open(page: Page, scenario = "none") {
  await page.goto(scenario === "none" ? "/" : `/?scenario=${scenario}`);
  await expect(page.getByTestId("city-label")).toHaveText("Synthetic demo city");
  await expect(page.getByText(/3 signalised/)).toBeVisible();
}

async function startSplit(page: Page) {
  await page.getByLabel("Playback speed").selectOption("4");
  await page.getByRole("button", { name: "▶ Start" }).click();
  await expect(page.getByText("● Connected")).toHaveCount(2);
}

async function shot(page: Page, name: string) {
  mkdirSync(screenshots, { recursive: true });
  await page.screenshot({ path: `${screenshots}/${name}.png`, fullPage: true });
}

test("demo city by default: no search box, no area tool, no Google panel", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("searchbox")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Select area/ })).toHaveCount(0);
  await expect(page.getByText(/Google/)).toHaveCount(0);
  await expect(page.getByText("Synthetic demo city").first()).toBeVisible();
  await expect(page.getByLabel("Demand level")).toBeVisible();
  await expect(page.getByLabel("Demand multiplier")).toBeVisible();
});

test("Fixed vs AI split with live metrics, then a Surge at the same at_t in both sessions", async ({ page }) => {
  await open(page);
  const demandCalls: { session_id: string; at_t: number }[] = [];
  page.on("request", r => { if (r.url().endsWith("/api/sim/demand")) demandCalls.push(r.postDataJSON()); });
  await startSplit(page);
  await expect(page.getByTestId("session-0").getByRole("heading")).toContainText(/Fixed/);
  await expect(page.getByTestId("session-1").getByRole("heading")).toContainText(/AI/);
  await expect.poll(async () => (await page.getByTestId("shared-time").textContent()) !== "t = 0.0s").toBeTruthy();
  await page.getByRole("button", { name: /Surge/ }).click();
  await expect(page.getByTestId("demand-notice")).toContainText("Surge");
  expect(demandCalls).toHaveLength(2);
  expect(new Set(demandCalls.map(c => c.session_id)).size).toBe(2);
  expect(demandCalls[0].at_t).toBe(demandCalls[1].at_t);
  await expect(page.getByTestId("gain")).not.toHaveText("Collecting data…", { timeout: 20000 });
  await shot(page, "split-demo");
});

test("AI limit: banner, toast, fixed fallback, stream keeps running", async ({ page }) => {
  await open(page, "ai_limit");
  await startSplit(page);
  await expect(page.getByTestId("ai-banner")).toContainText("AI limit reached: signals reverted to traditional fixed timers", { timeout: 30000 });
  await expect(page.getByTestId("fallback-toast")).toBeVisible();
  const before = Number((await page.getByTestId("session-time-1").textContent())!.match(/[\d.]+/)![0]);
  await expect.poll(async () => Number((await page.getByTestId("session-time-1").textContent())!.match(/[\d.]+/)![0]))
    .toBeGreaterThan(before + 5);
  await shot(page, "ai-limit");
});
