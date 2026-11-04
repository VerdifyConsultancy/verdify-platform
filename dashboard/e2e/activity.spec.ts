// SPDX-License-Identifier: MIT
// Activity (redesign §5.8 Alerts, §5.9 Log; SCR-06): the three alert groups from one read with the count stated once,
// repeat counts, Acknowledge in one tap with an optional note, details in place with the runbook and Open in Graphs ±2 h,
// the flow alert's spigot words and no Stop, Stop all water on a fence breach, Clear fault, Recent activity; and the
// log's facets in the URL, sticky day headers, one-line entries, an Iris plan collapsed to its changes, Load older and
// the CSV of the loaded entries.
import { readFile } from "node:fs/promises";
import type { Page } from "@playwright/test";
import { CAN_STILL_START, CAN_STILL_START_NO_ENGINE, PHONE, STOPPED, expect, fixtureRequests, fixtureWrites, load, test, toastLines, untilPolled, useScreensClock } from "./support";

const needsAction = (page: Page) => page.getByRole("region", { name: "Needs action" });
const status = (page: Page) => page.locator(".page-status");
const group = (page: Page, name: RegExp) => page.locator("details.alert-group").filter({ has: page.locator("summary", { hasText: name }) });
const logPanel = (page: Page) => page.getByRole("region", { name: "Activity log", exact: true });
const alertRow = (page: Page, id: number) => page.locator(`#alert-${id}`);
const logRows = (page: Page) => logPanel(page).locator("li.event");
const logRow = (page: Page, text: string) => logRows(page).filter({ has: page.locator(".act-entry-what", { hasText: text }) });
const writtenBy = (row: ReturnType<typeof logRow>) => row.locator(".event-by");
const filters = (page: Page) => page.getByRole("search", { name: "Activity log filters" });

test("with API-14 failing Needs action says so with Retry, never No open alerts, and the log still loads", async ({ page, request }) => {
  await useScreensClock(page, request, "activity-mixed", { fail: { "API-14": 503 } });
  await load(page, "/activity");
  await expect(needsAction(page).locator(".panel-error")).toContainText("Alerts unavailable.");
  await expect(needsAction(page).getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("No open alerts");
  await page.getByRole("navigation", { name: "Activity views" }).getByRole("link", { name: "Log" }).click();
  await expect(page).toHaveURL(/\/activity\/log$/);
  await expect(logRows(page)).toHaveCount(6);
});

test("alerts-merged: the status line states the count once, Needs action holds the unacknowledged with repeat counts, the acknowledged one is collapsed", async ({ page, request }) => {
  await useScreensClock(page, request, "alerts-merged");
  await load(page, "/activity");
  await expect(status(page)).toHaveText("2 open · 1 acknowledged");
  await expect(needsAction(page).getByRole("heading", { level: 2 })).toHaveText("Needs action");
  await expect(needsAction(page).locator("ol.alert-list > li")).toHaveCount(2);
  await expect(alertRow(page, 101).locator(".alert-facts")).toHaveText("Fan 2 · ×3 since 06:02 · last 06:40");
  await expect(group(page, /^Acknowledged, still open \(1\)$/)).not.toHaveAttribute("open", "");
  await expect(group(page, /^Resolved/)).toHaveCount(0);
  expect((await fixtureRequests(request, "API-14")).map((row) => row.path)).toEqual(["/v1/alerts?status=recent&limit=200"]);
});

test("Acknowledge in one tap with a note: one API-15, who and when, the acknowledged group, the nav count down by one and an alert.ack entry", async ({ page, request }) => {
  await useScreensClock(page, request, "alerts-merged");
  await load(page, "/activity");
  const nav = page.getByRole("navigation", { name: "Sections", exact: true }).getByRole("link", { name: /^Activity/ });
  await expect(nav).toContainText("Activity 2");
  const row = alertRow(page, 101);
  await row.locator("summary").filter({ hasText: "Add a note" }).click();
  await row.getByLabel("Note (optional)").fill("On it");
  await row.getByRole("button", { name: /^Acknowledge/ }).click();
  await expect(alertRow(page, 101)).toContainText("Acknowledged by Jason · 10:42");
  await expect(group(page, /^Acknowledged, still open/).locator("summary")).toHaveText("Acknowledged, still open (2)");
  // The group opens to show the row just acknowledged here.
  await expect(group(page, /^Acknowledged, still open/)).toHaveAttribute("open", "");
  await expect(alertRow(page, 101)).toBeVisible();
  await expect(nav).toContainText("Activity 1");
  await expect(status(page)).toHaveText("1 open · 2 acknowledged");
  const sent = await fixtureRequests(request, "API-15");
  expect(sent.map((item) => [item.path, item.body])).toEqual([["/v1/alerts/101/ack", { note: "On it" }]]);
  // The entry is in the log, on its own tab.
  await page.getByRole("navigation", { name: "Activity views" }).getByRole("link", { name: "Log" }).click();
  await expect(logRow(page, "Acknowledged “Relay stuck”").first().locator(".event-reason")).toHaveText("“On it”");
});

test("an expanded alert: Resolve with a note sends one API-16, and the alert moves to the resolved group", async ({ page, request }) => {
  await useScreensClock(page, request, "alerts-merged");
  await load(page, "/activity?alert=102");
  await alertRow(page, 102).getByLabel("Note (optional)").fill("Probe reseated");
  await alertRow(page, 102).getByRole("button", { name: "Resolve" }).click();
  await expect(needsAction(page).locator("ol.alert-list > li")).toHaveCount(1);
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  await expect(group(page, /^Resolved in the last 24 h \(1\)$/)).toHaveAttribute("open", "");
  await expect(alertRow(page, 102)).toContainText("Resolved by Jason · 10:42 · “Probe reseated”");
  const sent = await fixtureRequests(request, "API-16");
  expect(sent.map((item) => [item.path, item.body])).toEqual([["/v1/alerts/102/resolve", { resolution: "Probe reseated" }]]);
});

test("nothing open: the status line says No open alerts with what was resolved, and no Needs action is drawn", async ({ page, request }) => {
  await useScreensClock(page, request, "alert-resolved");
  await load(page, "/activity");
  await expect(status(page)).toHaveText("No open alerts · 1 resolved in 24 h");
  await expect(needsAction(page)).toHaveCount(0);
  await group(page, /^Resolved/).locator("summary").click();
  await expect(alertRow(page, 104)).toContainText("Resolved by Jason · 08:42 · “Probe reseated”");
});

test("Recent activity: the five newest log entries, each one line, and Open the log", async ({ page, request }) => {
  await useScreensClock(page, request, "activity-mixed");
  await load(page, "/activity");
  const recent = page.getByRole("region", { name: "Recent activity" });
  await expect(recent.locator("li.event")).toHaveCount(5);
  await recent.getByRole("link", { name: /^Open the log/ }).click();
  await expect(page).toHaveURL(/\/activity\/log$/);
});

test("activity-mixed: one line an entry, who wrote in text, AI for Iris and the agent only, an Iris plan collapsed to its changes, causes first", async ({ page, request }) => {
  await useScreensClock(page, request, "activity-mixed");
  await load(page, "/activity/log");
  const dashboard = logRow(page, "VPD Mister Engage 1.60 → 1.55 kPa");
  await expect(writtenBy(dashboard)).toHaveText("Jason · dashboard");
  await expect(dashboard.locator(".act-entry-text")).toHaveText("VPD Mister Engage 1.60 → 1.55 kPa · requested 1.53 · manual");
  await expect(dashboard.locator(".event-by [data-badge=ai]")).toHaveCount(0);
  await expect(dashboard.locator(".event-reason")).toHaveText("“Vandas drying”");
  const iris = logRow(page, "Plan · 3 changes");
  await expect(writtenBy(iris)).toHaveText("iris AI");
  await expect(iris.locator(".event-by [data-badge=ai]")).toHaveCount(1);
  await iris.locator("summary").click();
  await expect(iris.locator(".plan-changes li")).toHaveText(["VPD Mister Engage 1.70 kPa from 13:00", "Mister Pulse Gap 55 s from 16:30", "Mister Engage Delay 90 s from 19:00"]);
  const agent = logRow(page, "Changed line L01");
  await expect(writtenBy(agent)).toHaveText("steward · agent AI");
  const forecast = logRow(page, "Forecast rule “hot afternoon”");
  await expect(forecast.locator(".act-entry-text")).toHaveText("Forecast rule “hot afternoon” · Mister Pulse Gap 45 → 60 s");
  await expect(writtenBy(forecast)).toHaveText("forecast-engine · system");
  await expect(forecast.locator(".event-by [data-badge=ai]")).toHaveCount(0);
});

test("the log groups by day under sticky headers: Today, Yesterday, then the weekday and date", async ({ page, request }) => {
  await useScreensClock(page, request, "activity-days");
  await load(page, "/activity/log");
  const heads = logPanel(page).locator("h3.act-day-head");
  await expect(heads).toHaveText(["Today", "Yesterday", "Fri 30 Oct"]);
  expect(await heads.first().evaluate((node) => getComputedStyle(node).position)).toBe("sticky");
});

test("the flow alert reads its plain title, expands with its runbook and Open in Graphs ±2 h, and offers the spigot, never a Stop", async ({ page, request }) => {
  await useScreensClock(page, request, "flow-valves-closed");
  await load(page, "/activity?alert=201");
  const flow = alertRow(page, 201);
  await expect(flow.locator("h3")).toHaveText("Water flowing with every Verdify valve closed");
  await expect(flow).toContainText("If no one is using water, close the spigot.");
  await expect(flow.getByRole("button", { name: /Stop/ })).toHaveCount(0);
  await expect(flow.locator(".runbook")).toContainText("Walk the wall line and the manifold for a break.");
  await expect(flow.getByRole("link", { name: /^Open in Graphs/ })).toHaveAttribute("href", "/graphs?from=2026-11-02T15:38:00Z&to=2026-11-02T19:38:00Z");
  await expect(flow).not.toContainText("leak_detected");
});

test("a copied /activity?alert=<id> link still opens the alert after a new repeat merges", async ({ page, request }) => {
  await useScreensClock(page, request, ["flow-valves-closed", "alert-repeat"]);
  await load(page, "/activity?alert=201");
  await expect(alertRow(page, 201).locator(".alert-detail")).toBeVisible();
  await expect(alertRow(page, 201).locator(".alert-facts")).toContainText("×2 since 10:38 · last 10:41");
  await expect(page.getByRole("region", { name: "Linked alert" })).toHaveCount(0);
});

test("the run-missed alert reads its plain title with L02, never the raw code, and Details expands it in place", async ({ page, request }) => {
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

test("activity-mixed: an old /activity?target=L01 link lands on the log; the Who chips and the dates apply at once and live in the URL", async ({ page, request }) => {
  await useScreensClock(page, request, "activity-mixed");
  await load(page, "/activity?target=L01");
  await expect(page).toHaveURL(/\/activity\/log\?target=L01$/);
  await expect(logRows(page)).toHaveCount(2);
  await filters(page).getByRole("list", { name: "Who" }).getByRole("link", { name: "Agents" }).click();
  await expect(page).toHaveURL(/\/activity\/log\?target=L01&source=agent$/);
  await expect(logRows(page)).toHaveCount(1);
  await expect(filters(page).getByRole("list", { name: "Who" }).getByRole("link", { name: "Agents" })).toHaveAttribute("aria-current", "true");
  await page.reload();
  await expect(logRows(page)).toHaveCount(1);
  await expect(filters(page).locator("[name=target]")).toHaveValue("L01");
  // The target applies when it is left, with no Apply button.
  await expect(filters(page).getByRole("button", { name: "Apply" })).toHaveCount(0);
  await filters(page).locator("[name=target]").fill("L09");
  await filters(page).locator("[name=target]").press("Enter");
  await expect(page).toHaveURL(/\/activity\/log\?target=L09&source=agent$/);
  await filters(page).getByRole("link", { name: "Clear filters" }).click();
  await expect(page).toHaveURL(/\/activity\/log$/);
  await filters(page).locator("[name=from]").fill("2026-11-02");
  await expect(page).toHaveURL(/\/activity\/log\?from=2026-11-02$/);
  await expect(logRows(page)).toHaveCount(5);
});

test("at 390 px Who and What are selects, the chips hidden, and the entries stack", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "activity-mixed");
  await load(page, "/activity/log");
  await expect(filters(page).getByRole("list", { name: "Who" })).toBeHidden();
  await filters(page).locator("[name=action]").selectOption("setting.");
  await expect(page).toHaveURL(/\/activity\/log\?action=setting\.$/);
  await expect(logRows(page)).toHaveCount(1);
});

test("activity-paged: Load older adds the older page under the newer one, and CSV of these entries saves exactly the loaded entries", async ({ page, request }) => {
  await useScreensClock(page, request, "activity-paged");
  await load(page, "/activity/log");
  await expect(logRows(page)).toHaveCount(50);
  await page.getByRole("button", { name: "Load older" }).click();
  await expect(logRows(page)).toHaveCount(60);
  await expect(page.getByRole("button", { name: "Load older" })).toHaveCount(0);
  expect((await fixtureRequests(request, "API-17")).at(-1)?.path).toBe("/v1/activity?cursor=50");
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "CSV of these entries" }).click()]);
  expect(download.suggestedFilename()).toBe("verdify-activity-2026-11-02.csv");
  const lines = (await readFile(await download.path(), "utf8")).replace(/^\uFEFF/, "").trim().split("\r\n");
  expect(lines).toHaveLength(61);
  expect(lines[0]).toBe("time (America/Denver),who,source,action,target,what,reason,outcome,confirmed (America/Denver),request id");
});
