// SPDX-License-Identifier: MIT
import { useMemo } from "react";
import { useSearchParams } from "react-router";
import { EquipmentLanes } from "../charts/equipment-lanes";
import { RANGES, RangeChips, type RangeId } from "../charts/range-chips";
import { epochOf } from "../charts/time-axis";
import type { StateInterval, StateLane, StateLanes as LaneWindow } from "../charts/types";
import { chartLinks } from "../charts/uplot-chart";
import { BurstPanel } from "../components/burst-panel";
import { FindSetting } from "../components/control/find-setting";
import { SettingDrawerHost, useSettingEdit } from "../components/control/setting-drawer";
import { BoardError, GroupPanel, boardGroup } from "../components/equipment/board";
import { ControllerCard, ControllerStatus } from "../components/equipment/controller-card";
import { DailyRuntime, localToday } from "../components/equipment/daily-runtime";
import { FlagsCard } from "../components/equipment/flags";
import { LIGHTS, useLightControl } from "../components/equipment/light-controls";
import { WhyPanel } from "../components/equipment/why-panel";
import { useClockValue } from "../components/freshness";
import { PageHeader } from "../components/layout/page-header";
import { Panel, type PanelState } from "../components/panel";
import type { Setting, StateLanes } from "../lib/operator-api";
import { useEquipmentRead, useSettingsRead, useStatesWindowRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";

export { BOARD_GROUPS, EquipmentGroups, SINCE_WINDOW_DAYS, boardRows, type EquipmentItem } from "../components/equipment/board";
export { lightsDiffer } from "../components/equipment/light-controls";

/**
 * SCR-07 Equipment (redesign §5.12): what each actuator and the controller is doing, why, and how much it has run. The
 * header states the controller (uptime, firmware, Wi-Fi, age) and holds "Find a setting". Two balanced columns from
 * 861 px: Climate, Lights (Lutron beside the controller's intent, with the light controls) and Flags on the left; the
 * water relays and the Controller card on the right. Then, full width: the bursts (the quick actions Now shares, with
 * Restart controller under More actions), Why, the equipment lanes over the last 24 h or 72 h (`?range=`) and the daily
 * runtime of 14 days. The board and Why share one API-13 read every 15 s; the lanes read API-05 every 60 s. A light's
 * automatic switch opens its setting drawer in place (`?edit=`). Only operators see the controls. No greenhouse data
 * renders here or in SSR: data loads in the browser.
 */
export const meta = () => viewMeta("control-equipment");
export const links = () => chartLinks();

/** A read as its panel shows it (screens §1.5): a skeleton until the first answer, its own error while the newest read failed. */
export function readState(query: { readonly data: unknown; readonly error: unknown }): PanelState {
  if (query.error) return "error";
  return query.data === undefined ? "loading" : "ready";
}

const settingOf = (items: readonly Setting[] | undefined, param: string) => items?.find((row) => row.param === param);

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
    next.delete("edit"); next.delete("hist");
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

// ---------------------------------------------------------------------------------------------------- the page
export default function Equipment() {
  const equipment = useEquipmentRead();
  // The lights' automatic switches, from API-09: while its newest read fails they read as not known, never their last values.
  const settings = useSettingsRead();
  const rows = settings.error ? undefined : settings.data?.items;
  const [main, grow] = LIGHTS;
  // Held here, above the board, so a failed API-13 read loses no answer and no resync of a light command in progress.
  const lights = [useLightControl(main, settingOf(rows, main.autoParam)), useLightControl(grow, settingOf(rows, grow.autoParam))];
  const { editTo } = useSettingEdit();
  const today = useClockValue(localToday);
  const state = readState(equipment);
  const retry = () => void equipment.refetch();
  const items = equipment.data?.items ?? [];
  const board = (group: "climate" | "water" | "light") => <GroupPanel group={boardGroup(group)} items={items} state={state} onRetry={retry} lights={lights} settingTo={editTo} />;
  return <>
    <PageHeader view="control-equipment" status={<ControllerStatus />} statusShort={<ControllerStatus short />} actions={<FindSetting />} />
    <div className="page ctl-equipment">
      <div className="ctl-board equipment-board">
        <div className="ctl-board-col">
          {state === "error" ? <BoardError onRetry={retry} /> : <>{board("climate")}{board("light")}</>}
          <FlagsCard today={today} />
        </div>
        <div className="ctl-board-col">
          {state !== "error" && board("water")}
          <ControllerCard />
        </div>
      </div>
      <div className="ctl-equipment-more">
        <BurstPanel />
        <WhyPanel equipment={equipment.data} state={state} onRetry={retry} />
        <LanesPanel />
        <DailyRuntime today={today} />
      </div>
    </div>
    <SettingDrawerHost />
  </>;
}
