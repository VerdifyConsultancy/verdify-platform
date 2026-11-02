// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, render } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens } from "./fixtures/uplot-mock";
import { CHART_TOKENS, UPlotChart, chartLinks, readColors, resetZoom, type ChartEnv, type UPlotChartProps } from "./uplot-chart";

vi.mock("uplot", async () => ({ default: (await import("./fixtures/uplot-mock")).MockPlot }));

const RANGE = { from: 1_793_512_800, to: 1_793_570_400 };
const DATA: [number[], (number | null)[]] = [[RANGE.from, RANGE.from + 300], [64.2, null]];
let envs: ChartEnv[] = [];
function props(overrides: Partial<UPlotChartProps> = {}): UPlotChartProps {
  return {
    options: (env) => { envs.push(env); return { series: [{}, { label: "Indoor temperature", stroke: env.colors["--c-temp"], spanGaps: false }] }; },
    data: DATA, xRange: RANGE, structure: "°F", className: "plot", label: "Indoor temperature over time. The data table follows.", ...overrides,
  };
}
const settle = () => act(async () => { await vi.dynamicImportSettled(); });

beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = []; envs = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  setTokens();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style"); });

test("uPlot loads lazily into the reserved box, on Denver time, with token colours and no hex in the chart code", async () => {
  const { container } = render(<UPlotChart {...props()} />);
  const box = container.querySelector<HTMLElement>("div.plot")!;
  expect(box.getAttribute("role")).toBe("img");
  expect(box.getAttribute("aria-label")).toBe("Indoor temperature over time. The data table follows.");
  // Nothing is created until the import resolves.
  expect(created).toHaveLength(0);
  await settle();
  expect(created).toHaveLength(1);
  const [plot] = created;
  expect(plot.target).toBe(box);
  expect(plot.data).toBe(DATA);
  expect(plot.opts.series[1].stroke).toBe(TOKENS["--c-temp"]);
  expect(plot.opts.legend).toEqual({ show: false });
  expect(plot.opts.scales!.x).toMatchObject({ time: true, auto: false, min: RANGE.from, max: RANGE.to });
  // Without a stylesheet jsdom has no layout: the reserved 320 px stands in.
  expect(plot.opts.height).toBe(320);
  plot.opts.tzDate!(RANGE.from);
  expect(MockPlot.zones).toEqual(["America/Denver"]);
  expect(plot.opts.cursor!.drag).toEqual({ x: true, y: false, setScale: true });
  expect(plot.opts.cursor!.bind!.dblclick!(plot as never, box, () => null)).toBeNull();
  expect(envs[0].colors).toEqual(readColors());
});

test("colours are read from the design-system tokens on :root", () => {
  const colors = readColors();
  for (const [token, value] of Object.entries(TOKENS)) expect(colors[token]).toBe(value);
  expect(CHART_TOKENS).toContain("--c-band-wash");
  expect(CHART_TOKENS).toContain("--ink");
});

test("one ResizeObserver calls setSize; unmount destroys the chart and disconnects it", async () => {
  const { unmount } = render(<UPlotChart {...props({ className: "plot plot-small" })} />);
  await settle();
  const [observer] = FakeResizeObserver.instances;
  expect(FakeResizeObserver.instances).toHaveLength(1);
  observer.trigger();
  expect(created[0].sizes).toEqual([{ width: 1, height: 150 }]);
  unmount();
  expect(created[0].destroyed).toBe(true);
  expect(observer.disconnected).toBe(true);
});

test("a load that resolves after unmount is dropped", async () => {
  const { unmount } = render(<UPlotChart {...props()} />);
  unmount();
  await settle();
  expect(created).toHaveLength(0);
  expect(FakeResizeObserver.instances[0].disconnected).toBe(true);
});

test("new data keeps a zoom; an unzoomed chart follows its window; Reset zoom puts every synced chart back", async () => {
  const zooms: boolean[] = [];
  const { rerender } = render(<UPlotChart {...props({ syncKey: "verdify:graphs:24h", onZoom: (state) => zooms.push(state) })} />);
  await settle();
  const [plot] = created;
  expect(plot.opts.cursor!.sync).toEqual({ key: "verdify:graphs:24h", scales: ["x", null], setSeries: false });
  // The next poll: a newer Series for a window that ends later. Not zoomed, so x follows it.
  const next = { from: RANGE.from + 60, to: RANGE.to + 60 };
  const fresh: [number[], (number | null)[]] = [[...DATA[0], RANGE.from + 600], [64.2, null, 64.5]];
  rerender(<UPlotChart {...props({ syncKey: "verdify:graphs:24h", data: fresh, xRange: next, onZoom: (state) => zooms.push(state) })} />);
  expect(plot.dataUpdates).toEqual([fresh]);
  expect(plot.scales.x).toEqual({ min: next.from, max: next.to });
  // A drag zoom (uPlot sets x itself), then another poll: the data updates and the zoom stays.
  plot.setScale("x", { min: next.from + 3600, max: next.from + 7200 });
  expect(zooms).toEqual([true]);
  const later = { from: next.from + 60, to: next.to + 60 };
  rerender(<UPlotChart {...props({ syncKey: "verdify:graphs:24h", data: [...fresh] as typeof fresh, xRange: later, onZoom: (state) => zooms.push(state) })} />);
  expect(plot.dataUpdates).toHaveLength(2);
  expect(plot.scales.x).toEqual({ min: next.from + 3600, max: next.from + 7200 });
  resetZoom("verdify:graphs:24h", later);
  expect(plot.scales.x).toEqual({ min: later.from, max: later.to });
  expect(zooms).toEqual([true, false]);
  // A new structure rebuilds the chart.
  rerender(<UPlotChart {...props({ structure: "°F|outdoor", syncKey: "verdify:graphs:24h", xRange: later })} />);
  await settle();
  expect(plot.destroyed).toBe(true);
  expect(created).toHaveLength(2);
});

test("touch screens get no drag zoom", async () => {
  vi.stubGlobal("matchMedia", media({ fine: false }));
  render(<UPlotChart {...props()} />);
  await settle();
  expect(created[0].opts.cursor!.drag).toEqual({ x: false, y: false, setScale: false });
  expect(envs[0].fine).toBe(false);
});

test("server markup carries no style attribute, and charting routes link uPlot's stylesheet with the chart rules", () => {
  expect(renderToString(<UPlotChart {...props()} />)).not.toMatch(/style=/);
  // Vitest resolves `?url` imports to empty strings; chunks.test.ts checks the import specifiers themselves.
  const links = chartLinks();
  expect(links).toHaveLength(2);
  expect(links.every((link) => link.rel === "stylesheet")).toBe(true);
});
