// SPDX-License-Identifier: MIT
/**
 * Now's answer to "is everything OK?" (screens SCR-01, US-NOW-08): the first true row of the worst-first table, in the
 * banners' order. Pure: it reads the newest API-02 answer and whether the newest API-02 read failed, and states every
 * age at the answer's own `server_time`. The sentence uses no codes and reads the same for operators and viewers. It
 * never reads "All OK" while a row above it holds or the read it needs has failed, and before API-02 first answers it
 * claims nothing.
 *
 * 1. The newest API-02 read failed: "Can't reach the greenhouse data" (the shell's banner waits for a second failure).
 * 2. The ingestor heartbeat is stale (older than 180 s): "Ingestor not running since HH:MM".
 * 3. The heartbeat is fresh and the newest climate row is older than 300 s: "Controller not reporting since HH:MM".
 * 4. The leak alert is open and flow has not stopped: "Water flowing with every Verdify valve closed".
 * 5. A fresh reading is outside its band: the steady headline "Out of band", and `conditions` lists every reading that is
 *    out, temperature, then VPD, then RH, each as a plain phrase ("Too hot: 84.1 °F (band 62–70 °F)") the hero shows on
 *    its own line under the headline (Jason 2026-11-05). With a dwell state (`stepBandHold`), a reading counts as out
 *    only after it has been out for `BAND_DWELL_S`, and as back in only after the same, so the headline and the line do
 *    not flicker at a band edge. The band bars keep the raw verdict.
 * 6. Alerts nobody has acknowledged, the one count: "1 thing needs someone".
 * 7. The climate data is stale: "Waiting for fresh data" (the age line under the hero value says how old it is).
 * 8. "All OK".
 *
 * Rows 2 and 3 use the shell's own tests (`heartbeatFresh`, `climateFresh`), so the sentence and the fault banner never
 * disagree. Row 7 holds while API-02 marks the climate source stale, and also while an indoor reading is older than
 * twice the climate cadence at `server_time` or a reading with a band in force has no value: a reading the hero shows
 * can never be older than "fresh" allows under an "All OK".
 *
 * The headline is never a link: `link` says where a row is explained, and the hero shows it as a quiet affordance.
 */
import { formatValue, shortTime } from "./format";
import type { Now, Reading } from "./operator-api";
import { climateFresh, heartbeatFresh } from "./shell";

export type AnswerKind = "unreachable" | "ingestor" | "controller" | "flow" | "band" | "alerts" | "stale" | "ok";
/** Where the sentence links: the page that explains it. `to` starting with "#" is an anchor on Now itself. */
export type AnswerLink = { readonly to: string; readonly label: string };
export type BandMetric = "temp" | "vpd" | "rh";
export type BandVerdict = "in" | "above" | "below";
/** Row 5: one reading out of its band, as a plain phrase with its Graphs panel. */
export type BandCondition = { readonly metric: BandMetric; readonly verdict: "above" | "below"; readonly text: string; readonly link: AnswerLink };
export type NowAnswer = {
  /** The row of the table that holds, 1 to 8; 0 while API-02 has not answered and nothing is known. */
  readonly row: number;
  readonly kind: AnswerKind | "loading";
  readonly text: string;
  readonly link: AnswerLink | null;
  /** Row 5: every reading out of its band, in the order temperature, VPD, RH; empty on every other row. */
  readonly conditions: readonly BandCondition[];
  /** Rows 1 to 3 and 7 (and loading): no verdict anywhere, and the values muted with STALE. */
  readonly muted: boolean;
  /** Rows 1 to 3: the source is not reporting or out of reach; the freshness dot reads off. */
  readonly notReporting: boolean;
  /**
   * Rows 1 to 3: the equipment state is unknown since this instant: the time in the sentence (the heartbeat's or the
   * newest climate row's), or for row 1 the last good answer's `server_time`. Null when there is none.
   */
  readonly unknownSince: string | null;
  /**
   * Row 7: when the data the page still shows was read (the oldest stale reading's time, else the newest climate row's),
   * so what it says can be dated ("Last mode: holding (10:28)"). Null on every other row, or with no time.
   */
  readonly lastDataAt: string | null;
};
export type AnswerInput = {
  /** The newest API-02 answer, kept through failures; null before the first. */
  readonly now: Now | null;
  /** The newest API-02 read failed. */
  readonly failed: boolean;
  /** The dwell state over the polls so far (`stepBandHold`); without it row 5 reads the API's verdicts as they are. */
  readonly hold?: BandHold | null;
};

export const ALL_OK = "All OK";
export const UNREACHABLE_TEXT = "Can't reach the greenhouse data";
export const LOADING_TEXT = "Checking the greenhouse…";
/** Row 7. The age is the hero's own line ("Last data 14 min ago (10:28)"), so the heading never repeats it. */
export const STALE_TEXT = "Waiting for fresh data";
/** Row 5's steady headline; what is out, and by how much, is the condition line under it. */
export const OUT_OF_BAND_TEXT = "Out of band";
/**
 * A reading counts as out of band after 2 min out, and as back in after 2 min in (Jason 2026-11-05, #1338). Readings
 * arrive once a minute, so a value hovering at a band edge does not flip the headline on every 15 s poll.
 */
export const BAND_DWELL_S = 120;
/** A climate source is stale past twice its cadence (the `v_sensor_staleness` rule); the climate cadence is 60 s. */
const DEFAULT_CADENCE_S = 60;

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
const since = (value: string | null) => (value ? ` since ${shortTime(value)}` : "");
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const hasValue = (reading: Reading) => finite(reading.value);

const answer = (row: number, kind: AnswerKind, text: string, fields: Partial<Pick<NowAnswer, "link" | "conditions" | "muted" | "notReporting" | "unknownSince" | "lastDataAt">> = {}): NowAnswer => ({
  row, kind, text, link: null, conditions: [], muted: false, notReporting: false, unknownSince: null, lastDataAt: null, ...fields,
});
export const LOADING: NowAnswer = { row: 0, kind: "loading", text: LOADING_TEXT, link: null, conditions: [], muted: true, notReporting: false, unknownSince: null, lastDataAt: null };

/** Seconds from a reading's time to the answer's `server_time`; null when either is missing. */
function ageAtServer(observedAt: string | null | undefined, now: Pick<Now, "server_time">): number | null {
  if (!validTime(observedAt) || !validTime(now.server_time)) return null;
  return Math.max(0, (Date.parse(now.server_time) - Date.parse(observedAt)) / 1000);
}
const staleAfterS = (now: Pick<Now, "freshness">) => {
  const cadence = now.freshness.climate.cadence_s;
  return 2 * (finite(cadence) && cadence > 0 ? cadence : DEFAULT_CADENCE_S);
};
/** A reading with a value no older than twice the climate cadence at `server_time`. */
export function readingFresh(reading: Reading, now: Pick<Now, "server_time" | "freshness">): boolean {
  const age = ageAtServer(reading.observed_at, now);
  return hasValue(reading) && age !== null && age <= staleAfterS(now);
}

// Row 5's bounds read as the band is set: whole degrees and percent drop ".0" ("62–70 °F"); kPa keeps 2 dp.
const WHOLE_OR_ONE = new Intl.NumberFormat("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 1 });
const TWO = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const NBSP = "\u00a0";
const bound = (value: number, unit: string) => (unit === "kPa" ? TWO : WHOLE_OR_ONE).format(value).replace("-", "−");
/** "62–70 °F", "0.45–0.80 kPa", "50–80%"; null without both bounds. */
export function targetText(low: number | null | undefined, high: number | null | undefined, unit: string): string | null {
  if (!finite(low) || !finite(high)) return null;
  const range = `${bound(low, unit)}–${bound(high, unit)}`;
  return unit === "%" ? `${range}%` : `${range}${NBSP}${unit}`;
}

type Metric = { readonly metric: BandMetric; readonly reading: Reading; readonly position: Now["band"]["temp_f"] | null; readonly unit: string; readonly above: string; readonly below: string; readonly anchor: string };
const metrics = (now: Now): Metric[] => [
  { metric: "temp", reading: now.indoor.temp_f, position: now.band.temp_f, unit: "°F", above: "Too hot", below: "Too cold", anchor: "temperature" },
  { metric: "vpd", reading: now.indoor.vpd_kpa, position: now.band.vpd_kpa, unit: "kPa", above: "Air too dry", below: "Air too damp", anchor: "vpd" },
  { metric: "rh", reading: now.indoor.rh_pct, position: now.band.rh_pct, unit: "%", above: "Air too damp", below: "Air too dry", anchor: "rh" },
];
const BAND_METRICS: readonly BandMetric[] = ["temp", "vpd", "rh"];

/** The API's verdict for each reading: "above" or "below" only for a fresh reading with a band; else "in". */
export function rawVerdicts(now: Now): Readonly<Record<BandMetric, BandVerdict>> {
  const out: Record<BandMetric, BandVerdict> = { temp: "in", vpd: "in", rh: "in" };
  for (const { metric, reading, position } of metrics(now)) {
    const verdict = position?.verdict;
    if (readingFresh(reading, now) && (verdict === "above" || verdict === "below")) out[metric] = verdict === "above" ? "above" : "below";
  }
  return out;
}

/**
 * The dwell state behind row 5: per reading, the verdict that holds and a different one waiting since `since` (epoch
 * seconds at the answer's `server_time`) to replace it once it has lasted `BAND_DWELL_S`.
 */
export type BandHold = {
  readonly at: number;
  readonly metrics: Readonly<Record<BandMetric, { readonly held: BandVerdict; readonly next: BandVerdict | null; readonly since: number }>>;
};

/**
 * One poll's step of the dwell state. Pure and idempotent: the same answer (or an older one) leaves it as it is. The
 * first answer seen holds as it reads, with no history to wait on.
 */
export function stepBandHold(prev: BandHold | null | undefined, now: Now | null | undefined, dwellS = BAND_DWELL_S): BandHold | null {
  if (!now || !validTime(now.server_time)) return prev ?? null;
  const at = Date.parse(now.server_time) / 1000;
  if (prev && at <= prev.at) return prev;
  const raw = rawVerdicts(now);
  const next = {} as Record<BandMetric, BandHold["metrics"][BandMetric]>;
  for (const metric of BAND_METRICS) {
    const was = prev?.metrics[metric];
    const verdict = raw[metric];
    if (!was || verdict === was.held) next[metric] = { held: verdict, next: null, since: at };
    else if (was.next === verdict) next[metric] = at - was.since >= dwellS ? { held: verdict, next: null, since: at } : was;
    else next[metric] = { held: was.held, next: verdict, since: at };
  }
  return { at, metrics: next };
}

/**
 * Row 5: every fresh reading out of its band, in the order temperature, VPD, RH: the API's verdict, or with a dwell state
 * the held one. A reading that is not fresh is never listed: row 7 says so instead.
 */
export function bandConditions(now: Now, hold?: BandHold | null): BandCondition[] {
  const raw = rawVerdicts(now);
  const out: BandCondition[] = [];
  for (const { metric, reading, position, unit, above, below, anchor } of metrics(now)) {
    if (!position || !readingFresh(reading, now) || !finite(reading.value)) continue;
    const verdict = hold ? hold.metrics[metric].held : raw[metric];
    if (verdict === "in") continue;
    const target = targetText(position.low, position.high, unit);
    const text = `${verdict === "above" ? above : below}: ${formatValue(reading.value, unit)}${target ? ` (band ${target})` : ""}`;
    out.push({ metric, verdict, text, link: { to: `/graphs?range=24h#${anchor}`, label: "Graphs" } });
  }
  return out;
}

/**
 * Row 7: the climate source is stale, an indoor reading is older than twice its cadence, or a reading with a band in
 * force has no value. `at` is the oldest stale reading's time (else the climate row's): when the data still shown was read.
 */
function staleness(now: Now): { at: string | null } | null {
  const readings = [now.indoor.temp_f, now.indoor.vpd_kpa, now.indoor.rh_pct];
  const old = readings.filter((reading) => hasValue(reading) && !readingFresh(reading, now));
  const missing = metrics(now).some(({ reading, position }) => position !== null && !hasValue(reading));
  if (!now.freshness.climate.stale && !old.length && !missing) return null;
  const times = old.map((reading) => reading.observed_at).filter(validTime).sort((a, b) => Date.parse(a) - Date.parse(b));
  const climate = now.freshness.climate.stale && validTime(now.freshness.climate.observed_at) ? now.freshness.climate.observed_at : null;
  return { at: times[0] ?? climate };
}

/** The first row of the worst-first table that holds. */
export function nowAnswer({ now, failed, hold }: AnswerInput): NowAnswer {
  if (failed) {
    return answer(1, "unreachable", UNREACHABLE_TEXT, { muted: true, notReporting: true, unknownSince: validTime(now?.server_time) ? now.server_time : null });
  }
  if (!now) return LOADING;
  const { ingestor, climate } = now.freshness;
  if (!heartbeatFresh(ingestor)) {
    const at = validTime(ingestor.observed_at) ? ingestor.observed_at : null;
    return answer(2, "ingestor", `Ingestor not running${since(at)}`, { link: { to: "#data", label: "Data" }, muted: true, notReporting: true, unknownSince: at });
  }
  if (!climateFresh(climate)) {
    const at = validTime(climate.observed_at) ? climate.observed_at : null;
    return answer(3, "controller", `Controller not reporting${since(at)}`, { link: { to: "/equipment", label: "Equipment" }, muted: true, notReporting: true, unknownSince: at });
  }
  const { leak } = now;
  // Flow continues until a valid `stopped_at`, as the flow banner and the water card read it.
  if (finite(leak.alert_id) && !validTime(leak.stopped_at)) {
    return answer(4, "flow", "Water flowing with every Verdify valve closed", { link: { to: `/activity?alert=${leak.alert_id}`, label: "Open the alert" } });
  }
  const conditions = bandConditions(now, hold);
  if (conditions.length) return answer(5, "band", OUT_OF_BAND_TEXT, { conditions });
  const open = now.alerts.open;
  if (finite(open) && open > 0) {
    return answer(6, "alerts", open === 1 ? "1 thing needs someone" : `${open} things need someone`, { link: { to: "/activity", label: "Activity" } });
  }
  const stale = staleness(now);
  if (stale) return answer(7, "stale", STALE_TEXT, { muted: true, lastDataAt: stale.at });
  return answer(8, "ok", ALL_OK);
}
