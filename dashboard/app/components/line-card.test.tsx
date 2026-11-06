// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// A line's row on Water → Today (#1135, redesign §5.2, `ObjectRow`) over #1122's fixture lines, at 10:42 on Monday
// 2026-11-02: the title, kind and one pill, the state sentence, the primary action by state, a Stop only while it runs or reads unknown (D-UX-06, 2026-11-06) and ⋯.
import { act, cleanup, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import type { Session } from "../lib/session";
import { renderWithApi } from "../lib/test-fixture-api";
import { LineCard, MASTER_TEXT, type LineCardProps } from "./line-card";
import { denver, text } from "./test-render";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const status = (names: string | string[] = "in-band") => fixture(names, { now: NOW })["API-IRR-01"] as IrrigationStatus;
const lineOf = (from: IrrigationStatus, id: string) => from.lines.find((line) => line.line_id === id)!;
const OPERATOR: Partial<Session> = { role: "operator", writesEnabled: true };

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

function row(line: IrrigationLine, props: Partial<LineCardProps> = {}, session: Partial<Session> = OPERATOR) {
  renderWithApi(<LineCard line={line} engine today={TODAY} {...props} />, session);
  const article = document.querySelector<HTMLElement>(`article[data-line="${line.line_id}"]`)!;
  const actions = article.querySelector<HTMLElement>(":scope > .object-row-actions");
  return {
    article,
    title: text(article.querySelector("h4")),
    head: text(article.querySelector(".object-row-head")),
    state: text(article.querySelector(".line-state")),
    meta: text(article.querySelector(".object-row-meta")),
    // The buttons outside the ⋯ menu: the primary action and Stop.
    buttons: actions ? [...actions.querySelectorAll(":scope > button")].map((node) => text(node)) : [],
    menu: article.querySelector<HTMLElement>("details.menu"),
  };
}

test("an idle line: its title from zone and kind, CLEAN, its state with its last water, then Run… and ⋯, and no Stop (D-UX-06, 2026-11-06)", () => {
  const { article, title, head, state, buttons } = row(lineOf(status(), "L01"));
  expect(article.className).toContain("line-card");
  expect([article.getAttribute("data-state"), article.getAttribute("aria-labelledby") !== null]).toEqual(["idle", true]);
  expect(title).toBe("Wall drip");
  expect(head).toBe("Wall dripClean");
  // The fixture's "07:31" three days back.
  expect(state).toMatch(/^Idle · last Fri \d\d:\d\d, 12 min · ok$/);
  expect(buttons).toEqual(["Run… Wall drip"]);
  expect(article.querySelector(".btn-stop")).toBeNull();
});

test("a running line: the cause's dot, Running with its cause, schedule and time left counting down, and one filled Stop as its primary", () => {
  const running = row(lineOf(status("l01-running"), "L01"));
  expect(running.state).toBe("Running · run now · 3:00 left");
  expect(running.article.querySelector(".wat-dot")?.getAttribute("data-cause")).toBe("run_now");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(text(running.article.querySelector(".line-state"))).toBe("Running · run now · 2:59 left");
  expect(running.buttons).toEqual(["Stop Wall drip"]);
  expect(running.article.querySelector(".btn-stop")).not.toBeNull();
  expect(running.article.className).toMatch(/tone-running tone-eq-master/);
  cleanup();
  const scheduled = { ...lineOf(status(), "L01"), state: { code: "running", cause: "schedule", slot_id: 11, reason: null, ends_at: new Date(Date.now() + 40_000).toISOString(), observed_at: denver("10:30") } };
  expect(row(scheduled, { scheduleLabels: new Map([[11, "Wall MWF"]]) }).state).toBe("Running · schedule · Wall MWF · 0:40 left");
  cleanup();
  // VPD misting with no end: since when.
  const misting = { ...lineOf(status(), "L04"), state: { code: "running", cause: "vpd", slot_id: null, reason: null, ends_at: null, observed_at: denver("10:30") } };
  expect(row(misting).state).toBe("Running · VPD · since 10:30");
});

test("unknown keeps its Stop (the relay may be on); a disabled line keeps Run… (#1343) and ⋯ with no Stop; L05 reads Reserved · disabled", () => {
  const unknown = row(lineOf(status("controller-not-reporting"), "L01"));
  expect(unknown.state).toBe("Unknown · 3 days ago");
  expect(unknown.article.querySelector(".btn-stop")).not.toBeNull();
  cleanup();
  const disabled = row(lineOf(status(), "L02"));
  expect(disabled.state).toBe("Off · no check recorded");
  expect(disabled.buttons).toEqual(["Run… Wall drip (fertilized)"]);
  cleanup();
  const reserved = row(lineOf(status("reserved-lines"), "L05"));
  expect(reserved.state).toBe("Reserved · disabled");
  expect(reserved.head).toBe("Center dripClean");
});

test("L11 fog is climate only: Fog once… (a Stop only while it runs) and its enable switch with the engine (#1343 follow-up), no Options or pause, and its menu keeps its links", () => {
  const fog = row(lineOf(status(), "L11"));
  expect(fog.head).toBe("FogClimate only");
  expect(fog.buttons).toEqual(["Fog once… Fog"]);
  expect(fog.menu!.querySelectorAll("[role=switch]")).toHaveLength(1);
  expect(within(fog.menu!).queryByRole("button", { name: /^Options…|^Pause until…/ })).toBeNull();
  expect([...fog.menu!.querySelectorAll("a")].map((link) => [text(link), link.getAttribute("href")])).toEqual([
    ["Activity for Fog ›", "/activity/log?target=L11"], ["Runs of Fog ›", "/water/history?line=L11"], [expect.stringMatching(/^Graphs around Fog's last run ›$/), expect.stringMatching(/^\/graphs\?from=/)],
  ]);
});

test("⋯ holds the enable switch with its hint, Options…, Pause until…, the links and the check on record; a viewer's keeps the links alone", () => {
  const { menu } = row(lineOf(status("schedules-paused"), "L02"));
  const panel = within(menu!);
  expect(panel.getByRole("switch", { name: "Enabled Wall drip (fertilized)" }).getAttribute("aria-checked")).toBe("true");
  expect(menu!.querySelector("[data-menu-keep]")?.textContent).toContain("Off stops all water on this line, VPD misting included");
  expect(panel.getByRole("button", { name: "Options… of Wall drip (fertilized)" })).toBeTruthy();
  expect(panel.getByRole("button", { name: "Pause until… Wall drip (fertilized)" })).toBeTruthy();
  expect(panel.getByRole("link", { name: "Activity for Wall drip (fertilized)" }).getAttribute("href")).toBe("/activity/log?target=L02");
  expect(panel.getByRole("link", { name: "Runs of Wall drip (fertilized)" }).getAttribute("href")).toBe("/water/history?line=L02");
  expect(text(menu!.querySelector(".wat-menu-check"))).toMatch(/^Checked by jason Oct 31, \d\d:\d\d$/);
  cleanup();
  const viewer = row(lineOf(status("schedules-paused"), "L02"), {}, { role: "viewer" });
  expect(viewer.article.querySelectorAll("[data-write-control], [role=switch]")).toHaveLength(0);
  expect([...viewer.menu!.querySelectorAll("a")].map((link) => text(link).slice(0, 8))).toEqual(["Activity", "Runs of ", "Graphs a"]);
});

test("L02's row carries L03, the fertilizer master, as its sub-row", () => {
  const from = status("schedules-paused");
  const { article } = row(lineOf(from, "L02"), { master: lineOf(from, "L03") });
  const sub = article.querySelector(".line-subrow")!;
  expect(sub.getAttribute("data-line")).toBe("L03");
  // The elbow is drawn (IBM Plex has no └), beside the words.
  expect(text(sub)).toBe(`Fertilizer valve: ${MASTER_TEXT} · Off`);
  expect(sub.querySelector(".glyph-branch[aria-hidden=true]")).not.toBeNull();
  expect(MASTER_TEXT).toBe("opens the injector line from the tank with each feed");
});

test("a latched mister reads its fault as its state, in fault, with Clear fault as its primary; a back-off is quieter", () => {
  const latched = row(lineOf(status("mister-fault"), "L04"));
  const fault = latched.article.querySelector(".line-fault")!;
  expect([text(fault), fault.className]).toEqual(["Faulted: no misting until midnight (watchdog)", "line-fault fault"]);
  expect(latched.buttons).toEqual(["Clear fault on Center mister"]);
  expect(latched.article.className).toContain("tone-fault");
  cleanup();
  const backing = row({ ...lineOf(status(), "L07"), fault: "backing_off", fault_until: denver("13:20"), state: { ...lineOf(status(), "L07").state, code: "blocked", reason: "backing off" } });
  expect([backing.state, backing.article.querySelector(".line-fault")?.className]).toEqual(["Backing off until 13:20", "line-fault line-fault-warn"]);
  expect(backing.article.className).toContain("tone-warn");
});

test("paused, waiting and unknown read in words; a paused line's primary is Resume, and VPD misting on a paused zone shows the pause in the meta", () => {
  const paused = { paused_until: denver("14:00"), paused_by: { source: "dashboard", name: "jason" }, pause_reason: "rain" };
  const base = lineOf(status(), "L04");
  const held = row({ ...base, ...paused, state: { ...base.state, code: "paused" } });
  expect(held.state).toBe("Paused until 14:00 · VPD misting continues · by jason: rain");
  expect(held.buttons).toEqual(["Resume Center mister"]);
  cleanup();
  const misting = row({ ...base, ...paused, state: { ...base.state, code: "running", cause: "vpd", ends_at: null } });
  expect(misting.state).toMatch(/^Running · VPD/);
  expect(misting.meta).toBe("Paused until 14:00 · VPD misting continues · by jason: rain");
  cleanup();
  expect(row({ ...base, state: { ...base.state, code: "waiting", reason: "waiting for the manifold" } }).state).toBe("Waiting · waiting for the manifold");
});

test("one pill: REQUESTED with the revision while it travels, NOT CONFIRMED with the reject in words; none before the engine, where the status line says why", () => {
  const rev = status();
  const pill = (line: IrrigationLine, props: Partial<LineCardProps> = {}) => {
    const { article, meta } = row(line, { program: rev.program, ...props });
    const words = [text(article.querySelector(".object-row-pill")), meta];
    cleanup();
    return words;
  };
  expect(pill(lineOf(rev, "L01"))).toEqual(["", ""]);
  const sending = { ...rev.program, state: "requested", revision: 43 };
  expect(pill({ ...lineOf(rev, "L01"), sync: "requested" }, { program: sending })).toEqual(["Requested", "rev 43 sending"]);
  const refused = { ...rev.program, state: "not_confirmed", revision: 43, reject_code: "bad_line" };
  expect(pill({ ...lineOf(rev, "L01"), sync: "not_confirmed" }, { program: refused })).toEqual(["Not confirmed", "Not confirmed: the controller refused a line's settings · retrying at 1, 5 and 15 min"]);
  const before = status("before-engine");
  expect(pill(lineOf(before, "L01"), { engine: false, program: before.program })).toEqual(["", ""]);
});

test("before the irrigation firmware a row shows its relay; only L01 runs, through the bridge, and the rest say when Run arrives in ⋯", () => {
  const before = status("before-engine");
  const idle = row(lineOf(before, "L01"), { engine: false });
  expect(idle.state).toMatch(/^Relay Off since \d\d:\d\d$/);
  expect(idle.buttons).toEqual(["Run… Wall drip"]);
  cleanup();
  const other = row(lineOf(before, "L04"), { engine: false });
  expect(other.buttons).toEqual([]);
  expect(text(other.menu!.querySelector("[data-arrives=clear-fault]"))).toBe("Clear fault arrives with the irrigation firmware");
  cleanup();
  const feed = row(lineOf(before, "L02"), { engine: false });
  expect(text(feed.menu!.querySelector("[data-arrives=run]"))).toBe("Run arrives with the irrigation firmware");
  cleanup();
  expect(row(lineOf(status(["l01-running", "before-engine"]), "L01"), { engine: false }).state).toBe("Relay On since 10:30");
});

test("the row carries no style attribute and keeps its hooks: data-line, data-state, its h4 name and the strip slot", () => {
  const { article } = row(lineOf(status(), "L01"), { strip: <div className="wat-day">strip</div> });
  expect(article.outerHTML).not.toMatch(/\sstyle=/);
  expect(article.id).toBe("line-L01");
  expect(article.querySelector(":scope > .wat-day")).not.toBeNull();
});
