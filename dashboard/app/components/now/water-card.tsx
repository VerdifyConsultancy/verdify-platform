// SPDX-License-Identifier: MIT
/**
 * Water today on Now (redesign §5.1; screens SCR-01, US-NOW-04, US-WAT-13, US-WAT-18) from API-IRR-01: first the daily
 * mist & fog limit's gallons (SAF-4, #1349), the primary daily water limit (#1339), then each watering zone's minutes
 * today against its cap as a `Meter` (the drip zone in `--eq-water`, the mister zones in `--eq-mister`) with when it
 * last got water (`last-water.ts`), and the flow in words. Before the
 * irrigation firmware it says where the wall drip runs from. A failed or not yet served API-IRR-01 reads "Water status
 * unavailable." with Retry, never idle. Stop all water is not here: it is the first quick action, one per page.
 */
import { formatValue, shortTime } from "../../lib/format";
import { WATER_ZONES, WATER_ZONE_WORDS, lastWater, type WaterZone, type ZoneLastWater } from "../../lib/last-water";
import type { IrrigationLine, IrrigationStatus, Leak } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { SAF4_HELP, SAF4_PRIMARY, SAF4_TERM, climateTodayWords } from "../../lib/water";
import { cls } from "../common";
import { Meter, meterWords, type MeterDomain } from "../data/meter";
import { useNow } from "../freshness";
import { InfoTip } from "../info-tip";
import { Panel, type PanelState } from "../panel";
import { lineStatesUnknown } from "./status";

export const WATER_UNAVAILABLE = "Water status unavailable.";
export const BEFORE_ENGINE_LINE = "The wall drip runs from Water; run-now on the other lines arrives with the irrigation firmware";

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const isMister = (line: Pick<IrrigationLine, "kind">) => line.kind === "mister" || line.kind === "fert_mister";

export type ZoneWater = {
  readonly zone: WaterZone;
  /** Minutes today (API-IRR-01 `today.zones`), every purpose, as the cap counts them; null when the API has no number. */
  readonly minutes: number | null;
  /** The VPD and fallback misting inside them (`today.climate_zones`, #1348); null when the API has no number. */
  readonly climate: number | null;
  /** The zone's cap: the daily caps of its enabled lines that have one; null when none does. */
  readonly cap: number | null;
  readonly domain: MeterDomain;
  readonly last: ZoneLastWater;
};

/** Each watering zone in its fixed order (Wall, Center, South, West), with its minutes, cap, domain and last water. */
export function zoneWater(status: Pick<IrrigationStatus, "lines" | "today">, clock: number): ZoneWater[] {
  const last = lastWater(status.lines, clock);
  return WATER_ZONES.map((zone, index) => {
    const lines = status.lines.filter((line) => line.zone === zone && line.enabled);
    const caps = lines.map((line) => line.daily_cap_min).filter((cap) => finite(cap) && cap > 0);
    const minutes = Object.hasOwn(status.today.zones, zone) ? status.today.zones[zone] : null;
    const climateZones = status.today.climate_zones ?? {};
    const climate = Object.hasOwn(climateZones, zone) ? climateZones[zone] : null;
    const misters = lines.length > 0 && lines.every(isMister);
    return { zone, minutes: finite(minutes) ? minutes : null, climate: finite(climate) ? climate : null, cap: caps.length ? caps.reduce((sum, cap) => sum + cap, 0) : null, domain: misters ? "mister" : "water", last: last[index] };
  });
}

/**
 * The flow in words (US-WAT-18): water flowing with every Verdify valve closed while the leak alert is open and flow
 * has not stopped; "Flow stopped at 03:20" once it has, never an all clear while the alert is open; "Flow: unknown"
 * while the flag is unknown; else "No flow with every valve closed".
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
  return { text: "No flow with every valve closed", flowing: false };
}

/** "21 of 360 gal" against the daily mist & fog limit; an unknown use or limit reads "—". */
export function saf4Text(saf4: IrrigationStatus["limits"]["saf4_gal"]): string {
  const number = (value: number | null) => (finite(value) ? formatValue(value, "gal").replace(/\s*gal$/, "") : "—");
  return `${number(saf4.used)} of ${number(saf4.max)} gal`;
}

/**
 * "14 / 120 min", "60 / 60 min · cap reached", "132 / 120 min · +12 min over": whole minutes against the cap, as the
 * caps are set (never "2 h"); "— / 60 min" when the API has no number; the minutes alone with no cap. The Meter's own
 * words (`meterWords`), so Now and Water say a zone's minutes the same way.
 */
export function minutesWords(minutes: number | null, cap: number | null): { amount: string; over: string | null } {
  return meterWords(minutes, cap, "min");
}

/** "of which VPD 2 min" when VPD or fallback misting is in a zone's minutes today (#1348); null when none is. */
export function vpdPartWords(climate: number | null): string | null {
  return finite(climate) && climate > 0 ? `of which VPD ${Math.max(1, Math.round(climate))}\u00a0min` : null;
}

/** One zone's row: its name, its minutes against its cap (every purpose, as the cap counts), and when it last got water. */
function ZoneRow({ row, stale }: { row: ZoneWater; stale: boolean }) {
  const name = WATER_ZONE_WORDS[row.zone];
  const setUp = row.last.state !== "not_set_up";
  const words = minutesWords(row.minutes, row.cap);
  const vpd = vpdPartWords(row.climate);
  return <li className="now-water-row" data-water={row.zone}>
    <span className="now-water-name">{name}</span>
    {setUp
      ? <span className="now-water-meter">
        <Meter label={`${name} minutes today`} unit="min" value={row.minutes} cap={row.cap} domain={row.domain} decorative state={stale ? "stale" : "ready"} />
        <span className="now-water-amount">{words.amount}{words.over && <span className="now-water-over"><span className="now-water-sep"> · </span>{words.over}</span>}
          {vpd && <span className="meta" data-water="vpd"><span className="now-water-sep"> · </span>{vpd}</span>}</span>
      </span>
      : <span className="now-water-none meta">Not set up</span>}
    <span className={cls("now-water-last", row.last.state === "running" && "now-water-running")}>
      {setUp ? row.last.text : null}{row.last.reason && <span className="meta"> ({row.last.reason})</span>}
    </span>
  </li>;
}

export type WaterCardProps = { readonly irrigation: PanelRead<IrrigationStatus> };

export function WaterCard({ irrigation }: WaterCardProps) {
  const clock = useNow();
  const status = irrigation.failed ? null : irrigation.data;
  const state: PanelState = irrigation.failed ? "error" : status ? "ready" : "loading";
  const flow = status ? flowText(status.leak) : null;
  const stale = status ? lineStatesUnknown(status) : false;
  const saf4 = status?.limits.saf4_gal;
  const climate = status ? climateTodayWords(status.today.climate) : null;
  return <div className="now-water">
    <Panel title="Water today" thing="water status" state={state} reserve="sm" onRetry={irrigation.retry} errorText={WATER_UNAVAILABLE}
      link={{ to: "/water", label: <>Water <span className="screen-reader">today, by line</span></> }}>
      {status && <>
        <ul className="now-water-zones">
          {/* The primary daily water limit comes first (Jason, 2026-11-06, #1339); each zone's minutes are secondary. */}
          {saf4 && <li className="now-water-row now-water-saf4" data-water="saf4">
            <span className="now-water-name">{SAF4_TERM}<InfoTip term={SAF4_TERM}><p>{SAF4_HELP}</p></InfoTip> <span className="now-water-primary meta">{SAF4_PRIMARY}</span></span>
            <span className="now-water-meter">
              <Meter label={`${SAF4_TERM}, gallons today`} unit="gal" value={saf4.used} cap={saf4.max} domain="water" decorative state={stale ? "stale" : "ready"} />
              <span className="now-water-amount">{saf4Text(saf4).replace(" of ", " / ")}</span>
            </span>
            <span className="now-water-last meta">all misters and fog</span>
          </li>}
          {zoneWater(status, clock).map((row) => <ZoneRow key={row.zone} row={row} stale={stale} />)}
        </ul>
        {climate && <p className="meta" data-water="climate">{climate}</p>}
        {flow && <p data-water="flow" className={cls("now-water-flow", flow.flowing ? "fault" : "meta")}>{flow.text}</p>}
        {stale && <p className="meta" data-water="unknown">Line states not reported: the minutes are the last the controller sent.</p>}
        {!status.engine && <p className="meta" data-water="before-engine">{BEFORE_ENGINE_LINE}</p>}
      </>}
    </Panel>
  </div>;
}
