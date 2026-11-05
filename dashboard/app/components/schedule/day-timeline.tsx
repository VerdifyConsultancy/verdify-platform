// SPDX-License-Identifier: MIT
import { useCallback, useId, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { formatValue, shortTime } from "../../lib/format";
import { useApiDown } from "../../lib/session";
import { timeScale, type TimeScale } from "../../lib/time-scale";
import { cls } from "../common";
import { Meter, meterWords, type MeterDomain } from "../data/meter";
import type { SpanStatus } from "../data/time-track";
import { cssVars, rovingKeys } from "../data/viz";
import { episodeWords, slotClock, type DayBlock, type DayModel, type DayRow, type Episode } from "./model";

/** Past this point of the track a block whose words sit beside it has them on its left, so they stay inside the row. */
const LATE_PERCENT = 82;
/** A block label's width in CSS px: 12 px IBM Plex at about 6.2 px a character, plus its padding and offset. */
const labelPx = (text: string) => text.length * 6.2 + 9;
/** The track's width before it is measured (the server's render): a desktop day timeline's. */
const TRACK_PX = 900;

/** An element's width in CSS px, followed while it resizes; null until it is measured, as on the server. */
export function useWidthOf<T extends HTMLElement>(): [(element: T | null) => void, number | null] {
  const [width, setWidth] = useState<number | null>(null);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((element: T | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!element) return;
    setWidth(element.clientWidth || null);
    if (typeof ResizeObserver !== "function") return;
    observer.current = new ResizeObserver(() => setWidth(element.clientWidth || null));
    observer.current.observe(element);
  }, []);
  return [ref, width];
}

/** The room on each side of a block, in percent of the track: after it to its next neighbour, and before it back to the last. */
export type BlockRoom = { readonly after: number; readonly before: number };
/**
 * The room beside each block on its row, in percent of the track (redesign §6.2): from its end to the next block's
 * start or the row's end, and from its start back to the end of the one before or the row's start. The now rule is a
 * neighbour too, so words never run across it.
 */
export function blockRooms(blocks: readonly DayBlock[], scale: TimeScale, now: number | null = null): ReadonlyMap<string, BlockRoom> {
  const placed = blocks.flatMap((block) => {
    const place = scale.span(block.start, block.end);
    return place ? [{ key: block.key, start: place.start, end: place.start + place.width }] : [];
  }).sort((a, b) => a.start - b.start);
  const nowAt = now !== null && now >= scale.from && now <= scale.to ? scale.pos(now) : null;
  return new Map(placed.map((item, index) => {
    let after = (index + 1 < placed.length ? placed[index + 1].start : 100) - item.end;
    let before = item.start - (index > 0 ? placed[index - 1].end : 0);
    if (nowAt !== null && nowAt > item.end) after = Math.min(after, nowAt - item.end);
    if (nowAt !== null && nowAt < item.start) before = Math.min(before, item.start - nowAt);
    return [item.key, { after: Math.max(0, after), before: Math.max(0, before) }];
  }));
}

/**
 * A block's words for the room it has (redesign §6.2, §6.6): all of them when they fit ("15:00 skipped · RH 92%",
 * "16:00 · 10 min"); else a block that is not planned keeps its time and its outcome or reason word ("15:00 skipped",
 * "07:31 missed", "07:31 ok"), and a planned one its time; else its time alone; else none, its words staying in its name,
 * the popover and the agenda. `fits` says whether a text fits.
 */
export function blockWords(label: string, status: SpanStatus, fits: (text: string) => boolean): string {
  if (fits(label)) return label;
  const time = label.slice(0, 5);
  const word = status === "planned" ? "" : label.slice(5).replace(/^\s*·?\s*/, "").split(" · ")[0];
  const short = word ? `${time} ${word}` : time;
  if (fits(short)) return short;
  return fits(time) ? time : "";
}

/** The meter's domain by line kind (redesign §4.6): drip minutes `--eq-water`, mist `--eq-mister`, feeds `--eq-fert`. */
export function meterDomain(kind: string): MeterDomain {
  return kind === "mister" ? "mister" : kind === "fert_drip" || kind === "fert_mister" ? "fert" : kind === "drip" ? "water" : "neutral";
}

/**
 * A block on its track: a button with its name in words, or a draft's outline (not saved, nothing to open). A running
 * one is solid up to now and dashed after it (redesign §6.2), through `--sch-done`. Its words sit on it when they fit,
 * else beside it, as many as the room to its neighbour takes (`room`, percent of the track `trackPx` wide): after it,
 * or before it late in the day, or on the other side when only that side has room for them.
 */
export function Block({ block, scale, onSelect, selected = false, now = null, room = { after: 0, before: 0 }, trackPx = TRACK_PX }: {
  block: DayBlock; scale: TimeScale; onSelect?: (block: DayBlock, opener: HTMLElement) => void; selected?: boolean; now?: number | null; room?: BlockRoom; trackPx?: number;
}) {
  const place = scale.span(block.start, block.end);
  if (!place) return null;
  const done = block.status === "running" && now !== null ? Math.min(100, Math.max(0, ((now - block.start) / Math.max(1, block.end - block.start)) * 100)) : 100;
  const percent = (text: string) => (labelPx(text) / Math.max(1, trackPx)) * 100;
  const narrow = percent(block.label) > place.width;
  const late = place.start > LATE_PERCENT;
  const beside = (before: boolean) => blockWords(block.label, block.status, (text) => percent(text) <= (before ? room.before : room.after));
  const usual = beside(late);
  const swapped = narrow && usual !== block.label ? beside(!late) : "";
  const before = narrow && swapped.length > usual.length ? !late : late;
  const shown = narrow ? (before === late ? usual : swapped) : block.label;
  const className = cls(
    "sch-block", `sch-${block.status}`, `sch-${block.token}`, block.part !== "run" && "sch-part", narrow && (before ? "sch-narrow-end" : "sch-narrow"),
    block.capped && "sch-capped", block.draft && "sch-draft", block.conflict && "sch-conflict", selected && "is-selected",
  );
  const vars = cssVars({ "--sch-start": place.start, "--sch-width": place.width, "--sch-done": done });
  const words = block.part === "run" && shown ? <span className="sch-block-label" aria-hidden="true">{shown}</span> : null;
  if (block.part !== "run") return <span ref={vars} className={className} title={block.name} aria-hidden="true" />;
  if (block.draft || !onSelect) {
    return <span ref={vars} className={className} title={block.name}>{words}<span className="screen-reader">{block.name}</span></span>;
  }
  return <button ref={vars} type="button" className={className} aria-label={block.name} aria-haspopup="dialog" tabIndex={-1} data-roving="" data-block={block.blockKey}
    onClick={(event) => onSelect(block, event.currentTarget)}>{words}</button>;
}

/**
 * The valve's episodes as thin ticks along the lower third (past VPD misting on a mister row, redesign §6.2), and their
 * words. With `unknownUntil` (epoch s: API-05 has not answered) the lower third is a muted hatch up to then instead: a
 * gap in what is known, never an empty "the valve did not open".
 */
export function Ticks({ episodes, scale, token, unknownUntil = null }: { episodes: readonly Episode[]; scale: TimeScale; token: string; unknownUntil?: number | null }) {
  if (unknownUntil !== null) {
    const place = scale.span(scale.from, Math.min(scale.to, unknownUntil));
    return place ? <span ref={cssVars({ "--sch-start": place.start, "--sch-width": place.width })} className="sch-tick sch-tick-unknown" title="Valve time unknown" aria-hidden="true" /> : null;
  }
  return <>
    {episodes.map((episode) => {
      const place = scale.span(episode.start, episode.end);
      return place ? <span key={episode.start} ref={cssVars({ "--sch-start": place.start, "--sch-width": place.width })} className={cls("sch-tick", `sch-tick-${token}`)} title={episodeWords(episode)} aria-hidden="true" /> : null;
    })}
  </>;
}

/** Night before sunrise and after sunset, the now rule. */
export function DayBackdrop({ night, scale, now }: { night: readonly { from: number; to: number }[]; scale: TimeScale; now: number | null }) {
  return <>
    {night.map((span) => {
      const place = scale.span(span.from, span.to);
      return place ? <span key={span.from} ref={cssVars({ "--sch-start": place.start, "--sch-width": place.width })} className="sch-night" aria-hidden="true" /> : null;
    })}
    {now !== null && now >= scale.from && now <= scale.to && <span ref={cssVars({ "--sch-at": scale.pos(now) })} className="sch-now" aria-hidden="true" />}
  </>;
}

/** The hour ticks over a day's tracks: every 3 h from 861 px, every 6 h below (both render; the stylesheet shows one). */
export function DayAxis({ scale, className }: { scale: TimeScale; className?: string }) {
  // The next midnight is the window's end: it reads 24, not a second 00.
  const wide = scale.ticks(3).filter((tick) => tick.at < scale.to);
  const narrow = new Set(scale.ticks(6).map((tick) => tick.at));
  const end = { at: scale.to, label: "24:00", pos: 100 };
  return <div className={cls("sch-axis", className)} aria-hidden="true">
    {[...wide, end].map((tick) => <span key={tick.at} ref={cssVars({ "--sch-at": tick.pos })} className={cls("sch-axis-tick", !narrow.has(tick.at) && tick !== end && "sch-axis-wide")}>{tick.label.slice(0, 2)}</span>)}
  </div>;
}

/**
 * Minutes against a daily cap in words, "14 / 120 min", "cap reached", "+12 min over" (the Meter's own words, whole
 * minutes on both sides), with the planned minutes after for its name.
 */
export function minutesAgainstCap(done: number | null, cap: number, planned = 0): { amount: string; over: string | null; name: string } {
  const { amount, over } = meterWords(done, cap, "min");
  return { amount, over, name: `${amount}${over ? `, ${over}` : ""}${planned > 0 ? `, ${Math.round(planned)}\u00a0min more planned` : ""}` };
}

/** A minutes meter against a cap (redesign §4.6): the Meter's bar (planned dashed, the cap's tick) and its words in minutes. */
export function MinutesMeter({ label, done, planned = 0, cap, kind }: { label: string; done: number | null; planned?: number; cap: number; kind: string }) {
  const words = minutesAgainstCap(done, cap, planned);
  return <span className="sch-minutes-meter">
    <Meter label={label} unit="min" value={done} planned={planned} cap={cap} domain={meterDomain(kind)} decorative />
    <span className="meter-words"><span className="meter-amount">{words.amount}</span>{words.over && <span className="meter-over"><span className="meter-sep"> · </span>{words.over}</span>}</span>
    <span className="screen-reader">{label}: {words.name}</span>
  </span>;
}

/** A row's cap: minutes done and still planned against `daily_cap_min`, or its minutes alone with no cap. */
export function CapMeter({ row, when }: { row: Pick<DayRow, "name" | "kind" | "done" | "planned" | "cap">; when: DayModel["when"] }) {
  const label = `${row.name} minutes ${when === "today" ? "today" : when === "past" ? "that day" : "planned"}`;
  if (row.cap === null) {
    const minutes = when === "future" ? row.planned : row.done;
    return <span className="sch-cap sch-cap-none">{minutes === null ? "—" : formatValue(minutes, "min")}{when === "future" && minutes ? " planned" : ""}<span className="screen-reader">, no daily cap</span></span>;
  }
  return <span className="sch-cap"><MinutesMeter label={label} done={row.done} planned={row.planned} cap={row.cap} kind={row.kind} /></span>;
}

export type DayTimelineProps = {
  model: DayModel;
  /** Epoch ms: the now rule on today. */
  now: number;
  /** The head's own nav: the day's title, the sun's times, ‹ Today ›. */
  head: ReactNode;
  /** Names the track for a screen reader: "Runs on Monday, Nov 2". */
  label: string;
  /** The selected block's key, while its popover is open. */
  selected?: string | null;
  onBlock?: (block: DayBlock, opener: HTMLElement) => void;
  /** Operators: the editor for a new schedule on a line, prefilled; the row's [+] and an empty slot's click. */
  newTo?: (lineId: string, clock?: string) => string;
  onSlot?: (lineId: string, clock: string) => void;
};

/** The end column's head: what its minutes are against the cap, by the day shown. */
const END_HEAD: Readonly<Record<DayModel["when"], string>> = { today: "today / cap", past: "that day / cap", future: "planned / cap" };

/**
 * The day timeline (redesign §6.2): one local day across the width (25 h on the fall-back day), rows grouped by manifold
 * with night shaded, the now rule, each run as a block as long as its schedule (solid done, dashed planned, hatched when
 * it will not run, a fault outline when missed, a muted hatch when its outcome is unknown), past valve episodes as ticks,
 * a pause band with who paused it, and at each row's end its minutes against the cap and [+]. Blocks are one tab stop
 * whose arrow keys move along them; each opens its popover. An operator's click on an empty part of a row opens the
 * editor prefilled with that line, the day and the time rounded to 15 min; the row's [+] is the keyboard path.
 */
export function DayTimeline({ model, now, head, label, selected, onBlock, newTo, onSlot }: DayTimelineProps) {
  const headingId = useId();
  const apiDown = useApiDown();
  const scale = timeScale(model.from, model.to);
  // Every row's track is one width: the first one's, measured, sizes the blocks' words.
  const [trackRef, trackPx] = useWidthOf<HTMLDivElement>();
  const nowS = model.when === "today" ? now / 1000 : null;
  const slot = (row: DayRow) => (event: MouseEvent<HTMLDivElement>) => {
    if (!onSlot || (event.target as Element).closest("button, a")) return;
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width <= 0) return;
    onSlot(row.lineId, slotClock(model.day, (event.clientX - box.left) / box.width));
  };
  return <section className={cls("sch-day", apiDown && "is-neutral")}>
    <div className="sch-day-head">{head}</div>
    <div className="sch-day-body" role="group" aria-label={label} tabIndex={0} onKeyDown={rovingKeys()}>
      <div className="sch-axis-row"><span className="sch-axis-gap" /><DayAxis scale={scale} /><span className="sch-axis-end meta">{END_HEAD[model.when]}</span></div>
      {model.groups.map((group, groupIndex) => <div key={group.key} className="sch-group">
        <p className="eyebrow sch-group-label" id={`${headingId}-${group.key}`}>{group.label}</p>
        <ul className="sch-rows" aria-labelledby={`${headingId}-${group.key}`}>
          {group.rows.map((row, rowIndex) => <li key={row.lineId} className={cls("sch-row", row.muted && "is-muted")} data-line={row.lineId}>
            <div className="sch-row-head">
              <span className="sch-row-name" title={row.name}>{row.name}</span>
              {row.note && <span className="sch-row-note">{row.note}</span>}
            </div>
            <div ref={groupIndex === 0 && rowIndex === 0 ? trackRef : undefined} className={cls("sch-track", onSlot && "sch-track-new")} onClick={onSlot ? slot(row) : undefined}>
              <DayBackdrop night={model.night} scale={scale} now={nowS} />
              {row.pause && (() => {
                const place = scale.span(row.pause.start, row.pause.end);
                return place && <span ref={cssVars({ "--sch-start": place.start, "--sch-width": place.width })} className="sch-pause"><span className="sch-pause-words">{row.pause.words}</span></span>;
              })()}
              <Ticks episodes={row.episodes} scale={scale} token={row.kind === "mister" || row.kind === "fert_mister" ? "mister" : row.kind === "fert_drip" ? "fert" : "water"} unknownUntil={row.valveKnown ? null : nowS} />
              {(() => {
                const rooms = blockRooms(row.blocks, scale, nowS);
                return row.blocks.map((block) => <Block key={block.key} block={block} scale={scale} onSelect={onBlock} selected={selected === block.blockKey && block.blockKey !== ""} now={nowS}
                  room={rooms.get(block.key)} trackPx={trackPx ?? TRACK_PX} />);
              })()}
              {row.valveKnown ? row.episodes.length > 0 && <span className="screen-reader">{row.episodes.map(episodeWords).join("; ")}</span> : <span className="screen-reader">valve time unknown</span>}
            </div>
            <div className="sch-row-end">
              <CapMeter row={row} when={model.when} />
              {newTo && <Link className="button-link sch-add" to={newTo(row.lineId)} preventScrollReset data-write-control="true">
                <span aria-hidden="true">+</span><span className="screen-reader">New schedule on {row.name}</span>
              </Link>}
            </div>
          </li>)}
        </ul>
      </div>)}
    </div>
  </section>;
}

/** "sunrise 06:31 · solar noon 11:42 · sunset 16:52" for the day's head; null without its solar entry. */
export function sunWords(solar: DayModel["solar"]): string | null {
  if (!solar) return null;
  return `sunrise ${shortTime(solar.sunrise)} · solar noon ${shortTime(solar.solar_noon)} · sunset ${shortTime(solar.sunset)}`;
}
