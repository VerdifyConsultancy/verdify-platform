// SPDX-License-Identifier: MIT
// SCR-05 Settings (#1132, redesign §5.10): the catalog from the rendered registry fixture, the rail's filters in the URL
// with their counts, each row's device value and age, bounds bar, one pill and meta line, a setting going REQUESTED → CONFIRMED
// or NOT CONFIRMED, the refusal beside the field, Release, the site caps, read-only drawers, and the phone's selects.
// #1139: the drawer's history with Revert, and a crop-band anchor's curve preview.
import type { Page } from "@playwright/test";
import { FIXTURE_API, NOW, SMALL_PHONE, WEBKIT, expect, expectNoHorizontalScroll, fixtureRequests, fixtureWrites, load, test, toastLines, untilPolled, useScreensClock } from "./support";

const row = (page: Page, param: string) => page.locator(`tr.setting-row[data-param="${param}"]`);
const pill = (page: Page, param: string) => row(page, param).locator(".ctl-row-pill [data-badge]");
const drawer = (page: Page) => page.locator("dialog[open]");
const ENGAGE = "/settings?q=mister_engage_kpa&edit=mister_engage_kpa";

test("one row per registry row; read-only rows show why and have no Edit; the band in force is DERIVED with its age", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/settings");
  const served = await request.get(`${FIXTURE_API}/v1/settings`);
  const { items } = await served.json() as { items: { param: string; writable: boolean; group: string; control_class: string }[] };
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Settings");
  await expect(page.locator(".page-status")).toHaveText(`${items.length} settings · 0 waiting · 0 not applied · 0 manual`);
  // Retired rows wait behind "Show retired" (#1333).
  await expect(page.locator("tr.setting-row")).toHaveCount(items.filter((item) => item.control_class !== "retired").length);
  await expect(page.locator(".setting-edit")).toHaveCount(items.filter((item) => item.writable).length);
  await expect(row(page, "mister_engage_kpa").locator(".setting-label")).toHaveText("VPD Mister Engage");
  // The device value large, its bounds bar with the next plan's ring, and one meta line; no pill in the steady state.
  await expect(row(page, "mister_engage_kpa").locator(".setting-value")).toHaveText("1.60 kPa");
  // Every row shows its age (Jason, 2026-11-07), worded as the drawer words it.
  await expect(page.locator("tr.setting-row .setting-age")).toHaveCount(await page.locator("tr.setting-row").count());
  await expect(row(page, "mister_engage_kpa").locator(".setting-age")).toHaveText(/^reported \d+\s+min ago$/);
  await expect(row(page, "mister_engage_kpa").locator(".setting-age")).toBeVisible();
  await expect(row(page, "mister_engage_kpa").locator(".ctl-bounds-end")).toHaveText(["0.50", "2.50"]);
  await expect(row(page, "mister_engage_kpa").locator(".ctl-bounds-planned")).toHaveCount(1);
  await expect(row(page, "mister_engage_kpa").locator(".setting-next")).toHaveText("next 1.70 kPa at 13:00 · AI");
  await expect(page.locator(".setting-row [data-badge=confirmed], .ctl-row-pill [data-badge]")).toHaveCount(0);
  await expect(row(page, "temp_low").locator(".setting-device")).toContainText("Legacy limit, not used while the on-chip band runs");
  await expect(row(page, "temp_low").locator(".setting-edit")).toHaveCount(0);
  const band = page.locator('tbody[data-group="band_in_force"] tr.setting-row');
  await expect(band).toHaveCount(items.filter((item) => item.group === "band_in_force").length);
  await expect(band.first()).toContainText("Derived · 4 min ago");
  await expect(band.locator("[data-badge=no_readback], .setting-edit")).toHaveCount(0);
  // Who wrote the value in force: Iris and an agent with AI as a text mark; a forecast rule as the system, never AI.
  await expect(row(page, "mister_pulse_gap_s").locator(".setting-writer")).toHaveText("Iris changed this at 07:02 · AI");
  await expect(row(page, "fog_rh_ceiling_pct").locator(".setting-writer")).toHaveText("steward changed this at 06:47 · “Dry afternoon ahead” · AI");
  await expect(row(page, "sw_economiser_enabled").locator(".setting-writer")).toHaveText("Forecast rule · system");
  await expect(row(page, "sw_economiser_enabled").locator("[data-badge=ai]")).toHaveCount(0);
});

test("the rail's Show filters carry counts, filter the rows and survive a reload of the URL; Groups jump to their group", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/settings");
  const rail = page.getByRole("navigation", { name: "Settings filters and groups" });
  await expect(rail.locator(".ctl-rail-list").first().getByRole("link")).toHaveText([
    /^All \d+$/, "Changed in the last 24 h 5", "Waiting for controller 0", "Not applied 0", "Manual (Iris skips) 0", "Set by Iris or an agent 4", /^Read-only \d+$/,
  ]);
  await rail.getByRole("link", { name: "Controller safety 27" }).click();
  await expect(page.locator("#group-controller_safety")).toBeInViewport();
  await expect(page).toHaveURL(/\/settings$/);
  await page.getByRole("link", { name: "Set by Iris or an agent 4" }).click();
  await expect(page).toHaveURL(/\/settings\?filter=agent$/);
  const params = async () => page.locator("tr.setting-row").evaluateAll((rows) => rows.map((node) => node.getAttribute("data-param")));
  await expect(page.locator("tr.setting-row")).toHaveCount(4);
  const shown = await params();
  expect(shown).toContain("mister_engage_kpa");
  await page.reload();
  await expect(page.locator("tr.setting-row")).toHaveCount(4);
  expect(await params()).toEqual(shown);
  await expect(page.getByRole("link", { name: "Set by Iris or an agent 4" })).toHaveAttribute("aria-current", "true");
});

test("?edit= opens with Manual ticked; 1.53 saves as 1.55 and reads REQUESTED, then CONFIRMED on the fixture readback", async ({ page, request }) => {
  await useScreensClock(page, request, "requested-to-confirmed");
  await load(page, ENGAGE);
  await expect(drawer(page).getByRole("checkbox", { name: "Let Iris change this" })).not.toBeChecked();
  await drawer(page).getByLabel("New value").fill("1.53");
  await drawer(page).getByLabel("Reason (optional)").fill("Vandas drying");
  await drawer(page).getByRole("button", { name: "Save" }).click();
  await expect(drawer(page)).toHaveCount(0);
  await expect(toastLines(page)).toHaveText(["Requested 1.55 kPa for VPD Mister Engage (snapped from 1.53)"]);
  const engage = row(page, "mister_engage_kpa");
  await expect(pill(page, "mister_engage_kpa")).toHaveAttribute("data-badge", "requested");
  await expect(engage.locator(".setting-request")).toContainText("→ 1.55 kPa");
  await expect(engage.locator(".setting-request")).toContainText("· manual · jason · “Vandas drying”");
  // REQUESTED reads API-09 every 5 s; the fixture's readback matches on the third read after the write.
  await untilPolled(page, () => expect(engage.locator(".setting-request")).toHaveCount(0, { timeout: 500 }));
  await expect(engage.locator(".setting-value").first()).toContainText("1.55 kPa");
  // A manual value's one pill is MANUAL, so the new confirmation reads in words beside who wrote it.
  await expect(pill(page, "mister_engage_kpa")).toHaveAttribute("data-badge", "manual");
  await expect(engage.locator(".setting-writer")).toContainText("confirmed 10:42");
  const writes = await fixtureWrites(request);
  expect(writes.map((write) => [write.operationId, write.body])).toEqual([["API-10", { value: 1.53, manual: true, reason: "Vandas drying" }]]);
});

test("with no matching readback it reads NOT CONFIRMED with its reason, and no Send again exists", async ({ page, request }) => {
  await useScreensClock(page, request, "requested-to-confirmed", { confirm: false });
  await load(page, ENGAGE);
  await drawer(page).getByLabel("New value").fill("1.53");
  await drawer(page).getByRole("button", { name: "Save" }).click();
  const engage = row(page, "mister_engage_kpa");
  await untilPolled(page, () => expect(pill(page, "mister_engage_kpa")).toHaveAttribute("data-badge", "not_confirmed", { timeout: 500 }));
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
  await expect(engage.locator(".setting-next")).toHaveText("superseded until Iris's next plan");
  // Take it manual at the value in force, then release it.
  await page.getByRole("link", { name: "Edit VPD Mister Engage" }).click();
  await drawer(page).getByRole("button", { name: "Save" }).click();
  await expect(engage.locator(".setting-request")).toContainText("manual");
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
  await expect(row(page, "mister_daily_volume_max_gal").locator(".setting-value")).toHaveText("On controller · rev 42: 360 gal");
});

test("before the engine, saving a site cap reads REQUESTED with its reason at once", async ({ page, request }) => {
  await useScreensClock(page, request, "before-engine");
  await load(page, "/settings?q=sched_mist&edit=sched_mist_rh_max_pct");
  await drawer(page).getByLabel("New value").fill("88");
  await drawer(page).getByRole("button", { name: "Save" }).click();
  const cap = row(page, "sched_mist_rh_max_pct");
  await expect(pill(page, "sched_mist_rh_max_pct")).toHaveAttribute("data-badge", "requested");
  await expect(cap.locator(".state-reason")).toHaveText(/^applies with the irrigation firmware$/i);
});

test("a viewer sees every row with no Edit, and ?edit= opens the drawer read-only", async ({ page, request }) => {
  await useScreensClock(page, request, "viewer-session");
  await load(page, "/settings");
  await expect(page.locator("tr.setting-row").first()).toBeVisible();
  await expect(page.locator(".setting-edit")).toHaveCount(0);
  // Every row opens its drawer read-only, from Details.
  await expect(row(page, "mister_engage_kpa").getByRole("link", { name: "Details of VPD Mister Engage" })).toHaveAttribute("href", "/settings?edit=mister_engage_kpa");
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

// ---------------------------------------------------------------------------------------------------- history and revert (#1139)
const CENTER = "/settings?q=zone_vpd_target_center_sm&edit=zone_vpd_target_center_sm";
const history = (page: Page) => drawer(page).locator(".drawer-history");
const preview = (page: Page) => drawer(page).locator(".band-preview");
const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;
const iso = (ms: number) => new Date(ms).toISOString();
const windowOf = (path: string) => { const query = new URL(path, "http://x").searchParams; return [query.get("from"), query.get("to")]; };

test("the history reads 7 d to 12 h ahead by default, with its window chips in the URL; the key names each source present", async ({ page, request }) => {
  await useScreensClock(page, request, "setting-history");
  await load(page, ENGAGE);
  const now = Date.parse(NOW);
  await expect(history(page).getByRole("link", { name: "7 d" })).toHaveAttribute("aria-current", "true");
  await expect(history(page).locator(".plot canvas").first()).toBeVisible();
  await expect(history(page).locator(".chart-key li")).toHaveText(["Device readback", "iris · AI", "Jason · dashboard", "SUPERSEDED", "Min and max"]);
  expect((await fixtureRequests(request, "API-12")).map((row) => windowOf(row.path))).toEqual([[iso(now - 7 * DAY_MS), iso(now + 12 * HOUR_MS)]]);
  await history(page).getByRole("link", { name: "30 d" }).click();
  await expect(page).toHaveURL(/edit=mister_engage_kpa&hist=30d$/);
  await expect(history(page).getByRole("link", { name: "30 d" })).toHaveAttribute("aria-current", "true");
  await expect.poll(async () => (await fixtureRequests(request, "API-12")).map((row) => windowOf(row.path)[0])).toEqual([iso(now - 7 * DAY_MS), iso(now - 30 * DAY_MS)]);
  // The tooltip's and the table's words, and the CSV.
  await history(page).locator("summary").click();
  await expect(history(page).locator("tbody")).toContainText("requested 1.60 kPa · iris · AI · CONFIRMED");
  await expect(history(page).locator("tbody")).toContainText("requested 1.55 kPa · Jason · dashboard · CONFIRMED · “Vandas drying”");
  await expect(history(page).getByRole("link", { name: "CSV of this window" })).toHaveAttribute("href", /^blob:/);
  // Closing the drawer drops its window from the URL.
  await drawer(page).getByRole("button", { name: "Cancel" }).click();
  await expect(page).toHaveURL(/\/settings\?q=mister_engage_kpa$/);
});

test("with setting-history Revert reads Revert to 1.60 kPa and sends one API-10; the row reads REQUESTED, then CONFIRMED", async ({ page, request }) => {
  await useScreensClock(page, request, ["setting-history", "requested-to-confirmed"]);
  await load(page, ENGAGE);
  await expect(drawer(page).getByLabel("Revert reason")).toHaveValue("Revert to 1.60 kPa (iris, 11/02 07:02)");
  await drawer(page).getByRole("button", { name: "Revert to 1.60 kPa (iris, 11/02 07:02)" }).click();
  await expect(drawer(page)).toHaveCount(0);
  await expect(toastLines(page)).toHaveText(["Requested 1.60 kPa for VPD Mister Engage"]);
  const engage = row(page, "mister_engage_kpa");
  await expect(pill(page, "mister_engage_kpa")).toHaveAttribute("data-badge", "requested");
  await untilPolled(page, () => expect(engage.locator(".setting-request")).toHaveCount(0, { timeout: 500 }));
  await expect(engage.locator(".setting-value").first()).toContainText("1.60 kPa");
  await expect(engage.locator(".setting-writer")).toContainText("confirmed");
  const writes = await fixtureWrites(request);
  expect(writes.map((write) => [write.operationId, write.body])).toEqual([["API-10", { value: 1.6, manual: true, reason: "Revert to 1.60 kPa (iris, 11/02 07:02)" }]]);
  expect(writes[0].idempotency_key).toBeTruthy();
});

test("with setting-history-release Revert to auto sends one API-11 and the row reads Released, then the readback", async ({ page, request }) => {
  await useScreensClock(page, request, "setting-history-release");
  await load(page, ENGAGE);
  await drawer(page).getByRole("button", { name: "Revert to auto" }).click();
  await expect(drawer(page)).toHaveCount(0);
  const engage = row(page, "mister_engage_kpa");
  await expect(engage.locator(".setting-writer")).toHaveText("Released: plan, band or default applies");
  await expect(engage.locator(".setting-value").first()).toContainText("1.55 kPa");
  expect((await fixtureWrites(request)).map((write) => [write.operationId, write.method, write.body])).toEqual([["API-11", "DELETE", null]]);
});

test("with no earlier row in 90 days Revert reads 90 days back once, then goes back to auto; with an older row, to that row", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/settings?q=fog_rh_ceiling_pct&edit=fog_rh_ceiling_pct");
  await expect(drawer(page).getByRole("button", { name: "Revert to auto" })).toBeVisible();
  const now = Date.parse(NOW);
  expect((await fixtureRequests(request, "API-12")).map((row) => windowOf(row.path)[0])).toEqual([iso(now - 7 * DAY_MS), iso(now - 90 * DAY_MS)]);
  await useScreensClock(page, request, "setting-history-older");
  await load(page, ENGAGE);
  await expect(drawer(page).getByRole("button", { name: "Revert to 1.60 kPa (steward, 10/21 08:02)" })).toBeVisible();
});

test("a setting with no desired row has no Revert; a viewer sees the history and no Revert", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, ENGAGE);
  await expect(history(page).locator(".plot canvas").first()).toBeVisible();
  await expect(history(page).getByRole("button", { name: /^Revert/ })).toHaveCount(0);
  await useScreensClock(page, request, ["setting-history", "viewer-session"]);
  await load(page, ENGAGE);
  await expect(history(page).locator(".chart-key li").first()).toHaveText("Device readback");
  await expect(history(page).locator(".drawer-revert")).toHaveCount(0);
});

test("a band edit redraws the API-19 preview before Save; a draft outside the bounds reads Must be … and Save still works", async ({ page, request }) => {
  await useScreensClock(page, request, "band-preview-center");
  await load(page, CENTER);
  await expect(preview(page).locator("figcaption")).toHaveText("Center zone VPD · kPa");
  await expect(preview(page).locator(".plot canvas").first()).toBeVisible();
  await expect(preview(page).locator(".chart-notes li")).toHaveText(["Sunrise 06:35", "Solar noon 11:47", "Sunset 16:58"]);
  const field = drawer(page).getByLabel("New value");
  await field.fill("1.25");
  await expect(preview(page).locator(".chart-key li")).toHaveText(["Band as stored", "Target as stored", "PREVIEW"]);
  expect((await fixtureRequests(request, "API-19")).at(-1)?.path).toMatch(/\/band\/preview\?param=zone_vpd_target_center_sm&value=1\.25$/);
  await field.fill("3.5");
  await expect(preview(page).locator(".chart-message")).toHaveText("Must be 0.10–3.00 kPa");
  expect(await fixtureWrites(request)).toEqual([]);
  await expect(drawer(page).getByRole("button", { name: "Save" })).toBeEnabled();
  await field.fill("1.25");
  await expect(preview(page).locator(".chart-message")).toHaveCount(0);
  await drawer(page).getByRole("button", { name: "Save" }).click();
  await expect(drawer(page)).toHaveCount(0);
  expect((await fixtureWrites(request)).map((write) => [write.operationId, write.body])).toEqual([["API-10", { value: 1.25, manual: true }]]);
});

test("after saving an anchor, Revert restores the previous one through API-10, and the preview's current curve is the previous one again", async ({ page, request }) => {
  await useScreensClock(page, request, "band-preview-center");
  await load(page, CENTER);
  const edit = page.getByRole("link", { name: "Edit Zone VPD Target Center SM" });
  // Row 72 of the table is 12:00, where the fixture's curve reaches the anchor.
  const noonTarget = async () => {
    await preview(page).locator("summary").click();
    return preview(page).locator("tbody tr").nth(72).locator("td").nth(1);
  };
  await expect(await noonTarget()).toHaveText("1.10 kPa");
  await drawer(page).getByLabel("New value").fill("1.25");
  await drawer(page).getByRole("button", { name: "Save" }).click();
  await expect(drawer(page)).toHaveCount(0);
  await edit.click();
  await expect(await noonTarget()).toHaveText("1.25 kPa");
  await drawer(page).getByRole("button", { name: "Revert to 1.10 kPa (device, 10/31 07:00)" }).click();
  await expect(drawer(page)).toHaveCount(0);
  await edit.click();
  await expect(await noonTarget()).toHaveText("1.10 kPa");
  expect((await fixtureWrites(request)).map((write) => [write.operationId, (write.body as { value: number }).value])).toEqual([["API-10", 1.25], ["API-10", 1.1]]);
});

test("at 360 px Settings uses two selects, the rows stack as cards that open their drawer, and nothing scrolls sideways, the drawer included", WEBKIT, async ({ page, request }) => {
  await page.setViewportSize(SMALL_PHONE);
  await useScreensClock(page, request, "in-band");
  await load(page, "/settings");
  const rail = page.getByRole("navigation", { name: "Settings filters and groups" });
  await expect(rail.locator(".ctl-rail-lists")).toBeHidden();
  const show = page.getByRole("combobox", { name: "Show" });
  await expect(show).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Group" })).toBeVisible();
  await show.selectOption("agent");
  await expect(page).toHaveURL(/\/settings\?filter=agent$/);
  await expect(page.locator("tr.setting-row")).toHaveCount(4);
  // A row is a stacked card: no bounds bar, its value, pill and meta under its label, and its link at least 44 px.
  const engage = row(page, "mister_engage_kpa");
  await expect(engage.locator(".ctl-bounds")).toBeHidden();
  // Every card shows its age beside its value at phone width too.
  await expect(page.locator("tr.setting-row .setting-age")).toHaveCount(4);
  await expect(engage.locator(".setting-age")).toBeVisible();
  await expect(engage.locator(".setting-age")).toHaveText(/ ago$/);
  const [card, catalog] = await Promise.all([engage.boundingBox(), page.locator(".ctl-catalog").boundingBox()]);
  expect(Math.abs(card!.width - catalog!.width)).toBeLessThanOrEqual(2);
  expect((await engage.getByRole("link", { name: "Edit VPD Mister Engage" }).boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await expectNoHorizontalScroll(page);
  // The link covers the card: a tap anywhere on it (here its middle) opens the drawer.
  await engage.click();
  await expect(page).toHaveURL(/edit=mister_engage_kpa/);
  await expect(drawer(page)).toBeVisible();
  await expectNoHorizontalScroll(page);
  expect(await drawer(page).locator(".drawer-body").evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(0);
});
