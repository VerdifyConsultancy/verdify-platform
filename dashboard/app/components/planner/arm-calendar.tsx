// SPDX-License-Identifier: MIT
import type { CalendarDay } from "../../lib/operator-api";
import { dayTitle } from "../../lib/format";
import { DAY_STATUS_WORDS, armWords, dayEventWords } from "../../lib/planner";
import { LocalTime } from "../common";

const VOIDING = new Set(["paused", "stopped"]);

/**
 * A cell's words: "Dec 1: AI planner, pair 1, 1 deviation (boundary not confirmed), today". A void day (a pair a pause
 * or stop interrupted) says it runs again and why; a cancelled one that it never runs.
 */
export function cellWords(day: CalendarDay, today: string): string {
  const parts = [armWords(day.arm)];
  if (day.pair_index != null) parts.push(`pair ${day.pair_index + 1}`);
  const reasons = dayEventWords(day.events.filter((event) => !VOIDING.has(event)));
  if (day.deviations > 0) parts.push(`${day.deviations} ${day.deviations === 1 ? "deviation" : "deviations"}${reasons.length ? ` (${reasons.join(", ")})` : ""}`);
  const status = DAY_STATUS_WORDS[day.status];
  const why = dayEventWords(day.events.filter((event) => VOIDING.has(event)));
  if (status) parts.push(day.status === "void" && why.length ? `${status} (${why.join(", ")})` : status);
  if (day.date === today) parts.push("today");
  else if (day.date > today) parts.push("to come");
  return `${dayTitle(day.date, "short")}: ${parts.join(", ")}`;
}

/**
 * The arm calendar (#1361, #1360): one cell per study day (11:00 to 11:00 local), coloured by arm, a deviation marked
 * with a triangle, today outlined, days to come hollow and void or cancelled days struck through, then the next
 * boundary. Each cell's label says it all in words.
 */
export function ArmCalendar({ days, today, nextBoundary }: { days: readonly CalendarDay[]; today: string; nextBoundary: string | null }) {
  if (days.length === 0) return <p className="muted">No study day is assigned yet.</p>;
  const struck = days.some((day) => day.status === "void" || day.status === "cancelled");
  return <div className="pln-calendar">
    <ol className="pln-calendar-days" aria-label="Study days by arm">
      {days.map((day) => <li key={`${day.date}-${day.pair_index}`} className="pln-day" data-arm={day.arm ?? "none"} data-status={day.status}
        data-deviation={day.deviations > 0 || undefined} data-when={day.date === today ? "today" : day.date > today ? "future" : "past"}
        aria-current={day.date === today ? "date" : undefined} title={cellWords(day, today)}>
        <span className="screen-reader">{cellWords(day, today)}</span>
        <span className="pln-day-num" aria-hidden="true">{Number(day.date.slice(8))}</span>
        {day.deviations > 0 && <span className="pln-day-mark" aria-hidden="true">▲</span>}
      </li>)}
    </ol>
    <p className="pln-calendar-legend meta">
      <span className="pln-key" data-arm="ai" aria-hidden="true" /> AI planner <span className="pln-key" data-arm="baseline" aria-hidden="true" /> Frozen baseline
      <span aria-hidden="true"> ▲</span> deviation
      {struck && <> · struck through: void (runs again) or cancelled</>}
      {nextBoundary && <> · next boundary <LocalTime at={nextBoundary} date /></>}
    </p>
  </div>;
}
