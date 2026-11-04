// SPDX-License-Identifier: MIT
import { formatNumber, formatValue, localDate } from "../lib/format";
import type { Plan } from "../lib/operator-api";
import { words } from "../lib/words";
import { LocalTime } from "./common";
import { Mark } from "./control/marks";
import { ValidityBar } from "./control/validity-bar";
import { Details } from "./details";
import { Kv } from "./kv";
import { Panel, type PanelState } from "./panel";

/** API-07's `PlanRecord`, as the client parses it. */
type PlanRecord = NonNullable<Plan["plan"]>;
const NBSP = " ";
const HOUR_S = 3600;

/** True when `instant` falls on another America/Denver day than `nowMs`, so its time carries the date. */
export function otherDay(instant: string, nowMs: number): boolean {
  return localDate(instant) !== localDate(new Date(nowMs).toISOString());
}

/** "6 h 48 min left" (hours and minutes), "40 min left" under an hour, "ended" once past; null when the time is unreadable. */
export function timeLeft(until: string, nowMs: number): string | null {
  const seconds = (Date.parse(until) - nowMs) / 1000;
  if (!Number.isFinite(seconds)) return null;
  if (seconds <= 0) return "ended";
  if (seconds < HOUR_S) return `${Math.max(1, Math.floor(seconds / 60))}${NBSP}min left`;
  const hours = Math.floor(seconds / HOUR_S), minutes = Math.floor((seconds % HOUR_S) / 60);
  return `${hours}${NBSP}h${minutes ? `${NBSP}${minutes}${NBSP}min` : ""} left`;
}

/** One stress window of a plan (`StressWindow`, verdify_schemas/plan.py), its codes still raw. */
export type StressLine = { kind: string; severity: string | null; start: string | null; end: string | null; mitigation: string | null };
/** Why one setting was planned: from, to, the effect Iris expects and the evidence it names. */
export type RationaleLine = { param: string; from: number | null; to: number | null; why: string | null; evidence: string | null };
/** A plan waypoint's reason (`climate_intents[].reason`) at its time. */
export type ReasonLine = { at: string | null; reason: string };
export type PlanDetailText = { stress: StressLine[]; rationale: RationaleLine[]; reasons: ReasonLine[] };

type Json = Readonly<Record<string, unknown>>;
const object = (value: unknown): Json | null => (value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Json) : null);
const list = (value: unknown): readonly unknown[] => (Array.isArray(value) ? value : []);
const text = (value: unknown): string | null => (typeof value === "string" && value.trim() !== "" ? value : null);
const number = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

/**
 * What a plan's `detail` holds as API-07 serves it (`hypothesis_structured` and `climate_intents` as stored): its stress
 * windows, the per-setting rationale (`hypothesis_structured.rationale`, and a climate intent that names its setting)
 * and each other climate intent's reason. Only strings and finite numbers are read; anything else is left out.
 */
export function planDetail(detail: Json | null | undefined): PlanDetailText {
  const structured = object(detail?.hypothesis_structured) ?? {};
  const stress: StressLine[] = [];
  for (const item of list(structured.stress_windows ?? detail?.stress_windows)) {
    const span = object(item);
    const kind = text(span?.kind);
    if (!span || !kind) continue;
    stress.push({ kind, severity: text(span.severity), start: text(span.start), end: text(span.end), mitigation: text(span.mitigation) });
  }
  const rationale: RationaleLine[] = [];
  for (const item of list(structured.rationale)) {
    const entry = object(item);
    const param = text(entry?.parameter) ?? text(entry?.param);
    if (!entry || !param) continue;
    rationale.push({ param, from: number(entry.old_value), to: number(entry.new_value), why: text(entry.expected_effect) ?? text(entry.rationale), evidence: text(entry.forecast_anchor) });
  }
  const reasons: ReasonLine[] = [];
  for (const item of list(detail?.climate_intents)) {
    const intent = object(item);
    const why = text(intent?.rationale) ?? text(intent?.reason);
    if (!intent || !why) continue;
    const param = text(intent.param) ?? text(intent.parameter);
    if (param) rationale.push({ param, from: null, to: null, why, evidence: null });
    else reasons.push({ at: text(intent.ts), reason: why });
  }
  return { stress, rationale, reasons };
}

/** A setting's label and unit, from API-07's lanes, for the rationale lines. */
type SettingNames = ReadonlyMap<string, { label: string; unit: string | null }>;

/** "07:02", or "Nov 1, 22:58" on another day; the full local time is in its title. */
function When({ at, now }: { at: string; now: number }) {
  return <LocalTime at={at} date={otherDay(at, now)} />;
}

function StressItem({ line, now }: { line: StressLine; now: number }) {
  return <li>
    <strong>{words("stress_kind", line.kind)}</strong>
    {line.severity && <> · {words("stress_severity", line.severity)}</>}
    {line.start && line.end && <> · <When at={line.start} now={now} />–<When at={line.end} now={now} /></>}
    {line.mitigation && <>: {line.mitigation}</>}
  </li>;
}

function RationaleItem({ line, names }: { line: RationaleLine; names: SettingNames }) {
  const setting = names.get(line.param);
  const value = (amount: number) => (setting ? formatValue(amount, setting.unit) : formatNumber(amount));
  const change = line.from !== null && line.to !== null ? ` ${value(line.from)} → ${value(line.to)}` : line.to !== null ? ` → ${value(line.to)}` : "";
  return <li>
    {setting && <>{setting.label} </>}<span className="setting-key">{line.param}</span>{change}
    {line.why && <>: {line.why}</>}
    {line.evidence && <span className="muted"> Evidence: {line.evidence}</span>}
  </li>;
}

/** The disclosure: conditions and expected outcome, then the stress windows, per-setting rationale and intent reasons. */
function PlanDetails({ plan, names, now }: { plan: PlanRecord; names: SettingNames; now: number }) {
  const detail = planDetail(plan.detail);
  return <Details summary="Conditions, stress windows and rationale" className="plan-detail">
    <Kv items={[
      { key: "conditions", term: "Conditions", value: plan.conditions_summary || null, reason: "not recorded" },
      { key: "outcome", term: "Expected outcome", value: plan.expected_outcome || null, reason: "not recorded" },
    ]} />
    <h3>Stress windows</h3>
    {detail.stress.length ? <ul>{detail.stress.map((line, index) => <StressItem key={index} line={line} now={now} />)}</ul> : <p className="muted">None recorded</p>}
    <h3>Per-setting rationale</h3>
    {detail.rationale.length ? <ul>{detail.rationale.map((line, index) => <RationaleItem key={index} line={line} names={names} />)}</ul> : <p className="muted">None recorded</p>}
    {detail.reasons.length > 0 && <>
      <h3>Waypoint reasons</h3>
      <ul>{detail.reasons.map((line, index) => <li key={index}>{line.at && <><When at={line.at} now={now} /> · </>}{line.reason}</li>)}</ul>
    </>}
  </Details>;
}

/**
 * The plan in force (redesign §5.11): its trigger and id, the validity bar from when it was written to when it ends with
 * now marked and the time left, Iris's hypothesis marked AI, the disclosure, and what applies after it.
 */
function InForce({ plan, names, now }: { plan: PlanRecord; names: SettingNames; now: number }) {
  const until = plan.valid_until ?? null;
  const left = until ? timeLeft(until, now) : null;
  return <>
    <p className="plan-facts"><span className="mono plan-trigger">{plan.trigger}</span> · <span className="mono plan-id">{plan.plan_id}</span></p>
    <div className="ctl-validity-row">
      {until && <ValidityBar from={plan.written_at} to={until} now={now} />}
      <p className="ctl-validity-words">
        written <When at={plan.written_at} now={now} /> <span aria-hidden="true">→</span> {until
          ? <>valid to <When at={until} now={now} />{left && <> · <strong className="plan-left">{left}</strong></>}</>
          : <span className="muted">no end time recorded</span>}
      </p>
    </div>
    {plan.hypothesis
      ? <p className="plan-hypothesis">“{plan.hypothesis}” <Mark kind="ai" /></p>
      : <p className="plan-hypothesis">— <span className="muted">no hypothesis recorded</span></p>}
    <PlanDetails plan={plan} names={names} now={now} />
    {until && <p className="plan-after meta">After <When at={until} now={now} />: band and defaults until the next plan</p>}
  </>;
}

export type PlanCardProps = {
  /** The API-07 answer, or null while it loads or after it failed. */
  data: Plan | null;
  state: PanelState;
  /** When API-07 answered (epoch ms): the time left counts from it. */
  now: number;
  onRetry?: () => void;
};

/**
 * The Plan in force card (SCR-03, US-PLN-01) from API-07 `plan`. With no plan in force it reads "No plan in force: band
 * and defaults apply since HH:MM" from `no_plan_since`. Times are America/Denver, 24-hour, with the date when they fall
 * on another day. It writes nothing.
 */
export function PlanCard({ data, state, now, onRetry }: PlanCardProps) {
  const names: SettingNames = new Map((data?.lanes ?? []).map((lane) => [lane.param, { label: lane.label, unit: lane.unit ?? null }]));
  const since = data?.no_plan_since ?? null;
  return <Panel title="Plan in force" thing="the plan in force" state={state} reserve="md" onRetry={onRetry}>
    {data && (data.plan
      ? <InForce plan={data.plan} names={names} now={now} />
      : <p className="plan-none">No plan in force: band and defaults apply{since && <> since <When at={since} now={now} /></>}</p>)}
  </Panel>;
}
