// SPDX-License-Identifier: MIT
/**
 * Now's ink hero (screens SCR-01), on #1123's `Hero`: the one-sentence answer as the page's heading (`now-answer.ts`),
 * indoor temperature as the hero value with VPD and RH beside it, each with its verdict against the band in force, the
 * band sub-line ("band in force 62.0–70.0 °F · DERIVED · 4 min"), "Updated N s ago", the eight tiles and the "All
 * readings" disclosure with the raw mode code, stage and reason and every source's time. Rows 1 to 3 and 7 of the
 * answer hide every verdict and mute the values with STALE; a null reading is "—" with its reason.
 */
import { useId, type ReactNode } from "react";
import { Link } from "react-router";
import { duration, formatValue } from "../../lib/format";
import { LOADING_TEXT, type NowAnswer } from "../../lib/now-answer";
import type { Freshness, IrrigationStatus, Now, Reading } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { useApiDown } from "../../lib/session";
import { words } from "../../lib/words";
import { Button } from "../button";
import { LocalTime } from "../common";
import { useNow } from "../freshness";
import { Hero } from "../hero";
import { Kv, type KvItem } from "../kv";
import { bandText, nowTiles } from "./tiles";

/** The time zone is stated once, here (design system §5.4). */
export const NOW_EYEBROW = "Greenhouse · Longmont · America/Denver";
const NO_ANSWER = "No answer from the greenhouse data";
const ONE_DP = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));

/**
 * The answer, linked to the page that explains it. Row 2's `#data` is the Data tile on this page: a router link, so the
 * router sees the hash (it scrolls there, and Now opens "All readings" for it on a phone).
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

/** A reading and its verdict, for the line beside the hero value: "VPD 0.62 kPa · in band". */
function besideText(label: string, reading: Reading, unit: string, verdict: string | null): string {
  if (!finite(reading.value)) return `${label} — (${reading.reason || "No reading"})`;
  return `${label} ${formatValue(reading.value, unit)}${verdict ? ` · ${verdict}` : ""}`;
}
function Beside({ now, verdicts }: { now: Now; verdicts: boolean }) {
  const rhBand = now.band.rh_pct;
  const rhNote = rhBand ? (verdicts ? words("band_verdict", rhBand.verdict) : null) : "no RH band";
  return <>
    <span>{besideText("VPD", now.indoor.vpd_kpa, "kPa", verdicts ? words("band_verdict", now.band.vpd_kpa.verdict) : null)}</span>
    <span>{besideText("RH", now.indoor.rh_pct, "%", rhNote)}</span>
  </>;
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

/** Every reading with its source and time, and the mode as the controller reports it (code, stage and reason, raw). */
function AllReadings({ now }: { now: Now }) {
  const { band, mode, freshness, writer } = now;
  const items: KvItem[] = [
    readingItem("Indoor temperature", now.indoor.temp_f, "°F"),
    readingItem("Indoor VPD", now.indoor.vpd_kpa, "kPa"),
    readingItem("Indoor RH", now.indoor.rh_pct, "%"),
    readingItem("Outdoor temperature (Tempest, through the controller)", now.outdoor.temp_f, "°F"),
    readingItem("Outdoor RH (Tempest, through the controller)", now.outdoor.rh_pct, "%"),
    {
      key: "band", term: "Band in force (DERIVED from the crop band anchors)",
      value: <>{bandText(band.temp_f, "°F")} · VPD {bandText(band.vpd_kpa, "kPa")} · {band.rh_pct ? `RH ${bandText(band.rh_pct, "%")}` : "no RH band"}{at(band.observed_at)}</>,
    },
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
      key: "writer", term: "Setpoints pipeline (writer)",
      value: validTime(writer.last_at) && finite(writer.age_s) ? <><LocalTime at={writer.last_at} /> ({duration(Math.max(0, writer.age_s))} old)</> : null, reason: "no push on record",
    },
  ];
  return <Kv items={items} className="now-readings" />;
}

/** The hero before API-02 first answers: the answer claims nothing, and the value and tiles keep their height. */
function HeroLoading() {
  const headingId = useId();
  return <section className="hero hero-loading" aria-labelledby={headingId} aria-busy="true">
    <div>
      <span className="eyebrow">{NOW_EYEBROW}</span>
      <h1 id={headingId} className="hero-answer">{LOADING_TEXT}</h1>
      <div className="skeleton skeleton-hero-value" aria-hidden="true" />
    </div>
    <div className="skeleton skeleton-hero-tiles" aria-hidden="true" />
  </section>;
}

export type NowHeroProps = {
  /** The newest API-02 answer (the shell's poll), kept through failures; null before the first. */
  readonly now: Now | null;
  readonly answer: NowAnswer;
  readonly irrigation: PanelRead<IrrigationStatus>;
  /** Reads API-02 again (row 1's Retry). */
  readonly onRetry: () => void;
};

export function NowHero({ now, answer, irrigation, onRetry }: NowHeroProps) {
  const clock = useNow();
  const apiDown = useApiDown();
  if (answer.kind === "loading") return <HeroLoading />;
  const temp = now?.indoor.temp_f ?? null;
  const verdicts = !answer.muted && !apiDown;
  const band = now ? bandLine(now.band, clock) : null;
  const sub = answer.row === 1
    ? <>{band && <span>{band}</span>}<Button onClick={onRetry}>Retry</Button></>
    : band;
  return <Hero
    eyebrow={NOW_EYEBROW}
    title={<AnswerTitle answer={answer} />}
    value={temp?.value ?? null}
    unit="°F"
    reason={temp ? temp.reason || "No indoor temperature" : NO_ANSWER}
    verdict={now ? words("band_verdict", now.band.temp_f.verdict) : undefined}
    beside={now ? <Beside now={now} verdicts={verdicts} /> : undefined}
    sub={sub}
    observedAt={temp?.observed_at ?? null}
    stale={answer.muted}
    notReporting={answer.notReporting}
    tiles={nowTiles({ now, answer, irrigation, clock })}
    readings={now ? <AllReadings now={now} /> : undefined}
  />;
}
