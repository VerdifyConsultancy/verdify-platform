// SPDX-License-Identifier: MIT
import { isoLocal } from "./time-axis";
import type { MetricDef, Series, XRange } from "./types";

/**
 * "CSV of this window" (design system §5.3): exactly the plotted columns, the local time with its offset and then each
 * trace's `avg` in canonical units, an empty cell for a gap. Built in the browser; nothing is fetched.
 */
export type CsvColumn = { header: string; values: readonly (number | string | null | undefined)[] };
/** One chart's columns on its own bucket starts. */
export type CsvBlock = { t: readonly number[]; columns: readonly CsvColumn[] };

export const TIME_HEADER = "time (America/Denver)";

/** RFC 4180: a cell with a comma, quote or line break is quoted, quotes doubled. */
export function csvCell(text: string): string {
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}
const valueCell = (value: number | string | null | undefined) => (typeof value === "string" ? value : value === null || value === undefined || !Number.isFinite(value) ? "" : String(value));
const line = (cells: readonly string[]) => cells.map(csvCell).join(",");

/** The CSV text: a byte-order mark (spreadsheets then read the units' UTF-8), a header row and one row per bucket. */
export function csvText(block: CsvBlock): string {
  return windowCsv([block]);
}

/**
 * Several charts' columns in one file, one row per bucket start across them (Graphs' "CSV of this window"); a chart
 * with no bucket at that time leaves its cells empty.
 */
export function windowCsv(blocks: readonly CsvBlock[]): string {
  const times = [...new Set(blocks.flatMap((block) => block.t))].sort((a, b) => a - b);
  const lookups = blocks.map((block) => new Map(block.t.map((at, index) => [at, index])));
  const rows = [line([TIME_HEADER, ...blocks.flatMap((block) => block.columns.map((column) => column.header))])];
  for (const at of times) {
    const cells = [isoLocal(at)];
    blocks.forEach((block, position) => {
      const index = lookups[position].get(at);
      for (const column of block.columns) cells.push(index === undefined ? "" : valueCell(column.values[index]));
    });
    rows.push(line(cells));
  }
  return `\uFEFF${rows.join("\r\n")}\r\n`;
}

/** A column per metric's `avg`, headed with its key and unit; a metric the Series lacks is all gaps. */
export function seriesCsvBlock(series: Series | null | undefined, metrics: readonly MetricDef[], unit: string): CsvBlock {
  if (!series) return { t: [], columns: [] };
  return { t: series.t, columns: metrics.map((metric) => ({ header: `${metric.key} (${unit})`, values: series.t.map((_, index) => series.series[metric.key]?.avg[index] ?? null) })) };
}

/** "verdify-graphs-2026-10-31T0700-2026-11-01T1800.csv", local times. */
export function csvFilename(name: string, range: XRange): string {
  const stamp = (at: number) => isoLocal(at).slice(0, 16).replace(":", "");
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "chart";
  return `verdify-${slug}-${stamp(range.from)}-${stamp(range.to)}.csv`;
}
