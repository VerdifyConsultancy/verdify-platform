// SPDX-License-Identifier: MIT
// SCR-01 Now (#1129) at the screens' clock: the answer and Stop all water above a phone's fold, the honest states, Stop
// with and without the irrigation firmware, the occupancy chip, the Iris tile and Acknowledge; Coming up and the zone tiles
// (#1138). That a viewer sees no Stop or Acknowledge is roles.spec.ts.
import type { Page } from "@playwright/test";
import { CAN_STILL_START, CAN_STILL_START_NO_ENGINE, PHONE, SMALL_PHONE, STOPPED, expect, expectAboveTheFold, fixtureRequests, fixtureWrites, load, test, toastLines, untilPolled, useScreensClock } from "./support";

const answer = (page: Page) => page.getByRole("heading", { level: 1 });
const heroLines = (page: Page) => page.locator(".hero > div > .hero-meta");
const stopAll = (page: Page) => page.getByRole("button", { name: "Stop all water" });
const NOBODY_SEEN = "Nobody seen: misters may run (last person 05:10)";
const VERDICTS = /in band|above band|below band|band unavailable/;

test("in band at 360 × 740: All OK, 64.8 °F and Updated 20 s ago sit above the fold", async ({ page, request }) => {
  await page.setViewportSize(SMALL_PHONE);
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  await expect(answer(page)).toHaveText("All OK");
  await expect(page.locator(".hero-temp")).toHaveText("64.8 °F");
  const updated = heroLines(page).filter({ hasText: "Updated 20 s ago" });
  await expect(updated).toBeVisible();
  await expectAboveTheFold(page, answer(page));
  await expectAboveTheFold(page, page.locator(".hero-temp"));
  await expectAboveTheFold(page, updated);
});

for (const fixture of ["in-band", "l01-running"]) {
  test(`${fixture} at 390 × 844: the water card with Stop all water sits above the fold`, async ({ page, request }) => {
    await page.setViewportSize(PHONE);
    await useScreensClock(page, request, fixture);
    await load(page, "/");
    await expect(page.locator(".now-water")).toBeVisible();
    await expect(stopAll(page)).toBeVisible();
    await expectAboveTheFold(page, stopAll(page));
  });
}

test("the band line reads DERIVED with its age, and with no observed_at it states no age", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  await expect(heroLines(page).filter({ hasText: "band in force" })).toHaveText("band in force 62.0–70.0 °F · DERIVED · 4 min");
  await useScreensClock(page, request, "band-age-unknown");
  await load(page, "/");
  await expect(heroLines(page).filter({ hasText: "band in force" })).toHaveText("band in force 62.0–70.0 °F · DERIVED");
});

for (const [fixture, says, missing] of [
  ["ingestor-not-running", "Ingestor not running since 10:38", "In-app alerts may be missing: ingestor not running since 10:38"],
  ["controller-not-reporting", "Controller not reporting since 10:36", "In-app alerts may be missing: no data since 10:36"],
] as const) {
  test(`${fixture}: the answer names it, no verdict, and alerts may be missing`, async ({ page, request }) => {
    await useScreensClock(page, request, fixture);
    await load(page, "/");
    await expect(answer(page)).toHaveText(says);
    await expect(page.locator("main")).not.toContainText(VERDICTS);
    await expect(page.locator("#needs-someone .panel-empty")).toHaveText(missing);
  });
}

test("a null RH reads — with its reason, never 0", async ({ page, request }) => {
  await useScreensClock(page, request, "rh-missing");
  await load(page, "/");
  await expect(page.locator(".hero-beside")).toContainText("RH — (No indoor RH)");
});

test("Stop all water with the irrigation firmware: one API-IRR-07 and no dialog, the wet chip goes off, and the toast offers Disable", async ({ page, request }) => {
  await useScreensClock(page, request, "l01-running");
  await load(page, "/");
  const chips = page.locator(".now-running [data-chip]");
  await expect(chips).toHaveText(["Wall drip · run now · since 10:30 · 3 min left", NOBODY_SEEN]);
  await stopAll(page).click();
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  await untilPolled(page, () => expect(chips).toHaveText([NOBODY_SEEN], { timeout: 500 }));
  await expect(toastLines(page)).toHaveText([STOPPED, CAN_STILL_START]);
  await expect(page.getByRole("button", { name: "Disable L01" })).toBeVisible();
  expect((await fixtureWrites(request)).map((row) => row.operationId)).toEqual(["API-IRR-07"]);
});

test("Stop all water before the irrigation firmware: the same one request, and the toast names what can still start", async ({ page, request }) => {
  await useScreensClock(page, request, ["l01-running", "before-engine"]);
  await load(page, "/");
  await stopAll(page).click();
  const chips = page.locator(".now-running [data-chip]");
  await untilPolled(page, () => expect(chips).toHaveText([NOBODY_SEEN], { timeout: 500 }));
  await expect(toastLines(page)).toHaveText([STOPPED, CAN_STILL_START_NO_ENGINE]);
  await expect(page.getByRole("button", { name: /^Disable/ })).toHaveCount(0);
  expect((await fixtureWrites(request)).map((row) => row.operationId)).toEqual(["API-IRR-07"]);
});

test("with changes switched off Stop stays live and Acknowledge is disabled", async ({ page, request }) => {
  await useScreensClock(page, request, ["alerts-merged", "writes-off"]);
  await load(page, "/");
  await expect(stopAll(page)).toBeEnabled();
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

test("a missed SUNSET turns the Iris tile amber", async ({ page, request }) => {
  await useScreensClock(page, request, "missed-sunset");
  await load(page, "/");
  await expect(page.locator('.hero > dl.stats > [data-tile="iris"]')).toHaveClass(/stat-warn/);
});

test("Acknowledge sends one API-15 and the answer follows the one count", async ({ page, request }) => {
  await useScreensClock(page, request, "alerts-merged");
  await load(page, "/");
  await expect(answer(page)).toHaveText("2 things need someone");
  const needsSomeone = page.locator("#needs-someone");
  await needsSomeone.getByRole("button", { name: /^Acknowledge/ }).first().click();
  await expect(answer(page)).toHaveText("1 thing needs someone");
  await expect(needsSomeone).toContainText("Acknowledged by jason · 10:42");
  expect((await fixtureRequests(request, "API-15")).map((row) => row.path)).toEqual(["/v1/alerts/101/ack"]);
});

test("Coming up: the next transition without manual settings, the next trigger, each line's next run with who paused it and why, and the sun; a forecast rule never reads AI", async ({ page, request }) => {
  await useScreensClock(page, request, ["plan-next", "l01-paused"]);
  await load(page, "/");
  const coming = page.locator("#coming-up");
  await expect(coming.locator("[data-coming=plan] li")).toHaveText(["13:00 · VPD Mister Engage 1.60 → 1.70 kPa · AI", "13:00 · Mister Engage Delay 60.0 → 90.0 s · Forecast rule · system"]);
  await expect(coming.locator("[data-coming=iris] dd")).toHaveText("SUNSET at 16:50");
  await expect(coming.locator("[data-coming=runs] li").filter({ hasText: "L01" })).toHaveText("L01 Paused until 14:00 by Jason: rain · next Wed 07:35");
  await expect(coming.locator("[data-coming=sun] dd")).toHaveText("Sunrise 06:35 · Sunset 16:58");
  expect((await fixtureRequests(request, "API-07")).map((row) => row.path)).toEqual(["/v1/plan?hours=24"]);
  expect((await fixtureRequests(request, "API-IRR-02")).map((row) => row.path)).toEqual(["/v1/irrigation/schedules?days=7"]);
});

test("Coming up and the zones sit below Needs someone, so the hero and the water card keep their place", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  const top = async (selector: string) => (await page.locator(selector).boundingBox())?.y ?? Number.NaN;
  const [alerts, coming, zones] = [await top("#needs-someone"), await top("#coming-up"), await top("#zones")];
  expect(alerts).toBeLessThan(coming);
  expect(coming).toBeLessThan(zones);
  await expectAboveTheFold(page, stopAll(page));
});

const zoneTile = (page: Page, zone: string) => page.locator(`#zones [data-zone=${zone}]`);

test("the zone tiles: five with live values and tags; north says house band, center reads HOUSE, and a tile opens its zone's charts", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/");
  await expect(page.locator("#zones [data-zone]")).toHaveCount(5);
  await expect(page.locator("#zones [data-zone-tag]")).toHaveText(["MEASURED", "MEASURED", "MEASURED", "MEASURED", "HOUSE"]);
  await expect(zoneTile(page, "north").locator(".zone-band").first()).toHaveText("house band 62–70 °F · 0.45–0.80 kPa");
  await expect(zoneTile(page, "north").locator(".zone-band").nth(1)).toHaveText(/^house target \d\.\d\d\skPa$/);
  await expect(zoneTile(page, "south").locator(".zone-band").first()).toHaveText("band 62–70 °F · 0.45–0.80 kPa");
  await zoneTile(page, "south").getByRole("link").click();
  await expect(page).toHaveURL(/\/graphs\/zones\?zone=south$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("South zone");
});

test("south's tile reads PEER while its probe is out, with the peers' value and no STALE", async ({ page, request }) => {
  await useScreensClock(page, request, "south-probe-out");
  await load(page, "/");
  await expect(zoneTile(page, "south").locator("[data-zone-tag]")).toHaveText("PEER");
  await expect(zoneTile(page, "south").locator("[data-tile=vpd] dd")).toContainText(/^\d\.\d\d\skPa/);
  await expect(zoneTile(page, "south").locator("[data-badge=stale]")).toHaveCount(0);
});
