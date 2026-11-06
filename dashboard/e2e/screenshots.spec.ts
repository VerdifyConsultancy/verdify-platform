// SPDX-License-Identifier: MIT
/**
 * SCRATCH, do not merge: visual QA screenshots for sprint W6 lane A (#1337 #1338 #1339 #1346 #1347 #1349 #1354 #1355 #1356 #1322). Renders each screen at 1440 × 900 (the full page from
 * the top, cut at MAX_FULL px) and at 390 × 844 (the viewport) against the fixture API, as the operator at the screens'
 * clock, then a few telling states, and prints each capture to stdout as a base64 JPEG between marker lines, LINE
 * characters a line: the in-cluster CI is the only place a browser runs, and its log is the way out. Nothing here
 * asserts, so a page that does not settle is still captured and its notes say what was pending; the guard is read, not
 * enforced, so its findings are notes too. Every capture shares one byte budget (about 6 MB of base64), kept in a ledger
 * file for the run, because a worker that restarts loses its module state.
 */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { test, type APIRequestContext, type Page } from "@playwright/test";
import { DESKTOP, NOW, PHONE, guardPage, load, useFixture, useScreensClock } from "./support";

const BEGIN = "@@VERDIFY-SCREENSHOT-BEGIN-a7c3@@";
const END = "@@VERDIFY-SCREENSHOT-END-a7c3@@";
const NOTE = "@@VERDIFY-SCREENSHOT-NOTE-a7c3@@";
/** Base64 characters a line: 8 kB. */
const LINE = 8 * 1024;
/** Raw JPEG bytes for every capture together: 5.9 MB once base64-encoded. */
const BUDGET = Math.floor((5_900_000 / 4) * 3);
const QUALITIES = [55, 45, 38, 30] as const;
/** A desktop capture keeps at most this much of the page, from its top. */
const MAX_FULL = 7_200;
const LOAD_LIMIT_MS = 30_000;
const WAIT_MS = 10_000;
/** One ledger per run: every worker of a run, a restarted one too, is a child of the same runner. */
const LEDGER = join(tmpdir(), `verdify-w6a-screens-${process.ppid}.json`);

type Size = "desktop" | "phone";
type Screen = {
  name: string; route: string; path: string; state: string; fixture: string | readonly string[];
  group: "screen" | "state"; dialog?: boolean; apiDown?: boolean;
};
type Shot = Screen & { size: Size; weight: number; file: string };
type Capture = { jpeg: Buffer; quality: number; width: number; height: number; pageHeight: number };
type Ledger = { spent: number; done: string[] };

const HEALTHY = "healthy (in-band), operator";
const screen = (name: string, route: string, path: string, extra: Partial<Screen> = {}): Screen => (
  { name, route, path, state: HEALTHY, fixture: "in-band", group: "screen", ...extra }
);
const SCREENS: Screen[] = [
  screen("now", "Now", "/", { fixture: ["in-band", "time-in-band"] }),
  screen("water-today", "Water · Today", "/water"),
  screen("water-schedules-day", "Water · Schedules (Day)", "/water/schedules?view=day&day=2026-11-02"),
  screen("water-schedules-week", "Water · Schedules (Week)", "/water/schedules?view=week&day=2026-11-02"),
  screen("water-history", "Water · History", "/water/history"),
  screen("graphs-climate-24h", "Graphs · Climate 24 h", "/graphs?range=24h"),
  screen("graphs-climate-72h", "Graphs · Climate 72 h", "/graphs?range=72h"),
  screen("control-equipment", "Control · Equipment", "/equipment"),
  screen("control-plan", "Control · Plan", "/plan"),
  screen("state-viewer-now", "Now as a viewer", "/", { group: "state", fixture: ["in-band", "time-in-band", "viewer-session"], state: "healthy, viewer session (#1346)" }),
  screen("state-stale-now", "Now", "/", { group: "state", fixture: ["stale-data", "time-in-band"], state: "stale data (stale-data): indoor readings 14 min old" }),
  screen("state-api-down-now", "Now", "/", { group: "state", fixture: "api-down", apiDown: true, state: "API down (api-down), after the second failed poll" }),
  screen("state-too-hot-now", "Now", "/", { group: "state", fixture: ["too-hot", "time-in-band"], state: "out of band (too-hot)" }),
  screen("state-l01-running-water", "Water · Today", "/water", { group: "state", fixture: "l01-running", state: "L01 Wall drip running (l01-running)" }),
];
const SHOTS: Shot[] = SCREENS
  .flatMap((entry) => (["desktop", "phone"] as const).map((size) => ({ entry, size })))
  .map(({ entry, size }, index) => ({
    ...entry, size,
    // A desktop page runs several screens tall and a phone capture is one screen; the screens come before the states.
    weight: size === "phone" ? 1 : entry.group === "screen" ? 6 : 4,
    file: `${String(index + 1).padStart(2, "0")}-${entry.name}-${size}.jpg`,
  }));

const firstLine = (error: unknown) => (error instanceof Error ? error.message : String(error)).split("\n")[0];
const note = (fields: Record<string, unknown>) => process.stdout.write(`${NOTE} ${JSON.stringify(fields)}\n`);

function readLedger(): Ledger | null {
  try {
    const ledger = JSON.parse(readFileSync(LEDGER, "utf8")) as Partial<Ledger>;
    return { spent: typeof ledger.spent === "number" ? ledger.spent : 0, done: Array.isArray(ledger.done) ? ledger.done : [] };
  } catch {
    return null;
  }
}

/** Polls `check` in the page from Node every 250 ms (the page's own timers may be the fake clock's) until it holds. */
async function until(page: Page, check: () => boolean, limit = WAIT_MS): Promise<boolean> {
  const giveUp = Date.now() + limit;
  do {
    if (await page.evaluate(check).catch(() => false)) return true;
    await sleep(250);
  } while (Date.now() < giveUp);
  return false;
}

/** Selects the shot's fixture, loads its path with support.ts's `load`, and waits for its data, dialog and charts. */
async function arrive(page: Page, request: APIRequestContext, shot: Shot, notes: string[]) {
  if (shot.apiDown) {
    // As states.spec.ts does it: an installed clock, so the shell's second failed poll, 30 s on, can be fast-forwarded.
    await useFixture(request, shot.fixture, { now: NOW });
    await page.clock.install({ time: NOW });
  } else {
    await useScreensClock(page, request, shot.fixture);
  }
  const loaded = load(page, shot.path).then(() => "", (error: unknown) => `load() failed: ${firstLine(error)}`);
  const outcome = await Promise.race([loaded, sleep(LOAD_LIMIT_MS, `load() had not settled after ${LOAD_LIMIT_MS / 1000} s`)]);
  if (outcome) notes.push(outcome);
  if (shot.apiDown) {
    const banner = () => document.querySelector("[data-banner=fault]") !== null;
    for (let poll = 0; poll < 4 && !(await until(page, banner, 1_500)); poll += 1) await page.clock.fastForward(31_000);
    if (!(await until(page, banner, 5_000))) notes.push("the fault banner never showed");
  }
  if (shot.dialog && !(await until(page, () => document.querySelector("dialog[open]") !== null))) notes.push("no open dialog after 10 s");
  const quiet = () => ![...document.querySelectorAll("[aria-busy=true], .skeleton")].some((element) => element.getClientRects().length > 0);
  if (!(await until(page, quiet))) notes.push("still loading after 10 s: an aria-busy region or a skeleton shows");
  // Every uPlot wrapper (uplot-chart.tsx's div[role=img]; the gallons strip and the schedule tracks are not charts).
  const drawn = () => [...document.querySelectorAll("div[role=img]:not(.gr-gallons):not(.sch-track)")].every((element) => element.querySelector("canvas") !== null);
  if (!(await until(page, drawn))) notes.push("a chart had drawn no canvas after 10 s");
  // uPlot commits its first draw a microtask after it mounts; this also covers a late font swap.
  await sleep(600);
}

/** The shot as a JPEG within `allowance` bytes: lower quality first, then, on desktop, less of the page. */
async function capture(page: Page, shot: Shot, allowance: number, notes: string[]): Promise<Capture> {
  const full = shot.size === "desktop";
  const width = full ? DESKTOP.width : PHONE.width;
  const pageHeight = full ? await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight ?? 0)) : PHONE.height;
  let height = full ? Math.min(pageHeight, MAX_FULL) : PHONE.height;
  if (pageHeight > height) notes.push(`the page is ${pageHeight} px tall; this keeps its first ${height} px`);
  const take = async (quality: number): Promise<Capture> => {
    const jpeg = await page.screenshot({
      type: "jpeg", quality, animations: "disabled", timeout: 30_000, ...(full ? { fullPage: true, clip: { x: 0, y: 0, width, height } } : {}),
    });
    return { jpeg, quality, width, height, pageHeight };
  };
  let best = await take(QUALITIES[0]);
  for (const quality of QUALITIES.slice(1)) {
    if (best.jpeg.length <= allowance) return best;
    best = await take(quality);
  }
  if (best.jpeg.length <= allowance || !full) return best;
  const fits = Math.max(DESKTOP.height, Math.floor((height * allowance * 0.95) / best.jpeg.length));
  if (fits >= height) return best;
  height = fits;
  notes.push(`over its byte share at quality ${best.quality}, so cut to the first ${height} px`);
  return take(best.quality);
}

function emit(shot: Shot, image: Capture, notes: readonly string[]) {
  const data = image.jpeg.toString("base64");
  const lines: string[] = [];
  for (let at = 0; at < data.length; at += LINE) lines.push(data.slice(at, at + LINE));
  const meta = {
    file: shot.file, route: shot.route, path: shot.path, state: shot.state, size: shot.size,
    viewport: shot.size === "desktop" ? `${DESKTOP.width}x${DESKTOP.height}` : `${PHONE.width}x${PHONE.height}`,
    image: `${image.width}x${image.height}`, page_height: image.pageHeight, quality: image.quality, bytes: image.jpeg.length,
    sha256: createHash("sha256").update(image.jpeg).digest("hex"), lines: lines.length, notes,
  };
  process.stdout.write(`${BEGIN} ${JSON.stringify(meta)}\n${lines.join("\n")}\n${END} ${shot.file}\n`);
}

for (const shot of SHOTS) {
  test(`${shot.file}: ${shot.route} · ${shot.state}`, async ({ page, request, browserName }) => {
    test.setTimeout(180_000);
    const notes: string[] = [];
    let ledger = readLedger();
    if (!ledger) {
      ledger = { spent: 0, done: [] };
      writeFileSync(LEDGER, JSON.stringify(ledger));
      note({ plan: SHOTS.map(({ file, route, path, state }) => ({ file, route, path, state })), budget_bytes: BUDGET });
    }
    let problems: readonly string[] = [];
    try {
      const guard = await guardPage(page, browserName);
      problems = guard.problems;
      await page.setViewportSize(shot.size === "desktop" ? DESKTOP : PHONE);
      await arrive(page, request, shot, notes);
    } catch (error) {
      notes.push(`did not arrive: ${firstLine(error)}`);
    }
    notes.push(...problems.map((problem) => `guard: ${problem}`));
    const { spent, done } = readLedger() ?? ledger;
    // A shot that is emitted, skipped or fails is done: its share goes to the shots after it.
    const settle = (bytes: number) => writeFileSync(LEDGER, JSON.stringify({ spent: spent + bytes, done: [...done, shot.file] }));
    try {
      const left = SHOTS.filter((other) => other.file === shot.file || !done.includes(other.file)).reduce((sum, other) => sum + other.weight, 0);
      const allowance = Math.floor(((BUDGET - spent) * shot.weight) / Math.max(1, left));
      const image = await capture(page, shot, allowance, notes);
      if (spent + image.jpeg.length > BUDGET) {
        settle(0);
        note({ file: shot.file, skipped: `over the run's byte budget: ${image.jpeg.length} bytes with ${BUDGET - spent} left`, notes });
        return;
      }
      emit(shot, image, notes);
      settle(image.jpeg.length);
    } catch (error) {
      settle(0);
      note({ file: shot.file, failed: `no capture: ${firstLine(error)}`, notes });
    }
  });
}
