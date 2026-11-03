// SPDX-License-Identifier: MIT
// SCR-07 Equipment (#1134): the board's states and the unknown rule, both grow lights, the lanes' window in the URL and
// their unknown span in words. That the board has no write control for a viewer is roles.spec.ts.
import type { Page } from "@playwright/test";
import { PHONE, expect, expectNoHorizontalScroll, fixtureRequests, load, test, useScreensClock } from "./support";

const board = (page: Page) => page.locator(".equipment-row");
const row = (page: Page, key: string) => page.locator(`.equipment-row[data-equipment="${key}"]`);
const state = (page: Page, key: string) => row(page, key).locator(".equipment-state");
const facts = (page: Page, key: string) => row(page, key).locator(".equipment-facts");

test("with the controller reporting every actuator shows its state, since, runtime and cycles; fan1 reads OFF since 10/27 04:26, never unknown", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/equipment");
  await expect(board(page)).toHaveCount(18);
  await expect(state(page, "fan1")).toHaveText("Off");
  await expect(facts(page, "fan1")).toHaveText("since 10/27 04:26 · on today 0 min · cycles 0 · reported 6 d ago");
  await expect(facts(page, "fan2")).toHaveText("since 08:31 · on today 2 h 10 min · cycles 6 · reported 2 h ago");
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
  const hours = (await fixtureRequests(request, "API-05")).map((read) => {
    const query = new URL(read.path, "http://fixture.invalid").searchParams;
    return (Date.parse(query.get("to") ?? "") - Date.parse(query.get("from") ?? "")) / 3_600_000;
  });
  expect(hours).toEqual([24, 72]);
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
