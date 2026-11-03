// SPDX-License-Identifier: MIT
/**
 * What runs now (screens SCR-01, US-NOW-03): a chip per API-02 `running` item with its plain name and "since HH:MM";
 * a wet line adds its cause in words and, with the irrigation firmware, the time left from API-IRR-01
 * `active_runs[].ends_at`; a burst from the controller's readback (API-02 `overrides`) reads "Fans burst · 12 min
 * left". While the answer says the greenhouse data is out of reach, the ingestor is not running or the controller is
 * not reporting (rows 1 to 3), one line reads "Equipment state unknown since HH:MM" instead, never "Nothing running".
 * The occupancy chip always shows (US-WAT-16, D-IRP-10: unknown occupancy never holds mist).
 */
import { useId } from "react";
import { causeWords } from "../../charts/causes";
import { duration, localDate, shortDateTime, shortTime } from "../../lib/format";
import type { NowAnswer } from "../../lib/now-answer";
import type { IrrigationStatus, Now } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { words } from "../../lib/words";
import { useNow } from "../freshness";

export type RunningChip = { readonly key: string; readonly kind: "wet" | "dry" | "burst"; readonly text: string };
export const NOTHING_RUNNING = "Nothing running";

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
/** " · 12 min left" while `endsAt` is ahead of the clock; empty otherwise. */
function left(endsAt: string | null | undefined, clock: number): string {
  if (!validTime(endsAt)) return "";
  const seconds = Math.floor((Date.parse(endsAt) - clock) / 1000);
  return seconds > 0 ? ` · ${duration(seconds)} left` : "";
}
const sinceText = (value: string | null | undefined) => (validTime(value) ? ` · since ${shortTime(value)}` : "");

/**
 * The running and burst chips, in the API's order. A wet line's cause is API-02's, else its API-IRR-01 line state's,
 * else "cause unknown"; its time left needs the irrigation firmware (`engine`) and an `ends_at` ahead.
 */
export function runningChips(now: Pick<Now, "running" | "overrides">, irrigation: IrrigationStatus | null, clock: number): RunningChip[] {
  const chips: RunningChip[] = now.running.map((item, index) => {
    const name = words("equipment", item.equipment);
    if (!item.line_id) return { key: `${item.equipment}-${index}`, kind: "dry", text: `${name}${sinceText(item.since)}` };
    const line = irrigation?.lines.find((row) => row.line_id === item.line_id);
    const cause = causeWords(item.cause ?? line?.state.cause ?? "unknown");
    const run = irrigation?.engine === true ? irrigation.active_runs.find((row) => row.line_id === item.line_id) : undefined;
    return { key: `${item.equipment}-${index}`, kind: "wet", text: `${name} · ${cause}${sinceText(item.since)}${left(run?.ends_at, clock)}` };
  });
  for (const [index, burst] of now.overrides.entries()) {
    const held = burst.held_reason ? ` · held: ${words("fog_block_reason", burst.held_reason)}` : "";
    const tail = left(burst.ends_at, clock) || sinceText(burst.since);
    chips.push({ key: `burst-${burst.kind}-${index}`, kind: "burst", text: `${words("burst", burst.kind)}${held}${tail}` });
  }
  return chips;
}

/** "since 05:10" today, else with the date ("Oct 30, 05:10"). */
function clockOrDate(value: string, clock: number): string {
  return localDate(value) === localDate(new Date(clock).toISOString()) ? shortTime(value) : shortDateTime(value);
}

/**
 * The occupancy chip from API-IRR-01 `occupancy`, the first that holds: the hold switched off; someone inside (Frigate
 * saw them N min ago); nobody seen (last person at HH:MM); unknown. While API-IRR-01 fails or has not answered it
 * reads "Occupancy unknown", and the water card says why.
 */
export function occupancyText(irrigation: PanelRead<IrrigationStatus>, clock: number): string {
  if (irrigation.failed || !irrigation.data) return "Occupancy unknown";
  const { occupied, seen_at: seenAt, hold_enabled: hold } = irrigation.data.occupancy;
  if (hold === false) return "Occupancy mist hold is off: misters may run";
  if (occupied === true) {
    const ago = validTime(seenAt) ? `, ${duration(Math.max(0, Math.floor((clock - Date.parse(seenAt)) / 1000)))} ago` : "";
    return `Someone inside: misters wait (Frigate${ago})`;
  }
  if (occupied === false) return `Nobody seen: misters may run${validTime(seenAt) ? ` (last person ${clockOrDate(seenAt, clock)})` : ""}`;
  return "Occupancy unknown: misters may run";
}

export type RunningPanelProps = {
  /** The newest API-02 answer; null before the first. */
  readonly now: Now | null;
  readonly answer: NowAnswer;
  readonly irrigation: PanelRead<IrrigationStatus>;
};

export function RunningPanel({ now, answer, irrigation }: RunningPanelProps) {
  const headingId = useId();
  const clock = useNow();
  const unknown = answer.row >= 1 && answer.row <= 3;
  let body;
  if (answer.kind === "loading") {
    body = <><div className="skeleton skeleton-chips" aria-hidden="true" /><p className="screen-reader">Loading what is running</p></>;
  } else if (unknown || !now) {
    const at = answer.unknownSince;
    body = <p className="state-note" data-running="unknown">Equipment state unknown{validTime(at) ? ` since ${shortTime(at)}` : ""}</p>;
  } else {
    const chips = runningChips(now, irrigation.failed ? null : irrigation.data, clock);
    body = chips.length
      ? <ul className="chips now-chips" aria-labelledby={headingId}>{chips.map((chip) => <li key={chip.key}><span className="chip chip-static" data-chip={chip.kind}>{chip.text}</span></li>)}</ul>
      : <p className="state-note" data-running="none">{NOTHING_RUNNING}</p>;
  }
  return <section className="now-running" aria-labelledby={headingId} aria-busy={answer.kind === "loading" || undefined}>
    <h2 id={headingId} className="eyebrow">Running · commanded</h2>
    {body}
    <p className="now-occupancy"><span className="chip chip-static" data-chip="occupancy">{occupancyText(irrigation, clock)}</span></p>
  </section>;
}
