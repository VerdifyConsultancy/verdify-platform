// SPDX-License-Identifier: MIT
/**
 * Graphs' reads (SCR-02, #1130): the page clock and the window reads. A window that includes now follows the clock, so
 * its reads move with it every 60 s; a closed window reads once and never refetches. Each read is one TanStack Query
 * entry under `readKey(operationId, params)`, the exact request; a read that moves with the clock keeps its previous
 * answer on screen until the next one arrives (screens §1.5: a refetch keeps the previous render). Nothing loads in SSR.
 */
import { useContext, useEffect, useRef, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { apiGet, type ParamsOf, type Parsed, type ReadOperationId } from "./operator-api";
import { nextPollDelay } from "./polling";
import { readKey } from "./queries";
import { PollingPaused } from "./use-current";

/** Graphs and the scorecard poll every 60 s while their window includes now (architecture §3). */
export const GRAPH_POLL_MS = 60_000;
/**
 * One read gives up after this long: past the dashboard login's 10 s statement timeout, so a slow window gets the API's
 * 503 `query_timeout` ("narrow it") rather than a client timeout.
 */
export const GRAPH_READ_TIMEOUT_MS = 15_000;
const MINUTE_MS = 60_000;
const hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

/**
 * The page's now, in epoch seconds at the start of the minute; null until the page has mounted, so the server and the
 * hydrating render show no time. While `live` it moves on at each minute (every second minute while reads fail, as the
 * polls back off), only while the tab is visible and the session lasts; showing the tab again moves it at once.
 */
export function useGraphClock(live: boolean, failing = false): number | null {
  const [now, setNow] = useState<number | null>(null);
  const paused = useContext(PollingPaused);
  useEffect(() => {
    const read = () => setNow(Math.floor(Date.now() / MINUTE_MS) * 60);
    read();
    if (!live || paused) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      clearTimeout(timer);
      if (hidden()) return;
      // A second past the next minute (or the one after while failing), so the newest minute has landed.
      const delay = (failing ? 2 : 1) * MINUTE_MS - (Date.now() % MINUTE_MS) + 1000;
      timer = setTimeout(() => { read(); schedule(); }, delay);
    };
    const visibility = () => {
      if (!hidden()) read();
      schedule();
    };
    schedule();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [live, failing, paused]);
  return now;
}

/** One slot's read: `id` names what it shows (a panel, a setting) across polls; `params` is the exact request. */
export type ReadSlot<K extends ReadOperationId> = { readonly id: string; readonly params: ParamsOf<K> };
export type WindowRead<T> = {
  /** The answer for the current request, else the slot's previous answer while the current one loads; null before any. */
  readonly data: T | null;
  /** True when `data` answers the current request. */
  readonly current: boolean;
  /** No answer at all yet, and the request is out. */
  readonly loading: boolean;
  /** The current request failed. */
  readonly error: unknown;
  readonly refetch: () => void;
};

/**
 * Reads one operation for each slot. `pollMs` null reads each request once (a window read: the clock changes the
 * request); a number refetches the same request at that cadence, backing off on failures (the scorecard). Every read
 * stops while the session has ended.
 */
export function useWindowReads<K extends ReadOperationId>(operationId: K, slots: readonly ReadSlot<K>[], pollMs: number | null): ReadonlyMap<string, WindowRead<Parsed<K>>> {
  const paused = useContext(PollingPaused);
  const results = useQueries({
    queries: slots.map((slot) => ({
      queryKey: readKey(operationId, slot.params),
      queryFn: () => apiGet(operationId, slot.params, { signal: AbortSignal.timeout(GRAPH_READ_TIMEOUT_MS) }),
      enabled: !paused,
      staleTime: Infinity,
      retry: false,
      refetchOnMount: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      refetchIntervalInBackground: false,
      refetchInterval: pollMs === null ? (false as const) : (query: { state: { fetchFailureCount: number } }) => nextPollDelay(query.state.fetchFailureCount, pollMs),
    })),
  });
  // The previous answer per slot, kept while the slot's next request loads; slots no longer shown are forgotten.
  const kept = useRef(new Map<string, Parsed<K>>());
  const reads = new Map<string, WindowRead<Parsed<K>>>();
  slots.forEach((slot, index) => {
    const result = results[index];
    const own = result.data as Parsed<K> | undefined;
    if (own !== undefined) kept.current.set(slot.id, own);
    const data = own ?? kept.current.get(slot.id) ?? null;
    reads.set(slot.id, {
      data, current: own !== undefined, loading: data === null && result.error === null, error: result.error,
      refetch: () => { void result.refetch(); },
    });
  });
  for (const id of [...kept.current.keys()]) if (!reads.has(id)) kept.current.delete(id);
  return reads;
}

/** The one read of a slot list that has a single slot (API-03, the band, the lanes), or an idle read when there is none. */
export function slotRead<T>(reads: ReadonlyMap<string, WindowRead<T>>, id: string): WindowRead<T> {
  return reads.get(id) ?? { data: null, current: false, loading: false, error: null, refetch: () => {} };
}
