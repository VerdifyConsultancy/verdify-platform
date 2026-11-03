// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * The real uPlot 1.6.32, not the stand-in: the options TimeSeries builds must be ones uPlot accepts and ranges as the
 * design says. jsdom has no canvas, so the 2D context is a recording stub and Path2D a no-op; nothing is drawn.
 */
import { act, cleanup, render } from "@testing-library/react";
import type uPlot from "uplot";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import bandFixture from "./fixtures/band.json";
import metricsFixture from "./fixtures/metrics.json";
import holeFixture from "./fixtures/series-hole.json";
import historyFixture from "./fixtures/setting-history.json";
import { FakeResizeObserver, RecordingContext, load, media, setTokens } from "./fixtures/uplot-mock";
import { TimeSeries } from "./time-series";
import type { BandTimeline, MetricDef, Series, SettingHistory } from "./types";
import { resetZoom } from "./uplot-chart";

const metrics = load<MetricDef[]>(metricsFixture);
const metric = (key: string) => metrics.find((item) => item.key === key)!;
const epoch = (instant: string) => Date.parse(instant) / 1000;
const NOW = epoch("2026-11-01T10:00:00Z");
const RANGE = { from: epoch("2026-11-01T06:00:00Z"), to: epoch("2026-11-01T22:00:00Z") };
const KEY = "verdify:graphs:24h";

class StubPath { moveTo() {} lineTo() {} rect() {} arc() {} bezierCurveTo() {} closePath() {} addPath() {} }
/** A 2D context that records what the chart's hooks paint and accepts everything uPlot calls. */
function stubContext(canvas: HTMLCanvasElement) {
  const recording = new RecordingContext();
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
let contexts: RecordingContext[] = [];
// jsdom lays nothing out: the plot box gets the size the stylesheet reserves at 1440 px.
const clientWidth = Object.getOwnPropertyDescriptor(Element.prototype, "clientWidth")!;
const clientHeight = Object.getOwnPropertyDescriptor(Element.prototype, "clientHeight")!;
const box = (size: number, fallback: PropertyDescriptor) => ({ configurable: true, get(this: HTMLElement) { return this.classList.contains("plot") ? size : fallback.get!.call(this); } });

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", box(1200, clientWidth));
  Object.defineProperty(HTMLElement.prototype, "clientHeight", box(320, clientHeight));
  contexts = [];
  FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  vi.stubGlobal("Path2D", StubPath);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (this: HTMLCanvasElement) {
    const context = stubContext(this);
    contexts.push(context as unknown as RecordingContext);
    return context as unknown as CanvasRenderingContext2D;
  } as never);
  setTokens();
});
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style");
  // Back to the inherited Element getters.
  delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
  delete (HTMLElement.prototype as { clientHeight?: number }).clientHeight;
});

async function chart() {
  const view = render(<TimeSeries series={load<Series>(holeFixture)} metrics={[metric("climate.vpd_avg")]} band={load<BandTimeline>(bandFixture)} settings={[{ history: load<SettingHistory>(historyFixture) }]} now={NOW} xRange={RANGE} syncKey={KEY} />);
  await act(async () => { await vi.dynamicImportSettled(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  const UPlot = (await import("uplot")).default;
  const [plot] = UPlot.sync(KEY).plots as uPlot[];
  return { ...view, plot, UPlot };
}

test("uPlot takes the options: x is the window, y the padded union, the nulls stay, and the hooks paint", async () => {
  const { plot } = await chart();
  expect(plot).toBeTruthy();
  expect([plot.width, plot.height]).toEqual([1200, 320]);
  expect([plot.scales.x.min, plot.scales.x.max]).toEqual([RANGE.from, RANGE.to]);
  // The crop band dips to 0.51 kPa and the plan reaches 1.80: the y range holds both, padded 5%.
  expect(plot.scales.y.min!).toBeLessThan(0.51);
  expect(plot.scales.y.max!).toBeGreaterThan(1.8);
  expect(plot.scales.y.max!).toBeLessThan(1.9);
  expect(plot.series[1].spanGaps).toBe(false);
  expect((plot.data[1] as (number | null)[]).slice(38, 44)).toEqual([null, null, null, null, null, null]);
  expect(plot.axes).toHaveLength(2);
  const painted = contexts.flatMap((context) => context.texts.map((text) => text.text));
  expect(painted).toContain("Now");
  expect(painted.some((text) => text.endsWith("PLANNED · AI"))).toBe(true);
  expect(painted.some((text) => text.endsWith("Forecast rule · system"))).toBe(true);
});

test("a settings-only chart: uPlot takes no data at all, ranges y from the steps and paints them", async () => {
  const history = load<SettingHistory>(historyFixture);
  render(<TimeSeries series={null} metrics={[]} unit="kPa" settings={[{ history, allRequests: true }]} now={NOW} xRange={RANGE} syncKey={KEY} />);
  await act(async () => { await vi.dynamicImportSettled(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  const UPlot = (await import("uplot")).default;
  const [plot] = UPlot.sync(KEY).plots as uPlot[];
  expect([plot.scales.x.min, plot.scales.x.max]).toEqual([RANGE.from, RANGE.to]);
  // The lowest step is 1.50 kPa and the highest 1.80: the y range holds both, padded 5%.
  expect(plot.scales.y.min).toBeCloseTo(1.5 - 0.3 * 0.05, 9);
  expect(plot.scales.y.max).toBeCloseTo(1.8 + 0.3 * 0.05, 9);
  const painted = contexts.flatMap((context) => context.texts.map((text) => text.text));
  expect(painted).toContain("Now");
  expect(painted).toContain("jason · dashboard");
  expect(painted.some((text) => text.endsWith("PLANNED · AI"))).toBe(true);
  // The cursor over no data still names the steps at that time.
  const over = plot.over;
  const tip = over.querySelector<HTMLElement>(".chart-tooltip")!;
  act(() => plot.setCursor({ left: 100, top: 40 }));
  expect(tip.hidden).toBe(false);
  expect(tip.textContent).toContain("mister_engage_kpa");
});

test("a zoom survives new data, and Reset zoom restores the window", async () => {
  const { plot, rerender } = await chart();
  act(() => plot.setScale("x", { min: RANGE.from + 3600, max: RANGE.from + 7200 }));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect([plot.scales.x.min, plot.scales.x.max]).toEqual([RANGE.from + 3600, RANGE.from + 7200]);
  const newer = load<Series>(holeFixture);
  newer.series["climate.vpd_avg"].avg[47] = 0.97;
  rerender(<TimeSeries series={newer} metrics={[metric("climate.vpd_avg")]} band={load<BandTimeline>(bandFixture)} settings={[{ history: load<SettingHistory>(historyFixture) }]} now={NOW + 60} xRange={{ from: RANGE.from + 60, to: RANGE.to + 60 }} syncKey={KEY} />);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect((plot.data[1] as (number | null)[])[47]).toBe(0.97);
  expect([plot.scales.x.min, plot.scales.x.max]).toEqual([RANGE.from + 3600, RANGE.from + 7200]);
  act(() => resetZoom(KEY, { from: RANGE.from + 60, to: RANGE.to + 60 }));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect([plot.scales.x.min, plot.scales.x.max]).toEqual([RANGE.from + 60, RANGE.to + 60]);
});

test("unmount destroys the uPlot instance and leaves its sync group", async () => {
  const { unmount, UPlot } = await chart();
  expect(UPlot.sync(KEY).plots).toHaveLength(1);
  unmount();
  expect(UPlot.sync(KEY).plots).toHaveLength(0);
  expect(FakeResizeObserver.instances.every((observer) => observer.disconnected)).toBe(true);
});
