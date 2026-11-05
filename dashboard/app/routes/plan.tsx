// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { PlanLanes, type PlanRow } from "../charts/plan-lanes";
import { chartLinks } from "../charts/uplot-chart";
import { Chips } from "../components/chips";
import { LocalTime } from "../components/common";
import { FindSetting } from "../components/control/find-setting";
import { Mark, PillBadge, useRecentWrite, type SettingPill } from "../components/control/marks";
import { SettingDrawerHost, useSettingEdit } from "../components/control/setting-drawer";
import { useServerClockSkew } from "../components/freshness";
import { PageHeader } from "../components/layout/page-header";
import { Panel, type PanelState } from "../components/panel";
import { PlanCard, otherDay, timeLeft } from "../components/plan-card";
import { PlannerHealth, useIrisReliability } from "../components/planner-health";
import type { DesiredSetting, Plan as PlanAnswer } from "../lib/operator-api";
import { useApiRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { useShellNow } from "../lib/shell";

export const meta = () => viewMeta("control-plan");
export const links = () => chartLinks();

/** API-07 every 60 s (architecture §3), every 5 s while a lane's newest write still reads REQUESTED; paused while hidden. */
export const PLAN_POLL_MS = 60_000;
/** What applies once the plan in force ends, as the lanes mark it. */
export const TAKEOVER = "band/default";

/** The lane window from the URL: `?hours=72` is 72 h, anything else the default 24 h. */
export function planHours(search: URLSearchParams): 24 | 72 {
  return search.get("hours") === "72" ? 72 : 24;
}

/** "1 setting MANUAL · Iris skips it", "3 settings MANUAL · Iris skips them"; nothing when none is manual. */
export function manualCount(count: number): string | null {
  if (!Number.isFinite(count) || count <= 0) return null;
  return count === 1 ? "1 setting MANUAL · Iris skips it" : `${count} settings MANUAL · Iris skips them`;
}

/** A lane's newest write is still on its way to the controller: the read then settles every 5 s. */
export function planSettling(plan: PlanAnswer): boolean {
  return plan.lanes.some((lane) => lane.desired?.state === "requested" && lane.desired.state_reason?.code !== "no_engine");
}

/**
 * The status line (redesign §5.11): "SUNRISE plan · written 07:02 · valid to 17:30 (6 h 48 min left) · AI", or what
 * applies with no plan in force; null until API-07 answers, so the line keeps its height.
 */
export function PlanStatus({ data, now }: { data: PlanAnswer | null; now: number }): ReactNode {
  if (!data) return null;
  const plan = data.plan;
  if (!plan) {
    const since = data.no_plan_since ?? null;
    return <>No plan in force · band and defaults apply{since && <> since <LocalTime at={since} date={otherDay(since, now)} /></>}</>;
  }
  const until = plan.valid_until ?? null;
  const left = until ? timeLeft(until, now) : null;
  return <>
    <span className="mono">{plan.trigger}</span> plan · written <LocalTime at={plan.written_at} date={otherDay(plan.written_at, now)} />
    {until && <> · valid to <LocalTime at={until} date={otherDay(until, now)} />{left && <> ({left})</>}</>} · <Mark kind="ai" />
  </>;
}

/** The pill a lane's newest write earns (redesign §4.4): REQUESTED, NOT CONFIRMED or HELD, CONFIRMED for 10 minutes. */
function LanePill({ desired }: { desired: DesiredSetting | null }) {
  const state = desired?.state ?? null;
  const recent = useRecentWrite(state === "confirmed" ? desired?.created_at : null);
  const pill: SettingPill | null = state === "requested" ? "requested" : state === "not_confirmed" ? "not_confirmed" : state === "held" ? "held"
    : state === "confirmed" && recent ? "confirmed" : null;
  return pill ? <span className="ctl-lane-pill"><PillBadge pill={pill} /></span> : null;
}

/**
 * A lane's label opens its setting drawer here, in place (redesign §5.11, D-UX-02): the link lies over the lane's head,
 * where PlanLanes prints the label, and the lane's newest write shows its one pill at the head's end. The same link
 * for every role; a viewer's drawer is read-only.
 */
function LaneEnd({ row, to, desired }: { row: PlanRow; to: string; desired: DesiredSetting | null }) {
  return <>
    <Link className="ctl-lane-link" to={to} preventScrollReset><span className="screen-reader">{row.label}</span></Link>
    <LanePill desired={desired} />
  </>;
}

/**
 * SCR-03 Plan (redesign §5.11): what Iris will tell the controller, whether Iris is healthy and how to override it. The
 * Iris plan in force with its validity bar, planner health with the 36 h marks and the 14-day strip, then one lane per
 * planned setting for the next 24 h (72 h on a chip, `?hours=72`), all from one API-07 read. A lane's label opens the
 * setting drawer here (`?edit=<param>`), the only way Plan writes. Device staleness is the shell's API-02
 * `freshness.readback.stale`, never a second poll. API-07 failing shows Retry in each panel and keeps no earlier answer on
 * screen. No greenhouse data renders in SSR.
 */
export default function Plan() {
  const [search] = useSearchParams();
  const hours = planHours(search);
  const read = useApiRead("API-07", { hours }, { intervalMs: PLAN_POLL_MS, settling: planSettling });
  const shellNow = useShellNow();
  const skew = useServerClockSkew();
  const reliability = useIrisReliability();
  const { editTo } = useSettingEdit();
  // While the newest read failed, the last answer is not shown: nothing on the page may read as current.
  const data = read.isError ? null : read.data ?? null;
  const state: PanelState = data ? "ready" : read.isError ? "error" : "loading";
  const retry = () => { void read.refetch(); };
  // Everything reads as of the answer, on the server's clock as every age does: the lanes start there, and the time
  // left and lateness count from it, so a device clock running ahead never marks a trigger late too early.
  const now = read.dataUpdatedAt - skew;
  const stale = shellNow.data?.freshness.readback.stale === true;
  const manual = data ? manualCount(data.manual_count) : null;
  const desired = new Map((data?.lanes ?? []).map((lane) => [lane.param, lane.desired]));
  const hoursTo = (value: 24 | 72) => {
    const next = new URLSearchParams(search);
    if (value === 72) next.set("hours", "72"); else next.delete("hours");
    next.delete("edit"); next.delete("hist");
    const query = next.toString();
    return query ? `/plan?${query}` : "/plan";
  };
  const chips = [
    { key: "24", label: "24 h", to: hoursTo(24), current: hours === 24, keepScroll: true },
    { key: "72", label: "72 h", to: hoursTo(72), current: hours === 72, keepScroll: true },
  ];
  return <>
    <PageHeader view="control-plan" status={<PlanStatus data={data} now={now} />} actions={<FindSetting />} />
    <div className="page ctl-plan">
      <div className="l-cols-2 ctl-plan-top">
        <PlanCard data={data} state={state} now={now} onRetry={retry} />
        <PlannerHealth health={data?.health ?? []} state={state} now={now} onRetry={retry} reliability={reliability} />
      </div>
      <Panel title="Planned settings" thing="the planned settings" state={state} reserve="lg" onRetry={retry}
        actions={<div className="plan-lane-tools">
          {manual && <p className="plan-manual">{manual}</p>}
          <Chips kind="nav" small label="Lane window" items={chips} />
        </div>}>
        {data && <div className="ctl-plan-lanes">
          <PlanLanes plan={data} now={Math.floor(now / 1000)} hours={hours} takeover={TAKEOVER} stale={stale} syncKey={`verdify:plan:${hours}h`}
            rowEnd={(row) => <LaneEnd row={row} to={editTo(row.key)} desired={desired.get(row.key) ?? null} />} />
        </div>}
      </Panel>
    </div>
    <SettingDrawerHost />
  </>;
}
