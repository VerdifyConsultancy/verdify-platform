// SPDX-License-Identifier: MIT
import { Link, Outlet, useLocation } from "react-router";
import { Badge } from "../components/badge";
import { Button } from "../components/button";
import { PageHead, cls } from "../components/common";
import { Ago } from "../components/line-card";
import { PauseControls } from "../components/pause-lines";
import { useIrrigationRead } from "../lib/queries";
import { useShellNow, useShellStatus } from "../lib/shell";
import { programBanner, type WaterOutlet } from "../lib/water";
import { useStop } from "../lib/writes";

export const meta = () => [{ title: "Water · Verdify dashboard" }];

const VIEWS = [{ to: "/water", label: "Board" }, { to: "/water/schedules", label: "Schedules" }, { to: "/water/history", label: "History" }] as const;

/** Board, Schedules and History; the current one carries `aria-current="page"`. Sticky with Stop all water at phone width. */
function WaterTabs() {
  const path = useLocation().pathname.replace(/\/+$/, "") || "/";
  return <nav className="water-tabs" aria-label="Water views"><ul className="chips">
    {VIEWS.map((view) => <li key={view.to}><Link to={view.to} aria-current={path === view.to ? "page" : undefined}>{view.label}</Link></li>)}
  </ul></nav>;
}

/**
 * Stop all water (API-IRR-07 `{}` through `useStop`): operators only, one tap, no dialog, one Idempotency-Key per tap,
 * "Sending…" until the 202, then the Stop result toast. Never disabled by the writes flag, and shown whatever the
 * program banner says; before the engine the bridge stops every wet line. At phone width it reads "■ Stop all".
 */
function StopAllWater() {
  const { stop, sending } = useStop();
  return <Button variant="stop" write busy={sending} className="stop-all" onClick={() => void stop()}>
    <span aria-hidden="true">■ </span>Stop all <span className="stop-all-rest">water</span>
  </Button>;
}

/** The program banner (#1135): one line from API-IRR-01 `engine` and `program` and the shell's reporting state. */
function ProgramBanner({ irrigation }: WaterOutlet) {
  const status = useShellStatus();
  const now = useShellNow();
  const banner = programBanner({
    failing: irrigation.isError, status: irrigation.data ?? null, ingestorRunning: status.ingestorRunning,
    controllerReporting: status.controllerReporting, climateAt: now.data?.freshness.climate.observed_at,
  });
  if (banner.kind === "loading") return <p className="program-banner program-banner-loading skeleton" aria-hidden="true" />;
  return <p className={cls("notice", "program-banner", banner.tone === "fault" && "notice-fault", banner.tone === "warn" && "notice-warn")} data-program={banner.kind}>
    <span>{banner.text}</span>
    {banner.badge === "confirmed" && <> <Badge kind="confirmed" />{banner.confirmedAt && <> <Ago at={banner.confirmedAt} /></>}</>}
    {banner.badge === "requested" && <> <Badge kind="requested" /></>}
    {banner.kind === "unavailable" && <> <Button onClick={() => void irrigation.refetch()}>Retry</Button></>}
  </p>;
}

/**
 * SCR-04 Water's layout: the page head, the Board, Schedules and History tabs with Stop all water, the program banner
 * with Pause lines…, then the view. It polls API-IRR-01 once for all three views (every 15 s, 5 s while an item settles
 * with the engine on), under the key Now's water card and `useStop` share, and hands it to them. No greenhouse data
 * renders in SSR.
 */
export default function Water() {
  const irrigation = useIrrigationRead();
  const context: WaterOutlet = { irrigation };
  return <div className="water-page">
    <PageHead eyebrow="Water · America/Denver" title="Water" />
    <div className="water-bar">
      <WaterTabs />
      <div className="water-actions"><StopAllWater /></div>
    </div>
    <div className="water-program">
      <ProgramBanner irrigation={irrigation} />
      <PauseControls lines={irrigation.data?.lines} />
    </div>
    <Outlet context={context} />
  </div>;
}
