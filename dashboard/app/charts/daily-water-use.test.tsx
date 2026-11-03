// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Daily water use (#1140): API-21's days to the bars, with no-data days null, 0 gal as 0 and the no-valve series apart from
// the stacks; each day's two bars beside each other; the hatch, the partial edge, the key, the tooltip, the summary and the
// data table in the same whole gallons.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { NBSP } from "../components/test-render";
import type { WaterUse } from "../lib/operator-api";
import { DailyWaterUse, NO_VALVE_WORDS, buildWaterUse, lineToken, waterKey, waterSummary, type WaterSplit } from "./daily-water-use";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens } from "./fixtures/uplot-mock";

vi.mock("uplot", async () => ({ default: (await import("./fixtures/uplot-mock")).MockPlot }));

const NOW = "2026-11-02T17:42:00.000Z";
const gal = (value: number) => `${value}${NBSP}gal`;
const settle = () => act(async () => { await vi.dynamicImportSettled(); });
const same = (a: number[], b: number[]) => JSON.stringify(a) === JSON.stringify(b);
const day = (date: string, fields: Partial<WaterUse["days"][number]> = {}): WaterUse["days"][number] => ({ date, coverage: "complete", meter_gal: 10, by_line: { L04: 6 }, by_cause: { vpd: 6 }, no_valve_gal: 4, ...fields });
const NO_DATA = { coverage: "no_data", meter_gal: null, by_line: null, by_cause: null, no_valve_gal: null };
/** 10/29 to 11/02: a clean drip under a feed, a no-data day, a day of no water, a hose left on, and today partial. */
const WEEK: Pick<WaterUse, "days"> = {
  days: [
    day("2026-10-29", { meter_gal: 21, by_line: { L02: 5, L01: 9, L04: 4 }, by_cause: { feed: 5, schedule: 9, vpd: 4 }, no_valve_gal: 3 }),
    day("2026-10-30", NO_DATA),
    day("2026-10-31", { meter_gal: 0, by_line: {}, by_cause: {}, no_valve_gal: 0 }),
    day("2026-11-01", { meter_gal: 20, by_line: { L04: 6 }, by_cause: { vpd: 6 }, no_valve_gal: 14 }),
    day("2026-11-02", { coverage: "partial", meter_gal: 8, by_line: { L04: 3, L11: 2 }, by_cause: { vpd: 5 }, no_valve_gal: 3 }),
  ],
};
const NAMES = { L01: "Drip • Wall", L02: "Drip • Wall (Fert.)", L04: "Mister • Center", L11: "Fog" };
async function mount(split: WaterSplit = "line", data: Pick<WaterUse, "days"> = WEEK) {
  const view = render(<DailyWaterUse data={data} split={split} lineNames={NAMES} />);
  await settle();
  const plot = created[0];
  plot.fire("ready");
  plot.draw();
  return { ...view, plot };
}
const tooltip = (plot: MockPlot) => [...plot.over.querySelectorAll(".chart-tooltip > div")].map((line) => line.textContent);
const centre = (plot: MockPlot, index: number) => plot.valToPos((plot.data[0] as number[])[index], "x");
const row = (container: HTMLElement, index: number) => [...container.querySelectorAll("tbody tr")[index].children].map((cell) => cell.textContent);

beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ narrow: false, fine: true }));
  setTokens();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style"); });

test("the transform: a no_data day stays null, 0 stays 0, and the no-valve series stays out of the stacks", () => {
  const model = buildWaterUse(WEEK, "line", NAMES);
  // Clean below fertilizer, whatever order the API lists them.
  expect(model.segments.map((segment) => [segment.key, segment.token, segment.label])).toEqual([
    ["L01", "--eq-water", "L01 Drip • Wall"], ["L04", "--eq-mister", "L04 Mister • Center"], ["L11", "--eq-fog", "L11 Fog"], ["L02", "--eq-fert", "L02 Drip • Wall (Fert.)"],
  ]);
  // Each column is the running total up to it; the no-data day is null in all, and the day of no water 0 in all.
  expect(model.stacks).toEqual([
    [9, null, 0, 0, 0],
    [13, null, 0, 6, 3],
    [13, null, 0, 6, 5],
    [18, null, 0, 6, 5],
  ]);
  expect(model.noValve).toEqual([3, null, 0, 14, 3]);
  expect(model.days.map((item) => item.stacked)).toEqual([18, null, 0, 6, 5]);
  expect(model.days.map((item) => item.meter)).toEqual([21, null, 0, 20, 8]);
  // Drawn as API-21 returns it: 18 gal of parts under a 21 gal day are not rescaled, and nothing sums to the meter.
  expect(model.total).toBe(49);
  expect(model.noValveTotal).toBe(20);
  expect(model.noData).toBe(1);
  // The scale takes the taller of a day's two bars, with room above; never less than 10 gal.
  expect(model.yMax).toBe(21);
  expect(buildWaterUse({ days: [day("2026-11-02", { meter_gal: 0, by_line: {}, by_cause: {}, no_valve_gal: 0 })] }, "line").yMax).toBe(10);
});

test("the transform by cause: the feed above the clean water, a cause it does not know raw, in the cause table's colours", () => {
  const model = buildWaterUse({ days: [day("2026-11-02", { by_cause: { feed: 5, schedule: 9, vpd: 4, hail: 1 } })] }, "cause");
  expect(model.segments.map((segment) => [segment.key, segment.token, segment.label])).toEqual([
    ["vpd", "--eq-mister", "VPD"], ["schedule", "--eq-water", "Schedule"], ["hail", "--c-bound", "Hail"], ["feed", "--eq-fert", "Feed"],
  ]);
  expect(model.stacks.map((column) => column[0])).toEqual([4, 13, 14, 19]);
});

test("a partial day with no split yet keeps its null, a part it lacks is 0, and a newer coverage reads partial", () => {
  const model = buildWaterUse({ days: [day("2026-11-01"), day("2026-11-02", { coverage: "catching_up", by_line: null, meter_gal: null, no_valve_gal: 2 })] }, "line");
  expect(model.days[1].coverage).toBe("partial");
  expect(model.stacks).toEqual([[6, null]]);
  expect(model.noValve).toEqual([4, 2]);
  expect(buildWaterUse({ days: [day("2026-11-01", { by_line: { L01: 2 } }), day("2026-11-02", { by_line: { L04: 3 } })] }, "line").stacks).toEqual([[2, 0], [2, 3]]);
});

test("each line takes its own token by kind: drips, fertilizer lines, the master, misters and fog", () => {
  expect(["L01", "L05", "L02", "L06", "L08", "L10", "L03", "L04", "L07", "L09", "L11", "L12"].map(lineToken)).toEqual([
    "--eq-water", "--eq-water", "--eq-fert", "--eq-fert", "--eq-fert", "--eq-fert", "--eq-master", "--eq-mister", "--eq-mister", "--eq-mister", "--eq-fog", "--c-bound",
  ]);
});

test("the chart: the stack left of each day and the no-valve bar right of it, in --fault, with no band tying them", async () => {
  const { plot } = await mount();
  const { series, bands } = plot.opts;
  expect(series.slice(1).map((item) => item.label)).toEqual(["L01 Drip • Wall", "L04 Mister • Center", "L11 Fog", "L02 Drip • Wall (Fert.)", NO_VALVE_WORDS]);
  const bars = series.slice(1).map((item) => (item.paths as unknown as { bars: { align: number; size: number[] } }).bars);
  expect(bars.map((item) => item.align)).toEqual([-1, -1, -1, -1, 1]);
  expect(series[5].fill).toBe(TOKENS["--fault"]);
  expect(series[1].fill).toBe(TOKENS["--eq-water"]);
  expect(series[4].fill).toBe(TOKENS["--eq-fert"]);
  // Each part runs from the one below it; the no-valve series (index 5) is in no band.
  expect(bands).toEqual([{ series: [2, 1] }, { series: [3, 2] }, { series: [4, 3] }]);
  // Nulls stay null in uPlot's columns, and the x column holds the America/Denver midnights.
  expect(plot.data.slice(1).map((values) => (values as (number | null)[])[1])).toEqual([null, null, null, null, null]);
  expect(plot.data[5]).toEqual([3, null, 0, 14, 3]);
  expect(plot.data[0]).toEqual(["2026-10-29T06:00:00Z", "2026-10-30T06:00:00Z", "2026-10-31T06:00:00Z", "2026-11-01T06:00:00Z", "2026-11-02T07:00:00Z"].map((at) => Date.parse(at) / 1000));
  expect(plot.scales.y).toEqual({ min: 0, max: 21 });
});

test("a no-data day is a hatched gap across both bars and today has a dashed top edge on each", async () => {
  const { plot } = await mount();
  const ctx = plot.ctx;
  expect(ctx.depth).toBe(0);
  const hatch = ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--muted"] && same(stroke.dash, []));
  expect(hatch).toHaveLength(1);
  expect(new Set(hatch[0].path.map(([, y]) => y))).toEqual(new Set([0, 300]));
  // Each bar is 18 px at most here (40% of a 200 px day), so the hatch starts one height left of the 18 px stack.
  expect(Math.min(...hatch[0].path.map(([px]) => px))).toBeCloseTo(centre(plot, 1) - 18 - 300, 6);
  const edges = ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--ink"] && same(stroke.dash, [3, 2]));
  expect(edges.map((stroke) => [Math.round(stroke.path[0][0]), Math.round(stroke.path[1][0]), stroke.path[0][1]])).toEqual([
    [Math.round(centre(plot, 4) - 18), Math.round(centre(plot, 4)), plot.valToPos(5, "y")],
    [Math.round(centre(plot, 4)), Math.round(centre(plot, 4) + 18), plot.valToPos(3, "y")],
  ]);
});

test("the tooltip says the day's meter total, each part, the no-valve water, partial and no data", async () => {
  const { plot } = await mount();
  const at = (index: number) => { plot.hover((plot.data[0] as number[])[index], index, 30); return tooltip(plot); };
  expect(at(0)).toEqual(["Oct 29", `${gal(21)} on the meter`, `L01 Drip • Wall · ${gal(9)}`, `L04 Mister • Center · ${gal(4)}`, `L02 Drip • Wall (Fert.) · ${gal(5)}`, `No Verdify valve open · ${gal(3)}`]);
  expect(at(1)).toEqual(["Oct 30", "no data"]);
  expect(at(2)).toEqual(["Oct 31", `${gal(0)} on the meter`, `No Verdify valve open · ${gal(0)}`]);
  expect(at(4)).toEqual(["Nov 2", `${gal(8)} on the meter`, "partial", `L04 Mister • Center · ${gal(3)}`, `L11 Fog · ${gal(2)}`, `No Verdify valve open · ${gal(3)}`]);
});

test("the key names the no-valve bar in --fault, hatched no data and the dashed partial day, and lines of one kind share an entry", () => {
  const items = waterKey(buildWaterUse({ days: [...WEEK.days, day("2026-11-03", { by_line: { L07: 1 } })] }, "line", NAMES));
  expect(items.map((item) => [item.token, item.kind, item.label])).toEqual([
    ["--eq-water", "fill", "L01 Drip • Wall"], ["--eq-mister", "fill", "L04 Mister • Center, L07"], ["--eq-fog", "fill", "L11 Fog"], ["--eq-fert", "fill", "L02 Drip • Wall (Fert.)"],
    ["--fault", "fill", "No Verdify valve open: hose, sink, hydro or a leak"], ["--muted", "hatch", "no data"], ["--ink", "dashed", "partial day"],
  ]);
});

test("the summary gives the window's total and its no-valve gallons, and the data table the same whole gallons, — on a no-data day", async () => {
  const { container } = await mount();
  expect(container.querySelector(".water-summary")?.textContent).toBe(`Last 5 days: ${gal(49)} · ${gal(20)} with no Verdify valve open · 1 day with no data`);
  const table = container.querySelector("details.chart-table")!;
  expect(table.querySelector("summary")?.textContent).toBe("Data table · 5 rows");
  expect(table.querySelector("tbody")).toBeNull();
  (table as HTMLDetailsElement).open = true;
  fireEvent(table, new Event("toggle"));
  expect([...container.querySelectorAll("thead th")].map((cell) => cell.textContent)).toEqual(["Day", "Total", "L01 Drip • Wall", "L04 Mister • Center", "L11 Fog", "L02 Drip • Wall (Fert.)", "No valve", "Coverage"]);
  expect(row(container, 0)).toEqual(["Oct 29", gal(21), gal(9), gal(4), gal(0), gal(5), gal(3), "complete"]);
  expect(row(container, 1)).toEqual(["Oct 30", "—", "—", "—", "—", "—", "—", "no data"]);
  expect(row(container, 2)).toEqual(["Oct 31", gal(0), gal(0), gal(0), gal(0), gal(0), gal(0), "complete"]);
  expect(row(container, 3)).toEqual(["Nov 1", gal(20), gal(0), gal(6), gal(0), gal(0), gal(14), "complete"]);
  expect(row(container, 4)).toEqual(["Nov 2", gal(8), gal(0), gal(3), gal(2), gal(0), gal(3), "partial"]);
});

test("by cause redraws the same days cut by cause, and the chart is rebuilt for its new series", async () => {
  const { rerender } = await mount("line");
  rerender(<DailyWaterUse data={WEEK} split="cause" lineNames={NAMES} />);
  await settle();
  const plot = created[1];
  expect(plot.opts.series.slice(1).map((item) => item.label)).toEqual(["VPD", "Schedule", "Feed", NO_VALVE_WORDS]);
  expect(plot.data[1]).toEqual([4, null, 0, 6, 5]);
  expect(plot.data[3]).toEqual([18, null, 0, 6, 5]);
  expect(plot.data[4]).toEqual([3, null, 0, 14, 3]);
  expect(screen.getByRole("figure").querySelector("figcaption")?.textContent).toBe("Gallons per day by cause · last 5 days");
});

test("a window with no days says so", async () => {
  render(<DailyWaterUse data={{ days: [] }} split="line" />);
  await settle();
  expect(created).toHaveLength(0);
  expect(screen.getByText("No water data in this window")).toBeTruthy();
});

test("water-use-90d: 90 days with one no-data day, one complete day of 0 gal, 14 gal with no valve open, and today partial", async () => {
  const data = fixture("water-use-90d", { now: NOW })["API-21"];
  const model = buildWaterUse(data, "line");
  expect(model.days).toHaveLength(90);
  expect(model.days.filter((item) => item.coverage === "no_data").map((item) => item.date)).toEqual(["2026-10-24"]);
  const zero = model.days.find((item) => item.date === "2026-10-29");
  expect(zero).toMatchObject({ coverage: "complete", meter: 0, stacked: 0, noValve: 0 });
  expect(model.days.find((item) => item.date === "2026-10-27")).toMatchObject({ noValve: 14 });
  expect(model.days.at(-1)?.coverage).toBe("partial");
  expect(waterSummary(model)).toContain("1 day with no data");
});
