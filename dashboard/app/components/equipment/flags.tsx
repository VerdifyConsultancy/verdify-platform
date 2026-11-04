// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { formatValue, localDate } from "../../lib/format";
import type { Leak, Occupancy } from "../../lib/operator-api";
import { READ_INTERVAL_MS, panelRead, useApiRead } from "../../lib/queries";
import { useShellNow, useShellStatus } from "../../lib/shell";
import { LocalTime, cls } from "../common";
import { finite } from "../data/viz";
import { Ago } from "../freshness";
import { Panel, type PanelState } from "../panel";

/**
 * The flags (redesign §5.12): occupancy from API-IRR-01 (Frigate through Home Assistant), and the leak flag and the site
 * meter's flow from API-02 `leak`. Each says what it is in words; unknown is "Unknown", never off or 0, and while the
 * ingestor is not running or the API is down the API-02 flags read unknown, because their last values are old news.
 */
export type FlagTone = "fault" | "warn" | undefined;
export type FlagWords = { readonly state: "on" | "off" | "unknown"; readonly text: ReactNode; readonly tone?: FlagTone };

/** Someone inside (with when Frigate saw them), nobody seen (with the last person's time), or unknown; the mist hold off says so. */
export function occupancyFlag(occupancy: Occupancy | null | undefined, today: string): FlagWords {
  const hold = occupancy?.hold_enabled === false ? " · mist hold off" : "";
  const seen = occupancy?.seen_at && Number.isFinite(Date.parse(occupancy.seen_at)) ? occupancy.seen_at : null;
  if (occupancy?.occupied === true) return { state: "on", text: <>Someone inside{seen && <> · seen <Ago at={seen} /></>}{hold}</> };
  if (occupancy?.occupied === false) return { state: "off", text: <>Nobody seen{seen && <> · last person <LocalTime at={seen} date={localDate(seen) !== today} /></>}{hold}</> };
  return { state: "unknown", text: <>Unknown{hold}</> };
}

/** Water flowing with every Verdify valve closed (the leak alert), flow stopped, none, or unknown. */
export function leakFlag(leak: Leak | null | undefined): FlagWords {
  if (leak?.stopped_at && Number.isFinite(Date.parse(leak.stopped_at))) return { state: "off", text: <>Flow stopped at <LocalTime at={leak.stopped_at} /></>, tone: "warn" };
  if (leak?.active === true) {
    const since = leak.since && Number.isFinite(Date.parse(leak.since)) ? leak.since : null;
    return { state: "on", text: <><span aria-hidden="true">▲ </span>Water flowing with every valve closed{since && <> · since <LocalTime at={since} /></>}</>, tone: "fault" };
  }
  if (leak?.active === false) return { state: "off", text: "None" };
  return { state: "unknown", text: "Unknown" };
}

/** The site meter's flow: "1 gal/min", "No flow", or unknown. */
export function flowFlag(leak: Leak | null | undefined): FlagWords {
  const flow = leak?.flow_gpm;
  if (!finite(flow)) return { state: "unknown", text: "Unknown" };
  return flow > 0 ? { state: "on", text: <>Flowing · {formatValue(flow, "gal/min")}</> } : { state: "off", text: "No flow" };
}

function Flag({ flag, name, words }: { flag: string; name: string; words: FlagWords }) {
  return <li className={cls("ctl-flag", words.tone && `is-${words.tone}`)} data-flag={flag} data-state={words.state}>
    <span className="ctl-flag-name">{name}</span>
    <span className="ctl-flag-state">{words.text}</span>
  </li>;
}

export function FlagsCard({ today }: { today: string }) {
  const shell = useShellNow();
  const status = useShellStatus();
  // The engine flag's own cadence: occupancy changes on Home Assistant's 300 s poll.
  const irrigation = panelRead(useApiRead("API-IRR-01", {}, { intervalMs: READ_INTERVAL_MS.settings }));
  const now = shell.data;
  // The meter's flags are the ingestor's: while it is not running, or the API is down, they are not known.
  const leak = now && status.ingestorRunning !== false && !status.apiDown ? now.leak : null;
  const occupancy = irrigation.failed ? null : irrigation.data?.occupancy ?? null;
  const state: PanelState = !now && shell.pending && irrigation.pending ? "loading" : "ready";
  return <Panel title="Flags" thing="the flags" state={state} reserve="sm">
    <ul className="ctl-flags">
      <Flag flag="occupancy" name="Occupancy" words={occupancyFlag(occupancy, today)} />
      <Flag flag="leak" name="Leak" words={leakFlag(leak)} />
      <Flag flag="flow" name="Water flowing" words={flowFlag(leak)} />
    </ul>
  </Panel>;
}
