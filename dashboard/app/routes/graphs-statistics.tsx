// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { DailyRange } from "../charts/stats-daily-range";
import { InBandBars, type InBandRow } from "../charts/stats-in-band-bars";
import { TypicalDay } from "../charts/stats-typical-day";
import { localDay } from "../charts/time-axis";
import { chartLinks } from "../charts/uplot-chart";
import { Card } from "../components/card";
import { Chips } from "../components/chips";
import { DayByDay } from "../components/graphs/day-by-day";
import { GraphNotices, RangeForm } from "../components/graphs/controls";
import { ScorecardTable, buildScorecard, daysWindow } from "../components/graphs/scorecard-table";
import { StatsKpis } from "../components/graphs/stats-kpis";
import { daysOf, kpis as keyFigures, statDays } from "../components/graphs/stats-model";
import { PageHeader } from "../components/layout/page-header";
import { Section, SectionHeader } from "../components/layout/section-header";
import { PanelError } from "../components/panel";
import { dateShift, dayTitle, localDayStart } from "../lib/format";
import { GRAPH_POLL_MS, identity, slotRead, useGraphClock, useWindowReads } from "../lib/graph-reads";
import { DAILY_GROUP, MAX_METRICS, STATS_DAYS, TYPICAL_DAYS, isoOf, statsDays, statsHref, parseStatsUrl, within400, type StatsSpan } from "../lib/graph-url";
import type { MetricDef } from "../lib/operator-api";
import { viewMeta } from "../lib/sections";
import { useApiDown } from "../lib/session";

export const meta = () => viewMeta("graphs-statistics");
export const links = () => chartLinks();

const REGISTRY = [{ id: "registry", params: {} }] as const;
const TEMP = "climate.temp_avg", VPD = "climate.vpd_avg";
/** At most 8 keys per API-04 request: two requests when API-03 lists more `daily` keys. */
export function chunks<T>(items: readonly T[], size = MAX_METRICS): T[][] {
  const out: T[][] = [];
  for (let start = 0; start < items.length; start += size) out.push(items.slice(start, start + size));
  return out;
}
/** A window's local days as API-04 reads them: [local midnight of From, local midnight after To), within 400 days. */
function dayRange(from: string, to: string) {
  const range = within400({ from: Date.parse(localDayStart(from)) / 1000, to: Date.parse(localDayStart(dateShift(to, 1))) / 1000 });
  return { from: isoOf(range.from), to: isoOf(range.to) };
}
/** "Last 30 days", "Oct 1–Oct 30". */
const spanWords = (span: StatsSpan) => (span.kind === "days" ? `Last ${span.days} days` : `${dayTitle(span.from, "short")}–${dayTitle(span.to, "short")}`);

/**
 * Graphs → Statistics (redesign §5.7, §7.2, D-UX-04): is the greenhouse doing better week to week? Over the last 30, 90
 * or 365 days, or a custom run of days: four key figures with their change against the window before, the day-by-day
 * strip (each day opens in Climate), the typical day of the last 14 or 30 days by hour against the band, each day's
 * range, time in band per day, and the data table with its CSV. From API-04's `daily.*` keys (`v_daily_scorecard`) for
 * the window and the one before, API-04's daily and hourly climate buckets, API-06's band and API-IRR-10's minutes by
 * zone. A window that reaches today polls every 60 s; the window before and the typical day read once a day. It writes
 * nothing, and while the dashboard API is down it grades nothing. No greenhouse data renders in SSR.
 */
export default function Statistics() {
  const [params] = useSearchParams();
  const search = params.toString();
  const { span, typical, notices } = useMemo(() => parseStatsUrl(search), [search]);
  const apiDown = useApiDown();
  const [failing, setFailing] = useState(false);
  const now = useGraphClock(true, failing);
  const today = now === null ? null : localDay(now);
  const window = today ? statsDays(span, today) : null;
  const live = window !== null && today !== null && window.to >= today;
  const poll = live ? GRAPH_POLL_MS : null;
  const registry = slotRead(useWindowReads("API-03", REGISTRY, null), "registry");
  const metrics = registry.data?.items ?? null;
  const keys = useMemo(() => (metrics ?? []).filter((metric) => metric.group === DAILY_GROUP).map((metric) => metric.key), [metrics]);
  const previous = window ? { from: dateShift(window.from, -window.count), to: dateShift(window.from, -1) } : null;

  // The daily keys for the window (polled while it reaches today) and for the window before (read once).
  const dailySlots = (range: { from: string; to: string } | null, tag: string, count: number) => (range && metrics ? chunks(keys).map((list, index) => ({
    id: `${tag}|${range.from}|${range.to}|${index}`, params: { metric: list, ...dayRange(range.from, range.to), points: Math.min(2000, count + 1) },
  })) : []);
  const currentSlots = dailySlots(window, "now", window?.count ?? 0);
  const currentReads = useWindowReads("API-04", currentSlots, poll);
  const previousSlots = dailySlots(previous, "before", window?.count ?? 0);
  const previousReads = useWindowReads("API-04", previousSlots, null);
  // Each day's climate range, and the hourly buckets of the typical day's full days with the band of the last of them.
  const rangeId = window ? `range|${window.from}|${window.to}` : "range";
  const ranges = slotRead(useWindowReads("API-04", window && metrics ? [{ id: rangeId, params: { metric: [TEMP, VPD], ...dayRange(window.from, window.to), points: Math.min(2000, window.count + 1) } }] : [], poll), rangeId);
  const typicalFrom = today ? dateShift(today, -typical) : null;
  const typicalId = `typical|${typicalFrom}|${typical}`;
  const typicalRange = typicalFrom && today ? dayRange(typicalFrom, dateShift(today, -1)) : null;
  const typicalPoints = typicalRange ? Math.min(2000, Math.ceil((Date.parse(typicalRange.to) - Date.parse(typicalRange.from)) / 3_600_000) + 1) : 0;
  const hourly = slotRead(useWindowReads("API-04", typicalRange && metrics ? [{ id: typicalId, params: { metric: [TEMP, VPD], ...typicalRange, points: typicalPoints } }] : [], null), typicalId);
  const bandId = `band|${today}`;
  const band = slotRead(useWindowReads("API-06", today && metrics ? [{ id: bandId, params: dayRange(dateShift(today, -1), dateShift(today, -1)) }] : [], null), bandId);
  const minutesId = window ? `minutes|${window.from}|${window.to}` : "minutes";
  const minutes = slotRead(useWindowReads("API-IRR-10", window ? [{ id: minutesId, params: { from: window.from, to: window.to, group_by: "zone" as const } }] : [], poll), minutesId);

  const currentList = currentSlots.map((slot) => currentReads.get(slot.id)!);
  const previousList = previousSlots.map((slot) => previousReads.get(slot.id)!);
  const all = [registry, ranges, hourly, band, minutes, ...currentList, ...previousList];
  const anyError = all.some((read) => read.error !== null);
  useEffect(() => { setFailing(anyError); }, [anyError]);

  const ready = window !== null && today !== null && metrics !== null && currentList.length > 0 && currentList.every((read) => read.data !== null);
  const days = useMemo(() => (ready ? statDays(window.from, window.to, today, currentList.map((read) => read.data)) : []),
    [ready, window?.from, window?.to, today, currentList.map((read) => identity(read.data)).join()]);
  const before = useMemo(() => (previous && previousList.length && previousList.every((read) => read.data !== null) ? statDays(previous.from, previous.to, today ?? "", previousList.map((read) => read.data)) : null),
    [previous?.from, previous?.to, today, previousList.map((read) => identity(read.data)).join()]);
  const figures = useMemo(() => keyFigures(days, before), [days, before]);
  const failedKeys = new Set(currentSlots.flatMap((slot, index) => (currentList[index].data === null && currentList[index].error !== null ? slot.params.metric : [])));
  const table = ready ? buildScorecard({ today, window, metrics, series: currentList.map((read) => read.data), failedKeys, minutes: minutes.data, minutesFailed: minutes.data === null && minutes.error !== null }) : null;
  const failed = all.filter((read) => read.error !== null && read.data === null);
  const metricOf = (key: string): MetricDef | undefined => metrics?.find((metric) => metric.key === key);
  const vs = `previous ${window?.count ?? (span.kind === "days" ? span.days : 0)} d`;
  const waterHref = `/water/history?range=${span.kind === "days" ? `${span.days}d` : "30d"}`;
  const inBandRows: InBandRow[] = [
    { key: "temp", name: "Temperature", token: "--c-temp", days: days.map((day) => ({ date: day.date, value: day.values.temp, partial: day.partial })) },
    { key: "vpd", name: "VPD", token: "--c-vpd", days: days.map((day) => ({ date: day.date, value: day.values.vpd, partial: day.partial })) },
  ];
  // The status line names time in band, so it shows only with the window's data and the API up (redesign §8).
  const status = ready && !apiDown ? `${spanWords(span)} · time in band graded against the band the controller served` : null;
  // A phone's wording (redesign §5.7): the period and what the figures are, the split's limit left to the table.
  const statusShort = status === null ? null : `${spanWords(span).replace(/^Last (\d+) days$/, "$1 days")} · whole-day time in band`;

  let body;
  if (apiDown) body = <p className="meta gr-stats-none">No statistics while the dashboard API is unavailable.</p>;
  else if (!ready && (registry.error !== null || currentList.some((read) => read.error !== null && read.data === null))) {
    body = <PanelError thing="the statistics" onRetry={() => { for (const read of failed) read.refetch(); }} />;
  } else if (!ready || !window || !today) {
    body = <div className="page gr-stats-loading" aria-busy="true">
      <div className="l-cols-4">{[0, 1, 2, 3].map((index) => <div key={index} className="card metric-tile"><span className="metric-skeleton viz-skeleton" aria-hidden="true" /></div>)}</div>
      <div className="viz-skeleton gr-skeleton-strip" aria-hidden="true" />
      <p className="screen-reader">Loading the statistics</p>
    </div>;
  } else {
    const dayList = daysOf(window.from, window.to);
    body = <div className="page gr-stats">
      {failed.length > 0 && <PanelError thing="part of the statistics" text="Couldn't load part of the statistics." onRetry={() => { for (const read of failed) read.refetch(); }} />}
      <StatsKpis kpis={figures} days={days} vs={vs} waterHref={waterHref} />
      {/* Every chart of the page is one titled card, its title and its meta inside the frame (redesign §5.7). */}
      <Card title="Day by day" className="gr-chart-card gr-days-card" eyebrow={`${dayTitle(window.from, "short")}–${dayTitle(window.to, "short")} · each day opens in Climate`}>
        <DayByDay days={days} />
      </Card>
      <div className="l-cols-2 gr-pair">
        <Card title="Typical day" className="gr-chart-card"
          eyebrow={`Last ${typical} days, by hour`}
          actions={<Chips small label="Typical day length" items={TYPICAL_DAYS.map((count) => ({ key: String(count), label: `${count} d`, to: statsHref(span, count), current: count === typical, keepScroll: true }))} />}>
          {hourly.data === null && hourly.error !== null
            ? <PanelError thing="the typical day" onRetry={hourly.refetch} />
            : <>
              {metricOf(TEMP) && <TypicalDay series={hourly.data} metric={metricOf(TEMP)!} band={band.data} kind="temp" now={now!} days={typical} />}
              {metricOf(VPD) && <TypicalDay series={hourly.data} metric={metricOf(VPD)!} band={band.data} kind="vpd" now={now!} days={typical} />}
              <p className="meta gr-chart-key"><span className="gr-key-line" aria-hidden="true" /> median <span className="gr-key-dash" aria-hidden="true" /> middle half <span className="gr-key-band" aria-hidden="true" /> band</p>
            </>}
        </Card>
        <Card title="Daily range" className="gr-chart-card" eyebrow="Low to high each day · tick at the mean">
          {ranges.data === null && ranges.error !== null
            ? <PanelError thing="the daily range" onRetry={ranges.refetch} />
            : <>
              {metricOf(TEMP) && <DailyRange series={ranges.data} metric={metricOf(TEMP)!} days={dayList} today={today} token="--c-temp" name="Temperature" />}
              {metricOf(VPD) && <DailyRange series={ranges.data} metric={metricOf(VPD)!} days={dayList} today={today} token="--c-vpd" name="VPD" />}
            </>}
        </Card>
      </div>
      <Card title="Time in band per day" className="gr-chart-card" eyebrow="Whole day, temperature and VPD, against a 90% target">
        <InBandBars rows={inBandRows} syncKey={`verdify:graphs-statistics:${window.from}`} />
      </Card>
      <Section header={<SectionHeader title="Data" meta={`${days.length} ${days.length === 1 ? "row" : "rows"}, newest first`} />}>
        <details className="gr-data">
          <summary>Show the daily table</summary>
          {table && <ScorecardTable model={table} window={daysWindow(window.from, window.to)} />}
        </details>
      </Section>
    </div>;
  }

  return <>
    <PageHeader view="graphs-statistics" status={status} statusShort={statusShort} />
    <GraphNotices notices={notices} />
    <div className="page-toolbar gr-toolbar gr-stats-toolbar">
      <Chips kind="nav" small label="Period" items={STATS_DAYS.map((count) => ({ key: String(count), label: `${count} d`, to: statsHref({ kind: "days", days: count }, typical), current: span.kind === "days" && span.days === count, keepScroll: true }))} />
      <details className="range-custom" open={span.kind === "custom" || undefined}>
        <summary>Custom…</summary>
        <div className="range-custom-body">
          <RangeForm initial={span.kind === "custom" ? { from: span.from, to: span.to } : window ? { from: window.from, to: window.to } : { from: "", to: "" }} action="/graphs/statistics"
            hidden={typical === 30 ? [] : [["typical", String(typical)]]} href={(from, to) => statsHref({ kind: "custom", from, to }, typical)} />
        </div>
      </details>
    </div>
    {body}
  </>;
}
