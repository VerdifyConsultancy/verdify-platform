// SPDX-License-Identifier: MIT
import { useEffect, useId, useMemo, useState } from "react";
import { Link, useSearchParams, type SetURLSearchParams } from "react-router";
import { useQueries } from "@tanstack/react-query";
import { causeWords } from "../charts/causes";
import { dayCsvFilename, tableCsv } from "../charts/csv";
import { DailyWaterUse } from "../charts/daily-water-use";
import { CsvLink } from "../charts/data-table";
import { MinutesByZone, lineWords } from "../charts/minutes-by-zone";
import { chartLinks } from "../charts/uplot-chart";
import { ChemistryBadge } from "../components/badge";
import { Button } from "../components/button";
import { Chips } from "../components/chips";
import { Delta } from "../components/data/delta";
import { MetricTile, type TileState } from "../components/data/metric-tile";
import { Sparkline } from "../components/data/sparkline";
import { lineLabel } from "../components/forms";
import { useClockValue } from "../components/freshness";
import { SectionHeader } from "../components/layout/section-header";
import { PanelError } from "../components/panel";
import { Table, type TableColumn } from "../components/table";
import { dateShift, localDayStart } from "../lib/format";
import { runGraphUrl } from "../lib/graph-link";
import { LINES } from "../lib/lines";
import { CAUSES, OUTCOMES, apiGet, type IrrigationLine, type Run, type RunPage } from "../lib/operator-api";
import { readKey, useApiRead, useSchedulesRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { usePolledQuery } from "../lib/use-current";
import {
  HISTORY_DAYS, RUNS_MAX_DAYS, RUNS_TALLY_MAX_DAYS, barsReport, historyDays, historyMeasure, historySummary, historyWindows, localDay, minutesCaps, minutesGroup,
  periodWords, plannedToday, runAmountWords, runGallons, runSpanWords, runsFilters, runsWindow, shortfallNote, shortfallWords, tallyMinutes, tallyRuns, toMinute,
  useWaterOutlet, waterUrl, waterUseSplit, zoneWord, type MinutesTally, type RunsTally,
} from "../lib/water";
import { words } from "../lib/words";

export const meta = () => viewMeta("water-history");
export const links = () => chartLinks();

const PATH = "/water/history";
/** The current period's reads every 60 s; the previous period's, which no longer changes, every 5 min. */
const HISTORY_POLL_MS = 60_000;
const PREVIOUS_POLL_MS = 300_000;
/** A runs tally reads the ledger's pages of 200 until they end, 10 pages at most. */
const TALLY_PAGE = 200;
const TALLY_PAGES = 10;
const NO_LINES: readonly IrrigationLine[] = [];
const LINE_IDS = LINES.map((line) => line.id);
const GROUPS = ["zone", "line", "cause"] as const;

/** The ledger counted over one window of local days (31 at most): every page of it, as a tally; null when not counted. */
function useRunsTally(window: { readonly from: string; readonly to: string }, counted: boolean, intervalMs: number) {
  const fromAt = localDayStart(window.from);
  const toAt = localDayStart(dateShift(window.to, 1));
  return usePolledQuery<RunsTally | null>(["water", "runs-tally", { from: fromAt, to: toAt, counted }], async (signal) => {
    if (!counted) return null;
    const runs: Run[] = [];
    let cursor: string | null = null;
    let pages = 0;
    do {
      const page: RunPage = await apiGet("API-IRR-11", { from: fromAt, to: toAt, limit: TALLY_PAGE, ...(cursor ? { cursor } : {}) }, { signal });
      runs.push(...page.items);
      cursor = page.next_cursor;
      pages += 1;
    } while (cursor && pages < TALLY_PAGES);
    return tallyRuns(runs, cursor === null);
  }, intervalMs);
}

/** The relative change in percent, or null when either side is unknown or the previous one is 0. */
const relative = (current: number | null | undefined, previous: number | null | undefined) =>
  typeof current === "number" && typeof previous === "number" && previous > 0 ? ((current - previous) / previous) * 100 : null;
const difference = (current: number | null | undefined, previous: number | null | undefined) =>
  typeof current === "number" && typeof previous === "number" ? current - previous : null;
const stateOf = (pending: boolean, failed: boolean): TileState => (failed ? "error" : pending ? "loading" : "ready");

/**
 * The KPI tiles (redesign §5.4): watering time, gallons, runs, and missed, short or at the cap, each against the previous
 * equal period. Minutes and gallons come from API-IRR-10 by line (up to 400 days); runs from the ledger, which is counted
 * over 31 days at most (API-IRR-11), so a longer period says so rather than guess.
 */
function Kpis({ days, current, previous, runs, runsBefore, counted, retry }: {
  days: number; current: { tally: MinutesTally | null; pending: boolean; failed: boolean }; previous: MinutesTally | null;
  runs: { tally: RunsTally | null; pending: boolean; failed: boolean }; runsBefore: RunsTally | null; counted: boolean; retry: { minutes: () => void; runs: () => void };
}) {
  const vs = `previous ${days} d`;
  const minutes = current.tally;
  const shortfall = runs.tally ? runs.tally.missed + runs.tally.short + runs.tally.cap : null;
  const shortfallBefore = runsBefore ? runsBefore.missed + runsBefore.short + runsBefore.cap : null;
  const notCounted = `Counted over ${RUNS_TALLY_MAX_DAYS} days at most`;
  const runsState = counted ? stateOf(runs.pending, runs.failed) : "ready";
  return <section className="wat-kpis" aria-label={`${periodWords(days)} at a glance`}>
    <div className="l-cols-4">
      <MetricTile label="Watering" data={{ "data-kpi": "watering" }} state={stateOf(current.pending, current.failed)} onRetry={retry.minutes}
        value={minutes && minutes.minutes !== null ? <MinutesValue minutes={minutes.minutes} /> : null} reason="No data in this period"
        delta={minutes && <Delta value={relative(minutes.minutes, previous?.minutes)} unit="%" vs={vs} />}
        visual={minutes && <Sparkline kind="bars" token="water" values={minutes.days.map((day) => day.minutes)} label={`Watering minutes per day, ${periodWords(days).toLowerCase()}`} />}
        meta={minutes && minutes.fog !== null && minutes.fog > 0 ? `Fog apart: ${Math.round(minutes.fog)}\u00a0min of humidification` : "Drips and misters; fog apart"} />
      <MetricTile label="Gallons" data={{ "data-kpi": "gallons" }} state={stateOf(current.pending, current.failed)} onRetry={retry.minutes}
        value={minutes && minutes.gal !== null ? <>{minutes.galMissing ? "≥ " : ""}{Math.round(minutes.gal)}<span className="metric-unit">{"\u00a0"}gal</span></> : null} reason="No meter data"
        delta={minutes && <Delta value={relative(minutes.gal, previous?.gal)} unit="%" vs={vs} />}
        visual={minutes && <Sparkline kind="bars" token="water" values={minutes.days.map((day) => day.gal)} label={`Gallons per day, ${periodWords(days).toLowerCase()}`} />}
        meta={minutes && minutes.galMissing ? "Some runs have no meter data" : "Verdify valves, from the site meter"} />
      <MetricTile label="Runs" data={{ "data-kpi": "runs" }} state={runsState} onRetry={retry.runs}
        value={counted && runs.tally ? <>{runs.tally.runs}{runs.tally.complete ? "" : "+"}</> : null} reason={counted ? "No data" : notCounted}
        delta={counted && runs.tally ? <Delta value={difference(runs.tally.runs, runsBefore?.runs)} vs={vs} /> : undefined}
        meta={counted ? "Valve-open runs, VPD episodes included" : <a href="#runs">Runs, {RUNS_MAX_DAYS} days at a time<span aria-hidden="true"> ›</span></a>} />
      <MetricTile label="Missed, short or cap" data={{ "data-kpi": "shortfall" }} state={runsState} onRetry={retry.runs} tone={shortfall ? "warn" : undefined}
        value={counted && runs.tally ? shortfallWords(runs.tally) : null} reason={counted ? "No data" : notCounted}
        delta={counted && runs.tally ? <Delta value={difference(shortfall, shortfallBefore)} vs={vs} /> : undefined}
        meta={counted && runs.tally ? shortfallNote(runs.tally.newest) ?? "Every counted run ended as planned" : undefined} />
    </div>
  </section>;
}
/** "6 h 12 min" with its units small. */
function MinutesValue({ minutes }: { minutes: number }) {
  const whole = Math.round(minutes);
  if (whole < 120) return <>{whole}<span className="metric-unit">{"\u00a0"}min</span></>;
  return <>{Math.floor(whole / 60)}<span className="metric-unit">{"\u00a0"}h</span>{whole % 60 ? <> {whole % 60}<span className="metric-unit">{"\u00a0"}min</span></> : null}</>;
}

/**
 * Minutes or gallons by zone, line or cause (#1127's bars, `MinutesByZone`) over the page's period, from API-IRR-10 by
 * line: no-data days hatched, today partial, cap ticks from the lines' caps and a dashed outline for the minutes still
 * planned today (API-IRR-02); fog apart, as humidification; "CSV of this chart".
 */
function MinutesSection({ read, days, lines, names, today, group, measure }: {
  read: ReturnType<typeof useApiRead<"API-IRR-10">>; days: number; lines: readonly IrrigationLine[]; names: Readonly<Record<string, string>>; today: string;
  group: (typeof GROUPS)[number]; measure: "min" | "gal";
}) {
  const headingId = useId();
  const agenda = useSchedulesRead();
  const now = useClockValue(toMinute);
  const planned = useMemo(() => plannedToday(agenda.data, lines, today, now), [agenda.data, lines, today, now]);
  const report = useMemo(() => (read.data ? barsReport(read.data) : null), [read.data]);
  // Noon today: which local day is today, steady all day so the bars do not rebuild every second.
  const noon = Date.parse(localDayStart(today)) / 1000 + 12 * 3600;
  const what = measure === "gal" ? "Gallons" : "Watering minutes";
  return <section className="wat-section" aria-labelledby={headingId}>
    <SectionHeader id={headingId} title={`${what} by ${group}`} meta={periodWords(days).toLowerCase()} />
    {read.isError && <PanelError thing={`the ${what.toLowerCase()}`} onRetry={() => void read.refetch()} />}
    {report
      ? <MinutesByZone report={report} group={group} caps={minutesCaps(lines, group)} planned={planned[group]} now={noon} lineNames={names} measure={measure === "gal" ? "gal" : "minutes"}
        title={`${what} per day by ${group}`} />
      : !read.isError && <div className="skeleton skeleton-lg" aria-hidden="true" />}
  </section>;
}

/**
 * Daily water use (#1140's bars) from API-21, which serves 30 or 90 days: 30 for a period up to 30 days and 90 above,
 * which its meta says; split by line or cause (`?water_use_by=`), with the water that ran while no Verdify valve was open
 * apart. Whole gallons only (Q-10).
 */
function WaterUseSection({ days, names, search }: { days: number; names: Readonly<Record<string, string>>; search: URLSearchParams }) {
  const headingId = useId();
  const served = days <= 30 ? 30 : 90;
  const split = waterUseSplit(search.get("water_use_by"));
  const use = useApiRead("API-21", { days: served }, { intervalMs: HISTORY_POLL_MS });
  const note = days < served ? `last ${served} days, the shortest this chart serves` : days > served ? `last ${served} days, the longest this chart serves` : `last ${served} days`;
  return <section className="wat-section" aria-labelledby={headingId}>
    <SectionHeader id={headingId} title="Daily water use" meta={note}
      chips={<Chips label="Split water use by" small items={(["line", "cause"] as const).map((value) => ({ key: value, label: value, to: waterUrl(PATH, search, { water_use_by: value === "line" ? null : "cause" }), current: split === value, keepScroll: true }))} />} />
    {use.isError && <PanelError thing="daily water use" onRetry={() => void use.refetch()} />}
    {use.data
      ? <DailyWaterUse data={use.data} split={split} lineNames={names} />
      : !use.isError && <div className="skeleton skeleton-lg" aria-hidden="true" />}
  </section>;
}

/** One run per row, its cells as the ledger reads them; whole gallons only, never a rate or a flow flag (Q-10). */
function runColumns(names: Readonly<Record<string, string>>, today: string): TableColumn<Run>[] {
  return [
    { key: "when", header: "Start–end", rowHeader: true, cell: (run) => runSpanWords(run, today) },
    { key: "line", header: "Line", cell: (run) => <>{lineWords(run.line_id, names)} <span className="muted">· {zoneWord(run.zone)}</span></> },
    { key: "water", header: "Water", cell: (run) => <ChemistryBadge chemistry={run.chemistry === "fert" ? "fert" : "clean"} /> },
    { key: "cause", header: "Cause", cell: (run) => causeWords(run.cause) },
    { key: "schedule", header: "Schedule", cell: (run) => run.schedule_label || "—" },
    { key: "actor", header: "Actor", cell: (run) => run.actor?.name ?? "controller" },
    { key: "minutes", header: "Minutes", className: "num", cell: (run) => runAmountWords(run) },
    {
      key: "gal", header: "Gallons", className: "num", cell: (run) => {
        const gallons = runGallons(run.gal);
        return gallons.reason ? <>{gallons.text} <span className="muted">{gallons.reason}</span></> : gallons.text;
      },
    },
    { key: "outcome", header: "Outcome", cell: (run) => <span className={run.outcome === "missed" ? "fault" : undefined}>{words("run_outcome", run.outcome)}{run.reason && <span className="muted"> · {run.reason}</span>}</span> },
    { key: "graphs", header: "Graphs", cell: (run) => <Link to={runGraphUrl(run)}>Graphs <span className="screen-reader">around this run</span><span aria-hidden="true"> ›</span></Link> },
  ];
}

/** The loaded runs as CSV: every column of the table, times in UTC and America/Denver words. */
function runsCsv(runs: readonly Run[], names: Readonly<Record<string, string>>, today: string): string {
  return tableCsv(
    ["started (UTC)", "ended (UTC)", "when (America/Denver)", "line", "zone", "water", "cause", "schedule", "actor", "valve-open minutes", "pulses", "gallons", "outcome", "reason"],
    runs.map((run) => [run.started_at, run.ended_at ?? null, runSpanWords(run, today), lineWords(run.line_id, names), zoneWord(run.zone), run.chemistry, run.cause, run.schedule_label ?? null,
      run.actor?.name ?? "controller", Math.round((run.open_s / 60) * 10) / 10, run.pulses ?? null, run.gal ?? null, run.outcome, run.reason ?? null]),
  );
}

/** One window of the ledger and the cursor into it: "Load older" follows the cursor, then the 31 days before. */
type Page = { readonly fromAt: string; readonly toAt: string; readonly cursor?: string };

/**
 * The run ledger (API-IRR-11, newest first, every 60 s): line, cause, outcome and dates applied on change and in the URL;
 * 31 days per window, the newest of the period unless From and To say otherwise; "Load older" follows the cursor, then
 * steps back 31 days inside the period; "CSV of these runs" downloads what is loaded; each run links to its Graphs window.
 * At phone width the rows stack.
 */
function RunsSection({ names, today, days, search, setSearch }: { names: Readonly<Record<string, string>>; today: string; days: number; search: URLSearchParams; setSearch: SetURLSearchParams }) {
  const headingId = useId();
  const explicit = search.get("from") !== null || search.get("to") !== null;
  const periodStart = dateShift(today, -(days - 1));
  const range = runsWindow(explicit ? search.get("from") : dateShift(today, -(Math.min(days, RUNS_MAX_DAYS) - 1)), search.get("to"), today);
  const filters = runsFilters(search);
  const base = { from: range.fromAt, to: range.toAt, ...filters };
  const pageKey = JSON.stringify([base, days]);
  const first = useApiRead("API-IRR-11", base, { intervalMs: HISTORY_POLL_MS });
  // Older pages: a cursor into a window, or the window before; a new window, period or filter starts again.
  const [older, setOlder] = useState<{ key: string; pages: readonly Page[] }>({ key: pageKey, pages: [] });
  const pages = older.key === pageKey ? older.pages : [];
  const reads = useQueries({
    queries: pages.map((page) => {
      const params = { ...filters, from: page.fromAt, to: page.toAt, ...(page.cursor ? { cursor: page.cursor } : {}) };
      return { queryKey: readKey("API-IRR-11", params), staleTime: Infinity, retry: false, queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("API-IRR-11", params, { signal }) };
    }),
  });
  const loaded = [first.data ?? null, ...reads.map((read) => read.data ?? null)];
  const loadingOlder = reads.some((read) => read.isPending);
  const failedOlder = reads.find((read) => read.isError);
  const last = pages.at(-1) ?? { fromAt: range.fromAt, toAt: range.toAt };
  const lastPage = loaded[loaded.length - 1];
  const lastFrom = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Denver", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(last.fromAt));
  const next: Page | null = lastPage?.next_cursor ? { fromAt: last.fromAt, toAt: last.toAt, cursor: lastPage.next_cursor }
    : !explicit && lastFrom > periodStart ? (() => {
      // The 31 local days before (30 if they hold the fall-back), never before the period.
      const toDay = dateShift(lastFrom, -1);
      const window = runsWindow(dateShift(toDay, -(RUNS_MAX_DAYS - 1)) < periodStart ? periodStart : dateShift(toDay, -(RUNS_MAX_DAYS - 1)), toDay, today);
      return { fromAt: window.fromAt, toAt: window.toAt };
    })() : null;
  const seen = new Set<string>();
  const runs = loaded.flatMap((page) => page?.items ?? []).filter((run) => !seen.has(run.run_id) && seen.add(run.run_id));
  const update = (changes: Readonly<Record<string, string | null>>) => setSearch((current) => {
    const nextSearch = new URLSearchParams(current);
    for (const [name, value] of Object.entries(changes)) {
      if (value === null || value === "") nextSearch.delete(name);
      else nextSearch.set(name, value);
    }
    return nextSearch;
  }, { replace: true, preventScrollReset: true });
  const span = range.from === range.to ? range.from : `${range.from} to ${range.to}`;
  return <section className="wat-section wat-runs" id="runs" aria-labelledby={headingId}>
    <SectionHeader id={headingId} title="Runs" meta={`newest first · ${explicit ? span : runs.length && pages.length ? `${periodWords(days).toLowerCase()} as loaded` : span}`} />
    <div className="filters runs-filters">
      <label>Line<select value={filters.line ?? ""} onChange={(event) => update({ line: event.target.value })}>
        <option value="">All lines</option>
        {LINE_IDS.map((id) => <option key={id} value={id}>{lineWords(id, names)}</option>)}
      </select></label>
      <label>Cause<select value={filters.cause ?? ""} onChange={(event) => update({ cause: event.target.value })}>
        <option value="">All causes</option>
        {CAUSES.map((cause) => <option key={cause} value={cause}>{causeWords(cause)}</option>)}
      </select></label>
      <label>Outcome<select value={filters.outcome ?? ""} onChange={(event) => update({ outcome: event.target.value })}>
        <option value="">All outcomes</option>
        {OUTCOMES.map((outcome) => <option key={outcome} value={outcome}>{words("run_outcome", outcome)}</option>)}
      </select></label>
      <label>From<input type="date" value={range.from} max={range.to} onChange={(event) => update({ from: event.target.value, to: range.to })} /></label>
      <label>To<input type="date" value={range.to} max={today} onChange={(event) => update({ to: event.target.value, from: range.from })} /></label>
      {explicit && <Button onClick={() => update({ from: null, to: null })}>Clear dates</Button>}
    </div>
    {explicit && range.clamped && <p className="meta">Runs show {RUNS_MAX_DAYS} days at a time: from {range.from}.</p>}
    {first.isError && <PanelError thing="runs" onRetry={() => void first.refetch()} />}
    {first.data
      ? <>
        <div className="wat-runs-table">
          <Table label="Runs" caption="Runs, newest first · America/Denver" captionHidden columns={runColumns(names, today)} rows={runs} rowKey={(run) => run.run_id}
            empty={<p className="meta">No runs in this window.{!explicit && days < 30 && <> <Link to={waterUrl(PATH, search, { range: "30d" })}>Show 30 days</Link></>}</p>} />
        </div>
        <ul className="wat-runs-list" aria-label="Runs, newest first">
          {runs.map((run) => <li key={run.run_id} className="wat-run" data-outcome={run.outcome}>
            <p className="wat-run-head"><strong>{runSpanWords(run, today)}</strong> · {lineWords(run.line_id, names)}</p>
            <p className="meta">{[causeWords(run.cause), run.schedule_label, runAmountWords(run), runGallons(run.gal).text].filter(Boolean).join(" · ")}</p>
            <p className="wat-run-foot"><span className={run.outcome === "missed" ? "fault" : undefined}>{words("run_outcome", run.outcome)}{run.reason ? ` · ${run.reason}` : ""}</span>
              <Link to={runGraphUrl(run)}>Graphs <span className="screen-reader">around this run</span><span aria-hidden="true"> ›</span></Link></p>
          </li>)}
        </ul>
        {failedOlder && <PanelError thing="older runs" onRetry={() => void failedOlder.refetch()} />}
        <div className="actions wat-runs-actions">
          {(next || loadingOlder) && <Button disabled={loadingOlder} onClick={() => { if (next) setOlder({ key: pageKey, pages: [...pages, next] }); }}>Load older</Button>}
          {/* Rebuilt only when the loaded runs change, not on every render. */}
          {runs.length > 0 && <CsvLink filename={dayCsvFilename("runs", range.from, range.to)} build={() => runsCsv(runs, names, today)} version={runs.map((run) => `${run.run_id}:${run.outcome}`).join(",")} label="CSV of these runs" />}
        </div>
      </>
      : !first.isError && <div className="skeleton skeleton-md" aria-hidden="true" />}
  </section>;
}

/**
 * SCR-04 History (`/water/history`, redesign §5.4): one period for the page (`range=7d|30d|90d|365d`), Group by (zone, line,
 * cause) and Measure (minutes, gallons), all in the URL; then the KPIs against the previous equal period, minutes or
 * gallons by group, daily water use and the run ledger. The period's summary is the page's status line.
 */
export default function WaterHistory() {
  const { irrigation, setSummary } = useWaterOutlet();
  const [search, setSearch] = useSearchParams();
  const today = useClockValue(localDay);
  const days = historyDays(search.get("range"), search.get("days"));
  const measure = historyMeasure(search.get("measure"));
  const group = minutesGroup(search.get("group"));
  const windows = historyWindows(today, days);
  const lines = irrigation.data?.lines ?? NO_LINES;
  // Water names lines as its rows do: "L01 Wall drip".
  const names = useMemo(() => Object.fromEntries(lines.map((line) => [line.line_id, lineLabel(line)])), [lines]);
  const current = useApiRead("API-IRR-10", { from: windows.current.from, to: windows.current.to, group_by: "line" }, { intervalMs: HISTORY_POLL_MS });
  const previous = useApiRead("API-IRR-10", { from: windows.previous.from, to: windows.previous.to, group_by: "line" }, { intervalMs: PREVIOUS_POLL_MS });
  const counted = days <= RUNS_TALLY_MAX_DAYS;
  const runs = useRunsTally(windows.current, counted, HISTORY_POLL_MS);
  const runsBefore = useRunsTally(windows.previous, counted, PREVIOUS_POLL_MS);
  const tally = useMemo(() => (current.data ? tallyMinutes(current.data) : null), [current.data]);
  const before = useMemo(() => (previous.data ? tallyMinutes(previous.data) : null), [previous.data]);
  const summary = historySummary(days, tally, counted ? runs.data ?? null : null);
  useEffect(() => {
    setSummary?.(summary);
    return () => setSummary?.(null);
  }, [setSummary, summary]);
  const pick = (name: string, value: string | null) => waterUrl(PATH, search, { [name]: value });
  return <div className="page wat-history">
    <div className="page-toolbar wat-history-bar">
      <Chips kind="nav" small label="Period" items={HISTORY_DAYS.map((value) => ({ key: String(value), label: `${value} d`, to: pick("range", value === 7 ? null : `${value}d`), current: days === value }))} />
      <label className="wat-select">Group by<select value={group} onChange={(event) => setSearch((currentSearch) => {
        const nextSearch = new URLSearchParams(currentSearch);
        if (event.target.value === "zone") nextSearch.delete("group"); else nextSearch.set("group", event.target.value);
        return nextSearch;
      }, { preventScrollReset: true })}>
        {GROUPS.map((value) => <option key={value} value={value}>{value}</option>)}
      </select></label>
      <label className="wat-select">Measure<select value={measure} onChange={(event) => setSearch((currentSearch) => {
        const nextSearch = new URLSearchParams(currentSearch);
        if (event.target.value === "min") nextSearch.delete("measure"); else nextSearch.set("measure", event.target.value);
        return nextSearch;
      }, { preventScrollReset: true })}>
        <option value="min">minutes</option>
        <option value="gal">gallons</option>
      </select></label>
    </div>
    <Kpis days={days} current={{ tally, pending: current.data === undefined && !current.isError, failed: current.isError && !current.data }} previous={before}
      runs={{ tally: runs.data ?? null, pending: runs.data === undefined && !runs.isError, failed: runs.isError && runs.data === undefined }} runsBefore={runsBefore.data ?? null} counted={counted}
      retry={{ minutes: () => void current.refetch(), runs: () => void runs.refetch() }} />
    <MinutesSection read={current} days={days} lines={lines} names={names} today={today} group={group} measure={measure} />
    <WaterUseSection days={days} names={names} search={search} />
    <RunsSection names={names} today={today} days={days} search={search} setSearch={setSearch} />
  </div>;
}
