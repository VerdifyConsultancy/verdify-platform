// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * SCR-03 Plan (#1131, redesign §5.11) inside the shell, against #1122's fixture API in process: the card with its validity
 * bar, planner health with the 36 h marks and the 14-day strip, and the lanes from API-07 under the superseded-lane,
 * no-plan, manual-lane, missed-sunset and forecast-rule fixtures, each lane rule, the outcome words and the ▲ rule, a lane
 * label opening its setting drawer in place, the 24/72 h chip in the URL, the states, the polls and that nothing is
 * written but through the drawer. The clock is the screens' 10:42 America/Denver on 2026-11-02 (MST); uPlot is the
 * recording mock.
 */
import { renderToString } from "react-dom/server";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
import { afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens, type Stroke } from "../charts/fixtures/uplot-mock";
import { FORECAST_RULE, PLANNED_AI } from "../charts/marks";
import { loadUPlot } from "../charts/uplot-chart";
import { AppShell } from "../components/app-shell";
import { ServerClockProvider } from "../components/freshness";
import { PlanCard, planDetail, timeLeft } from "../components/plan-card";
import { NEWEST_SHOWN, PlannerHealth, healthRows, isLate, markPosition, needsAttention } from "../components/planner-health";
import { NBSP, denver, renderUi, text } from "../components/test-render";
import type { Plan as PlanBody, PlannerTrigger } from "../lib/operator-api";
import { NOW, advance, requests, sentAt, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import { WORDS } from "../lib/words";
import Plan, { PLAN_POLL_MS, manualCount, planHours } from "./plan";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
const epoch = (instant: string) => Date.parse(instant) / 1000;
const START = NOW / 1000;
// The mock plot is 1000 px wide over the 24 h window from 10:42 and starts at y = 0; rows are 44 px (20 px header, 24 px strip).
const x = (clock: string) => Math.round(((epoch(denver(clock)) - START) / (24 * 3600)) * 1000);
const lineY = (row: number) => row * 44 + 20 + 16;
const labelY = (row: number) => row * 44 + 20 + 7;
const onRow = (stroke: Stroke, row: number) => stroke.path.every(([, y]) => y === lineY(row));
const strokes = (plot: MockPlot, row: number) => plot.ctx.strokes.filter((stroke) => onRow(stroke, row))
  .map((stroke) => [stroke.stroke, stroke.dash.join(","), ...stroke.path.map(([px]) => Math.round(px))]);
const labels = (plot: MockPlot, row: number) => plot.ctx.texts.filter((label) => label.y === labelY(row)).map((label) => [label.text.replaceAll(NBSP, " "), label.color]);
const DEVICE = TOKENS["--c-setpoint"], INK = TOKENS["--ink"], MUTED = TOKENS["--c-bound"];

/** Settings (#1132) stands in as its URL, so "Find a setting"'s target is visible; Plan's own URL shows beside it. */
function SettingsProbe() {
  const { pathname, search } = useLocation();
  return <p data-testid="settings">{`${pathname}${search}`}</p>;
}
function Here() {
  const { pathname, search } = useLocation();
  return <output data-testid="here">{`${pathname}${search}`}</output>;
}
/** Plan at `path` inside the shell, the fixture API answering; returns the lanes' plot once it is painted. */
async function open(path = "/plan", fixture: FixtureOptions["fixture"] = "in-band", options: Omit<FixtureOptions, "fixture"> = {}) {
  const api = startFixtureApi({ fixture, ...options });
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ narrow: false, fine: true }));
  const Stub = createRoutesStub([{
    path: "/", Component: () => <AppShell build={BUILD} authError={null} />,
    children: [{ path: "plan", Component: () => <><Plan /><Here /></> }, { path: "settings", Component: SettingsProbe }],
  }]);
  const view = render(<Stub initialEntries={[path]} />);
  await settle();
  return { api, view, plot: paint() };
}
/** Lets the reads answer and the lanes mount (uPlot is already loaded, so it resolves in microtasks). */
async function settle() { await advance(0); await advance(0); }
/** The newest plot, ready and painted. */
function paint(): MockPlot {
  const plot = created[created.length - 1];
  if (plot) { plot.fire("ready"); plot.draw(); }
  return plot;
}
const region = (name: string) => screen.getByRole("region", { name });
const status = (param: string) => document.querySelector(`[data-param=${param}] .plan-status`)?.textContent ?? null;
const tooltip = (plot: MockPlot) => [...plot.over.querySelectorAll(".chart-tooltip > div")].map((line) => text(line));

beforeAll(async () => { await loadUPlot(); });
beforeEach(() => { MockPlot.reset(); FakeResizeObserver.instances = []; setTokens(); });
afterEach(() => { cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style"); });

// ---------------------------------------------------------------------------------------------------- the card
test("a plan in force: trigger and id, the validity bar with now and the time left, the hypothesis marked AI, and what applies after", async () => {
  await open("/plan", "superseded-lane");
  // The status line states the plan once, with the time left and AI.
  expect(text(document.querySelector(".page-status"))).toBe("SUNRISE plan · written 07:02 · valid to 17:30 (6 h 48 min left) · AI");
  expect(within(document.querySelector(".page-header") as HTMLElement).getByRole("searchbox", { name: "Find a setting" })).toBeTruthy();
  const card = region("Plan in force");
  expect(text(card.querySelector(".plan-facts"))).toBe("SUNRISE · iris-20261102-0702");
  expect(text(card.querySelector(".ctl-validity-words"))).toBe("written 07:02 → valid to 17:30 · 6 h 48 min left");
  expect([...card.querySelectorAll(".ctl-validity-words time")].map((time) => time.getAttribute("title"))).toEqual(["11/02/2026, 07:02:00 GMT-07:00", "11/02/2026, 17:30:00 GMT-07:00"]);
  // 07:02 to 17:30 is 628 min, of which 220 have passed at 10:42: the bar is filled to 35% with now marked there.
  const bar = card.querySelector("svg.ctl-validity")!;
  expect(bar.getAttribute("aria-hidden")).toBe("true");
  expect(bar.querySelector(".ctl-validity-fill")!.getAttribute("width")).toBe("35.03%");
  expect(bar.querySelector(".ctl-validity-now")!.getAttribute("x1")).toBe("35.03%");
  const hypothesis = card.querySelector(".plan-hypothesis")!;
  expect(text(hypothesis)).toBe("“Hot, dry afternoon: engage misting earlier from 13:00 and widen the pulse gap at dusk.” AI");
  expect(hypothesis.querySelector("[data-badge=ai]")!.className).toBe("ctl-mark");
  expect(text(card.querySelector(".plan-after"))).toBe("After 17:30: band and defaults until the next plan");
  // The disclosure holds the conditions, the expected outcome and the per-setting rationale, as text.
  const details = card.querySelector("details")!;
  expect(text(details.querySelector("summary"))).toBe("Conditions, stress windows and rationale");
  expect([...details.querySelectorAll(".kv dt, .kv dd")].map((cell) => text(cell))).toEqual([
    "Conditions", "Clear, 58 °F high outside, RH falling to 20%.", "Expected outcome", "VPD stays under 0.80 kPa through the afternoon peak.",
  ]);
  expect([...details.querySelectorAll("h3")].map((heading) => text(heading))).toEqual(["Stress windows", "Per-setting rationale"]);
  expect([...details.querySelectorAll("li")].map((item) => text(item))).toEqual([
    "VPD Mister Engage mister_engage_kpa: Engage earlier before the 13:00 dry spell.", "Mister Pulse Gap mister_pulse_gap_s: Longer evaporation dwell at dusk.",
  ]);
  expect(text(details).includes("None recorded")).toBe(true);
});

test("no plan in force reads what applies and since when, and the lanes show device values only", async () => {
  const { plot } = await open("/plan", "no-plan");
  const card = region("Plan in force");
  expect(text(card.querySelector(".plan-none"))).toBe("No plan in force: band and defaults apply since 09:30");
  expect(text(document.querySelector(".page-status"))).toBe("No plan in force · band and defaults apply since 09:30");
  expect(card.querySelector("[data-badge=ai], details, .plan-facts, .ctl-validity")).toBeNull();
  // Device values only: each lane holds its value solid to the horizon, with no waypoint, header words or plan end.
  expect([0, 1, 2].map((row) => strokes(plot, row))).toEqual([[[DEVICE, "", 0, 1000]], [[DEVICE, "", 0, 1000]], [[DEVICE, "", 0, 1000]]]);
  expect(document.querySelectorAll(".plan-status")).toHaveLength(0);
  expect(plot.ctx.texts.some((label) => label.text.startsWith("Plan ends"))).toBe(false);
  expect([...document.querySelectorAll(".plan-device")].map((cell) => text(cell))).toEqual(["1.60 kPa", "45.0 s", "60.0 s"]);
  // Planner health still renders: the ledger is not the plan.
  expect(region("Planner health").querySelectorAll(".trigger-row").length).toBeGreaterThan(0);
});

test("a no-plan time or a valid-until on another day carries its date; the time left is hours and minutes, then minutes", () => {
  const empty: PlanBody = { plan: null, no_plan_since: null, lanes: [], manual_count: 0, health: [] };
  const yesterday = new Date(Date.parse(denver("22:58")) - 86_400_000).toISOString();
  let { container } = renderUi(<PlanCard data={{ ...empty, no_plan_since: yesterday }} state="ready" now={NOW} />);
  expect(text(container.querySelector(".plan-none"))).toBe("No plan in force: band and defaults apply since Nov 1, 22:58");
  cleanup();
  ({ container } = renderUi(<PlanCard data={empty} state="ready" now={NOW} />));
  expect(text(container.querySelector(".plan-none"))).toBe("No plan in force: band and defaults apply");
  cleanup();
  const record = { plan_id: "iris-20261102-1240", trigger: "FORECAST_DEVIATION", written_at: denver("12:41"), valid_until: "2026-11-05T19:00:00Z", hypothesis: null, detail: {} };
  const ahead = renderUi(<PlanCard data={{ ...empty, plan: record }} state="ready" now={Date.parse(denver("12:42"))} />).container;
  expect(text(ahead.querySelector(".plan-facts"))).toBe("FORECAST_DEVIATION · iris-20261102-1240");
  expect(text(ahead.querySelector(".ctl-validity-words"))).toBe("written 12:41 → valid to Nov 5, 12:00 · 71 h 18 min left");
  expect(text(ahead.querySelector(".plan-hypothesis"))).toBe("— no hypothesis recorded");
  expect(ahead.querySelector("[data-badge=ai]")).toBeNull();
  expect(text(ahead.querySelector(".plan-after"))).toBe("After Nov 5, 12:00: band and defaults until the next plan");
  expect(timeLeft(denver("17:30"), NOW)).toBe(`6${NBSP}h${NBSP}48${NBSP}min left`);
  expect(timeLeft(denver("12:42"), NOW)).toBe(`2${NBSP}h left`);
  expect(timeLeft(denver("11:20"), NOW)).toBe(`38${NBSP}min left`);
  expect(timeLeft(denver("10:42", 30), NOW)).toBe(`1${NBSP}min left`);
  expect(timeLeft(denver("10:00"), NOW)).toBe("ended");
  expect(timeLeft("not a time", NOW)).toBeNull();
});

test("the Details render a stored plan's stress windows, per-setting rationale and waypoint reasons as text, in words", () => {
  const detail = {
    hypothesis_structured: {
      conditions: { notes: "Cold overcast Monday", outdoor_temp_peak_f: 55 },
      stress_windows: [
        { kind: "cold", start: "2026-11-02T18:00:00-07:00", end: "2026-11-03T08:00:00-07:00", severity: "high", mitigation: "Preserve heat." },
        { kind: "vpd_high", start: "2026-11-04T10:00:00-07:00", end: "2026-11-04T16:00:00-07:00", severity: "medium", mitigation: "Wet assist on live edge excess only." },
        { kind: "frost", severity: "extreme" }, { severity: "low" }, "not a window",
      ],
      rationale: [{ parameter: "mister_engage_kpa", old_value: 1.2, new_value: 1.04, expected_effect: "Avoid forecast-only misting.", forecast_anchor: "Indoor VPD 0.68 is below the high edge." }, { new_value: 3 }],
    },
    climate_intents: [{ ts: "2026-11-02T12:40:00-07:00", reason: "Rain miss only; indoor state shows no regime shift.", climate_intent: { wet_cutoff_hour: 19 } }, { ts: "2026-11-02T13:00:00-07:00" }],
  };
  expect(planDetail(detail)).toEqual({
    stress: [
      { kind: "cold", severity: "high", start: "2026-11-02T18:00:00-07:00", end: "2026-11-03T08:00:00-07:00", mitigation: "Preserve heat." },
      { kind: "vpd_high", severity: "medium", start: "2026-11-04T10:00:00-07:00", end: "2026-11-04T16:00:00-07:00", mitigation: "Wet assist on live edge excess only." },
      { kind: "frost", severity: "extreme", start: null, end: null, mitigation: null },
    ],
    rationale: [{ param: "mister_engage_kpa", from: 1.2, to: 1.04, why: "Avoid forecast-only misting.", evidence: "Indoor VPD 0.68 is below the high edge." }],
    reasons: [{ at: "2026-11-02T12:40:00-07:00", reason: "Rain miss only; indoor state shows no regime shift." }],
  });
  expect(planDetail(undefined)).toEqual({ stress: [], rationale: [], reasons: [] });
  expect(planDetail({ hypothesis_structured: null, climate_intents: { not: "a list" } })).toEqual({ stress: [], rationale: [], reasons: [] });
  const data: PlanBody = {
    plan: { plan_id: "iris-20261102-0634", trigger: "SUNRISE", written_at: denver("06:39"), valid_until: denver("20:00"), hypothesis: "Hold heat.", conditions_summary: null, expected_outcome: null, detail },
    no_plan_since: null, lanes: [{ param: "mister_engage_kpa", label: "VPD Mister Engage", unit: "kPa", device: { value: 1.04, observed_at: denver("10:40") }, waypoints: [], desired: null, applied_from: "plan" }],
    manual_count: 0, health: [],
  };
  const { container } = renderUi(<PlanCard data={data} state="ready" now={NOW} />);
  const details = container.querySelector("details")!;
  expect([...details.querySelectorAll(".kv dd")].map((cell) => text(cell))).toEqual(["— not recorded", "— not recorded"]);
  expect([...details.querySelectorAll("h3")].map((heading) => text(heading))).toEqual(["Stress windows", "Per-setting rationale", "Waypoint reasons"]);
  expect([...details.querySelectorAll("li")].map((item) => text(item))).toEqual([
    "Cold · high severity · 18:00–Nov 3, 08:00: Preserve heat.",
    "VPD high · medium severity · Nov 4, 10:00–Nov 4, 16:00: Wet assist on live edge excess only.",
    // A kind or severity the table does not know reads raw.
    "frost · extreme",
    "VPD Mister Engage mister_engage_kpa 1.20 kPa → 1.04 kPa: Avoid forecast-only misting. Evidence: Indoor VPD 0.68 is below the high edge.",
    "12:40 · Rain miss only; indoor state shows no regime shift.",
  ]);
  // Times are <time> elements with the full local time in their title; nothing is injected as HTML.
  expect(details.querySelectorAll("time")).toHaveLength(5);
  expect(details.innerHTML).not.toMatch(/<script|wet_cutoff_hour/);
});

// ---------------------------------------------------------------------------------------------------- the lanes
test("the superseded lane reads YOUR VALUE 1.55 · jason 08:00 · holds until Iris's next plan, and the 13:00 waypoint is muted and labelled SUPERSEDED", async () => {
  // Plan written 07:02 with a 13:00 waypoint of 1.70; a non-manual desired row of 1.55 at 08:00 holds it off.
  const { plot } = await open("/plan", "superseded-lane");
  expect(status("mister_engage_kpa")).toBe("YOUR VALUE 1.55 · jason 08:00 · holds until Iris's next plan");
  // The device value (1.55, as read back) holds solid to 13:00; Iris's 1.70 from 13:00 is dashed in the muted colour, never as the plan ahead.
  expect(strokes(plot, 0)).toEqual([[DEVICE, "", 0, x("13:00")], [MUTED, "8,4", x("13:00"), x("17:30")]]);
  expect(labels(plot, 0)).toEqual([["1.70 kPa SUPERSEDED", MUTED]]);
  plot.hover(epoch(denver("14:00")), null, 0 * 44 + 30);
  expect(tooltip(plot)).toEqual([
    "11/02/2026 14:00 GMT-07:00", "VPD Mister Engage", "SUPERSEDED 1.70 kPa · Iris plan · your value 1.55 kPa (jason) holds until Iris's next plan",
    "YOUR VALUE 1.55 · jason 08:00 · holds until Iris's next plan",
  ]);
  // The other lanes keep Iris's plan: PLANNED · AI.
  expect(status("mister_pulse_gap_s")).toBe(PLANNED_AI);
  // The plan's end inside the window, with what takes over.
  expect(plot.ctx.texts.find((label) => label.text.startsWith("Plan ends"))?.text).toBe("Plan ends 17:30 · band/default");
});

test("a manual lane reads MANUAL · Iris skips, draws no waypoints, the head counts it, and its label opens its setting drawer here", async () => {
  const { plot, api } = await open("/plan", "manual-lane");
  expect(status("mister_pulse_on_s")).toBe("MANUAL · Iris skips · jason since 10/30");
  const row = document.querySelector("[data-param=mister_pulse_on_s]")!;
  expect(row.classList.contains("plan-row-muted")).toBe(true);
  // Its held value is solid grey across the window; no dashed waypoint.
  expect(strokes(plot, 3)).toEqual([[MUTED, "", 0, 1000]]);
  expect(labels(plot, 3)).toEqual([]);
  const lanes = region("Planned settings");
  expect(text(lanes.querySelector(".plan-manual"))).toBe("1 setting MANUAL · Iris skips it");
  // Every lane's label is a link to its setting drawer on this page (redesign §5.11), not a write control; Open is gone.
  const opens = [...lanes.querySelectorAll<HTMLAnchorElement>(".plan-row a.ctl-lane-link")];
  expect(opens.map((link) => [text(link), link.getAttribute("href")])).toEqual([
    ["VPD Mister Engage", "/plan?edit=mister_engage_kpa"], ["Mister Pulse Gap", "/plan?edit=mister_pulse_gap_s"],
    ["Mister Engage Delay", "/plan?edit=mister_engage_delay_s"], ["Mister Pulse ON", "/plan?edit=mister_pulse_on_s"],
  ]);
  expect(opens.every((link) => !link.hasAttribute("data-write-control"))).toBe(true);
  expect(within(lanes).queryAllByRole("link", { name: /^Open/ })).toEqual([]);
  // API-09 is read only once a drawer is asked for.
  expect(requests(api, "API-09")).toEqual([]);
  fireEvent.click(within(row as HTMLElement).getByRole("link", { name: "Mister Pulse ON" }));
  await settle();
  await settle();
  expect(screen.getByTestId("here").textContent).toBe("/plan?edit=mister_pulse_on_s");
  const drawer = document.querySelector("dialog")!;
  expect(drawer.hasAttribute("open")).toBe(true);
  expect(text(drawer.querySelector(".drawer-heading"))).toBe("Mister Pulse ON");
  expect(text(within(drawer).getByRole("heading", { level: 2 }))).toBe("Edit setting");
  // Closing it stays on Plan, without the drawer's URL.
  fireEvent.click(within(drawer).getByRole("button", { name: "Cancel" }));
  await settle();
  expect(screen.getByTestId("here").textContent).toBe("/plan");
  expect(document.querySelector("dialog[open]")).toBeNull();
  expect(manualCount(0)).toBeNull();
  expect(manualCount(3)).toBe("3 settings MANUAL · Iris skips them");
});

test("a lane's newest write shows its one pill at the lane's head: REQUESTED while on its way, and the read settles every 5 s", async () => {
  const { api } = await open("/plan?edit=mister_engage_kpa", "in-band");
  await settle();
  const drawer = document.querySelector("dialog")!;
  fireEvent.change(within(drawer).getByLabelText("New value"), { target: { value: "1.55" } });
  fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
  await settle();
  await settle();
  expect(writes(api).map((row) => [row.operationId, row.body])).toEqual([["API-10", { value: 1.55, manual: true }]]);
  expect(screen.getByTestId("here").textContent).toBe("/plan");
  const pill = () => [...document.querySelectorAll("[data-param=mister_engage_kpa] .ctl-lane-pill [data-badge]")].map((node) => node.getAttribute("data-badge"));
  expect(pill()).toEqual(["requested"]);
  expect(status("mister_engage_kpa")).toBe("MANUAL · Iris skips · jason since 11/02");
  // While a lane's write is on its way API-07 is read every 5 s, not every 60 s.
  const reads = sentAt("API-07").length;
  await advance(5_000);
  await settle();
  expect(sentAt("API-07").length).toBe(reads + 1);
});

test("a forecast-rule waypoint reads \"Forecast rule · system\" and never carries AI; Iris's waypoints read PLANNED · AI", async () => {
  const { plot } = await open("/plan", "forecast-rule");
  expect(status("mister_engage_delay_s")).toBe(FORECAST_RULE);
  const row = document.querySelector("[data-param=mister_engage_delay_s]")!;
  expect(row.textContent).not.toMatch(/\bAI\b/);
  expect(row.querySelector("[data-badge=ai]")).toBeNull();
  // The device value holds to 14:00, then the rule's 90 s is dashed to the plan's end; its label never says AI.
  expect(strokes(plot, 2)).toEqual([[DEVICE, "", 0, x("14:00")], [INK, "8,4", x("14:00"), x("17:30")]]);
  expect(labels(plot, 2).every(([words]) => !/\bAI\b/.test(words))).toBe(true);
  plot.hover(epoch(denver("15:00")), null, 2 * 44 + 30);
  expect(tooltip(plot).slice(1)).toEqual(["Mister Engage Delay", `${FORECAST_RULE} 90.0 s from 14:00`]);
  expect(status("mister_engage_kpa")).toBe(PLANNED_AI);
});

// ---------------------------------------------------------------------------------------------------- planner health
test("a trigger mark's place on the 36 h scale: −36 h at the start, now at the end, an older or unreadable time at the start", () => {
  const now = Date.parse("2026-11-02T17:42:00Z");
  const ago = (hours: number) => new Date(now - hours * 3_600_000).toISOString();
  expect([markPosition(ago(36), now), markPosition(ago(18), now), markPosition(ago(0), now), markPosition(ago(40), now), markPosition("soon", now)]).toEqual([0, 50, 100, 0, 0]);
});

test("planner health: the last 36 h as marks, the next trigger, the missed SUNSET in view with ▲, the 14-day strip, and the ledger in a disclosure", async () => {
  await open("/plan", "missed-sunset");
  const health = region("Planner health");
  // The fired triggers oldest first, quiet when done and loud when they failed; an optional one is shorter.
  const marks = [...health.querySelectorAll(".ctl-trigger-marks > li")].map((mark) => [mark.getAttribute("title"), mark.getAttribute("data-kind"), mark.getAttribute("data-required")]);
  expect(marks).toEqual([
    ["MIDNIGHT plan written", "ok", "true"], ["SUNRISE plan written", "ok", "true"], ["SOLAR_MAX plan written", "ok", null],
    ["SUNSET missed", "failed", "true"], ["MIDNIGHT plan written", "ok", "true"], ["SUNRISE plan written", "ok", "true"],
  ]);
  expect(text(health.querySelector(".ctl-trigger-marks > li:nth-child(4) .screen-reader"))).toBe("SUNSET missed at ");
  // Each at its time on the 36 h scale, −36 h to now; the key says what a short mark is (#1308 visual QA 37).
  const at = [...health.querySelectorAll<HTMLElement>(".ctl-trigger-marks > li")].map((mark) => Number(mark.style.getPropertyValue("--at")));
  expect(at.every((value) => value >= 0 && value <= 100)).toBe(true);
  expect(at).toEqual([...at].sort((a, b) => a - b));
  expect(text(health.querySelector(".ctl-trigger-ends"))).toBe("−36 hnow");
  expect([...health.querySelectorAll(".ctl-trigger-legend li")].map((item) => text(item))).toEqual(["done", "late or fallback", "failed or missed", "short = optional"]);
  expect(text(health.querySelector(".ctl-next-trigger"))).toBe("Next SOLAR_MAX at 12:41");
  const rows = (list: Element) => [...list.querySelectorAll(":scope > li")].map((item) => [
    item.getAttribute("data-trigger"), text(item.querySelector(".trigger-due time")), text(item.querySelector(".trigger-outcome")),
    item.querySelector(".trigger-class") ? text(item.querySelector(".trigger-class")) : null, item.classList.contains("trigger-marked"),
  ]);
  const [attention, ledger] = [...health.querySelectorAll("ol.trigger-list")];
  // The missed SUNSET stays in view, with ▲, the amber rule and its failure class.
  expect(rows(attention)).toEqual([["SUNSET", "Nov 1, 17:20", "missed", "no_plan_written", true]]);
  expect(attention.closest("details")).toBeNull();
  expect(attention.querySelector(".trigger-mark")!.textContent).toBe("▲Needs attention: ");
  // The 14-day strip: daily.iris_required_* from API-04, one cell a day, today partial.
  expect(text(health.querySelector(".ctl-reliability-words"))).toBe("Last 14 days 42 of 42 required plans written");
  const cells = [...health.querySelectorAll(".strip-cell")];
  expect(cells).toHaveLength(14);
  expect(cells.map((cell) => cell.getAttribute("data-level"))).toEqual(Array(14).fill("0"));
  expect(cells.at(-1)!.getAttribute("aria-label")).toBe("Nov 2: 3 required plans written, 0 missed so far");
  expect(cells.at(-1)!.getAttribute("data-partial")).toBe("true");
  // The ledger: everything else, newest first with the next row ahead first. The fixture keeps yesterday's MIDNIGHT 34 h
  // 27 min before 10:42, which on the fall-back day is 01:15 MDT, so its due time falls in the repeated hour and carries
  // its offset.
  expect(text(health.querySelector("details summary"))).toBe("Trigger ledger · 8");
  expect(rows(ledger)).toEqual([
    ["SOLAR_MAX", "13:11", "due", null, false], ["SUNRISE", "07:30", "plan written", null, false], ["MIDNIGHT", "00:45", "plan written", null, false],
    ["SOLAR_MAX", "Nov 1, 13:11", "plan written", null, false], ["SUNRISE", "Nov 1, 07:30", "plan written", null, false],
    ["MIDNIGHT", "Nov 3, 00:45", "due", null, false], ["SUNSET", "17:20", "due", null, false], ["MIDNIGHT", "Nov 1, 01:45 GMT-06:00", "plan written", null, false],
  ]);
  // No ledger status renders as a raw code.
  const outcomes = [...health.querySelectorAll(".trigger-outcome")].map((cell) => text(cell));
  expect(outcomes.every((words) => Object.values(WORDS.trigger_outcome).includes(words))).toBe(true);
  expect(text(health)).not.toMatch(/\bplan_written\b|\bexpected\b|\backed\b/);
  // No attempts column and no recent plans.
  expect(text(health)).not.toMatch(/attempt|recent plans/i);
});

test("the 14-day strip reads daily.iris_required_* for the 14 local days ending today, one bucket a day; a missed day is loud and a day with none a gap", async () => {
  const { api } = await open("/plan", "scorecard-30d");
  const asked = new URL(requests(api, "API-04")[0].path, "http://x").searchParams;
  expect([asked.getAll("metric"), asked.get("from"), asked.get("to"), asked.get("points")]).toEqual([
    ["daily.iris_required_written", "daily.iris_required_missed"], "2026-10-20T06:00:00.000Z", "2026-11-03T07:00:00.000Z", "15",
  ]);
  const health = region("Planner health");
  // 9 days back one required plan was missed; 12 days back there is no row at all.
  const cells = [...health.querySelectorAll(".strip-cell")];
  expect(cells[13 - 9].getAttribute("data-level")).toBe("2");
  expect(cells[13 - 9].getAttribute("aria-label")).toBe("Oct 24: 2 required plans written, 1 missed");
  expect(cells[13 - 12].getAttribute("data-level")).toBe("none");
  expect(cells[13 - 12].getAttribute("aria-label")).toBe("Oct 21: no data");
  // 13 days with a row: 11 of 3 plans, today's 2 so far and the missed day's 2, against one missed.
  expect(text(health.querySelector(".ctl-reliability-count"))).toBe("37 of 38 required plans written");
});

const trigger = (fields: Partial<PlannerTrigger>): PlannerTrigger => ({
  trigger: "SUNSET", required: true, expected_at: denver("09:00"), due_at: denver("09:30"), outcome: "plan_written", resolved_at: null, failure_class: null, ...fields,
});
test("the ▲ rule: a required trigger missed, timed out or not delivered, or still due or delivered past its due time; never an optional one", () => {
  for (const outcome of ["missed", "timed_out", "delivery_failed"]) expect(needsAttention(trigger({ outcome }), NOW), outcome).toBe(true);
  for (const outcome of ["plan_written", "acked", "action_completed", "neutral_fallback", "wrong_action"]) expect(needsAttention(trigger({ outcome }), NOW), outcome).toBe(false);
  // Late: still expected or delivered after due_at.
  expect(needsAttention(trigger({ outcome: "expected" }), NOW)).toBe(true);
  expect(needsAttention(trigger({ outcome: "delivered" }), NOW)).toBe(true);
  expect(isLate(trigger({ outcome: "delivered" }), NOW)).toBe(true);
  // Not yet due, or optional (SOLAR_MAX): no ▲, even when missed. An unreadable due time is unknown, never late.
  expect(needsAttention(trigger({ outcome: "expected", due_at: denver("11:00") }), NOW)).toBe(false);
  expect([isLate(trigger({ outcome: "expected", due_at: "not a time" }), NOW), needsAttention(trigger({ outcome: "delivered", due_at: "" }), NOW)]).toEqual([false, false]);
  expect(needsAttention(trigger({ trigger: "SOLAR_MAX", required: false, outcome: "missed" }), NOW)).toBe(false);
  expect(isLate(trigger({ trigger: "SOLAR_MAX", required: false, outcome: "expected" }), NOW)).toBe(true);
  // A late or unknown outcome renders with its words, "late" after them, and an unknown status raw.
  const rows = [
    trigger({ trigger: "SUNRISE", expected_at: denver("07:00"), due_at: denver("07:30"), outcome: "delivered" }),
    trigger({ trigger: "TRANSITION", required: false, expected_at: denver("08:00"), due_at: denver("08:30"), outcome: "rescheduled" }),
  ];
  const { container } = renderUi(<PlannerHealth health={rows} state="ready" now={NOW} />);
  // The late required SUNRISE stays in view; the rest is in the ledger.
  expect([...container.querySelectorAll(".trigger-outcome")].map((cell) => text(cell))).toEqual(["delivered · late", "rescheduled"]);
  expect([...container.querySelectorAll(".ctl-trigger-marks > li")].map((mark) => mark.getAttribute("data-kind"))).toEqual(["late", "other"]);
  expect([...container.querySelectorAll(".trigger-marked")].map((item) => item.getAttribute("data-trigger"))).toEqual(["SUNRISE"]);
  // Newest first: the next row ahead, the five newest that fired and every marked row; the rest after them.
  const many = [
    ...Array.from({ length: 8 }, (_, index) => trigger({ trigger: `T${index}`, required: index === 0, expected_at: denver(`0${index}:10`), due_at: denver(`0${index}:40`), outcome: index === 0 ? "timed_out" : "acked" })),
    trigger({ trigger: "U1", expected_at: denver("12:00"), due_at: denver("12:30"), outcome: "expected" }),
    trigger({ trigger: "U2", expected_at: denver("15:00"), due_at: denver("15:30"), outcome: "expected" }),
  ];
  const { shown, rest } = healthRows(many, NOW);
  expect(shown.map((row) => row.trigger)).toEqual(["U1", "T7", "T6", "T5", "T4", "T3", "T0"]);
  expect(rest.map((row) => row.trigger)).toEqual(["U2", "T2", "T1"]);
  // Nothing ahead: the five newest and the marked rows only.
  expect(healthRows(many.slice(0, 8), NOW).shown.map((row) => row.trigger)).toEqual(["T7", "T6", "T5", "T4", "T3", "T0"]);
  expect(NEWEST_SHOWN).toBe(5);
});

// ---------------------------------------------------------------------------------------------------- window, layout, states
test("the 72 h chip sets ?hours=72 and reads 72 h, and loading /plan?hours=72 keeps it", async () => {
  const { api } = await open("/plan");
  const lanes = region("Planned settings");
  expect(requests(api, "API-07").map((row) => row.path)).toEqual(["/api/v1/plan?hours=24"]);
  expect(within(lanes).getByRole("link", { name: "24 h" }).getAttribute("aria-current")).toBe("page");
  expect(within(lanes).getByRole("link", { name: "72 h" }).getAttribute("href")).toBe("/plan?hours=72");
  expect(text(lanes.querySelector("figcaption"))).toBe("Next 24 h · one lane per planned setting");
  fireEvent.click(within(lanes).getByRole("link", { name: "72 h" }));
  await settle();
  paint();
  expect(requests(api, "API-07").map((row) => row.path)).toEqual(["/api/v1/plan?hours=24", "/api/v1/plan?hours=72"]);
  expect(within(region("Planned settings")).getByRole("link", { name: "72 h" }).getAttribute("aria-current")).toBe("page");
  expect(text(region("Planned settings").querySelector("figcaption"))).toBe("Next 72 h · one lane per planned setting");
  expect(created[created.length - 1].opts.scales!.x).toMatchObject({ min: START, max: START + 72 * 3600 });
  cleanup();
  stopFixtureApi();
  // Reloading the URL keeps the window.
  const reloaded = await open("/plan?hours=72");
  expect(requests(reloaded.api, "API-07").map((row) => row.path)).toEqual(["/api/v1/plan?hours=72"]);
  expect(within(region("Planned settings")).getByRole("link", { name: "72 h" }).getAttribute("aria-current")).toBe("page");
  expect(within(region("Planned settings")).getByRole("link", { name: "24 h" }).getAttribute("href")).toBe("/plan");
  expect([planHours(new URLSearchParams("hours=72")), planHours(new URLSearchParams("hours=48")), planHours(new URLSearchParams())]).toEqual([72, 24, 24]);
});

test("at 390 px the card, health and lanes stack: card and health share .l-cols-2 and the lanes follow them", async () => {
  await open("/plan", "superseded-lane");
  // .l-cols-2 stacks below 600 px (plan-layout.test.ts checks the stylesheet rules, the 6rem lane labels included).
  const grid = document.querySelector(".l-cols-2.ctl-plan-top")!;
  expect([...grid.children].map((child) => text(child.querySelector("h2")))).toEqual(["Plan in force", "Planner health"]);
  expect(grid.nextElementSibling?.querySelector("h2")?.textContent).toBe("Planned settings");
  expect(grid.nextElementSibling?.querySelector(".lanes")?.classList.contains("lanes-plan")).toBe(true);
});

test("loading shows the three panels as skeletons at their reserved heights; nothing from API-07 renders on the server", async () => {
  const api = startFixtureApi();
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media());
  const Stub = createRoutesStub([{ path: "/", Component: () => <AppShell build={BUILD} authError={null} />, children: [{ path: "plan", Component: Plan }] }]);
  // A server render is the page head and three skeletons: no plan, ledger or lane, and no request.
  const html = renderToString(<Stub initialEntries={["/plan"]} />);
  expect(html).toContain("<h1>Plan</h1>");
  expect(html.match(/class="skeleton skeleton-(md|lg)"/g)).toHaveLength(3);
  expect(html).not.toMatch(/iris-2026|SUNRISE|plan-row|trigger-row/);
  expect(requests(api)).toEqual([]);
  render(<Stub initialEntries={["/plan"]} />);
  const busy = [...document.querySelectorAll("section[aria-busy=true]")];
  expect(busy.map((section) => [section.querySelector("h2")!.textContent, section.querySelector(".skeleton")!.className])).toEqual([
    ["Plan in force", "skeleton skeleton-md"], ["Planner health", "skeleton skeleton-md"], ["Planned settings", "skeleton skeleton-lg"],
  ]);
  await settle();
  expect(document.querySelectorAll("section[aria-busy=true]")).toHaveLength(0);
});

test("API-07 failing shows Retry in each panel and keeps no earlier answer on screen; Retry reads it again", async () => {
  const { api } = await open("/plan", "in-band", { fail: { "API-07": 503 } });
  const panels = ["Plan in force", "Planner health", "Planned settings"].map((name) => region(name));
  expect(panels.map((panel) => text(panel.querySelector(".panel-error p")))).toEqual([
    "Couldn't load the plan in force.", "Couldn't load planner health.", "Couldn't load the planned settings.",
  ]);
  expect(panels.every((panel) => within(panel).getByRole("button", { name: "Retry" }))).toBe(true);
  // Nothing green: no fresh dot, confirmed or ok tag anywhere on the page.
  expect(document.querySelector("main [data-fresh=ok], main .tag-ok, main .status-complete")).toBeNull();
  // The API answers again: Retry reads it at once.
  api.respond({ method: "POST", url: "/__control", body: { fixture: "in-band" } });
  fireEvent.click(within(panels[0]).getByRole("button", { name: "Retry" }));
  await settle();
  expect(text(region("Plan in force").querySelector(".plan-facts"))).toContain("iris-20261102-0702");
  // A poll that fails after an answer replaces it with the error: the old plan is not kept on screen.
  api.respond({ method: "POST", url: "/__control", body: { fixture: "in-band", fail: { "API-07": 503 } } });
  await advance(2 * PLAN_POLL_MS);
  await settle();
  expect(document.querySelector(".plan-facts")).toBeNull();
  expect(text(region("Plan in force").querySelector(".panel-error p"))).toBe("Couldn't load the plan in force.");
});

test("while the controller is not reporting the banner shows, the plan still renders, and device values read STALE while waypoints still draw", async () => {
  const { plot } = await open("/plan", "controller-not-reporting");
  expect(text(document.querySelector("[data-banner=fault]"))).toMatch(/^Controller not reporting since 10:36\./);
  expect(text(region("Plan in force").querySelector(".plan-facts"))).toContain("iris-20261102-0702");
  expect(text(document.querySelector(".chart-note-stale"))).toBe("STALE · device values last read 10:41");
  expect([...document.querySelectorAll(".plan-device")].map((cell) => [text(cell), cell.classList.contains("plan-device-stale")])).toEqual([
    ["1.60 kPa", true], ["45.0 s", true], ["60.0 s", true],
  ]);
  // The device value is muted and labelled STALE; Iris's 13:00 waypoint still draws dashed as PLANNED.
  expect(strokes(plot, 0)).toEqual([[MUTED, "", 0, x("13:00")], [INK, "8,4", x("13:00"), x("17:30")]]);
  expect(labels(plot, 0)[0]).toEqual(["STALE", MUTED]);
  cleanup();
  stopFixtureApi();
  await open("/plan", "ingestor-not-running");
  expect(text(document.querySelector("[data-banner=fault]"))).toBe("Ingestor not running since 10:38. The controller keeps running on its own. Data on Now ›");
  expect(text(region("Plan in force").querySelector(".plan-facts"))).toContain("iris-20261102-0702");
  expect(document.querySelectorAll(".plan-device-stale")).toHaveLength(3);
  cleanup();
  stopFixtureApi();
  // Fresh readback: nothing reads STALE.
  await open("/plan");
  expect(document.querySelector(".chart-note-stale, .plan-device-stale")).toBeNull();
});

test("the time left and the lanes' window count on the server's clock, not a device clock running ahead of it", async () => {
  // The device clock reads 10:42 and runs 50 min ahead of the server's (the shell's API-02 skew): the page reads as of 09:52.
  startFixtureApi({ fixture: "in-band" });
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ narrow: false, fine: true }));
  const ahead = () => <ServerClockProvider skewMs={50 * 60_000}><Plan /></ServerClockProvider>;
  const Stub = createRoutesStub([{ path: "/", Component: () => <AppShell build={BUILD} authError={null} />, children: [{ path: "plan", Component: ahead }] }]);
  render(<Stub initialEntries={["/plan"]} />);
  await settle();
  const plot = paint();
  // 17:30 is 7 h 38 min after 09:52 (6 h 48 min after the device's 10:42).
  expect(text(region("Plan in force").querySelector(".ctl-validity-words"))).toBe("written 07:02 → valid to 17:30 · 7 h 38 min left");
  expect(plot.opts.scales!.x).toMatchObject({ min: START - 3000, max: START - 3000 + 24 * 3600 });
});

test("API-07 is polled every 60 s, paused while the tab is hidden; API-02 stays the shell's one poll; nothing is ever written", async () => {
  const { api } = await open("/plan");
  await advance(PLAN_POLL_MS);
  expect(sentAt("API-07")).toEqual([0, PLAN_POLL_MS]);
  // Staleness is read from the shell's API-02 poll (15 s), never a second one.
  expect(sentAt("API-02")).toEqual([0, 15_000, 30_000, 45_000, 60_000]);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
  await advance(3 * PLAN_POLL_MS);
  expect(sentAt("API-07")).toEqual([0, PLAN_POLL_MS]);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  document.dispatchEvent(new Event("visibilitychange"));
  await settle();
  // Opening the disclosures and switching the window send no write.
  for (const summary of document.querySelectorAll("summary")) fireEvent.click(summary);
  fireEvent.click(within(region("Planned settings")).getByRole("link", { name: "72 h" }));
  await settle();
  expect(requests(api).map((row) => row.method)).not.toContain("PUT");
  expect(writes(api)).toEqual([]);
});

test("a viewer sees the same page with no write control: a lane's label is a link for every role, to a read-only drawer", async () => {
  await open("/plan", ["viewer-session", "manual-lane"]);
  expect(document.querySelector("[data-role]")?.getAttribute("data-role")).toBe("viewer");
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
  expect(region("Planned settings").querySelectorAll("a.ctl-lane-link")).toHaveLength(4);
  expect(status("mister_pulse_on_s")).toBe("MANUAL · Iris skips · jason since 10/30");
  // The one button is "Find a setting"'s search: the lanes, the chips and the disclosures are links and summaries.
  expect([...document.querySelector("main")!.querySelectorAll("button")].map((button) => text(button))).toEqual(["Find"]);
  fireEvent.click(within(region("Planned settings")).getByRole("link", { name: "Mister Pulse ON" }));
  await settle();
  await settle();
  expect(text(within(document.querySelector("dialog")!).getByRole("heading", { level: 2 }))).toBe("Setting");
  expect(within(document.querySelector("dialog")!).queryByRole("button", { name: "Save" })).toBeNull();
});

test("Find a setting searches Settings by name or key", async () => {
  await open("/plan");
  const find = within(document.querySelector(".page-header") as HTMLElement);
  fireEvent.change(find.getByRole("searchbox", { name: "Find a setting" }), { target: { value: "pulse gap" } });
  fireEvent.click(find.getByRole("button", { name: "Find" }));
  await settle();
  await settle();
  expect(screen.getByTestId("settings").textContent).toBe("/settings?q=pulse+gap");
});
