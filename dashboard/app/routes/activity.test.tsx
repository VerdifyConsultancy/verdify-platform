// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * SCR-06 Activity and alerts (#1133) inside the app shell, against #1122's fixture API in process (fetch is stubbed onto
 * it; GET /__requests gives each request with its body and Idempotency-Key). The clock is the screens' 10:42 America/Denver
 * on 2026-11-02, and the signed-in operator is jason (Jason) unless a fixture says otherwise.
 */
import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
import { afterEach, expect, test } from "vitest";
import { AppShell } from "../components/app-shell";
import { text } from "../components/test-render";
import { UUID, advance, requests, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import Activity from "./activity";

const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
function Where() {
  const location = useLocation();
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>;
}
function mount(path: string) {
  const Stub = createRoutesStub([{
    path: "/", Component: () => <AppShell build={BUILD} authError={null} />,
    children: [{ path: "activity", Component: () => <><Activity /><Where /></> }],
  }]);
  return render(<StrictMode><Stub initialEntries={[path]} /></StrictMode>);
}
/** The fixture API with `options`, then /activity at `path` with every first read answered. */
async function open(path: string, options: FixtureOptions = {}) {
  const api = startFixtureApi(options);
  mount(path);
  await advance(0);
  return api;
}
afterEach(() => { cleanup(); stopFixtureApi(); });

const alertsPanel = () => screen.getByRole("region", { name: /^Open alerts/ });
const logPanel = () => screen.getByRole("region", { name: "Activity log" });
const alertRow = (id: number) => {
  const node = document.getElementById(`alert-${id}`);
  if (!node) throw new Error(`no alert ${id} on the page`);
  return node;
};
const openList = () => [...alertsPanel().querySelectorAll(":scope > ol.alert-list > li")].map((node) => node.id);
const navCount = () => text(within(screen.getByRole("navigation", { name: "Sections" })).getByRole("link", { name: /^Activity/ }));
const logRows = () => [...logPanel().querySelectorAll("li.event")];
const logTitles = () => logRows().map((node) => text(node.querySelector("h3")));
const logRow = (title: string) => {
  const node = logRows().find((item) => text(item.querySelector("h3")).startsWith(title));
  if (!node) throw new Error(`no log row "${title}" in ${JSON.stringify(logTitles())}`);
  return node;
};
/** Who wrote, then the change: the muted line under a row's title. */
const writtenBy = (node: Element) => text(node.querySelector(".event-body > p"));
const politeToasts = () => [...screen.getByRole("status").querySelectorAll(".toast-body")].map((body) => [...body.querySelectorAll("p")].map(text).join(" "));
const stopped = () => { cleanup(); stopFixtureApi(); };

// ---------------------------------------------------------------------------------------------------- alerts
test("with API-14 failing the alert panel reads Alerts unavailable. Retry, never No open alerts, and the log still loads", async () => {
  await open("/activity", { fixture: "activity-mixed", fail: { "API-14": 503 } });
  const panel = alertsPanel();
  expect(text(panel.querySelector(".panel-error"))).toBe("Alerts unavailable.Retry");
  expect(within(panel).getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(text(panel)).not.toContain("No open alerts");
  expect(logRows()).toHaveLength(6);
});

test("alerts-merged: the unacknowledged rows in API order under Open alerts · N with N from API-02, the acknowledged one collapsed with who and when", async () => {
  await open("/activity", { fixture: "alerts-merged" });
  const panel = alertsPanel();
  expect(within(panel).getByRole("heading", { level: 2 }).textContent).toBe("Open alerts · 2");
  expect(navCount()).toBe("Activity 2 open alerts");
  // API order (severity, then age); the merged repeats read as AlertRow draws them.
  expect(openList()).toEqual(["alert-101", "alert-102"]);
  expect(text(alertRow(101).querySelector("h3"))).toBe("Relay stuck");
  expect(text(alertRow(101).querySelector(".alert-facts"))).toBe("fan2 · since 06:02 (4 h) · seen 3 times, last 06:40");
  expect(text(alertRow(102).querySelector(".alert-facts"))).toMatch(/^south · since Oct 30, 03:10 \(3 d\) · seen 12 times, last 10:22$/);
  const group = panel.querySelector("details.alert-group");
  expect(text(group?.querySelector("summary"))).toBe("Acknowledged, still open (1)");
  expect(group?.hasAttribute("open")).toBe(false);
  expect(alertRow(103).closest("details")).toBe(group);
  // The API names people by username; the signed-in person reads by name.
  expect(text(alertRow(103))).toContain("Acknowledged by Jason · 06:47");
  expect(within(alertRow(103)).queryByRole("button", { name: /^Acknowledge/ })).toBeNull();
  expect(screen.queryByRole("region", { name: "Resolved in the last 24 h" })).toBeNull();
});

test("an empty list says No open alerts only while the ingestor and climate are fresh; otherwise in-app alerts may be missing", async () => {
  const cases = [
    ["in-band", "No open alerts"],
    ["ingestor-not-running", "In-app alerts may be missing: ingestor not running since 10:38"],
    ["controller-not-reporting", "In-app alerts may be missing: no data since 10:36"],
  ] as const;
  for (const [fixture, words] of cases) {
    await open("/activity", { fixture });
    expect(text(alertsPanel().querySelector(".alert-empty")), fixture).toBe(words);
    stopped();
  }
});

test("Acknowledge with a note: one API-15 with the note, Acknowledged by Jason, under Acknowledged, still open, the nav count down by one and an alert.ack entry", async () => {
  const api = await open("/activity?alert=101", { fixture: "alerts-merged" });
  const item = alertRow(101);
  const note = within(item).getByLabelText("Note (optional)");
  fireEvent.change(note, { target: { value: "On it" } });
  expect(text(item.querySelector(".field-hint"))).toBe("5/500 characters, sent with Acknowledge or Resolve");
  const acknowledge = within(item).getByRole("button", { name: "Acknowledge" });
  fireEvent.click(acknowledge);
  fireEvent.click(acknowledge);
  await advance(0);
  const sent = requests(api, "API-15");
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({ method: "POST", path: "/api/v1/alerts/101/ack", body: { note: "On it" } });
  expect(sent[0].idempotency_key).toMatch(UUID);
  await advance(0);
  expect(politeToasts()).toEqual(["“Relay stuck” acknowledged by Jason · 10:42"]);
  // R: the row reads who and when at once, under the acknowledged group, which opens to show it.
  const moved = alertRow(101);
  expect(text(moved)).toContain("Acknowledged by Jason · 10:42");
  expect(moved.closest("details")?.hasAttribute("open")).toBe(true);
  expect(text(alertsPanel().querySelector("details.alert-group > summary"))).toBe("Acknowledged, still open (2)");
  expect(openList()).toEqual(["alert-102"]);
  // The one count: the nav and the head both read API-02's, refreshed at once.
  expect(navCount()).toBe("Activity 1 open alerts");
  expect(within(alertsPanel()).getByRole("heading", { level: 2 }).textContent).toBe("Open alerts · 1");
  const entry = logRow("Acknowledged “Relay stuck”");
  expect(writtenBy(entry)).toBe("Jason · dashboard");
  expect(text(entry.querySelector(".event-reason"))).toBe("“On it”");
  // The note was sent: the field is empty again.
  expect((within(alertRow(101)).getByLabelText("Note (optional)") as HTMLInputElement).value).toBe("");
});

test("Resolve with a note sends one API-16 and the alert leaves the open list; resolved=24h then lists it as resolved", async () => {
  const api = await open("/activity?alert=102", { fixture: "alerts-merged" });
  fireEvent.change(within(alertRow(102)).getByLabelText("Note (optional)"), { target: { value: "Probe reseated" } });
  fireEvent.click(within(alertRow(102)).getByRole("button", { name: "Resolve" }));
  await advance(0);
  await advance(0);
  expect(requests(api, "API-16")).toEqual([expect.objectContaining({ method: "POST", path: "/api/v1/alerts/102/resolve", body: { resolution: "Probe reseated" } })]);
  expect(requests(api, "API-16")[0].idempotency_key).toMatch(UUID);
  // Resolve is never refused and asks nothing: no dialog.
  expect(document.querySelector("dialog[open]")).toBeNull();
  expect(openList()).toEqual(["alert-101"]);
  expect(navCount()).toBe("Activity 1 open alerts");
  // The link still names it, so it shows as resolved under its own head, read from status=recent.
  const linked = screen.getByRole("region", { name: "Linked alert" });
  expect(alertRow(102).closest("section")).toBe(linked);
  expect(text(alertRow(102))).toContain("Resolved by Jason · 10:42");
  expect(text(alertRow(102))).toContain("“Probe reseated”");
  expect(within(alertRow(102)).queryByRole("button", { name: /Acknowledge|Resolve/ })).toBeNull();
  expect(requests(api, "API-14").map((row) => row.path)).toContain("/api/v1/alerts?status=recent&limit=200");
  expect(text(logRow("Resolved “Soil probe offline”").querySelector(".event-reason"))).toBe("“Probe reseated”");
  fireEvent.click(screen.getByRole("link", { name: /Show resolved in the last 24 h/ }));
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity?alert=102&resolved=24h");
  expect(alertRow(102).closest("section")).toBe(screen.getByRole("region", { name: "Resolved in the last 24 h" }));
  expect(screen.queryByRole("region", { name: "Linked alert" })).toBeNull();
});

test("Acknowledge on a collapsed row sends no note, even one typed while the row was open", async () => {
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
  const lines = [...item.querySelectorAll(".event-body > p")].map(text);
  expect(lines).toEqual(expect.arrayContaining([
    "A leak, or the hose, sink or hydro in use.", "3 gal in the last 10 min · Updated 20 s ago", "If no one is using water, close the spigot.",
  ]));
  // The title says it once: the alert's message is the same words, shown in the expanded fields.
  expect(lines).not.toContain("Water flowing with every Verdify valve closed");
  expect(within(item).queryByRole("button", { name: /Stop/ })).toBeNull();
  expect(text(item.querySelector(".runbook"))).toBe("1. Check the hose, sink and hydro. 2. Walk the wall line and the manifold for a break. 3. If no one is using water, close the spigot.");
  // First seen 10:38 (17:38Z): the window 15:38Z to 19:38Z.
  expect(within(item).getByRole("link", { name: "Open in Graphs" }).getAttribute("href")).toBe("/graphs?from=2026-11-02T15:38:00Z&to=2026-11-02T19:38:00Z");
  // Every field, each code through the words table: the type reads as the title does, never raw.
  expect(text(item.querySelector(".alert-detail .kv"))).toContain("TypeWater flowing with every Verdify valve closedCategorywaterSeverityCritical");
  expect(text(item)).not.toContain("leak_detected");
  stopped();
  // Once the meter has read under 0.1 gal/min twice: Flow stopped at HH:MM, never an all clear.
  await open("/activity", { fixture: "flow-stopped" });
  const stoppedLines = [...alertRow(201).querySelectorAll(".event-body > p")].map(text);
  expect(stoppedLines).toEqual(expect.arrayContaining(["Flow stopped at 10:40", "If no one is using water, close the spigot."]));
  // The ingestor's message says the same ("Flow stopped at 10:40"): the row says it once.
  expect(stoppedLines.filter((line) => line === "Flow stopped at 10:40")).toHaveLength(1);
  expect(text(alertRow(201))).not.toMatch(/all clear|0 gal/i);
});

test("a runbook renders as text with its line breaks, never as HTML", async () => {
  await open("/activity?alert=501", { fixture: "fence-breach" });
  const runbook = alertRow(501).querySelector(".runbook");
  expect(runbook?.textContent).toBe("1. Stop all water.\n2. Check that the south fert mister (L08) relay reads OFF.\n3. If water still flows, close the spigot.");
  expect(runbook?.children).toHaveLength(0);
  expect(text(alertRow(501))).not.toContain("irrigation_fence_breach");
});

test("a copied /activity?alert=<id> link still opens the alert after a new repeat merges; an id no longer open is looked up in the last 24 h", async () => {
  await open("/activity?alert=201", { fixture: ["flow-valves-closed", "alert-repeat"] });
  expect(alertRow(201).querySelector(".alert-detail")).not.toBeNull();
  expect(text(alertRow(201).querySelector(".alert-facts"))).toContain("seen 2 times, last 10:41");
  expect(screen.queryByRole("region", { name: "Linked alert" })).toBeNull();
  stopped();
  const api = await open("/activity?alert=999", { fixture: "alerts-merged" });
  // The open rows answer first; only then is status=recent read, and TanStack renders its answer a moment later.
  expect(text(screen.getByRole("region", { name: "Linked alert" }))).toBe("Linked alertLoading alert 999");
  await advance(20);
  expect(text(screen.getByRole("region", { name: "Linked alert" }))).toBe("Linked alertAlert 999 is not open or resolved in the last 24 h");
  expect(requests(api, "API-14").map((row) => row.path)).toEqual(["/api/v1/alerts?status=open&limit=200", "/api/v1/alerts?status=recent&limit=200"]);
});

test("the run-missed alert reads its plain title with L02, never the raw code, expanded too", async () => {
  await open("/activity", { fixture: "run-missed" });
  const item = alertRow(502);
  expect(text(item.querySelector("h3"))).toBe("A scheduled watering was missed");
  expect(text(item.querySelector(".alert-facts"))).toBe("L02 · since 09:35 (1 h)");
  expect(text(item)).not.toContain("irrigation_run_missed");
  fireEvent.click(within(item).getByRole("link", { name: /^Details/ }));
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity?alert=502");
  expect(text(alertRow(502).querySelector(".alert-detail .kv"))).toContain("TypeA scheduled watering was missed");
  expect(text(alertRow(502))).not.toContain("irrigation_run_missed");
});

test("the acknowledged group opens for the alert it holds, then stays as left, and its rows keep what was typed", async () => {
  const api = await open("/activity", { fixture: "alerts-merged" });
  const group = () => alertsPanel().querySelector("details.alert-group") as HTMLDetailsElement;
  expect(group().open).toBe(false);
  // Its own row expands from a link: the group opens, and the note goes out with Resolve alone (it is acknowledged).
  fireEvent.click(within(alertRow(103)).getByRole("link", { name: /^Details/, hidden: true }));
  await advance(0);
  expect(group().open).toBe(true);
  const item = alertRow(103);
  expect(text(item.querySelector(".field-hint"))).toBe("0/500 characters, sent with Resolve");
  expect(within(item).queryByRole("button", { name: "Acknowledge" })).toBeNull();
  fireEvent.change(within(item).getByLabelText("Note (optional)"), { target: { value: "Waiting on the part" } });
  // Expanding another alert, then hiding it, neither closes the group nor drops the draft.
  fireEvent.click(within(alertRow(101)).getByRole("link", { name: /^Details/ }));
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity?alert=101");
  expect(group().open).toBe(true);
  fireEvent.click(within(alertRow(101)).getByRole("link", { name: "Hide details" }));
  await advance(0);
  expect(group().open).toBe(true);
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
    expect([...item.querySelectorAll(".event-body > p")].map(text), fixture.join()).toContain("Valve that opened: L08 Mister • South Wall (Fert.)");
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
    // The relays read OFF at the first readback, 5 s on: the Stop result.
    await advance(5000);
    expect(politeToasts(), fixture.join()).toEqual([result]);
    stopped();
  }
});

test("mister-fault: Clear fault shows only with the irrigation firmware, sends API-IRR-13 with the line and reads the latch back; changes off disable it", async () => {
  const api = await open("/activity", { fixture: "mister-fault" });
  const clear = within(alertRow(301)).getByRole("button", { name: "Clear fault on L04" });
  fireEvent.click(clear);
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-13", path: "/api/v1/irrigation/clear-fault", body: { line_id: "L04" } })]);
  expect(text(alertRow(301))).toContain("Waiting for the controller");
  await advance(5000);
  expect(text(alertRow(301))).toContain("Latch cleared");
  stopped();
  for (const options of [{ fixture: ["mister-fault", "before-engine"] }, { fixture: "mister-fault", fail: { "API-IRR-01": 503 } }]) {
    await open("/activity", options);
    expect(within(alertRow(301)).queryByRole("button", { name: /Clear fault/ }), JSON.stringify(options)).toBeNull();
    // The rest of the row is there: its title and Acknowledge.
    expect(text(alertRow(301).querySelector("h3"))).toBe("A mister zone faulted");
    expect(within(alertRow(301)).getByRole("button", { name: /^Acknowledge/ })).toBeTruthy();
    stopped();
  }
  await open("/activity", { fixture: ["mister-fault", "writes-off"] });
  expect(within(alertRow(301)).getByRole("button", { name: "Clear fault on L04" }).hasAttribute("disabled")).toBe(true);
});

test("a viewer reads every alert and the log, expanded too, with no Acknowledge, Resolve, Stop all water, Clear fault or note", async () => {
  await open("/activity?alert=501", { fixture: ["alerts-merged", "fence-breach", "mister-fault", "activity-mixed", "viewer-session"] });
  expect(openList()).toEqual(["alert-501", "alert-101", "alert-301", "alert-102"]);
  expect(alertRow(501).querySelector(".alert-detail .runbook")).not.toBeNull();
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
  expect(screen.queryByRole("button", { name: /Acknowledge|Resolve|Stop all water|Clear fault/ })).toBeNull();
  expect(screen.queryByLabelText("Note (optional)")).toBeNull();
  expect(logRows()).toHaveLength(6);
});

// ---------------------------------------------------------------------------------------------------- the log
test("activity-mixed: who wrote in text, AI for Iris and the agent only, each change and its reason, and both plan.write rows from detail.changes", async () => {
  await open("/activity", { fixture: "activity-mixed" });
  expect(logTitles()).toEqual([
    "Set VPD Mister Engage Confirmed 07:42", "Plan iris-20261102-0702", "Changed line L01 Confirmed 06:49", "Acknowledged “Relay stuck”",
    "Plan preemptive-20261102-0630", "Ran L01",
  ]);
  const ai = (node: Element) => node.querySelector(".event-by [data-badge=ai]") !== null;
  const dashboard = logRow("Set VPD Mister Engage");
  expect(writtenBy(dashboard)).toBe("Jason · dashboard · 1.60 → 1.55 kPa · requested 1.53 · manual");
  expect(ai(dashboard)).toBe(false);
  expect(text(dashboard.querySelector(".event-reason"))).toBe("“Vandas drying”");
  const iris = logRow("Plan iris-20261102-0702");
  expect(writtenBy(iris)).toBe("iris AI · trigger 5f0c6a1e-2b7d-4c9a-8e3f-1d2c3b4a5e6f");
  expect(ai(iris)).toBe(true);
  expect(text(iris.querySelector(".event-reason"))).toBe("“Hot, dry afternoon: engage misting earlier from 13:00 and widen the pulse gap at dusk.”");
  expect(text(iris.querySelector("summary"))).toBe("3 changes");
  expect([...iris.querySelectorAll(".plan-changes li")].map(text)).toEqual([
    "VPD Mister Engage 1.70 kPa from 13:00", "Mister Pulse Gap 55 s from 16:30", "Mister Engage Delay 90 s from 19:00",
  ]);
  const agent = logRow("Changed line L01");
  expect(writtenBy(agent)).toBe("steward · agent AI · Daily cap 60 → 45 min · manual cleared · during experiment 7c9e6679-7425-40de-944b-e07fc1f90ae7");
  expect(ai(agent)).toBe(true);
  expect(text(agent.querySelector(".event-reason"))).toBe("“forecast 92 °F”");
  // The forecast rule is the system, never AI: its one change inline, before → after, with its rule.
  const forecast = logRow("Plan preemptive-20261102-0630");
  expect(writtenBy(forecast)).toBe("forecast-engine · system · Mister Pulse Gap 45 → 60 s · rule “hot afternoon”");
  expect(ai(forecast)).toBe(false);
  expect(text(forecast.querySelector(".event-reason"))).toBe("“Forecast: hot afternoon — temp_f > 88 (actual 92 at 14:00)”");
  expect(forecast.querySelector("details")).toBeNull();
  expect(text(logRow("Acknowledged “Relay stuck”").querySelector(".event-reason"))).toBe("“breaker reset”");
  // A row from another day carries its date.
  expect(text(logRow("Ran L01").querySelector(".event-when"))).toBe("Oct 30, 08:31");
  expect(writtenBy(logRow("Ran L01"))).toBe("Jason · dashboard · 12 min");
});

test("activity-mixed: /activity?target=L01 lists only L01 entries, and the filters live in the URL, so a reload keeps them", async () => {
  let api = await open("/activity?target=L01", { fixture: "activity-mixed" });
  expect(requests(api, "API-17").map((row) => row.path)).toEqual(["/api/v1/activity?target=L01"]);
  expect(logTitles()).toEqual(["Changed line L01 Confirmed 06:49", "Ran L01"]);
  const form = () => screen.getByRole("search", { name: "Activity log filters" });
  const control = (name: string) => form().querySelector(`[name=${name}]`) as HTMLInputElement | HTMLSelectElement;
  expect(control("target").value).toBe("L01");
  // Source lists dashboard, iris, agent, system and each agent seen.
  expect([...control("source").querySelectorAll("option")].map((option) => option.value)).toEqual(["", "dashboard", "iris", "agent", "system", "agent:steward"]);
  fireEvent.change(control("source"), { target: { value: "agent:steward" } });
  await advance(0);
  const url = text(screen.getByTestId("where"));
  expect(url).toBe("/activity?target=L01&source=agent%3Asteward");
  expect(logTitles()).toEqual(["Changed line L01 Confirmed 06:49"]);
  stopped();
  // A reload of that URL: the same filters, sent the same way.
  api = await open(url, { fixture: "activity-mixed" });
  expect(requests(api, "API-17").map((row) => row.path)).toEqual(["/api/v1/activity?source=agent%3Asteward&target=L01"]);
  expect([control("source").value, control("target").value]).toEqual(["agent:steward", "L01"]);
  expect(logTitles()).toEqual(["Changed line L01 Confirmed 06:49"]);
  // The target applies on Enter; Clear filters drops every filter and nothing else.
  fireEvent.change(control("target"), { target: { value: "L09" } });
  fireEvent.submit(form());
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity?target=L09&source=agent%3Asteward");
  expect(text(logPanel().querySelector(".panel-empty"))).toBe("No activity matches these filtersClear filters");
  fireEvent.click(within(logPanel()).getAllByRole("link", { name: "Clear filters" })[0]);
  await advance(0);
  expect(text(screen.getByTestId("where"))).toBe("/activity");
  expect(logRows()).toHaveLength(6);
});

test("Load older passes next_cursor as cursor and adds the older page under the newer one", async () => {
  const api = await open("/activity", { fixture: "activity-paged" });
  expect(logRows()).toHaveLength(50);
  fireEvent.click(screen.getByRole("button", { name: "Load older" }));
  await advance(0);
  expect(requests(api, "API-17").map((row) => row.path)).toEqual(["/api/v1/activity", "/api/v1/activity", "/api/v1/activity?cursor=50"]);
  expect(logRows()).toHaveLength(60);
  expect(logRows().map((node) => node.id).slice(48, 52)).toEqual(["activity-148", "activity-149", "activity-150", "activity-151"]);
  expect(screen.queryByRole("button", { name: "Load older" })).toBeNull();
  // The 60 s poll reads both pages again from the newest, so a refresh leaves no gap.
  await advance(60_000);
  expect(requests(api, "API-17").map((row) => row.path).slice(3)).toEqual(["/api/v1/activity", "/api/v1/activity?cursor=50"]);
  expect(logRows()).toHaveLength(60);
});
