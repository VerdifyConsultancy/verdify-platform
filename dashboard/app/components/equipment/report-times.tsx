// SPDX-License-Identifier: MIT
import { TIME_UNAVAILABLE, duration, localTime, sinceClock } from "../../lib/format";
import { useNow } from "../freshness";

/** When the current run or value began: "08:31" today, "10/27 04:26" before, on the server's clock; the full time in `title`. */
export function Since({ at }: { at: string }) {
  const now = useNow();
  return <time dateTime={at} title={Number.isFinite(Date.parse(at)) ? localTime(at) : undefined}>{sinceClock(at, now)}</time>;
}

/** How long ago a report came ("14 min ago"), ticking silently on the server's clock; the full time in `title`. */
export function Ago({ at }: { at: string }) {
  const now = useNow();
  const ms = Date.parse(at);
  if (!Number.isFinite(ms)) return <>{TIME_UNAVAILABLE}</>;
  return <time dateTime={at} title={localTime(at)}>{duration(Math.max(0, Math.floor((now - ms) / 1000)))} ago</time>;
}
