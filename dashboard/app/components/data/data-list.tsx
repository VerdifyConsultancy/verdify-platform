// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { cls } from "../common";

export type DataItem = { readonly term: ReactNode; readonly value: ReactNode; readonly reason?: ReactNode; readonly key?: string };
const missing = (value: ReactNode) => value === null || value === undefined || (typeof value === "number" && !Number.isFinite(value));

/**
 * DataList (redesign §3.6 rule 3): where a " · " chain would run past three items, the facts as a `<dl>` of short terms
 * and values that wrap as pairs. `inline` sets the pairs in a row ("Flow none · Occupancy nobody seen"), otherwise in a
 * two-column grid. A missing value is "—" with its reason, never 0 or blank.
 */
export function DataList({ items, inline = false, className }: { items: readonly DataItem[]; inline?: boolean; className?: string }) {
  return <dl className={cls("data-list", inline && "data-list-inline", className)}>{items.map((item, index) => <div key={item.key ?? index} className="data-pair">
    <dt>{item.term}</dt>
    <dd>{missing(item.value) ? <>—{item.reason && <span className="meta"> {item.reason}</span>}</> : item.value}</dd>
  </div>)}</dl>;
}
