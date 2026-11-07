// SPDX-License-Identifier: MIT
import { Fragment, memo, type ReactNode } from "react";
import { Link } from "react-router";
import { TIME_UNAVAILABLE, duration, localDate, localTime } from "../lib/format";
import type { Actor, Setting } from "../lib/operator-api";
import { keptValueText, rangeText, settingDisplay, settingUnit, steppedNumber } from "../lib/setting-values";
import { useRole } from "../lib/session";
import { isAiSource, type StateReason } from "../lib/vocab";
import { LocalTime, cls } from "./common";
import { BoundsBar } from "./control/bounds-bar";
import { Mark, PillBadge, isManual, settingPill, useRecentWrite, writtenAt, type SettingPill } from "./control/marks";
import { SettingSummary, readOnlyWords } from "./control/setting-help";
import { AgeLabel, useClockValue, useNow } from "./freshness";
import { StateReasonLine } from "./state-reason-line";

/**
 * A setting key that wraps only after its underscores ("sw_direct_wet_stress_<wbr>override_<wbr>enabled"), never in
 * the middle of a word: identifiers keep `overflow-wrap: normal` (control.css), and each `<wbr>` adds no text.
 */
export function breakableKey(key: string): ReactNode {
  const parts = key.split("_");
  return parts.map((part, index) => <Fragment key={index}>{part}{index < parts.length - 1 && <>_<wbr /></>}</Fragment>);
}

/**
 * Settings' rows (SCR-05, redesign §5.10). Everything a row says comes from API-09 as served: `state`, `state_reason`,
 * `applied_from`, `effective`, `writable` and `read_only_reason` are the API's, so the row never compares a readback or
 * decides writability. It keys on `group`, never on `readback_via` alone: a Band in force row is DERIVED with its age,
 * though the API serves it with no readback path.
 *
 * A row is two lines. The first: the label and key, the device value large, its age (`SettingAgeLabel`, every row), the
 * bounds bar (the allowed range, the value, the next planned value as a hollow ring) and the one pill (redesign §4.4). The second, one `.meta` line: who
 * wrote the value in force, what Iris plans next and, while a change is on its way, the request and its reason.
 */

// ---------------------------------------------------------------------------------------------------- groups
/**
 * The groups in their fixed order, with their headings (no column sort). The API's `read_only` group is named by what it
 * holds, the rows kept for reference (retired, legacy limits, readback-only and derived), so it never shares a name with
 * the rail's "Read-only" filter, which counts every row that cannot be written, Band in force's included.
 */
export const SETTING_GROUPS: readonly (readonly [string, string])[] = [
  ["planner_policy", "Planner policy"],
  ["scheduled_policy", "Scheduled policy"],
  ["controller_safety", "Controller safety"],
  ["crop_band", "Crop band"],
  ["site_water_caps", "Site water caps"],
  ["band_in_force", "Band in force"],
  ["read_only", "Reference only"],
];
const GROUP_WORDS: Readonly<Record<string, string>> = Object.fromEntries(SETTING_GROUPS);
/** A group's heading; a group this build does not know reads raw. */
export const groupLabel = (group: string): string => GROUP_WORDS[group] ?? group;
const CLASS_WORDS: Readonly<Record<string, string>> = {
  planner_policy: "Planner policy", scheduled_policy: "Scheduled policy", controller_safety: "Controller safety", crop_band: "Crop band",
  irrigation_site: "Site water caps", readback_context: "Readback context", retired: "Retired",
};
/** A `control_class` in words, for the drawer's "class and key" line; a class this build does not know reads raw. */
export const classLabel = (controlClass: string): string => CLASS_WORDS[controlClass] ?? controlClass;

// ---------------------------------------------------------------------------------------------------- what a row says
/** The states that show the request line: the value being applied is not on the device yet, or something else is. */
const PENDING: readonly string[] = ["requested", "not_confirmed", "held"];
export const isPending = (state: string): boolean => PENDING.includes(state);

/** Who wrote, as people read it: Iris by name, an agent by its name, a person by their username. */
export const actorName = (actor: Actor): string => (actor.source === "iris" ? "Iris" : actor.name);

/** "0.50–2.50 · 0.05": the bounds (`planner_effective_bounds`) at the step's decimals, then the step; switches ON or OFF. */
export function boundsText(setting: Pick<Setting, "kind" | "min" | "max" | "step" | "options">): string {
  if (setting.kind === "switch") return "ON or OFF";
  if (setting.kind === "enum" && setting.options?.length) return setting.options.map((option) => option.label).join(", ");
  const { min, max, step } = setting;
  const known = (value: number | null | undefined): value is number => value !== null && value !== undefined && Number.isFinite(value);
  const range = known(min) && known(max) ? rangeText(min, max, null, step) : known(min) ? `at least ${steppedNumber(min, step)}` : known(max) ? `at most ${steppedNumber(max, step)}` : null;
  const grid = known(step) && step > 0 ? steppedNumber(step, step) : null;
  return [range, grid].filter(Boolean).join(" · ") || "—";
}

/** The value a row's value and the drawer's field start from: the one in force (desired, plan, else the device's). */
export function valueInForce(setting: Pick<Setting, "applied_from" | "desired" | "plan" | "readback">): number | null {
  const desired = setting.desired?.value;
  if ((setting.applied_from === "manual" || setting.applied_from === "desired") && desired !== null && desired !== undefined) return desired;
  const planned = setting.plan?.value;
  if (setting.applied_from === "plan" && planned !== null && planned !== undefined) return planned;
  return setting.readback?.value ?? null;
}

/**
 * The request the row is waiting on: the value being applied, when it was asked for, who asked, and the platform's
 * `state_reason` for it. Only a desired row carries one; a plan value has none, so its reason line reads the default.
 */
export type Pending = {
  readonly value: number; readonly at: string | null; readonly by: ReactNode; readonly manual: boolean; readonly reason: string | null;
  readonly stateReason: StateReason | null;
};
/** `desired.value`, or `plan.value` when `applied_from` is plan; null when neither is being applied. */
export function pendingRequest(setting: Pick<Setting, "applied_from" | "desired" | "plan">): Pending | null {
  const plan = setting.plan;
  if (setting.applied_from === "plan" && plan && plan.value !== null && plan.value !== undefined) {
    return { value: plan.value, at: plan.written_at, by: planWriter(plan.source), manual: false, reason: null, stateReason: null };
  }
  const desired = setting.desired;
  if (desired && desired.value !== null && desired.value !== undefined) {
    return {
      value: desired.value, at: desired.created_at, by: <Writer actor={desired.actor} />, manual: desired.manual, reason: desired.reason ?? null,
      stateReason: (desired.state_reason ?? null) as StateReason | null,
    };
  }
  return null;
}

/** The value in force was written by Iris or a named agent: SCR-05's "Changed by an agent" filter. */
export function changedByAgent(setting: Pick<Setting, "applied_from" | "desired" | "plan">): boolean {
  if (setting.applied_from === "plan") return setting.plan?.source === "iris";
  if (setting.applied_from === "manual" || setting.applied_from === "desired") return Boolean(setting.desired && isAiSource(setting.desired.actor.source));
  return false;
}

/** Settings' "Changed in the last …" windows (#1333): 24 h by default, or 7 d. */
export const RECENT_WINDOWS = { "24h": { label: "24 h", ms: 86_400_000 }, "7d": { label: "7 d", ms: 7 * 86_400_000 } } as const;
export type RecentWindow = keyof typeof RECENT_WINDOWS;
/**
 * When the newest write in force was made: the desired row's `created_at` (a person, an agent, or a release), or the
 * plan's `written_at` while an Iris or forecast-rule plan value applies. Null when neither is known.
 */
export function lastWriteAt(setting: Pick<Setting, "desired" | "plan" | "applied_from">): number | null {
  const times = [setting.desired ? Date.parse(setting.desired.created_at) : Number.NaN];
  if (setting.applied_from === "plan" && setting.plan) times.push(Date.parse(setting.plan.written_at));
  const known = times.filter(Number.isFinite);
  return known.length ? Math.max(...known) : null;
}
/** Settings' "Changed in the last 24 h" (or 7 d): any write in force inside the window, Iris plans included (#1333). */
export function recentlyChanged(setting: Pick<Setting, "desired" | "plan" | "applied_from">, now: number, windowMs: number = RECENT_WINDOWS["24h"].ms): boolean {
  const at = lastWriteAt(setting);
  return at !== null && now - at <= windowMs;
}

/** `/activity/log?target=<param>`: every write to the setting (redesign §2.6). */
export const activityHref = (param: string): string => `/activity/log?target=${encodeURIComponent(param)}`;

/** Where Iris's next planned value lands, when a waypoint is ahead and applies. */
export function plannedNext(setting: Pick<Setting, "desired" | "plan">): number | null {
  const next = setting.plan?.next;
  if (isManual(setting) || !next || !next.effective || next.value === null || next.value === undefined) return null;
  return next.value;
}

// ---------------------------------------------------------------------------------------------------- small parts
/** "20 s": how long a request has been out, ticking silently. */
function Elapsed({ at }: { at: string | null | undefined }) {
  const now = useNow();
  const time = at ? Date.parse(at) : Number.NaN;
  if (!Number.isFinite(time)) return null;
  return <span className="age">{duration(Math.max(0, Math.floor((now - time) / 1000)))}</span>;
}
/** "2 min ago", ticking silently; `bare` leaves out the `.age` span, for a parent that is one. */
function Ago({ at, bare = false }: { at: string | null | undefined; bare?: boolean }) {
  const now = useNow();
  const time = at ? Date.parse(at) : Number.NaN;
  const words = Number.isFinite(time) ? `${duration(Math.max(0, Math.floor((now - time) / 1000)))} ago` : TIME_UNAVAILABLE;
  return bare ? <>{words}</> : <span className="age">{words}</span>;
}
/** A clock time, with its date when it is not today's (`today`, America/Denver). */
function When({ at, today }: { at: string; today: string | null }) {
  const valid = Number.isFinite(Date.parse(at));
  return <LocalTime at={at} date={valid && today !== null && localDate(at) !== today} />;
}

const sep = <span className="ctl-sep" aria-hidden="true"> · </span>;
/** A writer by name: the name, then AI for Iris and the named agents; "<name> · system" for the platform itself. */
function Writer({ actor }: { actor: Actor }) {
  if (isAiSource(actor.source)) return <><span>{actorName(actor)}</span>{sep}<Mark kind="ai" /></>;
  if (actor.source === "system") return <span>{actor.name} · system</span>;
  return <span>{actorName(actor)}</span>;
}
/** A plan's writer: Iris with AI, or the forecast action engine, "Forecast rule · system" and never AI. */
function planWriter(source: string): ReactNode {
  return source === "preemptive" ? <span>Forecast rule · system</span> : <><span>Iris</span>{sep}<Mark kind="ai" /></>;
}

// ---------------------------------------------------------------------------------------------------- the setting's age
/**
 * How old the row's value is (Jason, 2026-11-07, #1132: an age on every row): `derived` for a Band in force row (its
 * value's computed time), `reported` while the device reports a value (`readback.observed_at`, the time the drawer's
 * "reported 2 min ago" counts from), else `changed` from the newest write in force (`lastWriteAt`: the desired row or
 * the plan), else `unknown`. `at` is the ISO time the age counts from.
 */
export type SettingAge = { readonly kind: "derived" | "reported" | "changed"; readonly at: string } | { readonly kind: "unknown"; readonly at: null };
export function settingAge(setting: Pick<Setting, "group" | "readback" | "desired" | "plan" | "applied_from">): SettingAge {
  const observed = setting.readback?.observed_at;
  const value = setting.readback?.value;
  const parses = (at: string | null | undefined): at is string => typeof at === "string" && Number.isFinite(Date.parse(at));
  if (value !== null && value !== undefined && Number.isFinite(value) && parses(observed)) {
    return { kind: setting.group === "band_in_force" ? "derived" : "reported", at: observed };
  }
  const written = lastWriteAt(setting);
  if (written !== null) return { kind: "changed", at: new Date(written).toISOString() };
  return { kind: "unknown", at: null };
}

/**
 * A row's age, beside its device value: "reported 2 min ago" as the drawer words it, "Last data 14 min ago (06:02)"
 * under the STALE rule (readbacks stale, on a row that can be STALE: `AgeLabel`, as the drawer shows it), "changed 3 h
 * ago" for a row the device does not report, "age unknown" when nothing dates it. A Band in force row with a value has its
 * age in its value line ("Derived · 4 min ago"), so the row shows this label for every other row. Ticks silently.
 */
export function SettingAgeLabel({ setting, readbackStale = false }: { setting: Setting; readbackStale?: boolean }) {
  const age = settingAge(setting);
  if (age.kind === "unknown") return <span className="setting-age age" data-age="unknown">age unknown</span>;
  const canBeStale = setting.group !== "band_in_force" && setting.readback_via !== "none";
  if (age.kind === "reported" && readbackStale && canBeStale) {
    return <span className="setting-age" data-age="stale"><AgeLabel observedAt={age.at} stale /></span>;
  }
  return <span className="setting-age age" data-age={age.kind}>{age.kind === "changed" ? "changed " : "reported "}<Ago at={age.at} bare /></span>;
}

// ---------------------------------------------------------------------------------------------------- the device value
export type DeviceProps = {
  setting: Setting;
  /** Device readbacks are stale (API-02 `freshness.readback`), or the controller is not reporting. */
  readbackStale?: boolean;
  /** A site cap's program revision on the controller (API-IRR-01 `program.applied_revision`); null while unknown. */
  programRevision?: number | null;
};

/**
 * The device's own report with its unit (design system §4.2), large. "—" with `readback.reason` when there is no value;
 * a Band in force row is DERIVED with its age and never NO READBACK; NO READBACK shows only for another row with no
 * readback path. A site cap reads the value in the program on the controller ("On controller · rev 42: 360 gal"). While
 * readbacks are stale the value is muted (the row's pill says STALE). `age` adds when it was reported (the drawer). A
 * row reads the value as Plan's lanes do (`settingDisplay`: the key's unit where the registry has none, "5 min" for
 * 300000 ms); the drawer keeps it as stored, with that unit.
 */
export function DeviceLine({ setting, readbackStale = false, programRevision = null, age = false }: DeviceProps & { age?: boolean }) {
  const reading = setting.readback;
  const value = reading?.value;
  const band = setting.group === "band_in_force";
  const noReadback = !band && setting.readback_via === "none";
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return <p className="setting-line setting-value" data-device="none">
      <span className="setting-device-value value-muted">—</span>
      {reading?.reason && <> <span className="state-note">{reading.reason}</span></>}
      {noReadback && <> <Mark kind="no_readback" /></>}
    </p>;
  }
  const text = age ? keptValueText(value, { ...setting, unit: settingUnit(setting) }) : settingDisplay(value, setting);
  if (band) {
    return <p className="setting-line setting-value" data-device="derived">
      <span className="setting-device-value">{text}</span> <Mark kind="derived" />{sep}<span className="setting-age age" data-age="derived"><Ago at={reading?.observed_at} bare /></span>
    </p>;
  }
  if (noReadback) return <p className="setting-line setting-value" data-device="no_readback"><span className="setting-device-value">{text}</span> <Mark kind="no_readback" /></p>;
  let shown = text;
  if (setting.readback_via === "program") {
    const revision = programRevision !== null && programRevision !== undefined && Number.isFinite(programRevision) ? ` · rev ${programRevision}` : "";
    shown = `On controller${revision}: ${text}`;
  }
  if (readbackStale) {
    return <p className="setting-line setting-value" data-device="stale">
      <span className="setting-device-value value-muted">{shown}</span>{age && <> <AgeLabel observedAt={reading?.observed_at} stale /></>}
    </p>;
  }
  return <p className="setting-line setting-value" data-device={setting.readback_via === "program" ? "program" : "confirmed"}>
    <span className="setting-device-value" title={reading?.observed_at && Number.isFinite(Date.parse(reading.observed_at)) ? `Reported ${localTime(reading.observed_at)}` : undefined}>{shown}</span>
    {age && <>{sep}<span className="age">reported </span><Ago at={reading?.observed_at} /></>}
  </p>;
}

/** The text "Sent. This setting has no readback, so it can never show CONFIRMED" (screens SCR-05). */
export const NO_READBACK_SENT = "Sent. This setting has no readback, so it can never show CONFIRMED";

/**
 * A write to a setting with no readback path, while it is the value in force (`state` no_readback, applied from a
 * desired row): it is sent and can never confirm. Never for a Band in force row, and never once a plan has won.
 */
function sentWithoutReadback(setting: Setting): Pending | null {
  if (setting.group === "band_in_force" || setting.readback_via !== "none" || setting.state !== "no_readback") return null;
  return setting.applied_from === "manual" || setting.applied_from === "desired" ? pendingRequest(setting) : null;
}

/**
 * The request on its way, while the row is REQUESTED, NOT CONFIRMED or HELD: "→ 1.55 kPa · 20 s · manual · jason ·
 * “Vandas drying”", then its reason once due (`StateReasonLine`: after 60 s, at once when NOT CONFIRMED, HELD or
 * `no_engine`). The state itself is the row's one pill, so the line carries none. Null when nothing is on its way.
 */
function RequestParts({ setting }: { setting: Setting }): ReactNode {
  const pending = isPending(setting.state) ? pendingRequest(setting) : null;
  const shown = pending ?? sentWithoutReadback(setting);
  if (!shown) return null;
  return <>
    <span className="setting-request" data-request={setting.state}>
      <span aria-hidden="true">→</span> <span className="setting-request-value">{keptValueText(shown.value, setting)}</span>
      {shown.at && <>{sep}<Elapsed at={shown.at} /></>}
      {shown.manual && <>{sep}manual</>}
      {sep}{shown.by}
      {shown.reason && <>{sep}<span className="setting-reason">“{shown.reason}”</span></>}
    </span>
    {pending
      ? <StateReasonLine state={setting.state} reason={pending.stateReason} requestedAt={pending.at} unit={setting.unit} />
      : <span className="state-reason">{NO_READBACK_SENT}</span>}
  </>;
}

/**
 * Who wrote the value in force, when no request is pending: "jason changed this at 08:00 · “Vandas drying” · Iris may
 * adjust", "Iris changed this at 07:02 · AI", "Forecast rule · system", "Released: plan, band or default applies".
 * MANUAL is the row's pill, so the line does not repeat it. `confirmed`: the write was confirmed under 10 minutes ago
 * but a higher pill (MANUAL, STALE) holds the row's one pill, so the line says it in words ("· confirmed 10:43").
 */
function WriterPart({ setting, today, confirmed = false }: { setting: Setting; today: string | null; confirmed?: boolean }): ReactNode {
  if (setting.state === "released") return <span className="setting-writer">Released: plan, band or default applies</span>;
  const desired = setting.desired;
  if ((setting.applied_from === "manual" || setting.applied_from === "desired") && desired && desired.value !== null) {
    const actor = desired.actor;
    const confirmedAt = confirmed && desired.state === "confirmed" && desired.confirmed_at && Number.isFinite(Date.parse(desired.confirmed_at)) ? desired.confirmed_at : null;
    return <span className="setting-writer">
      {actorName(actor)} changed this at <When at={desired.created_at} today={today} />{actor.source === "system" && " · system"}
      {desired.reason && <>{sep}<span className="setting-reason">“{desired.reason}”</span></>}
      {actor.source === "dashboard" && !desired.manual && <>{sep}Iris may adjust</>}
      {isAiSource(actor.source) && <>{sep}<Mark kind="ai" /></>}
      {confirmedAt && <>{sep}<span className="ctl-confirmed">confirmed <When at={confirmedAt} today={today} /></span></>}
    </span>;
  }
  const plan = setting.plan;
  if (setting.applied_from === "plan" && plan) {
    if (plan.source === "preemptive") return <span className="setting-writer">Forecast rule · system</span>;
    return <span className="setting-writer">Iris changed this at <When at={plan.written_at} today={today} />{sep}<Mark kind="ai" /></span>;
  }
  return null;
}

/**
 * What Iris plans next (redesign §5.10): "Iris skips it" while the setting is MANUAL; "superseded until Iris's next plan"
 * while a newer non-manual value holds the next waypoint off (`effective` false); else "next 1.70 kPa at 13:00 · AI"
 * (Iris) or "· Forecast rule · system". Null when nothing is planned: a quiet row says nothing.
 */
export function NextPlan({ setting, today = null }: { setting: Pick<Setting, "desired" | "plan" | "kind" | "unit" | "step" | "options">; today?: string | null }): ReactNode {
  if (isManual(setting)) return <span className="setting-next">Iris skips it</span>;
  const next = setting.plan?.next;
  if (next && !next.effective) return <span className="setting-next">superseded until Iris's next plan</span>;
  if (next && next.value !== null && next.value !== undefined) {
    return <span className="setting-next">
      next {keptValueText(next.value, setting)} at <When at={next.at} today={today} />
      {next.source === "preemptive" ? <>{sep}Forecast rule · system</> : <>{sep}<Mark kind="ai" /></>}
    </span>;
  }
  return null;
}

/**
 * The meta line's parts in order: the request (or who wrote the value), the next plan, a read-only row's reason (unless
 * its group head already says it, `hideReadOnlyReason`).
 */
type MetaOptions = { setting: Setting; today: string | null; hideReadOnlyReason?: boolean; confirmed?: boolean };
function MetaParts({ setting, today, hideReadOnlyReason = false, confirmed = false }: MetaOptions): ReactNode[] {
  const parts: ReactNode[] = [];
  const request = <RequestParts setting={setting} />;
  const pending = isPending(setting.state) || sentWithoutReadback(setting) !== null;
  if (pending) parts.push(<span key="request" className="ctl-meta-request">{request}</span>);
  else {
    const writer = WriterPart({ setting, today, confirmed });
    if (writer) parts.push(<span key="writer">{writer}</span>);
  }
  if (setting.writable) {
    const next = NextPlan({ setting, today });
    if (next) parts.push(<span key="next">{next}</span>);
  } else if (!hideReadOnlyReason) {
    // Why it is read-only, by kind (#1333): "Computed (band in force)", "Retired", ...; its definition on hover.
    const words = readOnlyWords(setting);
    if (words) parts.push(<span key="note" className="setting-note" data-read-only-kind={setting.read_only_kind ?? undefined} title={words.definition}>{words.label}</span>);
  }
  return parts;
}

/** The one `.meta` line under the value; nothing for a row with nothing to say. */
function RowMeta(options: MetaOptions) {
  const parts = MetaParts(options);
  if (!parts.length) return null;
  return <p className="ctl-row-meta meta">{parts.map((part, index) => <span key={index} className="ctl-meta-part">{index > 0 && sep}{part}</span>)}</p>;
}

/**
 * The setting's one pill (redesign §4.4), with CONFIRMED only while its newest write is under 10 minutes old (D-UX-07),
 * and whether such a confirmation must be said in words because a higher pill holds the place.
 */
export function useSettingPill(setting: Setting, readbackStale: boolean): { pill: SettingPill | null; confirmedWords: boolean } {
  const recent = useRecentWrite(setting.state === "confirmed" ? writtenAt(setting) : null);
  const pill = settingPill(setting, { readbackStale, recent });
  return { pill, confirmedWords: recent && pill !== "confirmed" };
}

/**
 * The setting's state as the drawer shows it above its fields: the device value large with when it was reported, the
 * one pill, the bounds bar, then the request or who wrote the value, the next plan and a read-only row's reason.
 */
export function SettingStatus(props: DeviceProps) {
  const { setting, readbackStale = false } = props;
  const today = useToday();
  const { pill, confirmedWords } = useSettingPill(setting, readbackStale);
  const pending = isPending(setting.state) ? pendingRequest(setting) : null;
  return <div className="ctl-setting-status">
    <div className="ctl-row-line">
      <DeviceLine {...props} age />
      <span className="ctl-row-pill"><PillBadge pill={pill} /></span>
    </div>
    {setting.kind === "numeric" && <BoundsBar min={setting.min} max={setting.max} step={setting.step} unit={setting.unit} value={setting.readback?.value}
      planned={plannedNext(setting)} requested={pending?.value ?? null} stale={readbackStale} />}
    <ul className="ctl-status-lines meta">{MetaParts({ setting, today, confirmed: confirmedWords }).map((part, index) => <li key={index}>{part}</li>)}</ul>
  </div>;
}

/** Today's America/Denver date on the server's clock, for times that carry their date on another day; it re-renders once a day. */
export function useToday(): string {
  return useClockValue((now) => localDate(new Date(now).toISOString()));
}

// ---------------------------------------------------------------------------------------------------- the row
export type SettingRowProps = DeviceProps & {
  /** The drawer's URL: `?edit=<param>` with the page's other URL state. Edit for an operator on a writable row, else Details. */
  editTo: string;
  /** Today's America/Denver date, so a time on another day carries its date; null leaves every time a clock time. */
  today?: string | null;
  /** The group head says the read-only reason once (Band in force), so the row leaves it out. */
  hideReadOnlyReason?: boolean;
};

/**
 * One setting (redesign §5.10): a table row of two lines at 1440 px, a stacked card at 860 px and below whose Edit (or
 * Details) link covers the row. Line one is the label and key, the device value large with its age, the bounds bar and the one pill;
 * line two the meta line. An operator on a writable row gets Edit; everyone else Details, which opens the same drawer
 * read-only. Memoized: a poll re-renders only the rows whose data changed (TanStack Query keeps unchanged rows by
 * reference).
 */
export const SettingRow = memo(function SettingRow({ setting, readbackStale = false, programRevision = null, editTo, today = null, hideReadOnlyReason = false }: SettingRowProps) {
  const operator = useRole() === "operator";
  const { pill, confirmedWords } = useSettingPill(setting, readbackStale);
  const pending = isPending(setting.state) ? pendingRequest(setting) : null;
  const numeric = setting.kind === "numeric" && setting.group !== "band_in_force";
  return <tr className={cls("setting-row", !setting.writable && "setting-read-only")} data-param={setting.param} data-state={setting.state}>
    <th scope="row" className="setting-name">
      <span className="setting-label">{setting.label}</span> <span className="setting-param mono">{breakableKey(setting.param)}</span>
      <SettingSummary setting={setting} />
    </th>
    <td className="setting-device">
      <div className="ctl-row-line">
        <DeviceLine setting={setting} readbackStale={readbackStale} programRevision={programRevision} />
        {settingAge(setting).kind !== "derived" && <SettingAgeLabel setting={setting} readbackStale={readbackStale} />}
        {numeric
          ? <BoundsBar min={setting.min} max={setting.max} step={setting.step} unit={setting.unit} value={setting.readback?.value} planned={plannedNext(setting)}
            requested={pending?.value ?? null} stale={readbackStale} />
          : <span className="ctl-bounds ctl-bounds-none" aria-hidden="true" />}
        <span className="ctl-row-pill"><PillBadge pill={pill} /></span>
      </div>
      <RowMeta setting={setting} today={today} hideReadOnlyReason={hideReadOnlyReason} confirmed={confirmedWords} />
    </td>
    <td className="setting-actions">
      {operator && setting.writable
        ? <Link className="setting-edit ctl-row-link" to={editTo} preventScrollReset aria-label={`Edit ${setting.label}`} data-write-control="true">Edit</Link>
        : <Link className="ctl-details ctl-row-link" to={editTo} preventScrollReset aria-label={`Details of ${setting.label}`}>Details</Link>}
    </td>
  </tr>;
});
