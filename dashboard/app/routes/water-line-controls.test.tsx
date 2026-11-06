// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// #1182's acceptance on SCR-04 in the app shell against #1122's fixture API in process: line enables with the one check
// (in a row's ⋯), the mister switches (in a zone's head), line options, Pause and Resume, the block popover, and what
// viewers and changes-off see (redesign §5.2, §6.2). The clock is 10:42 America/Denver on Monday 2026-11-02; times are fake.
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
/** A row's one pill and its meta line, where the sync reads (redesign §4.4, D-UX-07). */
const sync = (id: string) => [text(card(id).querySelector(".object-row-pill")), text(card(id).querySelector(".object-row-meta"))].filter(Boolean).join(" · ");
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
  expect(text(card("L02").querySelector(".line-state"))).toBe("Off · no check recorded");
  const enable = () => within(card("L02")).getByRole("switch", { name: "Enabled Wall drip (fertilized)" });
  expect(enable().getAttribute("aria-checked")).toBe("false");
  expect(text(card("L02"))).toContain(DISABLE_HINT);
  await press(enable());
  expect(writes(api)).toEqual([]);
  expect(text(screen.getByRole("dialog").querySelector("h2"))).toBe("Enable Wall drip (fertilized)");
  const save = dialog().getByRole<HTMLButtonElement>("button", { name: "Enable" });
  expect(save.disabled).toBe(true);
  fireEvent.click(dialog().getByRole("checkbox", { name: "Checked: tank, injector and line checked, no leaks" }));
  fireEvent.change(dialog().getByLabelText("Note (optional)"), { target: { value: "No leaks at the injector" } });
  await press(dialog().getByRole("button", { name: "Enable" }));
  expect(writes(api)).toHaveLength(1);
  expect(lastWrite(api)).toMatchObject({ method: "PUT", operationId: "API-IRR-08", path: "/api/v1/irrigation/lines/L02", body: { enabled: true, checked: true, check_note: "No leaks at the injector", manual: true } });
  expect(lastWrite(api)?.idempotency_key).toMatch(UUID);
  expect(screen.queryByRole("dialog")).toBeNull();
  // REQUESTED while the fixture's next two reads say so, on the row and the status line, polled every 5 s.
  expect(enable().getAttribute("aria-checked")).toBe("true");
  expect(sync("L02")).toBe("Requested · rev 43 sending");
  expect(text(document.querySelector("[data-program]"))).toBe("Program rev 42 on controller · rev 43 sending Requested");
  await poll();
  expect(sync("L02")).toBe("Requested · rev 43 sending");
  await poll();
  // CONFIRMED once the hash matches, for the 10 minutes after (D-UX-07).
  expect(sync("L02")).toBe("Confirmed");
  expect(text(document.querySelector("[data-program]"))).toMatch(/^Program rev 43 on controller · confirmed/);
  expect(text(card("L02").querySelector(".wat-menu-check"))).toMatch(/^Checked by jason .*: No leaks at the injector$/);
  await advance(11 * 60_000);
  expect(sync("L02")).toBe("");
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
  await press(within(card("L04")).getByRole("switch", { name: "Enabled Center mister" }));
  expect(lastWrite(api)?.body).toEqual({ enabled: false, manual: true });
  await settle();
  expect(sync("L04")).toBe("Not confirmed · Not confirmed: the controller refused a line's settings · retrying at 1, 5 and 15 min");
});

test("turning Schedule on for L04 with no check asks for the check; each switch has its own badge; both off reads \"This mister will not mist automatically\"", async () => {
  const api = open("/water", { fixture: "lines-unchecked" });
  await load();
  const mister = () => within(zone("L04"));
  // Nothing pressed and long confirmed: no pill (D-UX-07).
  expect(flag("L04", "vpd_member")).not.toContain("Confirmed");
  await press(mister().getByRole("switch", { name: "Schedule Center mister" }));
  expect(writes(api)).toEqual([]);
  expect(text(screen.getByRole("dialog").querySelector("h2"))).toBe("Turn on Schedule for Center mister");
  fireEvent.click(dialog().getByRole("checkbox", { name: "Checked: nozzles, coverage and spray area checked" }));
  await press(dialog().getByRole("button", { name: "Turn on Schedule" }));
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L04", { schedule_member: true, checked: true, manual: true }]]);
  // The flag pressed reads REQUESTED, not its neighbour.
  expect(flag("L04", "schedule_member")).toContain("Requested");
  expect(flag("L04", "vpd_member")).not.toContain("Requested");
  expect(text(zone("L04"))).toContain("rev 43 sending");
  await settle();
  expect(flag("L04", "schedule_member")).toContain("Confirmed");
  // Now the zone has its check: Schedule off and VPD off send at once.
  await press(mister().getByRole("switch", { name: "Schedule Center mister" }));
  expect(lastWrite(api)?.body).toEqual({ schedule_member: false, manual: true });
  await press(mister().getByRole("switch", { name: "VPD control Center mister" }));
  expect(lastWrite(api)?.body).toEqual({ vpd_member: false, manual: true });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(text(zone("L04"))).toContain(WILL_NOT_MIST_TEXT);
  expect(WILL_NOT_MIST_TEXT).toBe("This mister will not mist automatically");
});

test("a badge follows what changed: a cancelled Schedule press carries none, and a change confirmed earlier does not read REQUESTED again", async () => {
  open("/water", { fixture: "lines-unchecked" });
  await load();
  const mister = () => within(zone("L04"));
  await press(mister().getByRole("switch", { name: "Schedule Center mister" }));
  await press(dialog().getByRole("button", { name: "Cancel" }));
  expect(flag("L04", "schedule_member")).not.toMatch(/Confirmed|Requested/);
  await press(mister().getByRole("switch", { name: "VPD control Center mister" }));
  expect(flag("L04", "vpd_member")).toContain("Requested");
  expect(flag("L04", "schedule_member")).not.toContain("Requested");
  await settle();
  expect(flag("L04", "vpd_member")).toContain("Confirmed");
  await press(mister().getByRole("switch", { name: "Schedule Center mister" }));
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
  expect(document.querySelector("article[data-line]")).toBeNull();
  expect(text(document.body)).toContain("Water status unavailable");
  expect(note().value).toBe("No leaks");
  await press(dialog().getByRole("button", { name: "Enable" }));
  expect(lastWrite(api)?.body).toEqual({ enabled: true, checked: true, check_note: "No leaks", manual: true });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("product J4: L07 disabled and unchecked is enabled with its check, then VPD control and Schedule go on at once; Schedule pressed first asks for that", async () => {
  const api = open("/water", { fixture: "lines-unchecked" });
  await load();
  await press(within(zone("L07")).getByRole("switch", { name: "Schedule South mister" }));
  expect(text(screen.getByRole("dialog").querySelector("h2"))).toBe("Turn on Schedule for South mister");
  await press(dialog().getByRole("button", { name: "Cancel" }));
  expect(writes(api)).toEqual([]);
  await press(within(card("L07")).getByRole("switch", { name: "Enabled South mister" }));
  fireEvent.click(dialog().getByRole("checkbox", { name: "Checked: nozzles, coverage and spray area checked" }));
  await press(dialog().getByRole("button", { name: "Enable" }));
  expect(lastWrite(api)?.body).toEqual({ enabled: true, checked: true, manual: true });
  await press(within(zone("L07")).getByRole("switch", { name: "VPD control South mister" }));
  expect(lastWrite(api)?.body).toEqual({ vpd_member: true, manual: true });
  await press(within(zone("L07")).getByRole("switch", { name: "Schedule South mister" }));
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
  // A paused line says so in its state, or in its meta while it reads otherwise (VPD misting runs on a paused zone).
  const words = (id: string) => [text(card(id).querySelector(".line-state")), text(card(id).querySelector(".object-row-meta"))].find((line) => line.startsWith("Paused"));
  // A VPD zone says its misting carries on; a drip has none to carry on.
  expect(words("L04")).toBe("Paused until 14:00 · VPD misting continues · by jason");
  expect(words("L01")).toBe("Paused until 14:00 · by jason");
  // Fog is climate only, and no pause holds it.
  expect(words("L11")).toBeUndefined();
  expect(manual()).toEqual(before);
  // Resume on one card, with no manual, then Resume all for the rest.
  await press(within(card("L04")).getByRole("button", { name: "Resume Center mister" }));
  expect(lastWrite(api)).toMatchObject({ path: "/api/v1/irrigation/lines/L04", body: { paused_until: expect.any(String) } });
  expect(Object.keys(lastWrite(api)?.body as object)).toEqual(["paused_until"]);
  expect(words("L04")).toBeUndefined();
  expect(within(card("L04")).queryByRole("button", { name: "Resume Center mister" })).toBeNull();
  await press(screen.getByRole("button", { name: "Resume all" }));
  expect(lastWrite(api)).toMatchObject({ path: "/api/v1/irrigation/lines/all" });
  expect(Object.keys(lastWrite(api)?.body as object)).toEqual(["paused_until"]);
  expect(words("L01")).toBeUndefined();
  expect(screen.queryByRole("button", { name: "Resume all" })).toBeNull();
  expect(manual()).toEqual(before);
});

test("the pause drawer opens from the URL on any Water view, and a row's \"Pause until…\" opens it on that line", async () => {
  open("/water/schedules?pause=L01");
  await load();
  expect(text(screen.getByRole("dialog").querySelector("h2"))).toBe("Pause lines");
  expect(dialog().getByLabelText<HTMLSelectElement>("Line").selectedOptions[0].textContent).toBe("Wall drip");
  cleanup(); stopFixtureApi();
  open("/water");
  await load();
  await press(within(card("L04")).getByRole("button", { name: "Pause until… Center mister" }));
  expect(dialog().getByLabelText<HTMLSelectElement>("Line").selectedOptions[0].textContent).toBe("Center mister");
});

test("enabling L05 asks for the Checked box once, and its row no longer reads Reserved once enabled", async () => {
  const api = open("/water", { fixture: "reserved-lines" });
  await load();
  expect(text(card("L05").querySelector(".line-state"))).toBe("Reserved · disabled");
  await press(within(card("L05")).getByRole("switch", { name: "Enabled Center drip" }));
  fireEvent.click(dialog().getByRole("checkbox", { name: "Checked: line flushed and emitters checked" }));
  await press(dialog().getByRole("button", { name: "Enable" }));
  expect(lastWrite(api)).toMatchObject({ path: "/api/v1/irrigation/lines/L05", body: { enabled: true, checked: true, manual: true } });
  expect(text(card("L05").querySelector(".line-state"))).not.toContain("Reserved");
  expect(text(card("L06").querySelector(".line-state"))).toBe("Reserved · disabled");
  await press(within(card("L05")).getByRole("switch", { name: "Enabled Center drip" }));
  await press(within(card("L05")).getByRole("switch", { name: "Enabled Center drip" }));
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
    expect(text(zone("L04").querySelector(".mister-flags"))).toBe("VPD controlOnScheduleOff");
    cleanup(); stopFixtureApi();
  }
  open("/water/schedules", { fixture: ["schedules-paused", "viewer-session"] });
  await load();
  expect(document.querySelector(".sch-block")).not.toBeNull();
  expect(document.querySelector(".sch-add, .sch-track-new")).toBeNull();
});

test("with changes switched off every line control is disabled beside the banner; Stop all water stays live", async () => {
  open("/water", { fixture: ["lines-unchecked", "writes-off"] });
  await load();
  expect(text(document.body)).toContain("Changes are switched off");
  for (const control of screen.getAllByRole("switch")) expect(control.hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Pause lines…" }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Options… of Center mister" }).disabled).toBe(true);
  // Both copies (the header's from 861 px, the tabs row's at 860 px and below) stay live.
  const stops = screen.getAllByRole<HTMLButtonElement>("button", { name: "Stop all water" });
  expect(stops).toHaveLength(2);
  for (const stop of stops) expect(stop.disabled).toBe(false);
});

test("L04 options with cap 130 and reserve 60 send {daily_cap_min: 130, vpd_reserve_min: 60, manual: true}; before the engine the change reads REQUESTED with its words and never NOT CONFIRMED", async () => {
  const api = open("/water", { fixture: "before-engine" });
  await load();
  await press(within(card("L04")).getByRole("button", { name: "Options… of Center mister" }));
  fireEvent.change(dialog().getByLabelText("Daily cap"), { target: { value: "130" } });
  fireEvent.change(dialog().getByLabelText("VPD reserve"), { target: { value: "60" } });
  await press(dialog().getByRole("button", { name: "Save" }));
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L04", { daily_cap_min: 130, vpd_reserve_min: 60, manual: true }]]);
  // Before the engine every change waits for the firmware: the status line says so once, and the zone's cap follows.
  expect(text(document.querySelector("[data-program]"))).toBe("No irrigation engine on the controller yet: relay states only; the wall drip runs, and every line stops, through the bridge");
  expect(text(document.querySelector('[data-limit="L04"]'))).toMatch(/^14 \/ 130 min/);
  await advance(6 * 60_000);
  expect(sync("L04")).toBe("");
  expect(text(document.body)).not.toContain("Not confirmed");
  void APPLIES;
});

test("with the engine, the same options read REQUESTED, then CONFIRMED by the hash, and the limits follow", async () => {
  const api = open("/water?options=L04");
  await load();
  fireEvent.change(dialog().getByLabelText("Daily cap"), { target: { value: "130" } });
  await press(dialog().getByRole("button", { name: "Save" }));
  expect(lastWrite(api)?.body).toEqual({ daily_cap_min: 130, vpd_reserve_min: 60, manual: true });
  expect(sync("L04")).toBe("Requested · rev 43 sending");
  await settle();
  expect(sync("L04")).toBe("Confirmed");
  expect(text(document.querySelector('[data-limit="L04"]'))).toMatch(/^14 \/ 130 min.*60 min kept for VPD/);
});

test("Site today's daily mist & fog limit (SAF-4) links to its Settings drawer and explains itself in a tip (#1349)", async () => {
  open("/water");
  await load();
  const link = within(document.querySelector<HTMLElement>('[data-limit="saf4"]')!).getByRole("link");
  expect(link.getAttribute("href")).toBe("/settings?edit=mister_daily_volume_max_gal");
  expect(link.textContent).toBe("Daily mist & fog limit, in Settings");
  await press(screen.getByRole("button", { name: "About Daily mist & fog limit" }));
  expect(text(screen.getByRole("dialog"))).toContain("The controller stops misting when it reaches this limit; survival fog is exempt.");
  // No bare firmware term or upper-case budget label anywhere on the page.
  expect(text(document.querySelector(".wat-site"))).not.toMatch(/SAF-4|SOFT BUDGET|Soft budget/);
});

test("a planned block's popover offers \"Pause <line> until after this run\": one minute past its planned end; it then reads skipped, and a past block's offers no pause, only Edit", async () => {
  const api = open("/water/schedules?day=2026-11-04", { fixture: "schedules-paused" });
  await load();
  const block = () => document.querySelector<HTMLElement>('.sch-day .sch-row[data-line="L01"] .sch-block')!;
  await press(block());
  const pop = () => within(document.querySelector<HTMLElement>("dialog.popover")!);
  await press(pop().getByRole("button", { name: "Pause Wall drip until after this run" }));
  // 12 minutes of drip, plus one, from 07:35.
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L01", { paused_until: "2026-11-04T14:48:00.000Z" }]]);
  expect(block().className).toContain("sch-skipped");
  expect(text(document.querySelector("dialog.popover"))).toContain("line paused by jason");
  expect(pop().queryByRole("button", { name: /until after this run/ })).toBeNull();
  expect(pop().getByRole("link", { name: "Edit schedule" })).toBeTruthy();
  cleanup(); stopFixtureApi();
  open("/water/schedules?day=2026-10-30", { fixture: "schedules-paused" });
  await load();
  await press(document.querySelector<HTMLElement>('.sch-day .sch-row[data-line="L01"] .sch-block')!);
  expect(screen.queryByRole("button", { name: /until after this run/ })).toBeNull();
  expect(screen.getByRole("link", { name: "Edit schedule" })).toBeTruthy();
});
