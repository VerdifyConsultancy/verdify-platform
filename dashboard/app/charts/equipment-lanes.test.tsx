// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { EquipmentLanes, buildLanes, lanesCsv, lanesKey, mergeSpans, mismatchSpans, stateWords, type EquipmentLanesProps } from "./equipment-lanes";
import metricsFixture from "./fixtures/metrics.json";
import holeFixture from "./fixtures/series-hole.json";
import statesFixture from "./fixtures/states.json";
import { FakeResizeObserver, MockPlot, TOKENS, created, load, media, setTokens, type Stroke } from "./fixtures/uplot-mock";
import { InStack } from "./stack-context";
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
/** What shows at first: each system's header, then its rows that ran; idle rows fold under their header and Sensors folds whole. */
const SHOWN = ["Climate", "Fan 1", "Vent", "Heat 1 (electric)", "Heat 2 (gas)", "Misting", "Center mister", "Irrigation", "Wall drip", "Lighting", "Main grow light (Lutron)", "Grow light (Lutron)", "Sensors"];
/** Each shown row's top at the 20 px pitch: a header is 20 px too. */
const TOP: Record<string, number> = { fan1: 20, vent: 40, heat1: 60, heat2: 80, mister_center: 120, drip_wall: 160, grow_light_main: 200, grow_light_grow: 220 };
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
/** The band of the row whose top is `top`: 3 px in from each edge. */
const band = (top: number, pitch = 20) => ({ top: top + 3, bottom: top + pitch - 3 });
const inBand = (stroke: Stroke, top: number, pitch = 20) => stroke.path.every(([, y]) => y >= band(top, pitch).top && y <= band(top, pitch).bottom);
const shown = (container: HTMLElement) => [...container.querySelectorAll(".lanes-group-name, .lanes-row .lanes-label")].map((label) => label.textContent);

beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ narrow: false, fine: true }));
  setTokens();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style"); });

test("rows come by system with headers, in design order with the API's labels; the eyebrow says WHAT RAN; the lanes have their own time axis", async () => {
  const { container, plot } = await mount();
  expect(container.querySelector("figcaption .eyebrow")!.textContent).toBe("EQUIPMENT · WHAT RAN");
  expect(shown(container)).toEqual(SHOWN);
  // The API sends both intent lanes and each fertilized valve; none is a row: a line's two valves are one row.
  expect(container.querySelector("[data-equipment$=_intent], [data-equipment$=_fert]")).toBeNull();
  const model = buildLanes(load<StateLanes>(statesFixture));
  expect(model.rows.find((row) => row.equipment === "drip_wall")!.members).toEqual(["drip_wall", "drip_wall_fert"]);
  expect(model.groups.map((group) => [group.system, group.idle.map((row) => row.label)])).toEqual([
    ["climate", ["Fan 2"]], ["misting", ["South mister", "West mister", "Fog"]], ["irrigation", ["Fertilizer valve", "Center drip"]], ["lighting", []], ["sensors", ["Unexplained flow", "Occupancy"]],
  ]);
  expect((await openTable(container)).some((cells) => /controller|fertilized/.test(cells[0] ?? ""))).toBe(false);
  // One uPlot instance with an x scale only: the rows are painted in its draw hook, never as series; its time axis is under them.
  expect(plot.opts.series).toHaveLength(1);
  expect(plot.data).toEqual([[RANGE.from, RANGE.to]]);
  expect(plot.opts.axes!.map((axis) => axis.show !== false)).toEqual([true, false]);
  expect(plot.opts.hooks!.draw).toHaveLength(1);
  expect(container.querySelector(".lanes-axis")).not.toBeNull();
});

test("unknown spans are hatched where API-05 returns them and never drawn as off", async () => {
  const { plot, container } = await mount();
  const ctx = plot.ctx;
  expect(ctx.depth).toBe(0);
  // Vent is off, then unknown 09:10–09:40, then off: the unknown span is muted diagonals inside its band.
  const hatch = ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--muted"] && inBand(stroke, TOP.vent));
  expect(hatch).toHaveLength(1);
  const xs = hatch[0].path.map(([px]) => px);
  expect(Math.min(...xs)).toBeLessThan(x("09:10"));
  expect(Math.max(...xs)).toBeGreaterThan(x("09:40") - 14);
  expect(Math.min(...xs)).toBeGreaterThanOrEqual(x("09:10") - 14);
  // Off paints nothing on the row: no fill and no outline in Vent's band.
  expect(ctx.rects.filter((rect) => rect.y >= band(TOP.vent).top && rect.y < band(TOP.vent).bottom)).toEqual([]);
  expect(ctx.outlines.filter((rect) => rect.y >= band(TOP.vent).top && rect.y < band(TOP.vent).bottom)).toEqual([]);
  expect(ctx.texts.find((text) => text.text === "unknown" && text.y === TOP.vent + 3 + 7)).toMatchObject({ color: TOKENS["--muted"] });
  // The same outage hatches every row the API marks unknown, the Lutron row included; nothing else, and never a header.
  const hatched = Object.keys(TOP).filter((key) => ctx.strokes.some((stroke) => stroke.stroke === TOKENS["--muted"] && inBand(stroke, TOP[key])));
  expect(hatched).toEqual(["fan1", "vent", "heat1", "heat2", "mister_center", "grow_light_main"]);
  // In words: the tooltip and the data table say unknown.
  plot.hover(at("09:20"), null, TOP.vent + 10);
  expect(tooltip(plot)).toEqual(["Vent", "unknown", "11/01/2026 02:10 GMT-07:00–02:40 GMT-07:00", `30${NBSP}min`]);
  const rows = await openTable(container);
  expect(rows.filter((cells) => cells[0] === "Vent").map((cells) => cells[1])).toEqual(["off", "unknown", "off"]);
});

test("each system folds its idle rows under its header, which opens them; Sensors folds whole", async () => {
  const { container, plot } = await mount();
  const folds = screen.getAllByRole("button").filter((button) => button.className === "lanes-group-fold");
  expect(folds.map((button) => [button.getAttribute("aria-label"), button.textContent, button.getAttribute("aria-expanded")])).toEqual([
    ["Climate: 1 idle", "1 idle", "false"], ["Misting: 3 idle", "3 idle", "false"], ["Irrigation: 2 idle", "2 idle", "false"], ["Sensors: 3 rows", "3 rows", "false"],
  ]);
  // Lighting has nothing idle, so no fold.
  expect(container.querySelector("[data-system=lighting] button")).toBeNull();
  fireEvent.click(folds[0]);
  expect(folds[0].getAttribute("aria-expanded")).toBe("true");
  expect(shown(container).slice(0, 6)).toEqual(["Climate", "Fan 1", "Vent", "Heat 1 (electric)", "Heat 2 (gas)", "Fan 2"]);
  // Sensors opens whole: water flowing is a solid bar in its colour on its row, under the Sensors header (now at 260).
  fireEvent.click(folds[3]);
  expect(shown(container).slice(-4)).toEqual(["Sensors", "Water flowing", "Unexplained flow", "Occupancy"]);
  expect(plot.dataUpdates.length).toBeGreaterThan(0);
  plot.draw();
  const water = plot.ctx.rects.filter((rect) => rect.color === TOKENS["--eq-water"] && rect.y === band(280).top);
  expect(water.map((rect) => [Math.round(rect.x), Math.round(rect.x + rect.width)])).toEqual([[Math.round(x("07:31")), Math.round(x("07:43"))]]);
});

test("the Lutron rows are solid bars in their colour; where the controller's intent differs an amber strip runs along the row's foot", async () => {
  const { container, plot } = await mount();
  const ctx = plot.ctx;
  const filled = (token: string) => ctx.rects.filter((rect) => rect.color === TOKENS[token]);
  // Main on 06:00–07:00, Grow on 06:30–08:00: filled, never outlined.
  expect(filled("--eq-light-main").map((rect) => [rect.y, Math.round(rect.x), Math.round(rect.x + rect.width)])).toEqual([[band(TOP.grow_light_main).top, 0, Math.round(x("07:00"))]]);
  expect(filled("--eq-light-grow").map((rect) => [rect.y, Math.round(rect.x), Math.round(rect.x + rect.width)])).toEqual([[band(TOP.grow_light_grow).top, Math.round(x("06:30")), Math.round(x("08:00"))]]);
  expect(ctx.outlines).toEqual([]);
  // The intent says on all window: it differs on main 07:00–09:10 (off, then unknown is no mismatch), on grow 06:00–06:30 and 08:00–10:00.
  const amber = filled("--amber").map((rect) => [rect.y, Math.round(rect.x), Math.round(rect.x + rect.width), rect.height]);
  const foot = (key: string) => band(TOP[key]).bottom - 3;
  expect(amber).toEqual([
    [foot("grow_light_main"), Math.round(x("07:00")), Math.round(x("09:10")), 3],
    [foot("grow_light_grow"), 0, Math.round(x("06:30")), 3], [foot("grow_light_grow"), Math.round(x("08:00")), 1000, 3],
  ]);
  expect(container.querySelector("[data-equipment=grow_light_main] .lanes-stat")!.textContent).toBe(`unknown · 60${NBSP}min · 0`);
  plot.hover(at("06:40"), null, TOP.grow_light_grow + 10);
  // 08:00Z is the fall-back instant: 02:00 MDT becomes 01:00 MST.
  expect(tooltip(plot)).toEqual(["Grow light (Lutron)", "on", "11/01/2026 00:30 GMT-06:00–01:00 GMT-07:00", `90${NBSP}min`]);
  plot.hover(at("09:00"), null, TOP.grow_light_grow + 10);
  expect(tooltip(plot)).toEqual(["Grow light (Lutron)", "off", "11/01/2026 01:00 GMT-07:00–03:00 GMT-07:00", `2${NBSP}h`, "controller intent differs"]);
});

test("a line's clean and fertilized valves are one row: on while either is open, fertilized in its own colour, in words and in the CSV", () => {
  const span = (start: number, end: number, state: "on" | "off" | "unknown" = "on") => ({ start, end, state, cause: null });
  expect(mergeSpans([span(0, 10), span(10, 30, "off")], [span(0, 5, "off"), span(5, 20), span(20, 30, "off")])).toEqual([
    { start: 0, end: 5, state: "on", cause: null }, { start: 5, end: 20, state: "on", cause: null, fert: true }, { start: 20, end: 30, state: "off", cause: null },
  ]);
  expect(mergeSpans([span(0, 10, "unknown")], [span(0, 10, "off")])).toEqual([span(0, 10, "unknown")]);
  expect(mismatchSpans([span(0, 10), span(10, 20, "off"), span(20, 30, "unknown")], [span(0, 30)])).toEqual([{ start: 10, end: 20 }]);
  const fixture = load<StateLanes>(statesFixture);
  const states: StateLanes = { ...fixture, lanes: fixture.lanes.map((lane) => (lane.equipment === "drip_wall_fert"
    ? { ...lane, intervals: [{ start: "2026-11-01T06:00:00Z", end: "2026-11-01T08:00:00Z", state: "off" }, { start: "2026-11-01T08:00:00Z", end: "2026-11-01T08:10:00Z", state: "on", cause: "schedule" }, { start: "2026-11-01T08:10:00Z", end: "2026-11-01T10:00:00Z", state: "off" }] }
    : lane)) };
  const model = buildLanes(states);
  const wall = model.rows.find((row) => row.equipment === "drip_wall")!;
  expect(wall.spans.filter((item) => item.state === "on").map((item) => [item.fert ?? false, item.cause])).toEqual([[false, "schedule"], [true, "schedule"]]);
  expect(stateWords(wall, "on", wall.spans.find((item) => item.fert))).toBe("on · fertilized");
  expect(lanesKey(model).map((item) => item.label)).toEqual(["on", "fertilized", "controller intent differs", "unknown"]);
  expect(lanesCsv(model)).toContain(",on fertilized,");
});

test("the key names only marks that are drawn: no intent strip when the lights' readback is unknown all window", () => {
  const fixture = load<StateLanes>(statesFixture);
  const whole = (state: "off" | "unknown") => [{ start: fixture.from, end: fixture.to, state }];
  const states: StateLanes = { ...fixture, lanes: fixture.lanes.map((lane) => (
    lane.equipment === "grow_light_main" || lane.equipment === "grow_light_grow" ? { ...lane, intervals: whole("unknown") } : lane)) };
  const key = lanesKey(buildLanes(states)).map((item) => item.key);
  expect(key).toEqual(["on", "unknown-span"]);
  expect(lanesKey(buildLanes(load<StateLanes>(statesFixture))).map((item) => item.key)).toEqual(["on", "intent", "unknown-span"]);
});

test("a wet line's runs fill in the line's own colour (#1354) and are named by cause after the run; a null cause has no cause word", async () => {
  const { container, plot } = await mount();
  const ctx = plot.ctx;
  // L01 (Wall drip): 07:31–07:43, cause schedule, in the drip line's colour, named after the run.
  const wall = ctx.rects.filter((rect) => rect.y === band(TOP.drip_wall).top);
  expect(wall.map((rect) => [rect.color, Math.round(rect.x), Math.round(rect.x + rect.width)])).toEqual([[TOKENS["--eq-water"], Math.round(x("07:31")), Math.round(x("07:43"))]]);
  expect(ctx.texts.filter((text) => text.text === "schedule" && text.y === band(TOP.drip_wall).top + 7).map((text) => Math.round(text.x))).toEqual([Math.round(x("07:43") + 4)]);
  plot.hover(at("07:35"), null, TOP.drip_wall + 10);
  expect(tooltip(plot)).toEqual(["Wall drip", "on", "11/01/2026 01:31 GMT-06:00–01:43 GMT-06:00", `12${NBSP}min`, "schedule"]);
  // L04 (Center mister): three VPD pulses named once, a scheduled pulse, and a pulse whose cause is still null, all in the mister's colour.
  const center = ctx.rects.filter((rect) => rect.y === band(TOP.mister_center).top);
  expect(center.map((rect) => rect.color)).toEqual(Array(5).fill(TOKENS["--eq-mister"]));
  const words = ctx.texts.filter((text) => text.y === band(TOP.mister_center).top + 7).map((text) => text.text);
  expect(words).toEqual(["VPD", "schedule", "unknown"]);
  // The null-cause pulse at 08:30: plain mister colour, nothing after it, and no cause in the tooltip.
  expect(Math.round(center[4].x)).toBe(Math.round(x("08:30")));
  expect(ctx.texts.some((text) => text.y === band(TOP.mister_center).top + 7 && text.x > x("08:31") && text.x < x("09:10"))).toBe(false);
  plot.hover(at("08:30") + 30, null, TOP.mister_center + 10);
  expect(tooltip(plot)).toEqual(["Center mister", "on", "11/01/2026 01:30 GMT-07:00–01:31 GMT-07:00", `1${NBSP}min`]);
  const rows = await openTable(container);
  const wallRows = rows.filter((cells) => cells[0] === "Wall drip");
  expect(wallRows.map((cells) => [cells[1], cells[4], cells[5]])).toEqual([["off", `91${NBSP}min`, ""], ["on", `12${NBSP}min`, "schedule"], ["off", `2${NBSP}h${NBSP}17${NBSP}min`, ""]]);
  const centerOn = rows.filter((cells) => cells[0] === "Center mister" && cells[1] === "on").map((cells) => cells[5]);
  expect(centerOn).toEqual(["VPD", "VPD", "VPD", "schedule", ""]);
  // Each row is named, so its colour needs no key; the key names on, the intent strip and the hatch.
  expect([...container.querySelectorAll(".chart-key li")].map((item) => item.textContent)).toEqual(["on", "controller intent differs", "unknown"]);
  // The key sits right under the rows it names, above the CSV and the table, which is named by its subject.
  const figure = container.querySelector(".chart-lanes")!;
  expect([...figure.children].map((child) => child.className).filter((name) => /chart-key|chart-tools|chart-table/.test(name))).toEqual(["chart-key", "chart-tools", "chart-table"]);
  expect(figure.querySelector(".chart-table summary")?.textContent).toMatch(/^Lanes table · \d+ rows$/);
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
  fireEvent.click(screen.getByRole("button", { name: "Climate: 1 idle" }));
  expect(stat("fan2")).toBe(`off · 0${NBSP}min · 0`);
  expect(container.querySelector(".lanes-columns")!.textContent).toBe("state · on time · starts");
  cleanup();
  MockPlot.reset();
  vi.stubGlobal("matchMedia", media({ narrow: true, fine: false }));
  const { plot } = await mount();
  // Vent's top at 24 px rows: the Climate header (20), Fan 1 (24).
  expect(plot.ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--muted"] && inBand(stroke, 44, 24))).toHaveLength(1);
  expect(plot.ctx.rects.find((rect) => rect.color === TOKENS["--eq-water"])!.height).toBe(18);
});

test("inside a chart stack: the panels' plot area with the time axis under the lanes; each row's name and facts head its track; the folds are in the headers", async () => {
  const { container } = render(<InStack.Provider value={true}><EquipmentLanes {...props()} /></InStack.Provider>);
  await settle();
  const plot = created[created.length - 1];
  plot.fire("ready");
  plot.draw();
  // No padding (the stylesheet puts the canvas over the panels' plot area), and the stack's one time axis is the lanes'.
  expect(plot.opts.padding).toEqual([0, 0, 0, 0]);
  expect(plot.opts.axes![0].show).not.toBe(false);
  expect(container.querySelector(".lanes")!.className).toBe("lanes lanes-equipment lanes-stacked");
  expect(container.querySelector(".lanes-axis")).not.toBeNull();
  // The disclosure's summary names the lanes; each row says its facts in words.
  expect(container.querySelector("figcaption")).toBeNull();
  expect(container.querySelector("[data-equipment=fan1]")!.textContent).toBe(`Fan 1 off · 80${NBSP}min on · 1 start`);
  expect(container.querySelector("[data-equipment=heat1]")!.textContent).toBe(`Heat 1 (electric) off · 70${NBSP}min on · 0 starts`);
  // Rows are 36 px: a 16 px line for the name, then the band; a header is 20 px. Vent's unknown span is hatched in its band.
  const stackedBand = (top: number) => ({ top: top + 16 + 3, bottom: top + 36 - 3 });
  const inStackedBand = (stroke: Stroke, top: number) => stroke.path.every(([, y]) => y >= stackedBand(top).top && y <= stackedBand(top).bottom);
  expect(plot.ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--muted"] && inStackedBand(stroke, 20 + 36))).toHaveLength(1);
  const fold = screen.getByRole("button", { name: "Climate: 1 idle" });
  expect(fold.closest(".lanes-group")?.getAttribute("data-system")).toBe("climate");
  fireEvent.click(fold);
  expect(fold.getAttribute("aria-expanded")).toBe("true");
  expect(container.querySelector("[data-equipment=fan2]")).not.toBeNull();
});

test("a cursor moved by another chart on the sync key shows no lane tooltip; one past now shows none either", async () => {
  const { plot } = await mount({ xRange: { from: RANGE.from, to: RANGE.to + 12 * 3600 } });
  plot.hover(at("07:35"), null, TOP.drip_wall + 10, true);
  expect(plot.over.querySelector<HTMLElement>(".chart-tooltip")!.hidden).toBe(true);
  plot.hover(RANGE.to + 3600, null, TOP.drip_wall + 10);
  expect(plot.over.querySelector<HTMLElement>(".chart-tooltip")!.hidden).toBe(true);
  // A system's header has no tooltip.
  plot.hover(at("07:35"), null, 10);
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
  const fixture = load<StateLanes>(statesFixture);
  const states: StateLanes = { ...fixture, lanes: fixture.lanes.map((lane) => (lane.equipment === "vent" ? { equipment: lane.equipment, group: lane.group, line_id: lane.line_id, intervals: lane.intervals } : lane)) };
  expect(buildLanes(states).rows.find((row) => row.equipment === "vent")!.label).toBe("vent");
  expect(renderToString(<EquipmentLanes {...props()} />)).not.toMatch(/style=/);
  const { container } = render(<EquipmentLanes {...props({ message: "Equipment lanes cover at most 31 days. Narrow the range to see them" })} />);
  await settle();
  expect(container.querySelector(".chart-message")!.textContent).toBe("Equipment lanes cover at most 31 days. Narrow the range to see them");
  expect(created).toHaveLength(0);
});
