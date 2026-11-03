// SPDX-License-Identifier: MIT
import { useId, type ReactNode } from "react";
import { causeWords } from "../charts/causes";
import type { IrrigationLine, IrrigationStatus, Occupancy } from "../lib/operator-api";
import { pauseWords, vpdSourceWords, zoneWord } from "../lib/water";
import { programSyncText } from "../lib/writes";
import { words } from "../lib/words";
import { Badge, ReadbackBadge } from "./badge";
import { Ago, FaultLine } from "./line-card";

/** Both flags off (irrigation §4.4): the words #1182's switches keep. */
export const WILL_NOT_MIST_TEXT = "This zone will not mist automatically";

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
/** The API's occupancy reason (REASON_WORDS `occ`), which the card dates with the Frigate sighting. */
const FRIGATE = /^someone inside \(Frigate\)$/;

/** ON or OFF for a mister flag; "—" while the API has none. */
function Flag({ on }: { on: boolean | null | undefined }) {
  if (on === null || on === undefined) return <>—</>;
  return <Badge kind={on ? "on" : "off"} />;
}

/** "someone inside (Frigate, seen 3 min ago)" when the reason is occupancy and Frigate's last sighting is known. */
function WaitReason({ reason, occupancy }: { reason: string | null | undefined; occupancy: Occupancy }) {
  if (reason && FRIGATE.test(reason) && validTime(occupancy.seen_at)) return <>someone inside (Frigate, seen <Ago at={occupancy.seen_at} />)</>;
  return <>{reason || "no reason given"}</>;
}

/** The zone's state in words ("Misting now (VPD)", "Waiting: someone inside (Frigate, seen 3 min ago)"); null while a fault line says it. */
function misterState(line: IrrigationLine, occupancy: Occupancy, today: string): ReactNode {
  const { state } = line;
  switch (state.code) {
    case "running": return <>Misting now{state.cause ? ` (${causeWords(state.cause)})` : ""}</>;
    case "waiting": return <>Waiting: <WaitReason reason={state.reason} occupancy={occupancy} /></>;
    case "blocked": return line.fault ? null : <>Can't mist now{state.reason ? `: ${state.reason}` : ""}</>;
    case "paused": return <>{pauseWords(line, today) ?? "Paused"}</>;
    case "disabled": return <>Line disabled: no misting</>;
    case "idle": return <>Not misting now</>;
    case "unknown": return <>unknown · {validTime(state.observed_at) ? <Ago at={state.observed_at} /> : "no state received"}</>;
    default: return <>{words("line_state", state.code)}</>;
  }
}

export type MisterAssociationProps = {
  /** L04 center, L07 south or L09 west. */
  line: IrrigationLine;
  /** API-IRR-01's `engine` and `program` (for the sync words) and `occupancy` (for the Frigate sighting). */
  status: Pick<IrrigationStatus, "engine" | "program" | "occupancy">;
  /** Today's America/Denver date. */
  today: string;
  /** Clear fault (#1136); #1182 turns the flags into switches. */
  actions?: ReactNode;
};

/**
 * MisterAssociation (design system §4.6, irrigation §4.4): one mister zone's "VPD control" and "Schedule" as ON or OFF,
 * each with the line's sync badge while the controller has not confirmed the program that carries it; which reading
 * its VPD control uses (D-IRP-13); its state in words; a watchdog back-off or latch (D-IRP-19); when it last misted; and
 * "This zone will not mist automatically" with both off.
 */
export function MisterAssociation({ line, status, today, actions }: MisterAssociationProps) {
  const headingId = useId();
  const pending = line.sync !== "confirmed";
  const syncWords = pending ? programSyncText(line.sync, status) : null;
  const source = vpdSourceWords(line.vpd_source);
  const state = misterState(line, status.occupancy, today);
  return <section className="card mister-zone" data-line={line.line_id} aria-labelledby={headingId}>
    <h3 id={headingId} className="eyebrow">{zoneWord(line.zone)} · {line.line_id}</h3>
    <dl className="kv mister-flags">
      <dt>VPD control</dt><dd><Flag on={line.vpd_member} />{pending && <> <ReadbackBadge state={line.sync} /></>}</dd>
      <dt>Schedule</dt><dd><Flag on={line.schedule_member} />{pending && <> <ReadbackBadge state={line.sync} /></>}</dd>
    </dl>
    {syncWords && <p className="state-note">{syncWords}</p>}
    <p className="mister-source">{source ?? <>VPD source —{!status.engine && <span className="muted"> arrives with the irrigation firmware</span>}</>}</p>
    {state && <p className="mister-state">{state}</p>}
    <FaultLine line={line} today={today} />
    <p>{line.last_water ? <>Last misted <Ago at={line.last_water.at} /></> : "No misting recorded"}</p>
    {line.vpd_member === false && line.schedule_member === false && <p className="mister-off">{WILL_NOT_MIST_TEXT}</p>}
    {actions && <div className="actions">{actions}</div>}
  </section>;
}
