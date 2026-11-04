// SPDX-License-Identifier: MIT
import { useMemo, useRef, type MutableRefObject } from "react";
import type uPlot from "uplot";
import { dayTitle, formatValue, localDayStart } from "../lib/format";
import { LINES, lineById } from "../lib/lines";
import type { WaterUse } from "../lib/operator-api";
import { causeRank, causeToken, causeWords } from "./causes";
import { dayCsvFilename, tableCsv } from "./csv";
import { CsvLink, DataTable, type DataTableBlock } from "./data-table";
import type { ChartColors, PaintTarget } from "./draw";
import { paintHatch } from "./lanes-draw";
import { ChartKey, type KeyItem } from "./legend";
import { coverageWords, dayAxis, dayBarWidth, lineWords, wholeAxis, type Coverage } from "./minutes-by-zone";
import { tooltipPlugin } from "./tooltip";
import type { XRange } from "./types";
import { UPlotChart, type ChartEnv, type ChartOptions } from "./uplot-chart";

/**
 * Daily water use (screens SCR-04, US-WAT-20): API-21's whole gallons per America/Denver day, 30 or 90 days. Each day a
 * stacked bar of `by_line` or `by_cause`, clean below fertilizer, and beside it, not stacked, a bar of `no_valve_gal`, in
 * `--fault`: water that ran while no Verdify valve was open. A no-data day is a hatched gap and never 0, a partial day has
 * a dashed top edge, and a complete day with no water is a real 0. What the API returns is drawn as it is, never rescaled:
 * the parts are attributed per run, so they can sit a gallon or two off the meter's total. No rate and no flow flag (Q-10).
 */
export type WaterSplit = "line" | "cause";
const COVERAGES: readonly Coverage[] = ["complete", "partial", "no_data"];
/** A coverage newer than this client reads partial, drawn with the dashed edge, as the minutes do. */
const coverageOf = (coverage: string): Coverage => ((COVERAGES as readonly string[]).includes(coverage) ? coverage as Coverage : "partial");

/** One stacked part: a line or a cause. */
export type WaterSegment = { key: string; label: string; token: string; fert: boolean };
export type WaterDay = {
  date: string;
  at: number;
  coverage: Coverage;
  /** The meter's day total; null when it has none. */
  meter: number | null;
  /** The split, by segment key; null when the API sends none (every no-data day). */
  parts: Readonly<Record<string, number>> | null;
  /** The top of the stack: null with no split, and 0 on a day with no water. */
  stacked: number | null;
  noValve: number | null;
};
export type WaterModel = {
  days: WaterDay[];
  segments: WaterSegment[];
  /** Per segment, the running total up to and including it each day: uPlot's stacked data. Null with no split that day. */
  stacks: (number | null)[][];
  /** The no-valve column, apart from the stacks; null where the API sends none. */
  noValve: (number | null)[];
  split: WaterSplit;
  yMax: number;
  xRange: XRange;
  /** The window's meter total and no-valve total, and how many days had no data. */
  total: number;
  noValveTotal: number;
  noData: number;
};

const DAY = 86_400;
const LINE_TOKENS: Readonly<Record<string, string>> = { drip: "--eq-water", fert_drip: "--eq-fert", fert_mister: "--eq-fert", master: "--eq-master", mister: "--eq-mister", fog: "--eq-fog" };
/** A line's own token by its kind (#1127); a line the register does not know takes the "cause unknown" colour. */
export function lineToken(id: string): string {
  const line = lineById(id);
  return line ? LINE_TOKENS[line.kind] ?? "--c-bound" : "--c-bound";
}
const lineRank = (id: string) => {
  const rank = LINES.findIndex((line) => line.id === id);
  return rank < 0 ? LINES.length : rank;
};
const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** The segments in stacking order: clean below fertilizer, each in the register's or the cause table's order. */
function segmentsOf(keys: ReadonlySet<string>, split: WaterSplit, names?: Readonly<Record<string, string>>): WaterSegment[] {
  if (split === "line") {
    return [...keys].map((key) => ({ key, label: lineWords(key, names), token: lineToken(key), fert: lineById(key)?.chemistry === "fert" }))
      .sort((a, b) => Number(a.fert) - Number(b.fert) || lineRank(a.key) - lineRank(b.key) || a.key.localeCompare(b.key));
  }
  // The feed is the one cause that carries fertilizer; its prewet and flush are clean water.
  return [...keys].map((key) => ({ key, label: capital(causeWords(key)), token: causeToken(key), fert: key === "feed" }))
    .sort((a, b) => Number(a.fert) - Number(b.fert) || causeRank(a.key) - causeRank(b.key) || a.key.localeCompare(b.key));
}

/** API-21's days as the bars take them: nulls stay null, 0 stays 0, and the no-valve series stays out of the stack. */
export function buildWaterUse(data: Pick<WaterUse, "days">, split: WaterSplit, names?: Readonly<Record<string, string>>): WaterModel {
  const days = data.days
    .map((day): WaterDay => {
      const coverage = coverageOf(day.coverage);
      const empty = coverage === "no_data";
      const parts = empty ? null : (split === "line" ? day.by_line : day.by_cause) ?? null;
      return {
        date: day.date, at: Date.parse(localDayStart(day.date)) / 1000, coverage, meter: empty ? null : day.meter_gal ?? null, parts,
        stacked: parts === null ? null : Object.values(parts).reduce((sum, gallons) => sum + gallons, 0), noValve: empty ? null : day.no_valve_gal ?? null,
      };
    })
    .filter((day) => Number.isFinite(day.at))
    .sort((a, b) => a.at - b.at);
  const segments = segmentsOf(new Set(days.flatMap((day) => Object.keys(day.parts ?? {}))), split, names);
  const stacks = segments.map((_, level) => days.map((day) => {
    if (day.parts === null) return null;
    return segments.slice(0, level + 1).reduce((sum, segment) => sum + (day.parts?.[segment.key] ?? 0), 0);
  }));
  const peak = Math.max(0, ...days.map((day) => Math.max(day.stacked ?? 0, day.noValve ?? 0)));
  const first = days[0]?.at ?? 0, last = days[days.length - 1]?.at ?? 0;
  const sum = (pick: (day: WaterDay) => number | null) => days.reduce((total, day) => total + (pick(day) ?? 0), 0);
  return {
    days, segments, stacks, noValve: days.map((day) => day.noValve), split, yMax: Math.max(10, Math.ceil(peak * 1.15)), xRange: { from: first - DAY / 2, to: last + DAY / 2 },
    total: sum((day) => day.meter), noValveTotal: sum((day) => day.noValve), noData: days.filter((day) => day.coverage === "no_data").length,
  };
}

/** The label of the no-valve bar, in the key, the tooltip and the table. */
export const NO_VALVE_WORDS = "No Verdify valve open: hose, sink, hydro or a leak";

const gallons = (value: number | null | undefined) => formatValue(value, "gal");

/** "Last 30 days: 612 gal · 40 gal with no Verdify valve open · 1 day with no data". */
export function waterSummary(model: WaterModel): string {
  const parts = [`Last ${model.days.length} days: ${gallons(model.total)}`, `${gallons(model.noValveTotal)} with no Verdify valve open`];
  if (model.noData > 0) parts.push(`${model.noData} ${model.noData === 1 ? "day" : "days"} with no data`);
  return parts.join(" · ");
}

/** The tooltip for the day under the pointer: the day, its total and coverage, each part, then the no-valve water. */
export function waterTooltip(model: WaterModel, plot: Pick<uPlot, "cursor">): string[] | null {
  if (!plot.cursor.event) return null;
  const position = plot.cursor.idx;
  if (position === null || position === undefined || position < 0 || position >= model.days.length) return null;
  const day = model.days[position];
  const lines = [dayTitle(day.date, "short")];
  if (day.coverage === "no_data") return [...lines, "no data"];
  lines.push(`${gallons(day.meter)} on the meter`);
  if (day.coverage === "partial") lines.push("partial");
  for (const segment of model.segments) {
    const value = day.parts?.[segment.key];
    if (value) lines.push(`${segment.label} · ${gallons(value)}`);
  }
  if (day.noValve !== null) lines.push(`No Verdify valve open · ${gallons(day.noValve)}`);
  return lines;
}

/** Each of a day's two bars takes this share of the day and at most this many CSS px: the stack left of the day, no-valve right of it. */
export const WATER_BAR_SIZE = 0.4;
export const WATER_BAR_MAX = 18;

/** draw, over the bars: the hatched no-data days across both bars' width, then the dashed top edge of each partial day's bars. */
export function paintWaterUse(plot: PaintTarget, model: WaterModel, colors: ChartColors, px: number) {
  const { ctx, bbox } = plot;
  const width = dayBarWidth(plot, model.days, px, WATER_BAR_SIZE, WATER_BAR_MAX);
  const edge = (left: number, value: number) => {
    const top = plot.valToPos(value, "y", true);
    ctx.beginPath();
    ctx.moveTo(left, top);
    ctx.lineTo(left + width, top);
    ctx.stroke();
  };
  ctx.save();
  try {
    for (const day of model.days) {
      const center = plot.valToPos(day.at, "x", true);
      if (day.coverage === "no_data") {
        paintHatch(ctx, center - width, bbox.top, 2 * width, bbox.height, colors["--muted"], px);
      } else if (day.coverage === "partial") {
        ctx.strokeStyle = colors["--ink"];
        ctx.lineWidth = 1.5 * px;
        ctx.setLineDash([3 * px, 2 * px]);
        if (day.stacked !== null) edge(center - width, day.stacked);
        if (day.noValve !== null) edge(center, day.noValve);
      }
    }
  } finally {
    ctx.restore();
  }
}

export function waterOptions(env: ChartEnv, modelRef: MutableRefObject<WaterModel>): ChartOptions {
  const { UPlot, colors, px } = env;
  const model = modelRef.current;
  const size: [number, number] = [WATER_BAR_SIZE, WATER_BAR_MAX];
  // Left of the day's tick, and right of it: beside each other, whatever the day's stack holds.
  const stack = UPlot.paths.bars!({ size, align: -1 });
  const apart = UPlot.paths.bars!({ size, align: 1 });
  const value = (_plot: uPlot, gal: number | null) => gallons(gal);
  return {
    padding: [8, 12, 0, 0],
    series: [
      {},
      ...model.segments.map((segment): uPlot.Series => ({ label: segment.label, scale: "y", paths: stack, fill: colors[segment.token], stroke: colors[segment.token], width: 0, points: { show: false }, value })),
      { label: NO_VALVE_WORDS, scale: "y", paths: apart, fill: colors["--fault"], stroke: colors["--fault"], width: 0, points: { show: false }, value },
    ],
    // Each part's bar runs from the running total below it (uPlot's stacked bars); the no-valve bar has no band.
    bands: model.segments.slice(1).map((_, level) => ({ series: [level + 2, level + 1] as [number, number] })),
    scales: { y: { range: () => [0, modelRef.current.yMax] } },
    axes: [dayAxis(colors, true), wholeAxis(colors)],
    cursor: { points: { show: false } },
    hooks: { draw: [(plot) => paintWaterUse(plot, modelRef.current, colors, px())] },
    plugins: [tooltipPlugin((plot) => waterTooltip(modelRef.current, plot))],
  };
}

/** One row per day: the total, each line or cause, no valve and coverage in words. A no-data day is "—", never 0. */
export function waterTable(model: WaterModel): DataTableBlock {
  return {
    caption: `Daily water use by ${model.split}, whole gallons · America/Denver`,
    headers: ["Day", "Total", ...model.segments.map((segment) => segment.label), "No valve", "Coverage"], count: model.days.length,
    rows: () => model.days.map((day) => [
      <time key="day" dateTime={day.date}>{dayTitle(day.date, "short")}</time>,
      gallons(day.meter),
      ...model.segments.map((segment) => (day.parts === null ? "—" : gallons(day.parts[segment.key] ?? 0))),
      gallons(day.noValve),
      coverageWords(day.coverage),
    ]),
  };
}

/**
 * "CSV of this chart" (redesign §7.1): one row per local day with the meter's total, each line or cause, the no-valve
 * water and coverage, in whole gallons; a no-data day is empty cells, never 0.
 */
export function waterUseCsv(model: WaterModel): string {
  return tableCsv(
    ["day (America/Denver)", "total (gal)", ...model.segments.map((segment) => `${segment.label} (gal)`), "no valve (gal)", "coverage"],
    model.days.map((day) => [day.date, day.meter, ...model.segments.map((segment) => (day.parts === null ? null : day.parts[segment.key] ?? 0)), day.noValve, day.coverage]),
  );
}

/** The key: one entry per colour drawn (the lines of one kind share theirs), the no-valve bar, then the marks that are not water. */
export function waterKey(model: WaterModel): KeyItem[] {
  const fills = new Map<string, KeyItem>();
  for (const segment of model.segments) {
    const known = fills.get(segment.token);
    fills.set(segment.token, { key: segment.token, label: known ? `${known.label}, ${segment.label}` : segment.label, token: segment.token, kind: "fill" });
  }
  const items = [...fills.values(), { key: "no-valve", label: NO_VALVE_WORDS, token: "--fault", kind: "fill" } as KeyItem];
  if (model.noData > 0) items.push({ key: "no-data", label: "no data", token: "--muted", kind: "hatch" });
  if (model.days.some((day) => day.coverage === "partial")) items.push({ key: "partial", label: "partial day", token: "--ink", kind: "dashed" });
  return items;
}

export type DailyWaterUseProps = {
  data: Pick<WaterUse, "days">;
  split: WaterSplit;
  /** Line names from API-IRR-01 (`name`), for the rows; the line id alone without them. */
  lineNames?: Readonly<Record<string, string>>;
};

export function DailyWaterUse({ data, split, lineNames }: DailyWaterUseProps) {
  // Names arrive as a new object on every status poll: the bars rebuild only when their values change.
  const names = JSON.stringify(lineNames ?? null);
  const model = useMemo(() => buildWaterUse(data, split, lineNames), [data, split, names]);
  const modelRef = useRef(model);
  modelRef.current = model;
  const columns = useMemo<uPlot.AlignedData>(() => [model.days.map((day) => day.at), ...model.stacks, model.noValve], [model]);
  const structure = `water|${split}|${model.segments.map((segment) => segment.key).join(",")}`;
  const label = `Whole gallons per day for ${model.days.length} days, stacked by ${split} with clean below fertilizer, beside a bar of water that ran with no Verdify valve open. No-data days are hatched, never zero. The data table follows.`;
  return <figure className="chart chart-water-use">
    <figcaption className="eyebrow">Gallons per day by {split} · last {model.days.length} days</figcaption>
    {model.days.length > 0
      ? <>
        <UPlotChart options={(env) => waterOptions(env, modelRef)} data={columns} xRange={model.xRange} structure={structure} className="plot-water-use" label={label} marks={model} zoom={false} />
        <ChartKey items={waterKey(model)} />
        <p className="plot-summary water-summary">{waterSummary(model)}</p>
        <div className="chart-tools">
          <CsvLink filename={dayCsvFilename(`water-use-by-${split}`, model.days[0].date, model.days[model.days.length - 1].date)} build={() => waterUseCsv(model)} version={model} label="CSV of this chart" />
        </div>
        <DataTable tables={[waterTable(model)]} />
      </>
      : <div className="plot plot-small chart-message"><p>No water data in this window</p></div>}
  </figure>;
}
