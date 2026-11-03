// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// LineCard (#1135, design system §4.6) over #1122's fixture lines, with the clock at 10:42 on Monday 2026-11-02.
import { act, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import { Button } from "./button";
import { BEFORE_ENGINE_TEXT, LineCard, MASTER_TEXT, type LineCardProps } from "./line-card";
import { denver, renderUi, text } from "./test-render";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const status = (names: string | string[] = "in-band") => fixture(names, { now: NOW })["API-IRR-01"] as IrrigationStatus;
const lineOf = (from: IrrigationStatus, id: string) => from.lines.find((line) => line.line_id === id)!;

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

function card(line: IrrigationLine, props: Partial<LineCardProps> = {}) {
  renderUi(<LineCard line={line} engine today={TODAY} {...props} />, { role: "operator", writesEnabled: true });
  const article = document.querySelector<HTMLElement>(`.line-card[data-line="${line.line_id}"]`)!;
  return {
    article,
    head: text(article.querySelector(".line-card-head")),
    state: text(article.querySelector(".line-state")),
    lines: [...article.querySelectorAll(":scope > p")].map((node) => text(node)),
  };
}

test("an idle line: name, CLEAN and enabled; its state, last water and outcome, today against its cap, no check, History", () => {
  const { head, state, lines, article } = card(lineOf(status(), "L01"));
  expect(head).toBe("L01 Drip • WallClean Enabled: On");
  expect(state).toBe("Idle");
  // The fixture's "07:31" three days back, in MDT.
  expect(lines).toEqual(["Idle", expect.stringMatching(/^Last water Fri \d\d:\d\d, 12 min · ok$/), "Today 0 of 60 min", "No check recorded"]);
  const history = article.querySelector("a")!;
  expect([history.getAttribute("href"), text(history)]).toEqual(["/activity?target=L01", "History of L01 ›"]);
});

test("a running line: RUNNING with its cause, schedule and the time left, counting down", () => {
  const { state } = card(lineOf(status("l01-running"), "L01"));
  expect(state).toBe("Running · run now · 3:00 left");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(text(document.querySelector(".line-state"))).toBe("Running · run now · 2:59 left");
  cleanup();
  const scheduled = { ...lineOf(status(), "L01"), state: { code: "running", cause: "schedule", slot_id: 11, reason: null, ends_at: new Date(Date.now() + 40_000).toISOString(), observed_at: denver("10:30") } };
  expect(card(scheduled, { scheduleLabels: new Map([[11, "Wall MWF"]]) }).state).toBe("Running · schedule · Wall MWF · 0:40 left");
  cleanup();
  expect(card(scheduled).state).toBe("Running · schedule · slot 11 · 0:40 left");
});

test("L05 and L06 read Reserved with DISABLED; L11 reads Climate only with no controls", () => {
  const reserved = card(lineOf(status("reserved-lines"), "L05"));
  expect(reserved.head).toBe("L05 Drip • CenterClean Disabled Reserved");
  // DISABLED is in the head once, not again as the state.
  expect(reserved.article.querySelector(".line-state")).toBeNull();
  cleanup();
  const fog = card(lineOf(status(), "L11"), { actions: <Button write>Run L11</Button> });
  expect(fog.head).toBe("L11 FogClimate only");
  expect(fog.article.querySelector("button")).toBeNull();
  expect(fog.lines).toEqual(["Idle", expect.stringMatching(/^Last water today 05:10, 6 min$/), "Today 6 min"]);
  cleanup();
  // Any other line holds the controls #1136 and #1182 add.
  expect(card(lineOf(status(), "L01"), { actions: <Button write>Run L01</Button> }).article.querySelector("button")?.textContent).toBe("Run L01");
});

test("before the irrigation firmware a card shows its relay and says live job state comes with the firmware", () => {
  const idle = card(lineOf(status("before-engine"), "L01"), { engine: false });
  expect(idle.state).toMatch(/^Relay Off since \d\d:\d\d$/);
  expect(idle.lines).toContain(BEFORE_ENGINE_TEXT);
  expect(BEFORE_ENGINE_TEXT).toBe("Live job state arrives with the irrigation firmware");
  cleanup();
  expect(card(lineOf(status(["l01-running", "before-engine"]), "L01"), { engine: false }).state).toBe("Relay On since 10:30");
  cleanup();
  // Reserved lines read DISABLED before the engine too.
  expect(card(lineOf(status(["before-engine", "reserved-lines"]), "L06"), { engine: false }).head).toBe("L06 Drip • Center (Fert.)Fert Disabled Reserved");
});

test("L02's card carries L03, the fertilizer master, as a sub-row with its state and today's minutes", () => {
  const from = status("schedules-paused");
  const { article } = card(lineOf(from, "L02"), { master: lineOf(from, "L03") });
  const sub = article.querySelector(".line-subrow")!;
  expect(sub.getAttribute("data-line")).toBe("L03");
  expect([...sub.querySelectorAll("p")].map((node) => text(node))).toEqual([`Fert master L03 · ${MASTER_TEXT}`, "Disabled · Today 0 min"]);
  expect(MASTER_TEXT).toBe("opens the injector line from the tank with each feed");
  expect(text(article.querySelector(".line-card-head"))).toBe("L02 Drip • Wall (Fert.)Fert Enabled: On");
  // The fixture's "09:10" two days back, in MDT.
  expect(text(article)).toMatch(/Checked by jason Oct 31, \d\d:\d\d/);
});

test("a latched mister says so with its end; a back-off is quieter", () => {
  const latched = card(lineOf(status("mister-fault"), "L04"));
  expect(latched.state).toBe("Can't run now");
  const fault = latched.article.querySelector(".line-fault")!;
  expect([text(fault), fault.className]).toEqual(["Faulted: no misting until midnight (watchdog)", "line-fault fault"]);
  cleanup();
  const backing = card({ ...lineOf(status(), "L07"), fault: "backing_off", fault_until: denver("13:20") });
  expect([text(backing.article.querySelector(".line-fault")), backing.article.querySelector(".line-fault")?.className]).toEqual(["Backing off until 13:20", "line-fault line-fault-warn"]);
});

test("paused, waiting and unknown read in words; unknown only because the API says so", () => {
  const paused = { paused_until: denver("14:00"), paused_by: { source: "dashboard", name: "jason" }, pause_reason: "rain" };
  const base = lineOf(status(), "L04");
  expect(card({ ...base, ...paused, state: { ...base.state, code: "paused" } }).state).toBe("Paused until 14:00 · VPD misting continues · by jason: rain");
  cleanup();
  // VPD misting runs on a paused zone: the pause shows beside the running state.
  const misting = card({ ...base, ...paused, state: { ...base.state, code: "running", cause: "vpd", ends_at: null } });
  expect(misting.state).toBe("Running · VPD");
  expect(misting.lines).toContain("Paused until 14:00 · VPD misting continues · by jason: rain");
  cleanup();
  expect(card({ ...base, state: { ...base.state, code: "waiting", reason: "waiting for the manifold" } }).state).toBe("Waiting · waiting for the manifold");
  cleanup();
  expect(card(lineOf(status("controller-not-reporting"), "L01")).state).toBe("Unknown · 3 days ago");
});

test("a card reads its program state: REQUESTED with the revision, CONFIRMED by the hash, the reject in words, and before the engine why it waits; fog has none", () => {
  const rev = status();
  const sync = (line: IrrigationLine, props: Partial<LineCardProps> = {}) => {
    const { article } = card(line, { program: rev.program, ...props });
    const node = article.querySelector(".line-sync");
    const words = [node?.getAttribute("data-sync"), text(node)];
    cleanup();
    return words;
  };
  expect(sync(lineOf(rev, "L01"))).toEqual(["confirmed", "Confirmed"]);
  const sending = { ...rev.program, state: "requested", revision: 43 };
  expect(sync({ ...lineOf(rev, "L01"), sync: "requested" }, { program: sending })).toEqual(["requested", "Requestedrev 43 sending"]);
  expect(sync({ ...lineOf(rev, "L01"), sync: "requested" }, { program: undefined })).toEqual(["requested", "RequestedSending"]);
  const refused = { ...rev.program, state: "not_confirmed", revision: 43, reject_code: "bad_line" };
  expect(sync({ ...lineOf(rev, "L01"), sync: "not_confirmed" }, { program: refused })).toEqual(["not_confirmed", "Not confirmedNot confirmed: the controller refused a line's settings · retrying at 1, 5 and 15 min"]);
  const before = status("before-engine");
  expect(sync(lineOf(before, "L01"), { engine: false, program: before.program })).toEqual(["requested", "RequestedApplies with the irrigation firmware"]);
  expect(card(lineOf(rev, "L11")).article.querySelector(".line-sync")).toBeNull();
});

test("a pause reads before the engine too, since it is stored; fog, which a pause of every line leaves alone, never reads paused", () => {
  const pause = { paused_until: denver("14:00"), paused_by: null, pause_reason: null };
  const before = status("before-engine");
  expect(card({ ...lineOf(before, "L04"), ...pause }, { engine: false }).lines).toContain("Paused until 14:00 · VPD misting continues");
  cleanup();
  // Whatever code the API gives it: the state line shows the relay until the engine.
  const coded = lineOf(before, "L04");
  expect(card({ ...coded, ...pause, state: { ...coded.state, code: "paused" } }, { engine: false }).lines).toContain("Paused until 14:00 · VPD misting continues");
  cleanup();
  expect(text(card({ ...lineOf(status(), "L11"), ...pause }).article)).not.toContain("Paused");
});
