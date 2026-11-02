// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// ScheduleWeek (#1135, design system §4.6) over #1122's schedules-paused week, at 10:42 on Monday 2026-11-02.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationStatus, ScheduleAgenda } from "../lib/operator-api";
import { NO_SCHEDULES_TEXT, ScheduleWeek, weekDayLabel } from "./schedule-week";
import { renderUi, text } from "./test-render";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const set = (names: string | string[]) => fixture(names, { now: NOW });
afterEach(cleanup);

function week(names: string | string[], past: boolean, agenda?: ScheduleAgenda) {
  const served = set(names);
  renderUi(<ScheduleWeek agenda={agenda ?? served["API-IRR-02"] as ScheduleAgenda} lines={(served["API-IRR-01"] as IrrigationStatus).lines} past={past} today={TODAY} now={NOW} />);
  const table = screen.getByRole("table");
  const days = [...table.querySelectorAll("thead th[data-day]")].map((cell) => cell.getAttribute("data-day"));
  const cell = (line: string, day: string) => table.querySelector<HTMLElement>(`tr[data-line="${line}"] td[data-day="${day}"]`)!;
  const blocks = (line: string, day: string) => [...cell(line, day).querySelectorAll(".week-block")].map((block) => ({
    head: text(block.querySelector(".week-block-head")), status: block.getAttribute("data-status"), hatched: block.classList.contains("week-block-hatched"),
    notes: [...block.querySelectorAll(".week-block-label, .week-block-detail, .week-block-note")].map((node) => text(node)),
  }));
  return { table, days, cell, blocks };
}

test("the next 7 days: each occurrence in its line × day cell with its time, amount, FERT and MANUAL or \"Iris may adjust\"", () => {
  const { days, blocks, cell, table } = week("schedules-paused", false);
  expect(days).toEqual(["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08"]);
  expect(text(screen.getByText(/schedules$/))).toBe("3 of 24 schedules");
  expect(text(table.querySelector("thead th.week-today"))).toBe("Mon 11/2 today");
  // L01 Wall MWF at sunrise + 60: this morning's has run (ok); Wednesday's and Friday's are ahead.
  expect(blocks("L01", "2026-11-02")).toEqual([{ head: "07:35 12m Manual", status: "done", hatched: false, notes: ["Wall MWF", "ok"] }]);
  expect(blocks("L01", "2026-11-04")).toEqual([{ head: "07:35 12m Manual", status: "planned", hatched: false, notes: ["Wall MWF"] }]);
  expect(text(cell("L01", "2026-11-03"))).toBe("·");
  // The steward's dawn drench on L04, which Iris may adjust; the Sunday feed on L02 with its stages.
  expect(blocks("L04", "2026-11-03")).toEqual([{ head: "06:50 8×60s Iris may adjust", status: "planned", hatched: false, notes: ["Dawn drench"] }]);
  expect(blocks("L02", "2026-11-08")).toEqual([{ head: "07:35 5m Fert Manual", status: "planned", hatched: false, notes: ["Weekly feed", "prewet 2 min · feed 5 min · flush 4 min"] }]);
  // Rows L01, L02 and L04–L10, the reserved center drips marked.
  expect([...table.querySelectorAll("tbody tr")].map((row) => row.getAttribute("data-line"))).toEqual(["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10"]);
  expect([...table.querySelectorAll("tbody th")].map((cellNode) => text(cellNode)).filter((label) => label.includes("Reserved"))).toEqual(["L05 Drip • Center Reserved", "L06 Drip • Center (Fert.) Reserved"]);
});

test("the last 7 days: a past L02 block is hatched and reads \"skipped · line paused\"; past runs read their outcome", () => {
  const { days, blocks } = week("schedules-paused", true);
  expect(days).toEqual(["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]);
  expect(blocks("L02", "2026-11-01")).toEqual([{ head: "07:35 5m Fert Manual", status: "skipped", hatched: true, notes: ["Weekly feed", "prewet 2 min · feed 5 min · flush 4 min", "skipped · line paused by jason"] }]);
  expect(blocks("L01", "2026-10-30")[0]).toMatchObject({ status: "done", notes: ["Wall MWF", "ok"] });
});

test("the seeded fallback reads \"only if VPD sensing is degraded\"; a week before the engine is hatched with why", () => {
  const { blocks } = week("in-band", false);
  expect(blocks("L04", "2026-11-03").map((block) => block.notes)).toEqual([
    ["vanda-degraded", "only if VPD sensing is degraded"], ["vanda-degraded", "only if VPD sensing is degraded"], ["vanda-degraded", "only if VPD sensing is degraded"],
  ]);
  expect(blocks("L04", "2026-11-03").map((block) => block.head)).toEqual(["09:47 4×45s Iris may adjust", "11:47 4×45s Iris may adjust", "13:47 4×45s Iris may adjust"]);
  cleanup();
  const engineless = week(["schedules-paused", "before-engine"], false);
  expect(engineless.blocks("L01", "2026-11-04")).toEqual([{ head: "07:35 12m Manual", status: "blocked", hatched: true, notes: ["Wall MWF", "arrives with the irrigation firmware"] }]);
});

test("at phone width a day switcher shows one day for every line, with no horizontal scroll", () => {
  const { table } = week("schedules-paused", false);
  const switcher = screen.getByRole("list", { name: "Day" });
  expect(within(switcher).getAllByRole("button").map((button) => text(button))).toEqual(["Mon 11/2", "Tue 11/3", "Wed 11/4", "Thu 11/5", "Fri 11/6", "Sat 11/7", "Sun 11/8"]);
  const shown = () => [...new Set([...table.querySelectorAll(".is-day")].map((node) => node.getAttribute("data-day")))];
  expect(shown()).toEqual(["2026-11-02"]);
  // One day's cell in every row, and its header.
  expect(table.querySelectorAll("td.is-day")).toHaveLength(9);
  fireEvent.click(within(switcher).getByRole("button", { name: "Wed 11/4" }));
  expect(shown()).toEqual(["2026-11-04"]);
  expect(within(switcher).getByRole("button", { name: "Wed 11/4" }).getAttribute("aria-pressed")).toBe("true");
  // The stylesheet hides every other day at 860 px and below, where the switcher shows.
  // jsdom's import.meta.url is no file URL: vitest runs from the dashboard package.
  const css = readFileSync(join(process.cwd(), "app/styles.css"), "utf8");
  const phone = css.slice(css.indexOf("@media(max-width:860px){\n  .water-bar"));
  expect(phone).toContain(".week-days{display:block");
  expect(phone).toContain(".week-table [data-day]:not(.is-day){display:none}");
  expect(phone).toContain(".week-table{min-width:0}");
  expect(weekDayLabel("2026-11-01")).toBe("Sun 11/1");
});

test("with no schedules the week says so", () => {
  const empty = { ...set("in-band")["API-IRR-02"], schedules: [], occurrences: [], counts: { schedules: 0, max_schedules: 24 } } as ScheduleAgenda;
  renderUi(<ScheduleWeek agenda={empty} past={false} today={TODAY} now={NOW} />);
  expect(screen.getByText(NO_SCHEDULES_TEXT)).toBeTruthy();
  expect(NO_SCHEDULES_TEXT).toBe("No schedules yet. Lines water only from VPD control and Run now.");
  expect(screen.queryByRole("table")).toBeNull();
});
