// SPDX-License-Identifier: MIT
import { useMemo } from "react";
import { dateShift, formatValue, localDate, localDayStart } from "../../lib/format";
import { apiGet, type StateLanes } from "../../lib/operator-api";
import { usePolledQuery } from "../../lib/use-current";
import { Sparkline } from "../data/sparkline";
import { finite } from "../data/viz";
import { Details } from "../details";
import { useServerSkew } from "../freshness";
import { Panel, type PanelState } from "../panel";

/**
 * Daily runtime (redesign §5.12): how long each heater, fan, the vent and fog ran on each of the last 14 America/Denver
 * days, from API-05's intervals (it serves up to 31 days). One bar row per actuator on one shared scale, so a heater's
 * hours and a fan's minutes compare at a glance, with the daily average over the complete days; the table under it is
 * the same numbers in words. A day the API marks unknown in part is partial, and one with no known time is a gap, never 0.
 */
export const RUNTIME_DAYS = 14;
export const RUNTIME_KEYS = ["heat1", "heat2", "fan1", "fan2", "vent", "fog"] as const;
/** API-05's 14-day read changes slowly: every 10 minutes is enough for today's bar. */
export const RUNTIME_POLL_MS = 10 * 60_000;
const DAY_MS = 86_400_000;

export type RuntimeDay = { readonly date: string; readonly seconds: number | null; readonly partial: boolean };
export type RuntimeRow = { readonly equipment: string; readonly label: string; readonly days: readonly RuntimeDay[]; readonly average: number | null };

/** The local days ending `today`, oldest first. */
export const runtimeDates = (today: string, days = RUNTIME_DAYS): string[] => Array.from({ length: days }, (_, back) => dateShift(today, back - days + 1));

const overlap = (start: number, end: number, from: number, to: number) => Math.max(0, Math.min(end, to) - Math.max(start, from));

/**
 * Each actuator's time on per local day from API-05: on spans summed within the day, a day with unknown time partial,
 * a day with no known time (or outside the window API-05 answered) null. The average is over the complete days only.
 */
export function dailyRuntime(window: StateLanes, dates: readonly string[], keys: readonly string[] = RUNTIME_KEYS): RuntimeRow[] {
  const windowFrom = Date.parse(window.from), windowTo = Date.parse(window.to);
  return keys.flatMap((key) => {
    const lane = window.lanes.find((item) => item.equipment === key);
    if (!lane) return [];
    const spans = lane.intervals.map((span) => ({ start: Date.parse(span.start), end: Date.parse(span.end), state: span.state }))
      .filter((span) => Number.isFinite(span.start) && Number.isFinite(span.end) && span.end > span.start);
    const days = dates.map((date): RuntimeDay => {
      const start = Date.parse(localDayStart(date)), end = Date.parse(localDayStart(dateShift(date, 1)));
      const from = Math.max(start, windowFrom), to = Math.min(end, windowTo);
      if (!(to > from)) return { date, seconds: null, partial: true };
      let on = 0, seen = 0;
      for (const span of spans) {
        const part = overlap(span.start, span.end, from, to);
        if (!part || span.state === "unknown") continue;
        seen += part;
        if (span.state === "on") on += part;
      }
      if (seen === 0) return { date, seconds: null, partial: true };
      return { date, seconds: Math.round(on / 1000), partial: seen < end - start - 1000 };
    });
    const complete = days.filter((day) => !day.partial && day.seconds !== null).map((day) => day.seconds as number);
    const average = complete.length ? complete.reduce((sum, value) => sum + value, 0) / complete.length : null;
    return [{ equipment: key, label: lane.label ?? key, days, average }];
  });
}

/** "3 h 5 min", "12 min", "0 min"; "—" for a day with no known time. */
const minutesWords = (seconds: number | null | undefined) => (finite(seconds) ? formatValue(seconds / 60, "min") : "—");
const shortDay = (date: string) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${date}T12:00:00Z`));

/** API-05 from the local midnight 13 days back to now on the server's clock, under a key that moves once a day. */
function useRuntimeWindow(today: string) {
  const skewMs = useServerSkew();
  const from = localDayStart(dateShift(today, 1 - RUNTIME_DAYS));
  return usePolledQuery<StateLanes>(["api", "API-05", { runtime_from: from }], (signal) => {
    const to = new Date(Math.max(Date.parse(from) + DAY_MS / 24, Date.now() - skewMs)).toISOString();
    return apiGet("API-05", { from, to, equipment: [...RUNTIME_KEYS] }, { signal });
  }, RUNTIME_POLL_MS);
}

function RuntimeRowView({ row, max }: { row: RuntimeRow; max: number }) {
  const values = row.days.map((day) => (day.seconds === null ? null : day.seconds / 3600));
  const most = row.days.reduce<RuntimeDay | null>((best, day) => (day.seconds !== null && (best === null || day.seconds > (best.seconds ?? -1)) ? day : best), null);
  const label = `${row.label}, time on per day for ${RUNTIME_DAYS} days: average ${minutesWords(row.average)} a day${most ? `, most ${minutesWords(most.seconds)} on ${shortDay(most.date)}` : ""}`;
  return <li className="ctl-runtime-row" data-equipment={row.equipment}>
    <span className="ctl-runtime-name">{row.label}</span>
    <span className="ctl-runtime-bars"><Sparkline values={values} kind="bars" size="tile" domain={{ low: 0, high: max }} label={label} /></span>
    <span className="ctl-runtime-average"><span className="ctl-runtime-figure">{minutesWords(row.average)}</span> <span className="meta">a day</span></span>
  </li>;
}

export function DailyRuntime({ today }: { today: string }) {
  const read = useRuntimeWindow(today);
  const data = read.data;
  const dates = useMemo(() => runtimeDates(today), [today]);
  const rows = useMemo(() => (data ? dailyRuntime(data, dates) : []), [data, dates]);
  const max = Math.max(1, ...rows.flatMap((row) => row.days.map((day) => (day.seconds ?? 0) / 3600)));
  const state: PanelState = data ? (rows.length ? "ready" : "empty") : read.isError ? "error" : "loading";
  return <Panel title={`Daily runtime · ${RUNTIME_DAYS} days`} thing="the daily runtime" state={state} reserve="md" onRetry={() => void read.refetch()}
    foot={data ? <span>Time on per America/Denver day; today so far. The average counts the complete days.</span> : undefined}>
    <ul className="ctl-runtime">{rows.map((row) => <RuntimeRowView key={row.equipment} row={row} max={max} />)}</ul>
    <Details summary="Daily runtime table" className="ctl-runtime-table">
      <div className="table-wrap" role="region" tabIndex={0} aria-label="Daily runtime table">
        <table>
          <caption className="screen-reader">Time on per day, America/Denver, oldest first</caption>
          <thead><tr><th scope="col">Day</th>{rows.map((row) => <th key={row.equipment} scope="col">{row.label}</th>)}</tr></thead>
          <tbody>{dates.map((date, index) => <tr key={date}>
            <th scope="row">{shortDay(date)}{date === today && <span className="meta"> (today)</span>}</th>
            {rows.map((row) => <td key={row.equipment} className="ctl-num">{minutesWords(row.days[index]?.seconds)}</td>)}
          </tr>)}</tbody>
        </table>
      </div>
    </Details>
  </Panel>;
}

/** Today's America/Denver date for a given epoch ms. */
export const localToday = (now: number): string => localDate(new Date(now).toISOString());
