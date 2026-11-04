// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { isIntentKey } from "../../charts/equipment-lanes";
import { formatValue } from "../../lib/format";
import { LINES, lineByEquipment } from "../../lib/lines";
import type { Equipment } from "../../lib/operator-api";
import { useApiDown } from "../../lib/session";
import { cls } from "../common";
import { Panel, PanelError, type PanelState } from "../panel";
import { LightControls, lightsDiffer, type LightControl } from "./light-controls";
import { Ago, Since } from "./report-times";

/**
 * The equipment board (SCR-07, redesign §5.12): every actuator from API-13 in fixed groups, each row its name, its state
 * as a word with a dot (ON with a dot in the equipment's token, OFF plain, UNKNOWN muted with a hatched dot), then
 * since, time on today and cycles. Every state, since, runtime, cycle count and unknown is the API's; the board never
 * collapses edges, counts cycles or decides from an age that a state is unknown.
 */
export type EquipmentItem = Equipment["items"][number];
export type BoardGroup = { readonly group: "climate" | "water" | "light"; readonly title: string; readonly keys: readonly string[]; readonly reserve: "sm" | "md" | "lg" };

/**
 * The board's groups and rows in their fixed order, whatever order API-13 sends: climate, the water relays as L01–L11 in
 * line order, and the two grow lights. Each row reads the label the API serves (`EQUIPMENT_LABELS`); none is written here.
 */
export const BOARD_GROUPS: readonly BoardGroup[] = [
  { group: "climate", title: "Climate", keys: ["fan1", "fan2", "vent", "heat1", "heat2"], reserve: "md" },
  { group: "water", title: "Water relays", keys: LINES.map((line) => line.equipment), reserve: "lg" },
  { group: "light", title: "Lights", keys: ["grow_light_main", "grow_light_grow"], reserve: "sm" },
];
const FIXED = new Set(BOARD_GROUPS.flatMap((group) => group.keys));
export const boardGroup = (group: BoardGroup["group"]): BoardGroup => BOARD_GROUPS.find((item) => item.group === group)!;

/** One board row: a key and what API-13 says of it, null when the API omits it. */
export type BoardRow = { readonly key: string; readonly item: EquipmentItem | null };
/** The group's fixed rows, then any other item the API puts in the group (never a controller `_intent` key). */
export function boardRows(group: BoardGroup, items: readonly EquipmentItem[]): BoardRow[] {
  const byKey = new Map(items.map((item) => [item.equipment, item]));
  const extra = [...byKey.values()].filter((item) => item.group === group.group && !FIXED.has(item.equipment) && !isIntentKey(item.equipment));
  return [...group.keys.map((key) => ({ key, item: byKey.get(key) ?? null })), ...extra.map((item) => ({ key: item.equipment, item }))];
}

export const known = (state: string | null | undefined): state is "on" | "off" => state === "on" || state === "off";

const LINE_TOKENS: Readonly<Record<string, string>> = { drip: "eq-water", fert_drip: "eq-fert", master: "eq-master", mister: "eq-mister", fert_mister: "eq-fert", fog: "eq-fog" };
/** The equipment's own colour token (design system §5.2), for its ON dot; null for one the build does not know. */
export function equipmentToken(equipment: string): string | null {
  if (["fan1", "fan2", "vent", "heat1", "heat2"].includes(equipment)) return `eq-${equipment}`;
  if (equipment === "grow_light_main") return "eq-light-main";
  if (equipment === "grow_light_grow") return "eq-light-grow";
  const line = lineByEquipment(equipment);
  return line ? LINE_TOKENS[line.kind] ?? null : null;
}

/**
 * ON, OFF or UNKNOWN exactly as the API sends it, as a word with a dot (redesign §4.4): ON's dot is the equipment's
 * token, OFF has a quiet ring, UNKNOWN a hatched dot and muted words. The vent's on and off read OPEN and CLOSED. While
 * the dashboard API is down the dot is neutral. `data-badge` keeps the board's state hook.
 */
export function StateWord({ equipment, state }: { equipment: string; state: string }) {
  const apiDown = useApiDown();
  const vent = equipment === "vent";
  const word = state === "on" ? (vent ? "Open" : "On") : state === "off" ? (vent ? "Closed" : "Off") : "Unknown";
  const kind = state === "on" || state === "off" ? state : "unknown";
  return <span className={cls("ctl-state", `is-${kind}`, apiDown && "is-neutral")} data-badge={kind}>
    <span className="ctl-dot" data-token={kind === "on" ? equipmentToken(equipment) ?? undefined : undefined} aria-hidden="true" />{word}
  </span>;
}

const runtime = (seconds: number | null | undefined) => (seconds === null || seconds === undefined ? "—" : formatValue(seconds / 60, "min"));

/**
 * API-13 reads each actuator's edges over this many days (`STATE_WINDOW` in verdify_services/reads.py, #1167): a known
 * state with a null `since` has not changed in that window, so the start of its run is older than the API looks.
 */
export const SINCE_WINDOW_DAYS = 31;
/** A null since with its reason: a known state has not changed in API-13's window; an unknown one says why in its head. */
const noSince = (state: string) => (known(state) ? `— (no change in ${SINCE_WINDOW_DAYS} days)` : "—");

/**
 * Since, time on today and cycles today; for a polled readback (the lights) also the report's age, unless the state
 * says it already. A relay reports on change, so its since is its newest report. A null value is "—".
 */
function Facts({ item, age }: { item: EquipmentItem; age: boolean }) {
  const parts: ReactNode[] = [
    <>since {item.since ? <Since at={item.since} /> : noSince(item.state)}</>,
    <>{item.equipment === "vent" ? "open" : "on"} today {runtime(item.runtime_today_s)}</>,
    <>cycles {item.cycles_today ?? "—"}</>,
  ];
  if (age) parts.push(item.observed_at ? <>reported <Ago at={item.observed_at} /></> : "no data");
  return <p className="equipment-facts">{parts.map((part, index) => <span key={index}>{index > 0 && " · "}{part}</span>)}</p>;
}

/** A relay: its state, and while the API says unknown, the age of its last report beside it, never OFF. */
function RelayRow({ item }: { item: EquipmentItem }) {
  const unknown = !known(item.state);
  return <li className="equipment-row" data-equipment={item.equipment} data-state={item.state}>
    <span className="equipment-name">{item.label}</span>
    <span className="equipment-state">
      <StateWord equipment={item.equipment} state={item.state} />
      {unknown && <> · {item.observed_at ? <>last report <Ago at={item.observed_at} /></> : "no data"}</>}
    </span>
    <Facts item={item} age={false} />
  </li>;
}

/**
 * A grow light: the controller's intent beside the Lutron readback, and an amber "differ" marker in words when both are
 * known and differ. An unknown Lutron state reads with its age (Home Assistant down) and never marks a difference; an
 * intent the API does not serve yet reads "—". The controls are held above the board (`useLightControl`).
 */
function LightRow({ item, control, settingTo }: { item: EquipmentItem; control?: LightControl; settingTo?: string }) {
  const unknown = !known(item.state);
  const intent = item.intent === null || item.intent === undefined
    ? <span title="The controller has not reported its intent">—</span>
    : <StateWord equipment={item.equipment} state={item.intent} />;
  return <li className="equipment-row equipment-row-light" data-equipment={item.equipment} data-state={item.state}>
    <span className="equipment-name">{item.label}</span>
    <span className="equipment-state">
      {intent} (controller) · Lutron <StateWord equipment={item.equipment} state={item.state} />
      {unknown && <> · {item.observed_at ? <Ago at={item.observed_at} /> : "no data"}</>}
      {lightsDiffer(item.intent, item.state) && <> <span className="tag tag-warn" data-differ="true" title="The controller's intent and the Lutron readback differ"><span aria-hidden="true">▲ </span>Differ</span></>}
    </span>
    <Facts item={item} age={!unknown} />
    {control && <LightControls control={control} item={item} settingTo={settingTo} />}
  </li>;
}

/** An actuator API-13 did not send: "—" with "no data", never off, under its key (no label is made up). */
function MissingRow({ equipment }: { equipment: string }) {
  return <li className="equipment-row" data-equipment={equipment} data-state="missing">
    <span className="equipment-name equipment-key">{equipment}</span>
    <span className="equipment-state">—</span>
    <p className="equipment-facts">no data</p>
  </li>;
}

export type GroupPanelProps = {
  group: BoardGroup;
  items: readonly EquipmentItem[];
  state: PanelState;
  onRetry?: () => void;
  lights?: readonly LightControl[];
  /** The automatic switch's drawer for a light (`?edit=sw_gl_<light>_auto_mode`). */
  settingTo?: (param: string) => string;
};

/** One group of the board as its card, a skeleton until API-13 first answers. */
export function GroupPanel({ group, items, state, onRetry, lights, settingTo }: GroupPanelProps) {
  return <Panel title={group.title} thing={`${group.group} equipment`} state={state} reserve={group.reserve} onRetry={onRetry}
    foot={group.group === "light" ? <span className="equipment-note">(controller) is the state the controller set; Lutron is the state the switch reports (sensed). Since, time on and cycles follow the Lutron readback.</span> : undefined}>
    <ul className={cls("equipment-rows", `equipment-rows-${group.group}`)}>
      {boardRows(group, items).map(({ key, item }) => {
        if (item === null) return <MissingRow key={key} equipment={key} />;
        if (group.group !== "light") return <RelayRow key={key} item={item} />;
        const control = lights?.find((light) => light.equipment === key);
        return <LightRow key={key} item={item} control={control} settingTo={control && settingTo ? settingTo(control.autoParam) : undefined} />;
      })}
    </ul>
  </Panel>;
}

/** A failed API-13 read in place of the board's three groups: one "Couldn't load equipment." and Retry. */
export function BoardError({ onRetry }: { onRetry?: () => void }) {
  return <section className="card equipment-error" aria-label="Equipment board"><PanelError thing="equipment" onRetry={onRetry} /></section>;
}

/** The three groups, stacked (the route lays them out in two columns with the flags and the controller). */
export function EquipmentGroups({ items, state, onRetry, lights }: { items: readonly EquipmentItem[]; state: PanelState; onRetry?: () => void; lights?: readonly LightControl[] }) {
  if (state === "error") return <BoardError onRetry={onRetry} />;
  return <div className="equipment-board">
    {BOARD_GROUPS.map((group) => <GroupPanel key={group.group} group={group} items={items} state={state} onRetry={onRetry} lights={lights} />)}
  </div>;
}
