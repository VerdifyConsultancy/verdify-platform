// SPDX-License-Identifier: MIT
import { Fragment, useId, useState, type ReactNode } from "react";
import { causeWords } from "../charts/causes";
import type { IrrigationLine, IrrigationStatus, Occupancy } from "../lib/operator-api";
import { useRole } from "../lib/session";
import { lineSyncWords, pauseWords, useWaterDrawer, vpdSourceWords, zoneWord } from "../lib/water";
import { useProgramWrite } from "../lib/writes";
import { words } from "../lib/words";
import { Badge, ReadbackBadge } from "./badge";
import { FieldError, Switch, describedBy, useFieldIds } from "./forms";
import { Ago, FaultLine } from "./line-card";

/** Both flags off (irrigation §4.4). */
export const WILL_NOT_MIST_TEXT = "This zone will not mist automatically";

type MisterFlag = "vpd_member" | "schedule_member";
const FLAGS: readonly { readonly flag: MisterFlag; readonly label: string }[] = [{ flag: "vpd_member", label: "VPD control" }, { flag: "schedule_member", label: "Schedule" }];

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
/** The API's occupancy reason (REASON_WORDS `occ`), which the card dates with the Frigate sighting. */
const FRIGATE = /^someone inside \(Frigate\)$/;

/** ON or OFF for a mister flag; "—" while the API has none. */
function Flag({ on }: { on: boolean | null | undefined }) {
  if (on === null || on === undefined) return <>—</>;
  return <Badge kind={on ? "on" : "off"} />;
}

/**
 * Whether a flag shows its badge. The API's `sync` is per line, so while it is settling the flag pressed here carries it,
 * or both when none was (another writer, or a reload). Once confirmed, a flag that is on or was pressed reads CONFIRMED;
 * an untouched flag that is off has nothing to confirm. `pressed` holds what each press asked for, and a press counts
 * only while the line reads that: a cancelled check sheet or a refused write changed nothing, so it carries no badge.
 */
export function flagShowsBadge(line: Pick<IrrigationLine, "sync" | "vpd_member" | "schedule_member">, flag: MisterFlag, pressed: ReadonlyMap<MisterFlag, boolean>): boolean {
  const changed = (item: MisterFlag) => pressed.has(item) && pressed.get(item) === line[item];
  if (line.sync !== "confirmed") return changed(flag) || !FLAGS.some((item) => changed(item.flag));
  return changed(flag) || line[flag] === true;
}

/**
 * One flag's switch, for operators: `{vpd_member, manual: true}` or `{schedule_member, manual: true}` at once. Schedule on
 * for a line with no check sends nothing and opens the check sheet instead, which sends it with `checked`.
 */
function FlagSwitch({ line, flag, label, badge, onPress }: { line: IrrigationLine; flag: MisterFlag; label: string; badge: boolean; onPress: (flag: MisterFlag, on: boolean) => void }) {
  const write = useProgramWrite("API-IRR-08");
  const { open: openCheck } = useWaterDrawer("check");
  const ids = useFieldIds();
  const change = (on: boolean) => {
    onPress(flag, on);
    if (flag === "schedule_member" && on && !line.check) openCheck(line.line_id, "schedule");
    else void write.send({ line_id: line.line_id }, flag === "vpd_member" ? { vpd_member: on, manual: true } : { schedule_member: on, manual: true });
  };
  const value = line[flag];
  return <div className="mister-flag" data-flag={flag}>
    {/* A flag the API has no value for is never shown as OFF (screens §1.5). */}
    {value === null || value === undefined
      ? <><span className="field-label">{label}</span> <Flag on={value} /></>
      : <Switch write label={<>{label} <span className="screen-reader">{line.line_id}</span></>} checked={value} busy={write.sending} onChange={change} describedBy={describedBy(write.error && ids.errorId)} />}
    {badge && <ReadbackBadge state={line.sync} />}
    {write.error && <FieldError id={ids.errorId}>{write.error.message}</FieldError>}
  </div>;
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
  /** Clear fault (#1136). */
  actions?: ReactNode;
};

/**
 * MisterAssociation (design system §4.6, irrigation §4.4): one mister zone's "VPD control" and "Schedule", switches for
 * an operator and ON or OFF for a viewer, each with its own badge, REQUESTED until the controller reports the program
 * that carries the change, then CONFIRMED; which reading its VPD control uses (D-IRP-13); its state in words; a watchdog
 * back-off or latch (D-IRP-19); when it last misted; and "This zone will not mist automatically" with both off.
 */
export function MisterAssociation({ line, status, today, actions }: MisterAssociationProps) {
  const headingId = useId();
  const operator = useRole() === "operator";
  const [pressed, setPressed] = useState<ReadonlyMap<MisterFlag, boolean>>(() => new Map());
  // A press once the line has settled starts afresh, so a change confirmed earlier does not read REQUESTED again.
  const press = (flag: MisterFlag, on: boolean) => setPressed((current) => new Map(line.sync === "confirmed" ? [] : current).set(flag, on));
  const pending = line.sync !== "confirmed";
  const syncWords = pending ? lineSyncWords(line.sync, status) : null;
  const source = vpdSourceWords(line.vpd_source);
  const state = misterState(line, status.occupancy, today);
  return <section className="card mister-zone" data-line={line.line_id} aria-labelledby={headingId}>
    <h3 id={headingId} className="eyebrow">{zoneWord(line.zone)} · {line.line_id}</h3>
    {operator
      ? <div className="mister-flags mister-switches">{FLAGS.map(({ flag, label }) => <FlagSwitch key={flag} line={line} flag={flag} label={label} badge={flagShowsBadge(line, flag, pressed)} onPress={press} />)}</div>
      : <dl className="kv mister-flags">{FLAGS.map(({ flag, label }) => <Fragment key={flag}>
        <dt>{label}</dt><dd><Flag on={line[flag]} />{flagShowsBadge(line, flag, pressed) && <> <ReadbackBadge state={line.sync} /></>}</dd>
      </Fragment>)}</dl>}
    {syncWords && <p className="state-note">{syncWords}</p>}
    <p className="mister-source">{source ?? <>VPD source —{!status.engine && <span className="muted"> arrives with the irrigation firmware</span>}</>}</p>
    {state && <p className="mister-state">{state}</p>}
    <FaultLine line={line} today={today} />
    <p>{line.last_water ? <>Last misted <Ago at={line.last_water.at} /></> : "No misting recorded"}</p>
    {line.vpd_member === false && line.schedule_member === false && <p className="mister-off">{WILL_NOT_MIST_TEXT}</p>}
    {actions && <div className="actions">{actions}</div>}
  </section>;
}
