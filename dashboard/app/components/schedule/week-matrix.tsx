// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import { shortTime } from "../../lib/format";
import { cls } from "../common";
import { ScrollRegion } from "../layout/page";
import { LANE_GROUPS, STATUS_WORDS, matrixCell, spanStatus, type Entry, type LineInfo } from "./model";

const WEEKDAY = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short" });
/** A column's day: "Mon 11/2". */
export function weekDayLabel(day: string): string {
  const [, month, date] = day.split("-").map(Number);
  return `${WEEKDAY.format(new Date(`${day}T12:00:00Z`))} ${month}/${date}`;
}

export type WeekMatrixProps = {
  /** Monday to Sunday of the selected week. */
  days: readonly string[];
  today: string;
  entries: readonly Entry[];
  info: LineInfo;
  /** A cell opens that day's timeline. */
  dayTo: (date: string) => string;
};

/**
 * The week matrix (`view=week`, redesign §6.3), from 861 px: one row per physical line, drips then misters (#1347), the seven days across, a fertilized run's mark in the feed colour. Each cell has
 * one mark per occurrence (solid done, outline planned, hatched when it will not run, a fault outline when missed, a
 * fallback window dotted) and
 * the minutes against the line's cap, "22 / 60", amber with "cap" past it. A cell opens its day. Below 1100 px it scrolls
 * inside its own labelled region; the page never scrolls sideways.
 */
export function WeekMatrix({ days, today, entries, info, dayTo }: WeekMatrixProps) {
  return <ScrollRegion label="Week by line" className="sch-matrix-wrap">
    <table className="week-table sch-matrix">
      <caption className="screen-reader">Runs by line and day, with the minutes against each line's daily cap; America/Denver</caption>
      <thead><tr>
        <th scope="col">Line</th>
        {days.map((day) => <th key={day} scope="col" data-day={day} className={cls(day === today && "week-today")}>{weekDayLabel(day)}{day === today && <span className="week-today-word"> today</span>}</th>)}
      </tr></thead>
      {LANE_GROUPS.map((group) => <tbody key={group.key}>
        <tr className="table-group"><th scope="rowgroup" colSpan={days.length + 1}>{group.label}</th></tr>
        {group.lanes.map((lane) => {
          const lineId = lane.lines[0];
          const relays = lane.lines.map((id) => info.lineOf(id));
          const line = relays[0];
          const cap = line && line.daily_cap_min > 0 ? line.daily_cap_min : null;
          const off = relays.every((relay) => relay && !relay.enabled);
          const fertilized = new Set(lane.lines.slice(1));
          return <tr key={lane.id} data-line={lineId} data-lane={lane.id} className={cls(off && "is-muted")}>
            <th scope="row">{lane.name}{off && relays.every((relay) => relay?.reserved) && <span className="meta"> · reserved</span>}</th>
            {days.map((day) => {
              const cells = lane.lines.map((id) => matrixCell(entries, id, day));
              const cell = { entries: cells.flatMap((item) => item.entries).sort((a, b) => Date.parse(a.occurrence.planned_start) - Date.parse(b.occurrence.planned_start)), minutes: cells.reduce((sum, item) => sum + item.minutes, 0) };
              const over = cap !== null && cell.minutes > cap;
              const marks = cell.entries.map((entry) => ({ entry, status: spanStatus(entry.occurrence, entry.schedule), fert: fertilized.has(entry.occurrence.line_id) }));
              const words = marks.map(({ entry, status, fert }) => `${shortTime(entry.occurrence.planned_start)}${fert ? " fertilized" : ""} ${entry.draft ? "draft" : STATUS_WORDS[status]}`).join(", ");
              const total = cell.minutes > 0 || cap ? `${Math.round(cell.minutes)}${cap ? ` / ${cap}` : ""}` : null;
              return <td key={day} data-day={day} className={cls(day === today && "week-today", over && "is-over")}>
                {marks.length > 0 || total
                  ? <Link className="sch-cell" to={dayTo(day)} aria-label={`${lane.name}, ${weekDayLabel(day)}: ${marks.length ? words : "no runs"}${total ? `; ${total} min${over ? ", past the cap" : ""}` : ""}`}>
                    <span className="sch-marks" aria-hidden="true">{marks.map(({ entry, status, fert }) => <span key={`${entry.draft ? "d" : ""}${entry.occurrence.slot_id}-${entry.occurrence.planned_start}`}
                      className={cls("sch-mark", `sch-${status}`, fert ? "sch-mark-fert" : "sch-mark-clean", entry.draft && "sch-draft")} />)}</span>
                    {total && <span className="sch-cell-total" aria-hidden="true">{total}{over && <span className="sch-cell-over"> cap</span>}</span>}
                  </Link>
                  : <span className="sch-cell-none" aria-hidden="true">·</span>}
              </td>;
            })}
          </tr>;
        })}
      </tbody>)}
    </table>
  </ScrollRegion>;
}
