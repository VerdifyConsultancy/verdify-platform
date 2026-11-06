// SPDX-License-Identifier: MIT
// Screens §1.3: a viewer has the same screens with no write control on any of them, under fixtures that give the write
// controls something to act on. The operator run on the same fixtures shows the viewer check is not vacuous.
import { ROUTES, WRITE_ROUTES } from "./routes";
import { WEBKIT, expect, expectRolePill, load, test, useFixture, useScreensClock, writeControls } from "./support";

const SOMETHING_TO_ACT_ON = ["alerts-merged", "l01-running", "mister-fault"];

for (const path of ROUTES) {
  test(`${path}: a viewer sees no write control, and the pill reads VIEWER`, WEBKIT, async ({ page, request }) => {
    await useFixture(request, [...SOMETHING_TO_ACT_ON, "viewer-session"]);
    await load(page, path);
    await expectRolePill(page, "VIEWER");
    await expect(writeControls(page, { includeHidden: true })).toHaveCount(0);
    await expect(page.locator("[data-write-control]")).toHaveCount(0);
  });

  test(`${path}: for an operator the pill reads OPERATOR${WRITE_ROUTES.includes(path) ? " and a write control shows" : ""}`, async ({ page, request }) => {
    await useFixture(request, SOMETHING_TO_ACT_ON);
    await load(page, path);
    await expectRolePill(page, "OPERATOR");
    if (WRITE_ROUTES.includes(path)) await expect(writeControls(page).first()).toBeVisible();
  });
}

test("a viewer reads the greenhouse snapshot on Now, as an operator does (Jason, 2026-11-06, #1346)", async ({ page, request }) => {
  await useScreensClock(page, request, ["in-band", "time-in-band", "viewer-session"]);
  await load(page, "/");
  await expectRolePill(page, "VIEWER");
  await expect(page.locator(".now-snapshot .now-snapshot-text")).toContainText("The greenhouse is at");
});
