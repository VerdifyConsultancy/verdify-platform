// SPDX-License-Identifier: MIT
/**
 * The shell on every page (screens §1.2): it polls API-01 (`/v1/me`, 60 s) and API-02 (`/v1/now`, 15 s) once for the
 * whole page, and `shellStatus` turns those answers into at most one fault banner, worst first, the warn banners and
 * the one open-alert count. Screens read the same queries through `useMe()`, `useShellNow()` and `useShellStatus()`;
 * they never start a second poll of either. Nothing here renders in SSR: every value loads in the browser.
 */
import { createContext, useContext, useMemo } from "react";
import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { CLIMATE_REPORTING_S, HEARTBEAT_FRESH_S } from "../components/alert-row";
import { serverClockSkew } from "../components/freshness";
import { shortTime } from "./format";
import { SessionEnded, apiGet, type Freshness, type Me, type Now } from "./operator-api";
import { POLL_INTERVAL } from "./polling";
import { readKey } from "./queries";
import { usePolledQuery, type PolledQueryResult } from "./use-current";

/** API-01 every 60 s and API-02 every 15 s (architecture §3, polling). */
export const ME_POLL_MS = 60_000;
export const NOW_POLL_MS = POLL_INTERVAL;
/** "Dashboard API unavailable" after this many API-02 failures in a row. */
export const API_DOWN_AFTER = 2;
/**
 * The reads' one keys (`readKey`), so the write hooks share the shell's entries: Stop notes what runs from this API-02
 * entry and reads it every 5 s while it confirms, and an Acknowledge refetches the count here.
 */
export const ME_QUERY_KEY = readKey("API-01");
export const NOW_QUERY_KEY = readKey("API-02");
export const fetchMe = (signal: AbortSignal) => apiGet("API-01", {}, { signal });
export const fetchNow = (signal: AbortSignal) => apiGet("API-02", {}, { signal });

// ---------------------------------------------------------------------------------------------------- status
export type BannerKind = "api_down" | "ingestor" | "controller" | "flow" | "stuck" | "writes_off";
export type ShellBanner = {
  readonly kind: BannerKind;
  readonly tone: "fault" | "warn";
  readonly text: string;
  /** The page that explains it. */
  readonly link?: { readonly to: string; readonly label: string };
};
export type ShellStatus = {
  /** At most one: API down, then ingestor not running, then controller not reporting, then the flow banner. */
  readonly fault: ShellBanner | null;
  /** Every warn that is true, one line each: settings not reaching the controller, then changes switched off. */
  readonly warns: readonly ShellBanner[];
  /** API-02 failed twice in a row: nothing green shows. */
  readonly apiDown: boolean;
  /**
   * API-02 `alerts.open`, the one open-alert count; null until API-02 first answers, and while the API is down, when
   * the last count is old news (screens §1.5: unknown never shows the previous value).
   */
  readonly openAlerts: number | null;
  /** The ingestor heartbeat is at most 180 s old; null while unknown (no answer yet, or the API is down). */
  readonly ingestorRunning: boolean | null;
  /** The heartbeat is fresh and the newest climate row at most 300 s old (architecture §7, the reporting test). */
  readonly controllerReporting: boolean | null;
};

/** The ingestor heartbeat (a `system_state` row every 60 s, Q-01) is fresh: at most 180 s old and not stale. */
export function heartbeatFresh(ingestor: Freshness | null | undefined): boolean {
  return !!ingestor && !ingestor.stale && ingestor.age_s !== null && ingestor.age_s <= HEARTBEAT_FRESH_S;
}
/** The newest climate row is at most 300 s old (the `GET /health` threshold). */
export function climateFresh(climate: Freshness | null | undefined): boolean {
  return !!climate && climate.age_s !== null && climate.age_s <= CLIMATE_REPORTING_S;
}
const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
/** " since 06:10", or nothing when the time is missing. */
const since = (value: string | null | undefined) => (validTime(value) ? ` since ${shortTime(value)}` : "");

export const API_DOWN_TEXT = "Dashboard API unavailable";
export const WRITES_OFF_TEXT = "Changes are switched off; reading still works";

function flowBanner(leak: Now["leak"]): ShellBanner {
  const link = { to: `/activity?alert=${leak.alert_id}`, label: "Open the alert" };
  // Once the meter reads under 0.1 gal/min twice the banner says when flow stopped; it never reads as an all clear
  // while the alert is open (VD-72).
  if (validTime(leak.stopped_at)) return { kind: "flow", tone: "fault", text: `Flow stopped at ${shortTime(leak.stopped_at)}`, link };
  const gallons = leak.gal_last_10min;
  const volume = gallons !== null && gallons !== undefined && Number.isFinite(gallons) ? ` · ${gallons} gal in 10 min` : "";
  return {
    kind: "flow", tone: "fault", link,
    text: `Water flowing with every Verdify valve closed${since(leak.since)}${volume}: a leak, or the hose, sink or hydro in use. If no one is using water, close the spigot.`,
  };
}

/**
 * The shell's banners and count from the newest API-01 and API-02 answers (null before the first) and API-02's
 * failures in a row. It never says "Controller offline": the ingestor banner names a dead ingestor, and the controller
 * banner says the controller keeps running what it holds.
 */
export function shellStatus(me: Me | null, now: Now | null, consecutiveFailures: number): ShellStatus {
  const apiDown = consecutiveFailures >= API_DOWN_AFTER;
  const faults: ShellBanner[] = [];
  if (apiDown) faults.push({ kind: "api_down", tone: "fault", text: API_DOWN_TEXT });
  const ingestorRunning = now ? heartbeatFresh(now.freshness.ingestor) : null;
  const climateReporting = now ? climateFresh(now.freshness.climate) : null;
  if (now && !ingestorRunning) {
    faults.push({
      kind: "ingestor", tone: "fault", link: { to: "/#data", label: "Data on Now" },
      text: `Ingestor not running${since(now.freshness.ingestor.observed_at)}. The controller keeps running on its own.`,
    });
  } else if (now && !climateReporting) {
    faults.push({
      kind: "controller", tone: "fault", link: { to: "/equipment", label: "Equipment" },
      text: `Controller not reporting${since(now.freshness.climate.observed_at)}. If it has power, it keeps running VPD misting, its caps and (with the irrigation firmware) its schedules.`,
    });
  }
  if (now && now.leak.alert_id !== null && now.leak.alert_id !== undefined) faults.push(flowBanner(now.leak));
  const warns: ShellBanner[] = [];
  if (now?.writer.stuck) {
    const differing = Number.isFinite(now.writer.differing) ? ` (${now.writer.differing} differ)` : "";
    warns.push({
      kind: "stuck", tone: "warn", link: { to: "/settings?filter=requested", label: "Requested settings" },
      text: `Settings not reaching the controller${since(now.writer.oldest_since)}${differing}`,
    });
  }
  if (me && !me.writes_enabled) warns.push({ kind: "writes_off", tone: "warn", text: WRITES_OFF_TEXT });
  return {
    fault: faults[0] ?? null,
    warns,
    apiDown,
    // While the API is down the last answer is old news: the count and whether anything reports are unknown. A stale
    // "Activity 0" beside the API banner would read as an all clear.
    openAlerts: !apiDown && now && Number.isFinite(now.alerts.open) ? now.alerts.open : null,
    ingestorRunning: apiDown ? null : ingestorRunning,
    controllerReporting: apiDown || ingestorRunning === null ? null : ingestorRunning && Boolean(climateReporting),
  };
}

// ---------------------------------------------------------------------------------------------------- footer and sign-in
const SHA = /^[0-9a-f]{7,40}$/i;
/** The first 12 hex of a commit, or "unknown"; null (not loaded yet) reads "—". */
export function revisionLabel(revision: string | null | undefined): string {
  if (revision === null || revision === undefined) return "—";
  return SHA.test(revision) ? revision.slice(0, 12).toLowerCase() : "unknown";
}
/** "Verdify dashboard · build 0123456789ab · API fedcba987654": the BFF's build and API-01's `source_revision`. */
export function buildLine(build: string, api: string | null | undefined): string {
  return `Verdify dashboard · build ${revisionLabel(build)} · API ${revisionLabel(api)}`;
}
/** The longest returnTo the BFF keeps (BFF-01), and the longest request target server.mjs serves (414 past it). */
const MAX_RETURN_TO = 2048;
const MAX_TARGET = 4096;
/**
 * Sign in, then back to `pathAndQuery` (the BFF keeps only a same-origin path). A page longer than the BFF keeps, or
 * one whose encoded form would make /auth/login too long to serve, returns to "/", as the BFF's own sign-in redirect
 * does, so the link always works.
 */
export function signInHref(pathAndQuery: string): string {
  const target = pathAndQuery || "/";
  const href = `/auth/login?returnTo=${encodeURIComponent(target)}`;
  return target.length <= MAX_RETURN_TO && href.length <= MAX_TARGET ? href : "/auth/login?returnTo=%2F";
}

/**
 * The page's query client. Any query or mutation that fails with `SessionEnded` (a 401: the 12 h session ended or was
 * removed) calls `onSessionEnded`, so every poll and every write ends the same way: the session-ended card.
 */
export function createQueryClient(onSessionEnded: () => void): QueryClient {
  const ended = (error: unknown) => { if (error instanceof SessionEnded) onSessionEnded(); };
  return new QueryClient({
    queryCache: new QueryCache({ onError: ended }),
    mutationCache: new MutationCache({ onError: ended }),
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } },
  });
}

// ---------------------------------------------------------------------------------------------------- the shared state
/** One poll as screens read it. `data` is the newest answer, kept through failures. */
export type ShellQuery<T> = {
  readonly data: T | null;
  /** The newest failure, or null after an answer. */
  readonly error: unknown;
  /** No answer and no failure yet. */
  readonly pending: boolean;
  /** Failures in a row since the newest answer. */
  readonly failures: number;
  /** When the newest answer arrived (epoch ms), 0 before the first. */
  readonly updatedAt: number;
};
export type ShellState = {
  /** The BFF's own build commit (`SOURCE_REVISION`), from the root loader. */
  readonly build: string;
  readonly me: ShellQuery<Me>;
  readonly now: ShellQuery<Now>;
  readonly status: ShellStatus;
};
const EMPTY_QUERY: ShellQuery<never> = { data: null, error: null, pending: true, failures: 0, updatedAt: 0 };
export const EMPTY_SHELL: ShellState = { build: "unknown", me: EMPTY_QUERY, now: EMPTY_QUERY, status: shellStatus(null, null, 0) };
export const ShellContext = createContext<ShellState>(EMPTY_SHELL);

const snapshot = <T>(query: PolledQueryResult<T>): ShellQuery<T> => ({
  data: query.data ?? null, error: query.error, pending: query.isPending, failures: query.consecutiveFailures, updatedAt: query.dataUpdatedAt,
});

/**
 * The page's two polls and what they say, plus the device clock's lead over the server's from API-02's
 * `server_time` (for `ServerClockProvider`). The shell calls it once, under its QueryClientProvider; screens read the
 * result through the hooks below.
 */
export function useShellPolls(build: string): ShellState & { readonly skewMs: number } {
  const me = usePolledQuery(ME_QUERY_KEY, fetchMe, ME_POLL_MS);
  const now = usePolledQuery(NOW_QUERY_KEY, fetchNow, NOW_POLL_MS);
  const meData = me.data ?? null;
  const nowData = now.data ?? null;
  // A 401 is the session ending, not the API failing: the session-ended card says so, never the API banner.
  const apiFailures = now.error instanceof SessionEnded ? 0 : now.consecutiveFailures;
  const status = useMemo(() => shellStatus(meData, nowData, apiFailures), [meData, nowData, apiFailures]);
  return { build, me: snapshot(me), now: snapshot(now), status, skewMs: nowData ? serverClockSkew(nowData.server_time, now.dataUpdatedAt) : 0 };
}

export const useShell = (): ShellState => useContext(ShellContext);
/** API-01: who is signed in, the role, whether changes are on and the API's build; null until it first answers. */
export const useMe = (): Me | null => useShell().me.data;
/** The shell's API-02 poll: Now and every page that shows freshness read it from here. */
export const useShellNow = (): ShellQuery<Now> => useShell().now;
/** The banners, the one open-alert count, API down and the reporting test. */
export const useShellStatus = (): ShellStatus => useShell().status;
