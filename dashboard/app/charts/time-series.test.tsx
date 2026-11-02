// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { dailySeries } from "./fixtures/daily";
import bandFixture from "./fixtures/band.json";
import metricsFixture from "./fixtures/metrics.json";
import reasonsFixture from "./fixtures/series-gap-reasons.json";
import holeFixture from "./fixtures/series-hole.json";
import historyFixture from "./fixtures/setting-history.json";
import { FakeResizeObserver, MockPlot, TOKENS, created, load, media, setTokens } from "./fixtures/uplot-mock";
import { TimeSeries, buildTimeSeriesModel, tickIncrements, traceTokens, type TimeSeriesProps } from "./time-series";
import type { BandTimeline, MetricDef, Series, SettingHistory } from "./types";

vi.mock("uplot", async () => ({ default: (await import("./fixtures/uplot-mock")).MockPlot }));

const NBSP = "\u00a0";
const metrics = load<MetricDef[]>(metricsFixture);
const metric = (key: string) => metrics.find((item) => item.key === key)!;
const epoch = (instant: string) => Date.parse(instant) / 1000;
const NOW = epoch("2026-11-01T10:00:00Z");
const RANGE = { from: epoch("2026-11-01T06:00:00Z"), to: epoch("2026-11-01T22:00:00Z") };
const HOLE = [38, 39, 40, 41, 42, 43];
const settle = () => act(async () => { await vi.dynamicImportSettled(); });

function props(overrides: Partial<TimeSeriesProps> = {}): TimeSeriesProps {
  return { series: load<Series>(holeFixture), metrics: [metric("climate.temp_avg")], now: NOW, xRange: RANGE, ...overrides };
}
async function mount(overrides: Partial<TimeSeriesProps> = {}) {
  const view = render(<TimeSeries {...props(overrides)} />);
  await settle();
  const plot = created[created.length - 1];
  plot.fire("ready");
  return { ...view, plot };
}
async function openTable(container: HTMLElement) {
  const details = container.querySelector<HTMLDetailsElement>("details.chart-table")!;
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  await waitFor(() => expect(details.querySelector("table")).toBeTruthy());
  return details;
}
const tooltip = (plot: MockPlot) => [...plot.over.querySelectorAll(".chart-tooltip > div")].map((line) => line.textContent);

const { createObjectURL, revokeObjectURL } = URL;
beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ narrow: false, fine: true }));
  // jsdom has no Blob URLs.
  URL.createObjectURL = vi.fn(() => "blob:csv");
  URL.revokeObjectURL = vi.fn();
  setTokens();
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style");
  URL.createObjectURL = createObjectURL; URL.revokeObjectURL = revokeObjectURL;
});

test("a 30-minute hole reaches uPlot as nulls with spanGaps false, the summary reads 1 gap, and nothing reads 0", async () => {
  const { container, plot } = await mount();
  const temp = plot.data[1] as (number | null)[];
  expect(HOLE.map((index) => temp[index])).toEqual([null, null, null, null, null, null]);
  expect(temp.filter((value) => value === 0)).toEqual([]);
  expect(plot.opts.series[1]).toMatchObject({ label: "Indoor temperature", spanGaps: false, width: 2, stroke: TOKENS["--c-temp"] });
  // Nothing is joined or faked: x is the Series' own bucket starts, one uPlot series per metric.
  expect(plot.data[0]).toEqual(load<Series>(holeFixture).t);
  expect(plot.opts.series).toHaveLength(2);
  expect(container.querySelector(".plot-summary")!.textContent).toMatch(/, 1 gap$/);
  // The tooltip inside the hole: a dash, never 0, and the outage in words.
  plot.hover(epoch("2026-11-01T09:20:00Z"), 40);
  expect(tooltip(plot)).toEqual(["11/01/2026 02:20 GMT-07:00", "Indoor temperature —", "No data 02:10–02:40: controller connection lost"]);
  // The data table: the run's first row carries the reason; every hole cell is a dash.
  const details = await openTable(container);
  const rows = [...details.querySelectorAll("tbody tr")];
  expect(details.querySelector("summary")!.textContent).toBe("Data table · 48 rows");
  expect(HOLE.map((index) => rows[index].children[1].textContent)).toEqual(["—", "—", "—", "—", "—", "—"]);
  expect(rows[38].lastElementChild!.textContent).toBe("No data 02:10–02:40: controller connection lost");
  expect(rows[39].lastElementChild!.textContent).toBe("");
  expect([...details.querySelectorAll("tbody td")].some((cell) => /^0(\.0)?(\u00a0°F)?$/.test(cell.textContent || ""))).toBe(false);
});

test("a gaps row's reason reads in words, an unknown one raw, and a run with no row only its start and end", async () => {
  const { plot, container } = await mount({ series: load<Series>(reasonsFixture) });
  plot.hover(epoch("2026-11-01T08:25:00Z"), 29);
  expect(tooltip(plot)).toEqual(["11/01/2026 01:25 GMT-07:00", "Indoor temperature —", "No data 01:20 GMT-07:00–01:30 GMT-07:00"]);
  plot.hover(epoch("2026-11-01T09:10:00Z"), 38);
  expect(tooltip(plot).slice(1)).toEqual(["Indoor temperature —", "No data 02:10–02:40: ingestor restarted"]);
  // The trailing bucket is still filling: a dash, and no gap line.
  plot.hover(epoch("2026-11-01T09:55:00Z"), 47);
  expect(tooltip(plot).slice(1)).toEqual(["Indoor temperature —"]);
  expect(container.querySelector(".plot-summary")!.textContent).toMatch(/, 2 gaps$/);
  cleanup();
  const unknown = load<Series>(reasonsFixture);
  unknown.gaps![0].reason = "watchdog_reset";
  const next = await mount({ series: unknown });
  next.plot.hover(epoch("2026-11-01T09:10:00Z"), 38);
  expect(tooltip(next.plot)[2]).toBe("No data 02:10–02:40: watchdog_reset");
});

test("on 2026-11-01 axis, tooltip and data-table times from 01:00 to 01:59 carry their offset, at 1440 and 390 px", async () => {
  const { plot, container } = await mount();
  const values = plot.opts.axes![0].values as (self: unknown, splits: number[], axis: number, space: number, increment: number) => string[];
  const passes = [epoch("2026-11-01T07:00:00Z"), epoch("2026-11-01T07:59:00Z"), epoch("2026-11-01T08:00:00Z"), epoch("2026-11-01T08:59:00Z")];
  expect(values(plot, passes, 0, 140, 3600)).toEqual(["11/01/2026\n01:00 GMT-06:00", "11/01/2026\n01:59 GMT-06:00", "11/01/2026\n01:00 GMT-07:00", "11/01/2026\n01:59 GMT-07:00"]);
  vi.stubGlobal("matchMedia", media({ narrow: true }));
  expect(values(plot, passes, 0, 64, 3600)).toEqual(["01:00 GMT-06:00", "01:59 GMT-06:00", "01:00 GMT-07:00", "01:59 GMT-07:00"]);
  expect(values(plot, [epoch("2026-11-01T06:00:00Z"), epoch("2026-11-01T09:00:00Z")], 0, 64, 3600)).toEqual(["00:00", "02:00"]);
  plot.hover(epoch("2026-11-01T07:30:00Z"), 18);
  expect(tooltip(plot)[0]).toBe("11/01/2026 01:30 GMT-06:00");
  plot.hover(epoch("2026-11-01T08:30:00Z"), 30);
  expect(tooltip(plot)[0]).toBe("11/01/2026 01:30 GMT-07:00");
  const details = await openTable(container);
  const times = [...details.querySelectorAll("tbody tr th")].map((cell) => cell.textContent);
  expect(times.slice(12, 24).every((time) => /^11\/01\/2026 01:[0-5]\d GMT-06:00$/.test(time!))).toBe(true);
  expect(times.slice(24, 36).every((time) => /^11\/01\/2026 01:[0-5]\d GMT-07:00$/.test(time!))).toBe(true);
  expect(details.querySelector("tbody time")!.getAttribute("datetime")).toBe("2026-11-01T06:00:00.000Z");
});

test("steps: SUPERSEDED muted, the forecast rule never AI, a request dashed amber and never the device line", async () => {
  const history = load<SettingHistory>(historyFixture);
  const { plot } = await mount({ metrics: [metric("climate.vpd_avg")], band: load<BandTimeline>(bandFixture), settings: [{ history }] });
  plot.draw();
  const ctx = plot.ctx;
  // Every hook restores the context it changed: uPlot caches its styles between draws.
  expect(ctx.depth).toBe(0);
  const label = (text: string) => ctx.texts.find((item) => item.text === text);
  const kpa = (value: string) => `${value}${NBSP}kPa`;
  expect(label(`${kpa("1.80")} SUPERSEDED`)).toMatchObject({ color: TOKENS["--c-bound"] });
  expect(label(`${kpa("1.70")} PLANNED · AI`)).toMatchObject({ color: TOKENS["--c-vpd"] });
  expect(label(`${kpa("1.65")} Forecast rule · system`)).toMatchObject({ color: TOKENS["--c-vpd"] });
  expect(ctx.texts.filter((item) => item.text.includes("Forecast rule")).every((item) => !item.text.includes("AI"))).toBe(true);
  expect(label("Now")).toMatchObject({ color: TOKENS["--ink"] });
  const strokes = (color: string, dash: number[]) => ctx.strokes.filter((stroke) => stroke.stroke === color && JSON.stringify(stroke.dash) === JSON.stringify(dash));
  // Superseded: muted --c-bound, dashed [8,4]; planned: the series colour, dashed [8,4].
  expect(strokes(TOKENS["--c-bound"], [8, 4])).toHaveLength(2);
  expect(strokes(TOKENS["--c-vpd"], [8, 4])).toHaveLength(2);
  // Requested: amber, dashed [6,4], its own stroke; the device line is solid forest.
  const requested = strokes(TOKENS["--c-requested"], [6, 4]);
  expect(requested).toHaveLength(1);
  expect(requested[0].width).toBe(2);
  const device = strokes(TOKENS["--c-setpoint"], []);
  expect(device).toHaveLength(2);
  expect(device.every((stroke) => stroke.path.every(([, y]) => y !== requested[0].path[1][1]))).toBe(true);
  // The now line: 1.5px ink.
  expect(ctx.strokes.some((stroke) => stroke.stroke === TOKENS["--ink"] && stroke.width === 1.5)).toBe(true);
  // max 2.50 is above the y range: an edge label and no line. min 0.50 is inside it (the crop band dips to 0.51):
  // a dotted line, labelled at the right edge.
  expect(plot.scales.y.max).toBeLessThan(2.5);
  expect(label(`max ${kpa("2.50")} ↑`)).toMatchObject({ color: TOKENS["--c-bound"] });
  expect(plot.scales.y.min).toBeLessThan(0.5);
  expect(label(`min ${kpa("0.50")}`)).toMatchObject({ color: TOKENS["--c-bound"] });
  expect(strokes(TOKENS["--c-bound"], [1, 3])).toHaveLength(1);
  // The band wash under the grid, its 1px edges above it.
  expect(ctx.fills.some((fill) => fill.color === TOKENS["--c-band-wash"])).toBe(true);
  expect(ctx.strokes.some((stroke) => stroke.stroke === TOKENS["--c-band-edge"] && stroke.width === 1)).toBe(true);
  // The tooltip names the request's state, the band and the plan ahead.
  plot.hover(epoch("2026-11-01T09:50:00Z"), 46);
  expect(tooltip(plot).slice(2)).toEqual([`Band in force · DERIVED ${kpa(String(load<BandTimeline>(bandFixture).band.vpd_low[15]!.toFixed(2)))}–${kpa(load<BandTimeline>(bandFixture).band.vpd_high[15]!.toFixed(2))}`, `mister_engage_kpa device ${kpa("1.50")}`, `mister_engage_kpa REQUESTED ${kpa("1.55")} · jason`]);
  plot.hover(epoch("2026-11-01T19:00:00Z"), 47);
  expect(tooltip(plot)).toContain(`mister_engage_kpa SUPERSEDED ${kpa("1.80")} · Iris plan · ${kpa("1.55")} (jason) holds until a newer plan`);
});

test("step labels read at 4.5:1: the RH panel's plan in ink, a request in the amber text token", async () => {
  const history = load<SettingHistory>(historyFixture);
  const percent = (value: number | null) => (value === null ? null : value * 50);
  const rh: SettingHistory = {
    ...history, param: "fog_rh_ceiling_pct", unit: "%", min: 50, max: 95,
    desired: history.desired.map((row) => ({ ...row, param: "fog_rh_ceiling_pct", value: percent(row.value) })),
    plan: history.plan.map((waypoint) => ({ ...waypoint, value: percent(waypoint.value) })),
    readback: history.readback.map((point) => ({ ...point, value: percent(point.value) })),
  };
  const { plot } = await mount({ metrics: [metric("climate.rh_avg")], settings: [{ history: rh }] });
  plot.draw();
  const label = (text: string) => plot.ctx.texts.find((item) => item.text === text);
  // The line keeps --c-rh (3:1 for lines); its label does not.
  expect(plot.ctx.strokes.some((stroke) => stroke.stroke === TOKENS["--c-rh"] && JSON.stringify(stroke.dash) === "[8,4]")).toBe(true);
  expect(label("85.0% PLANNED · AI")).toMatchObject({ color: TOKENS["--ink"] });
  expect(label("77.5% REQUESTED")).toMatchObject({ color: TOKENS["--warn-text"] });
  expect(plot.ctx.strokes.some((stroke) => stroke.stroke === TOKENS["--c-requested"] && JSON.stringify(stroke.dash) === "[6,4]")).toBe(true);
});

test("a bound inside the y range is a dotted line labelled at the right edge", async () => {
  const history = load<SettingHistory>(historyFixture);
  history.max = 1.75;
  const { plot } = await mount({ metrics: [metric("climate.vpd_avg")], settings: [{ history }] });
  plot.draw();
  expect(plot.ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--c-bound"] && JSON.stringify(stroke.dash) === "[1,3]")).toHaveLength(1);
  expect(plot.ctx.texts.some((item) => item.text === `max 1.75${NBSP}kPa`)).toBe(true);
});

test("the y range is the union of the shown traces, band and steps, padded 5%", async () => {
  const history = load<SettingHistory>(historyFixture);
  const band = load<BandTimeline>(bandFixture);
  const { plot } = await mount({ metrics: [metric("climate.vpd_avg")], band, settings: [{ history }] });
  plot.draw();
  const model = buildTimeSeriesModel({ ...props({ metrics: [metric("climate.vpd_avg")], band, settings: [{ history }] }) });
  const low = Math.min(...model.runs.flatMap((run) => run.low)), high = 1.8;
  expect(plot.scales.y.min).toBeCloseTo(low - (high - low) * 0.05, 9);
  expect(plot.scales.y.max).toBeCloseTo(high + (high - low) * 0.05, 9);
});

test("400 daily summaries draw one point per day and say so; heap reads kB and is beyond retention before its retained_from", async () => {
  const series = dailySeries();
  const range = { from: epoch(series.from), to: epoch(series.to) };
  const temp = await mount({ series, metrics: [metric("climate.temp_avg")], now: range.to, xRange: range });
  expect(temp.plot.opts.series[1].points).toMatchObject({ show: true });
  const notes = [...temp.container.querySelectorAll(".chart-notes li")].map((note) => note.textContent);
  expect(notes).toEqual(["one point per day · daily summaries", "Daily summaries before 11/02/2025: raw data kept 365 days"]);
  temp.plot.draw();
  expect(temp.plot.ctx.texts.some((item) => item.text === "Daily summaries before 11/02/2025: raw data kept 365 days")).toBe(true);
  expect(temp.plot.ctx.rects.some((rect) => rect.color === TOKENS["--field"] && rect.width > 0)).toBe(true);
  // The data table says so too, on its first row.
  const table = await openTable(temp.container);
  expect(table.querySelector("tbody tr")!.lastElementChild!.textContent).toBe("Daily summaries before 11/02/2025: raw data kept 365 days");
  cleanup();
  const heap = await mount({ series, metrics: [metric("diagnostics.heap_free_kb")], now: range.to, xRange: range });
  expect([...heap.container.querySelectorAll(".chart-notes li")].map((note) => note.textContent)).toEqual(["beyond retention (180 days kept)"]);
  // No line before retained_from: nulls, which count against coverage but are no gap.
  expect((heap.plot.data[1] as (number | null)[]).slice(0, 200).every((value) => value === null)).toBe(true);
  expect(heap.container.querySelector(".plot-summary")!.textContent).toMatch(/coverage 4\d\.\d%, 0 gaps$/);
  const ticks = heap.plot.opts.axes![1].values as (self: unknown, splits: number[]) => string[];
  expect(ticks(heap.plot, [30, 36])).toEqual([`30${NBSP}kB`, `36${NBSP}kB`]);
  expect(heap.plot.opts.axes![1].incrs).toEqual(tickIncrements(0));
  heap.plot.hover(range.from + 10 * 86_400, 10);
  expect(tooltip(heap.plot).slice(1)).toEqual(["Free heap —", "beyond retention (180 days kept)"]);
});

test("a stale series stops at its last sample and says so; nothing is extended to now", async () => {
  const series = load<Series>(holeFixture);
  const avg = series.series["climate.temp_avg"].avg;
  for (let index = 30; index < avg.length; index++) avg[index] = null;
  const { plot, container } = await mount({ series });
  expect(container.querySelector(".chart-note-stale")!.textContent).toBe("STALE · last data 01:25 GMT-07:00");
  expect((plot.data[1] as (number | null)[]).slice(30).every((value) => value === null)).toBe(true);
  expect(container.querySelector(".plot-summary")!.textContent).toMatch(/, 0 gaps$/);
});

test("legend: none for one series; with two, buttons toggle setSeries and the table and CSV follow what is plotted", async () => {
  const single = await mount();
  expect(single.container.querySelector(".chart-legend")).toBeNull();
  cleanup();
  const series = load<Series>(holeFixture);
  series.series["climate.outdoor_temp_f"] = { ...series.series["climate.temp_avg"], avg: series.series["climate.temp_avg"].avg.map((value) => (value === null ? null : value - 20)) };
  const urls: Blob[] = [];
  URL.createObjectURL = vi.fn((blob: Blob) => { urls.push(blob); return `blob:csv-${urls.length}`; });
  URL.revokeObjectURL = vi.fn();
  const { plot, container } = await mount({ series, metrics: [metric("climate.temp_avg"), metric("climate.outdoor_temp_f")] });
  expect(plot.opts.series.map((item) => item.stroke)).toEqual([undefined, TOKENS["--c-temp"], TOKENS["--c-outdoor"]]);
  const legend = within(container.querySelector<HTMLElement>(".chart-legend")!);
  const outdoor = legend.getByRole("button", { name: "Outdoor temperature" });
  expect(outdoor.getAttribute("aria-pressed")).toBe("true");
  expect(outdoor.querySelector(".chart-swatch")!.getAttribute("data-token")).toBe("c-outdoor");
  const link = screen.getByRole("link", { name: "CSV of this window" });
  expect(link.getAttribute("download")).toMatch(/^verdify-indoor-temperature-outdoor-temperature-f-2026-11-01T0000-2026-11-01T1500\.csv$/);
  // A byte-order mark leads the file (Blob.text() drops it when decoding).
  expect([...new Uint8Array(await urls[0].arrayBuffer()).slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  expect((await urls[0].text()).split("\r\n")[0]).toBe("time (America/Denver),climate.temp_avg (°F),climate.outdoor_temp_f (°F)");
  fireEvent.click(outdoor);
  expect(outdoor.getAttribute("aria-pressed")).toBe("false");
  expect(plot.seriesCalls).toEqual([{ index: 2, show: false }]);
  await waitFor(() => expect(urls).toHaveLength(2));
  expect((await urls[1].text()).split("\r\n")[0]).toBe("time (America/Denver),climate.temp_avg (°F)");
  const details = await openTable(container);
  expect([...details.querySelectorAll("thead th")].map((cell) => cell.textContent)).toEqual(["Time", "Indoor temperature (°F)", "Note"]);
  vi.stubGlobal("matchMedia", media({ narrow: true }));
  cleanup();
  const narrow = await mount({ series, metrics: [metric("climate.temp_avg"), metric("climate.outdoor_temp_f")] });
  expect(narrow.container.querySelector("details.chart-legend > summary")!.textContent).toBe("Legend");
});

test("the plot is an image that names what is plotted and says a table follows; markup has no style attribute", async () => {
  const { container } = await mount({ metrics: [metric("climate.vpd_avg")], band: load<BandTimeline>(bandFixture), settings: [{ history: load<SettingHistory>(historyFixture), label: "VPD Mister Engage" }] });
  const image = container.querySelector("[role=img]")!;
  expect(image.getAttribute("aria-label")).toBe("Indoor VPD in kPa over time, America/Denver, with the band ribbon (Band in force · DERIVED; Crop band reconstructed from today's anchors), with setting steps for VPD Mister Engage. Gaps are left empty, never zero. The data table follows.");
  expect(container.querySelector("figcaption")!.textContent).toBe("Indoor VPD · kPa");
  expect(container.querySelectorAll("[style]")).toHaveLength(0);
});

test("one unit per plot: a metric in another unit is left out; colours never repeat within a panel", () => {
  const model = buildTimeSeriesModel(props({ metrics: [metric("climate.temp_avg"), metric("climate.rh_avg")] }));
  expect(model.traces.map((trace) => trace.key)).toEqual(["climate.temp_avg"]);
  expect(traceTokens([metric("climate.rh_avg"), metric("climate.soil_moisture_south_1")], "%")).toEqual(["--c-rh", "--eq-fan2"]);
  expect(traceTokens([metric("climate.soil_moisture_south_1"), metric("climate.rh_avg")], "%")).toEqual(["--eq-fan2", "--c-rh"]);
  expect(traceTokens([metric("climate.flow_gpm")], "gal/min")).toEqual(["--eq-fan2"]);
  expect(tickIncrements(2).slice(0, 6)).toEqual([0.01, 0.02, 0.05, 0.1, 0.2, 0.25]);
});

test("a parent re-render with fresh arrays of the same data repaints nothing; a newer Series updates the chart in place", async () => {
  const series = load<Series>(holeFixture);
  const history = load<SettingHistory>(historyFixture);
  const base = { series, metrics: [metric("climate.vpd_avg")], settings: [{ history }] };
  const { plot, rerender } = await mount(base);
  const blobs = vi.mocked(URL.createObjectURL).mock.calls.length;
  rerender(<TimeSeries {...props({ ...base, metrics: [metric("climate.vpd_avg")], settings: [{ history }] })} />);
  expect(plot.dataUpdates).toHaveLength(0);
  expect(vi.mocked(URL.createObjectURL).mock.calls.length).toBe(blobs);
  // The next poll: the same chart takes the new data, and no second uPlot is built.
  const newer = load<Series>(holeFixture);
  newer.series["climate.vpd_avg"].avg[47] = 0.97;
  rerender(<TimeSeries {...props({ ...base, series: newer })} />);
  expect(plot.dataUpdates).toHaveLength(1);
  expect((plot.dataUpdates[0][1] as (number | null)[])[47]).toBe(0.97);
  expect(created).toHaveLength(1);
});
