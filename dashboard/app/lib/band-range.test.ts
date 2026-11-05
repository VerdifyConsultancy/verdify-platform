// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { bandRange } from "./band-range";

const close = (range: [number, number], expected: [number, number]) => {
  expect(range[0]).toBeCloseTo(expected[0], 9);
  expect(range[1]).toBeCloseTo(expected[1], 9);
};

test("a band takes half its width each side, so it fills half the plot and keeps its edges", () => {
  // 62–70 °F with the day's temperatures inside it: 58–74, the band the middle half.
  close(bandRange({ low: 62, high: 70 }, [63, 69]), [58, 74]);
  // A band that moves through the window pads by its widest width.
  close(bandRange({ low: 0.45, high: 0.9, width: 0.35 }, [0.5, 0.8]), [0.275, 1.075]);
});

test("data past the band's room widens the range to it, with 5% to spare on that side only", () => {
  // Outdoor at 35 °F under a 62–70 band: down to 35 and 5% of 35–74 past it; the top keeps the band's half width.
  close(bandRange({ low: 62, high: 70 }, [35, 69]), [35 - (74 - 35) * 0.05, 74]);
  close(bandRange({ low: 0.45, high: 0.8 }, [0.5, 1.8]), [0.275, 1.8 + (1.8 - 0.275) * 0.05]);
});

test("with no band it is the data padded 5% each side; a flat line by 5% of its value; nothing at all is 0–1", () => {
  close(bandRange(null, [10, 20]), [9.5, 20.5]);
  close(bandRange(undefined, [50, 50]), [47.5, 52.5]);
  close(bandRange(null, [0, 0]), [-1, 1]);
  expect(bandRange(null, [null, null])).toEqual([0, 1]);
  expect(bandRange(null, null)).toEqual([0, 1]);
  close(bandRange({ low: 62, high: 70 }, null), [58, 74]);
});
