// SPDX-License-Identifier: MIT
/**
 * SCR-01 Now at `/` (#1129): the five-second answer to "is everything OK?", what runs, whether water is fine and what
 * needs someone.
 *
 * Reads, each polled through weather's `polling.ts` (paused while the tab is hidden, backing off on failures):
 * - API-02 `GET /v1/now`, the shell's own poll (`useShellNow`): one request serves the banners and Now. Every 15 s,
 *   and every 5 s while a Stop confirms (`useStop` reads the same entry).
 * - API-IRR-01 `GET /v1/irrigation`: the water card, the occupancy chip and the Water tile. Every 15 s, every 5 s while
 *   a Stop is pending.
 * - API-14 `GET /v1/alerts?status=open`: Needs someone. Every 15 s.
 * - Below Needs someone, so the hero and the water card stay above a phone's fold: Coming up (API-07 and API-IRR-02) and
 *   the zone tiles (API-03, API-04 and API-06), each read every 60 s (#1138).
 *
 * Each panel fails alone, and Stop all water renders whatever the water card can or cannot show. Nothing here renders
 * greenhouse data on the server: the server render is the loading state, and the data loads in the browser.
 */
import { useCallback, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router";
import { ComingUp } from "../components/coming-up";
import { NowHero } from "../components/now/hero";
import { NeedsSomeone } from "../components/now/needs-someone";
import { RunningPanel } from "../components/now/running";
import { WaterCard, type StopControl } from "../components/now/water-card";
import { PanelErrorBoundary } from "../components/panel";
import { ZoneTiles } from "../components/zone-tiles";
import { PHONE_QUERY } from "../lib/narrow";
import { nowAnswer } from "../lib/now-answer";
import { SessionEnded } from "../lib/operator-api";
import { READ_INTERVAL_MS, SETTLING_INTERVAL_MS, irrigationSettling, panelRead, readKey, useApiRead } from "../lib/queries";
import { useShellNow, useShellStatus } from "../lib/shell";
import { useStop } from "../lib/writes";

export const meta = () => [{ title: "Now · Verdify dashboard" }];
/** Needs someone reads API-14 every 15 s. */
export const ALERTS_POLL_MS = 15_000;
const OPEN_ALERTS = { status: "open" } as const;

/**
 * The ingestor banner and the answer link to `#data`, the Data tile. On a phone that tile sits in the closed "All
 * readings" disclosure, so open it and bring its copy into view, once the hero has drawn it.
 */
function useDataTileOnPhones(drawn: boolean) {
  const { hash } = useLocation();
  useEffect(() => {
    if (!drawn || hash !== "#data" || typeof window.matchMedia !== "function" || !window.matchMedia(PHONE_QUERY).matches) return;
    const details = document.querySelector<HTMLDetailsElement>(".hero details");
    if (!details) return;
    details.open = true;
    details.querySelector<HTMLElement>('[data-tile="data"]')?.scrollIntoView?.({ block: "center" });
  }, [hash, drawn]);
}

export default function Now() {
  const shell = useShellNow();
  const status = useShellStatus();
  const client = useQueryClient();
  const { stop, sending, confirming } = useStop();
  const pending = sending || confirming;
  const irrigation = panelRead(useApiRead("API-IRR-01", {}, { intervalMs: pending ? SETTLING_INTERVAL_MS : READ_INTERVAL_MS.irrigation, settling: irrigationSettling }));
  const alerts = panelRead(useApiRead("API-14", OPEN_ALERTS, { intervalMs: ALERTS_POLL_MS }));
  // The newest API-02 read failed: the sentence says so at once; the shell's banner waits for a second failure. The
  // entry's error is the newest read's, the shell's poll or a Stop's readback (a success clears it); the poll's own
  // failure count lags a readback that answered. A 401 is the session ending, which the shell's card says.
  const failed = shell.error !== null && shell.error !== undefined && !(shell.error instanceof SessionEnded);
  const answer = nowAnswer({ now: shell.data, failed });
  const retryNow = useCallback(() => { void client.refetchQueries({ queryKey: readKey("API-02"), exact: true }); }, [client]);
  const stopControl: StopControl = { stop: () => { void stop(); }, sending, confirming };
  useDataTileOnPhones(answer.kind !== "loading");
  return <div className="now-page">
    <PanelErrorBoundary thing="the greenhouse data" onRetry={retryNow}>
      <NowHero now={shell.data} answer={answer} irrigation={irrigation} onRetry={retryNow} />
    </PanelErrorBoundary>
    <PanelErrorBoundary thing="what is running">
      <RunningPanel now={shell.data} answer={answer} irrigation={irrigation} />
    </PanelErrorBoundary>
    <div className="now-panels">
      <WaterCard irrigation={irrigation} stop={stopControl} />
      <PanelErrorBoundary thing="alerts" text="Alerts unavailable" onRetry={alerts.retry}>
        <NeedsSomeone alerts={alerts} now={shell.data} apiFailed={failed} openCount={failed ? null : status.openAlerts} />
      </PanelErrorBoundary>
    </div>
    <PanelErrorBoundary thing="what is coming up">
      <ComingUp irrigation={irrigation} />
    </PanelErrorBoundary>
    <PanelErrorBoundary thing="the zones">
      <ZoneTiles irrigation={irrigation} />
    </PanelErrorBoundary>
  </div>;
}
