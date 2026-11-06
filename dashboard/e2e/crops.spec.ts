// SPDX-License-Identifier: MIT
// SCR-08 Crops → Map and History (#1330) at 1440 and 390 px against #1122's fixture API: the cards and the hydro rack,
// planting a selection of cells (API-CROP-07), a conflict shown before Save and refused by the server on Save, moving
// and harvesting a planting from its drawer, the history table, and that a viewer has no write control on either view.
import type { Page } from "@playwright/test";
import { DESKTOP, PHONE, axe, expect, expectNoHorizontalScroll, fixtureRequests, fixtureWrites, load, test, useFixture, useScreensClock } from "./support";

const cell = (page: Page, label: string) => page.locator(`button[data-cell="${label}"]`);
const toolbar = (page: Page) => page.getByRole("region", { name: "Selection" });
const dialog = (page: Page) => page.getByRole("dialog");

for (const viewport of [DESKTOP, PHONE]) {
  test(`${viewport.width} px: the map draws the 60-cell rack and every wall, the history lists the plantings, and both are axe clean`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    await useScreensClock(page, request, "crops-planted");
    await load(page, "/crops");
    await expect(page.locator('button[data-cell^="EAST-HYDRO-"]')).toHaveCount(60);
    await expect(page.getByText(/19 of 60 hydro cells planted/)).toBeVisible();
    await expect(cell(page, "EAST-HYDRO-1")).toHaveAttribute("aria-label", /^EAST-HYDRO-1, Lettuce, Butterhead, /);
    await expect(page.getByRole("region", { name: /cells, scrolls sideways$/ })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await axe(page);
    await load(page, "/crops/history");
    await expect(page.getByRole("table", { name: "Plantings" })).toBeVisible();
    await expect(page.getByRole("rowheader", { name: "Canna Lilies" })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await axe(page);
  });
}

test("an operator selects three empty cells and plants them: one API-CROP-07 with the range, then the cells read the crop", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "in-band");
  await load(page, "/crops");
  await cell(page, "EAST-HYDRO-41").click();
  await cell(page, "EAST-HYDRO-43").click({ modifiers: ["Shift"] });
  await expect(toolbar(page)).toContainText("3 selected: EAST-HYDRO-41..43");
  await toolbar(page).getByRole("button", { name: "Plant…" }).click();
  await dialog(page).getByLabel("Crop", { exact: true }).fill("Lettuce");
  await dialog(page).getByLabel("Plants per cell").fill("1");
  await expect(dialog(page).getByRole("status")).toContainText("(3 cells)");
  await dialog(page).getByRole("button", { name: "Save planting" }).click();
  await expect(cell(page, "EAST-HYDRO-42")).toHaveAttribute("aria-label", /^EAST-HYDRO-42, Lettuce, /);
  const writes = await fixtureWrites(request);
  expect(writes.map((row) => row.operationId)).toEqual(["API-CROP-07"]);
  expect(writes[0].body).toMatchObject({ crop: "Lettuce", positions: ["EAST-HYDRO-41..43"] });
});

test("planting over a planted cell shows the conflict before Save, and Save gives the server's refusal and changes nothing", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "crops-planted");
  await load(page, "/crops");
  await cell(page, "EAST-HYDRO-10").click();
  await cell(page, "EAST-HYDRO-11").click();
  await toolbar(page).getByRole("button", { name: "Plant…" }).click();
  await dialog(page).getByLabel("Crop", { exact: true }).fill("Kale");
  await expect(dialog(page).locator(".crop-conflicts")).toContainText("Already planted:");
  await dialog(page).getByRole("button", { name: "Save planting" }).click();
  await expect(dialog(page)).toContainText("Already planted");
  await expect(cell(page, "EAST-HYDRO-10")).toHaveAttribute("aria-label", /Lettuce/);
  expect((await fixtureRequests(request, "API-CROP-07")).length).toBe(1);
});

test("an operator moves a planting from the selection, then harvests it from its drawer", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "crops-planted");
  await load(page, "/crops");
  for (const label of ["EAST-HYDRO-31", "EAST-HYDRO-32", "EAST-HYDRO-33", "EAST-HYDRO-34", "EAST-HYDRO-45"]) await cell(page, label).click();
  await toolbar(page).getByRole("button", { name: "Move…" }).click();
  await expect(dialog(page).getByRole("heading", { name: "Move planting" })).toBeVisible();
  const to = dialog(page).getByLabel("To", { exact: true });
  await to.fill("EAST-HYDRO-45..48");
  await dialog(page).getByRole("button", { name: "Save move" }).click();
  await expect(cell(page, "EAST-HYDRO-45")).toHaveAttribute("aria-label", /Strawberries/);
  const move = await fixtureRequests(request, "API-CROP-08");
  expect(move).toHaveLength(1);
  expect(move[0].body).toMatchObject({ to_positions: ["EAST-HYDRO-45..48"] });
  await toolbar(page).getByRole("button", { name: "Clear" }).click();
  await cell(page, "EAST-HYDRO-45").click();
  await toolbar(page).getByRole("button", { name: "Planting details" }).click();
  await dialog(page).getByRole("button", { name: "Harvest…" }).click();
  await dialog(page).getByLabel("Plants harvested (optional)").fill("4");
  await dialog(page).getByRole("button", { name: "Save harvest" }).click();
  const harvest = await fixtureRequests(request, "API-CROP-09");
  expect(harvest).toHaveLength(1);
  expect(harvest[0].body).toMatchObject({ count: 4 });
});

for (const path of ["/crops", "/crops/history"]) {
  test(`${path}: a viewer selects and reads, with no write control; an operator gets Plant…`, async ({ page, request }) => {
    await page.setViewportSize(DESKTOP);
    await useFixture(request, ["crops-planted", "viewer-session"]);
    await load(page, path);
    if (path === "/crops") {
      await cell(page, "EAST-HYDRO-1").click();
      await expect(toolbar(page).getByRole("button", { name: "Planting details" })).toBeVisible();
    }
    await expect(page.locator("[data-write-control]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^(Plant|Move|Harvest|Remove|Edit)( \S+)?…$/ })).toHaveCount(0);
    if (path === "/crops") {
      await useFixture(request, "crops-planted");
      await load(page, path);
      await expect(toolbar(page).getByRole("button", { name: "Plant…" })).toBeVisible();
    }
  });
}
