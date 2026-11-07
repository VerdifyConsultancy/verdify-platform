// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// ChartStack and ChartGrid (redesign §7.1) on the uPlot stand-in: one time axis at the bottom, one y-axis width, no
// floating tooltip but header rows that read the cursor and one time chip; the layers (excursions, envelope, compare,
// events) painted only when asked; lanes in their disclosure; small multiples on one y range per row.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ChartGrid, cellReadout, rowRange } from "./chart-grid";
import { ChartStack, STACK_AXIS_PX, bare, panelReadout, panelStats, seriesWords, type StackPanel } from "./chart-stack";
import type { ChartEvent } from "./events";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens } from "./fixtures/uplot-mock";
import { compareLine } from "./layers";
import { ACHIEVABLE } from "./marks";
import { buildTimeSeriesModel, chartNotes } from "./time-series";
import type { BandTimeline, MetricDef, Series } from "./types";

vi.mock("uplot", async () => ({ default: (await import("./fixtures/uplot-mock")).MockPlot }));

const HOUR = 3600;
// Monday 2026-11-02 from local midnight: an ordinary day (the day before is the fall-back day).
const FROM = Date.parse("2026-11-02T07:00:00Z") / 1000;
const TO = FROM + 24 * HOUR;
const NOW = TO + HOUR;
const RANGE = { from: FROM, to: TO };
const iso = (at: number) => new Date(at * 1000).toISOString();
/** An element's words with no-break spaces read as spaces. */
const text = (node: Element) => (node.textContent ?? "").replaceAll("\u00a0", " ");
const metric = (key: string, label: string, unit: string): MetricDef => ({ key, label, group: "house climate", unit, cadence_s: 60, retention_days: 365, provenance: "MEASURED", zone: null, daily: false, note: null });
const TEMP = metric("climate.temp_avg", "Indoor temperature", "°F");
const OUTDOOR = metric("climate.outdoor_temp_f", "Outdoor temperature", "°F");
const VPD = metric("climate.vpd_avg", "Indoor VPD", "kPa");
const RH = metric("climate.rh_avg", "Indoor RH", "%");

/** A window of buckets of `bucket` seconds; each key's mean from its function; temperature's min and max 1 °F either side. */
function series(bucket: number, columns: Record<string, (index: number) => number | null>, shift = 0): Series {
  const t: number[] = [];
  for (let at = FROM - shift; at < TO - shift; at += bucket) t.push(at);
  const column = (key: string, value: (index: number) => number | null) => {
    const avg = t.map((_, index) => value(index));
    const spread = key.includes("temp") ? 1 : 0;
    return { avg, min: avg.map((v) => (v === null ? null : v - spread)), max: avg.map((v) => (v === null ? null : v + spread)), count: avg.map((v) => (v === null ? 0 : 10)), source: "raw" };
  };
  return { from: iso(FROM - shift), to: iso(TO - shift), bucket_s: bucket, server_time: iso(NOW), t, series: Object.fromEntries(Object.entries(columns).map(([key, value]) => [key, column(key, value)])), gaps: [] } as unknown as Series;
}
/** The band 62–70 °F and 0.6–1.2 kPa all day. */
function band(): BandTimeline {
  const t: number[] = [];
  for (let at = FROM; at <= TO; at += 900) t.push(at);
  const flat = (value: number) => t.map(() => value);
  const columns = { temp_low: flat(62), temp_high: flat(70), vpd_low: flat(0.6), vpd_high: flat(1.2) };
  return { from: iso(FROM), to: iso(TO), step_s: 900, t, band: columns, crop: columns } as unknown as BandTimeline;
}
/** Temperature rising from 60 to 75 over the day: below the band early, above it late. */
const rising = (count: number) => (index: number) => 60 + (15 * index) / (count - 1);
function panels(bucket = 300, overrides: Partial<Record<string, Partial<StackPanel>>> = {}): StackPanel[] {
  const count = (24 * HOUR) / bucket;
  return [
    { id: "temperature", name: "Temperature", unit: "°F", metrics: [TEMP, OUTDOOR], series: series(bucket, { "climate.temp_avg": rising(count), "climate.outdoor_temp_f": () => 41 }), ...overrides.temperature },
    { id: "vpd", name: "VPD", unit: "kPa", metrics: [VPD], series: series(bucket, { "climate.vpd_avg": () => 0.8 }), ...overrides.vpd },
    { id: "rh", name: "RH", unit: "%", metrics: [RH], series: series(bucket, { "climate.rh_avg": () => 71 }), ...overrides.rh },
  ];
}
const settle = () => act(async () => { await vi.dynamicImportSettled(); });
async function mount(ui: ReactElement) {
  const view = render(<MemoryRouter>{ui}</MemoryRouter>);
  await settle();
  return view;
}
const head = (id: string) => document.querySelector(`[data-panel=${id}] .stack-stats`)!.textContent!.replaceAll("\u00a0", " ");

const { createObjectURL, revokeObjectURL } = URL;
const blobs: Blob[] = [];
beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = []; blobs.length = 0;
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  URL.createObjectURL = vi.fn((blob: Blob) => { blobs.push(blob); return `blob:${blobs.length}`; });
  URL.revokeObjectURL = vi.fn();
  setTokens();
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style");
  URL.createObjectURL = createObjectURL; URL.revokeObjectURL = revokeObjectURL;
});

test("one card of panels, each with its anchor; one time axis at the bottom, one y-axis width, and no floating tooltip", async () => {
  await mount(<ChartStack label="Climate, last 24 h" panels={panels()} band={band()} now={NOW} xRange={RANGE} syncKey="verdify:graphs:24h" />);
  const stack = screen.getByRole("region", { name: "Climate, last 24 h" });
  expect([...stack.querySelectorAll("[data-panel]")].map((panel) => [panel.id, panel.getAttribute("data-panel")])).toEqual([["temperature", "temperature"], ["vpd", "vpd"], ["rh", "rh"]]);
  expect(created).toHaveLength(3);
  // Only the last panel's time axis has height and labels; the others keep its splits for their grid.
  const xAxes = created.map((plot) => plot.opts.axes![0]);
  expect(xAxes.map((axis) => axis.size)).toEqual([0, 0, expect.any(Function)]);
  expect((xAxes[0].values as (u: unknown, splits: number[]) => string[])(null, [FROM, FROM + HOUR])).toEqual(["", ""]);
  expect(created.map((plot) => plot.opts.axes![1].size)).toEqual([STACK_AXIS_PX, STACK_AXIS_PX, STACK_AXIS_PX]);
  expect(created.map((plot) => plot.opts.plugins ?? [])).toEqual([[], [], []]);
  expect(new Set(created.map((plot) => plot.opts.cursor?.sync?.key))).toEqual(new Set(["verdify:graphs:24h"]));
  expect(stack.querySelector("[style]")).toBeNull();
  // The header rows state the window: in band % where a band is drawn, low, mean and high with their times.
  // Low and high are the buckets' own extremes (min and max), as the summary has always read them.
  expect(head("temperature")).toMatch(/^in band 53% · low 59\.0 \(00:00\) · mean 67\.5 · high 76\.0 \(23:55\)$/);
  expect(head("vpd")).toMatch(/^in band 100% · low 0\.80 /);
  expect(head("rh")).toMatch(/^low 71\.0 /);
  // Inside the named panel each toggle reads its series alone, its full name kept as the accessible name.
  expect(within(stack.querySelector("[data-panel=temperature]") as HTMLElement).getAllByRole("button").map((button) => [button.textContent, button.getAttribute("aria-label"), button.getAttribute("aria-pressed")])).toEqual([
    ["Indoor", "Indoor temperature", "true"], ["Outdoor", "Outdoor temperature", "true"],
  ]);
});

test("a series reads by what sets it apart inside its panel, whole when nothing is left", () => {
  expect(seriesWords("Indoor temperature", "Temperature")).toBe("Indoor");
  expect(seriesWords("Forecast temperature", "Temperature")).toBe("Forecast");
  // The provenance word stays in the toggle's accessible name and hover words, not in its short text (#1354).
  expect(seriesWords("Outdoor VPD · DERIVED", "VPD")).toBe("Outdoor");
  expect(seriesWords("Outdoor relative humidity", "RH")).toBe("Outdoor");
  expect(seriesWords("Controller VPD target", "VPD")).toBe("Controller target");
  expect(seriesWords("Temperature", "Temperature")).toBe("Temperature");
  expect(seriesWords("VPD · DERIVED", "VPD")).toBe("VPD · DERIVED");
  expect(seriesWords("Soil moisture south 1", "Soil moisture south 1")).toBe("Soil moisture south 1");
});

test("a cursor on any panel turns every header row into its values there, with one time chip; leaving restores the window's", async () => {
  await mount(<ChartStack label="Climate" panels={panels()} band={band()} now={NOW} xRange={RANGE} syncKey="k" />);
  const chips = document.querySelectorAll(".stack-chip");
  expect(chips).toHaveLength(1);
  expect((chips[0] as HTMLElement).hidden).toBe(true);
  const at = FROM + 23 * HOUR;
  act(() => created[1].hover(at, (23 * HOUR) / 300));
  expect((chips[0] as HTMLElement).hidden).toBe(false);
  expect(chips[0].textContent).toMatch(/^\d\d:\d\d$/);
  expect(head("temperature")).toMatch(/^Indoor temperature 74\.\d · Outdoor temperature 41\.0 · band 62\.0–70\.0 · above$/);
  expect(head("vpd")).toBe("0.80 · band 0.60–1.20 · in");
  expect(head("rh")).toBe("71.0");
  // While the readout shows, the window's statistics hold the row's height unseen, so nothing below it moves.
  const held = document.querySelector("[data-panel=vpd] .stack-stats-held")!;
  expect([held.getAttribute("aria-hidden"), held.textContent]).toEqual(["true", expect.stringMatching(/^in band 100% · low 0\.80 /)]);
  act(() => created[1].hover(FROM - HOUR, null));
  expect(head("vpd")).toMatch(/^in band 100%/);
  expect(document.querySelector(".stack-stats-held")).toBeNull();
});

test("excursions, the envelope and compare paint only when asked", async () => {
  const red = TOKENS["--tint-warn"], envelope = TOKENS["--ink-muted"], bound = TOKENS["--c-bound"];
  const paint = () => { for (const plot of created) plot.draw(); return created[0].ctx; };
  // 30 min buckets: the envelope's resolution.
  await mount(<ChartStack label="Climate" panels={panels(1800)} band={band()} now={NOW} xRange={RANGE} syncKey="k" />);
  let ctx = paint();
  expect(ctx.fills.some((fill) => fill.color === red)).toBe(false);
  expect(ctx.strokes.some((stroke) => stroke.stroke === envelope)).toBe(false);
  expect(ctx.strokes.some((stroke) => stroke.stroke === bound && stroke.dash.length > 0)).toBe(false);
  cleanup(); MockPlot.reset();
  const earlier = series(1800, { "climate.temp_avg": () => 66, "climate.vpd_avg": () => 0.7, "climate.rh_avg": () => 70 }, 86_400);
  const compared = panels(1800, { temperature: { compare: earlier }, vpd: { compare: earlier }, rh: { compare: earlier } });
  await mount(<ChartStack label="Climate" panels={compared} band={band()} now={NOW} xRange={RANGE} syncKey="k" excursions envelope compare={{ label: "yesterday", shift: 86_400 }} />);
  ctx = paint();
  // Below the band in the morning and above it at night: two shaded stretches, under the line.
  expect(ctx.fills.filter((fill) => fill.color === red)).toHaveLength(2);
  expect(ctx.strokes.filter((stroke) => stroke.stroke === envelope).length).toBe(48);
  expect(ctx.strokes.some((stroke) => stroke.stroke === bound && stroke.dash.length === 2 && stroke.width === 1.25)).toBe(true);
  // The header carries the compare's mean and the change against it.
  expect(head("temperature")).toMatch(/ · yesterday's mean 66\.0 · ▲ 1\.5 vs yesterday$/);
  // The envelope waits for 30 min buckets: at 5 min none draws, even when asked.
  cleanup(); MockPlot.reset();
  await mount(<ChartStack label="Climate" panels={panels(300)} band={band()} now={NOW} xRange={RANGE} syncKey="k" envelope />);
  expect(paint().strokes.some((stroke) => stroke.stroke === envelope)).toBe(false);
});

test("the event rail draws only when given events: an icon per kind, a key that turns kinds off, a list per mark, and the list under the stack", async () => {
  await mount(<ChartStack label="Climate" panels={panels()} now={NOW} xRange={RANGE} syncKey="k" />);
  expect(document.querySelector(".stack-rail, .stack-events, .rail-key")).toBeNull();
  cleanup(); MockPlot.reset();
  const events: ChartEvent[] = [
    { id: "a", at: FROM + 2 * HOUR, source: "people", kind: "setting", glyph: "✎", token: "forest", label: "09:00 · jason · Set VPD Mister Engage", href: "/activity/log?target=mister_engage_kpa#activity-1", touch: { kind: "setting", param: "mister_engage_kpa" } },
    { id: "b", at: FROM + 10 * HOUR, source: "alerts", kind: "alert", glyph: "▲", token: "fault", label: "17:00 · critical · Relay stuck", href: "/activity?alert=7", touch: { kind: "span", from: FROM + 10 * HOUR, to: FROM + 12 * HOUR } },
    { id: "p", at: FROM + 14 * HOUR, source: "iris", kind: "plan", glyph: "◆", badge: "↓", token: "ink", label: "21:00 · iris AI · Plan" },
    { id: "c", at: FROM - HOUR, source: "runs", kind: "water", glyph: "▮", token: "eq-water", label: "outside the window" },
  ];
  await mount(<ChartStack label="Climate" panels={panels()} now={NOW} xRange={RANGE} syncKey="k" events={events} />);
  const rail = screen.getByRole("group", { name: "Events on the charts, 3" });
  const marks = within(rail).getAllByRole("button");
  expect(marks.map((mark) => [mark.getAttribute("aria-label"), mark.getAttribute("data-kind"), mark.textContent, mark.getAttribute("tabindex")])).toEqual([
    ["09:00 · jason · Set VPD Mister Engage", "setting", "✎", "-1"], ["17:00 · critical · Relay stuck", "alert", "▲", "-1"], ["21:00 · iris AI · Plan", "plan", "◆↓", "-1"],
  ]);
  expect(marks[1].querySelector(".rail-glyph")?.getAttribute("data-token")).toBe("fault");
  // The key names the kinds in the window, and a plan's triggers.
  const key = screen.getByRole("list", { name: "Event marks" });
  expect(within(key).getAllByRole("button").map((button) => [button.textContent, button.getAttribute("aria-pressed")])).toEqual([["✎setting", "true"], ["◆plan", "true"], ["▲alert", "true"]]);
  expect(key.textContent).toContain("↑ sunrise · ↓ sunset · ☾ midnight");
  // Hovering the alert draws the hairline and lights its span; the list under the stack lights its row.
  fireEvent.mouseEnter(marks[1]);
  expect(document.querySelector(".stack-hairline")).not.toBeNull();
  expect(document.querySelectorAll(".stack-span")).toHaveLength(1);
  const under = screen.getByRole("region", { name: "Events in this window (3)" });
  expect(under.querySelector("[data-lit]")?.id).toBe("stack-event-b");
  fireEvent.click(marks[1]);
  const list = screen.getByRole("list", { name: "Events at this mark" });
  expect(within(list).getByRole("link", { name: "17:00 · critical · Relay stuck" }).getAttribute("href")).toBe("/activity?alert=7");
  expect(within(under).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual(["/activity/log?target=mister_engage_kpa#activity-1", "/activity?alert=7"]);
  // Escape in the list closes it and puts focus back on the mark that opened it; Escape on the mark does the same.
  const link = within(list).getByRole("link");
  link.focus();
  fireEvent.keyDown(link, { key: "Escape" });
  expect(screen.queryByRole("list", { name: "Events at this mark" })).toBeNull();
  expect(document.activeElement).toBe(marks[1]);
  fireEvent.click(marks[1]);
  expect(screen.getByRole("list", { name: "Events at this mark" })).toBeTruthy();
  fireEvent.keyDown(marks[1], { key: "Escape" });
  expect(screen.queryByRole("list", { name: "Events at this mark" })).toBeNull();
  expect(document.activeElement).toBe(marks[1]);
  // A pinned list closes on a pointer down elsewhere (a menu opened after it is never under it).
  fireEvent.click(marks[1]);
  expect(screen.getByRole("list", { name: "Events at this mark" })).toBeTruthy();
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("list", { name: "Events at this mark" })).toBeNull();
  // Hovering a list row lights its mark; turning the alert kind off takes it off the rail and the list.
  fireEvent.mouseLeave(marks[1]);
  fireEvent.mouseEnter(document.getElementById("stack-event-a")!);
  expect(marks[0].hasAttribute("data-lit")).toBe(true);
  fireEvent.click(within(key).getByRole("button", { name: /alert/ }));
  expect(screen.getByRole("group", { name: "Events on the charts, 2" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "Events in this window (2)" })).toBeTruthy();
});

test("marks closer than 8 px stack into one: the first one's icon and +N, every event in its list", async () => {
  const events: ChartEvent[] = [0, 1, 2].map((n) => ({ id: `s${n}`, at: FROM + 2 * HOUR + n * 60, source: "people", kind: "setting", glyph: "✎", token: "forest", label: `set ${n}` }));
  const width = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
  await mount(<ChartStack label="Climate" panels={panels()} now={NOW} xRange={RANGE} syncKey="k" events={events} />);
  width.mockRestore();
  const marks = within(screen.getByRole("group", { name: "Events on the charts, 3" })).getAllByRole("button");
  expect(marks.map((mark) => mark.textContent)).toEqual(["✎+2"]);
  fireEvent.click(marks[0]);
  expect(within(screen.getByRole("list", { name: "Events at this mark" })).getAllByRole("listitem")).toHaveLength(3);
});

test("lanes sit in a disclosure open from 861 px; the foot offers CSV of this window and one data table", async () => {
  await mount(<ChartStack label="Climate" panels={panels()} now={NOW} xRange={RANGE} syncKey="k" lanes={<p>lanes here</p>} foot="One point per 5 min" csvName="graphs" />);
  const lanes = screen.getByText("lanes here").closest("details")!;
  expect([lanes.className, lanes.open, lanes.querySelector("summary")?.textContent]).toEqual(["stack-lanes", true, "Equipment"]);
  const csv = screen.getByRole("link", { name: "CSV of this window" });
  expect(csv.getAttribute("download")).toMatch(/^verdify-graphs-/);
  const text = await blobs[0].text();
  // (Blob.text() reads the byte-order mark away.)
  expect(text.split("\r\n")[0]).toBe("time (America/Denver),climate.temp_avg (°F),climate.outdoor_temp_f (°F),climate.vpd_avg (kPa),climate.rh_avg (%)");
  expect(document.querySelectorAll("details.chart-table")).toHaveLength(1);
  expect(document.querySelector(".stack-foot")?.textContent).toContain("One point per 5 min");
  cleanup(); MockPlot.reset();
  // On a phone the lanes start closed.
  vi.stubGlobal("matchMedia", media({ narrow: true, fine: false }));
  await mount(<ChartStack label="Climate" panels={panels()} now={NOW} xRange={RANGE} syncKey="k" lanes={<p>lanes here</p>} />);
  expect(screen.getByText("lanes here").closest("details")!.open).toBe(false);
});

test("a panel whose read failed or is loading shows its message in its frame and states nothing about the window; the others still draw", async () => {
  await mount(<ChartStack label="Climate" panels={panels(300, { vpd: { series: null, message: "Couldn't load the VPD chart." }, rh: { series: null, message: "Loading RH…" } })} now={NOW} xRange={RANGE} syncKey="k" />);
  expect(document.querySelector("[data-panel=vpd] .chart-message")?.textContent).toBe("Couldn't load the VPD chart.");
  expect(created).toHaveLength(1);
  // Never "no data in this window" for a read that has not answered: the header row stays empty at its height.
  expect([head("vpd"), head("rh")]).toEqual(["", ""]);
  expect(head("temperature")).toMatch(/^low 59\.0 \(00:00\) · mean 67\.5 /);
});

test("the header words: bare numbers in the unit's decimals, gaps counted, the readout and compare line from the model", () => {
  expect(bare(64.83, "°F")).toBe("64.8");
  expect(bare(0.625, "kPa")).toBe("0.63");
  const model = buildTimeSeriesModel({ series: series(300, { "climate.temp_avg": (index) => (index === 10 ? null : 65) }), metrics: [TEMP], band: band(), now: NOW, xRange: RANGE });
  expect(panelStats(model, null)).toBe("in band 100% · low 64.0 (00:00) · mean 65.0 · high 66.0 (00:00) · 1 gap");
  const compare = compareLine(series(300, { "climate.temp_avg": () => 64 }, 86_400), "climate.temp_avg", 86_400, "yesterday");
  expect(panelReadout(model, new Set(), compare, FROM + HOUR).replaceAll("\u00a0", " ")).toBe("65.0 · band 62.0–70.0 · in · yesterday 64.0");
});

test("the achievable VPD band (#1433): graded beside the band, read at the cursor where it leaves the band, keyed and noted", () => {
  const timeline = band();
  // Night (the first 8 h): the house is expected to reach no more than 0.45 kPa, so the floor drops there.
  const night = (at: number) => at < FROM + 8 * HOUR;
  const achievable = {
    vpd_low: timeline.t.map((at) => (night(at) ? 0.45 : 0.6)), vpd_high: timeline.t.map(() => 1.2),
    expected_low: timeline.t.map((at) => (night(at) ? 0.45 : null)), expected_high: timeline.t.map((at) => (night(at) ? 0.55 : null)),
    days: 14, nights: [],
  };
  const withAchievable = { ...timeline, achievable } as unknown as BandTimeline;
  // VPD 0.5 at night (below the band, inside the achievable band), 0.8 by day.
  const vpd = series(300, { "climate.vpd_avg": (index) => (index < 96 ? 0.5 : 0.8) });
  const model = buildTimeSeriesModel({ series: vpd, metrics: [VPD], band: withAchievable, now: NOW, xRange: RANGE });
  expect(panelStats(model, null)).toMatch(/^in band 67% · achievable 100% · /);
  expect(panelReadout(model, new Set(), null, FROM + HOUR)).toBe("0.50 · band 0.60–1.20 · below · achievable 0.45–1.20");
  expect(panelReadout(model, new Set(), null, FROM + 12 * HOUR)).toBe("0.80 · band 0.60–1.20 · in");
  expect(chartNotes(model)).toContain(ACHIEVABLE);
  // Without the field (an older API, a zone, a window past 35 days) nothing changes.
  const plain = buildTimeSeriesModel({ series: vpd, metrics: [VPD], band: timeline, now: NOW, xRange: RANGE });
  expect(plain.achievable).toEqual([]);
  expect(panelStats(plain, null)).toMatch(/^in band 67% · low /);
});

test("ChartGrid: one y range per row, the y axis on the first column and the time axis on the last row; readouts per cell; a data table per row", async () => {
  const columns = ["north", "south"].map((key) => ({ key, head: <span>{key}</span>, name: key === "north" ? "North" : "South" }));
  const cell = (column: string, offset: number) => ({ column, metrics: [TEMP], series: series(300, { "climate.temp_avg": () => 64 + offset }), band: band() });
  const rows = [
    { key: "temp", name: "Temperature", unit: "°F", cells: [cell("north", 0), cell("south", 3)] },
    { key: "vpd", name: "VPD", unit: "kPa", cells: [{ column: "north", metrics: [VPD], series: series(300, { "climate.vpd_avg": () => 0.7 }) }, { column: "south", metrics: [VPD], series: series(300, { "climate.vpd_avg": () => 0.9 }) }] },
  ];
  await mount(<ChartGrid label="Zones" columns={columns} rows={rows} now={NOW} xRange={RANGE} syncKey="zones" height={120} />);
  expect(created).toHaveLength(4);
  const range = (index: number) => (created[index].opts.scales!.y!.range as (u: unknown, min: number, max: number) => [number, number])(created[index], 0, 1);
  // One range per row, the same for both cells, holding the band and both zones.
  expect(range(0)).toEqual(range(1));
  expect(range(0)[0]).toBeLessThan(62);
  expect(range(0)[1]).toBeGreaterThan(70);
  expect(range(2)).toEqual(range(3));
  expect(created.map((plot) => plot.opts.axes![1].size)).toEqual([STACK_AXIS_PX, 0, STACK_AXIS_PX, 0]);
  expect(created.map((plot) => plot.opts.axes![0].size === 0)).toEqual([true, true, false, false]);
  // The first column's plot reaches back over the row name's column for its axis, so every plot is one width.
  expect([...document.querySelectorAll(".grid-cell")].map((cell) => cell.classList.contains("grid-cell-first"))).toEqual([true, false, true, false]);
  // Each cell is a small multiple: a 6 px top pad and no "Now" or band words on it (the head and foot say them once).
  expect(created.map((plot) => plot.opts.padding![0])).toEqual([6, 6, 6, 6]);
  created[0].draw();
  expect(created[0].ctx.texts.map((item) => item.text)).toEqual([]);
  // The readout heads its plot, with its unit. The window ended an hour before now, on the day before: the newest
  // values read with their date and time, never "now".
  expect([...document.querySelectorAll(".grid-cell")].map((cell) => cell.querySelector(".grid-readout + .plot") !== null)).toEqual([true, true, true, true]);
  const readouts = () => [...document.querySelectorAll(".grid-readout")].map((node) => text(node));
  expect(readouts()).toEqual(["64.0 °F at Nov 2, 23:55", "67.0 °F at Nov 2, 23:55", "0.70 kPa at Nov 2, 23:55", "0.90 kPa at Nov 2, 23:55"]);
  act(() => created[3].hover(FROM + HOUR, 12));
  expect(readouts()[0]).toMatch(/^64\.0 °F at \d\d:\d\d$/);
  expect(rowRange([], RANGE)).toEqual([0, 1]);
  expect(cellReadout(buildTimeSeriesModel({ series: null, metrics: [TEMP], now: NOW, xRange: RANGE }), null, false)).toBe("—");
  // A live window whose newest bucket is the latest reads "now"; one whose probe went quiet hours ago reads its time.
  const live = { from: FROM, to: TO + 6 * HOUR };
  const fresh = buildTimeSeriesModel({ series: series(300, { "climate.temp_avg": () => 65 }), metrics: [TEMP], now: TO + 60, xRange: live });
  expect(cellReadout(fresh, null, false, TO + 60)).toBe("65.0\u00a0°F now");
  const quiet = buildTimeSeriesModel({ series: series(300, { "climate.temp_avg": (index) => (index < 100 ? 65 : null) }), metrics: [TEMP], now: TO + 60, xRange: live });
  expect(cellReadout(quiet, null, false, TO + 60)).toBe("65.0\u00a0°F at Nov 2, 08:15");
  // The grid's data table: one block per row, a column per zone, in the row's unit (redesign §8).
  const table = screen.getByText(/^Data table · /).closest("details")!;
  expect(table.closest(".chart-grid")).not.toBeNull();
  act(() => { table.open = true; table.dispatchEvent(new Event("toggle")); });
  const captions = [...table.querySelectorAll("caption")].map((caption) => caption.textContent);
  expect(captions).toEqual(["Temperature · °F · America/Denver", "VPD · kPa · America/Denver"]);
  expect([...table.querySelectorAll("table")[0].querySelectorAll("thead th")].map((cell) => cell.textContent)).toEqual(["Time", "North (°F)", "South (°F)"]);
  expect([...table.querySelectorAll("table")[0].querySelectorAll("tbody tr")[0].querySelectorAll("td")].map((cell) => cell.textContent?.replaceAll("\u00a0", " "))).toEqual(["64.0 °F", "67.0 °F"]);
});
