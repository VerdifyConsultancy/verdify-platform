// SPDX-License-Identifier: MIT
import { useApiDown } from "../../lib/session";
import { cls } from "../common";

/**
 * The plan's validity bar (redesign §5.11): a track from when the plan was written to when it ends, filled up to now,
 * with now as an ink tick. It sits beside the same times in words ("07:02 → 17:30 · 6 h 48 min left"), so it is
 * `aria-hidden`. Positions are SVG percentages (no style attribute). An unreadable time draws the bare track; while the
 * API is down the fill is muted.
 */
export function ValidityBar({ from, to, now }: { from: string; to: string; now: number }) {
  const apiDown = useApiDown();
  const start = Date.parse(from), end = Date.parse(to);
  const known = Number.isFinite(start) && Number.isFinite(end) && end > start;
  const done = known ? Math.min(100, Math.max(0, ((now - start) / (end - start)) * 100)) : 0;
  const pct = `${Math.round(done * 100) / 100}%`;
  return <svg className={cls("ctl-validity", apiDown && "is-neutral", !known && "is-unknown")} width="100%" height="12" aria-hidden="true" focusable="false">
    <rect className="ctl-validity-track" x="0" y="2" width="100%" height="8" rx="4" />
    {known && done > 0 && <rect className="ctl-validity-fill" x="0" y="2" width={pct} height="8" rx="4" />}
    {known && done > 0 && done < 100 && <line className="ctl-validity-now" x1={pct} x2={pct} y1="0" y2="12" />}
  </svg>;
}
