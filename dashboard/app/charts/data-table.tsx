// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState, type ReactNode } from "react";
import { fullTime } from "./time-axis";

/**
 * The keyboard and screen-reader equivalent of a chart (design system §7): a `<details>` "Data table · N rows" whose
 * tables render only once opened, in weather's focusable `.table-wrap` scroll region. Times carry their offset.
 */
export type DataTableBlock = {
  caption: string;
  headers: readonly string[];
  /** Built only while the disclosure is open. */
  rows: () => readonly (readonly ReactNode[])[];
  count: number;
};

export function DataTable({ tables }: { tables: readonly DataTableBlock[] }) {
  const [open, setOpen] = useState(false);
  const count = tables.reduce((sum, table) => sum + table.count, 0);
  return <details className="chart-table" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Data table · {count} {count === 1 ? "row" : "rows"}</summary>
    {open && tables.map((table) => <div key={table.caption} className="table-wrap" role="region" tabIndex={0} aria-label={table.caption}>
      <table>
        <caption>{table.caption}</caption>
        <thead><tr>{table.headers.map((header) => <th key={header} scope="col">{header}</th>)}</tr></thead>
        <tbody>{table.rows().map((cells, row) => <tr key={row}>{cells.map((cell, column) => (column === 0 ? <th key={column} scope="row">{cell}</th> : <td key={column}>{cell}</td>))}</tr>)}</tbody>
      </table>
    </div>)}
  </details>;
}

/** A local time with its offset, machine-readable in `dateTime`. */
export function TimeCell({ at }: { at: number }) {
  return <time dateTime={new Date(at * 1000).toISOString()}>{fullTime(at)}</time>;
}

/**
 * "CSV of this window": a Blob URL behind an `<a download>`, rebuilt when `version` changes and revoked when replaced or
 * unmounted. `build` runs in the effect, never during render.
 */
export function CsvLink({ filename, build, version, label = "CSV of this window" }: { filename: string; build: () => string; version: unknown; label?: string }) {
  const latest = useRef(build);
  latest.current = build;
  const [href, setHref] = useState<string | undefined>(undefined);
  useEffect(() => {
    let url: string;
    try {
      url = URL.createObjectURL(new Blob([latest.current()], { type: "text/csv;charset=utf-8" }));
    } catch {
      // No Blob URLs here: the link stays without a target rather than breaking the chart.
      setHref(undefined);
      return;
    }
    setHref(url);
    return () => URL.revokeObjectURL(url);
  }, [version]);
  return <a className="button-link" href={href} download={filename}>{label}</a>;
}
