// SPDX-License-Identifier: MIT
import { useContext, useMemo, useRef, useState, type MutableRefObject } from "react";
import type uPlot from "uplot";
import { LINES, lineByEquipment, lineById, type Line } from "../lib/lines";
import { causeWords } from "./causes";
import { csvFilename, tableCsv } from "./csv";
import { CsvLink, DataTable, TimeCell, type DataTableBlock } from "./data-table";
import { labelColor, paintNow, type ChartColors, type PaintTarget } from "./draw";
import { LANE_HEAD, LANE_PITCH, RowLabels, clipColumns, intervalText, paintHatch, rowBand, spanDuration, spanPixels, type RowLayout } from "./lanes-draw";
import { ChartKey, type KeyItem } from "./legend";
import { lastAtOrBefore } from "./marks";
import { lineKindToken } from "./palette";
import { InStack, StackLink, type LitLane } from "./stack-context";
import { epochOf, isoLocal, spanText, timeAxis } from "./time-axis";
import { cursorX, tooltipPlugin } from "./tooltip";
import type { IrrigationCause, StateLane, StateLanes, XRange } from "./types";
import { UPlotChart, useNarrow, useZoomReset, type ChartEnv, type ChartOptions } from "./uplot-chart";

/**
 * EquipmentLanes (design system §5.2, US-GRF-02, #1356): what ran when, one row per piece of equipment from API-05,
 * painted in a `draw` hook on an x-only uPlot that shares the page's cursor and draws its own time axis under the rows,
 * with the same ticks as the panels above it. Rows are grouped by system under a header, in this order: Climate (fans,
 * vent, heat 1 electric, heat 2 gas), Misting (the misters and fog), Irrigation (the drip lines and the fertilizer
 * valve), Lighting (main and grow) and Sensors (water flowing, the leak flag and occupancy), which starts folded. A
 * group's rows off all window fold into its header ("Irrigation · 3 idle").
 *
 * On is a solid bar in the row's colour, everywhere: a line's own kind colour (as on Water and Now), fertilized water
 * in the fertilizer colour on its physical line's one row (a clean and a fertilized valve of one line share a row), a
 * light the Lutron readback. A light's controller intent (Q-09) is not a row: where it disagrees with the readback a
 * thin amber strip runs along the row's foot. Unknown spans are hatched exactly where the API returns them and off
 * draws nothing. A wet run's cause is said in words after the run (#1354). Labels are the API's.
 */
export type LaneState = "on" | "off" | "unknown";
export type LaneSpan = { start: number; end: number; state: LaneState; cause: IrrigationCause | null; /** On through the fertilized valve. */ fert?: boolean };
export type LaneSystem = "climate" | "misting" | "irrigation" | "lighting" | "sensors";
export const LANE_SYSTEMS: readonly LaneSystem[] = ["climate", "misting", "irrigation", "lighting", "sensors"];
export const SYSTEM_WORDS: Readonly<Record<LaneSystem, string>> = { climate: "Climate", misting: "Misting", irrigation: "Irrigation", lighting: "Lighting", sensors: "Sensors" };
/** Groups whose fold hides every row, not only the idle ones: the flags are read on demand. */
const FOLDED_SYSTEMS: ReadonlySet<LaneSystem> = new Set(["sensors"]);

export type LaneRow = {
  /** The row's key: the clean valve's on a physical line with two valves. */
  equipment: string;
  /** Every API-05 lane the row draws. */
  members: readonly string[];
  /** The API's label; the key itself when the API sent none. */
  label: string;
  group: StateLane["group"];
  system: LaneSystem;
  lineId: string | null;
  /** The row's own colour. */
  token: string;
  /** A wet line: its runs are named by cause in words after each run. */
  wet: boolean;
  spans: LaneSpan[];
  /** A grow light: where the controller's intent and the Lutron readback, both known, disagree. */
  mismatch: { start: number; end: number }[];
  /** Off for the whole window: folds into its group's header. */
  idle: boolean;
  /** The state at the window's end. */
  current: LaneState | null;
  /** Seconds on in the window. */
  runtime: number;
  /** OFF→ON edges in the window. */
  starts: number;
};
export type LaneGroupModel = { system: LaneSystem; rows: LaneRow[]; active: LaneRow[]; idle: LaneRow[] };
export type LanesModel = { from: number; to: number; rows: LaneRow[]; groups: LaneGroupModel[]; active: LaneRow[]; idle: LaneRow[] };

/** Climate, then each line in the register's order, then the lights and the flags. A key not listed follows its group's own. */
export const EQUIPMENT_ORDER: readonly string[] = [
  "fan1", "fan2", "vent", "heat1", "heat2",
  ...LINES.map((line) => line.equipment),
  "grow_light_main", "grow_light_grow",
  "water_flowing", "leak_detected", "occupancy",
];
const ROW_TOKENS: Readonly<Record<string, string>> = {
  fan1: "--eq-fan1", fan2: "--eq-fan2", vent: "--eq-vent", heat1: "--eq-heat1", heat2: "--eq-heat2",
  grow_light_main: "--eq-light-main", grow_light_grow: "--eq-light-grow",
  occupancy: "--muted", leak_detected: "--fault", water_flowing: "--eq-water",
};
/** The controller's grow-light intent (Q-09): never a row; it marks where the readback disagrees. */
export const isIntentKey = (equipment: string) => equipment.endsWith("_intent");

const STATES: readonly LaneState[] = ["on", "off", "unknown"];
/** A state the lanes draw; a newer one from the API is dropped, never drawn as off. */
const isLaneState = (state: string): state is LaneState => (STATES as readonly string[]).includes(state);

/** A fertilized valve's clean partner on the same physical line ("Wall drip (fertilized)" → "Wall drip"). */
export function physicalLine(line: Line): Line {
  if (line.chemistry !== "fert") return line;
  const name = line.name.replace(/\s*\(fertilized\)$/, "");
  return LINES.find((other) => other.chemistry === "clean" && other.name === name) ?? line;
}

/** Which system a lane belongs to: misters and fog are misting, drips and the fertilizer valve irrigation. */
export function systemOf(group: StateLane["group"], line: Line | undefined): LaneSystem {
  if (group === "climate") return "climate";
  if (group === "light") return "lighting";
  if (group === "flag") return "sensors";
  if (line && (line.kind === "mister" || line.kind === "fert_mister" || line.kind === "fog")) return "misting";
  return "irrigation";
}

function spansOf(lane: StateLane, from: number, to: number): LaneSpan[] {
  const spans: LaneSpan[] = [];
  for (const interval of lane.intervals) {
    const start = epochOf(interval.start), end = epochOf(interval.end), state = interval.state;
    if (start === null || end === null || !isLaneState(state)) continue;
    const clipped = { start: Math.max(start, from), end: Math.min(end, to) };
    if (clipped.end <= clipped.start) continue;
    spans.push({ ...clipped, state, cause: interval.cause ?? null });
  }
  return spans.sort((a, b) => a.start - b.start);
}

/** The span covering `x` in a sorted span list, by binary search. */
function spanIn(spans: readonly LaneSpan[], x: number): LaneSpan | null {
  const index = lastAtOrBefore(spans.map((span) => span.start), x);
  return index >= 0 && x < spans[index].end ? spans[index] : null;
}

/** Every instant either list changes state at, in order. */
function cuts(lists: readonly (readonly LaneSpan[])[]): number[] {
  return [...new Set(lists.flatMap((spans) => spans.flatMap((span) => [span.start, span.end])))].sort((a, b) => a - b);
}

/**
 * One physical line's row from its clean and fertilized valves: on while either is open (fertilized while that valve is),
 * unknown while either is unknown and neither is on, off while both are off; equal neighbours join.
 */
export function mergeSpans(clean: readonly LaneSpan[], fert: readonly LaneSpan[]): LaneSpan[] {
  const out: LaneSpan[] = [];
  const points = cuts([clean, fert]);
  for (let index = 0; index + 1 < points.length; index++) {
    const start = points[index], end = points[index + 1];
    const a = spanIn(clean, start), b = spanIn(fert, start);
    if (!a && !b) continue;
    let span: LaneSpan;
    if (b?.state === "on") span = { start, end, state: "on", cause: b.cause ?? a?.cause ?? null, fert: true };
    else if (a?.state === "on") span = { start, end, state: "on", cause: a.cause };
    else if (a?.state === "unknown" || b?.state === "unknown") span = { start, end, state: "unknown", cause: null };
    else span = { start, end, state: "off", cause: null };
    const last = out.at(-1);
    if (last && last.end === start && last.state === span.state && last.cause === span.cause && Boolean(last.fert) === Boolean(span.fert)) last.end = end;
    else out.push(span);
  }
  return out;
}

/** Where a light's controller intent and its readback, both on or off, disagree. */
export function mismatchSpans(readback: readonly LaneSpan[], intent: readonly LaneSpan[]): { start: number; end: number }[] {
  const out: { start: number; end: number }[] = [];
  const points = cuts([readback, intent]);
  for (let index = 0; index + 1 < points.length; index++) {
    const start = points[index], end = points[index + 1];
    const a = spanIn(readback, start)?.state, b = spanIn(intent, start)?.state;
    if (!a || !b || a === "unknown" || b === "unknown" || a === b) continue;
    const last = out.at(-1);
    if (last && last.end === start) last.end = end;
    else out.push({ start, end });
  }
  return out;
}

function row(lane: StateLane, spans: LaneSpan[], members: readonly string[], line: Line | undefined, mismatch: LaneRow["mismatch"] = []): LaneRow {
  let runtime = 0, starts = 0;
  spans.forEach((span, index) => {
    if (span.state !== "on") return;
    runtime += span.end - span.start;
    if (index > 0 && spans[index - 1].state === "off") starts++;
  });
  return {
    equipment: lane.equipment, members, label: lane.label || lane.equipment, group: lane.group, system: systemOf(lane.group, line), lineId: line?.id ?? lane.line_id ?? null,
    token: ROW_TOKENS[lane.equipment] ?? (line ? lineKindToken(line.kind) ?? "--muted" : "--muted"),
    wet: line !== undefined, spans, mismatch, idle: spans.length > 0 && spans.every((span) => span.state === "off"),
    current: spans.length ? spans[spans.length - 1].state : null, runtime, starts,
  };
}

/** The rows by system, in design order; a physical line's two valves share a row, and the intent lanes mark their light. */
export function buildLanes(states: StateLanes): LanesModel {
  const from = epochOf(states.from) ?? -Infinity, to = epochOf(states.to) ?? Infinity;
  const lineOf = (lane: StateLane) => (lane.line_id ? lineById(lane.line_id) : undefined) ?? lineByEquipment(lane.equipment);
  const byKey = new Map(states.lanes.map((lane) => [lane.equipment, lane]));
  const rank = (lane: StateLane) => {
    const order = EQUIPMENT_ORDER.indexOf(lane.equipment);
    return [LANE_SYSTEMS.indexOf(systemOf(lane.group, lineOf(lane))), order < 0 ? EQUIPMENT_ORDER.length : order];
  };
  const shown = states.lanes.filter((lane) => !isIntentKey(lane.equipment))
    .map((lane, position) => ({ lane, position, rank: rank(lane) }))
    .sort((a, b) => a.rank[0] - b.rank[0] || a.rank[1] - b.rank[1] || a.position - b.position);
  const merged = new Set<string>();
  const rows: LaneRow[] = [];
  for (const { lane } of shown) {
    if (merged.has(lane.equipment)) continue;
    const line = lineOf(lane);
    const physical = line ? physicalLine(line) : undefined;
    // A fertilized valve joins its clean partner's row when the API sends both.
    if (line && physical && physical !== line && byKey.has(physical.equipment)) continue;
    const partner = line && line.chemistry === "clean" ? LINES.find((other) => other !== line && other.chemistry === "fert" && physicalLine(other) === line) : undefined;
    const fertLane = partner ? byKey.get(partner.equipment) : undefined;
    const spans = spansOf(lane, from, to);
    if (fertLane) {
      merged.add(fertLane.equipment);
      rows.push(row(lane, mergeSpans(spans, spansOf(fertLane, from, to)), [lane.equipment, fertLane.equipment], line));
      continue;
    }
    const intent = byKey.get(`${lane.equipment}_intent`);
    rows.push(row(lane, spans, [lane.equipment], line, intent ? mismatchSpans(spans, spansOf(intent, from, to)) : []));
  }
  const groups = LANE_SYSTEMS.flatMap((system): LaneGroupModel[] => {
    const members = rows.filter((item) => item.system === system);
    return members.length ? [{ system, rows: members, active: members.filter((item) => !item.idle), idle: members.filter((item) => item.idle) }] : [];
  });
  return { from, to, rows, groups, active: rows.filter((item) => !item.idle), idle: rows.filter((item) => item.idle) };
}

/** The state in words: "on", "on · fertilized". */
export function stateWords(_row: LaneRow, state: LaneState | null, span?: LaneSpan | null): string {
  if (!state) return "—";
  return span?.fert && state === "on" ? "on · fertilized" : state;
}

/** The span covering `x`, by binary search. */
export function spanAt(row: LaneRow, x: number): LaneSpan | null {
  return spanIn(row.spans, x);
}

/** A group's fold is open: its idle rows show, or, for a group folded whole, its rows show at all. */
export const isFoldedWhole = (system: LaneSystem) => FOLDED_SYSTEMS.has(system);
/** The groups whose fold starts open: none; Sensors starts hidden and every idle row folded. */
export const OPEN_AT_START: ReadonlySet<LaneSystem> = new Set();

/** One line of the lanes, top to bottom: a group's header, or a row. */
export type LaneEntry = { kind: "head"; group: LaneGroupModel } | { kind: "row"; row: LaneRow };
/** The lines shown: each group's header, its active rows, then its idle rows while its fold is open. */
export function laneEntries(model: LanesModel, open: ReadonlySet<LaneSystem>): LaneEntry[] {
  return model.groups.flatMap((group): LaneEntry[] => {
    const rows = isFoldedWhole(group.system) ? (open.has(group.system) ? group.rows : []) : open.has(group.system) ? [...group.active, ...group.idle] : group.active;
    return [{ kind: "head", group }, ...rows.map((item): LaneEntry => ({ kind: "row", row: item }))];
  });
}

/** A group header's height in CSS px (charts.css mirrors it): its fold is a 24 px touch target (WCAG 2.5.8). */
export const GROUP_HEAD = 24;

/** What the canvas paints: one entry per shown line (null for a group header), each line's top, the row pitch and now. */
export type LanesView = {
  rows: (LaneRow | null)[];
  tops: number[];
  pitch: number;
  head?: number;
  now: number | null;
  /** The spans the hovered event touched (#1355), outlined. */
  lit?: readonly LitLane[];
  onLane?: ((equipment: readonly string[] | null, at: number | null) => void) | null;
};

/** The view of the entries: a header is GROUP_HEAD high, a row `pitch`. */
export function laneView(entries: readonly LaneEntry[], pitch: number, head: number, now: number | null): LanesView {
  const tops: number[] = [];
  let top = 0;
  for (const entry of entries) { tops.push(top); top += entry.kind === "head" ? GROUP_HEAD : pitch; }
  return { rows: entries.map((entry) => (entry.kind === "row" ? entry.row : null)), tops, pitch, head, now };
}

/** The row at `top` CSS px from the plot's top; null over a header or below the rows. */
export function rowAtTop(view: LanesView, top: number): LaneRow | null {
  const index = lastAtOrBefore(view.tops, top);
  if (index < 0) return null;
  const item = view.rows[index];
  return item && top < view.tops[index] + view.pitch ? item : null;
}

/** The tooltip for the row under the pointer: name, state, start–end with offsets, duration and a wet line's cause. */
export function laneTooltip(view: LanesView, plot: Pick<uPlot, "cursor" | "posToVal">): string[] | null {
  // A cursor moved by another chart on the sync key has no row; that chart shows its own tooltip.
  if (!plot.cursor.event) return null;
  const x = cursorX(plot as uPlot), top = plot.cursor.top;
  if (x === null || top === undefined || top === null || top < 0) return null;
  const row = rowAtTop(view, top);
  if (!row) return null;
  const span = spanAt(row, x);
  if (!span) return null;
  const lines = [row.label, stateWords(row, span.state, span), intervalText(span.start, span.end), spanDuration(span.end - span.start)];
  if (row.wet && span.state === "on" && span.cause) lines.push(causeWords(span.cause));
  if (row.mismatch.some((part) => part.start <= x && x < part.end)) lines.push("controller intent differs");
  return lines;
}

/**
 * draw: each shown row's spans as solid bars, a light's intent mismatch along its foot, the outlines of what a hovered
 * event touched, then the labels. A wet line's run of pulses with one cause is named once, after the run; an unknown
 * span is named after it; a label that would overlap a span or another label is dropped.
 */
export function paintLanes(plot: PaintTarget, view: LanesView, colors: ChartColors, px: number) {
  const layout: RowLayout = { pitch: view.pitch, head: view.head, tops: view.tops };
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
        else {
          ctx.fillStyle = colors[span.fert ? "--eq-fert" : row.token];
          ctx.fillRect(pixels.left, band.top, width, band.height);
        }
        labels.reserve(index, pixels.left, pixels.left + width);
      }
      for (const part of row.mismatch) {
        const pixels = spanPixels(plot, part.start, part.end);
        if (!pixels) continue;
        ctx.fillStyle = colors["--amber"];
        ctx.fillRect(pixels.left, band.top + band.height - 3 * px, Math.max(pixels.right - pixels.left, px), 3 * px);
      }
      for (const lit of view.lit ?? []) {
        if (!row.members.includes(lit.equipment)) continue;
        const pixels = spanPixels(plot, lit.from, Math.max(lit.to, lit.from + 1));
        if (!pixels) continue;
        ctx.strokeStyle = colors["--ink"];
        ctx.lineWidth = 2 * px;
        ctx.setLineDash([]);
        ctx.strokeRect(pixels.left - px, band.top - px, Math.max(pixels.right - pixels.left, px) + 2 * px, band.height + 2 * px);
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
        labels.place(index, band, causeWords(span.cause), pixels.right + 4 * px, limit, labelColor(colors[row.token] ?? "", colors["--ink"]));
      }
    });
    if (view.now !== null) paintNow(plot, view.now, colors, px);
  });
}

/**
 * The lanes' uPlot options: their own time axis under the rows, alone or inside a chart stack, where they draw on
 * exactly the panels' plot area (no padding: the stylesheet places the canvas 56 px from the left and 10 px from the
 * right, as the panels' y axis and right padding), so the ticks line up with the panels' (#1356).
 */
export function lanesOptions(env: ChartEnv, viewRef: MutableRefObject<LanesView>, stacked = false): ChartOptions {
  const { colors, px } = env;
  const tokens = { muted: colors["--muted"], grid: colors["--c-grid"] };
  return {
    padding: stacked ? [0, 0, 0, 0] : [0, null, 0, null],
    series: [{}],
    // uPlot always makes a second (y) axis: the lanes have no y, so it is hidden.
    axes: [timeAxis(tokens), { show: false }],
    hooks: {
      draw: [(plot) => paintLanes(plot, viewRef.current, colors, px())],
      // The pointer's own row and instant light the marks that touched it (#1355); a synced cursor has no row.
      setCursor: [(plot) => {
        const onLane = viewRef.current.onLane;
        if (!onLane) return;
        const x = plot.cursor.event ? cursorX(plot) : null, top = plot.cursor.top;
        const row = x !== null && top !== undefined && top !== null && top >= 0 ? rowAtTop(viewRef.current, top) : null;
        onLane(row ? row.members : null, row ? x : null);
      }],
    },
    plugins: [tooltipPlugin((plot) => laneTooltip(viewRef.current, plot))],
  };
}

/** The data table: every span of every row, in row order, with its state and a wet line's cause in words. */
export function lanesTable(model: LanesModel): DataTableBlock {
  const count = model.rows.reduce((sum, item) => sum + item.spans.length, 0);
  return {
    caption: "Equipment intervals · America/Denver", headers: ["Equipment", "State", "From", "Until", "Duration", "Cause"], count,
    rows: () => model.rows.flatMap((item) => item.spans.map((span) => [
      item.label, stateWords(item, span.state, span), <TimeCell key="from" at={span.start} />, <TimeCell key="until" at={span.end} />,
      spanDuration(span.end - span.start), item.wet && span.state === "on" && span.cause ? causeWords(span.cause) : "",
    ])),
  };
}

/**
 * "CSV of these lanes" (redesign §7.1): every interval of every row, in row order, with its local start and end, its
 * length and a wet line's cause; unknown is its own state, never off, and a fertilized run says so.
 */
export function lanesCsv(model: LanesModel): string {
  return tableCsv(
    ["equipment", "label", "state", "start (America/Denver)", "end (America/Denver)", "seconds", "cause"],
    model.rows.flatMap((item) => item.spans.map((span) => [
      item.equipment, item.label, span.fert && span.state === "on" ? "on fertilized" : span.state, isoLocal(span.start), isoLocal(span.end), Math.round(span.end - span.start),
      item.wet && span.state === "on" ? span.cause ?? "" : "",
    ])),
  );
}

/**
 * The key: every mark that is drawn and that a row's name does not explain: on as a solid bar, fertilized water, the
 * intent mismatch and unknown's hatch, in words. Each row is named, so its colour needs no key, and a run's cause is
 * written after it.
 */
export function lanesKey(model: LanesModel): KeyItem[] {
  let on = false, fert = false, unknown = false, mismatch = false;
  for (const item of model.rows) {
    if (item.mismatch.length) mismatch = true;
    for (const span of item.spans) {
      if (span.state === "unknown") unknown = true;
      if (span.state === "on") { on = true; if (span.fert) fert = true; }
    }
  }
  return [
    ...(on ? [{ key: "on", label: "on", token: "--ink", kind: "fill" } as const] : []),
    ...(fert ? [{ key: "fert", label: "fertilized", token: "--eq-fert", kind: "fill" } as const] : []),
    ...(mismatch ? [{ key: "intent", label: "controller intent differs", token: "--amber", kind: "line" } as const] : []),
    ...(unknown ? [{ key: "unknown-span", label: "unknown", token: "--muted", kind: "hatch" } as const] : []),
  ];
}

const runtimeText = (row: LaneRow) => spanDuration(row.runtime);

/** A row's state at the window's end, on time and starts in words, for its line inside a chart stack: "off · 80 min on · 1 start". */
export function rowFacts(row: LaneRow): string {
  return `${stateWords(row, row.current, row.spans.at(-1))} · ${runtimeText(row)} on · ${row.starts} ${row.starts === 1 ? "start" : "starts"}`;
}

function LaneItem({ row, stacked }: { row: LaneRow; stacked: boolean }) {
  // In a stack the row's name and its facts head its track, in words; alone they are columns beside it.
  if (stacked) {
    const facts = rowFacts(row);
    return <li className="lanes-row" data-equipment={row.equipment} title={`${row.label} · ${facts}`}>
      <span className="lanes-label">{row.label}</span>{" "}<span className="lanes-stat">{facts}</span>
    </li>;
  }
  return <li className="lanes-row" data-equipment={row.equipment}>
    <span className="lanes-label" title={row.label}>{row.label}</span>
    <span className="lanes-stat">
      <span className="lanes-wide">{stateWords(row, row.current, row.spans.at(-1))} · </span>
      <span>{runtimeText(row)}</span>
      <span className="lanes-wide"> · {row.starts}</span>
    </span>
  </li>;
}

/** A group's header: its name and, when it has rows to fold, the fold ("3 idle", or "3 rows" for Sensors). */
function GroupHead({ group, open, onToggle }: { group: LaneGroupModel; open: boolean; onToggle: () => void }) {
  const whole = isFoldedWhole(group.system);
  const count = whole ? group.rows.length : group.idle.length;
  const words = whole ? `${count} ${count === 1 ? "row" : "rows"}` : `${count} idle`;
  return <li className="lanes-group" data-system={group.system}>
    <span className="lanes-group-name">{SYSTEM_WORDS[group.system]}</span>
    {count > 0 && <button type="button" className="lanes-group-fold" aria-expanded={open} aria-label={`${SYSTEM_WORDS[group.system]}: ${words}`} onClick={onToggle}>{words}</button>}
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
  /** Only these systems' groups (Graphs → Lighting draws Lighting and Sensors); default every one. */
  systems?: readonly LaneSystem[];
  /** Groups whose fold starts open (Graphs → Lighting opens Sensors, for occupancy). */
  open?: readonly LaneSystem[];
};

export function EquipmentLanes(props: EquipmentLanesProps) {
  const { states, xRange, syncKey } = props;
  const narrow = useNarrow();
  // Inside a chart stack the lanes share the panels' plot area (redesign §7.1) and its link to the event rail (#1355).
  const stacked = useContext(InStack);
  const link = useContext(StackLink);
  const [open, setOpen] = useState<ReadonlySet<LaneSystem>>(() => new Set(props.open ?? OPEN_AT_START));
  const zoom = useZoomReset(xRange, syncKey);
  const systems = props.systems;
  const model = useMemo(() => {
    if (!states) return null;
    const built = buildLanes(states);
    if (!systems) return built;
    const keep = (item: { system: LaneSystem }) => systems.includes(item.system);
    return { ...built, rows: built.rows.filter(keep), groups: built.groups.filter(keep), active: built.active.filter(keep), idle: built.idle.filter(keep) };
  }, [states, systems?.join()]);
  const head = stacked ? LANE_HEAD : 0;
  const entries = model ? laneEntries(model, open) : [];
  const view: LanesView = { ...laneView(entries, (narrow ? LANE_PITCH.narrow : LANE_PITCH.wide) + head, head, props.now ?? null), lit: link?.lit ?? [], onLane: link?.onLane ?? null };
  const viewRef = useRef(view);
  viewRef.current = view;
  const data = useMemo<uPlot.AlignedData>(() => [[xRange.from, xRange.to]], [xRange.from, xRange.to]);
  // New intervals, a fold, the row pitch, now or what an event lit repaint the canvas without rebuilding it.
  const marks = useMemo(() => ({ model, open, pitch: view.pitch, now: view.now, lit: view.lit }), [model, open, view.pitch, view.now, view.lit]);
  const drawable = !props.message && model !== null && model.rows.length > 0;
  const toggle = (system: LaneSystem) => setOpen((previous) => {
    const next = new Set(previous);
    if (!next.delete(system)) next.add(system);
    return next;
  });
  return <figure className="chart chart-lanes">
    {/* In a stack its disclosure's summary names the lanes, and each row says its facts in words. */}
    {!stacked && <figcaption className="lanes-caption">
      <span className="eyebrow">EQUIPMENT · WHAT RAN</span>
      <span className="lanes-columns" aria-hidden="true"><span className="lanes-wide">state · </span>on time<span className="lanes-wide"> · starts</span></span>
    </figcaption>}
    {drawable
      ? <div className={stacked ? "lanes lanes-equipment lanes-stacked" : "lanes lanes-equipment"} onMouseLeave={() => link?.onLane(null, null)}>
        <ul className="lanes-rows" aria-label="Equipment by system, state at the window's end, on time and starts">
          {entries.map((entry) => (entry.kind === "head"
            ? <GroupHead key={`head-${entry.group.system}`} group={entry.group} open={open.has(entry.group.system)} onToggle={() => toggle(entry.group.system)} />
            : <LaneItem key={entry.row.equipment} row={entry.row} stacked={stacked} />))}
        </ul>
        <div className="lanes-axis" aria-hidden="true" />
        <UPlotChart options={(env) => lanesOptions(env, viewRef, stacked)} data={data} xRange={xRange} structure={`lanes|${syncKey ?? ""}|${stacked ? "stacked" : "alone"}`} className="lanes-canvas"
          label="Equipment lanes by system: what ran when, America/Denver. On is a solid bar; unknown spans are hatched, never drawn as off. The data table follows."
          syncKey={syncKey} marks={marks} onZoom={zoom.onZoom} onPlot={zoom.onPlot} />
      </div>
      : <div className="plot plot-small chart-message"><p>{props.message ?? "No equipment data in this window"}</p></div>}
    {/* The key sits right under the rows it names, above the CSV and the table. */}
    {drawable && <ChartKey items={lanesKey(model)} />}
    {drawable && <div className="chart-tools">
      {(props.zoomControl ?? true) && zoom.zoomed && <button type="button" onClick={zoom.reset}>Reset zoom</button>}
      <CsvLink filename={csvFilename("equipment-lanes", xRange)} build={() => lanesCsv(model)} version={model} label="CSV of these lanes" />
    </div>}
    {drawable && <DataTable name="Lanes table" tables={[lanesTable(model)]} />}
  </figure>;
}
