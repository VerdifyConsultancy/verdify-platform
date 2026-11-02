// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Alert, Freshness } from "../lib/vocab";
import { ALERTS_UNAVAILABLE, AlertRow, NO_OPEN_ALERTS, alertListEmptyText, alertPlace, type AlertRowProps } from "./alert-row";
import { Button } from "./button";
import { Panel } from "./panel";
import { denver, renderUi, text } from "./test-render";

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(denver("06:42"))); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const ALERT: Alert = {
  id: 41, alert_type: "relay_stuck", category: "equipment", severity: "warning", message: "Fan 2 relay on with no command",
  zone: null, details: { equipment: "fan2" }, status: "open", first_seen_at: denver("06:02"), last_seen_at: denver("06:02"), count: 1,
};
const row = (props: Omit<Partial<AlertRowProps>, "alert"> & { alert?: Partial<Alert> } = {}, role: "operator" | "viewer" = "operator") =>
  renderUi(<ol className="events"><AlertRow {...props} alert={{ ...ALERT, ...props.alert }} /></ol>, { role, writesEnabled: true });

test("critical, warning and info rows carry a severity-coloured rule and the severity word in text", () => {
  for (const [severity, word] of [["critical", "Critical"], ["warning", "Warning"], ["info", "Info"]] as const) {
    const item = row({ alert: { severity } }).container.querySelector("li")!;
    expect(item.className).toBe(`event event-row alert-row alert-${severity}`);
    expect(text(item.querySelector(".alert-severity"))).toBe(word);
    cleanup();
  }
});

test("plain title (raw until #1124 adds the alert types), place, first seen with its age, and the message", () => {
  const { container } = row();
  expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("relay_stuck");
  expect(text(container.querySelector(".alert-facts"))).toBe("fan2 · since 06:02 (40 min)");
  expect(text(container)).toContain("Fan 2 relay on with no command");
  cleanup();
  expect(screen.queryByRole("heading")).toBeNull();
  row({ title: "Fan 2 relay stuck" });
  expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Fan 2 relay stuck");
  // The zone first, else the line, else the equipment.
  expect(alertPlace({ zone: "south", details: { line_id: "L07" } })).toBe("south");
  expect(alertPlace({ zone: null, details: { line_id: "L07", equipment: "mister_south" } })).toBe("L07");
  expect(alertPlace({ zone: null, details: {} })).toBeNull();
  expect(alertPlace({ zone: null })).toBeNull();
});

test("a merged row with count 3 reads \"seen 3 times\" with first and last seen, never \"× 3\"", () => {
  const { container } = row({ alert: { count: 3, first_seen_at: denver("06:02"), last_seen_at: denver("06:40") } });
  const facts = text(container.querySelector(".alert-facts"));
  expect(facts).toBe("fan2 · since 06:02 (40 min) · seen 3 times, last 06:40");
  expect(text(container)).not.toMatch(/[×x]\s?3/);
  cleanup();
  expect(text(row({ alert: { count: 1 } }).container)).not.toContain("seen");
});

test("the current value shows when the alert has one, and an older first seen carries its date", () => {
  expect(text(row({ alert: { metric_value: 84.1 }, unit: "°F" }).container.querySelector(".alert-facts"))).toBe("fan2 · since 06:02 (40 min) · value 84.1 °F");
  cleanup();
  expect(text(row({ alert: { first_seen_at: "2026-10-31T12:02:00Z", metric_value: null } }).container.querySelector(".alert-facts"))).toBe("fan2 · since Oct 31, 06:02 (2 d)");
});

test("the Acknowledge slot shows for an operator and never for a viewer; Open is for everyone", () => {
  const acknowledge = <Button write wide>Acknowledge</Button>;
  row({ acknowledge, openTo: "/activity?alert=41" });
  expect(screen.getByRole("button", { name: "Acknowledge" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Open relay_stuck" }).getAttribute("href")).toBe("/activity?alert=41");
  cleanup();
  const { container } = row({ acknowledge, openTo: "/activity?alert=41" }, "viewer");
  expect(screen.queryByRole("button")).toBeNull();
  expect(container.querySelector("[data-write-control]")).toBeNull();
  expect(screen.getByRole("link", { name: "Open relay_stuck" })).toBeTruthy();
  cleanup();
  // A viewer with nothing else to act on gets no empty action row.
  expect(row({ acknowledge }, "viewer").container.querySelector(".actions")).toBeNull();
});

test("an acknowledged row says who acknowledged it and when", () => {
  const { container } = row({ alert: { status: "acknowledged", acknowledged_by: "Jason", acknowledged_at: denver("06:47") } });
  expect(text(container)).toContain("Acknowledged by Jason · 06:47");
});

const fresh = (age_s: number, at: string, stale = false): Freshness => ({ observed_at: at, age_s, stale });
test("the empty list says \"No open alerts\" only after a good read with the heartbeat and climate fresh", () => {
  const heartbeat = fresh(30, denver("06:41", 30));
  const climate = fresh(40, denver("06:41", 20));
  expect(alertListEmptyText({ readOk: true, ingestor: heartbeat, climate })).toBe(NO_OPEN_ALERTS);
  // The limits are inclusive: heartbeat 180 s, climate 300 s. Climate's own stale flag (twice its cadence) is not the test.
  expect(alertListEmptyText({ readOk: true, ingestor: fresh(180, denver("06:39")), climate: fresh(300, denver("06:37"), true) })).toBe(NO_OPEN_ALERTS);
  // Ingestor not running: never "No open alerts".
  expect(alertListEmptyText({ readOk: true, ingestor: fresh(240, denver("06:02"), true), climate })).toBe("In-app alerts may be missing: ingestor not running since 06:02");
  expect(alertListEmptyText({ readOk: true, ingestor: fresh(181, denver("06:02")), climate })).toBe("In-app alerts may be missing: ingestor not running since 06:02");
  expect(alertListEmptyText({ readOk: true, ingestor: { observed_at: null, age_s: null, stale: true }, climate })).toBe("In-app alerts may be missing: ingestor not running");
  // Controller not reporting: never "No open alerts".
  expect(alertListEmptyText({ readOk: true, ingestor: heartbeat, climate: fresh(360, denver("06:02"), true) })).toBe("In-app alerts may be missing: no data since 06:02");
  expect(alertListEmptyText({ readOk: true, ingestor: heartbeat, climate: fresh(301, denver("06:02")) })).toBe("In-app alerts may be missing: no data since 06:02");
  // A failed read, and a read without API-02's freshness.
  expect(alertListEmptyText({ readOk: false, ingestor: heartbeat, climate })).toBe(ALERTS_UNAVAILABLE);
  expect(ALERTS_UNAVAILABLE).toBe("Alerts unavailable");
  expect(alertListEmptyText({ readOk: true, ingestor: null, climate })).toBe("In-app alerts may be missing: can't reach the greenhouse data");
});

test("an empty alert panel renders those words: never \"No open alerts\" while the ingestor or controller is down", () => {
  const list = (props: Parameters<typeof alertListEmptyText>[0]) => <Panel title="Needs someone" thing="alerts" state="empty" empty={<p>{alertListEmptyText(props)}</p>} />;
  const climate = fresh(40, denver("06:41", 20));
  for (const props of [{ readOk: true, ingestor: fresh(240, denver("06:02"), true), climate }, { readOk: true, ingestor: fresh(30, denver("06:41", 30)), climate: fresh(360, denver("06:02"), true) }]) {
    const shown = text(renderUi(list(props)).container);
    expect(shown).toContain("In-app alerts may be missing");
    expect(shown).not.toContain(NO_OPEN_ALERTS);
    cleanup();
  }
  expect(text(renderUi(list({ readOk: false, ingestor: null, climate: null })).container)).toBe("Needs someoneAlerts unavailable");
});
