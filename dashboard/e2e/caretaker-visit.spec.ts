// SPDX-License-Identifier: MIT
// J9, the caretaker's visit, at 360 × 740 against the fixture API (#1136, product §J9): an operator walks Now, Equipment
// and Water, runs and stops the wall drip, then acknowledges the flow alert with a note; a viewer walks the same pages with
// no control and the spigot guidance. Every write is counted in the browser with page.on('request'), across the fixture
// switch to flowing water, which clears the fixture API's own log.
import type { Page } from "@playwright/test";
import { CAN_STILL_START, SMALL_PHONE, STOPPED, WEBKIT, bottomBar, expect, expectNoHorizontalScroll, load, test, toastLines, untilPolled, useScreensClock, writeControls } from "./support";

const VISIT = "caretaker-visit";
const OCCUPIED = "Someone inside: misters wait (Frigate, 1 min ago)";
const LAST_WATER = "Wall today 07:31, 12 min · Center misted 09:58 (VPD)";
const FLOWING = "Water flowing with every Verdify valve closed";
const SPIGOT = "If no one is using water, close the spigot.";

type Write = { method: string; path: string; key: string | undefined; body: unknown };
/** Every write the page sends to the API from here on. */
function countWrites(page: Page): Write[] {
  const writes: Write[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() === "GET" || !url.pathname.startsWith("/api/")) return;
    writes.push({ method: request.method(), path: url.pathname, key: request.headers()["idempotency-key"], body: request.postDataJSON() });
  });
  return writes;
}
const tab = (page: Page, name: RegExp) => bottomBar(page).getByRole("link", { name });
const lineCard = (page: Page, id: string) => page.locator(`.line-card[data-line="${id}"]`);

async function expectNoControl(page: Page) {
  await expect(writeControls(page, { includeHidden: true })).toHaveCount(0);
  await expect(page.locator("[data-write-control]")).toHaveCount(0);
}
/** Now's first screen, the same for both roles. */
async function expectMorning(page: Page) {
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("All OK");
  await expect(page.locator(".hero > div > .hero-meta").filter({ hasText: "Updated 20 s ago" })).toBeVisible();
  await expect(page.locator("[data-water=last]")).toContainText(LAST_WATER);
  await expect(page.locator("[data-chip=occupancy]")).toHaveText(OCCUPIED);
  await expectNoHorizontalScroll(page);
}
/** Equipment is a Control view (redesign §2.1): the Control tab opens Settings, and the Equipment tab is beside it. */
const toEquipment = async (page: Page) => {
  await tab(page, /^Control/).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Settings");
  await page.getByRole("navigation", { name: "Control views" }).getByRole("link", { name: "Equipment" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Equipment");
};

test("an operator's visit at 360 × 740: a burst, a run and its stop, then a note on the flow alert, each one request", WEBKIT, async ({ page, request }) => {
  // The whole visit on the CI pod.
  test.slow();
  await page.setViewportSize(SMALL_PHONE);
  const writes = countWrites(page);
  await useScreensClock(page, request, VISIT);
  await load(page, "/");
  await expectMorning(page);

  await toEquipment(page);
  await page.getByRole("button", { name: "Fans 15 min" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ method: "POST", path: "/api/v1/equipment/override", body: { kind: "fans", minutes: 15 } });
  await expect(page.locator("dialog[open]")).toHaveCount(0);

  await tab(page, /^Now/).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("All OK");
  const burstChip = page.locator(".now-running [data-chip=burst]");
  // A regex reads the no-break space before the unit as it is.
  await untilPolled(page, () => expect(burstChip).toHaveText(/^Fans burst · \d+\smin left$/, { timeout: 500 }));
  await expect(page.getByRole("button", { name: "Clear bursts" })).toBeVisible();

  await tab(page, /^Water/).click();
  const wall = lineCard(page, "L01");
  await wall.getByRole("button", { name: /^Run…/ }).click();
  const dialog = page.locator("dialog[open]");
  await expect(dialog).toContainText("Run L01 Wall drip (clean) for 10 min?");
  await dialog.getByRole("button", { name: "Run 10 min" }).click();
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1]).toMatchObject({ method: "POST", path: "/api/v1/irrigation/lines/L01/run", body: { minutes: 10 } });
  await untilPolled(page, () => expect(wall).toHaveAttribute("data-state", "running", { timeout: 500 }));

  await wall.getByRole("button", { name: /^Stop/ }).click();
  await expect.poll(() => writes.length).toBe(3);
  expect(writes[2]).toMatchObject({ method: "POST", path: "/api/v1/irrigation/stop", body: { line_id: "L01" } });
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  await untilPolled(page, async () => {
    await expect(wall).toHaveAttribute("data-state", "idle", { timeout: 500 });
    await expect(toastLines(page)).toHaveText([STOPPED, CAN_STILL_START], { timeout: 500 });
  });
  await expect(page.getByRole("button", { name: "Disable L01" })).toBeVisible();

  // Hand-watering with the hose: the banner names it and has no Stop, because no Verdify valve is open.
  await useScreensClock(page, request, [VISIT, "flow-valves-closed"]);
  await load(page, "/");
  const banner = page.locator("[data-banner=fault]");
  await expect(banner).toContainText(FLOWING);
  await expect(banner.getByRole("button")).toHaveCount(0);
  const alerts = page.locator("#needs-someone");
  await alerts.locator("summary").filter({ hasText: "Add a note" }).click();
  await alerts.getByLabel("Note (optional)").fill("me, hose");
  await alerts.getByRole("button", { name: /^Acknowledge/ }).click();
  await expect(alerts).toContainText("Acknowledged by jason · 10:42");
  expect(writes).toHaveLength(4);
  expect(writes[3]).toMatchObject({ method: "POST", path: "/api/v1/alerts/201/ack", body: { note: "me, hose" } });
  expect(new Set(writes.map((write) => write.key)).size).toBe(4);
});

test("a viewer's visit at 360 × 740: the same pages with no control, no write and the spigot guidance", WEBKIT, async ({ page, request }) => {
  test.slow();
  await page.setViewportSize(SMALL_PHONE);
  const writes = countWrites(page);
  await useScreensClock(page, request, [VISIT, "viewer-session"]);
  await load(page, "/");
  await expectMorning(page);
  await expectNoControl(page);

  await toEquipment(page);
  await expect(page.locator("[data-bursts=none]")).toHaveText("Running: none");
  await expectNoControl(page);

  await tab(page, /^Water/).click();
  await expect(lineCard(page, "L01")).toBeVisible();
  await expectNoControl(page);

  await useScreensClock(page, request, [VISIT, "viewer-session", "flow-valves-closed"]);
  await load(page, "/");
  const banner = page.locator("[data-banner=fault]");
  await expect(banner).toContainText(FLOWING);
  await expect(banner).toContainText(SPIGOT);
  await expectNoControl(page);
  expect(writes).toEqual([]);
});
