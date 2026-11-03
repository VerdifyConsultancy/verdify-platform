// SPDX-License-Identifier: MIT
// SCR-04 Water (#1135, #1136) at 1440 and 390 px: the board, the week and its day switcher, minutes by zone, the run ledger,
// the program banner, Stop all water on all three views, and a card's targets and the run dialog. That a viewer sees no
// Stop is roles.spec.ts, and a card's Run… and Stop in a visit are caretaker-visit.spec.ts.
import type { Page } from "@playwright/test";
import { CAN_STILL_START, CAN_STILL_START_NO_ENGINE, DESKTOP, PHONE, STOPPED, axe, expect, expectNoHorizontalScroll, fixtureWrites, load, test, toastLines, untilPolled, useScreensClock } from "./support";

const VIEWS = ["/water", "/water/schedules", "/water/history"];
const card = (page: Page, id: string) => page.locator(`.line-card[data-line="${id}"]`);
const banner = (page: Page) => page.locator("[data-program]");
const stopAll = (page: Page) => page.getByRole("button", { name: "Stop all water" });

for (const viewport of [DESKTOP, PHONE]) {
  test(`${viewport.width} px: the board shows every line; L05 and L06 are reserved and L11 is climate only with no controls`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "schedules-paused");
    await load(page, "/water");
    await expect(page.locator(".line-card")).toHaveCount(10);
    const ids = await page.locator(".line-card").evaluateAll((cards) => cards.map((node) => node.getAttribute("data-line")));
    expect(ids).toEqual(["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10", "L11"]);
    await expect(card(page, "L01")).toContainText("Today 0 of 60 min");
    await expect(card(page, "L01")).toContainText("next Wed 07:35 · 12 min");
    await expect(card(page, "L02").locator(".line-subrow")).toContainText("Fert master L03");
    for (const id of ["L05", "L06"]) await expect(card(page, id).locator(".line-card-head")).toContainText(/Disabled\s+Reserved/);
    await expect(card(page, "L11").locator(".line-card-head")).toContainText("Climate only");
    await expect(card(page, "L11").getByRole("button")).toHaveCount(0);
    await expectNoHorizontalScroll(page);
  });
}

test("the week puts each occurrence in its line × day cell, and a past L02 block reads skipped · line paused", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "schedules-paused");
  await load(page, "/water/schedules");
  const week = page.locator(".week-table");
  await expect(week.locator('tr[data-line="L01"] td[data-day="2026-11-04"] .week-block-head')).toContainText("07:35");
  await page.getByRole("link", { name: "Last 7 days" }).click();
  const past = week.locator('tr[data-line="L02"] td[data-day="2026-11-01"] .week-block');
  await expect(past).toHaveClass(/week-block-hatched/);
  await expect(past).toContainText("skipped · line paused");
});

test("at 390 px the week shows one day for every line, the switcher changes it, and nothing scrolls sideways", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "schedules-paused");
  await load(page, "/water/schedules");
  const days = page.locator(".week-table thead [data-day]").filter({ visible: true });
  await expect(days).toHaveCount(1);
  await expect(days).toHaveAttribute("data-day", "2026-11-02");
  await expect(page.locator(".week-table tbody tr")).not.toHaveCount(0);
  await page.locator(".week-days").getByRole("button", { name: /Wed/ }).click();
  await expect(days).toHaveAttribute("data-day", "2026-11-04");
  await expect(days).toHaveCount(1);
  await expectNoHorizontalScroll(page);
});

test("minutes by zone keys a no-data day, never 0, and marks today partial", async ({ page, request }) => {
  await useScreensClock(page, request, "minutes-gap");
  await load(page, "/water/history");
  await expect(page.locator(".minutes-row")).toHaveCount(5);
  await expect(page.locator(".minutes-today").first()).toHaveText("today 0 min (partial) · cap 60");
  await expect(page.locator(".chart-key").first()).toContainText("no data");
});

test("a latched mister reads its fault, and a zone on its peers says so", async ({ page, request }) => {
  await useScreensClock(page, request, ["mister-fault", "south-peer"]);
  await load(page, "/water");
  await expect(page.locator('.mister-zone[data-line="L04"] .line-fault')).toHaveText("Faulted: no misting until midnight (watchdog)");
  await expect(card(page, "L04").locator(".line-fault")).toHaveText("Faulted: no misting until midnight (watchdog)");
  await expect(page.locator('.mister-zone[data-line="L07"] .mister-source')).toHaveText("VPD from peer probes");
});

for (const [fixture, says] of [
  ["before-engine", "No irrigation engine on the controller yet: relay states only; L01 runs, and every line stops, through the bridge"],
  ["program-rev-42", "On controller: rev 42 · Confirmed 2 min ago"],
  ["program-rom", "Running the built-in program: L04 VPD only, L01 run-now, no schedules"],
]) {
  test(`${fixture}: the program banner reads what the controller runs`, async ({ page, request }) => {
    await useScreensClock(page, request, fixture);
    await load(page, "/water");
    await expect(banner(page)).toHaveText(says);
  });
}

test("with API-IRR-01 failing the page says Water status unavailable, never idle, and Stop all water stays", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band", { fail: { "API-IRR-01": 503 } });
  await load(page, "/water");
  await expect(banner(page)).toContainText("Water status unavailable.");
  await expect(page.locator(".line-card")).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText(/\bIdle\b/i);
  await expect(stopAll(page)).toBeEnabled();
});

test("the run ledger reads 0 gal for a run that moved no water, and shows no rate or flow flag", async ({ page, request }) => {
  await useScreensClock(page, request, "runs-zero-and-unknown");
  await load(page, "/water/history");
  const ledger = page.getByRole("table", { name: /Runs, newest first/ });
  await expect(ledger.locator("tbody tr").filter({ hasText: "0 gal" }).first()).toBeVisible();
  await expect(ledger.locator("tbody tr").filter({ hasText: "— no meter data" }).first()).toBeVisible();
  await expect(ledger).not.toContainText(/gal\/min|flow/i);
});

for (const path of VIEWS) {
  test(`${path}: an operator's Stop all water sends one API-IRR-07 with no dialog, then the Stop result with Disable for L01`, async ({ page, request }) => {
    await useScreensClock(page, request, "l01-running");
    await load(page, path);
    await stopAll(page).click();
    await expect(page.locator("dialog[open]")).toHaveCount(0);
    await untilPolled(page, () => expect(toastLines(page)).toHaveText([STOPPED, CAN_STILL_START], { timeout: 500 }));
    await expect(page.getByRole("button", { name: "Disable L01" })).toBeVisible();
    expect((await fixtureWrites(request)).map((write) => [write.operationId, write.body])).toEqual([["API-IRR-07", {}]]);
  });
}

for (const [fixture, result] of [["writes-off", CAN_STILL_START], ["before-engine", CAN_STILL_START_NO_ENGINE]]) {
  test(`${fixture}: Stop all water stays enabled and sends one API-IRR-07`, async ({ page, request }) => {
    await useScreensClock(page, request, fixture);
    await load(page, "/water");
    await expect(stopAll(page)).toBeEnabled();
    await stopAll(page).click();
    await untilPolled(page, () => expect(toastLines(page)).toHaveText([STOPPED, result], { timeout: 500 }));
    expect((await fixtureWrites(request)).map((write) => write.operationId)).toEqual(["API-IRR-07"]);
  });
}

test("at 390 px a card's Run… is full width and each of its targets is at least 44 px", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "in-band");
  await load(page, "/water");
  const actions = card(page, "L01").locator(".line-card-actions");
  const run = actions.getByRole("button", { name: /^Run…/ });
  const [row, wide] = await Promise.all([actions.boundingBox(), run.boundingBox()]);
  expect(wide?.width).toBeCloseTo(row?.width ?? 0, 0);
  for (const target of [run, actions.getByRole("button", { name: /^Stop/ }), actions.getByRole("link")]) expect((await target.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  await expectNoHorizontalScroll(page);
});

test("the run dialog with its most fields passes WCAG 2.2 AA at 390 px", async ({ page, request }) => {
  test.slow();
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "run-dialogs");
  await load(page, "/water");
  await card(page, "L08").getByRole("button", { name: /^Run…/ }).click();
  await expect(page.locator("dialog[open]")).toContainText("Run L08 South feed (FERT)");
  await axe(page);
});

test("at 390 px Stop reads ■ Stop all and stays reachable while the page scrolls", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "in-band");
  await load(page, "/water");
  const stop = page.getByRole("button", { name: "Stop all", exact: true });
  await expect(stop).toBeVisible();
  await expect(stop).toHaveText("■ Stop all", { useInnerText: true });
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect(stop).toBeInViewport();
});
