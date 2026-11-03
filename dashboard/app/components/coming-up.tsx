// SPDX-License-Identifier: MIT
/**
 * Coming up on Now (screens SCR-01, US-NOW-05): the next plan change (AI; manual settings left out), the next Iris trigger,
 * the next run per line, a paused one naming who paused it and why, and sunrise and sunset, from API-07, API-IRR-02 and
 * API-IRR-01. API-07 and API-IRR-02 poll every 60 s and pause while the tab is hidden; each row fails alone, and a failed
 * read says so, never "none". Reads only.
 */
import type { ReactNode } from "react";
import { FORECAST_RULE } from "../charts/marks";
import { formatValue, localDate, shortTime } from "../lib/format";
import { dayClock } from "../lib/last-water";
import type { IrrigationLine, IrrigationStatus, Me, Occurrence, Plan, PlanLane, PlanWaypoint, PlannerTrigger, Schedule, ScheduleAgenda } from "../lib/operator-api";
import { READ_INTERVAL_MS, panelRead, useApiRead, type PanelRead } from "../lib/queries";
import { keptNumber, withUnit } from "../lib/setting-values";
import { useMe } from "../lib/shell";
import { occurrenceWords, pauseUntilWords, pausedNow } from "../lib/water";
import { Badge } from "./badge";
import { LocalTime } from "./common";
import { useClockValue } from "./freshness";
import { Panel, PanelError } from "./panel";
import { otherDay } from "./plan-card";

const PLAN_PARAMS = { hours: 24 } as const;
const AGENDA_PARAMS = { days: 7 } as const;
export const NO_PLAN = "No plan in force";
export const NO_TRIGGER = "Next Iris trigger not scheduled yet";
export const NO_RUNS = "No run planned in the next 7 days";
export const NO_RUNS_BEFORE_ENGINE = "Schedules run with the irrigation firmware";
const DEGRADED_ONLY = "only if VPD sensing is degraded";

const timeOf = (instant: string | null | undefined): number => (instant ? Date.parse(instant) : Number.NaN);
const validTime = (instant: string | null | undefined): instant is string => Number.isFinite(timeOf(instant));

/** A lane's value in words: a switch ON or OFF, a number with the decimals it carries, without its unit. */
function laneValue(lane: Pick<PlanLane, "param" | "unit">, value: number): string {
  if (lane.param.startsWith("sw_") && (value === 0 || value === 1)) return value === 1 ? "ON" : "OFF";
  return keptNumber(value, null, lane.unit);
}

/** One lane's part of the next transition: "VPD Mister Engage 1.60 → 1.70 kPa", and who planned it. */
export type PlanChange = { readonly param: string; readonly text: string; readonly source: PlanWaypoint["source"] };
export type Transition = { readonly at: string; readonly changes: readonly PlanChange[] };

/**
 * The next plan transition: the earliest time, after now, at which a planned lane's value changes, with every lane's
 * change at that time. A lane in force by hand (`applied_from` manual, or `desired.manual`) is left out, as Iris skips it,
 * and so is a waypoint the API marks not effective. A waypoint that repeats what the lane already holds changes nothing:
 * Iris's plans restate most settings at each time, so it never counts as the transition or reads "1.60 → 1.60". Null when
 * no lane changes.
 */
export function nextTransition(plan: Pick<Plan, "lanes">, now: number): Transition | null {
  const next = plan.lanes.flatMap((lane) => {
    if (lane.applied_from === "manual" || lane.desired?.manual === true) return [];
    const from = lane.device.value === null ? null : laneValue(lane, lane.device.value);
    const waypoint = lane.waypoints
      .filter((point) => point.effective && point.value !== null && timeOf(point.at) > now)
      .sort((a, b) => timeOf(a.at) - timeOf(b.at))
      .find((point) => laneValue(lane, point.value as number) !== from);
    return waypoint ? [{ lane, waypoint, from }] : [];
  });
  if (!next.length) return null;
  const earliest = Math.min(...next.map(({ waypoint }) => timeOf(waypoint.at)));
  const at = next.filter(({ waypoint }) => timeOf(waypoint.at) === earliest);
  return {
    at: at[0].waypoint.at,
    changes: at.map(({ lane, waypoint, from }) => ({
      param: lane.param, source: waypoint.source, text: `${lane.label} ${from ?? "—"} → ${withUnit(laneValue(lane, waypoint.value as number), lane.unit)}`,
    })),
  };
}

/** The earliest row still `expected` whose `expected_at`, when it fires, is after now; null when the ledger holds none yet. */
export function nextTrigger(health: readonly PlannerTrigger[], now: number): PlannerTrigger | null {
  return health.filter((row) => row.outcome === "expected" && timeOf(row.expected_at) > now).sort((a, b) => timeOf(a.expected_at) - timeOf(b.expected_at))[0] ?? null;
}

/** Other people read by username or source; only the signed-in person reads by display name. */
export function actorName(name: string, me: Pick<Me, "username" | "display_name"> | null): string {
  return me?.display_name && me.username === name ? me.display_name : name;
}

/** "12 min · Wall MWF": the schedule's valve-open minutes and label, and any note the API or the slot carries. */
function runDetail(occurrence: Occurrence, schedule: Schedule | undefined, now: number): string[] {
  const note = occurrenceWords(occurrence, now);
  const degraded = schedule?.degraded_only && !(note ?? "").includes("VPD sensing is degraded") ? DEGRADED_ONLY : null;
  return [
    ...(schedule ? [formatValue(((schedule.on_s ?? 0) * (schedule.count ?? 1)) / 60, "min")] : []),
    ...(schedule?.label ? [schedule.label] : []),
    ...(degraded ? [degraded] : []),
    ...(note ? [note] : []),
  ];
}

type PausableLine = Pick<IrrigationLine, "line_id" | "paused_until" | "paused_by" | "pause_reason">;
/** "Paused until 14:00 by Jason: rain", "by Jason" alone when no reason was given. */
export function pausedWords(line: PausableLine, now: number, me: Pick<Me, "username" | "display_name"> | null): string {
  const until = pauseUntilWords(line.paused_until as string, localDate(new Date(now).toISOString()));
  const who = line.paused_by ? ` by ${actorName(line.paused_by.name, me)}` : "";
  return `Paused until ${until}${who}${line.pause_reason ? `: ${line.pause_reason}` : ""}`;
}

/**
 * The next run per line, soonest first: the earliest `planned` occurrence after now, joined to its schedule: "L01 Wed
 * 07:34 · 12 min · Wall MWF". A line paused past now reads "L01 Paused until 14:00 by Jason: rain · next Thu 07:34", its
 * next run the first `planned` occurrence after the pause. A line with no planned run is left out.
 */
export function nextRuns(agenda: Pick<ScheduleAgenda, "schedules" | "occurrences">, lines: readonly PausableLine[], now: number, me: Pick<Me, "username" | "display_name"> | null): { key: string; text: string }[] {
  const planned = agenda.occurrences.filter((item) => item.status === "planned" && validTime(item.planned_start)).sort((a, b) => timeOf(a.planned_start) - timeOf(b.planned_start));
  return [...new Set(planned.map((item) => item.line_id))].flatMap((lineId) => {
    const line = lines.find((item) => item.line_id === lineId);
    const paused = line && pausedNow(line, now) ? line : null;
    const after = paused ? timeOf(paused.paused_until) : now;
    const occurrence = planned.find((item) => item.line_id === lineId && timeOf(item.planned_start) > after);
    if (!occurrence) return [];
    const when = dayClock(occurrence.planned_start, now);
    const text = paused
      ? `${lineId} ${pausedWords(paused, now, me)} · next ${when}`
      : [`${lineId} ${when}`, ...runDetail(occurrence, agenda.schedules.find((item) => item.slot_id === occurrence.slot_id), now)].join(" · ");
    return [{ key: lineId, at: timeOf(occurrence.planned_start), text }];
  }).sort((a, b) => a.at - b.at).map(({ key, text }) => ({ key, text }));
}

/** Today's sunrise and sunset from API-IRR-02's solar times; after sunset, tomorrow's sunrise. Null with no solar row. */
export function sunWords(solar: ScheduleAgenda["solar"], now: number): string | null {
  const today = solar[0];
  if (!today) return null;
  if (now <= timeOf(today.sunset)) return `Sunrise ${shortTime(today.sunrise)} · Sunset ${shortTime(today.sunset)}`;
  const next = solar[1];
  if (!next) return `Sunset ${shortTime(today.sunset)}`;
  return `Sunrise ${otherDay(next.sunrise, now) ? "tomorrow " : ""}${shortTime(next.sunrise)}`;
}

/** One row: its term and what the read says, its failure or its wait. */
function Row({ name, term, failed, thing, onRetry, pending, children }: { name: string; term: string; failed: boolean; thing: string; onRetry: () => void; pending: boolean; children: ReactNode }) {
  return <div data-coming={name}>
    <dt>{term}</dt>
    <dd>{failed ? <PanelError thing={thing} text={`${thing} unavailable`} onRetry={onRetry} /> : pending ? <span className="muted">Loading…</span> : children}</dd>
  </div>;
}

function PlanChanges({ plan, now }: { plan: Plan; now: number }) {
  const next = nextTransition(plan, now);
  const ends = plan.plan?.valid_until;
  if (!next) {
    if (!plan.plan) return <>{NO_PLAN}</>;
    return <>No planned change before the plan ends{validTime(ends) && <> at <LocalTime at={ends} date={otherDay(ends, now)} /></>}</>;
  }
  return <ul className="coming-list">
    {next.changes.map((change) => <li key={change.param}>
      <LocalTime at={next.at} date={otherDay(next.at, now)} /> · {change.text} · {change.source === "iris" ? <Badge kind="ai" /> : FORECAST_RULE}
    </li>)}
  </ul>;
}

export function ComingUp({ irrigation }: { irrigation: PanelRead<IrrigationStatus> }) {
  const plan = panelRead(useApiRead("API-07", PLAN_PARAMS, { intervalMs: READ_INTERVAL_MS.plan }));
  const agenda = panelRead(useApiRead("API-IRR-02", AGENDA_PARAMS, { intervalMs: READ_INTERVAL_MS.schedules }));
  const me = useMe();
  // The clock's minute, on the server's clock: what comes next is worth one render a minute.
  const now = useClockValue((clock) => Math.floor(clock / 60_000) * 60_000);
  const trigger = plan.data ? nextTrigger(plan.data.health, now) : null;
  const sun = agenda.data ? sunWords(agenda.data.solar, now) : null;
  const runsFailed = agenda.failed || irrigation.failed;
  const retryRuns = () => { agenda.retry(); irrigation.retry(); };
  const runs = agenda.data && irrigation.data ? nextRuns(agenda.data, irrigation.data.lines, now, me) : [];
  return <Panel id="coming-up" title="Coming up" thing="what is coming up" state={plan.pending && agenda.pending ? "loading" : "ready"} reserve="sm">
    <dl className="coming-up">
      <Row name="plan" term="Next change" failed={plan.failed} thing="Plan" onRetry={plan.retry} pending={plan.pending}>{plan.data && <PlanChanges plan={plan.data} now={now} />}</Row>
      <Row name="iris" term="Next Iris trigger" failed={plan.failed} thing="Iris trigger" onRetry={plan.retry} pending={plan.pending}>
        {trigger ? <>{trigger.trigger} at <LocalTime at={trigger.expected_at} date={otherDay(trigger.expected_at, now)} /></> : NO_TRIGGER}
      </Row>
      <Row name="runs" term="Next run" failed={runsFailed} thing="Next run" onRetry={retryRuns} pending={agenda.pending || irrigation.pending}>
        {runs.length
          ? <ul className="coming-list">{runs.map((run) => <li key={run.key}>{run.text}</li>)}</ul>
          : irrigation.data?.engine === false ? NO_RUNS_BEFORE_ENGINE : NO_RUNS}
      </Row>
      <Row name="sun" term="Sun" failed={agenda.failed} thing="Sun times" onRetry={agenda.retry} pending={agenda.pending}>{sun ?? "Sun times unavailable"}</Row>
    </dl>
  </Panel>;
}
