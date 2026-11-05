// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import minutesFixture from "./fixtures/minutes.json";
import { FakeResizeObserver, MockPlot, TOKENS, created, load, media, setTokens } from "./fixtures/uplot-mock";
import { MinutesByZone, buildMinutes, minutesKey, type MinutesByZoneProps } from "./minutes-by-zone";
import type { MinutesReport } from "./types";

vi.mock("uplot", async () => ({ default: (await import("./fixtures/uplot-mock")).MockPlot }));

const NBSP = " ";
const min = (value: number) => `${value}${NBSP}min`;
const epoch = (instant: string) => Date.parse(instant) / 1000;
// 10:00 on Monday 11/02/2026, America/Denver: today is 11/02, a partial day.
const NOW = epoch("2026-11-02T17:00:00Z");
const NAMES = { L01: "Wall drip", L02: "Wall feed", L04: "Center mister", L07: "South mister", L11: "Fog" };
const CAPS = { wall: 60, center: 120, south: 45, west: 45 };
const settle = () => act(async () => { await vi.dynamicImportSettled(); });
const same = (a: number[], b: number[]) => JSON.stringify(a) === JSON.stringify(b);

function props(overrides: Partial<MinutesByZoneProps> = {}): MinutesByZoneProps {
  return { report: load<MinutesReport>(minutesFixture), caps: CAPS, planned: { wall: 12, center: 20 }, now: NOW, lineNames: NAMES, ...overrides };
}
async function mount(overrides: Partial<MinutesByZoneProps> = {}) {
  const view = render(<MinutesByZone {...props(overrides)} />);
  await settle();
  for (const plot of created) { plot.fire("ready"); plot.draw(); }
  const [wall, center, south, west, fog] = created;
  return { ...view, wall, center, south, west, fog };
}
const tooltip = (plot: MockPlot) => [...plot.over.querySelectorAll(".chart-tooltip > div")].map((line) => line.textContent);
/** Day `index`'s bar centre on the mock's 1000 px plot. */
const centre = (plot: MockPlot, index: number) => plot.valToPos((plot.data[0] as number[])[index], "x");

beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ narrow: false, fine: true }));
  setTokens();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style"); });

test("a no-data day is a hatched full-height gap, never a zero bar, and today is marked partial", async () => {
  const { container, center } = await mount();
  expect(created).toHaveLength(5);
  // 10/30 (index 3) has no data: every stacked series is null there in every row, never 0.
  for (const plot of created) for (const values of plot.data.slice(1)) expect((values as (number | null)[])[3]).toBeNull();
  const ctx = center.ctx;
  expect(ctx.depth).toBe(0);
  // --c-bound (the cap) has --muted's value: the hatch is the solid one.
  const hatch = ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--muted"] && same(stroke.dash, []));
  expect(hatch).toHaveLength(1);
  expect(new Set(hatch[0].path.map(([, y]) => y))).toEqual(new Set([0, 300]));
  // The diagonals start one plot height left of the bar, which is 48 px wide and centred on the day.
  expect(Math.min(...hatch[0].path.map(([px]) => px))).toBeCloseTo(centre(center, 3) - 24 - 300, 6);
  // Today (11/02, 46 min) and the outage day 10/31 (54 min) are partial: a dashed ink top edge on each bar.
  const edges = ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--ink"] && same(stroke.dash, [3, 2]));
  expect(edges.map((stroke) => [Math.round(stroke.path[0][0]), Math.round(stroke.path[1][0]), stroke.path[0][1]])).toEqual([
    [Math.round(centre(center, 4) - 24), Math.round(centre(center, 4) + 24), center.valToPos(54, "y")],
    [Math.round(centre(center, 6) - 24), Math.round(centre(center, 6) + 24), center.valToPos(46, "y")],
  ]);
  center.hover((center.data[0] as number[])[6], 6, 30);
  expect(tooltip(center)).toEqual(["Nov 2 · Center", min(46), "partial", `L04 Center mister · VPD · ${min(46)} · 3 gal`, `still planned today ${min(20)}`, `cap 120 ${"min"}`]);
  center.hover((center.data[0] as number[])[3], 3, 30);
  expect(tooltip(center)).toEqual(["Oct 30 · Center", "no data"]);
  expect([...container.querySelectorAll(".minutes-today")].map((cell) => cell.textContent)).toEqual([
    `today ${min(0)} (partial) · cap 60`, `today ${min(46)} (partial) · cap 120`, `today ${min(0)} (partial) · cap 45`, `today ${min(0)} (partial) · cap 45`, `today ${min(6)} (partial)`,
  ]);
  const details = container.querySelector<HTMLDetailsElement>("details.chart-table")!;
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  await waitFor(() => expect(details.querySelector("table")).toBeTruthy());
  expect(details.querySelector("summary")!.textContent).toBe("Data table · 35 rows");
  const centerRows = [...details.querySelectorAll(".table-wrap")[1].querySelectorAll("tbody tr")].map((row) => [...row.children].map((cell) => cell.textContent));
  expect(centerRows[3]).toEqual(["Oct 30", "—", "", "no data"]);
  expect(centerRows[6]).toEqual(["Nov 2", min(46), `L04 Center mister · VPD · ${min(46)} · 3 gal; still planned today ${min(20)}`, "partial"]);
});

test("bars are uPlot.paths.bars stacked by cause, clean below fertilizer, each coloured as the cause table says", async () => {
  const { wall, center, west } = await mount();
  expect(wall.opts.series.slice(1).map((series) => series.label)).toEqual(["schedule", "run now", "prewet", "flush", "cause unknown", "feed · fertilizer"]);
  expect(wall.opts.series.slice(1).map((series) => series.fill)).toEqual(["--eq-water", "--eq-master", "--eq-fog", "--eq-fog", "--c-bound", "--eq-fert"].map((token) => TOKENS[token]));
  for (const series of wall.opts.series.slice(1)) expect((series.paths as unknown as { bars: unknown }).bars).toEqual({ size: [0.6, 48], align: 0 });
  // Each segment's bar runs from the running total below it.
  expect(wall.opts.bands).toEqual([{ series: [2, 1] }, { series: [3, 2] }, { series: [4, 3] }, { series: [5, 4] }, { series: [6, 5] }]);
  // 10/28: prewet 2 and flush 3 on L01, then the 8 min feed on L02 on top; L03's supply minutes never count.
  expect(wall.data.slice(1).map((values) => (values as (number | null)[])[1])).toEqual([0, 0, 2, 5, 5, 13]);
  expect(wall.data.slice(1).map((values) => (values as (number | null)[])[0])).toEqual([12, 12, 12, 12, 12, 12]);
  expect(center.data.slice(1)).toEqual([[52, 47, 61, null, 44, 58, 46], [52, 47, 61, null, 54, 58, 46]]);
  // West watered on no day: no series, no bars, and its days read 0, never no data.
  expect(west.opts.series).toHaveLength(1);
  const model = buildMinutes({ report: load<MinutesReport>(minutesFixture), caps: CAPS, now: NOW });
  expect(model.zones[3].days.map((day) => day.total)).toEqual([0, 0, 0, null, 0, 0, 0]);
});

test("the cap is a dotted line, today's still-planned minutes a dashed outline on today's bar; fog has neither", async () => {
  const { center, fog } = await mount();
  const cap = center.ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--c-bound"] && same(stroke.dash, [1, 3]));
  expect(cap.map((stroke) => stroke.path)).toEqual([[[0, center.valToPos(120, "y")], [1000, center.valToPos(120, "y")]]]);
  const outline = center.ctx.outlines.filter((rect) => rect.color === TOKENS["--muted"]);
  expect(outline).toHaveLength(1);
  expect(outline[0]).toMatchObject({ dash: [3, 2], y: center.valToPos(66, "y"), h: center.valToPos(46, "y") - center.valToPos(66, "y") });
  expect(outline[0].x).toBeCloseTo(centre(center, 6) - 24, 6);
  expect(fog.ctx.strokes.some((stroke) => same(stroke.dash, [1, 3]))).toBe(false);
  expect(fog.ctx.outlines).toEqual([]);
  // The four watering zones share one minutes scale (the 120 min cap, 15% over), fog its own.
  expect(created.map((plot) => plot.scales.y.max)).toEqual([138, 138, 138, 138, 35]);
});

test("the tooltip and the data table split each day by line and cause in words, with whole gallons and no rate", async () => {
  const { container, wall, fog } = await mount();
  wall.hover((wall.data[0] as number[])[1], 1, 30);
  expect(tooltip(wall)).toEqual(["Oct 28 · Wall", min(13), `L01 Wall drip · prewet · ${min(2)} · 1 gal`, `L01 Wall drip · flush · ${min(3)} · 2 gal`, `L02 Wall feed · feed · ${min(8)} · 4 gal`, "cap 60 min"]);
  fog.hover((fog.data[0] as number[])[0], 0, 30);
  expect(tooltip(fog)).toEqual(["Oct 27 · Fog", min(30), `L11 Fog · VPD · ${min(30)}`]);
  // Without the screen's line names the line id stands alone.
  cleanup();
  MockPlot.reset();
  const next = await mount({ lineNames: undefined });
  next.wall.hover((next.wall.data[0] as number[])[0], 0, 30);
  expect(tooltip(next.wall)).toEqual(["Oct 27 · Wall", min(12), `L01 · schedule · ${min(12)} · 9 gal`, "cap 60 min"]);
  expect(container.textContent).not.toMatch(/gal\/min|flow/);
  // A cursor moved from another row shows no tooltip here.
  next.center.hover((next.center.data[0] as number[])[0], 0, 30, true);
  expect(next.center.over.querySelector<HTMLElement>(".chart-tooltip")!.hidden).toBe(true);
});

test("rows share one cursor, have no drag zoom, and only the last shows the day labels; 30 days work as 7 do", async () => {
  const { container } = await mount();
  expect(container.querySelector("figcaption")!.textContent).toBe("Watering minutes by zone · last 7 days");
  expect([...container.querySelectorAll(".minutes-label")].map((label) => label.firstChild!.textContent)).toEqual(["Wall", "Center", "South", "West", "Fog"]);
  const keys = new Set(created.map((plot) => plot.opts.cursor!.sync!.key));
  expect(keys).toEqual(new Set(["verdify:minutes:2026-10-27:2026-11-02"]));
  expect(created.every((plot) => plot.opts.cursor!.drag!.x === false && plot.opts.cursor!.drag!.setScale === false)).toBe(true);
  expect(created.map((plot) => plot.opts.axes![0].show)).toEqual([false, false, false, false, true]);
  const values = created[4].opts.axes![0].values as (self: unknown, splits: number[]) => string[];
  expect(values(null, [epoch("2026-10-27T06:00:00Z"), epoch("2026-11-02T07:00:00Z")])).toEqual(["Oct 27", "Nov 2"]);
  expect([...container.querySelectorAll(".chart-key li")].map((item) => item.textContent)).toEqual(["VPD", "schedule", "run now", "feed", "prewet", "flush", "cause unknown", "no data", "partial day", "still planned today", "daily cap"]);
  // 30 days, oldest first: one bar slot per day and the window half a day past each end.
  const month: MinutesReport = {
    ...load<MinutesReport>(minutesFixture), from: "2026-10-04",
    periods: Array.from({ length: 30 }, (_, day) => ({ start: new Date(Date.UTC(2026, 9, 4 + day)).toISOString().slice(0, 10), coverage: day === 29 ? "partial" : "complete" })),
  };
  const model = buildMinutes({ report: month, now: NOW });
  expect(model.days).toHaveLength(30);
  expect(model.days[29].day).toBe("2026-11-02");
  expect(model.xRange).toEqual({ from: epoch("2026-10-04T06:00:00Z") - 43_200, to: epoch("2026-11-02T07:00:00Z") + 43_200 });
  expect(model.zones[1].days[29].total).toBe(46);
});

test("a no-data today is hatched with no planned outline, and the key does not list one", async () => {
  const fixture = load<MinutesReport>(minutesFixture);
  const report: MinutesReport = {
    ...fixture,
    periods: fixture.periods.map((period) => (period.start === "2026-11-02" ? { ...period, coverage: "no_data" } : period)),
    rows: fixture.rows.filter((row) => row.period !== "2026-11-02"),
  };
  const { container, center } = await mount({ report });
  expect(center.ctx.outlines).toEqual([]);
  expect(container.querySelector("[data-zone=center] .minutes-today")!.textContent).toBe("today — · cap 120");
  expect(minutesKey(buildMinutes({ report, caps: CAPS, planned: { center: 20 }, now: NOW })).map((item) => item.key)).not.toContain("planned");
  expect(minutesKey(buildMinutes({ report: load<MinutesReport>(minutesFixture), caps: CAPS, planned: { center: 20 }, now: NOW })).map((item) => item.key)).toContain("planned");
});

test("a report grouped by cause has no zones to draw, and the server markup has no style attribute", async () => {
  expect(renderToString(<MinutesByZone {...props()} />)).not.toMatch(/style=/);
  const report = { ...load<MinutesReport>(minutesFixture), group_by: "cause" as const };
  const { container } = render(<MinutesByZone {...props({ report })} />);
  await settle();
  expect(container.querySelector(".chart-message")!.textContent).toBe("Minutes by zone needs the report by line or by zone");
  expect(created).toHaveLength(0);
});

test("Group by line (#1135): one row per line in the report, in line order, each line's own cap and plan; fog keeps its scale", async () => {
  const { container } = await mount({ group: "line", caps: { L01: 60, L04: 120, L07: 45 }, planned: { L01: 12 } });
  expect(container.querySelector("figcaption")!.textContent).toBe("Watering minutes by line · last 7 days");
  expect([...container.querySelectorAll(".minutes-row")].map((row) => row.getAttribute("data-row"))).toEqual(["L01", "L02", "L03", "L04", "L07", "L11"]);
  expect([...container.querySelectorAll(".minutes-label")].map((label) => label.textContent)).toEqual(["L01 Wall drip", "L02 Wall feed", "L03", "L04 Center mister", "L07 South mister", "L11 Fog humidification"]);
  const model = buildMinutes({ report: load<MinutesReport>(minutesFixture), group: "line", now: NOW });
  expect(model.zones.map((row) => [row.key, row.zone, row.fog, row.days.map((day) => day.total)])).toEqual([
    ["L01", null, false, [12, 5, 10, null, 5, 12, 0]], ["L02", null, false, [0, 8, 0, null, 0, 0, 0]], ["L03", null, false, [0, 8, 0, null, 0, 0, 0]],
    ["L04", null, false, [52, 47, 61, null, 54, 58, 46]], ["L07", null, false, [0, 0, 12, null, 0, 0, 0]], ["L11", null, true, [30, 18, 24, null, 9, 21, 6]],
  ]);
  // Caps and the planned outline are keyed by the row's line; the watering rows share one scale, fog has its own.
  expect([...container.querySelectorAll(".minutes-today")].map((cell) => cell.textContent)).toEqual([
    `today ${min(0)} (partial) · cap 60`, `today ${min(0)} (partial)`, `today ${min(0)} (partial)`, `today ${min(46)} (partial) · cap 120`, `today ${min(0)} (partial) · cap 45`, `today ${min(6)} (partial)`,
  ]);
  expect(created.map((plot) => plot.scales.y.max)).toEqual([138, 138, 138, 138, 138, 35]);
  expect(created[0].ctx.outlines.filter((rect) => rect.color === TOKENS["--muted"])).toHaveLength(1);
});

test("Group by cause (#1135): one row per cause in cause order, fog apart, the fertilizer master never counted twice", async () => {
  const { container } = await mount({ group: "cause", caps: undefined, planned: { schedule: 12 } });
  expect(container.querySelector("figcaption")!.textContent).toBe("Watering minutes by cause · last 7 days");
  expect([...container.querySelectorAll(".minutes-label")].map((label) => label.textContent)).toEqual(["VPD", "Schedule", "Run now", "Feed", "Prewet", "Flush", "Cause unknown", "Fog humidification"]);
  const model = buildMinutes({ report: load<MinutesReport>(minutesFixture), group: "cause", now: NOW });
  expect(Object.fromEntries(model.zones.map((row) => [row.key, row.days.map((day) => day.total)]))).toEqual({
    vpd: [52, 47, 73, null, 44, 58, 46], schedule: [12, 0, 0, null, 10, 12, 0], run_now: [0, 0, 10, null, 0, 0, 0], feed: [0, 8, 0, null, 0, 0, 0],
    prewet: [0, 2, 0, null, 0, 0, 0], flush: [0, 3, 0, null, 0, 0, 0], unknown: [0, 0, 0, null, 5, 0, 0], fog: [30, 18, 24, null, 9, 21, 6],
  });
  // A cause row splits by line in the tooltip and the table: L04 and L07 both mist on VPD.
  const vpd = created[0];
  vpd.hover((vpd.data[0] as number[])[2], 2, 30);
  expect(tooltip(vpd)).toEqual(["Oct 29 · VPD", min(73), `L04 Center mister · VPD · ${min(61)} · 4 gal`, `L07 South mister · VPD · ${min(12)} · 1 gal`]);
  expect(container.querySelector("[data-row=schedule] .minutes-today")!.textContent).toBe(`today ${min(0)} (partial)`);
  expect(created.map((plot) => plot.scales.y.max)).toEqual([84, 84, 84, 84, 84, 84, 84, 35]);
});

test("Group by from a report by zone: by cause still works, by line says it needs the report by line", async () => {
  const zones: MinutesReport = {
    from: "2026-11-01", to: "2026-11-02", group_by: "zone", periods: [{ start: "2026-11-01", coverage: "complete" }, { start: "2026-11-02", coverage: "partial" }],
    rows: [
      { period: "2026-11-01", group: "center", cause: "vpd", chemistry: "clean", minutes: 40 }, { period: "2026-11-01", group: "wall", cause: "schedule", chemistry: "clean", minutes: 12 },
      { period: "2026-11-01", group: "fog", cause: "vpd", chemistry: "clean", minutes: 20 },
    ],
  };
  expect(buildMinutes({ report: zones, group: "cause", now: NOW }).zones.map((row) => [row.key, row.days.map((day) => day.total)])).toEqual([["vpd", [40, 0]], ["schedule", [12, 0]], ["fog", [20, 0]]]);
  const { container } = render(<MinutesByZone {...props({ report: zones, group: "line" })} />);
  await settle();
  expect(container.querySelector(".chart-message")!.textContent).toBe("Minutes by line needs the report by line");
  cleanup();
  // A report with no watering at all has no line to draw.
  const quiet = { ...load<MinutesReport>(minutesFixture), rows: [] };
  const empty = render(<MinutesByZone {...props({ report: quiet, group: "line" })} />);
  await settle();
  expect(empty.container.querySelector(".chart-message")!.textContent).toBe("No watering data in this window");
});
