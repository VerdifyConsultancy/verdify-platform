// SPDX-License-Identifier: MIT
// Now (redesign §5.1, SCR-01) at the screens' clock: the answer, the temperature, "Updated 20 s ago" and the three band
// bars above a phone's fold and Stop all water above it at 390 × 844; the band bars with today's range and a verdict only
// out of band; time in band today; the honest states; the quick actions (Stop with and without the irrigation firmware, a
// burst); Needs someone and Acknowledge; "Running and next" as a track from 861 px and two lists below; Later; the zone
// strip; and each fact once. That a viewer sees no write control is roles.spec.ts.
import type { Page } from "@playwright/test";
import {
  CAN_STILL_START, CAN_STILL_START_NO_ENGINE, DESKTOP, PHONE, SMALL_PHONE, STOPPED, expect, expectAboveTheFold, fixtureRequests, fixtureWrites, load, test, toastLines,
  untilPolled, useFixture, useScreensClock,
} from "./support";

const answer = (page: Page) => page.getByRole("heading", { level: 1 });
const updated = (page: Page) => page.locator("#data");
const band = (page: Page, tile: string) => page.locator(`.now-band[data-tile=${tile}]`);
const stopAll = (page: Page) => page.getByRole("button", { name: "Stop all water" });
const runningRows = (page: Page) => page.locator(".now-running [data-chip]");
const NOBODY_SEEN = "Nobody seen: misters may run (last person 05:10)";
const VERDICTS = /in band|above band|below band|band unavailable/;

test("in band at 360 × 740: All OK, 64.8 °F, Updated 20 s ago and the three band bars sit above the fold", async ({ page, request }) => {
  await page.setViewportSize(SMALL_PHONE);
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  await expect(answer(page)).toHaveText("All OK");
  await expect(page.locator(".hero-temp")).toHaveText("64.8 °F");
  await expect(updated(page)).toHaveText("Updated 20 s ago");
  for (const target of [answer(page), page.locator(".hero-temp"), updated(page), band(page, "temp"), band(page, "vpd"), band(page, "rh")]) await expectAboveTheFold(page, target);
});

for (const fixture of ["in-band", "l01-running"]) {
  test(`${fixture} at 390 × 844: Stop all water, the first quick action, sits above the fold at full width`, async ({ page, request }) => {
    await page.setViewportSize(PHONE);
    await useScreensClock(page, request, fixture);
    await load(page, "/");
    await expect(stopAll(page)).toBeVisible();
    await expectAboveTheFold(page, stopAll(page));
    const [button, actions] = await Promise.all([stopAll(page).boundingBox(), page.locator(".now-actions").boundingBox()]);
    expect(button?.width).toBeCloseTo(actions?.width ?? 0, 0);
    expect(button?.height).toBeGreaterThanOrEqual(44);
  });
}

test("1440 × 900: the hero and the quick actions sit whole in the first screen, and Needs someone and Water today start in it", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  for (const [selector, whole] of [[".now-hero", true], [".now-actions", true], ["#needs-someone", false], [".now-water", false]] as const) {
    const box = await page.locator(selector).boundingBox();
    expect(box, selector).not.toBeNull();
    expect(whole ? box!.y + box!.height : box!.y + 80, selector).toBeLessThanOrEqual(DESKTOP.height);
  }
});

test("each band bar draws one track, the band box and the value, with today's low–high in its name only; RH has no band; in band adds no verdict, out of band does", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  for (const tile of ["temp", "vpd"]) {
    const bar = band(page, tile).locator("svg.bb");
    await expect(bar.locator(".bb-band")).toHaveCount(1);
    await expect(bar.locator(".bb-dot")).toHaveCount(1);
    await expect(bar.locator(".bb-track")).toHaveCount(1);
    await expect(bar.locator(".bb-today")).toHaveCount(0);
    await expect(bar).toHaveAttribute("aria-label", /, today /);
  }
  await expect(band(page, "rh").locator(".bb-band")).toHaveCount(0);
  await expect(band(page, "rh")).toContainText("no band");
  await expect(page.locator(".now-hero .now-off")).toHaveCount(0);
  await expect(band(page, "temp").getByRole("link")).toHaveAttribute("href", "/graphs?range=24h#temperature");
  await useScreensClock(page, request, "too-hot");
  await load(page, "/");
  await expect(answer(page)).toHaveText("Out of band");
  await expect(page.locator(".now-condition [data-condition=temp]")).toHaveText("Too hot: 84.1 °F (band 62–70 °F)");
  await expect(band(page, "temp").locator(".now-off")).toHaveText("above band");
  await expect(band(page, "vpd").locator(".now-off")).toHaveCount(0);
});

test("time in band today against yesterday, from the daily keys", async ({ page, request }) => {
  await useScreensClock(page, request, ["in-band", "time-in-band"]);
  await load(page, "/");
  await expect(page.locator(".now-tib")).toHaveText("In band today temperature 96% · VPD 88% (yesterday 94% · 91%)");
});

test("the status line: the mode links to Equipment's why, Iris to Plan, outdoor to Graphs; occupancy reads as it is; a missed SUNSET turns Iris amber", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  await expect(page.locator(".now-status [data-tile=mode]")).toHaveAttribute("href", "/equipment#why");
  await expect(page.locator(".now-status [data-tile=iris]")).toHaveAttribute("href", "/plan");
  await expect(page.locator(".now-status [data-tile=outdoor]")).toHaveAttribute("href", "/graphs?range=24h#temperature");
  await expect(page.locator("[data-chip=occupancy]")).toHaveText(NOBODY_SEEN);
  await useScreensClock(page, request, "missed-sunset");
  await load(page, "/");
  await expect(page.locator(".now-status [data-tile=iris]")).toHaveAttribute("data-tone", "warn");
});

test("Updated N s ago opens All readings in place, with every source's time and the band in force, DERIVED with its age", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  const readings = page.locator("#readings");
  await expect(readings).not.toHaveAttribute("open", "");
  await updated(page).click();
  await expect(readings).toHaveAttribute("open", "");
  await expect(readings.locator(".kv")).toContainText("band in force 62.0–70.0 °F · DERIVED · 4 min");
  await expect(readings.locator(".kv")).toContainText("IDLE");
});

for (const [fixture, says, missing] of [
  ["ingestor-not-running", "Ingestor not running since 10:38", "In-app alerts may be missing: ingestor not running since 10:38"],
  ["controller-not-reporting", "Controller not reporting since 10:36", "In-app alerts may be missing: no data since 10:36"],
] as const) {
  test(`${fixture}: the answer names it, no verdict, the mode unknown, and alerts may be missing`, async ({ page, request }) => {
    await useScreensClock(page, request, fixture);
    await load(page, "/");
    await expect(answer(page)).toHaveText(says);
    await expect(page.locator("main")).not.toContainText(VERDICTS);
    await expect(page.locator(".now-status [data-tile=mode]")).toHaveText("Mode unknown");
    await expect(page.locator("#needs-someone .panel-empty p")).toHaveText(missing);
    await expect(page.locator(".now-next-now")).toContainText("Equipment state unknown since");
  });
}

test("stale data: Waiting for fresh data, no verdict word anywhere, the mode as last said and dated, the band bars dated and drawn neutral", async ({ page, request }) => {
  await useScreensClock(page, request, "stale-data");
  await load(page, "/");
  await expect(answer(page)).toHaveText("Waiting for fresh data");
  await expect(updated(page)).toHaveText("Last data 14 min ago (10:28)");
  await expect(page.locator(".now-status [data-tile=mode]")).toHaveText("Last mode: holding (10:28)");
  for (const part of [".now-status", ".now-bands"]) await expect(page.locator(part)).not.toContainText(/in band|above band|below band/i);
  await expect(page.locator(".now-hero .now-hero-verdict, .now-hero .now-off")).toHaveCount(0);
  await expect(page.locator(".now-hero-climate[data-stale] .now-bands-asof")).toHaveText("As of 10:28");
  await expect(page.locator(".now-band svg.bb.is-stale")).toHaveCount(3);
  // The band reads neutral (--ink-2), as with the API down: a muted dot inside a green band would still say in band.
  await expect(band(page, "temp").locator(".bb-band")).toHaveCSS("fill", "rgb(27, 45, 61)");
});

test("with the API down, after the second failed poll: no band bars, no In band today, nothing green", async ({ page, request }) => {
  await useFixture(request, "api-down");
  await page.clock.install();
  await load(page, "/");
  await untilPolled(page, () => expect(page.locator("[data-banner=fault]")).toHaveText("Dashboard API unavailable", { timeout: 1_000 }), 31_000);
  await expect(page.locator(".now-bands")).toHaveCount(0);
  await expect(page.locator(".now-hero-climate, .now-tib")).toHaveCount(0);
  await expect(page.locator(".tag-ok, .status-complete, .fresh-ok")).toHaveCount(0);
});

test("a null RH reads — with its reason, never 0, and its bar is hatched", async ({ page, request }) => {
  await useScreensClock(page, request, "rh-missing");
  await load(page, "/");
  await expect(band(page, "rh").locator(".now-band-value")).toHaveText("—");
  await expect(band(page, "rh").locator(".now-band-why")).toHaveText("No indoor RH");
  await expect(band(page, "rh").locator("svg.bb.is-unknown")).toHaveCount(1);
});

test("Stop all water with the irrigation firmware: one API-IRR-07 and no dialog, the wet row goes, and the toast offers Disable", async ({ page, request }) => {
  await useScreensClock(page, request, "l01-running");
  await load(page, "/");
  await expect(runningRows(page)).toHaveText(["Wall drip · run now · since 10:30 · 3 min left"]);
  await stopAll(page).click();
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  await untilPolled(page, () => expect(runningRows(page)).toHaveCount(0, { timeout: 500 }));
  await expect(page.locator("[data-running=none]")).toHaveText("Nothing running");
  await expect(toastLines(page)).toHaveText([STOPPED, CAN_STILL_START]);
  await expect(page.getByRole("button", { name: "Disable L01" })).toBeVisible();
  expect((await fixtureWrites(request)).map((row) => row.operationId)).toEqual(["API-IRR-07"]);
});

test("Stop all water before the irrigation firmware: the same one request, the toast names what can still start, and the bursts say when they arrive", async ({ page, request }) => {
  await useScreensClock(page, request, ["l01-running", "before-engine"]);
  await load(page, "/");
  await expect(page.locator("[data-arrives=bursts]")).toHaveText("Bursts arrive with the irrigation firmware");
  await expect(page.getByRole("button", { name: "Fans 15 min" })).toHaveCount(0);
  await stopAll(page).click();
  await untilPolled(page, () => expect(runningRows(page)).toHaveCount(0, { timeout: 500 }));
  await expect(toastLines(page)).toHaveText([STOPPED, CAN_STILL_START_NO_ENGINE]);
  await expect(page.getByRole("button", { name: /^Disable/ })).toHaveCount(0);
  expect((await fixtureWrites(request)).map((row) => row.operationId)).toEqual(["API-IRR-07"]);
});

test("Fans 15 min from the quick actions: one API-IRR-12 with no dialog, and its row in Running now once the controller reads it back", async ({ page, request }) => {
  await useScreensClock(page, request, "burst-readback-late");
  await load(page, "/");
  await page.getByRole("button", { name: "Fans 15 min" }).click();
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  // A regex reads the no-break space before the unit as it is.
  await untilPolled(page, () => expect(page.locator(".now-running [data-chip=burst]")).toHaveText(/^Fans burst · \d+\smin left$/, { timeout: 500 }));
  await expect(page.getByRole("button", { name: "Clear bursts" })).toBeVisible();
  expect((await fixtureWrites(request)).map((write) => [write.operationId, write.body])).toEqual([["API-IRR-12", { kind: "fans", minutes: 15 }]]);
});

test("with changes switched off Stop stays live while the bursts and Acknowledge are disabled", async ({ page, request }) => {
  await useScreensClock(page, request, ["alerts-merged", "writes-off"]);
  await load(page, "/");
  await expect(stopAll(page)).toBeEnabled();
  await expect(page.getByRole("button", { name: "Fans 15 min" })).toBeDisabled();
  const acknowledge = page.locator("#needs-someone").getByRole("button", { name: /^Acknowledge/ });
  await expect(acknowledge).not.toHaveCount(0);
  for (const button of await acknowledge.all()) await expect(button).toBeDisabled();
});

for (const [fixture, chip] of [["occupancy-unknown", "Occupancy unknown: misters may run"], ["occupancy-hold-off", "Occupancy mist hold is off: misters may run"]]) {
  test(`${fixture}: occupancy always shows, reading "${chip}"`, async ({ page, request }) => {
    await useScreensClock(page, request, fixture);
    await load(page, "/");
    await expect(page.locator("[data-chip=occupancy]")).toHaveText(chip);
  });
}

test("each fact once: the count in the answer or in Needs someone's head, never both; one Stop all water", async ({ page, request }) => {
  await useScreensClock(page, request, "alerts-merged");
  await load(page, "/");
  await expect(answer(page)).toHaveText("2 things need someone");
  await expect(page.locator("#needs-someone h2")).toHaveText("Needs someone");
  await expect(stopAll(page)).toHaveCount(1);
  await useScreensClock(page, request, ["alerts-merged", "too-hot"]);
  await load(page, "/");
  await expect(page.locator("#needs-someone h2")).toHaveText("Needs someone · 2");
});

test("Needs someone with nothing open: what was resolved in 24 h, linked into Activity's resolved group only when there is some", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  const empty = page.locator("#needs-someone .panel-empty");
  await expect(empty.locator("p").first()).toHaveText("No open alerts · nothing resolved in 24 h");
  await expect(empty.getByRole("link")).toHaveCount(0);
  await useScreensClock(page, request, "alert-resolved");
  await load(page, "/");
  await expect(empty.locator("p").first()).toHaveText("No open alerts");
  await empty.getByRole("link", { name: "1 resolved in 24 h" }).click();
  await expect(page).toHaveURL(/\/activity\?resolved=24h$/);
  await expect(page.locator("details.alert-group", { hasText: "Resolved in the last 24 h (1)" })).toHaveAttribute("open", "");
});

test("Acknowledge sends one API-15 and the answer follows the one count", async ({ page, request }) => {
  await useScreensClock(page, request, "alerts-merged");
  await load(page, "/");
  const needsSomeone = page.locator("#needs-someone");
  await needsSomeone.getByRole("button", { name: /^Acknowledge/ }).first().click();
  await expect(answer(page)).toHaveText("1 thing needs someone");
  await expect(needsSomeone).toContainText("Acknowledged by Jason · 10:42");
  expect((await fixtureRequests(request, "API-15")).map((row) => row.path)).toEqual(["/v1/alerts/101/ack"]);
});

test("Water today: each zone's minutes against its cap and when it last got water, then SAF-4 in gallons", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  const water = page.locator(".now-water");
  await expect(water.locator("[data-water=center] .now-water-amount")).toHaveText("14 / 120 min");
  await expect(water.locator("[data-water=center] .now-water-last")).toHaveText("misted 06:58 (VPD)");
  await expect(water.locator("[data-water=wall] .now-water-amount")).toHaveText("0 / 60 min");
  await expect(water.locator("[data-water=saf4] .now-water-amount")).toHaveText("21 / 360 gal");
  await expect(water.getByRole("link", { name: /^Water/ })).toHaveAttribute("href", "/water");
});

test("Running and next from 861 px: the track over the last 6 h and the next 12 h, with its rows, markers, night and the now rule", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  const coming = page.locator("#coming-up");
  await coming.scrollIntoViewIfNeeded();
  const track = coming.getByRole("group", { name: /^Running and next/ });
  await expect(track.locator(".tt-label")).toHaveText(["Heat", "Vent, fans", "Fog, mist", "Drips", "Iris", "Plan", "Sun"]);
  await expect(track.locator(".tt-row").nth(0).locator(".tt-span").first()).toBeVisible();
  await expect(track.locator(".tt-now").first()).toBeVisible();
  // Iris's triggers read as plan events with their times, so "SUNSET plan 16:50" never reads as a second sunset.
  await expect(track.locator(".tt-row").nth(4).locator(".tt-marker-label")).toHaveText(["SUNRISE plan 07:00 ✓", "SOLAR_MAX plan 12:41", "SUNSET plan 16:50"]);
  await expect(track.locator(".tt-row").nth(6).locator(".tt-marker-label")).toHaveText(["Sunrise 06:35", "Sunset 16:58"]);
  // The now rule is named at the top of the track, and the plan's labels stop at the next marker, so its row keeps the others' height.
  await expect(track.locator(".tt-now-label")).toHaveText("now 10:42");
  const [iris, plan] = await Promise.all([track.locator(".tt-row").nth(4).boundingBox(), track.locator(".tt-row").nth(5).boundingBox()]);
  expect(Math.round(plan!.height)).toBe(Math.round(iris!.height));
  // The phone's lists are there for a narrow screen, hidden here.
  await expect(coming.locator(".now-next")).toBeHidden();
  await expect(coming.locator("[data-coming=later]")).toHaveText("Later No schedule: L01, L07, L09");
  expect((await fixtureRequests(request, "API-05")).length).toBeGreaterThan(0);
});

test("Running and next on a phone: Running now and the Next 12 h list, with each plan change, trigger, run and the sun; no track", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, ["plan-next", "l01-paused"]);
  await load(page, "/");
  const coming = page.locator("#coming-up");
  await expect(coming.locator(".now-track")).toBeHidden();
  await expect(coming.locator("[data-coming=plan]")).toHaveText(["13:00VPD Mister Engage 1.60 → 1.70 kPa AI", "13:00Mister Engage Delay 60.0 → 90.0 s · Forecast rule · system", "16:30Mister Pulse Gap 45.0 → 55.0 s AI"]);
  await expect(coming.locator("[data-coming=iris]")).toHaveText(["16:50Iris SUNSET trigger"]);
  await expect(coming.locator("[data-coming=sun]")).toHaveText(["16:58Sunset"]);
  await expect(coming.locator("[data-coming=later]")).toContainText("L01 Wall drip paused until 14:00 by Jason: rain · next Wed 07:35");
  expect((await fixtureRequests(request, "API-07")).map((row) => row.path)).toEqual(["/v1/plan?hours=24"]);
  expect((await fixtureRequests(request, "API-IRR-02")).map((row) => row.path)).toEqual(["/v1/irrigation/schedules?days=7"]);
  // A phone never reads the track's six hours of equipment.
  expect(await fixtureRequests(request, "API-05")).toEqual([]);
});

test("the order: Needs someone and Water today, then Running and next, then the zones; the quick actions above them", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  const top = async (selector: string) => (await page.locator(selector).boundingBox())?.y ?? Number.NaN;
  const [actions, alerts, water, coming, zones] = [await top(".now-actions"), await top("#needs-someone"), await top(".now-water"), await top("#coming-up"), await top("#zones")];
  expect(actions).toBeLessThan(alerts);
  expect(alerts).toBeLessThan(water);
  expect(water).toBeLessThan(coming);
  expect(coming).toBeLessThan(zones);
});

const zoneTile = (page: Page, zone: string) => page.locator(`#zones [data-zone=${zone}]`);

test("the zone strip: five tiles with a band bar and a 24 h sparkline named VPD, the tag as text only off a zone's own probe; north says house band; a tile opens its zone's charts", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  await expect(page.locator("#zones [data-zone]")).toHaveCount(5);
  // Graphs → Zones' order, so a zone keeps its place from page to page.
  expect(await page.locator("#zones [data-zone]").evaluateAll((tiles) => tiles.map((tile) => tile.getAttribute("data-zone")))).toEqual(["north", "center", "south", "east", "west"]);
  // A zone on its own probe (MEASURED) is the quiet normal and shows no mark; center always reads the house.
  await expect(page.locator("#zones [data-zone-tag]")).toHaveText(["no probe · house average"]);
  await expect(zoneTile(page, "center").locator("[data-zone-tag]")).toHaveAttribute("data-zone-tag", "HOUSE");
  for (const zone of ["north", "center", "south", "east", "west"]) {
    await expect(zoneTile(page, zone).locator(".now-zone-viz-label")).toHaveText("VPD");
    await expect(zoneTile(page, zone).locator(".now-zone-viz svg.bb")).toHaveCount(1);
    await expect(zoneTile(page, zone).locator(".now-zone-viz svg.spark")).toHaveAttribute("aria-label", /VPD over the last 24 h$/);
  }
  await expect(zoneTile(page, "north").locator(".now-zone-meta")).toHaveText("house band 0.45–0.80 kPa");
  await expect(zoneTile(page, "south").locator(".now-zone-meta")).toHaveText("band 0.45–0.80 kPa");
  await zoneTile(page, "south").getByRole("link").click();
  await expect(page).toHaveURL(/\/graphs\/zones\?zone=south$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("South zone");
});

test("south's tile says its probe is offline and shows the West + East average, amber, with the peers' value and no STALE", async ({ page, request }) => {
  await useScreensClock(page, request, "south-probe-out");
  await load(page, "/");
  await expect(zoneTile(page, "south").locator("[data-zone-tag]")).toHaveText("probe offline · West + East average");
  await expect(zoneTile(page, "south").locator("[data-zone-tag]")).toHaveAttribute("data-zone-tag", "PEER");
  await expect(zoneTile(page, "south").locator(".now-zone-tag-news")).toHaveCount(1);
  await expect(zoneTile(page, "south").locator("[data-tile=vpd]")).toHaveText(/^\d\.\d\d\skPa$/);
  await expect(zoneTile(page, "south").locator("[data-badge=stale]")).toHaveCount(0);
});
