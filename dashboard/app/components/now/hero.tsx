// SPDX-License-Identifier: MIT
/**
 * Now's ink hero (redesign §5.1, §4.2 "Status hero"): the one-sentence answer as the page's heading (`now-answer.ts`), the
 * indoor temperature as the hero value with "Updated N s ago" (`#data`, which opens "All readings" in place), three band
 * bars (temperature, VPD and RH against the band in force, each with today's low and high, a verdict word only when out
 * of band), time in band today against yesterday, the status line (mode, Iris and the plan in force, outdoor, occupancy),
 * and the "All readings" disclosure with every source's time and the raw mode code.
 *
 * Honesty: rows 1 to 3 and 7 of the answer mute the values with STALE and show no verdict, and rows 1 to 3 read the mode
 * as unknown. While the dashboard API is down the band bars and "In band today" do not render at all. A null reading is
 * "—" with its reason, never 0, and a bar with no value is hatched.
 */
import { useEffect, useId, useRef, type MouseEvent, type ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { duration, formatValue, localDate } from "../../lib/format";
import { LOADING_TEXT, type NowAnswer } from "../../lib/now-answer";
import type { Freshness, IrrigationStatus, Now, Reading, Series } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { useApiDown } from "../../lib/session";
import { words } from "../../lib/words";
import { Badge } from "../badge";
import { Button } from "../button";
import { LocalTime, cls } from "../common";
import { BandBar } from "../data/band-bar";
import { AgeLabel, FreshnessDot, useNow } from "../freshness";
import { Kv, type KvItem } from "../kv";
import { bandRows, bandWords, hasTimeInBand, outOfBand, percentText, readingText, timeInBand, todayStartS, type BandRow, type TimeInBand } from "./climate";
import { deviceUnknown, irisText, irisWarns, modeText, occupancyText, planLine } from "./status";

/** The eyebrow: the time zone is stated once, in the footer and on axes (D-UX-08). */
export const NOW_EYEBROW = "Greenhouse · Longmont";
const NO_ANSWER = "No answer from the greenhouse data";
const ONE_DP = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
/** The hashes that open "All readings": `#data` (the ingestor banner, the answer's row 2) and `#readings`. */
const READINGS_HASHES = new Set(["#data", "#readings"]);
export const READINGS_ID = "readings";

/**
 * The answer, linked to the page that explains it. Row 2's `#data` is the freshness link on this page: a router link, so
 * the router sees the hash and Now opens "All readings" for it.
 */
export function AnswerTitle({ answer }: { answer: NowAnswer }) {
  if (!answer.link) return <>{answer.text}</>;
  return <Link className="hero-answer-link" to={answer.link.to}>{answer.text}</Link>;
}

/**
 * "band in force 62.0–70.0 °F · DERIVED · 4 min": the newest house band rows, their age ticking from `band.observed_at`.
 * With no `observed_at` the line states no age, never a guessed one.
 */
export function bandLine(band: Now["band"], clock: number): string {
  const { low, high } = band.temp_f;
  if (!finite(low) || !finite(high)) return "band unavailable";
  const at = validTime(band.observed_at) ? Date.parse(band.observed_at) : null;
  const age = at === null ? "" : ` · ${duration(Math.max(0, Math.floor((clock - at) / 1000)))}`;
  return `band in force ${ONE_DP.format(low)}–${formatValue(high, "°F")} · DERIVED${age}`;
}

function at(value: string | null | undefined): ReactNode {
  return validTime(value) ? <> at <LocalTime at={value} /></> : null;
}
function readingItem(term: string, reading: Reading, unit: string): KvItem {
  return { key: term, term, value: finite(reading.value) ? <>{formatValue(reading.value, unit)}{at(reading.observed_at)}</> : null, reason: reading.reason || "No reading" };
}
function sourceItem(term: string, source: Freshness): KvItem {
  const value = validTime(source.observed_at) && finite(source.age_s)
    ? <><LocalTime at={source.observed_at} /> ({duration(Math.max(0, source.age_s))} old{source.stale ? ", stale" : ""})</>
    : null;
  return { key: term, term, value, reason: "never seen" };
}

/** Every reading with its source and time, the band in force, and the mode as the controller reports it (raw). */
function AllReadings({ now, clock }: { now: Now; clock: number }) {
  const { band, mode, freshness, writer } = now;
  const items: KvItem[] = [
    readingItem("Indoor temperature", now.indoor.temp_f, "°F"),
    readingItem("Indoor VPD", now.indoor.vpd_kpa, "kPa"),
    readingItem("Indoor RH", now.indoor.rh_pct, "%"),
    readingItem("Outdoor temperature (Tempest, through the controller)", now.outdoor.temp_f, "°F"),
    readingItem("Outdoor RH (Tempest, through the controller)", now.outdoor.rh_pct, "%"),
    { key: "band", term: "Band in force (DERIVED from the crop band anchors)", value: <>{bandLine(band, clock)}{band.rh_pct ? "" : " · no RH band"}</> },
    {
      key: "mode", term: "Mode code",
      value: <><span className="mono">{mode.code}</span>{mode.stage && <> · stage <span className="mono">{mode.stage}</span></>}{validTime(mode.since) && <> · since <LocalTime at={mode.since} /></>}</>,
    },
    { key: "mode_reason", term: "Mode reason", value: mode.reason ? <span className="mono">{mode.reason}</span> : null, reason: "none reported" },
    sourceItem("Newest climate row", freshness.climate),
    sourceItem("Ingestor heartbeat", freshness.ingestor),
    sourceItem("Newest diagnostics row", freshness.diagnostics),
    sourceItem("Newest setpoint readback", freshness.readback),
    {
      key: "writer", term: "Settings pushed (writer)",
      value: validTime(writer.last_at) && finite(writer.age_s) ? <><LocalTime at={writer.last_at} /> ({duration(Math.max(0, writer.age_s))} old)</> : null, reason: "no push on record",
    },
  ];
  return <Kv items={items} className="now-readings-list" />;
}

/** The three band bars: each row links to its Graphs panel; a verdict word shows only out of band. */
function BandRows({ rows, muted }: { rows: readonly BandRow[]; muted: boolean }) {
  return <ul className="now-bands" aria-label="Indoor climate against the band in force">
    {rows.map((row) => {
      const off = muted ? null : outOfBand(row.verdict);
      const missing = !finite(row.reading.value);
      return <li key={row.key} className={cls("now-band", off && "now-band-off")} data-tile={row.key}>
        <Link className="now-band-label" to={row.href}>{row.label}<span className="screen-reader">: {row.name} in Graphs</span></Link>
        <span className="now-band-bar"><BandBar label={row.name} unit={row.unit} value={missing ? null : row.reading.value} band={row.band} today={row.today} hero decorative={false} state={muted ? "stale" : "ready"} /></span>
        <span className={cls("now-band-value", muted && "value-muted")}>{readingText(row.reading, row.unit)}</span>
        <span className="now-band-meta">
          {missing ? <span className="now-band-why">{row.reading.reason || "No reading"}</span>
            : <>{off && <span className="now-off">{words("band_verdict", off)}</span>}<span className="now-band-range">{off && " · "}{bandWords(row)}</span></>}
        </span>
      </li>;
    })}
  </ul>;
}

/** "In band today: temperature 96% · VPD 88% (yesterday 94% · 91%)", only with data. */
function InBandToday({ tib, muted }: { tib: TimeInBand | null; muted: boolean }) {
  if (!tib || !hasTimeInBand(tib)) return <p className="now-tib" aria-hidden="true" />;
  const yesterday = tib.temp.yesterday !== null || tib.vpd.yesterday !== null;
  return <p className={cls("now-tib", muted && "value-muted")} data-tib="">
    <span className="now-tib-label">In band today</span>{" "}
    <span className="now-tib-values">temperature <strong>{percentText(tib.temp.today)}</strong> · VPD <strong>{percentText(tib.vpd.today)}</strong></span>
    {yesterday && <> <span className="now-tib-before">(yesterday {percentText(tib.temp.yesterday)} · {percentText(tib.vpd.yesterday)})</span></>}
  </p>;
}

/** The status line: the mode, Iris and its plan, outdoor and occupancy, each a quiet phrase; the links go where each is explained. */
function StatusLine({ now, answer, irrigation, clock }: { now: Now | null; answer: NowAnswer; irrigation: PanelRead<IrrigationStatus>; clock: number }) {
  const unreachable = answer.row === 1;
  return <ul className="now-status" aria-label="Status">
    {now && !unreachable && <>
      <li><Link className={cls("now-status-item", answer.muted && "value-muted")} to="/equipment#why" data-tile="mode">{modeText(now, answer)}</Link></li>
      <li><Link className="now-status-item" to="/plan" data-tile="iris" data-tone={irisWarns(now.iris) ? "warn" : undefined}>
        {irisText(now.iris)}<span className="now-sr-phone">, {planLine(now.iris)}</span>
      </Link></li>
      <li><Link className={cls("now-status-item", answer.muted && "value-muted")} to="/graphs?range=24h#temperature" data-tile="outdoor">
        <span className="now-sr-phone">Outdoor </span>{deviceUnknown(answer) ? "—" : readingText(now.outdoor.temp_f, "°F")}
      </Link></li>
    </>}
    <li><span className="now-status-item now-status-static" data-chip="occupancy">{occupancyText(irrigation, clock)}</span></li>
  </ul>;
}

/** The hero before API-02 first answers: the answer claims nothing, and every part keeps its height. */
function HeroLoading() {
  const headingId = useId();
  return <section className="hero now-hero hero-loading" aria-labelledby={headingId} aria-busy="true">
    <div className="now-hero-main">
      <span className="eyebrow">{NOW_EYEBROW}</span>
      <h1 id={headingId} className="hero-answer">{LOADING_TEXT}</h1>
      <div className="skeleton now-skeleton-value" aria-hidden="true" />
    </div>
    <div className="now-hero-climate"><div className="skeleton now-skeleton-bands" aria-hidden="true" /></div>
    <div className="skeleton now-skeleton-status" aria-hidden="true" />
  </section>;
}

/** Opens "All readings" and brings it into view when the page's hash asks for it (`#data`, `#readings`). */
function useReadingsHash(ref: { readonly current: HTMLDetailsElement | null }, drawn: boolean) {
  const { hash } = useLocation();
  useEffect(() => {
    const details = ref.current;
    if (!drawn || !details || !READINGS_HASHES.has(hash)) return;
    details.open = true;
    details.scrollIntoView?.({ block: "nearest" });
  }, [hash, drawn, ref]);
}

export type NowHeroProps = {
  /** The newest API-02 answer (the shell's poll), kept through failures; null before the first. */
  readonly now: Now | null;
  readonly answer: NowAnswer;
  readonly irrigation: PanelRead<IrrigationStatus>;
  /** The day read (API-04, the last 25 h): today's low and high. */
  readonly day: PanelRead<Series>;
  /** The daily read (API-04's time-in-band keys over yesterday and today). */
  readonly daily: PanelRead<Series>;
  /** Reads API-02 again (row 1's Retry). */
  readonly onRetry: () => void;
};

export function NowHero({ now, answer, irrigation, day, daily, onRetry }: NowHeroProps) {
  const headingId = useId();
  const clock = useNow();
  const apiDown = useApiDown();
  const readings = useRef<HTMLDetailsElement>(null);
  useReadingsHash(readings, answer.kind !== "loading" && now !== null);
  if (answer.kind === "loading") return <HeroLoading />;
  const temp = now?.indoor.temp_f ?? null;
  const missing = !temp || !finite(temp.value);
  const muted = answer.muted;
  const inBand = !muted && !apiDown && !missing && now?.band.temp_f.verdict === "in";
  const since = todayStartS(clock);
  const rows = now && !apiDown ? bandRows(now, day.failed ? null : day.data, since) : null;
  const tib = now && !apiDown && daily.data && !daily.failed ? timeInBand(daily.data, localDate(new Date(clock).toISOString())) : null;
  const openReadings = (event: MouseEvent<HTMLAnchorElement>) => {
    const details = readings.current;
    if (!details) return;
    details.open = true;
    if (window.location.hash === "#readings") { event.preventDefault(); details.scrollIntoView?.({ block: "nearest" }); }
  };
  return <section className={cls("hero now-hero", !rows && "now-hero-plain")} aria-labelledby={headingId} data-answer={answer.kind}>
    <div className="now-hero-main">
      <span className="eyebrow">{NOW_EYEBROW}</span>
      <h1 id={headingId} className="hero-answer"><AnswerTitle answer={answer} /></h1>
      <p className="now-hero-value">
        <span className={cls("hero-temp", muted && "value-muted")} data-hero-value="">{formatValue(missing ? null : temp.value, "°F")}</span>
        {inBand && <span className="now-hero-verdict">{words("band_verdict", "in")}</span>}
        {muted && now && <Badge kind="stale" />}
      </p>
      {missing && <p className="hero-meta">{temp ? temp.reason || "No indoor temperature" : NO_ANSWER}</p>}
      <p className="hero-meta now-fresh">
        <FreshnessDot stale={muted} notReporting={answer.notReporting} />
        <Link id="data" className="now-fresh-link" to={`#${READINGS_ID}`} data-tile="data" onClick={openReadings}>
          <AgeLabel observedAt={temp?.observed_at ?? null} stale={muted || answer.notReporting} />
        </Link>
        {answer.row === 1 && <Button onClick={onRetry}>Retry</Button>}
      </p>
    </div>
    {rows && <div className="now-hero-climate">
      <BandRows rows={rows} muted={muted} />
      <InBandToday tib={tib} muted={muted} />
    </div>}
    <StatusLine now={now} answer={answer} irrigation={irrigation} clock={clock} />
    {now && <details ref={readings} className="now-readings" id={READINGS_ID}>
      <summary>All readings<span className="now-readings-hint"> · sources, times and the mode code{finite(now.writer.age_s) && `, settings pushed ${duration(Math.max(0, now.writer.age_s))} ago`}</span></summary>
      <AllReadings now={now} clock={clock} />
    </details>}
  </section>;
}
