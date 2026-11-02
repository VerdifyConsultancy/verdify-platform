// SPDX-License-Identifier: MIT
import { createContext, useContext, type ReactNode } from "react";
import { formatValue } from "../lib/format";
import { useApiDown } from "../lib/session";
import { Badge } from "./badge";
import { cls } from "./common";
import { AgeLabel, FreshnessDot } from "./freshness";
import { usePanelInfo } from "./panel";

const VariantContext = createContext<"ink" | "light">("light");

/** The tiles' list: weather's `.stats` on the ink hero, `.summary-stats` on white. */
export function StatList({ variant = "light", className, children }: { variant?: "ink" | "light"; className?: string; children?: ReactNode }) {
  return <VariantContext.Provider value={variant}><dl className={cls(variant === "ink" ? "stats" : "summary-stats", className)}>{children}</dl></VariantContext.Provider>;
}

export type StatTileProps = {
  /** The eyebrow label: "VPD". */
  label: ReactNode;
  /** A reading, formatted with `unit` by `formatValue` (°F 1 dp, kPa 2 dp, …). */
  value?: number | null;
  unit?: string | null;
  /** A value in words instead of a number ("In band, holding"); it wins over `value`. */
  text?: ReactNode;
  /** Why the value is missing (`Reading.reason`). */
  reason?: string | null;
  sub?: ReactNode;
  /** A judgement on the value ("in band"): never shown on stale or missing data. */
  verdict?: ReactNode;
  /** This tile's source time, when it is not the panel's. */
  observedAt?: string | null;
  /** The API marks this value stale; omitted, the tile follows its panel. */
  stale?: boolean;
  /** A badge the panel does not already carry. */
  badge?: ReactNode;
  /** A stable name for the tile (`data-tile`). */
  name?: string;
  id?: string;
  className?: string;
};

const sameInstant = (a: string | null | undefined, b: string | null | undefined) => {
  if (!a || !b) return !a && !b;
  const [x, y] = [Date.parse(a), Date.parse(b)];
  return Number.isFinite(x) && Number.isFinite(y) ? x === y : a === b;
};

/**
 * One tile: eyebrow label, value and unit, sub-line. A missing value is "—" with its reason: never 0, "off" or the
 * value it showed before (nothing is remembered between renders). Freshness and STALE show only when they differ from
 * the panel's, and a verdict only on a value that is there and fresh.
 */
export function StatTile({ label, value, unit, text, reason, sub, verdict, observedAt, stale, badge, name, id, className }: StatTileProps) {
  const variant = useContext(VariantContext);
  const panel = usePanelInfo();
  const apiDown = useApiDown();
  const ownStale = stale ?? panel.stale;
  const hasText = text !== undefined && text !== null && text !== "";
  const missing = !hasText && (value === null || value === undefined || !Number.isFinite(value));
  const showAge = observedAt !== undefined && (ownStale !== panel.stale || !sameInstant(observedAt, panel.observedAt));
  return <div className={cls(variant === "ink" && "stat", className)} id={id} data-tile={name}>
    <dt>{label}</dt>
    <dd>
      <span className={ownStale ? "value-muted" : undefined}>{hasText ? text : formatValue(missing ? null : value, unit)}</span>
      {missing && <small>{reason || "No reading"}</small>}
      {verdict && !missing && !ownStale && panel.verdict && !apiDown && <small>{verdict}</small>}
      {sub && <small>{sub}</small>}
      {showAge && <small className="fresh"><FreshnessDot stale={ownStale} /><AgeLabel observedAt={observedAt} stale={ownStale} /></small>}
      {ownStale && !panel.stale && <Badge kind="stale" />}
      {badge}
    </dd>
  </div>;
}
