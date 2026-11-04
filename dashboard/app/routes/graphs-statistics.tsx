// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useState } from "react";
import { ScorecardTable, buildScorecard, scorecardWindow } from "../components/graphs/scorecard-table";
import type { PageHandle } from "../components/app-shell";
import { PageHeader } from "../components/layout/page-header";
import { Panel, type PanelState } from "../components/panel";
import { localDate } from "../lib/format";
import { GRAPH_POLL_MS, slotRead, useGraphClock, useWindowReads } from "../lib/graph-reads";
import { DAILY_GROUP, MAX_METRICS } from "../lib/graph-url";
import { viewMeta } from "../lib/sections";

export const meta = () => viewMeta("graphs-statistics");
export const handle: PageHandle = { wide: true };

const REGISTRY = [{ id: "registry", params: {} }] as const;
/** At most 8 keys per API-04 request: two requests when API-03 lists more `daily` keys. */
export function chunks<T>(items: readonly T[], size = MAX_METRICS): T[][] {
  const out: T[][] = [];
  for (let start = 0; start < items.length; start += size) out.push(items.slice(start, start + size));
  return out;
}

/**
 * Graphs → Statistics (`/graphs/statistics`, redesign §5.7, D-UX-04), for now the daily scorecard as #1130 built it,
 * moved from `/graphs/scorecard` (which redirects here) unchanged: the 30 America/Denver days ending today, from the `daily.*` keys API-03 lists
 * (API-04 over [local midnight 29 days ago, the next local midnight) with `points=31`, so one bucket per day) and
 * API-IRR-10's minutes by zone over the same dates. It polls every 60 s and writes nothing. No greenhouse data renders
 * in SSR.
 */
export default function Statistics() {
  const [failing, setFailing] = useState(false);
  const now = useGraphClock(true, failing);
  const today = now === null ? null : localDate(new Date(now * 1000).toISOString());
  const registry = slotRead(useWindowReads("API-03", REGISTRY, null), "registry");
  const metrics = registry.data?.items ?? null;
  const keys = useMemo(() => (metrics ?? []).filter((metric) => metric.group === DAILY_GROUP).map((metric) => metric.key), [metrics]);
  const window = today ? scorecardWindow(today) : null;
  const seriesSlots = window && metrics ? chunks(keys).map((list, index) => ({ id: `daily-${index}`, params: { metric: list, from: window.start, to: window.end, points: 31 } })) : [];
  const seriesReads = useWindowReads("API-04", seriesSlots, GRAPH_POLL_MS);
  const minutes = slotRead(useWindowReads("API-IRR-10", window ? [{ id: "minutes", params: { from: window.from, to: window.to, group_by: "zone" as const } }] : [], GRAPH_POLL_MS), "minutes");
  const seriesList = seriesSlots.map((slot) => seriesReads.get(slot.id)!);
  const anyError = [registry, minutes, ...seriesList].some((read) => read.error !== null);
  useEffect(() => { setFailing(anyError); }, [anyError]);

  const loading = !window || registry.loading || minutes.loading || seriesList.some((read) => read.loading);
  const failedKeys = new Set(seriesSlots.flatMap((slot, index) => (seriesList[index].data === null && seriesList[index].error !== null ? slot.params.metric : [])));
  const model = !loading && metrics && today
    ? buildScorecard({ today, metrics, series: seriesList.map((read) => read.data), failedKeys, minutes: minutes.data, minutesFailed: minutes.data === null && minutes.error !== null })
    : null;
  const failed = [registry, minutes, ...seriesList].filter((read) => read.error !== null && read.data === null);
  const state: PanelState = registry.error !== null && !metrics ? "error" : model ? "ready" : "loading";
  return <>
    <PageHeader view="graphs-statistics" status="One row per America/Denver day for 30 days, newest first. A missing value reads — with its reason, never 0." />
    <Panel title="Last 30 days" thing="the scorecard" state={state} reserve="lg" errorText="Couldn't load the metric list." onRetry={registry.refetch}>
      {model && window && <>
        {failed.length > 0 && <p className="state-note">Some columns couldn't load. <button type="button" className="btn" onClick={() => { for (const read of failed) read.refetch(); }}>Retry</button></p>}
        <ScorecardTable model={model} window={window} />
      </>}
    </Panel>
  </>;
}
