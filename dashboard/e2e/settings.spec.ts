// SPDX-License-Identifier: MIT
// SCR-05 Settings (#1132): the catalog from the rendered registry fixture, the chips in the URL, a setting going
// REQUESTED → CONFIRMED or NOT CONFIRMED, the refusal beside the field, Release, the site caps, and read-only drawers.
import type { Page } from "@playwright/test";
import { expect, fixtureWrites, load, test, toastLines, untilPolled, useScreensClock } from "./support";

const row = (page: Page, param: string) => page.locator(`tr.setting-row[data-param="${param}"]`);
const drawer = (page: Page) => page.locator("dialog[open]");
const ENGAGE = "/settings?q=mister_engage_kpa&edit=mister_engage_kpa";

test("one row per registry row; read-only rows show why and have no Edit; the band in force is DERIVED with its age", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/settings");
  const served = await request.get("http://localhost:4174/v1/settings");
  const { items } = await served.json() as { items: { param: string; writable: boolean; group: string }[] };
  await expect(page.locator("tr.setting-row")).toHaveCount(items.length);
  await expect(page.locator(".setting-edit")).toHaveCount(items.filter((item) => item.writable).length);
  await expect(row(page, "mister_engage_kpa").locator(".setting-label")).toHaveText("VPD Mister Engage");
  await expect(row(page, "temp_low").locator(".setting-device")).toContainText("legacy limit, read-only");
  await expect(row(page, "temp_low").locator(".setting-edit")).toHaveCount(0);
  const band = page.locator('tbody[data-group="band_in_force"] tr.setting-row');
  await expect(band).toHaveCount(items.filter((item) => item.group === "band_in_force").length);
  await expect(band.first()).toContainText("DERIVED · 4 min ago");
  await expect(band.locator("[data-badge=no_readback], .setting-edit")).toHaveCount(0);
  // Who wrote the value in force: Iris and an agent with AI; a forecast rule as the system, never AI.
  await expect(row(page, "mister_pulse_gap_s").locator(".setting-writer")).toHaveText("AI Iris changed this at 07:02");
  await expect(row(page, "fog_rh_ceiling_pct").locator(".setting-writer")).toHaveText("AI steward changed this at 06:47");
  await expect(row(page, "sw_economiser_enabled").locator(".setting-writer")).toHaveText("Forecast rule · system");
  await expect(row(page, "sw_economiser_enabled").locator("[data-badge=ai]")).toHaveCount(0);
});

test("a chip filters the rows and survives a reload of the URL", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/settings");
  await page.getByRole("link", { name: "Changed by an agent 4" }).click();
  await expect(page).toHaveURL(/\/settings\?filter=agent$/);
  const params = async () => page.locator("tr.setting-row").evaluateAll((rows) => rows.map((node) => node.getAttribute("data-param")));
  await expect(page.locator("tr.setting-row")).toHaveCount(4);
  const shown = await params();
  expect(shown).toContain("mister_engage_kpa");
  await page.reload();
  await expect(page.locator("tr.setting-row")).toHaveCount(4);
  expect(await params()).toEqual(shown);
  await expect(page.getByRole("link", { name: "Changed by an agent 4" })).toHaveAttribute("aria-current", "true");
});

test("?edit= opens with Manual ticked; 1.53 saves as 1.55 and reads REQUESTED, then CONFIRMED on the fixture readback", async ({ page, request }) => {
  await useScreensClock(page, request, "requested-to-confirmed");
  await load(page, ENGAGE);
  await expect(drawer(page).getByRole("checkbox", { name: "Manual: Iris skips this" })).toBeChecked();
  await drawer(page).getByLabel("New value").fill("1.53");
  await drawer(page).getByLabel("Reason (optional)").fill("Vandas drying");
  await drawer(page).getByRole("button", { name: "Save" }).click();
  await expect(drawer(page)).toHaveCount(0);
  await expect(toastLines(page)).toHaveText(["Requested 1.55 kPa for VPD Mister Engage (snapped from 1.53)"]);
  const engage = row(page, "mister_engage_kpa");
  await expect(engage.locator(".setting-request [data-badge=requested]")).toBeVisible();
  // REQUESTED reads API-09 every 5 s; the fixture's readback matches on the third read after the write.
  await untilPolled(page, () => expect(engage.locator(".setting-request")).toHaveCount(0, { timeout: 500 }));
  await expect(engage.locator(".setting-value").first()).toContainText("1.55 kPa");
  await expect(engage.locator(".setting-value [data-badge=confirmed]").first()).toBeVisible();
  const writes = await fixtureWrites(request);
  expect(writes.map((write) => [write.operationId, write.body])).toEqual([["API-10", { value: 1.53, manual: true, reason: "Vandas drying" }]]);
});

test("with no matching readback it reads NOT CONFIRMED with its reason, and no Send again exists", async ({ page, request }) => {
  await useScreensClock(page, request, "requested-to-confirmed", { confirm: false });
  await load(page, ENGAGE);
  await drawer(page).getByLabel("New value").fill("1.53");
  await drawer(page).getByRole("button", { name: "Save" }).click();
  const engage = row(page, "mister_engage_kpa");
  await untilPolled(page, () => expect(engage.locator(".setting-request [data-badge=not_confirmed]")).toBeVisible({ timeout: 500 }));
  await expect(engage.locator(".state-reason")).toHaveText("Sent 10:42; no matching readback yet");
  await expect(page.getByRole("button", { name: /send again/i })).toHaveCount(0);
  expect(await fixtureWrites(request)).toHaveLength(1);
});

test("2.60 is refused beside the field; the drawer stays open and nothing is sent", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, ENGAGE);
  const field = drawer(page).getByLabel("New value");
  await field.fill("2.60");
  await drawer(page).getByRole("button", { name: "Save" }).click();
  await expect(drawer(page).getByText("Must be 0.50–2.50 kPa")).toBeVisible();
  await expect(field).toHaveAttribute("aria-invalid", "true");
  await expect(drawer(page)).toBeVisible();
  expect(await fixtureWrites(request)).toEqual([]);
});

test("Release to auto sends one API-11 and removes MANUAL; a newer non-manual value reads superseded until Iris's next plan", async ({ page, request }) => {
  await useScreensClock(page, request, "superseded-lane");
  await load(page, "/settings?q=mister_engage_kpa");
  const engage = row(page, "mister_engage_kpa");
  await expect(engage.locator(".setting-next")).toHaveText("Next (plan) superseded until Iris's next plan");
  // Take it manual at the value in force, then release it.
  await page.getByRole("link", { name: "Edit VPD Mister Engage" }).click();
  await drawer(page).getByRole("button", { name: "Save" }).click();
  await expect(engage.locator("[data-badge=manual]")).not.toHaveCount(0);
  await page.getByRole("link", { name: "Edit VPD Mister Engage" }).click();
  await drawer(page).getByRole("button", { name: "Release to auto" }).click();
  await expect(drawer(page)).toHaveCount(0);
  await expect(engage.locator("[data-badge=manual]")).toHaveCount(0);
  await expect(toastLines(page).filter({ hasText: "Released VPD Mister Engage: plan, band or default applies" })).toHaveCount(1);
  expect((await fixtureWrites(request)).map((write) => [write.operationId, write.method])).toEqual([["API-10", "PUT"], ["API-11", "DELETE"]]);
});

test("Iris's unsnapped value shows as written", async ({ page, request }) => {
  await useScreensClock(page, request, "iris-unsnapped");
  await load(page, "/settings?q=mister_engage_delay_s");
  await expect(row(page, "mister_engage_delay_s").locator(".setting-value").first()).toContainText("48.75");
});

test("with program rev 42 a site cap reads On controller · rev 42", async ({ page, request }) => {
  await useScreensClock(page, request, "program-rev-42");
  await load(page, "/settings?q=mister_daily_volume");
  await expect(row(page, "mister_daily_volume_max_gal").locator(".setting-value")).toHaveText("On controller · rev 42: 360 gal Confirmed · 2 min ago");
});

test("before the engine, saving a site cap reads REQUESTED with its reason at once", async ({ page, request }) => {
  await useScreensClock(page, request, "before-engine");
  await load(page, "/settings?q=sched_mist&edit=sched_mist_rh_max_pct");
  await drawer(page).getByLabel("New value").fill("88");
  await drawer(page).getByRole("button", { name: "Save" }).click();
  const cap = row(page, "sched_mist_rh_max_pct");
  await expect(cap.locator(".setting-request [data-badge=requested]")).toBeVisible();
  await expect(cap.locator(".state-reason")).toHaveText(/^applies with the irrigation firmware$/i);
});

test("a viewer sees every row with no Edit, and ?edit= opens the drawer read-only", async ({ page, request }) => {
  await useScreensClock(page, request, "viewer-session");
  await load(page, "/settings");
  await expect(page.locator("tr.setting-row").first()).toBeVisible();
  await expect(page.locator(".setting-edit")).toHaveCount(0);
  await load(page, ENGAGE);
  await expect(drawer(page).getByRole("heading", { level: 2 })).toHaveText("Setting");
  await expect(drawer(page).getByLabel("New value")).toBeDisabled();
  await expect(drawer(page).getByRole("button", { name: /^(Save|Release to auto|Cancel)$/ })).toHaveCount(0);
  await expect(drawer(page).getByRole("button", { name: "Done" })).toBeVisible();
});

test("with changes switched off an operator's Save and Release are disabled beside the banner", async ({ page, request }) => {
  await useScreensClock(page, request, "writes-off");
  await load(page, ENGAGE);
  await expect(page.locator("[data-banner=warn]")).toHaveText("Changes are switched off; reading still works");
  await expect(drawer(page).getByRole("button", { name: "Save" })).toBeDisabled();
  await expect(drawer(page).getByRole("button", { name: "Release to auto" })).toBeDisabled();
});
