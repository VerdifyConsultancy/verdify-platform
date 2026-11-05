// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useApiDown } from "../../lib/session";
import { timeScale, type TimeScale } from "../../lib/time-scale";
import { cls } from "../common";
import { STATE_WORDS, cssVars, rovingKeys, type VizState } from "./viz";

/**
 * What a span is (redesign §4.6, §6.2): done is a solid fill in its token; planned a 1.5 px dashed outline on white;
 * running solid to now and dashed after; skipped and blocked a `--line` hatch with a `--muted` outline; missed a
 * `--fault` outline; unknown a `--muted` hatch (a gap, never missed and never off); conditional, a fallback window that runs
 * only if VPD sensing fails, a dotted `--eq-heat1` outline with no fill.
 */
export type SpanStatus = "done" | "planned" | "running" | "skipped" | "blocked" | "missed" | "unknown" | "conditional";
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
  /** "SUNRISE", "13:00 engage 1.60 → 1.70 (AI)". */
  readonly label: string;
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
  done: "done", planned: "planned", running: "running", skipped: "skipped", blocked: "blocked", missed: "missed", unknown: "outcome unknown", conditional: "fallback",
};
/** Under this share of the window a span's words sit after it rather than on it. */
const ROOM_PERCENT = 9;
/** Past this point of the window a marker's or a narrow span's words sit before it, so the track never cuts them off. */
const LATE_PERCENT = 85;

function Span({ span, scale }: { span: TrackSpan; scale: TimeScale }) {
  const place = scale.span(span.start, span.end);
  if (!place) return null;
  const narrow = place.width < ROOM_PERCENT;
  const className = cls("tt-span", `tt-${span.status}`, span.token && `tt-${span.token}`, narrow && "tt-narrow", narrow && place.start + place.width > LATE_PERCENT && "tt-narrow-end");
  const vars = cssVars({ "--tt-start": place.start, "--tt-width": place.width });
  const words = span.label ? <span className="tt-span-label">{span.label}</span> : null;
  const name = span.name ?? (span.label ? `${span.label}, ${STATUS_WORDS[span.status]}` : STATUS_WORDS[span.status]);
  if (span.href !== undefined) return <Link ref={vars} to={span.href} className={className} aria-label={name} tabIndex={-1} data-roving="">{words}</Link>;
  if (span.onSelect) {
    const select = span.onSelect;
    return <button ref={vars} type="button" className={className} aria-label={name} tabIndex={-1} data-roving="" onClick={(event) => select(event.currentTarget)}>{words}</button>;
  }
  return <span ref={vars} className={className} title={name}>{words}<span className="screen-reader">{name}</span></span>;
}

function Marker({ marker, scale }: { marker: TrackMarker; scale: TimeScale }) {
  if (marker.at < scale.from || marker.at > scale.to) return null;
  const at = scale.pos(marker.at);
  return <span ref={cssVars({ "--tt-at": at })} className={cls("tt-marker", at > LATE_PERCENT && "tt-marker-end")}><span className="tt-marker-label">{marker.label}</span></span>;
}

function Ticks({ scale }: { scale: TimeScale }) {
  const wide = scale.ticks(3), narrow = new Set(scale.ticks(6).map((tick) => tick.at));
  return <div className="tt-axis" aria-hidden="true">
    {wide.map((tick) => <span key={tick.at} ref={cssVars({ "--tt-at": tick.pos })} className={cls("tt-tick", !narrow.has(tick.at) && "tt-tick-wide")}>{tick.label}</span>)}
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
  return <div className={cls("tt", withEnd && "tt-with-end", state === "stale" && "is-stale", apiDown && "is-neutral", state === "loading" && "viz-skeleton")} role="group" aria-label={label}
    tabIndex={drawn ? 0 : undefined} onKeyDown={drawn ? rovingKeys() : undefined}>
    {axis && (drawn ? <Ticks scale={scale} /> : <div className="tt-axis" aria-hidden="true" />)}
    {drawn
      ? <ul className="tt-rows">
        {rows.map((row) => <li key={row.key} className={cls("tt-row", row.muted && "tt-row-muted")} {...row.data}>
          <span className="tt-label">{row.label}</span>
          <span className="tt-track">
            {night.map((span) => {
              const place = scale.span(span.from, span.to);
              return place ? <span key={span.from} ref={cssVars({ "--tt-start": place.start, "--tt-width": place.width })} className="tt-night" aria-hidden="true" /> : null;
            })}
            {row.spans.map((span) => <Span key={span.key} span={span} scale={scale} />)}
            {row.markers?.map((marker) => <Marker key={marker.key} marker={marker} scale={scale} />)}
            {showNow && <span ref={cssVars({ "--tt-at": scale.pos(now) })} className="tt-now" aria-hidden="true" />}
            {row.note && <span className="tt-note">{row.note}</span>}
          </span>
          {row.end !== undefined && <span className="tt-end">{row.end}</span>}
        </li>)}
      </ul>
      : <p className="tt-empty meta">{STATE_WORDS[state]}</p>}
  </div>;
}
