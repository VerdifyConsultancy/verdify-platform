// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// What runs now (#1129, US-NOW-03, US-WAT-16) over #1122's fixtures at 10:42: a chip per running item with its plain
// name and "since HH:MM", wet lines with the cause and the time left, bursts from the readback; "Equipment state unknown
// since HH:MM" while rows 1 to 3 hold, never "Nothing running"; and the occupancy chip, always.
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { nowAnswer } from "../../lib/now-answer";
import { parseIrrigationStatus, parseNow, type IrrigationStatus, type Now } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { renderUi, text } from "../test-render";
import { RunningPanel, occupancyText, runningChips } from "./running";

const NOW = Date.parse("2026-11-02T17:42:00Z");
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const iso = (ms: number) => new Date(ms).toISOString();
const plain = (value: string) => value.replaceAll(" ", " ");
const nowOf = (names: string | string[]): Now => parseNow(fixture(names, { now: NOW })["API-02"])!;
const statusOf = (names: string | string[]): IrrigationStatus => parseIrrigationStatus(fixture(names, { now: NOW })["API-IRR-01"])!;
const read = <T,>(data: T | null, failed = false): PanelRead<T> => ({ data, failed, pending: data === null && !failed, retry: vi.fn() });
const chips = (now: Now, status: IrrigationStatus | null) => runningChips(now, status, NOW).map((chip) => plain(chip.text));
function renderPanel(names: string | string[], { failed = false, irrigation }: { failed?: boolean; irrigation?: PanelRead<IrrigationStatus> } = {}) {
  const now = nowOf(names);
  renderUi(<RunningPanel now={now} answer={nowAnswer({ now, failed })} irrigation={irrigation ?? read(statusOf(names))} />);
}
const panel = () => plain(text(document.querySelector(".now-running")));

test("a wet line: its plain name, the cause in words, since HH:MM and, with the irrigation firmware, the time left", () => {
  expect(chips(nowOf("l01-running"), statusOf("l01-running"))).toEqual(["Wall drip · run now · since 10:30 · 3 min left"]);
  // Before the irrigation firmware there is no time left to read.
  expect(chips(nowOf(["l01-running", "before-engine"]), statusOf(["l01-running", "before-engine"]))).toEqual(["Wall drip · run now · since 10:30"]);
  // The cause from the line's state when API-02 has none, else "cause unknown".
  const quiet = { ...nowOf("l01-running"), running: nowOf("l01-running").running.map((item) => ({ ...item, cause: null })) };
  expect(chips(quiet, statusOf("l01-running"))).toEqual(["Wall drip · run now · since 10:30 · 3 min left"]);
  expect(chips(quiet, null)).toEqual(["Wall drip · cause unknown · since 10:30"]);
});

test("dry equipment reads its name and since; bursts read their time left, since when open-ended, and why they are held", () => {
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
});

test("in band: Nothing running, and the occupancy chip from API-IRR-01", () => {
  renderPanel("in-band");
  expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Running · commanded");
  expect(panel()).toBe("Running · commandedNothing runningNobody seen: misters may run (last person 05:10)");
});

test("while the ingestor is not running or the controller is not reporting: Equipment state unknown since HH:MM, never Nothing running", () => {
  renderPanel("ingestor-not-running");
  expect(panel()).toContain("Equipment state unknown since 10:38");
  expect(panel()).not.toContain("Nothing running");
  expect(document.querySelector("[data-chip=occupancy]")).not.toBeNull();
  cleanup();
  renderPanel("controller-not-reporting");
  expect(panel()).toContain("Equipment state unknown since 10:36");
  cleanup();
  // API-02 failing: unknown since the last good answer, and occupancy still shows.
  renderPanel("l01-running", { failed: true });
  expect(panel()).toContain("Equipment state unknown since 10:42");
  expect(panel()).not.toContain("Wall drip");
});

test("occupancy always shows: someone inside, nobody seen, unknown, the hold off, and unknown while API-IRR-01 fails", () => {
  expect(plain(occupancyText(read(statusOf("occupied")), NOW))).toBe("Someone inside: misters wait (Frigate, 4 min ago)");
  expect(occupancyText(read(statusOf("in-band")), NOW)).toBe("Nobody seen: misters may run (last person 05:10)");
  expect(occupancyText(read(statusOf("occupancy-unknown")), NOW)).toBe("Occupancy unknown: misters may run");
  expect(occupancyText(read(statusOf(["occupied", "occupancy-hold-off"])), NOW)).toBe("Occupancy mist hold is off: misters may run");
  expect(occupancyText(read(statusOf("in-band"), true), NOW)).toBe("Occupancy unknown");
  expect(occupancyText(read<IrrigationStatus>(null), NOW)).toBe("Occupancy unknown");
  const older = statusOf("in-band");
  expect(occupancyText(read({ ...older, occupancy: { ...older.occupancy, seen_at: iso(NOW - 3 * 86_400_000) } }), NOW)).toBe("Nobody seen: misters may run (last person Oct 30, 11:42)");
  renderPanel("in-band", { irrigation: read(statusOf("in-band"), true) });
  expect(text(document.querySelector("[data-chip=occupancy]"))).toBe("Occupancy unknown");
});
