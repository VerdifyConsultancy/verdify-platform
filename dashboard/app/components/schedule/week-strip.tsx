// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import { dateShift } from "../../lib/format";
import { useApiDown } from "../../lib/session";
import { cls } from "../common";
import { weekCellName, type WeekCell } from "./model";

const DAY_NUMBER = (date: string) => Number(date.slice(8, 10));
const WEEKDAY = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short" });
const MONTH = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short" });
const noon = (date: string) => new Date(`${date}T12:00:00Z`);

export type WeekStripProps = {
  cells: readonly WeekCell[];
  /** The selected day (`day=`): the timeline's and the agenda's. */
  selected: string;
  /** The URL that selects a day, and those of the weeks before and after (null at the ends of what the API serves). */
  dayTo: (date: string) => string;
  previousTo: string | null;
  nextTo: string | null;
};

/**
 * The week strip on top of every Schedules view (redesign §6.3): Monday to Sunday of the selected week, past and future
 * side by side. Each day says what happened or what comes ("5 ran ok", "2 ok, 3 next", "6 next") over a level bar: quiet
 * when every outcome was ok, amber when one was short or capped, fault when one was missed, hatched with no data, today
 * outlined as partial. The total against the caps is in each day's name and title. Selecting a day sets `day=`; ‹ ›
 * move a week and stop at the ends of the days API-IRR-02 serves. While the API is down nothing reads green.
 */
export function WeekStrip({ cells, selected, dayTo, previousTo, nextTo }: WeekStripProps) {
  const apiDown = useApiDown();
  const first = cells[0]?.date ?? selected;
  const last = cells.at(-1)?.date ?? selected;
  const span = `${MONTH.format(noon(first))} ${DAY_NUMBER(first)} – ${MONTH.format(noon(last)) === MONTH.format(noon(first)) ? "" : `${MONTH.format(noon(last))} `}${DAY_NUMBER(last)}`;
  return <nav className={cls("sch-week", apiDown && "is-neutral")} aria-label="Week">
    {previousTo ? <Link className="button-link sch-week-step" to={previousTo} preventScrollReset><span aria-hidden="true">‹</span><span className="screen-reader">Previous week</span></Link>
      : <span className="sch-week-step is-off" aria-hidden="true">‹</span>}
    <ol className="week-days sch-week-days" aria-label={`Week of ${span}`}>
      {cells.map((cell) => {
        const name = weekCellName(cell);
        const body = <>
          <span className="sch-day-date"><span className="sch-day-weekday">{WEEKDAY.format(noon(cell.date))}</span> {DAY_NUMBER(cell.date)}{cell.when === "today" && <span className="sch-day-today"> today</span>}</span>
          <span className="sch-day-summary">{cell.summary}</span>
          <span className="sch-day-level" data-level={cell.level === null ? "none" : cell.level === undefined ? "ahead" : String(cell.level)} data-partial={cell.when === "today" ? "true" : undefined} aria-hidden="true" />
        </>;
        return <li key={cell.date} className={cls("sch-week-day", `is-${cell.when}`, cell.date === selected && "is-selected")}>
          {cell.inRange
            ? <Link to={dayTo(cell.date)} aria-current={cell.date === selected ? "date" : undefined} aria-label={name} title={name} preventScrollReset>{body}</Link>
            : <span className="sch-week-none" title={name}>{body}<span className="screen-reader">{name}</span></span>}
        </li>;
      })}
    </ol>
    {nextTo ? <Link className="button-link sch-week-step" to={nextTo} preventScrollReset><span aria-hidden="true">›</span><span className="screen-reader">Next week</span></Link>
      : <span className="sch-week-step is-off" aria-hidden="true">›</span>}
  </nav>;
}

/** The selected week's Monday-based neighbours, inside the days the read serves: a day in the week before or after, or null. */
export function weekStep(selected: string, monday: string, direction: -1 | 1, first: string, last: string): string | null {
  const target = dateShift(selected, 7 * direction);
  const start = dateShift(monday, 7 * direction);
  const end = dateShift(start, 6);
  if (end < first || start > last) return null;
  return target < first ? first : target > last ? last : target;
}
