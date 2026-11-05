// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The schedule views on their own (redesign §6, §4.6): the day timeline, the today strip, the week strip and the agenda
// over #1122's schedules-paused week at 10:42 on Monday 2026-11-02 — what the server sends, the API-down neutral state,
// the timeline's one tab stop with arrow keys along its blocks, and an empty slot's click.
import { renderToString } from "react-dom/server";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import type { IrrigationStatus, ScheduleAgenda, StateLanes } from "../../lib/operator-api";
import { SessionProvider } from "../../lib/session";
import { renderUi, text } from "../test-render";
import { DayStrip } from "../water/day-strip";
import { Agenda } from "./agenda";
import { DayTimeline, blockRooms, blockWords } from "./day-timeline";
import { agendaDays, dayModel, entriesOf, mondayOf, weekCells, type DayBlock } from "./model";
import { timeScale } from "../../lib/time-scale";
import { WeekStrip } from "./week-strip";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const served = fixture("schedules-paused", { now: NOW });
const agenda = served["API-IRR-02"] as ScheduleAgenda;
const status = served["API-IRR-01"] as IrrigationStatus;
const lanes = served["API-05"] as StateLanes;
const entries = entriesOf(agenda, null, NOW);
const model = (day = TODAY) => dayModel({ day, today: TODAY, now: NOW, agenda, lines: status.lines, lanes: day <= TODAY ? lanes : null, entries });

afterEach(cleanup);

test("the server sends the timeline's rows and words with no style attribute: positions are CSSOM custom properties set on mount", () => {
  const html = renderToString(<MemoryRouter><DayTimeline model={model()} now={NOW} head={<h2>Monday</h2>} label="Runs on Monday by line" /></MemoryRouter>);
  expect(html).toContain('data-line="L01"');
  expect(html).not.toMatch(/\sstyle=/);
  const strip = renderToString(<DayStrip name="L04 Center mister" from={model().from} to={model().to} night={model().night} blocks={[]} episodes={[]} now={NOW} token="mister" />);
  expect(strip).not.toMatch(/\sstyle=/);
  expect(strip).toContain('role="img"');
});

test("the timeline is one tab stop: its blocks are out of the tab order and the arrow keys move along them; an operator's empty slot gives its line and time", () => {
  const onSlot = vi.fn();
  const onBlock = vi.fn();
  renderUi(<DayTimeline model={model("2026-11-04")} now={NOW} head={<h2>Wednesday</h2>} label="Runs on Wednesday by line" onBlock={onBlock} onSlot={onSlot} newTo={(line) => `/new?line=${line}`} />, { role: "operator", writesEnabled: true });
  const group = screen.getByRole("group", { name: "Runs on Wednesday by line" });
  expect(group.getAttribute("tabindex")).toBe("0");
  const blocks = [...group.querySelectorAll<HTMLElement>("button.sch-block")];
  expect(blocks.map((block) => block.getAttribute("tabindex"))).toEqual(blocks.map(() => "-1"));
  expect(blocks.map((block) => block.getAttribute("aria-label")?.slice(0, 13))).toEqual(["L01 Wall drip", "L04 Center mi"]);
  group.focus();
  fireEvent.keyDown(group, { key: "ArrowRight" });
  expect(document.activeElement).toBe(blocks[0]);
  fireEvent.keyDown(group, { key: "End" });
  expect(document.activeElement).toBe(blocks.at(-1));
  fireEvent.click(blocks[0]);
  expect(onBlock).toHaveBeenCalledWith(expect.objectContaining({ blockKey: "11-1793802900" }), blocks[0]);
  // A click on a block never makes a new schedule; one on the empty track does, rounded to 15 min.
  expect(onSlot).not.toHaveBeenCalled();
  const track = group.querySelector<HTMLElement>('.sch-row[data-line="L09"] .sch-track')!;
  track.getBoundingClientRect = () => ({ left: 0, width: 960, top: 0, height: 28, right: 960, bottom: 28, x: 0, y: 0, toJSON: () => ({}) });
  fireEvent.click(track, { clientX: 960 * (15.1 / 24) });
  expect(onSlot).toHaveBeenCalledWith("L09", "15:00");
  expect(screen.getByRole("link", { name: "New schedule on L09 West mister" }).getAttribute("href")).toBe("/new?line=L09");
});

test("a block's words take the room beside it: all of them, else its time and outcome (a planned one its time); the end column's head names the day", () => {
  expect(blockWords("15:00 skipped · RH 92%", "skipped", () => true)).toBe("15:00 skipped · RH 92%");
  expect(blockWords("15:00 skipped · RH 92%", "skipped", () => false)).toBe("15:00 skipped");
  expect(blockWords("07:31 missed · no valid clock", "missed", () => false)).toBe("07:31 missed");
  expect(blockWords("07:31 · running", "running", () => false)).toBe("07:31 running");
  expect(blockWords("16:00 · 10 min", "planned", () => false)).toBe("16:00");
  // The room is to the next block on the row, or to the row's end; a block late in the day looks back to the one before.
  const day = model();
  const scale = timeScale(day.from, day.to);
  const hour = (h: number) => day.from + h * 3600;
  const block = (key: string, start: number, end: number) => ({ key, start, end } as DayBlock);
  const rooms = blockRooms([block("b", hour(12), hour(13)), block("a", hour(6), hour(7)), block("c", hour(22), hour(23))], scale);
  expect(rooms.get("a")).toBeCloseTo(50 - 7 * 100 / 24, 6);
  expect(rooms.get("b")).toBeCloseTo((22 - 13) * 100 / 24, 6);
  expect(rooms.get("c")).toBeCloseTo((22 - 13) * 100 / 24, 6);
  // Drawn: L01's 12-minute run is too short for its words, which sit beside it whole, "07:35 ok", not its time alone.
  renderUi(<DayTimeline model={day} now={NOW} head={<h2>Monday</h2>} label="Runs on Monday by line" />);
  expect(text(document.querySelector('.sch-row[data-line="L01"] .sch-block-label'))).toBe("07:35 ok");
  expect(document.querySelector('.sch-row[data-line="L01"] .sch-block')?.classList.contains("sch-narrow")).toBe(true);
  expect(text(document.querySelector(".sch-axis-end"))).toBe("today / cap");
  cleanup();
  renderUi(<DayTimeline model={model("2026-10-30")} now={NOW} head={<h2>Friday</h2>} label="Runs" />);
  expect(text(document.querySelector(".sch-axis-end"))).toBe("that day / cap");
  cleanup();
  renderUi(<DayTimeline model={model("2026-11-04")} now={NOW} head={<h2>Wednesday</h2>} label="Runs" />);
  expect(text(document.querySelector(".sch-axis-end"))).toBe("planned / cap");
});

test("a strip without API-05's answer says its valve time is unknown and hatches it up to now; with the answer and nothing in it, none", () => {
  const day = model();
  renderUi(<>
    <DayStrip name="L04 Center mister" from={day.from} to={day.to} night={[]} blocks={[]} episodes={[]} valveKnown={false} now={NOW} token="mister" />
    <DayStrip name="L09 West mister" from={day.from} to={day.to} night={[]} blocks={[]} episodes={[]} now={NOW} token="mister" />
  </>);
  expect(screen.getByRole("img", { name: "L04 Center mister today: valve time unknown" }).querySelectorAll(".sch-tick-unknown")).toHaveLength(1);
  expect(screen.getByRole("img", { name: "L09 West mister today: no runs and no valve time" }).querySelector(".sch-tick")).toBeNull();
  // The timeline's rows say the same in words when the day's API-05 has not answered.
  cleanup();
  const unread = dayModel({ day: TODAY, today: TODAY, now: NOW, agenda, lines: status.lines, lanes: null, entries });
  renderUi(<DayTimeline model={unread} now={NOW} head={<h2>Monday</h2>} label="Runs" />);
  expect(document.querySelector('.sch-row[data-line="L04"] .sch-track')?.textContent).toContain("valve time unknown");
  expect(document.querySelectorAll(".sch-tick-unknown")).toHaveLength(9);
});

test("a viewer's timeline offers no new schedule: no [+], and the track takes no click", () => {
  renderUi(<DayTimeline model={model()} now={NOW} head={<h2>Monday</h2>} label="Runs on Monday by line" />);
  expect(document.querySelector(".sch-add, .sch-track-new")).toBeNull();
  // Without onBlock the blocks are words, not buttons.
  expect(document.querySelectorAll("button.sch-block")).toHaveLength(0);
});

test("with the API down the timeline, the strip, the week and the agenda go neutral: nothing green, no verdict colour", () => {
  const down = { role: "viewer" as const, writesEnabled: false, apiDown: true };
  const cells = weekCells(mondayOf(TODAY), { agenda, lines: status.lines, today: TODAY, first: "2026-10-26", last: "2026-11-08" });
  renderUi(<>
    <DayTimeline model={model()} now={NOW} head={<h2>Monday</h2>} label="Runs" />
    <DayStrip name="L04" from={model().from} to={model().to} night={[]} blocks={[]} episodes={[]} now={NOW} token="mister" />
    <WeekStrip cells={cells} selected={TODAY} dayTo={(date) => `/d/${date}`} previousTo={null} nextTo={null} />
    <Agenda days={agendaDays(entries, { first: TODAY, days: 1, last: "2026-11-08", today: TODAY, now: NOW, lines: status.lines })} />
  </>, down);
  expect([".sch-day", ".wat-day", ".sch-week", ".sch-agenda"].map((selector) => document.querySelector(selector)?.classList.contains("is-neutral"))).toEqual([true, true, true, true]);
});

test("the week strip: seven days with their summaries and levels, the selected one current; a day outside what is served is not a link", () => {
  const cells = weekCells("2026-11-02", { agenda, lines: status.lines, today: TODAY, first: "2026-10-26", last: "2026-11-05" });
  renderUi(<WeekStrip cells={cells} selected="2026-11-04" dayTo={(date) => `/d/${date}`} previousTo="/d/2026-10-28" nextTo={null} />);
  const list = screen.getByRole("list", { name: "Week of Nov 2 – 8" });
  expect([...list.querySelectorAll("li")].map((item) => text(item.querySelector(".sch-day-summary")))).toEqual(["2 ok", "1 next", "2 next", "1 next", "no data", "no data", "no data"]);
  expect(screen.getAllByRole("link").filter((link) => link.closest("ol")).map((link) => link.getAttribute("aria-current"))).toEqual([null, null, "date", null]);
  expect([...list.querySelectorAll(".sch-day-level")].map((level) => level.getAttribute("data-level"))).toEqual(["0", "ahead", "ahead", "ahead", "none", "none", "none"]);
  expect(list.querySelector('.sch-day-level[data-partial="true"]')).not.toBeNull();
  expect(screen.getByRole("link", { name: "Previous week" }).getAttribute("href")).toBe("/d/2026-10-28");
  expect(screen.queryByRole("link", { name: "Next week" })).toBeNull();
});

test("the agenda: a row is a button that opens its popover, NEXT on the next planned run, and the pauses above the days", () => {
  const onOpen = vi.fn();
  renderUi(<Agenda days={agendaDays(entries, { first: TODAY, days: 2, last: "2026-11-08", today: TODAY, now: NOW, lines: status.lines })} pauses={["L01 Wall drip: paused until 14:00 by jason"]} onOpen={onOpen} />);
  expect(text(document.querySelector(".sch-agenda-pauses"))).toBe("L01 Wall drip: paused until 14:00 by jason");
  const rows = screen.getAllByRole("button");
  expect(rows.map((row) => row.getAttribute("aria-label")?.replaceAll(" ", " "))).toEqual([
    "06:50 L04 Center mister · 8 × 60 s, Dawn drench · ok, done", "07:35 L01 Wall drip · 12 min, Wall MWF · ok, done", "06:50 L04 Center mister · 8 × 60 s, Dawn drench · Iris may adjust, next",
  ]);
  fireEvent.click(rows[2]);
  expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ next: true, lineId: "L04" }), rows[2]);
});

test("a session provider is all the timeline needs to render for a viewer, with no router write path", () => {
  const html = renderToString(<MemoryRouter><SessionProvider role="viewer"><DayTimeline model={model()} now={NOW} head={null} label="Runs" /></SessionProvider></MemoryRouter>);
  expect(html).not.toContain("data-write-control");
});
