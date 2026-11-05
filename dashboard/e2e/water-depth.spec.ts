// SPDX-License-Identifier: MIT
// Water depth (#1140, redesign §5.4) at 1440 and 390 px: daily water use with the no-valve water apart and missing days as
// gaps, the window it serves for the page's period, its cause chips, a failed API-21 read in its own panel, and a failed
// status read that leaves the Run dialog open.
// What the bars paint on their canvas is daily-water-use.test.tsx; here are the key, the table and the summary. Split
// from equipment-water-depth.spec.ts (redesign §10.2, WP0.1); the Equipment half is equipment-depth.spec.ts.
import { DESKTOP, PHONE, axe, expect, expectNoHorizontalScroll, fixtureRequests, load, test, untilPolled, useScreensClock } from "./support";

const VIEWPORTS = [DESKTOP, PHONE];

for (const viewport of VIEWPORTS) {
  test(`${viewport.width} px: daily water use keeps no-valve water apart, draws missing days as gaps, and carries the same whole gallons in its table`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "water-use-90d");
    await load(page, "/water/history");
    const panel = page.getByRole("region", { name: "Daily water use", exact: true });
    await expect(panel.locator("figcaption")).toHaveText("Gallons per day by line · last 30 days");
    await expect(panel.locator(".plot-water-use canvas").first()).toBeVisible();
    // The page's 7 days are shorter than the 30 the chart serves, and its head says so.
    await expect(panel.locator(".section-header .meta")).toHaveText("gallons · last 30 days, the shortest this chart serves");
    // The key says what the canvas draws: the no-valve bar in neutral --muted (hose and sink use is no fault), hatched no-data days and the partial day's dashed edge.
    const key = panel.locator(".chart-key li");
    const noValve = key.filter({ hasText: "No Verdify valve open: hose, sink, hydro or a leak" }).locator(".chart-key-swatch");
    await expect(noValve).toHaveAttribute("data-token", "muted");
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

test("a 90 d period asks API-21 for 90 days and the cause chip cuts the same days by cause, in the URL; a reload keeps them", async ({ page, request }) => {
  await useScreensClock(page, request, "water-use-90d");
  await load(page, "/water/history");
  const panel = page.getByRole("region", { name: "Daily water use", exact: true });
  await page.getByRole("list", { name: "Period" }).getByRole("link", { name: "90 d" }).click();
  await expect(page).toHaveURL(/\/water\/history\?range=90d$/);
  await expect(panel.locator("figcaption")).toHaveText("Gallons per day by line · last 90 days");
  await expect(panel.locator(".section-header .meta")).toHaveText("gallons · last 90 days");
  await expect(panel.locator("details.chart-table summary")).toHaveText("Data table · 90 rows");
  await panel.getByRole("list", { name: "Split water use by" }).getByRole("link", { name: "cause" }).click();
  await expect(page).toHaveURL(/\/water\/history\?range=90d&water_use_by=cause$/);
  await expect(panel.locator("figcaption")).toHaveText("Gallons per day by cause · last 90 days");
  await panel.locator("details.chart-table summary").click();
  await expect(panel.locator("details.chart-table thead th").filter({ hasText: /^VPD$/ })).toHaveCount(1);
  await expect(panel.locator("details.chart-table tbody tr")).toHaveCount(90);
  await page.reload();
  await expect(panel.locator("figcaption")).toHaveText("Gallons per day by cause · last 90 days");
  const days = (await fixtureRequests(request, "API-21")).map((read) => new URL(read.path, "http://fixture.invalid").searchParams.get("days"));
  expect(new Set(days)).toEqual(new Set(["30", "90"]));
  // 365 days: still the 90 it serves, and it says so.
  await page.getByRole("list", { name: "Period" }).getByRole("link", { name: "365 d" }).click();
  await expect(panel.locator(".section-header .meta")).toHaveText("gallons · last 90 days, the longest this chart serves");
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
  await page.locator('article[data-line="L01"]').getByRole("button", { name: /^Run…/ }).click();
  const dialog = page.locator("dialog[open]");
  await dialog.getByLabel("Reason (optional)").fill("Dry bench");
  await expect(page).toHaveURL(/\/water\?run=L01$/);
  await useScreensClock(page, request, "run-dialogs", { fail: { "API-IRR-01": 503 } });
  await untilPolled(page, () => expect(page.locator("article[data-line]")).toHaveCount(0, { timeout: 500 }));
  await expect(dialog.getByLabel("Reason (optional)")).toHaveValue("Dry bench");
});
