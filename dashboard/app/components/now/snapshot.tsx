// SPDX-License-Identifier: MIT
/**
 * The greenhouse snapshot (#1346): one paragraph in the hero, under the headline and the condition line, written by the
 * local Cortex model (Qwen) from the current conditions every 15 minutes or on a change (API-02 `snapshot`). Its author,
 * time and the AI text mark (D-UX-07) follow the text.
 *
 * Fallback: stale (older than two cadences, or a later attempt failed) reads muted with "as of HH:MM"; outdated (a band
 * verdict or the alerts changed since it was written) and anything older than 2 h never show, so the deterministic
 * status line stands alone. The page's own muting (stale readings, the API down) mutes it too.
 */
import type { Now } from "../../lib/operator-api";
import { LocalTime, cls } from "../common";

export const SNAPSHOT_AUTHOR = "Qwen (Cortex)";
/** The server stops serving a snapshot after 2 h; a Now held through an outage is checked against the clock too. */
export const SNAPSHOT_HIDE_AFTER_MS = 2 * 60 * 60 * 1000;

type Snapshot = NonNullable<Now["snapshot"]>;

/** How the snapshot shows at `clockMs`: fresh, stale (muted, "as of"), or not at all. */
export function snapshotState(snapshot: Snapshot | null | undefined, clockMs: number): "fresh" | "stale" | null {
  if (!snapshot || snapshot.outdated || !snapshot.text.trim()) return null;
  const asOf = Date.parse(snapshot.as_of);
  if (!Number.isFinite(asOf) || clockMs - asOf > SNAPSHOT_HIDE_AFTER_MS) return null;
  return snapshot.stale ? "stale" : "fresh";
}

export function NowSnapshot({ snapshot, clock, muted }: { snapshot: Snapshot | null | undefined; clock: number; muted: boolean }) {
  const state = snapshotState(snapshot, clock);
  if (!state || !snapshot) return null;
  const quiet = muted || state === "stale";
  return <p className={cls("now-snapshot", quiet && "value-muted")} data-snapshot={quiet ? "stale" : "fresh"}>
    {snapshot.text}{" "}
    <span className="now-snapshot-mark">
      {quiet
        ? <>Written by {SNAPSHOT_AUTHOR} · as of <LocalTime at={snapshot.as_of} /></>
        : <>Written by {SNAPSHOT_AUTHOR} at <LocalTime at={snapshot.generated_at} /></>}
      {" · AI"}
    </span>
  </p>;
}
