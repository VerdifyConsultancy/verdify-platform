// SPDX-License-Identifier: MIT
import { useSearchParams } from "react-router";
import { dayCsvFilename, tableCsv } from "../charts/csv";
import { CsvLink } from "../charts/data-table";
import { chartLinks } from "../charts/uplot-chart";
import { Chips } from "../components/chips";
import { LocalTime } from "../components/common";
import { PageHeader } from "../components/layout/page-header";
import { Panel, type PanelState } from "../components/panel";
import { Table, type TableColumn } from "../components/table";
import { dayTitle, formatNumber, localDate } from "../lib/format";
import type { PlannerDay, PlannerHistory } from "../lib/operator-api";
import { HISTORY_DAYS, confirmedShare, historyDays, historySentence, historyTotals, tokenWords, triggerMix } from "../lib/planner";
import { useApiRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";

export const meta = () => viewMeta("planner-history");
// The CSV link is chart code: its route links the chart stylesheet (charts/chunks.test.ts).
export const links = () => chartLinks();

/** API-25 every 10 minutes: plans arrive a few times a day. */
const HISTORY_POLL_MS = 600_000;

type Plan = PlannerHistory["plans"][number];

/** The day's cost in words, or "not recorded" (before the Cortex planner, #1350, records a call). */
function dayCost(day: PlannerDay): string {
  if (!day.cost) return "not recorded";
  const tokens = tokenWords(day.cost.input_tokens, day.cost.output_tokens);
  const wh = day.cost.energy_wh == null ? null : `${formatNumber(day.cost.energy_wh, 1)} Wh`;
  return [`${day.cost.calls} ${day.cost.calls === 1 ? "call" : "calls"}`, tokens, wh].filter(Boolean).join(" · ");
}

function dayColumns(cutover: string | null): TableColumn<PlannerDay>[] {
  return [
    { key: "date", header: "Day", rowHeader: true, cell: (day) => <>{dayTitle(day.date, "short")}{cutover === day.date && <span className="tag pln-cutover">Cortex from here</span>}</> },
    { key: "plans", header: "Plans", className: "ctl-num", cell: (day) => day.plans_written },
    { key: "required", header: "Required written", className: "ctl-num", cell: (day) => `${day.required_written} of ${day.required_due}` },
    { key: "failed", header: "Failed", className: "ctl-num", cell: (day) => (day.required_failed ? <strong className="pln-bad">{day.required_failed}</strong> : "0") },
    { key: "late", header: "Late", className: "ctl-num", cell: (day) => (day.required_late ? <strong className="pln-warn">{day.required_late}</strong> : "0") },
    { key: "triggers", header: "Triggers", cell: (day) => <span className="mono pln-triggers">{triggerMix(day.triggers) || "—"}</span> },
    { key: "dispatched", header: "Settings changed", className: "ctl-num", cell: (day) => (day.params_dispatched ? `${day.params_dispatched} (${day.changes_dispatched} writes)` : "none") },
    { key: "confirmed", header: "Confirmed", className: "ctl-num", cell: (day) => confirmedShare(day) },
    { key: "cost", header: "Cost", cell: (day) => <span className={day.cost ? undefined : "muted"}>{dayCost(day)}</span> },
  ];
}

const PLAN_COLUMNS: TableColumn<Plan>[] = [
  { key: "written", header: "Written", rowHeader: true, cell: (plan) => <LocalTime at={plan.written_at} date /> },
  { key: "trigger", header: "Trigger", cell: (plan) => <span className="mono">{plan.trigger}</span> },
  { key: "hypothesis", header: "Hypothesis", cell: (plan) => <span className="pln-hypothesis">{plan.hypothesis ?? "—"}</span> },
  { key: "score", header: "Self-score", className: "ctl-num", cell: (plan) => formatNumber(plan.outcome_score) },
  { key: "dispatched", header: "Settings changed", className: "ctl-num", cell: (plan) => plan.params_dispatched || "none" },
  {
    key: "cost", header: "Cost", cell: (plan) => (plan.cost
      ? <>{plan.cost.model_id ?? "model unknown"} · {tokenWords(plan.cost.input_tokens, plan.cost.output_tokens) ?? "—"}{plan.cost.latency_ms != null && <> · {formatNumber(plan.cost.latency_ms / 1000, 0)} s</>}</>
      : <span className="muted">not recorded</span>),
  },
];

function historyCsv(days: readonly PlannerDay[]): string {
  return tableCsv(
    ["date", "plans_written", "required_due", "required_written", "required_failed", "required_late", "params_dispatched", "changes_dispatched", "changes_confirmed",
      "calls", "input_tokens", "output_tokens", "energy_wh", "latency_ms_median"],
    days.map((day) => [day.date, day.plans_written, day.required_due, day.required_written, day.required_failed, day.required_late, day.params_dispatched,
      day.changes_dispatched, day.changes_confirmed, day.cost?.calls ?? null, day.cost?.input_tokens ?? null, day.cost?.output_tokens ?? null,
      day.cost?.energy_wh ?? null, day.cost?.latency_ms_median ?? null]),
  );
}

/**
 * Planner → History (#1361): the planner over 30 or 90 days from one API-25 read. A summary line, then one row per day,
 * newest first: plans written, required plans written of due with failed and late, the trigger mix, the settings the
 * plans changed on the controller and the share it confirmed, and the cost. Before the Cortex planner (#1350) records
 * a call, cost reads "not recorded", and the first costed day is marked. Then the newest plans. Viewer and operator see
 * the same; nothing here writes.
 */
export default function PlannerHistoryPage() {
  const [search] = useSearchParams();
  const days = historyDays(search);
  const read = useApiRead("API-25", { days }, { intervalMs: HISTORY_POLL_MS });
  const data = read.isError ? null : read.data ?? null;
  const state: PanelState = data ? "ready" : read.isError ? "error" : "loading";
  const retry = () => { void read.refetch(); };
  const totals = data ? historyTotals(data.days) : null;
  const cutover = data?.cost_recorded_from ? localDate(data.cost_recorded_from) : null;
  const rows = data ? [...data.days].reverse() : [];
  const chips = HISTORY_DAYS.map((value) => ({ key: String(value), label: `${value} days`, to: value === 30 ? "?" : `?days=${value}`, current: value === days }));
  return <>
    <PageHeader view="planner-history" status={totals ? historySentence(totals) : null} />
    <div className="page-stack">
      <Chips label="Window" items={chips} kind="nav" />
      <Panel title="Plans per day" thing="plan history" state={state} onRetry={retry} reserve="lg"
        foot={data && <>
          {cutover ? <>Cost recorded from <LocalTime at={data.cost_recorded_from!} date /> (Cortex planner). Earlier plans: not recorded.</>
            : <>Cost not recorded: the planner records tokens, latency and energy from the Cortex cutover (#1350).</>}
          {" "}<CsvLink filename={dayCsvFilename("planner-history", data.from, data.to)} build={() => historyCsv(data.days)} version={data} label="CSV of these days" />
        </>}>
        {data && <Table label="Plans per day" caption={`Plans per day, ${dayTitle(data.from, "short")} to ${dayTitle(data.to, "short")}, newest first`} captionHidden
          columns={dayColumns(cutover)} rows={rows} rowKey={(day) => day.date} />}
      </Panel>
      <Panel title="Newest plans" thing="plans" state={data && data.plans.length === 0 ? "empty" : state} onRetry={retry}
        foot={data && <>Self-score is the planner's own grade of its plan, not a measured outcome.</>}>
        {data && <Table label="Newest plans" caption="The newest plans, newest first" captionHidden columns={PLAN_COLUMNS} rows={data.plans} rowKey={(plan) => plan.plan_id} />}
      </Panel>
    </div>
  </>;
}
