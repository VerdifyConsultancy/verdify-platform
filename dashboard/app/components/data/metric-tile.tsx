// SPDX-License-Identifier: MIT
import { useId, type ReactNode } from "react";
import { Link } from "react-router";
import { useApiDown } from "../../lib/session";
import { Badge } from "../badge";
import { Button } from "../button";
import { cls } from "../common";

/** What a tile shows (redesign §4.3): its data, its skeleton, one line when empty, muted with STALE, or its own error. */
export type TileState = "ready" | "loading" | "empty" | "stale" | "error";

export type MetricTileProps = {
  /** The mono label: "Temperature in band", "Watering". */
  label: string;
  /** "94.2%", "6 h 12 min", "14.2 gal/day": the value with its unit, at `--fs-xl`. Null is unknown: "—" and `reason`. */
  value: ReactNode | null;
  /** Why the value is unknown ("No probe", "Not served before Nov 1"). */
  reason?: ReactNode;
  /** The comparison under the value: a `Delta`. */
  delta?: ReactNode;
  /** One visual: a `Sparkline`, `BandBar`, `Meter` or `StatusStrip`. */
  visual?: ReactNode;
  /** The meta line: source, age, a note. */
  meta?: ReactNode;
  /** The label links where the tile's detail is ("Water history"); the tile itself is never a link. */
  href?: string;
  /** Amber or fault rule on the left when the value is abnormal, with its words in `meta` or `value`. */
  tone?: "warn" | "fault";
  state?: TileState;
  /** empty: the one line (default "No data in this period"). */
  empty?: ReactNode;
  /** error: Retry, in this tile only. */
  onRetry?: () => void;
  /** Data hooks the screen keeps (`data-tile`). */
  data?: Readonly<Record<`data-${string}`, string>>;
};

/**
 * MetricTile (redesign §4.2): a label (the mono eyebrow that names the tile; an eyebrow is never a heading, §3.5), the
 * value and unit, a delta line, one visual and a meta line, at a fixed height in
 * every state, so a grid of tiles never shifts while it loads. A stale tile mutes its value and says STALE; an empty one
 * says so in one line; an error is "Couldn't load <label>." with Retry, in that tile only. While the API is down the
 * visual goes neutral (each visual reads `useApiDown()` itself) and no verdict shows.
 */
export function MetricTile({ label, value, reason, delta, visual, meta, href, tone, state = "ready", empty, onRetry, data }: MetricTileProps) {
  const id = useId();
  const apiDown = useApiDown();
  const title = href ? <Link to={href}>{label}</Link> : label;
  let body: ReactNode;
  if (state === "loading") body = <><span className="metric-skeleton viz-skeleton" aria-hidden="true" /><span className="screen-reader">Loading {label}</span></>;
  else if (state === "error") body = <div className="metric-error"><p className="fault">Couldn't load {label.toLowerCase()}.</p>{onRetry && <Button onClick={onRetry}>Retry</Button>}</div>;
  else if (state === "empty") body = <p className="metric-empty meta">{empty ?? "No data in this period"}</p>;
  else body = <>
    <p className={cls("metric-value", state === "stale" && "value-muted")}>
      {value ?? <>—{reason && <span className="metric-reason meta"> {reason}</span>}</>}
      {state === "stale" && <> <Badge kind="stale" /></>}
    </p>
    {delta && <p className="metric-delta">{delta}</p>}
    {visual && <div className="metric-visual">{visual}</div>}
  </>;
  return <article className={cls("card", "metric-tile", tone && !apiDown && `tone-${tone}`)} aria-labelledby={id} aria-busy={state === "loading" || undefined} {...data}>
    <p id={id} className="eyebrow metric-label">{title}</p>
    {body}
    {meta && state !== "loading" && <p className="metric-meta meta">{meta}</p>}
  </article>;
}
