// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import { localDate, shortTime } from "../../lib/format";
import { graphWindowUrl } from "../../lib/graph-link";
import type { IrrigationStatus, Schedule, ScheduleAgenda } from "../../lib/operator-api";
import { OperatorOnly } from "../../lib/session";
import { amountWords, daysWords, occurrenceReasonWords, pauseAfterRun, runSeconds, startWords } from "../../lib/water";
import { words } from "../../lib/words";
import { programSyncText } from "../../lib/writes";
import { Badge, ReadbackBadge } from "../badge";
import { PauseAfterRun } from "../pause-lines";
import { Popover } from "../popover";
import { STATUS_WORDS, nextStarts, spanStatus, type Entry, type LineInfo } from "./model";

const DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" });
const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

export type BlockPopoverProps = {
  entry: Entry | null;
  anchor: HTMLElement | null;
  onClose: () => void;
  agenda: Pick<ScheduleAgenda, "occurrences" | "solar">;
  info: LineInfo;
  program: Pick<IrrigationStatus, "engine" | "program"> | null;
  now: number;
  /** Operators: the editor for this schedule. */
  editTo?: (slotId: number) => string;
};

/**
 * A block's popover (redesign §6.2), anchored from 861 px and a sheet below: the run's day, time (with its rule's start
 * in words) and amount; what happened or why it will not run; its rule; its next starts; and for operators Edit and, on
 * one still planned, "Pause <line> until after this run". Then "Runs of L01 ›" and "Graphs around this run ›".
 */
export function BlockPopover({ entry, anchor, onClose, agenda, info, program, now, editTo }: BlockPopoverProps) {
  if (!entry) return null;
  const { occurrence } = entry;
  const schedule = entry.schedule as Schedule | undefined;
  const lineId = occurrence.line_id;
  const kind = info.kindOf(lineId);
  const status = spanStatus(occurrence);
  const day = localDate(occurrence.planned_start);
  const start = Date.parse(occurrence.planned_start);
  const length = schedule ? ((schedule.prewet_s ?? 0) + runSeconds(schedule) + (schedule.flush_s ?? 0)) * 1000 : 0;
  const graphs = graphWindowUrl(start - 30 * 60_000, start + length + 30 * 60_000);
  const until = schedule ? pauseAfterRun(occurrence, schedule, now) : null;
  const sync = schedule?.sync;
  const syncWords = sync && sync !== "confirmed" ? programSyncText(sync, program) : null;
  const next = schedule ? nextStarts(agenda, schedule.slot_id, now) : [];
  const title = `${info.nameOf(lineId)}${schedule?.label ? ` · ${schedule.label}` : ""}`;
  const when = `${DAY.format(new Date(`${day}T12:00:00Z`))} · ${shortTime(occurrence.planned_start)}${schedule?.start && schedule.start.kind !== "clock" ? ` (${startWords(schedule.start)})` : ""}${schedule ? ` · ${amountWords(schedule, kind, info.partnerOf(lineId))}` : ""}`;
  const reason = occurrenceReasonWords(occurrence.reason);
  const outcome = status === "done"
    ? ["Done", occurrence.outcome ? words("run_outcome", occurrence.outcome) : null, reason].filter(Boolean).join(" · ")
    : reason && reason.toLowerCase().startsWith(STATUS_WORDS[status]) ? capital(reason) : [capital(STATUS_WORDS[status]), reason].filter(Boolean).join(" · ");
  return <Popover open anchor={anchor} onClose={onClose} title={title} className="sch-popover">
    <dl className="data-list sch-pop-facts">
      <div className="data-pair"><dt>When</dt><dd>{when}</dd></div>
      <div className="data-pair" data-status={status}><dt>{status === "planned" ? "Status" : "Outcome"}</dt><dd>{outcome}{sync && sync !== "confirmed" && <> <ReadbackBadge state={sync} /></>}</dd></div>
      {schedule && <div className="data-pair"><dt>Rule</dt><dd>
        {daysWords(schedule.days)} · {startWords(schedule.start)} · {schedule.degraded_only ? "only if VPD sensing is degraded · " : ""}{schedule.manual ? <Badge kind="manual" /> : "Iris may adjust"}
      </dd></div>}
      {next.length > 0 && <div className="data-pair"><dt>Next</dt><dd>{next.join(" · ")}</dd></div>}
      {!schedule && <div className="data-pair"><dt>Rule</dt><dd>This schedule no longer exists</dd></div>}
    </dl>
    {syncWords && <p className="meta">{syncWords}</p>}
    {schedule && editTo && <OperatorOnly>
      <div className="sch-pop-actions">
        <Link className="button-link btn-primary" to={editTo(schedule.slot_id)} preventScrollReset data-write-control="true">Edit schedule</Link>
        {until && <PauseAfterRun lineId={lineId} until={until} />}
      </div>
    </OperatorOnly>}
    <p className="sch-pop-links">
      <Link to={`/water/history?line=${encodeURIComponent(lineId)}`}>Runs of {lineId}<span aria-hidden="true"> ›</span></Link>
      {graphs && <Link to={graphs}>Graphs around this run<span aria-hidden="true"> ›</span></Link>}
    </p>
  </Popover>;
}
