// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { StrictMode } from "react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { usePolledQuery } from "./use-current";
import { MAX_POLL_DELAY, POLL_INTERVAL, nextPollDelay } from "./polling";

type Reading = { observed_at: string };
const reading: Reading = { observed_at: "2026-11-01T08:15:00Z" };
const KEY = ["dashboard", "now"] as const;
async function fetchReading(signal: AbortSignal): Promise<Reading> {
  const response = await fetch("/api/v1/now", { signal, cache: "no-store", headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error("Dashboard API unavailable");
  return (await response.json()) as Reading;
}
function Probe({ intervalMs }: { intervalMs: number }) {
  const result = usePolledQuery(KEY, fetchReading, intervalMs);
  return <p data-testid="source">{result.data?.observed_at ?? "no data"} · {result.isError ? "API unavailable" : "available"}</p>;
}
// Each call gets its own Response: a body can be read only once.
const answers = () => vi.fn(() => Promise.resolve(new Response(JSON.stringify(reading))));
let client: QueryClient;
function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
  document.dispatchEvent(new Event("visibilitychange"));
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-01T08:15:05Z"));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  focusManager.setFocused(true);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => { cleanup(); client.clear(); vi.useRealTimers(); vi.unstubAllGlobals(); focusManager.setFocused(undefined); });
function mount(intervalMs = POLL_INTERVAL) { render(<StrictMode><QueryClientProvider client={client}><Probe intervalMs={intervalMs} /></QueryClientProvider></StrictMode>); }
async function advance(milliseconds: number) { await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); }); }

test("nextPollDelay doubles the base interval per failure up to 120 s", () => {
  expect([0, 1, 2, 3, 4, 99].map((failures) => nextPollDelay(failures))).toEqual([15_000, 30_000, 60_000, 120_000, 120_000, 120_000]);
  expect([0, 1, 2, 3, 4, 5, 6].map((failures) => nextPollDelay(failures, 5_000))).toEqual([5_000, 10_000, 20_000, 40_000, 80_000, 120_000, 120_000]);
  expect([0, 1, 2].map((failures) => nextPollDelay(failures, 60_000))).toEqual([60_000, 120_000, 120_000]);
  expect(nextPollDelay(-3, 60_000)).toBe(60_000);
  expect(nextPollDelay(Number.NaN)).toBe(POLL_INTERVAL);
  expect(nextPollDelay(10_000)).toBe(MAX_POLL_DELAY);
  // A base above the cap never shortens.
  expect(nextPollDelay(2, 300_000)).toBe(300_000);
});

test("polling fetches on mount, then every interval; StrictMode, focus and reconnect cause no duplicates", async () => {
  const fetch = answers(); vi.stubGlobal("fetch", fetch);
  mount(); await advance(0);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId("source").textContent).toBe(`${reading.observed_at} · available`);
  await advance(14_999); expect(fetch).toHaveBeenCalledTimes(1);
  await advance(1); expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new Event("online")); });
  await advance(1000); expect(fetch).toHaveBeenCalledTimes(2);
});

test("one request stays in flight; a hidden tab aborts it and stops polling; showing it refreshes once", async () => {
  let signal: AbortSignal | undefined;
  let calls = 0;
  const fetch = vi.fn((_url: string, init: RequestInit) => {
    calls += 1;
    if (calls > 1) return Promise.resolve(new Response(JSON.stringify(reading)));
    signal = init.signal ?? undefined;
    return new Promise<Response>(() => {});
  });
  vi.stubGlobal("fetch", fetch); mount(); await advance(0);
  expect(fetch).toHaveBeenCalledTimes(1);
  await advance(35_000); expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { setVisibility("hidden"); });
  expect(signal?.aborted).toBe(true);
  await advance(120_000); expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId("source").textContent).toBe("no data · available");
  await act(async () => { setVisibility("visible"); });
  expect(fetch).toHaveBeenCalledTimes(2);
  await advance(0);
  expect(screen.getByTestId("source").textContent).toBe(`${reading.observed_at} · available`);
  await advance(15_000); expect(fetch).toHaveBeenCalledTimes(3);
});

test("failures keep the last data and back off to a bounded cadence; a success restores it", async () => {
  const fetch = vi.fn((): Promise<Response> => Promise.reject(new Error("Synthetic network outage")));
  fetch.mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify(reading))));
  vi.stubGlobal("fetch", fetch);
  mount(); await advance(0); expect(fetch).toHaveBeenCalledTimes(1);
  await advance(15_000); expect(fetch).toHaveBeenCalledTimes(2);
  // TanStack notifies observers on a 0 ms timer, so the failure renders a tick after the poll.
  await advance(1);
  expect(screen.getByTestId("source").textContent).toBe(`${reading.observed_at} · API unavailable`);
  await advance(29_998); expect(fetch).toHaveBeenCalledTimes(2);
  await advance(1); expect(fetch).toHaveBeenCalledTimes(3);
  await advance(59_999); expect(fetch).toHaveBeenCalledTimes(3);
  await advance(1); expect(fetch).toHaveBeenCalledTimes(4);
  await advance(119_999); expect(fetch).toHaveBeenCalledTimes(4);
  await advance(1); expect(fetch).toHaveBeenCalledTimes(5);
  await advance(120_000); expect(fetch).toHaveBeenCalledTimes(6);
  expect(client.getQueryData(KEY)).toEqual(reading);
  fetch.mockImplementation(() => Promise.resolve(new Response(JSON.stringify(reading))));
  await advance(120_000); expect(fetch).toHaveBeenCalledTimes(7);
  await advance(1);
  expect(screen.getByTestId("source").textContent).toBe(`${reading.observed_at} · available`);
  await advance(15_000); expect(fetch).toHaveBeenCalledTimes(8);
});

test("the interval is the caller's: a 60-second query polls every 60 s and backs off to 120 s", async () => {
  const fetch = answers(); vi.stubGlobal("fetch", fetch);
  mount(60_000); await advance(0); expect(fetch).toHaveBeenCalledTimes(1);
  await advance(59_999); expect(fetch).toHaveBeenCalledTimes(1);
  fetch.mockImplementation(() => Promise.resolve(new Response("unavailable", { status: 503 })));
  await advance(1); expect(fetch).toHaveBeenCalledTimes(2);
  await advance(119_999); expect(fetch).toHaveBeenCalledTimes(2);
  await advance(1); expect(fetch).toHaveBeenCalledTimes(3);
});

test("a tab opened hidden waits, and repeated visibility events refresh only once", async () => {
  const fetch = answers(); vi.stubGlobal("fetch", fetch);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  mount(); await advance(60_000);
  expect(fetch).not.toHaveBeenCalled();
  await act(async () => { setVisibility("visible"); setVisibility("visible"); });
  expect(fetch).toHaveBeenCalledTimes(1);
  await advance(0);
  await act(async () => { setVisibility("hidden"); });
  await act(async () => { setVisibility("visible"); setVisibility("visible"); });
  expect(fetch).toHaveBeenCalledTimes(2);
});
