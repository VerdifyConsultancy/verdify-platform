// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Coming up on Now (#1138): the next-transition pick, the next Iris trigger, the next run per line and the sun, against #1122's
// fixtures at 10:42 America/Denver on Monday 2026-11-02.
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationLine, IrrigationStatus, Plan, PlanLane, PlannerTrigger, ScheduleAgenda } from "../lib/operator-api";
import { panelRead, useIrrigationRead } from "../lib/queries";
import { NOW, advance, renderWithApi, requests, startFixtureApi, stopFixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import { ComingUp, NO_PLAN, NO_RUNS, NO_RUNS_BEFORE_ENGINE, NO_TRIGGER, actorName, nextRuns, nextTransition, nextTrigger, pausedWords, sunWords } from "./coming-up";
import { denver, text } from "./test-render";

afterEach(() => { cleanup(); stopFixtureApi(); vi.unstubAllGlobals(); });

const set = (names: string | string[] = "in-band") => fixture(names, { now: NOW });
const planOf = (names?: string | string[]) => set(names)["API-07"] as Plan;
const lane = (fields: Partial<PlanLane> & { param: string }): PlanLane => ({
  label: fields.param, unit: "kPa", device: { value: 1.6, observed_at: denver("10:40"), reason: null }, waypoints: [], desired: null, applied_from: "plan", ...fields,
} as PlanLane);
const waypoint = (clock: string, value: number | null, fields: { source?: string; effective?: boolean } = {}) => ({ at: denver(clock), value, source: fields.source ?? "iris", effective: fields.effective ?? true });
const plain = (value: string) => value.replaceAll(" ", " ");

test("the next transition: the earliest waypoint after now, every lane's change at that time, with its from and to", () => {
  const next = nextTransition(planOf(), NOW);
  expect(next?.at).toBe(denver("13:00"));
  expect(next?.changes.map((change) => [plain(change.text), change.source])).toEqual([["VPD Mister Engage 1.60 → 1.70 kPa", "iris"]]);
  // The plan's other lanes change later, so they are not listed.
  const both = nextTransition({ lanes: [lane({ param: "a", waypoints: [waypoint("13:00", 1.7)] }), lane({ param: "b", label: "B", unit: "s", device: { value: 45, observed_at: null, reason: null }, waypoints: [waypoint("13:00", 55)] }), lane({ param: "c", waypoints: [waypoint("14:00", 1.8)] })] }, NOW);
  expect(both?.changes.map((change) => change.param)).toEqual(["a", "b"]);
  expect(plain(both!.changes[1].text)).toBe("B 45.0 → 55.0 s");
});

test("a manual setting is left out, however it reads: applied_from manual or desired.manual", () => {
  const manual = { manual: true } as PlanLane["desired"];
  const plan = {
    lanes: [
      lane({ param: "by_hand", applied_from: "manual", waypoints: [waypoint("11:00", 1.9)] }),
      lane({ param: "desired_manual", desired: manual, waypoints: [waypoint("11:30", 1.9)] }),
      lane({ param: "planned", waypoints: [waypoint("13:00", 1.7)] }),
    ],
  };
  expect(nextTransition(plan, NOW)?.changes.map((change) => change.param)).toEqual(["planned"]);
  // A desired row that is not manual does not leave the lane out.
  expect(nextTransition({ lanes: [lane({ param: "held", desired: { manual: false } as PlanLane["desired"], waypoints: [waypoint("11:00", 1.9)] })] }, NOW)?.changes[0].param).toBe("held");
  // The fixture: Jason's manual lane has a 12:00 waypoint, and the next change is still 13:00.
  expect(nextTransition(planOf("plan-next"), NOW)?.at).toBe(denver("13:00"));
});

test("only an effective waypoint after now with a value counts", () => {
  const plan = {
    lanes: [lane({ param: "a", waypoints: [waypoint("09:00", 1.9), waypoint("11:00", 1.9, { effective: false }), waypoint("11:30", null), waypoint("15:00", 1.8)] })],
  };
  const next = nextTransition(plan, NOW);
  expect(next?.at).toBe(denver("15:00"));
  expect(plain(next!.changes[0].text)).toBe("a 1.60 → 1.80 kPa");
  expect(nextTransition({ lanes: [lane({ param: "a", waypoints: [waypoint("10:42", 1.9)] })] }, NOW)).toBeNull();
});

test("a waypoint that repeats the lane's value is no change: Iris restates most settings at each time", () => {
  const plan = {
    lanes: [
      lane({ param: "same", waypoints: [waypoint("11:00", 1.6)] }),
      // A binary32 readback's noise is not a change either.
      lane({ param: "noise", device: { value: 1.600000023841858, observed_at: null, reason: null }, waypoints: [waypoint("11:00", 1.6)] }),
      lane({ param: "later", waypoints: [waypoint("11:00", 1.6), waypoint("14:00", 1.75)] }),
    ],
  };
  const next = nextTransition(plan, NOW);
  expect(next?.at).toBe(denver("14:00"));
  expect(next?.changes.map((change) => change.param)).toEqual(["later"]);
  // No device value to compare: it is a change, "—" to the value.
  const unknown = nextTransition({ lanes: [lane({ param: "new", device: { value: null, observed_at: null, reason: "not reported" }, waypoints: [waypoint("11:00", 1.6)] })] }, NOW);
  expect(plain(unknown!.changes[0].text)).toBe("new — → 1.60 kPa");
  // A switch reads ON or OFF, and a value keeps the decimals it carries.
  const words = nextTransition({
    lanes: [
      lane({ param: "sw_fog_closes_vent", label: "Fog closes vent", unit: null, device: { value: 0, observed_at: null, reason: null }, waypoints: [waypoint("11:00", 1)] }),
      lane({ param: "vpd_hysteresis", label: "Hysteresis", waypoints: [waypoint("11:00", 0.1875)] }),
    ],
  }, NOW);
  expect(words?.changes.map((change) => plain(change.text))).toEqual(["Fog closes vent OFF → ON", "Hysteresis 1.60 → 0.1875 kPa"]);
});

test("the next trigger is the earliest expected row ahead; a row already resolved or past does not count", () => {
  const rows = (fields: [string, string, string][]) => fields.map(([trigger, clock, outcome]) => ({ trigger, required: true, expected_at: denver(clock), due_at: denver(clock), outcome })) as PlannerTrigger[];
  expect(nextTrigger(rows([["SUNSET", "16:50", "expected"], ["SOLAR_MAX", "12:41", "expected"], ["SUNRISE", "07:00", "expected"], ["MIDDAY", "11:00", "plan_written"]]), NOW)?.trigger).toBe("SOLAR_MAX");
  expect(nextTrigger(rows([["SUNRISE", "07:00", "expected"]]), NOW)).toBeNull();
  expect(nextTrigger([], NOW)).toBeNull();
  expect(nextTrigger(planOf("plan-next").health, NOW)).toMatchObject({ trigger: "SUNSET", expected_at: denver("16:50") });
});

const agendaOf = (names: string | string[]) => set(names)["API-IRR-02"] as ScheduleAgenda;
const linesOf = (names: string | string[]) => (set(names)["API-IRR-01"] as IrrigationStatus).lines;
const ME = { username: "jason", display_name: "Jason" };

test("the next run per line joins its schedule, soonest first: time, minutes and label", () => {
  const runs = nextRuns(agendaOf("schedules-paused"), linesOf("schedules-paused"), NOW, ME).map((run) => plain(run.text));
  expect(runs).toEqual(["L04 Tue 06:50 · 8 min · Dawn drench", "L01 Wed 07:35 · 12 min · Wall MWF", "L02 Sun 07:35 · 5 min · Weekly feed"]);
});

test("a line paused past now says who paused it and why, and its next run is the first after the pause", () => {
  const lines = linesOf("l01-paused");
  const runs = nextRuns(agendaOf("l01-paused"), lines, NOW, ME).map((run) => plain(run.text));
  expect(runs).toContain("L01 Paused until 14:00 by Jason: rain · next Wed 07:35");
  // The reason is optional.
  const l01 = lines.find((line) => line.line_id === "L01") as IrrigationLine;
  expect(pausedWords({ ...l01, pause_reason: null }, NOW, ME)).toBe("Paused until 14:00 by Jason");
  expect(pausedWords({ ...l01, paused_by: null, pause_reason: null }, NOW, ME)).toBe("Paused until 14:00");
  // A pause already over reads as no pause; a pause past the only planned run leaves the line out.
  const over = lines.map((line) => (line.line_id === "L01" ? { ...line, paused_until: denver("10:00") } : line));
  expect(nextRuns(agendaOf("schedules-paused"), over, NOW, ME).map((run) => plain(run.text))).toContain("L01 Wed 07:35 · 12 min · Wall MWF");
  const forever = lines.map((line) => (line.line_id === "L01" ? { ...line, paused_until: "2026-12-25T00:00:00Z" } : line));
  expect(nextRuns(agendaOf("schedules-paused"), forever, NOW, ME).some((run) => run.key === "L01")).toBe(false);
});

test("other people read by username or source, only the signed-in person by display name", () => {
  expect(actorName("jason", ME)).toBe("Jason");
  expect(actorName("caretaker", ME)).toBe("caretaker");
  expect(actorName("steward", ME)).toBe("steward");
  expect(actorName("jason", null)).toBe("jason");
  expect(actorName("jason", { username: "jason", display_name: null })).toBe("jason");
});

test("lines with no planned run are left out, and the fallback slots say when they run", () => {
  expect(nextRuns({ schedules: [], occurrences: [] }, [], NOW, ME)).toEqual([]);
  const blocked = agendaOf(["schedules-paused", "before-engine"]);
  expect(blocked.occurrences.every((row) => row.status !== "planned")).toBe(true);
  expect(nextRuns(blocked, [], NOW, ME)).toEqual([]);
  expect(nextRuns(agendaOf("in-band"), linesOf("in-band"), NOW, ME).map((run) => plain(run.text))).toEqual(["L04 today 11:47 · 3 min · vanda-degraded · only if VPD sensing is degraded"]);
});

test("the sun: today's sunrise and sunset; after sunset, tomorrow's sunrise", () => {
  const solar = [
    { date: "2026-11-02", sunrise: denver("06:35"), solar_noon: denver("11:47"), sunset: denver("16:58") },
    { date: "2026-11-03", sunrise: "2026-11-03T13:36:00Z", solar_noon: "2026-11-03T18:47:00Z", sunset: "2026-11-03T23:57:00Z" },
  ];
  expect(sunWords(solar, NOW)).toBe("Sunrise 06:35 · Sunset 16:58");
  expect(sunWords(solar, Date.parse(denver("05:00")))).toBe("Sunrise 06:35 · Sunset 16:58");
  expect(sunWords(solar, Date.parse(denver("16:59")))).toBe("Sunrise tomorrow 06:36");
  expect(sunWords([], NOW)).toBeNull();
});

/** The panel over the fixture API: it reads API-07 and API-IRR-02 itself, and takes API-IRR-01 from Now. */
function Panel() {
  return <ComingUp irrigation={panelRead(useIrrigationRead())} />;
}
async function open(options: FixtureOptions = {}) {
  const api = startFixtureApi(options);
  renderWithApi(<Panel />);
  await advance(0);
  return api;
}
const row = (name: string) => plain(text(document.querySelector(`[data-coming=${name}]`)));

test("in the panel: the transition with AI, the trigger, the runs and the sun, and a forecast rule reads Forecast rule · system, never AI", async () => {
  await open({ fixture: ["plan-next", "l01-paused"] });
  const changes = [...document.querySelectorAll("[data-coming=plan] li")].map((node) => plain(text(node)));
  expect(changes).toEqual(["13:00 · VPD Mister Engage 1.60 → 1.70 kPa · AI", "13:00 · Mister Engage Delay 60.0 → 90.0 s · Forecast rule · system"]);
  expect(document.querySelectorAll("[data-coming=plan] [data-badge=ai]")).toHaveLength(1);
  expect(row("iris")).toBe("Next Iris triggerSUNSET at 16:50");
  // Mister Pulse ON is manual, so its 12:00 waypoint never reads.
  expect(text(document.querySelector("[data-coming=plan]"))).not.toContain("Mister Pulse");
  // The "by jason" of the record reads as is without a session: the shell gives the display name on Now.
  expect(row("runs")).toContain("L01 Paused until 14:00 by jason: rain · next Wed 07:35");
  expect(row("sun")).toBe("SunSunrise 06:35 · Sunset 16:58");
});

test("the empty rows say so: no plan, no planned change before the plan ends, no trigger yet, no run", async () => {
  await open({ fixture: "no-plan" });
  expect(row("plan")).toBe(`Next change${NO_PLAN}`);
  cleanup(); stopFixtureApi();
  // Every waypoint gone, with a plan still in force.
  const api = startFixtureApi();
  const answered = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const response = await answered(input, init);
    if (!input.includes("/v1/plan")) return response;
    const body = await response.json() as Plan;
    return { ...response, json: async () => ({ ...body, lanes: body.lanes.map((item) => ({ ...item, waypoints: [] })), health: [] }) } as Response;
  }));
  renderWithApi(<Panel />);
  await advance(0);
  expect(requests(api, "API-07").map((item) => item.path)).toEqual(["/api/v1/plan?hours=24"]);
  expect(requests(api, "API-IRR-02").map((item) => item.path)).toEqual(["/api/v1/irrigation/schedules?days=7"]);
  expect(row("plan")).toBe("Next changeNo planned change before the plan ends at 17:30");
  expect(row("iris")).toBe(`Next Iris trigger${NO_TRIGGER}`);
  cleanup(); stopFixtureApi();
  await open({ fixture: ["schedules-paused", "before-engine"] });
  expect(row("runs")).toBe(`Next run${NO_RUNS_BEFORE_ENGINE}`);
  cleanup(); stopFixtureApi();
  const none = startFixtureApi();
  none.respond({ method: "POST", url: "/__control", body: { fixture: "in-band" } });
  const plainFetch = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const response = await plainFetch(input, init);
    if (!input.includes("/v1/irrigation/schedules")) return response;
    const body = await response.json() as ScheduleAgenda;
    return { ...response, json: async () => ({ ...body, occurrences: [] }) } as Response;
  }));
  renderWithApi(<Panel />);
  await advance(0);
  expect(row("runs")).toBe(`Next run${NO_RUNS}`);
});

test("each row fails alone with Retry, never reading none: API-07 down leaves the runs and the sun", async () => {
  const api = await open({ fail: { "API-07": 503 } });
  expect(row("plan")).toContain("Plan unavailable");
  expect(row("iris")).toContain("Iris trigger unavailable");
  expect(row("sun")).toContain("Sunrise");
  expect(row("runs")).toContain("L04 today 11:47");
  expect(document.body.textContent).not.toContain(NO_PLAN);
  api.respond({ method: "POST", url: "/__control", body: { fixture: "in-band" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Retry" })[0]);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(row("plan")).toContain("13:00");
  cleanup(); stopFixtureApi();
  // A failed API-IRR-01 leaves pauses unknown, so the runs say unavailable rather than list a paused line as running.
  await open({ fixture: "l01-paused", fail: { "API-IRR-01": 503 } });
  expect(row("runs")).toContain("Next run unavailable");
  expect(row("runs")).not.toContain("L01");
  expect(row("sun")).toContain("Sunrise");
  cleanup(); stopFixtureApi();
  await open({ fail: { "API-IRR-02": 503 } });
  expect(row("runs")).toContain("Next run unavailable");
  expect(row("sun")).toContain("Sun times unavailable");
  expect(row("plan")).toContain("13:00");
});

test("each read polls every 60 s and pauses while the tab is hidden", async () => {
  const api = await open();
  const counts = () => [requests(api, "API-07").length, requests(api, "API-IRR-02").length];
  expect(counts()).toEqual([1, 1]);
  await advance(60_000);
  expect(counts()).toEqual([2, 2]);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await advance(180_000);
  expect(counts()).toEqual([2, 2]);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await advance(0);
  expect(counts()).toEqual([3, 3]);
});
