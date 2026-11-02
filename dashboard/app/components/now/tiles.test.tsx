// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Now's eight tiles (#1129) over #1122's fixtures at 10:42: what each says, where each links, the Iris tile amber for a
// late, missed or failed required trigger, and the Water tile "unavailable", never "Idle", while API-IRR-01 fails.
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { nowAnswer } from "../../lib/now-answer";
import { parseIrrigationStatus, parseNow, type IrrigationStatus, type Now } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { Hero } from "../hero";
import { renderUi, text } from "../test-render";
import { alertCountText, bandText, dataText, lineStatesUnknown, nextRunText, nowTiles, planLine, waterTile } from "./tiles";

const NOW = Date.parse("2026-11-02T17:42:00Z");
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const set = (names: string | string[]) => fixture(names, { now: NOW });
const nowOf = (names: string | string[]): Now => parseNow(set(names)["API-02"])!;
const statusOf = (names: string | string[]): IrrigationStatus => parseIrrigationStatus(set(names)["API-IRR-01"])!;
const read = <T,>(data: T | null, failed = false): PanelRead<T> => ({ data, failed, pending: data === null && !failed, retry: vi.fn() });
const plain = (value: string | undefined | null) => (value ?? "").replaceAll(" ", " ");

/** The tiles in a hero, as Now renders them. */
function renderTiles(names: string | string[], { irrigation }: { irrigation?: PanelRead<IrrigationStatus> } = {}) {
  const now = nowOf(names);
  const answer = nowAnswer({ now, failed: false });
  const tiles = nowTiles({ now, answer, irrigation: irrigation ?? read(statusOf(names)), clock: NOW });
  renderUi(<Hero eyebrow="Greenhouse" title={answer.text} value={now.indoor.temp_f.value} unit="°F" observedAt={now.indoor.temp_f.observed_at} stale={answer.muted} tiles={tiles} />);
}
const tile = (name: string) => document.querySelector<HTMLElement>(`.hero > dl.stats > [data-tile="${name}"]`)!;

test("eight tiles, 4 × 2, and on phones Mode, Iris, Water and Alerts stay while the rest move under All readings", () => {
  renderTiles("in-band");
  const band = document.querySelector(".hero > dl.stats")!;
  expect([...band.querySelectorAll(":scope > div")].map((node) => node.getAttribute("data-tile"))).toEqual(["vpd", "rh", "outdoor", "mode", "iris", "water", "alerts", "data"]);
  expect([...band.querySelectorAll(":scope > .stat-more")].map((node) => node.getAttribute("data-tile"))).toEqual(["vpd", "rh", "outdoor", "data"]);
  // The Data tile is the ingestor banner's #data target, once.
  expect(document.querySelectorAll("#data")).toHaveLength(1);
  expect(tile("data").id).toBe("data");
});

test("in band: what each tile says, and where each links", () => {
  renderTiles("in-band");
  expect(plain(text(tile("vpd")))).toBe("VPD: open Graphs0.62 kPain bandband 0.45–0.80 kPa");
  expect(plain(text(tile("rh")))).toBe("RH: open Graphs71.0%no RH band");
  expect(plain(text(tile("outdoor")))).toContain("41.2 °FRH 58.0%Updated 2 min ago");
  expect(plain(text(tile("mode")))).toBe("Mode: open GraphsIn band, holdingsince 09:55");
  expect(plain(text(tile("iris")))).toBe("Iris: open PlanOKSUNRISE plan to 17:30");
  expect(plain(text(tile("water")))).toBe("Water: open WaterIdleno run scheduled");
  expect(plain(text(tile("alerts")))).toBe("Alerts: open ActivityNone open");
  expect(plain(text(tile("data")))).toBe("Data: open EquipmentFresh · 20 sheartbeat 20 s · writer 41 min");
  const links = ["vpd", "rh", "outdoor", "mode", "iris", "water", "alerts", "data"].map((name) => tile(name).querySelector("a.stat-link")?.getAttribute("href"));
  expect(links).toEqual(["/graphs", "/graphs", "/graphs", "/graphs", "/plan", "/water", "/activity", "/equipment"]);
  expect(tile("iris").className).not.toContain("stat-warn");
});

test("a missed SUNSET turns the Iris tile amber; late and failed do too", () => {
  renderTiles("missed-sunset");
  expect(tile("iris").className).toContain("stat-warn");
  expect(plain(text(tile("iris")))).toBe("Iris: open PlanMissedSUNRISE plan to 17:30");
  cleanup();
  for (const state of ["late", "failed"]) {
    const now = nowOf("in-band");
    const tiles = nowTiles({ now: { ...now, iris: { ...now.iris, required_trigger: state } }, answer: nowAnswer({ now, failed: false }), irrigation: read(statusOf("in-band")), clock: NOW });
    expect(tiles.find((item) => item.name === "iris")?.className, state).toBe("stat-warn");
  }
  expect(planLine({ plan_id: null, trigger: null, valid_until: null, required_trigger: "ok" })).toBe("No plan: band and defaults");
});

test("Water: the running line with RUNNING, Unknown while line states are not reported, and unavailable while API-IRR-01 fails", () => {
  renderTiles("l01-running");
  expect(plain(text(tile("water")))).toBe("Water: open WaterWall dripRunning");
  cleanup();
  renderTiles("in-band", { irrigation: read(statusOf("in-band"), true) });
  expect(plain(text(tile("water")))).toBe("Water: open Waterunavailablewater status not loaded");
  expect(plain(text(tile("water")))).not.toContain("Idle");
  expect(waterTile(read<IrrigationStatus>(null), NOW)).toEqual({ value: null, reason: "Loading water status" });
  expect(waterTile(read(statusOf("controller-not-reporting")), NOW)).toMatchObject({ text: "Unknown" });
  expect(nextRunText(statusOf("schedules-paused"), NOW)).toBe("next L04 Tue 06:50");
});

test("Alerts reads the one count by severity; while the ingestor is not running it adds that alerts may be missing", () => {
  renderTiles("alerts-merged");
  expect(plain(text(tile("alerts")))).toBe("Alerts: open Activity1 warning · 1 info");
  cleanup();
  renderTiles("ingestor-not-running");
  expect(plain(text(tile("alerts")))).toBe("Alerts: open ActivityNone openIn-app alerts may be missing");
  expect(alertCountText({ open: 3, critical: 1, warning: 2, info: 0 })).toBe("1 critical · 2 warnings");
  expect(alertCountText({ open: 2, critical: 0, warning: 0, info: 0 })).toBe("2 open");
});

test("Data: climate STALE with its age, the heartbeat's and the writer's; an age it lacks reads — with why; band sub-lines without bounds", () => {
  const now = nowOf("ingestor-not-running");
  expect(dataText(now.freshness, now.writer)).toEqual({ text: "STALE · 4 min", sub: "heartbeat STALE · 4 min · writer 41 min" });
  const never = { observed_at: null, age_s: null, cadence_s: 60, stale: true };
  expect(dataText({ ...now.freshness, climate: never, ingestor: never }, { ...now.writer, age_s: null })).toEqual({
    text: "No data", sub: "heartbeat STALE · — (never seen) · writer — (no push on record)",
  });
  expect(bandText({ low: null, high: 0.8 }, "kPa")).toBe("band unavailable");
});

test("Outdoor: a null RH reads — with its reason, never 0", () => {
  const now = nowOf("in-band");
  const tiles = nowTiles({
    now: { ...now, outdoor: { ...now.outdoor, rh_pct: { value: null, observed_at: null, reason: "No outdoor RH in the last hour" } } },
    answer: nowAnswer({ now, failed: false }), irrigation: read(statusOf("in-band")), clock: NOW,
  });
  expect(plain(String(tiles.find((item) => item.name === "outdoor")?.sub))).toBe("RH — (No outdoor RH in the last hour)");
});

test("Water before the irrigation firmware: relay states stand alone, so an irr_state never seen is not Unknown", () => {
  const before = statusOf("before-engine");
  const neverSeen = { ...before, freshness: { observed_at: null, age_s: null, stale: true } };
  expect(lineStatesUnknown(neverSeen)).toBe(false);
  expect(waterTile(read(neverSeen), NOW)).toMatchObject({ text: "Idle" });
  // With the firmware, a stale irr_state is the controller gone quiet: unknown, never Idle.
  expect(lineStatesUnknown({ ...neverSeen, engine: true })).toBe(true);
  expect(waterTile(read({ ...neverSeen, engine: true }), NOW)).toMatchObject({ text: "Unknown" });
});

test("device tiles follow the hero's STALE; the platform tiles stay readable while the controller is quiet", () => {
  renderTiles("controller-not-reporting");
  expect(tile("vpd").querySelector("dd > span")?.className).toBe("value-muted");
  expect(text(tile("vpd"))).not.toContain("in band");
  for (const name of ["iris", "alerts", "data", "water"]) expect(tile(name).querySelector("dd > span")?.className, name).toBe("");
  expect(screen.getAllByText("Stale").length).toBeGreaterThan(0);
});
