// SPDX-License-Identifier: MIT
import { useId, useMemo, useState } from "react";
import { Link, useSearchParams, type SetURLSearchParams } from "react-router";
import { useQueries } from "@tanstack/react-query";
import { causeWords } from "../charts/causes";
import { DailyWaterUse } from "../charts/daily-water-use";
import { MinutesByZone, lineWords } from "../charts/minutes-by-zone";
import { chartLinks } from "../charts/uplot-chart";
import { ChemistryBadge } from "../components/badge";
import { Button } from "../components/button";
import { Chips } from "../components/chips";
import { useClockValue } from "../components/freshness";
import { PanelError } from "../components/panel";
import { Table, type TableColumn } from "../components/table";
import { dateShift, localDayStart } from "../lib/format";
import { runGraphUrl } from "../lib/graph-link";
import { LINES } from "../lib/lines";
import { CAUSES, OUTCOMES, apiGet, type IrrigationLine, type Run } from "../lib/operator-api";
import { viewMeta } from "../lib/sections";
import { readKey, useApiRead, useSchedulesRead } from "../lib/queries";
import {
  RUNS_MAX_DAYS, barsReport, localDay, minutesCaps, minutesDays, minutesGroup, plannedToday, runAmountWords, runGallons, runSpanWords, runsFilters, runsWindow,
  toMinute, useWaterOutlet, waterUseDays, waterUseSplit, zoneWord,
} from "../lib/water";
import { words } from "../lib/words";

export const meta = () => viewMeta("water-history");
export const links = () => chartLinks();

/** API-IRR-10, API-IRR-11 and API-21 are read every 60 s on this view. */
const HISTORY_POLL_MS = 60_000;
const NO_LINES: readonly IrrigationLine[] = [];
const LINE_IDS = LINES.map((line) => line.id);
const GROUPS = ["zone", "line", "cause"] as const;

/** This view's URL with `changes` applied (null removes a parameter), so every view state is in the link. */
function withParams(search: URLSearchParams, changes: Readonly<Record<string, string | null>>): string {
  const next = new URLSearchParams(search);
  for (const [name, value] of Object.entries(changes)) {
    if (value === null) next.delete(name);
    else next.set(name, value);
  }
  const query = next.toString();
  return `/water/history${query ? `?${query}` : ""}`;
}

/**
 * Minutes by zone (#1127's bars) over API-IRR-10 by line, 7 or 30 days (`?days=`), grouped by zone, line or cause
 * (`?group=`): no-data days hatched, today partial, cap ticks from the lines' caps and a dashed outline for the minutes
 * still planned today (API-IRR-02); fog apart, as humidification.
 */
function MinutesSection({ lines, names, today, search }: { lines: readonly IrrigationLine[]; names: Readonly<Record<string, string>>; today: string; search: URLSearchParams }) {
  const headingId = useId();
  const days = minutesDays(search.get("days"));
  const group = minutesGroup(search.get("group"));
  const minutes = useApiRead("API-IRR-10", { from: dateShift(today, -(days - 1)), to: today, group_by: "line" }, { intervalMs: HISTORY_POLL_MS });
  const agenda = useSchedulesRead();
  const now = useClockValue(toMinute);
  const planned = useMemo(() => plannedToday(agenda.data, lines, today, now), [agenda.data, lines, today, now]);
  const report = useMemo(() => (minutes.data ? barsReport(minutes.data) : null), [minutes.data]);
  // Noon today: which local day is today, steady all day so the bars do not rebuild every second.
  const noon = Date.parse(localDayStart(today)) / 1000 + 12 * 3600;
  return <section className="water-section" aria-labelledby={headingId}>
    <div className="section-head history-head">
      <h2 id={headingId} className="eyebrow">Watering minutes</h2>
      <div className="history-controls">
        <Chips label="Days" small items={([7, 30] as const).map((value) => ({ key: String(value), label: `${value} d`, to: withParams(search, { days: value === 7 ? null : "30" }), current: days === value }))} />
        <span className="chips-label">Group by</span>
        <Chips label="Group by" small items={GROUPS.map((value) => ({ key: value, label: value, to: withParams(search, { group: value === "zone" ? null : value }), current: group === value }))} />
      </div>
    </div>
    {minutes.isError && <PanelError thing="the watering minutes" onRetry={() => void minutes.refetch()} />}
    {report
      ? <MinutesByZone report={report} group={group} caps={minutesCaps(lines, group)} planned={planned[group]} now={noon} lineNames={names} />
      : !minutes.isError && <div className="skeleton skeleton-lg" aria-hidden="true" />}
  </section>;
}

/**
 * Daily water use (#1140's bars) from API-21: 30 or 90 days (`?water_use=`), split by line or cause (`?water_use_by=`),
 * with the water that ran while no Verdify valve was open apart. Whole gallons only (Q-10).
 */
function WaterUseSection({ names, search }: { names: Readonly<Record<string, string>>; search: URLSearchParams }) {
  const headingId = useId();
  const days = waterUseDays(search.get("water_use"));
  const split = waterUseSplit(search.get("water_use_by"));
  const use = useApiRead("API-21", { days }, { intervalMs: HISTORY_POLL_MS });
  return <section className="water-section" aria-labelledby={headingId}>
    <div className="section-head history-head">
      <h2 id={headingId} className="eyebrow">Daily water use</h2>
      <div className="history-controls">
        <Chips label="Water use days" small items={([30, 90] as const).map((value) => ({ key: String(value), label: `${value} d`, to: withParams(search, { water_use: value === 30 ? null : "90d" }), current: days === value, keepScroll: true }))} />
        <span className="chips-label">Split by</span>
        <Chips label="Split water use by" small items={(["line", "cause"] as const).map((value) => ({ key: value, label: value, to: withParams(search, { water_use_by: value === "line" ? null : "cause" }), current: split === value, keepScroll: true }))} />
      </div>
    </div>
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
    { key: "minutes", header: "Minutes", cell: (run) => runAmountWords(run) },
    {
      key: "gal", header: "Gallons", cell: (run) => {
        const gallons = runGallons(run.gal);
        return gallons.reason ? <>{gallons.text} <span className="muted">{gallons.reason}</span></> : gallons.text;
      },
    },
    { key: "outcome", header: "Outcome", cell: (run) => <>{words("run_outcome", run.outcome)}{run.reason && <span className="muted"> · {run.reason}</span>}</> },
    { key: "graphs", header: "Graphs", cell: (run) => <Link to={runGraphUrl(run)}>Graphs<span className="screen-reader"> around this run</span><span aria-hidden="true"> ›</span></Link> },
  ];
}

/**
 * The run ledger (API-IRR-11, newest first, every 60 s): line, cause, outcome and date filters in the URL (31 days at
 * most per window), each run linked to its Graphs window, and "Load older" through `cursor`.
 */
function RunsSection({ names, today, search, setSearch }: { names: Readonly<Record<string, string>>; today: string; search: URLSearchParams; setSearch: SetURLSearchParams }) {
  const headingId = useId();
  const range = runsWindow(search.get("from"), search.get("to"), today);
  const filters = runsFilters(search);
  const params = { from: range.fromAt, to: range.toAt, ...filters };
  const pageKey = JSON.stringify(params);
  const first = useApiRead("API-IRR-11", params, { intervalMs: HISTORY_POLL_MS });
  // Older pages, by the cursor each came from; a new window or filter starts again from the newest page.
  const [older, setOlder] = useState<{ key: string; cursors: readonly string[] }>({ key: pageKey, cursors: [] });
  const cursors = older.key === pageKey ? older.cursors : [];
  const pages = useQueries({
    queries: cursors.map((cursor) => ({
      queryKey: readKey("API-IRR-11", { ...params, cursor }), staleTime: Infinity, retry: false,
      queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("API-IRR-11", { ...params, cursor }, { signal }),
    })),
  });
  const loaded = [first.data ?? null, ...pages.map((page) => page.data ?? null)];
  const loadingOlder = pages.some((page) => page.isPending);
  const failedOlder = pages.find((page) => page.isError);
  const next = loaded[loaded.length - 1]?.next_cursor ?? null;
  const seen = new Set<string>();
  const runs = loaded.flatMap((page) => page?.items ?? []).filter((run) => !seen.has(run.run_id) && seen.add(run.run_id));
  const update = (changes: Readonly<Record<string, string | null>>) => setSearch((current) => {
    const nextSearch = new URLSearchParams(current);
    for (const [name, value] of Object.entries(changes)) {
      if (value === null || value === "") nextSearch.delete(name);
      else nextSearch.set(name, value);
    }
    return nextSearch;
  }, { replace: true });
  return <section className="water-section" aria-labelledby={headingId}>
    <h2 id={headingId} className="eyebrow">Runs · {range.from === dateShift(today, -6) && range.to === today ? "last 7 days" : `${range.from} to ${range.to}`}</h2>
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
    </div>
    {range.clamped && <p className="state-note">Runs show {RUNS_MAX_DAYS} days at most: from {range.from}.</p>}
    {first.isError && <PanelError thing="runs" onRetry={() => void first.refetch()} />}
    {first.data
      ? <>
        <Table label="Runs" caption="Runs, newest first · America/Denver" captionHidden columns={runColumns(names, today)} rows={runs} rowKey={(run) => run.run_id} empty="No runs in this window." />
        {failedOlder && <PanelError thing="older runs" onRetry={() => void failedOlder.refetch()} />}
        {(next || loadingOlder) && <div className="actions">
          <Button disabled={loadingOlder} onClick={() => { if (next) setOlder({ key: pageKey, cursors: [...cursors, next] }); }}>Load older</Button>
        </div>}
      </>
      : !first.isError && <div className="skeleton skeleton-md" aria-hidden="true" />}
  </section>;
}

/** SCR-04 History (`/water/history`): watering minutes by zone, line or cause, daily water use, and the run ledger. */
export default function WaterHistory() {
  const { irrigation } = useWaterOutlet();
  const [search, setSearch] = useSearchParams();
  const today = useClockValue(localDay);
  const lines = irrigation.data?.lines ?? NO_LINES;
  const names = useMemo(() => Object.fromEntries(lines.map((line) => [line.line_id, line.name])), [lines]);
  return <>
    <MinutesSection lines={lines} names={names} today={today} search={search} />
    <WaterUseSection names={names} search={search} />
    <RunsSection names={names} today={today} search={search} setSearch={setSearch} />
  </>;
}
