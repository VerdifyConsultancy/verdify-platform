// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { useApiDown } from "../lib/session";
import type { EquipmentState, LineKind, ReadbackVia } from "../lib/vocab";
import { LocalTime } from "./common";

/**
 * The badges of design system §4.1. Text always, colour second: each badge carries its word (the stylesheet
 * uppercases it). `ok` marks the green family, which turns neutral while the dashboard API is down, so nothing green
 * shows then (screens §1.2).
 */
export const BADGES = {
  requested: { className: "status status-pending", text: "Requested" },
  confirmed: { className: "status status-complete", text: "Confirmed", ok: true },
  not_confirmed: { className: "status status-failed", text: "Not confirmed" },
  held: { className: "tag badge-held", text: "Held" },
  no_readback: { className: "tag", text: "No readback" },
  manual: { className: "tag badge-manual", text: "Manual" },
  ai: { className: "tag badge-ai", text: "AI" },
  planned: { className: "tag badge-planned", text: "Planned" },
  superseded: { className: "tag badge-superseded", text: "Superseded" },
  stale: { className: "status status-stale", text: "Stale" },
  clean: { className: "tag tag-clean", text: "Clean" },
  fert: { className: "tag tag-fert", text: "Fert" },
  running: { className: "tag tag-ok", text: "Running", ok: true },
  idle: { className: "tag", text: "Idle" },
  disabled: { className: "tag tag-muted", text: "Disabled" },
  on: { className: "tag tag-ok", text: "On", ok: true },
  off: { className: "tag", text: "Off" },
  unknown: { className: "tag", text: "Unknown" },
} as const satisfies Record<string, { className: string; text: string; ok?: true }>;
export type BadgeKind = keyof typeof BADGES;

export function Badge({ kind, children, title }: { kind: BadgeKind; children?: ReactNode; title?: string }) {
  const apiDown = useApiDown();
  const badge: { className: string; text: string; ok?: true } = BADGES[kind];
  // The neutral base is the first class (`tag` or `status`) without its colour.
  const className = badge.ok && apiDown ? badge.className.split(" ")[0] : badge.className;
  return <span className={className} data-badge={kind} title={title}>{children ?? badge.text}</span>;
}

const READBACK_BADGES: Readonly<Record<string, BadgeKind>> = { requested: "requested", confirmed: "confirmed", not_confirmed: "not_confirmed", held: "held", no_readback: "no_readback" };
/**
 * A setting's or program item's `ReadbackState` (architecture §5.5). `overridden` is AI with "Iris changed this at
 * HH:MM" (the time of the plan that won, when known); `released` has no badge. HELD, the override and released follow
 * the state, because they say who or what applies; otherwise NO READBACK also shows when the API says the setting has
 * none (`readback_via: none`). A state this build does not know shows raw.
 */
export function ReadbackBadge({ state, readbackVia, overriddenAt }: { state: string; readbackVia?: ReadbackVia | (string & {}) | null; overriddenAt?: string | null }) {
  if (state === "released") return null;
  if (state === "overridden") return <><Badge kind="ai" /> <span className="state-note">Iris changed this{overriddenAt ? <> at <LocalTime at={overriddenAt} /></> : null}</span></>;
  if (state !== "held" && (readbackVia === "none" || state === "no_readback")) return <Badge kind="no_readback" />;
  const kind = Object.hasOwn(READBACK_BADGES, state) ? READBACK_BADGES[state] : undefined;
  return kind ? <Badge kind={kind} /> : <span className="tag" data-badge="raw">{state}</span>;
}

/** The chemistry of a line kind: the fertilizer lines and the fertilizer master valve (L03) are FERT. */
export function lineChemistry(kind: LineKind | (string & {})): "clean" | "fert" | null {
  if (kind === "fert_drip" || kind === "fert_mister" || kind === "master") return "fert";
  if (kind === "drip" || kind === "mister" || kind === "fog") return "clean";
  return null;
}
/** CLEAN or FERT: a `.tag` whose leading dot is `--eq-water` or `--eq-fert`. */
export function ChemistryBadge({ chemistry }: { chemistry: "clean" | "fert" | null | undefined }) {
  return chemistry ? <Badge kind={chemistry} /> : null;
}

/** An actuator's state as the API sends it: ON, OFF or UNKNOWN. Unknown comes only from the API, never from an age. */
const EQUIPMENT_STATES: readonly string[] = ["on", "off", "unknown"] satisfies EquipmentState[];
export function EquipmentStateBadge({ state }: { state: EquipmentState | (string & {}) }) {
  return EQUIPMENT_STATES.includes(state) ? <Badge kind={state as EquipmentState} /> : <span className="tag" data-badge="raw">{state}</span>;
}
