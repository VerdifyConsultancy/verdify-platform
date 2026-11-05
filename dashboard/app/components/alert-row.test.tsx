// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Alert, Freshness } from "../lib/vocab";
import { ALERTS_UNAVAILABLE, AlertRow, NO_OPEN_ALERTS, alertListEmptyText, alertPlace, alertPlaceWords, type AlertRowProps } from "./alert-row";
import { Button } from "./button";
import { Panel } from "./panel";
import { denver, renderUi, text } from "./test-render";

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(denver("06:42"))); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const ALERT: Alert = {
  id: 41, alert_type: "relay_stuck", category: "equipment", severity: "warning", message: "Fan 2 relay on with no command",
  zone: null, details: { equipment: "fan2" }, status: "open", first_seen_at: denver("06:02"), last_seen_at: denver("06:02"), count: 1,
};
const row = (props: Omit<Partial<AlertRowProps>, "alert"> & { alert?: Partial<Alert> } = {}, role: "operator" | "viewer" = "operator", apiDown = false) =>
  renderUi(<ol className="alert-list"><AlertRow {...props} alert={{ ...ALERT, ...props.alert }} /></ol>, { role, writesEnabled: true, apiDown });

test("an ObjectRow with the severity's rule (fault for critical, amber for a warning, muted for info) and the severity word in text", () => {
  for (const [severity, word, tone] of [["critical", "Critical", " tone-fault"], ["warning", "Warning", " tone-warn"], ["info", "Info", ""]] as const) {
    const item = row({ alert: { severity } }).container.querySelector("li")!;
    expect(item.className).toBe(`object-row${tone} alert-row alert-${severity}`);
    expect(item.getAttribute("data-severity")).toBe(severity);
    expect(text(item.querySelector(".alert-severity"))).toBe(word);
    cleanup();
  }
  // While the API is down the row drops its verdict colour; the severity word stays.
  const down = row({ alert: { severity: "critical" } }, "operator", true).container.querySelector("li")!;
  expect(down.className).toBe("object-row alert-row alert-critical");
  expect(down.querySelector("[style]")).toBeNull();
});

test("plain title from the words table (raw for a type it lacks), the place in words, first seen with its age, and the message", () => {
  const { container } = row();
  expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Relay stuck");
  expect(text(container.querySelector(".alert-facts"))).toBe("Fan 2 · since 06:02 (40 min)");
  expect(text(container.querySelector(".alert-message"))).toBe("Fan 2 relay on with no command");
  // A screen that says the message live itself leaves it out.
  cleanup();
  expect(row({ message: false }).container.querySelector(".alert-message")).toBeNull();
  cleanup();
  expect(screen.queryByRole("heading")).toBeNull();
  row({ title: "Fan 2 relay stuck" });
  expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Fan 2 relay stuck");
  // The zone first, else the line, else the equipment.
  expect(alertPlace({ zone: "south", details: { line_id: "L07" } })).toBe("south");
  expect(alertPlace({ zone: null, details: { line_id: "L07", equipment: "mister_south" } })).toBe("L07");
  expect(alertPlace({ zone: null, details: {} })).toBeNull();
  expect(alertPlace({ zone: null })).toBeNull();
  expect([alertPlaceWords({ zone: "south" }), alertPlaceWords({ zone: null, details: { line_id: "L02" } }), alertPlaceWords({ zone: null, details: { equipment: "fan2" } }), alertPlaceWords({ zone: null, details: { equipment: "pump9" } })])
    .toEqual(["South", "L02", "Fan 2", "pump9"]);
});

test("merged repeats read \"×3 since 06:02 · last 06:40\" (redesign §5.8); a single one its age; another day carries its date", () => {
  const { container } = row({ alert: { count: 3, first_seen_at: denver("06:02"), last_seen_at: denver("06:40") } });
  expect(text(container.querySelector(".alert-facts"))).toBe("Fan 2 · ×3 since 06:02 · last 06:40");
  cleanup();
  expect(text(row({ alert: { count: 12, first_seen_at: "2026-10-30T09:10:00Z", last_seen_at: denver("06:22") } }).container.querySelector(".alert-facts"))).toBe("Fan 2 · ×12 since Oct 30, 03:10 · last 06:22");
  cleanup();
  expect(text(row({ alert: { count: 1 } }).container.querySelector(".alert-facts"))).not.toContain("×");
  cleanup();
  // The value is in the expanded fields, not the row.
  expect(text(row({ alert: { first_seen_at: "2026-10-31T12:02:00Z", metric_value: 84.1 } }).container.querySelector(".alert-facts"))).toBe("Fan 2 · since Oct 31, 06:02 (2 d)");
});

test("the Acknowledge slot shows for an operator and never for a viewer; Details › and what the alert calls for are for everyone", () => {
  const acknowledge = <Button write wide>Acknowledge</Button>;
  row({ acknowledge, openTo: "/activity?alert=41", action: <span data-action="">Stop</span> });
  expect(screen.getByRole("button", { name: "Acknowledge" }).closest(".alert-ack")).not.toBeNull();
  expect(screen.getByRole("link", { name: "Details of Relay stuck" }).getAttribute("href")).toBe("/activity?alert=41");
  // The actions sit at the row's end: what it calls for, then Details.
  expect([...document.querySelector(".object-row-actions")!.children].map((node) => node.getAttribute("data-action") ?? node.className)).toEqual(["", "alert-open"]);
  cleanup();
  const { container } = row({ acknowledge, openTo: "/activity?alert=41" }, "viewer");
  expect(screen.queryByRole("button")).toBeNull();
  expect(container.querySelector("[data-write-control]")).toBeNull();
  expect(container.querySelector(".alert-ack")).toBeNull();
  expect(screen.getByRole("link", { name: /^Details/ })).toBeTruthy();
  cleanup();
  // Activity passes its own toggle in place of the link.
  row({ details: <a href="/activity?alert=41">Hide details</a> });
  expect(screen.getByRole("link").textContent).toBe("Hide details");
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
