// SPDX-License-Identifier: MIT
import { createContext, useContext, useSyncExternalStore, type ReactNode } from "react";
import { TIME_UNAVAILABLE, duration } from "../lib/format";
import { useApiDown } from "../lib/session";
import { LocalTime } from "./common";

// One shared one-second ticker for every age on the page; it runs only while something shows an age.
const TICK_MS = 1000;
const listeners = new Set<() => void>();
let ticker: ReturnType<typeof setInterval> | undefined;
function subscribe(listener: () => void) {
  listeners.add(listener);
  ticker ??= setInterval(() => { for (const notify of listeners) notify(); }, TICK_MS);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && ticker !== undefined) { clearInterval(ticker); ticker = undefined; }
  };
}
// The current whole second: stable between ticks, as useSyncExternalStore requires.
const currentSecond = () => Math.floor(Date.now() / TICK_MS) * TICK_MS;

const SkewContext = createContext(0);
/**
 * Ages count on the server's clock (weather's `CurrentConditions` rule): a device clock running ahead would otherwise
 * make every reading look old. The shell measures the skew on each successful API-02 poll with `serverClockSkew` and
 * provides it here.
 */
export function ServerClockProvider({ skewMs, children }: { skewMs: number; children?: ReactNode }) {
  return <SkewContext.Provider value={Number.isFinite(skewMs) ? skewMs : 0}>{children}</SkewContext.Provider>;
}
/** The device clock's lead over the server's, from a response's `server_time` and when it arrived; under 2 s is latency. */
export function serverClockSkew(serverTime: string | null | undefined, receivedAt: number): number {
  const server = serverTime ? Date.parse(serverTime) : Number.NaN;
  if (!Number.isFinite(server) || !Number.isFinite(receivedAt)) return 0;
  const offset = receivedAt - server;
  return Math.abs(offset) > 2000 ? offset : 0;
}
/** The device clock's lead over the server's in ms (0 until API-02 first answers), for a read that asks up to now. */
export const useServerSkew = (): number => useContext(SkewContext);
/**
 * A value worked out from now on the server's clock (`select` gets epoch ms), re-rendering only when it changes: the
 * local date, or the current minute, where a once-a-second render of a whole view would be waste.
 */
export function useClockValue<T extends string | number | boolean>(select: (now: number) => T): T {
  const skew = useContext(SkewContext);
  const snapshot = () => select(currentSecond() - skew);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
/** Now on the server's clock, in epoch ms, re-rendering once a second. */
export function useNow(): number {
  const skew = useContext(SkewContext);
  return useSyncExternalStore(subscribe, currentSecond, currentSecond) - skew;
}
/** The device clock's lead over the server's (ms, 0 within 2 s), for a time read once rather than ticking. */
export function useServerClockSkew(): number {
  return useContext(SkewContext);
}

export type FreshnessTone = "ok" | "stale" | "off";
/**
 * The freshness dot: ok, amber when the API marks the value stale, fault when the source is not reporting. It is never
 * ok while the dashboard API is down. Decorative: `AgeLabel` says the same in words.
 */
export function FreshnessDot({ stale, notReporting = false }: { stale: boolean; notReporting?: boolean }) {
  const apiDown = useApiDown();
  const tone: FreshnessTone = apiDown || notReporting ? "off" : stale ? "stale" : "ok";
  return <span className={`dot fresh-${tone}`} data-fresh={tone} aria-hidden="true" />;
}

/**
 * How old a value is: "Updated 20 s ago", or, when the API marks it stale, "Last data 14 min ago (06:02)". Whether it
 * is stale is the API's call, never inferred here from the age. The age ticks silently: there is no live region.
 */
export function AgeLabel({ observedAt, stale, now }: { observedAt: string | null | undefined; stale: boolean; now?: number }) {
  const ticking = useNow();
  if (!observedAt) return <span className="age">No data yet</span>;
  const at = Date.parse(observedAt);
  if (!Number.isFinite(at)) return <span className="age">Last data {TIME_UNAVAILABLE}</span>;
  // A timestamp a moment ahead of this clock reads as just now, never as a negative age.
  const age = Math.max(0, Math.floor(((now ?? ticking) - at) / 1000));
  if (!stale) return <span className="age">Updated {duration(age)} ago</span>;
  return <span className="age">Last data {duration(age)} ago (<LocalTime at={observedAt} date={age >= 86_400} />)</span>;
}
