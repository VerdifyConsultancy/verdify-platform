// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * The real uPlot 1.6.32 for the lanes and the bars: the x-only lanes and the stacked bars must be options uPlot
 * accepts, and a lane must move the panels' cursor to the same instant through `cursor.sync`. jsdom has no canvas,
 * so the 2D context records what is painted and Path2D is a no-op.
 */
import { act, cleanup, render } from "@testing-library/react";
import type uPlot from "uplot";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { EquipmentLanes } from "./equipment-lanes";
import metricsFixture from "./fixtures/metrics.json";
import minutesFixture from "./fixtures/minutes.json";
import planFixture from "./fixtures/plan.json";
import holeFixture from "./fixtures/series-hole.json";
import statesFixture from "./fixtures/states.json";
import { FakeResizeObserver, RecordingContext, TOKENS, load, media, setTokens } from "./fixtures/uplot-mock";
import { MinutesByZone } from "./minutes-by-zone";
import { PlanLanes } from "./plan-lanes";
import { TimeSeries } from "./time-series";
import type { MetricDef, MinutesReport, Plan, Series, StateLanes } from "./types";

const epoch = (instant: string) => Date.parse(instant) / 1000;
const RANGE = { from: epoch("2026-11-01T06:00:00Z"), to: epoch("2026-11-01T22:00:00Z") };
const KEY = "verdify:graphs:24h";
const metric = load<MetricDef[]>(metricsFixture).find((item) => item.key === "climate.temp_avg")!;

class StubPath { moveTo() {} lineTo() {} rect() {} arc() {} bezierCurveTo() {} closePath() {} addPath() {} roundRect() {} }
let contexts: RecordingContext[] = [];
function stubContext(canvas: HTMLCanvasElement) {
  const recording = new RecordingContext();
  contexts.push(recording);
  return new Proxy(recording, {
    get(target, name) {
      if (name === "canvas") return canvas;
      const value = Reflect.get(target, name);
      if (value !== undefined) return typeof value === "function" ? value.bind(target) : value;
      return () => undefined;
    },
    set(target, name, value) { return Reflect.set(target, name, value); },
  });
}
// jsdom lays nothing out: each chart box gets the size the stylesheet gives it at 1440 px.
const SIZES: Record<string, [number, number]> = { plot: [1200, 150], "lanes-canvas": [1000, 9 * 20 + 20 + 36], "plot-bars": [900, 92] };
const clientWidth = Object.getOwnPropertyDescriptor(Element.prototype, "clientWidth")!;
const clientHeight = Object.getOwnPropertyDescriptor(Element.prototype, "clientHeight")!;
const sized = (axis: 0 | 1, fallback: PropertyDescriptor) => ({
  configurable: true,
  get(this: HTMLElement) {
    const match = Object.keys(SIZES).find((name) => this.classList.contains(name));
    return match ? SIZES[match][axis] : fallback.get!.call(this);
  },
});
const settle = () => act(async () => { await vi.dynamicImportSettled(); await new Promise((resolve) => setTimeout(resolve, 0)); });

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", sized(0, clientWidth));
  Object.defineProperty(HTMLElement.prototype, "clientHeight", sized(1, clientHeight));
  contexts = [];
  FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  vi.stubGlobal("Path2D", StubPath);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (this: HTMLCanvasElement) {
    return stubContext(this) as unknown as CanvasRenderingContext2D;
  } as never);
  setTokens();
});
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style");
  delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
  delete (HTMLElement.prototype as { clientHeight?: number }).clientHeight;
});

test("hovering a lane moves the panel's cursor to the same instant: one sync key, one x scale", async () => {
  render(<>
    <TimeSeries series={load<Series>(holeFixture)} metrics={[metric]} now={epoch("2026-11-01T10:00:00Z")} xRange={RANGE} syncKey={KEY} linked />
    <EquipmentLanes states={load<StateLanes>(statesFixture)} xRange={RANGE} syncKey={KEY} now={epoch("2026-11-01T10:00:00Z")} />
  </>);
  await settle();
  const UPlot = (await import("uplot")).default;
  const [panel, lanes] = UPlot.sync(KEY).plots as uPlot[];
  expect(panel.series).toHaveLength(2);
  // The lanes are an x scale only: no y series, the time axis alone shown, the window as x.
  expect(lanes.series).toHaveLength(1);
  expect(lanes.axes.map((axis) => axis.show)).toEqual([true, false]);
  expect([lanes.scales.x.min, lanes.scales.x.max]).toEqual([RANGE.from, RANGE.to]);
  expect([lanes.width, lanes.height]).toEqual([1000, 236]);
  // The pointer over the L01 lane at 07:35 (01:35 GMT-06:00): the panel's cursor follows to 07:35.
  const at = epoch("2026-11-01T07:35:00Z");
  const setCursor = lanes.setCursor as (opts: { left: number; top: number }, fire?: boolean, publish?: boolean) => void;
  act(() => setCursor({ left: lanes.valToPos(at, "x"), top: 4 * 20 + 10 }, true, true));
  expect(panel.cursor.left).toBeCloseTo(panel.valToPos(at, "x"), 0);
  expect(panel.posToVal(panel.cursor.left!, "x")).toBeCloseTo(at, -2);
  // And the lanes painted their rows: the L01 run is named by its cause.
  const painted = contexts.flatMap((context) => context.texts.map((text) => text.text));
  expect(painted).toContain("schedule");
  expect(contexts.some((context) => context.rects.some((rect) => rect.color === TOKENS["--eq-water"]))).toBe(true);
});

test("uPlot stacks the minutes bars through bands, in the causes' colours, over a scale from 0", async () => {
  render(<MinutesByZone report={load<MinutesReport>(minutesFixture)} caps={{ wall: 60, center: 120, south: 45, west: 45 }} planned={{ center: 20 }} now={epoch("2026-11-02T17:00:00Z")} />);
  await settle();
  const UPlot = (await import("uplot")).default;
  const plots = UPlot.sync("verdify:minutes:2026-10-27:2026-11-02").plots as uPlot[];
  expect(plots).toHaveLength(5);
  const [wall, center] = plots;
  expect((wall as uPlot & { bands: uPlot.Band[] }).bands).toHaveLength(5);
  expect([center.scales.y.min, center.scales.y.max]).toEqual([0, 138]);
  expect([plots[4].scales.y.min, plots[4].scales.y.max]).toEqual([0, 35]);
  const filled = new Set(contexts.flatMap((context) => context.fills.map((fill) => fill.color)));
  for (const token of ["--cause-vpd", "--cause-schedule", "--eq-fert", "--cause-prep", "--forest", "--c-bound"]) expect(filled, token).toContain(TOKENS[token]);
});

test("uPlot takes the plan lanes: an x-only window from now to the horizon, the rows painted in its draw hook", async () => {
  const now = epoch("2026-11-02T17:00:00Z");
  render(<PlanLanes plan={load<Plan>(planFixture)} now={now} syncKey="verdify:plan:24h" />);
  await settle();
  const UPlot = (await import("uplot")).default;
  const [plot] = UPlot.sync("verdify:plan:24h").plots as uPlot[];
  expect([plot.scales.x.min, plot.scales.x.max]).toEqual([now, now + 24 * 3600]);
  expect(plot.series).toHaveLength(1);
  const painted = contexts.flatMap((context) => context.texts.map((text) => text.text));
  expect(painted.some((text) => text.endsWith("PLANNED · AI"))).toBe(true);
  expect(painted.some((text) => text.endsWith("Forecast rule · system"))).toBe(true);
  expect(painted).toContain("Plan ends 17:30");
});
