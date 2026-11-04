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
import { BURSTS, BURSTS_ARRIVE_TEXT, CommandNote, burstsIgnoredText, useBurstCommand, type BurstKind } from "../burst-panel";
import { Button } from "../button";
import { StopAllWater, type StopAllControl } from "../stop-all-water";

/** "Fans" and "15 min": the burst's name over its length, so three fit side by side on a phone. */
function QuickBurst({ kind, minutes, label }: { kind: BurstKind; minutes: number; label: string }) {
  const command = useBurstCommand();
  const name = label.replace(/\s+\d+\s*min$/, "");
  return <div className="now-burst">
    <Button write className="now-burst-button" busy={command.sending} onClick={() => void command.send({}, { kind, minutes })}>
      <span className="now-burst-name">{name}</span> <span className="now-burst-length">{minutes} min</span>
    </Button>
    <CommandNote phase={command.phase} message={command.message} error={command.error} />
  </div>;
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
    <div className="now-bursts">{BURSTS.map((burst) => <QuickBurst key={burst.kind} kind={burst.kind} minutes={burst.minutes} label={burst.label} />)}</div>
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
