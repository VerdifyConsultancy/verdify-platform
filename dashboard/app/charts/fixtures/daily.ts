// SPDX-License-Identifier: MIT
/**
 * The 400-day fixture, built rather than stored: one bucket per America/Denver day from 09/28/2025 to 11/01/2026.
 * Indoor temperature reads `daily_summary` throughout with `retained_from` 11/02/2025 (climate keeps 365 days raw);
 * free heap has no daily binding, reads raw daily buckets and is null before its `retained_from` 05/06/2026
 * (diagnostics keep 180 days).
 */
import { dateShift, localDayStart } from "../../lib/format";
import type { Series } from "../types";

export const DAYS = 400;
export const FIRST_DAY = "2025-09-28";
export const CLIMATE_RETAINED_FROM = "2025-11-02T06:00:00Z";
export const HEAP_RETAINED_FROM = "2026-05-06T06:00:00Z";

export function dailySeries(): Series {
  const t = Array.from({ length: DAYS }, (_, day) => Date.parse(localDayStart(dateShift(FIRST_DAY, day))) / 1000);
  const heapFrom = Date.parse(HEAP_RETAINED_FROM) / 1000;
  const temp = t.map((_, day) => Math.round((64 + 6 * Math.sin((day / 365) * 2 * Math.PI)) * 10) / 10);
  const heap = t.map((at, day) => (at < heapFrom ? null : 30 + (day % 7)));
  return {
    from: new Date(t[0] * 1000).toISOString(), to: localDayStart(dateShift(FIRST_DAY, DAYS)), bucket_s: 86_400, t,
    series: {
      "climate.temp_avg": { avg: temp, min: temp.map((value) => value - 4), max: temp.map((value) => value + 5), count: temp.map(() => 1440), source: "daily_summary", retained_from: CLIMATE_RETAINED_FROM },
      "diagnostics.heap_free_kb": { avg: heap, min: heap, max: heap, count: heap.map((value) => (value === null ? 0 : 1440)), source: "raw", retained_from: HEAP_RETAINED_FROM },
    },
    watermark: "2026-11-02T06:59:00Z", server_time: "2026-11-02T07:00:30Z",
  };
}
