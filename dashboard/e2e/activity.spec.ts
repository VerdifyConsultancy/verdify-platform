// SPDX-License-Identifier: MIT
// SCR-06 Activity and alerts (#1133): a failed read that never says "No open alerts", Acknowledge and Resolve with a
// note, who wrote what in the log, the flow alert and its link, Stop all water on a fence breach, Clear fault, and the
// filters and paging in the URL.
import type { Page } from "@playwright/test";
import { CAN_STILL_START, CAN_STILL_START_NO_ENGINE, STOPPED, expect, fixtureRequests, fixtureWrites, load, test, toastLines, untilPolled, useScreensClock } from "./support";

const alertsPanel = (page: Page) => page.getByRole("region", { name: /^Open alerts/ });
const logPanel = (page: Page) => page.getByRole("region", { name: "Activity log", exact: true });
const alertRow = (page: Page, id: number) => page.locator(`#alert-${id}`);
const logRows = (page: Page) => logPanel(page).locator("li.event");
const logRow = (page: Page, title: string) => logRows(page).filter({ has: page.locator("h3", { hasText: title }) });
const writtenBy = (row: ReturnType<typeof logRow>) => row.locator(".event-body > p").first();

test("with API-14 failing the alert panel says so with Retry, never No open alerts, and the log still loads", async ({ page, request }) => {
  await useScreensClock(page, request, "activity-mixed", { fail: { "API-14": 503 } });
  await load(page, "/activity");
  await expect(alertsPanel(page).locator(".panel-error")).toContainText("Alerts unavailable.");
  await expect(alertsPanel(page).getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(alertsPanel(page)).not.toContainText("No open alerts");
  await expect(logRows(page)).toHaveCount(6);
});

test("Acknowledge with a note: one API-15, who and when, the acknowledged group, the nav count down by one and an alert.ack entry", async ({ page, request }) => {
  await useScreensClock(page, request, "alerts-merged");
  await load(page, "/activity?alert=101");
  const nav = page.getByRole("navigation", { name: "Sections", exact: true }).getByRole("link", { name: /^Activity/ });
  await expect(nav).toContainText("Activity 2");
  await alertRow(page, 101).getByLabel("Note (optional)").fill("On it");
  await alertRow(page, 101).getByRole("button", { name: "Acknowledge" }).click();
  await expect(alertRow(page, 101)).toContainText("Acknowledged by Jason · 10:42");
  await expect(alertsPanel(page).locator("details.alert-group > summary")).toHaveText("Acknowledged, still open (2)");
  await expect(nav).toContainText("Activity 1");
  await expect(alertsPanel(page).getByRole("heading", { level: 2 })).toHaveText("Open alerts · 1");
  await expect(logRow(page, "Acknowledged “Relay stuck”").first().locator(".event-reason")).toHaveText("“On it”");
  const sent = await fixtureRequests(request, "API-15");
  expect(sent.map((row) => [row.path, row.body])).toEqual([["/v1/alerts/101/ack", { note: "On it" }]]);
});

test("Resolve with a note sends one API-16 and the alert leaves the open list", async ({ page, request }) => {
  await useScreensClock(page, request, "alerts-merged");
  await load(page, "/activity?alert=102");
  await alertRow(page, 102).getByLabel("Note (optional)").fill("Probe reseated");
  await alertRow(page, 102).getByRole("button", { name: "Resolve" }).click();
  await expect(alertsPanel(page).locator(":scope > ol.alert-list > li")).toHaveCount(1);
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  const sent = await fixtureRequests(request, "API-16");
  expect(sent.map((row) => [row.path, row.body])).toEqual([["/v1/alerts/102/resolve", { resolution: "Probe reseated" }]]);
});

test("activity-mixed: who wrote in text, AI for Iris and the agent only, each change and its reason", async ({ page, request }) => {
  await useScreensClock(page, request, "activity-mixed");
  await load(page, "/activity");
  const dashboard = logRow(page, "Set VPD Mister Engage");
  await expect(writtenBy(dashboard)).toHaveText("Jason · dashboard · 1.60 → 1.55 kPa · requested 1.53 · manual");
  await expect(dashboard.locator(".event-by [data-badge=ai]")).toHaveCount(0);
  await expect(dashboard.locator(".event-reason")).toHaveText("“Vandas drying”");
  const iris = logRow(page, "Plan iris-20261102-0702");
  await expect(writtenBy(iris)).toContainText("iris AI");
  await expect(iris.locator(".event-by [data-badge=ai]")).toHaveCount(1);
  await iris.locator("summary").click();
  await expect(iris.locator(".plan-changes li")).toHaveText(["VPD Mister Engage 1.70 kPa from 13:00", "Mister Pulse Gap 55 s from 16:30", "Mister Engage Delay 90 s from 19:00"]);
  const agent = logRow(page, "Changed line L01");
  await expect(writtenBy(agent)).toContainText("steward · agent AI");
  await expect(agent.locator(".event-by [data-badge=ai]")).toHaveCount(1);
  const forecast = logRow(page, "Plan preemptive-20261102-0630");
  await expect(writtenBy(forecast)).toHaveText("forecast-engine · system · Mister Pulse Gap 45 → 60 s · rule “hot afternoon”");
  await expect(forecast.locator(".event-by [data-badge=ai]")).toHaveCount(0);
});

test("the flow alert reads its plain title, expands with its runbook and Open in Graphs ±2 h, and offers the spigot, never a Stop", async ({ page, request }) => {
  await useScreensClock(page, request, "flow-valves-closed");
  await load(page, "/activity?alert=201");
  const flow = alertRow(page, 201);
  await expect(flow.locator("h3")).toHaveText("Water flowing with every Verdify valve closed");
  await expect(flow).toContainText("If no one is using water, close the spigot.");
  await expect(flow.getByRole("button", { name: /Stop/ })).toHaveCount(0);
  await expect(flow.locator(".runbook")).toContainText("Walk the wall line and the manifold for a break.");
  await expect(flow.getByRole("link", { name: "Open in Graphs" })).toHaveAttribute("href", "/graphs?from=2026-11-02T15:38:00Z&to=2026-11-02T19:38:00Z");
  await expect(flow).not.toContainText("leak_detected");
});

test("a copied /activity?alert=<id> link still opens the alert after a new repeat merges", async ({ page, request }) => {
  await useScreensClock(page, request, ["flow-valves-closed", "alert-repeat"]);
  await load(page, "/activity?alert=201");
  await expect(alertRow(page, 201).locator(".alert-detail")).toBeVisible();
  await expect(alertRow(page, 201).locator(".alert-facts")).toContainText("seen 2 times, last 10:41");
  await expect(page.getByRole("region", { name: "Linked alert" })).toHaveCount(0);
});

test("the run-missed alert reads its plain title with L02, never the raw code, expanded too", async ({ page, request }) => {
  await useScreensClock(page, request, "run-missed");
  await load(page, "/activity");
  const missed = alertRow(page, 502);
  await expect(missed.locator("h3")).toHaveText("A scheduled watering was missed");
  await expect(missed.locator(".alert-facts")).toHaveText("L02 · since 09:35 (1 h)");
  await missed.getByRole("link", { name: /^Details/ }).click();
  await expect(page).toHaveURL(/\/activity\?alert=502$/);
  await expect(alertRow(page, 502).locator(".alert-detail")).toBeVisible();
  await expect(alertRow(page, 502)).not.toContainText("irrigation_run_missed");
});

for (const [fixtures, result] of [
  [["fence-breach"], CAN_STILL_START],
  [["fence-breach", "before-engine"], CAN_STILL_START_NO_ENGINE],
  [["fence-breach", "writes-off"], CAN_STILL_START],
] as const) {
  test(`${fixtures.join(" + ")}: Stop all water shows and sends one API-IRR-07 with no dialog`, async ({ page, request }) => {
    await useScreensClock(page, request, fixtures);
    await load(page, "/activity");
    const breach = alertRow(page, 501);
    await expect(breach.locator("h3")).toHaveText("A watering valve opened outside its program");
    await expect(breach.getByRole("button", { name: /^Acknowledge/ })).toBeEnabled({ enabled: !(fixtures as readonly string[]).includes("writes-off") });
    const stop = breach.getByRole("button", { name: "Stop all water" });
    await expect(stop).toBeEnabled();
    await stop.click();
    await expect(page.locator("dialog[open]")).toHaveCount(0);
    await untilPolled(page, () => expect(toastLines(page)).toHaveText([STOPPED, result], { timeout: 500 }));
    expect((await fixtureWrites(request)).map((write) => [write.operationId, write.body])).toEqual([["API-IRR-07", {}]]);
  });
}

test("mister-fault: Clear fault sends API-IRR-13 with the line and reads the latch back", async ({ page, request }) => {
  await useScreensClock(page, request, "mister-fault");
  await load(page, "/activity");
  await alertRow(page, 301).getByRole("button", { name: "Clear fault on L04" }).click();
  await untilPolled(page, () => expect(alertRow(page, 301)).toContainText("Latch cleared", { timeout: 500 }));
  expect((await fixtureWrites(request)).map((write) => [write.operationId, write.body])).toEqual([["API-IRR-13", { line_id: "L04" }]]);
});

test("mister-fault before the engine: no Clear fault, and the rest of the row is there", async ({ page, request }) => {
  await useScreensClock(page, request, ["mister-fault", "before-engine"]);
  await load(page, "/activity");
  await expect(alertRow(page, 301).locator("h3")).toHaveText("A mister zone faulted");
  await expect(alertRow(page, 301).getByRole("button", { name: /^Acknowledge/ })).toBeVisible();
  await expect(alertRow(page, 301).getByRole("button", { name: /Clear fault/ })).toHaveCount(0);
});

test("activity-mixed: ?target=L01 lists only L01 entries, and the filters survive a reload of the URL", async ({ page, request }) => {
  await useScreensClock(page, request, "activity-mixed");
  await load(page, "/activity?target=L01");
  const titles = logRows(page).locator("h3");
  await expect(titles).toHaveText(["Changed line L01 Confirmed 06:49", "Ran L01"]);
  const filters = page.getByRole("search", { name: "Activity log filters" });
  await filters.locator("[name=source]").selectOption("agent:steward");
  await expect(page).toHaveURL(/\/activity\?target=L01&source=agent%3Asteward$/);
  await expect(titles).toHaveText(["Changed line L01 Confirmed 06:49"]);
  await page.reload();
  await expect(titles).toHaveText(["Changed line L01 Confirmed 06:49"]);
  await expect(filters.locator("[name=source]")).toHaveValue("agent:steward");
  await expect(filters.locator("[name=target]")).toHaveValue("L01");
});

test("activity-paged: Load older adds the older page under the newer one", async ({ page, request }) => {
  await useScreensClock(page, request, "activity-paged");
  await load(page, "/activity");
  await expect(logRows(page)).toHaveCount(50);
  await page.getByRole("button", { name: "Load older" }).click();
  await expect(logRows(page)).toHaveCount(60);
  await expect(page.getByRole("button", { name: "Load older" })).toHaveCount(0);
  expect((await fixtureRequests(request, "API-17")).at(-1)?.path).toBe("/v1/activity?cursor=50");
});
