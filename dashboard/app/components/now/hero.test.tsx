// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Now's hero (redesign §5.1) over the fixtures at 10:42: the answer as the page's one heading, linked to what explains it;
// 64.8 °F with "in band" and "Updated 20 s ago" (#data, which opens All readings); three band bars with today's low and
// high, a verdict word only out of band and RH's "no band"; time in band today against yesterday; the status line; and
// the honest states: no verdict and STALE values while rows 1 to 3 or 7 hold (row 7's mode with no verdict, the bars
// dated and never live-looking), no bars or "In band today" with the API down, a null reading as "—" with its reason.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { answer, buildWorld, fixture } from "../../../e2e/fixtures/index.mjs";
import { dateShift, localDayStart } from "../../lib/format";
import { nowAnswer } from "../../lib/now-answer";
import { parseIrrigationStatus, parseNow, parseSeries, type IrrigationStatus, type Now, type Series } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { DEFAULT_SESSION, SessionProvider } from "../../lib/session";
import { renderUi, text } from "../test-render";
import { DAY_KEYS, DAY_POINTS, DAY_WINDOW_S, TIB_KEYS } from "./climate";
import { NowHero, bandLine } from "./hero";

const NOW = Date.parse("2026-11-02T17:42:00Z");
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const plain = (value: string) => value.replaceAll(String.fromCharCode(0xa0), " ");
const series = (names: string | string[], metric: readonly string[], from: string, to: string, points: number): Series => {
  const query = new URLSearchParams([...metric.map((key) => ["metric", key]), ["from", from], ["to", to], ["points", String(points)]]);
  return parseSeries(answer(buildWorld(names, { now: NOW }), { method: "GET", url: `/v1/series?${query}` }).body)!;
};
const read = <T,>(data: T | null, failed = false): PanelRead<T> => ({ data, failed, pending: data === null && !failed, retry: vi.fn() });
const dayRead = (names: string | string[]) => read(series(names, DAY_KEYS, new Date(NOW - DAY_WINDOW_S * 1000).toISOString(), new Date(NOW).toISOString(), DAY_POINTS));
const dailyRead = (names: string | string[]) => read(series(names, TIB_KEYS, localDayStart(dateShift("2026-11-02", -1)), localDayStart(dateShift("2026-11-02", 1)), 10));
function renderHero(names: string | string[], { failed = false, data = true, retry = vi.fn(), apiDown = false, path = "/" } = {}) {
  const now = data ? parseNow(fixture(names, { now: NOW })["API-02"]) : null;
  const irrigation = read(parseIrrigationStatus(fixture(names, { now: NOW })["API-IRR-01"]));
  const props = { now, answer: nowAnswer({ now, failed }), irrigation, day: dayRead(names), daily: dailyRead(names), onRetry: retry };
  const view = render(<MemoryRouter initialEntries={[path]}><SessionProvider {...DEFAULT_SESSION} apiDown={apiDown}><NowHero {...props} /></SessionProvider></MemoryRouter>);
  return { ...view, retry, props };
}
const heading = () => plain(text(screen.getByRole("heading", { level: 1 })));
const bands = () => [...document.querySelectorAll(".now-band")].map((row) => ({
  tile: row.getAttribute("data-tile"), value: plain(text(row.querySelector(".now-band-value"))), meta: plain(text(row.querySelector(".now-band-meta"))),
  bar: plain(row.querySelector("svg.bb")?.getAttribute("aria-label") ?? ""),
}));
const status = () => [...document.querySelectorAll(".now-status > li")].map((item) => plain(text(item)));
const VERDICTS = /in band|above band|below band|band unavailable/;

test("in band: All OK, 64.8 °F in band, Updated 20 s ago, the three band bars with today's range, and the status line", () => {
  renderHero(["in-band", "time-in-band"]);
  expect(heading()).toBe("All OK");
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(plain(text(document.querySelector(".hero-temp")))).toBe("64.8 °F");
  expect(text(document.querySelector(".now-hero-verdict"))).toBe("in band");
  expect(text(document.querySelector("#data"))).toBe("Updated 20 s ago");
  expect(document.querySelector("#data")?.getAttribute("data-tile")).toBe("data");
  const rows = bands();
  expect(rows.map(({ tile, value, meta }) => [tile, value, meta])).toEqual([
    ["temp", "64.8 °F", "band 62.0–70.0 °F"], ["vpd", "0.62 kPa", "band 0.45–0.80 kPa"], ["rh", "71.0%", "no band"],
  ]);
  // Each bar names its numbers, today's low and high among them; in band adds no verdict word.
  expect(rows[0].bar).toMatch(/^Temperature 64\.8 °F, band 62\.0–70\.0 °F, today \d\d\.\d–\d\d\.\d °F$/);
  expect(rows[2].bar).toMatch(/^RH 71\.0%, no band, today/);
  expect(document.querySelector(".now-off")).toBeNull();
  expect(plain(text(document.querySelector(".now-tib")))).toBe("In band today temperature 96% · VPD 88% (yesterday 94% · 91%)");
  expect(status()).toEqual(["In band, holding", "Iris OK, Sunrise plan to 17:30", "Outdoor 41.2 °F", "Nobody seen: misters may run (last person 05:10)"]);
});

test("each bar links to its Graphs panel; the status line links the mode to Equipment's why, Iris to Plan and outdoor to Graphs", () => {
  renderHero("in-band");
  const links = (selector: string) => [...document.querySelectorAll(selector)].map((link) => link.getAttribute("href"));
  expect(links(".now-band-label")).toEqual(["/graphs?range=24h#temperature", "/graphs?range=24h#vpd", "/graphs?range=24h#rh"]);
  expect(screen.getByRole("link", { name: "Temp: Temperature in Graphs" })).toBeTruthy();
  expect(["mode", "iris", "outdoor"].map((tile) => document.querySelector(`.now-status [data-tile=${tile}]`)?.getAttribute("href"))).toEqual(["/equipment#why", "/plan", "/graphs?range=24h#temperature"]);
  expect(document.querySelector("[data-chip=occupancy]")?.tagName).toBe("SPAN");
});

test("out of band: a steady headline that is no link, the reading in a plain line under it, the bar says above band, and the hero value drops its in band", () => {
  renderHero("too-hot");
  expect(heading()).toBe("Out of band");
  const h1 = screen.getByRole("heading", { level: 1 });
  expect(h1.querySelector("a")).toBeNull();
  expect(document.querySelector(".hero-answer-link")).toBeNull();
  // The condition line: the phrase in plain text, with a quiet "Graphs ›" to the reading's own panel.
  const condition = document.querySelector(".now-condition")!;
  expect(condition.previousElementSibling).toBe(h1);
  expect(plain(text(condition.querySelector("[data-condition=temp]")))).toBe("Too hot: 84.1 °F (band 62–70 °F)");
  expect(screen.getByRole("link", { name: /^Graphs: temperature/ }).getAttribute("href")).toBe("/graphs?range=24h#temperature");
  expect(bands()[0].meta).toBe("above band · band 62.0–70.0 °F");
  expect(text(document.querySelector(".now-band[data-tile=temp] .now-off"))).toBe("above band");
  expect(document.querySelector(".now-hero-verdict")).toBeNull();
  // The bar clamps a value past its scale to the edge with an arrowhead.
  expect(document.querySelector(".now-band[data-tile=temp] .bb-clamp")).not.toBeNull();
});

test("rows 2 and 3: no verdict anywhere, values STALE and muted, the mode unknown while the controller cannot vouch for it", () => {
  for (const [names, says, asOf] of [["ingestor-not-running", "Ingestor not running since 10:38", "As of 10:38"], ["controller-not-reporting", "Controller not reporting since 10:36", "As of 10:36"]] as const) {
    renderHero(names);
    expect(heading()).toBe(says);
    expect(text(document.body)).not.toMatch(VERDICTS);
    expect(document.querySelector(".hero-temp")?.className).toContain("value-muted");
    expect(document.querySelector(".now-hero-value [data-badge=stale]")).not.toBeNull();
    expect(document.querySelector("[data-fresh]")?.getAttribute("data-fresh")).toBe("off");
    expect(status()[0]).toBe("Mode unknown");
    expect(document.querySelectorAll(".now-band svg.bb.is-stale")).toHaveLength(3);
    expect(text(document.querySelector(".now-hero-climate[data-stale] .now-bands-asof"))).toBe(asOf);
    cleanup();
  }
});

test("row 7, stale data: no verdict word anywhere, the mode as last said and dated, and band bars that say their age and never read as live", () => {
  renderHero(["stale-data", "time-in-band"]);
  // The heading does not repeat the age line under the value.
  expect(heading()).toBe("Waiting for fresh data");
  expect(plain(text(document.querySelector("#data")))).toBe("Last data 14 min ago (10:28)");
  expect(document.querySelector(".hero-temp")?.className).toContain("value-muted");
  expect(document.querySelector(".now-hero-value [data-badge=stale]")).not.toBeNull();
  expect(document.querySelector("[data-fresh]")?.getAttribute("data-fresh")).toBe("stale");
  // No verdict: not beside the value, not on a bar, and not in the mode, whose IDLE words assert the band.
  expect(document.querySelector(".now-hero-verdict, .now-off")).toBeNull();
  expect(status()[0]).toBe("Last mode: holding (10:28)");
  expect(text(document.querySelector(".now-status"))).not.toMatch(/\bin band\b|above band|below band/i);
  expect(text(document.querySelector(".now-bands"))).not.toMatch(/\bin band\b|above band|below band/i);
  // The bars: each stale (muted), none hatched (hatching is for unknown), and the group dated in words.
  expect(document.querySelectorAll(".now-band svg.bb.is-stale")).toHaveLength(3);
  expect(document.querySelectorAll(".now-band svg.bb.is-unknown")).toHaveLength(0);
  expect(bands().map(({ value }) => value)).toEqual(["64.8 °F", "0.62 kPa", "71.0%"]);
  expect(document.querySelector(".now-hero-climate")?.hasAttribute("data-stale")).toBe(true);
  expect(text(document.querySelector(".now-bands-asof"))).toBe("As of 10:28");
  // A stale band is drawn neutral, as with the API down, so a muted dot inside it is no in-band verdict.
  const css = readFileSync(join(process.cwd(), "app/styles/primitives.css"), "utf8");
  expect(css).toContain(".bb-hero.is-stale .bb-band{fill:var(--ink-2)}.bb-hero.is-stale .bb-edge{stroke:var(--ink-muted)}");
  expect(css).toContain(".bb.is-stale .bb-band{fill:var(--field)}.bb.is-stale .bb-edge{stroke:var(--line-strong)}");
  expect(css.indexOf(".bb-hero.is-stale .bb-band")).toBeGreaterThan(css.indexOf(".bb.is-stale .bb-band"));
  cleanup();
  // Fresh, the group carries no age and no stale mark.
  renderHero("in-band");
  expect(document.querySelector(".now-hero-climate")?.hasAttribute("data-stale")).toBe(false);
  expect(document.querySelector(".now-bands-asof")).toBeNull();
  expect(document.querySelectorAll(".now-band svg.bb.is-stale")).toHaveLength(0);
});

test("the ingestor's answer is no link (the freshness link below opens #data), and #data opens All readings in place", () => {
  renderHero("ingestor-not-running", { path: "/#data" });
  expect(screen.getByRole("heading", { level: 1 }).querySelector("a")).toBeNull();
  expect(document.querySelector(".now-condition")).toBeNull();
  expect(document.querySelector<HTMLDetailsElement>("#readings")?.open).toBe(true);
  cleanup();
  renderHero("in-band");
  const readings = document.querySelector<HTMLDetailsElement>("#readings")!;
  expect(readings.open).toBe(false);
  fireEvent.click(screen.getByRole("link", { name: /^Updated 20\s+s ago$/ }));
  expect(readings.open).toBe(true);
  const kv = plain(text(readings.querySelector(".now-readings-list")));
  expect(kv).toContain("band in force 62.0–70.0 °F · DERIVED · 4 min");
  expect(kv).toContain("IDLE");
  expect(plain(text(readings.querySelector("summary")))).toBe("All readings · sources, times and the mode code, settings pushed 41 min ago");
});

test("the band line reads DERIVED with its age from band.observed_at, and states none without it", () => {
  const now = parseNow(fixture("in-band", { now: NOW })["API-02"]) as Now;
  expect(plain(bandLine(now.band, NOW))).toBe("band in force 62.0–70.0 °F · DERIVED · 4 min");
  expect(plain(bandLine({ ...now.band, observed_at: null }, NOW))).toBe("band in force 62.0–70.0 °F · DERIVED");
  expect(bandLine({ ...now.band, temp_f: { low: null, high: 70, verdict: "in" } }, NOW)).toBe("band unavailable");
});

test("a null reading reads — with its reason, never 0, and its bar is hatched", () => {
  renderHero("rh-missing");
  const rh = document.querySelector(".now-band[data-tile=rh]")!;
  expect(text(rh.querySelector(".now-band-value"))).toBe("—");
  expect(text(rh.querySelector(".now-band-why"))).toBe("No indoor RH");
  expect(rh.querySelector("svg.bb.is-unknown")).not.toBeNull();
  expect(text(rh)).not.toMatch(/\b0\.0%/);
});

test("API-02 failing: row 1 with Retry, the status line holds occupancy alone; with the API down no bars and no In band today", () => {
  const { retry } = renderHero(["in-band", "time-in-band"], { failed: true });
  expect(heading()).toBe("Can't reach the greenhouse data");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledOnce();
  expect(status()).toEqual(["Nobody seen: misters may run (last person 05:10)"]);
  cleanup();
  renderHero(["in-band", "time-in-band"], { failed: true, apiDown: true });
  expect(document.querySelector(".now-bands")).toBeNull();
  // Nothing to draw on the right: the hero is one column, never an empty block beside the answer.
  expect(document.querySelector(".now-hero-climate, .now-tib")).toBeNull();
  expect(document.querySelector(".now-hero")?.classList.contains("now-hero-plain")).toBe(true);
  expect(text(document.body)).not.toMatch(/in band|All OK/i);
  expect(document.querySelector(".tag-ok, .status-complete, .fresh-ok")).toBeNull();
  cleanup();
  // Never answered at all: no value, its reason, and nothing to draw.
  renderHero("in-band", { failed: true, data: false });
  expect(plain(text(document.querySelector(".hero-temp")))).toBe("—");
  expect(text(document.querySelector(".now-hero-main"))).toContain("No answer from the greenhouse data");
  expect(document.querySelector("#readings")).toBeNull();
});

test("a missed SUNSET turns the Iris phrase amber; the hero before API-02 answers claims nothing and keeps its height", () => {
  renderHero("missed-sunset");
  expect(document.querySelector(".now-status [data-tile=iris]")?.getAttribute("data-tone")).toBe("warn");
  expect(text(document.querySelector(".now-status [data-tile=iris]"))).toMatch(/^Iris Missed/);
  cleanup();
  const loading = { now: null, answer: nowAnswer({ now: null, failed: false }), irrigation: read<IrrigationStatus>(null), day: read<Series>(null), daily: read<Series>(null), onRetry: vi.fn() };
  const html = renderToStaticMarkup(<MemoryRouter><SessionProvider><NowHero {...loading} /></SessionProvider></MemoryRouter>);
  expect(html).toContain("Checking the greenhouse…");
  expect(html).not.toMatch(/All OK|64\.8|Nobody seen|in band/);
  expect(html).not.toMatch(/\sstyle=/);
  renderUi(<NowHero {...loading} />);
  expect(within(document.querySelector<HTMLElement>(".hero")!).getByRole("heading", { level: 1 }).textContent).toBe("Checking the greenhouse…");
  expect(document.querySelectorAll(".hero .skeleton")).toHaveLength(3);
});

// ── The greenhouse snapshot (#1346) ─────────────────────────────────────────

const snapshotOf = () => document.querySelector<HTMLElement>(".now-snapshot");
function renderWithSnapshot(names: string | string[], patch: Record<string, unknown> | null, apiDown = false) {
  const body = fixture(names, { now: NOW })["API-02"] as Record<string, unknown>;
  const snapshot = patch === null ? null : { ...(body.snapshot as Record<string, unknown>), ...patch };
  const now = parseNow({ ...body, snapshot });
  const props = {
    now, answer: nowAnswer({ now, failed: false }), irrigation: read(parseIrrigationStatus(fixture(names, { now: NOW })["API-IRR-01"])),
    day: dayRead(names), daily: dailyRead(names), onRetry: vi.fn(),
  };
  return render(<MemoryRouter><SessionProvider {...DEFAULT_SESSION} apiDown={apiDown}><NowHero {...props} /></SessionProvider></MemoryRouter>);
}

test("the snapshot: one paragraph under the headline with its author, time and the AI text mark", () => {
  renderHero(["in-band", "time-in-band"]);
  const paragraph = snapshotOf();
  expect(paragraph?.getAttribute("data-snapshot")).toBe("fresh");
  expect(paragraph?.className).not.toContain("value-muted");
  expect(plain(text(paragraph?.querySelector(".now-snapshot-text")))).toMatch(/^The greenhouse is at 64\.8 °F .* no open alerts\.$/);
  expect(plain(text(paragraph?.querySelector(".now-snapshot-mark")))).toBe("Written by Qwen (Cortex) at 10:36 · AI");
  // On a phone the text keeps to two lines until Read all (the clamp is CSS; the toggle is the same everywhere).
  const more = screen.getByRole("button", { name: "Read all" });
  expect(more.getAttribute("aria-controls")).toBe(paragraph?.querySelector(".now-snapshot-text")?.id);
  expect(more.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(more);
  expect(screen.getByRole("button", { name: "Show less" }).getAttribute("aria-expanded")).toBe("true");
  expect(paragraph?.hasAttribute("data-open")).toBe(true);
  // It follows the headline inside the hero's main block, and it is not a pill (D-UX-07).
  expect(paragraph?.closest(".now-hero-main")).not.toBeNull();
  expect(paragraph?.querySelector("[data-badge]")).toBeNull();
});

test("every signed-in user sees the snapshot, viewers as well as operators (Jason, 2026-11-06, #1346)", () => {
  const body = fixture(["in-band", "time-in-band"], { now: NOW })["API-02"] as Record<string, unknown>;
  const now = parseNow(body);
  const props = {
    now, answer: nowAnswer({ now, failed: false }), irrigation: read(parseIrrigationStatus(fixture("in-band", { now: NOW })["API-IRR-01"])),
    day: dayRead("in-band"), daily: dailyRead(["in-band", "time-in-band"]), onRetry: vi.fn(),
  };
  const seen = (["viewer", "operator"] as const).map((role) => {
    render(<MemoryRouter><SessionProvider role={role} writesEnabled={role === "operator"}><NowHero {...props} /></SessionProvider></MemoryRouter>);
    const words = plain(text(snapshotOf()?.querySelector(".now-snapshot-text")));
    cleanup();
    return words;
  });
  expect(seen[0]).toMatch(/^The greenhouse is at/);
  expect(seen[1]).toBe(seen[0]);
});

test("the snapshot when stale (old or a failed later attempt) reads muted with its as-of time", () => {
  renderHero(["stale-data", "time-in-band"]);
  expect(snapshotOf()?.getAttribute("data-snapshot")).toBe("stale");
  expect(snapshotOf()?.className).toContain("value-muted");
  expect(plain(text(snapshotOf()?.querySelector(".now-snapshot-mark")))).toBe("Written by Qwen (Cortex) · as of 10:02 · AI");
  cleanup();
  renderWithSnapshot("in-band", { stale: true, stale_reason: "old", as_of: new Date(NOW - 45 * 60_000).toISOString() });
  expect(plain(text(snapshotOf()?.querySelector(".now-snapshot-mark")))).toBe("Written by Qwen (Cortex) · as of 09:57 · AI");
  cleanup();
  renderWithSnapshot("in-band", {}, true); // the API down mutes it with the rest of the hero
  expect(snapshotOf()?.getAttribute("data-snapshot")).toBe("stale");
});

test("the snapshot never shows when outdated, missing, empty or older than 2 h, and the status line stands alone", () => {
  for (const patch of [{ outdated: true }, null, { text: " " }, { as_of: new Date(NOW - 121 * 60_000).toISOString(), stale: true }]) {
    renderWithSnapshot("in-band", patch);
    expect(snapshotOf()).toBeNull();
    expect(document.querySelector(".now-status")).not.toBeNull();
    cleanup();
  }
  // A server before #1346 sends no snapshot at all.
  const body = { ...(fixture("in-band", { now: NOW })["API-02"] as Record<string, unknown>) };
  delete body.snapshot;
  expect(parseNow(body)?.snapshot).toBeUndefined();
});
