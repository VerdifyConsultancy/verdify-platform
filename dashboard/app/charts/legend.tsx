// SPDX-License-Identifier: MIT
import { useNarrow } from "./uplot-chart";

/**
 * A chart's legend (design system §5.3): one `<button aria-pressed>` per series that shows or hides it. Below the plot
 * above 860 px, inside a `<details>` at 860 px and below, and none on a single-series chart. The swatch takes its colour
 * from the token named in `data-token` (charts.css), never from a style attribute.
 */
export type LegendItem = { key: string; label: string; token: string; shown: boolean };

export function Legend({ items, onToggle }: { items: readonly LegendItem[]; onToggle: (key: string) => void }) {
  const narrow = useNarrow();
  if (items.length < 2) return null;
  const buttons = <ul className="chart-legend-items">
    {items.map((item) => <li key={item.key}>
      <button type="button" aria-pressed={item.shown} onClick={() => onToggle(item.key)}>
        <span className="chart-swatch" data-token={item.token.replace(/^--/, "")} aria-hidden="true" />{item.label}
      </button>
    </li>)}
  </ul>;
  return narrow ? <details className="chart-legend"><summary>Legend</summary>{buttons}</details> : <div className="chart-legend">{buttons}</div>;
}

/**
 * A key for marks that are not series (the lanes' causes, hatching and outlines; the bars' causes, no-data days, the
 * planned outline and the cap): the same swatch-and-words items as the legend, without toggles, so colour, hatch and
 * line style always carry words.
 */
export type KeyItem = { key: string; label: string; token: string; kind: "fill" | "line" | "outline" | "hatch" | "dashed" | "dotted" | "band"; title?: string };

export function ChartKey({ items, className, label = "Key" }: { items: readonly KeyItem[]; className?: string; label?: string }) {
  if (!items.length) return null;
  return <ul className={className ? `chart-key ${className}` : "chart-key"} aria-label={label}>
    {items.map((item) => <li key={item.key} title={item.title}>
      <span className="chart-key-swatch" data-token={item.token.replace(/^--/, "")} data-kind={item.kind} aria-hidden="true" />{item.label}
    </li>)}
  </ul>;
}
