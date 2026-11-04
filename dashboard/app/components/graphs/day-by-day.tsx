// SPDX-License-Identifier: MIT
import { useLayoutEffect, useRef } from "react";
import { dayTitle, formatValue } from "../../lib/format";
import { dayHref } from "../../lib/graph-url";
import { useApiDown } from "../../lib/session";
import { cls } from "../common";
import { StatusStrip, StripLegend } from "../data/status-strip";
import { cssVars } from "../data/viz";
import { STRIP_ROWS, type StatDay } from "./stats-model";

/**
 * "Day by day" (redesign §5.7): a `StatusStrip` row each for temperature in band, VPD in band, Iris missed and alerts
 * raised, then the gallons as bars on the same cells, over one row of dates. Every cell opens that day in Climate; quiet
 * when good, loud when bad, a day with no data hatched and today outlined. The rows share one scroll region, so their
 * cells stay in line; a long window scrolls inside it and opens at the newest day.
 */
export function DayByDay({ days }: { days: readonly StatDay[] }) {
  const region = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = region.current;
    if (element) element.scrollLeft = element.scrollWidth;
  }, [days.length]);
  const first = days[0], last = days[days.length - 1];
  return <div className="gr-days-wrap">
    <div ref={(element) => { region.current = element; cssVars({ "--gr-days": days.length })(element); }} className="gr-days l-scroll-x" role="region" tabIndex={0}
      aria-label={`Day by day, ${first ? dayTitle(first.date, "short") : ""} to ${last ? dayTitle(last.date, "short") : ""}`}>
      <div className="gr-days-grid">
        {STRIP_ROWS.map((row) => <div key={row.key} className="gr-days-row">
          <span className="gr-days-label">{row.label}</span>
          <StatusStrip label={`${row.label}, day by day`} cells={days.map((day) => ({
            key: day.date, level: row.level(day), label: row.words(day), partial: day.partial, href: dayHref(day.date),
          }))} />
        </div>)}
        <div className="gr-days-row">
          <span className="gr-days-label">Gallons</span>
          <GallonsRow days={days} />
        </div>
        <div className="gr-days-row gr-days-axis-row" aria-hidden="true">
          <span className="gr-days-label" />
          <DayAxis days={days} />
        </div>
      </div>
    </div>
    <StripLegend />
  </div>;
}

/** The gallons as bars in the strip's cells: a day with no meter reading hatched, never 0; today outlined. */
function GallonsRow({ days }: { days: readonly StatDay[] }) {
  const apiDown = useApiDown();
  const values = days.map((day) => day.values.water);
  const known = values.filter((value): value is number => value !== null);
  const peak = Math.max(1, ...known);
  const total = known.reduce((sum, value) => sum + value, 0);
  const label = known.length
    ? `Gallons per day, ${dayTitle(days[0].date, "short")} to ${dayTitle(days[days.length - 1].date, "short")}: ${formatValue(Math.min(...known), "gal")} to ${formatValue(Math.max(...known), "gal")} a day, ${formatValue(total, "gal")} in all. The data table below has each day.`
    : "Gallons per day: no meter readings in this period";
  // The strip's own 2 px inset, so these cells sit under its cells; an image named by its numbers, not a list.
  return <div className="gr-days-pad">
    <div className={cls("gr-gallons", apiDown && "is-neutral")} role="img" aria-label={label}>
      {days.map((day) => <span key={day.date} className="gr-bar-cell" data-none={day.values.water === null ? "true" : undefined} data-partial={day.partial ? "true" : undefined}>
        {day.values.water !== null && <span className="gr-bar" ref={cssVars({ "--gr-h": `${Math.round((day.values.water / peak) * 100)}%` })} />}
      </span>)}
    </div>
  </div>;
}

/** Dates under the cells: every seventh day back from the newest (14th or 28th on longer windows), so the newest is named. */
function DayAxis({ days }: { days: readonly StatDay[] }) {
  const lastIndex = days.length - 1;
  const step = days.length > 120 ? 28 : days.length > 45 ? 14 : 7;
  const place = (index: number) => (index === lastIndex ? "gr-days-tick gr-days-tick-end" : index < 3 ? "gr-days-tick gr-days-tick-start" : "gr-days-tick");
  return <div className="gr-days-pad">
    <ol className="strip gr-days-axis">
      {days.map((day, index) => <li key={day.date}>{(lastIndex - index) % step === 0 && <span className={place(index)}>{dayTitle(day.date, "short")}</span>}</li>)}
    </ol>
  </div>;
}
