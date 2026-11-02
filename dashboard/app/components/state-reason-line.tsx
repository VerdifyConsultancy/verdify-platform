// SPDX-License-Identifier: MIT
import { formatValue } from "../lib/format";
import type { StateReason } from "../lib/vocab";
import { hasWords, words } from "../lib/words";
import { LocalTime } from "./common";
import { useNow } from "./freshness";

/** A REQUESTED row shows its reason once the request is this old (design system §4.2). */
export const REASON_AFTER_MS = 60_000;

export type ReasonPart = string | { readonly time: string };
const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);
const validTime = (value: string | null | undefined): string | null => (value && Number.isFinite(Date.parse(value)) ? value : null);
// An applied value with no unit keeps its digits rather than the 1 dp a unitless reading gets.
const PLAIN = new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 });
const applied = (value: number, unit: string | null | undefined) => (unit ? formatValue(value, unit) : PLAIN.format(value).replace("-", "−"));

/**
 * The reason in words, as parts: text, and the times, which are always `state_reason.since` (when the reason began).
 * A code without words, or no reason at all, reads "No matching readback yet": the line never shows a raw code.
 */
export function reasonParts(reason: StateReason | null | undefined, unit?: string | null): ReasonPart[] {
  if (!reason || !hasWords("state_reason", reason.code)) return [words("state_reason", "no_match")];
  const base = words("state_reason", reason.code);
  const since = validTime(reason.since);
  const detail = reason.detail?.trim() || null;
  switch (reason.code) {
    case "clamped": {
      const value = reason.applied_value;
      const head = value === null || value === undefined || !Number.isFinite(value) ? base : `${base} to ${applied(value, unit)}`;
      return [detail ? `${head}: ${detail}` : head];
    }
    case "experiment":
      return [detail ? `Held by experiment ${detail}: applies when it ends` : `${base}: applies when it ends`];
    case "deferred":
    case "no_match":
      return since ? ["Sent ", { time: since }, `; ${lowerFirst(base)}`] : [base];
    case "push_failed": {
      const tail = detail ? `: ${detail}` : "";
      return since ? ["Sent ", { time: since }, `; ${lowerFirst(base)}${tail}`] : [`${base}${tail}`];
    }
    case "writer_down":
    case "controller_not_reporting":
      return since ? [`${base} since `, { time: since }] : [base];
    default:
      return [base];
  }
}

/** True when the line shows: under HELD and NOT CONFIRMED always, under REQUESTED once 60 s old or at once for `no_engine`. */
export function reasonShows(state: string, reason: StateReason | null | undefined, requestedAt: string | null | undefined, now: number): boolean {
  if (state === "held" || state === "not_confirmed") return true;
  if (state !== "requested") return false;
  if (reason?.code === "no_engine") return true;
  const at = requestedAt ? Date.parse(requestedAt) : Number.NaN;
  return Number.isFinite(at) && now - at >= REASON_AFTER_MS;
}

function Reason({ reason, unit }: { reason: StateReason | null | undefined; unit?: string | null }) {
  return <span className="state-reason" data-reason={reason && hasWords("state_reason", reason.code) ? reason.code : "none"}>
    {reasonParts(reason, unit).map((part, index) => (typeof part === "string" ? part : <LocalTime key={index} at={part.time} />))}
  </span>;
}
function AfterOneMinute({ reason, unit, requestedAt }: { reason: StateReason | null | undefined; unit?: string | null; requestedAt: string | null | undefined }) {
  const now = useNow();
  return reasonShows("requested", reason, requestedAt, now) ? <Reason reason={reason} unit={unit} /> : null;
}

/**
 * The reason line under a REQUESTED, NOT CONFIRMED or HELD value (design system §4.2), from the API's `state_reason`.
 * `requestedAt` (the write's `created_at`) only decides when a REQUESTED line appears; it is never shown.
 */
export function StateReasonLine({ state, reason, requestedAt, unit }: { state: string; reason?: StateReason | null; requestedAt?: string | null; unit?: string | null }) {
  if (state === "held" || state === "not_confirmed" || (state === "requested" && reason?.code === "no_engine")) return <Reason reason={reason} unit={unit} />;
  return state === "requested" ? <AfterOneMinute reason={reason} unit={unit} requestedAt={requestedAt} /> : null;
}
