// SPDX-License-Identifier: MIT
// J9, the caretaker's visit, at 360 × 740 against the fixture API (redesign §10.3, product §J9) on the five sections: an
// operator starts Fans 15 min from Now's quick actions, runs and stops the wall drip on Water, then acknowledges the flow
// alert with a note on Activity; a viewer walks the same pages with no control and the spigot guidance. Every write is
// counted in the browser with page.on('request'), across the fixture switch to flowing water, which clears the fixture
// API's own log.
import type { Page } from "@playwright/test";
import { CAN_STILL_START, SMALL_PHONE, STOPPED, WEBKIT, bottomBar, expect, expectNoHorizontalScroll, load, test, toastLines, untilPolled, useScreensClock, writeControls } from "./support";

const VISIT = "caretaker-visit";
const OCCUPIED = "Someone inside: misters wait (Frigate, 1 min ago)";
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
/** A line's row on Water: the article named for it, with `data-line` and `data-state` (redesign §5.2). */
const lineRow = (page: Page, id: string) => page.locator(`article[data-line="${id}"]`);

async function expectNoControl(page: Page) {
  await expect(writeControls(page, { includeHidden: true })).toHaveCount(0);
  await expect(page.locator("[data-write-control]")).toHaveCount(0);
}
/** Now's first screen, the same for both roles: the answer, the age, when each zone got water, and who is inside. */
async function expectMorning(page: Page) {
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("All OK");
  await expect(page.locator("#data")).toHaveText("Updated 20 s ago");
  await expect(page.locator("[data-water=wall] .now-water-last")).toHaveText("today 07:31, 12 min");
  await expect(page.locator("[data-water=center] .now-water-last")).toHaveText("misted 09:58 (VPD)");
  await expect(page.locator("[data-chip=occupancy]")).toHaveText(OCCUPIED);
  await expectNoHorizontalScroll(page);
}

test("an operator's visit at 360 × 740: a burst from Now, a run and its stop on Water, then a note on the flow alert in Activity, each one request", WEBKIT, async ({ page, request }) => {
  // The whole visit on the CI pod.
  test.slow();
  await page.setViewportSize(SMALL_PHONE);
  const writes = countWrites(page);
  await useScreensClock(page, request, VISIT);
  await load(page, "/");
  await expectMorning(page);

  // Now's quick actions hold the bursts (D-UX-05): no trip to Equipment.
  await page.getByRole("button", { name: "Fans 15 min" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ method: "POST", path: "/api/v1/equipment/override", body: { kind: "fans", minutes: 15 } });
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  // A regex reads the no-break space before the unit as it is.
  await untilPolled(page, () => expect(page.locator(".now-running [data-chip=burst]")).toHaveText(/^Fans burst · \d+\smin left$/, { timeout: 500 }));
  await expect(page.getByRole("button", { name: "Clear bursts" })).toBeVisible();

  await tab(page, /^Water/).click();
  const wall = lineRow(page, "L01");
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
  await tab(page, /^Activity/).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Alerts");
  const flow = page.locator("#alert-201");
  await expect(flow).toContainText(SPIGOT);
  await flow.locator("summary").filter({ hasText: "Add a note" }).click();
  await flow.getByLabel("Note (optional)").fill("me, hose");
  await flow.getByRole("button", { name: /^Acknowledge/ }).click();
  await expect(flow).toContainText("Acknowledged by Jason · 10:42");
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
  await expect(page.locator(".now-actions")).toHaveCount(0);
  await expectNoControl(page);

  await tab(page, /^Water/).click();
  await expect(lineRow(page, "L01")).toBeVisible();
  await expectNoControl(page);

  await useScreensClock(page, request, [VISIT, "viewer-session", "flow-valves-closed"]);
  await load(page, "/");
  const banner = page.locator("[data-banner=fault]");
  await expect(banner).toContainText(FLOWING);
  await expect(banner).toContainText(SPIGOT);
  await expectNoControl(page);
  await tab(page, /^Activity/).click();
  await expect(page.locator("#alert-201")).toContainText(SPIGOT);
  await expectNoControl(page);
  expect(writes).toEqual([]);
});
