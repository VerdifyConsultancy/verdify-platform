// SPDX-License-Identifier: MIT
/**
 * The words of Now's status line and its shared helpers (redesign §5.1): the mode, Iris and the plan in force, the
 * outdoor temperature and occupancy, each in one short phrase; and the line names and states the water card and the
 * running list read. Pure, so the tests check them without a page.
 */
import { duration, localDate, shortDateTime, shortTime } from "../../lib/format";
import { lineById } from "../../lib/lines";
import type { NowAnswer } from "../../lib/now-answer";
import type { IrrigationLine, IrrigationStatus, Now } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { modeWords, modeWordsNoVerdict, words } from "../../lib/words";

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));

/** Rows 1 to 3 of the answer: the greenhouse data is out of reach, the ingestor is down or the controller is quiet. */
export const deviceUnknown = (answer: Pick<NowAnswer, "row">): boolean => answer.row >= 1 && answer.row <= 3;

/**
 * The mode in words ("Venting to cool"), or "Mode unknown" while the controller's state cannot be vouched for: what it
 * last said would read as now (and `IDLE` reads "In band, holding", which asserts health). While the data is stale (row
 * 7, redesign §4.3) it is what the controller last said, dated and with no verdict in it: "Last mode: holding (10:28)",
 * at the time of the data still shown, or the mode's own start when that is newer.
 */
export function modeText(now: Pick<Now, "mode" | "server_time">, answer: Pick<NowAnswer, "row" | "muted" | "lastDataAt">): string {
  if (deviceUnknown(answer)) return "Mode unknown";
  if (!answer.muted) return modeWords(now.mode.code, now.mode.stage);
  const last = `Last mode: ${modeWordsNoVerdict(now.mode.code, now.mode.stage)}`;
  if (!validTime(answer.lastDataAt)) return last;
  const at = validTime(now.mode.since) && Date.parse(now.mode.since) > Date.parse(answer.lastDataAt) ? now.mode.since : answer.lastDataAt;
  return `${last} (${validTime(now.server_time) ? clockOrDate(at, Date.parse(now.server_time)) : shortTime(at)})`;
}

/** The plan in force and its end ("SUNRISE plan to 17:30"), or "No plan: band and defaults". */
export function planLine(iris: Now["iris"]): string {
  if (iris.plan_id === null) return "No plan: band and defaults";
  const head = iris.trigger ? `${iris.trigger} plan` : "Plan in force";
  return validTime(iris.valid_until) ? `${head} to ${shortTime(iris.valid_until)}` : head;
}
/** "Iris OK", "Iris missed": the newest required trigger's state. */
export const irisText = (iris: Now["iris"]): string => `Iris ${words("required_trigger", iris.required_trigger)}`;
/** Iris needs a look: its newest required trigger was late, missed or failed. */
export const irisWarns = (iris: Now["iris"]): boolean => iris.required_trigger !== "ok";

/** "since 05:10" today, else with the date ("Oct 30, 05:10"). */
function clockOrDate(value: string, clock: number): string {
  return localDate(value) === localDate(new Date(clock).toISOString()) ? shortTime(value) : shortDateTime(value);
}

/**
 * The occupancy phrase from API-IRR-01 `occupancy`, the first that holds: the hold switched off; someone inside (Frigate
 * saw them N min ago); nobody seen (last person at HH:MM); unknown. While API-IRR-01 fails or has not answered it reads
 * "Occupancy unknown", and the water card says why. Unknown occupancy never holds mist (D-IRP-10).
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

/**
 * The line states are not known: an enabled line reads unknown (the controller is not reporting) or, with the
 * irrigation firmware, `irr_state` is stale. Before that firmware no `irr_state` exists (a source never seen reads
 * stale), and the relay states stand alone (irrigation §9: `irr_state`'s age alone never makes a line unknown).
 */
export function lineStatesUnknown(status: Pick<IrrigationStatus, "engine" | "lines" | "freshness">): boolean {
  return status.lines.some((line) => line.enabled && line.state.code === "unknown") || (status.engine === true && status.freshness.stale);
}

/** A line's plain name: the words table's for its relay ("Center mister"), else the API's. */
export function lineName(line: Pick<IrrigationLine, "line_id" | "name">): string {
  const equipment = lineById(line.line_id)?.equipment;
  return equipment ? words("equipment", equipment) : line.name;
}
