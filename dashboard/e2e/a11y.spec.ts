// SPDX-License-Identifier: MIT
// US-NOW-07's bar: WCAG 2.2 AA on every route and SCR-09 card at three widths, no rule disabled and no route skipped.
import { CARD_PAGES, ROUTES } from "./routes";
import { axe, expect, load, test, useFixture } from "./support";

const PAGES = [...ROUTES.map((path) => ({ path, status: 200 })), ...CARD_PAGES];

for (const width of [320, 768, 1440]) for (const { path, status } of PAGES) {
  test(`${path} · ${width} px · WCAG 2.2 AA`, async ({ page, request }) => {
    // A whole-page axe scan on the CI pod, with hundreds of rows on /settings.
    test.slow();
    await useFixture(request, "in-band");
    await page.setViewportSize({ width, height: 900 });
    const response = await load(page, path);
    expect(response.status()).toBe(status);
    expect(await response.text()).not.toMatch(/\sstyle\s*=/i);
    await axe(page);
  });
}
