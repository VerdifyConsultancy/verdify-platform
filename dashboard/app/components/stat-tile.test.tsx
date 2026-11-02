// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Panel } from "./panel";
import { StatList, StatTile, type StatTileProps } from "./stat-tile";
import { denver, renderUi, text } from "./test-render";

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(denver("06:16"))); });
afterEach(() => { cleanup(); vi.useRealTimers(); });
const tile = (props: StatTileProps) => <StatList><StatTile {...props} /></StatList>;
const value = (container: HTMLElement) => text(container.querySelector("dd > span"));

test("a value renders with its unit through formatValue, under its eyebrow label", () => {
  const { container } = renderUi(tile({ label: "VPD", value: 0.62, unit: "kPa", sub: "band 0.45–0.80" }));
  expect(text(container.querySelector("dt"))).toBe("VPD");
  expect(value(container)).toBe("0.62 kPa");
  expect(text(container.querySelector("dd small"))).toBe("band 0.45–0.80");
  expect(container.querySelector("dl")?.className).toBe("summary-stats");
});

test("re-rendering 64.8 as null shows \"—\" and its reason: never 0, off or the value it showed before", () => {
  const { container, rerender } = renderUi(tile({ label: "Indoor", value: 64.8, unit: "°F" }));
  expect(value(container)).toBe("64.8 °F");
  rerender(tile({ label: "Indoor", value: null, unit: "°F", reason: "probe offline" }));
  expect(value(container)).toBe("—");
  expect(text(container)).toContain("probe offline");
  expect(text(container)).not.toMatch(/64\.8|\b0\b|\boff\b/i);
  // With no reason given it still says why there is nothing.
  rerender(tile({ label: "Indoor", value: undefined, unit: "°F" }));
  expect(text(container.querySelector("dd"))).toBe("—No reading");
  // A verdict the screen still passes never sits beside "—".
  rerender(tile({ label: "Indoor", value: null, unit: "°F", reason: "probe offline", verdict: "in band" }));
  expect(text(container.querySelector("dd"))).toBe("—probe offline");
});

test("a value in words wins over a number, and an empty one is missing", () => {
  expect(value(renderUi(tile({ label: "Mode", text: "In band, holding" })).container)).toBe("In band, holding");
  cleanup();
  expect(value(renderUi(tile({ label: "Mode", text: null, reason: "no mode reported" })).container)).toBe("—");
});

test("freshness and STALE show only when they differ from the panel, and a verdict only on fresh data", () => {
  const panel = (state: "ready" | "stale", props: StatTileProps) => <Panel title="Climate" thing="climate" state={state} observedAt={denver("06:15", 40)}><StatList><StatTile {...props} /></StatList></Panel>;
  // Same source as the panel: no age of its own, verdict shown.
  let { container } = renderUi(panel("ready", { label: "VPD", value: 0.62, unit: "kPa", verdict: "in band", observedAt: denver("06:15", 40) }));
  expect(container.querySelector("[data-tile] .age")).toBeNull();
  expect(text(container)).toContain("in band");
  cleanup();
  // Another source, older: the tile shows its own age.
  ({ container } = renderUi(panel("ready", { label: "Outdoor", value: 41.2, unit: "°F", observedAt: denver("06:14") })));
  expect(text(container.querySelector("dd .age"))).toBe("Updated 2 min ago");
  cleanup();
  // Stale in a fresh panel: STALE, muted value and no verdict.
  ({ container } = renderUi(panel("ready", { label: "RH", value: 71, unit: "%", verdict: "in band", stale: true, observedAt: denver("06:02") })));
  expect(container.querySelector("dd [data-badge=stale]")).not.toBeNull();
  expect(container.querySelector("dd > span")?.className).toBe("value-muted");
  expect(text(container.querySelector("dd .age"))).toBe("Last data 14 min ago (06:02)");
  expect(text(container)).not.toContain("in band");
  cleanup();
  // In a stale panel the tile does not repeat STALE, and still gives no verdict.
  ({ container } = renderUi(panel("stale", { label: "VPD", value: 0.62, unit: "kPa", verdict: "in band" })));
  expect(container.querySelectorAll("[data-badge=stale]")).toHaveLength(1);
  expect(container.querySelector("dd [data-badge=stale]")).toBeNull();
  expect(text(container)).not.toContain("in band");
});

test("no verdict while the dashboard API is down", () => {
  expect(text(renderUi(tile({ label: "VPD", value: 0.62, unit: "kPa", verdict: "in band" }), { apiDown: true }).container)).not.toContain("in band");
});
