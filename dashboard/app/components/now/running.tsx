// SPDX-License-Identifier: MIT
/**
 * What runs now (redesign §5.1 "Running now"; screens SCR-01, US-NOW-03): a row per API-02 `running` item with its plain
 * name, a dot in its equipment's token and "since HH:MM"; a wet line adds its cause in words and, with the irrigation
 * firmware, the time left from API-IRR-01 `active_runs[].ends_at`; a burst from the controller's readback (API-02
 * `overrides`) reads "Fans burst · 12 min left", with one Clear for operators (#1136) while the irrigation firmware runs;
 * before it a push-button burst still shows, with no Clear, since API-IRR-12 answers 503. While the answer says the
 * greenhouse data is out of reach, the ingestor is not running or the controller is not reporting (rows 1 to 3), one line
 * reads "Equipment state unknown since HH:MM" instead, never "Nothing running".
 */
import { causeWords } from "../../charts/causes";
import { leftWords, shortTime } from "../../lib/format";
import type { NowAnswer } from "../../lib/now-answer";
import type { IrrigationStatus, Now } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { OperatorOnly, useApiDown } from "../../lib/session";
import { words } from "../../lib/words";
import { ClearBursts } from "../burst-panel";
import { cls } from "../common";
import { useNow } from "../freshness";
import { equipmentRow } from "./next";
import { deviceUnknown } from "./status";

export type RunningChip = { readonly key: string; readonly kind: "wet" | "dry" | "burst"; readonly text: string; readonly token: string | null };
export const NOTHING_RUNNING = "Nothing running";

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
/** " · 12 min left" while `endsAt` is ahead of the clock; empty otherwise. */
function left(endsAt: string | null | undefined, clock: number): string {
  const time = leftWords(endsAt, clock);
  return time ? ` · ${time}` : "";
}
const sinceText = (value: string | null | undefined) => (validTime(value) ? ` · since ${shortTime(value)}` : "");
const BURST_TOKENS: Readonly<Record<string, string>> = { fans: "eq-fan1", humidify: "eq-fog", vent_bypass: "eq-vent" };

/**
 * The running and burst rows, in the API's order. A wet line's cause is API-02's, else its API-IRR-01 line state's,
 * else "cause unknown"; its time left needs the irrigation firmware (`engine`) and an `ends_at` ahead.
 */
export function runningChips(now: Pick<Now, "running" | "overrides">, irrigation: IrrigationStatus | null, clock: number): RunningChip[] {
  const chips: RunningChip[] = now.running.map((item, index) => {
    const name = words("equipment", item.equipment);
    const token = equipmentRow(item.equipment)?.[1] ?? null;
    if (!item.line_id) return { key: `${item.equipment}-${index}`, kind: "dry", text: `${name}${sinceText(item.since)}`, token };
    const line = irrigation?.lines.find((row) => row.line_id === item.line_id);
    const cause = causeWords(item.cause ?? line?.state.cause ?? "unknown");
    const run = irrigation?.engine === true ? irrigation.active_runs.find((row) => row.line_id === item.line_id) : undefined;
    return { key: `${item.equipment}-${index}`, kind: "wet", text: `${name} · ${cause}${sinceText(item.since)}${left(run?.ends_at, clock)}`, token };
  });
  for (const [index, burst] of now.overrides.entries()) {
    const held = burst.held_reason ? ` · held: ${words("fog_block_reason", burst.held_reason)}` : "";
    const tail = left(burst.ends_at, clock) || sinceText(burst.since);
    chips.push({ key: `burst-${burst.kind}-${index}`, kind: "burst", text: `${words("burst", burst.kind)}${held}${tail}`, token: BURST_TOKENS[burst.kind] ?? null });
  }
  return chips;
}

export type RunningNowProps = {
  /** The newest API-02 answer; null before the first. */
  readonly now: Now | null;
  readonly answer: NowAnswer;
  readonly irrigation: PanelRead<IrrigationStatus>;
};

/** "Running now": the rows, "Nothing running", or why the state is unknown; Clear beside the bursts for operators. */
export function RunningNow({ now, answer, irrigation }: RunningNowProps) {
  const clock = useNow();
  const apiDown = useApiDown();
  if (answer.kind === "loading") return <><div className="skeleton now-skeleton-chips" aria-hidden="true" /><p className="screen-reader">Loading what is running</p></>;
  if (deviceUnknown(answer) || !now) {
    const at = answer.unknownSince;
    return <p className="meta now-running-note" data-running="unknown">Equipment state unknown{validTime(at) ? ` since ${shortTime(at)}` : ""}</p>;
  }
  const chips = runningChips(now, irrigation.failed ? null : irrigation.data, clock);
  if (!chips.length) return <p className="meta now-running-note" data-running="none">{NOTHING_RUNNING}</p>;
  const clearable = chips.some((chip) => chip.kind === "burst") && !irrigation.failed && irrigation.data?.engine === true;
  return <ul className="now-running">
    {chips.map((chip) => <li key={chip.key}>
      <span className="now-chip" data-chip={chip.kind}>
        <span className={cls("now-dot", apiDown && "is-neutral")} data-token={chip.token ?? undefined} aria-hidden="true" />{chip.text}
      </span>
    </li>)}
    {clearable && <OperatorOnly><li className="now-chip-action"><ClearBursts /></li></OperatorOnly>}
  </ul>;
}
