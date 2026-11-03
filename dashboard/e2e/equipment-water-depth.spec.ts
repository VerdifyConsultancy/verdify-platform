// SPDX-License-Identifier: MIT
// Equipment and Water depth (#1140) at 1440 and 390 px: the Why panel's words and Graphs windows, its age when the
// controller is not reporting, a grow light's Turn on, Turn off and Back to automatic with one request per tap and the
// Lutron readback, a 503's words, who gets the controls, and daily water use with the no-valve water apart and missing days
// as gaps. What the bars paint on their canvas is daily-water-use.test.tsx; here are the key, the table and the summary.
import type { Page } from "@playwright/test";
import { DESKTOP, PHONE, axe, expect, expectNoHorizontalScroll, fixtureRequests, fixtureWrites, load, test, untilPolled, useScreensClock } from "./support";

const VIEWPORTS = [DESKTOP, PHONE];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const reason = (page: Page, key: string) => page.locator(`.why-row[data-reason="${key}"]`);
const light = (page: Page, key: string) => page.locator(`.equipment-row[data-equipment="${key}"]`);
const lutron = (page: Page, key: string) => light(page, key).locator(".equipment-state");
const automatic = (page: Page, key: string) => light(page, key).locator("[data-auto]");
const MAIN = "grow_light_main";
const GROW = "grow_light_grow";
/** Every write the fixture API has had, as sent. */
const sentWrites = async (request: Parameters<typeof fixtureWrites>[0]) => (await fixtureWrites(request)).map((write) => [write.method, write.path, write.body]);
const TURN_ON = ["POST", "/v1/equipment/lights/main/on", null];
const AUTO_MAIN = ["PUT", "/v1/settings/sw_gl_main_auto_mode", { value: 1, manual: true, reason: "Back to automatic" }];
const AUTO_GROW = ["PUT", "/v1/settings/sw_gl_grow_auto_mode", { value: 1, manual: true, reason: "Back to automatic" }];

for (const viewport of VIEWPORTS) {
  test(`${viewport.width} px: each reason reads in words with since and the Graphs window around it, under the board and above the lanes`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "why-reasons");
    await load(page, "/equipment");
    await expect(reason(page, "mode_reason")).toContainText("Climate mode");
    await expect(reason(page, "mode_reason").locator(".why-words")).toHaveText("Venting: too warm · since 11/01 13:40");
    await expect(reason(page, "fog_block_reason")).toContainText("Fog (L11)");
    await expect(reason(page, "fog_block_reason").locator(".why-words")).toHaveText("Humidity at the fog ceiling · since 09:12");
    await expect(reason(page, "moisture_block_reason")).toContainText("Misters (L04, L07, L09)");
    await expect(reason(page, "moisture_block_reason").locator(".why-words")).toHaveText("VPD in band: no misting needed · since 07:12");
    await expect(reason(page, "fog_block_reason").locator("time")).toHaveAttribute("datetime", "2026-11-02T16:12:00.000Z");
    await expect(reason(page, "mode_reason").getByRole("link", { name: /^Graphs/ })).toHaveAttribute("href", "/graphs?from=2026-11-01T18:40:00Z&to=2026-11-01T22:40:00Z");
    await expect(reason(page, "fog_block_reason").getByRole("link", { name: /^Graphs/ })).toHaveAttribute("href", "/graphs?from=2026-11-02T14:12:00Z&to=2026-11-02T18:12:00Z");
    // One card under the three groups, above the lanes, with no page scroll sideways.
    const [board, why, lanes] = await Promise.all([page.locator(".equipment-board").boundingBox(), page.getByRole("region", { name: "Why", exact: true }).boundingBox(), page.getByRole("heading", { level: 2, name: "Last 24 h" }).boundingBox()]);
    expect(why!.y).toBeGreaterThanOrEqual(board!.y + board!.height);
    expect(lanes!.y).toBeGreaterThanOrEqual(why!.y + why!.height);
    await expectNoHorizontalScroll(page);
    await axe(page);
  });
}

test("a reason's Graphs link opens Graphs on its window", async ({ page, request }) => {
  await useScreensClock(page, request, "why-reasons");
  await load(page, "/equipment");
  await reason(page, "fog_block_reason").getByRole("link", { name: /^Graphs/ }).click();
  await expect(page).toHaveURL(/\/graphs\?from=2026-11-02T14(:|%3A)12(:|%3A)00Z&to=2026-11-02T18(:|%3A)12(:|%3A)00Z$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

test("why-unknown-code renders brand_new_code raw, in mono", async ({ page, request }) => {
  await useScreensClock(page, request, "why-unknown-code");
  await load(page, "/equipment");
  await expect(reason(page, "mode_reason").locator("code")).toHaveText("brand_new_code");
  await expect(reason(page, "mode_reason").locator("code")).toHaveCSS("font-family", /Mono|mono/);
});

test("with the controller not reporting every reason reads unknown with its age, never its last words", async ({ page, request }) => {
  await useScreensClock(page, request, ["why-reasons", "controller-not-reporting"]);
  await load(page, "/equipment");
  for (const key of ["mode_reason", "fog_block_reason", "moisture_block_reason"]) {
    await expect(reason(page, key).locator(".why-words")).toHaveText("unknown · last report 1 min ago");
    await expect(reason(page, key).getByRole("link")).toHaveCount(0);
  }
  await expect(page.locator(".why-rows")).not.toContainText(/Venting|ceiling|misting/);
});

for (const viewport of VIEWPORTS) {
  test(`${viewport.width} px: two fast taps on Turn on send one API-20 with one key; Sending…, then the Lutron readback, then the row in Activity`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "lights-confirmed");
    await load(page, "/equipment");
    await expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off");
    // Hold the answer back, so the tap's "Sending…" can be read before the API answers.
    let release = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/v1/equipment/lights/main/on", async (route) => { await held; await route.continue(); });
    await light(page, MAIN).getByRole("button", { name: "Turn on Main" }).dblclick();
    await expect(light(page, MAIN).getByRole("button", { name: "Sending…" })).toBeDisabled();
    await expect(light(page, MAIN).getByRole("button", { name: "Turn off Main" })).toBeDisabled();
    await expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off");
    release();
    await untilPolled(page, () => expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron On ▲ Differ", { timeout: 500 }));
    const sent = await fixtureWrites(request);
    expect(sent.map((write) => [write.method, write.path, write.operationId])).toEqual([["POST", "/v1/equipment/lights/main/on", "API-20"]]);
    expect(sent[0].idempotency_key).toMatch(UUID);
    await light(page, MAIN).getByRole("link", { name: "Activity of Main" }).click();
    await expect(page).toHaveURL(/\/activity\?target=grow_light_main$/);
    const entry = page.getByRole("region", { name: "Activity log", exact: true }).locator("li.event").filter({ has: page.locator("h3", { hasText: "Lights on Main lights" }) });
    await expect(entry).toHaveCount(1);
    await expect(entry.locator(".event-body > p").first()).toContainText("Jason · dashboard");
  });
}

test("Back to automatic: one API-10 write reads REQUESTED then CONFIRMED, and only then one API-20 back to the intent", async ({ page, request }) => {
  await useScreensClock(page, request, ["lights-confirmed", "requested-to-confirmed"]);
  await load(page, "/equipment");
  await light(page, MAIN).getByRole("button", { name: "Turn on Main" }).click();
  await untilPolled(page, () => expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron On ▲ Differ", { timeout: 500 }));
  await expect(automatic(page, MAIN)).toHaveText("Automatic");
  await light(page, MAIN).getByRole("button", { name: "Back to automatic Main" }).click();
  await expect(automatic(page, MAIN).locator("[data-badge=requested]")).toBeVisible();
  expect(await sentWrites(request)).toEqual([TURN_ON, AUTO_MAIN]);
  await untilPolled(page, () => expect(automatic(page, MAIN).locator("[data-badge=confirmed]")).toBeVisible({ timeout: 500 }));
  await expect(automatic(page, MAIN)).toHaveText("Confirmed Automatic");
  await expect.poll(() => sentWrites(request)).toEqual([TURN_ON, AUTO_MAIN, ["POST", "/v1/equipment/lights/main/off", { reason: "Back to automatic" }]]);
  await untilPolled(page, () => expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off", { timeout: 500 }));
  await expect(page.locator("[data-differ]")).toHaveCount(0);
  // Both show in Activity: the command with its reason, under the light's own link, and the write under the switch.
  await light(page, MAIN).getByRole("link", { name: "Activity of Main" }).click();
  const log = page.getByRole("region", { name: "Activity log", exact: true }).locator("li.event");
  await expect(log.filter({ has: page.locator("h3", { hasText: "Lights off Main lights" }) }).locator(".event-reason")).toHaveText("“Back to automatic”");
  await load(page, "/activity?target=sw_gl_main_auto_mode");
  await expect(log.filter({ has: page.locator("h3", { hasText: "Set GL Main Auto Mode" }) }).locator(".event-reason")).toHaveText("“Back to automatic”");
});

test("grow-lights-differ: Back to automatic sends one on request once CONFIRMED", async ({ page, request }) => {
  await useScreensClock(page, request, ["grow-lights-differ", "requested-to-confirmed"]);
  await load(page, "/equipment");
  await light(page, GROW).getByRole("button", { name: "Back to automatic Grow" }).click();
  await untilPolled(page, () => expect(automatic(page, GROW).locator("[data-badge=confirmed]")).toBeVisible({ timeout: 500 }));
  await expect.poll(() => sentWrites(request)).toEqual([AUTO_GROW, ["POST", "/v1/equipment/lights/grow/on", { reason: "Back to automatic" }]]);
});

for (const fixture of ["lights-intent-unknown", "ha-down"]) {
  test(`${fixture}: Back to automatic sends no API-20, because the intent and the Lutron state are not both known`, async ({ page, request }) => {
    await useScreensClock(page, request, [fixture, "requested-to-confirmed"]);
    await load(page, "/equipment");
    await light(page, MAIN).getByRole("button", { name: "Back to automatic Main" }).click();
    await untilPolled(page, () => expect(automatic(page, MAIN).locator("[data-badge=confirmed]")).toBeVisible({ timeout: 500 }));
    // The resync's own read follows the API-09 read that said CONFIRMED, so a command would be on its way by now.
    await expect.poll(async () => {
      const reads = (await fixtureRequests(request)).map((read) => read.operationId);
      return reads.lastIndexOf("API-13") > reads.lastIndexOf("API-09");
    }).toBe(true);
    await page.clock.fastForward(5_000);
    expect(await sentWrites(request)).toEqual([AUTO_MAIN]);
  });
}

for (const [fixture, says] of [["lights-ha-fail", "Lutron did not switch: Home Assistant did not answer"], ["lights-not-confirmed", "Lutron did not reach the state"]]) {
  test(`${fixture}: the result reads in words, the readback stays, and after 30 s it says still OFF`, async ({ page, request }) => {
    await useScreensClock(page, request, fixture);
    await load(page, "/equipment");
    await light(page, MAIN).getByRole("button", { name: "Turn on Main" }).click();
    await expect(light(page, MAIN).locator(".field-error")).toHaveText(says);
    await expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off");
    await untilPolled(page, () => expect(light(page, MAIN).locator("[data-light-note=late]")).toHaveText("Lutron hasn't confirmed: still OFF", { timeout: 500 }));
    await expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off");
    expect(await sentWrites(request)).toEqual([TURN_ON]);
  });
}

test("viewer-session: the board reads the same with no light control", async ({ page, request }) => {
  await useScreensClock(page, request, "viewer-session");
  await load(page, "/equipment");
  await expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off");
  await expect(automatic(page, MAIN)).toHaveText("Automatic");
  for (const key of [MAIN, GROW]) {
    await expect(light(page, key).getByRole("button")).toHaveCount(0);
    await expect(light(page, key).getByRole("link")).toHaveCount(0);
  }
});

test("writes-off: each light control is disabled beside the changes-off banner", async ({ page, request }) => {
  await useScreensClock(page, request, "writes-off");
  await load(page, "/equipment");
  for (const key of [MAIN, GROW]) {
    const buttons = light(page, key).getByRole("button");
    await expect(buttons).toHaveCount(3);
    for (const button of await buttons.all()) await expect(button).toBeDisabled();
  }
  await expect(page.locator("[data-banner]").first()).toBeVisible();
});

test("at 390 px a light's buttons are full width, each target is at least 44 px, and nothing scrolls sideways", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "lights-confirmed");
  await load(page, "/equipment");
  const row = await light(page, MAIN).locator(".light-actions").boundingBox();
  for (const name of ["Turn on Main", "Turn off Main", "Back to automatic Main"]) {
    const box = await light(page, MAIN).getByRole("button", { name }).boundingBox();
    expect(box?.width, name).toBeCloseTo(row?.width ?? 0, 0);
    expect(box?.height, name).toBeGreaterThanOrEqual(44);
  }
  expect((await light(page, MAIN).getByRole("link", { name: "Activity of Main" }).boundingBox())?.height).toBeGreaterThanOrEqual(44);
  await expectNoHorizontalScroll(page);
});

for (const viewport of VIEWPORTS) {
  test(`${viewport.width} px: daily water use keeps no-valve water apart, draws missing days as gaps, and carries the same whole gallons in its table`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "water-use-90d");
    await load(page, "/water/history");
    const panel = page.getByRole("region", { name: "Daily water use", exact: true });
    await expect(panel.locator("figcaption")).toHaveText("Gallons per day by line · last 30 days");
    await expect(panel.locator(".plot-water-use canvas").first()).toBeVisible();
    await expect(panel.getByRole("list", { name: "Water use days" }).getByRole("link", { name: "30 d" })).toHaveAttribute("aria-current", "true");
    // The key says what the canvas draws: the no-valve bar in --fault, hatched no-data days and the partial day's dashed edge.
    const key = panel.locator(".chart-key li");
    const noValve = key.filter({ hasText: "No Verdify valve open: hose, sink, hydro or a leak" }).locator(".chart-key-swatch");
    await expect(noValve).toHaveAttribute("data-token", "fault");
    await expect(noValve).toHaveAttribute("data-kind", "fill");
    await expect(key.filter({ hasText: /^no data$/ }).locator(".chart-key-swatch")).toHaveAttribute("data-kind", "hatch");
    await expect(key.filter({ hasText: /^partial day$/ }).locator(".chart-key-swatch")).toHaveAttribute("data-kind", "dashed");
    await expect(panel.locator(".water-summary")).toContainText(/^Last 30 days: \d+\s+gal · \d+\s+gal with no Verdify valve open · 1 day with no data$/);
    await expect(panel.locator("details.chart-table summary")).toHaveText("Data table · 30 rows");
    await panel.locator("details.chart-table summary").click();
    const rows = panel.locator("details.chart-table tbody tr");
    await expect(rows).toHaveCount(30);
    const cells = (hasText: string) => rows.filter({ has: page.locator("th", { hasText }) }).locator("th, td").allTextContents();
    const flat = async (hasText: string) => (await cells(hasText)).map((cell) => cell.replaceAll(String.fromCharCode(0xa0), " "));
    // textContent: the stylesheet uppercases table headers, and innerText reads them as drawn.
    const headers = await panel.locator("details.chart-table thead th").allTextContents();
    expect(headers.slice(0, 2)).toEqual(["Day", "Total"]);
    expect(headers.slice(-2)).toEqual(["No valve", "Coverage"]);
    // Nine days back has no data: every value is —, never 0.
    const noData = await flat("Oct 24");
    expect(noData.slice(1, -1).every((cell) => cell === "—")).toBe(true);
    expect(noData.at(-1)).toBe("no data");
    // Four days back is a real 0, six days back ran 14 gal with no valve open, and today is partial.
    const zero = await flat("Oct 29");
    expect(zero.slice(1, -1).every((cell) => cell === "0 gal")).toBe(true);
    expect(zero.at(-1)).toBe("complete");
    const hose = await flat("Oct 27");
    expect(hose.at(1)).toBe("20 gal");
    expect(hose.at(-2)).toBe("14 gal");
    expect((await flat("Nov 2")).at(-1)).toBe("partial");
    await expectNoHorizontalScroll(page);
    await axe(page);
  });
}

test("the 90 d and cause chips ask API-21 for 90 days and cut the same days by cause, in the URL; a reload keeps them", async ({ page, request }) => {
  await useScreensClock(page, request, "water-use-90d");
  await load(page, "/water/history");
  const panel = page.getByRole("region", { name: "Daily water use", exact: true });
  await panel.getByRole("list", { name: "Water use days" }).getByRole("link", { name: "90 d" }).click();
  await expect(page).toHaveURL(/\/water\/history\?water_use=90d$/);
  await expect(panel.locator("figcaption")).toHaveText("Gallons per day by line · last 90 days");
  await expect(panel.locator("details.chart-table summary")).toHaveText("Data table · 90 rows");
  await panel.getByRole("list", { name: "Split water use by" }).getByRole("link", { name: "cause" }).click();
  await expect(page).toHaveURL(/\/water\/history\?water_use=90d&water_use_by=cause$/);
  await expect(panel.locator("figcaption")).toHaveText("Gallons per day by cause · last 90 days");
  await panel.locator("details.chart-table summary").click();
  await expect(panel.locator("details.chart-table thead th").filter({ hasText: /^VPD$/ })).toHaveCount(1);
  await expect(panel.locator("details.chart-table tbody tr")).toHaveCount(90);
  await page.reload();
  await expect(panel.locator("figcaption")).toHaveText("Gallons per day by cause · last 90 days");
  const days = (await fixtureRequests(request, "API-21")).map((read) => new URL(read.path, "http://fixture.invalid").searchParams.get("days"));
  expect(new Set(days)).toEqual(new Set(["30", "90"]));
});

test("a failed API-21 read says so in its own panel with Retry; the minutes and the ledger keep working", async ({ page, request }) => {
  await useScreensClock(page, request, "water-use-90d", { fail: { "API-21": 503 } });
  await load(page, "/water/history");
  const panel = page.getByRole("region", { name: "Daily water use", exact: true });
  await expect(panel.locator(".panel-error")).toContainText("Couldn't load daily water use.");
  await expect(panel.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.locator(".minutes-row")).toHaveCount(5);
  await expect(page.getByRole("table", { name: /Runs, newest first/ })).toBeVisible();
});

test("a failed status read leaves the Run dialog open with what was typed in it", async ({ page, request }) => {
  await useScreensClock(page, request, "run-dialogs");
  await load(page, "/water");
  await page.locator('.line-card[data-line="L01"]').getByRole("button", { name: /^Run…/ }).click();
  const dialog = page.locator("dialog[open]");
  await dialog.getByLabel("Reason (optional)").fill("Dry bench");
  await expect(page).toHaveURL(/\/water\?run=L01$/);
  await useScreensClock(page, request, "run-dialogs", { fail: { "API-IRR-01": 503 } });
  await untilPolled(page, () => expect(page.locator(".line-card")).toHaveCount(0, { timeout: 500 }));
  await expect(dialog.getByLabel("Reason (optional)")).toHaveValue("Dry bench");
});
