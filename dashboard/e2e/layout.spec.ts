// SPDX-License-Identifier: MIT
// The caretaker's phone: no page scrolls sideways at 360 or 390 px and the bottom bar holds every section tab; with reduced
// motion emulated, nothing moves. And the shell of redesign §2 at 1440 px and on phones: the seven sections (on one row
// from 880 px), Control owning its views, the account menu, the fault row that never scrolls away, the warn notices in
// flow, the count pill's severity, the redirects and the end of /more.
import { CARD_PAGES, REDIRECTS, ROUTES, SETTING_DRAWERS } from "./routes";
import {
  DESKTOP, PHONE, SECTION_LINKS, SMALL_PHONE, WEBKIT, accountMenu, bottomBar, expect, expectNoHorizontalScroll, expectRolePill, load, sectionsNav, test,
  useFixture,
} from "./support";

for (const viewport of [SMALL_PHONE, PHONE]) {
  const size = `${viewport.width} × ${viewport.height}`;
  for (const path of ROUTES) {
    test(`${path} · ${size}: no horizontal scroll, and the bottom bar holds every section tab`, WEBKIT, async ({ page, request }) => {
      await useFixture(request, "in-band");
      await page.setViewportSize(viewport);
      await load(page, path);
      await expectNoHorizontalScroll(page);
      await expect(bottomBar(page).getByRole("link")).toHaveText(SECTION_LINKS);
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

// ---------------------------------------------------------------------------------------------------- the shell
test("1440 px: the header lists the seven sections; Control opens Settings and is current on Settings and Equipment, Planner on Plan, History and Experiments", async ({ page, request }) => {
  await useFixture(request, "alerts-merged");
  await page.setViewportSize(DESKTOP);
  await load(page, "/");
  await expect(sectionsNav(page).getByRole("link")).toHaveText(SECTION_LINKS);
  await expect(sectionsNav(page).getByRole("link", { name: /^Control/ })).toHaveAttribute("href", "/settings");
  for (const [path, section] of [["/settings", "Control"], ["/equipment", "Control"], ["/plan", "Planner"], ["/planner/history", "Planner"], ["/planner/experiments", "Planner"]]) {
    await load(page, path);
    await expect(sectionsNav(page).locator("[aria-current=page]"), path).toHaveText(section);
    await expect(page.getByRole("navigation", { name: `${section} views` }).locator("[aria-current=page]"), path).toHaveAttribute("href", path);
  }
  // The count pill: API-02 alerts.open, filled by the worst open severity.
  await expect(sectionsNav(page).locator(".nav-count")).toHaveText("2");
  await expect(sectionsNav(page).locator(".nav-count")).toHaveAttribute("data-severity", /^(critical|warning|info)$/);
  // Nothing links to /more any more.
  await expect(page.locator("a[href='/more']")).toHaveCount(0);
});

for (const width of [880, 1100]) {
  test(`${width} px: the seven section links fit the header's one row, clear of the lockup and the account button`, async ({ page, request }) => {
    await useFixture(request, "alerts-merged");
    await page.setViewportSize({ width, height: 900 });
    await load(page, "/activity");
    await expect(sectionsNav(page).getByRole("link")).toHaveText(SECTION_LINKS);
    await expect(sectionsNav(page).locator(".nav-count")).toHaveText("2");
    const boxes = async (selector: string) => Promise.all((await page.locator(selector).all()).map(async (element) => (await element.boundingBox())!));
    const [brand] = await boxes(".site-header > .shell > .brand");
    const [account] = await boxes(".site-header > .shell > .account-menu");
    const links = await boxes(".site-header .primary-nav a");
    expect(links).toHaveLength(7);
    for (const link of links) {
      // One row: every link shares the lockup's row and sits between it and the account button.
      expect(Math.abs(link.y + link.height / 2 - (brand.y + brand.height / 2))).toBeLessThanOrEqual(2);
      expect(link.x).toBeGreaterThanOrEqual(brand.x + brand.width);
      expect(link.x + link.width).toBeLessThanOrEqual(account.x);
    }
    expect(Math.abs(account.y + account.height / 2 - (brand.y + brand.height / 2))).toBeLessThanOrEqual(2);
    const nav = await page.locator(".site-header .primary-nav").evaluate((element) => ({ scroll: element.scrollWidth, client: element.clientWidth }));
    expect(nav.scroll).toBeLessThanOrEqual(nav.client);
    await expectNoHorizontalScroll(page);
  });
}

test("1440 px: every page shares the header's one centred container, so switching views never moves the page's edges; a short page's footer reaches the bottom", async ({ page, request }) => {
  await useFixture(request, "in-band");
  await page.setViewportSize(DESKTOP);
  // Graphs and Water → Schedules were the wide pages; Activity → Alerts is a short one.
  for (const path of ["/water/schedules", "/graphs", "/activity"]) {
    await load(page, path);
    const [header, main, footer] = await Promise.all([".site-header > .shell", "main#main-content", ".site-footer > .shell"].map((selector) => page.locator(selector).boundingBox()));
    expect([main!.x, main!.width], path).toEqual([header!.x, header!.width]);
    expect([footer!.x, footer!.width], path).toEqual([header!.x, header!.width]);
  }
  const footer = await page.locator(".site-footer").boundingBox();
  expect(Math.round(footer!.y + footer!.height)).toBeGreaterThanOrEqual(DESKTOP.height);
});

for (const viewport of [SMALL_PHONE, PHONE]) {
  test(`${viewport.width} px: each phone tab is an icon over its label, at least 44 px, and the header keeps the role pill`, WEBKIT, async ({ page, request }) => {
    await useFixture(request, "in-band");
    await page.setViewportSize(viewport);
    await load(page, "/");
    const tabs = bottomBar(page).getByRole("link");
    await expect(tabs).toHaveCount(SECTION_LINKS.length);
    for (let index = 0; index < SECTION_LINKS.length; index += 1) {
      const box = await tabs.nth(index).boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
      await expect(tabs.nth(index).locator("svg.tab-icon")).toHaveCount(1);
    }
    await expectRolePill(page, "OPERATOR");
    await expect(sectionsNav(page)).toBeHidden();
  });
}

for (const viewport of [SMALL_PHONE, DESKTOP]) {
  test(`${viewport.width} px: the account menu reads OPERATOR, opens to the name, the role and Sign out, a POST to /auth/logout`, async ({ page, request }) => {
    await useFixture(request, "in-band");
    await page.setViewportSize(viewport);
    await load(page, "/water");
    const account = accountMenu(page);
    await expectRolePill(page, "OPERATOR");
    await expect(account.panel).toBeHidden();
    await account.open();
    await expect(account.panel).toContainText("Signed in as Jason (jason)");
    await expect(account.panel).toContainText("Operator: can change anything");
    await expect(account.signOut).toBeVisible();
    await expect(account.menu.locator("form")).toHaveAttribute("action", "/auth/logout");
    await expect(account.menu.locator("form")).toHaveAttribute("method", "post");
    // Escape closes it and puts focus back on its button.
    await page.keyboard.press("Escape");
    await expect(account.panel).toBeHidden();
    await expect(account.button).toBeFocused();
    await expectNoHorizontalScroll(page);
  });
}

test("the fault banner is a row of the sticky header and stays in view after scrolling 2,000 px; warn banners sit in flow", async ({ page, request }) => {
  await useFixture(request, ["controller-not-reporting", "writes-off"]);
  await page.setViewportSize(DESKTOP);
  await load(page, "/settings");
  const fault = page.locator("header.site-header [data-banner=fault]");
  await expect(fault).toHaveText(/^Controller not reporting since/);
  await expect(page.locator("main [data-banner=warn]")).toHaveText(["Changes are switched off; reading still works"]);
  await page.evaluate(() => window.scrollTo(0, 2000));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(2000);
  const box = await fault.boundingBox();
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(200);
  await expect(page.locator("main [data-banner=warn]")).not.toBeInViewport();
});

/** The page each redirect lands on, by its h1. */
const REDIRECT_TITLES: Readonly<Record<string, string>> = { "/graphs/statistics": "Statistics", "/plan": "Plan" };

test("the old paths: /graphs/scorecard, /planner and a log-filtered /activity redirect with their query, ?alert= stays on Alerts, and /more is not found", async ({ page, request }) => {
  await useFixture(request, "in-band");
  for (const { path, to } of REDIRECTS) {
    await load(page, `${path}?x=1`);
    await expect(page).toHaveURL(new RegExp(`${to.replace(/\//g, "\\/")}\\?x=1$`));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(REDIRECT_TITLES[to]);
  }
  await load(page, "/activity?target=L01");
  await expect(page).toHaveURL(/\/activity\/log\?target=L01$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Activity log");
  await load(page, "/activity?alert=7");
  await expect(page).toHaveURL(/\/activity\?alert=7$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Alerts");
  const response = await load(page, "/more");
  expect(response.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Page not found");
});
