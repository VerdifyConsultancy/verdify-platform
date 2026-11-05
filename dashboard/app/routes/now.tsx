// SPDX-License-Identifier: MIT
/**
 * Now at `/` (redesign §5.1; SCR-01, #1129): five seconds to know whether everything is OK, what is running, whether
 * water is fine and what needs someone, with the caretaker's on-site actions. In order: the hero (the answer, the
 * temperature, three band bars, time in band today and the status line), the quick actions (operators), Needs someone
 * beside Water today, "Running and next", and the zone strip. Each fact shows once: the open-alert count in the answer or
 * Needs someone's head, Stop all water once, the next runs only in "Running and next".
 *
 * Reads, each polled through weather's `polling.ts` (paused while the tab is hidden, backing off on failures):
 * - API-02 `GET /v1/now`, the shell's own poll (`useShellNow`): one request serves the banners and Now. Every 15 s,
 *   and every 5 s while a Stop confirms (`useStop` reads the same entry).
 * - API-IRR-01: Water today, occupancy, the engine flag, the mister faults. Every 15 s, every 5 s while a Stop is pending.
 * - API-14 `status=recent`, Activity's read under its key: Needs someone's open alerts and what was resolved in the
 *   last 24 h. Every 15 s.
 * - Every 60 s: API-04 over the last 25 h (today's range, the sparklines) and its time-in-band keys over yesterday and
 *   today; API-07 and API-IRR-02 for "Running and next"; API-03, API-04 and API-06 for the zones. API-05 for the track's
 *   last 6 h only once the track nears the screen (never on a phone, which shows lists instead).
 *
 * Each panel fails alone, and Stop all water renders whatever the water status can or cannot show. Nothing here renders
 * greenhouse data on the server: the server render is the loading state, and the data loads in the browser.
 */
import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { RunningAndNext } from "../components/coming-up";
import { useClockValue } from "../components/freshness";
import { todayStartS } from "../components/now/climate";
import { NowHero } from "../components/now/hero";
import { NeedsSomeone } from "../components/now/needs-someone";
import { QuickActions } from "../components/now/quick-actions";
import { useDayRead, useTimeInBandRead } from "../components/now/reads";
import { WaterCard } from "../components/now/water-card";
import { PanelErrorBoundary } from "../components/panel";
import { ZoneTiles } from "../components/zone-tiles";
import { RECENT_ALERTS } from "../lib/activity";
import { localDate } from "../lib/format";
import { nowAnswer } from "../lib/now-answer";
import { SessionEnded } from "../lib/operator-api";
import { READ_INTERVAL_MS, SETTLING_INTERVAL_MS, irrigationSettling, panelRead, readKey, useApiRead } from "../lib/queries";
import { useShellNow, useShellStatus } from "../lib/shell";
import { useStop } from "../lib/writes";

export const meta = () => [{ title: "Now · Verdify dashboard" }];
/** Needs someone reads API-14 every 15 s. */
export const ALERTS_POLL_MS = 15_000;

export default function Now() {
  const shell = useShellNow();
  const status = useShellStatus();
  const client = useQueryClient();
  const { stop, sending, confirming } = useStop();
  const pending = sending || confirming;
  const irrigation = panelRead(useApiRead("API-IRR-01", {}, { intervalMs: pending ? SETTLING_INTERVAL_MS : READ_INTERVAL_MS.irrigation, settling: irrigationSettling }));
  const alerts = panelRead(useApiRead("API-14", RECENT_ALERTS, { intervalMs: ALERTS_POLL_MS }));
  const day = panelRead(useDayRead());
  const today = useClockValue((clock) => localDate(new Date(clock).toISOString()));
  const since = useClockValue(todayStartS);
  const daily = panelRead(useTimeInBandRead(today));
  // The newest API-02 read failed: the sentence says so at once; the shell's banner waits for a second failure. The
  // entry's error is the newest read's, the shell's poll or a Stop's readback (a success clears it); the poll's own
  // failure count lags a readback that answered. A 401 is the session ending, which the shell's card says.
  const failed = shell.error !== null && shell.error !== undefined && !(shell.error instanceof SessionEnded);
  const answer = nowAnswer({ now: shell.data, failed });
  const retryNow = useCallback(() => { void client.refetchQueries({ queryKey: readKey("API-02"), exact: true }); }, [client]);
  const stopControl = { stop: () => { void stop(); }, sending, confirming };
  // The page rhythm (redesign §3.4): the hero with the quick actions under it, then the sections at the section gap.
  return <div className="page now-page">
    <div className="now-head">
      <PanelErrorBoundary thing="the greenhouse data" onRetry={retryNow}>
        <NowHero now={shell.data} answer={answer} irrigation={irrigation} day={day} daily={daily} onRetry={retryNow} />
      </PanelErrorBoundary>
      <QuickActions stop={stopControl} irrigation={irrigation} mode={shell.data?.mode.code ?? null} />
    </div>
    <div className="now-pair">
      <PanelErrorBoundary thing="alerts" text="Alerts unavailable" onRetry={alerts.retry}>
        <NeedsSomeone alerts={alerts} now={shell.data} answer={answer} apiFailed={failed} openCount={failed ? null : status.openAlerts} irrigation={irrigation} />
      </PanelErrorBoundary>
      <PanelErrorBoundary thing="water status" text="Water status unavailable." onRetry={irrigation.retry}>
        <WaterCard irrigation={irrigation} />
      </PanelErrorBoundary>
    </div>
    <PanelErrorBoundary thing="what is running and next">
      <RunningAndNext now={shell.data} answer={answer} irrigation={irrigation} />
    </PanelErrorBoundary>
    <PanelErrorBoundary thing="the zones">
      <ZoneTiles irrigation={irrigation} day={day} since={since} />
    </PanelErrorBoundary>
  </div>;
}
