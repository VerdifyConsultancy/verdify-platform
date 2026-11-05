// SPDX-License-Identifier: MIT
import { useApiDown } from "../../lib/session";
import { timeScale } from "../../lib/time-scale";
import { cls } from "../common";
import { Block, DayAxis, DayBackdrop, Ticks, blockRooms, useWidthOf } from "../schedule/day-timeline";
import { episodeWords, type DayBlock, type Episode } from "../schedule/model";

export type DayStripProps = {
  /** "L01 Wall drip": names the strip's picture. */
  name: string;
  /** Today's window, epoch seconds (`localDayWindow`: 25 h on the fall-back day). */
  from: number;
  to: number;
  night: readonly { from: number; to: number }[];
  blocks: readonly DayBlock[];
  episodes: readonly Episode[];
  /** False while API-05 has not answered: no tick is then unknown, never none. */
  valveKnown?: boolean;
  /** Epoch ms: the now rule. */
  now: number;
  /** The valve's colour: water, mister, fert or fog. */
  token: string;
  /** The hour ticks over it: the first row of a zone card only. */
  axis?: boolean;
  /** "next 16:00 · 10 min" beside the strip; null reads "nothing planned"; left out, nothing (fog, before the engine). */
  next?: string | null;
};

/** The strip's picture in words: each block's time and status, then the valve's episodes. */
export function stripWords(name: string, blocks: readonly DayBlock[], episodes: readonly Episode[], valveKnown = true): string {
  // Each block's own name without the line's: "Wall MWF, Mon 07:35, 12 min, done, ok".
  const runs = blocks.filter((block) => block.part === "run").map((block) => (block.name.startsWith(name) ? block.name.slice(name.length).replace(/^( · |, )/, "") : block.name));
  // Valve time the API has not given is unknown, never none.
  const parts = [...runs, ...(valveKnown ? episodes.map(episodeWords) : ["valve time unknown"])];
  return `${name} today: ${parts.length ? parts.join("; ") : "no runs and no valve time"}`;
}

/**
 * A line's today strip on Water → Today (redesign §5.2): one row from local midnight to the next, with night washed, the
 * scheduled runs as the day timeline draws them (solid done, dashed planned, hatched when they will not run) and the
 * valve's open time as ticks along the lower third, the now rule, and the next run beside it. It is a picture with its
 * numbers in its name, not a tab stop: the row's state sentence, "Runs of L01 ›" and Schedules carry the detail.
 */
export function DayStrip({ name, from, to, night, blocks, episodes, valveKnown = true, now, token, axis = false, next }: DayStripProps) {
  const apiDown = useApiDown();
  const scale = timeScale(from, to);
  // A block's words take the room to its neighbour on this strip, measured once it is drawn.
  const [trackRef, trackPx] = useWidthOf<HTMLDivElement>();
  const rooms = blockRooms(blocks, scale);
  return <div className={cls("wat-day", apiDown && "is-neutral")}>
    {axis && <DayAxis scale={scale} className="wat-day-axis" />}
    <div className="wat-day-row">
      <div ref={trackRef} className="sch-track wat-track" role="img" aria-label={stripWords(name, blocks, episodes, valveKnown)}>
        <DayBackdrop night={night} scale={scale} now={now / 1000} />
        <Ticks episodes={episodes} scale={scale} token={token} unknownUntil={valveKnown ? null : now / 1000} />
        {blocks.map((block) => <Block key={block.key} block={block} scale={scale} now={now / 1000} room={rooms.get(block.key)} trackPx={trackPx ?? 600} />)}
      </div>
      {next !== undefined && <p className="wat-next">{next ?? <span className="meta">nothing planned</span>}</p>}
    </div>
  </div>;
}
