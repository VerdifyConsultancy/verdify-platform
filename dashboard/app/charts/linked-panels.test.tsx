// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import bandFixture from "./fixtures/band.json";
import metricsFixture from "./fixtures/metrics.json";
import holeFixture from "./fixtures/series-hole.json";
import historyFixture from "./fixtures/setting-history.json";
import { FakeResizeObserver, MockPlot, created, load, media, setTokens } from "./fixtures/uplot-mock";
import { LinkedPanels, groupByUnit, seriesFor, syncKey, type LinkedPanelsProps } from "./linked-panels";
import type { BandTimeline, MetricDef, Series, SettingHistory } from "./types";

vi.mock("uplot", async () => ({ default: (await import("./fixtures/uplot-mock")).MockPlot }));

const metrics = load<MetricDef[]>(metricsFixture);
const metric = (key: string) => metrics.find((item) => item.key === key)!;
const epoch = (instant: string) => Date.parse(instant) / 1000;
const NOW = epoch("2026-11-01T10:00:00Z");
const RANGE = { from: epoch("2026-11-01T06:00:00Z"), to: epoch("2026-11-01T22:00:00Z") };
const settle = () => act(async () => { await vi.dynamicImportSettled(); });

/** Graphs asks API-04 once per panel: each Series holds that panel's keys. */
function panelSeries(keys: string[]): Series {
  const hole = load<Series>(holeFixture);
  const temp = hole.series["climate.temp_avg"];
  return { ...hole, series: Object.fromEntries(keys.map((key) => [key, hole.series[key] ?? { ...temp, avg: temp.avg.map((value) => (value === null ? null : Math.round(value * 7) / 100)) }])) };
}
const PICKED = [metric("climate.temp_avg"), metric("climate.vpd_avg"), metric("climate.rh_avg"), metric("climate.outdoor_temp_f"), metric("climate.flow_gpm")];
const SERIES = [panelSeries(["climate.temp_avg", "climate.outdoor_temp_f"]), panelSeries(["climate.vpd_avg"]), panelSeries(["climate.rh_avg"]), panelSeries(["climate.flow_gpm"])];
function props(overrides: Partial<LinkedPanelsProps> = {}): LinkedPanelsProps {
  return { route: "graphs", range: "24h", metrics: PICKED, series: SERIES, band: load<BandTimeline>(bandFixture), settings: [{ history: load<SettingHistory>(historyFixture) }], now: NOW, xRange: RANGE, ...overrides };
}
async function mount(overrides: Partial<LinkedPanelsProps> = {}) {
  const view = render(<MemoryRouter><LinkedPanels {...props(overrides)} /></MemoryRouter>);
  await settle();
  return view;
}

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

test("groupByUnit: °F, kPa and % first; a metric joins its unit's panel; any other unit opens a panel below", () => {
  const panels = groupByUnit([metric("climate.flow_gpm"), metric("climate.rh_avg"), metric("climate.temp_avg"), metric("diagnostics.heap_free_kb"), metric("climate.soil_moisture_south_1"), metric("climate.outdoor_temp_f"), metric("climate.vpd_avg")]);
  expect(panels.map((panel) => [panel.unit, panel.metrics.map((item) => item.key)])).toEqual([
    ["°F", ["climate.temp_avg", "climate.outdoor_temp_f"]],
    ["kPa", ["climate.vpd_avg"]],
    ["%", ["climate.rh_avg", "climate.soil_moisture_south_1"]],
    ["gal/min", ["climate.flow_gpm"]],
    ["kB", ["diagnostics.heap_free_kb"]],
  ]);
  // A key picked twice is one trace.
  expect(groupByUnit([metric("climate.temp_avg"), metric("climate.temp_avg")])[0].metrics).toHaveLength(1);
  expect(syncKey("graphs", "72h")).toBe("verdify:graphs:72h");
  expect(seriesFor(panels[0], SERIES)).toBe(SERIES[0]);
  expect(seriesFor(panels[4], SERIES)).toBeNull();
});

test("a second °F metric joins the temperature panel, flow opens a new linked panel, and no panel has a second y axis", async () => {
  const { container } = await mount();
  expect(created).toHaveLength(4);
  expect([...container.querySelectorAll("figcaption")].map((caption) => caption.textContent)).toEqual(["Indoor temperature · Outdoor temperature · °F", "Indoor VPD · kPa", "Indoor RH · %", "Water flow · gal/min"]);
  expect(created[0].opts.series.map((series) => series.label)).toEqual([undefined, "Indoor temperature", "Outdoor temperature"]);
  for (const plot of created) {
    expect(plot.opts.axes).toHaveLength(2);
    expect(plot.opts.series.slice(1).every((series) => series.scale === "y")).toBe(true);
    expect(Object.keys(plot.opts.scales!).sort()).toEqual(["x", "y"]);
    expect(plot.opts.cursor!.sync!.key).toBe("verdify:graphs:24h");
    expect(plot.opts.height).toBe(150);
    expect(plot.target.className).toBe("plot plot-small");
  }
  // The band goes to °F and kPa; RH and flow get none. The setting's steps go to its unit's panel only.
  const marks = created.map((plot) => { plot.draw(); return plot.ctx.fills.length > 0; });
  expect(marks).toEqual([true, true, false, false]);
  expect(created.map((plot) => plot.ctx.texts.some((text) => text.text.includes("PLANNED · AI")))).toEqual([false, true, false, false]);
  expect(container.querySelectorAll("[style]")).toHaveLength(0);
});

test("range chips link each range with aria-current on the one shown", async () => {
  await mount({ range: "72h", rangeHref: (range) => `/graphs?range=${range}` });
  const chips = within(screen.getByRole("list", { name: "Range" })).getAllByRole("link");
  expect(chips.map((chip) => [chip.textContent, chip.getAttribute("href"), chip.getAttribute("aria-current")])).toEqual([
    ["24 h", "/graphs?range=24h", null], ["72 h", "/graphs?range=72h", "page"], ["7 d", "/graphs?range=7d", null], ["30 d", "/graphs?range=30d", null],
  ]);
  expect(created[0].opts.cursor!.sync!.key).toBe("verdify:graphs:72h");
});

test("a drag zoom on one panel shows Reset zoom, which puts every synced chart back to the window", async () => {
  await mount();
  expect(screen.queryByRole("button", { name: "Reset zoom" })).toBeNull();
  // uPlot's sync makes every panel take the same drag; each sets its own x.
  act(() => { for (const plot of created) plot.setScale("x", { min: RANGE.from + 3600, max: RANGE.from + 7200 }); });
  fireEvent.click(await screen.findByRole("button", { name: "Reset zoom" }));
  expect(created.map((plot) => plot.scales.x)).toEqual(created.map(() => ({ min: RANGE.from, max: RANGE.to })));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Reset zoom" })).toBeNull());
});

test("one CSV of the window holds every plotted column; a legend toggle hides a series from the chart and the file", async () => {
  const { container } = await mount();
  expect(screen.getAllByRole("link", { name: "CSV of this window" })).toHaveLength(1);
  const header = async (blob: Blob) => (await blob.text()).split("\r\n")[0];
  expect(await header(blobs[0])).toBe("time (America/Denver),climate.temp_avg (°F),climate.outdoor_temp_f (°F),climate.vpd_avg (kPa),climate.rh_avg (%),climate.flow_gpm (gal/min)");
  const legend = within(container.querySelector<HTMLElement>(".chart-legend")!);
  fireEvent.click(legend.getByRole("button", { name: "Outdoor temperature" }));
  expect(created[0].seriesCalls).toEqual([{ index: 2, show: false }]);
  await waitFor(() => expect(blobs).toHaveLength(2));
  expect(await header(blobs[1])).toBe("time (America/Denver),climate.temp_avg (°F),climate.vpd_avg (kPa),climate.rh_avg (%),climate.flow_gpm (gal/min)");
});

test("a panel with a message keeps its reserved frame while the others draw", async () => {
  const { container } = await mount({ series: SERIES.slice(0, 1).concat([null as unknown as Series], SERIES.slice(2)), messages: { kPa: "This window took too long. Narrow it." } });
  expect(created).toHaveLength(3);
  const frame = container.querySelector(".chart-message")!;
  expect(frame.textContent).toBe("This window took too long. Narrow it.");
  expect(frame.className).toBe("plot plot-small chart-message");
});
