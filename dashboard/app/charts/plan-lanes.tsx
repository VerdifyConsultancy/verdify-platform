// SPDX-License-Identifier: MIT
import { useMemo, useRef, type MutableRefObject, type ReactNode } from "react";
import type uPlot from "uplot";
import { displayNumber, settingDisplay } from "../lib/setting-values";
import type { Plan, PlanLane, PlanWaypoint } from "../lib/operator-api";
import { isAiSource } from "../lib/vocab";
import { DataTable, TimeCell, type DataTableBlock } from "./data-table";
import { labelColor, type ChartColors, type PaintTarget } from "./draw";
import { RowLabels, clipColumns, laneFont, rowBand, spanPixels, type RowLayout } from "./lanes-draw";
import { FORECAST_RULE, PLANNED_AI, SUPERSEDED } from "./marks";
import { clockTime, dayWords, epochOf, fullTime, localDay, timeAxis } from "./time-axis";
import { cursorX, tooltipPlugin } from "./tooltip";
import type { XRange } from "./types";
import { UPlotChart, useNarrow, useZoomReset, type ChartEnv, type ChartOptions } from "./uplot-chart";

/**
 * PlanLanes (design system §5.2, US-PLN-02): one row per planned setting from now to the horizon. The device value
 * sits at the left edge and holds until the first waypoint; dashed [8,4] segments run from each waypoint to the next,
 * the plan's end or the horizon, whichever comes first, labelled with the planned value. The row header names whose
 * value applies, exactly as API-07 marks it (`applied_from`, `effective`, `source`): the client decides none of it.
 * The row model is generic (`PlanRow`); `planRows` maps API-07 lanes, as the client parses them, into it.
 */
export type PlanSegmentKind = "device" | "stale" | "planned" | "superseded" | "manual";
export type PlanSegment = {
  kind: PlanSegmentKind;
  start: number;
  end: number;
  value: number;
  /** Drawn at the segment's start when it fits. */
  label: string;
  /** A shorter label tried when `label` does not fit. */
  short: string;
  /** The tooltip and data-table words. */
  detail: string;
  /** What the segment is, in words, for the data table. */
  what: string;
};
export type PlanRow = {
  key: string;
  label: string;
  unit: string | null;
  /** The row header: PLANNED · AI, Forecast rule · system, MANUAL · Iris skips …, YOUR VALUE … */
  head: string;
  /** A manual row: muted, no waypoints. */
  muted: boolean;
  /** The device value at now, "—" when the API has none. */
  device: string;
  /** Why the device value is missing, in words. */
  deviceNote: string | null;
  /** When the device value was read (epoch seconds), null when unknown. */
  deviceAt: number | null;
  /** The device value is STALE (API-02 `freshness.readback.stale`): it reads muted, and the waypoints still draw. */
  stale: boolean;
  segments: PlanSegment[];
};

export const MANUAL_SKIPS = "MANUAL · Iris skips";
export const HOLDS_UNTIL_NEXT_PLAN = "holds until Iris's next plan";
export const STALE = "STALE";

/** Who a waypoint's value comes from: Iris or a named agent read AI; the forecast action engine never does. */
export function waypointWho(source: string): string {
  if (isAiSource(source)) return PLANNED_AI;
  return source === "preemptive" ? FORECAST_RULE : "PLANNED";
}

/** A value without its unit ("1.55", "50"), as the row header prints it and as Settings writes the number; a true minus. */
export function plainValue(value: number, unit: string | null): string {
  return displayNumber(value, null, unit);
}
const at = (instant: string | null | undefined) => epochOf(instant);

/**
 * One API-07 lane as a row from `now` to `horizon`; `planEnd` is the plan's `valid_until`. `stale` marks the device
 * value STALE: it draws muted and labelled, and nothing else on the row changes.
 */
export function planRow(lane: PlanLane, now: number, horizon: number, planEnd: number | null, stale = false): PlanRow {
  const unit = lane.unit ?? null;
  // Values read as Settings reads them (`settingDisplay`): "60 s" on both pages, the key's unit where the lane has none.
  const value = (number: number) => settingDisplay(number, { unit, param: lane.param });
  const device = lane.device.value;
  const desired = lane.desired;
  const row: PlanRow = {
    key: lane.param, label: lane.label, unit, head: "", muted: false,
    device: typeof device === "number" && Number.isFinite(device) ? value(device) : "—", deviceNote: device === null ? (lane.device.reason ?? "no readback") : null,
    deviceAt: at(lane.device.observed_at), stale: stale && device !== null, segments: [],
  };
  // Waypoints stop at the plan's end; the device value runs to it too, or to the horizon with no plan in force.
  const stop = planEnd !== null ? Math.min(planEnd, horizon) : horizon;
  const deviceStop = planEnd !== null && planEnd > now ? stop : horizon;

  if (lane.applied_from === "manual") {
    row.muted = true;
    const when = desired ? at(desired.created_at) : null;
    row.head = desired ? `${MANUAL_SKIPS} · ${desired.actor.name}${when !== null ? ` since ${dayWords(when)}` : ""}` : MANUAL_SKIPS;
    const held = desired?.value ?? device;
    if (held !== null && held !== undefined) {
      row.segments.push({ kind: "manual", start: now, end: horizon, value: held, label: "", short: "", detail: `${row.head} · ${value(held)}`, what: "MANUAL" });
    }
    return row;
  }

  const yours = lane.applied_from === "desired" && desired && desired.value !== null && !desired.manual ? desired : null;
  let heldBy = "a newer or manual value keeps it from applying";
  if (yours && yours.value !== null) {
    const when = at(yours.created_at);
    const clock = when === null ? "" : ` ${clockTime(when, localDay(when) !== localDay(now))}`;
    row.head = `YOUR VALUE ${plainValue(yours.value, unit)} · ${yours.actor.name}${clock} · ${HOLDS_UNTIL_NEXT_PLAN}`;
    heldBy = `your value ${value(yours.value)} (${yours.actor.name}) ${HOLDS_UNTIL_NEXT_PLAN}`;
  }

  const waypoints = lane.waypoints
    .map((waypoint) => ({ waypoint, at: at(waypoint.at) }))
    .filter((entry): entry is { waypoint: PlanWaypoint; at: number } => entry.at !== null)
    .sort((a, b) => a.at - b.at);
  const sources = new Set<string>();
  waypoints.forEach(({ waypoint, at: start }, index) => {
    const end = Math.min(index + 1 < waypoints.length ? waypoints[index + 1].at : Infinity, stop);
    const from = Math.max(start, now);
    if (waypoint.value === null || end <= from) return;
    const who = waypointWho(waypoint.source);
    const text = value(waypoint.value);
    if (waypoint.effective) {
      sources.add(who);
      row.segments.push({ kind: "planned", start: from, end, value: waypoint.value, label: `${text} ${who}`, short: text, detail: `${who} ${text} from ${clockTime(start, localDay(start) !== localDay(now))}`, what: who });
    } else {
      const origin = isAiSource(waypoint.source) ? "Iris plan" : who;
      row.segments.push({ kind: "superseded", start: from, end, value: waypoint.value, label: `${text} ${SUPERSEDED}`, short: SUPERSEDED, detail: `${SUPERSEDED} ${text} · ${origin} · ${heldBy}`, what: SUPERSEDED });
    }
  });
  if (!row.head) row.head = [...sources].sort((a, b) => (a === PLANNED_AI ? -1 : b === PLANNED_AI ? 1 : 0)).join(" · ");

  // The device value holds from now until the first drawn segment, the plan's end or the horizon; a stale one is muted.
  const first = row.segments[0]?.start ?? deviceStop;
  if (device !== null && first > now) {
    row.segments.unshift(row.stale
      ? { kind: "stale", start: now, end: first, value: device, label: STALE, short: STALE, detail: `Device ${value(device)} · ${STALE}`, what: `Device · ${STALE}` }
      : { kind: "device", start: now, end: first, value: device, label: "", short: "", detail: `Device ${value(device)}`, what: "Device" });
  }
  return row;
}

export function planRows(plan: Plan, now: number, horizon: number, stale = false): PlanRow[] {
  const planEnd = at(plan.plan?.valid_until);
  return plan.lanes.map((lane) => planRow(lane, now, horizon, planEnd, stale));
}

/** A row's device value in words for its title: the API's reason when it has none, STALE with when it was read. */
export function deviceTitle(row: PlanRow, now: number): string | undefined {
  if (row.deviceNote) return row.deviceNote;
  if (!row.stale) return undefined;
  return row.deviceAt === null ? STALE : `${STALE} · read ${clockTime(row.deviceAt, localDay(row.deviceAt) !== localDay(now))}`;
}

/** "STALE · device values last read 06:02": the newest read among the stale rows, or none when no row has a time. */
export function staleNote(rows: readonly PlanRow[], now: number): string | null {
  const stale = rows.filter((row) => row.stale);
  if (!stale.length) return null;
  const times = stale.map((row) => row.deviceAt).filter((time): time is number => time !== null);
  if (!times.length) return `${STALE} · device values`;
  const newest = Math.max(...times);
  return `${STALE} · device values last read ${clockTime(newest, localDay(newest) !== localDay(now))}`;
}

/** The plan's end when it falls inside the window: "Plan ends 17:30", with what takes over when the caller says. */
export function planEndLabel(plan: Plan | null, now: number, horizon: number, takeover?: string): { at: number; text: string } | null {
  const end = at(plan?.plan?.valid_until);
  if (end === null || end <= now || end > horizon) return null;
  const text = `Plan ends ${clockTime(end, localDay(end) !== localDay(now))}`;
  return { at: end, text: takeover ? `${text} · ${takeover}` : text };
}

/** Plan rows: an 18 px band at the top for the plan's end, then per row a header line and a 24 px strip. */
export const PLAN_TOP = 18;
export const PLAN_HEAD = { wide: 20, narrow: 36 } as const;
export const PLAN_STRIP = 24;

export type PlanView = { rows: PlanRow[]; head: number; end: { at: number; text: string } | null };
const planLayout = (view: PlanView): RowLayout => ({ pitch: view.head + PLAN_STRIP });

/**
 * Stroke, width and dash of each segment kind (design system §5.1: device solid, planned dashed, superseded muted; a
 * stale device value solid and muted, as design system §4.5 mutes a stale value).
 */
export function segmentStyle(kind: PlanSegmentKind, colors: ChartColors, px: number): { stroke: string; width: number; dash: number[] } {
  switch (kind) {
    case "device": return { stroke: colors["--c-setpoint"], width: 2 * px, dash: [] };
    case "stale": return { stroke: colors["--c-bound"], width: 2 * px, dash: [] };
    case "planned": return { stroke: colors["--ink"], width: 2 * px, dash: [8 * px, 4 * px] };
    case "superseded": return { stroke: colors["--c-bound"], width: 2 * px, dash: [8 * px, 4 * px] };
    case "manual": return { stroke: colors["--c-bound"], width: 2 * px, dash: [] };
  }
}

/**
 * draw: each row's strip (the device line, the dashed waypoints, a manual row's held value in grey), each segment's
 * label at its start where it fits (the value alone when the full label does not), then the plan's end.
 */
export function paintPlan(plot: PaintTarget, view: PlanView, colors: ChartColors, px: number) {
  const layout = planLayout(view);
  clipColumns(plot, () => {
    const { ctx } = plot;
    const labels = new RowLabels(ctx, px);
    view.rows.forEach((row, index) => {
      const band = rowBand(plot, layout, index, px, 0);
      // The strip under the row's header line: the line in its lower part, labels above it.
      const strip = { top: band.top + view.head * px, height: PLAN_STRIP * px, middle: band.top + (view.head + 16) * px };
      for (const segment of row.segments) {
        const pixels = spanPixels(plot, segment.start, segment.end);
        if (!pixels) continue;
        const style = segmentStyle(segment.kind, colors, px);
        ctx.strokeStyle = style.stroke;
        ctx.lineWidth = style.width;
        ctx.setLineDash(style.dash);
        ctx.beginPath();
        ctx.moveTo(pixels.left, strip.middle);
        ctx.lineTo(pixels.right, strip.middle);
        ctx.stroke();
      }
      for (const segment of row.segments) {
        if (!segment.label) continue;
        const pixels = spanPixels(plot, segment.start, segment.end);
        if (!pixels) continue;
        const color = labelColor(segmentStyle(segment.kind, colors, px).stroke, colors["--ink"]);
        const labelRow = { ...strip, middle: strip.top + 7 * px };
        const x = pixels.left + 4 * px, limit = pixels.right - 2 * px;
        if (!labels.place(index, labelRow, segment.label, x, limit, color)) labels.place(index, labelRow, segment.short, x, limit, color);
      }
    });
  });
  if (view.end) paintPlanEnd(plot, view.end, colors, px);
}

/** The plan's end: a dashed vertical marker through the rows, its words in the band above them. */
function paintPlanEnd(plot: PaintTarget, end: { at: number; text: string }, colors: ChartColors, px: number) {
  const { ctx, bbox } = plot;
  const min = plot.scales.x.min ?? end.at, max = plot.scales.x.max ?? end.at;
  if (end.at <= min || end.at > max) return;
  const x = plot.valToPos(end.at, "x", true);
  ctx.save();
  try {
    ctx.strokeStyle = colors["--muted"];
    ctx.lineWidth = px;
    ctx.setLineDash([4 * px, 3 * px]);
    ctx.beginPath();
    ctx.moveTo(x, bbox.top);
    ctx.lineTo(x, bbox.top + bbox.height);
    ctx.stroke();
    ctx.font = laneFont(px);
    ctx.fillStyle = colors["--muted"];
    ctx.textBaseline = "middle";
    const width = ctx.measureText(end.text).width;
    const left = bbox.left + bbox.width - x >= width + 6 * px;
    ctx.textAlign = left ? "left" : "right";
    ctx.fillText(end.text, left ? x + 4 * px : x - 4 * px, bbox.top - (PLAN_TOP / 2) * px);
  } finally {
    ctx.restore();
  }
}

/** The segment covering `x` in a row. */
export function segmentAt(row: PlanRow, x: number): PlanSegment | null {
  return row.segments.find((segment) => x >= segment.start && x < segment.end) ?? null;
}

/** The tooltip for the row under the pointer: the time with its offset, the setting, what applies there, the header. */
export function planTooltip(view: PlanView, plot: Pick<uPlot, "cursor" | "posToVal">): string[] | null {
  if (!plot.cursor.event) return null;
  const x = cursorX(plot as uPlot), top = plot.cursor.top;
  if (x === null || top === undefined || top === null || top < 0) return null;
  const row = view.rows[Math.floor(top / planLayout(view).pitch)];
  if (!row) return null;
  const segment = segmentAt(row, x);
  const lines = [fullTime(x), row.label];
  if (segment) lines.push(segment.detail);
  else if (row.deviceNote) lines.push(`Device — · ${row.deviceNote}`);
  if (row.head && !(segment?.detail.startsWith(row.head))) lines.push(row.head);
  return lines;
}

export function planOptions(env: ChartEnv, viewRef: MutableRefObject<PlanView>): ChartOptions {
  const { colors, px } = env;
  return {
    padding: [PLAN_TOP, null, 0, null],
    series: [{}],
    axes: [{ ...timeAxis({ muted: colors["--muted"], grid: colors["--c-grid"] }), grid: { show: false } }, { show: false }],
    hooks: { draw: [(plot) => paintPlan(plot, viewRef.current, colors, px())] },
    plugins: [tooltipPlugin((plot) => planTooltip(viewRef.current, plot))],
  };
}

/** The data table: per setting its header, then each segment's span, value and source in words. */
export function planTable(rows: readonly PlanRow[]): DataTableBlock {
  const count = rows.reduce((sum, row) => sum + Math.max(1, row.segments.length), 0);
  return {
    caption: "Plan lanes · America/Denver", headers: ["Setting", "From", "Until", "Value", "What", "Note"], count,
    rows: () => rows.flatMap((row) => {
      const note = [row.head, row.deviceNote ? `device: ${row.deviceNote}` : ""].filter(Boolean).join(" · ");
      if (!row.segments.length) return [[row.label, "—", "—", row.device, "Device", note]];
      return row.segments.map((segment, index) => [
        row.label, <TimeCell key="from" at={segment.start} />, <TimeCell key="until" at={segment.end} />,
        settingDisplay(segment.value, { unit: row.unit, param: row.key }), segment.what, index === 0 ? note : "",
      ]);
    }),
  };
}

export type PlanLanesProps = {
  /** API-07; null while it has not loaded. */
  plan: Plan | null;
  /** Epoch seconds: the window runs from now to now plus `hours`. */
  now: number;
  hours?: 24 | 72;
  /** What applies after the plan's end, written by the screen ("band/default"). */
  takeover?: string;
  syncKey?: string;
  title?: string;
  /** Shown in the reserved frame instead of the lanes. */
  message?: string;
  /** Its own "Reset zoom" while zoomed. Default true. */
  zoomControl?: boolean;
  /**
   * The device values are stale (API-02 `freshness.readback.stale`): each reads muted and STALE, a note above the rows
   * says when they were last read, and the waypoints still draw.
   */
  stale?: boolean;
  /** Ends each row with the screen's own control for that row (Plan's Open link), in a column beside the plot. */
  rowEnd?: (row: PlanRow) => ReactNode;
};

export function PlanLanes(props: PlanLanesProps) {
  const { plan, now, hours = 24, syncKey, stale = false, rowEnd } = props;
  const narrow = useNarrow();
  const horizon = now + hours * 3600;
  const rows = useMemo(() => (plan ? planRows(plan, now, horizon, stale) : []), [plan, now, horizon, stale]);
  const view: PlanView = { rows, head: narrow ? PLAN_HEAD.narrow : PLAN_HEAD.wide, end: planEndLabel(plan, now, horizon, props.takeover) };
  const viewRef = useRef(view);
  viewRef.current = view;
  const xRange: XRange = { from: now, to: horizon };
  const zoom = useZoomReset(xRange, syncKey);
  const data = useMemo<uPlot.AlignedData>(() => [[now, horizon]], [now, horizon]);
  // New rows, the header height or the plan's end repaint the canvas without rebuilding it.
  const marks = useMemo(() => ({ rows, head: view.head, end: view.end?.text }), [rows, view.head, view.end?.text]);
  const drawable = !props.message && plan !== null && rows.length > 0;
  const note = drawable ? staleNote(rows, now) : null;
  return <figure className="chart chart-lanes">
    {/* A meta line under the card's title, never an eyebrow below its heading (redesign §3.5). */}
    <figcaption className="meta chart-lanes-caption">{props.title ?? `Next ${hours} h · one lane per planned setting`}</figcaption>
    {note && <ul className="chart-notes"><li className="chart-note-stale">{note}</li></ul>}
    {drawable
      ? <div className={rowEnd ? "lanes lanes-plan lanes-plan-end" : "lanes lanes-plan"}>
        <div className="lanes-top" aria-hidden="true" />
        <ul className="lanes-rows" aria-label="Planned settings">
          {rows.map((row) => <li key={row.key} className={row.muted ? "plan-row plan-row-muted" : "plan-row"} data-param={row.key}>
            <p className="plan-head" title={row.head ? `${row.label} · ${row.head}` : row.label}>
              <span className="plan-label">{row.label}</span>
              {row.head && <><span className="plan-sep" aria-hidden="true">·</span><span className="plan-status">{row.head}</span></>}
            </p>
            <p className="plan-line"><span className={row.stale ? "plan-device plan-device-stale" : "plan-device"} title={deviceTitle(row, now)}>{row.device}</span></p>
            {rowEnd && <div className="plan-end">{rowEnd(row)}</div>}
          </li>)}
        </ul>
        <div className="lanes-axis" aria-hidden="true" />
        <UPlotChart options={(env) => planOptions(env, viewRef)} data={data} xRange={xRange} structure={`plan|${syncKey ?? ""}`} className="lanes-canvas"
          label={`Plan lanes: the next ${hours} hours of each planned setting, America/Denver. The data table follows.`}
          syncKey={syncKey} marks={marks} onZoom={zoom.onZoom} onPlot={zoom.onPlot} />
      </div>
      : <div className="plot plot-small chart-message"><p>{props.message ?? "No planned settings in this window"}</p></div>}
    {drawable && (props.zoomControl ?? true) && zoom.zoomed && <div className="chart-tools"><button type="button" onClick={zoom.reset}>Reset zoom</button></div>}
    {drawable && <DataTable tables={[planTable(rows)]} />}
  </figure>;
}
