// SPDX-License-Identifier: MIT
// SCR-07 Equipment (#1134, #1136, redesign §5.12): the board in two columns with its states and the unknown rule, both
// grow lights, the flags and the Controller card, the lanes' window in the URL and their unknown span in words, the daily
// runtime, then the bursts and Restart controller. That a viewer has no write control is roles.spec.ts.
import type { Page } from "@playwright/test";
import { DESKTOP, PHONE, expect, expectNoHorizontalScroll, fixtureRequests, fixtureWrites, load, test, untilPolled, useScreensClock } from "./support";

const board = (page: Page) => page.locator(".equipment-row");
const row = (page: Page, key: string) => page.locator(`.equipment-row[data-equipment="${key}"]`);
const state = (page: Page, key: string) => row(page, key).locator(".equipment-state");
const facts = (page: Page, key: string) => row(page, key).locator(".equipment-facts");
/** The lanes' API-05 reads, not the daily runtime's, which names its six actuators. */
const laneReads = async (request: Parameters<typeof fixtureRequests>[0]) => (await fixtureRequests(request, "API-05")).filter((read) => !read.path.includes("equipment="));

test("with the controller reporting every actuator shows its state, since, runtime and cycles; fan1 reads OFF since 10/27 04:26, never unknown", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/equipment");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Equipment");
  await expect(board(page)).toHaveCount(18);
  await expect(state(page, "fan1")).toHaveText("Off");
  await expect(facts(page, "fan1")).toHaveText("since 10/27 04:26 · on today 0 min · cycles 0");
  await expect(facts(page, "fan2")).toHaveText("since 08:31 · on today 2 h 10 min · cycles 6");
  await expect(state(page, "vent")).toHaveText("Closed");
  await expect(page.locator('.equipment-board [data-badge="unknown"]')).toHaveCount(0);
});

test("at 390 px the board and the lanes stack with no horizontal scroll", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "in-band");
  await load(page, "/equipment");
  await expect(board(page)).toHaveCount(18);
  await expect(page.locator(".lanes-canvas canvas")).toHaveCount(1);
  await expectNoHorizontalScroll(page);
});

test("with the controller not reporting every state reads unknown with its age, never OFF", async ({ page, request }) => {
  await useScreensClock(page, request, "controller-not-reporting");
  await load(page, "/equipment");
  await expect(board(page)).toHaveCount(18);
  await expect(page.locator('.equipment-row:not([data-state="unknown"])')).toHaveCount(0);
  await expect(page.locator('.equipment-board [data-badge="off"], .equipment-board [data-badge="on"]')).toHaveCount(0);
  await expect(state(page, "fan1")).toHaveText("Unknown · last report 6 d ago");
});

test("each grow light shows the controller's intent beside the Lutron readback", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/equipment");
  await expect(state(page, "grow_light_main")).toHaveText("Off (controller) · Lutron Off");
  await expect(state(page, "grow_light_grow")).toHaveText("Off (controller) · Lutron Off");
  await expect(page.locator("[data-differ]")).toHaveCount(0);
});

test("grow-lights-differ reads differ, as an amber marker with the word", async ({ page, request }) => {
  await useScreensClock(page, request, "grow-lights-differ");
  await load(page, "/equipment");
  await expect(state(page, "grow_light_grow")).toHaveText("On (controller) · Lutron Off ▲ Differ");
  await expect(page.locator("[data-differ]")).toHaveCount(1);
  await expect(row(page, "grow_light_grow").locator("[data-differ]")).toHaveClass(/tag-warn/);
});

test("with Home Assistant down both Lutron states read unknown with their age, never off, and the intent keeps its value", async ({ page, request }) => {
  await useScreensClock(page, request, ["grow-lights-differ", "ha-down"]);
  await load(page, "/equipment");
  await expect(state(page, "grow_light_main")).toHaveText("Off (controller) · Lutron Unknown · 11 min ago");
  await expect(state(page, "grow_light_grow")).toHaveText("On (controller) · Lutron Unknown · 11 min ago");
  await expect(page.locator("[data-differ]")).toHaveCount(0);
  await expect(state(page, "fan1")).toHaveText("Off");
});

test("the 72 h chip sets ?range=72h and redraws the lanes from API-05, and a reload keeps it", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/equipment");
  await expect(page.getByRole("heading", { level: 2, name: "Last 24 h" })).toBeVisible();
  await page.getByRole("list", { name: "Range" }).getByRole("link", { name: "72 h" }).click();
  await expect(page).toHaveURL(/\/equipment\?range=72h$/);
  await expect(page.getByRole("heading", { level: 2, name: "Last 72 h" })).toBeVisible();
  await expect(page.locator(".lanes-canvas canvas")).toHaveCount(1);
  await expect.poll(async () => (await laneReads(request)).map((read) => {
    const query = new URL(read.path, "http://fixture.invalid").searchParams;
    return (Date.parse(query.get("to") ?? "") - Date.parse(query.get("from") ?? "")) / 3_600_000;
  })).toEqual([24, 72]);
  await page.reload();
  await expect(page.getByRole("heading", { level: 2, name: "Last 72 h" })).toBeVisible();
});

test("the lanes-unknown span reads unknown in the key and the data table, between two offs, never as off", async ({ page, request }) => {
  await useScreensClock(page, request, "lanes-unknown");
  await load(page, "/equipment");
  await expect(page.locator(".chart-key li, .chart-legend li").filter({ hasText: /^unknown$/ }).first()).toBeVisible();
  const table = page.locator("details.chart-table");
  await table.locator("summary").click();
  const fan1 = table.locator("tbody tr").filter({ has: page.locator("th", { hasText: /^Fan 1$/ }) });
  await expect(fan1.locator("td").first()).toBeVisible();
  const states = await fan1.evaluateAll((rows) => rows.map((node) => node.children[1]?.textContent));
  expect(states).toEqual(["off", "unknown", "off"]);
});

test("Fans 15 min sends one API-IRR-12 with no dialog, and reads Fans burst · 15 min left once the readback turns on", async ({ page, request }) => {
  await useScreensClock(page, request, "burst-readback-late");
  await load(page, "/equipment");
  await expect(page.locator("[data-bursts=none]")).toHaveText("Running: none");
  await page.getByRole("button", { name: "Fans 15 min" }).click();
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  await untilPolled(page, () => expect(page.locator(".burst-panel [data-chip=burst]")).toHaveText("Fans burst · 15 min left", { timeout: 500 }));
  expect((await fixtureWrites(request)).map((write) => [write.operationId, write.body])).toEqual([["API-IRR-12", { kind: "fans", minutes: 15 }]]);
});

test("Restart controller under More actions sends one API-IRR-12 and reads Restart sent until the uptime drops", async ({ page, request }) => {
  await useScreensClock(page, request, "restart-uptime-reset");
  await load(page, "/equipment");
  await page.locator(".burst-more summary").click();
  await page.getByRole("button", { name: "Restart controller" }).click();
  await expect(page.getByRole("button", { name: "Restart sent" })).toBeDisabled();
  await untilPolled(page, () => expect(page.getByRole("button", { name: "Restart controller" })).toBeEnabled({ timeout: 500 }));
  expect((await fixtureWrites(request)).map((write) => [write.operationId, write.body])).toEqual([["API-IRR-12", { kind: "restart" }]]);
});

test("before the irrigation firmware the bursts and Restart say when they arrive, with no button", async ({ page, request }) => {
  await useScreensClock(page, request, "before-engine");
  await load(page, "/equipment");
  await expect(page.locator("[data-arrives=bursts]")).toHaveText("Bursts arrive with the irrigation firmware (manual_override)");
  await expect(page.locator("[data-arrives=restart]")).toHaveText("Restart arrives with the irrigation firmware");
  await expect(page.locator(".burst-panel [data-write-control]")).toHaveCount(0);
});

test("at 390 px the burst buttons are full width and at least 44 px", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "in-band");
  await load(page, "/equipment");
  const row = await page.locator(".burst-panel .burst-actions").first().boundingBox();
  for (const name of ["Fans 15 min", "Humidify 5 min", "Vent bypass 10 min", "Clear bursts"]) {
    const box = await page.getByRole("button", { name }).boundingBox();
    expect(box?.width, name).toBeCloseTo(row?.width ?? 0, 0);
    expect(box?.height, name).toBeGreaterThanOrEqual(44);
  }
  await expectNoHorizontalScroll(page);
});

test("at 1440 px the board is two columns side by side, Climate, Lights and Flags beside the water relays and the Controller; at 390 px they stack", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "in-band");
  await load(page, "/equipment");
  const columns = page.locator(".equipment-board > .ctl-board-col");
  await expect(columns.nth(0).locator(":scope > section h2")).toHaveText(["Climate", "Lights", "Flags"]);
  await expect(columns.nth(1).locator(":scope > section h2")).toHaveText(["Water relays", "Controller"]);
  const [left, right] = await Promise.all([columns.nth(0).boundingBox(), columns.nth(1).boundingBox()]);
  expect(right!.x).toBeGreaterThan(left!.x + left!.width - 1);
  expect(Math.abs(right!.y - left!.y)).toBeLessThanOrEqual(1);
  // Balanced: neither column runs more than half again as tall as the other.
  expect(Math.max(left!.height, right!.height) / Math.min(left!.height, right!.height)).toBeLessThan(1.5);
  await page.setViewportSize(PHONE);
  const [top, bottom] = await Promise.all([columns.nth(0).boundingBox(), columns.nth(1).boundingBox()]);
  expect(bottom!.y).toBeGreaterThanOrEqual(top!.y + top!.height);
  await expectNoHorizontalScroll(page);
});

test("the Controller card and the status line show firmware, uptime, reset reason, heap against the 32 kB floor, largest block and Wi-Fi, with their age", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/equipment");
  await expect(page.locator(".page-status")).toHaveText("Controller up 3 d 3 h · firmware 2026.10.30.1412.3240714 · Wi-Fi −61 dBm · Updated 45 s ago");
  const controller = page.getByRole("region", { name: "Controller", exact: true });
  await expect(controller.locator(".panel-state")).toHaveText("Updated 45 s ago");
  await expect(controller.locator(".ctl-heap-words")).toContainText("6 kB above the 32 kB floor");
  await expect(controller.locator(".data-pair dt")).toHaveText(["Firmware", "Uptime", "Last reset", "Largest block", "Wi-Fi"]);
  await expect(controller.locator(".data-pair dd")).toHaveText(["2026.10.30.1412.3240714", "3 d 3 h", "Software reset", "22 kB", "−61 dBm"]);
  const flags = page.getByRole("region", { name: "Flags", exact: true });
  await expect(flags.locator(".ctl-flag-state")).toHaveText(["Nobody seen · last person 05:10", "None", "No flow"]);
});

test("Daily runtime · 14 days shows one bar row per heat 1, heat 2, fan 1, fan 2, vent and fog, with the daily average and a table", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/equipment");
  const runtime = page.getByRole("region", { name: "Daily runtime · 14 days" });
  await expect(runtime.locator(".ctl-runtime-row")).toHaveCount(6);
  await expect(runtime.locator(".ctl-runtime-name")).toHaveText(["Heat 1 electric", "Heat 2 gas", "Fan 1", "Fan 2", "Vent", "Fog L11"]);
  await expect(runtime.locator(".ctl-runtime-row").first().locator(".spark-bar")).toHaveCount(14);
  await runtime.locator("summary").click();
  await expect(runtime.locator("tbody tr")).toHaveCount(14);
  await expect(runtime.locator("tbody tr").last()).toContainText("Nov 2 (today)");
  const reads = (await fixtureRequests(request, "API-05")).filter((read) => read.path.includes("equipment="));
  expect(reads).toHaveLength(1);
});

test("a light's automatic switch opens its setting drawer here, and closing it returns focus to the link", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/equipment");
  const link = row(page, "grow_light_main").getByRole("link", { name: "Automation setting of Main" });
  await link.click();
  await expect(page).toHaveURL(/\/equipment\?edit=sw_gl_main_auto_mode$/);
  await expect(page.locator("dialog[open] .drawer-heading")).toHaveText("GL Main Auto Mode");
  await page.locator("dialog[open]").getByRole("button", { name: "Cancel" }).click();
  await expect(page).toHaveURL(/\/equipment$/);
  await expect(link).toBeFocused();
});

test("controller-heap-low: free heap under the 32 kB floor turns the Controller card's rule and meter fault, in words", async ({ page, request }) => {
  await useScreensClock(page, request, "controller-heap-low");
  await load(page, "/equipment");
  const controller = page.getByRole("region", { name: "Controller", exact: true });
  await expect(controller).toHaveClass(/tone-fault/);
  await expect(controller.locator(".ctl-heap-low")).toHaveText("▲ 4 kB below the 32 kB floor");
  await expect(controller.locator("svg.ctl-heap")).toHaveClass(/is-below/);
  await expect(controller.locator(".data-pair dd").nth(3)).toHaveText("10 kB");
});
