// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Needs someone (redesign §5.1, US-ACT-02) against #1122's fixture API in process: the top three unacknowledged alerts by
// severity then age with the one count in the head (unless the answer counts them); Acknowledge sends one API-15 request
// with one Idempotency-Key and the row then reads "Acknowledged by jason · 10:42"; a mister's latch with Clear fault, read
// once; the settings not confirmed; a fence breach pointing to the quick action; the empty and degraded text; viewers
// get no Acknowledge.
import { cleanup, fireEvent, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { Alert } from "../../lib/operator-api";
import { nowAnswer } from "../../lib/now-answer";
import { panelRead, useApiRead, useNowRead } from "../../lib/queries";
import { UUID, advance, renderWithApi, startFixtureApi, stopFixtureApi, writes } from "../../lib/test-fixture-api";
import { text } from "../test-render";
import { NeedsSomeone, faultText, topAlerts } from "./needs-someone";

afterEach(() => { cleanup(); stopFixtureApi(); });

/** Now's two reads for the panel: API-14 open alerts, and API-02 for the count and the freshness. */
function Harness({ apiFailed = false }: { apiFailed?: boolean }) {
  const alerts = panelRead(useApiRead("API-14", { status: "open" }, { intervalMs: 15_000 }));
  const irrigation = panelRead(useApiRead("API-IRR-01", {}, { intervalMs: 15_000 }));
  const now = useNowRead();
  const answer = nowAnswer({ now: now.data ?? null, failed: apiFailed });
  return <NeedsSomeone alerts={alerts} now={now.data ?? null} answer={answer} apiFailed={apiFailed} openCount={now.data?.alerts.open ?? null} irrigation={irrigation} />;
}
const panel = () => document.querySelector<HTMLElement>("section.panel")!;
const rows = () => [...panel().querySelectorAll<HTMLElement>("li.alert-row")];
const title = () => text(within(panel()).getByRole("heading", { level: 2 }));

test("alerts-merged: the unacknowledged rows, warning before info, Details links and Acknowledge; the count in the head only when the answer has not given it", async () => {
  startFixtureApi({ fixture: "alerts-merged" });
  renderWithApi(<Harness />);
  await advance(0);
  // The answer reads "2 things need someone", so the head does not count again (redesign §2.5).
  expect(title()).toBe("Needs someone");
  expect(rows().map((row) => row.getAttribute("data-severity"))).toEqual(["warning", "info"]);
  expect(rows().map((row) => text(row.querySelector("h3")))).toEqual(["Relay stuck", "Soil probe offline"]);
  expect(text(rows()[0].querySelector(".alert-facts"))).toBe("Fan 2 · ×3 since 06:02 · last 06:40");
  expect(within(rows()[0]).getByRole("link", { name: /^Details/ }).getAttribute("href")).toBe("/activity?alert=101");
  expect(within(panel()).getByRole("link", { name: /All alerts/ }).getAttribute("href")).toBe("/activity");
  expect(within(panel()).getAllByRole("button", { name: /^Acknowledge/ })).toHaveLength(2);
  // The acknowledged repeat (103) is not one that needs someone.
  expect(text(panel())).not.toContain("fog_rh_ceiling_pct");
});

test("Acknowledge sends one API-15 request with one key, then the row reads Acknowledged by jason · 10:42 and the count drops", async () => {
  const api = startFixtureApi({ fixture: "alerts-merged" });
  renderWithApi(<Harness />);
  await advance(0);
  fireEvent.click(within(rows()[0]).getByRole("button", { name: /^Acknowledge/ }));
  await advance(0);
  const sent = writes(api);
  expect(sent).toEqual([expect.objectContaining({ method: "POST", operationId: "API-15", path: "/api/v1/alerts/101/ack", body: null })]);
  expect(sent[0].idempotency_key).toMatch(UUID);
  expect(text(rows()[0])).toContain("Acknowledged by jason · 10:42");
  expect(within(rows()[0]).queryByRole("button", { name: /^Acknowledge/ })).toBeNull();
  await advance(0);
  // One open now, and the answer still counts it ("1 thing needs someone").
  expect(title()).toBe("Needs someone");
});

test("the head counts the open alerts when the answer says something else first: an out-of-band reading", async () => {
  startFixtureApi({ fixture: ["alerts-merged", "too-hot"] });
  renderWithApi(<Harness />);
  await advance(0);
  expect(title()).toBe("Needs someone · 2");
});

test("a latched mister reads once, on its alert's row with Clear fault; the settings not confirmed link to Settings, unless the stuck banner says so", async () => {
  const api = startFixtureApi({ fixture: ["mister-fault", "settings-unconfirmed"] });
  renderWithApi(<Harness />);
  await advance(0);
  const faulted = rows().find((row) => text(row.querySelector("h3")) === "A mister zone faulted")!;
  expect(document.querySelectorAll("[data-water=fault]")).toHaveLength(0);
  fireEvent.click(within(faulted).getByRole("button", { name: "Clear fault on L04" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-13", body: { line_id: "L04" } })]);
  const unconfirmed = within(panel()).getByRole("link", { name: /^2 settings not confirmed/ });
  expect(unconfirmed.getAttribute("href")).toBe("/settings?filter=not_confirmed");
  cleanup();
  stopFixtureApi();
  // With no alert naming it, a latch has its own row; while delivery is stuck the banner says so and the link does not repeat it.
  startFixtureApi({ fixture: ["mister-fault", "stuck-delivery"] });
  renderWithApi(<Harness />);
  await advance(0);
  expect(within(panel()).queryByRole("link", { name: /not confirmed/ })).toBeNull();
});

test("a mister's fault in words: a back-off until its time, a latch until midnight, a code this build lacks raw", () => {
  const line = { line_id: "L07", name: "Mister • South Wall", fault_until: "2026-11-02T20:20:00Z" };
  expect(faultText({ ...line, fault: "backing_off" })).toBe("South mister: backing off until 13:20");
  expect(faultText({ ...line, fault: "mister_fault" })).toBe("South mister: faulted, no misting until midnight (watchdog)");
  expect(faultText({ ...line, fault: "brand_new" })).toBe("South mister: brand_new");
});

test("a fence breach in Needs someone points to Stop all water in the quick actions instead of repeating it", async () => {
  startFixtureApi({ fixture: "fence-breach" });
  renderWithApi(<Harness />);
  await advance(0);
  expect(text(panel())).toContain("Stop all water is in the quick actions above.");
  expect(within(panel()).queryByRole("button", { name: /Stop all water/ })).toBeNull();
});

test("an optional note goes with the acknowledgement, at most 500 characters", async () => {
  const api = startFixtureApi({ fixture: "alerts-merged" });
  renderWithApi(<Harness />);
  await advance(0);
  const row = rows()[0];
  fireEvent.click(within(row).getByText("Add a note"));
  const note = within(row).getByLabelText("Note (optional)");
  expect(note.getAttribute("maxlength")).toBe("500");
  fireEvent.change(note, { target: { value: "Breaker reset" } });
  fireEvent.click(within(row).getByRole("button", { name: /^Acknowledge/ }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-15", body: { note: "Breaker reset" } })]);
});

test("Acknowledge is disabled while changes are switched off, and a viewer sees none", async () => {
  startFixtureApi({ fixture: ["alerts-merged", "writes-off"] });
  renderWithApi(<Harness />, { role: "operator", writesEnabled: false });
  await advance(0);
  expect(within(panel()).getAllByRole("button", { name: /^Acknowledge/ }).every((button) => button.hasAttribute("disabled"))).toBe(true);
  cleanup();
  stopFixtureApi();
  startFixtureApi({ fixture: ["alerts-merged", "viewer-session"] });
  renderWithApi(<Harness />, { role: "viewer", writesEnabled: false });
  await advance(0);
  expect(rows()).toHaveLength(2);
  expect(within(panel()).queryByRole("button", { name: /^Acknowledge/ })).toBeNull();
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
});

test.each([
  ["in-band", "No open alerts"],
  ["ingestor-not-running", "In-app alerts may be missing: ingestor not running since 10:38"],
  ["controller-not-reporting", "In-app alerts may be missing: no data since 10:36"],
])("%s with nothing open reads \"%s\"", async (name, expected) => {
  startFixtureApi({ fixture: name });
  renderWithApi(<Harness />);
  await advance(0);
  expect(text(panel().querySelector(".panel-empty p"))).toBe(expected);
  // Only an honest "No open alerts" offers the alerts resolved in the last 24 h.
  expect(panel().querySelector(".panel-empty a")?.getAttribute("href") ?? null).toBe(expected === "No open alerts" ? "/activity?resolved=24h" : null);
});

test("while the ingestor is not running the rows show with a note that more may be missing; a failed read reads Alerts unavailable", async () => {
  startFixtureApi({ fixture: ["alerts-merged", "ingestor-not-running"] });
  renderWithApi(<Harness />);
  await advance(0);
  expect(rows()).toHaveLength(2);
  expect(text(panel().querySelector("[data-alerts=coverage]"))).toBe("In-app alerts may be missing: ingestor not running since 10:38");
  cleanup();
  stopFixtureApi();
  startFixtureApi({ fixture: "alerts-merged", fail: { "API-14": 503 } });
  renderWithApi(<Harness />);
  await advance(0);
  expect(text(panel().querySelector(".panel-error"))).toBe("Alerts unavailableRetry");
  expect(within(panel()).getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(text(panel())).not.toContain("No open alerts");
});

test("with API-02 failing an empty list cannot vouch for itself", async () => {
  startFixtureApi({ fixture: "in-band" });
  renderWithApi(<Harness apiFailed />);
  await advance(0);
  expect(text(panel().querySelector(".panel-empty p"))).toBe("In-app alerts may be missing: can't reach the greenhouse data");
});

test("the top three: critical, warning, info, each in API-14's order; acknowledged rows only when kept from this page", () => {
  const alert = (id: number, severity: string, firstSeen: string, status = "open"): Alert => ({
    id, alert_type: "relay_stuck", category: "system", severity, message: "", zone: null, metric_value: null, threshold_value: null, status,
    first_seen_at: firstSeen, last_seen_at: null, count: 1, acknowledged_at: null, acknowledged_by: null, notes: null, resolved_at: null, resolved_by: null,
    resolution: null, runbook: null,
  });
  const items = [
    alert(1, "info", "2026-11-02T10:00:00Z"), alert(2, "warning", "2026-11-02T12:00:00Z"), alert(3, "critical", "2026-11-02T14:00:00Z"),
    alert(4, "warning", "2026-11-02T11:00:00Z"), alert(5, "critical", "2026-11-02T09:00:00Z", "acknowledged"),
  ];
  // Within a severity the API's order stands (it sorts by last seen, newest first), never Now's own idea of age.
  expect(topAlerts(items).map((item) => item.id)).toEqual([3, 2, 4]);
  expect(topAlerts(items, new Set([5])).map((item) => item.id)).toEqual([3, 5, 2]);
});
