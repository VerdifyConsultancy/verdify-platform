// SPDX-License-Identifier: MIT
// The caretaker's phone: no page scrolls sideways at 360 or 390 px and the bottom bar holds the five tabs; and with
// reduced motion emulated, nothing moves.
import { CARD_PAGES, ROUTES, SETTING_DRAWERS } from "./routes";
import { PHONE, SMALL_PHONE, WEBKIT, bottomBar, expect, expectNoHorizontalScroll, load, test, useFixture } from "./support";

for (const viewport of [SMALL_PHONE, PHONE]) {
  const size = `${viewport.width} × ${viewport.height}`;
  for (const path of ROUTES) {
    test(`${path} · ${size}: no horizontal scroll, and the bottom bar holds the five tabs`, WEBKIT, async ({ page, request }) => {
      await useFixture(request, "in-band");
      await page.setViewportSize(viewport);
      await load(page, path);
      await expectNoHorizontalScroll(page);
      await expect(bottomBar(page).getByRole("link")).toHaveText([/^Now/, /^Water/, /^Graphs/, /^Activity/, /^More/]);
    });
  }
  for (const { path, fixture, charts } of SETTING_DRAWERS) {
    test(`${path} under ${fixture} · ${size}: the drawer's charts fit, with no horizontal scroll`, WEBKIT, async ({ page, request }) => {
      await useFixture(request, fixture);
      await page.setViewportSize(viewport);
      await load(page, path);
      const body = page.locator("dialog[open] .drawer-body");
      await expect(body.locator(".plot canvas")).toHaveCount(charts);
      await expectNoHorizontalScroll(page);
      expect(await body.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(0);
    });
  }
  for (const { path, status, heading } of CARD_PAGES) {
    test(`${path} · ${size}: the card alone, with no horizontal scroll`, WEBKIT, async ({ page, request }) => {
      await useFixture(request, "in-band");
      await page.setViewportSize(viewport);
      const response = await load(page, path);
      expect(response.status()).toBe(status);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(heading);
      await expect(bottomBar(page)).toHaveCount(0);
      await expect(page.locator(".role-pill")).toHaveCount(0);
      await expect(page.locator("[data-banner]")).toHaveCount(0);
      await expectNoHorizontalScroll(page);
    });
  }
}

for (const path of [...ROUTES, ...CARD_PAGES.map((card) => card.path)]) {
  test(`${path}: with reduced motion no element has a transition or an animation`, async ({ page, request }) => {
    await useFixture(request, "in-band");
    await page.emulateMedia({ reducedMotion: "reduce" });
    await load(page, path);
    const moving = await page.evaluate(() => {
      // css-animations-2: `auto`, a computed animation-duration in some engines, is 0s for an animation on the clock.
      const still = (durations: string) => durations.split(",").every((duration) => ["0s", "auto"].includes(duration.trim()));
      const found: string[] = [];
      for (const element of document.querySelectorAll("*")) {
        for (const pseudo of [null, "::before", "::after"]) {
          const style = getComputedStyle(element, pseudo);
          if (!still(style.transitionDuration) || !still(style.animationDuration)) found.push(`${element.tagName.toLowerCase()}.${element.className}${pseudo ?? ""}`);
        }
      }
      // What computed styles cannot show: anything running now, a script's element.animate() included.
      for (const animation of document.getAnimations()) found.push(`running: ${animation.constructor.name}`);
      return found;
    });
    expect(moving).toEqual([]);
  });
}
