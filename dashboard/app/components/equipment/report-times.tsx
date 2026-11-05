// SPDX-License-Identifier: MIT
import { localTime, sinceClock } from "../../lib/format";
import { useNow } from "../freshness";

/** How long ago a report came ("14 min ago"): the one `Ago`, in freshness.tsx (redesign §10.2). */
export { Ago } from "../freshness";

/** When the current run or value began: "08:31" today, "Oct 27, 04:26" before, on the server's clock; the full time in `title`. */
export function Since({ at }: { at: string }) {
  const now = useNow();
  return <time dateTime={at} title={Number.isFinite(Date.parse(at)) ? localTime(at) : undefined}>{sinceClock(at, now)}</time>;
}
