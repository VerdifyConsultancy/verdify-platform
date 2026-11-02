// SPDX-License-Identifier: MIT
import { formatValue } from "../lib/format";
import { LABEL_FONT, type PaintTarget } from "./draw";
import { fullTime, localDay } from "./time-axis";

/**
 * Canvas painting shared by the lanes (EquipmentLanes, PlanLanes): the row layout, the hatch for unknown spans and
 * label fitting. Rows are HTML (their labels and the right-hand stats are text); the canvas sits over the plot column
 * and paints each row's band at a fixed pitch from the plot's top, so canvas rows and HTML rows line up exactly. Every
 * colour is a token value read by UPlotChart, and painting restores the context it changed, because uPlot caches its
 * styles between draws.
 */

/** A lane layout in CSS pixels: rows start at the plot's top (its padding sits above them), one every `pitch`. */
export type RowLayout = { pitch: number };
/** One row's band in canvas pixels. */
export type Band = { top: number; height: number; middle: number };

/** Equipment rows: 20 px above 860 px, 24 px at 860 px and below (design system §6). */
export const LANE_PITCH = { wide: 20, narrow: 24 } as const;

/** Row `index`'s band: the row from the plot's top, inset by `inset` CSS px above and below. */
export function rowBand(plot: Pick<PaintTarget, "bbox">, layout: RowLayout, index: number, px: number, inset = 3): Band {
  const top = plot.bbox.top + (index * layout.pitch + inset) * px;
  const height = Math.max(px, (layout.pitch - 2 * inset) * px);
  return { top, height, middle: top + height / 2 };
}

/** Paints inside the plot's width (rows may sit below its last tick), restoring the context afterwards. */
export function clipColumns(plot: PaintTarget, paint: () => void) {
  const { ctx, bbox } = plot;
  ctx.save();
  try {
    ctx.beginPath();
    ctx.rect(bbox.left, 0, bbox.width, bbox.top + bbox.height);
    ctx.clip();
    paint();
  } finally {
    ctx.restore();
  }
}

/** The x extent of [start, end] on the plot, or null when it is outside the shown range. */
export function spanPixels(plot: PaintTarget, start: number, end: number): { left: number; right: number } | null {
  const min = plot.scales.x.min ?? start, max = plot.scales.x.max ?? end;
  const from = Math.max(start, min), to = Math.min(end, max);
  if (to <= from) return null;
  const left = plot.valToPos(from, "x", true), right = plot.valToPos(to, "x", true);
  return right > left ? { left, right } : null;
}

/**
 * The hatch for a span the API calls unknown: 1px diagonals every 6 px in `color`, clipped to the box. It is drawn,
 * never inferred, and nothing else marks the span, so unknown never reads as off.
 */
export function paintHatch(ctx: CanvasRenderingContext2D, left: number, top: number, width: number, height: number, color: string, px: number) {
  if (width <= 0 || height <= 0) return;
  ctx.save();
  try {
    ctx.beginPath();
    ctx.rect(left, top, width, height);
    ctx.clip();
    ctx.strokeStyle = color;
    ctx.lineWidth = px;
    ctx.setLineDash([]);
    const step = 6 * px;
    ctx.beginPath();
    for (let x = left - height; x < left + width; x += step) {
      ctx.moveTo(x, top + height);
      ctx.lineTo(x + height, top);
    }
    ctx.stroke();
  } finally {
    ctx.restore();
  }
}

const NBSP = "\u00a0";

export const laneFont = (px: number) => `${Math.round(11 * px)}px ${LABEL_FONT}`;

/** `text` cut with an ellipsis to fit `maxWidth` canvas px; null when not even one character and the ellipsis fit. */
export function fitText(ctx: Pick<CanvasRenderingContext2D, "measureText">, text: string, maxWidth: number): string | null {
  if (maxWidth <= 0) return null;
  if (ctx.measureText(text).width <= maxWidth) return text;
  let low = 1, high = text.length - 1, best: string | null = null;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const candidate = `${text.slice(0, middle).trimEnd()}…`;
    if (ctx.measureText(candidate).width <= maxWidth) { best = candidate; low = middle + 1; } else high = middle - 1;
  }
  return best;
}

/**
 * Labels inside rows. A label is placed whole at `x` on its row's middle when it ends before `limit` and overlaps no
 * label already on that row; otherwise it is dropped, and its words stay in the tooltip and the data table.
 */
export class RowLabels {
  private placed = new Map<number, { left: number; right: number }[]>();
  constructor(private ctx: CanvasRenderingContext2D, private px: number) {}
  place(row: number, band: Band, text: string, x: number, limit: number, color: string): boolean {
    const { ctx } = this;
    ctx.font = laneFont(this.px);
    const width = ctx.measureText(text).width;
    if (x + width > limit) return false;
    const taken = this.placed.get(row) ?? [];
    if (taken.some((box) => x < box.right && box.left < x + width)) return false;
    taken.push({ left: x, right: x + width });
    this.placed.set(row, taken);
    ctx.fillStyle = color;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(text, x, band.middle);
    return true;
  }
  /** Marks a stretch of a row as taken (a painted span), so no label is placed over it. */
  reserve(row: number, left: number, right: number) {
    const taken = this.placed.get(row) ?? [];
    taken.push({ left, right });
    this.placed.set(row, taken);
  }
}

/** "11/01/2026 06:58 GMT-07:00–07:12 GMT-07:00", both dates when the span ends on another local day. */
export function intervalText(start: number, end: number): string {
  const to = fullTime(end);
  return `${fullTime(start)}–${localDay(start) === localDay(end) ? to.slice(11) : to}`;
}

/** A span's length: "45 s" under a minute, else whole minutes ("14 min", "2 h 10 min"); none at all is "0 min". */
export function spanDuration(seconds: number): string {
  if (seconds > 0 && seconds < 59.5) return `${Math.max(1, Math.round(seconds))}${NBSP}s`;
  return formatValue(Math.max(0, seconds) / 60, "min");
}
