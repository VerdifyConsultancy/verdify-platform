// SPDX-License-Identifier: MIT
import { memo, type ReactNode } from "react";
import { Link } from "react-router";
import { TIME_UNAVAILABLE, duration } from "../lib/format";
import type { Actor, Setting } from "../lib/operator-api";
import { keptValueText, rangeText, steppedNumber } from "../lib/setting-values";
import { useRole } from "../lib/session";
import { isAiSource, type StateReason } from "../lib/vocab";
import { AiBadge, Badge, ReadbackBadge } from "./badge";
import { LocalTime, cls } from "./common";
import { AgeLabel, useNow } from "./freshness";
import { StateReasonLine } from "./state-reason-line";

/**
 * Settings' rows (SCR-05; design system §4.2 and §4.6). Everything a row says comes from API-09 as served: `state`,
 * `state_reason`, `applied_from`, `effective`, `writable` and `read_only_reason` are the API's, so the row never
 * compares a readback or decides writability. It keys on `group`, never on `readback_via` alone: a Band in force row is
 * DERIVED with its age, though the API serves it with no readback path.
 */

// ---------------------------------------------------------------------------------------------------- groups
/** The groups in their fixed order, with their headings (no column sort). */
export const SETTING_GROUPS: readonly (readonly [string, string])[] = [
  ["planner_policy", "Planner policy"],
  ["scheduled_policy", "Scheduled policy"],
  ["controller_safety", "Controller safety"],
  ["crop_band", "Crop band"],
  ["site_water_caps", "Site water caps"],
  ["band_in_force", "Band in force"],
  ["read_only", "Read-only"],
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

/** The value in force was written by Iris or a named agent: SCR-05's "Changed by an agent" chip. */
export function changedByAgent(setting: Pick<Setting, "applied_from" | "desired" | "plan">): boolean {
  if (setting.applied_from === "plan") return setting.plan?.source === "iris";
  if (setting.applied_from === "manual" || setting.applied_from === "desired") return Boolean(setting.desired && isAiSource(setting.desired.actor.source));
  return false;
}

/** `/activity?target=<param>`: every write to the setting. */
export const historyHref = (param: string): string => `/activity?target=${encodeURIComponent(param)}`;

// ---------------------------------------------------------------------------------------------------- small parts
/** "2 min ago", ticking silently. */
function Ago({ at }: { at: string | null | undefined }) {
  const now = useNow();
  const time = at ? Date.parse(at) : Number.NaN;
  if (!Number.isFinite(time)) return <span className="age">{TIME_UNAVAILABLE}</span>;
  return <span className="age">{duration(Math.max(0, Math.floor((now - time) / 1000)))} ago</span>;
}
/** "20 s": how long a request has been out, ticking silently. */
function Elapsed({ at }: { at: string | null | undefined }) {
  const now = useNow();
  const time = at ? Date.parse(at) : Number.NaN;
  if (!Number.isFinite(time)) return null;
  return <span className="age">{duration(Math.max(0, Math.floor((now - time) / 1000)))}</span>;
}

/** A writer by name: AI and the name for Iris and the named agents; "<name> · system" for the platform itself. */
function Writer({ actor }: { actor: Actor }) {
  if (isAiSource(actor.source)) return <><AiBadge source={actor.source} /> <span>{actorName(actor)}</span></>;
  if (actor.source === "system") return <span>{actor.name} · system</span>;
  return <span>{actorName(actor)}</span>;
}
/** A plan's writer: Iris with AI, or the forecast action engine, "Forecast rule · system" and never AI. */
function planWriter(source: string): ReactNode {
  return source === "preemptive" ? <span>Forecast rule · system</span> : <><AiBadge source="iris" /> <span>Iris</span></>;
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
 * The device line (design system §4.2): the device's own report, with its unit, CONFIRMED and its age; STALE and
 * "Last data N min ago (HH:MM)" while readbacks are stale; "—" with `readback.reason` when there is no value. A Band in
 * force row is DERIVED with its age and never NO READBACK; NO READBACK shows only for another row with no readback
 * path. A site cap reads the value in the program on the controller, "On controller · rev 42: 360 gal", and reads
 * STALE like any device value while readbacks are stale or the controller is not reporting.
 */
export function DeviceLine({ setting, readbackStale = false, programRevision = null }: DeviceProps) {
  const reading = setting.readback;
  const value = reading?.value;
  const band = setting.group === "band_in_force";
  const noReadback = !band && setting.readback_via === "none";
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return <p className="setting-line setting-value" data-device="none">
      <span className="setting-device-value value-muted">—</span>
      {reading?.reason && <> <span className="state-note">{reading.reason}</span></>}
      {noReadback && <> <Badge kind="no_readback" /></>}
    </p>;
  }
  const text = keptValueText(value, setting);
  if (band) {
    return <p className="setting-line setting-value" data-device="derived"><span className="setting-device-value">{text}</span> <span className="state-note">· DERIVED ·</span> <Ago at={reading?.observed_at} /></p>;
  }
  if (noReadback) return <p className="setting-line setting-value" data-device="no_readback"><span className="setting-device-value">{text}</span> <Badge kind="no_readback" /></p>;
  let shown = text;
  if (setting.readback_via === "program") {
    const revision = programRevision !== null && programRevision !== undefined && Number.isFinite(programRevision) ? ` · rev ${programRevision}` : "";
    shown = `On controller${revision}: ${text}`;
  }
  if (readbackStale) {
    return <p className="setting-line setting-value" data-device="stale">
      <span className="setting-device-value value-muted">{shown}</span> <Badge kind="stale" /> <AgeLabel observedAt={reading?.observed_at} stale />
    </p>;
  }
  return <p className="setting-line setting-value" data-device={setting.readback_via === "program" ? "program" : "confirmed"}>
    <span className="setting-device-value">{shown}</span> <Badge kind="confirmed" /> <span className="state-note">·</span> <Ago at={reading?.observed_at} />
  </p>;
}

/** The text "Sent. This setting has no readback, so it can never show CONFIRMED" (screens SCR-05). */
export const NO_READBACK_SENT = "Sent. This setting has no readback, so it can never show CONFIRMED";

/**
 * Under the device line: the request being applied while the row is REQUESTED, NOT CONFIRMED or HELD ("→ 1.55 kPa
 * REQUESTED · 20 s · MANUAL · jason · “Vandas drying”") with its reason once due (`StateReasonLine`: after 60 s, at once
 * when NOT CONFIRMED, HELD or `no_engine`); otherwise who wrote the value in force ("Iris changed this at 07:02" with AI,
 * "Forecast rule · system", "Released: plan, band or default applies"); and a read-only row's reason.
 */
export function SettingStatus(props: DeviceProps) {
  const { setting } = props;
  return <>
    <DeviceLine {...props} />
    <RequestLine setting={setting} />
    {!setting.writable && setting.read_only_reason && <p className="setting-line setting-note">{setting.read_only_reason}</p>}
  </>;
}

/**
 * A write to a setting with no readback path, while it is the value in force (`state` no_readback, applied from a
 * desired row): it is sent and can never confirm. Never for a Band in force row, and never once a plan has won.
 */
function sentWithoutReadback(setting: Setting): Pending | null {
  if (setting.group === "band_in_force" || setting.readback_via !== "none" || setting.state !== "no_readback") return null;
  return setting.applied_from === "manual" || setting.applied_from === "desired" ? pendingRequest(setting) : null;
}

function RequestLine({ setting }: { setting: Setting }) {
  const pending = isPending(setting.state) ? pendingRequest(setting) : null;
  const shown = pending ?? sentWithoutReadback(setting);
  if (shown) {
    return <>
      <p className="setting-line setting-request" data-request={setting.state}>
        <span aria-hidden="true">→</span> <span className="setting-request-value">{keptValueText(shown.value, setting)}</span>
        {pending && <> <ReadbackBadge state={setting.state} /></>}
        {shown.at && <> <span className="state-note">·</span> <Elapsed at={shown.at} /></>}
        {shown.manual && <> <span className="state-note">·</span> <Badge kind="manual" /></>}
        {" "}<span className="state-note">·</span> {shown.by}
        {shown.reason && <> <span className="state-note">·</span> <span className="setting-reason">“{shown.reason}”</span></>}
      </p>
      {pending
        ? <StateReasonLine state={setting.state} reason={pending.stateReason} requestedAt={pending.at} unit={setting.unit} />
        : <span className="state-reason">{NO_READBACK_SENT}</span>}
    </>;
  }
  return <WriterLine setting={setting} />;
}

/** Who wrote the value in force, when no request is pending. */
function WriterLine({ setting }: { setting: Setting }) {
  if (setting.state === "released") return <p className="setting-line setting-writer">Released: plan, band or default applies</p>;
  const desired = setting.desired;
  if ((setting.applied_from === "manual" || setting.applied_from === "desired") && desired && desired.value !== null) {
    const actor = desired.actor;
    const may = actor.source === "dashboard" && !desired.manual ? " · Iris may adjust" : "";
    return <p className="setting-line setting-writer">
      {isAiSource(actor.source) && <><AiBadge source={actor.source} /> </>}
      <span>{actorName(actor)} changed this at <LocalTime at={desired.created_at} />{actor.source === "system" ? " · system" : ""}{may}</span>
    </p>;
  }
  const plan = setting.plan;
  if (setting.applied_from === "plan" && plan) {
    if (plan.source === "preemptive") return <p className="setting-line setting-writer">Forecast rule · system</p>;
    return <p className="setting-line setting-writer"><AiBadge source="iris" /> <span>Iris changed this at <LocalTime at={plan.written_at} /></span></p>;
  }
  return null;
}

// ---------------------------------------------------------------------------------------------------- next (plan)
/**
 * Next (plan): "MANUAL · Iris skips" while the setting is manual; "superseded until Iris's next plan" while a newer
 * non-manual value holds the next waypoint off (`effective` false); else the next planned value and its time with AI
 * (Iris) or "Forecast rule · system"; else "not planned".
 */
export function NextPlan({ setting }: { setting: Pick<Setting, "desired" | "plan" | "kind" | "unit" | "step" | "options"> }) {
  const desired = setting.desired;
  if (desired?.manual && desired.value !== null) return <span className="setting-next-text"><Badge kind="manual" /> <span>· Iris skips</span></span>;
  const next = setting.plan?.next;
  if (next && !next.effective) return <span className="setting-next-text state-note">superseded until Iris's next plan</span>;
  if (next && next.value !== null && next.value !== undefined) {
    return <span className="setting-next-text">
      <span>{keptValueText(next.value, setting)} at <LocalTime at={next.at} /></span>{" "}
      {next.source === "preemptive" ? <span>· Forecast rule · system</span> : <AiBadge source="iris" />}
    </span>;
  }
  return <span className="setting-next-text state-note">not planned</span>;
}

// ---------------------------------------------------------------------------------------------------- the row
export type SettingRowProps = DeviceProps & {
  /** Edit's target, `?edit=<param>` with the page's other URL state. Shown to operators on writable rows only. */
  editTo: string;
};

/**
 * One setting (design system §4.6 `SettingRow`): a table row at 1440 px, a stacked card at 390 px. Label and key; the
 * device value with its request, reason or writer; Next (plan); bounds and step; Edit for an operator on a writable
 * row, and "History ›". A read-only row shows its reason and no Edit. Memoized: a poll re-renders only the rows whose
 * data changed (TanStack Query keeps unchanged rows by reference).
 */
export const SettingRow = memo(function SettingRow({ setting, readbackStale = false, programRevision = null, editTo }: SettingRowProps) {
  const operator = useRole() === "operator";
  return <tr className={cls("setting-row", !setting.writable && "setting-read-only")} data-param={setting.param} data-state={setting.state}>
    <th scope="row" className="setting-name"><span className="setting-label">{setting.label}</span> <span className="setting-param mono">{setting.param}</span></th>
    <td className="setting-device"><SettingStatus setting={setting} readbackStale={readbackStale} programRevision={programRevision} /></td>
    <td className="setting-next">{setting.writable && <><span className="setting-cell-label">Next (plan) </span><NextPlan setting={setting} /></>}</td>
    <td className="setting-bounds"><span className="setting-cell-label">Allowed · step </span>{boundsText(setting)}</td>
    <td className="setting-actions">
      {operator && setting.writable && <Link className="button-link setting-edit" to={editTo} aria-label={`Edit ${setting.label}`} data-write-control="true">Edit</Link>}
      {setting.group !== "band_in_force" && <Link className="setting-history" to={historyHref(setting.param)} aria-label={`History of ${setting.label}`}>History<span aria-hidden="true"> ›</span></Link>}
    </td>
  </tr>;
});
