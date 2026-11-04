// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// "Running now" (redesign §5.1, US-NOW-03) over the fixtures at 10:42: a row per running item with its plain name, a dot
// in its equipment's token and "since HH:MM", wet lines with the cause and the time left, bursts from the readback;
// "Equipment state unknown since HH:MM" while rows 1 to 3 hold, never "Nothing running".
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { nowAnswer } from "../../lib/now-answer";
import { parseIrrigationStatus, parseNow, type IrrigationStatus, type Now } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { renderUi, text } from "../test-render";
import { RunningNow, runningChips } from "./running";

const NOW = Date.parse("2026-11-02T17:42:00Z");
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const iso = (ms: number) => new Date(ms).toISOString();
const plain = (value: string) => value.replaceAll(String.fromCharCode(0xa0), " ");
const nowOf = (names: string | string[]): Now => parseNow(fixture(names, { now: NOW })["API-02"])!;
const statusOf = (names: string | string[]): IrrigationStatus => parseIrrigationStatus(fixture(names, { now: NOW })["API-IRR-01"])!;
const read = <T,>(data: T | null, failed = false): PanelRead<T> => ({ data, failed, pending: data === null && !failed, retry: vi.fn() });
const chips = (now: Now, status: IrrigationStatus | null) => runningChips(now, status, NOW).map((chip) => plain(chip.text));
function renderList(names: string | string[], { failed = false, apiDown = false, role = "operator" as "operator" | "viewer" } = {}) {
  const now = nowOf(names);
  renderUi(<QueryClientProvider client={new QueryClient()}><RunningNow now={now} answer={nowAnswer({ now, failed })} irrigation={read(statusOf(names))} /></QueryClientProvider>, { role, writesEnabled: true, apiDown });
}
const shown = () => plain(text(document.body));

test("a wet line: its plain name, the cause in words, since HH:MM and, with the irrigation firmware, the time left", () => {
  expect(chips(nowOf("l01-running"), statusOf("l01-running"))).toEqual(["Wall drip · run now · since 10:30 · 3 min left"]);
  // Before the irrigation firmware there is no time left to read.
  expect(chips(nowOf(["l01-running", "before-engine"]), statusOf(["l01-running", "before-engine"]))).toEqual(["Wall drip · run now · since 10:30"]);
  // The cause from the line's state when API-02 has none, else "cause unknown".
  const quiet = { ...nowOf("l01-running"), running: nowOf("l01-running").running.map((item) => ({ ...item, cause: null })) };
  expect(chips(quiet, statusOf("l01-running"))).toEqual(["Wall drip · run now · since 10:30 · 3 min left"]);
  expect(chips(quiet, null)).toEqual(["Wall drip · cause unknown · since 10:30"]);
});

test("dry equipment reads its name and since; bursts read their time left, since when open-ended, and why they are held; each has its token", () => {
  const now = nowOf("in-band");
  const busy: Now = {
    ...now,
    running: [{ equipment: "heat1", line_id: null, cause: null, since: iso(NOW - 3 * 3_600_000 - 2 * 60_000) }, { equipment: "mister_center", line_id: "L04", cause: "vpd", since: iso(NOW - 4 * 60_000) }],
    overrides: [
      { kind: "fans", since: iso(NOW - 3 * 60_000), ends_at: iso(NOW + 12 * 60_000), held_reason: null },
      { kind: "humidify", since: iso(NOW - 60_000), ends_at: null, held_reason: "occupancy" },
    ],
  };
  expect(chips(busy, statusOf("in-band"))).toEqual([
    "Heat 1 (electric) · since 07:40", "Center mister · VPD · since 10:38", "Fans burst · 12 min left", "Humidify burst · held: Someone inside · since 10:41",
  ]);
  expect(runningChips(busy, null, NOW).map((chip) => chip.token)).toEqual(["eq-heat1", "eq-mister", "eq-fan1", "eq-fog"]);
});

test("in band: Nothing running; a burst's row with Clear for an operator while the irrigation firmware runs, none for a viewer", () => {
  renderList("in-band");
  expect(shown()).toBe("Nothing running");
  expect(document.querySelector("[data-running=none]")).not.toBeNull();
  cleanup();
  renderList("fans-burst");
  expect([...document.querySelectorAll(".now-running [data-chip]")].map((chip) => [chip.getAttribute("data-chip"), plain(text(chip))])).toEqual([["burst", "Fans burst · 12 min left"]]);
  expect(document.querySelector(".now-dot")?.getAttribute("data-token")).toBe("eq-fan1");
  expect(document.querySelector("button")?.textContent).toBe("Clear bursts");
  cleanup();
  renderList("fans-burst", { role: "viewer" });
  expect(document.querySelector("button")).toBeNull();
  cleanup();
  // While the API is down every dot is neutral.
  renderList("l01-running", { apiDown: true });
  expect(document.querySelector(".now-dot")?.className).toBe("now-dot is-neutral");
});

test("while the ingestor is not running or the controller is not reporting: Equipment state unknown since HH:MM, never Nothing running", () => {
  renderList("ingestor-not-running");
  expect(shown()).toBe("Equipment state unknown since 10:38");
  cleanup();
  renderList("controller-not-reporting");
  expect(shown()).toBe("Equipment state unknown since 10:36");
  cleanup();
  // API-02 failing: unknown since the last good answer.
  renderList("l01-running", { failed: true });
  expect(shown()).toBe("Equipment state unknown since 10:42");
});
