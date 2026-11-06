// SPDX-License-Identifier: MIT
import type { CalendarDay } from "../../lib/operator-api";
import { dayTitle } from "../../lib/format";
import { armWords } from "../../lib/planner";
import { LocalTime } from "../common";

/** A cell's words: "Dec 1: AI planner, pair 1, 1 deviation (esp32_reboot), today". */
export function cellWords(day: CalendarDay, today: string): string {
  const parts = [armWords(day.arm)];
  if (day.pair_index != null) parts.push(`pair ${day.pair_index + 1}`);
  if (day.deviations > 0) parts.push(`${day.deviations} ${day.deviations === 1 ? "deviation" : "deviations"}${day.events.length ? ` (${day.events.join(", ")})` : ""}`);
  if (day.status === "excluded") parts.push("excluded");
  if (day.date === today) parts.push("today");
  else if (day.date > today) parts.push("to come");
  return `${dayTitle(day.date, "short")}: ${parts.join(", ")}`;
}

/**
 * The arm calendar (#1361): one cell per study day (11:00 to 11:00 local), coloured by arm, a deviation marked with a
 * triangle, today outlined and days to come hollow, then the next boundary. Each cell's label says it all in words.
 */
export function ArmCalendar({ days, today, nextBoundary }: { days: readonly CalendarDay[]; today: string; nextBoundary: string | null }) {
  if (days.length === 0) return <p className="muted">No study day is assigned yet.</p>;
  return <div className="pln-calendar">
    <ol className="pln-calendar-days" aria-label="Study days by arm">
      {days.map((day) => <li key={`${day.date}-${day.pair_index}`} className="pln-day" data-arm={day.arm ?? "none"} data-deviation={day.deviations > 0 || undefined}
        data-when={day.date === today ? "today" : day.date > today ? "future" : "past"} aria-current={day.date === today ? "date" : undefined}
        title={cellWords(day, today)}>
        <span className="screen-reader">{cellWords(day, today)}</span>
        <span className="pln-day-num" aria-hidden="true">{Number(day.date.slice(8))}</span>
        {day.deviations > 0 && <span className="pln-day-mark" aria-hidden="true">▲</span>}
      </li>)}
    </ol>
    <p className="pln-calendar-legend meta">
      <span className="pln-key" data-arm="ai" aria-hidden="true" /> AI planner <span className="pln-key" data-arm="baseline" aria-hidden="true" /> Frozen baseline
      <span aria-hidden="true"> ▲</span> deviation
      {nextBoundary && <> · next boundary <LocalTime at={nextBoundary} date /></>}
    </p>
  </div>;
}
