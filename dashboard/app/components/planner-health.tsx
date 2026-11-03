// SPDX-License-Identifier: MIT
import type { PlannerTrigger } from "../lib/operator-api";
import { words } from "../lib/words";
import { LocalTime, cls } from "./common";
import { Details } from "./details";
import { EmptyState, Panel, type PanelState } from "./panel";
import { otherDay } from "./plan-card";

/** The newest rows that have fired, always shown; the next row ahead and every marked required trigger show too. */
export const NEWEST_SHOWN = 5;
/** Outcomes that mean a required trigger failed: missed, timed out, or never delivered. */
const FAILED: ReadonlySet<string> = new Set(["missed", "timed_out", "delivery_failed"]);

const epoch = (instant: string): number => {
  const ms = Date.parse(instant);
  return Number.isFinite(ms) ? ms : Number.NEGATIVE_INFINITY;
};

/** Still due or delivered after its `due_at`: late, whether required or not. An unreadable due time is never late. */
export function isLate(row: PlannerTrigger, nowMs: number): boolean {
  const due = Date.parse(row.due_at);
  return (row.outcome === "expected" || row.outcome === "delivered") && Number.isFinite(due) && due < nowMs;
}

/**
 * A required trigger (`required`: MIDNIGHT, SUNRISE, SUNSET) that needs someone: missed, timed out or not delivered, or
 * still due or delivered after its due time. It gets the ▲ and the amber rule. The outcome is the ledger's; only the
 * lateness is read against now.
 */
export function needsAttention(row: PlannerTrigger, nowMs: number): boolean {
  return row.required && (FAILED.has(row.outcome) || isLate(row, nowMs));
}

/**
 * The rows newest first, by when each trigger fires. The ledger seeds a whole day's rows at midnight, so the expected
 * rows ahead would otherwise fill the list: `shown` holds the next row ahead, the five newest that have fired and every
 * marked required trigger, and `rest` the other rows ahead and the rest of the 36 h.
 */
export function healthRows(rows: readonly PlannerTrigger[], nowMs: number): { shown: PlannerTrigger[]; rest: PlannerTrigger[] } {
  const newest = rows.map((row, index) => ({ row, index }))
    .sort((a, b) => epoch(b.row.expected_at) - epoch(a.row.expected_at) || b.index - a.index)
    .map(({ row }) => row);
  const ahead = newest.filter((row) => epoch(row.expected_at) > nowMs);
  const next = ahead[ahead.length - 1];
  const shown: PlannerTrigger[] = [];
  const rest: PlannerTrigger[] = [];
  let fired = 0;
  for (const row of newest) {
    if (ahead.includes(row)) (row === next ? shown : rest).push(row);
    else (fired++ < NEWEST_SHOWN || needsAttention(row, nowMs) ? shown : rest).push(row);
  }
  return { shown, rest };
}

/** One ledger row: ▲ when it needs someone, the trigger, its due time, the outcome in words and the failure class. */
function TriggerRow({ row, now }: { row: PlannerTrigger; now: number }) {
  const marked = needsAttention(row, now);
  return <li className={cls("trigger-row", marked && "trigger-marked")} data-trigger={row.trigger} data-outcome={row.outcome}>
    <span className="trigger-mark">{marked && <><span aria-hidden="true">▲</span><span className="screen-reader">Needs attention: </span></>}</span>
    <span className="trigger-name">{row.trigger}</span>
    <span className="trigger-due"><span className="screen-reader">due </span><LocalTime at={row.due_at} date={otherDay(row.due_at, now)} /></span>
    <span className="trigger-outcome">{words("trigger_outcome", row.outcome)}{isLate(row, now) && " · late"}</span>
    {/* The ledger's failure classes are open-ended; the words table has no family for them, so each reads raw. */}
    {row.failure_class && <span className="trigger-class">{words("failure_class", row.failure_class)}</span>}
  </li>;
}

function TriggerList({ rows, now }: { rows: readonly PlannerTrigger[]; now: number }) {
  return <ol className="trigger-list">{rows.map((row, index) => <TriggerRow key={`${row.trigger}|${row.expected_at}|${index}`} row={row} now={now} />)}</ol>;
}

export type PlannerHealthProps = {
  /** API-07 `health`: `planner_trigger_ledger` over the last 36 h and the expected rows ahead. */
  health: readonly PlannerTrigger[];
  state: PanelState;
  /** When API-07 answered (epoch ms): lateness is read against it. */
  now: number;
  onRetry?: () => void;
};

/**
 * Planner health (SCR-03, US-PLN-03): the trigger, due time, outcome in words and failure class of each ledger row,
 * newest first: the next row ahead, the five newest that have fired and every marked required trigger, then "All
 * triggers" for the rest. A required trigger that was missed, timed out or failed delivery, or is late, carries ▲ and an
 * amber rule, so the owner on call can tell whether a missed SUNSET is urgent. No attempts column and no recent plans.
 */
export function PlannerHealth({ health, state, now, onRetry }: PlannerHealthProps) {
  const { shown, rest } = healthRows(health, now);
  const panelState: PanelState = state === "ready" && health.length === 0 ? "empty" : state;
  return <Panel title="Planner health" eyebrow="Last 36 h" thing="planner health" state={panelState} reserve="md" onRetry={onRetry}
    empty={<EmptyState message="No planner triggers in the last 36 h." detail="Each MIDNIGHT, SUNRISE and SUNSET trigger and every other planner trigger shows here." />}>
    <TriggerList rows={shown} now={now} />
    {rest.length > 0 && <Details summary={<>All triggers <span className="trigger-more">· {rest.length} more</span></>} className="trigger-all">
      <TriggerList rows={rest} now={now} />
    </Details>}
  </Panel>;
}
