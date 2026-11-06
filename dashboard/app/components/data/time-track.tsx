// SPDX-License-Identifier: MIT
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { shortTime } from "../../lib/format";
import { useApiDown } from "../../lib/session";
import { timeScale, type TimeScale } from "../../lib/time-scale";
import { cls } from "../common";
import { Popover } from "../popover";
import { STATE_WORDS, cssVars, rovingKeys, type VizState } from "./viz";

/**
 * What a span is (redesign §4.6, §6.2): done is a solid fill in its token; planned a 1.5 px dashed outline on white;
 * running solid to now and dashed after; skipped and blocked a `--line` hatch with a `--muted` outline; missed a
 * `--fault` outline; unknown a `--muted` hatch (a gap, never missed and never off); conditional, a fallback window that runs
 * only if VPD sensing fails, a dotted `--eq-heat1` outline with no fill (a light's window in which it may switch on, a
 * dotted outline in its own token); intent, what the controller wants where it differs from the readback, a dashed ink
 * outline with no fill over whatever the readback drew.
 */
export type SpanStatus = "done" | "planned" | "running" | "skipped" | "blocked" | "missed" | "unknown" | "conditional" | "intent";
/** The equipment or cause tokens a span may take (`charts/causes.ts` maps a cause to one). */
export type SpanToken = "eq-water" | "eq-fert" | "eq-fog" | "eq-mister" | "eq-heat1" | "eq-heat2" | "eq-fan1" | "eq-fan2" | "eq-vent" | "eq-master" | "eq-light-main" | "eq-light-grow" | "muted";

export type TrackSpan = {
  readonly key: string;
  /** Epoch seconds. */
  readonly start: number;
  readonly end: number;
  readonly status: SpanStatus;
  readonly token?: SpanToken;
  /** Short words on or beside it: "07:31 ok", "4 × 45 s", "skipped · RH 92%". */
  readonly label?: string;
  /** Its name in words, for a span with detail: "L01 Wall drip, Tue 07:31, 12 min, done, ok". */
  readonly name?: string;
  /** A span with detail opens it: a button (the popover) or a link. */
  readonly onSelect?: (opener: HTMLElement) => void;
  readonly href?: string;
};
export type TrackMarker = {
  readonly key: string;
  readonly at: number;
  /** Short words beside the mark: "Sunrise plan ✓", "Mist pulse gap 45 → 55 s · AI". */
  readonly label: string;
  /** The popover's heading; the label when absent. */
  readonly title?: string;
  /** The popover's lines, in full: each member of a merged mark, a change with its time. The label when absent. */
  readonly detail?: readonly string[];
};
export type TrackRow = {
  readonly key: string;
  readonly label: ReactNode;
  readonly spans: readonly TrackSpan[];
  readonly markers?: readonly TrackMarker[];
  /** A disabled line's row, read muted ("Disabled: its 3 runs this week will not run"). */
  readonly muted?: boolean;
  /** A note across the row in place of spans, or beside them ("paused until Sat 09:00 · jason"). */
  readonly note?: ReactNode;
  /** What ends the row: its cap `Meter`, a [+] button. */
  readonly end?: ReactNode;
  /** Data hooks the screen keeps (`data-line`, `data-equipment`). */
  readonly data?: Readonly<Record<`data-${string}`, string>>;
};

export type TimeTrackProps = {
  /** Names the track for a screen reader: "Running and next, the last 6 h and the next 12 h". */
  label: string;
  /** The window, epoch seconds. */
  from: number;
  to: number;
  rows: readonly TrackRow[];
  /** The now rule, a 1.5 px ink line, when inside the window. */
  now?: number | null;
  /** Night, before sunrise and after sunset: a `--field` wash behind the rows. */
  night?: readonly { readonly from: number; readonly to: number }[];
  /** The tick row: every 3 h at 861 px and up, every 6 h below (both render; the stylesheet shows one). */
  axis?: boolean;
  state?: VizState;
};

const STATUS_WORDS: Readonly<Record<SpanStatus, string>> = {
  done: "done", planned: "planned", running: "running", skipped: "skipped", blocked: "blocked", missed: "missed", unknown: "outcome unknown", conditional: "fallback", intent: "the controller's intent",
};
/** Under this share of the window a span's words sit after it rather than on it. */
const ROOM_PERCENT = 9;
/** Past this point of the window a marker's or a narrow span's words sit before it, so the track never cuts them off. */
const LATE_PERCENT = 85;
/**
 * Under this share of the window a label is left out rather than cut to a stub: its words stay in the span's name and
 * title, the marker's title, the popover and the agenda.
 */
const MIN_ROOM_PERCENT = 5;

/** Where a label's words run: on its span (a planned or missed one wide enough), after its mark, or before it late in the window. */
type LabelSide = "on" | "after" | "before";
/** The marks on a row a label must stop at, in percent of the window: each span's start and end, each marker and the now rule. */
type RowMarks = { readonly starts: readonly number[]; readonly ends: readonly number[]; readonly points: readonly number[] };

function rowMarks(row: TrackRow, scale: TimeScale, now: number | null): RowMarks {
  const places = row.spans.flatMap((span) => scale.span(span.start, span.end) ?? []);
  const points = (row.markers ?? []).filter((marker) => marker.at >= scale.from && marker.at <= scale.to).map((marker) => scale.pos(marker.at));
  return { starts: places.map((place) => place.start), ends: places.map((place) => place.start + place.width), points: now === null ? points : [...points, scale.pos(now)] };
}

/**
 * A row of markers alone (Iris, the plan, the sun) has two text lanes (#1340): its markers in time order take turns
 * above and below, so neighbours never cut each other; a label stops only at the next mark in its own lane. The now rule
 * draws over a label rather than cutting it.
 */
export function markerLanes(markers: readonly TrackMarker[], scale: TimeScale): { readonly lane: Map<string, 0 | 1>; readonly marks: readonly [RowMarks, RowMarks] } {
  const shown = markers.filter((marker) => marker.at >= scale.from && marker.at <= scale.to).sort((a, b) => a.at - b.at);
  const lane = new Map<string, 0 | 1>(shown.map((marker, index) => [marker.key, (index % 2) as 0 | 1]));
  const points = (which: 0 | 1) => shown.filter((marker) => lane.get(marker.key) === which).map((marker) => scale.pos(marker.at));
  return { lane, marks: [{ starts: [], ends: [], points: points(0) }, { starts: [], ends: [], points: points(1) }] };
}

/**
 * The room a label has, in percent of the window (redesign §4.6): from where its words start to the next span, marker or
 * the now rule, or back to the one before for words set before their mark, so neighbours never print over each other.
 */
export function labelRoom(origin: number, side: LabelSide, marks: RowMarks): number {
  if (side === "before") return origin - Math.max(0, ...[...marks.ends, ...marks.points].filter((at) => at < origin - 1e-9));
  return Math.min(100, ...[...marks.starts, ...marks.points].filter((at) => at > origin + 1e-9)) - origin;
}

function Span({ span, scale, marks }: { span: TrackSpan; scale: TimeScale; marks: RowMarks }) {
  const place = scale.span(span.start, span.end);
  if (!place) return null;
  const narrow = place.width < ROOM_PERCENT;
  const late = place.start + place.width > LATE_PERCENT;
  const className = cls("tt-span", `tt-${span.status}`, span.token && `tt-${span.token}`, narrow && "tt-narrow", narrow && late && "tt-narrow-end");
  const side: LabelSide = narrow ? (late ? "before" : "after") : span.status === "planned" || span.status === "missed" ? "on" : "after";
  const room = labelRoom(side === "after" ? place.start + place.width : place.start, side, marks);
  const vars = cssVars({ "--tt-start": place.start, "--tt-width": place.width, "--tt-room": room });
  // Cut with an ellipsis where the neighbour starts, and left out where even a stub would not fit.
  const words = span.label && room >= MIN_ROOM_PERCENT ? <span className="tt-span-label" aria-hidden="true">{span.label}</span> : null;
  const name = span.name ?? (span.label ? `${span.label}, ${STATUS_WORDS[span.status]}` : STATUS_WORDS[span.status]);
  if (span.href !== undefined) return <Link ref={vars} to={span.href} className={className} aria-label={name} tabIndex={-1} data-roving="">{words}</Link>;
  if (span.onSelect) {
    const select = span.onSelect;
    return <button ref={vars} type="button" className={className} aria-label={name} tabIndex={-1} data-roving="" onClick={(event) => select(event.currentTarget)}>{words}</button>;
  }
  return <span ref={vars} className={className} title={name}>{words}<span className="screen-reader">{name}</span></span>;
}

/**
 * A marker: its dashed tick and its words, one button that opens the marker's detail in a popover (#1340), so nothing
 * lives only in a label the track had to cut. A label with no room is not drawn; the tick still opens it.
 */
function Marker({ marker, scale, marks, lane, onOpen }: { marker: TrackMarker; scale: TimeScale; marks: RowMarks; lane: 0 | 1 | null; onOpen: (marker: TrackMarker, anchor: HTMLElement) => void }) {
  if (marker.at < scale.from || marker.at > scale.to) return null;
  const at = scale.pos(marker.at);
  const room = labelRoom(at, at > LATE_PERCENT ? "before" : "after", marks);
  return <span ref={cssVars({ "--tt-at": at, "--tt-room": room })} className={cls("tt-marker", at > LATE_PERCENT && "tt-marker-end", lane === 1 && "tt-marker-low")}>
    <button type="button" className="tt-marker-button" tabIndex={-1} data-roving="" aria-haspopup="dialog" onClick={(event) => onOpen(marker, event.currentTarget)}>
      <span className={cls("tt-marker-label", room < MIN_ROOM_PERCENT && "screen-reader")} title={marker.title ?? marker.label}>{marker.label}</span>
    </button>
  </span>;
}

/** Ticks this close to an end of the window keep their words inside it; the rest are centred on their hour. */
const EDGE_PERCENT = 4;
/** A tick this close to the now rule leaves its words out, so they never sit under "now 10:42". */
const NEAR_NOW_PERCENT = 6;

/**
 * The tick row: each hour's words centred on a 4 px mark at its true position (anchored inside the window only within
 * `EDGE_PERCENT` of an end), and the now rule named at the top of the track, "now 10:42".
 */
function Ticks({ scale, now }: { scale: TimeScale; now: number | null }) {
  const wide = scale.ticks(3), narrow = new Set(scale.ticks(6).map((tick) => tick.at));
  const nowAt = now === null ? null : scale.pos(now);
  return <div className="tt-axis" aria-hidden="true">
    {wide.map((tick) => <span key={tick.at} ref={cssVars({ "--tt-at": tick.pos })}
      className={cls("tt-tick", !narrow.has(tick.at) && "tt-tick-wide", tick.pos < EDGE_PERCENT && "tt-tick-start", tick.pos > 100 - EDGE_PERCENT && "tt-tick-end")}>
      <span className={cls("tt-tick-words", nowAt !== null && Math.abs(tick.pos - nowAt) < NEAR_NOW_PERCENT && "tt-tick-quiet")}>{tick.label}</span>
    </span>)}
    {now !== null && nowAt !== null && <span ref={cssVars({ "--tt-at": nowAt })} className={cls("tt-now-label", nowAt < EDGE_PERCENT && "tt-tick-start", nowAt > 100 - EDGE_PERCENT && "tt-tick-end")}>now {shortTime(new Date(now * 1000).toISOString())}</span>}
  </div>;
}

/**
 * TimeTrack (redesign §4.6): rows of spans over one local time scale (`lib/time-scale.ts`, DST-correct), 28 px a row on
 * desktop and 24 px on phones, with night washed, markers (Iris, the plan) as dashed ink ticks, and the now rule. The rows
 * are a `<ul>`, so the track reads as a list; a span with detail is a button named in words. Spans are `tabIndex={-1}`
 * and the track is one tab stop whose arrow keys move along them (`rovingKeys`), so a 12-minute run never becomes an
 * undersized tab stop. Positions are CSSOM custom properties, never a style attribute. While the API is down every span
 * is neutral. A track that draws no rows (loading, empty, unknown, an error) names no time either: its axis row keeps
 * its height with no ticks, so Now's server render, the loading track, never depends on the clock, and a browser whose
 * clock reads otherwise (a skewed phone, the browser suite's pinned clock) hydrates it unchanged.
 */
export function TimeTrack({ label, from, to, rows, now = null, night = [], axis = true, state = "ready" }: TimeTrackProps) {
  const apiDown = useApiDown();
  const scale = timeScale(from, to);
  const drawn = state === "ready" || state === "stale";
  const showNow = now !== null && now >= from && now <= to;
  // A row's end column (`row.end`) needs its grid column on every row, so the track keeps one width.
  const withEnd = rows.some((row) => row.end !== undefined);
  const [opened, setOpened] = useState<{ readonly marker: TrackMarker; readonly anchor: HTMLElement } | null>(null);
  const open = (marker: TrackMarker, anchor: HTMLElement) => setOpened((current) => (current?.marker.key === marker.key ? null : { marker, anchor }));
  return <div className={cls("tt", withEnd && "tt-with-end", state === "stale" && "is-stale", apiDown && "is-neutral", state === "loading" && "viz-skeleton")} role="group" aria-label={label}
    tabIndex={drawn ? 0 : undefined} onKeyDown={drawn ? rovingKeys() : undefined}>
    {axis && (drawn ? <Ticks scale={scale} now={showNow ? now : null} /> : <div className="tt-axis" aria-hidden="true" />)}
    {drawn
      ? <ul className="tt-rows">
        {rows.map((row) => {
          const marks = rowMarks(row, scale, showNow ? now : null);
          const lanes = row.spans.length === 0 && (row.markers?.length ?? 0) > 0 ? markerLanes(row.markers ?? [], scale) : null;
          return <li key={row.key} className={cls("tt-row", row.muted && "tt-row-muted", lanes && "tt-row-lanes")} {...row.data}>
          <span className="tt-label">{row.label}</span>
          <span className="tt-track">
            {night.map((span) => {
              const place = scale.span(span.from, span.to);
              return place ? <span key={span.from} ref={cssVars({ "--tt-start": place.start, "--tt-width": place.width })} className="tt-night" aria-hidden="true" /> : null;
            })}
            {row.spans.map((span) => <Span key={span.key} span={span} scale={scale} marks={marks} />)}
            {row.markers?.map((marker) => {
              const lane = lanes?.lane.get(marker.key) ?? null;
              return <Marker key={marker.key} marker={marker} scale={scale} marks={lanes && lane !== null ? lanes.marks[lane] : marks} lane={lane} onOpen={open} />;
            })}
            {showNow && <span ref={cssVars({ "--tt-at": scale.pos(now) })} className="tt-now" aria-hidden="true" />}
            {row.note && <span className="tt-note">{row.note}</span>}
          </span>
          {row.end !== undefined && <span className="tt-end">{row.end}</span>}
        </li>;
        })}
      </ul>
      : <p className="tt-empty meta">{STATE_WORDS[state]}</p>}
    <Popover open={opened !== null} onClose={() => setOpened(null)} anchor={opened?.anchor ?? null} title={opened ? opened.marker.title ?? opened.marker.label : ""} className="tt-marker-popover">
      {opened && <ul className="tt-marker-detail">{(opened.marker.detail ?? [opened.marker.label]).map((line, index) => <li key={index}>{line}</li>)}</ul>}
    </Popover>
  </div>;
}
