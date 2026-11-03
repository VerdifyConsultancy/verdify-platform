// SPDX-License-Identifier: MIT
/**
 * What every browser spec shares (#1128), after weather's web/e2e/public.spec.ts. Specs import `test` and `expect` from
 * here: `test` guards each page for the whole test, so no spec can forget. Each test selects its fixture first (one
 * fixture API holds one state, and the suite runs on one worker). No test passes by loosening the app: a CSP violation,
 * a page error or a request off localhost fails it.
 */
import { setTimeout as sleep } from "node:timers/promises";
import AxeBuilder from "@axe-core/playwright";
import { expect, test as base, type APIRequestContext, type Locator, type Page, type Response } from "@playwright/test";

export { expect };

const FIXTURE_API = "http://localhost:4174";
/** 10:42 America/Denver on Monday 2026-11-02 (MST), the screens' clock and the vitest route tests'. */
export const NOW = "2026-11-02T17:42:00.000Z";
/** The WebKit project runs only the tests that carry this: the 360 and 390 px checks and the viewer check. */
export const WEBKIT = { tag: "@webkit" };
export const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];
/** The phones of the layout, Now and Plan checks. */
export const PHONE = { width: 390, height: 844 };
export const SMALL_PHONE = { width: 360, height: 740 };
export const DESKTOP = { width: 1440, height: 900 };

// ---------------------------------------------------------------------------------------------------- the guard
type Guard = { readonly problems: string[]; axeRan: boolean; inFlight: number; busyAt: number };
type Guarded = Window & { __reportCspViolation?: (directive: string, selects: number) => void };
const guards = new WeakMap<Page, Guard>();

/**
 * Records, for the whole test and across navigations: every `securitypolicyviolation`, every page error and every
 * request to a host but localhost. Two `style-src-attr` reports are not the app's and are left out. WebKit creates one
 * forbidden inline style inside a native `<select>`'s user-agent shadow tree (weather's public.spec.ts: upstream 327212,
 * PW 43497), which is safe to allow there because Chromium walks the same pages with no such allowance. And axe's own
 * probes add inline styles once it has started, so `axe()` checks the page first.
 */
export async function guardPage(page: Page, browserName: string): Promise<Guard> {
  const guard: Guard = { problems: [], axeRan: false, inFlight: 0, busyAt: 0 };
  guards.set(page, guard);
  const busy = (change: number) => { guard.inFlight += change; guard.busyAt = Date.now(); };
  page.on("pageerror", (error) => guard.problems.push(`page error: ${error.message}`));
  page.on("request", (request) => {
    busy(1);
    const url = new URL(request.url());
    if (/^(?:https?|wss?):$/.test(url.protocol) && url.hostname !== "localhost") guard.problems.push(`request off localhost: ${request.url()}`);
  });
  page.on("requestfinished", () => busy(-1));
  page.on("requestfailed", () => busy(-1));
  await page.exposeFunction("__reportCspViolation", (directive: string, selects: number) => {
    const styleAttribute = directive === "style-src-attr";
    if (styleAttribute && (guard.axeRan || (browserName === "webkit" && selects > 0))) return;
    guard.problems.push(`CSP violation: ${directive}`);
  });
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => {
      (window as Guarded).__reportCspViolation?.(event.effectiveDirective, document.querySelectorAll("select").length);
    });
  });
  return guard;
}

function expectClean(page: Page) {
  expect(guards.get(page)?.problems ?? []).toEqual([]);
}

/** `test` with `guardPage` on before the test's first load and its findings checked after its last step. */
export const test = base.extend<{ guard: Guard }>({
  guard: [async ({ page, browserName }, use) => {
    await use(await guardPage(page, browserName));
    expectClean(page);
  }, { auto: true }],
});

// ---------------------------------------------------------------------------------------------------- the fixture API
export type FixtureOptions = { fail?: Record<string, number>; confirm?: boolean; now?: string };
export type FixtureRequest = { method: string; path: string; operationId: string | null; idempotency_key: string | null; body: unknown };

/** Selects a named fixture or a list, applied in order, through the control endpoint; it also clears the request log. */
export async function useFixture(request: APIRequestContext, name: string | readonly string[], options: FixtureOptions = {}) {
  const response = await request.post(`${FIXTURE_API}/__control`, { data: { fixture: name, ...options } });
  expect(response.ok(), await response.text()).toBe(true);
}

/**
 * `useFixture` with the fixture API and the page's `Date` both pinned to the screens' clock, so "Updated 20 s ago" and
 * "written 07:02" read exactly as the screens have them. The page's timers keep running.
 */
export async function useScreensClock(page: Page, request: APIRequestContext, name: string | readonly string[], options: FixtureOptions = {}) {
  await page.clock.setFixedTime(NOW);
  await useFixture(request, name, { now: NOW, ...options });
}

/** Every request the fixture API has had since the last control call. */
export async function fixtureRequests(request: APIRequestContext, operationId?: string): Promise<FixtureRequest[]> {
  const response = await request.get(`${FIXTURE_API}/__requests`);
  const { requests } = await response.json() as { requests: FixtureRequest[] };
  return operationId ? requests.filter((row) => row.operationId === operationId) : requests;
}

/** The writes among them: anything but a GET. */
export async function fixtureWrites(request: APIRequestContext): Promise<FixtureRequest[]> {
  return (await fixtureRequests(request)).filter((row) => row.method !== "GET");
}

// ---------------------------------------------------------------------------------------------------- pages
const QUIET_MS = 500;
const QUIET_WITHIN_MS = 5_000;

/**
 * Navigates, then waits until the page has read its data and drawn with its fonts. Network idle alone cannot say so: it
 * fires once per load, 500 ms after the last asset, and on the CI pod hydration can outlast that, so a check would see
 * the skeleton and pass on nothing. A page with the shell therefore also waits for API-02's first answer (an SCR-09 card
 * has no shell and reads nothing), then for 500 ms with no request in flight (a poll's next tick is 15 s away). A page
 * that stays busy past 5 s goes on to its checks, which say what they found.
 */
async function arrive(page: Page, what: string, navigate: () => Promise<Response | null>): Promise<Response> {
  const shellRead = page.waitForResponse((answer) => new URL(answer.url()).pathname === "/api/v1/now").catch(() => null);
  const guard = guards.get(page);
  if (guard) guard.inFlight = 0;
  const response = await navigate();
  if (!response) throw new Error(`No response for ${what}`);
  await page.waitForLoadState("networkidle");
  if (response.ok()) await shellRead;
  const giveUp = Date.now() + QUIET_WITHIN_MS;
  while (guard && Date.now() < giveUp && (guard.inFlight > 0 || Date.now() - guard.busyAt < QUIET_MS)) await sleep(50);
  await page.evaluate(async () => { await document.fonts.ready; });
  return response;
}

/** Loads `path`, as `arrive` says. */
export const load = (page: Page, path: string) => arrive(page, path, () => page.goto(path));
/** Reloads the page, as `arrive` says, for a check that counts what the new load read. */
export const reload = (page: Page) => arrive(page, "the reload", () => page.reload());

/**
 * Moves the page's timers on, a poll at a time, until `check` passes, so a test waits for no real poll. The clock fires
 * each due timer at most once per step.
 */
export async function untilPolled(page: Page, check: () => Promise<void>, stepMs = 5_000) {
  await expect(async () => {
    await page.clock.fastForward(stepMs);
    await check();
  }).toPass({ timeout: 15_000 });
}

/** WCAG 2.2 AA with every rule on, zero violations. The app is checked for CSP findings first, before axe touches it. */
export async function axe(page: Page) {
  expectClean(page);
  const guard = guards.get(page);
  if (guard) guard.axeRan = true;
  const { violations } = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  expect(violations.map(({ id, help, nodes }) => ({ id, help, targets: nodes.map((node) => node.target.join(" ")) }))).toEqual([]);
}

export async function expectNoHorizontalScroll(page: Page) {
  const { scrollWidth, innerWidth, reaching } = await page.evaluate(() => {
    const widest = document.documentElement.scrollWidth;
    const past = widest <= window.innerWidth ? [] : [...document.querySelectorAll("body *")].filter((element) => Math.ceil(element.getBoundingClientRect().right) >= widest);
    return { scrollWidth: widest, innerWidth: window.innerWidth, reaching: [...new Set(past.map((element) => `${element.tagName.toLowerCase()}.${String(element.className)}`))] };
  });
  // CI's traces leave with its pod, so the failure names what reaches the page's right edge.
  expect(scrollWidth, `what reaches the right edge: ${reaching.join(", ")}`).toBeLessThanOrEqual(innerWidth);
}

/** The phone's fixed bottom bar. */
export const bottomBar = (page: Page) => page.getByRole("navigation", { name: "Sections, phone" });

/** `target` ends above the bottom bar's top edge, in the first screen of the page. */
export async function expectAboveTheFold(page: Page, target: Locator) {
  const [box, bar] = await Promise.all([target.boundingBox(), bottomBar(page).boundingBox()]);
  if (!box || !bar) throw new Error("Nothing to measure: the target or the bottom bar is not rendered");
  expect(box.y + box.height).toBeLessThanOrEqual(bar.y);
}

/** The polite toasts' lines, each toast's paragraphs in order. */
export const toastLines = (page: Page) => page.locator(".toasts [role=status] .toast-body p");
/** What the Stop result says, with the irrigation firmware and before it. */
export const STOPPED = "Stopped every running valve.";
export const CAN_STILL_START = "VPD misting, fog and the next scheduled run can still start on their own; to keep a line off, disable it.";
export const CAN_STILL_START_NO_ENGINE = "VPD misting and fog can still start on their own.";

// ---------------------------------------------------------------------------------------------------- roles
/** Screens §1.3's write controls by accessible name: "Run…" covers each Run, "Stop" Stop all water and a line's Stop, and SCR-04's New schedule reads "+ New schedule". */
export const WRITE_CONTROLS = /^\+?\s*(?:Edit|Save|Release|Run|Stop|Pause|Resume|New schedule|Clear fault|Acknowledge|Resolve|Fans|Humidify|Vent bypass|Restart)\b/;

/** Each write control on the page by role and name, plus any switch. `includeHidden` also finds one rendered but hidden. */
export function writeControls(page: Page, { includeHidden = false }: { includeHidden?: boolean } = {}): Locator {
  return page.getByRole("button", { name: WRITE_CONTROLS, includeHidden })
    .or(page.getByRole("link", { name: WRITE_CONTROLS, includeHidden }))
    .or(page.getByRole("switch", { includeHidden }));
}

/** The pill the stylesheet uppercases: read as the eye reads it. */
export async function expectRolePill(page: Page, reads: "OPERATOR" | "VIEWER") {
  await expect(page.locator(".role-pill")).toHaveText(reads, { useInnerText: true });
}

/** Text as the eye reads it, the stylesheet's uppercasing included, without the page's inline scripts. */
export async function expectNoText(page: Page, text: string | RegExp) {
  await expect(page.locator("body")).not.toContainText(text, { useInnerText: true });
}
