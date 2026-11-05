// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { Labels, labelColor, linesCross, type PaintTarget } from "./draw";
import { RecordingContext, TOKENS } from "./fixtures/uplot-mock";

test("a label keeps its mark's colour only where that reads at 4.5:1 on white", () => {
  const ink = TOKENS["--ink"];
  // VPD 6.64:1, the bounds 5.41:1, the device line 8.20:1 and temperature 16.19:1: kept.
  for (const token of ["--c-vpd", "--c-bound", "--c-setpoint", "--c-temp", "--eq-fan2"]) expect(labelColor(TOKENS[token], ink), token).toBe(TOKENS[token]);
  // RH (3.47:1) and the requested amber (4.01:1) are lines at 3:1, but too light for text.
  expect(labelColor(TOKENS["--c-rh"], ink)).toBe(ink);
  expect(labelColor(TOKENS["--c-requested"], TOKENS["--warn-text"])).toBe(TOKENS["--warn-text"]);
  expect(labelColor("#fff", ink)).toBe(ink);
  // A value that is not a hex colour never passes as text.
  expect(labelColor("", ink)).toBe(ink);
  expect(labelColor("rebeccapurple", ink)).toBe(ink);
});

test("a label is drawn over a 3 px halo of the chart's background, at the first place no drawn line crosses", () => {
  const ctx = new RecordingContext();
  // x 0–100 over 400 px, y 0–100 over 200 px; one shown series flat at 60 (y 80 px), a hidden one at 40 (y 120 px).
  const plot = {
    ctx, bbox: { left: 0, top: 0, width: 400, height: 200 }, scales: { x: { min: 0, max: 100 }, y: { min: 0, max: 100 } },
    valToPos: (value: number, scale: string) => (scale === "x" ? value * 4 : 200 - value * 2),
    data: [[0, 100], [60, 60], [40, 40]], series: [{}, { show: true }, { show: false }],
  } as unknown as PaintTarget;
  const labels = new Labels(plot, 1, "rgb(255, 255, 255)", linesCross(plot, { now: null, retention: [], bounds: [], steps: [], runs: [] }));
  // Above a line at y 90 px the box (74–87) would sit on the series: the label goes below it instead.
  expect(labels.place("Band in force · DERIVED", [{ at: 20, line: 90, align: "left" }], TOKENS["--muted"])).toBe(true);
  expect(ctx.texts.map((item) => [item.text, item.y])).toEqual([["Band in force · DERIVED", 93]]);
  expect(ctx.halos).toEqual([{ text: "Band in force · DERIVED", x: 20, y: 93, color: "rgb(255, 255, 255)", width: 3 }]);
  // The hidden series is not a line on the chart: a box over it is free.
  expect(labels.place("Forecast · issued 07:00", [{ at: 200, line: 130, align: "left" }], TOKENS["--muted"])).toBe(true);
  expect(ctx.texts.at(-1)?.y).toBe(114);
  // Where a line crosses every place, the first that overlaps no label still takes it, its halo keeping the words legible.
  const crowded = { ...plot, data: [[0, 100], [60, 60], [47.5, 47.5]], series: [{}, { show: true }, { show: true }] } as unknown as PaintTarget;
  const both = new Labels(crowded, 1, "rgb(255, 255, 255)", linesCross(crowded, { now: null, retention: [], bounds: [], steps: [], runs: [] }));
  expect(both.place("max 2.50 kPa", [{ at: 300, line: 92, align: "right" }], TOKENS["--ink"])).toBe(true);
  expect(ctx.texts.at(-1)?.y).toBe(76);
});
