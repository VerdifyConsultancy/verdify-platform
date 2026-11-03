// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// #1182's acceptance on SCR-04 in the app shell against #1122's fixture API in process: line enables with the one
// check, the mister switches, line options, Pause and Resume, the block menu, and what viewers and changes-off see.
// The clock is 10:42 America/Denver on Monday 2026-11-02; times are fake.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { AppShell } from "../components/app-shell";
import { DISABLE_HINT } from "../components/line-enable";
import { WILL_NOT_MIST_TEXT } from "../components/mister-association";
import { text } from "../components/test-render";
import { UUID, advance, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import Water from "./water";
import WaterBoard from "./water-board";
import WaterSchedules from "./water-schedules";

const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
const APPLIES = "Applies with the irrigation firmware";
const AT_14 = "2026-11-02T21:00:00.000Z";

afterEach(() => { cleanup(); stopFixtureApi(); });

function open(path: string, options: FixtureOptions = {}) {
  const api = startFixtureApi(options);
  const Stub = createRoutesStub([{
    path: "/", Component: () => <AppShell build={BUILD} authError={null} />,
    children: [{ path: "water", Component: Water, children: [{ index: true, Component: WaterBoard }, { path: "schedules", Component: WaterSchedules }] }],
  }]);
  render(<Stub initialEntries={[path]} />);
  return api;
}
const load = async () => { await advance(0); await act(async () => { await vi.dynamicImportSettled(); }); };
const card = (id: string) => document.querySelector<HTMLElement>(`.line-card[data-line="${id}"]`)!;
const zone = (id: string) => document.querySelector<HTMLElement>(`.mister-zone[data-line="${id}"]`)!;
const dialog = () => within(screen.getByRole("dialog"));
const sync = (id: string) => text(card(id).querySelector(".line-sync"));
const flag = (id: string, name: "vpd_member" | "schedule_member") => text(zone(id).querySelector(`[data-flag="${name}"]`));
const press = async (node: HTMLElement) => { fireEvent.click(node); await advance(0); };
const lastWrite = (api: ReturnType<typeof open>) => writes(api).at(-1);
/** One poll, and its answer, which TanStack renders on a timer a moment later. */
const poll = async () => { await advance(5000); await advance(5); };
/** The polls at 5 s and 10 s: the second is the fixture's third read since the write. */
const settle = async () => { await poll(); await poll(); };

test("enabling an unchecked line shows the Checked box once and sends checked with the note; the card reads REQUESTED, then CONFIRMED on the fixture hash", async () => {
  const api = open("/water", { fixture: "lines-unchecked" });
  await load();
  expect(text(card("L02"))).toContain("No check recorded");
  const enable = () => within(card("L02")).getByRole("switch", { name: "Enabled L02" });
  expect(enable().getAttribute("aria-checked")).toBe("false");
  expect(text(card("L02"))).toContain(DISABLE_HINT);
  await press(enable());
  expect(writes(api)).toEqual([]);
  expect(text(screen.getByRole("dialog").querySelector("h2"))).toBe("Enable L02");
  const save = dialog().getByRole<HTMLButtonElement>("button", { name: "Enable" });
  expect(save.disabled).toBe(true);
  fireEvent.click(dialog().getByRole("checkbox", { name: "Checked: line, tank and injector checked, no leaks" }));
  fireEvent.change(dialog().getByLabelText("Note (optional)"), { target: { value: "No leaks at the injector" } });
  await press(dialog().getByRole("button", { name: "Enable" }));
  expect(writes(api)).toHaveLength(1);
  expect(lastWrite(api)).toMatchObject({ method: "PUT", operationId: "API-IRR-08", path: "/api/v1/irrigation/lines/L02", body: { enabled: true, checked: true, check_note: "No leaks at the injector", manual: true } });
  expect(lastWrite(api)?.idempotency_key).toMatch(UUID);
  expect(screen.queryByRole("dialog")).toBeNull();
  // REQUESTED while the fixture's next two reads say so, on the card and the banner, polled every 5 s.
  expect(enable().getAttribute("aria-checked")).toBe("true");
  expect(sync("L02")).toBe("Requestedrev 43 sending");
  expect(text(document.querySelector("[data-program]"))).toBe("On controller: rev 42 · rev 43 sending Requested");
  await poll();
  expect(sync("L02")).toBe("Requestedrev 43 sending");
  await poll();
  expect(sync("L02")).toBe("Confirmed");
  expect(text(document.querySelector("[data-program]"))).toMatch(/^On controller: rev 43 · Confirmed/);
  expect(text(card("L02"))).toContain("Checked by jason");
  expect(text(card("L02"))).toContain("No leaks at the injector");
  // The check is done: off and on again send at once and never ask again.
  await press(enable());
  expect(lastWrite(api)?.body).toEqual({ enabled: false, manual: true });
  expect(screen.queryByRole("dialog")).toBeNull();
  await press(enable());
  expect(lastWrite(api)?.body).toEqual({ enabled: true, manual: true });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a change the controller refuses reads \"Not confirmed: <reject in words>\" on the card", async () => {
  const api = open("/water", { fixture: "lines-unchecked", confirm: false });
  await load();
  await press(within(card("L04")).getByRole("switch", { name: "Enabled L04" }));
  expect(lastWrite(api)?.body).toEqual({ enabled: false, manual: true });
  await settle();
  expect(sync("L04")).toBe("Not confirmedNot confirmed: the controller refused a line's settings · retrying at 1, 5 and 15 min");
});

test("turning Schedule on for L04 with no check asks for the check; each switch has its own badge; both off reads \"This zone will not mist automatically\"", async () => {
  const api = open("/water", { fixture: "lines-unchecked" });
  await load();
  const mister = () => within(zone("L04"));
  expect(flag("L04", "vpd_member")).toContain("Confirmed");
  await press(mister().getByRole("switch", { name: "Schedule L04" }));
  expect(writes(api)).toEqual([]);
  expect(text(screen.getByRole("dialog").querySelector("h2"))).toBe("Turn on Schedule for L04");
  fireEvent.click(dialog().getByRole("checkbox", { name: "Checked: nozzles over the Vandas, coverage and walkway checked" }));
  await press(dialog().getByRole("button", { name: "Turn on Schedule" }));
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L04", { schedule_member: true, checked: true, manual: true }]]);
  // The flag pressed reads REQUESTED, not its neighbour.
  expect(flag("L04", "schedule_member")).toContain("Requested");
  expect(flag("L04", "vpd_member")).not.toContain("Requested");
  expect(text(zone("L04"))).toContain("rev 43 sending");
  await settle();
  expect(flag("L04", "schedule_member")).toContain("Confirmed");
  // Now the zone has its check: Schedule off and VPD off send at once.
  await press(mister().getByRole("switch", { name: "Schedule L04" }));
  expect(lastWrite(api)?.body).toEqual({ schedule_member: false, manual: true });
  await press(mister().getByRole("switch", { name: "VPD control L04" }));
  expect(lastWrite(api)?.body).toEqual({ vpd_member: false, manual: true });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(text(zone("L04"))).toContain(WILL_NOT_MIST_TEXT);
  expect(WILL_NOT_MIST_TEXT).toBe("This zone will not mist automatically");
});

test("a badge follows what changed: a cancelled Schedule press carries none, and a change confirmed earlier does not read REQUESTED again", async () => {
  open("/water", { fixture: "lines-unchecked" });
  await load();
  const mister = () => within(zone("L04"));
  await press(mister().getByRole("switch", { name: "Schedule L04" }));
  await press(dialog().getByRole("button", { name: "Cancel" }));
  expect(flag("L04", "schedule_member")).not.toContain("Confirmed");
  await press(mister().getByRole("switch", { name: "VPD control L04" }));
  expect(flag("L04", "vpd_member")).toContain("Requested");
  expect(flag("L04", "schedule_member")).not.toContain("Requested");
  await settle();
  expect(flag("L04", "vpd_member")).toContain("Confirmed");
  await press(mister().getByRole("switch", { name: "Schedule L04" }));
  fireEvent.click(dialog().getByRole("checkbox", { name: /^Checked:/ }));
  await press(dialog().getByRole("button", { name: "Turn on Schedule" }));
  expect(flag("L04", "schedule_member")).toContain("Requested");
  expect(flag("L04", "vpd_member")).not.toContain("Requested");
});

test("a failed status read says so behind an open drawer and leaves what was typed in it, which then saves", async () => {
  const api = open("/water?check=L02", { fixture: "lines-unchecked" });
  await load();
  const note = () => dialog().getByLabelText<HTMLInputElement>("Note (optional)");
  fireEvent.click(dialog().getByRole("checkbox", { name: /^Checked:/ }));
  fireEvent.change(note(), { target: { value: "No leaks" } });
  api.respond({ method: "POST", url: "/__control", body: { fixture: "lines-unchecked", fail: { "API-IRR-01": 503 } } });
  await advance(15_000);
  await advance(5);
  expect(document.querySelector(".line-card")).toBeNull();
  expect(text(document.body)).toContain("Water status unavailable");
  expect(note().value).toBe("No leaks");
  await press(dialog().getByRole("button", { name: "Enable" }));
  expect(lastWrite(api)?.body).toEqual({ enabled: true, checked: true, check_note: "No leaks", manual: true });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("product J4: L07 disabled and unchecked is enabled with its check, then VPD control and Schedule go on at once; Schedule pressed first asks for that", async () => {
  const api = open("/water", { fixture: "lines-unchecked" });
  await load();
  await press(within(zone("L07")).getByRole("switch", { name: "Schedule L07" }));
  expect(text(screen.getByRole("dialog").querySelector("h2"))).toBe("Turn on Schedule for L07");
  await press(dialog().getByRole("button", { name: "Cancel" }));
  expect(writes(api)).toEqual([]);
  await press(within(card("L07")).getByRole("switch", { name: "Enabled L07" }));
  fireEvent.click(dialog().getByRole("checkbox", { name: "Checked: line drained, nozzles checked, no walkway spray" }));
  await press(dialog().getByRole("button", { name: "Enable" }));
  expect(lastWrite(api)?.body).toEqual({ enabled: true, checked: true, manual: true });
  await press(within(zone("L07")).getByRole("switch", { name: "VPD control L07" }));
  expect(lastWrite(api)?.body).toEqual({ vpd_member: true, manual: true });
  await press(within(zone("L07")).getByRole("switch", { name: "Schedule L07" }));
  expect(lastWrite(api)?.body).toEqual({ schedule_member: true, manual: true });
  expect(screen.queryByRole("dialog")).toBeNull();
  await settle();
  expect(sync("L07")).toBe("Confirmed");
  expect(flag("L07", "vpd_member")).toContain("Confirmed");
  expect(flag("L07", "schedule_member")).toContain("Confirmed");
});

test("pause all until 14:00 sends line_id all with paused_until; the cards read it; Resume clears it; neither changes manual", async () => {
  const api = open("/water");
  await load();
  const manual = () => (api.respond({ url: "/api/v1/irrigation" }).body.lines as { line_id: string; manual: boolean }[]).map((line) => line.manual);
  const before = manual();
  expect(screen.queryByRole("button", { name: "Resume all" })).toBeNull();
  await press(screen.getByRole("button", { name: "Pause lines…" }));
  fireEvent.change(dialog().getByLabelText("Until"), { target: { value: "2026-11-02T14:00" } });
  await press(dialog().getByRole("button", { name: "Pause" }));
  expect(writes(api).map((row) => [row.method, row.path, row.body])).toEqual([["PUT", "/api/v1/irrigation/lines/all", { paused_until: AT_14 }]]);
  expect(screen.queryByRole("dialog")).toBeNull();
  const words = (id: string) => [...card(id).querySelectorAll(":scope > p")].map((node) => text(node)).find((line) => line.startsWith("Paused"));
  // A VPD zone says its misting carries on; a drip has none to carry on.
  expect(words("L04")).toBe("Paused until 14:00 · VPD misting continues · by jason");
  expect(words("L01")).toBe("Paused until 14:00 · by jason");
  // Fog is climate only, and no pause holds it.
  expect(words("L11")).toBeUndefined();
  expect(manual()).toEqual(before);
  // Resume on one card, with no manual, then Resume all for the rest.
  await press(within(card("L04")).getByRole("button", { name: "Resume L04" }));
  expect(lastWrite(api)).toMatchObject({ path: "/api/v1/irrigation/lines/L04", body: { paused_until: expect.any(String) } });
  expect(Object.keys(lastWrite(api)?.body as object)).toEqual(["paused_until"]);
  expect(words("L04")).toBeUndefined();
  expect(within(card("L04")).queryByRole("button", { name: "Resume L04" })).toBeNull();
  await press(screen.getByRole("button", { name: "Resume all" }));
  expect(lastWrite(api)).toMatchObject({ path: "/api/v1/irrigation/lines/all" });
  expect(Object.keys(lastWrite(api)?.body as object)).toEqual(["paused_until"]);
  expect(words("L01")).toBeUndefined();
  expect(screen.queryByRole("button", { name: "Resume all" })).toBeNull();
  expect(manual()).toEqual(before);
});

test("Pause lines… sits on all three views' head: the drawer opens from the Schedules view too", async () => {
  open("/water/schedules?pause=L01");
  await load();
  expect(text(screen.getByRole("dialog").querySelector("h2"))).toBe("Pause lines");
  expect(dialog().getByLabelText<HTMLSelectElement>("Line").selectedOptions[0].textContent).toMatch(/^L01/);
});

test("enabling L05 asks for the Checked box once, and its card no longer reads Reserved once enabled", async () => {
  const api = open("/water", { fixture: "reserved-lines" });
  await load();
  expect(text(card("L05").querySelector(".line-card-head"))).toMatch(/Disabled Reserved$/);
  await press(within(card("L05")).getByRole("switch", { name: "Enabled L05" }));
  fireEvent.click(dialog().getByRole("checkbox", { name: "Checked: line connected and checked" }));
  await press(dialog().getByRole("button", { name: "Enable" }));
  expect(lastWrite(api)).toMatchObject({ path: "/api/v1/irrigation/lines/L05", body: { enabled: true, checked: true, manual: true } });
  expect(text(card("L05").querySelector(".line-card-head"))).toBe("L05 Drip • CenterClean Enabled: On");
  expect(text(card("L06").querySelector(".line-card-head"))).toMatch(/Disabled Reserved$/);
  await press(within(card("L05")).getByRole("switch", { name: "Enabled L05" }));
  await press(within(card("L05")).getByRole("switch", { name: "Enabled L05" }));
  expect(lastWrite(api)?.body).toEqual({ enabled: true, manual: true });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("viewers see no switch, option or Pause, even from a link; the flags read as words", async () => {
  for (const path of ["/water", "/water?check=L02", "/water?options=L04", "/water?pause=all"]) {
    open(path, { fixture: ["lines-unchecked", "viewer-session"] });
    await load();
    expect(document.querySelector(".line-card"), path).not.toBeNull();
    expect(screen.queryByRole("switch"), path).toBeNull();
    expect(screen.queryByRole("dialog"), path).toBeNull();
    expect(screen.queryByRole("button", { name: /^(Options|Resume|Pause)/ }), path).toBeNull();
    expect(text(document.body), path).not.toContain(DISABLE_HINT);
    expect(text(zone("L04").querySelector(".mister-flags"))).toBe("VPD controlOn ConfirmedScheduleOff");
    cleanup(); stopFixtureApi();
  }
  open("/water/schedules", { fixture: ["schedules-paused", "viewer-session"] });
  await load();
  expect(document.querySelector(".week-block")).not.toBeNull();
  expect(document.querySelector(".week-block-menu")).toBeNull();
});

test("with changes switched off every line control is disabled beside the banner; Stop all water stays live", async () => {
  open("/water", { fixture: ["lines-unchecked", "writes-off"] });
  await load();
  expect(text(document.body)).toContain("Changes are switched off");
  for (const control of screen.getAllByRole("switch")) expect(control.hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Pause lines…" }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Options of L04" }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Stop all water" }).disabled).toBe(false);
});

test("L04 options with cap 130 and reserve 60 send {daily_cap_min: 130, vpd_reserve_min: 60, manual: true}; before the engine the change reads REQUESTED with its words and never NOT CONFIRMED", async () => {
  const api = open("/water", { fixture: "before-engine" });
  await load();
  await press(within(card("L04")).getByRole("button", { name: "Options of L04" }));
  fireEvent.change(dialog().getByLabelText("Daily cap"), { target: { value: "130" } });
  fireEvent.change(dialog().getByLabelText("VPD reserve"), { target: { value: "60" } });
  await press(dialog().getByRole("button", { name: "Save" }));
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L04", { daily_cap_min: 130, vpd_reserve_min: 60, manual: true }]]);
  expect(sync("L04")).toBe(`Requested${APPLIES}`);
  expect(text(card("L04"))).toContain("Today 14 of 130 min");
  await advance(6 * 60_000);
  expect(sync("L04")).toBe(`Requested${APPLIES}`);
  expect(text(document.body)).not.toContain("Not confirmed");
});

test("with the engine, the same options read REQUESTED, then CONFIRMED by the hash, and the limits follow", async () => {
  const api = open("/water?options=L04");
  await load();
  fireEvent.change(dialog().getByLabelText("Daily cap"), { target: { value: "130" } });
  await press(dialog().getByRole("button", { name: "Save" }));
  expect(lastWrite(api)?.body).toEqual({ daily_cap_min: 130, vpd_reserve_min: 60, manual: true });
  expect(sync("L04")).toBe("Requestedrev 43 sending");
  await settle();
  expect(sync("L04")).toBe("Confirmed");
  expect(text(document.querySelector('[data-limit="L04"]'))).toBe("Center 14 of 130 min (60 kept for VPD)");
});

test("the limits strip's SAF-4 figure links to its Settings drawer", async () => {
  open("/water");
  await load();
  const link = within(document.querySelector<HTMLElement>('[data-limit="saf4"]')!).getByRole("link");
  expect(link.getAttribute("href")).toBe("/settings?edit=mister_daily_volume_max_gal");
  expect(text(link)).toBe("SAF-4 21 of 360 gal");
});

test("a planned block offers \"Pause <line> until after this run\": one minute past its planned end; it then reads skipped, and a past or skipped block offers none", async () => {
  const api = open("/water/schedules", { fixture: "schedules-paused" });
  await load();
  const block = () => document.querySelector<HTMLElement>('tr[data-line="L01"] td[data-day="2026-11-04"] .week-block')!;
  const start = Date.parse(text(block().querySelector("time")) ? block().querySelector("time")!.getAttribute("datetime")! : "");
  await press(within(block()).getByRole("button", { name: "Pause L01 until after this run" }));
  // 12 minutes of drip, plus one.
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L01", { paused_until: new Date(start + 13 * 60_000).toISOString() }]]);
  expect(block().getAttribute("data-status")).toBe("skipped");
  expect(text(block())).toContain("line paused by jason");
  expect(block().querySelector(".week-block-menu")).toBeNull();
  cleanup(); stopFixtureApi();
  open("/water/schedules?past=7", { fixture: "schedules-paused" });
  await load();
  expect(document.querySelector(".week-block")).not.toBeNull();
  expect(document.querySelector(".week-block-menu")).toBeNull();
});
