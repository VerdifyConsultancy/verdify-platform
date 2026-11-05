// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { EmptyState, Panel, type HeadLink, type PanelProps } from "../panel";

export type ListCardProps = Omit<PanelProps, "title" | "empty" | "children"> & {
  title: ReactNode;
  /** The count after the title ("Needs someone · 2"): the one place on the page that count shows. Null while unknown. */
  count?: number | null;
  /** "All alerts ›" at the head's right. */
  link?: HeadLink;
  /** The rows: `ObjectRow`s in a list, or a `DataList`. */
  children?: ReactNode;
  /** One line of what would appear, plus one widening action ("No runs in 7 days · Show 30 days"). */
  empty?: { message: ReactNode; widen?: { to: string; label: ReactNode } };
};

/**
 * ListCard (redesign §4.2): a section-like head with its title, its count and one "All … ›" link, then rows, or one
 * empty line with a widening action. It is a `Panel`, so it loads, goes stale, fails and retries the way every data card
 * does, at its reserved height (Needs someone, Site today, Recent activity).
 */
export function ListCard({ title, count, link, children, empty, ...panel }: ListCardProps) {
  const heading = count === null || count === undefined ? title : <>{title} <span className="list-count">· {count}</span></>;
  return <Panel {...panel} title={heading} link={link} empty={empty ? <EmptyState message={empty.message} widen={empty.widen} /> : undefined}>
    <div className="list-card-rows">{children}</div>
  </Panel>;
}
