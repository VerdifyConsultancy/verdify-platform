import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { createServer } from "node:http";
import { access, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(ROOT, "dist");
const QUALITY_FIXTURE = path.join(ROOT, "tests", "fixtures", "quality");
const MARKETING_CONTRACT = JSON.parse(
  await readFile(path.join(QUALITY_FIXTURE, "marketing-page-visual-contract.json"), "utf8"),
);
const shellReady = JSON.parse(await readFile(path.join(
  ROOT,
  ".generated",
  "site-shell-root",
  ".site-shell-ready.json",
), "utf8"));
let installedMarketingContract = null;
try {
  installedMarketingContract = JSON.parse(await readFile(path.join(
    ROOT,
    ".generated",
    "site-shell-root",
    "vendor",
    "verdify-site-shell",
    "contract",
    "page-primitives-visual.json",
  ), "utf8"));
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
const securityHeaders = await readFile(path.join(ROOT, "nginx", "security-headers.inc"), "utf8");
const CSP = securityHeaders.match(/Content-Security-Policy "([^"]+)"/)?.[1];
if (!CSP) throw new Error("runtime CSP is missing");

const contentTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "application/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".pagefind", "application/octet-stream"],
  [".pf_fragment", "application/octet-stream"],
  [".pf_index", "application/octet-stream"],
  [".pf_meta", "application/octet-stream"],
  [".svg", "image/svg+xml"],
  [".wasm", "application/wasm"],
  [".webp", "image/webp"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
]);

const routes = [
  { label: "home", path: "/", family: "home" },
  { label: "planner", path: "/plans/2026-07-12", family: "plan" },
  { label: "forecast", path: "/data/forecast", family: "forecast" },
  { label: "archive", path: "/data/plans", family: "archive" },
  { label: "evidence", path: "/start/evidence", family: "evidence" },
  { label: "contact", path: "/start/contact", family: "contact" },
  { label: "media", path: "/greenhouse", family: "article" },
];
const viewports = MARKETING_CONTRACT.viewports.filter(({ name }) => name !== "tablet");

async function resolveRequest(pathname) {
  const decoded = decodeURIComponent(pathname);
  if (decoded.includes("\0") || decoded.split("/").includes("..")) return null;
  const relative = decoded.replace(/^\/+/, "");
  const candidates = path.extname(relative)
    ? [relative]
    : relative
      ? [`${relative}.html`, `${relative}/index.html`]
      : ["index.html"];
  for (const candidate of candidates) {
    const absolute = path.resolve(DIST, candidate);
    if (!absolute.startsWith(`${DIST}${path.sep}`) && absolute !== DIST) continue;
    try {
      await access(absolute);
      return absolute;
    } catch {
      // Try the next canonical static route shape.
    }
  }
  return null;
}

let server;
let origin;

test.beforeAll(async () => {
  server = createServer(async (request, response) => {
    const target = await resolveRequest(new URL(request.url, "http://localhost").pathname);
    response.setHeader("Content-Security-Policy", CSP);
    response.setHeader("X-Content-Type-Options", "nosniff");
    if (!target) {
      response.statusCode = 404;
      response.end("not found");
      return;
    }
    response.setHeader("Content-Type", contentTypes.get(path.extname(target)) ?? "application/octet-stream");
    if (request.method === "HEAD") {
      response.end();
      return;
    }
    response.end(await readFile(target));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  origin = `http://127.0.0.1:${address.port}`;
});

test.afterAll(async () => {
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
});

function observeFailures(page) {
  const failures = [];
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => failures.push(`pageerror: ${error.message}`));
  page.on("requestfailed", (request) => failures.push(`request: ${request.url()} (${request.failure()?.errorText})`));
  page.on("response", (response) => {
    if (response.status() >= 400) failures.push(`response: ${response.status()} ${response.url()}`);
  });
  return failures;
}

async function installPerformanceObservers(context) {
  await context.addInitScript(() => {
    window.__verdifyQuality = { cls: 0, lcp: 0, longTaskCount: 0, longTaskDuration: 0 };
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) window.__verdifyQuality.lcp = Math.max(window.__verdifyQuality.lcp, entry.startTime);
      }).observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!entry.hadRecentInput) window.__verdifyQuality.cls += entry.value;
        }
      }).observe({ type: "layout-shift", buffered: true });
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          window.__verdifyQuality.longTaskCount += 1;
          window.__verdifyQuality.longTaskDuration += entry.duration;
        }
      }).observe({ type: "longtask", buffered: true });
    } catch {
      // The metric assertion below still covers timing, bytes, and DOM size if
      // an older browser omits one optional observer entry type.
    }
  });
}

async function visit(page, route) {
  const response = await page.goto(`${origin}${route}`, { waitUntil: "load" });
  expect(response?.status()).toBe(200);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(80);
}

async function performanceMetrics(page) {
  return page.evaluate(() => {
    const navigation = performance.getEntriesByType("navigation")[0];
    const resources = performance.getEntriesByType("resource");
    const firstContentfulPaint = performance.getEntriesByName("first-contentful-paint")[0]?.startTime ?? 0;
    return {
      cls: window.__verdifyQuality?.cls ?? 0,
      domContentLoaded: navigation?.domContentLoadedEventEnd ?? 0,
      domNodes: document.querySelectorAll("*").length,
      firstContentfulPaint,
      largestContentfulPaint: window.__verdifyQuality?.lcp ?? 0,
      load: navigation?.loadEventEnd ?? 0,
      longTaskCount: window.__verdifyQuality?.longTaskCount ?? 0,
      longTaskDuration: window.__verdifyQuality?.longTaskDuration ?? 0,
      scriptCount: resources.filter((entry) => entry.initiatorType === "script").length,
      totalDecodedBytes: (navigation?.decodedBodySize ?? 0)
        + resources.reduce((total, entry) => total + (entry.decodedBodySize ?? 0), 0),
    };
  });
}

function expectWithinPerformanceBudget(metrics, { domNodes = 1_500, longTaskDuration = 150 } = {}) {
  expect(metrics.firstContentfulPaint, "FCP must be observed").toBeGreaterThan(0);
  expect(metrics.largestContentfulPaint, "LCP must be observed").toBeGreaterThan(0);
  expect(metrics.domContentLoaded, "DOMContentLoaded must stay below 2 seconds").toBeLessThan(2_000);
  expect(metrics.load, "load must stay below 2.5 seconds").toBeLessThan(2_500);
  expect(metrics.firstContentfulPaint, "FCP must stay below 1.5 seconds").toBeLessThan(1_500);
  expect(metrics.largestContentfulPaint, "LCP must stay below 2.5 seconds").toBeLessThan(2_500);
  expect(metrics.cls, "CLS must stay within the good threshold").toBeLessThanOrEqual(0.1);
  expect(metrics.longTaskDuration, `long-task time must stay below ${longTaskDuration}ms`).toBeLessThan(longTaskDuration);
  expect(metrics.domNodes, `representative pages must stay below ${domNodes} DOM nodes`).toBeLessThan(domNodes);
  expect(metrics.totalDecodedBytes, "first-load decoded bytes must stay below 1.5MiB").toBeLessThan(1_572_864);
  expect(metrics.scriptCount, "static evidence pages must keep JS requests bounded").toBeLessThanOrEqual(4);
}

test("@quality Marketing visual contract is the Lab quality source", async ({ browser }) => {
  if (installedMarketingContract) expect(installedMarketingContract).toEqual(MARKETING_CONTRACT);
  else expect(shellReady.contractVersion, "only the predecessor shell may lack the page visual contract").toBe("1.0.0");
  expect(MARKETING_CONTRACT.contractVersion).toBe("1.1.0");
  expect(MARKETING_CONTRACT.viewports.map(({ width }) => width)).toEqual([390, 768, 1440]);
  expect(MARKETING_CONTRACT.assertions).toEqual([
    "body-has-no-horizontal-overflow",
    "page-defaults-to-light",
    "hero-copy-precedes-media-on-mobile",
    "content-width-is-bounded",
    "media-reserves-intrinsic-space",
    "form-controls-have-visible-boundaries",
    "focus-visible-is-three-pixel-harvest-gold",
    "lightbox-is-native-dialog",
    "lightbox-restores-opener-focus",
    "reduced-motion-disables-nonessential-transition",
  ]);

  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  await visit(page, "/start/evidence");
  const sharedStyles = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    return {
      field: root.getPropertyValue("--color-field").trim(),
      font: getComputedStyle(document.body).fontFamily,
      forest: root.getPropertyValue("--color-forest").trim(),
      ink: root.getPropertyValue("--color-ink").trim(),
      line: root.getPropertyValue("--color-line").trim(),
      mint: root.getPropertyValue("--color-mint").trim(),
    };
  });
  expect(sharedStyles).toEqual({
    field: MARKETING_CONTRACT.tokens.field,
    font: expect.stringContaining("IBM Plex Sans Variable"),
    forest: MARKETING_CONTRACT.tokens.forest,
    ink: MARKETING_CONTRACT.tokens.ink,
    line: MARKETING_CONTRACT.tokens.line,
    mint: MARKETING_CONTRACT.tokens.mint,
  });
  await expect(page.locator("header")).toHaveCount(1);
  await expect(page.locator("body > footer")).toHaveCount(1);
  await expect(page.locator('header img[src="/assets/verdify-lab-lockup.svg"]')).toBeVisible();
  await context.close();
});

for (const route of routes) {
  test(`@quality ${route.label} is accessible, responsive, light-default, and within budget`, async ({ browser }) => {
    for (const viewport of viewports) {
      const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        reducedMotion: "reduce",
      });
      await installPerformanceObservers(context);
      const page = await context.newPage();
      const failures = observeFailures(page);
      await visit(page, route.path);

      await expect(page.locator("body")).toHaveAttribute("data-page-family", route.family);
      await expect(page.locator("main h1")).toHaveCount(1);
      expect(await page.evaluate(() => document.documentElement.dataset.theme ?? "light")).toBe("light");
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
      expect(await page.evaluate(() => document.body.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
      expect(await page.locator(".lab-frame").evaluate((element) => element.getBoundingClientRect().width)).toBeLessThanOrEqual(viewport.width);

      const darkOffenders = await page.locator(".lab-frame").evaluate((frame) => {
        const allowed = "pre, .home-launch-video, .verdify-contact-actions button";
        const channel = (value) => {
          const normalized = value / 255;
          return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
        };
        return [...frame.querySelectorAll("*")].flatMap((element) => {
          if (element.matches(allowed) || element.closest(allowed)) return [];
          if (!element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return [];
          const color = getComputedStyle(element).backgroundColor;
          const match = /^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/.exec(color);
          if (!match || Number(match[4] ?? 1) < 0.5) return [];
          const luminance = 0.2126 * channel(Number(match[1]))
            + 0.7152 * channel(Number(match[2]))
            + 0.0722 * channel(Number(match[3]));
          return luminance < 0.16 ? [`${element.tagName.toLowerCase()}.${element.className}`] : [];
        });
      });
      expect(darkOffenders, "Lab content uses dark surfaces only where explicitly allowed").toEqual([]);

      const performanceBudget = route.family === "archive"
        ? { domNodes: 2_000 }
        : route.family === "plan"
          ? { domNodes: 2_500, longTaskDuration: 250 }
          : undefined;
      expectWithinPerformanceBudget(await performanceMetrics(page), performanceBudget);
      const accessibility = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      expect(
        accessibility.violations,
        `${route.path} at ${viewport.width}px must have no WCAG A/AA violations`,
      ).toEqual([]);
      expect(failures).toEqual([]);
      await context.close();
    }
  });
}

test("@quality keyboard skip link and mobile navigation preserve focus", async ({ browser }) => {
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  const desktopPage = await desktop.newPage();
  await visit(desktopPage, "/start/evidence");
  await desktopPage.keyboard.press("Tab");
  await expect(desktopPage.locator(".skip-link")).toBeFocused();
  await desktopPage.keyboard.press("Enter");
  await expect(desktopPage.locator("#lab-content")).toBeFocused();
  await desktop.close();

  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
  const mobilePage = await mobile.newPage();
  await visit(mobilePage, "/data/forecast");
  const toggle = mobilePage.getByRole("button", { name: "Lab index" });
  await toggle.focus();
  await mobilePage.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(mobilePage.locator("#lab-navigation-panel")).toBeVisible();
  await expect(mobilePage.locator("#lab-search")).toBeFocused();
  await mobilePage.keyboard.press("Escape");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();
  await mobile.close();
});

test("@quality planner, archive, evidence, and contact templates keep semantic interactions", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
  const page = await context.newPage();

  await visit(page, "/plans/2026-07-12");
  expect(await page.locator("details.technical-section").count()).toBeGreaterThanOrEqual(2);
  await expect(page.locator("details.technical-section").first()).toHaveAttribute("open", "");
  const secondSummary = page.locator("details.technical-section summary").nth(1);
  await secondSummary.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("details.technical-section").nth(1)).toHaveAttribute("open", "");

  await visit(page, "/data/plans");
  await expect(page.locator('.lab-table-scroll[role="region"]')).toHaveAttribute("tabindex", "0");
  const archiveFilter = page.getByRole("searchbox", { name: "Filter planning archive" });
  const newestArchiveDate = (await page.locator("tbody tr").first().locator("td").first().textContent())?.trim();
  expect(newestArchiveDate).toBeTruthy();
  await archiveFilter.fill(newestArchiveDate);
  await expect(page.locator(".archive-tools output")).toHaveText("1 record");
  await expect(page.locator("tbody tr:visible")).toHaveCount(1);

  await visit(page, "/start/evidence");
  if (await page.locator(".grafana-evidence").count() === 0) await visit(page, "/data/operations");
  expect(await page.locator(".grafana-evidence").count()).toBeGreaterThan(0);
  await expect(page.locator(".grafana-evidence__status").first()).toContainText("graph fallback");

  await visit(page, "/start/contact");
  const name = page.getByRole("textbox", { name: "Name" });
  const message = page.getByRole("textbox", { name: "Message" });
  const submit = page.getByRole("button", { name: "Send message" });
  await expect(name).toBeVisible();
  await expect(message).toBeVisible();
  await name.focus();
  expect(await name.evaluate((element) => getComputedStyle(element).boxShadow)).not.toBe("none");
  expect((await submit.boundingBox()).height).toBeGreaterThanOrEqual(44);
  await context.close();
});

test("@quality media reserves responsive space and the native lightbox is keyboard complete", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  const failures = observeFailures(page);
  await visit(page, "/greenhouse");

  const images = page.locator(".media-grid img");
  expect(await images.count()).toBeGreaterThanOrEqual(2);
  for (const image of await images.all()) {
    await expect(image).toHaveAttribute("width", /^\d+$/);
    await expect(image).toHaveAttribute("height", /^\d+$/);
    await expect(image).toHaveAttribute("srcset", /\d+w/);
    await expect(image).toHaveAttribute("sizes", /vw/);
    const dimensions = await image.evaluate((element) => ({
      height: Number(element.getAttribute("height")),
      naturalHeight: element.naturalHeight,
      naturalWidth: element.naturalWidth,
      width: Number(element.getAttribute("width")),
    }));
    expect(dimensions.naturalWidth).toBeGreaterThan(0);
    expect(dimensions.naturalHeight).toBeGreaterThan(0);
    expect(dimensions.naturalWidth / dimensions.naturalHeight).toBeCloseTo(dimensions.width / dimensions.height, 1);
  }

  const opener = page.locator(".media-grid a").first();
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.locator("dialog.media-lightbox");
  await expect(dialog).toHaveAttribute("open", "");
  await expect(page.getByRole("button", { name: "Close image viewer" })).toBeFocused();
  await expect.poll(() => dialog.locator("[data-lightbox-image]").evaluate(
    (image) => image.complete && image.naturalWidth > 0,
  )).toBe(true);
  const firstOriginal = await dialog.locator("[data-lightbox-original]").getAttribute("href");
  await page.keyboard.press("ArrowRight");
  await expect(dialog.locator("[data-lightbox-original]")).not.toHaveAttribute("href", firstOriginal);
  await expect.poll(() => dialog.locator("[data-lightbox-image]").evaluate(
    (image) => image.complete && image.naturalWidth > 0,
  )).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).not.toHaveAttribute("open", "");
  await expect(opener).toBeFocused();
  expect(failures).toEqual([]);
  await context.close();
});

test("@quality viewport scrolls and the lightbox scroll lock applies then releases", async ({ browser }) => {
  // Regression guard for the P0 no-scroll defect (2026-07-13): the shared
  // shell's `.site-page { overflow: clip }` sits on <body> in this layout, and
  // body overflow propagates to the viewport — `clip` removed the viewport
  // scroll container entirely, so nothing on lab-stage could scroll.
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, reducedMotion: "reduce" });
  const page = await context.newPage();

  for (const route of ["/", "/greenhouse"]) {
    await visit(page, route);
    const scroller = await page.evaluate(() => ({
      bodyOverflowY: getComputedStyle(document.body).overflowY,
      clientHeight: document.scrollingElement.clientHeight,
      scrollHeight: document.scrollingElement.scrollHeight,
    }));
    expect(scroller.bodyOverflowY, `${route} body must not clip the viewport scroll container`).not.toBe("clip");
    expect(scroller.scrollHeight, `${route} must overflow the viewport`).toBeGreaterThan(scroller.clientHeight);

    await page.evaluate(() => window.scrollTo(0, 160));
    expect(await page.evaluate(() => window.scrollY), `${route} must scroll programmatically`).toBe(160);
    await page.evaluate(() => window.scrollTo(0, 0));

    await page.keyboard.press("End");
    await expect
      .poll(() => page.evaluate(() => window.scrollY), { message: `${route} must scroll from the keyboard` })
      .toBeGreaterThan(0);
    await page.evaluate(() => window.scrollTo(0, 0));
  }

  // The lightbox is a native modal dialog; showModal() alone does not stop the
  // background document from scrolling. The lock must hold while it is open
  // and release when it closes.
  const opener = page.locator(".media-grid a").first();
  await opener.click();
  const dialog = page.locator("dialog.media-lightbox");
  await expect(dialog).toHaveAttribute("open", "");
  expect(await page.evaluate(() => getComputedStyle(document.body).overflowY)).toBe("hidden");
  const lockedY = await page.evaluate(() => window.scrollY);
  await page.mouse.wheel(0, 480);
  await page.waitForTimeout(250);
  expect(
    await page.evaluate(() => window.scrollY),
    "wheel must not scroll the page behind the open lightbox",
  ).toBe(lockedY);

  await page.keyboard.press("Escape");
  await expect(dialog).not.toHaveAttribute("open", "");
  expect(await page.evaluate(() => getComputedStyle(document.body).overflowY)).not.toBe("hidden");
  await page.mouse.wheel(0, 480);
  await expect
    .poll(() => page.evaluate(() => window.scrollY), { message: "scrolling must release after the lightbox closes" })
    .toBeGreaterThan(lockedY);
  await context.close();
});

test("@quality reduced motion collapses nonessential transitions", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  await visit(page, "/greenhouse");
  const transitionDuration = await page.locator(".media-figure img").first()
    .evaluate((element) => getComputedStyle(element).transitionDuration);
  expect(Number.parseFloat(transitionDuration) || 0).toBeLessThanOrEqual(0.01);
  await context.close();
});
