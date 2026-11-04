// SPDX-License-Identifier: MIT
import { useId } from "react";
import { ZONE_WORDS } from "../../lib/lines";
import type { IrrigationLine, IrrigationStatus, Schedule } from "../../lib/operator-api";
import { useApiDown } from "../../lib/session";
import { ZONE_KIND_WORDS, amountShort, dayClockWords, nextRunShort, type ZoneSummary } from "../../lib/water";
import { cls } from "../common";
import { LineCard } from "../line-card";
import { MisterControls } from "../mister-association";
import { MinutesMeter } from "../schedule/day-timeline";
import type { DayBlock, Episode } from "../schedule/model";
import { lineTitle } from "../schedule/model";
import { DayStrip } from "./day-strip";

/** Today's window for the strips, and each line's blocks and valve episodes on it (`dayModel`); null before API-IRR-02 answers. */
export type TodayStrips = {
  readonly from: number;
  readonly to: number;
  readonly night: readonly { from: number; to: number }[];
  readonly byLine: ReadonlyMap<string, { readonly blocks: readonly DayBlock[]; readonly episodes: readonly Episode[] }>;
};

const tokenOf = (kind: string) => (kind === "mister" ? "mister" : kind === "fert_drip" || kind === "fert_mister" ? "fert" : kind === "fog" ? "fog" : "water");

export type ZoneCardProps = {
  summary: ZoneSummary;
  status: Pick<IrrigationStatus, "engine" | "program">;
  today: string;
  /** Epoch ms. */
  now: number;
  strips: TodayStrips | null;
  /** API-IRR-02's schedules, for "Running · schedule · Wall MWF" and the next run's amount. */
  schedules: readonly Schedule[];
};

/**
 * A zone's card on Water → Today (redesign §5.2): the zone's name and kind, its minutes today against the cap line's
 * daily cap (planned minutes dashed) and when it last watered; a mister zone adds VPD control and Schedule with their VPD
 * source; then a row per line in register order, L03 under L02. The order never changes with state.
 */
export function ZoneCard({ summary, status, today, now, strips, schedules }: ZoneCardProps) {
  const headingId = useId();
  const apiDown = useApiDown();
  const { zone, lines, master, mister, minutes, planned, cap, capLine, reserve, lastAt } = summary;
  const name = ZONE_WORDS[zone];
  const labels = new Map(schedules.flatMap((schedule) => (schedule.label ? [[schedule.slot_id, schedule.label] as const] : [])));
  const bySlot = new Map(schedules.map((schedule) => [schedule.slot_id, schedule]));
  const amountOf = (line: IrrigationLine) => {
    const schedule = line.next_run ? bySlot.get(line.next_run.slot_id) : undefined;
    return schedule ? amountShort(schedule, line.kind) : null;
  };
  const strip = (line: IrrigationLine, index: number) => {
    const own = strips?.byLine.get(line.line_id);
    const show = own && (line.enabled || own.blocks.length > 0 || own.episodes.length > 0);
    if (!strips || !own || !show) return null;
    return <DayStrip name={lineTitle(line)} from={strips.from} to={strips.to} night={strips.night} blocks={own.blocks} episodes={own.episodes} now={now}
      token={tokenOf(line.kind)} axis={index === 0} next={status.engine && line.kind !== "fog" ? nextRunShort(line.next_run, today, amountOf(line)) : undefined} />;
  };
  return <section className={cls("card", "wat-zone", apiDown && "is-neutral")} data-zone={zone} aria-labelledby={headingId}>
    <div className="wat-zone-head">
      <h2 id={headingId} className="wat-zone-name">{name} <span className="wat-zone-kind">{ZONE_KIND_WORDS[zone]}</span></h2>
      <div className="wat-zone-today" data-limit={capLine ?? undefined}>
        {cap !== null
          ? <MinutesMeter label={`${name} minutes today`} done={minutes} planned={planned ?? 0} cap={cap} kind={mister?.kind ?? lines[0]?.kind ?? "drip"} />
          : <span className="wat-zone-minutes">{minutes === null ? "—" : `${Math.round(minutes)}\u00a0min`} today</span>}
        <span className="meta wat-zone-last">{reserve !== null && <>{reserve}{"\u00a0"}min kept for VPD · </>}{lastAt ? <>last <time dateTime={lastAt}>{dayClockWords(lastAt, today)}</time></> : "no water recorded"}</span>
      </div>
    </div>
    {mister && <MisterControls line={mister} status={status} />}
    <div className="wat-zone-lines">
      {lines.map((line, index) => <LineCard key={line.line_id} line={line} master={line.line_id === "L02" ? master : null} engine={status.engine} program={status.program}
        today={today} scheduleLabels={labels} strip={strip(line, index)} />)}
    </div>
  </section>;
}
