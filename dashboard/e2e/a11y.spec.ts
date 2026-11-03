// SPDX-License-Identifier: MIT
// US-NOW-07's bar: WCAG 2.2 AA on every route and SCR-09 card at three widths, no rule disabled and no route skipped.
import { CARD_PAGES, ROUTES, SETTING_DRAWERS } from "./routes";
import { axe, expect, load, test, useFixture } from "./support";

const PAGES = [...ROUTES.map((path) => ({ path, status: 200 })), ...CARD_PAGES];

for (const width of [320, 768, 1440]) for (const { path, status } of PAGES) {
  test(`${path} · ${width} px · WCAG 2.2 AA`, async ({ page, request }) => {
    test.slow(path === "/settings", "axe over hundreds of rows takes about 15 s on the CI pod");
    await useFixture(request, "in-band");
    await page.setViewportSize({ width, height: 900 });
    const response = await load(page, path);
    expect(response.status()).toBe(status);
    expect(await response.text()).not.toMatch(/\sstyle\s*=/i);
    await axe(page);
  });
}

for (const width of [320, 768, 1440]) for (const { path, fixture, charts } of SETTING_DRAWERS) {
  test(`${path} under ${fixture} · ${width} px · WCAG 2.2 AA`, async ({ page, request }) => {
    await useFixture(request, fixture);
    await page.setViewportSize({ width, height: 900 });
    await load(page, path);
    await expect(page.locator("dialog[open] .plot canvas")).toHaveCount(charts);
    await axe(page);
  });
}
