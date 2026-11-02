// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { EquipmentLanes, buildLanes, type EquipmentLanesProps } from "./equipment-lanes";
import metricsFixture from "./fixtures/metrics.json";
import holeFixture from "./fixtures/series-hole.json";
import statesFixture from "./fixtures/states.json";
import { FakeResizeObserver, MockPlot, TOKENS, created, load, media, setTokens, type Stroke } from "./fixtures/uplot-mock";
import { TimeSeries } from "./time-series";
import type { MetricDef, Series, StateLanes } from "./types";
import { resetZoom } from "./uplot-chart";

vi.mock("uplot", async () => ({ default: (await import("./fixtures/uplot-mock")).MockPlot }));

const NBSP = " ";
const epoch = (instant: string) => Date.parse(instant) / 1000;
const at = (clock: string) => epoch(`2026-11-01T${clock}:00Z`);
const RANGE = { from: at("06:00"), to: at("10:00") };
const KEY = "verdify:graphs:24h";
const settle = () => act(async () => { await vi.dynamicImportSettled(); });
// The mock's plot is 1000 px wide over the 4 h window, from x = 0.
const x = (clock: string) => ((at(clock) - RANGE.from) / (RANGE.to - RANGE.from)) * 1000;
const ACTIVE = ["Fan 1", "Vent", "Heat 1 (electric)", "Heat 2 (gas)", "L01 Wall drip", "L04 Center mister", "Main grow light (Lutron)", "Grow light (Lutron)", "Water flowing"];
const IDLE = ["Fan 2", "L02 Wall feed", "L03 Fert master", "L05 Center drip", "L06 Center feed", "L07 South mister", "L08 South feed mister", "L09 West mister", "L10 West feed mister", "L11 Fog", "Occupancy", "Unexplained flow"];

function props(overrides: Partial<EquipmentLanesProps> = {}): EquipmentLanesProps {
  return { states: load<StateLanes>(statesFixture), xRange: RANGE, syncKey: KEY, now: RANGE.to, ...overrides };
}
async function mount(overrides: Partial<EquipmentLanesProps> = {}) {
  const view = render(<EquipmentLanes {...props(overrides)} />);
  await settle();
  const plot = created[created.length - 1];
  plot.fire("ready");
  plot.draw();
  return { ...view, plot };
}
async function openTable(container: HTMLElement) {
  const details = container.querySelector<HTMLDetailsElement>("details.chart-table")!;
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  await waitFor(() => expect(details.querySelector("table")).toBeTruthy());
  return [...details.querySelectorAll("tbody tr")].map((row) => [...row.children].map((cell) => cell.textContent));
}
const tooltip = (plot: MockPlot) => [...plot.over.querySelectorAll(".chart-tooltip > div")].map((line) => line.textContent);
/** Row `index`'s band at the 20 px pitch: 3 px in from each edge. */
const band = (index: number, pitch = 20) => ({ top: index * pitch + 3, bottom: index * pitch + pitch - 3 });
const inBand = (stroke: Stroke, index: number, pitch = 20) => stroke.path.every(([, y]) => y >= band(index, pitch).top && y <= band(index, pitch).bottom);

beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ narrow: false, fine: true }));
  setTokens();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style"); });

test("rows come in design order with the API's labels; the eyebrow says COMMANDED; no lane draws an _intent key", async () => {
  const { container, plot } = await mount();
  expect(container.querySelector("figcaption .eyebrow")!.textContent).toBe("EQUIPMENT · COMMANDED");
  const labels = (list: Element) => [...list.querySelectorAll(".lanes-row:not(.lanes-fold) .lanes-label")].map((label) => label.textContent);
  const [active, idle] = container.querySelectorAll("ul.lanes-rows");
  expect(labels(active)).toEqual(ACTIVE);
  expect(labels(idle)).toEqual(IDLE);
  // The API sends both intent lanes; neither is a row, in the DOM, the model or the data table.
  expect(container.querySelector("[data-equipment$=_intent]")).toBeNull();
  const model = buildLanes(load<StateLanes>(statesFixture));
  expect(model.rows.map((row) => row.equipment).filter((key) => key.endsWith("_intent"))).toEqual([]);
  expect((await openTable(container)).some((cells) => /controller/.test(cells[0] ?? ""))).toBe(false);
  // One uPlot instance with an x scale only: the rows are painted in its draw hook, never as series.
  expect(plot.opts.series).toHaveLength(1);
  expect(plot.data).toEqual([[RANGE.from, RANGE.to]]);
  expect(plot.opts.axes!.map((axis) => axis.show !== false)).toEqual([true, false]);
  expect(plot.opts.hooks!.draw).toHaveLength(1);
});

test("unknown spans are hatched where API-05 returns them and never drawn as off", async () => {
  const { plot, container } = await mount();
  const ctx = plot.ctx;
  expect(ctx.depth).toBe(0);
  // Vent (row 1) is off, then unknown 09:10–09:40, then off: the unknown span is muted diagonals inside its band.
  const hatch = ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--muted"] && inBand(stroke, 1));
  expect(hatch).toHaveLength(1);
  const xs = hatch[0].path.map(([px]) => px);
  expect(Math.min(...xs)).toBeLessThan(x("09:10"));
  expect(Math.max(...xs)).toBeGreaterThan(x("09:40") - 14);
  expect(Math.min(...xs)).toBeGreaterThanOrEqual(x("09:10") - 14);
  // Off paints nothing on the row: no fill and no outline in Vent's band.
  expect(ctx.rects.filter((rect) => rect.y >= band(1).top && rect.y < band(1).bottom)).toEqual([]);
  expect(ctx.outlines.filter((rect) => rect.y >= band(1).top && rect.y < band(1).bottom)).toEqual([]);
  expect(ctx.texts.find((text) => text.text === "unknown" && text.y === 1 * 20 + 3 + 7)).toMatchObject({ color: TOKENS["--muted"] });
  // The same outage hatches every row the API marks unknown, the Lutron row included; nothing else does.
  const hatchedRows = [0, 1, 2, 3, 4, 5, 6, 7, 8].filter((index) => ctx.strokes.some((stroke) => stroke.stroke === TOKENS["--muted"] && inBand(stroke, index)));
  expect(hatchedRows).toEqual([0, 1, 2, 3, 5, 6]);
  // In words: the tooltip and the data table say unknown.
  plot.hover(at("09:20"), null, 1 * 20 + 10);
  expect(tooltip(plot)).toEqual(["Vent", "unknown", "11/01/2026 02:10 GMT-07:00–02:40 GMT-07:00", `30${NBSP}min`]);
  const rows = await openTable(container);
  expect(rows.filter((cells) => cells[0] === "Vent").map((cells) => cells[1])).toEqual(["off", "unknown", "off"]);
});

test("an all-idle row folds into the one \"12 idle rows\" line, which expands them", async () => {
  const { container, plot } = await mount();
  const button = screen.getByRole("button", { name: "12 idle rows" });
  expect(button.getAttribute("aria-expanded")).toBe("false");
  const idle = document.getElementById(button.getAttribute("aria-controls")!) as HTMLUListElement;
  expect(container.contains(idle)).toBe(true);
  expect(idle.hidden).toBe(true);
  // Folded, the canvas paints the nine active rows and leaves the fold line (row 9) empty.
  const painted = (index: number) => plot.ctx.rects.some((rect) => rect.y === band(index).top) || plot.ctx.outlines.some((rect) => rect.y > band(index).top - 1 && rect.y < band(index).bottom) || plot.ctx.strokes.some((stroke) => inBand(stroke, index));
  expect([0, 1, 2, 3, 4, 5, 6, 7, 8].every(painted)).toBe(true);
  expect(painted(9)).toBe(false);
  fireEvent.click(button);
  expect(button.getAttribute("aria-expanded")).toBe("true");
  expect(idle.hidden).toBe(false);
  expect([...idle.querySelectorAll(".lanes-label")].map((label) => label.textContent)).toEqual(IDLE);
  // The new rows reach the chart as marks: it repaints them, all off, so still nothing below the fold line.
  expect(plot.dataUpdates.length).toBeGreaterThan(0);
  plot.draw();
  expect([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21].some(painted)).toBe(false);
});

test("the Lutron rows draw the readback outlined as sensed, and the controller's intent never draws", async () => {
  const { container, plot } = await mount();
  const ctx = plot.ctx;
  const outlined = (token: string) => ctx.outlines.filter((rect) => rect.color === TOKENS[token]);
  // Main grow light (row 6) on 06:00–07:00, Grow light (row 7) on 06:30–08:00, Water flowing (row 8) on 07:31–07:43.
  expect(outlined("--eq-light-main").map((rect) => [Math.round(rect.x), rect.y > band(6).top - 1 && rect.y < band(6).bottom])).toEqual([[1, true]]);
  expect(outlined("--eq-light-grow")).toHaveLength(1);
  expect(outlined("--eq-light-grow")[0].y).toBeGreaterThan(band(7).top - 1);
  expect(outlined("--eq-water")).toHaveLength(1);
  expect(outlined("--eq-water")[0].y).toBeGreaterThan(band(8).top - 1);
  expect(outlined("--eq-light-main")[0].width).toBe(1.5);
  // Sensed rows are never filled.
  expect(ctx.rects.some((rect) => [TOKENS["--eq-light-main"], TOKENS["--eq-light-grow"]].includes(rect.color))).toBe(false);
  // The intent lanes say "on" all window: had either drawn, a mark would span the whole plot. Only the three sensed spans
  // are outlined.
  expect(ctx.outlines).toHaveLength(3);
  expect([...ctx.rects.map((rect) => rect.width), ...ctx.outlines.map((rect) => rect.w)].some((width) => width > 990)).toBe(false);
  expect(container.querySelector("[data-equipment=grow_light_main] .lanes-stat")!.textContent).toBe(`unknown · sensed · 60${NBSP}min · 0`);
  plot.hover(at("06:40"), null, 7 * 20 + 10);
  // 08:00Z is the fall-back instant: 02:00 MDT becomes 01:00 MST.
  expect(tooltip(plot)).toEqual(["Grow light (Lutron)", "on · sensed", "11/01/2026 00:30 GMT-06:00–01:00 GMT-07:00", `90${NBSP}min`]);
});

test("a wet line's schedule interval names \"schedule\"; a null cause draws in the line's colour with no cause word", async () => {
  const { container, plot } = await mount();
  const ctx = plot.ctx;
  // L01 (row 4): 07:31–07:43, cause schedule, in the schedule colour, named after the run.
  const wall = ctx.rects.filter((rect) => rect.y === band(4).top);
  expect(wall.map((rect) => [rect.color, Math.round(rect.x), Math.round(rect.x + rect.width)])).toEqual([[TOKENS["--eq-water"], Math.round(x("07:31")), Math.round(x("07:43"))]]);
  expect(ctx.texts.filter((text) => text.text === "schedule" && text.y === band(4).top + 7).map((text) => Math.round(text.x))).toEqual([Math.round(x("07:43") + 4)]);
  plot.hover(at("07:35"), null, 4 * 20 + 10);
  expect(tooltip(plot)).toEqual(["L01 Wall drip", "on", "11/01/2026 01:31 GMT-06:00–01:43 GMT-06:00", `12${NBSP}min`, "schedule"]);
  // L04 (row 5): three VPD pulses named once, a scheduled pulse, and a pulse whose cause is still null.
  const center = ctx.rects.filter((rect) => rect.y === band(5).top);
  expect(center.map((rect) => rect.color)).toEqual([TOKENS["--eq-mister"], TOKENS["--eq-mister"], TOKENS["--eq-mister"], TOKENS["--eq-water"], TOKENS["--eq-mister"]]);
  const words = ctx.texts.filter((text) => text.y === band(5).top + 7).map((text) => text.text);
  expect(words).toEqual(["VPD", "schedule", "unknown"]);
  // The null-cause pulse at 08:30: plain mister colour, nothing after it, and no cause in the tooltip.
  expect(Math.round(center[4].x)).toBe(Math.round(x("08:30")));
  expect(ctx.texts.some((text) => text.y === band(5).top + 7 && text.x > x("08:31") && text.x < x("09:10"))).toBe(false);
  plot.hover(at("08:30") + 30, null, 5 * 20 + 10);
  expect(tooltip(plot)).toEqual(["L04 Center mister", "on", "11/01/2026 01:30 GMT-07:00–01:31 GMT-07:00", `1${NBSP}min`]);
  const rows = await openTable(container);
  const wallRows = rows.filter((cells) => cells[0] === "L01 Wall drip");
  expect(wallRows.map((cells) => [cells[1], cells[4], cells[5]])).toEqual([["off", `91${NBSP}min`, ""], ["on", `12${NBSP}min`, "schedule"], ["off", `2${NBSP}h${NBSP}17${NBSP}min`, ""]]);
  const centerOn = rows.filter((cells) => cells[0] === "L04 Center mister" && cells[1] === "on").map((cells) => cells[5]);
  expect(centerOn).toEqual(["VPD", "VPD", "VPD", "schedule", ""]);
  expect([...container.querySelectorAll(".chart-key li")].map((item) => item.textContent)).toEqual(["VPD", "schedule", "unknown", "sensed"]);
});

test("each row ends with its state, on time and starts; at 860 px and below the rows are 24 px", async () => {
  const { container } = await mount();
  const stat = (key: string) => container.querySelector(`[data-equipment=${key}] .lanes-stat`)!.textContent;
  // Fan 1: on 40 min, off, on 40 min (one OFF→ON edge), then off and unknown; it ends off.
  expect(stat("fan1")).toBe(`off · 80${NBSP}min · 1`);
  // Heat 1 is on when the window opens: that is no start.
  expect(stat("heat1")).toBe(`off · 70${NBSP}min · 0`);
  expect(stat("drip_wall")).toBe(`off · 12${NBSP}min · 1`);
  expect(stat("mister_center")).toBe(`off · 9${NBSP}min · 5`);
  expect(stat("fan2")).toBe(`off · 0${NBSP}min · 0`);
  expect(container.querySelector(".lanes-columns")!.textContent).toBe("state · on time · starts");
  cleanup();
  MockPlot.reset();
  vi.stubGlobal("matchMedia", media({ narrow: true, fine: false }));
  const { plot } = await mount();
  expect(plot.ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--muted"] && inBand(stroke, 1, 24))).toHaveLength(1);
  expect(plot.ctx.rects.find((rect) => rect.color === TOKENS["--eq-water"])!.height).toBe(18);
});

test("a cursor moved by another chart on the sync key shows no lane tooltip; one past now shows none either", async () => {
  const { plot } = await mount({ xRange: { from: RANGE.from, to: RANGE.to + 12 * 3600 } });
  plot.hover(at("07:35"), null, 4 * 20 + 10, true);
  expect(plot.over.querySelector<HTMLElement>(".chart-tooltip")!.hidden).toBe(true);
  plot.hover(RANGE.to + 3600, null, 4 * 20 + 10);
  expect(plot.over.querySelector<HTMLElement>(".chart-tooltip")!.hidden).toBe(true);
  // The fold line has no tooltip.
  plot.hover(at("07:35"), null, 9 * 20 + 10);
  expect(plot.over.querySelector<HTMLElement>(".chart-tooltip")!.hidden).toBe(true);
});

test("the lanes and the TimeSeries panels on one page share cursor.sync.key, and Reset zoom puts both back", async () => {
  const metric = load<MetricDef[]>(metricsFixture).find((item) => item.key === "climate.temp_avg")!;
  const page = { from: epoch("2026-11-01T06:00:00Z"), to: epoch("2026-11-01T22:00:00Z") };
  render(<>
    <TimeSeries series={load<Series>(holeFixture)} metrics={[metric]} now={epoch("2026-11-01T10:00:00Z")} xRange={page} syncKey={KEY} linked />
    <EquipmentLanes {...props({ xRange: page })} />
  </>);
  await settle();
  expect(created).toHaveLength(2);
  const [panel, lanes] = created;
  expect(MockPlot.sync(KEY).plots).toEqual([panel, lanes]);
  expect(lanes.opts.cursor!.sync).toEqual(panel.opts.cursor!.sync);
  expect(lanes.opts.cursor!.sync).toEqual({ key: KEY, scales: ["x", null], setSeries: false });
  expect(lanes.opts.scales!.x).toMatchObject({ time: true, min: page.from, max: page.to });
  lanes.setScale("x", { min: page.from + 3600, max: page.from + 7200 });
  panel.setScale("x", { min: page.from + 3600, max: page.from + 7200 });
  resetZoom(KEY, page);
  expect([panel.scales.x, lanes.scales.x]).toEqual([{ min: page.from, max: page.to }, { min: page.from, max: page.to }]);
});

test("a drag zoom on the lanes offers Reset zoom, which puts every chart on the sync key back", async () => {
  const { plot } = await mount();
  expect(screen.queryByRole("button", { name: "Reset zoom" })).toBeNull();
  act(() => plot.setScale("x", { min: at("07:00"), max: at("08:00") }));
  fireEvent.click(screen.getByRole("button", { name: "Reset zoom" }));
  expect(plot.scales.x).toEqual({ min: RANGE.from, max: RANGE.to });
  expect(screen.queryByRole("button", { name: "Reset zoom" })).toBeNull();
  cleanup();
  MockPlot.reset();
  const page = await mount({ zoomControl: false });
  act(() => page.plot.setScale("x", { min: at("07:00"), max: at("08:00") }));
  expect(screen.queryByRole("button", { name: "Reset zoom" })).toBeNull();
});

test("labels are the API's, never made up; a message stands in for the lanes; the server markup has no style attribute", async () => {
  const states = load<StateLanes>(statesFixture);
  delete states.lanes.find((lane) => lane.equipment === "vent")!.label;
  expect(buildLanes(states).rows.find((row) => row.equipment === "vent")!.label).toBe("vent");
  expect(renderToString(<EquipmentLanes {...props()} />)).not.toMatch(/style=/);
  const { container } = render(<EquipmentLanes {...props({ message: "Equipment lanes cover at most 31 days. Narrow the range to see them" })} />);
  await settle();
  expect(container.querySelector(".chart-message")!.textContent).toBe("Equipment lanes cover at most 31 days. Narrow the range to see them");
  expect(created).toHaveLength(0);
});
