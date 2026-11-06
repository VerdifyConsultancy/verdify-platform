// SPDX-License-Identifier: MIT
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { lineDisplayName } from "../lib/lines";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import { useRole } from "../lib/session";
import { lineSyncWords, useWaterDrawer, vpdSourceWords } from "../lib/water";
import { useProgramWrite } from "../lib/writes";
import { Badge, ReadbackBadge } from "./badge";
import { FieldError, Switch, describedBy, useFieldIds } from "./forms";
import { useNow } from "./freshness";
import { InfoTip } from "./info-tip";
import { CONFIRMED_FRESH_MS } from "./schedule/rules-list";

/** Both flags off (irrigation §4.4). */
export const WILL_NOT_MIST_TEXT = "This mister will not mist automatically";

/** The mister rank the arbiter uses by default (`zone_priority_*`, firmware globals.yaml): center 1, south 2, west 3. */
const RANKED = ["L04", "L07", "L09"];
const whole = (value: number | null | undefined) => (typeof value === "number" && Number.isFinite(value) ? String(Math.round(value)) : null);

/**
 * What VPD control does to the controller (#1344; irrigation §4.4, from the firmware's `irr_vpd_zone_allowed`,
 * `mister_blocked` and `arbitrate_wet_zone`), in plain words with this mister's daily cap and the site's daily mist & fog
 * limit from API-IRR-01. Paragraphs, for the info tip.
 */
export function vpdControlHelp(line: Pick<IrrigationLine, "line_id" | "daily_cap_min" | "vpd_reserve_min">, saf4Max: number | null | undefined): string[] {
  const cap = line.daily_cap_min > 0 ? `its ${line.daily_cap_min} min daily cap` : "its daily minutes";
  const limit = whole(saf4Max) ? `the ${whole(saf4Max)} gal daily mist & fog limit` : "the daily mist & fog limit";
  const order = line.line_id === RANKED[0]
    ? "Only one mister runs at a time. Center goes first, unless South or West also has VPD control on and its own zone is drier than its target."
    : "Only one mister runs at a time. Center goes first, unless this mister's own zone is drier than its target, or Center is capped or faulted.";
  const probe = line.line_id === RANKED[0] ? null
    : "It reads this zone's own humidity probe; while that probe is offline, the other probes, then the house average.";
  return [
    "When the greenhouse needs humidity and the climate safety checks pass, the controller may send short mist pulses to this mister.",
    order,
    `Pulses count toward ${cap} and ${limit}. Misting waits while someone is inside, a vent is open or a drip runs.`,
    ...(probe ? [probe] : []),
    "The line has to be on: a mister that is off never mists, whatever this switch says.",
  ];
}

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
function FlagSwitch({ line, flag, label, badge, onPress, help }: { line: IrrigationLine; flag: MisterFlag; label: string; badge: boolean; onPress: (flag: MisterFlag, on: boolean) => void; help?: ReactNode }) {
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
      : <Switch write label={<>{label} <span className="screen-reader">{lineDisplayName(line)}</span></>} checked={value} busy={write.sending} onChange={change} describedBy={describedBy(write.error && ids.errorId)} />}
    {help}
    {badge && <ReadbackBadge state={line.sync} />}
    {write.error && <FieldError id={ids.errorId}>{write.error.message}</FieldError>}
  </div>;
}

export type MisterControlsProps = {
  /** L04 center, L07 south or L09 west. */
  line: IrrigationLine;
  /** API-IRR-01's `engine` and `program`, for the sync words, and its `limits` for the help's daily limit. */
  status: Pick<IrrigationStatus, "engine" | "program"> & Partial<Pick<IrrigationStatus, "limits">>;
};

/**
 * A mister's own controls, on its clean row and nowhere else (redesign §5.2, irrigation §4.4, #1344): "VPD control" with
 * an info tip saying what it does to the controller, and "Schedule"; switches for an operator and ON or OFF for a viewer,
 * each with its own pill while it settles (REQUESTED until the controller reports the program that carries it, then
 * CONFIRMED for 10 minutes), which reading VPD control uses (D-IRP-13), and "This mister will not mist automatically"
 * with both off. On a disabled mister VPD control is no switch but words: the controller ignores it there, so it never
 * reads a false ON; the API refuses it too (`line_disabled`).
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
  const name = lineDisplayName(line);
  const help = <InfoTip term={`VPD control on ${name}`}>{vpdControlHelp(line, status.limits?.saf4_gal.max).map((text) => <p key={text}>{text}</p>)}</InfoTip>;
  // A disabled mister ignores VPD control (the firmware's effective membership needs the enable bit): words, no switch.
  const off = line.enabled === false;
  const offWords = <div className="mister-flag" data-flag="vpd_member" data-off="">
    <span className="field-label">VPD control</span> <span>{line.vpd_member ? "set, but the line is off: no VPD misting" : "off while the line is off"}</span>{help}
  </div>;
  return <div className="mister-zone" data-line={line.line_id}>
    {operator
      ? <div className="mister-flags mister-switches">{FLAGS.map(({ flag, label }) => (flag === "vpd_member" && off ? <Fragment key={flag}>{offWords}</Fragment>
        : <FlagSwitch key={flag} line={line} flag={flag} label={label} badge={flagShowsBadge(line, flag, pressed, now, status.engine)} onPress={press} help={flag === "vpd_member" ? help : undefined} />))}</div>
      : <dl className="kv mister-flags">{FLAGS.map(({ flag, label }) => <Fragment key={flag}>
        <dt>{label}{flag === "vpd_member" && help}</dt><dd>{flag === "vpd_member" && off ? (line.vpd_member ? "set, but the line is off" : "off") : <Flag on={line[flag]} />}{flagShowsBadge(line, flag, pressed, now, status.engine) && <> <ReadbackBadge state={line.sync} /></>}</dd>
      </Fragment>)}</dl>}
    {off && operator && <p className="mister-help">Turn {name} on (⋯ → Enabled) to use VPD control.</p>}
    <p className="mister-source meta">{source ?? <>VPD source —{!status.engine && <span> · arrives with the irrigation firmware</span>}</>}</p>
    {syncWords && <p className="meta mister-sync">{syncWords}</p>}
    {line.vpd_member === false && line.schedule_member === false && <p className="mister-off">{WILL_NOT_MIST_TEXT}</p>}
  </div>;
}
