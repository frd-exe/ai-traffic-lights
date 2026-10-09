import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import type { SimStartRequest } from "../src/types/contract.gen";

const screenshots = "../handoff/screens";
test.beforeEach(async ({ page }) => {
  // Exercise tile failures while keeping the real mock REST/WS server online.
  await page.route(/https:\/\/(?!127\.0\.0\.1|localhost)/, route => route.abort());
});
async function prepare(page: Page, scenario = "none") {
  await page.goto(scenario === "none" ? "/" : `/?scenario=${scenario}`);
  await expect(page.getByText("MOCK BACKEND · SIMULATED")).toBeVisible();
  await page.getByRole("button", { name: "Analyze area" }).click();
  await expect(page.getByText(/roads · .*ranked junctions/)).toBeVisible();
  await page.getByLabel("Playback speed").selectOption("4");
  await page.getByRole("button", { name: "Resolve data" }).click();
  await expect(page.getByText(/Resolved profile:/)).toBeVisible();
}
async function aligned(page: Page) {
  await expect.poll(async () => {
    const values = await page.locator('[data-testid^="session-time-"]').allTextContents();
    return values.length === 2 && values[0] === values[1] && !values[0].includes("= 0.0s");
  }).toBeTruthy();
}
async function screenshot(page: Page, name: string) {
  await page.locator(".setup-panel").evaluate(element => { element.scrollTop = 0; });
  await page.locator(".simulation-space").evaluate(element => { element.scrollTop = 0; });
  mkdirSync(screenshots, { recursive: true });
  await page.screenshot({ path: `${screenshots}/${name}.png`, fullPage: true });
}

test("Enter-only search, area validation, ranked selection and concurrent shared-profile sessions", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  let geocodes = 0;
  const starts: SimStartRequest[] = [];
  page.on("request", request => {
    if (request.url().includes("/api/geocode")) geocodes++;
    if (request.url().includes("/api/sim/start")) starts.push(request.postDataJSON() as SimStartRequest);
  });
  await page.goto("/");
  const search = page.getByRole("textbox", { name: "Search for a place" });
  await search.fill("Barcelona"); expect(geocodes).toBe(0);
  await search.press("Enter"); await expect(page.getByRole("button", { name: /Eixample/ })).toBeVisible();
  expect(geocodes).toBe(1); await page.getByRole("button", { name: /Eixample/ }).click();
  await page.getByText("Coordinates · keyboard selection").click();
  await page.getByLabel("Area east").fill("2.2");
  await expect(page.getByRole("button", { name: "Analyze area" })).toBeDisabled();
  await expect(page.getByText(/Select a smaller valid area/)).toBeVisible();
  await page.getByRole("button", { name: "Use center" }).click();
  await page.getByLabel("Ignore existing OSM signals").check();
  await page.getByRole("button", { name: "Analyze area" }).click();
  await expect(page.getByText(/6 selected/)).toBeVisible();
  await page.getByLabel("Recommended locations").fill("4");
  await expect(page.getByText(/4 selected/)).toBeVisible();
  await page.getByText("Review ranked junctions").click();
  await page.getByLabel(/#1 Junction/).uncheck();
  await expect(page.getByText(/3 selected/)).toBeVisible();
  await page.getByLabel("Data source").selectOption("baseline");
  await page.getByLabel("Demand level").selectOption("rush");
  await page.getByRole("button", { name: "Resolve data" }).click();
  await expect(page.getByTestId("google-banner")).toContainText("manual baseline");
  await page.getByLabel("Seed").fill("77");
  await page.getByLabel("Playback speed").selectOption("4");
  await page.getByRole("button", { name: "Start", exact: false }).click();
  await expect(page.getByText("AI active", { exact: false })).toBeVisible();
  await expect.poll(async () => page.getByTestId("session-time-1").textContent()).not.toContain("= 0.0s");
  await aligned(page);
  expect(starts).toHaveLength(2);
  expect(starts[0].demand_profile_id).toBe(starts[1].demand_profile_id);
  expect(starts[0].seed).toBe(77); expect(starts[1].seed).toBe(77);
  expect(starts[0].selected_intersections).toEqual(starts[1].selected_intersections);
  expect(starts[0].selected_intersections).toHaveLength(3);
  // A resolve during a run must not start replacement sessions or mutate either request.
  await page.getByLabel("Data source").selectOption("google_snapshot");
  await page.getByRole("button", { name: "Resolve data" }).click();
  expect(starts).toHaveLength(2);
  await expect(page.getByTestId("google-banner")).toContainText("manual baseline");
  await page.getByRole("button", { name: "Stop", exact: false }).click();
  await expect(page.getByRole("button", { name: "Stop", exact: false })).toBeDisabled();
  expect(errors).toEqual([]);
});

test("AI cap fallback: persistent banner, one-time toast, feed, dashed chart and reset", async ({ page }) => {
  await prepare(page, "ai_limit");
  await page.getByRole("button", { name: "Start", exact: false }).click();
  await expect(page.getByTestId("ai-banner")).toBeVisible({ timeout: 18000 });
  await expect(page.getByTestId("ai-banner")).toContainText("AI limit reached: signals reverted to traditional fixed timers (since t=20 s)");
  await expect(page.getByTestId("fallback-toast")).toBeVisible();
  await expect(page.locator(".feed")).toContainText("signals reverted to traditional fixed timers");
  await expect(page.locator('svg path[stroke-dasharray="5 5"]')).toHaveCount(1);
  await aligned(page);
  await screenshot(page, "ai-limit");
  await expect(page.getByTestId("fallback-toast")).toBeHidden({ timeout: 10000 });
  await expect(page.getByTestId("ai-banner")).toBeVisible();
  const reset = page.waitForRequest(r => r.url().includes("/api/ai/reset"));
  await page.getByRole("button", { name: "Re-enable AI" }).click();
  await reset;
  await page.getByRole("button", { name: "Stop", exact: false }).click();
});

test("Google unavailable banner and offline network remain usable; snapshot demo is live", async ({ page }) => {
  await prepare(page, "google_down");
  await expect(page.getByTestId("google-banner")).toContainText("Google data unavailable: using recorded snapshot");
  await page.getByRole("button", { name: "Start", exact: false }).click();
  await expect(page.getByText("AI active", { exact: false })).toBeVisible();
  await expect(page.getByText("Offline · network view")).toHaveCount(2);
  await aligned(page);
  await expect.poll(async () => Number((await page.getByTestId("shared-time").textContent())?.match(/[\d.]+/)?.[0])).toBeGreaterThan(15);
  await screenshot(page, "google-unavailable");
  await page.getByRole("button", { name: "Stop", exact: false }).click();
  const demandRequest = page.waitForRequest(r => r.url().includes("/api/demand/resolve"));
  await page.getByRole("button", { name: "Demo mode" }).click();
  expect((await demandRequest).postDataJSON()).toMatchObject({ source: "google_snapshot", level: "rush" });
  await expect(page.getByText("DEMO · LIVE SIMULATED RESULTS")).toBeVisible();
  await expect(page.getByText(/Fixed [0-9.]+s/)).toBeVisible();
  await aligned(page);
  await expect.poll(async () => Number((await page.getByTestId("shared-time").textContent())?.match(/[\d.]+/)?.[0])).toBeGreaterThan(15);
  await screenshot(page, "split-demo");
  await page.getByRole("button", { name: "Stop", exact: false }).click();
});

test("rectangle tool and keyboard cancel", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Select area" }).click();
  await expect(page.getByText(/Drag a rectangle/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText(/Drag a rectangle/)).toBeHidden();
  await page.getByRole("button", { name: "Select area" }).click();
  const box = await page.locator(".maplibregl-canvas").first().boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + 80, box!.y + 100);
  await page.mouse.down(); await page.mouse.move(box!.x + 190, box!.y + 210, { steps: 12 }); await page.mouse.up();
  await expect(page.getByText(/Drag a rectangle/)).toBeHidden();
  await expect(page.getByRole("button", { name: "Analyze area" })).toBeEnabled();
});

test("server errors are visible and allow retry", async ({ page }) => {
  await page.goto("/");
  await page.route("**/api/area", route => route.fulfill({ status: 503, contentType: "application/json",
    body: JSON.stringify({ error: { code: "unavailable", message: "Area service temporarily unavailable" } }) }));
  await page.getByRole("button", { name: "Analyze area" }).click();
  await expect(page.getByRole("alert")).toContainText("Area service temporarily unavailable");
  await expect(page.getByRole("button", { name: "Analyze area" })).toBeEnabled();
});

test("snapshot demo shows both fallback banners together", async ({ page }) => {
  await page.goto("/?scenario=ai_limit");
  await page.getByLabel("Playback speed").selectOption("4");
  await page.getByRole("button", { name: "Demo mode" }).click();
  await expect(page.getByTestId("google-banner")).toContainText("using recorded snapshot");
  await expect(page.getByTestId("ai-banner")).toBeVisible({ timeout: 18000 });
  await aligned(page);
  await screenshot(page, "both-banners");
  await page.getByRole("button", { name: "Stop", exact: false }).click();
});

test("partial compare start rolls back the first session", async ({ page }) => {
  await prepare(page);
  let starts = 0;
  const stops: string[] = [];
  page.on("request", r => { if (r.url().endsWith("/api/sim/stop")) stops.push(r.postDataJSON().session_id as string); });
  await page.route("**/api/sim/start", async route => {
    if (++starts === 1) await route.continue();
    else await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "unavailable", message: "Second session failed" } }) });
  });
  await page.getByRole("button", { name: "Start", exact: false }).click();
  await expect(page.getByRole("alert")).toContainText("Second session failed");
  expect(stops).toHaveLength(1);
  await expect(page.getByRole("button", { name: "Start", exact: false })).toBeEnabled();
});
