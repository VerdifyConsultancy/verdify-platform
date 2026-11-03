// SPDX-License-Identifier: MIT
/**
 * Now's water card (screens SCR-01, US-NOW-04, US-WAT-13, US-WAT-18, US-WAT-22) from API-IRR-01: minutes today by zone,
 * when each zone last got water (`last-water.ts`), the running line, the next run per enabled line, the flow in words,
 * SAF-4 used of its ceiling and a mister's watchdog back-off or latch, with Clear fault beside a latch (#1136). Before the
 * irrigation firmware it shows relay states only and says where L01 runs from. A failed or not yet served API-IRR-01 reads
 * "Water status unavailable" with Retry, never idle.
 *
 * Stop all water (operators only) renders in every state of the card, outside its error boundaries, so nothing the
 * card fails to draw can take it away. It is one tap, no dialog, never disabled by the writes flag, and goes through
 * #1125's `useStop`, which the route owns. At 860 px and below the card shows its title, the minutes line and Stop all
 * water first.
 */
import { useId } from "react";
import { Link } from "react-router";
import { causeWords } from "../../charts/causes";
import { formatValue, shortTime } from "../../lib/format";
import { WATER_ZONES, WATER_ZONE_WORDS, dayClock, lastWater } from "../../lib/last-water";
import type { IrrigationStatus, Leak } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { OperatorOnly } from "../../lib/session";
import { Button } from "../button";
import { ClearFaultButton } from "../clear-fault-button";
import { useNow } from "../freshness";
import { PanelError, PanelErrorBoundary } from "../panel";
import { lineName, lineStatesUnknown } from "./tiles";

/** What the route passes from `useStop`: Stop all water, and whether it is sending or confirming. */
export type StopControl = { readonly stop: () => void; readonly sending: boolean; readonly confirming: boolean };
export const WATER_UNAVAILABLE = "Water status unavailable.";
export const BEFORE_ENGINE_LINE = "L01 runs from Water; run-now on the other lines arrives with the irrigation firmware";

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);

/**
 * Minutes today by zone, most first, the zone order breaking ties: "Center 14 min · Wall 0 · South 0 · West 0". A
 * zone with no number reads "—".
 */
export function minutesLine(zones: Readonly<Record<string, number | null>>): string {
  const rows = WATER_ZONES.map((zone, order) => ({ zone, order, minutes: Object.hasOwn(zones, zone) ? zones[zone] : null }));
  rows.sort((a, b) => (finite(b.minutes) ? b.minutes : -1) - (finite(a.minutes) ? a.minutes : -1) || a.order - b.order);
  return rows.map(({ zone, minutes }) => {
    const value = !finite(minutes) ? "—" : minutes > 0 ? formatValue(minutes, "min") : "0";
    return `${WATER_ZONE_WORDS[zone]} ${value}`;
  }).join(" · ");
}

/** "Center mister (VPD)" for each running line; "none"; "unknown" while the line states are not reported. */
export function runningLine(status: Pick<IrrigationStatus, "engine" | "lines" | "freshness">): string {
  const running = status.lines.filter((line) => line.state.code === "running");
  if (running.length) return running.map((line) => `${lineName(line)} (${causeWords(line.state.cause ?? "unknown")})`).join(" · ");
  if (lineStatesUnknown(status)) return "unknown, line states not reported";
  return "none";
}

/** The next run per enabled line, soonest first: "L04 today 13:00 · L01 Wed 07:34, 12 min"; null when none. */
export function nextRunsLine(status: Pick<IrrigationStatus, "lines">, clock: number): string | null {
  const runs = status.lines.flatMap((line) => (line.enabled && line.next_run && validTime(line.next_run.start_at) ? [line.next_run] : []))
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
  if (!runs.length) return null;
  return runs.map((run) => `${run.line_id} ${dayClock(run.start_at, clock)}${finite(run.minutes) ? `, ${formatValue(run.minutes, "min")}` : ""}`).join(" · ");
}

/**
 * The flow in words (US-WAT-18): water flowing with every Verdify valve closed while the leak alert is open and flow
 * has not stopped; "Flow stopped at 03:20" once it has, never an all clear while the alert is open; "Flow: unknown"
 * while the flag is unknown; else "Unexplained flow: none".
 */
export function flowText(leak: Leak): { text: string; flowing: boolean } {
  const alertOpen = finite(leak.alert_id);
  if (alertOpen && validTime(leak.stopped_at)) return { text: `Flow stopped at ${shortTime(leak.stopped_at)}`, flowing: false };
  if (alertOpen || leak.active === true) {
    const since = validTime(leak.since) ? ` since ${shortTime(leak.since)}` : "";
    const gallons = finite(leak.gal_last_10min) ? ` · ${leak.gal_last_10min} gal in 10 min` : "";
    return { text: `Water flowing with every Verdify valve closed${since}${gallons}`, flowing: true };
  }
  if (leak.active === null) return { text: "Flow: unknown", flowing: false };
  return { text: "Unexplained flow: none", flowing: false };
}

/** "SAF-4 21 of 360 gal"; an unknown use or ceiling reads "—". */
export function saf4Text(saf4: IrrigationStatus["limits"]["saf4_gal"]): string {
  const number = (value: number | null) => (finite(value) ? formatValue(value, "gal").replace(/\s*gal$/, "") : "—");
  return `SAF-4 ${number(saf4.used)} of ${number(saf4.max)} gal`;
}

/** One line's watchdog back-off or latch in words (D-IRP-19): "South: backing off until 13:20", "South: faulted, no misting until midnight (watchdog)". */
function faultText(line: IrrigationStatus["lines"][number]): string {
  const zone = line.kind === "mister" && Object.hasOwn(WATER_ZONE_WORDS, line.zone) ? WATER_ZONE_WORDS[line.zone as keyof typeof WATER_ZONE_WORDS] : lineName(line);
  if (line.fault === "backing_off") return `${zone}: backing off${validTime(line.fault_until) ? ` until ${shortTime(line.fault_until)}` : ""}`;
  if (line.fault === "mister_fault") return `${zone}: faulted, no misting until midnight (watchdog)`;
  return `${zone}: ${line.fault}`;
}
/** Every line in a back-off or latch, in words. */
export function faultLines(status: Pick<IrrigationStatus, "lines">): string[] {
  return status.lines.filter((line) => line.fault).map(faultText);
}

/** Stop all water for operators, then "Water ›" for everyone: the card's actions row. */
function WaterActions({ stop }: { stop: StopControl }) {
  return <div className="water-stop">
    <OperatorOnly>
      <Button variant="stop" write wide busy={stop.sending} onClick={() => stop.stop()}>Stop all water</Button>
      {stop.confirming && <span className="state-note">Waiting for the valves to read off</span>}
    </OperatorOnly>
    <Link className="card-link-more" to="/water">Water<span className="screen-reader"> board</span><span aria-hidden="true"> ›</span></Link>
  </div>;
}

function WaterDetails({ status, clock }: { status: IrrigationStatus; clock: number }) {
  const zones = lastWater(status.lines, clock);
  const flow = flowText(status.leak);
  const next = nextRunsLine(status, clock);
  return <div className="water-details">
    <p data-water="last">Last water: {zones.map((zone, index) => <span key={zone.zone}>{index > 0 && " · "}{WATER_ZONE_WORDS[zone.zone]} {zone.text}{zone.reason && <span className="muted"> ({zone.reason})</span>}</span>)}</p>
    <p data-water="running">Running: {runningLine(status)}</p>
    {status.engine
      ? <p data-water="next">Next: {next ?? "none scheduled"}</p>
      : <p data-water="before-engine">{BEFORE_ENGINE_LINE}</p>}
    <p data-water="flow" className={flow.flowing ? "fault" : undefined}>{flow.text}</p>
    <p data-water="saf4">{saf4Text(status.limits.saf4_gal)}</p>
    {status.lines.filter((line) => line.fault).map((line) => <div key={line.line_id} className="water-fault">
      <p className="fault" data-water="fault">{faultText(line)}</p>
      <ClearFaultButton line={line} engine={status.engine} />
    </div>)}
  </div>;
}

export type WaterCardProps = { readonly irrigation: PanelRead<IrrigationStatus>; readonly stop: StopControl };

export function WaterCard({ irrigation, stop }: WaterCardProps) {
  const headingId = useId();
  const clock = useNow();
  const status = irrigation.failed ? null : irrigation.data;
  const state = irrigation.failed ? "error" : status ? "ready" : "loading";
  const failed = <PanelError thing="water status" text={WATER_UNAVAILABLE} onRetry={irrigation.retry} />;
  return <section className="card panel now-water" aria-labelledby={headingId} aria-busy={state === "loading" || undefined} data-state={state}>
    <div className="card-head"><div><h2 id={headingId}>Water today</h2></div></div>
    <div className="water-body">
      {state === "loading" && <><div className="skeleton skeleton-sm" aria-hidden="true" /><p className="screen-reader">Loading water status</p></>}
      {state === "error" && failed}
      {status && <PanelErrorBoundary thing="water status" text={WATER_UNAVAILABLE} onRetry={irrigation.retry}>
        <p className="water-minutes" data-water="minutes">{minutesLine(status.today.zones)}</p>
      </PanelErrorBoundary>}
      <WaterActions stop={stop} />
      {status && <PanelErrorBoundary thing="water status" text={WATER_UNAVAILABLE} onRetry={irrigation.retry}>
        <WaterDetails status={status} clock={clock} />
      </PanelErrorBoundary>}
    </div>
  </section>;
}
