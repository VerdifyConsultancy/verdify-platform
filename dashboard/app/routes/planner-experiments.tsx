// SPDX-License-Identifier: MIT
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { dayCsvFilename, tableCsv } from "../charts/csv";
import { CsvLink } from "../charts/data-table";
import { chartLinks } from "../charts/uplot-chart";
import { LocalTime, cls } from "../components/common";
import { PageHeader } from "../components/layout/page-header";
import { Panel, type PanelState } from "../components/panel";
import { ArmCalendar } from "../components/planner/arm-calendar";
import { CiRows } from "../components/planner/ci-row";
import { ArmSummaryTable, CostTables, RunControls, RunFacts, StudyControlDialog } from "../components/planner/study-run";
import { Table, type TableColumn } from "../components/table";
import { dayTitle, formatNumber, localDate } from "../lib/format";
import type { ExperimentDetail, ExperimentSummary } from "../lib/operator-api";
import { RUN_STATUS_WORDS, STATUS_WORDS, armWords, daysWords, filledIds, selectedExperiment, type StudyVerb } from "../lib/planner";
import { useApiRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { useStudyControl } from "../lib/writes";

export const meta = () => viewMeta("planner-experiments");
// The CSV link is chart code: its route links the chart stylesheet (charts/chunks.test.ts).
export const links = () => chartLinks();

/** API-26 and API-27 every 5 minutes: a study day is 24 h and the analyzer runs once a day. */
const EXPERIMENTS_POLL_MS = 300_000;

type Definition = NonNullable<ExperimentDetail["definition"]>;
type Outcome = ExperimentDetail["outcomes"][number];

function ExperimentItem({ item, current }: { item: ExperimentSummary; current: boolean }) {
  return <li className={cls("pln-experiment", current && "is-current")} data-status={item.status}>
    <Link to={`?id=${encodeURIComponent(item.id)}`} aria-current={current ? "page" : undefined} preventScrollReset>
      <strong>{item.name}</strong>
    </Link>
    <span className="meta"> · <span className="pln-status">{STATUS_WORDS[item.status] ?? item.status}</span> · {daysWords(item)}
      {item.deviations > 0 && <> · {item.deviations} {item.deviations === 1 ? "deviation" : "deviations"}</>}</span>
    {item.closed_reason && <p className="meta pln-closed">Closed{item.closed_at && <> <LocalTime at={item.closed_at} date /></>}: {item.closed_reason}</p>}
  </li>;
}

function Design({ definition }: { definition: Definition }) {
  const primary = definition.metrics.find((metric) => metric.role === "primary");
  const guardrails = definition.metrics.filter((metric) => metric.role === "guardrail");
  const reported = definition.metrics.filter((metric) => metric.direction === "report");
  const { schedule } = definition;
  return <dl className="pln-design">
    <dt>Arms</dt>
    <dd>{definition.arms.map((arm) => <p key={arm.label}><strong>{armWords(arm.label)}</strong> ({arm.planner_mode.replace("_", "-")}): {arm.description}</p>)}</dd>
    <dt>Schedule</dt>
    <dd>{schedule.pairs} pairs of adjacent days, each day {schedule.boundary_local} to {schedule.boundary_local} local, arm order random within a pair;
      the first {formatNumber(schedule.washout_hours, 0)} h of a day are washout and not scored. No blinding.</dd>
    <dt>Primary</dt>
    <dd>{primary ? `${primary.label} (${primary.unit}), higher is better` : "—"}</dd>
    <dt>Guardrails</dt>
    <dd>{guardrails.length ? guardrails.map((metric) => `${metric.label}: not worse by more than ${formatNumber(metric.noninferiority_margin, 1)} ${metric.unit}`).join("; ") : "—"}</dd>
    <dt>Reported, never scored</dt>
    <dd>{reported.map((metric) => metric.label).join(", ") || "—"}. Electric heat is the primary heat source: its run time is never a cost defect.</dd>
    <dt>Looks</dt>
    <dd>{definition.looks.map((look) => `${look.pairs} pairs (${look.purpose.replaceAll("_", " ")})`).join(", ")}</dd>
    <dt>Stopping rules</dt>
    <dd><ul>{definition.stopping_rules.map((rule) => <li key={rule.id}>{rule.rule} → {rule.action}</li>)}</ul></dd>
    <dt>Definition</dt>
    <dd className="meta">Version {definition.version} · <span className="mono">{definition.definition_sha256.slice(0, 12)}</span> · {definition.preregistered ? "preregistered" : "not yet preregistered (no seed or schedule hash)"}</dd>
  </dl>;
}

function outcomeColumns(ids: readonly string[], labels: Readonly<Record<string, string>>): TableColumn<Outcome>[] {
  return [
    { key: "date", header: "Day", rowHeader: true, cell: (row) => dayTitle(row.date, "short") },
    { key: "arm", header: "Arm", cell: (row) => <span className="pln-arm" data-arm={row.arm}>{armWords(row.arm)}</span> },
    { key: "deviation", header: "Deviation", cell: (row) => (row.deviation ? <strong className="pln-warn">yes</strong> : "no") },
    ...ids.map((id): TableColumn<Outcome> => ({ key: id, header: labels[id] ?? id, className: "ctl-num", cell: (row) => formatNumber(row.values[id] ?? null, 1) })),
  ];
}

function outcomesCsv(rows: readonly Outcome[], ids: readonly string[]): string {
  return tableCsv(["date", "arm", "deviation", ...ids], rows.map((row) => [row.date, row.arm, row.deviation ? "yes" : "no", ...ids.map((id) => row.values[id] ?? null)]));
}

/**
 * Planner → Experiments (#1361, #1360): the experiments the planner is studied by, the selected one's design, its arm
 * calendar, its results and its daily outcomes. API-26 lists the committed study definitions and the experiment
 * records; API-27 reads one. Every estimate and interval is the frozen analyzer's: until it has run, Results say why
 * there are none. A defined study also has its Run (#1360): status, the arm in force, holds and boundaries, and for an
 * operator Start, Pause, Resume and Stop, each confirmed in a dialog and sent as API-28, which the operator API's study
 * runner applies within a minute; then per-arm live means and cost. A viewer sees the same panels without the controls.
 */
export default function PlannerExperimentsPage() {
  const [search] = useSearchParams();
  const list = useApiRead("API-26", {}, { intervalMs: EXPERIMENTS_POLL_MS });
  const items = list.isError ? null : list.data?.items ?? null;
  const chosen = items ? selectedExperiment(items, search.get("id")) : null;
  const listState: PanelState = items ? (items.length ? "ready" : "empty") : list.isError ? "error" : "loading";
  const retryList = () => { void list.refetch(); };
  const status = chosen ? `${chosen.name}: ${STATUS_WORDS[chosen.status] ?? chosen.status}, ${daysWords(chosen)}` : items ? (search.get("id") ? "No such experiment." : "No experiment is defined.") : null;
  return <>
    <PageHeader view="planner-experiments" status={status} />
    <div className="page-stack">
      <Panel title="Experiments" thing="experiments" state={listState} onRetry={retryList}>
        {items && <ul className="pln-experiments">{items.map((item) => <ExperimentItem key={item.id} item={item} current={item.id === chosen?.id} />)}</ul>}
      </Panel>
      {chosen ? <ExperimentPanels key={chosen.id} id={chosen.id} />
        : listState !== "ready" && listState !== "empty" && <Panel title="Design" thing="the study design" state={listState === "error" ? "error" : "loading"} onRetry={retryList} />}
    </div>
  </>;
}

/** The selected experiment's panels, from API-27: read only once one is chosen. */
function ExperimentPanels({ id }: { id: string }) {
  const detailRead = useApiRead("API-27", { id }, { intervalMs: EXPERIMENTS_POLL_MS });
  const detail = detailRead.isError ? null : detailRead.data ?? null;
  const detailState: PanelState = detail ? "ready" : detailRead.isError ? "error" : "loading";
  const retryDetail = () => { void detailRead.refetch(); };
  const today = localDate(new Date(detailRead.dataUpdatedAt || Date.now()).toISOString());
  const metrics = detail?.definition?.metrics ?? [];
  const margins = Object.fromEntries(metrics.map((metric) => [metric.id, metric.noninferiority_margin ?? null]));
  const labels = Object.fromEntries(metrics.map((metric) => [metric.id, metric.label]));
  const metricIds = metrics.map((metric) => metric.id);
  // The columns some day has a value for: the live ones (heat, vent, wetting, plans written) until the frozen analyzer.
  const outcomeIds = filledIds(metricIds, detail?.outcomes.map((row) => row.values) ?? []);
  const results = detail?.results;
  const definition = detail?.definition ?? null;
  const run = detail?.run ?? null;
  return <>
      {definition && <RunPanel studyId={definition.study_id} run={run} preregistered={definition.preregistered} today={today} />}
      <Panel title="Design" thing="the study design" state={detailState} onRetry={retryDetail}>
        {detail && (detail.definition ? <Design definition={detail.definition} />
          : <p className="muted">This record has no committed definition: it predates the study definitions in the repository.</p>)}
      </Panel>
      <Panel title="Arm calendar" thing="the arm calendar" state={detailState} onRetry={retryDetail}>
        {detail && <ArmCalendar days={detail.calendar} today={today} nextBoundary={detail.next_boundary ?? null} />}
      </Panel>
      {detail && run && <>
        <Panel title="Per arm" thing="per-arm values" state={detail.arms.every((arm) => arm.days_done === 0) ? "empty" : "ready"}
          empty={<p className="muted">No study day has finished yet.</p>}
          foot="Live values, not the frozen analysis: the mean per finished day. The climate metrics wait for the frozen analyzer.">
          <ArmSummaryTable arms={detail.arms} order={metricIds} labels={labels} />
        </Panel>
        <Panel title="Cost" thing="cost" state={detail.cost.every((arm) => arm.days === 0) ? "empty" : "ready"} empty={<p className="muted">No study day has started yet.</p>}>
          <CostTables cost={detail.cost} />
        </Panel>
      </>}
      <Panel title="Results" thing="results" state={detailState} onRetry={retryDetail}
        pill={results && results.state !== "not_started" ? <span className={cls("tag", results.state === "final" && "tag-ok")}>{results.state === "interim" ? "Interim" : "Final"}</span> : undefined}
        foot={results?.analyzed_at ? <>Analyzed <LocalTime at={results.analyzed_at} date />{results.output_sha256 && <> · <span className="mono">{results.output_sha256.slice(0, 12)}</span></>} · AI minus baseline, pair fixed effects, 95% intervals.</> : undefined}>
        {results && (results.state === "not_started"
          ? <p className="muted">No results yet. {results.reason ?? ""}</p>
          : <>
            <p>{results.sentence ?? `${results.pairs_done}${results.pairs_planned ? ` of ${results.pairs_planned}` : ""} pairs analyzed.`}</p>
            <CiRows metrics={results.metrics} margins={margins} />
          </>)}
      </Panel>
      <Panel title="Daily outcomes" thing="daily outcomes" state={detail && detail.outcomes.length === 0 ? "empty" : detailState} onRetry={retryDetail}
        foot={detail && detail.outcomes.length > 0 && <CsvLink filename={dayCsvFilename("planner-study", detail.outcomes[0].date, detail.outcomes[detail.outcomes.length - 1].date)}
          build={() => outcomesCsv(detail.outcomes, outcomeIds)} version={detail} label="CSV of these days" />}>
        {detail && <Table label="Daily outcomes" caption="Each finished study day, by arm: live values until the frozen analyzer" captionHidden
          columns={outcomeColumns(outcomeIds, labels)} rows={detail.outcomes} rowKey={(row) => `${row.date}-${row.arm}`} />}
      </Panel>
  </>;
}

/**
 * The study's run (#1360): its facts, or "Not started", and the operator's controls for its status. Each control opens
 * its confirmation; the 200 refetches API-26 and API-27, so the panel reads the new status at once.
 */
function RunPanel({ studyId, run, preregistered, today }: { studyId: string; run: ExperimentDetail["run"]; preregistered: boolean; today: string }) {
  const control = useStudyControl(studyId);
  const [verb, setVerb] = useState<StudyVerb | null>(null);
  const choose = (next: StudyVerb) => { control.clearError(); setVerb(next); };
  return <Panel title="Run" thing="the study run" state="ready"
    pill={run ? <span className={cls("tag", run.status === "running" && "tag-ok")}>{RUN_STATUS_WORDS[run.status] ?? run.status}</span> : undefined}>
    {run ? <RunFacts run={run} />
      : <p className="muted">Not started. Start places the pairs from the first day you pick; nothing is held until that day's 11:00 boundary.</p>}
    <RunControls status={run?.status ?? null} preregistered={preregistered} onChoose={choose} />
    <StudyControlDialog verb={verb} onClose={() => setVerb(null)} onSend={control.send} sending={control.sending} error={control.error} today={today} />
  </Panel>;
}
