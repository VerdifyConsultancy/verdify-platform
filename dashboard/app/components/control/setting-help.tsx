// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";
import type { Setting } from "../../lib/operator-api";

/**
 * What each setting, filter, state and read-only kind means, in the words Settings shows (#1332, #1333). A setting's own
 * text is API-09's `help` (verdify_schemas.setting_help, the same words MCP and the lab reference page carry); the
 * definitions of the screen's own words live here, so the rail, the legend, the pills and the drawer say one thing.
 */

// ---------------------------------------------------------------------------------------------------- states
/** A setting state as Settings names it, with its one-line definition (#1333). */
export type StateWords = { readonly label: string; readonly definition: string };
export const STATE_WORDS: Readonly<Record<string, StateWords>> = {
  requested: { label: "Waiting for controller", definition: "Sent less than 5 min ago; the controller hasn't reported the new value yet." },
  not_confirmed: { label: "Not applied", definition: "Sent 5+ min ago and the controller still reports a different value. Check the controller or the reason shown." },
  held: { label: "Held", definition: "The platform applies something else on purpose: a guardrail clamp or an armed experiment. The reason is shown on the row." },
  confirmed: { label: "Confirmed", definition: "The controller reports the value in force. The pill shows for 10 minutes after a write." },
  no_readback: { label: "No readback", definition: "The controller reports nothing for this setting, or nothing in the last 24 h, and nothing was sent that waits on it." },
  stale: { label: "Stale", definition: "Device values are old: the controller or the dashboard API is not reporting, so no value can be vouched for." },
  manual: { label: "Manual (Iris skips)", definition: "You or an agent pinned this value; Iris won't change it until you Release to auto. It lasts until released, not for a set time." },
  overridden: { label: "Overridden by Iris", definition: "A newer Iris plan value replaced a non-manual write." },
  released: { label: "Released", definition: "Handed back to Iris: the plan, the band or the default applies." },
};
/** The pill words on Settings rows: shorter than the filter names, the same meaning. */
export const PILL_WORDS: Readonly<Record<string, string>> = { requested: "Waiting", not_confirmed: "Not applied", manual: "Manual" };
export const stateDefinition = (state: string): string | undefined => STATE_WORDS[state]?.definition;

// ---------------------------------------------------------------------------------------------------- read-only kinds
/** Why a row is read-only (`read_only_kind`), named and defined as the row and the drawer show it (#1333). */
export const READ_ONLY_WORDS: Readonly<Record<string, StateWords>> = {
  band_in_force: { label: "Computed (band in force)", definition: "Calculated from the crop band; edit the anchors under Crop band." },
  retired: { label: "Retired", definition: "The controller no longer uses it. Kept for reference and history." },
  readback_context: { label: "Reported by the controller", definition: "The controller reports or measures it; nothing sets it." },
  legacy_wide_limit: { label: "Legacy limit, not used while the on-chip band runs", definition: "An older fixed limit. The band in force comes from the crop band anchors; the guardrails are Safety Min and Safety Max." },
  zone_vpd_target: { label: "Computed zone target, still used", definition: "Worked out each cycle from the zone's VPD target curve. The controller uses it to decide which zone needs misting and how long a pulse lasts; edit the curve under Crop band." },
  activity_mirror: { label: "Mirrors the light schedule", definition: "Copied from the main grow-light schedule each cycle; change the schedule instead." },
  band_owner: { label: "Decides who owns the band", definition: "Switches between the band the controller computes and the legacy limits. Changed only through a reviewed rollout." },
  no_anchor_row: { label: "No crop band row", definition: "No crop band anchor row holds this value yet, so a change would reach nothing." },
  no_entity: { label: "No controller entity", definition: "The controller has no entity to write this value to." },
  unknown: { label: "Not a setting", definition: "Not a registered setting." },
};
/** The read-only words of a row: its kind's, else the API's reason as served. */
export function readOnlyWords(setting: Pick<Setting, "writable" | "read_only_kind" | "read_only_reason">): StateWords | null {
  if (setting.writable) return null;
  const kind = setting.read_only_kind ? READ_ONLY_WORDS[setting.read_only_kind] : undefined;
  if (kind) return kind;
  return setting.read_only_reason ? { label: setting.read_only_reason, definition: setting.read_only_reason } : null;
}

// ---------------------------------------------------------------------------------------------------- the text
type HelpShape = Pick<Setting, "kind" | "help">;
/** "Raising it" or, for a switch, "Turning it on". */
export const raiseWord = (kind: string): string => (kind === "switch" ? "Turning it on" : "Raising it");
export const lowerWord = (kind: string): string => (kind === "switch" ? "Turning it off" : "Lowering it");

/** The one-line summary a row shows under its label; nothing when the API serves no text. */
export function SettingSummary({ setting }: { setting: HelpShape }) {
  const summary = setting.help?.summary;
  return summary ? <span className="setting-summary">{summary}</span> : null;
}

/**
 * The drawer's explanation, above New value: what it is, what it changes, raising and lowering it, the caution, and the
 * lab reference entry. For a read-only row, why it is read-only and, for a derived row, the settings it is computed from,
 * each opening its own drawer (`editTo`).
 */
export function SettingHelpPanel({ setting, editTo, labelOf }: {
  setting: Pick<Setting, "kind" | "help" | "writable" | "read_only_kind" | "read_only_reason" | "source_params">;
  editTo?: (param: string) => string;
  labelOf?: (param: string) => string;
}) {
  const help = setting.help;
  const readOnly = readOnlyWords(setting);
  const sources = setting.source_params ?? [];
  if (!help && !readOnly) return null;
  const rows: [string, ReactNode][] = [];
  if (help) {
    rows.push(["What it changes", help.effect]);
    if (help.raise) rows.push([raiseWord(setting.kind), help.raise]);
    if (help.lower) rows.push([lowerWord(setting.kind), help.lower]);
  }
  return <section className="ctl-help" aria-label="About this setting">
    {help && <p className="ctl-help-summary">{help.summary}</p>}
    {readOnly && <p className="ctl-help-readonly" data-read-only-kind={setting.read_only_kind ?? undefined}>
      <strong>Read-only: {readOnly.label}.</strong>{readOnly.definition !== readOnly.label && <> {readOnly.definition}</>}
    </p>}
    {rows.length > 0 && <dl className="ctl-help-list">
      {rows.map(([term, text]) => <div key={term}><dt>{term}</dt><dd>{text}</dd></div>)}
    </dl>}
    {help?.caution && <p className={help.guardrail ? "notice notice-warn ctl-help-caution" : "ctl-help-caution"}>
      {help.guardrail && <strong>Guardrail. </strong>}{help.caution}
    </p>}
    {sources.length > 0 && <p className="ctl-help-sources">
      <span>Computed from: </span>
      {sources.map((param, index) => <span key={param}>{index > 0 && ", "}{editTo
        ? <Link to={editTo(param)} preventScrollReset>{labelOf?.(param) ?? param}</Link>
        : <span>{labelOf?.(param) ?? param}</span>}</span>)}
    </p>}
    {help?.reference_url && <p className="ctl-help-ref"><a href={help.reference_url} target="_blank" rel="noreferrer">Full reference entry<span aria-hidden="true"> ›</span></a></p>}
  </section>;
}

// ---------------------------------------------------------------------------------------------------- the legend
/** The definitions at the foot of the rail, on every width: the filters, then the states, then the read-only kinds. */
export function SettingsLegend({ filters }: { filters: readonly { readonly key: string; readonly label: string; readonly definition: string }[] }) {
  const states = ["requested", "not_confirmed", "held", "confirmed", "no_readback", "stale", "manual", "released"];
  return <details className="ctl-legend">
    <summary>What these mean</summary>
    <dl>
      {filters.map((item) => <div key={`f-${item.key}`}><dt>{item.label}</dt><dd>{item.definition}</dd></div>)}
    </dl>
    <p className="eyebrow">States</p>
    <dl>
      {states.map((state) => <div key={`s-${state}`}><dt>{STATE_WORDS[state].label}</dt><dd>{STATE_WORDS[state].definition}</dd></div>)}
    </dl>
    <p className="eyebrow">Read-only, because</p>
    <dl>
      {["band_in_force", "retired", "readback_context", "legacy_wide_limit", "zone_vpd_target", "activity_mirror"].map((kind) => <div key={`r-${kind}`}>
        <dt>{READ_ONLY_WORDS[kind].label}</dt><dd>{READ_ONLY_WORDS[kind].definition}</dd>
      </div>)}
    </dl>
  </details>;
}
