// SPDX-License-Identifier: MIT
/**
 * Now's eight hero tiles (screens SCR-01): VPD, RH, Outdoor and Mode, each linking to Graphs; Iris, linking to Plan;
 * Water, from API-IRR-01, linking to Water; Alerts, the one open-alert count by severity, linking to Activity; and
 * Data (`id="data"`, the ingestor banner's target), linking to Equipment. At 860 px and below Mode, Iris, Water and
 * Alerts stay in the band and the other four move under "All readings" (#1123's `Hero`).
 *
 * The device tiles (VPD, RH, Outdoor, Mode) follow the hero: muted with no verdict while the answer mutes it. Iris,
 * Water, Alerts and Data come from the platform rather than a sensor, so they stay readable while the controller is
 * quiet, and mute only while API-02 itself is out of reach.
 */
import type { ReactNode } from "react";
import { Link } from "react-router";
import { duration, formatValue, shortTime } from "../../lib/format";
import { dayClock } from "../../lib/last-water";
import { lineById } from "../../lib/lines";
import type { NowAnswer } from "../../lib/now-answer";
import { targetText } from "../../lib/now-answer";
import type { IrrigationLine, IrrigationStatus, Now } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { modeWords, words } from "../../lib/words";
import { Badge } from "../badge";
import type { HeroTile } from "../hero";

/** A tile's label as a link that covers the whole tile (`.stat-link`), named for where it goes. */
export function TileLink({ to, page, children }: { to: string; page: string; children: ReactNode }) {
  return <Link className="stat-link" to={to}>{children}<span className="screen-reader">: open {page}</span></Link>;
}

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
const ageText = (seconds: number | null | undefined) => (typeof seconds === "number" && Number.isFinite(seconds) ? duration(Math.max(0, seconds)) : "—");

/** "band 0.45–0.80 kPa" for a tile's sub-line; "band unavailable" without both bounds. */
export function bandText(position: Pick<Now["band"]["temp_f"], "low" | "high">, unit: string): string {
  const target = targetText(position.low, position.high, unit);
  return target ? `band ${target}` : "band unavailable";
}

/** The plan in force and its end ("SUNRISE plan to 17:30"), or "No plan: band and defaults". */
export function planLine(iris: Now["iris"]): string {
  if (iris.plan_id === null) return "No plan: band and defaults";
  const head = iris.trigger ? `${iris.trigger} plan` : "Plan in force";
  return validTime(iris.valid_until) ? `${head} to ${shortTime(iris.valid_until)}` : head;
}

/** API-02 `alerts` by severity: "1 warning", "1 critical · 2 warnings"; "None open" at zero. */
export function alertCountText(alerts: Now["alerts"]): string {
  if (!(alerts.open > 0)) return "None open";
  const parts = ([[alerts.critical, "critical", "critical"], [alerts.warning, "warning", "warnings"], [alerts.info, "info", "info"]] as const)
    .filter(([count]) => count > 0)
    .map(([count, one, many]) => `${count} ${count === 1 ? one : many}`);
  return parts.length ? parts.join(" · ") : `${alerts.open} open`;
}

/** The climate row's age ("Fresh · 8 s", "STALE · 14 min") and, under it, the heartbeat's and the writer's. */
export function dataText(freshness: Now["freshness"], writer: Now["writer"]): { text: string; sub: string } {
  const { climate, ingestor } = freshness;
  const text = climate.age_s === null ? "No data" : `${climate.stale ? "STALE" : "Fresh"} · ${ageText(climate.age_s)}`;
  const heartbeat = `heartbeat ${ingestor.stale ? "STALE · " : ""}${ageText(ingestor.age_s)}`;
  return { text, sub: `${heartbeat} · writer ${ageText(writer.age_s)}` };
}

/** A line's plain name: the words table's for its relay ("Center mister"), else the API's. */
export function lineName(line: Pick<IrrigationLine, "line_id" | "name">): string {
  const equipment = lineById(line.line_id)?.equipment;
  return equipment ? words("equipment", equipment) : line.name;
}

/** The soonest next run of an enabled line: "next L01 Wed 07:34"; null when none is scheduled. */
export function nextRunText(status: Pick<IrrigationStatus, "lines">, clock: number): string | null {
  const runs = status.lines.flatMap((line) => (line.enabled && line.next_run && validTime(line.next_run.start_at) ? [line.next_run] : []))
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
  return runs.length ? `next ${runs[0].line_id} ${dayClock(runs[0].start_at, clock)}` : null;
}

/** The Water tile: the running line or "Idle" with the next run; "unavailable", never "Idle", while API-IRR-01 fails. */
export function waterTile(irrigation: PanelRead<IrrigationStatus>, clock: number): Pick<HeroTile, "text" | "value" | "reason" | "sub" | "badge"> {
  if (irrigation.failed) return { text: "unavailable", sub: "water status not loaded" };
  const status = irrigation.data;
  if (!status) return { value: null, reason: "Loading water status" };
  const next = nextRunText(status, clock);
  const running = status.lines.filter((line) => line.state.code === "running");
  if (running.length) {
    const name = lineName(running[0]);
    return { text: running.length === 1 ? name : `${name} +${running.length - 1}`, badge: <Badge kind="running" />, sub: next ?? undefined };
  }
  if (status.freshness.stale || status.lines.some((line) => line.enabled && line.state.code === "unknown")) return { text: "Unknown", sub: "line states not reported" };
  return { text: "Idle", sub: next ?? "no run scheduled" };
}

export type TilesInput = {
  /** The newest API-02 answer; null when API-02 has never answered. */
  readonly now: Now | null;
  readonly answer: NowAnswer;
  readonly irrigation: PanelRead<IrrigationStatus>;
  /** Now on the server's clock (epoch ms). */
  readonly clock: number;
};

/** The eight tiles in the band's order, 4 × 2: VPD, RH, Outdoor, Mode; Iris, Water, Alerts, Data. */
export function nowTiles({ now, answer, irrigation, clock }: TilesInput): HeroTile[] {
  const missing = answer.row === 1 ? "No answer from the greenhouse data" : "No reading";
  // Platform tiles mute only while API-02 is out of reach; device tiles follow the hero.
  const platformStale = answer.row === 1 ? undefined : false;
  const graphs = (label: string) => <TileLink to="/graphs" page="Graphs">{label}</TileLink>;
  const vpd = now?.indoor.vpd_kpa;
  const rh = now?.indoor.rh_pct;
  const rhBand = now?.band.rh_pct ?? null;
  const water = waterTile(irrigation, clock);
  const data = now ? dataText(now.freshness, now.writer) : null;
  return [
    {
      name: "vpd", label: graphs("VPD"), value: vpd?.value ?? null, unit: "kPa", reason: vpd?.reason ?? missing,
      sub: now ? bandText(now.band.vpd_kpa, "kPa") : undefined, verdict: now ? words("band_verdict", now.band.vpd_kpa.verdict) : undefined,
    },
    {
      name: "rh", label: graphs("RH"), value: rh?.value ?? null, unit: "%", reason: rh?.reason ?? missing,
      sub: now ? (rhBand ? bandText(rhBand, "%") : "no RH band") : undefined, verdict: rhBand ? words("band_verdict", rhBand.verdict) : undefined,
    },
    {
      name: "outdoor", label: graphs("Outdoor"), value: now?.outdoor.temp_f.value ?? null, unit: "°F", reason: now?.outdoor.temp_f.reason ?? missing,
      sub: now ? `RH ${formatValue(now.outdoor.rh_pct.value, "%")}` : undefined, observedAt: now ? now.outdoor.temp_f.observed_at : undefined,
    },
    {
      name: "mode", label: graphs("Mode"), phone: true, text: now ? modeWords(now.mode.code, now.mode.stage) : undefined, reason: missing,
      sub: now && validTime(now.mode.since) ? `since ${shortTime(now.mode.since)}` : undefined,
    },
    {
      name: "iris", label: <TileLink to="/plan" page="Plan">Iris</TileLink>, phone: true, stale: platformStale, reason: missing,
      text: now ? words("required_trigger", now.iris.required_trigger) : undefined, sub: now ? planLine(now.iris) : undefined,
      className: now && now.iris.required_trigger !== "ok" ? "stat-warn" : undefined,
    },
    // API-IRR-01 has its own state: it says "unavailable" or "Unknown" itself rather than following API-02's.
    { name: "water", label: <TileLink to="/water" page="Water">Water</TileLink>, phone: true, stale: false, ...water },
    {
      name: "alerts", label: <TileLink to="/activity" page="Activity">Alerts</TileLink>, phone: true, stale: platformStale, reason: missing,
      text: now ? alertCountText(now.alerts) : undefined,
      sub: now && answer.row >= 1 && answer.row <= 3 ? "In-app alerts may be missing" : undefined,
    },
    {
      name: "data", id: "data", label: <TileLink to="/equipment" page="Equipment">Data</TileLink>, stale: platformStale, reason: missing,
      text: data?.text, sub: data?.sub,
    },
  ];
}
