// SPDX-License-Identifier: MIT
// Equipment depth (#1140) at 1440 and 390 px: the Why panel's words and Graphs windows, its age when the controller is
// not reporting, and a grow light's Turn on, Turn off and Back to automatic with one request per tap and the Lutron
// readback, a 503's words and who gets the controls. Split from equipment-water-depth.spec.ts (redesign §10.2, WP0.1);
// the Water half is water-depth.spec.ts.
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
const AUTO_MAIN = ["PUT", "/v1/settings/sw_gl_main_auto_mode", { value: 1, manual: false, reason: "Back to automatic" }];
const AUTO_GROW = ["PUT", "/v1/settings/sw_gl_grow_auto_mode", { value: 1, manual: false, reason: "Back to automatic" }];

for (const viewport of VIEWPORTS) {
  test(`${viewport.width} px: each reason reads in words with since and the Graphs window around it, under the board and above the lanes`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "why-reasons");
    await load(page, "/equipment");
    await expect(reason(page, "mode_reason")).toContainText("Climate mode");
    await expect(reason(page, "mode_reason").locator(".why-words")).toHaveText("Venting: too warm · since Nov 1, 13:40");
    await expect(reason(page, "fog_block_reason")).toContainText("Fog");
    await expect(reason(page, "fog_block_reason").locator(".why-words")).toHaveText("Humidity at the fog ceiling · since 09:12");
    await expect(reason(page, "moisture_block_reason")).toContainText("Misters (center, south, west)");
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
    // Off in automatic, matching the controller: the one button turns it on (#1308 visual QA 26).
    await expect(light(page, MAIN).locator(".light-actions button")).toHaveText(["Turn on 15 min Main"]);
    await light(page, MAIN).getByRole("button", { name: "Turn on 15 min Main" }).dblclick();
    await expect(light(page, MAIN).getByRole("button", { name: "Sending…" })).toBeDisabled();
    // While it is on its way both switches show, so the other could override it; nothing more is sent meanwhile.
    await expect(light(page, MAIN).getByRole("button", { name: "Turn off 15 min Main" })).toBeDisabled();
    await expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off");
    release();
    await untilPolled(page, () => expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron On Differ", { timeout: 500 }));
    // On by hand against the controller: Back to automatic first, then Turn off.
    await expect(light(page, MAIN).locator(".light-actions button")).toHaveText(["Back to automatic Main", "Turn off 15 min Main"]);
    const sent = await fixtureWrites(request);
    expect(sent.map((write) => [write.method, write.path, write.operationId])).toEqual([["POST", "/v1/equipment/lights/main/on", "API-20"]]);
    expect(sent[0].idempotency_key).toMatch(UUID);
    // The light's Activity link opens the log filtered to it, where the command shows.
    await light(page, MAIN).getByRole("link", { name: "Activity of Main" }).click();
    await expect(page).toHaveURL(/\/activity\/log\?target=grow_light_main$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Activity log");
    await expect(page.getByRole("region", { name: "Activity log", exact: true })).toContainText("Lights on");
  });
}

test("Back to automatic: one API-10 write reads REQUESTED then CONFIRMED, and only then one API-20 back to the intent", async ({ page, request }) => {
  await useScreensClock(page, request, ["lights-confirmed", "requested-to-confirmed"]);
  await load(page, "/equipment");
  await light(page, MAIN).getByRole("button", { name: "Turn on 15 min Main" }).click();
  await untilPolled(page, () => expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron On Differ", { timeout: 500 }));
  await expect(automatic(page, MAIN)).toHaveText("Automatic");
  await light(page, MAIN).getByRole("button", { name: "Back to automatic Main" }).click();
  await expect(automatic(page, MAIN).locator("[data-badge=requested]")).toBeVisible();
  expect(await sentWrites(request)).toEqual([TURN_ON, AUTO_MAIN]);
  await untilPolled(page, () => expect(automatic(page, MAIN).locator("[data-badge=confirmed]")).toBeVisible({ timeout: 500 }));
  await expect(automatic(page, MAIN)).toHaveText("Confirmed Automatic");
  await expect.poll(() => sentWrites(request)).toEqual([TURN_ON, AUTO_MAIN, ["POST", "/v1/equipment/lights/main/off", { reason: "Back to automatic" }]]);
  await untilPolled(page, () => expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off", { timeout: 500 }));
  await expect(page.locator("[data-differ]")).toHaveCount(0);
  // Both show in Activity with their reason: the command under the light's own link, and the write under the switch.
  await light(page, MAIN).getByRole("link", { name: "Activity of Main" }).click();
  await expect(page).toHaveURL(/\/activity\/log\?target=grow_light_main$/);
  const log = page.getByRole("region", { name: "Activity log", exact: true });
  await expect(log).toContainText("Lights off");
  await expect(log).toContainText("“Back to automatic”");
  await load(page, "/activity/log?target=sw_gl_main_auto_mode");
  await expect(log).toContainText("GL Main Auto Mode");
  await expect(log).toContainText("“Back to automatic”");
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
    await light(page, MAIN).getByRole("button", { name: "Turn on 15 min Main" }).click();
    await expect(light(page, MAIN).locator(".field-error")).toHaveText(says);
    await expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off");
    await untilPolled(page, () => expect(light(page, MAIN).locator("[data-light-note=late]")).toHaveText("Lutron hasn't confirmed: still OFF", { timeout: 500 }));
    await expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off");
    expect(await sentWrites(request)).toEqual([TURN_ON]);
  });
}

test("viewer-session: the board reads the same with no light control; the automatic switch's setting opens read-only", async ({ page, request }) => {
  await useScreensClock(page, request, "viewer-session");
  await load(page, "/equipment");
  await expect(lutron(page, MAIN)).toHaveText("Off (controller) · Lutron Off");
  await expect(automatic(page, MAIN)).toHaveText("Automatic");
  for (const key of [MAIN, GROW]) {
    await expect(light(page, key).getByRole("button")).toHaveCount(0);
    await expect(light(page, key).getByRole("link")).toHaveText([/^Automation setting/]);
  }
  await light(page, MAIN).getByRole("link", { name: "Automation setting of Main" }).click();
  await expect(page.locator("dialog[open]").getByRole("heading", { level: 2 })).toHaveText("Setting");
  await expect(page.locator("dialog[open]").getByRole("button", { name: "Save" })).toHaveCount(0);
});

test("writes-off: each light control is disabled beside the changes-off banner", async ({ page, request }) => {
  // Main off in automatic, Grow overridden (its controller says on): each its own buttons by state, every one disabled.
  await useScreensClock(page, request, ["writes-off", "grow-lights-differ"]);
  await load(page, "/equipment");
  for (const [key, names] of [[MAIN, ["Turn on 15 min Main"]], [GROW, ["Back to automatic Grow", "Turn on 15 min Grow"]]] as const) {
    const buttons = light(page, key).getByRole("button");
    await expect(buttons).toHaveText([...names]);
    for (const button of await buttons.all()) await expect(button).toBeDisabled();
  }
  await expect(page.locator("[data-banner]").first()).toBeVisible();
});

test("at 390 px a light's buttons are full width, each target is at least 44 px, and nothing scrolls sideways", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, ["lights-confirmed", "grow-lights-differ"]);
  await load(page, "/equipment");
  for (const [key, names] of [[MAIN, ["Turn on 15 min Main"]], [GROW, ["Back to automatic Grow", "Turn on 15 min Grow"]]] as const) {
    const row = await light(page, key).locator(".light-actions").boundingBox();
    for (const name of names) {
      const box = await light(page, key).getByRole("button", { name }).boundingBox();
      expect(box?.width, name).toBeCloseTo(row?.width ?? 0, 0);
      expect(box?.height, name).toBeGreaterThanOrEqual(44);
    }
  }
  expect((await light(page, MAIN).getByRole("link", { name: "Activity of Main" }).boundingBox())?.height).toBeGreaterThanOrEqual(44);
  await expectNoHorizontalScroll(page);
});
