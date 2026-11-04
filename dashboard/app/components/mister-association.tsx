// SPDX-License-Identifier: MIT
import { Fragment, useEffect, useRef, useState } from "react";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import { useRole } from "../lib/session";
import { lineSyncWords, useWaterDrawer, vpdSourceWords } from "../lib/water";
import { useProgramWrite } from "../lib/writes";
import { Badge, ReadbackBadge } from "./badge";
import { FieldError, Switch, describedBy, useFieldIds } from "./forms";
import { useNow } from "./freshness";
import { CONFIRMED_FRESH_MS } from "./schedule/rules-list";

/** Both flags off (irrigation §4.4). */
export const WILL_NOT_MIST_TEXT = "This zone will not mist automatically";

type MisterFlag = "vpd_member" | "schedule_member";
const FLAGS: readonly { readonly flag: MisterFlag; readonly label: string }[] = [{ flag: "vpd_member", label: "VPD control" }, { flag: "schedule_member", label: "Schedule" }];

/** ON or OFF for a mister flag; "—" while the API has none. */
function Flag({ on }: { on: boolean | null | undefined }) {
  if (on === null || on === undefined) return <>—</>;
  return <Badge kind={on ? "on" : "off"} />;
}

/**
 * Whether a flag shows its pill. The API's `sync` is per line, so while it is settling the flag pressed here carries it,
 * or both when none was (another writer, or a reload). Once confirmed, only a flag pressed here reads CONFIRMED, and only
 * for 10 minutes (D-UX-07); an untouched flag has nothing to confirm. `pressed` holds what each press asked for and when,
 * and a press counts only while the line reads that: a cancelled check sheet or a refused write changed nothing.
 */
export function flagShowsBadge(line: Pick<IrrigationLine, "sync" | "vpd_member" | "schedule_member">, flag: MisterFlag, pressed: ReadonlyMap<MisterFlag, { readonly on: boolean; readonly at: number }>, now = Date.now(), engine = true): boolean {
  const changed = (item: MisterFlag) => pressed.has(item) && pressed.get(item)!.on === line[item];
  // Before the engine every flag waits for the firmware: only a press made here carries its pill.
  if (!engine) return changed(flag);
  if (line.sync !== "confirmed") return changed(flag) || !FLAGS.some((item) => changed(item.flag));
  return changed(flag) && now - pressed.get(flag)!.at < CONFIRMED_FRESH_MS;
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

export type MisterControlsProps = {
  /** L04 center, L07 south or L09 west. */
  line: IrrigationLine;
  /** API-IRR-01's `engine` and `program`, for the sync words. */
  status: Pick<IrrigationStatus, "engine" | "program">;
};

/**
 * A mister zone's head controls (redesign §5.2, irrigation §4.4): "VPD control" and "Schedule", switches for an operator and
 * ON or OFF for a viewer, each with its own pill while it settles (REQUESTED until the controller reports the program
 * that carries it, then CONFIRMED for 10 minutes), which reading VPD control uses (D-IRP-13), and "This zone will not mist
 * automatically" with both off. The zone's state, fault and Clear fault are its mister's row.
 */
export function MisterControls({ line, status }: MisterControlsProps) {
  const operator = useRole() === "operator";
  const now = useNow();
  const [pressed, setPressed] = useState<ReadonlyMap<MisterFlag, { on: boolean; at: number }>>(() => new Map());
  // A press once the line has settled starts afresh, so a change confirmed earlier does not read REQUESTED again.
  const press = (flag: MisterFlag, on: boolean) => setPressed((current) => new Map(line.sync === "confirmed" ? [] : current).set(flag, { on, at: Date.now() }));
  // A change confirmed while watched restarts its 10 minutes from the confirmation.
  const last = useRef(line.sync);
  useEffect(() => {
    if (last.current !== "confirmed" && line.sync === "confirmed") setPressed((current) => new Map([...current].map(([flag, item]) => [flag, { ...item, at: Date.now() }])));
    last.current = line.sync;
  }, [line.sync]);
  const syncWords = line.sync !== "confirmed" && status.engine ? lineSyncWords(line.sync, status) : null;
  const source = vpdSourceWords(line.vpd_source);
  return <div className="mister-zone" data-line={line.line_id}>
    {operator
      ? <div className="mister-flags mister-switches">{FLAGS.map(({ flag, label }) => <FlagSwitch key={flag} line={line} flag={flag} label={label} badge={flagShowsBadge(line, flag, pressed, now, status.engine)} onPress={press} />)}</div>
      : <dl className="kv mister-flags">{FLAGS.map(({ flag, label }) => <Fragment key={flag}>
        <dt>{label}</dt><dd><Flag on={line[flag]} />{flagShowsBadge(line, flag, pressed, now, status.engine) && <> <ReadbackBadge state={line.sync} /></>}</dd>
      </Fragment>)}</dl>}
    <p className="mister-source meta">{source ?? <>VPD source —{!status.engine && <span> · arrives with the irrigation firmware</span>}</>}</p>
    {syncWords && <p className="meta mister-sync">{syncWords}</p>}
    {line.vpd_member === false && line.schedule_member === false && <p className="mister-off">{WILL_NOT_MIST_TEXT}</p>}
  </div>;
}
