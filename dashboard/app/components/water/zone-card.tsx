// SPDX-License-Identifier: MIT
import { useId } from "react";
import { lineDisplayName } from "../../lib/lines";
import type { IrrigationLine, IrrigationStatus, Schedule } from "../../lib/operator-api";
import { useApiDown } from "../../lib/session";
import { ZONE_CARD_WORDS, ZONE_KIND_WORDS, amountShort, dayClockWords, nextRunShort, vpdTodayWords, zoneLastAt, type ZoneSummary } from "../../lib/water";
import { cls } from "../common";
import { LineCard } from "../line-card";
import { MisterControls } from "../mister-association";
import { MinutesMeter } from "../schedule/day-timeline";
import type { DayBlock, LaneEpisode } from "../schedule/model";
import { DayStrip } from "./day-strip";

/** Today's window for the strips, and each line's blocks and valve episodes on it (`dayModel`); null before API-IRR-02 answers. */
export type TodayStrips = {
  readonly from: number;
  readonly to: number;
  readonly night: readonly { from: number; to: number }[];
  readonly byLine: ReadonlyMap<string, { readonly blocks: readonly DayBlock[]; readonly episodes: readonly LaneEpisode[]; readonly valveKnown: boolean }>;
};

const tokenOf = (kind: string) => (kind === "mister" ? "mister" : kind === "fert_drip" || kind === "fert_mister" ? "fert" : kind === "fog" ? "fog" : "water");

export type ZoneCardProps = {
  summary: ZoneSummary;
  status: Pick<IrrigationStatus, "engine" | "program"> & Partial<Pick<IrrigationStatus, "limits">>;
  today: string;
  /** Epoch ms. */
  now: number;
  strips: TodayStrips | null;
  /** API-IRR-02's schedules, for "Running · schedule · Wall MWF" and the next run's amount. */
  schedules: readonly Schedule[];
};

/** A card's kind, for its coloured edge (#1349): drip, mister or fog. */
export const zoneKind = (zone: string): "drip" | "mister" | "fog" => (zone === "fog" ? "fog" : zone === "wall" || zone === "center" ? "drip" : "mister");

/**
 * A physical line's card on Water → Today (redesign §5.2, #1336, #1349): a coloured edge by kind, the line's name and
 * variants, its minutes today against the cap line's daily cap (planned minutes dashed) and when it last watered,
 * "running now" while one of its lines runs; then a row per wired variant in register order, L03 under L02, the clean
 * mister's row holding VPD control and Schedule (#1344), and "Fertilized · not wired" for a variant with no relay. Every
 * row has its today strip, a disabled one faded (#1347); a card whose lines are all off reads quiet. The order never
 * changes with state.
 */
export function ZoneCard({ summary, status, today, now, strips, schedules }: ZoneCardProps) {
  const headingId = useId();
  const apiDown = useApiDown();
  const { zone, lines, master, mister, minutes, planned, cap, capLine, reserve, running, unwired } = summary;
  const name = ZONE_CARD_WORDS[zone];
  // The zone's minutes count every purpose (the cap does); its mister's VPD misting is said apart (#1348).
  const vpd = vpdTodayWords(mister?.today);
  // Today's valve starts (API-05) stand in for a `last_water` the API has not filed yet once today's minutes are above 0.
  const lastAt = zoneLastAt(summary, lines.flatMap((line) => strips?.byLine.get(line.line_id)?.episodes.map((episode) => episode.start) ?? []), today);
  const labels = new Map(schedules.flatMap((schedule) => (schedule.label ? [[schedule.slot_id, schedule.label] as const] : [])));
  const bySlot = new Map(schedules.map((schedule) => [schedule.slot_id, schedule]));
  const amountOf = (line: IrrigationLine) => {
    const schedule = line.next_run ? bySlot.get(line.next_run.slot_id) : undefined;
    return schedule ? amountShort(schedule, line.kind) : null;
  };
  const strip = (line: IrrigationLine, index: number) => {
    const own = strips?.byLine.get(line.line_id);
    if (!strips || !own) return null;
    return <DayStrip name={lineDisplayName(line)} from={strips.from} to={strips.to} night={strips.night} blocks={own.blocks} episodes={own.episodes} valveKnown={own.valveKnown} now={now}
      token={tokenOf(line.kind)} axis={index === 0} next={status.engine && line.kind !== "fog" ? nextRunShort(line.next_run, today, amountOf(line)) : undefined} />;
  };
  const off = zone !== "fog" && lines.length > 0 && lines.every((line) => line.enabled === false) && !running;
  return <section className={cls("card", "wat-zone", off && "is-off", apiDown && "is-neutral")} data-zone={zone} data-kind={zoneKind(zone)} aria-labelledby={headingId}>
    <div className="wat-zone-head">
      <h3 id={headingId} className="wat-zone-name">{name} <span className="wat-zone-kind">{off ? "off" : ZONE_KIND_WORDS[zone]}</span></h3>
      <div className="wat-zone-today" data-limit={capLine ?? undefined}>
        {cap !== null
          ? <MinutesMeter label={`${name} minutes today`} done={minutes} planned={planned ?? 0} cap={cap} kind={mister?.kind ?? lines[0]?.kind ?? "drip"} />
          : <span className="wat-zone-minutes">{minutes === null ? "—" : `${Math.round(minutes)}\u00a0min`} today</span>}
        {vpd && <span className="meta wat-zone-vpd" data-water="vpd">{vpd}</span>}
        <span className="meta wat-zone-last">{reserve !== null && <>{reserve}{"\u00a0"}min kept for VPD · </>}{running ? <span className="wat-zone-running">running now</span>
          : lastAt ? <>last <time dateTime={lastAt}>{dayClockWords(lastAt, today)}</time></> : "no water recorded"}</span>
      </div>
    </div>
    <div className="wat-zone-lines">
      {lines.map((line, index) => <LineCard key={line.line_id} line={line} master={line.line_id === "L02" ? master : null} engine={status.engine} program={status.program}
        today={today} scheduleLabels={labels} strip={strip(line, index)} controls={line === mister ? <MisterControls line={line} status={status} /> : undefined} />)}
      {unwired.map((variant) => <p key={variant} className="meta wat-unwired" data-variant={variant}>{variant === "clean" ? "Clean" : "Fertilized"} · not wired</p>)}
    </div>
  </section>;
}
