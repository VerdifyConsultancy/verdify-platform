// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import planFixture from "./fixtures/plan.json";
import { FakeResizeObserver, MockPlot, TOKENS, created, load, media, setTokens, type Stroke } from "./fixtures/uplot-mock";
import { FORECAST_RULE, PLANNED_AI } from "./marks";
import { PlanLanes, planRows, plainValue, waypointWho, type PlanLanesProps } from "./plan-lanes";
import type { Plan } from "./types";

vi.mock("uplot", async () => ({ default: (await import("./fixtures/uplot-mock")).MockPlot }));

const NBSP = " ";
const epoch = (instant: string) => Date.parse(instant) / 1000;
// 10:00 on Monday 11/02/2026, America/Denver (GMT-07:00).
const NOW = epoch("2026-11-02T17:00:00Z");
const HORIZON = NOW + 24 * 3600;
const settle = () => act(async () => { await vi.dynamicImportSettled(); });
// The mock's plot is 1000 px wide over the 24 h window and starts at y = 0; rows are 44 px (a 20 px header, a 24 px strip).
const x = (instant: string) => ((epoch(instant) - NOW) / (HORIZON - NOW)) * 1000;
const lineY = (row: number) => row * 44 + 20 + 16;
const labelY = (row: number) => row * 44 + 20 + 7;
const onRow = (stroke: Stroke, row: number) => stroke.path.every(([, y]) => y === lineY(row));
const same = (a: number[], b: number[]) => JSON.stringify(a) === JSON.stringify(b);

function props(overrides: Partial<PlanLanesProps> = {}): PlanLanesProps {
  return { plan: load<Plan>(planFixture), now: NOW, takeover: "band/default", syncKey: "verdify:plan:24h", ...overrides };
}
async function mount(overrides: Partial<PlanLanesProps> = {}) {
  const view = render(<PlanLanes {...props(overrides)} />);
  await settle();
  const plot = created[created.length - 1];
  plot.fire("ready");
  plot.draw();
  return { ...view, plot };
}
const status = (container: HTMLElement, param: string) => container.querySelector(`[data-param=${param}] .plan-status`)?.textContent ?? null;
const tooltip = (plot: MockPlot) => [...plot.over.querySelectorAll(".chart-tooltip > div")].map((line) => line.textContent);

beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ narrow: false, fine: true }));
  setTokens();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style"); });

test("the superseded lane reads YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan, and its waypoints draw muted as SUPERSEDED", async () => {
  // Plan written 07:02 with a 13:00 waypoint of 1.70; a non-manual desired row of 1.55 at 08:00 holds it off.
  const { container, plot } = await mount();
  expect(status(container, "mister_engage_kpa")).toBe("YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan");
  const [row] = planRows(load<Plan>(planFixture), NOW, HORIZON);
  expect(row.segments.map((segment) => [segment.kind, segment.value, segment.start, segment.end])).toEqual([
    ["superseded", 1.6, NOW, epoch("2026-11-02T20:00:00Z")],
    ["superseded", 1.7, epoch("2026-11-02T20:00:00Z"), epoch("2026-11-03T00:30:00Z")],
  ]);
  const ctx = plot.ctx;
  expect(ctx.depth).toBe(0);
  const muted = ctx.strokes.filter((stroke) => onRow(stroke, 0) && stroke.stroke === TOKENS["--c-bound"] && same(stroke.dash, [8, 4]));
  expect(muted.map((stroke) => stroke.path.map(([px]) => Math.round(px)))).toEqual([[0, Math.round(x("2026-11-02T20:00:00Z"))], [Math.round(x("2026-11-02T20:00:00Z")), Math.round(x("2026-11-03T00:30:00Z"))]]);
  // Nothing on the lane is drawn as the plan ahead.
  expect(ctx.strokes.filter((stroke) => onRow(stroke, 0) && stroke.stroke !== TOKENS["--c-bound"])).toEqual([]);
  const labels = ctx.texts.filter((text) => text.y === labelY(0));
  expect(labels.map((text) => [text.text, text.color])).toEqual([[`1.60${NBSP}kPa SUPERSEDED`, TOKENS["--c-bound"]], [`1.70${NBSP}kPa SUPERSEDED`, TOKENS["--c-bound"]]]);
  plot.hover(epoch("2026-11-02T21:00:00Z"), null, 0 * 44 + 30);
  expect(tooltip(plot)).toEqual([
    "11/02/2026 14:00 GMT-07:00", "VPD Mister Engage",
    `SUPERSEDED 1.70${NBSP}kPa · Iris plan · your value 1.55${NBSP}kPa (Jason) holds until Iris's next plan`,
    "YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan",
  ]);
});

test("a forecast-rule waypoint reads \"Forecast rule · system\" with no AI; Iris's waypoints read PLANNED · AI", async () => {
  const { container, plot } = await mount();
  expect(status(container, "fog_rh_ceiling_pct")).toBe(FORECAST_RULE);
  expect(container.querySelector("[data-param=fog_rh_ceiling_pct]")!.textContent).not.toMatch(/\bAI\b/);
  expect(status(container, "mister_pulse_gap_s")).toBe(PLANNED_AI);
  const ctx = plot.ctx;
  const texts = (row: number) => ctx.texts.filter((text) => text.y === labelY(row)).map((text) => text.text);
  expect(texts(2)).toEqual(["85.0% Forecast rule · system"]);
  expect(texts(2).some((text) => /\bAI\b/.test(text))).toBe(false);
  // The device value holds solid until the waypoint; the rule's value is dashed to the plan's end.
  const strokes = ctx.strokes.filter((stroke) => onRow(stroke, 2)).map((stroke) => [stroke.stroke, stroke.dash, ...stroke.path.map(([px]) => Math.round(px))]);
  expect(strokes).toEqual([
    [TOKENS["--c-setpoint"], [], 0, Math.round(x("2026-11-02T19:00:00Z"))],
    [TOKENS["--ink"], [8, 4], Math.round(x("2026-11-02T19:00:00Z")), Math.round(x("2026-11-03T00:30:00Z"))],
  ]);
  // Iris's lane: each waypoint to the next, the last to the plan's end; a label that does not fit drops to its value.
  expect(texts(1)).toEqual([`50.0${NBSP}s PLANNED · AI`, `60.0${NBSP}s`]);
  expect(waypointWho("iris")).toBe(PLANNED_AI);
  expect(waypointWho("agent:steward")).toBe(PLANNED_AI);
  expect(waypointWho("preemptive")).toBe(FORECAST_RULE);
  expect(waypointWho("system")).toBe("PLANNED");
  plot.hover(epoch("2026-11-02T20:00:00Z"), null, 2 * 44 + 30);
  expect(tooltip(plot).slice(1)).toEqual(["Fog RH ceiling", `${FORECAST_RULE} 85.0% from 12:00`]);
});

test("a manual lane is muted, reads MANUAL · Iris skips · Jason since Oct 30 and draws no waypoints", async () => {
  const { container, plot } = await mount();
  expect(status(container, "mister_pulse_on_s")).toBe("MANUAL · Iris skips · Jason since Oct 30");
  expect(container.querySelector("[data-param=mister_pulse_on_s]")!.classList.contains("plan-row-muted")).toBe(true);
  const strokes = plot.ctx.strokes.filter((stroke) => onRow(stroke, 3));
  // The held value, solid grey across the window; Iris's 45 s waypoint at 13:00 is not drawn.
  expect(strokes.map((stroke) => [stroke.stroke, stroke.dash, ...stroke.path.map(([px]) => Math.round(px))])).toEqual([[TOKENS["--c-bound"], [], 0, 1000]]);
  expect(plot.ctx.texts.filter((text) => text.y === labelY(3))).toEqual([]);
  const rows = planRows(load<Plan>(planFixture), NOW, HORIZON);
  expect(rows[3].segments.map((segment) => segment.kind)).toEqual(["manual"]);
});

test("the device value sits at the left, a missing one reads — with its reason, and the plan's end is marked", async () => {
  const { container, plot } = await mount();
  expect([...container.querySelectorAll(".plan-device")].map((cell) => cell.textContent)).toEqual([`1.60${NBSP}kPa`, `60.0${NBSP}s`, "88.0%", `60.0${NBSP}s`, "—"]);
  expect(container.querySelector("[data-param=fan_stage_temp_f] .plan-device")!.getAttribute("title")).toBe("no readback yet");
  expect(status(container, "fan_stage_temp_f")).toBeNull();
  const ctx = plot.ctx;
  const end = Math.round(x("2026-11-03T00:30:00Z"));
  expect(ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--muted"] && same(stroke.dash, [4, 3])).map((stroke) => stroke.path.map(([px, py]) => [Math.round(px), py]))).toEqual([[[end, 0], [end, 300]]]);
  expect(ctx.texts.find((text) => text.text.startsWith("Plan ends"))).toMatchObject({ text: "Plan ends 17:30 · band/default", color: TOKENS["--muted"] });
  plot.hover(NOW + 3600, null, 4 * 44 + 30);
  expect(tooltip(plot).slice(1)).toEqual(["Fan stage temperature", "Device — · no readback yet"]);
  // The data table: each setting's spans in words, its header words and a missing device value's reason.
  const details = container.querySelector<HTMLDetailsElement>("details.chart-table")!;
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  await waitFor(() => expect(details.querySelector("table")).toBeTruthy());
  const rows = [...details.querySelectorAll("tbody tr")].map((row) => [...row.children].map((cell) => cell.textContent));
  expect(details.querySelector("summary")!.textContent).toBe("Data table · 9 rows");
  expect(rows.map((cells) => [cells[0], cells[3], cells[4]])).toEqual([
    ["VPD Mister Engage", `1.60${NBSP}kPa`, "SUPERSEDED"], ["VPD Mister Engage", `1.70${NBSP}kPa`, "SUPERSEDED"],
    ["Mister pulse gap", `60.0${NBSP}s`, "Device"], ["Mister pulse gap", `50.0${NBSP}s`, PLANNED_AI], ["Mister pulse gap", `60.0${NBSP}s`, PLANNED_AI],
    ["Fog RH ceiling", "88.0%", "Device"], ["Fog RH ceiling", "85.0%", FORECAST_RULE],
    ["Mister pulse on", `60.0${NBSP}s`, "MANUAL"],
    ["Fan stage temperature", "—", "Device"],
  ]);
  expect(rows[0][5]).toBe("YOUR VALUE 1.55 · Jason 08:00 · holds until Iris's next plan");
  expect(rows[8][5]).toBe("device: no readback yet");
});

test("with no plan in force the lanes hold the device values to the horizon; 72 h widens the window", async () => {
  const fixture = load<Plan>(planFixture);
  const plan: Plan = {
    ...fixture, plan: null,
    lanes: fixture.lanes.map((lane) => ({ ...lane, waypoints: [], applied_from: lane.applied_from === "manual" ? "manual" : "default", desired: lane.applied_from === "manual" ? lane.desired : null })),
  };
  const { plot, container } = await mount({ plan, hours: 72 });
  expect(container.querySelector("figcaption")!.textContent).toBe("Next 72 h · one lane per planned setting");
  expect(plot.opts.scales!.x).toMatchObject({ min: NOW, max: NOW + 72 * 3600 });
  expect(plot.opts.cursor!.sync!.key).toBe("verdify:plan:24h");
  const device = plot.ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--c-setpoint"]);
  expect(device.map((stroke) => stroke.path.map(([px]) => Math.round(px)))).toEqual([[0, 1000], [0, 1000], [0, 1000]]);
  expect(plot.ctx.texts.some((text) => text.text.startsWith("Plan ends"))).toBe(false);
  expect([...container.querySelectorAll(".plan-status")].map((item) => item.textContent)).toEqual(["MANUAL · Iris skips · Jason since Oct 30"]);
});

test("stale marks each device value STALE and muted, with when it was read, while the waypoints still draw", async () => {
  const { container, plot } = await mount({ stale: true });
  // 16:58Z is 09:58 in America/Denver.
  expect([...container.querySelectorAll(".chart-note-stale")].map((note) => note.textContent)).toEqual(["STALE · device values last read 09:58"]);
  const devices = [...container.querySelectorAll(".plan-device")];
  expect(devices.map((cell) => [cell.textContent, cell.classList.contains("plan-device-stale"), cell.getAttribute("title")])).toEqual([
    [`1.60${NBSP}kPa`, true, "STALE · read 09:58"], [`60.0${NBSP}s`, true, "STALE · read 09:58"], ["88.0%", true, "STALE · read 09:58"],
    [`60.0${NBSP}s`, true, "STALE · read 09:58"],
    // No value: "—" with the API's reason, never STALE.
    ["—", false, "no readback yet"],
  ]);
  const ctx = plot.ctx;
  // Mister pulse gap: the device value muted and labelled STALE up to the first waypoint, then Iris's waypoints dashed as before.
  expect(ctx.strokes.filter((stroke) => onRow(stroke, 1)).map((stroke) => [stroke.stroke, stroke.dash, ...stroke.path.map(([px]) => Math.round(px))])).toEqual([
    [TOKENS["--c-bound"], [], 0, Math.round(x("2026-11-02T18:00:00Z"))],
    [TOKENS["--ink"], [8, 4], Math.round(x("2026-11-02T18:00:00Z")), Math.round(x("2026-11-02T22:00:00Z"))],
    [TOKENS["--ink"], [8, 4], Math.round(x("2026-11-02T22:00:00Z")), Math.round(x("2026-11-03T00:30:00Z"))],
  ]);
  expect(ctx.texts.filter((text) => text.y === labelY(1)).map((text) => [text.text, text.color])).toEqual([
    ["STALE", TOKENS["--c-bound"]], [`50.0${NBSP}s PLANNED · AI`, TOKENS["--ink"]], [`60.0${NBSP}s`, TOKENS["--ink"]],
  ]);
  // The forecast rule's waypoint still draws after the stale device value.
  expect(ctx.strokes.filter((stroke) => onRow(stroke, 2)).map((stroke) => [stroke.stroke, stroke.dash])).toEqual([[TOKENS["--c-bound"], []], [TOKENS["--ink"], [8, 4]]]);
  expect(ctx.strokes.some((stroke) => stroke.stroke === TOKENS["--c-setpoint"])).toBe(false);
  plot.hover(NOW + 1800, null, 1 * 44 + 30);
  expect(tooltip(plot).slice(1)).toEqual(["Mister pulse gap", `Device 60.0${NBSP}s · STALE`, PLANNED_AI]);
  const details = container.querySelector<HTMLDetailsElement>("details.chart-table")!;
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  await waitFor(() => expect(details.querySelector("table")).toBeTruthy());
  const what = [...details.querySelectorAll("tbody tr")].map((row) => [row.children[0].textContent, row.children[4].textContent]);
  expect(what.filter(([, words]) => words?.startsWith("Device"))).toEqual([
    ["Mister pulse gap", "Device · STALE"], ["Fog RH ceiling", "Device · STALE"], ["Fan stage temperature", "Device"],
  ]);
  cleanup();
  // Fresh values carry no STALE anywhere.
  const fresh = await mount({ stale: false });
  expect(fresh.container.querySelector(".chart-note-stale")).toBeNull();
  expect(fresh.container.querySelector(".plan-device-stale")).toBeNull();
  expect(fresh.plot.ctx.texts.some((text) => text.text === "STALE")).toBe(false);
});

test("rowEnd ends every row with the screen's control in a column beside the plot", async () => {
  const { container } = await mount({ rowEnd: (row) => <a href={`/settings?edit=${row.key}`}>Open{" "}<span className="screen-reader">{row.label}</span></a> });
  expect(container.querySelector(".lanes")!.classList.contains("lanes-plan-end")).toBe(true);
  const ends = [...container.querySelectorAll(".plan-row")].map((row) => [row.getAttribute("data-param"), row.querySelector(".plan-end a")?.getAttribute("href")]);
  expect(ends).toEqual([
    ["mister_engage_kpa", "/settings?edit=mister_engage_kpa"], ["mister_pulse_gap_s", "/settings?edit=mister_pulse_gap_s"],
    ["fog_rh_ceiling_pct", "/settings?edit=fog_rh_ceiling_pct"], ["mister_pulse_on_s", "/settings?edit=mister_pulse_on_s"],
    ["fan_stage_temp_f", "/settings?edit=fan_stage_temp_f"],
  ]);
  // The end sits after the row's two lines, outside the canvas, which keeps its own role and label.
  expect([...container.querySelector("[data-param=mister_pulse_on_s]")!.children].map((child) => child.className)).toEqual(["plan-head", "plan-line", "plan-end"]);
  expect(container.querySelector(".lanes-canvas")!.querySelector("a")).toBeNull();
  cleanup();
  const plain = await mount();
  expect(plain.container.querySelector(".plan-end")).toBeNull();
  expect(plain.container.querySelector(".lanes")!.classList.contains("lanes-plan-end")).toBe(false);
});

test("values in the header use the unit's decimals without the unit, and the server markup has no style attribute", () => {
  expect(plainValue(1.55, "kPa")).toBe("1.55");
  expect(plainValue(72.04, "°F")).toBe("72.0");
  expect(plainValue(125, "min")).toBe("125");
  expect(plainValue(-0.4, "°F")).toBe("−0.4");
  expect(renderToString(<PlanLanes {...props()} />)).not.toMatch(/style=/);
});
