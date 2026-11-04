// SPDX-License-Identifier: MIT
/**
 * Now's own reads (redesign §5.1), on the shared keys and cadences of lib/queries.ts: each polls every 60 s, pauses while
 * the tab is hidden, and asks for the window that ends at its own moment on the server's clock.
 * - The day read: API-04 over the last 25 h in 15 min buckets, for today's low and high and the zones' sparklines.
 * - The daily read: API-04's time-in-band keys over yesterday and today, keyed by the local day so it moves at midnight.
 * - `useNearViewport`: the "Running and next" track reads its six hours of API-05 only once it is near the screen, so a
 *   phone, which shows the two lists in its place, never reads them at all.
 */
import { useEffect, useState, type RefObject } from "react";
import { dateShift, localDayStart } from "../../lib/format";
import { apiGet, type Series } from "../../lib/operator-api";
import { READ_INTERVAL_MS, useApiRead } from "../../lib/queries";
import { usePolledQuery } from "../../lib/use-current";
import { useServerSkew } from "../freshness";
import { DAY_KEYS, DAY_POINTS, DAY_WINDOW_S, TIB_KEYS } from "./climate";

const iso = (epochMs: number) => new Date(epochMs).toISOString();

/** API-04 over the last 25 h, 15 min buckets: the hero's today range and each zone's 24 h VPD. */
export function useDayRead() {
  const skewMs = useServerSkew();
  return usePolledQuery<Series>(["api", "API-04", { last_s: DAY_WINDOW_S, metric: DAY_KEYS, points: DAY_POINTS }], (signal) => {
    const to = Date.now() - skewMs;
    return apiGet("API-04", { metric: [...DAY_KEYS], from: iso(to - DAY_WINDOW_S * 1000), to: iso(to), points: DAY_POINTS }, { signal });
  }, READ_INTERVAL_MS.zones);
}

/** API-04's time-in-band keys over yesterday and today ([local midnight yesterday, local midnight tomorrow)). */
export function useTimeInBandRead(today: string) {
  const params = { metric: [...TIB_KEYS], from: localDayStart(dateShift(today, -1)), to: localDayStart(dateShift(today, 1)), points: 10 };
  return useApiRead("API-04", params, { intervalMs: READ_INTERVAL_MS.zones });
}

/**
 * True once the element has come within `margin` of the viewport, and from then on. Where there is no
 * IntersectionObserver (a server render, an old browser without it) it stays false, so what it guards never loads there;
 * an element the stylesheet hides (`display: none`) never intersects either.
 */
export function useNearViewport(ref: RefObject<Element | null>, margin = "400px"): boolean {
  const [near, setNear] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (near || !element || typeof IntersectionObserver !== "function") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setNear(true);
    }, { rootMargin: margin });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, margin, near]);
  return near;
}
