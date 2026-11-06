// SPDX-License-Identifier: MIT
import { type ReactNode } from "react";
import { Link } from "react-router";
import { causeWords } from "../charts/causes";
import { localTime } from "../lib/format";
import { runGraphUrl } from "../lib/graph-link";
import { lineDisplayName } from "../lib/lines";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import { OperatorOnly, useApiDown } from "../lib/session";
import { agoWords, faultWords, lastWaterParts, lineSyncWords, pauseWords, pausedNow, timeLeftWords, useWaterDrawer } from "../lib/water";
import { words } from "../lib/words";
import { ChemistryBadge, EquipmentStateBadge, ReadbackBadge, lineChemistry } from "./badge";
import { Button } from "./button";
import { ClearFaultButton } from "./clear-fault-button";
import { LocalTime, cls } from "./common";
import { Ago, useClockValue, useNow } from "./freshness";
import { ObjectRow, type RowTone } from "./layout/object-row";
import { LineEnable } from "./line-enable";
import { OptionsButton } from "./line-options";
import { Menu } from "./menu";
import { ResumeLine } from "./pause-lines";
import { RUN_ARRIVES_TEXT, RunControl, runOffer } from "./run-dialog";
import { useFreshConfirmed } from "./schedule/rules-list";
import { StopButton } from "./stop-button";

/** L03's sub-row under L02 (Round 2 #7). */
export const MASTER_TEXT = "opens the injector line from the tank with each feed";

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));

export { Ago };

/** "0:40 left", counting down on the server's clock. */
export function TimeLeft({ until }: { until: string }) {
  const now = useNow();
  if (!validTime(until)) return null;
  return <span className="time-left"><time dateTime={until} title={localTime(until)}>{timeLeftWords(Date.parse(until) - now)}</time> left</span>;
}

/** The relay as the API reports it before the engine: running is ON, unknown stays unknown, every other state OFF. */
export const relayState = (code: string): "on" | "off" | "unknown" => (code === "running" ? "on" : code === "unknown" ? "unknown" : "off");

/** The running dot in its cause's colour (redesign §4.4: a dot beside the word, no tint); neutral while the API is down. */
function RunningDot({ cause }: { cause: string | null | undefined }) {
  return <span className="wat-dot" data-cause={cause ?? "unknown"} aria-hidden="true" />;
}

/**
 * A line's state in one sentence (`irr_state`, the API's call, redesign §5.2): "Running · VPD · since 06:58" or with the
 * time left; "Paused until Sat 09:00 · VPD misting continues"; "Faulted: no misting until midnight (watchdog)"; "Idle ·
 * last 07:31, 12 min · ok"; "Off · no check recorded"; "Reserved · disabled"; "Unknown · 3 min ago" only when the API
 * says unknown. Before the irrigation firmware, the relay ("Relay ON since 07:31").
 */
export function LineStateText({ line, engine, today, scheduleLabels }: { line: IrrigationLine; engine: boolean; today: string; scheduleLabels?: ReadonlyMap<number, string> }) {
  const { state } = line;
  if (!engine) {
    return <>Relay <EquipmentStateBadge state={relayState(state.code)} />{validTime(state.observed_at) && <> since <LocalTime at={state.observed_at} /></>}</>;
  }
  const fault = faultWords(line, today);
  switch (state.code) {
    case "running": {
      const label = state.slot_id === null || state.slot_id === undefined ? null : scheduleLabels?.get(state.slot_id) ?? `slot ${state.slot_id}`;
      return <><RunningDot cause={state.cause} />Running{state.cause && <> · {causeWords(state.cause)}</>}{label && <> · {label}</>}
        {validTime(state.ends_at) ? <> · <TimeLeft until={state.ends_at} /></> : validTime(state.observed_at) && <> · since <LocalTime at={state.observed_at} /></>}</>;
    }
    case "paused":
      return <>{pauseWords(line, today) ?? "Paused"}</>;
    case "unknown":
      return <>Unknown · {validTime(state.observed_at) ? <Ago at={state.observed_at} words={agoWords} /> : "no state received"}</>;
    case "waiting":
      return <>Waiting{state.reason && <> · {state.reason}</>}</>;
    case "blocked":
      // A watchdog fault says it with its end time.
      return fault ? <span className={cls("line-fault", line.fault === "mister_fault" ? "fault" : "line-fault-warn")} data-fault={line.fault}>{fault}</span> : <>Can't run now{state.reason && <> · {state.reason}</>}</>;
    case "disabled":
      // L03 is checked with L02 and fog is climate only: neither has a check of its own.
      return line.reserved ? <>Reserved · disabled</> : <>Off{!line.check && line.kind !== "fog" && line.kind !== "master" ? " · no check recorded" : ""}</>;
    case "idle":
      return <>Idle · {lastWaterParts(line, today).state}</>;
    default:
      return <>{words("line_state", state.code)}</>;
  }
}
/** "Pause until…" in a line's ⋯ menu, for operators: the pause drawer with this line chosen (`?pause=<line_id>`). */
function PauseThisLine({ line }: { line: Pick<IrrigationLine, "line_id"> & Partial<Pick<IrrigationLine, "name">> }) {
  const { open } = useWaterDrawer("pause");
  return <Button write aria-haspopup="dialog" onClick={() => open(line.line_id)}>Pause until… <span className="screen-reader">{lineDisplayName(line)}</span></Button>;
}

export type LineCardProps = {
  line: IrrigationLine;
  /** L03, the fertilizer master, as L02's sub-row. */
  master?: IrrigationLine | null;
  /** API-IRR-01 `engine`: false before the irrigation firmware, when the row shows the relay state. */
  engine: boolean;
  /** API-IRR-01 `program`, for "rev 43 sending" and the reject in words. */
  program?: IrrigationStatus["program"];
  /** Today's America/Denver date, for "today 07:31". */
  today: string;
  /** Schedule labels by slot id (API-IRR-02), for "Running · schedule · Wall MWF". */
  scheduleLabels?: ReadonlyMap<number, string>;
  /** The today strip and "next 16:00 · 10 min" (redesign §5.2). */
  strip?: ReactNode;
  /** A mister's own VPD control and Schedule (#1344), above its strip. */
  controls?: ReactNode;
  /** Only the first row of a zone card carries the hour ticks. */
  className?: string;
};

/**
 * A line's row on Water → Today (redesign §5.2, `ObjectRow`): "Wall drip" with CLEAN or FERT and its one sync pill,
 * the state sentence, the today strip with the next run, one primary action by state (Run… while idle, Stop while
 * running, Clear fault on a latched mister, Resume while paused), the line's own Stop only while it runs or its relay
 * reads unknown (D-UX-06 as Jason set it on 2026-11-06, #1349: no Stop on an idle row; Stop all water in the header
 * stops every line), never disabled by the writes flag, and ⋯ with
 * the enable switch and its check sheet, Options…, Pause until…, "Activity for Wall drip ›", "Runs of Wall drip ›" and Graphs. Fog
 * (L11) is a climate device: no sync pill and no Options, but it runs once ("Fog once…", #1343 follow-up), with its own
 * Stop, and the enable switch it needs first (the controller runs no disabled line); no pause. Viewers get the same row with the menu's links alone.
 */
export function LineCard({ line, master = null, engine, program, today, scheduleLabels, strip, controls, className }: LineCardProps) {
  const apiDown = useApiDown();
  const now = useNow();
  const fog = line.kind === "fog";
  const title = lineDisplayName(line);
  const code = line.state.code;
  const running = code === "running";
  const latched = engine && line.fault === "mister_fault";
  const paused = useClockValue((at) => pausedNow(line, at));
  const fresh = useFreshConfirmed(line.sync, null, now);
  // Before the engine every line waits for the firmware: the status line says so once, not a pill on every row.
  const pill = fog || !engine ? null : line.sync === "requested" || line.sync === "not_confirmed" ? <ReadbackBadge state={line.sync} /> : fresh ? <ReadbackBadge state="confirmed" /> : null;
  const syncWords = fog || !engine ? null : line.sync !== "confirmed" ? lineSyncWords(line.sync, { engine, program }) : null;
  const last = lastWaterParts(line, today);
  // A pause is the state while the line rests; while it runs (VPD misting carries on) or reads unknown, it is a note.
  const pausedWords = !fog && paused ? pauseWords(line, today) : null;
  const pauseIsState = Boolean(pausedWords) && engine && code !== "running" && code !== "unknown";
  const pauseNote = pausedWords && !pauseIsState ? pausedWords : null;
  const meta = [syncWords, pauseNote, code === "idle" && engine ? last.meta : null].filter(Boolean);
  const offer = runOffer(line, engine);
  // D-UX-06 (2026-11-06): a Stop only on a row that runs, or whose relay reads unknown and so may be on.
  const stoppable = running || code === "unknown";
  const primary = running ? null : latched ? <ClearFaultButton line={line} engine={engine} /> : paused && !fog ? <ResumeLine line={line} /> : offer === "run" || offer === "mist" || offer === "enable" ? <RunControl line={line} engine={engine} /> : null;
  const tone: RowTone | undefined = running ? "running" : latched ? "fault" : engine && line.fault === "backing_off" ? "warn" : undefined;
  const token = running ? (line.state.cause === "vpd" ? "eq-mister" : line.state.cause === "feed" ? "eq-fert" : line.state.cause === "run_now" ? "eq-master" : "eq-water") : undefined;
  const graphs = line.last_water && validTime(line.last_water.at) ? runGraphUrl({ started_at: line.last_water.at, ended_at: null, open_s: line.last_water.open_s }) : "/graphs";
  const id = line.line_id;
  const menu = <Menu name={`More for ${title}`}>
    {!fog && <OperatorOnly>
      <div className="wat-menu-switch" data-menu-keep=""><LineEnable line={line} /></div>
      <OptionsButton line={line} />
      <PauseThisLine line={line} />
      {offer === "arrives" && <p className="state-note" data-arrives="run">{RUN_ARRIVES_TEXT}</p>}
      {!engine && line.kind === "mister" && <ClearFaultButton line={line} engine={engine} />}
    </OperatorOnly>}
    {fog && engine && <OperatorOnly><div className="wat-menu-switch" data-menu-keep=""><LineEnable line={line} /></div></OperatorOnly>}
    <Link to={`/activity/log?target=${encodeURIComponent(id)}`}>Activity for {title}<span aria-hidden="true"> ›</span></Link>
    <Link to={`/water/history?line=${encodeURIComponent(id)}`}>Runs of {title}<span aria-hidden="true"> ›</span></Link>
    <Link to={graphs}>Graphs <span className="screen-reader">around {title}'s last run</span><span aria-hidden="true"> ›</span></Link>
    {!fog && <p className="meta wat-menu-check">{line.check
      ? <>Checked by {line.check.checked_by} <LocalTime at={line.check.checked_at} date />{line.check.note && <>: {line.check.note}</>}</>
      : "No check recorded"}</p>}
  </Menu>;
  // A disabled line is one quiet row (#1349): its state, its faded strip, nothing else to read.
  const off = !fog && !running && (code === "disabled" || line.enabled === false);
  return <ObjectRow headingLevel={4} className={cls("line-card", "wat-line", off && "is-off", className)} data={{ "data-line": id, "data-state": code }} id={`line-${id}`}
    title={title} tag={fog ? <span className="tag">Climate only</span> : <ChemistryBadge chemistry={lineChemistry(line.kind)} />} pill={pill}
    state={<span className={cls("line-state", apiDown && "is-neutral")}>{pauseIsState ? pausedWords : <LineStateText line={line} engine={engine} today={today} scheduleLabels={scheduleLabels} />}</span>}
    meta={meta.length ? meta.join(" · ") : undefined}
    primary={primary}
    stop={(fog && !engine) || !stoppable ? undefined : <StopButton lineId={id} />}
    menu={menu} tone={tone} token={token}>
    {controls}
    {strip}
    {master && <div className="line-subrow" data-line={master.line_id}>
      <p><span className="glyph glyph-branch" aria-hidden="true" /><strong>{lineDisplayName(master)}</strong>: {MASTER_TEXT} · <LineStateText line={master} engine={engine} today={today} /></p>
    </div>}
  </ObjectRow>;
}
