// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The schedule editor (#1137, screens SCR-04) on the Schedules view against #1122's fixture API in process, at 10:42
// America/Denver on Monday 2026-11-02: the fields per line kind, the draft in the week and against the caps, Save with
// the one-time check, the refusals in words, REQUESTED to CONFIRMED on the program hash, and delete.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { UUID, advance, requests, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import { SCHEDULE_LINES } from "../lib/water";
import type { WriteError } from "../lib/writes";
import Activity from "../routes/activity";
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
const setLine = (id: (typeof SCHEDULE_LINES)[number]) => change("Line", String(SCHEDULE_LINES.indexOf(id)));
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
const week = () => document.querySelector<HTMLElement>(".week-table")!;
const cell = (line: string, day: string) => week().querySelector<HTMLElement>(`tr[data-line="${line}"] td[data-day="${day}"]`)!;
const blocks = (line: string, day: string) => [...cell(line, day).querySelectorAll<HTMLElement>(".week-block:not([hidden])")].map((block) => ({
  head: text(block.querySelector(".week-block-head")), draft: block.classList.contains("week-block-draft"), hatched: block.classList.contains("week-block-hatched"),
  notes: [...block.querySelectorAll(".week-block-label, .week-block-detail, .week-block-note")].map((node) => text(node)),
}));
const preview = () => text(drawer()!.querySelector(".schedule-preview"));
const previewLine = (id: string) => text(drawer()!.querySelector(`.schedule-preview-line[data-line="${id}"]`));
const banner = () => text(document.querySelector("[data-program]"));
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
  // PLANNED outlines in L01's row and the totals in the drawer; nothing sent yet.
  expect(blocks("L01", "2026-11-04")).toEqual([{ head: "07:35 12m Manual Planned", draft: true, hatched: false, notes: ["Wall MWF"] }]);
  expect(blocks("L01", "2026-11-02").map((block) => block.draft)).toEqual([true]);
  expect(blocks("L01", "2026-11-03")).toEqual([]);
  expect(previewLine("L01")).toBe("L01 Mon 12 · Wed 12 · Fri 12 minWall drip cap 60 min a day");
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
  // The drawer closes; the schedule's blocks and the banner read REQUESTED, and the head counts it.
  expect(drawer()).toBeNull();
  expect(where()).toBe("/water/schedules");
  expect(blocks("L01", "2026-11-04")).toEqual([{ head: "07:35 12m Manual Requested", draft: false, hatched: false, notes: ["Wall MWF", "rev 43 sending"] }]);
  expect(banner()).toBe("On controller: rev 42 · rev 43 sending Requested");
  expect(text(document.querySelector(".week-count"))).toBe("4 of 24 schedules");
  // API-IRR-02 and API-IRR-01 read every 5 s; the third read since the write carries the new hash.
  await advance(5_000);
  expect(blocks("L01", "2026-11-04")[0].head).toBe("07:35 12m Manual Requested");
  await advance(5_000);
  expect(blocks("L01", "2026-11-04")).toEqual([{ head: "07:35 12m Manual Confirmed", draft: false, hatched: false, notes: ["Wall MWF"] }]);
  await advance(5_000);
  expect(banner()).toMatch(/^On controller: rev 43 · Confirmed \d+ s ago$/);
  // Activity lists the create under the operator's name.
  fireEvent.click(screen.getAllByRole("link", { name: /^Activity/ })[0]);
  await advance(0);
  const rows = [...document.querySelectorAll("main li, main tr, main article")].filter((node) => text(node).includes("Added schedule"));
  expect(rows.length).toBeGreaterThan(0);
  expect(text(rows[0])).toContain("Jason · dashboard");
  expect(api.respond({ url: "/v1/activity" }).body.items.find((row: { action: string }) => row.action === "irrigation.schedule.create")).toMatchObject({ actor: { source: "dashboard", name: "jason" }, target: "4", state: "confirmed" });
});

test("a 25th schedule is refused above Save, as the refusal names no field, and the drawer stays open with nothing else sent", async () => {
  const api = open(NEW, { fixture: "schedules-full" });
  await fillWallMwf();
  expect(text(drawer()!.querySelector(".schedule-count"))).toBe("24 of 24 schedules");
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-03"]);
  expect(drawer()).not.toBeNull();
  expect(where()).toBe(NEW);
  expect(errors()).toEqual(["The controller holds 24 schedules; delete one first"]);
  // Above the actions, not beside a field.
  expect(drawer()!.querySelector(".field .field-error")).toBeNull();
  expect(drawer()!.querySelector(".drawer-body > .field-error")).not.toBeNull();
});

test("two L01 schedules 20 min apart are refused with too_close beside Start, naming the line", async () => {
  const api = open(NEW, { fixture: "l01-schedule" });
  await fillWallMwf();
  setSunrise(80);
  await save();
  expect(drawer()).not.toBeNull();
  expect(text(drawer()!.querySelector(".time-field .field-error"))).toBe("Another L01 schedule starts within 30 min of this one");
  expect(drawer()!.querySelector(".time-field input")?.getAttribute("aria-invalid")).toBe("true");
  expect(writes(api)).toHaveLength(1);
  // Far enough apart it saves, with one more request under its own key.
  setSunrise(120);
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
  // The draft reads as its saved blocks will: hatched, with why, and counted in no total.
  expect(blocks("L07", "2026-11-03")).toEqual([{ head: "14:00 4×45s Manual Planned", draft: true, hatched: true, notes: ["Skipped until Schedule is on"] }]);
  expect(preview()).toContain("Skipped until Schedule is on");
  expect(previewLine("L07")).toBe("L07 no minutes plannedSouth: 0 of 30 min for schedules; 15 kept for VPD");
  await save();
  expect(writes(api)[0].body).toMatchObject({ line_id: "L07", days: ["tue"], start: { kind: "clock", clock: "14:00" }, on_s: 45, count: 4, gap_s: 60, degraded_only: false });
  expect(drawer()).toBeNull();
  expect(blocks("L07", "2026-11-03")).toEqual([{ head: "14:00 4×45s Manual Requested", draft: false, hatched: true, notes: ["Skipped until Schedule is on", "rev 43 sending"] }]);
});

test("a degraded_only draft follows VPD control: on L04 unhatched \"only if VPD sensing is degraded\", on L07 \"Skipped: VPD control is off\"", async () => {
  open(NEW);
  await advance(0);
  setLine("L04");
  setDays("tue");
  setSunrise(15);
  // Without the box, a mist schedule on L04 (VPD on, Schedule off, as seeded) is skipped.
  expect(blocks("L04", "2026-11-03").find((block) => block.draft)?.notes).toEqual(["Skipped until Schedule is on"]);
  fireEvent.click(inDrawer().getByLabelText("Only if VPD sensing is degraded"));
  expect(blocks("L04", "2026-11-03").find((block) => block.draft)).toEqual({ head: "06:50 4×45s Manual Planned", draft: true, hatched: false, notes: ["only if VPD sensing is degraded"] });
  expect(preview()).toContain("only if VPD sensing is degraded");
  expect(previewLine("L04")).toBe("L04 no minutes plannedCenter: 0 of 60 min for schedules; 60 kept for VPD");
  setLine("L07");
  expect(blocks("L07", "2026-11-03")).toEqual([{ head: "06:50 4×45s Manual Planned", draft: true, hatched: true, notes: ["Skipped: VPD control is off"] }]);
  expect(preview()).toContain("Skipped: VPD control is off");
});

test("the Manual box starts ticked, unticking it saves manual: false, and it starts ticked again on an edit of a schedule Iris may adjust", async () => {
  const api = open(NEW);
  await fillWallMwf();
  const manual = () => inDrawer().getByLabelText("Manual: Iris skips this") as HTMLInputElement;
  expect(manual().checked).toBe(true);
  fireEvent.click(manual());
  expect(blocks("L01", "2026-11-04")[0].head).toBe("07:35 12m Iris may adjust Planned");
  await save();
  expect(writes(api)[0].body).toMatchObject({ manual: false });
  cleanup(); stopFixtureApi();
  const again = open("/water/schedules?schedule=13", { fixture: "schedules-paused" });
  await advance(0);
  expect(manual().checked).toBe(true);
  await save();
  expect(writes(again)[0].body).toMatchObject({ manual: true });
  expect(writes(again)[0].operationId).toBe("API-IRR-04");
});

test("an L02 feed shows FERT and the partner's note, sends prewet_s and flush_s, and counts 6 min on L01's cap", async () => {
  const api = open(NEW, { fixture: "l02-enabled" });
  await advance(0);
  setLine("L02");
  setDays("sun");
  setSunrise(60);
  change("Feed", "5");
  change("Prewet on L01", "120");
  change("Flush on L01", "240");
  expect(text(inDrawer().getByRole("heading", { level: 2 }))).toBe("New schedule Fert");
  expect(preview()).toContain("Sun 5 min");
  expect(text(drawer()!.querySelector(".schedule-fert"))).toBe("Prewets on L01, feeds through L03 from the tank, then flushes on L01");
  expect(previewLine("L01")).toBe("L01 Sun 6 minWall drip cap 60 min a day");
  expect(blocks("L02", "2026-11-08")).toEqual([{ head: "07:35 5m Fert Manual Planned", draft: true, hatched: false, notes: ["prewet 2 min · feed 5 min · flush 4 min"] }]);
  await save();
  expect(writes(api)[0].body).toEqual({
    line_id: "L02", enabled: true, days: ["sun"], start: { kind: "sunrise", offset_min: 60 }, on_s: 300, count: 1, gap_s: 0, prewet_s: 120, flush_s: 240, degraded_only: false, manual: true,
  });
});

test("with L01 unchecked, ticking Checked sends one API-IRR-08 {checked, check_note} with no manual, then one API-IRR-03, each under its own key", async () => {
  const api = open(NEW);
  await fillWallMwf();
  fireEvent.click(inDrawer().getByLabelText("Checked: line flushed, emitters checked"));
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
  fireEvent.click(inDrawer().getByLabelText("Checked: line flushed, emitters checked"));
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
  expect(box()?.closest("label")?.textContent).toBe("Checked: line, tank and injector checked, no leaks");
});

test("a refusal of the check keeps the drawer open and sends nothing else", async () => {
  const api = open(NEW, { fail: { "API-IRR-08": 422 } });
  await fillWallMwf();
  fireEvent.click(inDrawer().getByLabelText("Checked: line flushed, emitters checked"));
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-08"]);
  expect(drawer()).not.toBeNull();
  expect(errors()).toEqual(["Nothing by that name"]);
});

test("a check on record is not sent again when the schedule after it was refused", async () => {
  const api = open(NEW, { fixture: "l01-schedule" });
  await fillWallMwf();
  fireEvent.click(inDrawer().getByLabelText("Checked: line flushed, emitters checked"));
  setSunrise(80);
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-08", "API-IRR-03"]);
  expect(text(drawer()!.querySelector(".time-field .field-error"))).toBe("Another L01 schedule starts within 30 min of this one");
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
  expect(blocks("L01", "2026-11-04")).toEqual([{ head: "07:35 12m Manual Planned", draft: true, hatched: true, notes: ["Wall MWF", "arrives with the irrigation firmware"] }]);
  expect(previewLine("L01")).toBe("L01 Mon 12 · Wed 12 · Fri 12 minWall drip cap 60 min a day");
  fireEvent.click(inDrawer().getByLabelText("Checked: line flushed, emitters checked"));
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-08", "API-IRR-03"]);
  const read = () => blocks("L01", "2026-11-04");
  expect(read()).toEqual([{ head: "07:35 12m Manual Requested", draft: false, hatched: true, notes: ["Wall MWF", "Applies with the irrigation firmware"] }]);
  const before = requests(api, "API-IRR-02").length;
  await advance(6 * 60_000);
  // The slow cadence, since it cannot settle before the firmware ships.
  expect(requests(api, "API-IRR-02").length - before).toBe(6);
  expect(read()[0].head).toBe("07:35 12m Manual Requested");
  expect(text(document.querySelector(".schedule-week"))).not.toMatch(/not confirmed/i);
});

test("with no matching hash the schedule reads \"Not confirmed\" with the reject in words", async () => {
  open(NEW, { confirm: false });
  await fillWallMwf();
  await save();
  await advance(15_000);
  expect(blocks("L01", "2026-11-04")).toEqual([{ head: "07:35 12m Manual Not confirmed", draft: false, hatched: false, notes: ["Wall MWF", "Not confirmed: the controller refused a schedule · retrying at 1, 5 and 15 min"] }]);
  expect(banner()).toBe("Not confirmed: the controller refused a schedule · retrying at 1, 5 and 15 min");
});

// ---------------------------------------------------------------------------------------------------- the fields and the client's bounds
test("the line's kind picks the fields, their min and max from API-IRR-02 bounds, and only L01, L02 and L04–L10 are offered", async () => {
  open(NEW);
  await advance(0);
  const options = [...(inDrawer().getByLabelText("Line") as HTMLSelectElement).options].map((option) => text(option));
  expect(options).toEqual([
    "L01 Drip • Wall", "L02 Drip • Wall (Fert.)", "L04 Mister • Center", "L05 Drip • Center (reserved)", "L06 Drip • Center (Fert.) (reserved)", "L07 Mister • South Wall",
    "L08 Mister • South Wall (Fert.)", "L09 Mister • West Wall", "L10 Mister • West Wall (Fert.)",
  ]);
  expect((inDrawer().getByLabelText("Label (optional)") as HTMLInputElement).maxLength).toBe(60);
  expect((inDrawer().getByLabelText("Reason (optional)") as HTMLInputElement).maxLength).toBe(500);
  const labels = () => [...drawer()!.querySelectorAll(".number-field label")].map((node) => text(node));
  const hint = (label: string) => text(document.getElementById(`${inDrawer().getByLabelText(label).id}-hint`));
  const fertTitle = () => inDrawer().getByRole("heading", { level: 2 }).querySelector("[data-badge='fert']") !== null;
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
  expect(labels()).toEqual(["Feed", "Prewet on L05", "Flush on L05"]);
  expect(hint("Prewet on L05")).toBe("Allowed 0–1800 s · step 1");
  expect(fertTitle()).toBe(true);
  expect(text(drawer()!.querySelector(".schedule-fert"))).toBe("Prewets on L05, feeds through L03 from the tank, then flushes on L05");
  expect(inDrawer().queryByLabelText("Only if VPD sensing is degraded")).toBeNull();
  // fert_mister: the mister fields plus prewet and flush, and no fallback box.
  setLine("L10");
  expect(labels()).toEqual(["Pulse", "Pulses", "Gap between pulses", "Prewet on L09", "Flush on L09"]);
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

test("a day past the cap reads so, and Save still sends", async () => {
  const api = open(NEW);
  await advance(0);
  change("Run", "90");
  expect(preview()).toContain("Past the cap: the controller ends the run at the cap (Mon)");
  expect(blocks("L01", "2026-11-02")[0].notes).toEqual(["Past the cap: the controller ends the run at the cap"]);
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-03"]);
  expect(drawer()).toBeNull();
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
  expect(refusalWords(error({ code: "too_close", field: "start" }), drip)).toEqual({ field: "start", message: "Another L01 schedule starts within 30 min of this one" });
  expect(refusalWords(error({ code: "too_many_schedules", message: "The controller holds 24 schedules; delete one first" }), drip)).toEqual({ field: null, message: "The controller holds 24 schedules; delete one first" });
  expect(refusalWords(error({ code: "unknown_target", field: "line_id", message: "Nothing by that name" }), drip)).toEqual({ field: "line_id", message: "Nothing by that name" });
  expect(refusalWords(error({ code: "unknown_target", field: "slot_id", message: "Nothing by that name" }), { ...drip, editing: true })).toEqual({ field: null, message: GONE_TEXT });
  expect(refusalWords(error({ code: "unknown_target", field: "line_id", message: "Nothing by that name" }), { ...drip, editing: true })).toEqual({ field: "line_id", message: "Nothing by that name" });
  expect(GONE_TEXT).toBe("This schedule no longer exists");
});

// ---------------------------------------------------------------------------------------------------- edit, disable, delete
test("Edit on a block opens the stored schedule in the drawer; Save sends every field and manual, and the draft replaces its blocks still ahead", async () => {
  const api = open("/water/schedules", { fixture: "schedules-paused" });
  await advance(0);
  const block = () => cell("L01", "2026-11-04").querySelector<HTMLElement>(".week-block")!;
  fireEvent.click(block().querySelector("summary")!);
  fireEvent.click(within(block()).getByRole("link", { name: /^Edit/ }));
  await advance(0);
  expect(where()).toBe("/water/schedules?schedule=11");
  expect(text(inDrawer().getByRole("heading", { level: 2 }))).toBe("Edit schedule");
  expect((inDrawer().getByLabelText("Run") as HTMLInputElement).value).toBe("12");
  expect((inDrawer().getByLabelText("Label (optional)") as HTMLInputElement).value).toBe("Wall MWF");
  expect((inDrawer().getByLabelText("Minutes from sunrise (negative is before)") as HTMLInputElement).value).toBe("60");
  expect(inDrawer().getByRole("button", { name: "Friday" }).getAttribute("aria-pressed")).toBe("true");
  expect(inDrawer().getByRole("button", { name: "Tuesday" }).getAttribute("aria-pressed")).toBe("false");
  change("Run", "10");
  change("Reason (optional)", "Rain this week");
  // Monday's has run and stays; Wednesday's and Friday's are the draft's.
  expect(blocks("L01", "2026-11-02").map((row) => row.draft)).toEqual([false]);
  expect(blocks("L01", "2026-11-04")).toEqual([{ head: "07:35 10m Manual Planned", draft: true, hatched: false, notes: ["Wall MWF"] }]);
  expect(previewLine("L01")).toBe("L01 Wed 10 · Fri 10 · Sun 6 minWall drip cap 60 min a day");
  await save();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-04"]);
  expect(writes(api)[0].path).toBe("/api/v1/irrigation/schedules/11");
  expect(writes(api)[0].body).toEqual({
    line_id: "L01", label: "Wall MWF", enabled: true, days: ["mon", "wed", "fri"], start: { kind: "sunrise", offset_min: 60 }, on_s: 600, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0,
    degraded_only: false, manual: true, reason: "Rain this week",
  });
  expect(drawer()).toBeNull();
  expect(blocks("L01", "2026-11-04")[0].head).toBe("07:35 10m Manual Requested");
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
  expect(previewLine("L04")).toBe("L04 Tue 8 · Wed 8 · Thu 8 · Fri 8 · Sat 8 · Sun 8 minCenter: 8 of 60 min for schedules; 60 kept for VPD");
  cleanup(); stopFixtureApi();
  open("/water/schedules?schedule=12", { fixture: "schedules-paused" });
  await advance(0);
  expect([value("Feed"), value("Prewet on L01"), value("Flush on L01")]).toEqual(["5", "120", "240"]);
  expect(previewLine("L01")).toBe("L01 Wed 12 · Fri 12 · Sun 6 minWall drip cap 60 min a day");
});

test("disabling a schedule takes it out of the week, and the line under the week reaches it to enable it again", async () => {
  const api = open("/water/schedules?schedule=11", { fixture: "schedules-paused" });
  await advance(0);
  fireEvent.click(inDrawer().getByRole("switch", { name: "Enabled" }));
  expect(preview()).toContain("Disabled: this schedule will not run");
  expect(blocks("L01", "2026-11-04")).toEqual([]);
  await save();
  expect(writes(api)[0].body).toMatchObject({ enabled: false });
  expect(blocks("L01", "2026-11-04")).toEqual([]);
  expect(text(document.querySelector(".week-disabled"))).toBe("Disabled, so not in the week: L01 Wall MWF");
  fireEvent.click(within(document.querySelector<HTMLElement>(".week-disabled")!).getByRole("link", { name: "L01 Wall MWF" }));
  await advance(0);
  expect(where()).toBe("/water/schedules?schedule=11");
  fireEvent.click(inDrawer().getByRole("switch", { name: "Enabled" }));
  await save();
  expect(writes(api)[1].body).toMatchObject({ enabled: true });
});

test("Delete is one tap with no dialog and carries the reason, a double tap sending one; the schedule leaves the week and the banner reads REQUESTED", async () => {
  const api = open("/water/schedules?schedule=11", { fixture: "schedules-paused" });
  await advance(0);
  change("Reason (optional)", "Moved to the feed");
  const remove = inDrawer().getByRole("button", { name: "Delete" });
  await act(async () => { fireEvent.click(remove); fireEvent.click(remove); await vi.advanceTimersByTimeAsync(0); });
  expect(writes(api).map((row) => [row.operationId, row.path])).toEqual([["API-IRR-05", "/api/v1/irrigation/schedules/11?reason=Moved+to+the+feed"]]);
  expect(writes(api)[0].idempotency_key).toMatch(UUID);
  expect(document.querySelector("dialog")).toBeNull();
  expect(blocks("L01", "2026-11-04")).toEqual([]);
  expect(banner()).toBe("On controller: rev 42 · rev 43 sending Requested");
  expect(text(document.querySelector(".week-count"))).toBe("2 of 24 schedules");
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
  expect(document.querySelectorAll(".week-block-draft")).toHaveLength(0);
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

test("Last 7 days gives way to the next seven while the editor is open, and + New schedule leaves it", async () => {
  const api = open("/water/schedules?past=7", { fixture: "schedules-paused" });
  await advance(0);
  expect(text(document.querySelector(".schedule-week h2"))).toBe("Week · last 7 days");
  fireEvent.click(screen.getByRole("link", { name: "+ New schedule" }));
  await advance(0);
  expect(where()).toBe(NEW);
  expect(text(document.querySelector(".schedule-week h2"))).toBe("Week · planned");
  expect(requests(api, "API-IRR-02").map((row) => row.path)).toEqual(["/api/v1/irrigation/schedules?past_days=7", "/api/v1/irrigation/schedules"]);
});

test("Cancel and Escape close at once and leave the week as it was; focus goes back to the opener", async () => {
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
  expect(document.querySelectorAll(".week-block-draft")).toHaveLength(0);
  expect(document.activeElement).toBe(opener);
  // Edit's own link takes focus back too, though the draft hid its block while the drawer was open.
  const block = () => cell("L01", "2026-11-04").querySelector<HTMLElement>(".week-block")!;
  fireEvent.click(block().querySelector("summary")!);
  const edit = within(block()).getByRole("link", { name: /^Edit/ });
  edit.focus();
  fireEvent.click(edit);
  await advance(0);
  expect(block().hasAttribute("hidden")).toBe(true);
  fireEvent.click(inDrawer().getByRole("button", { name: "Cancel" }));
  await advance(0);
  expect(block().hasAttribute("hidden")).toBe(false);
  expect(document.activeElement).toBe(within(block()).getByRole("link", { name: /^Edit/ }));
  expect(writes(api)).toEqual([]);
});

test("a viewer sees no editor, New schedule or block menu, whatever the URL says; with changes off an operator sees the fields and Save disabled", async () => {
  open(NEW, { fixture: "viewer-session" });
  await advance(0);
  expect(drawer()).toBeNull();
  expect(screen.queryByRole("link", { name: "+ New schedule" })).toBeNull();
  expect(document.querySelector(".week-block-menu")).toBeNull();
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
  cleanup(); stopFixtureApi();
  const api = open("/water/schedules?schedule=2", { fixture: "writes-off" });
  await advance(0);
  expect(inDrawer().getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
  expect(inDrawer().getByRole("button", { name: "Delete" }).hasAttribute("disabled")).toBe(true);
  expect(drawer()!.querySelector("fieldset")?.disabled).toBe(true);
  expect(writes(api)).toEqual([]);
});
