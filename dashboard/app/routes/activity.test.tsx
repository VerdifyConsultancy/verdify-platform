// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * Activity (redesign §5.8 Alerts, §5.9 Log; SCR-06) inside the app shell, against #1122's fixture API in process (fetch is
 * stubbed onto it; GET /__requests gives each request with its body and Idempotency-Key). The clock is the screens' 10:42
 * America/Denver on 2026-11-02, and the signed-in operator is jason (Jason) unless a fixture says otherwise.
 */
import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { AppShell } from "../components/app-shell";
import { text } from "../components/test-render";
import { UUID, advance, requests, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import Activity, { clientLoader } from "./activity";
import ActivityLog, { LOG_STATUS } from "./activity-log";

const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
function Where() {
  const location = useLocation();
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>;
}
/** Activity → Alerts and Activity → Log; `redirect` adds Alerts' loader, which sends an old log link to the log. */
function mount(path: string, redirect = false) {
  const Stub = createRoutesStub([{
    path: "/", Component: () => <AppShell build={BUILD} authError={null} />,
    children: [
      { path: "activity", ...(redirect ? { loader: clientLoader } : {}), Component: () => <><Activity /><Where /></> },
      { path: "activity/log", Component: () => <><ActivityLog /><Where /></> },
    ],
  }]);
  return render(<StrictMode><Stub initialEntries={[path]} /></StrictMode>);
}
/** The fixture API with `options`, then the page at `path` with every first read answered. */
async function open(path: string, options: FixtureOptions = {}, redirect = false) {
  const api = startFixtureApi(options);
  mount(path, redirect);
  await advance(0);
  // The loader answers before the page renders: one more turn for its first reads.
  if (redirect) await advance(0);
  return api;
}
afterEach(() => { cleanup(); stopFixtureApi(); vi.unstubAllGlobals(); });

const plain = (value: string) => value.replaceAll(String.fromCharCode(0xa0), " ");
const needsAction = () => screen.getByRole("region", { name: "Needs action" });
const status = () => plain(text(document.querySelector(".page-status")));
const logPanel = () => screen.getByRole("region", { name: "Activity log" });
const alertRow = (id: number) => {
  const node = document.getElementById(`alert-${id}`);
  if (!node) throw new Error(`no alert ${id} on the page`);
  return node;
};
const openList = () => [...needsAction().querySelectorAll("ol.alert-list > li")].map((node) => node.id);
const group = (name: RegExp) => [...document.querySelectorAll<HTMLDetailsElement>("details.alert-group")].find((node) => name.test(text(node.querySelector("summary"))));
const navCount = () => text(within(screen.getByRole("navigation", { name: "Sections" })).getByRole("link", { name: /^Activity/ }));
const logRows = () => [...logPanel().querySelectorAll("li.event")];
const what = (node: Element) => plain(text(node.querySelector(".act-entry-what > .act-entry-text, .act-entry-what > details > summary")));
const logLines = () => logRows().map(what);
const logRow = (start: string) => {
  const node = logRows().find((item) => what(item).startsWith(start));
  if (!node) throw new Error(`no log entry "${start}" in ${JSON.stringify(logLines())}`);
  return node;
};
const writtenBy = (node: Element) => text(node.querySelector(".event-by"));
/** The Log tab, as a person reaches the log from Alerts. */
async function toLog() {
  fireEvent.click(within(screen.getByRole("navigation", { name: "Activity views" })).getByRole("link", { name: "Log" }));
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity/log");
}
const politeToasts = () => [...screen.getByRole("status").querySelectorAll(".toast-body")].map((body) => [...body.querySelectorAll("p")].map(text).join(" "));
const stopped = () => { cleanup(); stopFixtureApi(); };

// ---------------------------------------------------------------------------------------------------- alerts
test("with API-14 failing Needs action reads Alerts unavailable. Retry, never No open alerts, the status line claims nothing, and the log still loads", async () => {
  await open("/activity", { fixture: "activity-mixed", fail: { "API-14": 503 } });
  const panel = needsAction();
  expect(text(panel.querySelector(".panel-error"))).toBe("Alerts unavailable.Retry");
  expect(within(panel).getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(text(document.body)).not.toContain("No open alerts");
  expect(status()).toBe("");
  await toLog();
  expect(logRows()).toHaveLength(6);
});

test("alerts-merged: the status line states the count once; Needs action in API order with repeat counts; the acknowledged one collapsed with who and when", async () => {
  const api = await open("/activity", { fixture: "alerts-merged" });
  expect(status()).toBe("2 open · 1 acknowledged");
  expect(navCount()).toBe("Activity 2 open alerts");
  // The head does not count again: the status line says it.
  expect(within(needsAction()).getByRole("heading", { level: 2 }).textContent).toBe("Needs action");
  expect(openList()).toEqual(["alert-101", "alert-102"]);
  expect(text(alertRow(101).querySelector("h3"))).toBe("Relay stuck");
  expect(text(alertRow(101).querySelector(".alert-facts"))).toBe("Fan 2 · ×3 since 06:02 · last 06:40");
  expect(text(alertRow(102).querySelector(".alert-facts"))).toBe("South · ×12 since Oct 30, 03:10 · last 10:22");
  const acknowledged = group(/^Acknowledged, still open/)!;
  expect(text(acknowledged.querySelector("summary"))).toBe("Acknowledged, still open (1)");
  expect(acknowledged.open).toBe(false);
  expect(alertRow(103).closest("details")).toBe(acknowledged);
  // The API names people by username; the signed-in person reads by name.
  expect(text(alertRow(103))).toContain("Acknowledged by Jason · 06:47");
  expect(within(alertRow(103)).queryByRole("button", { name: /^Acknowledge/, hidden: true })).toBeNull();
  expect(group(/^Resolved/)).toBeUndefined();
  // One API-14 read serves the three groups.
  expect(requests(api, "API-14").map((row) => row.path)).toEqual(["/api/v1/alerts?status=recent&limit=200"]);
});

test("with nothing open the status line says so honestly and Needs action is not drawn; the resolved group holds the last 24 h", async () => {
  const cases = [
    ["in-band", "No open alerts"],
    ["ingestor-not-running", "In-app alerts may be missing: ingestor not running since 10:38"],
    ["controller-not-reporting", "In-app alerts may be missing: no data since 10:36"],
  ] as const;
  for (const [fixture, words] of cases) {
    await open("/activity", { fixture });
    expect(status(), fixture).toBe(words);
    expect(screen.queryByRole("region", { name: "Needs action" })).toBeNull();
    stopped();
  }
  await open("/activity", { fixture: "alert-resolved" });
  expect(status()).toBe("No open alerts · 1 resolved in 24 h");
  const resolved = group(/^Resolved/)!;
  expect(text(resolved.querySelector("summary"))).toBe("Resolved in the last 24 h (1)");
  expect(resolved.open).toBe(false);
  expect(text(alertRow(104))).toContain("Resolved by Jason · 08:42 · “Probe reseated”");
  stopped();
  // resolved=24h, as an old link has it, opens that group.
  await open("/activity?resolved=24h", { fixture: "alert-resolved" });
  expect(group(/^Resolved/)!.open).toBe(true);
});

test("Acknowledge in one tap with an optional note: one API-15 with the note, who and when, the row under Acknowledged, the nav count down by one", async () => {
  const api = await open("/activity", { fixture: "alerts-merged" });
  const item = alertRow(101);
  fireEvent.click(within(item).getByText("Add a note"));
  fireEvent.change(within(item).getByLabelText("Note (optional)"), { target: { value: "On it" } });
  const acknowledge = within(item).getByRole("button", { name: "Acknowledge Relay stuck" });
  fireEvent.click(acknowledge);
  fireEvent.click(acknowledge);
  await advance(0);
  const sent = requests(api, "API-15");
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({ method: "POST", path: "/api/v1/alerts/101/ack", body: { note: "On it" } });
  expect(sent[0].idempotency_key).toMatch(UUID);
  await advance(0);
  expect(politeToasts()).toEqual(["“Relay stuck” acknowledged by Jason · 10:42"]);
  // The row reads who and when at once, under the acknowledged group.
  expect(text(alertRow(101))).toContain("Acknowledged by Jason · 10:42");
  expect(text(group(/^Acknowledged/)!.querySelector("summary"))).toBe("Acknowledged, still open (2)");
  expect(openList()).toEqual(["alert-102"]);
  // The one count: the nav and the status line both read API-02's, refreshed at once.
  expect(navCount()).toBe("Activity 1 open alerts");
  expect(status()).toBe("1 open · 2 acknowledged");
  // The log, on its own tab, has the alert.ack entry with the note as its reason.
  await toLog();
  const entry = logRow("Acknowledged “Relay stuck”");
  expect(writtenBy(entry)).toBe("Jason · dashboard");
  expect(text(entry.querySelector(".event-reason"))).toBe("“On it”");
});

test("an expanded alert: Acknowledge and Resolve send the note typed there; Resolve moves it to the resolved group", async () => {
  const api = await open("/activity?alert=102", { fixture: "alerts-merged" });
  const item = alertRow(102);
  // Expanded, the row offers the note with both; the collapsed Acknowledge is gone.
  expect(within(item).queryByText("Add a note")).toBeNull();
  const note = within(item).getByLabelText("Note (optional)");
  fireEvent.change(note, { target: { value: "Probe reseated" } });
  expect(text(item.querySelector(".field-hint"))).toBe("14/500 characters, sent with Acknowledge or Resolve");
  fireEvent.click(within(item).getByRole("button", { name: "Resolve" }));
  await advance(0);
  await advance(0);
  expect(requests(api, "API-16")).toEqual([expect.objectContaining({ method: "POST", path: "/api/v1/alerts/102/resolve", body: { resolution: "Probe reseated" } })]);
  expect(requests(api, "API-16")[0].idempotency_key).toMatch(UUID);
  expect(document.querySelector("dialog[open]")).toBeNull();
  expect(openList()).toEqual(["alert-101"]);
  // The link still names it, so its group opens to show it as resolved.
  const resolved = group(/^Resolved/)!;
  expect(resolved.open).toBe(true);
  expect(alertRow(102).closest("details")).toBe(resolved);
  expect(text(alertRow(102))).toContain("Resolved by Jason · 10:42 · “Probe reseated”");
  expect(within(alertRow(102)).queryByRole("button", { name: /Acknowledge|Resolve/ })).toBeNull();
  await toLog();
  expect(text(logRow("Resolved “Soil probe offline”").querySelector(".event-reason"))).toBe("“Probe reseated”");
});

test("Acknowledge on a collapsed row sends only its own note, never one typed while the row was open", async () => {
  const api = await open("/activity?alert=102", { fixture: "alerts-merged" });
  fireEvent.change(within(alertRow(102)).getByLabelText("Note (optional)"), { target: { value: "draft" } });
  fireEvent.click(within(alertRow(102)).getByRole("link", { name: "Hide details" }));
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity");
  expect(alertRow(102).querySelector(".alert-detail")).toBeNull();
  fireEvent.click(within(alertRow(102)).getByRole("button", { name: "Acknowledge Soil probe offline" }));
  await advance(0);
  expect(requests(api, "API-15")).toEqual([expect.objectContaining({ path: "/api/v1/alerts/102/ack", body: null })]);
});

test("a refused Acknowledge says why beside its row and moves nothing; with changes off the note, Acknowledge and Resolve are disabled", async () => {
  await open("/activity", { fixture: "alerts-merged", fail: { "API-15": 503 } });
  fireEvent.click(within(alertRow(101)).getByRole("button", { name: "Acknowledge Relay stuck" }));
  await advance(0);
  expect(text(within(alertRow(101)).getByRole("alert"))).toBe("The fixture fails API-15 with 503");
  expect(openList()).toEqual(["alert-101", "alert-102"]);
  stopped();
  await open("/activity?alert=101", { fixture: ["alerts-merged", "writes-off"] });
  const item = alertRow(101);
  expect(within(item).getByLabelText("Note (optional)").hasAttribute("disabled")).toBe(true);
  for (const name of ["Acknowledge", "Resolve"]) expect(within(item).getByRole("button", { name }).hasAttribute("disabled"), name).toBe(true);
});

test("the flow alert reads its plain title, expands in place with its runbook and Open in Graphs ±2 h, and offers the spigot, never a Stop", async () => {
  await open("/activity?alert=201", { fixture: "flow-valves-closed" });
  const item = alertRow(201);
  expect(text(item.querySelector("h3"))).toBe("Water flowing with every Verdify valve closed");
  const lines = [...item.querySelectorAll(".act-leak > p")].map((node) => plain(text(node)));
  expect(lines).toEqual(["A leak, or the hose, sink or hydro in use.", "3 gal in the last 10 min · Updated 20 s ago", "If no one is using water, close the spigot."]);
  // The title says it once: the alert's message is the same words, shown in the expanded fields.
  expect(item.querySelector(".alert-message")).toBeNull();
  expect(within(item).queryByRole("button", { name: /Stop/ })).toBeNull();
  expect(text(item.querySelector(".runbook"))).toBe("1. Check the hose, sink and hydro. 2. Walk the wall line and the manifold for a break. 3. If no one is using water, close the spigot.");
  // First seen 10:38 (17:38Z): the window 15:38Z to 19:38Z.
  expect(within(item).getByRole("link", { name: /^Open in Graphs/ }).getAttribute("href")).toBe("/graphs?from=2026-11-02T15:38:00Z&to=2026-11-02T19:38:00Z");
  expect(text(item.querySelector(".alert-detail .kv"))).toContain("TypeWater flowing with every Verdify valve closedCategorywaterSeverityCritical");
  expect(text(item)).not.toContain("leak_detected");
  stopped();
  // Once the meter has read under 0.1 gal/min twice: Flow stopped at HH:MM, never an all clear.
  await open("/activity", { fixture: "flow-stopped" });
  const stoppedLines = [...alertRow(201).querySelectorAll(".act-leak > p")].map(text);
  expect(stoppedLines).toEqual(["A leak, or the hose, sink or hydro in use.", "Flow stopped at 10:40", "If no one is using water, close the spigot."]);
  expect(text(alertRow(201))).not.toMatch(/all clear|0 gal/i);
});

test("a runbook renders as text with its line breaks, never as HTML", async () => {
  await open("/activity?alert=501", { fixture: "fence-breach" });
  const runbook = alertRow(501).querySelector(".runbook");
  expect(runbook?.textContent).toBe("1. Stop all water.\n2. Check that the south fert mister (L08) relay reads OFF.\n3. If water still flows, close the spigot.");
  expect(runbook?.children).toHaveLength(0);
  expect(text(alertRow(501))).not.toContain("irrigation_fence_breach");
});

test("a copied /activity?alert=<id> link still opens the alert after a new repeat merges; an id in no group says so", async () => {
  await open("/activity?alert=201", { fixture: ["flow-valves-closed", "alert-repeat"] });
  expect(alertRow(201).querySelector(".alert-detail")).not.toBeNull();
  expect(text(alertRow(201).querySelector(".alert-facts"))).toContain("×2 since 10:38 · last 10:41");
  expect(screen.queryByRole("region", { name: "Linked alert" })).toBeNull();
  stopped();
  await open("/activity?alert=999", { fixture: "alerts-merged" });
  expect(text(screen.getByRole("region", { name: "Linked alert" }))).toBe("Linked alertAlert 999 is not open or resolved in the last 24 h");
});

test("the run-missed alert reads its plain title with L02, never the raw code, and Details expands it in place", async () => {
  await open("/activity", { fixture: "run-missed" });
  const item = alertRow(502);
  expect(text(item.querySelector("h3"))).toBe("A scheduled watering was missed");
  expect(text(item.querySelector(".alert-facts"))).toBe("L02 · since 09:35 (1 h)");
  expect(text(item)).not.toContain("irrigation_run_missed");
  fireEvent.click(within(item).getByRole("link", { name: /^Details/ }));
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity?alert=502");
  expect(text(alertRow(502).querySelector(".alert-detail .kv"))).toContain("TypeA scheduled watering was missed");
  expect(within(alertRow(502)).getByRole("link", { name: "Hide details" }).getAttribute("aria-expanded")).toBe("true");
});

test("the acknowledged group opens for the alert it holds, then stays as left, and its rows keep what was typed", async () => {
  const api = await open("/activity", { fixture: "alerts-merged" });
  const acknowledged = () => group(/^Acknowledged/)!;
  expect(acknowledged().open).toBe(false);
  // Its own row expands from a link: the group opens, and the note goes out with Resolve alone (it is acknowledged).
  fireEvent.click(within(alertRow(103)).getByRole("link", { name: /^Details/, hidden: true }));
  await advance(0);
  expect(acknowledged().open).toBe(true);
  const item = alertRow(103);
  expect(text(item.querySelector(".field-hint"))).toBe("0/500 characters, sent with Resolve");
  expect(within(item).queryByRole("button", { name: "Acknowledge" })).toBeNull();
  fireEvent.change(within(item).getByLabelText("Note (optional)"), { target: { value: "Waiting on the part" } });
  // Expanding another alert, then hiding it, neither closes the group nor drops the draft.
  fireEvent.click(within(alertRow(101)).getByRole("link", { name: /^Details/ }));
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity?alert=101");
  expect(acknowledged().open).toBe(true);
  fireEvent.click(within(alertRow(101)).getByRole("link", { name: "Hide details" }));
  await advance(0);
  expect(acknowledged().open).toBe(true);
  fireEvent.click(within(alertRow(103)).getByRole("link", { name: /^Details/ }));
  await advance(0);
  expect((within(alertRow(103)).getByLabelText("Note (optional)") as HTMLInputElement).value).toBe("Waiting on the part");
  // A note of spaces is no note.
  fireEvent.change(within(alertRow(103)).getByLabelText("Note (optional)"), { target: { value: "   " } });
  fireEvent.click(within(alertRow(103)).getByRole("button", { name: "Resolve" }));
  await advance(0);
  expect(requests(api, "API-16")).toEqual([expect.objectContaining({ path: "/api/v1/alerts/103/resolve", body: null })]);
});

test("fence-breach: Stop all water shows with and without the engine and sends one API-IRR-07 with no dialog, even with changes off", async () => {
  const cases = [
    [["fence-breach"], "Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own; to keep a line off, disable it."],
    [["fence-breach", "before-engine"], "Stopped every running valve. VPD misting and fog can still start on their own."],
    [["fence-breach", "writes-off"], "Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own; to keep a line off, disable it."],
  ] as const;
  for (const [fixture, result] of cases) {
    const api = await open("/activity", { fixture: [...fixture] });
    const item = alertRow(501);
    expect(text(item.querySelector("h3")), fixture.join()).toBe("A watering valve opened outside its program");
    expect(text(item.querySelector(".act-valve")), fixture.join()).toBe("Valve that opened: L08 Mister • South Wall (Fert.)");
    const stop = within(item).getByRole("button", { name: "Stop all water" });
    expect(stop.className, fixture.join()).toContain("btn-stop");
    expect(stop.hasAttribute("disabled"), fixture.join()).toBe(false);
    // With changes off, Acknowledge is disabled beside it; Stop is not.
    expect(within(item).getByRole("button", { name: /^Acknowledge/ }).hasAttribute("disabled"), fixture.join()).toBe((fixture as readonly string[]).includes("writes-off"));
    fireEvent.click(stop);
    expect(document.querySelector("dialog[open]")).toBeNull();
    await advance(0);
    expect(writes(api), fixture.join()).toEqual([expect.objectContaining({ operationId: "API-IRR-07", path: "/api/v1/irrigation/stop", body: {} })]);
    expect(writes(api)[0].idempotency_key).toMatch(UUID);
    await advance(5000);
    expect(politeToasts(), fixture.join()).toEqual([result]);
    stopped();
  }
});

test("mister-fault: Clear fault shows only with the irrigation firmware, sends API-IRR-13 with the line and reads the latch back; changes off disable it", async () => {
  const api = await open("/activity", { fixture: "mister-fault" });
  fireEvent.click(within(alertRow(301)).getByRole("button", { name: "Clear fault on L04" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-13", path: "/api/v1/irrigation/clear-fault", body: { line_id: "L04" } })]);
  expect(text(alertRow(301))).toContain("Waiting for the controller");
  await advance(5000);
  expect(text(alertRow(301))).toContain("Latch cleared");
  stopped();
  for (const options of [{ fixture: ["mister-fault", "before-engine"] }, { fixture: "mister-fault", fail: { "API-IRR-01": 503 } }]) {
    await open("/activity", options);
    expect(within(alertRow(301)).queryByRole("button", { name: /Clear fault/ }), JSON.stringify(options)).toBeNull();
    expect(text(alertRow(301).querySelector("h3"))).toBe("A mister zone faulted");
    expect(within(alertRow(301)).getByRole("button", { name: /^Acknowledge/ })).toBeTruthy();
    stopped();
  }
  await open("/activity", { fixture: ["mister-fault", "writes-off"] });
  expect(within(alertRow(301)).getByRole("button", { name: "Clear fault on L04" }).hasAttribute("disabled")).toBe(true);
});

test("Recent activity: the five newest log entries, one line each, and Open the log", async () => {
  const api = await open("/activity", { fixture: "activity-mixed" });
  const recent = screen.getByRole("region", { name: "Recent activity" });
  expect(recent.querySelectorAll("li.event")).toHaveLength(5);
  expect(plain(text(recent.querySelector("li.event .act-entry-text")))).toBe("VPD Mister Engage 1.60 → 1.55 kPa · requested 1.53 · manual");
  expect(within(recent).getByRole("link", { name: /^Open the log/ }).getAttribute("href")).toBe("/activity/log");
  expect(requests(api, "API-17").map((row) => row.path)).toEqual(["/api/v1/activity?limit=5"]);
});

test("a viewer reads every alert and the log, expanded too, with no Acknowledge, Resolve, Stop all water, Clear fault or note", async () => {
  await open("/activity?alert=501", { fixture: ["alerts-merged", "fence-breach", "mister-fault", "activity-mixed", "viewer-session"] });
  expect(openList()).toEqual(["alert-501", "alert-101", "alert-301", "alert-102"]);
  expect(alertRow(501).querySelector(".alert-detail .runbook")).not.toBeNull();
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
  expect(screen.queryByRole("button", { name: /Acknowledge|Resolve|Stop all water|Clear fault/ })).toBeNull();
  expect(screen.queryByLabelText("Note (optional)")).toBeNull();
  await toLog();
  expect(logRows()).toHaveLength(6);
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
});

// ---------------------------------------------------------------------------------------------------- the log
test("activity-mixed: one line an entry, who wrote in R4's words, AI for Iris and the agent only, an Iris plan collapsed to its changes, causes first", async () => {
  await open("/activity/log", { fixture: "activity-mixed" });
  expect(status()).toBe(LOG_STATUS);
  expect(logLines()).toEqual([
    "VPD Mister Engage 1.60 → 1.55 kPa · requested 1.53 · manual", "Plan · 3 changes", "Changed line L01 · Daily cap 60 → 45 min · manual cleared · during experiment 7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "Acknowledged “Relay stuck”", "Forecast rule “hot afternoon” · Mister Pulse Gap 45 → 60 s", "Ran L01 · 12 min",
  ]);
  const ai = (node: Element) => node.querySelector(".event-by [data-badge=ai]") !== null;
  const dashboard = logRow("VPD Mister Engage");
  expect([writtenBy(dashboard), ai(dashboard), text(dashboard.querySelector(".event-reason")), text(dashboard.querySelector(".act-entry-outcome"))]).toEqual(["Jason · dashboard", false, "“Vandas drying”", "Confirmed 07:42"]);
  const iris = logRow("Plan · 3 changes");
  expect([writtenBy(iris), ai(iris)]).toEqual(["iris AI", true]);
  fireEvent.click(iris.querySelector("summary")!);
  expect([...iris.querySelectorAll(".plan-changes li")].map((node) => plain(text(node)))).toEqual(["VPD Mister Engage 1.70 kPa from 13:00", "Mister Pulse Gap 55 s from 16:30", "Mister Engage Delay 90 s from 19:00"]);
  const agent = logRow("Changed line L01");
  expect([writtenBy(agent), ai(agent)]).toEqual(["steward · agent AI", true]);
  const forecast = logRow("Forecast rule");
  expect([writtenBy(forecast), ai(forecast)]).toEqual(["forecast-engine · system", false]);
  expect(forecast.querySelector("details")).toBeNull();
});

test("day headers: Today, Yesterday and the weekday and date, each over its own entries, sticky in their day", async () => {
  await open("/activity/log", { fixture: "activity-days" });
  const days = [...logPanel().querySelectorAll("section.act-day")];
  expect(days.map((day) => text(day.querySelector("h3.act-day-head")))).toEqual(["Today", "Yesterday", "Fri 30 Oct"]);
  expect(days.map((day) => day.querySelectorAll("li.event").length)).toEqual([2, 2, 2]);
  expect(screen.getByRole("region", { name: "Yesterday" })).toBeTruthy();
});

test("the facets apply on change and live in the URL: Who and What on API-17's source and action, the Target on Enter or leaving it, the dates", async () => {
  let api = await open("/activity?target=L01", { fixture: "activity-mixed" }, true);
  expect(text(screen.getByTestId("where"))).toBe("/activity/log?target=L01");
  expect(requests(api, "API-17").map((row) => row.path)).toEqual(["/api/v1/activity?target=L01"]);
  expect(logLines()).toEqual(["Changed line L01 · Daily cap 60 → 45 min · manual cleared · during experiment 7c9e6679-7425-40de-944b-e07fc1f90ae7", "Ran L01 · 12 min"]);
  const form = () => screen.getByRole("search", { name: "Activity log filters" });
  const control = (name: string) => form().querySelector(`[name=${name}]`) as HTMLInputElement | HTMLSelectElement;
  expect(control("target").value).toBe("L01");
  // Who as chips (a link each, the current aria-current=true) and as a select for phones, on the same parameter.
  const who = within(form()).getByRole("list", { name: "Who" });
  expect(within(who).getAllByRole("link").map((link) => [text(link), link.getAttribute("aria-current")])).toEqual([["All", "true"], ["People", null], ["Iris", null], ["Agents", null], ["System", null]]);
  expect([...control("source").querySelectorAll("option")].map((option) => option.value)).toEqual(["", "dashboard", "iris", "agent", "system"]);
  fireEvent.click(within(who).getByRole("link", { name: "Agents" }));
  await advance(0);
  const url = text(screen.getByTestId("where"));
  expect(url).toBe("/activity/log?target=L01&source=agent");
  expect(logLines()).toHaveLength(1);
  stopped();
  // A reload of that URL: the same filters, sent the same way.
  api = await open(url, { fixture: "activity-mixed" });
  expect(requests(api, "API-17").map((row) => row.path)).toEqual(["/api/v1/activity?source=agent&target=L01"]);
  expect([control("source").value, control("target").value]).toEqual(["agent", "L01"]);
  // What on the action prefix, from the select as a phone has it.
  fireEvent.change(control("action"), { target: { value: "irrigation." } });
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity/log?target=L01&source=agent&action=irrigation.");
  // The target applies on Enter, with no Apply button; the dates as soon as a whole day is picked.
  expect(within(form()).queryByRole("button", { name: "Apply" })).toBeNull();
  fireEvent.change(control("target"), { target: { value: "L09" } });
  fireEvent.keyDown(control("target"), { key: "Enter" });
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity/log?target=L09&source=agent&action=irrigation.");
  expect(text(logPanel().querySelector(".panel-empty"))).toBe("No activity matches these filtersClear filters");
  fireEvent.change(control("from"), { target: { value: "2026-11-01" } });
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity/log?target=L09&source=agent&action=irrigation.&from=2026-11-01");
  expect(requests(api, "API-17").at(-1)?.path).toBe("/api/v1/activity?source=agent&action=irrigation.&target=L09&from=2026-11-01T06%3A00%3A00.000Z");
  fireEvent.click(within(form()).getByRole("link", { name: "Clear filters" }));
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity/log");
  expect(logRows()).toHaveLength(6);
});

test("an old link with any log filter lands on the log with its query; ?alert= and resolved=24h stay on Alerts (D-UX-03)", async () => {
  for (const [path, lands] of [
    ["/activity?source=iris", "/activity/log?source=iris"], ["/activity?actor=jason&alert=7", "/activity/log?actor=jason&alert=7"],
    ["/activity?action=alert", "/activity/log?action=alert"], ["/activity?alert=7", "/activity?alert=7"], ["/activity?resolved=24h", "/activity?resolved=24h"],
  ] as const) {
    await open(path, { fixture: "alerts-merged" }, true);
    expect(text(screen.getByTestId("where")), path).toBe(lands);
    expect(screen.getByRole("heading", { level: 1 }).textContent, path).toBe(lands.startsWith("/activity/log") ? "Activity log" : "Alerts");
    stopped();
  }
  // An old actor filter shows as a chip that clears it.
  await open("/activity/log?actor=jason", { fixture: "activity-mixed" });
  fireEvent.click(screen.getByRole("link", { name: /^Only Jason/ }));
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity/log");
});

test("CSV of these entries builds the loaded entries in the browser; Load older adds the older page under the newer one", async () => {
  const blobs: Blob[] = [];
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: (blob: Blob) => { blobs.push(blob); return `blob:csv-${blobs.length}`; }, revokeObjectURL: vi.fn() }));
  const api = await open("/activity/log", { fixture: "activity-paged" });
  expect(logRows()).toHaveLength(50);
  const csv = screen.getByRole("link", { name: "CSV of these entries" });
  expect(csv.getAttribute("download")).toBe("verdify-activity-2026-11-02.csv");
  expect(csv.getAttribute("href")).toMatch(/^blob:csv-/);
  const lines = (await blobs.at(-1)!.text()).replace(/^\uFEFF/, "").trim().split("\r\n");
  expect(lines).toHaveLength(51);
  expect(lines[0]).toBe("time (America/Denver),who,source,action,target,what,reason,outcome,confirmed (America/Denver),request id");
  fireEvent.click(screen.getByRole("button", { name: "Load older" }));
  await advance(0);
  expect(requests(api, "API-17").map((row) => row.path)).toEqual(["/api/v1/activity", "/api/v1/activity", "/api/v1/activity?cursor=50"]);
  expect(logRows()).toHaveLength(60);
  expect(logRows().map((node) => node.id).slice(48, 52)).toEqual(["activity-148", "activity-149", "activity-150", "activity-151"]);
  expect(screen.queryByRole("button", { name: "Load older" })).toBeNull();
  // The CSV follows what is loaded.
  expect((await blobs.at(-1)!.text()).trim().split("\r\n")).toHaveLength(61);
  // The 60 s poll reads both pages again from the newest, so a refresh leaves no gap.
  await advance(60_000);
  expect(requests(api, "API-17").map((row) => row.path).slice(3)).toEqual(["/api/v1/activity", "/api/v1/activity?cursor=50"]);
  expect(logRows()).toHaveLength(60);
});
