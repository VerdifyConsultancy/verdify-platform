// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import { csvCell } from "../../charts/csv";
import { CsvLink } from "../../charts/data-table";
import { localDay } from "../../charts/time-axis";
import type { SeriesColumns } from "../../charts/types";
import { dateShift, formatValue, localDayStart } from "../../lib/format";
import { DAILY_GROUP, dayHref } from "../../lib/graph-url";
import { ZONE_WORDS, type MinutesZone } from "../../lib/lines";
import type { MetricDef, MinutesReport, Series } from "../../lib/operator-api";
import { words } from "../../lib/words";

/**
 * The daily table of Graphs → Statistics' "Data" (redesign §5.7, US-GRF-09, the former scorecard): one row per
 * America/Denver day of the window, newest first, from API-04's `daily.*` keys (`v_daily_scorecard`) and API-IRR-10's
 * minutes by zone. Rows are keyed by the America/Denver date of each bucket's `t`, never by adding 86,400 s, so a 23- or
 * 25-hour day counts as it ran. A missing value reads "—" with its reason, never 0: the bucket's `SeriesColumns.reason`
 * in words, else "no data that day", and for minutes a `no_data` day reads "no data (outage)". Whole-day time in band
 * has a value every day; the day/night split only for the 3 days `mv_zone_band_grade` keeps (Q-12). Each day opens in
 * Climate. "CSV of this table" is exactly these rows.
 */
export const SCORECARD_DAYS = 30;
export type ScorecardColumn = {
  readonly id: string;
  /** The header over a run of columns ("Time in band"); null spans both header rows. */
  readonly group: string | null;
  readonly header: string;
  /** Canonical unit; `count` is a whole number. */
  readonly unit: string;
  /** An API-04 `daily.*` key. */
  readonly key?: string;
  /** An API-IRR-10 zone. */
  readonly zone?: MinutesZone;
};
const daily = (key: string, group: string | null, header: string, unit: string): ScorecardColumn => ({ id: key, key, group, header, unit });
const minutes = (zone: MinutesZone): ScorecardColumn => ({ id: `minutes.${zone}`, zone, group: "Watering minutes", header: ZONE_WORDS[zone], unit: "min" });
/** The design's columns, keyed by VD-56's registry keys. A `daily` key API-03 lists beyond these follows as its own column. */
export const SCORECARD_COLUMNS: readonly ScorecardColumn[] = [
  daily("daily.temp_in_band_pct", "Time in band", "Temperature, whole day", "%"),
  daily("daily.vpd_in_band_pct", "Time in band", "VPD, whole day", "%"),
  daily("daily.time_in_band_day_pct", "Time in band", "Day (last 3 days)", "%"),
  daily("daily.time_in_band_night_pct", "Time in band", "Night (last 3 days)", "%"),
  daily("daily.water_gal", null, "Water", "gal"),
  minutes("wall"), minutes("center"), minutes("south"), minutes("west"),
  daily("daily.iris_required_written", "Iris required plans", "Written", "count"),
  daily("daily.iris_required_missed", "Iris required plans", "Missed", "count"),
  daily("daily.alerts_critical", "Alerts raised", "Critical", "count"),
  daily("daily.alerts_warning", "Alerts raised", "Warning", "count"),
  daily("daily.alerts_info", "Alerts raised", "Info", "count"),
];
/** Why a cell is empty when the API gave no reason. */
export const NO_DATA_DAY = "no data that day";
export const NO_DATA_OUTAGE = "no data (outage)";
export const NOT_SERVED = "not served";
export const NOT_LOADED = "couldn't load";

export type ScorecardCell = { readonly value: number | null; readonly text: string; readonly reason: string | null };
export type ScorecardRow = {
  readonly date: string;
  /** The day's length as it ran: 25 on the fall-back, 23 on the spring-forward. */
  readonly hours: number;
  /** Today, or a day API-IRR-10 marks partial. */
  readonly partial: boolean;
  readonly cells: readonly ScorecardCell[];
};
export type ScorecardModel = { readonly columns: readonly ScorecardColumn[]; readonly rows: readonly ScorecardRow[] };

export type ScorecardWindow = { readonly from: string; readonly to: string; readonly start: string; readonly end: string };
/** The local days From to To: dates for API-IRR-10 (To included), and local midnights for API-04's [start, end). */
export function daysWindow(from: string, to: string): ScorecardWindow {
  return { from, to, start: localDayStart(from), end: localDayStart(dateShift(to, 1)) };
}
/** The 30 days ending today. */
export const scorecardWindow = (today: string, days = SCORECARD_DAYS): ScorecardWindow => daysWindow(dateShift(today, -(days - 1)), today);
/** Newest first. */
export const scorecardDays = (today: string, days = SCORECARD_DAYS): string[] => Array.from({ length: days }, (_, ago) => dateShift(today, -ago));
/** The window's days, newest first. */
export function windowDays(window: Pick<ScorecardWindow, "from" | "to">): string[] {
  const out: string[] = [];
  for (let day = window.to; day >= window.from; day = dateShift(day, -1)) out.push(day);
  return out;
}
/** A local day's length in hours, as it ran. */
export const dayHours = (date: string): number => Math.round((Date.parse(localDayStart(dateShift(date, 1))) - Date.parse(localDayStart(date))) / 3_600_000);

/** The design's columns, then any `daily` key API-03 lists that they do not name. */
export function scorecardColumns(metrics: readonly MetricDef[]): ScorecardColumn[] {
  const named = new Set(SCORECARD_COLUMNS.flatMap((column) => (column.key ? [column.key] : [])));
  const extra = metrics.filter((metric) => metric.group === DAILY_GROUP && !named.has(metric.key)).map((metric) => daily(metric.key, null, metric.label, metric.unit));
  return [...SCORECARD_COLUMNS, ...extra];
}

/** A value at its unit's decimals; counts whole. */
export function cellText(value: number, unit: string): string {
  return unit === "count" ? String(Math.round(value)) : formatValue(value, unit);
}
const valued = (value: number | null | undefined): value is number => value !== null && value !== undefined && Number.isFinite(value);
const missing = (reason: string): ScorecardCell => ({ value: null, text: "—", reason });

export type ScorecardInput = {
  readonly today: string;
  /** The days to show; the 30 ending today when omitted. */
  readonly window?: Pick<ScorecardWindow, "from" | "to">;
  /** API-03. */
  readonly metrics: readonly MetricDef[];
  /** The API-04 answers for the `daily.*` keys, at most 8 keys each. */
  readonly series: readonly (Series | null)[];
  /** Keys whose API-04 request failed. */
  readonly failedKeys?: ReadonlySet<string>;
  /** API-IRR-10 by zone; null when it failed (`minutesFailed`) or is not served. */
  readonly minutes: MinutesReport | null;
  readonly minutesFailed?: boolean;
};

export function buildScorecard(input: ScorecardInput): ScorecardModel {
  const columns = scorecardColumns(input.metrics);
  const listed = new Set(input.metrics.filter((metric) => metric.group === DAILY_GROUP).map((metric) => metric.key));
  const located = new Map<string, { columns: SeriesColumns; index: ReadonlyMap<string, number> }>();
  for (const series of input.series) {
    if (!series) continue;
    // Each bucket's own America/Denver date: the 25-hour 2026-11-01 is one row.
    const index = new Map(series.t.map((at, position) => [localDay(at), position]));
    for (const [key, values] of Object.entries(series.series)) located.set(key, { columns: values, index });
  }
  const coverage = new Map((input.minutes?.periods ?? []).map((period) => [period.start, period.coverage]));
  const zoneMinutes = new Map<string, number>();
  if (input.minutes?.group_by === "zone") {
    for (const row of input.minutes.rows) if (valued(row.minutes)) zoneMinutes.set(`${row.period}|${row.group}`, (zoneMinutes.get(`${row.period}|${row.group}`) ?? 0) + row.minutes);
  }
  const dailyCell = (column: ScorecardColumn, date: string): ScorecardCell => {
    const key = column.key!;
    if (!listed.has(key)) return missing(NOT_SERVED);
    if (input.failedKeys?.has(key)) return missing(NOT_LOADED);
    const found = located.get(key);
    const position = found?.index.get(date);
    if (!found || position === undefined) return missing(NO_DATA_DAY);
    const value = found.columns.avg[position];
    if (valued(value)) return { value, text: cellText(value, column.unit), reason: null };
    const reason = found.columns.reason?.[position];
    // Only a cell the API gives no reason for falls back: a null or zero-count bucket is a day with no data.
    return missing(reason ? words("scorecard_reason", reason) : NO_DATA_DAY);
  };
  const minutesCell = (zone: MinutesZone, date: string): ScorecardCell => {
    if (input.minutesFailed || !input.minutes) return missing(NOT_LOADED);
    const day = coverage.get(date);
    if (day === undefined) return missing(NO_DATA_DAY);
    if (day === "no_data") return missing(NO_DATA_OUTAGE);
    const value = zoneMinutes.get(`${date}|${zone}`) ?? 0;
    return { value, text: cellText(value, "min"), reason: null };
  };
  const rows = (input.window ? windowDays(input.window) : scorecardDays(input.today)).map((date): ScorecardRow => ({
    date, hours: dayHours(date), partial: date === input.today || coverage.get(date) === "partial",
    cells: columns.map((column) => (column.zone ? minutesCell(column.zone, date) : dailyCell(column, date))),
  }));
  return { columns, rows };
}

/** "25 h · partial": a day that did not run 24 hours, and a partial day. */
export function dayNote(row: Pick<ScorecardRow, "hours" | "partial">): string {
  return [row.hours !== 24 ? `${row.hours} h` : null, row.partial ? "partial" : null].filter(Boolean).join(" · ");
}
const DAY_LABEL = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" });
/** "Sun, Nov 1" for 2026-11-01. */
export const dayLabel = (date: string): string => DAY_LABEL.format(new Date(`${date}T12:00:00Z`));

/** A column's CSV header: "Time in band: Day (last 3 days) (%)". */
export const csvHeader = (column: ScorecardColumn): string => `${column.group ? `${column.group}: ` : ""}${column.header} (${column.unit})`;
/**
 * "CSV of this table": the rows shown with the same columns, the date as YYYY-MM-DD with the day's note, each value as
 * the number in its canonical unit and each "—" with its reason as text. A byte-order mark and CRLF rows, as the charts'.
 */
export function scorecardCsv(model: ScorecardModel): string {
  const lines = [["Date", ...model.columns.map(csvHeader)], ...model.rows.map((row) => {
    const note = dayNote(row);
    return [`${row.date}${note ? ` (${note})` : ""}`, ...row.cells.map((cell) => (cell.value !== null ? String(cell.value) : `— ${cell.reason ?? NO_DATA_DAY}`))];
  })];
  return `\uFEFF${lines.map((cells) => cells.map(csvCell).join(",")).join("\r\n")}\r\n`;
}

type HeaderRun = { group: string | null; columns: ScorecardColumn[] };
function headerRuns(columns: readonly ScorecardColumn[]): HeaderRun[] {
  const runs: HeaderRun[] = [];
  for (const column of columns) {
    const last = runs[runs.length - 1];
    if (last && column.group !== null && last.group === column.group) last.columns.push(column);
    else runs.push({ group: column.group, columns: [column] });
  }
  return runs;
}

export function ScorecardTable({ model, window }: { model: ScorecardModel; window: ScorecardWindow }) {
  const runs = headerRuns(model.columns);
  // The page rebuilds the model on every poll and clock tick; the file is made again only when its text changes, so the
  // link a person is about to use is never swapped under them.
  const csv = scorecardCsv(model);
  return <>
    <div className="scorecard-tools">
      <CsvLink label="CSV of this table" filename={`verdify-scorecard-${window.from}-${window.to}.csv`} build={() => csv} version={csv} />
    </div>
    <div className="table-wrap scorecard-wrap" role="region" tabIndex={0} aria-label="Daily scorecard, scrolls sideways">
      <table className="scorecard">
        <caption>{model.rows.length} days, newest first · America/Denver</caption>
        <thead>
          <tr>
            <th rowSpan={2} scope="col">Date</th>
            {runs.map((run) => (run.group === null
              ? <th key={run.columns[0].id} rowSpan={2} scope="col">{run.columns[0].header}</th>
              : <th key={run.group} colSpan={run.columns.length} scope="colgroup">{run.group}</th>))}
          </tr>
          <tr>{model.columns.filter((column) => column.group !== null).map((column) => <th key={column.id} scope="col">{column.header}</th>)}</tr>
        </thead>
        <tbody>
          {model.rows.map((row) => {
            const note = dayNote(row);
            return <tr key={row.date} data-date={row.date}>
              <th scope="row"><Link to={dayHref(row.date)}><time dateTime={row.date}>{dayLabel(row.date)}</time></Link>{note && <span className="scorecard-note"> · {note}</span>}</th>
              {row.cells.map((cell, index) => <td key={model.columns[index].id} className={cell.value === null ? "scorecard-missing" : undefined}>
                {cell.value === null ? <>—{cell.reason && <span className="scorecard-reason"> {cell.reason}</span>}</> : cell.text}
              </td>)}
            </tr>;
          })}
        </tbody>
      </table>
    </div>
  </>;
}
