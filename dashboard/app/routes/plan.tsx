// SPDX-License-Identifier: MIT
import { Link, useSearchParams } from "react-router";
import { PlanLanes, type PlanRow } from "../charts/plan-lanes";
import { chartLinks } from "../charts/uplot-chart";
import { Chips } from "../components/chips";
import { useServerClockSkew } from "../components/freshness";
import { PageHeader } from "../components/layout/page-header";
import { Panel, type PanelState } from "../components/panel";
import { PlanCard } from "../components/plan-card";
import { PlannerHealth } from "../components/planner-health";
import { useApiRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { useShellNow } from "../lib/shell";

export const meta = () => viewMeta("control-plan");
export const links = () => chartLinks();

/** API-07 every 60 s (architecture §3); `usePolledQuery` pauses it while the tab is hidden. */
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

/** A lane's Open: a plain link to its setting drawer on Settings (#1132), the same for viewers and operators. */
function OpenSetting({ row }: { row: PlanRow }) {
  return <Link to={`/settings?edit=${encodeURIComponent(row.key)}`}>Open{" "}<span className="screen-reader">{row.label}</span></Link>;
}
const openSetting = (row: PlanRow) => <OpenSetting row={row} />;

/**
 * SCR-03 Plan: the Iris plan in force, planner health over the last 36 h and one lane per planned setting for the next
 * 24 h (72 h on a chip, kept in the URL as `?hours=72`), all from one API-07 read polled every 60 s. Device staleness is
 * the shell's API-02 `freshness.readback.stale`, never a second poll. API-07 failing shows Retry in each panel and keeps
 * no earlier answer on screen. The screen writes nothing: Open is a link. No greenhouse data renders in SSR.
 */
export default function Plan() {
  const [search] = useSearchParams();
  const hours = planHours(search);
  const read = useApiRead("API-07", { hours }, { intervalMs: PLAN_POLL_MS });
  const shellNow = useShellNow();
  const skew = useServerClockSkew();
  // While the newest read failed, the last answer is not shown: nothing on the page may read as current.
  const data = read.isError ? null : read.data ?? null;
  const state: PanelState = data ? "ready" : read.isError ? "error" : "loading";
  const retry = () => { void read.refetch(); };
  // Everything reads as of the answer, on the server's clock as every age does: the lanes start there, and the time
  // left and lateness count from it, so a device clock running ahead never marks a trigger late too early.
  const now = read.dataUpdatedAt - skew;
  const stale = shellNow.data?.freshness.readback.stale === true;
  const manual = data ? manualCount(data.manual_count) : null;
  const chips = [
    { key: "24", label: "24 h", to: "/plan", current: hours === 24 },
    { key: "72", label: "72 h", to: "/plan?hours=72", current: hours === 72 },
  ];
  return <>
    <PageHeader view="control-plan" />
    <div className="grid grid-2 plan-top">
      <PlanCard data={data} state={state} now={now} onRetry={retry} />
      <PlannerHealth health={data?.health ?? []} state={state} now={now} onRetry={retry} />
    </div>
    <Panel title="Planned settings" thing="the planned settings" state={state} reserve="lg" onRetry={retry}
      actions={<div className="plan-lane-tools">
        <Chips label="Lane window" items={chips} />
        {manual && <p className="plan-manual">{manual}</p>}
      </div>}>
      {data && <PlanLanes plan={data} now={Math.floor(now / 1000)} hours={hours} takeover={TAKEOVER} stale={stale} syncKey={`verdify:plan:${hours}h`}
        rowEnd={openSetting} />}
    </Panel>
  </>;
}
