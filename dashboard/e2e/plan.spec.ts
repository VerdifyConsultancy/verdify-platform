// SPDX-License-Identifier: MIT
// SCR-03 Plan (#1131, redesign §5.11) at 1440 and 390 px: the card with its validity bar, each lane's wording, planner
// health with its marks and 14-day strip, and a lane's label opening its setting drawer here. The lanes draw on a
// canvas, so a waypoint's SUPERSEDED reads from the data table that is the chart's text equivalent.
import type { Page } from "@playwright/test";
import { DESKTOP, PHONE, expect, expectNoHorizontalScroll, fixtureRequests, fixtureWrites, load, test, useScreensClock } from "./support";

const region = (page: Page, name: string) => page.getByRole("region", { name });
const status = (page: Page, param: string) => page.locator(`[data-param=${param}] .plan-status`);

for (const viewport of [DESKTOP, PHONE]) {
  const at = `${viewport.width} px`;

  test(`${at}: a plan in force, its superseded lane in words, and no horizontal scroll`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "superseded-lane");
    await load(page, "/plan");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Plan");
    await expect(page.locator(".page-status")).toHaveText("SUNRISE plan · written 07:02 · valid to 17:30 (6 h 48 min left) · AI");
    const card = region(page, "Plan in force");
    await expect(card.locator(".plan-facts")).toHaveText("SUNRISE · iris-20261102-0702");
    await expect(card.locator(".ctl-validity-words")).toHaveText("written 07:02 → valid to 17:30 · 6 h 48 min left");
    await expect(card.locator("svg.ctl-validity .ctl-validity-now")).toHaveCount(1);
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

  test(`${at}: a manual lane reads MANUAL · Iris skips, the head counts it, and its label opens its setting drawer here`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "manual-lane");
    await load(page, "/plan");
    await expect(status(page, "mister_pulse_on_s")).toHaveText("MANUAL · Iris skips · jason since 10/30");
    await expect(region(page, "Planned settings").locator(".plan-manual")).toHaveText("1 setting MANUAL · Iris skips it");
    await expect(page.getByRole("link", { name: /^Open/ })).toHaveCount(0);
    const label = page.locator("[data-param=mister_pulse_on_s]").getByRole("link", { name: "Mister Pulse ON" });
    expect((await label.boundingBox())!.height).toBeGreaterThanOrEqual(viewport === PHONE ? 44 : 24);
    await label.click();
    await expect(page).toHaveURL(/\/plan\?edit=mister_pulse_on_s$/);
    await expect(page.locator("dialog[open] .drawer-heading")).toHaveText("Mister Pulse ON");
    await expectNoHorizontalScroll(page);
    // Escape closes it in place, and focus goes back to the lane's label.
    await page.keyboard.press("Escape");
    await expect(page.locator("dialog[open]")).toHaveCount(0);
    await expect(page).toHaveURL(/\/plan$/);
    await expect(label).toBeFocused();
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
    // The missed SUNSET stays in view, outside the ledger's disclosure; the 36 h marks show it as failed.
    await expect(missed).toBeVisible();
    await expect(health.locator(".ctl-trigger-marks > li[data-kind=failed]")).toHaveCount(1);
    await expect(health.locator(".ctl-next-trigger")).toHaveText("Next SOLAR_MAX at 12:41");
    // The 14-day strip from daily.iris_required_*, one cell a day.
    await expect(health.locator(".strip-cell")).toHaveCount(14);
    await expect(health.locator(".ctl-reliability-count")).toHaveText("42 of 42 required plans written");
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
  await expect(lanes.getByRole("link", { name: "24 h" })).toHaveAttribute("aria-current", "page");
  await lanes.getByRole("link", { name: "72 h" }).click();
  await expect(page).toHaveURL(/\/plan\?hours=72$/);
  await expect(lanes.locator("figcaption")).toHaveText("Next 72 h · one lane per planned setting");
  await page.reload();
  await expect(lanes.locator("figcaption")).toHaveText("Next 72 h · one lane per planned setting");
  await expect(lanes.getByRole("link", { name: "72 h" })).toHaveAttribute("aria-current", "page");
});

test("Save in a lane's drawer sends one API-10 and the lane reads REQUESTED in place, with Plan's API-07 read every 5 s while it settles", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/plan");
  await page.locator("[data-param=mister_engage_kpa]").getByRole("link", { name: "VPD Mister Engage" }).click();
  const drawer = page.locator("dialog[open]");
  await drawer.getByLabel("New value").fill("1.55");
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(drawer).toHaveCount(0);
  await expect(page).toHaveURL(/\/plan$/);
  await expect(page.locator("[data-param=mister_engage_kpa] .ctl-lane-pill [data-badge=requested]")).toBeVisible();
  await expect(status(page, "mister_engage_kpa")).toHaveText(/^MANUAL · Iris skips · jason since/);
  expect((await fixtureWrites(request)).map((write) => [write.operationId, write.body])).toEqual([["API-10", { value: 1.55, manual: true }]]);
  const before = (await fixtureRequests(request, "API-07")).length;
  await page.clock.fastForward(5_000);
  await expect.poll(async () => (await fixtureRequests(request, "API-07")).length).toBeGreaterThan(before);
});

test("Find a setting lands on Settings filtered by its words", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/plan");
  await page.getByRole("searchbox", { name: "Find a setting" }).fill("pulse gap");
  await page.getByRole("button", { name: "Find" }).click();
  await expect(page).toHaveURL(/\/settings\?q=pulse\+gap$/);
  await expect(page.locator("tr.setting-row")).toHaveCount(1);
});
