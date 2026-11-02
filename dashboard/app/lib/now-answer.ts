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
 * 5. A fresh reading is outside its band, temperature, then VPD, then RH: "Too hot: 84.1 °F (target 62–70 °F)".
 * 6. Alerts nobody has acknowledged, the one count: "1 thing needs someone".
 * 7. The climate data is stale: "Waiting for fresh data (last 14 min ago)".
 * 8. "All OK".
 *
 * Rows 2 and 3 use the shell's own tests (`heartbeatFresh`, `climateFresh`), so the sentence and the fault banner never
 * disagree. Row 7 holds while API-02 marks the climate source stale, and also while an indoor reading is older than
 * twice the climate cadence at `server_time` or a reading with a band in force has no value: a reading the hero shows
 * can never be older than "fresh" allows under an "All OK".
 */
import { duration, formatValue, shortTime } from "./format";
import type { Now, Reading } from "./operator-api";
import { climateFresh, heartbeatFresh } from "./shell";

export type AnswerKind = "unreachable" | "ingestor" | "controller" | "flow" | "band" | "alerts" | "stale" | "ok";
/** Where the sentence links: the page that explains it. `to` starting with "#" is an anchor on Now itself. */
export type AnswerLink = { readonly to: string; readonly label: string };
export type NowAnswer = {
  /** The row of the table that holds, 1 to 8; 0 while API-02 has not answered and nothing is known. */
  readonly row: number;
  readonly kind: AnswerKind | "loading";
  readonly text: string;
  readonly link: AnswerLink | null;
  /** Rows 1 to 3 and 7 (and loading): no verdict anywhere, and the values muted with STALE. */
  readonly muted: boolean;
  /** Rows 1 to 3: the source is not reporting or out of reach; the freshness dot reads off. */
  readonly notReporting: boolean;
  /**
   * Rows 1 to 3: the equipment state is unknown since this instant: the time in the sentence (the heartbeat's or the
   * newest climate row's), or for row 1 the last good answer's `server_time`. Null when there is none.
   */
  readonly unknownSince: string | null;
};
export type AnswerInput = {
  /** The newest API-02 answer, kept through failures; null before the first. */
  readonly now: Now | null;
  /** The newest API-02 read failed. */
  readonly failed: boolean;
};

export const ALL_OK = "All OK";
export const UNREACHABLE_TEXT = "Can't reach the greenhouse data";
export const LOADING_TEXT = "Checking the greenhouse…";
/** A climate source is stale past twice its cadence (the `v_sensor_staleness` rule); the climate cadence is 60 s. */
const DEFAULT_CADENCE_S = 60;

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
const since = (value: string | null) => (value ? ` since ${shortTime(value)}` : "");
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const hasValue = (reading: Reading) => finite(reading.value);

const answer = (row: number, kind: AnswerKind, text: string, fields: Partial<Pick<NowAnswer, "link" | "muted" | "notReporting" | "unknownSince">> = {}): NowAnswer => ({
  row, kind, text, link: null, muted: false, notReporting: false, unknownSince: null, ...fields,
});
export const LOADING: NowAnswer = { row: 0, kind: "loading", text: LOADING_TEXT, link: null, muted: true, notReporting: false, unknownSince: null };

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

type Metric = { readonly reading: Reading; readonly position: Now["band"]["temp_f"] | null; readonly unit: string; readonly above: string; readonly below: string };
const metrics = (now: Now): Metric[] => [
  { reading: now.indoor.temp_f, position: now.band.temp_f, unit: "°F", above: "Too hot", below: "Too cold" },
  { reading: now.indoor.vpd_kpa, position: now.band.vpd_kpa, unit: "kPa", above: "Air too dry", below: "Air too damp" },
  { reading: now.indoor.rh_pct, position: now.band.rh_pct, unit: "%", above: "Air too damp", below: "Air too dry" },
];

/** Row 5: the first fresh reading the API judges above or below its band, in the order temperature, VPD, RH. */
function outOfBand(now: Now): string | null {
  for (const { reading, position, unit, above, below } of metrics(now)) {
    if (!position || !readingFresh(reading, now) || !finite(reading.value)) continue;
    const head = position.verdict === "above" ? above : position.verdict === "below" ? below : null;
    if (!head) continue;
    const target = targetText(position.low, position.high, unit);
    return `${head}: ${formatValue(reading.value, unit)}${target ? ` (target ${target})` : ""}`;
  }
  return null;
}

/**
 * Row 7: the climate source is stale, an indoor reading is older than twice its cadence, or a reading with a band in
 * force has no value. `at` is the oldest stale reading's time (else the climate row's), for "last N min ago".
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
export function nowAnswer({ now, failed }: AnswerInput): NowAnswer {
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
  if (finite(leak.alert_id) && leak.stopped_at === null) {
    return answer(4, "flow", "Water flowing with every Verdify valve closed", { link: { to: `/activity?alert=${leak.alert_id}`, label: "Open the alert" } });
  }
  const band = outOfBand(now);
  if (band) return answer(5, "band", band, { link: { to: "/graphs", label: "Graphs" } });
  const open = now.alerts.open;
  if (finite(open) && open > 0) {
    return answer(6, "alerts", open === 1 ? "1 thing needs someone" : `${open} things need someone`, { link: { to: "/activity", label: "Activity" } });
  }
  const stale = staleness(now);
  if (stale) {
    const age = ageAtServer(stale.at, now);
    return answer(7, "stale", `Waiting for fresh data${age !== null ? ` (last ${duration(age)} ago)` : ""}`, { muted: true });
  }
  return answer(8, "ok", ALL_OK);
}
