// SPDX-License-Identifier: MIT
// SCR-03 Plan (#1131) at 1440 and 390 px: the card, each lane's wording, planner health and the row Open. The lanes draw
// on a canvas, so a waypoint's SUPERSEDED reads from the data table that is the chart's text equivalent.
import type { Page } from "@playwright/test";
import { DESKTOP, PHONE, expect, expectNoHorizontalScroll, fixtureWrites, load, test, useScreensClock } from "./support";

const region = (page: Page, name: string) => page.getByRole("region", { name });
const status = (page: Page, param: string) => page.locator(`[data-param=${param}] .plan-status`);

for (const viewport of [DESKTOP, PHONE]) {
  const at = `${viewport.width} px`;

  test(`${at}: a plan in force, its superseded lane in words, and no horizontal scroll`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "superseded-lane");
    await load(page, "/plan");
    const card = region(page, "Plan in force");
    await expect(card.locator(".plan-facts")).toHaveText("iris-20261102-0702 · SUNRISE · written 07:02 · to 17:30 (6 h left)");
    await expect(card.locator(".plan-hypothesis")).toContainText("Hot, dry afternoon: engage misting earlier from 13:00 and widen the pulse gap at dusk.");
    await expect(card.locator(".plan-hypothesis [data-badge=ai]")).toHaveText("AI");
    await expect(status(page, "mister_engage_kpa")).toHaveText("YOUR VALUE 1.55 · jason 08:00 · holds until Iris's next plan");
    await expect(status(page, "mister_pulse_gap_s")).toHaveText("PLANNED · AI");
    const lanes = region(page, "Planned settings");
    await lanes.locator("details.chart-table summary").click();
    const superseded = lanes.locator("details.chart-table tbody tr").filter({ hasText: "SUPERSEDED" });
    await expect(superseded).toHaveCount(1);
    await expect(superseded).toContainText("VPD Mister Engage");
    await expect(superseded).toContainText("13:00");
    await expect(superseded).toContainText("1.70 kPa");
    await expectNoHorizontalScroll(page);
    expect(await fixtureWrites(request)).toEqual([]);
  });

  test(`${at}: no plan in force reads what applies and since when, and the lanes show device values only`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "no-plan");
    await load(page, "/plan");
    const card = region(page, "Plan in force");
    await expect(card.locator(".plan-none")).toHaveText("No plan in force: band and defaults apply since 09:30");
    await expect(card.locator("[data-badge=ai], details, .plan-facts")).toHaveCount(0);
    await expect(page.locator(".plan-status")).toHaveCount(0);
    await expect(page.locator(".plan-device")).toHaveText(["1.60 kPa", "45.0 s", "60.0 s"]);
    await expectNoHorizontalScroll(page);
  });

  test(`${at}: a manual lane reads MANUAL · Iris skips, the head counts it, and Open goes to its setting`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "manual-lane");
    await load(page, "/plan");
    await expect(status(page, "mister_pulse_on_s")).toHaveText("MANUAL · Iris skips · jason since 10/30");
    await expect(region(page, "Planned settings").locator(".plan-manual")).toHaveText("1 setting MANUAL · Iris skips it");
    await page.locator("[data-param=mister_pulse_on_s]").getByRole("link", { name: "Open Mister Pulse ON" }).click();
    await expect(page).toHaveURL(/\/settings\?edit=mister_pulse_on_s$/);
    await expect(page.locator("dialog[open]")).toBeVisible();
  });

  test(`${at}: planner health marks the missed SUNSET with its failure class, and no status is a raw code`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "missed-sunset");
    await load(page, "/plan");
    const health = region(page, "Planner health");
    const missed = health.locator("[data-trigger=SUNSET][data-outcome=missed]");
    await expect(missed.locator(".trigger-outcome")).toHaveText("missed");
    await expect(missed.locator(".trigger-class")).toHaveText("no_plan_written");
    await expect(missed.locator(".trigger-mark")).toContainText("▲");
    await expect(health).not.toContainText(/\bplan_written\b|\bexpected\b|\backed\b/);
    await expectNoHorizontalScroll(page);
  });

  test(`${at}: a forecast-rule waypoint reads Forecast rule · system and never carries AI`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "forecast-rule");
    await load(page, "/plan");
    const row = page.locator("[data-param=mister_engage_delay_s]");
    await expect(status(page, "mister_engage_delay_s")).toHaveText("Forecast rule · system");
    await expect(row).not.toContainText(/\bAI\b/);
    await expect(row.locator("[data-badge=ai]")).toHaveCount(0);
    await expect(status(page, "mister_engage_kpa")).toHaveText("PLANNED · AI");
    const lanes = region(page, "Planned settings");
    await lanes.locator("details.chart-table summary").click();
    await expect(lanes.locator("details.chart-table tbody tr").filter({ hasText: "Mister Engage Delay" }).filter({ hasText: "90.0 s" })).toContainText("Forecast rule · system");
  });
}

test("at 390 px the card, health and lanes stack, with 6rem lane labels", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "superseded-lane");
  await load(page, "/plan");
  const [card, health, lanes] = await Promise.all(["Plan in force", "Planner health", "Planned settings"].map((name) => region(page, name).boundingBox()));
  if (!card || !health || !lanes) throw new Error("A Plan panel is not rendered");
  expect(health.y).toBeGreaterThanOrEqual(card.y + card.height);
  expect(lanes.y).toBeGreaterThanOrEqual(health.y + health.height);
  expect(health.x).toBe(card.x);
  // The canvas starts where the lane labels end.
  const [frame, canvas] = await Promise.all([page.locator(".lanes-plan").boundingBox(), page.locator(".lanes-canvas").boundingBox()]);
  if (!frame || !canvas) throw new Error("The plan lanes are not rendered");
  expect(canvas.x - frame.x).toBeCloseTo(6 * 16, 0);
});

test("the 72 h chip sets ?hours=72, and reloading the URL keeps it", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/plan");
  const lanes = region(page, "Planned settings");
  await expect(lanes.getByRole("link", { name: "24 h" })).toHaveAttribute("aria-current", "true");
  await lanes.getByRole("link", { name: "72 h" }).click();
  await expect(page).toHaveURL(/\/plan\?hours=72$/);
  await expect(lanes.locator("figcaption")).toHaveText("Next 72 h · one lane per planned setting");
  await page.reload();
  await expect(lanes.locator("figcaption")).toHaveText("Next 72 h · one lane per planned setting");
  await expect(lanes.getByRole("link", { name: "72 h" })).toHaveAttribute("aria-current", "true");
});
