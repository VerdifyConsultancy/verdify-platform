// SPDX-License-Identifier: MIT
import { localTime, shortDateTime, shortTime } from "../lib/format";

/**
 * An instant as a 24-hour America/Denver clock time ("06:02", or "Oct 27, 06:02" with `date`), with the full local
 * form and its offset in `title` (design system §7). A time in the repeated fall-back hour carries its offset.
 */
export function LocalTime({ at, date = false }: { at: string; date?: boolean }) {
  const valid = Number.isFinite(Date.parse(at));
  return <time dateTime={at} title={valid ? localTime(at) : undefined}>{date ? shortDateTime(at) : shortTime(at)}</time>;
}

/** Joins class names, dropping the empty ones; undefined when none is left. */
export function cls(...names: (string | false | null | undefined)[]): string | undefined {
  return names.filter(Boolean).join(" ") || undefined;
}
