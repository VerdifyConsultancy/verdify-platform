// SPDX-License-Identifier: MIT
import { useId, type ReactNode } from "react";
import { Link } from "react-router";
import { useApiDown } from "../../lib/session";
import { cls } from "../common";

/**
 * The left rule of a row or card (redesign §4.4): amber for a warning or out of band, fault for a fault or critical,
 * the running equipment or cause token while it runs. The background stays white; the words carry the state.
 */
export type RowTone = "warn" | "fault" | "running";

export type ObjectRowProps = {
  /** article (a line, an alert, a rule) or li (inside a list); div for anything else. */
  as?: "article" | "li" | "div";
  /** The row's name, its h3: "L01 Wall drip". A row with detail links its title (never the whole row). */
  title: ReactNode;
  titleHref?: string;
  headingLevel?: 3 | 4;
  /** The kind tag beside the title: CLEAN or FERT, a severity word. */
  tag?: ReactNode;
  /** The one state pill (redesign §4.4's priority: NOT CONFIRMED, REQUESTED, HELD, STALE, FAULT, MANUAL, CONFIRMED). */
  pill?: ReactNode;
  /** The state sentence: "Idle · last 07:31, 12 min · ok". */
  state?: ReactNode;
  /** One visual: a today strip (`TimeTrack`), a `Meter`, a `Sparkline`. */
  visual?: ReactNode;
  /** The meta line: up to three facts. */
  meta?: ReactNode;
  /** The one primary action, chosen by state: Stop while running, Run… while idle, Clear fault, Resume, Edit. */
  primary?: ReactNode;
  /** The emergency action (a line's Stop), shown whenever it can matter (D-UX-06); never in the menu. */
  stop?: ReactNode;
  /** The ⋯ `Menu` with everything else. */
  menu?: ReactNode;
  /** Anything below the row that belongs to it: an alert's detail expanded in place, a sub-row. */
  children?: ReactNode;
  tone?: RowTone;
  /** With `tone="running"`: the equipment or cause token of its rule ("eq-mister"). */
  token?: string;
  id?: string;
  className?: string;
  /** Data hooks the screen keeps (`data-line`, `data-state`, `data-equipment`). */
  data?: Readonly<Record<`data-${string}`, string>>;
};

/**
 * ObjectRow (redesign §4.2, §4.5): a thing on screen as one row: its title and kind, one state sentence and one pill,
 * an optional visual, the meta line, then its actions at the end: one primary, Stop when it matters, and ⋯. A row is
 * never itself a link (no nested interactives): its detail opens from the title. Viewers get the same rows; screens pass
 * no write action to a viewer (`OperatorOnly`, `Button write`), and the menu keeps its links. While the API is down the
 * tone is dropped, so no verdict colour shows.
 */
export function ObjectRow({ as = "article", title, titleHref, headingLevel = 3, tag, pill, state, visual, meta, primary, stop, menu, children, tone, token, id, className, data }: ObjectRowProps) {
  const headingId = useId();
  const apiDown = useApiDown();
  const Tag = as;
  const Heading = headingLevel === 3 ? "h3" : "h4";
  const actions = primary || stop || menu;
  return <Tag id={id} className={cls("object-row", tone && !apiDown && `tone-${tone}`, token && tone === "running" && !apiDown && `tone-${token}`, className)}
    aria-labelledby={as === "article" ? headingId : undefined} {...data}>
    <div className="object-row-main">
      <div className="object-row-head">
        <Heading id={headingId} className="object-row-title">{titleHref ? <Link to={titleHref}>{title}</Link> : title}</Heading>
        {tag && <span className="object-row-tag">{tag}</span>}
        {pill && <span className="object-row-pill">{pill}</span>}
      </div>
      {state && <p className="object-row-state">{state}</p>}
      {visual && <div className="object-row-visual">{visual}</div>}
      {meta && <p className="object-row-meta meta">{meta}</p>}
    </div>
    {actions && <div className="object-row-actions">{primary}{stop}{menu}</div>}
    {children}
  </Tag>;
}
