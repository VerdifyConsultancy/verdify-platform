// SPDX-License-Identifier: MIT
import { dayTitle, formatNumber, formatValue } from "../../lib/format";
import type { Range } from "../data/band-bar";
import { Delta } from "../data/delta";
import { MetricTile } from "../data/metric-tile";
import { Sparkline } from "../data/sparkline";
import { StatusStrip } from "../data/status-strip";
import { finite } from "../data/viz";
import { STRIP_ROWS, type Kpis, type StatDay } from "./stats-model";

export type StatsKpisProps = {
  kpis: Kpis;
  days: readonly StatDay[];
  /** "previous 30 d": what each change is against. */
  vs: string;
  /** Water → History for a matching period, where the water tile's detail is. */
  waterHref: string;
};

const percent = (value: number | null) => (value === null ? null : `${value.toFixed(1)}%`);
/** Time in band's scale: at least 10 points, within 0–100, so a change of under a point never fills the tile's height. */
export function percentDomain(values: readonly (number | null)[]): Range | null {
  const seen = values.filter(finite);
  if (!seen.length) return null;
  const low = Math.min(...seen), high = Math.max(...seen);
  const top = Math.min(100, Math.max(high, low + 10));
  return { low: Math.max(0, Math.min(low, top - 10)), high: top };
}
const MISSED = STRIP_ROWS.find((row) => row.key === "missed")!;
const fullDays = (count: number) => `Daily mean of ${count} full ${count === 1 ? "day" : "days"}`;
/** "Oct 28", "Oct 28 and Nov 1", "3 days": the days Iris missed a required plan. */
function missedWhen(dates: readonly string[]): string {
  if (!dates.length) return "";
  if (dates.length > 2) return `on ${dates.length} days`;
  return `(${dates.map((date) => dayTitle(date, "short")).join(" and ")})`;
}

/**
 * Statistics' four key figures (redesign §5.7): temperature and VPD in band (the daily mean over the full days), water
 * (gallons a day and in all) and Iris (required plans written of those due, and the misses), each with its change against
 * the previous window of the same length and a sparkline of its days. The changes are arrows and words in ink, never
 * green or red (§7.2); a change read from too few days is "no comparison".
 */
export function StatsKpis({ kpis, days, vs, waterHref }: StatsKpisProps) {
  const series = (pick: (day: StatDay) => number | null) => days.map(pick);
  // Today is not over: its point and its bar are drawn partial, beside the full days the means count.
  const partial = days.map((day) => day.partial);
  const due = kpis.iris.written !== null && kpis.iris.missed !== null ? kpis.iris.written + kpis.iris.missed : null;
  const temp = series((day) => day.values.temp), vpd = series((day) => day.values.vpd);
  return <div className="l-cols-4 gr-kpis">
    <MetricTile label="Temperature in band" value={percent(kpis.temp.mean)} reason="no full day graded yet" data={{ "data-kpi": "temp" }}
      delta={<Delta value={kpis.temp.change} unit="pts" vs={vs} />} meta={fullDays(kpis.temp.days)}
      visual={<Sparkline values={temp} token="temp" domain={percentDomain(temp)} partial={partial} />} />
    <MetricTile label="VPD in band" value={percent(kpis.vpd.mean)} reason="no full day graded yet" data={{ "data-kpi": "vpd" }}
      delta={<Delta value={kpis.vpd.change} unit="pts" vs={vs} />} meta={fullDays(kpis.vpd.days)}
      visual={<Sparkline values={vpd} token="vpd" domain={percentDomain(vpd)} partial={partial} />} />
    <MetricTile label="Water" href={waterHref} data={{ "data-kpi": "water" }}
      value={kpis.water.perDay === null ? null : <>{formatNumber(Math.round(kpis.water.perDay * 10) / 10)} <span className="metric-unit">gal a day</span></>} reason="no meter readings"
      delta={<Delta value={kpis.water.change} unit="%" vs={vs} />} meta={kpis.water.total === null ? "No gallons read in this period" : `${formatValue(kpis.water.total, "gal")} in all`}
      visual={<Sparkline values={series((day) => day.values.water)} kind="bars" token="water" partial={partial} />} />
    {/* Iris's visual is the strip redesign §5.7 draws: a quiet cell for each day with no miss, a loud one for a miss, so a normal month is never a blank band. */}
    <MetricTile label="Iris" data={{ "data-kpi": "iris" }}
      value={kpis.iris.written === null || due === null ? null : <>{formatNumber(kpis.iris.written)} of {formatNumber(due)} <span className="metric-unit">required plans</span></>} reason="no plans counted"
      delta={kpis.iris.missed === null ? undefined : <>{kpis.iris.missed === 0 ? "None missed" : `${formatNumber(kpis.iris.missed)} missed ${missedWhen(kpis.iris.missedDays)}`} · <Delta value={kpis.iris.change} vs={vs} /></>}
      meta="Missed required plans by day"
      visual={<div className="gr-kpi-strip"><StatusStrip label="Iris, missed required plans by day" cells={days.map((day) => ({ key: day.date, level: MISSED.level(day), label: MISSED.words(day), partial: day.partial }))} /></div>} />
  </div>;
}
