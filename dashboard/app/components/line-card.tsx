// SPDX-License-Identifier: MIT
import { useId, type ReactNode } from "react";
import { Link } from "react-router";
import { causeWords } from "../charts/causes";
import { localTime } from "../lib/format";
import type { IrrigationLine } from "../lib/operator-api";
import { agoWords, faultWords, lastWaterWords, nextRunWords, pauseWords, timeLeftWords, todayWords } from "../lib/water";
import { Badge, ChemistryBadge, EquipmentStateBadge, LineStateBadge, lineChemistry } from "./badge";
import { LocalTime, cls } from "./common";
import { useClockValue, useNow } from "./freshness";

/** Before the irrigation firmware a card shows the relay and says so (irrigation §9). */
export const BEFORE_ENGINE_TEXT = "Live job state arrives with the irrigation firmware";
/** L03's sub-row on L02's card (Round 2 #7). */
export const MASTER_TEXT = "opens the injector line from the tank with each feed";

const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));

/** "3 min ago" on the server's clock, with the local time in `title`; "—" for an unreadable time. */
export function Ago({ at }: { at: string }) {
  const now = useNow();
  if (!validTime(at)) return <>—</>;
  return <time dateTime={at} title={localTime(at)}>{agoWords((now - Date.parse(at)) / 1000)}</time>;
}

/** "0:40 left", counting down on the server's clock. */
export function TimeLeft({ until }: { until: string }) {
  const now = useNow();
  if (!validTime(until)) return null;
  return <span className="time-left"><time dateTime={until} title={localTime(until)}>{timeLeftWords(Date.parse(until) - now)}</time> left</span>;
}

/** The relay as the API reports it before the engine: running is ON, unknown stays unknown, every other state OFF. */
export const relayState = (code: string): "on" | "off" | "unknown" => (code === "running" ? "on" : code === "unknown" ? "unknown" : "off");

/**
 * A line's state in words (`LineState`, the API's call): RUNNING with cause, schedule and time left; paused until a time
 * (VPD misting continues on a VPD zone); DISABLED; waiting or blocked with the reason; "unknown · 3 min ago" only when
 * the API says unknown. Before the irrigation firmware, the relay ("Relay ON since 07:31").
 */
export function LineStateText({ line, engine, today, scheduleLabels }: { line: IrrigationLine; engine: boolean; today: string; scheduleLabels?: ReadonlyMap<number, string> }) {
  const { state } = line;
  if (!engine) {
    return <>Relay <EquipmentStateBadge state={relayState(state.code)} />{validTime(state.observed_at) && <> since <LocalTime at={state.observed_at} /></>}</>;
  }
  switch (state.code) {
    case "running": {
      const label = state.slot_id === null || state.slot_id === undefined ? null : scheduleLabels?.get(state.slot_id) ?? `slot ${state.slot_id}`;
      return <><LineStateBadge code="running" />{state.cause && <> · {causeWords(state.cause)}</>}{label && <> · {label}</>}{validTime(state.ends_at) && <> · <TimeLeft until={state.ends_at} /></>}</>;
    }
    case "paused":
      return <>{pauseWords(line, today) ?? "Paused"}</>;
    case "unknown":
      return <><LineStateBadge code="unknown" /> · {validTime(state.observed_at) ? <Ago at={state.observed_at} /> : "no state received"}</>;
    case "waiting":
    case "blocked":
      // A watchdog fault has its own line, with its end time.
      return <><LineStateBadge code={state.code} />{state.reason && !line.fault && <> · {state.reason}</>}</>;
    default:
      return <LineStateBadge code={state.code} />;
  }
}

/** A pause the state line does not already show: VPD misting can run on a paused zone. */
function PauseNote({ line, today }: { line: IrrigationLine; today: string }) {
  const until = line.paused_until;
  const ahead = useClockValue((now) => validTime(until) && Date.parse(until) > now);
  if (line.state.code === "paused" || !ahead) return null;
  return <p className="line-note">{pauseWords(line, today)}</p>;
}

/** The fault line: "Backing off until 13:20" (warn) or "Faulted: no misting until midnight (watchdog)" (fault). */
export function FaultLine({ line, today }: { line: Pick<IrrigationLine, "fault" | "fault_until">; today: string }) {
  const text = faultWords(line, today);
  if (!text) return null;
  return <p className={cls("line-fault", line.fault === "mister_fault" ? "fault" : "line-fault-warn")} data-fault={line.fault}>{text}</p>;
}

export type LineCardProps = {
  line: IrrigationLine;
  /** L03, the fertilizer master, as L02's sub-row. */
  master?: IrrigationLine | null;
  /** API-IRR-01 `engine`: false before the irrigation firmware, when the card shows the relay state. */
  engine: boolean;
  /** Today's America/Denver date, for "today 07:31". */
  today: string;
  /** Schedule labels by slot id (API-IRR-02), for "RUNNING · schedule · Wall MWF". */
  scheduleLabels?: ReadonlyMap<number, string>;
  /** Run…, Stop, Clear fault and the enable switch (#1136, #1182). */
  actions?: ReactNode;
};

/**
 * LineCard (design system §4.6): one line's card on the Water board. The head names the line with CLEAN or FERT and
 * whether it is enabled; a reserved line still disabled (L05, L06) reads Reserved with DISABLED, and fog (L11) reads
 * "Climate only" with no controls. Then its state in words, any watchdog fault, when it last watered and how that
 * ended, today's minutes against its cap, the next run, whether its one-time check is recorded, and History.
 */
export function LineCard({ line, master = null, engine, today, scheduleLabels, actions }: LineCardProps) {
  const headingId = useId();
  const fog = line.kind === "fog";
  // L05 and L06 (Round 2b E): reserved, and DISABLED until a person enables them, before the engine too.
  const reserved = line.reserved && !line.enabled;
  const next = engine ? nextRunWords(line.next_run, today) : null;
  return <article className="card line-card" data-line={line.line_id} data-state={line.state.code} aria-labelledby={headingId}>
    <div className="card-head line-card-head">
      <h3 id={headingId}>{line.line_id} {line.name}</h3>
      <span className="line-card-tags">
        {fog ? <span className="tag">Climate only</span> : <>
          <ChemistryBadge chemistry={lineChemistry(line.kind)} />{" "}
          {reserved
            ? <LineStateBadge code="disabled" reserved />
            : <Badge kind={line.enabled ? "on" : "off"}><span className="screen-reader">Enabled: </span>{line.enabled ? "On" : "Off"}</Badge>}
        </>}
      </span>
    </div>
    {!(engine && reserved && line.state.code === "disabled") && <p className="line-state"><LineStateText line={line} engine={engine} today={today} scheduleLabels={scheduleLabels} /></p>}
    {!engine && !fog && <p className="line-note">{BEFORE_ENGINE_TEXT}</p>}
    {engine && <FaultLine line={line} today={today} />}
    {engine && <PauseNote line={line} today={today} />}
    <p>{lastWaterWords(line, today)}</p>
    <p>{todayWords(line)}</p>
    {next && <p>{next}</p>}
    {!fog && (line.check
      ? <p className="line-note">Checked by {line.check.checked_by} <LocalTime at={line.check.checked_at} date />{line.check.note && <>: {line.check.note}</>}</p>
      : <p className="line-note">No check recorded</p>)}
    {master && <div className="line-subrow" data-line={master.line_id}>
      <p><strong>Fert master {master.line_id}</strong> · {MASTER_TEXT}</p>
      <p><LineStateText line={master} engine={engine} today={today} /> · {todayWords(master)}</p>
    </div>}
    <div className="actions line-card-actions">
      {!fog && actions}
      <Link to={`/activity?target=${encodeURIComponent(line.line_id)}`}>History<span className="screen-reader"> of {line.line_id}</span><span aria-hidden="true"> ›</span></Link>
    </div>
  </article>;
}
