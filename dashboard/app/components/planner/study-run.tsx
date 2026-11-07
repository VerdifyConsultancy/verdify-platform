// SPDX-License-Identifier: MIT
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { dateShift, dayTitle, formatNumber } from "../../lib/format";
import type { ArmCost, ArmSummary, StudyRun } from "../../lib/operator-api";
import {
  RUN_STATUS_WORDS, STUDY_SETTINGS, VERB_WORDS, armWords, filledIds, plannerName, runActions, tokenWords, type StudyVerb,
} from "../../lib/planner";
import type { StudyControlBody, WriteError, WriteOutcome } from "../../lib/writes";
import { Button } from "../button";
import { LocalTime } from "../common";
import { FieldError, FieldHint, describedBy, useFieldIds } from "../forms/field";
import { useModalDialog } from "../forms/modal";
import { Reason } from "../forms/reason";
import { Table, type TableColumn } from "../table";

const short = (hash: string) => hash.slice(0, 12);

/** "Running", "Paused since Nov 7, 09:12", "Stopped Nov 7, 09:12: the reason". */
function StatusLine({ run }: { run: StudyRun }) {
  const words = RUN_STATUS_WORDS[run.status] ?? run.status;
  if (run.status === "paused" && run.paused_at) return <>{words} since <LocalTime at={run.paused_at} date /></>;
  if (run.status === "stopped" && run.stopped_at) return <>{words} <LocalTime at={run.stopped_at} date />{run.stop_reason && <>: {run.stop_reason}</>}</>;
  return <>{words}</>;
}

/**
 * The run's facts (#1360), from API-27 `run`: status, the AI planner, the arm and study day in force, the next boundary,
 * pairs done and left, the holds in force, the settings someone else holds, the last boundary's readback result, the
 * deviations and what was pre-registered. Every number is the server's.
 */
export function RunFacts({ run }: { run: StudyRun }) {
  const boundary = run.last_boundary;
  return <dl className="pln-design pln-run">
    <dt>Status</dt>
    <dd><strong className="pln-run-status" data-status={run.status}><StatusLine run={run} /></strong>
      <span className="meta"> · started <LocalTime at={run.started_at} date /> by {run.started_by}, first day {dayTitle(run.first_day, "short")}</span></dd>
    <dt>AI planner</dt>
    <dd>{plannerName(run.ai_planner)}</dd>
    <dt>In force now</dt>
    <dd>{run.arm_now && run.day_now
      ? <><span className="pln-arm" data-arm={run.arm_now}>{armWords(run.arm_now)}</span>, study day {dayTitle(run.day_now, "short")} (11:00 to 11:00)</>
      : "No study day is in force"}</dd>
    <dt>Next boundary</dt>
    <dd>{run.next_boundary ? <LocalTime at={run.next_boundary} date /> : "None"}</dd>
    <dt>Pairs</dt>
    <dd>{run.pairs_done} done, {run.pairs_left} left of {run.pairs}</dd>
    <dt>Holds in force</dt>
    <dd>{run.holds_in_force} of {STUDY_SETTINGS} planner settings held at the baseline{run.holds_in_force === 0 && run.arm_now === "ai" ? " (an AI day: Hermes's plans apply)" : ""}</dd>
    <dt>Held by others</dt>
    <dd>{run.held_by_others.length
      ? <><span className="pln-warn">{run.held_by_others.length} {run.held_by_others.length === 1 ? "setting" : "settings"}</span>, a deviation for the day: {run.held_by_others.map((param, index) => <span key={param}>{index > 0 && ", "}<code>{param}</code></span>)}</>
      : "None"}</dd>
    <dt>Last boundary</dt>
    <dd>{boundary
      ? <>{dayTitle(boundary.date, "short")}, {armWords(boundary.arm)}: {boundary.result === "confirmed"
        ? <>confirmed by readback <LocalTime at={boundary.at} /></>
        : <strong className="pln-warn">not confirmed{boundary.unconfirmed.length > 0 && <> ({boundary.unconfirmed.length}: {boundary.unconfirmed.map((param, index) => <span key={param}>{index > 0 && ", "}<code>{param}</code></span>)})</>}</strong>}</>
      : "None yet"}</dd>
    <dt>Deviations</dt>
    <dd>{run.deviations > 0 ? <span className="pln-warn">{run.deviations}</span> : 0}</dd>
    <dt>Pre-registered</dt>
    <dd className="meta">Seed {run.seed} · schedule <span className="mono">{short(run.schedule_sha256)}</span> · baseline <span className="mono">{short(run.baseline_sha256)}</span></dd>
  </dl>;
}

const DIALOG_FACTS: Readonly<Record<StudyVerb, string>> = {
  start: `From the 11:00 boundary on the first day: the pairs in the committed seed's order. On a frozen-baseline day the study holds the ${STUDY_SETTINGS} planner settings manual at the baseline, so Hermes's plans are stored and not sent; on an AI day it releases them.`,
  pause: "Releases the study's holds now, so Hermes dispatches as normal. A pair already under way is void and runs again after Resume.",
  resume: "The remaining pairs start at the next 11:00 boundary, in their seeded order.",
  stop: "Releases the holds and ends the run. Days already run stay recorded; days not yet run are cancelled. A stopped run is not resumed.",
};

export type StudyDialogProps = {
  verb: StudyVerb | null;
  onClose: () => void;
  onSend: (verb: StudyVerb, body: StudyControlBody) => Promise<WriteOutcome<unknown>>;
  sending: boolean;
  error: WriteError | null;
  /** Today's local date: the first day defaults to the day after, and cannot be before today. */
  today: string;
};

/**
 * The confirmation for one study control (design system §4.4's native modal dialog): what the action does, Start's
 * first day (America/Denver, default tomorrow) and an optional reason, Cancel and the verb. A refusal (`study_state`,
 * in the server's own sentence) stays in the dialog; a 200 closes it.
 */
export function StudyControlDialog({ verb, onClose, onSend, sending, error, today }: StudyDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const factsId = useId();
  const errorId = useId();
  const day = useFieldIds();
  const open = verb !== null;
  const [firstDay, setFirstDay] = useState(() => dateShift(today, 1));
  const [reason, setReason] = useState("");
  const [check, setCheck] = useState<string | null>(null);
  const tomorrow = dateShift(today, 1);
  // Each opening starts from tomorrow and an empty reason.
  useEffect(() => {
    if (!open) return;
    setFirstDay(tomorrow);
    setReason("");
    setCheck(null);
  }, [open, tomorrow]);
  const { onKeyDown, onCancel } = useModalDialog(ref, open, onClose);
  const shown = verb ?? "start";
  const dayError = check ?? (error?.field === "first_day" ? error.message : null);
  const formError = error && error.field !== "first_day" && error.field !== "reason" ? error.message : null;
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!verb || sending) return;
    if (verb === "start") {
      if (!/^\d{4}-\d\d-\d\d$/.test(firstDay)) { setCheck("Pick the first study day"); return; }
      if (firstDay < today) { setCheck("The first day cannot be in the past"); return; }
    }
    setCheck(null);
    const body: StudyControlBody = { ...(verb === "start" ? { first_day: firstDay } : {}), ...(reason.trim() ? { reason: reason.trim() } : {}) };
    if ((await onSend(verb, body)).ok) onClose();
  };
  return <dialog ref={ref} className="confirm-dialog" aria-labelledby={titleId} aria-describedby={factsId} aria-modal="true" onKeyDown={onKeyDown} onCancel={onCancel}>
    {open && <form className="dialog-form" onSubmit={(event) => void submit(event)} noValidate>
      <h2 id={titleId}>{VERB_WORDS[shown]} the study?</h2>
      <p id={factsId} className="dialog-facts">{DIALOG_FACTS[shown]}</p>
      {shown === "start" && <div className="dialog-fields">
        <div className={dayError ? "field field-invalid" : "field"}>
          <label htmlFor={day.id}>First study day</label>
          <input id={day.id} type="date" value={firstDay} min={today} aria-invalid={dayError ? true : undefined}
            aria-describedby={describedBy(day.hintId, dayError ? day.errorId : null)} onChange={(event) => setFirstDay(event.target.value)} />
          <FieldHint id={day.hintId}>America/Denver; the day starts at its 11:00 boundary, which must still be ahead</FieldHint>
          {dayError && <FieldError id={day.errorId}>{dayError}</FieldError>}
        </div>
      </div>}
      <Reason value={reason} onChange={setReason} error={error?.field === "reason" ? error.message : null} />
      {formError && <FieldError id={errorId}>{formError}</FieldError>}
      <div className="dialog-actions">
        <Button onClick={onClose}>Cancel</Button>
        <Button write variant="primary" type="submit" busy={sending}>{VERB_WORDS[shown]}</Button>
      </div>
    </form>}
  </dialog>;
}

/**
 * The operator's controls for the run's status: Start with no run or an ended one (disabled, with why, until the
 * definition is pre-registered), Pause and Stop while scheduled or running, Resume and Stop while paused. Each opens
 * its confirmation. A viewer sees none (`Button write` renders for operators only).
 */
export function RunControls({ status, preregistered, onChoose }: { status: string | null; preregistered: boolean; onChoose: (verb: StudyVerb) => void }) {
  const whyId = useId();
  const verbs = runActions(status);
  const blocked = verbs.includes("start") && !preregistered;
  return <div className="pln-run-controls">
    {verbs.map((verb) => <Button key={verb} write disabled={verb === "start" && blocked} aria-describedby={verb === "start" && blocked ? whyId : undefined}
      onClick={() => onChoose(verb)}>{VERB_WORDS[verb]} the study</Button>)}
    {blocked && <p id={whyId} className="meta pln-run-why">Start needs a pre-registered definition: the seed, schedule hash and baseline hash committed (scripts/planner-study-preregister.py).</p>}
  </div>;
}

// ---------------------------------------------------------------------------------------------------- per arm
/** Per arm, over its finished days (API-27 `arms`): days done, deviation days, then the mean of each live value. */
export function ArmSummaryTable({ arms, order, labels }: { arms: readonly ArmSummary[]; order: readonly string[]; labels: Readonly<Record<string, string>> }) {
  const ids = filledIds(order, arms.map((arm) => arm.means));
  const columns: TableColumn<ArmSummary>[] = [
    { key: "arm", header: "Arm", rowHeader: true, cell: (row) => <span className="pln-arm" data-arm={row.arm}>{armWords(row.arm)}</span> },
    { key: "days", header: "Days done", className: "ctl-num", cell: (row) => row.days_done },
    { key: "deviation", header: "Deviation days", className: "ctl-num", cell: (row) => row.deviation_days },
    ...ids.map((id): TableColumn<ArmSummary> => ({ key: id, header: labels[id] ?? id, className: "ctl-num", cell: (row) => formatNumber(row.means[id] ?? null, 1) })),
  ];
  return <Table label="Per arm" caption="Mean per finished day, by arm: live values, not the frozen analysis" captionHidden columns={columns} rows={arms} rowKey={(row) => row.arm} />;
}

// ---------------------------------------------------------------------------------------------------- cost
type CohortRow = { arm: string; cohort: string; calls: number; ok: number; input_tokens: number; output_tokens: number; energy_wh: number | null };
/**
 * Cost per arm (API-27 `cost`): the AI planner's plans written and plan changes sent, then the Cortex shadow runs, one
 * row per arm and cohort and never summed across cohorts (each cohort is its own treatment). Hermes records no tokens.
 */
export function CostTables({ cost }: { cost: readonly ArmCost[] }) {
  const planner: TableColumn<ArmCost>[] = [
    { key: "arm", header: "Arm", rowHeader: true, cell: (row) => <span className="pln-arm" data-arm={row.arm}>{armWords(row.arm)}</span> },
    { key: "days", header: "Days", className: "ctl-num", cell: (row) => row.days },
    { key: "planner", header: "Planner", cell: (row) => plannerName(row.planner) },
    { key: "plans", header: "Plans written", className: "ctl-num", cell: (row) => row.plans_written },
    { key: "sent", header: "Plan changes sent", className: "ctl-num", cell: (row) => row.plan_changes_sent },
  ];
  const cohorts: CohortRow[] = cost.flatMap((row) => row.shadow.map((shadow) => ({ arm: row.arm, ...shadow, energy_wh: shadow.energy_wh ?? null })));
  const shadow: TableColumn<CohortRow>[] = [
    { key: "cohort", header: "Cohort", rowHeader: true, cell: (row) => <code>{row.cohort}</code> },
    { key: "arm", header: "Arm days", cell: (row) => <span className="pln-arm" data-arm={row.arm}>{armWords(row.arm)}</span> },
    { key: "calls", header: "Calls", className: "ctl-num", cell: (row) => row.calls },
    { key: "ok", header: "OK", className: "ctl-num", cell: (row) => row.ok },
    { key: "tokens", header: "Tokens", className: "ctl-num", cell: (row) => tokenWords(row.input_tokens, row.output_tokens) ?? "—" },
    { key: "energy", header: "Energy", className: "ctl-num", cell: (row) => (row.energy_wh == null ? "—" : `${formatNumber(row.energy_wh, 1)} Wh`) },
  ];
  return <>
    <Table label="Planner cost by arm" caption="The AI planner's plans and plan changes sent, by arm" captionHidden columns={planner} rows={cost} rowKey={(row) => row.arm} />
    <p className="meta">Hermes records no per-call tokens or energy: its cost is its plans and the changes they sent. On a frozen-baseline day its plans are stored, not sent.</p>
    <h3 className="pln-subhead">Cortex shadow runs</h3>
    {cohorts.length
      ? <Table label="Cortex shadow runs by cohort" caption="Cortex shadow runs, per cohort and arm; cohorts are separate treatments and are never added together" captionHidden
        columns={shadow} rows={cohorts} rowKey={(row) => `${row.cohort}-${row.arm}`} group={(row) => row.cohort} groupLabel={(cohort) => <code>{cohort}</code>} />
      : <p className="muted">No Cortex shadow run on a study day yet.</p>}
    {cohorts.length > 0 && <p className="meta">Each cohort is a different treatment (cortex-1482 is after the #1482 repair change); they are listed apart and never summed.</p>}
  </>;
}
