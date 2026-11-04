// SPDX-License-Identifier: MIT
import { useId } from "react";
import { dateShift, localDate, localDayStart } from "../lib/format";
import type { PlannerTrigger, Series } from "../lib/operator-api";
import { useApiRead } from "../lib/queries";
import { words } from "../lib/words";
import { LocalTime, cls } from "./common";
import { StatusStrip, missedLevel, type StripCell } from "./data/status-strip";
import { Details } from "./details";
import { useClockValue } from "./freshness";
import { EmptyState, Panel, type PanelState } from "./panel";
import { otherDay } from "./plan-card";

/** The newest rows that have fired, always shown; the next row ahead and every marked required trigger show too. */
export const NEWEST_SHOWN = 5;
/** Outcomes that mean a required trigger failed: missed, timed out, or never delivered. */
const FAILED: ReadonlySet<string> = new Set(["missed", "timed_out", "delivery_failed"]);
/** Outcomes that are a plan written, a setting changed or a delivery the planner took: the quiet marks. */
const DONE: ReadonlySet<string> = new Set(["plan_written", "action_completed", "acked", "delivered"]);

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

/** A fired trigger's mark: done (quiet), late or a fallback (amber), failed (fault), anything else neutral. */
export type MarkKind = "ok" | "late" | "failed" | "other";
export function markKind(row: PlannerTrigger, nowMs: number): MarkKind {
  if (FAILED.has(row.outcome) || row.outcome === "wrong_action") return "failed";
  if (isLate(row, nowMs) || row.outcome === "neutral_fallback") return "late";
  return DONE.has(row.outcome) ? "ok" : "other";
}
const MARK_WORDS: Readonly<Record<MarkKind, string>> = { ok: "done", late: "late or fallback", failed: "failed or missed", other: "other" };

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

/**
 * The last 36 h as a row of marks, oldest first (redesign §5.11): each fired trigger a square, quiet when it was done,
 * amber when late, fault when it failed, a required one full height and an optional one shorter. Each says its trigger,
 * time and outcome in words (its title, and to a screen reader); the ledger below lists the same rows.
 */
function TriggerMarks({ rows, now }: { rows: readonly PlannerTrigger[]; now: number }) {
  const labelId = useId();
  const fired = rows.filter((row) => epoch(row.expected_at) <= now).sort((a, b) => epoch(a.expected_at) - epoch(b.expected_at));
  return <div className="ctl-trigger-marks-wrap">
    <p id={labelId} className="eyebrow ctl-health-label">Last 36 h</p>
    {fired.length
      ? <ol className="ctl-trigger-marks" aria-labelledby={labelId}>
        {fired.map((row, index) => {
          const kind = markKind(row, now);
          const said = `${row.trigger} ${words("trigger_outcome", row.outcome)}${isLate(row, now) ? ", late" : ""}`;
          return <li key={`${row.trigger}|${row.expected_at}|${index}`} className="ctl-trigger-mark" data-kind={kind} data-required={row.required ? "true" : undefined} title={said}>
            <span className="screen-reader">{said} at </span><span className="screen-reader"><LocalTime at={row.expected_at} date={otherDay(row.expected_at, now)} /></span>
          </li>;
        })}
      </ol>
      : <p className="meta">No trigger has fired in the last 36 h.</p>}
    <ul className="strip-legend ctl-trigger-legend" aria-label="Key">
      {(["ok", "late", "failed"] as const).map((kind) => <li key={kind}><span className="ctl-trigger-mark ctl-trigger-swatch" data-kind={kind} aria-hidden="true" />{MARK_WORDS[kind]}</li>)}
    </ul>
  </div>;
}

// ---------------------------------------------------------------------------------------------------- 14 days
/** The reliability strip's days: Iris's required plans written and missed, per America/Denver day, from `daily.*`. */
export const RELIABILITY_DAYS = 14;
export const RELIABILITY_KEYS = ["daily.iris_required_written", "daily.iris_required_missed"] as const;
export type ReliabilityDay = { readonly date: string; readonly written: number | null; readonly missed: number | null };
export type Reliability = { readonly days: readonly ReliabilityDay[] | null; readonly failed: boolean; readonly retry: () => void };

/** The 14 local days ending `today`, oldest first, each with its counts from API-04's day buckets (null where none). */
export function reliabilityDays(series: Series | null, today: string, days = RELIABILITY_DAYS): ReliabilityDay[] {
  const byDate = new Map<string, number>();
  series?.t.forEach((at, index) => byDate.set(localDate(new Date(at * 1000).toISOString()), index));
  const value = (key: string, date: string): number | null => {
    const index = byDate.get(date);
    const column = series?.series[key];
    const raw = index === undefined || !column ? null : column.avg[index];
    return raw === null || raw === undefined || !Number.isFinite(raw) ? null : Math.round(raw);
  };
  return Array.from({ length: days }, (_, back) => dateShift(today, back - days + 1)).map((date) => ({
    date, written: value(RELIABILITY_KEYS[0], date), missed: value(RELIABILITY_KEYS[1], date),
  }));
}

/** API-04's `daily.iris_required_*` over the last 14 local days, one bucket a day, read every 10 min (it changes daily). */
export function useIrisReliability(): Reliability {
  const today = useClockValue((now) => localDate(new Date(now).toISOString()));
  const params = { metric: [...RELIABILITY_KEYS], from: localDayStart(dateShift(today, 1 - RELIABILITY_DAYS)), to: localDayStart(dateShift(today, 1)), points: RELIABILITY_DAYS + 1 };
  const read = useApiRead("API-04", params, { intervalMs: 10 * 60_000 });
  const data = read.data ?? null;
  return { days: data ? reliabilityDays(data, today) : null, failed: read.isError && !data, retry: () => { void read.refetch(); } };
}

const dayWords = (date: string) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${date}T12:00:00Z`));

/** "Last 14 days: 41 of 42 required plans written" over a strip of the days, a missed plan loud and a quiet day quiet. */
function ReliabilityStrip({ reliability, today }: { reliability: Reliability; today: string }) {
  const { days, failed } = reliability;
  const known = (days ?? []).filter((day) => day.written !== null || day.missed !== null);
  const written = known.reduce((sum, day) => sum + (day.written ?? 0), 0);
  const due = known.reduce((sum, day) => sum + (day.written ?? 0) + (day.missed ?? 0), 0);
  const cells: StripCell[] = (days ?? []).map((day) => {
    const partial = day.date === today;
    const counted = day.written !== null || day.missed !== null;
    return {
      key: day.date, level: counted ? missedLevel(day.missed ?? 0) : null, partial,
      label: counted
        ? `${dayWords(day.date)}: ${day.written ?? "—"} required ${day.written === 1 ? "plan" : "plans"} written, ${day.missed ?? "—"} missed${partial ? " so far" : ""}`
        : `${dayWords(day.date)}: no data`,
    };
  });
  return <div className="ctl-reliability">
    <p className="ctl-reliability-words">
      <span className="eyebrow ctl-health-label">Last {RELIABILITY_DAYS} days</span>{" "}
      {failed ? <span className="meta">Couldn't load the daily counts. <button type="button" className="btn ctl-inline-retry" onClick={reliability.retry}>Retry</button></span>
        : days === null ? <span className="meta">Loading…</span>
          : known.length ? <span className="ctl-reliability-count">{written} of {due} required plans written</span> : <span className="meta">No daily counts yet</span>}
    </p>
    <StatusStrip label={`Iris required plans, day by day for ${RELIABILITY_DAYS} days`} cells={cells} state={failed ? "error" : days === null ? "loading" : "ready"} />
  </div>;
}

export type PlannerHealthProps = {
  /** API-07 `health`: `planner_trigger_ledger` over the last 36 h and the expected rows ahead. */
  health: readonly PlannerTrigger[];
  state: PanelState;
  /** When API-07 answered (epoch ms): lateness is read against it. */
  now: number;
  onRetry?: () => void;
  /** The 14-day strip's read (`useIrisReliability`); left out, the card has no strip. */
  reliability?: Reliability;
};

/**
 * Planner health (SCR-03, redesign §5.11): the last 36 h of triggers as a row of marks, the next trigger, any required
 * trigger that needs someone (missed, timed out, failed delivery or late: ▲ and an amber rule, so the owner on call can
 * tell whether a missed SUNSET is urgent), the 14-day reliability strip from `daily.iris_required_*`, and the whole ledger
 * in a disclosure. No attempts column and no recent plans.
 */
export function PlannerHealth({ health, state, now, onRetry, reliability }: PlannerHealthProps) {
  const { shown, rest } = healthRows(health, now);
  const next = [...health].filter((row) => epoch(row.expected_at) > now).sort((a, b) => epoch(a.expected_at) - epoch(b.expected_at))[0] ?? null;
  const attention = shown.filter((row) => epoch(row.expected_at) <= now && needsAttention(row, now));
  const ledger = [...shown.filter((row) => !attention.includes(row)), ...rest];
  const today = localDate(new Date(now).toISOString());
  const panelState: PanelState = state === "ready" && health.length === 0 ? "empty" : state;
  return <Panel title="Planner health" thing="planner health" state={panelState} reserve="md" onRetry={onRetry}
    empty={<EmptyState message="No planner triggers in the last 36 h." detail="Each MIDNIGHT, SUNRISE and SUNSET trigger and every other planner trigger shows here." />}>
    <TriggerMarks rows={health} now={now} />
    {next && <p className="ctl-next-trigger">Next <span className="trigger-name">{next.trigger}</span> at <LocalTime at={next.expected_at} date={otherDay(next.expected_at, now)} /></p>}
    {attention.length > 0 && <TriggerList rows={attention} now={now} />}
    {reliability && <ReliabilityStrip reliability={reliability} today={today} />}
    {ledger.length > 0 && <Details summary={<>Trigger ledger <span className="trigger-more">· {ledger.length}</span></>} className="trigger-all">
      <TriggerList rows={ledger} now={now} />
    </Details>}
  </Panel>;
}
