// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The schedule editor (#1137, screens SCR-04, redesign §6.5) on the Schedules view against #1122's fixture API in
// process, at 10:42 America/Denver on Monday 2026-11-02: the fields per line kind, the draft on the day timeline and
// against the caps, the refusals and waits read out before Save, Save with the one-time check, the refusals in words,
// REQUESTED to CONFIRMED on the program hash in the rules list and the status line, and delete.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { UUID, advance, requests, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import { PHYSICAL_LINES, SCHEDULE_LINES, physicalOf } from "../lib/water";
import type { WriteError } from "../lib/writes";
import Activity from "../routes/activity";
import ActivityLog from "../routes/activity-log";
import Water from "../routes/water";
import WaterSchedules from "../routes/water-schedules";
import { AppShell } from "./app-shell";
import { GONE_TEXT, refusalWords } from "./schedule-editor";
import { text } from "./test-render";

const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
const NEW = "/water/schedules?schedule=new";

function Where() {
  const { pathname, search } = useLocation();
  return <span data-testid="where">{`${pathname}${search}`}</span>;
}
function open(path: string, options: FixtureOptions = {}, before?: (api: ReturnType<typeof startFixtureApi>) => void) {
  const api = startFixtureApi(options);
  before?.(api);
  const Stub = createRoutesStub([{
    path: "/", Component: () => <><AppShell build={BUILD} authError={null} /><Where /></>,
    children: [
      { path: "water", Component: Water, children: [{ path: "schedules", Component: WaterSchedules }] },
      { path: "activity", Component: Activity },
      { path: "activity/log", Component: ActivityLog },
    ],
  }]);
  render(<Stub initialEntries={[path]} />);
  return api;
}
afterEach(() => { cleanup(); stopFixtureApi(); });

const drawer = () => document.querySelector<HTMLDialogElement>("dialog.drawer");
const inDrawer = () => within(drawer()!);
const where = () => screen.getByTestId("where").textContent;
const change = (label: string, value: string) => fireEvent.change(inDrawer().getByLabelText(label), { target: { value } });
/** #1336: the physical line, then its variant when the line has two. */
const setLine = (id: (typeof SCHEDULE_LINES)[number]) => {
  const { index, variant } = physicalOf(id)!;
  change("Line", String(index));
  if (PHYSICAL_LINES[index].fertilized) change("Variant", variant === "fertilized" ? "1" : "0");
};
const NAMES = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" } as const;
/** Moves the day chips to `days`; a new schedule starts on today's weekday, Monday. */
function setDays(...days: (keyof typeof NAMES)[]) {
  const chip = (day: keyof typeof NAMES) => inDrawer().getByRole("button", { name: NAMES[day] });
  for (const day of days) if (chip(day).getAttribute("aria-pressed") === "false") fireEvent.click(chip(day));
  for (const day of Object.keys(NAMES) as (keyof typeof NAMES)[]) if (!days.includes(day) && chip(day).getAttribute("aria-pressed") === "true") fireEvent.click(chip(day));
}
const setSunrise = (minutes: number) => change("Minutes from sunrise (negative is before)", String(minutes));
const save = async () => {
  fireEvent.click(inDrawer().getByRole("button", { name: "Save" }));
  await advance(0);
};
/** The day timeline's blocks on `line`, the day it shows (the draft's next day while the editor is open). */
const day = () => document.querySelector<HTMLElement>(".sch-day")!;
const shownDay = () => text(day().querySelector("h2"));
const blocks = (line: string) => [...day().querySelectorAll<HTMLElement>(`.sch-row[data-lines~="${line}"] .sch-block:not(.sch-part)`)].map((block) => ({
  name: (block.getAttribute("aria-label") ?? block.getAttribute("title") ?? "").replaceAll("\u00a0", " "), draft: block.classList.contains("sch-draft"),
  hatched: block.classList.contains("sch-skipped") || block.classList.contains("sch-blocked"), capped: block.classList.contains("sch-capped"), conflict: block.classList.contains("sch-conflict"),
}));
/** A rule in the rules list: its one pill and its meta line. */
const rule = (slot: number) => document.querySelector<HTMLElement>(`.sch-rules [data-slot="${slot}"]`);
const pill = (slot: number) => text(rule(slot)?.querySelector(".object-row-pill"));
const ruleMeta = (slot: number) => text(rule(slot)?.querySelector(".object-row-meta"));
const checks = () => [...drawer()!.querySelectorAll(".sch-checks li")].map((node) => text(node));
const preview = () => text(drawer()!.querySelector(".schedule-preview"));
const previewLine = (id: string) => text(drawer()!.querySelector(`.schedule-preview-line[data-line="${id}"]`));
const banner = () => text(document.querySelector("[data-program]"));
const count = () => text(document.querySelector(".sch-count-words"));
const errors = () => [...drawer()!.querySelectorAll(".field-error")].map((node) => text(node));
/** L01, Mon, Wed, Fri at sunrise + 60 for 12 min: the acceptance's schedule. */
async function fillWallMwf() {
  await advance(0);
  setDays("mon", "wed", "fri");
  setSunrise(60);
  change("Run", "12");
  change("Label (optional)", "Wall MWF");
}

// ---------------------------------------------------------------------------------------------------- the acceptance's flows
test("creating L01 Mon, Wed, Fri at sunrise + 60 for 12 min previews against the 60 min cap, then reads REQUESTED, then CONFIRMED on the hash, and Activity lists it", async () => {
  const api = open(NEW);
  await fillWallMwf();
  // The draft draws dashed on the timeline of the next day it runs (today), and the drawer reads its totals; nothing sent yet.
  expect(shownDay()).toBe("Monday, Nov 2 · today");
  expect(blocks("L01")).toEqual([{ name: "Wall drip · Wall MWF, Mon 07:35, 12 min, draft, not saved, planned", draft: true, hatched: false, capped: false, conflict: false }]);
  expect(previewLine("L01")).toBe("Wall drip Mon 12 · Wed 12 · Fri 12 minWall drip cap 60 min a day");
  expect(checks()).toEqual([]);
  expect(writes(api)).toEqual([]);
  expect(text(drawer()!.querySelector(".field-resolved"))).toBe("→ 07:35 today");
  expect(text(drawer()!.querySelector(".schedule-count"))).toBe("3 of 24 schedules");
  await save();
  // One create with every field and manual, and the check left alone.
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-03"]);
  expect(writes(api)[0].body).toEqual({
    line_id: "L01", label: "Wall MWF", enabled: true, days: ["mon", "wed", "fri"], start: { kind: "sunrise", offset_min: 60 }, on_s: 720, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0,
    degraded_only: false, manual: true,
  });
  expect(writes(api)[0].idempotency_key).toMatch(UUID);
  // The drawer closes; the rule and the status line read REQUESTED, and the rules list counts it.
  expect(drawer()).toBeNull();
  expect(where()).toBe("/water/schedules");
  // The one pill is REQUESTED (redesign §4.4's priority), so MANUAL moves into the meta line meanwhile.
  expect([pill(4), ruleMeta(4)]).toEqual(["Requested", "manual · changed by jason 0 s ago · rev 43 sending"]);
  expect(banner()).toBe("Program rev 42 on controller · rev 43 sending Requested");
  expect(count()).toBe("4 of 24 schedules");
  // API-IRR-02 and API-IRR-01 read every 5 s; the third read since the write carries the new hash: CONFIRMED, for 10 minutes.
  await advance(5_000);
  expect(pill(4)).toBe("Requested");
  await advance(5_000);
  await advance(5);
  // CONFIRMED is news for 10 minutes (D-UX-07), but MANUAL outranks it: the meta says confirmed meanwhile.
  expect(pill(4)).toBe("Manual");
  expect(ruleMeta(4)).toMatch(/ · confirmed$/);
  await advance(5_000);
  expect(banner()).toMatch(/^Program rev 43 on controller · confirmed \d+ s ago$/);
  // Activity's Log lists the create under the operator's name.
  fireEvent.click(screen.getAllByRole("link", { name: /^Activity/ })[0]);
  await advance(0);
  fireEvent.click(within(screen.getByRole("navigation", { name: "Activity views" })).getByRole("link", { name: "Log" }));
  await advance(0);
  const rows = [...document.querySelectorAll("main li, main tr, main article")].filter((node) => text(node).includes("Added schedule"));
  expect(rows.length).toBeGreaterThan(0);
  expect(text(rows[0])).toContain("Jason · dashboard");
  expect(api.respond({ url: "/v1/activity" }).body.items.find((row: { action: string }) => row.action === "irrigation.schedule.create")).toMatchObject({ actor: { source: "dashboard", name: "jason" }, target: "4", state: "confirmed" });
});

test("a 25th schedule reads as the refusal it will be before Save, is refused above Save, and the drawer stays open with nothing else sent", async () => {
  const api = open(NEW, { fixture: "schedules-full" });
  await fillWallMwf();
  expect(text(drawer()!.querySelector(".schedule-count"))).toBe("24 of 24 schedules");
  expect(checks()).toContain("This would be the 25th schedule; the controller holds 24");
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-03"]);
  expect(drawer()).not.toBeNull();
  expect(where()).toBe(NEW);
  expect(errors()).toEqual(["The controller holds 24 schedules; delete one first"]);
  // Above the actions, not beside a field.
  expect(drawer()!.querySelector(".field .field-error")).toBeNull();
  expect(drawer()!.querySelector(".drawer-body > .field-error")).not.toBeNull();
});

test("two L01 schedules 20 min apart read too close as it is typed, the draft outlined in fault; Save is refused with too_close beside Start", async () => {
  const api = open(NEW, { fixture: "l01-schedule" });
  await fillWallMwf();
  setSunrise(80);
  expect(checks()).toContain("Starts 20 min after Wall MWF on Wall drip: schedules on one line need 30 min");
  expect(blocks("L01").find((block) => block.draft)?.conflict).toBe(true);
  // Save stays open: the server decides.
  await save();
  expect(drawer()).not.toBeNull();
  expect(text(drawer()!.querySelector(".time-field .field-error"))).toBe("Another Wall drip schedule starts within 30 min of this one");
  expect(drawer()!.querySelector(".time-field input")?.getAttribute("aria-invalid")).toBe("true");
  expect(writes(api)).toHaveLength(1);
  // Far enough apart it saves, with one more request under its own key.
  setSunrise(120);
  expect(checks()).toEqual([]);
  await save();
  expect(drawer()).toBeNull();
  const sent = writes(api);
  expect(sent.map((row) => row.operationId)).toEqual(["API-IRR-03", "API-IRR-03"]);
  expect(new Set(sent.map((row) => row.idempotency_key)).size).toBe(2);
});

test("a mist schedule on L07 with Schedule off saves, and its blocks read \"Skipped until Schedule is on\"", async () => {
  const api = open(NEW, { fixture: "l07-schedule-off" });
  await advance(0);
  setLine("L07");
  setDays("tue");
  change("Start: at a clock time or from the sun", "clock");
  change("Clock time (HH:MM)", "14:00");
  // The draft reads as its saved blocks will: hatched, with why, on Tuesday's timeline, and counted in no total.
  expect(shownDay()).toBe("Tuesday, Nov 3");
  expect(blocks("L07")).toEqual([{ name: "South mister, Tue 14:00, 4 × 45 s, draft, not saved, Skipped until Schedule is on", draft: true, hatched: true, capped: false, conflict: false }]);
  expect(preview()).toContain("Skipped until Schedule is on");
  expect(previewLine("L07")).toBe("South mister no minutes plannedSouth: 0 of 30 min for schedules; 15 kept for VPD");
  await save();
  expect(writes(api)[0].body).toMatchObject({ line_id: "L07", days: ["tue"], start: { kind: "clock", clock: "14:00" }, on_s: 45, count: 4, gap_s: 60, degraded_only: false });
  expect(drawer()).toBeNull();
  expect(pill(4)).toBe("Requested");
});

test("a degraded_only draft follows VPD control: on L04 \"only if VPD sensing is degraded\" in the fallback's colour, on L07 \"Skipped: VPD control is off\"", async () => {
  open(NEW);
  await advance(0);
  setLine("L04");
  setDays("tue");
  setSunrise(15);
  // Without the box, a mist schedule on L04 (VPD on, Schedule off, as seeded) is skipped.
  expect(blocks("L04").find((block) => block.draft)?.name).toMatch(/Skipped until Schedule is on$/);
  fireEvent.click(inDrawer().getByLabelText("Only if VPD sensing is degraded"));
  // A fallback window, not a planned run: it runs only if VPD sensing fails (#1308 visual QA 6).
  expect(blocks("L04").find((block) => block.draft)).toMatchObject({ name: "Center mister, Tue 06:50, 4 × 45 s, draft, not saved, fallback, only if VPD sensing is degraded", hatched: false });
  expect(day().querySelector('.sch-row[data-line="L04"] .sch-draft')?.className).toContain("sch-eq-heat1");
  expect(preview()).toContain("only if VPD sensing is degraded");
  expect(previewLine("L04")).toBe("Center mister no minutes plannedCenter: 0 of 60 min for schedules; 60 kept for VPD");
  setLine("L07");
  expect(blocks("L07")).toEqual([{ name: "South mister, Tue 06:50, 4 × 45 s, draft, not saved, Skipped: VPD control is off", draft: true, hatched: true, capped: false, conflict: false }]);
  expect(preview()).toContain("Skipped: VPD control is off");
});

test("Let Iris change this starts off (manual), ticking it saves manual: false, and it starts off again on an edit of a schedule Iris may adjust", async () => {
  const api = open(NEW);
  await fillWallMwf();
  const manual = () => inDrawer().getByLabelText("Let Iris change this") as HTMLInputElement;
  expect(manual().checked).toBe(false);
  fireEvent.click(manual());
  await save();
  expect(writes(api)[0].body).toMatchObject({ manual: false });
  // The rule says Iris may adjust it.
  expect(ruleMeta(4)).toMatch(/^Iris may adjust · /);
  cleanup(); stopFixtureApi();
  const again = open("/water/schedules?schedule=13", { fixture: "schedules-paused" });
  await advance(0);
  expect(manual().checked).toBe(false);
  await save();
  expect(writes(again)[0].body).toMatchObject({ manual: true });
  expect(writes(again)[0].operationId).toBe("API-IRR-04");
});

test("an L02 feed shows FERT and the partner's note, sends prewet_s and flush_s, counts 6 min on L01's cap, and draws its prewet and flush on L01's row", async () => {
  const api = open(NEW, { fixture: "l02-enabled" });
  await advance(0);
  setLine("L02");
  setDays("sun");
  setSunrise(60);
  change("Feed", "5");
  change("Prewet on Wall drip", "120");
  change("Flush on Wall drip", "240");
  expect(text(inDrawer().getByRole("heading", { level: 2 }))).toBe("New schedule Fert");
  expect(preview()).toContain("Sun 5 min");
  expect(text(drawer()!.querySelector(".schedule-fert"))).toBe("Prewets on Wall drip, feeds through the fertilizer valve from the tank, then flushes on Wall drip");
  expect(previewLine("L01")).toBe("Wall drip Sun 6 minWall drip cap 60 min a day");
  expect(shownDay()).toBe("Sunday, Nov 8");
  expect(blocks("L02")).toEqual([{ name: "Wall drip (fertilized), Sun 07:35, 5 min, draft, not saved, planned", draft: true, hatched: false, capped: false, conflict: false }]);
  expect([...day().querySelectorAll('.sch-row[data-line="L01"] .sch-part')].map((node) => node.getAttribute("title")?.replaceAll("\u00a0", " "))).toEqual([
    "prewet 2 min before Wall drip (fertilized)'s 07:35 feed, planned", "flush 4 min after Wall drip (fertilized)'s 07:35 feed, planned",
  ]);
  await save();
  expect(writes(api)[0].body).toEqual({
    line_id: "L02", enabled: true, days: ["sun"], start: { kind: "sunrise", offset_min: 60 }, on_s: 300, count: 1, gap_s: 0, prewet_s: 120, flush_s: 240, degraded_only: false, manual: true,
  });
});

test("with L01 unchecked, ticking Checked sends one API-IRR-08 {checked, check_note} with no manual, then one API-IRR-03, each under its own key", async () => {
  const api = open(NEW);
  await fillWallMwf();
  fireEvent.click(inDrawer().getByLabelText("Checked: line flushed and emitters checked"));
  expect((inDrawer().getByLabelText("Note (optional)") as HTMLInputElement).maxLength).toBe(500);
  change("Note (optional)", "flushed 10 min");
  await save();
  const sent = writes(api);
  expect(sent.map((row) => row.operationId)).toEqual(["API-IRR-08", "API-IRR-03"]);
  expect(sent[0].path).toBe("/api/v1/irrigation/lines/L01");
  expect(sent[0].body).toEqual({ checked: true, check_note: "flushed 10 min" });
  expect(sent[1].body).toMatchObject({ line_id: "L01", manual: true });
  expect(sent.every((row) => UUID.test(row.idempotency_key ?? ""))).toBe(true);
  expect(sent[0].idempotency_key).not.toBe(sent[1].idempotency_key);
  // The API took the actor and the time from the session; the line's flag stays.
  const status = api.respond({ url: "/v1/irrigation" }).body;
  expect(status.lines.find((line: { line_id: string }) => line.line_id === "L01")).toMatchObject({ check: { checked_by: "jason", note: "flushed 10 min" }, manual: false, sync: "confirmed" });
});

test("a double tap on Save sends each request once", async () => {
  const api = open(NEW);
  await fillWallMwf();
  fireEvent.click(inDrawer().getByLabelText("Checked: line flushed and emitters checked"));
  const form = drawer()!.querySelector("form")!;
  await act(async () => { fireEvent.submit(form); fireEvent.submit(form); await vi.advanceTimersByTimeAsync(0); });
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-08", "API-IRR-03"]);
});

test("the box shows only for an enabled drip line with no check: never a mister, a checked or a disabled line, and it stays optional", async () => {
  const api = open(NEW);
  await advance(0);
  const box = () => inDrawer().queryByRole("checkbox", { name: /^Checked: / });
  expect(box()).not.toBeNull();
  setLine("L04");
  expect(box()).toBeNull();
  setLine("L02");
  expect(box()).toBeNull();
  // Ticked on L01, then another line and back: it starts unticked, since a check belongs to one line.
  setLine("L01");
  fireEvent.click(box()!);
  setLine("L05");
  setLine("L01");
  expect((box() as HTMLInputElement).checked).toBe(false);
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-03"]);
  cleanup(); stopFixtureApi();
  open(NEW, { fixture: "schedules-paused" });
  await advance(0);
  // L02 is checked in this fixture; its check prompt shows only without one.
  setLine("L02");
  expect(box()).toBeNull();
  cleanup(); stopFixtureApi();
  // A fertilizer drip enabled with no check asks too, in its own words.
  open(NEW, { fixture: "l02-enabled-unchecked" });
  await advance(0);
  setLine("L02");
  expect(box()?.closest("label")?.textContent).toBe("Checked: tank, injector and line checked, no leaks");
});

test("a refusal of the check keeps the drawer open and sends nothing else", async () => {
  const api = open(NEW, { fail: { "API-IRR-08": 422 } });
  await fillWallMwf();
  fireEvent.click(inDrawer().getByLabelText("Checked: line flushed and emitters checked"));
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-08"]);
  expect(drawer()).not.toBeNull();
  expect(errors()).toEqual(["Nothing by that name"]);
});

test("a check on record is not sent again when the schedule after it was refused", async () => {
  const api = open(NEW, { fixture: "l01-schedule" });
  await fillWallMwf();
  fireEvent.click(inDrawer().getByLabelText("Checked: line flushed and emitters checked"));
  setSunrise(80);
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-08", "API-IRR-03"]);
  expect(text(drawer()!.querySelector(".time-field .field-error"))).toBe("Another Wall drip schedule starts within 30 min of this one");
  expect(inDrawer().queryByRole("checkbox", { name: /^Checked: / })).toBeNull();
  setSunrise(120);
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-08", "API-IRR-03", "API-IRR-03"]);
  expect(drawer()).toBeNull();
});

test("before the engine the schedule stays REQUESTED with \"Applies with the irrigation firmware\" and never reads not confirmed", async () => {
  const api = open(NEW, { fixture: "before-engine" });
  await fillWallMwf();
  // The draft reads as a blocked occurrence does, and still counts against the cap.
  expect(blocks("L01")).toEqual([{ name: "Wall drip · Wall MWF, Mon 07:35, 12 min, draft, not saved, will not run, arrives with the irrigation firmware", draft: true, hatched: true, capped: false, conflict: false }]);
  expect(previewLine("L01")).toBe("Wall drip Mon 12 · Wed 12 · Fri 12 minWall drip cap 60 min a day");
  fireEvent.click(inDrawer().getByLabelText("Checked: line flushed and emitters checked"));
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-08", "API-IRR-03"]);
  expect([pill(4), ruleMeta(4)]).toEqual(["Requested", "manual · changed by jason 0 s ago · Applies with the irrigation firmware"]);
  const before = requests(api, "API-IRR-02").length;
  await advance(6 * 60_000);
  // The slow cadence, since it cannot settle before the firmware ships.
  expect(requests(api, "API-IRR-02").length - before).toBe(6);
  expect(pill(4)).toBe("Requested");
  expect(text(document.querySelector(".sch-rules"))).not.toMatch(/not confirmed/i);
});

test("with no matching hash the schedule reads \"Not confirmed\" with the reject in words", async () => {
  open(NEW, { confirm: false });
  await fillWallMwf();
  await save();
  await advance(15_000);
  expect([pill(4), ruleMeta(4)]).toEqual(["Not confirmed", expect.stringMatching(/ · Not confirmed: the controller refused a schedule · retrying at 1, 5 and 15 min$/)]);
  expect(banner()).toBe("Not confirmed: the controller refused a schedule · retrying at 1, 5 and 15 min");
});

test("the line's kind picks the fields, their min and max from API-IRR-02 bounds, and only the five physical lines, each with its wired variants (#1336), are offered", async () => {
  open(NEW);
  await advance(0);
  const options = (label: string) => [...(inDrawer().getByLabelText(label) as HTMLSelectElement).options].map((option) => text(option));
  // #1336: the five physical lines, then the chosen line's variants; the center mister's fertilized side is not wired.
  expect(options("Line")).toEqual(["Wall drip", "Center drip (reserved)", "Center mister", "South mister", "West mister"]);
  expect(options("Variant")).toEqual(["Clean", "Fertilized"]);
  expect((inDrawer().getByLabelText("Label (optional)") as HTMLInputElement).maxLength).toBe(60);
  expect((inDrawer().getByLabelText("Reason (optional)") as HTMLInputElement).maxLength).toBe(500);
  const labels = () => [...drawer()!.querySelectorAll(".number-field label")].map((node) => text(node));
  const hint = (label: string) => text(document.getElementById(`${inDrawer().getByLabelText(label).id}-hint`));
  const fertTitle = () => inDrawer().getByRole("heading", { level: 2 }).querySelector("[data-badge='fert']") !== null;
  setLine("L04");
  expect(options("Variant")).toEqual(["Clean"]);
  expect(hint("Variant")).toBe("Fertilized · not wired");
  setLine("L05");
  expect(options("Variant")).toEqual(["Clean (reserved)", "Fertilized (reserved)"]);
  setLine("L01");
  // drip: whole minutes inside on_s 10–7200.
  expect(labels()).toEqual(["Run"]);
  expect(hint("Run")).toBe("Allowed 1–120 min · step 1");
  expect(fertTitle()).toBe(false);
  // mister: pulses, with the mister_min_off_s hint and the fallback box.
  setLine("L04");
  expect(labels()).toEqual(["Pulse", "Pulses", "Gap between pulses"]);
  expect(hint("Pulse")).toBe("Allowed 10–90 s · step 1");
  expect(hint("Pulses")).toBe("Allowed 1–60 · step 1");
  expect(hint("Gap between pulses")).toBe("Allowed 30–600 s · step 1 · The controller never pulses closer than mister_min_off_s");
  expect(inDrawer().queryByLabelText("Only if VPD sensing is degraded")).not.toBeNull();
  // fert_drip: feed minutes, prewet and flush on its clean partner.
  setLine("L06");
  expect(labels()).toEqual(["Feed", "Prewet on Center drip", "Flush on Center drip"]);
  expect(hint("Prewet on Center drip")).toBe("Allowed 0–1800 s · step 1");
  expect(fertTitle()).toBe(true);
  expect(text(drawer()!.querySelector(".schedule-fert"))).toBe("Prewets on Center drip, feeds through the fertilizer valve from the tank, then flushes on Center drip");
  expect(inDrawer().queryByLabelText("Only if VPD sensing is degraded")).toBeNull();
  // fert_mister: the mister fields plus prewet and flush, and no fallback box.
  setLine("L10");
  expect(labels()).toEqual(["Pulse", "Pulses", "Gap between pulses", "Prewet on West mister", "Flush on West mister"]);
  expect(inDrawer().queryByLabelText("Only if VPD sensing is degraded")).toBeNull();
});

test("the client checks only min and max, beside the field, and sends nothing past them", async () => {
  const api = open(NEW);
  await advance(0);
  change("Run", "121");
  await save();
  expect(text(inDrawer().getByLabelText("Run").closest(".field")?.querySelector(".field-error"))).toBe("Must be 1–120 min");
  change("Run", "12.5");
  await save();
  expect(text(inDrawer().getByLabelText("Run").closest(".field")?.querySelector(".field-error"))).toBe("Must be whole minutes, 1–120 min");
  // A draft holding no number has nothing to preview.
  expect(preview()).toContain("Enter a valid start and a whole number in each field to preview it.");
  change("Run", "12");
  setLine("L04");
  change("Pulse", "5");
  await save();
  expect(text(inDrawer().getByLabelText("Pulse").closest(".field")?.querySelector(".field-error"))).toBe("Must be 10–90 s");
  // Another line's fields start without the last one's errors.
  setLine("L07");
  expect(errors()).toEqual([]);
  change("Pulse", "45");
  setSunrise(300);
  await save();
  expect(text(drawer()!.querySelector(".time-field .field-error"))).toBe("Must be −240–240 min");
  expect(writes(api)).toEqual([]);
});

test("a day past the cap reads so before Save, the block outlined amber, and Save still sends", async () => {
  const api = open(NEW);
  await advance(0);
  change("Run", "90");
  expect(preview()).toContain("Past the cap: the controller ends the run at the cap (Mon)");
  expect(checks()).toContain("Mon would reach 90 of 60 min: runs past the cap end at the cap");
  expect(blocks("L01")[0]).toMatchObject({ capped: true, name: expect.stringMatching(/ends at the daily cap$/) });
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-03"]);
  expect(drawer()).toBeNull();
});

test("a start inside another line's run on the same manifold reads as a wait, which saves", async () => {
  open(NEW, { fixture: ["l01-schedule", "l02-enabled"] });
  await advance(0);
  setLine("L02");
  setDays("wed");
  change("Start: at a clock time or from the sun", "clock");
  change("Clock time (HH:MM)", "07:40");
  expect(checks()).toContain("Shares the drip manifold with Wall drip's 07:35 Wall MWF: it waits until 07:47");
});

test("the day shortcuts set every day or the weekdays, and an empty slot, a line's + Schedule and Duplicate prefill a new schedule", async () => {
  open(NEW);
  await advance(0);
  fireEvent.click(inDrawer().getByRole("button", { name: "Daily" }));
  expect(Object.values(NAMES).map((name) => inDrawer().getByRole("button", { name }).getAttribute("aria-pressed"))).toEqual(["true", "true", "true", "true", "true", "true", "true"]);
  fireEvent.click(inDrawer().getByRole("button", { name: "Weekdays" }));
  expect(inDrawer().getByRole("button", { name: "Weekdays" }).getAttribute("aria-pressed")).toBe("true");
  expect(inDrawer().getByRole("button", { name: "Saturday" }).getAttribute("aria-pressed")).toBe("false");
  cleanup(); stopFixtureApi();
  open("/water/schedules?schedule=new&line=L04&at=13%3A15&day=2026-11-05", { fixture: "schedules-paused" });
  await advance(0);
  expect((inDrawer().getByLabelText("Line") as HTMLSelectElement).selectedOptions[0].textContent).toBe("Center mister");
  expect((inDrawer().getByLabelText("Variant") as HTMLSelectElement).selectedOptions[0].textContent).toBe("Clean");
  expect((inDrawer().getByLabelText("Clock time (HH:MM)") as HTMLInputElement).value).toBe("13:15");
  expect(inDrawer().getByRole("button", { name: "Thursday" }).getAttribute("aria-pressed")).toBe("true");
  expect(shownDay()).toBe("Thursday, Nov 5");
  cleanup(); stopFixtureApi();
  open("/water/schedules?schedule=new&copy=12", { fixture: "schedules-paused" });
  await advance(0);
  expect([(inDrawer().getByLabelText("Label (optional)") as HTMLInputElement).value, (inDrawer().getByLabelText("Feed") as HTMLInputElement).value]).toEqual(["Weekly feed (copy)", "5"]);
  expect(text(inDrawer().getByRole("heading", { level: 2 }))).toBe("New schedule Fert");
});

test("a refusal reads in words beside its field: out_of_range with its unit, on_s in a drip's minutes, unknown_target beside Line or gone on an edit", () => {
  const error = (fields: Partial<WriteError>): WriteError => ({ status: 422, code: "out_of_range", field: null, min: null, max: null, message: "raw", ...fields });
  const plain = (refusal: ReturnType<typeof refusalWords>) => ({ ...refusal, message: refusal.message.replaceAll(" ", " ") });
  const mister = { lineId: "L07", kind: "mister", editing: false };
  const drip = { lineId: "L01", kind: "drip", editing: false };
  expect(plain(refusalWords(error({ field: "on_s", min: 10, max: 90 }), mister))).toEqual({ field: "on_s", message: "Must be 10–90 s" });
  expect(plain(refusalWords(error({ field: "on_s", min: 10, max: 7200 }), drip))).toEqual({ field: "on_s", message: "Must be 1–120 min" });
  expect(plain(refusalWords(error({ field: "count", min: 1, max: 60 }), mister))).toEqual({ field: "count", message: "Must be 1–60" });
  expect(plain(refusalWords(error({ field: "offset_min", min: -240, max: 240 }), drip))).toEqual({ field: "start", message: "Must be −240–240 min" });
  // As verdify_schemas names a start's parts.
  expect(plain(refusalWords(error({ field: "start.offset_min", min: -240, max: 240 }), drip))).toEqual({ field: "start", message: "Must be −240–240 min" });
  expect(refusalWords(error({ field: "start.clock", min: 0, max: 1439 }), drip).field).toBe("start");
  expect(refusalWords(error({ code: "too_close", field: "start" }), drip)).toEqual({ field: "start", message: "Another Wall drip schedule starts within 30 min of this one" });
  expect(refusalWords(error({ code: "too_many_schedules", message: "The controller holds 24 schedules; delete one first" }), drip)).toEqual({ field: null, message: "The controller holds 24 schedules; delete one first" });
  expect(refusalWords(error({ code: "unknown_target", field: "line_id", message: "Nothing by that name" }), drip)).toEqual({ field: "line_id", message: "Nothing by that name" });
  expect(refusalWords(error({ code: "unknown_target", field: "slot_id", message: "Nothing by that name" }), { ...drip, editing: true })).toEqual({ field: null, message: GONE_TEXT });
  expect(refusalWords(error({ code: "unknown_target", field: "line_id", message: "Nothing by that name" }), { ...drip, editing: true })).toEqual({ field: "line_id", message: "Nothing by that name" });
  expect(GONE_TEXT).toBe("This schedule no longer exists");
});

// ---------------------------------------------------------------------------------------------------- edit, disable, delete
test("Edit from a block's popover opens the stored schedule over it; Save sends every field and manual, and the draft replaces its blocks still ahead", async () => {
  const api = open("/water/schedules?day=2026-11-04", { fixture: "schedules-paused" });
  await advance(0);
  fireEvent.click(day().querySelector<HTMLElement>('.sch-row[data-line="L01"] .sch-block')!);
  await advance(0);
  fireEvent.click(within(document.querySelector<HTMLElement>("dialog.popover")!).getByRole("link", { name: "Edit schedule" }));
  await advance(0);
  expect(where()).toBe("/water/schedules?day=2026-11-04&block=11-1793802900&schedule=11");
  expect(text(inDrawer().getByRole("heading", { level: 2 }))).toBe("Edit schedule");
  expect((inDrawer().getByLabelText("Run") as HTMLInputElement).value).toBe("12");
  expect((inDrawer().getByLabelText("Label (optional)") as HTMLInputElement).value).toBe("Wall MWF");
  expect((inDrawer().getByLabelText("Minutes from sunrise (negative is before)") as HTMLInputElement).value).toBe("60");
  expect(inDrawer().getByRole("button", { name: "Friday" }).getAttribute("aria-pressed")).toBe("true");
  expect(inDrawer().getByRole("button", { name: "Tuesday" }).getAttribute("aria-pressed")).toBe("false");
  change("Run", "10");
  change("Reason (optional)", "Rain this week");
  // Wednesday's saved block gives way to the draft; Monday's has run and stays.
  expect(shownDay()).toBe("Wednesday, Nov 4");
  expect(blocks("L01")).toEqual([{ name: "Wall drip · Wall MWF, Wed 07:35, 10 min, draft, not saved, planned", draft: true, hatched: false, capped: false, conflict: false }]);
  expect(previewLine("L01")).toBe("Wall drip Wed 10 · Fri 10 · Sun 6 minWall drip cap 60 min a day");
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-04"]);
  expect(writes(api)[0].path).toBe("/api/v1/irrigation/schedules/11");
  expect(writes(api)[0].body).toEqual({
    line_id: "L01", label: "Wall MWF", enabled: true, days: ["mon", "wed", "fri"], start: { kind: "sunrise", offset_min: 60 }, on_s: 600, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0,
    degraded_only: false, manual: true, reason: "Rain this week",
  });
  // The editor closes onto the popover it opened from, which reads the change travelling.
  expect(drawer()).toBeNull();
  expect(where()).toBe("/water/schedules?day=2026-11-04&block=11-1793802900");
  expect(text(document.querySelector("dialog.popover"))).toContain("Requested");
  expect(pill(11)).toBe("Requested");
});

test("a stored run that is not whole minutes opens as it is and saves unchanged, so editing anything else keeps it; a new one must be whole minutes", async () => {
  // Iris may adjust this one: 690 s through MCP, which takes any on_s from 10 to 7200.
  const api = open("/water/schedules?schedule=11", { fixture: "schedules-paused" }, (fixtureApi) => fixtureApi.respond({ method: "PUT", url: "/v1/irrigation/schedules/11", body: { on_s: 690 } }));
  await advance(0);
  const run = () => inDrawer().getByLabelText("Run") as HTMLInputElement;
  expect(run().value).toBe("11.5");
  expect(previewLine("L01")).toContain("Wed 11.5");
  change("Run", "11.6");
  await save();
  expect(text(run().closest(".field")?.querySelector(".field-error"))).toBe("Must be whole minutes, 1–120 min");
  expect(drawer()).not.toBeNull();
  change("Run", "11.5");
  fireEvent.click(inDrawer().getByRole("switch", { name: "Enabled" }));
  await save();
  expect(drawer()).toBeNull();
  expect(writes(api).at(-1)?.body).toMatchObject({ on_s: 690, enabled: false });
});

test("a mister and a feed open with their own fields filled in", async () => {
  open("/water/schedules?schedule=13", { fixture: "schedules-paused" });
  await advance(0);
  const value = (label: string) => (inDrawer().getByLabelText(label) as HTMLInputElement).value;
  expect([value("Pulse"), value("Pulses"), value("Gap between pulses")]).toEqual(["60", "8", "60"]);
  expect(previewLine("L04")).toBe("Center mister Tue 8 · Wed 8 · Thu 8 · Fri 8 · Sat 8 · Sun 8 minCenter: 8 of 60 min for schedules; 60 kept for VPD");
  cleanup(); stopFixtureApi();
  open("/water/schedules?schedule=12", { fixture: "schedules-paused" });
  await advance(0);
  expect([value("Feed"), value("Prewet on Wall drip"), value("Flush on Wall drip")]).toEqual(["5", "120", "240"]);
  expect(previewLine("L01")).toBe("Wall drip Wed 12 · Fri 12 · Sun 6 minWall drip cap 60 min a day");
});

test("disabling a schedule takes its blocks off the timeline; it stays muted under its line in the rules list, which reaches it to enable it again", async () => {
  const api = open("/water/schedules?schedule=11", { fixture: "schedules-paused" });
  await advance(0);
  fireEvent.click(inDrawer().getByRole("switch", { name: "Enabled" }));
  expect(preview()).toContain("Disabled: this schedule will not run");
  await save();
  expect(writes(api)[0].body).toMatchObject({ enabled: false });
  expect(rule(11)?.className).toContain("is-muted");
  expect(ruleMeta(11)).toContain("disabled: will not run");
  fireEvent.click(within(rule(11)!).getByRole("link", { name: "Edit Wall MWF" }));
  await advance(0);
  expect(where()).toBe("/water/schedules?schedule=11");
  fireEvent.click(inDrawer().getByRole("switch", { name: "Enabled" }));
  await save();
  expect(writes(api)[1].body).toMatchObject({ enabled: true });
});

test("Delete is one tap with no dialog and carries the reason, a double tap sending one; the schedule leaves the rules and the status line reads REQUESTED", async () => {
  const api = open("/water/schedules?schedule=11", { fixture: "schedules-paused" });
  await advance(0);
  change("Reason (optional)", "Moved to the feed");
  const remove = inDrawer().getByRole("button", { name: "Delete" });
  await act(async () => { fireEvent.click(remove); fireEvent.click(remove); await vi.advanceTimersByTimeAsync(0); });
  expect(writes(api).map((row) => [row.operationId, row.path])).toEqual([["API-IRR-05", "/api/v1/irrigation/schedules/11?reason=Moved+to+the+feed"]]);
  expect(writes(api)[0].idempotency_key).toMatch(UUID);
  expect(document.querySelector("dialog")).toBeNull();
  expect(rule(11)).toBeNull();
  expect(banner()).toBe("Program rev 42 on controller · rev 43 sending Requested");
  expect(count()).toBe("2 of 24 schedules");
  expect(api.respond({ url: "/v1/activity" }).body.items.find((row: { action: string }) => row.action === "irrigation.schedule.delete")).toMatchObject({ actor: { name: "jason" }, reason: "Moved to the feed" });
  // A new schedule has nothing to delete.
  fireEvent.click(screen.getByRole("link", { name: "+ New schedule" }));
  await advance(0);
  expect(where()).toBe(NEW);
  expect(inDrawer().queryByRole("button", { name: "Delete" })).toBeNull();
});

test("a schedule deleted elsewhere reads \"This schedule no longer exists\": from a stale URL, on Save and on Delete", async () => {
  open("/water/schedules?schedule=99", { fixture: "schedules-paused" });
  await advance(0);
  expect(errors()).toEqual([GONE_TEXT]);
  expect(inDrawer().queryByRole("button", { name: "Save" })).toBeNull();
  expect(document.querySelectorAll(".sch-draft")).toHaveLength(0);
  fireEvent.click(inDrawer().getByRole("button", { name: "Done" }));
  await advance(0);
  expect(drawer()).toBeNull();
  expect(where()).toBe("/water/schedules");
  cleanup(); stopFixtureApi();
  const api = open("/water/schedules?schedule=11", { fixture: "schedules-paused" });
  await advance(0);
  change("Run", "10");
  api.respond({ method: "DELETE", url: "/v1/irrigation/schedules/11" });
  await advance(60_000);
  // The form survives the poll that no longer lists it, and the server has the last word.
  expect(drawer()).not.toBeNull();
  await save();
  expect(errors()).toEqual([GONE_TEXT]);
  fireEvent.click(inDrawer().getByRole("button", { name: "Delete" }));
  await advance(0);
  expect(errors()).toEqual([GONE_TEXT]);
  expect(drawer()).not.toBeNull();
});

test("while the editor is open the timeline shows the next day the draft runs, and goes back to the chosen day when it closes", async () => {
  open("/water/schedules?day=2026-11-03&schedule=new", { fixture: "schedules-paused" });
  await advance(0);
  // Prefilled with the chosen day's weekday? No line or slot was chosen: today's weekday, Monday, so Monday shows.
  setDays("thu");
  expect(shownDay()).toBe("Thursday, Nov 5");
  fireEvent.click(inDrawer().getByRole("button", { name: "Cancel" }));
  await advance(0);
  expect(shownDay()).toBe("Tuesday, Nov 3");
});

test("Cancel and Escape close at once and leave the timeline as it was; focus goes back to the opener", async () => {
  const api = open("/water/schedules", { fixture: "schedules-paused" });
  await advance(0);
  const opener = screen.getByRole("link", { name: "+ New schedule" });
  opener.focus();
  fireEvent.click(opener);
  await advance(0);
  expect(document.activeElement).toBe(inDrawer().getByLabelText("Line"));
  fireEvent.keyDown(drawer()!, { key: "Escape" });
  await advance(0);
  expect(drawer()).toBeNull();
  expect(where()).toBe("/water/schedules");
  expect(document.querySelectorAll(".sch-draft")).toHaveLength(0);
  expect(document.activeElement).toBe(opener);
  // A rule's Edit takes focus back too.
  const edit = within(rule(11)!).getByRole("link", { name: "Edit Wall MWF" });
  edit.focus();
  fireEvent.click(edit);
  await advance(0);
  fireEvent.click(inDrawer().getByRole("button", { name: "Cancel" }));
  await advance(0);
  expect(document.activeElement).toBe(within(rule(11)!).getByRole("link", { name: "Edit Wall MWF" }));
  expect(writes(api)).toEqual([]);
});

test("a viewer sees no editor, New schedule or block menu, whatever the URL says; with changes off an operator sees the fields and Save disabled", async () => {
  open(NEW, { fixture: "viewer-session" });
  await advance(0);
  expect(drawer()).toBeNull();
  expect(screen.queryByRole("link", { name: "+ New schedule" })).toBeNull();
  expect(document.querySelector(".sch-add")).toBeNull();
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
  cleanup(); stopFixtureApi();
  const api = open("/water/schedules?schedule=2", { fixture: "writes-off" });
  await advance(0);
  expect(inDrawer().getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
  expect(inDrawer().getByRole("button", { name: "Delete" }).hasAttribute("disabled")).toBe(true);
  expect(drawer()!.querySelector("fieldset")?.disabled).toBe(true);
  expect(writes(api)).toEqual([]);
});
