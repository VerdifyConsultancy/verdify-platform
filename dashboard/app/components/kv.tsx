// SPDX-License-Identifier: MIT
import { Fragment, type ReactNode } from "react";
import { cls } from "./common";

export type KvItem = { term: ReactNode; value: ReactNode; reason?: ReactNode; key?: string };
const missing = (value: ReactNode) => value === null || value === undefined || (typeof value === "number" && !Number.isFinite(value));

/** Facts as weather's `.kv` list. A missing value is "—" with its reason, never 0 or blank. */
export function Kv({ items, className }: { items: readonly KvItem[]; className?: string }) {
  return <dl className={cls("kv", className)}>{items.map((item, index) => <Fragment key={item.key ?? index}>
    <dt>{item.term}</dt>
    <dd>{missing(item.value) ? <>—{item.reason && <span className="muted"> {item.reason}</span>}</> : item.value}</dd>
  </Fragment>)}</dl>;
}
