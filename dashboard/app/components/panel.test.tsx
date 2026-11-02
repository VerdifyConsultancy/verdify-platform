// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Button } from "./button";
import { EmptyState, Panel, type PanelProps } from "./panel";
import { StatList, StatTile } from "./stat-tile";
import { denver, renderUi, text } from "./test-render";

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(denver("06:16"))); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

const RUNS = <StatList><StatTile label="Wall" value={12} unit="min" verdict="on schedule" /></StatList>;
const panel = (props: Partial<PanelProps>) => <Panel title="Runs today" thing="runs" state="ready" {...props}>{props.children ?? RUNS}</Panel>;
const stateLine = (container: HTMLElement) => text(container.querySelector(".panel-state"));

test("ready shows its content under its heading; loading is a skeleton at the reserved height", () => {
  let { container } = renderUi(panel({}));
  expect(screen.getByRole("region", { name: "Runs today" })).toBeTruthy();
  expect(text(container)).toContain("12 min");
  cleanup();
  ({ container } = renderUi(panel({ state: "loading", reserve: "lg" })));
  expect(container.querySelector(".skeleton.skeleton-lg")?.getAttribute("aria-hidden")).toBe("true");
  expect(container.querySelector("section")?.getAttribute("aria-busy")).toBe("true");
  expect(text(container)).toBe("Runs todayLoading runs");
});

test("a ready panel given its data's time shows its age, which its tiles then leave out", () => {
  const { container } = renderUi(panel({ observedAt: denver("06:15", 40), children: <StatList><StatTile label="Wall" value={12} unit="min" observedAt={denver("06:15", 40)} /></StatList> }));
  expect(stateLine(container)).toBe("Updated 20 s ago");
  expect(container.querySelector(".panel-state .dot")?.getAttribute("data-fresh")).toBe("ok");
  expect(container.querySelector("[data-tile] .age")).toBeNull();
  cleanup();
  // Without a time it shows no line at all.
  expect(renderUi(panel({})).container.querySelector(".panel-state")).toBeNull();
});

test("empty says what would appear and links to a wider range", () => {
  let { container } = renderUi(panel({ state: "empty", empty: <EmptyState message="No runs today." detail="A run shows here when a line waters." widen={{ to: "/water/history?range=7d", label: "Show the last 7 days" }} /> }));
  expect(text(container)).toContain("No runs today.A run shows here when a line waters.");
  expect(screen.getByRole("link", { name: "Show the last 7 days" }).getAttribute("href")).toBe("/water/history?range=7d");
  cleanup();
  ({ container } = renderUi(panel({ state: "empty" })));
  expect(text(container)).toContain("No runs.");
});

test("stale: a muted value, STALE, \"Last data 14 min ago (06:02)\" and no verdict", () => {
  const { container } = renderUi(panel({ state: "stale", observedAt: denver("06:02") }));
  expect(stateLine(container)).toBe("Stale Last data 14 min ago (06:02)");
  expect(container.querySelector(".panel-state .dot")?.getAttribute("data-fresh")).toBe("stale");
  expect(container.querySelector(".panel-muted")).not.toBeNull();
  expect(text(container)).not.toContain("on schedule");
});

test("ingestor not running and controller not reporting: device values STALE, no verdict, the line names the side", () => {
  let { container } = renderUi(panel({ state: "ingestor_down", since: denver("06:10") }));
  expect(stateLine(container)).toBe("Stale Ingestor not running since 06:10");
  expect(container.querySelector(".panel-state .dot")?.getAttribute("data-fresh")).toBe("off");
  expect(text(container)).not.toContain("on schedule");
  cleanup();
  ({ container } = renderUi(panel({ state: "controller_not_reporting", since: denver("06:10") })));
  expect(stateLine(container)).toBe("Stale Controller not reporting since 06:10");
  expect(text(container)).not.toMatch(/offline/i);
  cleanup();
  expect(stateLine(renderUi(panel({ state: "controller_not_reporting", since: null })).container)).toBe("Stale Controller not reporting");
});

test("error: \"Couldn't load runs.\" and Retry in that panel only", () => {
  const onRetry = vi.fn();
  const { container } = renderUi(<>{panel({ state: "error", onRetry })}<Panel title="Alerts" thing="alerts" state="error" errorText="Alerts unavailable." /><Panel title="Water" thing="water" state="ready"><p>Center 14 min</p></Panel></>);
  const [runs, alerts, water] = container.querySelectorAll("section");
  expect(text(runs)).toBe("Runs todayCouldn't load runs.Retry");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
  expect(text(alerts)).toBe("AlertsAlerts unavailable.");
  expect(text(water)).toBe("WaterCenter 14 min");
});

test("busy keeps the old render with \"Busy, retrying in N s\"", () => {
  const { container } = renderUi(panel({ state: "busy", retryInS: 5 }));
  expect(stateLine(container)).toBe("Busy, retrying in 5 s");
  expect(text(container)).toContain("12 min");
  expect(text(container)).toContain("on schedule");
  cleanup();
  // The old render keeps aging while the panel waits.
  expect(stateLine(renderUi(panel({ state: "busy", retryInS: 5, observedAt: denver("06:14") })).container)).toBe("Busy, retrying in 5 s Updated 2 min ago");
});

test("a panel that fails to render says so alone, and Retry renders it again", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  let broken = true;
  const Flaky = () => { if (broken) throw new Error("parser bug"); return <p>Recovered</p>; };
  const onRetry = vi.fn();
  const { container } = renderUi(<>{panel({ children: <Flaky />, onRetry })}<Panel title="Water" thing="water" state="ready"><p>Center 14 min</p></Panel></>);
  const [runs, water] = container.querySelectorAll("section");
  expect(text(runs)).toBe("Runs todayCouldn't load runs.Retry");
  expect(text(water)).toBe("WaterCenter 14 min");
  broken = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
  expect(text(container.querySelector("section"))).toBe("Runs todayRecovered");
});

test("a viewer's panel has no write controls; an operator's has them", () => {
  const actions = <Button write>Run wall drip</Button>;
  expect(renderUi(panel({ actions }), { role: "viewer" }).container.querySelector("[data-write-control]")).toBeNull();
  cleanup();
  expect(renderUi(panel({ actions }), { role: "operator", writesEnabled: true }).container.querySelector("[data-write-control]")).not.toBeNull();
});
