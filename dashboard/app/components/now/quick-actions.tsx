// SPDX-License-Identifier: MIT
/**
 * Now's quick actions (redesign §5.1, D-UX-05): the caretaker's on-site actions in one row, for operators only. Stop all
 * water comes first (full width on a phone, above the fold at 390 × 844), then the three bursts the Home Assistant
 * buttons ran: Fans 15 min, Humidify 5 min and Vent bypass 10 min, each one API-IRR-12 with no dialog. What runs is
 * only ever the controller's readback (API-02 `overrides`), which "Running and next" shows with Clear. Before the
 * irrigation firmware API-IRR-12 answers 503, so the row holds Stop all water and says when the bursts arrive. The
 * bursts stay on Equipment too. Viewers get no row at all.
 */
import type { IrrigationStatus } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { OperatorOnly } from "../../lib/session";
import { BURSTS, BURSTS_ARRIVE_TEXT, BurstButton, burstsIgnoredText } from "../burst-panel";
import { Button } from "../button";
import { StopAllWater, type StopAllControl } from "../stop-all-water";

/** The shared burst button (Equipment's too): its name over its length on a phone, so three fit side by side. */
function QuickBurst({ burst }: { burst: (typeof BURSTS)[number] }) {
  return <div className="now-burst"><BurstButton burst={burst} className="now-burst-button" /></div>;
}

/** The bursts, or why they are not here yet: while the engine flag is unknown the row keeps its height. */
function Bursts({ irrigation, mode }: { irrigation: PanelRead<IrrigationStatus>; mode: string | null }) {
  if (irrigation.failed && !irrigation.data) {
    return <p className="meta now-actions-note">Can't tell whether the controller has the irrigation firmware yet. <Button onClick={irrigation.retry}>Retry</Button></p>;
  }
  const engine = irrigation.data?.engine ?? null;
  if (engine === null) return <div className="skeleton now-bursts-skeleton" aria-hidden="true" />;
  if (!engine) return <p className="meta now-actions-note" data-arrives="bursts">{BURSTS_ARRIVE_TEXT}</p>;
  const ignored = burstsIgnoredText(mode);
  return <>
    <div className="now-bursts">{BURSTS.map((burst) => <QuickBurst key={burst.kind} burst={burst} />)}</div>
    {ignored && <p className="fault now-actions-note" data-bursts="ignored">{ignored}</p>}
  </>;
}

export type QuickActionsProps = {
  /** The route's own `useStop`, so its reads speed up while the Stop confirms. */
  readonly stop: StopAllControl & { readonly confirming: boolean };
  readonly irrigation: PanelRead<IrrigationStatus>;
  /** API-02 `mode.code`, for "Bursts are ignored during emergency cooling". */
  readonly mode: string | null;
};

export function QuickActions({ stop, irrigation, mode }: QuickActionsProps) {
  return <OperatorOnly>
    <section className="now-actions" aria-label="Quick actions">
      <div className="now-stop">
        <StopAllWater wide control={stop} />
        {stop.confirming && <span className="meta">Waiting for the valves to read off</span>}
      </div>
      <Bursts irrigation={irrigation} mode={mode} />
    </section>
  </OperatorOnly>;
}
