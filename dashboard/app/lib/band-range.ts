// SPDX-License-Identifier: MIT
/**
 * The y range of a plot or sparkline that draws a band (redesign §4.6, the `BandBar` rule): the band plus half its width
 * each side, widened to take the data with a 5% margin past it. The band then keeps visible edges, being in band reads
 * against the room around it, and a value just outside never hugs the plot's edge. Charts and `components/data` share it
 * here, because `components/data` never imports chart code (§9.7).
 */
export type BandExtent = {
  readonly low: number;
  readonly high: number;
  /** The band's width where it is widest, for a band that moves through the window; `high - low` when omitted. */
  readonly width?: number;
};

const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
/** The data's share added past it, so a line at the data's extreme never sits on the plot's edge. */
const MARGIN = 0.05;

/**
 * [low, high] for a band and the data's lowest and highest values (either may be missing). With no band it is the data
 * padded 5% each side (a flat line by 5% of its value, or 1); with neither, [0, 1].
 */
export function bandRange(band: BandExtent | null | undefined, data: readonly [number | null | undefined, number | null | undefined] | null | undefined): [number, number] {
  const lowData = data && finite(data[0]) ? data[0] : null, highData = data && finite(data[1]) ? data[1] : null;
  if (band && finite(band.low) && finite(band.high) && band.high >= band.low) {
    const half = ((finite(band.width) && band.width > 0 ? band.width : band.high - band.low) / 2) || Math.abs(band.high) * MARGIN || 1;
    let low = band.low - half, high = band.high + half;
    const beyondLow = lowData !== null && lowData < low, beyondHigh = highData !== null && highData > high;
    if (beyondLow) low = lowData;
    if (beyondHigh) high = highData;
    const pad = (high - low) * MARGIN;
    return [beyondLow ? low - pad : low, beyondHigh ? high + pad : high];
  }
  if (lowData === null || highData === null) {
    const only = lowData ?? highData;
    if (only === null) return [0, 1];
    const pad = Math.abs(only) * MARGIN || 1;
    return [only - pad, only + pad];
  }
  const pad = (highData - lowData) * MARGIN || Math.abs(highData) * MARGIN || 1;
  return [lowData - pad, highData + pad];
}
