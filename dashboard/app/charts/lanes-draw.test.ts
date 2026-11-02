// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { RecordingContext } from "./fixtures/uplot-mock";
import { RowLabels, intervalText, paintHatch, rowBand, spanDuration } from "./lanes-draw";

const NBSP = " ";
const epoch = (instant: string) => Date.parse(instant) / 1000;
const context = () => new RecordingContext() as unknown as CanvasRenderingContext2D & RecordingContext;

test("rows sit at a fixed pitch from the plot's top, inset above and below, in canvas pixels", () => {
  const plot = { bbox: { left: 0, top: 18, width: 1000, height: 300 } };
  expect(rowBand(plot, { pitch: 20 }, 0, 1)).toEqual({ top: 21, height: 14, middle: 28 });
  expect(rowBand(plot, { pitch: 20 }, 3, 1)).toEqual({ top: 81, height: 14, middle: 88 });
  // At a device pixel ratio of 2 every length doubles from the plot's top.
  expect(rowBand({ bbox: { ...plot.bbox, top: 36 } }, { pitch: 24 }, 1, 2)).toEqual({ top: 90, height: 36, middle: 108 });
});

test("the hatch is diagonals in its colour, clipped to the span, and restores the context", () => {
  const ctx = context();
  paintHatch(ctx, 100, 20, 60, 14, "#626b76", 1);
  expect(ctx.depth).toBe(0);
  expect(ctx.strokes).toHaveLength(1);
  const [stroke] = ctx.strokes;
  expect(stroke.stroke).toBe("#626b76");
  expect(stroke.width).toBe(1);
  // Each diagonal runs from the bottom edge up to the top edge, every 6 px across the span.
  const ys = new Set(stroke.path.map(([, y]) => y));
  expect([...ys].sort((a, b) => a - b)).toEqual([20, 34]);
  expect(stroke.path.length / 2).toBe(Math.ceil((60 + 14) / 6));
  paintHatch(ctx, 100, 20, 0, 14, "#626b76", 1);
  expect(ctx.strokes).toHaveLength(1);
});

test("row labels never overlap a span or another label on their row, and never pass their limit", () => {
  const ctx = context();
  const labels = new RowLabels(ctx, 1);
  const band = { top: 0, height: 14, middle: 7 };
  labels.reserve(0, 100, 160);
  expect(labels.place(0, band, "VPD", 90, 400, "#9d2398")).toBe(false);
  expect(labels.place(0, band, "VPD", 164, 400, "#9d2398")).toBe(true);
  expect(labels.place(0, band, "schedule", 170, 400, "#112231")).toBe(false);
  expect(labels.place(0, band, "schedule", 200, 230, "#112231")).toBe(false);
  // Another row has its own room.
  expect(labels.place(1, band, "schedule", 170, 400, "#112231")).toBe(true);
  expect(ctx.texts.map((text) => [text.text, text.x, text.color])).toEqual([["VPD", 164, "#9d2398"], ["schedule", 170, "#112231"]]);
});

test("a span reads its start and end with offsets, both dates across midnight, and its length in words", () => {
  expect(intervalText(epoch("2026-11-01T07:31:00Z"), epoch("2026-11-01T07:43:00Z"))).toBe("11/01/2026 01:31 GMT-06:00–01:43 GMT-06:00");
  // Across the fall-back hour: the same wall-clock hour, two offsets.
  expect(intervalText(epoch("2026-11-01T07:50:00Z"), epoch("2026-11-01T08:10:00Z"))).toBe("11/01/2026 01:50 GMT-06:00–01:10 GMT-07:00");
  expect(intervalText(epoch("2026-11-02T06:50:00Z"), epoch("2026-11-02T07:10:00Z"))).toBe("11/01/2026 23:50 GMT-07:00–11/02/2026 00:10 GMT-07:00");
  expect(spanDuration(45)).toBe(`45${NBSP}s`);
  expect(spanDuration(0)).toBe(`0${NBSP}min`);
  expect(spanDuration(12 * 60)).toBe(`12${NBSP}min`);
  expect(spanDuration(130 * 60)).toBe(`2${NBSP}h${NBSP}10${NBSP}min`);
});
