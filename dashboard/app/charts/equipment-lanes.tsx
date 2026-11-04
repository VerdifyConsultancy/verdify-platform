// SPDX-License-Identifier: MIT
import { useId, useMemo, useRef, useState, type MutableRefObject } from "react";
import type uPlot from "uplot";
import { LINES, lineByEquipment, lineById } from "../lib/lines";
import type { LineKind } from "../lib/vocab";
import { causeRank, causeToken, causeWords } from "./causes";
import { csvFilename, tableCsv } from "./csv";
import { CsvLink, DataTable, TimeCell, type DataTableBlock } from "./data-table";
import { labelColor, paintNow, type ChartColors, type PaintTarget } from "./draw";
import { LANE_PITCH, RowLabels, clipColumns, intervalText, paintHatch, rowBand, spanDuration, spanPixels, type RowLayout } from "./lanes-draw";
import { ChartKey, type KeyItem } from "./legend";
import { lastAtOrBefore } from "./marks";
import { epochOf, isoLocal, spanText, timeAxis } from "./time-axis";
import { cursorX, tooltipPlugin } from "./tooltip";
import type { IrrigationCause, StateLane, StateLanes, XRange } from "./types";
import { UPlotChart, useNarrow, useZoomReset, type ChartEnv, type ChartOptions } from "./uplot-chart";

/**
 * EquipmentLanes (design system §5.2, US-GRF-02): what each actuator was commanded to do, one row per API-05 lane,
 * painted in a `draw` hook on an x-only uPlot that shares the page's cursor. ON fills with the row's token (a wet line
 * with the colour of its cause), sensed rows are outlined, unknown spans are hatched exactly where the API returns them
 * and off draws nothing. Row labels are the API's; the client never makes one up.
 */
export type LaneState = "on" | "off" | "unknown";
export type LaneSpan = { start: number; end: number; state: LaneState; cause: IrrigationCause | null };
export type LaneRow = {
  equipment: string;
  /** The API's label; the key itself when the API sent none. */
  label: string;
  group: StateLane["group"];
  lineId: string | null;
  /** The row's own colour. */
  token: string;
  /** The Lutron readback and `water_flowing` are sensed, not commanded: outlined. */
  sensed: boolean;
  /** A wet line: its ON spans take their cause's colour. */
  wet: boolean;
  spans: LaneSpan[];
  /** Off for the whole window: folds into the "N idle rows" line. */
  idle: boolean;
  /** The state at the window's end. */
  current: LaneState | null;
  /** Seconds on in the window. */
  runtime: number;
  /** OFF→ON edges in the window. */
  starts: number;
};
export type LanesModel = { from: number; to: number; rows: LaneRow[]; active: LaneRow[]; idle: LaneRow[] };

const GROUPS: readonly StateLane["group"][] = ["climate", "water", "light", "flag"];
/** Climate, Water in L01–L11 order, Light (the Lutron readback), Flags. A key not listed follows its group's own. */
export const EQUIPMENT_ORDER: readonly string[] = [
  "fan1", "fan2", "vent", "heat1", "heat2",
  ...LINES.map((line) => line.equipment),
  "grow_light_main", "grow_light_grow",
  "occupancy", "leak_detected", "water_flowing",
];
const ROW_TOKENS: Readonly<Record<string, string>> = {
  fan1: "--eq-fan1", fan2: "--eq-fan2", vent: "--eq-vent", heat1: "--eq-heat1", heat2: "--eq-heat2",
  grow_light_main: "--eq-light-main", grow_light_grow: "--eq-light-grow",
  occupancy: "--muted", leak_detected: "--fault", water_flowing: "--eq-water",
};
const KIND_TOKENS: Readonly<Record<LineKind, string>> = { drip: "--eq-water", fert_drip: "--eq-fert", fert_mister: "--eq-fert", master: "--eq-master", mister: "--eq-mister", fog: "--eq-fog" };
const SENSED = new Set(["grow_light_main", "grow_light_grow", "water_flowing"]);
/** The controller's grow-light intent shows only on the Equipment board (Q-09), never as a lane. */
export const isIntentKey = (equipment: string) => equipment.endsWith("_intent");

const STATES: readonly LaneState[] = ["on", "off", "unknown"];
/** A state the lanes draw; a newer one from the API is dropped, never drawn as off. */
const isLaneState = (state: string): state is LaneState => (STATES as readonly string[]).includes(state);

function laneRow(lane: StateLane, from: number, to: number): LaneRow {
  const line = (lane.line_id ? lineById(lane.line_id) : undefined) ?? lineByEquipment(lane.equipment);
  const spans: LaneSpan[] = [];
  for (const interval of lane.intervals) {
    const start = epochOf(interval.start), end = epochOf(interval.end), state = interval.state;
    if (start === null || end === null || !isLaneState(state)) continue;
    const clipped = { start: Math.max(start, from), end: Math.min(end, to) };
    if (clipped.end <= clipped.start) continue;
    spans.push({ ...clipped, state, cause: interval.cause ?? null });
  }
  spans.sort((a, b) => a.start - b.start);
  let runtime = 0, starts = 0;
  spans.forEach((span, index) => {
    if (span.state !== "on") return;
    runtime += span.end - span.start;
    if (index > 0 && spans[index - 1].state === "off") starts++;
  });
  return {
    equipment: lane.equipment, label: lane.label || lane.equipment, group: lane.group, lineId: line?.id ?? lane.line_id ?? null,
    token: ROW_TOKENS[lane.equipment] ?? (line ? KIND_TOKENS[line.kind] : "--muted"),
    sensed: SENSED.has(lane.equipment), wet: line !== undefined,
    spans, idle: spans.length > 0 && spans.every((span) => span.state === "off"),
    current: spans.length ? spans[spans.length - 1].state : null, runtime, starts,
  };
}

/** The rows in design order, `_intent` keys dropped; rows off all window go to `idle`. */
export function buildLanes(states: StateLanes): LanesModel {
  const from = epochOf(states.from) ?? -Infinity, to = epochOf(states.to) ?? Infinity;
  const rank = (lane: StateLane) => {
    const group = GROUPS.indexOf(lane.group);
    const order = EQUIPMENT_ORDER.indexOf(lane.equipment);
    return [group < 0 ? GROUPS.length : group, order < 0 ? EQUIPMENT_ORDER.length : order];
  };
  const lanes = states.lanes.filter((lane) => !isIntentKey(lane.equipment))
    .map((lane, position) => ({ lane, position, rank: rank(lane) }))
    .sort((a, b) => a.rank[0] - b.rank[0] || a.rank[1] - b.rank[1] || a.position - b.position);
  const rows = lanes.map(({ lane }) => laneRow(lane, from, to));
  return { from, to, rows, active: rows.filter((row) => !row.idle), idle: rows.filter((row) => row.idle) };
}

/** The colour of an ON span: a wet line's cause when it has one, else the row's own. */
export function spanToken(row: LaneRow, span: LaneSpan): string {
  return row.wet && span.cause ? causeToken(span.cause) : row.token;
}

/** The state in words, "· sensed" on the readback rows. */
export function stateWords(row: LaneRow, state: LaneState | null): string {
  if (!state) return "—";
  return row.sensed ? `${state} · sensed` : state;
}

/** The span covering `x`, by binary search. */
export function spanAt(row: LaneRow, x: number): LaneSpan | null {
  const index = lastAtOrBefore(row.spans.map((span) => span.start), x);
  return index >= 0 && x < row.spans[index].end ? row.spans[index] : null;
}

/** What the canvas paints: the shown rows (null is the fold line), their pitch and now. */
export type LanesView = { rows: (LaneRow | null)[]; pitch: number; now: number | null };

export function visibleRows(model: LanesModel, expanded: boolean): (LaneRow | null)[] {
  if (!model.idle.length) return model.active;
  return [...model.active, null, ...(expanded ? model.idle : [])];
}

/** The tooltip for the row under the pointer: name, state, start–end with offsets, duration and a wet line's cause. */
export function laneTooltip(view: LanesView, plot: Pick<uPlot, "cursor" | "posToVal">): string[] | null {
  // A cursor moved by another chart on the sync key has no row; that chart shows its own tooltip.
  if (!plot.cursor.event) return null;
  const x = cursorX(plot as uPlot), top = plot.cursor.top;
  if (x === null || top === undefined || top === null || top < 0) return null;
  const row = view.rows[Math.floor(top / view.pitch)];
  if (!row) return null;
  const span = spanAt(row, x);
  if (!span) return null;
  const lines = [row.label, stateWords(row, span.state), intervalText(span.start, span.end), spanDuration(span.end - span.start)];
  if (row.wet && span.state === "on" && span.cause) lines.push(causeWords(span.cause));
  return lines;
}

/**
 * draw: each shown row's spans, then its labels. A wet line's run of pulses with one cause is named once, after the
 * run; an unknown span is named after it; a label that would overlap a span or another label is dropped.
 */
export function paintLanes(plot: PaintTarget, view: LanesView, colors: ChartColors, px: number) {
  const layout: RowLayout = { pitch: view.pitch };
  clipColumns(plot, () => {
    const { ctx, bbox } = plot;
    const labels = new RowLabels(ctx, px);
    const right = bbox.left + bbox.width;
    view.rows.forEach((row, index) => {
      if (!row) return;
      const band = rowBand(plot, layout, index, px);
      const drawn = row.spans.map((span) => ({ span, pixels: spanPixels(plot, span.start, span.end) }));
      for (const { span, pixels } of drawn) {
        if (!pixels || span.state === "off") continue;
        const width = Math.max(pixels.right - pixels.left, px);
        if (span.state === "unknown") paintHatch(ctx, pixels.left, band.top, width, band.height, colors["--muted"], px);
        else if (row.sensed) {
          ctx.strokeStyle = colors[spanToken(row, span)];
          ctx.lineWidth = 1.5 * px;
          ctx.setLineDash([]);
          ctx.strokeRect(pixels.left + 0.75 * px, band.top + 0.75 * px, Math.max(width - 1.5 * px, px), band.height - 1.5 * px);
        } else {
          ctx.fillStyle = colors[spanToken(row, span)];
          ctx.fillRect(pixels.left, band.top, width, band.height);
        }
        labels.reserve(index, pixels.left, pixels.left + width);
      }
      // Labels after runs: the next painted span (or the plot's edge) limits each.
      const painted = drawn.filter(({ span, pixels }) => pixels && span.state !== "off") as { span: LaneSpan; pixels: { left: number; right: number } }[];
      const gap = 24 * px;
      for (let position = 0; position < painted.length; position++) {
        const { span, pixels } = painted[position];
        const next = painted[position + 1];
        const limit = (next ? next.pixels.left : right) - 3 * px;
        if (span.state === "unknown") {
          const start = pixels.right + 4 * px;
          if (!labels.place(index, band, `unknown ${spanText(span.start, span.end)}`, start, limit, colors["--muted"])) labels.place(index, band, "unknown", start, limit, colors["--muted"]);
          continue;
        }
        if (!row.wet || !span.cause) continue;
        // One label per run: skip while the next span continues the run.
        if (next && next.span.state === "on" && next.span.cause === span.cause && next.pixels.left - pixels.right < gap) continue;
        const token = causeToken(span.cause);
        labels.place(index, band, causeWords(span.cause), pixels.right + 4 * px, limit, labelColor(colors[token] ?? "", colors["--ink"]));
      }
    });
    if (view.now !== null) paintNow(plot, view.now, colors, px);
  });
}

export function lanesOptions(env: ChartEnv, viewRef: MutableRefObject<LanesView>): ChartOptions {
  const { colors, px } = env;
  return {
    padding: [0, null, 0, null],
    series: [{}],
    // uPlot always makes a second (y) axis: the lanes have no y, so it is hidden.
    axes: [timeAxis({ muted: colors["--muted"], grid: colors["--c-grid"] }), { show: false }],
    hooks: { draw: [(plot) => paintLanes(plot, viewRef.current, colors, px())] },
    plugins: [tooltipPlugin((plot) => laneTooltip(viewRef.current, plot))],
  };
}

/** The data table: every span of every row, in row order, with its state and a wet line's cause in words. */
export function lanesTable(model: LanesModel): DataTableBlock {
  const count = model.rows.reduce((sum, row) => sum + row.spans.length, 0);
  return {
    caption: "Equipment intervals · America/Denver", headers: ["Equipment", "State", "From", "Until", "Duration", "Cause"], count,
    rows: () => model.rows.flatMap((row) => row.spans.map((span) => [
      row.label, stateWords(row, span.state), <TimeCell key="from" at={span.start} />, <TimeCell key="until" at={span.end} />,
      spanDuration(span.end - span.start), row.wet && span.state === "on" && span.cause ? causeWords(span.cause) : "",
    ])),
  };
}

/**
 * "CSV of these lanes" (redesign §7.1): every interval of every row, in row order, with its local start and end, its
 * length and a wet line's cause; unknown is its own state, never off.
 */
export function lanesCsv(model: LanesModel): string {
  return tableCsv(
    ["equipment", "label", "state", "start (America/Denver)", "end (America/Denver)", "seconds", "cause"],
    model.rows.flatMap((row) => row.spans.map((span) => [
      row.equipment, row.label, row.sensed ? `${span.state} sensed` : span.state, isoLocal(span.start), isoLocal(span.end), Math.round(span.end - span.start),
      row.wet && span.state === "on" ? span.cause ?? "" : "",
    ])),
  );
}

/** The key: each cause on a wet line, unknown's hatch and the sensed outline, in words; only marks that are drawn. */
export function lanesKey(model: LanesModel): KeyItem[] {
  const causes = new Set<string>();
  let unknown = false, sensed = false;
  for (const row of model.rows) {
    for (const span of row.spans) {
      if (span.state === "unknown") unknown = true;
      if (row.wet && span.state === "on" && span.cause) causes.add(span.cause);
      // A sensed row draws its outline only around an ON span; unknown alone is hatched.
      if (row.sensed && span.state === "on") sensed = true;
    }
  }
  return [
    ...[...causes].sort((a, b) => causeRank(a) - causeRank(b)).map((cause): KeyItem => ({ key: cause, label: causeWords(cause), token: causeToken(cause), kind: "fill" })),
    ...(unknown ? [{ key: "unknown-span", label: "unknown", token: "--muted", kind: "hatch" } as const] : []),
    ...(sensed ? [{ key: "sensed", label: "sensed", token: "--ink", kind: "outline" } as const] : []),
  ];
}

const runtimeText = (row: LaneRow) => spanDuration(row.runtime);

function LaneItem({ row }: { row: LaneRow }) {
  return <li className="lanes-row" data-equipment={row.equipment}>
    <span className="lanes-label" title={row.label}>{row.label}</span>
    <span className="lanes-stat">
      <span className="lanes-wide">{stateWords(row, row.current)} · </span>
      <span>{runtimeText(row)}</span>
      <span className="lanes-wide"> · {row.starts}</span>
    </span>
  </li>;
}

export type EquipmentLanesProps = {
  /** API-05; null while it has not loaded. */
  states: StateLanes | null;
  xRange: XRange;
  /** The page's `verdify:<route>:<range>`, shared with its TimeSeries panels. */
  syncKey?: string;
  /** Epoch seconds: the now line, when inside the range. */
  now?: number;
  /** Shown in the reserved frame instead of the lanes (an error, or a window over 31 days). */
  message?: string;
  /** Its own "Reset zoom" while zoomed; a page whose panels offer one for the sync key passes false. Default true. */
  zoomControl?: boolean;
};

export function EquipmentLanes(props: EquipmentLanesProps) {
  const { states, xRange, syncKey } = props;
  const narrow = useNarrow();
  const idleId = useId();
  const [expanded, setExpanded] = useState(false);
  const zoom = useZoomReset(xRange, syncKey);
  const model = useMemo(() => (states ? buildLanes(states) : null), [states]);
  const view: LanesView = { rows: model ? visibleRows(model, expanded) : [], pitch: narrow ? LANE_PITCH.narrow : LANE_PITCH.wide, now: props.now ?? null };
  const viewRef = useRef(view);
  viewRef.current = view;
  const data = useMemo<uPlot.AlignedData>(() => [[xRange.from, xRange.to]], [xRange.from, xRange.to]);
  // New intervals, the fold, the row pitch or now repaint the canvas without rebuilding it.
  const marks = useMemo(() => ({ model, expanded, pitch: view.pitch, now: view.now }), [model, expanded, view.pitch, view.now]);
  const drawable = !props.message && model !== null && model.rows.length > 0;
  const words = (count: number) => `${count} idle ${count === 1 ? "row" : "rows"}`;
  return <figure className="chart chart-lanes">
    <figcaption className="lanes-caption">
      <span className="eyebrow">EQUIPMENT · COMMANDED</span>
      <span className="lanes-columns" aria-hidden="true"><span className="lanes-wide">state · </span>on time<span className="lanes-wide"> · starts</span></span>
    </figcaption>
    {drawable
      ? <div className="lanes lanes-equipment">
        <ul className="lanes-rows" aria-label="Equipment, state at the window's end, on time and starts">
          {model.active.map((row) => <LaneItem key={row.equipment} row={row} />)}
          {model.idle.length > 0 && <li className="lanes-row lanes-fold">
            <button type="button" aria-expanded={expanded} aria-controls={idleId} onClick={() => setExpanded((open) => !open)}>{words(model.idle.length)}</button>
          </li>}
        </ul>
        {model.idle.length > 0 && <ul id={idleId} className="lanes-rows" hidden={!expanded} aria-label="Idle equipment">
          {model.idle.map((row) => <LaneItem key={row.equipment} row={row} />)}
        </ul>}
        <div className="lanes-axis" aria-hidden="true" />
        <UPlotChart options={(env) => lanesOptions(env, viewRef)} data={data} xRange={xRange} structure={`lanes|${syncKey ?? ""}`} className="lanes-canvas"
          label="Equipment lanes: what each actuator was commanded to do over time, America/Denver. Unknown spans are hatched, never drawn as off. The data table follows."
          syncKey={syncKey} marks={marks} onZoom={zoom.onZoom} onPlot={zoom.onPlot} />
      </div>
      : <div className="plot plot-small chart-message"><p>{props.message ?? "No equipment data in this window"}</p></div>}
    {drawable && <div className="chart-tools">
      {(props.zoomControl ?? true) && zoom.zoomed && <button type="button" onClick={zoom.reset}>Reset zoom</button>}
      <CsvLink filename={csvFilename("equipment-lanes", xRange)} build={() => lanesCsv(model)} version={model} label="CSV of these lanes" />
    </div>}
    {drawable && <ChartKey items={lanesKey(model)} />}
    {drawable && <DataTable tables={[lanesTable(model)]} />}
  </figure>;
}
