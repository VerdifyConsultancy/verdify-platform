// SPDX-License-Identifier: MIT
import { useMemo, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { EquipmentLanes, isIntentKey } from "../charts/equipment-lanes";
import { RANGES, RangeChips, type RangeId } from "../charts/range-chips";
import { epochOf } from "../charts/time-axis";
import type { StateInterval, StateLane, StateLanes as LaneWindow } from "../charts/types";
import { chartLinks } from "../charts/uplot-chart";
import { Badge } from "../components/badge";
import { BurstPanel } from "../components/burst-panel";
import { LIGHTS, LightControls, lightsDiffer, useLightControl, type LightControl } from "../components/equipment/light-controls";
import { Ago, Since } from "../components/equipment/report-times";
import { WhyPanel } from "../components/equipment/why-panel";
import { PageHeader } from "../components/layout/page-header";
import { Panel, PanelError, type PanelState } from "../components/panel";
import { formatValue } from "../lib/format";
import { LINES } from "../lib/lines";
import type { Equipment as EquipmentAnswer, Setting, StateLanes } from "../lib/operator-api";
import { useEquipmentRead, useSettingsRead, useStatesWindowRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";

/**
 * SCR-07 Equipment (#1134): the bursts and Restart controller (#1136), the board of every actuator from API-13, polled
 * every 15 s, the grow lights' controls and the Why panel from that same read (#1140), and the equipment lanes from API-05
 * over the last 24 h or 72 h (`?range=`), polled every 60 s. The board and the lanes read the same for every role, and only
 * operators see the controls. Every state, since, runtime, cycle count and unknown is the API's; the page never collapses
 * edges, counts cycles or decides from an age that a state is unknown. No greenhouse data renders here or in SSR: data
 * loads in the browser.
 */
export const meta = () => viewMeta("control-equipment");
export const links = () => chartLinks();

export type EquipmentItem = EquipmentAnswer["items"][number];
type BoardGroup = { readonly group: "climate" | "water" | "light"; readonly title: string; readonly keys: readonly string[]; readonly reserve: "sm" | "md" | "lg" };

/**
 * The board's groups and rows in their fixed order, whatever order API-13 sends: climate, water as L01–L11 in line
 * order, and the two grow lights. Each row reads the label the API serves (`EQUIPMENT_LABELS`); none is written here.
 */
export const BOARD_GROUPS: readonly BoardGroup[] = [
  { group: "climate", title: "Climate", keys: ["fan1", "fan2", "vent", "heat1", "heat2"], reserve: "md" },
  { group: "water", title: "Water", keys: LINES.map((line) => line.equipment), reserve: "lg" },
  { group: "light", title: "Light", keys: ["grow_light_main", "grow_light_grow"], reserve: "sm" },
];
const FIXED = new Set(BOARD_GROUPS.flatMap((group) => group.keys));

/** One board row: a key and what API-13 says of it, null when the API omits it. */
export type BoardRow = { readonly key: string; readonly item: EquipmentItem | null };
/** The group's fixed rows, then any other item the API puts in the group (never a controller `_intent` key). */
export function boardRows(group: BoardGroup, items: readonly EquipmentItem[]): BoardRow[] {
  const byKey = new Map(items.map((item) => [item.equipment, item]));
  const extra = [...byKey.values()].filter((item) => item.group === group.group && !FIXED.has(item.equipment) && !isIntentKey(item.equipment));
  return [...group.keys.map((key) => ({ key, item: byKey.get(key) ?? null })), ...extra.map((item) => ({ key: item.equipment, item }))];
}

const known = (state: string | null | undefined): state is "on" | "off" => state === "on" || state === "off";
export { lightsDiffer };

/** A read as its panel shows it (screens §1.5): a skeleton until the first answer, its own error while the newest read failed. */
export function readState(query: { readonly data: unknown; readonly error: unknown }): PanelState {
  if (query.error) return "error";
  return query.data === undefined ? "loading" : "ready";
}

// ---------------------------------------------------------------------------------------------------- the board
/** ON, OFF or UNKNOWN exactly as the API sends it; the vent's on and off read OPEN and CLOSED. */
function StateBadge({ equipment, state }: { equipment: string; state: string }) {
  if (state === "on") return <Badge kind="on">{equipment === "vent" ? "Open" : "On"}</Badge>;
  if (state === "off") return <Badge kind="off">{equipment === "vent" ? "Closed" : "Off"}</Badge>;
  return <Badge kind="unknown" />;
}

const runtime = (seconds: number | null | undefined) => (seconds === null || seconds === undefined ? "—" : formatValue(seconds / 60, "min"));

/**
 * API-13 reads each actuator's edges over this many days (`STATE_WINDOW` in verdify_services/reads.py, #1167): a known
 * state with a null `since` has not changed in that window, so the start of its run is older than the API looks.
 */
export const SINCE_WINDOW_DAYS = 31;
/** A null since with its reason: a known state has not changed in API-13's window; an unknown one says why in its head. */
const noSince = (state: string) => (known(state) ? `— (no change in ${SINCE_WINDOW_DAYS} days)` : "—");

/** Since, time on today, cycles today and, unless the head already says it, the report's age. A null value is "—". */
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
    <div className="equipment-head">
      <span className="equipment-name">{item.label}</span>
      <span className="equipment-state">
        <StateBadge equipment={item.equipment} state={item.state} />
        {unknown && <> · {item.observed_at ? <>last report <Ago at={item.observed_at} /></> : "no data"}</>}
      </span>
    </div>
    <Facts item={item} age={!unknown} />
  </li>;
}

/**
 * A grow light: the controller's intent beside the Lutron readback, and an amber "differ" marker in words when both are
 * known and differ. An unknown Lutron state reads with its age (Home Assistant down) and never marks a difference; an
 * intent the API does not serve yet reads "—". The controls are held above the board (`useLightControl`).
 */
function LightRow({ item, control }: { item: EquipmentItem; control?: LightControl }) {
  const unknown = !known(item.state);
  const intent = item.intent === null || item.intent === undefined
    ? <span title="The controller has not reported its intent">—</span>
    : <StateBadge equipment={item.equipment} state={item.intent} />;
  return <li className="equipment-row" data-equipment={item.equipment} data-state={item.state}>
    <div className="equipment-head">
      <span className="equipment-name">{item.label}</span>
      <span className="equipment-state">
        {intent} (controller) · Lutron <StateBadge equipment={item.equipment} state={item.state} />
        {unknown && <> · {item.observed_at ? <Ago at={item.observed_at} /> : "no data"}</>}
        {lightsDiffer(item.intent, item.state) && <> <span className="tag tag-warn" data-differ="true" title="The controller's intent and the Lutron readback differ"><span aria-hidden="true">▲ </span>Differ</span></>}
      </span>
    </div>
    <Facts item={item} age={!unknown} />
    {control && <LightControls control={control} item={item} />}
  </li>;
}

/** An actuator API-13 did not send: "—" with "no data", never off, under its key (no label is made up). */
function MissingRow({ equipment }: { equipment: string }) {
  return <li className="equipment-row" data-equipment={equipment} data-state="missing">
    <div className="equipment-head"><span className="equipment-name equipment-key">{equipment}</span><span className="equipment-state">—</span></div>
    <p className="equipment-facts">no data</p>
  </li>;
}

/**
 * The three groups in `.grid-3` (stacked on phones), each a skeleton until API-13 first answers. A failed read replaces
 * them with one "Couldn't load equipment." and Retry; the lanes keep working.
 */
export function EquipmentGroups({ items, state, onRetry, lights }: { items: readonly EquipmentItem[]; state: PanelState; onRetry?: () => void; lights?: readonly LightControl[] }) {
  if (state === "error") return <section className="card equipment-error" aria-label="Equipment board"><PanelError thing="equipment" onRetry={onRetry} /></section>;
  return <div className="grid grid-3 equipment-board">
    {BOARD_GROUPS.map((group) => <Panel key={group.group} title={group.title} thing={`${group.group} equipment`} state={state} reserve={group.reserve} onRetry={onRetry}>
      <ul className="equipment-rows">
        {boardRows(group, items).map(({ key, item }) => (item === null ? <MissingRow key={key} equipment={key} />
          : group.group === "light" ? <LightRow key={key} item={item} control={lights?.find((light) => light.equipment === key)} /> : <RelayRow key={key} item={item} />))}
      </ul>
      {group.group === "light" && <p className="small equipment-note">(controller) is the state the controller set; Lutron is the state the switch reports (sensed). Since, time on and cycles follow the Lutron readback.</p>}
    </Panel>)}
  </div>;
}

const settingOf = (items: readonly Setting[] | undefined, param: string) => items?.find((row) => row.param === param);

/**
 * The board and the Why panel on one API-13 poll, with the lights' controls held here: a failed read replaces the groups
 * with its error, and the answer and resync of a light command in progress outlive that. While API-09's newest read
 * fails, the automatic switches read as not known, never their last values as current.
 */
function EquipmentBoard() {
  const equipment = useEquipmentRead();
  const settings = useSettingsRead();
  const rows = settings.error ? undefined : settings.data?.items;
  const [main, grow] = LIGHTS;
  const lights = [useLightControl(main, settingOf(rows, main.autoParam)), useLightControl(grow, settingOf(rows, grow.autoParam))];
  const state = readState(equipment);
  const retry = () => void equipment.refetch();
  return <>
    <EquipmentGroups items={equipment.data?.items ?? []} state={state} onRetry={retry} lights={lights} />
    <div className="section"><WhyPanel equipment={equipment.data} state={state} onRetry={retry} /></div>
  </>;
}

// ---------------------------------------------------------------------------------------------------- the lanes
/**
 * API-05's answer in the chart layer's own copy of the schema (charts/types.ts, until #1130 re-exports the generated
 * types). The parser has already read every state and cause to a value the contract lists or to `unknown`, and a group
 * the lanes do not list sorts after theirs.
 */
export function laneWindow(data: StateLanes): LaneWindow {
  return {
    from: data.from, to: data.to,
    lanes: data.lanes.map((lane): StateLane => ({
      equipment: lane.equipment, label: lane.label, group: lane.group as StateLane["group"], line_id: lane.line_id,
      intervals: lane.intervals.map((span): StateInterval => ({ start: span.start, end: span.end, state: span.state as StateInterval["state"], cause: span.cause as StateInterval["cause"] })),
    })),
  };
}

const LANE_RANGES = ["24h", "72h"] as const satisfies readonly RangeId[];
/** `?range=`: 72h, else the default 24h. */
export const lanesRange = (value: string | null): (typeof LANE_RANGES)[number] => (value === "72h" ? "72h" : "24h");

/** "LAST 24 H" with its 24 h and 72 h chips over EquipmentLanes, drawn on the window API-05 answered for. */
function LanesPanel() {
  const [params] = useSearchParams();
  const range = RANGES.find((item) => item.id === lanesRange(params.get("range")))!;
  const states = useStatesWindowRead(range.seconds);
  const href = (id: RangeId) => {
    const next = new URLSearchParams(params);
    next.set("range", id);
    return `?${next.toString()}`;
  };
  const data = states.data;
  const lanes = useMemo(() => (data ? laneWindow(data) : null), [data]);
  const from = epochOf(data?.from), to = epochOf(data?.to);
  // An answer with no window to draw is as unusable as a failed read.
  const state = data && (from === null || to === null) ? "error" : readState(states);
  return <Panel title={`Last ${range.label}`} thing="equipment lanes" state={state} reserve="lg" observedAt={data?.to} onRetry={() => void states.refetch()}
    actions={<RangeChips only={LANE_RANGES} current={range.id} href={href} />}>
    {lanes && from !== null && to !== null && <EquipmentLanes states={lanes} xRange={{ from, to }} syncKey={`verdify:equipment:${range.id}`} now={to} />}
  </Panel>;
}

export default function Equipment() {
  return <>
    <PageHeader view="control-equipment" status="What the controller has each actuator doing now, since when, and today's time on and cycles." />
    <BurstPanel />
    <EquipmentBoard />
    <div className="section"><LanesPanel /></div>
  </>;
}
