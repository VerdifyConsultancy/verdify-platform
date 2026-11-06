// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import type { Setting } from "../../lib/operator-api";
import { Badge, type BadgeKind } from "../badge";
import { useClockValue } from "../freshness";
import { PILL_WORDS, stateDefinition } from "./setting-help";

/**
 * Control's marks and its one pill (redesign §4.4, D-UX-07). AI, DERIVED and NO READBACK are text marks: mono, muted
 * and after the value, never pills, so a row or card head carries at most one state pill. The marks keep their
 * `data-badge` hooks, so a spec that finds a row's AI finds it still. CONFIRMED shows only while the newest write is
 * under 10 minutes old; after that the value and its age say it.
 */

/** D-UX-07: CONFIRMED shows while the newest write is under this old. */
export const CONFIRMED_FOR_MS = 10 * 60_000;

export type MarkKind = "ai" | "derived" | "no_readback";
const MARK_WORDS: Readonly<Record<MarkKind, string>> = { ai: "AI", derived: "Derived", no_readback: "No readback" };

/** A text mark: "AI", "DERIVED", "NO READBACK" in muted mono (the stylesheet uppercases it). */
export function Mark({ kind, children, title }: { kind: MarkKind; children?: ReactNode; title?: string }) {
  return <span className="ctl-mark" data-badge={kind} title={title}>{children ?? MARK_WORDS[kind]}</span>;
}

/** The one pill a setting's row or drawer carries, by redesign §4.4's priority. */
export type SettingPill = Extract<BadgeKind, "not_confirmed" | "requested" | "held" | "stale" | "manual" | "confirmed">;

type PillShape = Pick<Setting, "state" | "group" | "readback_via" | "readback" | "applied_from" | "desired" | "plan">;

/** When the value in force was written: the desired row's `created_at`, or the plan's `written_at` when the plan applies. */
export function writtenAt(setting: Pick<Setting, "applied_from" | "desired" | "plan">): string | null {
  if (setting.applied_from === "plan") return setting.plan?.written_at ?? null;
  if (setting.applied_from === "manual" || setting.applied_from === "desired") return setting.desired?.created_at ?? null;
  return null;
}

/** The value in force is a person's or an agent's MANUAL value: Iris skips it. */
export const isManual = (setting: Pick<Setting, "desired">): boolean => Boolean(setting.desired?.manual) && setting.desired?.value !== null && setting.desired?.value !== undefined;

/**
 * The one pill: NOT CONFIRMED, REQUESTED, HELD, STALE (a device value while readbacks are stale), MANUAL, then
 * CONFIRMED while `recent` (the newest write under 10 minutes old). Null for a row in its steady state.
 */
export function settingPill(setting: PillShape, { readbackStale = false, recent = false }: { readbackStale?: boolean; recent?: boolean } = {}): SettingPill | null {
  if (setting.state === "not_confirmed") return "not_confirmed";
  if (setting.state === "requested") return "requested";
  if (setting.state === "held") return "held";
  const value = setting.readback?.value;
  const known = value !== null && value !== undefined && Number.isFinite(value);
  if (readbackStale && known && setting.group !== "band_in_force" && setting.readback_via !== "none") return "stale";
  if (isManual(setting)) return "manual";
  if (setting.state === "confirmed" && recent) return "confirmed";
  return null;
}

/** True while `at` is under 10 minutes old on the server's clock; it re-renders only when that flips. */
export function useRecentWrite(at: string | null | undefined): boolean {
  const time = at ? Date.parse(at) : Number.NaN;
  return useClockValue((now) => Number.isFinite(time) && now - time < CONFIRMED_FOR_MS);
}

/**
 * The pill itself, in Settings' words (WAITING, NOT APPLIED, #1333) with its definition as its title; nothing for a
 * steady row. `data-badge` keeps the state's key.
 */
export function PillBadge({ pill }: { pill: SettingPill | null }) {
  return pill ? <Badge kind={pill} title={stateDefinition(pill)}>{PILL_WORDS[pill]}</Badge> : null;
}
