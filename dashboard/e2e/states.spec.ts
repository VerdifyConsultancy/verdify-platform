// SPDX-License-Identifier: MIT
// The honesty rules most likely to slip (screens §1.2, §1.5): while data is missing no page reads as an all clear.
import { ROUTES } from "./routes";
import { expect, expectNoText, load, test, untilPolled, useFixture } from "./support";

for (const path of ROUTES) {
  test(`${path}: with the API down, after two failed polls the banner shows and nothing reads green`, async ({ page, request }) => {
    await useFixture(request, "api-down");
    await page.clock.install();
    await load(page, path);
    // The shell's backoff puts its second poll 30 s after the first, and the banner waits for that second failure.
    await untilPolled(page, () => expect(page.locator("[data-banner=fault]")).toHaveText("Dashboard API unavailable", { timeout: 1_000 }), 31_000);
    for (const reassurance of ["All OK", "No open alerts", "CONFIRMED"]) await expectNoText(page, reassurance);
    await expectNoText(page, /\bin band\b/i);
    await expect(page.locator(".tag-ok, .status-complete, .fresh-ok")).toHaveCount(0);
  });

  for (const [fixture, banner] of [
    ["ingestor-not-running", /^Ingestor not running since \d\d:\d\d\. The controller keeps running on its own\./],
    ["controller-not-reporting", /^Controller not reporting since \d\d:\d\d\. If it has power, it keeps running VPD misting/],
  ] as const) {
    test(`${path}: under ${fixture} the banner says so, never "Controller offline" or "No open alerts"`, async ({ page, request }) => {
      await useFixture(request, fixture);
      await load(page, path);
      await expect(page.locator("[data-banner=fault]")).toHaveText(banner);
      await expectNoText(page, /controller offline/i);
      await expectNoText(page, /no open alerts/i);
    });
  }
}
