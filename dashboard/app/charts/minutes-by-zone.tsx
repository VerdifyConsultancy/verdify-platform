// SPDX-License-Identifier: MIT
import { useMemo, useRef, type MutableRefObject } from "react";
import type uPlot from "uplot";
import { dayTitle, formatValue, localDayStart } from "../lib/format";
import { MINUTES_ZONES, ZONE_WORDS, minutesZone, type MinutesZone } from "../lib/lines";
import { causeRank, causeToken, causeWords } from "./causes";
import { DataTable, type DataTableBlock } from "./data-table";
import type { ChartColors, PaintTarget } from "./draw";
import { paintHatch } from "./lanes-draw";
import { ChartKey, type KeyItem } from "./legend";
import { AXIS_FONT, axisLabel, localDay } from "./time-axis";
import { tickIncrements } from "./time-series";
import { tooltipPlugin } from "./tooltip";
import type { MinutesPeriod, MinutesReport, XRange } from "./types";
import { UPlotChart, type ChartEnv, type ChartOptions } from "./uplot-chart";

/**
 * MinutesByZone (design system §5.2, US-WAT-03): one small daily bars chart per zone (Wall, Center, South, West, then
 * Fog as humidification), from API-IRR-10 by line. Bars are `uPlot.paths.bars`, stacked by cause with clean below
 * fertilizer. A no-data day is a hatched full-height gap, never a zero bar; a partial day (today, an outage) has a
 * dashed top edge; today's still-planned minutes are a dashed outline on today's bar; the daily cap is a dotted line.
 * The four watering zones share one minutes scale, so their bars compare; fog has its own.
 */
export type Coverage = MinutesPeriod["coverage"];
export type ZonePart = { line: string | null; cause: string; chemistry: "clean" | "fert"; minutes: number; pulses: number | null; gal: number | null };
export type ZoneDay = { day: string; at: number; coverage: Coverage; total: number | null; parts: ZonePart[] };
/** One stacked series: a cause within a chemistry. */
export type StackSegment = { key: string; cause: string; chemistry: "clean" | "fert"; token: string; label: string };
export type ZoneBars = {
  zone: MinutesZone;
  label: string;
  days: ZoneDay[];
  segments: StackSegment[];
  /** Per segment, the running total up to and including it each day: uPlot's stacked data. Null on a no-data day. */
  stacks: (number | null)[][];
  cap: number | null;
  /** Today's minutes still planned. */
  planned: number | null;
  /** Index of today in `days`, or -1. */
  today: number;
  yMax: number;
};
export type MinutesModel = { days: { day: string; at: number; coverage: Coverage }[]; zones: ZoneBars[]; xRange: XRange; byLine: boolean };

export type MinutesInput = {
  report: MinutesReport;
  /** Daily cap minutes per zone, as the screen passes them (API-IRR-01 `daily_cap_min`). */
  caps?: Partial<Record<MinutesZone, number | null>>;
  /** Today's minutes still planned per zone (API-IRR-02). */
  planned?: Partial<Record<MinutesZone, number | null>>;
  /** Epoch seconds: which local day is today. */
  now: number;
};

const DAY = 86_400;
const valued = (value: number | null | undefined): value is number => value !== null && value !== undefined && Number.isFinite(value);

/** The bars: one row per zone, each day's parts by line and cause, the stacks and the shared scale. */
export function buildMinutes(input: MinutesInput): MinutesModel {
  const { report } = input;
  const byLine = report.group_by === "line";
  const days = [...report.periods]
    .map((period) => ({ day: period.start, at: Date.parse(localDayStart(period.start)) / 1000, coverage: period.coverage }))
    .filter((day) => Number.isFinite(day.at))
    .sort((a, b) => a.at - b.at);
  const index = new Map(days.map((day, position) => [day.day, position]));
  const today = localDay(input.now);
  const parts = new Map<MinutesZone, ZonePart[][]>(MINUTES_ZONES.map((zone) => [zone, days.map(() => [])]));
  if (report.group_by !== "cause") {
    for (const row of report.rows) {
      const zone = byLine ? minutesZone(row.group) : (MINUTES_ZONES as readonly string[]).includes(row.group) ? (row.group as MinutesZone) : null;
      const position = index.get(row.period);
      if (!zone || position === undefined || days[position].coverage === "no_data" || !valued(row.minutes)) continue;
      parts.get(zone)![position].push({ line: byLine ? row.group : null, cause: row.cause, chemistry: row.chemistry === "fert" ? "fert" : "clean", minutes: row.minutes, pulses: row.pulses ?? null, gal: row.gal ?? null });
    }
  }
  const zones = MINUTES_ZONES.map((zone): ZoneBars => {
    const dayParts = parts.get(zone)!;
    const zoneDays = days.map((day, position): ZoneDay => {
      const list = dayParts[position].sort((a, b) => (a.line ?? "").localeCompare(b.line ?? "") || causeRank(a.cause) - causeRank(b.cause));
      return { ...day, total: day.coverage === "no_data" ? null : list.reduce((sum, part) => sum + part.minutes, 0), parts: list };
    });
    const seen = new Map<string, StackSegment>();
    for (const part of dayParts.flat()) {
      const key = `${part.chemistry}:${part.cause}`;
      if (!seen.has(key)) seen.set(key, { key, cause: part.cause, chemistry: part.chemistry, token: causeToken(part.cause), label: `${causeWords(part.cause)}${part.chemistry === "fert" ? " · fertilizer" : ""}` });
    }
    // Clean below fertilizer, each in the cause table's order.
    const segments = [...seen.values()].sort((a, b) => Number(a.chemistry === "fert") - Number(b.chemistry === "fert") || causeRank(a.cause) - causeRank(b.cause));
    const stacks = segments.map((_, level) => zoneDays.map((day) => {
      if (day.total === null) return null;
      const below = new Set(segments.slice(0, level + 1).map((segment) => segment.key));
      return day.parts.filter((part) => below.has(`${part.chemistry}:${part.cause}`)).reduce((sum, part) => sum + part.minutes, 0);
    }));
    const cap = input.caps?.[zone];
    const planned = input.planned?.[zone];
    return {
      zone, label: ZONE_WORDS[zone], days: zoneDays, segments, stacks,
      cap: valued(cap) && cap > 0 ? cap : null, planned: valued(planned) && planned > 0 ? planned : null,
      today: zoneDays.findIndex((day) => day.day === today), yMax: 0,
    };
  });
  // The four watering zones share a scale; fog, humidification, has its own.
  const top = (zone: ZoneBars) => Math.max(
    0, ...zone.days.map((day) => day.total ?? 0), zone.cap ?? 0,
    zone.today >= 0 && zone.planned !== null ? (zone.days[zone.today].total ?? 0) + zone.planned : 0,
  );
  const scale = (value: number) => Math.max(10, Math.ceil(value * 1.15));
  const shared = scale(Math.max(...zones.filter((zone) => zone.zone !== "fog").map(top)));
  for (const zone of zones) zone.yMax = zone.zone === "fog" ? scale(top(zone)) : shared;
  const first = days[0]?.at ?? input.now, last = days[days.length - 1]?.at ?? input.now;
  return { days, zones, xRange: { from: first - DAY / 2, to: last + DAY / 2 }, byLine };
}

/** "L04 Center mister" with the API's name for the line, else the line id. */
export function lineWords(line: string | null, names?: Readonly<Record<string, string>>): string {
  if (!line) return "";
  return names?.[line] ? `${line} ${names[line]}` : line;
}

/** One part in words: "L04 Center mister · VPD · 46 min · 3 gal"; whole gallons only where the API gives them. */
export function partWords(part: ZonePart, names?: Readonly<Record<string, string>>): string {
  const who = part.line ? lineWords(part.line, names) : part.chemistry === "fert" ? "fertilizer" : "clean";
  return [who, causeWords(part.cause), formatValue(part.minutes, "min"), ...(valued(part.gal) ? [`${Math.round(part.gal)} gal`] : [])].join(" · ");
}

const COVERAGE_WORDS: Readonly<Record<Coverage, string>> = { complete: "complete", partial: "partial", no_data: "no data" };
export const coverageWords = (coverage: Coverage) => COVERAGE_WORDS[coverage] ?? coverage;

/** "today 46 min · cap 120", "(partial)" while today is partial; the cap alone when today is outside the report. */
export function todayText(zone: ZoneBars): string {
  const day = zone.today >= 0 ? zone.days[zone.today] : null;
  const parts: string[] = [];
  if (day) parts.push(day.total === null ? "today —" : `today ${formatValue(day.total, "min")}${day.coverage === "partial" ? " (partial)" : ""}`);
  if (zone.cap !== null) parts.push(`cap ${zone.cap}`);
  return parts.join(" · ");
}

/** The tooltip for the day under the pointer: the day and zone, its total or "no data", each line and cause, plan and cap. */
export function minutesTooltip(zone: ZoneBars, plot: Pick<uPlot, "cursor">, names?: Readonly<Record<string, string>>): string[] | null {
  if (!plot.cursor.event) return null;
  const position = plot.cursor.idx;
  if (position === null || position === undefined || position < 0 || position >= zone.days.length) return null;
  const day = zone.days[position];
  const lines = [`${dayTitle(day.day, "short")} · ${zone.label}`];
  if (day.total === null) return [...lines, "no data"];
  lines.push(formatValue(day.total, "min"));
  if (day.coverage === "partial") lines.push("partial");
  lines.push(...day.parts.map((part) => partWords(part, names)));
  if (position === zone.today && zone.planned !== null) lines.push(`still planned today ${formatValue(zone.planned, "min")}`);
  if (zone.cap !== null) lines.push(`cap ${zone.cap} min`);
  return lines;
}

/** The bar geometry uPlot's bars use: 60% of the narrowest day spacing, at most 48 px, centred on the day. */
export const BAR_SIZE = 0.6;
export const BAR_MAX = 48;
function barWidth(plot: PaintTarget, zone: ZoneBars, px: number): number {
  let narrowest = Infinity;
  for (let position = 1; position < zone.days.length; position++) {
    narrowest = Math.min(narrowest, Math.abs(plot.valToPos(zone.days[position].at, "x", true) - plot.valToPos(zone.days[position - 1].at, "x", true)));
  }
  if (!Number.isFinite(narrowest)) narrowest = plot.bbox.width;
  return Math.max(px, Math.min(narrowest * BAR_SIZE, BAR_MAX * px));
}

/**
 * draw, over the bars: the hatched no-data days, the dashed top edge of a partial day, today's still-planned minutes as
 * a dashed outline on today's bar, then the cap as a dotted line.
 */
export function paintMinutes(plot: PaintTarget, zone: ZoneBars, colors: ChartColors, px: number) {
  const { ctx, bbox } = plot;
  const width = barWidth(plot, zone, px);
  const y = (value: number) => plot.valToPos(value, "y", true);
  ctx.save();
  try {
    zone.days.forEach((day, position) => {
      const left = plot.valToPos(day.at, "x", true) - width / 2;
      if (day.total === null) {
        paintHatch(ctx, left, bbox.top, width, bbox.height, colors["--muted"], px);
        return;
      }
      if (day.coverage === "partial") {
        ctx.strokeStyle = colors["--ink"];
        ctx.lineWidth = 1.5 * px;
        ctx.setLineDash([3 * px, 2 * px]);
        ctx.beginPath();
        ctx.moveTo(left, y(day.total));
        ctx.lineTo(left + width, y(day.total));
        ctx.stroke();
      }
      if (position === zone.today && zone.planned !== null) {
        ctx.strokeStyle = colors["--muted"];
        ctx.lineWidth = px;
        ctx.setLineDash([3 * px, 2 * px]);
        const top = y(day.total + zone.planned), bottom = y(day.total);
        ctx.strokeRect(left, top, width, bottom - top);
      }
    });
    if (zone.cap !== null) {
      ctx.strokeStyle = colors["--c-bound"];
      ctx.lineWidth = px;
      ctx.setLineDash([px, 3 * px]);
      ctx.beginPath();
      ctx.moveTo(bbox.left, y(zone.cap));
      ctx.lineTo(bbox.left + bbox.width, y(zone.cap));
      ctx.stroke();
    }
  } finally {
    ctx.restore();
  }
}

/** Day labels ("10/28") under the last row only; ticks a day or more apart. */
function dayAxis(colors: ChartColors, show: boolean): uPlot.Axis {
  return {
    show, stroke: colors["--muted"], font: AXIS_FONT, size: 28, space: 44, grid: { show: false },
    ticks: { stroke: colors["--c-grid"], width: 1, size: 4 },
    incrs: [DAY, 2 * DAY, 7 * DAY, 14 * DAY, 28 * DAY],
    values: (_plot, splits) => splits.map((split) => axisLabel(split, false, DAY)),
  };
}

/** Whole minutes up the left, one width on every row, so the rows' bars line up. */
function minutesAxis(colors: ChartColors): uPlot.Axis {
  return {
    stroke: colors["--muted"], font: AXIS_FONT, size: 44, space: 24,
    grid: { stroke: colors["--c-grid"], width: 1 }, ticks: { stroke: colors["--c-grid"], width: 1, size: 4 },
    incrs: tickIncrements(0), values: (_plot, splits) => splits.map((split) => String(Math.round(split))),
  };
}

export function minutesOptions(env: ChartEnv, zoneRef: MutableRefObject<ZoneBars>, last: boolean, namesRef: MutableRefObject<Readonly<Record<string, string>> | undefined>): ChartOptions {
  const { UPlot, colors, px } = env;
  const zone = zoneRef.current;
  const bars = UPlot.paths.bars!({ size: [BAR_SIZE, BAR_MAX], align: 0 });
  return {
    padding: [8, 12, 0, 0],
    series: [{}, ...zone.segments.map((segment): uPlot.Series => ({
      label: segment.label, scale: "y", paths: bars, fill: colors[segment.token], stroke: colors[segment.token], width: 0,
      points: { show: false }, value: (_plot, value) => formatValue(value, "min"),
    }))],
    // Each segment's bar runs from the running total below it (uPlot's stacked bars).
    bands: zone.segments.slice(1).map((_, level) => ({ series: [level + 2, level + 1] as [number, number] })),
    scales: { y: { range: () => [0, zoneRef.current.yMax] } },
    axes: [dayAxis(colors, last), minutesAxis(colors)],
    cursor: { points: { show: false } },
    hooks: { draw: [(plot) => paintMinutes(plot, zoneRef.current, colors, px())] },
    plugins: [tooltipPlugin((plot) => minutesTooltip(zoneRef.current, plot, namesRef.current))],
  };
}

/** The data table: per zone, one row per day with its minutes, the split by line and cause in words, and coverage. */
export function minutesTables(model: MinutesModel, names?: Readonly<Record<string, string>>): DataTableBlock[] {
  return model.zones.map((zone) => ({
    caption: `${zone.label}${zone.zone === "fog" ? " (humidification)" : ""} · minutes by day · America/Denver`,
    headers: ["Day", "Minutes", "By line and cause", "Coverage"], count: zone.days.length,
    rows: () => zone.days.map((day, position) => [
      <time key="day" dateTime={day.day}>{dayTitle(day.day, "short")}</time>,
      day.total === null ? "—" : formatValue(day.total, "min"),
      [...day.parts.map((part) => partWords(part, names)), ...(position === zone.today && zone.planned !== null ? [`still planned today ${formatValue(zone.planned, "min")}`] : [])].join("; "),
      coverageWords(day.coverage),
    ]),
  }));
}

/** The key: each cause drawn, then the marks that are not causes. */
export function minutesKey(model: MinutesModel): KeyItem[] {
  const causes = new Map<string, KeyItem>();
  for (const zone of model.zones) for (const segment of zone.segments) if (!causes.has(segment.cause)) causes.set(segment.cause, { key: segment.cause, label: causeWords(segment.cause), token: segment.token, kind: "fill" });
  const items = [...causes.values()].sort((a, b) => causeRank(a.key) - causeRank(b.key));
  if (model.days.some((day) => day.coverage === "no_data")) items.push({ key: "no-data", label: "no data", token: "--muted", kind: "hatch" });
  if (model.days.some((day) => day.coverage === "partial")) items.push({ key: "partial", label: "partial day", token: "--ink", kind: "dashed" });
  // The outline sits on today's bar, so a no-data today (hatched) draws none.
  if (model.zones.some((zone) => zone.planned !== null && zone.today >= 0 && zone.days[zone.today].total !== null)) items.push({ key: "planned", label: "still planned today", token: "--muted", kind: "dashed" });
  if (model.zones.some((zone) => zone.cap !== null)) items.push({ key: "cap", label: "daily cap", token: "--c-bound", kind: "dotted" });
  return items;
}

export type MinutesByZoneProps = MinutesInput & {
  /** Line names from API-IRR-01 (`name`), for the tooltip and the data table; the line id alone without them. */
  lineNames?: Readonly<Record<string, string>>;
  /** The rows share it, so the cursor sits on one day in every row. */
  syncKey?: string;
  title?: string;
  /** Shown in place of the bars (an error). */
  message?: string;
};

function ZoneChart({ zone, last, xRange, syncKey, names }: { zone: ZoneBars; last: boolean; xRange: XRange; syncKey: string; names?: Readonly<Record<string, string>> }) {
  const zoneRef = useRef(zone);
  zoneRef.current = zone;
  const namesRef = useRef(names);
  namesRef.current = names;
  const data = useMemo<uPlot.AlignedData>(() => [zone.days.map((day) => day.at), ...zone.stacks], [zone]);
  const structure = `minutes|${zone.zone}|${last}|${syncKey}|${zone.segments.map((segment) => segment.key).join(",")}`;
  const label = `${zone.label} watering minutes per day${zone.zone === "fog" ? " (humidification)" : ""}, stacked by cause with clean below fertilizer. No-data days are hatched, never zero. The data table follows.`;
  return <UPlotChart options={(env) => minutesOptions(env, zoneRef, last, namesRef)} data={data} xRange={xRange} structure={structure}
    className={last ? "plot-bars plot-bars-axis" : "plot-bars"} label={label} syncKey={syncKey} marks={zone} zoom={false} />;
}

export function MinutesByZone(props: MinutesByZoneProps) {
  const { report, caps, planned, now } = props;
  // Caps and plans arrive as literals on every render: they rebuild the bars only when their values change.
  const limits = JSON.stringify([caps ?? null, planned ?? null]);
  const model = useMemo(() => buildMinutes({ report, caps, planned, now }), [report, limits, now]);
  const syncKey = props.syncKey ?? `verdify:minutes:${report.from}:${report.to}`;
  const title = props.title ?? `Watering minutes by zone · last ${model.days.length} days`;
  const drawable = !props.message && report.group_by !== "cause" && model.days.length > 0;
  return <figure className="chart chart-minutes">
    <figcaption className="eyebrow">{title}</figcaption>
    {drawable
      ? <ul className="minutes-rows">
        {model.zones.map((zone, position) => <li key={zone.zone} className="minutes-row" data-zone={zone.zone}>
          <span className="minutes-label">{zone.label}{zone.zone === "fog" && <span className="minutes-note"> humidification</span>}</span>
          <ZoneChart zone={zone} last={position === model.zones.length - 1} xRange={model.xRange} syncKey={syncKey} names={props.lineNames} />
          <span className="minutes-today">{todayText(zone)}</span>
        </li>)}
      </ul>
      : <div className="plot plot-small chart-message"><p>{props.message ?? (report.group_by === "cause" ? "Minutes by zone needs the report by line or by zone" : "No watering data in this window")}</p></div>}
    {drawable && <ChartKey items={minutesKey(model)} />}
    {drawable && <DataTable tables={minutesTables(model, props.lineNames)} />}
  </figure>;
}
