// SPDX-License-Identifier: MIT
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient, type QueryKey, type UseQueryResult } from "@tanstack/react-query";
import { nextPollDelay } from "./polling";

/** A single poll gives up after this long, so a stalled request never holds the cadence. */
export const POLL_TIMEOUT_MS = 6000;
export type PollFetcher<T> = (signal: AbortSignal) => Promise<T>;
const foreground = () => typeof document === "undefined" || document.visibilityState !== "hidden";

/**
 * True stops every poll on the page. The shell (#1124) sets it once the sign-in session has ended (a 401), so nothing
 * keeps asking with a session the BFF no longer has; signing in again reloads the page.
 */
export const PollingPaused = createContext(false);

/** The query, plus how many polls in a row have failed since the last answer (0 after a success). */
export type PolledQueryResult<T> = UseQueryResult<T> & { readonly consecutiveFailures: number };

/**
 * Polls `fetcher` every `intervalMs` while the tab is visible. Nothing renders in SSR, so it fetches on mount; a hidden
 * tab aborts the request in flight and stops polling, and showing the tab again refreshes once. Consecutive failures
 * back off (`nextPollDelay`) and keep the last data; `consecutiveFailures` counts them (a hidden tab's abort is not
 * one). TanStack Query owns deduplication: StrictMode, focus and reconnect add no requests. `PollingPaused` stops it.
 */
export function usePolledQuery<T>(queryKey: QueryKey, fetcher: PollFetcher<T>, intervalMs: number): PolledQueryResult<T> {
  const latestKey = useRef(queryKey);
  latestKey.current = queryKey;
  const latestFetcher = useRef(fetcher);
  latestFetcher.current = fetcher;
  // The hook owns each request's abort. Reading TanStack's own signal would make it cancel the request whenever the
  // last observer unsubscribes, so a StrictMode remount would fetch twice on mount.
  const inFlight = useRef<AbortController | null>(null);
  const [visible, setVisible] = useState(foreground);
  const wasVisible = useRef(visible);
  const [resumes, setResumes] = useState(0);
  const failures = useRef(0);
  // The render-side copy of `failures`, so a component reads the count from state rather than a ref.
  const [consecutiveFailures, setConsecutiveFailures] = useState(0);
  const paused = useContext(PollingPaused);
  const client = useQueryClient();
  useEffect(() => {
    const changed = () => {
      const shown = foreground();
      if (shown && !wasVisible.current) setResumes((count) => count + 1);
      wasVisible.current = shown;
      setVisible(shown);
      if (!shown) {
        void client.cancelQueries({ queryKey: latestKey.current, exact: true });
        inFlight.current?.abort();
      }
    };
    changed();
    document.addEventListener("visibilitychange", changed);
    return () => document.removeEventListener("visibilitychange", changed);
  }, [client]);
  const result = useQuery({
    queryKey, staleTime: intervalMs,
    enabled: visible && !paused, retry: false, refetchOnWindowFocus: false,
    refetchOnReconnect: false, refetchIntervalInBackground: false,
    refetchInterval: () => nextPollDelay(failures.current, intervalMs),
    queryFn: async () => {
      const controller = new AbortController();
      inFlight.current = controller;
      try {
        const data = await latestFetcher.current(AbortSignal.any([controller.signal, AbortSignal.timeout(POLL_TIMEOUT_MS)]));
        failures.current = 0;
        setConsecutiveFailures(0);
        return data;
      } catch (error) {
        // A hidden tab's abort is not a failure; a timeout or a bad response is.
        if (!controller.signal.aborted) {
          failures.current += 1;
          setConsecutiveFailures(failures.current);
        }
        throw error;
      } finally {
        if (inFlight.current === controller) inFlight.current = null;
      }
    },
  });
  useEffect(() => {
    if (resumes > 0 && visible && !paused) void client.refetchQueries({ queryKey: latestKey.current, exact: true, type: "active" }, { cancelRefetch: false });
  }, [client, resumes, visible, paused]);
  // Spreading reads every field of TanStack's tracked result, so the caller re-renders on any change: fine for a poll.
  return { ...result, consecutiveFailures };
}
